from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import TYPE_CHECKING, ClassVar, Self

from trail import fmt
from trail.entry import Entries, Entry
from trail.util import CHILDREN, EVERYTHING, PathLike

if TYPE_CHECKING:
    from trail.session import Access
    from trail.trail import Trail


@dataclass(kw_only=True, eq=False, repr=False)
class Asset(Entry):
    """
    A file tracked by a Trail.

    >>> trail: Trail
    >>> trail.assets.select[0]
    Asset
        id: 9d167ab8f5f64f3f8ac72f3132c70ad1
        path: /tmp/tmpp_bl05lq/new.csv
        st_size: 17 B
        st_mtime: 2026-10-02 23:39:37
    """
    selectable: ClassVar[tuple[str, ...]] = (
        *Entry.selectable,
        'st_size',
        'st_mtime',
    )

    is_directory: bool = field(default=False, repr=False)

    if TYPE_CHECKING:
        _parent: Assets = field(init=False, repr=False)

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        """
        Yields the fields a repr shows. Size and mtime are read from the filesystem. A directory
        asset also shows how many files it holds.
        """
        yield from super()._repr_items()
        # stat'd on access rather than stored, so a resource that has gone says so rather than
        # raising out of a repr
        try:
            count, size, mtime = self.fingerprint
        except OSError:
            if self.is_directory:
                yield 'files', fmt.MISSING
            yield 'st_size', fmt.MISSING
            yield 'st_mtime', fmt.MISSING
            return
        if self.is_directory:
            yield 'files', count
        yield 'st_size', fmt.st_size(size)
        yield 'st_mtime', fmt.mtime(mtime)

    @classmethod
    def from_path(
            cls,
            path: PathLike,
            trail: Trail | None = None,
    ) -> Asset:
        """
        Returns a new asset for an existing file or directory. The asset is not tracked until
        `track` is called.
        - A class whose `is_directory` matches the path makes it as itself.
        - `Asset.from_path` makes a DirAsset for a directory.
        - `DirAsset.from_path` raises ValueError for a file.

        Raises ValueError for Trail metadata, and for paths that are not regular files or
        directories.

        >>> trail: Trail
        >>> project: Path
        >>> Asset.from_path(project / 'cube.zarr', trail=trail)
        DirAsset
            id: 4e260dc608f64dbca950835a8981ca5d
            path: /tmp/tmpp_bl05lq/cube.zarr
            files: 1
            st_size: 2 B
            st_mtime: 2026-10-02 23:39:37
        """
        path = Path(path).expanduser().resolve()
        metadata = path.stat()

        if trail is not None and trail._is_ignored(path):
            raise ValueError(f"Cannot track Trail metadata: {path}")

        if S_ISDIR(metadata.st_mode):
            is_directory = True
        elif S_ISREG(metadata.st_mode):
            is_directory = False
        else:
            raise ValueError(f"Not a regular file or directory: {path}")
        if cls.is_directory == is_directory:
            type_ = cls
        elif is_directory:
            type_ = DirAsset
        else:
            raise ValueError(f"Not a directory: {path}")
        out = type_(path=path)
        out._trail = trail
        if trail is not None:
            out._parent = trail.assets
        return out

    @property
    def fingerprint(self) -> tuple[int, int, float]:
        """
        The file count, the total size in bytes, and the newest mtime. A file counts itself. A
        directory asset counts every file inside it, at any depth. It is computed on access, not on
        every event.

        >>> self.fingerprint
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
        Modification time in seconds since the epoch. A directory asset takes the newest file inside
        it.
        """
        return self.fingerprint[2]

    def move(self, destination: PathLike) -> Self:
        """
        Repoint a tracked asset at `destination`. It keeps its ID and its position in the
        collection.
        """
        collection = self._parent
        destination = Path(destination).expanduser().resolve()
        if destination == self.path:
            return self
        trail = self._trail
        if trail._is_ignored(destination):
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


@dataclass(kw_only=True, eq=False, repr=False)
class DirAsset(Asset):
    """
    A directory tracked as one dataset, such as a zarr store, a file geodatabase or a Sentinel
    product. Its contents are never walked. Files inside it whose names match `children` are part of
    it. Their events are counted into its access.
    """

    is_directory: bool = field(default=True, repr=False)
    # the root's (st_dev, st_ino), to follow a move the watchdog reports as a creation
    inode: tuple[int, int] | None = field(default=None, init=False, repr=False)

    @staticmethod
    def is_match(path: Path) -> bool:
        """
        Whether a directory holds one dataset, judged by its suffix.

        >>> DirAsset.is_match(Path('cube.zarr'))
        True
        """
        out = path.suffix.lower() in CHILDREN
        return out

    def track(self) -> Self:
        """Hold the dirasset in its collection, and take its root's inode. Nothing is logged."""
        super().track()
        self._take_inode()
        return self

    def move(self, destination: PathLike) -> Self:
        """Repoint the dirasset at `destination`, keeping its ID, and take its root's inode."""
        super().move(destination)
        self._take_inode()
        return self

    def _take_inode(self) -> None:
        """Take the inode of the root, or None while it is missing."""
        try:
            metadata = self.path.stat()
        except OSError:
            self.inode = None
            return
        self.inode = (metadata.st_dev, metadata.st_ino)

    @property
    def children(self) -> str:
        """
        The pattern that matches the names of files in the dirasset. A directory tracked with
        `track DIR` holds everything in it.

        >>> self.children
        '\\d+(?:\\.\\d+)*|zarr\\.json|\\.z(?:array|attrs|group|metadata)'
        """
        return CHILDREN.get(self.path.suffix.lower(), EVERYTHING)

    @property
    def access(self) -> Access | None:
        """
        The open access of the dirasset. An empty one is made if none is open. An empty access is
        never logged. None while the session is not started. A closed access stops changing. Read
        this again to get the current one.
        """
        session = self._trail.watchdog.session
        if not session:
            return None
        return session.accesses[self.id]

    def offtrail(self) -> None:
        """Log the dirasset's open access, then remove it from the collection."""
        collection = self._parent
        if (
            collection is not None
            and collection.id2entry.get(self.id) is self
        ):
            self._trail.watchdog.session.flush(self)
        Asset.offtrail(self)


def walk(
        walks: Mapping[Path, bool],
        descends: Callable[[Path], bool],
) -> Iterator[Path]:
    """
    Yields the files in each directory of `walks`, and in its subfolders if it maps to True. A
    subfolder is entered only if `descends` allows it. A dirasset is yielded as a candidate and
    never entered. Symlinks are skipped.
    """
    # shallowest first, so a directory inside one walked recursively is walked once, as part
    # of it
    ordered = sorted(
        walks,
        key=lambda root: len(root.parts),
    )
    kept: list[Path] = []
    for root in ordered:
        covered = any(
            walks[ancestor]
            and root.is_relative_to(ancestor)
            for ancestor in kept
        )
        if not covered:
            kept.append(root)
    for root in kept:
        if not root.is_dir():
            continue
        if not walks[root]:
            with os.scandir(root) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    if entry.is_symlink():
                        continue
                    if (
                        entry.is_file()
                        or DirAsset.is_match(path)
                    ):
                        yield path
            continue
        for directory, dirnames, filenames in os.walk(root):
            parent = Path(directory)
            descended = []
            for name in dirnames:
                path = parent / name
                if DirAsset.is_match(path):
                    if not path.is_symlink():
                        yield path
                elif descends(path):
                    descended.append(name)
            # pruned in place so that os.walk does not descend
            dirnames[:] = descended
            for name in filenames:
                path = parent / name
                if not path.is_symlink():
                    yield path


class Assets(Entries[Asset]):
    """
    A collection of Asset objects tracked by a Trail, in the order they were tracked.

    >>> trail: Trail
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
        Offtrail everything the collection holds. The removals are recorded like any other, so a
        replay of the log does not bring the assets back.
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
        Track files that `.markers` picks up and the Trail does not track yet. A file in `paths` is
        checked alone. A directory in `paths` is walked. With no paths, every Dir is walked. A
        recursive Dir is walked at any depth. Any other Dir is walked for its own files only.
        Returns the assets newly tracked.

        >>> project: Path
        >>> self.scan(project / 'later.csv')
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
                    and not DirAsset.is_match(path)
                ):
                    walks.update(self._reach(path))
                else:
                    candidates.append(path)
        else:
            for directory in dirs:
                walks[directory.path] = directory.is_recursive
        candidates.extend(walk(walks, self._descends))
        marked = {
            path: dirs.match(path)
            for path in dict.fromkeys(candidates)
            if self._is_markable(path)
        }
        if not marked:
            return ()
        return tuple(trail._track(marked))

    def _reach(self, path: Path) -> dict[Path, bool]:
        """
        Returns the directories a scan of `path` walks, each mapped to whether the walk is
        recursive. It is recursive if a recursive Dir covers the path.

        >>> project: Path
        >>> self._reach(project / 'data')
        {PosixPath('/tmp/tmpp_bl05lq/data'): True}
        """
        dirs = self._trail.dirs
        path2dir = dirs.path2entry
        recursive = any(
            path2dir[parent].is_recursive
            for parent in (path, *path.parents)
            if parent in path2dir
        )
        # a Dir below the path is walked as deep as it reaches
        below = {
            directory.path: directory.is_recursive
            for directory in dirs
            if directory.path != path
            and directory.path.is_relative_to(path)
        }
        return {path: recursive} | below

    def holder(self, path: Path) -> Path | None:
        """
        Returns the root of the dirasset that contains the path, or None. The nearest dirasset above
        the path contains it if the path's name matches the dirasset's children. A directory with a
        dirasset suffix counts even if it is not tracked.

        >>> project: Path
        >>> self.holder(project / 'raw.zarr' / '0.0')
        PosixPath('/tmp/tmpp_bl05lq/raw.zarr')
        >>> self.holder(project / 'raw.zarr' / 'notes.md')
        """
        path2entry = self.path2entry
        for parent in path.parents:
            asset = path2entry.get(parent)
            if isinstance(asset, DirAsset):
                pattern = asset.children
            elif DirAsset.is_match(parent):
                pattern = CHILDREN[parent.suffix.lower()]
            else:
                continue
            if re.fullmatch(pattern, path.name, re.IGNORECASE):
                return parent
            return None
        return None

    def is_child(self, path: Path) -> bool:
        """Whether the path is part of a dirasset. `holder` returns that dirasset's root."""
        out = self.holder(path) is not None
        return out

    def _is_markable(self, path: Path) -> bool:
        """
        Whether a scan should track a path. The path must not be tracked or offtrailed, and must not
        be Trail metadata or part of a dirasset. `.markers` must decide it. It must be a file or a
        dirasset.
        """
        trail = self._trail
        # the stat comes last, since most candidates fail before it
        if (
            path in self.path2entry
            or path in trail._offtrailed_paths
            or trail._is_ignored(path)
            or not trail.dirs.tracks(path)
            or self.is_child(path)
        ):
            return False
        if path.is_file():
            return True
        return DirAsset.is_match(path) and path.is_dir()


    def _descends(self, path: Path) -> bool:
        """
        Whether a walk enters a directory. Symlinks, Trail metadata, offtrailed paths and
        directory assets are never entered. A hidden directory is entered only if a Dir sits at
        or below it.

        >>> project: Path
        >>> self._descends(project / '.git')
        False
        """
        trail = self._trail
        if (
            path.is_symlink()
            or trail._is_ignored(path)
            or path in trail._offtrailed_paths
            or DirAsset.is_match(path)
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
        if not path.name.startswith('.'):
            return True
        return any(
            directory.path.is_relative_to(path)
            for directory in trail.dirs
        )
