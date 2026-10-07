from __future__ import annotations

import json
import os
import warnings
from collections import Counter
from collections.abc import Iterable, Mapping
from functools import cached_property
from itertools import islice, takewhile
from pathlib import Path
from uuid import uuid4

from trail.asset import Asset, Assets
from trail.dir import Dirs
from trail.entry import Entry
from trail.event import AddAssetEvent, Events, RemoveAssetEvent, WatchdogEvent
from trail.markers import Markers, Scopes, canonical, parse
from trail.node import Node
from trail.util import PathLike, asset_repr, bare_repr, list_repr, normalize_id, plural
from trail.watchdog import Watchdog


class JSON(Node):
    _parent: Trail

    def __repr__(self) -> str:
        return asset_repr(type(self).__name__, self.path)

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
        """Determines the trail metadata file path based on the trail directory."""
        trail = self._trail
        if trail.dir:
            return trail.dir / "project.json"
        else:
            return None


class Trail(Node):
    """
    The record of a project. It holds the assets the project tracks, the Dirs `.markers`
    names, and the events that happened to both. The `.markers` file says which directories
    are watched and which files in them are tracked automatically.

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
    def watchdog(self):
        """
        Returns a Watchdog instance, which wraps the `watchdog` library's functionality
        for monitoring filesystem events.

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
        Returns an Assets instance, which maps IDs and paths to the assets tracked by the Trail.

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
        Returns a Dirs instance, which holds a Dir for each directory `.markers` names, by id
        and by path, and the root scopes. `Dirs.refresh` keeps them in line with `markers`
        after the log is replayed, after each change to `markers`, and when a directory asset
        is tracked or offtrailed. Each Dir that comes or goes is logged.

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
        Returns the root scopes. These are the lines of `.markers` without a directory part,
        such as `*.csv`, and they apply at any depth beneath a root. They are built with `dirs`.

        >>> self.scopes
        Scopes (1)
            *.csv
        """
        return self.dirs.scopes

    @cached_property
    def _offtrailed_paths(self) -> set[Path]:
        """The paths offtrailed in the log. `.markers` does not pick them up again."""
        return set()

    @cached_property
    def json(self):
        """Returns a JSON instance, which functions as a namespace for the Trail's metadata stored in a JSON file."""
        return JSON(self)

    @cached_property
    def markers(self) -> Markers:
        """
        Returns a Markers instance, which holds the lines of `.markers`. They name the
        directories to watch and the files to track in them.

        >>> self.markers
        Markers (3)
            ./
            *.csv
            data/*.png
        """
        out = Markers(self)
        out.text.read()
        return out

    @cached_property
    def events(self):
        """
        Returns an Events instance, which holds the events about the Trail's assets and Dirs,
        in the order they happened. A new Trail first logs a Dir for each directory `.markers`
        names, then the assets the markers pick up.

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
        markers: os.PathLike[str] | str | None = "default",
    ):
        """
        Open the Trail for a project directory, creating it if it does not exist yet.

        dir:
            The project directory, or its .trail directory. If None, the Trail is
            not saved to disk.

            The directory and then each of its parents are searched for a .trail, and
            the first one found is opened. If none is found, .trail is created in dir.
            The search does not climb into a directory listed in TRAIL_CEILING_DIRECTORIES.

        markers:
            The markers to start a new Trail with. Ignored when the Trail already exists on file.

            str:
                The name of a markers file, e.g. 'default'.
            Path:
                A markers file on disk.
            None:
                Start without markers, so nothing is tracked until something is marked or
                tracked.

        The bundled markers files start with `./`, which watches the project directory.
        """
        super().__init__()
        if dir is None:
            self.dir = None
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
            if created and markers is not None:
                self.markers.text.create(markers)
            self.json.load()
            self.events.jsonl.read()
            self.json.dump()
            # the replay rebuilt the Dirs that were live, and .markers may have changed since
            self.dirs.refresh()
            self.assets.scan()
            self.watchdog.reconcile()

    @staticmethod
    def locate(dir: PathLike) -> Path | None:
        """The .trail a Trail opened on `dir` would open, or None if it would create one."""
        dir = Path(dir).expanduser().resolve()
        # like GIT_CEILING_DIRECTORIES, the search never climbs into a ceiling
        ceilings = {
            Path(ceiling).expanduser().resolve()
            for ceiling in os.environ.get("TRAIL_CEILING_DIRECTORIES", "").split(os.pathsep)
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
        Assigns a unique identifier to the Trail instance using a UUID4 hex string.
        Performed as a lazy attribute so that `self.json.load()` may take precedence.
        """
        return uuid4().hex

    def track(self, *paths: PathLike) -> Asset | list[Asset]:
        """
        Track each path as an asset. A directory is tracked whole, as a directory asset, and its
        inside is never walked. One path returns its asset, and several return a list.

        >>> project: Path
        >>> self.track(project / 'notes.md')
        Asset
            id: 80438f85c910427d9001617c47b7507b
            path: /tmp/tmpp_bl05lq/notes.md
            st_size: 2 B
            st_mtime: 2026-10-02 23:39:37
        """
        requested = dict.fromkeys(
            Path(path).expanduser().resolve()
            for path in paths
        )
        new = any(
            path not in self.assets
            for path in requested
        )
        tracked = self._track(requested)
        if new:
            self.watchdog.reconcile()
        if len(paths) == 1:
            return tracked[0]
        return tracked

    def _track(self, requested: Mapping[Path, str | None]) -> list[Asset]:
        """
        Track each path, recording the line of `.markers` that picked it up, or None. Returns
        the assets, in the order requested.
        """
        for path in requested:
            if self._ignored(path):
                raise ValueError(f"Cannot track Trail metadata: {path}")
            if path not in self.assets:
                Asset.from_path(path, trail=self)
        tracked = []
        for path, rule in requested.items():
            asset = self.assets.get(path)
            if asset is None:
                event = AddAssetEvent(src_path=str(path), rule=rule)
                asset = event.apply(self)
                self.events.append(event)
                # a directory asset is never a Dir, so a Dir at its path goes
                if asset.is_directory:
                    self.dirs.refresh()
            tracked.append(asset)
        return tracked

    def _ignored(self, path: Path) -> bool:
        """Returns True if the path is relative to the `trail` directory, e.g. `/.trail/ignored"""
        return self.dir is not None and path.is_relative_to(self.dir)

    def offtrail(self, *paths: PathLike) -> Asset | list[Asset]:
        """Offtrails the specified filesystem paths from the Trail."""
        requested = dict.fromkeys(Path(path).expanduser().resolve() for path in paths)
        offtrailed = []
        for path in requested:
            asset = self.assets.get(path)
            if asset is None:
                continue
            event = RemoveAssetEvent(src_path=str(path))
            result = event.apply(self)
            if result is not None:
                self.events.append(event)
                offtrailed.append(result)
                # a Dir may be named at the path the directory asset held
                if result.is_directory:
                    self.dirs.refresh()
        if offtrailed:
            self.watchdog.reconcile()
        if len(paths) == 1 and offtrailed:
            return offtrailed[0]
        return offtrailed

    def mark(
        self,
        *texts: str,
        cwd: PathLike | None = None,
    ) -> list[str]:
        """
        Add a line to `.markers` for each text, then track the files the new lines indicate
        now. Each text is made canonical first, so 'csv' adds '*.csv'. Relative paths are read
        from `cwd`, the project directory by default, as the lines of `.markers` are. A line
        already in `.markers` is not added again. Returns the lines added.

        >>> self.mark('zarr')
        ['*.zarr']
        """
        lines = self._canonical(texts, cwd)
        project = self.markers.project
        # every line is checked before any is written
        bases = {
            line: parse(line, project)[0]
            for line in lines
        }
        added = self.markers.add(*lines)
        if not added:
            return []
        reached = [
            bases[line]
            for line in added
        ]
        # a root scope applies beneath every root, so every Dir is walked
        if None in reached:
            self.assets.scan()
        else:
            self.assets.scan(*reached)
        return added

    def unmark(
        self,
        *texts: str,
        cwd: PathLike | None = None,
    ) -> list[str]:
        """
        Remove the lines of `.markers` that each text stands for, compared in canonical form.
        Relative paths are read as `mark` reads them. This does not act retroactively. The
        assets a removed line matched stay tracked, and a warning says how many. `offtrail`
        removes them. Returns the lines removed.

        >>> self.unmark('.zarr')
        <stdin>:1: UserWarning: unmarking '*.zarr' leaves 1 asset tracked; offtrail removes it
        ['*.zarr']
        """
        markers = self.markers
        project = markers.project
        wanted = set(self._canonical(texts, cwd))
        removing = []
        for line in markers:
            try:
                form = canonical(line, project, project)
            except ValueError:
                form = line
            if (
                line in wanted
                or form in wanted
            ):
                removing.append(line)
        if not removing:
            return []
        matched = self._matched(removing)
        removed = markers.discard(*removing)
        for line in removed:
            count = matched[line]
            if not count:
                continue
            if count == 1:
                pronoun = "it"
            else:
                pronoun = "them"
            warnings.warn(
                f"unmarking {line!r} leaves {plural(count, 'asset')} tracked; "
                f"offtrail removes {pronoun}",
                stacklevel=2,
            )
        return removed

    def _canonical(
        self,
        texts: Iterable[str],
        cwd: PathLike | None,
    ) -> list[str]:
        """
        Returns the line of `.markers` each text stands for. Relative paths are read from
        `cwd`, the project directory by default, as the lines of `.markers` are.
        """
        project = self.markers.project
        if cwd is None:
            cwd = project
        cwd = Path(cwd).expanduser().resolve()
        out = [
            canonical(text, project, cwd)
            for text in texts
        ]
        return out

    def _matched(self, lines: list[str]) -> Counter[str]:
        """
        Returns how many tracked assets each line accounts for. A scope counts the assets it
        decides. A directory marker counts the assets beneath it.

        >>> self._matched(['./', '*.csv'])
        Counter({'./': 4, '*.csv': 1})
        """
        project = self.markers.project
        dirs = self.dirs
        decided = Counter(
            dirs.match(asset.path)
            for asset in self.assets
        )
        out: Counter[str] = Counter()
        for line in lines:
            try:
                base, pattern = parse(line, project)
            except ValueError:
                continue
            if (
                base is not None
                and not pattern
            ):
                out[line] = sum(
                    1
                    for asset in self.assets
                    if asset.path.is_relative_to(base)
                )
            else:
                out[line] = decided[line]
        return out

    def _moved(
        self,
        source: Path,
        destination: Path,
    ) -> list[Asset]:
        """
        Repath every asset under a renamed directory, keeping its ID. Each repath is logged as
        a moved event, so the log replays it. Warns when a Dir under the directory no longer
        exists. The lines of `.markers` are not rewritten to follow the rename.
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
                warnings.warn(
                    f"{path} is named by .markers but was moved to "
                    f"{destination / path.relative_to(source)}",
                    stacklevel=2,
                )
        return moved

    def clear(self) -> None:
        """
        Empties the project record. Every asset is offtrailed, and then the log is discarded.
        The Dirs stay, since `.markers` still names them, and `Events.clear` logs them again
        with their IDs. The Trail keeps its own id, so the directory remains the same project
        rather than becoming a new one.
        """
        self.assets.clear()
        self.events.clear()

    def _entry(self, identifier: str) -> Entry | None:
        """
        Returns the asset or the Dir with an id, or None if neither holds it.

        >>> self._entry('04e15251538241309989553fb4672653')
        Dir
            id: 04e15251538241309989553fb4672653
            path: /tmp/tmpp_bl05lq
            root: True
            scopes: []
        """
        asset = self.assets.id2entry.get(identifier)
        if asset is not None:
            return asset
        return self.dirs.id2entry.get(identifier)

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
            f"    id: {bare_repr(self.id)}",
            f"    dir: {bare_repr(directory)}",
        ]
        dirs = self.dirs
        shown = (
            str(directory.path)
            for directory in islice(dirs, self.repr_limit)
        )
        lines.extend(list_repr("dirs", shown, len(dirs)))
        assets = self.assets
        shown = (
            str(asset.path)
            for asset in islice(assets, self.repr_limit)
        )
        lines.extend(list_repr("assets", shown, len(assets)))
        return "\n".join(lines)
