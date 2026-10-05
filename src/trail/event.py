from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable, Iterator
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Self
from uuid import uuid4

from trail.asset import Asset
from trail.collection import Collection
from trail.node import Node
from trail.util import asset_repr, mtime_repr, normalize_id, st_size_repr

if TYPE_CHECKING:
    from trail.trail import Trail


class Get:
    """
    Allows for shorthand non-raising access.

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

        >>> trail.events.select[-1].get.rule
        >>> trail.events.select[0].get.rule
        '*.csv'
        """
        return getattr(self._parent, name, None)


@dataclass(kw_only=True, slots=True, repr=False)
class Event:
    classes: ClassVar[dict[str, type[Event]]] = {}

    asset: Asset | None = field(default=None, init=False, repr=False)
    id: str = field(default_factory=lambda: uuid4().hex, repr=True)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        self.id = normalize_id(self.id)

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        for event_field in fields(self):
            if not event_field.repr:
                continue
            value = getattr(self, event_field.name)
            if event_field.name == "timestamp":
                now = datetime.now(value.tzinfo)
                date_prefix = ""
                if value.year != now.year:
                    date_prefix = value.strftime("%Y-%m-%d ")
                elif value.month != now.month:
                    date_prefix = value.strftime("%m-%d ")
                elif value.day != now.day:
                    date_prefix = value.strftime("%d ")
                value = f"{date_prefix}{value:%H:%M:%S}.{value.microsecond // 1000:03d}"
            if value is None or value == "":
                continue
            # the baseline is stored as the stat reported it; the units are put back here
            if event_field.name == "st_size":
                value = st_size_repr(value)
            elif event_field.name == "st_mtime":
                value = mtime_repr(value)
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
    ) -> Asset | None:
        """
        Apply the event to the Trail and return the asset it is about. With `replay`, the event
        comes from the log and its asset is already resolved. Each subclass implements it.
        """
        raise NotImplementedError

    def __init_subclass__(cls, **kwargs) -> None:
        # slots=True replaces the class captured by zero-argument super()
        super(Event, cls).__init_subclass__(**kwargs)
        cls.classes[cls.__name__] = cls

    @classmethod
    def from_record(cls, /, trail: Trail, resolved_asset: Asset | None = None, **record) -> Event:
        """
        Rebuild an event from one record of the log. The asset it names is `resolved_asset` if
        given, otherwise the tracked asset with the record's asset id.
        """
        name = record.pop("cls")
        event_cls = cls.classes.get(name)

        if name == cls.__name__:
            event_cls = cls
        if event_cls is None:
            raise ValueError(f"Unknown event class: {name!r}")
        asset_id = normalize_id(record.pop("asset"))
        record["timestamp"] = datetime.fromisoformat(record["timestamp"])
        deferred = {
            field.name: record.pop(field.name)
            for field in fields(event_cls)
            if not field.init and field.name in record
        }
        event = event_cls(**record)
        for name, value in deferred.items():
            setattr(event, name, value)

        if resolved_asset is None:
            resolved_asset = trail.assets[asset_id]
        elif resolved_asset.id != asset_id:
            raise ValueError(f"Asset ID does not match event record: {asset_id}")
        event.asset = resolved_asset

        return event

    def to_record(self) -> dict:
        """
        Returns the event as one record of the log. The asset is held by its id, and the
        timestamp in ISO format.
        """
        out = {
            field.name: getattr(self, field.name) for field in fields(self) if field.name != "asset"
        }
        out["cls"] = type(self).__name__
        out["timestamp"] = self.timestamp.isoformat()
        out["asset"] = self.asset.id
        return out


@dataclass(kw_only=True, slots=True, repr=False)
class AddAssetEvent(Event):
    """
    A resource being tracked. The recorded size and mtime are the baseline the filesystem is
    later compared against, so they are sampled once here and never refreshed on replay.
    `rule` is the line of `.markers` that picked the resource up, or None if it was tracked
    explicitly.

    >>> trail.events.select[:1]
    Events (1)
        0. AddAssetEvent
            id: 2d75b41403bc42abb52af2966bf8a194
            timestamp: 06:39:37.824
            src_path: /tmp/tmpp_bl05lq/new.csv
            rule: *.csv
            st_size: 17 B
            st_mtime: 2026-10-02 23:39:37
    """

    src_path: str
    rule: str | None = None
    st_size: int | None = field(default=None, init=False)
    st_mtime: float | None = field(default=None, init=False)
    is_directory: bool = field(default=False, init=False, repr=False)
    asset: Asset | None = field(default=None, init=False, repr=False)

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
            asset = self.asset
        else:
            asset = trail.assets.get(self.src_path)
        if asset is None:
            asset = Asset.from_path(self.src_path, trail=trail)
        self.asset = asset

        asset.track()
        self.is_directory = asset.is_directory
        if not replay:
            self._stat(asset)
        trail._offtrailed_paths.discard(asset.path)
        return asset

    def _stat(self, asset: Asset) -> None:
        """
        Record the size and mtime of the asset. A directory asset records the totals of its
        fingerprint. A resource that vanished before it was tracked records neither, rather
        than failing to track.
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

    >>> trail.events.select[-1:]
    Events (1)
        0. RemoveAssetEvent
            id: 730d5f59b035476e85cc1848e9b0f984
            timestamp: 06:39:38.089
            src_path: /tmp/tmpp_bl05lq/later.csv
    """

    src_path: str
    asset: Asset | None = field(default=None, init=False, repr=False)

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
            asset = self.asset
        else:
            asset = trail.assets.get(self.src_path)
        if asset is None:
            return None
        self.asset = asset
        asset.offtrail()
        trail._offtrailed_paths.add(asset.path)
        return asset


@dataclass(kw_only=True, slots=True, repr=False)
class WatchdogEvent(Event):
    """
    A filesystem event on a tracked asset. Events on paths that are not tracked are not
    recorded. The watchdog decides whether such a path should be tracked.

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
    asset: Asset | None = field(default=None, init=False, repr=False)

    def apply(
        self,
        trail: Trail,
        *,
        replay: bool = False,
    ) -> Asset | None:
        """
        Apply the event to the tracked asset it is about, log it, and return the asset. A move
        repaths the asset. Returns None for an event about a path that is not tracked. On
        replay, the move or creation is applied to the asset the record names.
        """
        if replay:
            asset = self.asset
            if asset is None:
                raise ValueError("Cannot replay a watchdog event without an asset")
            if self.event_type == "moved" and self.dest_path:
                asset.move(self.dest_path)
            elif self.event_type == "created":
                asset.track()
            return asset

        if self.event_type not in {
            "created",
            "modified",
            "deleted",
            "moved",
            "opened",
        }:
            return None
        source = Path(self.src_path).expanduser().resolve()
        destination = None
        if self.event_type == "moved" and self.dest_path:
            destination = Path(self.dest_path).expanduser().resolve()
        for path in (source, destination):
            if path is not None and (path in trail._offtrailed_paths or trail._ignored(path)):
                return None
        assets = trail.assets
        asset = assets.get(source)
        if asset is None and destination is not None:
            asset = assets.get(destination)
        if (
            asset is None
            or asset.is_directory != self.is_directory
        ):
            return None
        self.src_path = str(source)
        if destination is not None:
            self.dest_path = str(destination)
            if asset.path != destination:
                asset.move(destination)
        elif self.event_type == "created":
            asset.track()
        self.asset = asset
        trail.events.append(self)
        return asset


@dataclass(kw_only=True, slots=True, repr=False)
class JupyterEvent(Event):
    def apply(
        self,
        trail: Trail,
        *,
        replay: bool = False,
    ) -> Asset | None:
        """Returns the asset the event names. A Jupyter event changes nothing on its own."""
        return self.asset


class JSONL(Node):
    _parent: Events

    def __repr__(self) -> str:
        return asset_repr(type(self).__name__, self.path)

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
        Replay the log file into the Trail. Each asset is made from the first record about it,
        and every record is then applied in order.
        """
        path = self.path
        trail = self._trail
        if path is None or not path.exists():
            return
        loaded: dict[str, Event] = {}
        assets: dict[str, Asset] = {}
        with path.open(encoding="utf-8") as file:
            for line in file:
                if not line.strip():
                    continue
                record = json.loads(line)
                asset_id = normalize_id(record["asset"])
                asset = assets.get(asset_id)
                if asset is None:
                    asset = trail.assets.id2asset.get(asset_id)
                    if asset is None:
                        asset = Asset(
                            path=Path(record["src_path"]),
                            id=asset_id,
                            is_directory=record.get("is_directory", False),
                        )
                        asset._parent = trail.assets
                    assets[asset_id] = asset
                event = Event.from_record(trail=trail, resolved_asset=asset, **record)
                if event.id in loaded:
                    raise ValueError(f"Duplicate event ID in {path}: {event.id}")
                loaded[event.id] = event

                event.apply(trail, replay=True)
        self._parent.data.clear()
        self._parent.data.update(loaded)
        self._parent.ids[:] = loaded

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
        text = "".join(json.dumps(event.to_record(), ensure_ascii=False) + "\n" for event in events)
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
    Suspends a consumer until the Trail records something, rather than leaving it to poll the
    collection. Each iteration holds its own waiter and its own cursor into the log, so several
    consumers may watch at once without consuming one another's events.
    """

    _parent: Events

    @cached_property
    def waiters(self) -> set[asyncio.Event]:
        """The flag held by each live iteration, raised when the log is appended to."""
        return set()

    def notify(self) -> None:
        """Wake every iteration so it may drain whatever was just appended."""
        for waiter in self.waiters:
            waiter.set()

    def __call__(
        self,
        timeout: float | None = None,
    ) -> AsyncIterator[Event]:
        """
        Yield each event appended after this call, suspending until the next one arrives rather
        than polling the collection. The cursor is taken here rather than when iteration begins,
        so a watcher made before the action which triggers the event cannot miss it, however long
        the loop takes to reach the `async for`. Iteration ends once `timeout` seconds have
        elapsed, so a `for ... else` distinguishes the awaited event from the deadline.

            watching = trail.events.watch(timeout=5)
            asset.open()
            async for event in watching:
                if isinstance(event, WatchdogEvent):
                    break
            else:
                raise TimeoutError
        """
        return self._iterate(len(self._parent.ids), timeout)

    async def _iterate(
        self,
        position: int,
        timeout: float | None,
    ) -> AsyncIterator[Event]:
        events = self._parent
        loop = asyncio.get_running_loop()
        if timeout is None:
            deadline = None
        else:
            deadline = loop.time() + timeout
        waiter = asyncio.Event()
        self.waiters.add(waiter)
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
            self.waiters.discard(waiter)

    def __repr__(self) -> str:
        lines = [
            type(self).__name__,
            f"    waiters: {len(self.waiters)!r}",
        ]
        return "\n".join(lines)


class Events(Collection[str, Event]):
    """
    A collection of Event objects that have occurred in a Trail.

    >>> trail.events
    Events (9)
        0. AddAssetEvent
            id: '8a43da699bf242e7976e7bb74df81d0f'
            timestamp: '06:02:12.114'
            src_path: '/tmp/tmpbzh09nb5/folder'
        1. WatchdogEvent
            id: 'c2152a8b8f31416abbfe7106fce8cb6c'
            timestamp: '06:02:12.130'
            src_path: '/tmp/tmpbzh09nb5/folder/new.csv'
            event_type: 'created'
        2. WatchdogEvent
            id: '620c0779453a4e5db975fa11e9efac7b'
            timestamp: '06:02:12.130'
            src_path: '/tmp/tmpbzh09nb5/folder/new.csv'
            event_type: 'opened'
    """

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
        replacing = any(identifier in self.data for identifier in batch)
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
        super().clear()
        self.jsonl.write()
