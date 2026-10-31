from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, overload

from trail import fmt
from trail.asset import DirAsset
from trail.node import Node

if TYPE_CHECKING:
    from trail.entry import Entry, EntryKey
    from trail.trail import Trail


class Entries(Node):
    """
    The assets and the Dirs of a Trail, looked up together by id or by path. It holds no entries.
    Each call reads `trail.assets` and `trail.dirs`. It also holds the checks that a scan, a walk
    and the watchdog apply to a path.
    """

    _parent: Trail

    def __init__(self, parent: Trail | None = None) -> None:
        Node.__init__(self, parent)
        # the paths removed with `OffTrail.__call__`. A scan does not pick them up again
        self.untracked: set[Path] = set()

    @overload
    def __getitem__(self, key: EntryKey) -> Entry: ...

    @overload
    def __getitem__(self, key: Iterable[EntryKey]) -> tuple[Entry, ...]: ...

    def __getitem__(
            self,
            key: EntryKey | Iterable[EntryKey],
    ) -> Entry | tuple[Entry, ...]:
        """
        Returns the asset or the Dir with an id or at a path. The assets are tried first. Several
        keys return a tuple. Raises KeyError for a key that is neither.
        """
        if not isinstance(key, (str, os.PathLike)):
            selected = []
            for value in key:
                if not isinstance(value, (str, os.PathLike)):
                    raise TypeError("Expected a path or an ID")
                selected.append(self[value])
            out = tuple(selected)
            return out
        trail = self._trail
        for collection in (trail.assets, trail.dirs):
            entry = collection.get(key)
            if entry is not None:
                return entry
        raise KeyError(key)

    @overload
    def get(self, key: EntryKey) -> Entry | None: ...

    @overload
    def get[D](
            self,
            key: EntryKey,
            default: D,
    ) -> Entry | D: ...

    def get[D](
            self,
            key: EntryKey,
            default: D | None = None,
    ) -> Entry | D | None:
        """Returns the asset or the Dir with an id or at a path, or `default`."""
        try:
            out = self[key]
        except KeyError:
            return default
        return out

    def __contains__(self, key: EntryKey | Entry) -> bool:
        """Whether an entry is held, or an id or a path names an asset or a Dir."""
        trail = self._trail
        out = (
            key in trail.assets
            or key in trail.dirs
        )
        return out

    def __iter__(self) -> Iterator[Entry]:
        """Yields the Dirs, then the assets, each in the order it was added."""
        trail = self._trail
        # two `yield from` would allow assets to change before iterating; unpredictable behavior
        records = [
            *trail.dirs,
            *trail.assets,
        ]
        out = iter(records)
        return out

    def __len__(self) -> int:
        trail = self._trail
        out = len(trail.dirs) + len(trail.assets)
        return out

    def __repr__(self) -> str:
        """Lists the Dirs, then the assets, as a collection's repr does."""
        out = fmt.items(type(self).__name__, self)
        return out

    def is_metadata(self, path: Path) -> bool:
        """
        Whether a path is Trail metadata: `.trail` or anything inside it, `.ontrail`, or
        `.offtrail`. A Trail without a directory has none.
        """
        trail = self._trail
        directory = trail.dir
        if directory is None:
            return False
        files = (
            trail.ontrail.txt.path,
            trail.offtrail.txt.path,
        )
        out = (
            path.is_relative_to(directory)
            or path in files
        )
        return out

    def is_trackable(self, path: Path) -> bool:
        """
        Whether a scan should track a path. It must be a file or a dirasset that `.ontrail` takes.
        It is False for these, even if `.ontrail` takes them:

        - A path Trail already tracks.
        - A path in `untracked`.
        - Trail metadata.
        - A path `.offtrail` ignores.
        - A path that is part of a dirasset.

        `Dirs.tracks` says whether `.ontrail` alone takes a path.
        """
        trail = self._trail
        assets = trail.assets
        if (
            path in assets.path2entry
            or path in self.untracked
            or self.is_metadata(path)
            or not trail.dirs.tracks(path)
            or assets.is_child(path)
        ):
            return False
        directory = (
            DirAsset.is_match(path)
            and path.is_dir()
        )
        if trail.offtrail.ignores(path, directory=directory):
            return False
        out = directory or path.is_file()
        return out

    def is_walkable(self, path: Path) -> bool:
        """
        Whether a walk enters a directory. Symlinks, Trail metadata, paths in `untracked` and
        directory assets are never entered. A directory `.offtrail` ignores, such as `.git`, is
        entered only if a Dir sits at or below it.

        >>> project: Path
        >>> self.is_walkable(project / '.git')
        False
        """
        trail = self._trail
        if (
            path.is_symlink()
            or self.is_metadata(path)
            or path in self.untracked
            or DirAsset.is_match(path)
        ):
            return False
        asset = trail.assets.path2entry.get(path)
        # the inside of a directory asset is never walked
        if (
            asset is not None
            and asset.is_directory
        ):
            return False
        # a Dir below an ignored directory is reached through it, since a walk skips a root that
        # a recursive root above it covers
        if not trail.offtrail.ignores(path, directory=True):
            return True
        out = any(
            directory.path.is_relative_to(path)
            for directory in trail.dirs
        )
        return out
