from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, fields
from functools import cached_property
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import TYPE_CHECKING, Self, overload
from uuid import uuid4

from trail.collection import Collection
from trail.node import Node
from trail.util import (
    MISSING,
    PathLike,
    bare_repr,
    container,
    mtime_repr,
    normalize_id,
    st_size_repr,
    walk,
)

if TYPE_CHECKING:
    from trail.event import Event
    from trail.trail import Trail

# an asset is addressed by its hexadecimal ID or by its path
AssetKey = PathLike


@dataclass(kw_only=True, eq=False, repr=False)
class Asset(Node):
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
    id: str = field(default_factory=lambda: uuid4().hex)
    path: Path
    is_directory: bool = field(default=False, repr=False)

    if TYPE_CHECKING:
        _parent: Assets

    def __post_init__(self) -> None:
        """Normalize the id and resolve the path."""
        Node.__init__(self)
        self.id = normalize_id(self.id)
        self.path = Path(self.path).expanduser().resolve()

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        """
        Yields the fields a repr shows. The size and mtime are read from the filesystem, and a
        directory asset also shows how many files it holds.
        """
        for asset_field in fields(self):
            if not asset_field.repr:
                continue
            value = getattr(self, asset_field.name)
            if asset_field.name == 'path':
                value = str(value)
            yield asset_field.name, value
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

    def __repr__(self) -> str:
        """Shows the id, the path, and the size and mtime on disk."""
        lines = [type(self).__name__]
        lines.extend(
            f'    {name}: {bare_repr(value)}'
            for name, value in self._repr_items()
        )
        return '\n'.join(lines)

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
    def name(self) -> str:
        """Name of the asset (last component of the path)."""
        return self.path.name

    @property
    def directory(self) -> Path:
        """Directory containing the asset."""
        return self.path.parent

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

    @cached_property
    def events(self) -> dict[str, Event]:
        """Events associated with the asset."""
        return {}

    def track(self) -> Self:
        """
        Add the asset to the Trail's assets and return it. Nothing is logged. An older asset
        with the same id, or another asset at the same path, is offtrailed first.
        """
        collection = self._parent
        if collection is None:
            raise ValueError("Asset has no Trail; pass trail to from_path")
        trail = collection._trail
        if self._trail is not trail:
            raise ValueError("Asset already belongs to another Trail")
        if collection is not trail.assets:
            raise ValueError("Asset belongs to the wrong collection")
        if trail._ignored(self.path):
            raise ValueError(f"Cannot track Trail metadata: {self.path}")
        previous = collection.id2asset.get(self.id)
        # a path holds one asset
        occupant = collection.path2asset.get(self.path)
        for old in (previous, occupant):
            if (
                old is None
                or old is self
            ):
                continue
            old.offtrail()
        collection[self.id] = self
        return self

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
        occupant = collection.path2asset.get(destination)
        if (
            occupant is not None
            and occupant is not self
        ):
            occupant.offtrail()
        collection.repath(self, previous)
        return self

    def offtrail(self) -> None:
        """Offtrail the asset, removing it from the collection holding it."""
        collection = self._parent
        if (
            collection is None
            or collection.id2asset.get(self.id) is not self
        ):
            return
        del collection[self.id]


class Assets(Collection[AssetKey, Asset]):
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

    @cached_property
    def path2asset(self) -> dict[Path, Asset]:
        """
        Maps each path to its asset. It is built from `data` rather than kept beside it, so a
        selection builds its own.
        """
        return {
            asset.path: asset
            for asset in self.data.values()
        }

    @property
    def id2asset(self) -> dict[str, Asset]:
        """Maps each id to its asset. This is `data` itself."""
        return self.data

    @overload
    def __getitem__(self, key: AssetKey) -> Asset: ...

    @overload
    def __getitem__(self, key: Iterable[AssetKey]) -> tuple[Asset, ...]: ...

    def __getitem__(
            self,
            key: AssetKey | Iterable[AssetKey],
    ) -> Asset | tuple[Asset, ...]:
        """
        Returns the asset with an id or at a path. Several keys return a tuple. Raises KeyError
        for a key that is neither.

        >>> trail.assets[project / 'new.csv']
        Asset
            id: 9d167ab8f5f64f3f8ac72f3132c70ad1
            path: /tmp/tmpp_bl05lq/new.csv
            st_size: 17 B
            st_mtime: 2026-10-02 23:39:37
        """
        if isinstance(key, str) and key in self.id2asset:
            return self.id2asset[key]
        if isinstance(key, (str, os.PathLike)):
            return self.path2asset[Path(key).expanduser().resolve()]
        selected = []
        for value in key:
            if not isinstance(value, (str, os.PathLike)):
                raise TypeError("Expected a path or asset ID")
            selected.append(self[value])
        return tuple(selected)

    def __setitem__(
            self,
            key: str,
            value: Asset,
    ) -> None:
        """Hold an asset under its id and its path. The key must be the asset's id."""
        if not isinstance(value, Asset):
            raise TypeError(f"Expected Asset, got {type(value).__name__}")
        if key != value.id:
            raise ValueError(f"Asset ID does not match its key: {key}")
        if key not in self.id2asset:
            self.ids.append(key)
        self.id2asset[key] = value
        self.path2asset[value.path] = value

    def __delitem__(self, key: AssetKey) -> None:
        """Remove an asset without logging it. `Trail.offtrail` is what logs a removal."""
        asset = self[key]
        del self.id2asset[asset.id]
        self.ids.remove(asset.id)
        if self.path2asset.get(asset.path) is asset:
            del self.path2asset[asset.path]

    def repath(
            self,
            asset: Asset,
            previous: Path,
    ) -> None:
        """Move an asset from its previous path to its current one in `path2asset`."""
        if self.path2asset.get(previous) is asset:
            del self.path2asset[previous]
        self[asset.id] = asset

    def __contains__(self, key: AssetKey | Asset) -> bool:
        """
        Whether an asset, an id or a path is held.

        >>> project / 'new.csv' in trail.assets
        True
        """
        if isinstance(key, Asset):
            return key.id in self.id2asset
        if isinstance(key, str) and key in self.id2asset:
            return True
        return Path(key).expanduser().resolve() in self.path2asset

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
            for directory in dirs.values():
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
        recursive = any(
            dirs[parent].recursive
            for parent in (path, *path.parents)
            if parent in dirs
        )
        out = {path: recursive}
        # a Dir below the path is walked as deep as it reaches
        out.update(
            (directory.path, directory.recursive)
            for directory in dirs.values()
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
            path not in self.path2asset
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
        asset = self.path2asset.get(path)
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
                directory.is_relative_to(path)
                for directory in trail.dirs
            )
        )
