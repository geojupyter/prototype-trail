from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import TYPE_CHECKING, Self

from trail.entry import Entries, Entry
from trail.util import (
    MISSING,
    PathLike,
    container,
    mtime_repr,
    st_size_repr,
    walk,
)

if TYPE_CHECKING:
    from trail.trail import Trail


@dataclass(kw_only=True, eq=False, repr=False)
class Asset(Entry):
    """
    A file or a directory tracked by a Trail. A directory asset is tracked whole, and its
    inside is never walked.

    >>> trail.assets.select[0]
    Asset
        id: 9d167ab8f5f64f3f8ac72f3132c70ad1
        path: /tmp/tmpp_bl05lq/new.csv
        st_size: 17 B
        st_mtime: 2026-10-02 23:39:37
    """
    is_directory: bool = field(default=False, repr=False)

    if TYPE_CHECKING:
        _parent: Assets

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        """
        Yields the fields a repr shows. The size and mtime are read from the filesystem, and a
        directory asset also shows how many files it holds.
        """
        yield from super()._repr_items()
        # stat'd on access rather than stored, so a resource that has gone says so rather than
        # raising out of a repr
        try:
            count, size, mtime = self.fingerprint
        except OSError:
            if self.is_directory:
                yield 'files', MISSING
            yield 'st_size', MISSING
            yield 'st_mtime', MISSING
            return
        if self.is_directory:
            yield 'files', count
        yield 'st_size', st_size_repr(size)
        yield 'st_mtime', mtime_repr(mtime)

    @classmethod
    def from_path(
            cls,
            path: PathLike,
            trail: Trail | None = None,
    ) -> Self:
        """
        Returns a new asset for an existing file or directory. It is not tracked until `track`
        is called. Raises ValueError for Trail metadata and for anything that is neither a
        regular file nor a directory.

        >>> Asset.from_path(project / 'cube.zarr', trail=trail)
        Asset
            id: 4e260dc608f64dbca950835a8981ca5d
            path: /tmp/tmpp_bl05lq/cube.zarr
            files: 1
            st_size: 2 B
            st_mtime: 2026-10-02 23:39:37
        """
        path = Path(path).expanduser().resolve()
        metadata = path.stat()

        if trail is not None and trail._ignored(path):
            raise ValueError(f"Cannot track Trail metadata: {path}")

        if S_ISDIR(metadata.st_mode):
            is_directory = True
        elif S_ISREG(metadata.st_mode):
            is_directory = False
        else:
            raise ValueError(f"Not a regular file or directory: {path}")

        out = cls(path=path, is_directory=is_directory)
        out._trail = trail
        if trail is not None:
            out._parent = trail.assets
        return out

    @property
    def fingerprint(self) -> tuple[int, int, float]:
        """
        The number of files, their total size in bytes, and the newest mtime. A file counts
        itself. A directory asset counts every file inside it, at any depth, which is computed
        on access rather than on every event.

        >>> trail.assets[project / 'new.csv'].fingerprint
        (1, 17, 1791009577.8193233)
        """
        metadata = self.path.stat()
        if not self.is_directory:
            return 1, metadata.st_size, metadata.st_mtime
        count = 0
        size = 0
        newest = metadata.st_mtime
        pending = [self.path]
        while pending:
            with os.scandir(pending.pop()) as entries:
                for entry in entries:
                    if entry.is_symlink():
                        continue
                    stat = entry.stat()
                    newest = max(newest, stat.st_mtime)
                    if entry.is_dir():
                        pending.append(Path(entry.path))
                    else:
                        count += 1
                        size += stat.st_size
        return count, size, newest

    @property
    def st_size(self) -> int:
        """Size of the asset, in bytes. A directory asset sums the files inside it."""
        return self.fingerprint[1]

    @property
    def st_mtime(self) -> float:
        """
        Last modification time of the asset, in seconds since the epoch. A directory asset
        takes the newest inside it.
        """
        return self.fingerprint[2]

    def move(self, destination: PathLike) -> Self:
        """
        Repoint a tracked asset at `destination`, keeping its ID and its position in the
        collection, so that renaming a resource outside the process does not fork its identity.
        """
        collection = self._parent
        destination = Path(destination).expanduser().resolve()
        if destination == self.path:
            return self
        trail = self._trail
        if trail._ignored(destination):
            raise ValueError(f"Cannot track Trail metadata: {destination}")
        previous = self.path
        self.path = destination
        # a path holds one asset
        occupant = collection.path2entry.get(destination)
        if (
            occupant is not None
            and occupant is not self
        ):
            occupant.offtrail()
        collection.repath(self, previous)
        return self


class Assets(Entries[Asset]):
    """
    A collection of Asset objects tracked by a Trail, in the order they were tracked.

    >>> trail.assets
    Assets (2)
        0. Asset
            id: 9d167ab8f5f64f3f8ac72f3132c70ad1
            path: /tmp/tmpp_bl05lq/new.csv
            st_size: 17 B
            st_mtime: 2026-10-02 23:39:37
        1. Asset
            id: 89260b75049b422699ac39efed9d28a9
            path: /tmp/tmpp_bl05lq/data/a.png
            st_size: 3 B
            st_mtime: 2026-10-02 23:39:37
    """

    entry_type = Asset

    def clear(self) -> None:
        """
        Offtrail everything the collection holds. The removals are recorded like any other, so
        that a cleared collection stays cleared rather than coming back with the log's replay.
        """
        paths = tuple(
            asset.path
            for asset in self
        )
        if not paths:
            return
        self._trail.offtrail(*paths)

    def scan(self, *paths: PathLike) -> tuple[Asset, ...]:
        """
        Track the files `.markers` indicates but that the Trail does not yet track. A file among
        `paths` is checked alone and a directory is walked. With no paths, every Dir is walked,
        recursively when it is recursive and otherwise only its own files. Returns the assets
        newly tracked.

        >>> trail.assets.scan(project / 'later.csv')
        (Asset
            id: 9290baf4fb794a31a94122068108c497
            path: /tmp/tmpp_bl05lq/later.csv
            st_size: 2 B
            st_mtime: 2026-10-02 23:39:37,)
        """
        trail = self._trail
        dirs = trail.dirs
        if not dirs:
            return ()
        candidates: list[Path] = []
        walks: dict[Path, bool] = {}
        if paths:
            for path in paths:
                path = Path(path).expanduser()
                # resolving would put the target in place of the link
                if path.is_symlink():
                    continue
                path = path.resolve()
                if (
                    path.is_dir()
                    and not container(path)
                ):
                    walks.update(self._reach(path))
                else:
                    candidates.append(path)
        else:
            for directory in dirs:
                walks[directory.path] = directory.recursive
        candidates.extend(walk(walks, self._descends))
        marked = {
            path: dirs.match(path)
            for path in dict.fromkeys(candidates)
            if self._markable(path)
        }
        if not marked:
            return ()
        return tuple(trail._track(marked))

    def _reach(self, path: Path) -> dict[Path, bool]:
        """
        Returns the directories a scan of `path` walks, each with whether it is walked
        recursively. The path is walked recursively if a recursive Dir covers it.

        >>> trail.assets._reach(project / 'data')
        {PosixPath('/tmp/tmpp_bl05lq/data'): True}
        """
        dirs = self._trail.dirs
        path2dir = dirs.path2entry
        recursive = any(
            path2dir[parent].recursive
            for parent in (path, *path.parents)
            if parent in path2dir
        )
        out = {path: recursive}
        # a Dir below the path is walked as deep as it reaches
        out.update(
            (directory.path, directory.recursive)
            for directory in dirs
            if (
                directory.path != path
                and directory.path.is_relative_to(path)
            )
        )
        return out

    def _markable(self, path: Path) -> bool:
        """
        Whether a scan should track a path. It must not be tracked or offtrailed yet, must not
        be Trail metadata, must be decided by `.markers`, and must be a file or a container.
        """
        trail = self._trail
        # the stat comes last, since most candidates fail before it
        return (
            path not in self.path2entry
            and path not in trail._offtrailed_paths
            and not trail._ignored(path)
            and trail.dirs.tracks(path)
            and (
                path.is_file()
                or (
                    container(path)
                    and path.is_dir()
                )
            )
        )

    def _descends(self, path: Path) -> bool:
        """
        Whether a walk enters a directory. Symlinks, Trail metadata, offtrailed paths and
        directory assets are never entered. A hidden directory is entered only if a Dir sits at
        or below it.

        >>> trail.assets._descends(project / '.git')
        False
        """
        trail = self._trail
        if (
            path.is_symlink()
            or trail._ignored(path)
            or path in trail._offtrailed_paths
            or container(path)
        ):
            return False
        asset = self.path2entry.get(path)
        # the inside of a directory asset is never walked
        if (
            asset is not None
            and asset.is_directory
        ):
            return False
        # a hidden directory is walked only if a Dir sits at or below it, which keeps .git and
        # .venv out of the walk
        return (
            not path.name.startswith('.')
            or any(
                directory.path.is_relative_to(path)
                for directory in trail.dirs
            )
        )
