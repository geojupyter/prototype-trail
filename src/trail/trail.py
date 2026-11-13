from __future__ import annotations

import json
import os
import warnings
from functools import cached_property
from itertools import islice, takewhile
from pathlib import Path
from uuid import uuid4

from trail import fmt
from trail.asset import Asset, Assets
from trail.dir import Dirs
from trail.entries import Entries
from trail.event import Events, WatchdogEvent
from trail.jupyter import Jupyter
from trail.lock import Lock
from trail.metadata.baselines import Baselines
from trail.node import Node
from trail.offtrail import OffTrail
from trail.ontrail import OnTrail, Scopes
from trail.util import PathLike, normalize_id
from trail.watchdog import Watchdog


class JSON(Node):
    _parent: Trail

    def __repr__(self) -> str:
        return fmt.asset(type(self).__name__, self.path)

    @property
    def record(self) -> dict:
        trail = self._parent
        out = {
            "id": trail.id,
        }
        return out

    def dump(self):
        """Dump the trail metadata to the JSON file."""
        path = self.path
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w") as f:
            json.dump(self.record, f)

    def load(self):
        """Load the trail metadata from the JSON file."""
        path = self.path
        if path is None or not path.exists():
            return
        with path.open("r") as f:
            data = json.load(f)
        trail = self._parent
        for key, value in data.items():
            if key == "id":
                value = normalize_id(value)
            self._setnested(trail, key, value)

    @cached_property
    def path(self):
        """Returns the path of the trail metadata file, or None if the Trail has no directory."""
        trail = self._trail
        if trail.dir:
            return trail.dir / "project.json"
        else:
            return None


class Trail(Node):
    """
    The record of a project. It holds the assets the project tracks, the Dirs `.ontrail`
    names, and the events that happened to both. The `.ontrail` file says which directories
    are watched and which files in them are tracked automatically. The `.offtrail` file says
    what is never tracked automatically.

    >>> trail: Trail
    >>> trail
    Trail
        id: 7cdc235fe92941d492b5c5f348484da3
        dir: /tmp/tmpp_bl05lq/.trail
        dirs: [
            /tmp/tmpp_bl05lq,
            /tmp/tmpp_bl05lq/data,
        ]
        assets: [
            /tmp/tmpp_bl05lq/new.csv,
            /tmp/tmpp_bl05lq/data/a.png,
        ]
    """

    # paths listed by __repr__ before the remainder is summarized
    repr_limit = 10

    @cached_property
    def jupyter(self) -> Jupyter:
        out = Jupyter(self)
        return out

    @cached_property
    def lock(self) -> Lock:
        out = Lock(self)
        return out

    @cached_property
    def baselines(self) -> Baselines:
        """The last observed state of each asset, kept for the next run."""
        out = Baselines(self)
        return out

    @cached_property
    def watchdog(self):
        """
        Returns a Watchdog instance that wraps the `watchdog` library to monitor filesystem events.

        >>> self.watchdog
        Watchdog
            running: True
            watches: [
                /tmp/tmpp_bl05lq,
                /tmp,
                /tmp/tmpp_bl05lq/data,
            ]
        """
        return Watchdog(self)

    @cached_property
    def assets(self):
        """
        Returns an Assets instance that maps IDs and paths to the tracked assets.

        >>> self.assets
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
        return Assets(self)

    @cached_property
    def dirs(self) -> Dirs:
        """
        Returns a Dirs instance that holds a Dir for each directory `.ontrail` names, by id and by
        path, and the root scopes. `Dirs.refresh` keeps them in line with `ontrail`. It runs after
        the log is replayed, after each change to `ontrail`, and when a directory asset is tracked
        or untracked. Each Dir that comes or goes is logged.

        >>> self.dirs
        Dirs (2)
            0. Dir
                id: 04e15251538241309989553fb4672653
                path: /tmp/tmpp_bl05lq
                root: True
                scopes: []
            1. Dir
                id: 3a654b91eb2047b3a2718d7f1d6a03e1
                path: /tmp/tmpp_bl05lq/data
                root: False
                scopes: [
                    ./*.png,
                ]
        """
        return Dirs(self)

    @property
    def scopes(self) -> Scopes:
        """
        Returns the root scopes. These are the rules of `.ontrail` with no directory part, such as
        `*.csv`. They apply at any depth beneath a root. They are built with `dirs`.

        >>> self.scopes
        Scopes (1)
            *.csv
        """
        return self.dirs.scopes

    @cached_property
    def entries(self) -> Entries:
        """
        Returns an Entries instance that looks up the assets and the Dirs together, by id or by
        path. It also holds the paths removed with `offtrail()`, and the checks that a scan, a walk
        and the watchdog apply to a path.
        """
        out = Entries(self)
        return out

    @cached_property
    def json(self):
        """Returns a JSON instance that reads and writes the Trail's metadata file."""
        return JSON(self)

    @cached_property
    def ontrail(self) -> OnTrail:
        """
        Returns an OnTrail instance that holds the rules of `.ontrail`. The rules name the
        directories to watch and the files to track in them. Calling it tracks paths immediately.

        >>> self.ontrail
        OnTrail (3)
            ./
            *.csv
            data/*.png
        """
        out = OnTrail(self)
        out.txt.read()
        return out

    @cached_property
    def offtrail(self) -> OffTrail:
        """
        Returns an OffTrail instance that holds the rules of `.offtrail`. The rules say what is
        excluded from automatic tracking before `.ontrail` is checked. Calling it removes assets.
        """
        out = OffTrail(self)
        out.txt.read()
        return out

    @cached_property
    def events(self):
        """
        Returns an Events instance that holds the events about the Trail's assets and Dirs, in the
        order they happened. A new Trail logs a Dir for each directory `.ontrail` names, then the
        assets its rules take.

        >>> self.events.select[1:3]
        Events (2)
            0. AddDirEvent
                id: 55d70cabf19f44b286114704794b1805
                timestamp: 02:48:26.945
                src_path: /tmp/tmpp_bl05lq/data
                rule: data/*.png
            1. AddAssetEvent
                id: f9b785f599e641ad8781eb615898eeef
                timestamp: 02:48:26.945
                src_path: /tmp/tmpp_bl05lq/new.csv
                rule: *.csv
                st_size: 17 B
                st_mtime: 2026-10-06 19:48:26
        """
        return Events(self)

    def __init__(
        self,
        dir: PathLike | None = None,
        ontrail: os.PathLike[str] | str | None = "default",
    ):
        """
        Open the Trail for a project directory, creating it if it does not exist yet.

        dir:
            The project directory, or its .trail directory. If None, the Trail is
            not saved to disk.

            The directory and each of its parents are searched for a .trail. The first one found
            is opened. If none is found, .trail is created in dir. The search does not climb into
            a directory listed in TRAIL_CEILING_DIRECTORIES.

        ontrail:
            The preset a new `.ontrail` starts from. Ignored when the Trail already exists on file.

            str:
                The name of a preset, e.g. 'default'.
            Path:
                A file of rules on disk.
            None:
                Start without rules, so nothing is tracked until a rule is added or a path is
                tracked.

        The presets start with `./`, which watches the project directory.
        """
        super().__init__()
        if dir is None:
            self.dir = None
            self.offtrail.default()
        else:
            dir = Path(dir).expanduser().resolve()
            if dir.name != ".trail":
                found = self.locate(dir)
                if found is None:
                    dir = dir / ".trail"
                else:
                    dir = found
            self.dir = dir
            created = not self.json.path.exists()
            if created and ontrail is not None:
                self.ontrail.txt.create(ontrail)
            # unlike .ontrail, a missing .offtrail is written on every open
            self.offtrail.default()
            self.json.load()
            self.events.jsonl.read()
            self.json.dump()
            if created:
                self._log_init()
            # the replay rebuilt the Dirs that were live, and .ontrail may have changed since
            self.dirs.refresh()
            self.assets.scan()
            self.watchdog.session.reconcile()

    def _log_init(self) -> None:
        """Log the start of a new project, with the rules it starts with."""
        from trail.event import InitTrailEvent

        ontrail = [
            str(rule)
            for rule in self.ontrail
        ]
        offtrail = [
            str(rule)
            for rule in self.offtrail
        ]
        event = InitTrailEvent(
            trail_id=self.id,
            root=str(self.dir.parent),
            ontrail=ontrail,
            offtrail=offtrail,
        )
        event.apply(self)

    @staticmethod
    def locate(dir: PathLike) -> Path | None:
        """The .trail a Trail opened on `dir` would open, or None if it would create one."""
        dir = Path(dir).expanduser().resolve()
        # like GIT_CEILING_DIRECTORIES, the search never climbs into a ceiling
        configured = os.environ.get("TRAIL_CEILING_DIRECTORIES", "")
        ceilings = {
            Path(ceiling)
            .expanduser()
            .resolve()
            for ceiling in configured.split(os.pathsep)
            if ceiling
        }
        parents = takewhile(
            lambda parent: parent not in ceilings,
            dir.parents,
        )
        # like git, the nearest enclosing .trail wins
        it = (
            parent / ".trail"
            for parent in (dir, *parents)
            if (parent / ".trail").is_dir()
        )
        return next(it, None)

    @cached_property
    def id(self) -> str:
        """
        Identifies the Trail with a UUID4 hex string. It is computed lazily, so `self.json.load()`
        can set it first.
        """
        return uuid4().hex

    def _moved(
        self,
        source: Path,
        destination: Path,
    ) -> list[Asset]:
        """
        Repath every asset under a renamed directory, keeping its ID. Each repath is logged as
        a moved event, so the log replays it. Warns when a Dir under the directory no longer
        exists. The rules of `.ontrail` are not rewritten to follow the rename.
        """
        moved = []
        for asset in self.assets:
            if (
                asset.path == source
                or not asset.path.is_relative_to(source)
            ):
                continue
            target = destination / asset.path.relative_to(source)
            event = WatchdogEvent(
                src_path=str(asset.path),
                dest_path=str(target),
                event_type="moved",
                is_directory=asset.is_directory,
                is_synthetic=True,
            )
            if event.apply(self) is not None:
                moved.append(asset)
        for directory in self.dirs:
            path = directory.path
            if (
                path.is_relative_to(source)
                and not path.exists()
            ):
                target = destination / path.relative_to(source)
                message = f"{path} is named by .ontrail but was moved to {target}"
                warnings.warn(message, stacklevel=2)
        return moved

    def clear(self) -> None:
        """
        Empties the project record. Every asset is untracked, then the log is discarded. The Dirs
        stay, since `.ontrail` still names them. `Events.clear` logs them again with their IDs. The
        Trail keeps its own id, so the directory stays the same project.
        """
        self.assets.clear()
        self.events.clear()

    def push(self):
        """Placeholder for possible remote synchronization"""

    def pull(self):
        """Placeholder for possible remote synchronization"""

    def __repr__(self) -> str:
        """Shows the id, the trail directory, and the paths of the Dirs and the assets."""
        if self.dir is None:
            directory = None
        else:
            directory = str(self.dir)
        lines = [
            type(self).__name__,
            f"    id: {fmt.bare(self.id)}",
            f"    dir: {fmt.bare(directory)}",
        ]
        dirs = self.dirs
        shown = (
            str(directory.path)
            for directory in islice(dirs, self.repr_limit)
        )
        lines.extend(fmt.bracketed("dirs", shown, len(dirs)))
        assets = self.assets
        shown = (
            str(asset.path)
            for asset in islice(assets, self.repr_limit)
        )
        lines.extend(fmt.bracketed("assets", shown, len(assets)))
        return "\n".join(lines)
