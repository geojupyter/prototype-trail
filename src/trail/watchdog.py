from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from trail import fmt
from trail.node import Node
from trail.session import Session

if TYPE_CHECKING:
    from trail.trail import Trail


class Watchdog(Node):
    """
    Decides which directories `session` watches, from the Trail's Dirs and assets. Every Dir and
    every dirasset is watched recursively. The parent of each Dir and asset is watched alone. The
    handler drops what a recursive watch sees inside `.git` and `.venv`, which a walk never enters.

    >>> trail: Trail
    >>> trail.watchdog
    Watchdog
        running: True
        watches: [
            /tmp/tmpp_bl05lq,
            /tmp,
        ]
    """

    _parent: Trail
    # directories listed by __repr__ before the remainder is summarized
    repr_limit = 10
    session = Session.descriptor()

    def __repr__(self) -> str:
        """
        Shows whether the session is active, and its watches. While it is not started, it shows
        the watches the next run will place.
        """
        session = self.session
        if session:
            directories = list(session.watches)
        else:
            directories = sorted(self.desired())
        lines = [
            type(self).__name__,
            f'    running: {self.session.is_active!r}',
        ]
        shown = (
            str(directory)
            for directory in directories[:self.repr_limit]
        )
        rendered = fmt.bracketed('watches', shown, len(directories))
        lines.extend(rendered)
        return '\n'.join(lines)

    def wanted(self) -> dict[Path, bool]:
        """
        Returns every directory that would be watched, each mapped to whether its watch is
        recursive, before `desired` leaves any out.
        """
        trail = self._trail
        out: dict[Path, bool] = {}
        for directory in trail.dirs:
            out[directory.path] = True
        for asset in trail.assets:
            if asset.is_directory:
                out[asset.path] = True
        dir_parents = [
            directory.path.parent
            for directory in trail.dirs
        ]
        asset_parents = [
            asset.directory
            for asset in trail.assets
        ]
        for parent in (*dir_parents, *asset_parents):
            out.setdefault(parent, False)
        return out

    def desired(self) -> dict[Path, bool]:
        """
        Returns the directories to watch, each mapped to whether its watch is recursive. Every Dir
        and every dirasset is watched recursively. The parent of each Dir and of each asset is
        watched alone, to see the Dir or asset renamed or deleted. A directory inside a recursive
        watch gets no watch of its own, since the recursive watch already sees it. Trail metadata
        and missing directories are left out.

        >>> self.desired()
        {PosixPath('/tmp/tmpp_bl05lq'): True, PosixPath('/tmp'): False}
        """
        trail = self._trail
        wanted = self.wanted()
        roots = {
            path
            for path, recursive in wanted.items()
            if recursive
        }
        return {
            path: recursive
            for path, recursive in wanted.items()
            if not any(
                parent in roots
                for parent in path.parents
            )
            and not trail._is_ignored(path)
            and path.is_dir()
        }

    def covers(self, directory: Path) -> bool:
        """
        Whether a directory is watched, directly or through a recursive watch above it. While
        the session is not started, it answers for the watches the next run will place.

        >>> project: Path
        >>> self.covers(project / 'data')
        True
        """
        if not directory.is_dir():
            return False
        session = self.session
        if session:
            watches = session.watches
        else:
            watches = self.desired()
        if directory in watches:
            return True
        return any(
            watches.get(parent, False)
            for parent in directory.parents
        )
