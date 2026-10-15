from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import suppress
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final, Self
from uuid import uuid4

from trail import fmt
from trail.asset import Asset, DirAsset
from trail.collection import Collection
from trail.node import Node
from trail.util import OPS, normalize_id

if TYPE_CHECKING:
    from trail.dir import Dir
    from trail.entry import Entry
    from trail.trail import Trail


EVENT_TYPES: Final = frozenset({"created", "modified", "deleted", "moved", "opened"})


class Get:
    """
    Attribute access that does not raise.

    event.src_path -> raises AttributeError
    getattr(event, 'src_path', None) -> returns None, but verbose and string literal
    event.get.src_path -> returns None, but concise and attribute access
    """
    __slots__ = ("_parent",)

    def __init__(self, parent: Event) -> None:
        self._parent = parent

    def __getattr__(self, name: str) -> object | None:
        """
        Returns the event's attribute, or None if the event has no such field.

        >>> trail: Trail
        >>> trail.events.select[2].get.rule
        '*.csv'
        >>> trail.events.select[2].get.event_type
        """
        return getattr(self._parent, name, None)


@dataclass(kw_only=True, slots=True, repr=False)
class Event:
    classes: ClassVar[dict[str, type[Event]]] = {}

    entry: Entry | None = field(default=None, init=False, repr=False)
    id: str = field(default_factory=lambda: uuid4().hex, repr=True)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        self.id = normalize_id(self.id)

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        """Yields the fields a repr shows. Every datetime is shown as a clock time."""
        for event_field in fields(self):
            if not event_field.repr:
                continue
            value = getattr(self, event_field.name)
            if isinstance(value, datetime):
                value = fmt.clock(value)
            if value is None or value == "":
                continue
            # the baseline is stored as the stat reported it; the units are put back here
            if event_field.name == "st_size":
                value = fmt.st_size(value)
            elif event_field.name == "st_mtime":
                value = fmt.mtime(value)
            yield event_field.name, value

    @property
    def get(self) -> Self | Get:
        return Get(self)

    def __repr__(self) -> str:
        attributes = ", ".join(f"{name}={value}" for name, value in self._repr_items())
        return f"{type(self).__name__}({attributes})"

    def apply(
        self,
        trail: Trail,
        *,
        replay: bool = False,
    ) -> Entry | None:
        """
        Apply the event to the Trail and return the entry it is about, an asset or a Dir. With
        `replay`, the event comes from the log and its entry is already resolved. Each subclass
        implements it.
        """
        raise NotImplementedError

    def __init_subclass__(cls, **kwargs) -> None:
        # slots=True replaces the class captured by zero-argument super()
        super(Event, cls).__init_subclass__(**kwargs)
        cls.classes[cls.__name__] = cls

    @classmethod
    def from_record(cls, /, trail: Trail, resolved_entry: Entry | None = None, **record) -> Event:
        """
        Rebuild an event from one record of the log. The entry it names is `resolved_entry` if
        given, otherwise the asset or the Dir with the record's entry id.
        """
        name = record.pop("cls")
        event_cls = cls.classes.get(name)

        if name == cls.__name__:
            event_cls = cls
        if event_cls is None:
            raise ValueError(f"Unknown event class: {name!r}")
        entry_id = normalize_id(record.pop("entry"))
        record["timestamp"] = datetime.fromisoformat(record["timestamp"])
        deferred = {
            field.name: record.pop(field.name)
            for field in fields(event_cls)
            if not field.init and field.name in record
        }
        event = event_cls(**record)
        for name, value in deferred.items():
            setattr(event, name, value)

        if resolved_entry is None:
            resolved_entry = trail._entry(entry_id)
            if resolved_entry is None:
                raise KeyError(entry_id)
        elif resolved_entry.id != entry_id:
            raise ValueError(f"Entry ID does not match event record: {entry_id}")
        event.entry = resolved_entry

        return event

    def to_record(self) -> dict:
        """
        Returns the event as one record of the log. The entry is held by its id, and every
        datetime in ISO format.
        """
        out = {}
        for event_field in fields(self):
            name = event_field.name
            if name == "entry":
                continue
            value = getattr(self, name)
            if isinstance(value, datetime):
                value = value.isoformat()
            out[name] = value
        out["cls"] = type(self).__name__
        out["entry"] = self.entry.id
        return out


@dataclass(kw_only=True, slots=True, repr=False)
class AddAssetEvent(Event):
    """
    A resource being tracked. The size and mtime recorded here are the baseline for later comparison
    with the filesystem. They are sampled once and not refreshed on replay. `rule` is the line of
    `.markers` that picked the resource up, or None if it was tracked explicitly.

    >>> trail: Trail
    >>> trail.events.select[2:3]
    Events (1)
        0. AddAssetEvent
            id: f9b785f599e641ad8781eb615898eeef
            timestamp: 02:48:26.945
            src_path: /tmp/tmpp_bl05lq/new.csv
            rule: *.csv
            st_size: 17 B
            st_mtime: 2026-10-06 19:48:26
    """

    src_path: str
    rule: str | None = None
    st_size: int | None = field(default=None, init=False)
    st_mtime: float | None = field(default=None, init=False)
    is_directory: bool = field(default=False, init=False, repr=False)
    entry: Asset | None = field(default=None, init=False, repr=False)

    def apply(
        self,
        trail: Trail,
        *,
        replay: bool = False,
    ) -> Asset:
        """
        Track the asset at `src_path` and return it. Unless this is a replay, its size and
        mtime are recorded as the baseline. The path is no longer counted as offtrailed.
        """
        if replay:
            asset = self.entry
        else:
            asset = trail.assets.get(self.src_path)
        if asset is None:
            asset = Asset.from_path(self.src_path, trail=trail)
        self.entry = asset

        asset.track()
        self.is_directory = asset.is_directory
        if not replay:
            self._stat(asset)
        trail._offtrailed_paths.discard(asset.path)
        return asset

    def _stat(self, asset: Asset) -> None:
        """
        Record the size and mtime of the asset. A directory asset records the totals of its
        fingerprint. A resource that vanished before it was tracked records neither, and tracking
        does not fail.
        """
        try:
            _, size, mtime = asset.fingerprint
        except OSError:
            return
        self.st_size = size
        self.st_mtime = mtime


@dataclass(kw_only=True, slots=True, repr=False)
class RemoveAssetEvent(Event):
    """
    An asset being offtrailed. Its path is remembered as offtrailed, so `.markers` does not
    pick it up again.

    >>> trail: Trail
    >>> trail.events.select[-1:]
    Events (1)
        0. RemoveAssetEvent
            id: 730d5f59b035476e85cc1848e9b0f984
            timestamp: 06:39:38.089
            src_path: /tmp/tmpp_bl05lq/later.csv
    """

    src_path: str
    entry: Asset | None = field(default=None, init=False, repr=False)

    def apply(
        self,
        trail: Trail,
        *,
        replay: bool = False,
    ) -> Asset | None:
        """
        Offtrail the asset at `src_path` and return it. Returns None if nothing is tracked
        there.
        """
        if replay:
            asset = self.entry
        else:
            asset = trail.assets.get(self.src_path)
        if asset is None:
            return None
        self.entry = asset
        asset.offtrail()
        trail._offtrailed_paths.add(asset.path)
        return asset


@dataclass(kw_only=True, slots=True, repr=False)
class AddDirEvent(Event):
    """
    A directory becoming a Dir, since a line of `.markers` names it. `rule` is the first line
    that names it. The Dir's root and scopes are not logged, since `.markers` holds them.

    >>> trail: Trail
    >>> trail.events.select[:1]
    Events (1)
        0. AddDirEvent
            id: 8e6ec01dffaa4fb9871f91f882164169
            timestamp: 02:48:26.945
            src_path: /tmp/tmpp_bl05lq
            rule: ./
    """

    src_path: str
    rule: str | None = None
    entry: Dir | None = field(default=None, init=False, repr=False)

    def apply(
        self,
        trail: Trail,
        *,
        replay: bool = False,
    ) -> Dir:
        """
        Hold the Dir in `trail.dirs` and return it. The Dir is made before the event, by
        `Dirs.refresh` or by the replay of the log.
        """
        directory = self.entry
        if directory is None:
            raise ValueError("An AddDirEvent is applied with its Dir already set")
        directory.track()
        return directory


@dataclass(kw_only=True, slots=True, repr=False)
class RemoveDirEvent(Event):
    """
    A Dir that no line of `.markers` names any more. Its ID stays in the log, with its history.

    >>> trail: Trail
    >>> trail.unmark('raw/')
    ['raw/']
    >>> trail.events.select[-1:]
    Events (1)
        0. RemoveDirEvent
            id: cf4ed22269d046fdb6bdd96dae49580a
            timestamp: 02:48:26.947
            src_path: /tmp/tmpp_bl05lq/raw
    """

    src_path: str
    entry: Dir | None = field(default=None, init=False, repr=False)

    def apply(
        self,
        trail: Trail,
        *,
        replay: bool = False,
    ) -> Dir | None:
        """Let go of the Dir at `src_path` and return it. Returns None if there is none."""
        if replay:
            directory = self.entry
        else:
            directory = trail.dirs.get(self.src_path)
        if directory is None:
            return None
        self.entry = directory
        directory.offtrail()
        return directory


@dataclass(kw_only=True, slots=True, repr=False)
class WatchdogEvent(Event):
    """
    A filesystem event on a tracked asset, or on a Dir's own directory. Events on other paths
    are not recorded. The watchdog decides whether such a path should be tracked.

    >>> trail: Trail
    >>> trail.events.select[-1:]
    Events (1)
        0. WatchdogEvent
            id: b1bf61c7ad8e418f973329e3201f9ea4
            timestamp: 06:42:07.593
            src_path: /tmp/tmpp_bl05lq/new.csv
            event_type: opened
    """

    src_path: str
    dest_path: str = ""
    event_type: str = ""
    is_directory: bool = field(default=False, repr=False)
    is_synthetic: bool = field(default=False, repr=False)
    entry: Entry | None = field(default=None, init=False, repr=False)

    def apply(
        self,
        trail: Trail,
        *,
        replay: bool = False,
    ) -> Entry | None:
        """
        Apply the event to the asset or the Dir it is about, log it, and return that entry.
        Returns None for an event about anything else. A move repaths an asset. A Dir keeps
        its path, since its line in `.markers` still names it. On replay, the move or creation
        is applied to the asset the record names, and nothing is applied to a Dir.
        """
        if replay:
            entry = self.entry
            if entry is None:
                raise ValueError("Cannot replay a watchdog event without an entry")
            if isinstance(entry, Asset):
                if self.event_type == "moved" and self.dest_path:
                    entry.move(self.dest_path)
                elif self.event_type == "created":
                    entry.track()
            return entry

        if self.event_type not in EVENT_TYPES:
            return None
        source = Path(self.src_path).expanduser().resolve()
        destination = None
        if self.event_type == "moved" and self.dest_path:
            destination = Path(self.dest_path).expanduser().resolve()
        for path in (source, destination):
            if path is None:
                continue
            if (
                path in trail._offtrailed_paths
                or trail._is_ignored(path)
            ):
                return None
        entry = self._find(trail, source, destination)
        if entry is None:
            return None
        self.src_path = str(source)
        if destination is not None:
            self.dest_path = str(destination)
        if isinstance(entry, Asset):
            if destination is None:
                if self.event_type == "created":
                    entry.track()
            elif entry.path != destination:
                entry.move(destination)
        self.entry = entry
        trail.events.append(self)
        return entry

    def _find(
        self,
        trail: Trail,
        source: Path,
        destination: Path | None,
    ) -> Entry | None:
        """
        Returns the entry the event is about. That is the asset at its source or destination, or the
        Dir there for a directory event. An entry of the wrong kind, such as a file for a directory
        event, is skipped.
        """
        collections = [trail.assets]
        if self.is_directory:
            collections.append(trail.dirs)
        for collection in collections:
            for path in (source, destination):
                if path is None:
                    continue
                entry = collection.get(path)
                if (
                    entry is not None
                    and entry.is_directory == self.is_directory
                ):
                    return entry
        return None


@dataclass(kw_only=True, slots=True, repr=False)
class DirectoryAccessEvent(WatchdogEvent):
    """
    One access to a dirasset. It covers what happened inside the dirasset from `started` to its last
    event, which is its `timestamp`. It is inferred from many filesystem events, so it is always
    synthetic. `count` holds the events of each type that were counted for the dirasset's children.
    It counts the events the watchdog delivered, not the files read or written. `event_type` is
    `opened` if only opens were counted, and `modified` otherwise.

    >>> trail: Trail
    >>> trail.events.select[-1:]
    Events (1)
        0. DirectoryAccessEvent
            id: 213069170ab24755b733578c438c0a93
            timestamp: 21:24:58.104
            src_path: /tmp/tmp69jvvrsu/cube.zarr
            event_type: modified
            started: 21:24:57.862
            count: opened 1, modified 72
    """

    started: datetime
    is_directory: bool = field(default=True, init=False, repr=False)
    is_synthetic: bool = field(default=True, init=False, repr=False)
    count: dict[str, int] = field(default_factory=dict)
    entry: DirAsset | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        WatchdogEvent.__post_init__(self)
        # a replay passes the start as the log holds it
        if isinstance(self.started, str):
            self.started = datetime.fromisoformat(self.started)

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        """Yields the fields a repr shows, with the counts on one line and zeros left out."""
        for name, value in WatchdogEvent._repr_items(self):
            if name == "count":
                value = ", ".join(
                    f"{op} {value[op]}"
                    for op in OPS
                    if value.get(op)
                )
            yield name, value

    def apply(
        self,
        trail: Trail,
        *,
        replay: bool = False,
    ) -> DirAsset | None:
        """
        Log the access and return its dirasset. An access changes nothing about the dirasset, so a
        replay only returns it.
        """
        if not replay:
            trail.events.append(self)
        return self.entry


@dataclass(kw_only=True, slots=True, repr=False)
class JupyterEvent(Event):
    def apply(
        self,
        trail: Trail,
        *,
        replay: bool = False,
    ) -> Entry | None:
        """Returns the entry the event names. A Jupyter event changes nothing on its own."""
        return self.entry


class JSONL(Node):
    _parent: Events

    def __repr__(self) -> str:
        return fmt.asset(type(self).__name__, self.path)

    @property
    def path(self) -> Path | None:
        trail = self._trail
        # only the trail's own log is kept on disk; a selection out of it stands alone, so that
        # changing one cannot rewrite the log with only what was selected
        if self._parent is not trail.events:
            return None
        if trail.dir:
            return trail.dir / "events.jsonl"
        else:
            return None

    def read(self) -> None:
        """
        Replay the log file into the Trail. Each entry is made from the first record about it,
        and every record is then applied in order.
        """
        path = self.path
        trail = self._trail
        if path is None or not path.exists():
            return
        loaded: dict[str, Event] = {}
        entries: dict[str, Entry] = {}
        with path.open(encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                record = json.loads(line)
                entry_id = normalize_id(record["entry"])
                entry = entries.get(entry_id)
                if entry is None:
                    entry = trail._entry(entry_id)
                    if entry is None:
                        entry = self.record2entry(record, entry_id)
                    entries[entry_id] = entry
                event = Event.from_record(trail=trail, resolved_entry=entry, **record)
                if event.id in loaded:
                    raise ValueError(f"Duplicate event ID in {path}: {event.id}")
                loaded[event.id] = event

                event.apply(trail, replay=True)
        self._parent.data.clear()
        self._parent.data.update(loaded)
        self._parent.ids[:] = loaded

    def record2entry(
        self,
        record: dict,
        identifier: str,
    ) -> Entry:
        """
        Returns a new entry for the first record about an id. That record is always the event
        that added the entry.
        - An AddDirEvent makes a Dir.
        - A record about a directory makes a DirAsset.
        - Any other record makes an Asset.
        """
        from trail.dir import Dir

        trail = self._trail
        path = Path(record["src_path"])
        if record["cls"] == AddDirEvent.__name__:
            entry = Dir(path=path, id=identifier)
            entry._parent = trail.dirs
            return entry
        if record.get("is_directory", False):
            entry = DirAsset(path=path, id=identifier)
        else:
            entry = Asset(path=path, id=identifier)
        entry._parent = trail.assets
        return entry

    def write(self) -> None:
        path = self.path
        if path is None:
            return
        text = "".join(
            json.dumps(event.to_record(), ensure_ascii=False) + "\n"
            for event in self._parent
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def append(self, events: Iterable[Event]) -> None:
        path = self.path
        if path is None:
            return
        text = "".join(
            json.dumps(event.to_record(), ensure_ascii=False) + "\n"
            for event in events
        )
        if not text:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a+b") as file:
            if file.tell():
                file.seek(-1, 2)
                if file.read(1) != b"\n":
                    file.write(b"\n")
            file.write(text.encode("utf-8"))


class Watch(Node):
    """
    Suspends a consumer until the Trail records something, so it does not poll the collection. Each
    iteration has its own waiter and its own cursor into the log. Several consumers can watch at
    once without consuming each other's events.
    """

    _parent: Events

    @cached_property
    def waiters(self) -> dict[asyncio.Event, asyncio.AbstractEventLoop]:
        """
        The flag of each live iteration, mapped to the loop the iteration runs on. The flag is
        raised when the log is appended to.
        """
        return {}

    def notify(self) -> None:
        """
        Wake every iteration to drain what was just appended. Each flag is set on its own loop,
        directly from that loop or through `call_soon_threadsafe` from any other thread. A closed
        loop is skipped. Nothing raises.
        """
        try:
            current = asyncio.get_running_loop()
        except RuntimeError:
            current = None
        for waiter, loop in tuple(self.waiters.items()):
            if loop is current:
                waiter.set()
            elif not loop.is_closed():
                # the loop may close between the check and the call
                with suppress(RuntimeError):
                    loop.call_soon_threadsafe(waiter.set)

    def __call__(
        self,
        timeout: float | None = None,
    ) -> AsyncIterator[Event]:
        """
        Yield each event appended after this call. It suspends until the next event arrives and does
        not poll. The cursor is taken here, not when iteration begins. A watcher made before the
        action that triggers the event cannot miss it, however long the loop takes to reach the
        `async for`. Iteration ends after `timeout` seconds, so a `for ... else` can tell the
        awaited event from the deadline.

            watching = trail.events.watch(timeout=5)
            asset.open()
            async for event in watching:
                if isinstance(event, WatchdogEvent):
                    break
            else:
                raise TimeoutError
        """
        return self.iterate(len(self._parent.ids), timeout)

    async def iterate(
        self,
        position: int,
        timeout: float | None,
    ) -> AsyncIterator[Event]:
        """
        Yield each event from `position` in the log onward, then suspend until `notify` sets this
        iteration's waiter.
        - The waiter is held in `waiters` while the iteration runs, and removed when it ends.
        - The waiter is cleared before the backlog is drained. An event appended during a yield
          sets it again, so the event is not missed.
        - A position past the end of the log is moved back to the end. This happens if events
          were removed while watching.
        - Iteration ends `timeout` seconds after it starts. It never ends on its own if `timeout`
          is None.
        """
        events = self._parent
        loop = asyncio.get_running_loop()
        if timeout is None:
            deadline = None
        else:
            deadline = loop.time() + timeout
        waiter = asyncio.Event()
        self.waiters[waiter] = loop
        try:
            while True:
                # cleared before the backlog is drained, so an arrival during a yield is not lost
                waiter.clear()
                # events removed while watching would leave the position beyond the collection
                position = min(position, len(events.ids))
                while position < len(events.ids):
                    event = events.data[events.ids[position]]
                    position += 1
                    yield event
                if deadline is None:
                    remaining = None
                else:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        return
                try:
                    await asyncio.wait_for(waiter.wait(), remaining)
                except TimeoutError:
                    return
        finally:
            self.waiters.pop(waiter, None)

    def __repr__(self) -> str:
        """
        Shows how many iterations are waiting on the log.

        >>> trail: Trail
        >>> trail.events.watch
        Watch
            waiters: 1
        """
        lines = [
            type(self).__name__,
            f"    waiters: {len(self.waiters)!r}",
        ]
        return "\n".join(lines)


class Events(Collection[str, Event]):
    """
    A collection of Event objects that have occurred in a Trail, about its assets and Dirs.

    >>> trail: Trail
    >>> trail.events.select[1:3]
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

    @property
    def types(self) -> list[type[Event]]:
        """
        Returns every registered event class. The `events` listing in the console offers their
        fields as `field=` completions. `Event.classes` is read, not `__subclasses__`, since a
        slots dataclass leaves its class from before the slots behind as a second subclass.
        """
        return list(Event.classes.values())

    @cached_property
    def jsonl(self) -> JSONL:
        return JSONL(self)

    @cached_property
    def watch(self) -> Watch:
        """Awaits the events yet to be recorded, in place of polling the collection."""
        return Watch(self)

    def extend(self, events: Iterable[Event]) -> None:
        """Record `events` together, so a burst of them costs the log one write, not one each."""
        batch: dict[str, Event] = {}
        for event in events:
            if not isinstance(event, Event):
                raise TypeError(f"Expected Event, got {type(event).__name__}")
            batch[event.id] = event
        replacing = any(
            identifier in self.data
            for identifier in batch
        )
        if not replacing:
            self.jsonl.append(batch.values())
        appended = [
            identifier
            for identifier in batch
            if identifier not in self.data
        ]
        self.ids.extend(appended)
        self.data.update(batch)
        if replacing:
            self.jsonl.write()
        if appended:
            self.watch.notify()

    def __setitem__(
        self,
        key: str,
        value: Event,
    ) -> None:
        if not isinstance(value, Event):
            raise TypeError(f"Expected Event, got {type(value).__name__}")
        if key in self.data:
            self.data[key] = value
            self.jsonl.write()
        else:
            self.jsonl.append([value])
            self.data[key] = value
            self.ids.append(key)
            self.watch.notify()

    def __delitem__(self, key: str) -> None:
        del self.data[key]
        self.ids.remove(key)
        self.jsonl.write()

    def clear(self) -> None:
        """
        Empty the log. Accesses still open are dropped, not logged. Each Dir named in `.markers` is
        logged again with its ID. Calling this on a selection only empties the selection.
        """
        trail = self._trail
        session = trail.watchdog.session
        if (
            self is trail.events
            and session
        ):
            session.accesses.drop()
        super().clear()
        self.jsonl.write()
        if self is trail.events:
            trail.dirs.relog()
