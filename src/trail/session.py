from __future__ import annotations

import asyncio
import atexit
import errno
import os
import re
import threading
import time
import warnings
import weakref
from collections import UserDict, deque
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple, Self

from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
    FileClosedEvent,
    FileClosedNoWriteEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileOpenedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from trail.asset import Asset, DirAsset
from trail.event import OPS, DirectoryAccessEvent, FileAccessEvent, MetadataEvent, WatchdogEvent
from trail.metadata import Metadata
from trail.metadata.geospatial import sibling_shp
from trail.metadata.reads import Reads
from trail.node import Node
from trail.offtrail import decide as offtrail_decision

if TYPE_CHECKING:
    from types import TracebackType

    from watchdog.observers.api import BaseObserver, ObservedWatch

    from trail.watchdog import Watchdog

# queued by stop() after everything else
STOP: Final = object()
# what a warning adds when a watch fails on a limit of the kernel's
LIMITS: Final = {
    errno.ENOSPC: '; raise fs.inotify.max_user_watches',
    errno.EMFILE: '; raise fs.inotify.max_user_instances',
}
EVENT_FILTER: Final = [
    FileCreatedEvent,
    FileModifiedEvent,
    FileDeletedEvent,
    FileMovedEvent,
    FileOpenedEvent,
    # a close tells when the writer is done, and whether an open was a read or a write
    FileClosedEvent,
    FileClosedNoWriteEvent,
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
]
# the file events folded into a session. A move or a deletion stays one event
FOLDED: Final = frozenset({'created', 'modified', 'opened', 'closed', 'closed_no_write'})
CLOSES: Final = frozenset({'closed', 'closed_no_write'})


def enclosing(
        path: str,
        roots: Mapping[str, object],
) -> str | None:
    """Returns the root among `roots` that a path is strictly inside of, or None."""
    end = len(path)
    # a walk up the parents as strings, which is cheaper than os.path.dirname
    while True:
        end = path.rfind(os.sep, 0, end)
        if end <= 0:
            return None
        parent = path[:end]
        if parent in roots:
            return parent


class Arrival(NamedTuple):
    """A watchdog event, with the time the observer thread received it, in epoch seconds."""

    at: float
    event: FileSystemEvent


@dataclass(slots=True, eq=False)
class Access:
    """
    The open access of one dirasset: how many events of each type were counted, and when its first
    and last events came. Concurrent readers and writers share it.
    """

    asset: str
    count: dict[str, int] = field(default_factory=lambda: dict.fromkeys(OPS, 0))
    # epoch seconds of the first and the last event, which become `started` and `timestamp`
    started: float | None = None
    ended: float | None = None
    # monotonic seconds of the last event, which the idle gap is measured from
    seen: float | None = None

    def add(
            self,
            op: str,
            at: float,
            seen: float,
    ) -> None:
        """Count one event at its wall-clock and monotonic times."""
        if self.started is None:
            self.started = at
        self.ended = at
        self.seen = seen
        self.count[op] += 1

    def to_event(self, asset: DirAsset) -> DirectoryAccessEvent:
        """
        Returns the event that logs the access, for the dirasset at its current path. An access with
        only opens is `opened`. Any other access is `modified`.
        """
        is_read = all(
            count == 0
            for op, count in self.count.items()
            if op != 'opened'
        )
        if is_read:
            event_type = 'opened'
        else:
            event_type = 'modified'
        event = DirectoryAccessEvent(
            src_path=str(asset.path),
            event_type=event_type,
            timestamp=datetime.fromtimestamp(self.ended, UTC),
            started=datetime.fromtimestamp(self.started, UTC),
            count=dict(self.count),
        )
        event.entry = asset
        return event


@dataclass(slots=True, eq=False)
class Fold:
    """
    The open session of one file path: its events folded until the writer is done. Times are epoch
    seconds of the events' arrival.

    - `started` is the first event.
    - `ended` is the last creation, write, write close or replacement. A read does not move it.
    - `last` is the last event of any kind, which the deadline is measured from.
    - `is_closed` says whether nothing is known to hold the file open. A file created or replaced
      without an open, as by a rename from outside the watched folders, is closed. So is a file
      whose last open was followed by a close.
    """

    path: str
    started: float
    ended: float
    last: float
    is_created: bool = False
    is_written: bool = False
    is_close_written: bool = False
    is_closed: bool = False

    def add(
            self,
            event_type: str,
            at: float,
    ) -> None:
        """Fold one event in. `replaced` is a rename onto the path, which ends a write."""
        self.last = at
        if event_type == 'created':
            self.is_created = True
            # a creation holds nothing open. A writer's open follows it at once
            self.is_closed = True
        elif event_type == 'opened':
            self.is_closed = False
        elif event_type == 'modified':
            self.is_written = True
        elif event_type == 'closed':
            self.is_close_written = True
            self.is_closed = True
        elif event_type == 'closed_no_write':
            self.is_closed = True
        elif event_type == 'replaced':
            self.is_written = True
            self.is_closed = True
        if event_type not in ('opened', 'closed_no_write'):
            self.ended = at

    def deadline(
            self,
            settle: float,
            quiet: float,
    ) -> float:
        """The epoch time the session ends if nothing else arrives."""
        if self.is_closed:
            out = self.last + settle
        else:
            out = self.last + quiet
        return out

    def event_type(self, is_changed: bool) -> str:
        """
        The folded event's type. `is_changed` says whether the size or mtime differs from the last
        recorded ones, which decides a session whose only sign of writing is a write close.
        """
        if self.is_created:
            return 'created'
        if (
            self.is_written
            or (
                self.is_close_written
                and is_changed
            )
        ):
            return 'modified'
        return 'opened'


class Accesses(UserDict[str, Access]):
    """
    The open access of each dirasset, by asset id. The observer thread counts into it and the loop
    closes it, so every change holds the session's lock. Reading a dirasset with no open access
    makes an empty one. An empty access is never logged.
    """

    def __init__(self, session: WatchdogSession) -> None:
        super().__init__()
        self.session = session

    def __missing__(self, asset: str) -> Access:
        with self.session.lock:
            access = Access(asset=asset)
            return self.data.setdefault(asset, access)

    def count(
            self,
            asset: str,
            op: str,
            at: float,
            seen: float,
    ) -> None:
        """
        Count one event of a dirasset. An event more than `idle_gap` seconds after the access's last
        event closes the access and opens the next one.
        """
        session = self.session
        with session.lock:
            access = self[asset]
            if (
                access.seen is not None
                and seen - access.seen > session.idle_gap
            ):
                self.close(asset)
                access = self[asset]
            if access.seen is None:
                # the consumer's next deadline depends on this access
                session.wake()
            access.add(op, at, seen)

    def close(
            self,
            asset: str | None = None,
            before: float | None = None,
    ) -> None:
        """
        Queue open accesses so the loop logs each once. `asset` selects one dirasset's access.
        `before` selects the accesses last seen at or before that monotonic time. With neither,
        every access is selected. An empty access is dropped.
        """
        session = self.session
        with session.lock:
            if asset is not None:
                identifiers = [asset]
            else:
                identifiers = [
                    identifier
                    for identifier, access in self.data.items()
                    if before is None
                    or (
                        access.seen is not None
                        and access.seen <= before
                    )
                ]
            for identifier in identifiers:
                access = self.data.pop(identifier, None)
                if (
                    access is not None
                    and access.seen is not None
                ):
                    session.put(access)

    @property
    def open(self) -> int:
        """The number of accesses open now, leaving out the empty ones a read of one makes."""
        with self.session.lock:
            return sum(
                1
                for access in self.data.values()
                if access.seen is not None
            )

    def deadline(self) -> float | None:
        """The monotonic time the quietest open access goes idle, or None when none is open."""
        session = self.session
        with session.lock:
            seen = [
                access.seen
                for access in self.data.values()
                if access.seen is not None
            ]
        if not seen:
            return None
        return min(seen) + session.idle_gap

    def close_within(self, path: str) -> None:
        """Close the access of every dirasset at or below a path that is being deleted or moved."""
        below = path + os.sep
        for root, (asset, _) in self.session.roots.items():
            if (
                root == path
                or root.startswith(below)
            ):
                self.close(asset)

    def drop(self) -> None:
        """Forget every access, open or queued, without logging it."""
        session = self.session
        with session.lock:
            self.data.clear()
            kept = [
                item
                for item in session.queue
                if not isinstance(item, Access)
            ]
            session.queue.clear()
            session.queue.extend(kept)


class Watches(UserDict[Path, bool]):
    """
    The directories the session watches, each mapped to whether its watch is recursive. Setting a
    directory schedules its watch, and deleting it unschedules the watch.
    """

    def __init__(self, session: WatchdogSession) -> None:
        super().__init__()
        self.session = session
        self.handles: dict[Path, ObservedWatch] = {}

    def __setitem__(
            self,
            directory: Path,
            recursive: bool,
    ) -> None:
        if self.data.get(directory) == recursive:
            return
        if directory in self.data:
            del self[directory]
        session = self.session
        handle = session.observer.schedule(
            session.handler,
            str(directory),
            recursive=recursive,
            event_filter=EVENT_FILTER,
        )
        self.handles[directory] = handle
        self.data[directory] = recursive

    def __delitem__(self, directory: Path) -> None:
        handle = self.handles.pop(directory)
        del self.data[directory]
        # the emitter stopped itself if its directory was deleted
        with suppress(KeyError):
            self.session.observer.unschedule(handle)

    def match(self, desired: Mapping[Path, bool]) -> None:
        """
        Make the watches the ones `desired` names. A watch it no longer names, or names with a
        different recursion, is removed. A watch that cannot be placed, such as one on a directory
        that just vanished, is skipped with a warning.
        """
        for path, recursive in tuple(self.items()):
            if desired.get(path) != recursive:
                del self[path]
        for path, recursive in desired.items():
            if path in self:
                continue
            try:
                self[path] = recursive
            except OSError as error:
                warnings.warn(f"{path} is not watched: {error}{LIMITS.get(error.errno, '')}")


class Handler(FileSystemEventHandler):
    """
    Receives watchdog's events on its observer thread and queues them for the loop. It never
    touches the Trail.
    """

    def __init__(self, session: WatchdogSession) -> None:
        self.session = session

    def on_any_event(self, event: FileSystemEvent) -> None:
        """Handle one event. An exception is queued for the loop, which raises it as a warning."""
        # one exception would kill watchdog's only dispatcher thread, which serves every watch
        try:
            self.handle(event)
        except Exception as error:  # noqa: BLE001
            self.session.put(error)

    def handle(self, event: FileSystemEvent) -> None:
        """
        Queue the event with the time it arrived, unless it is counted into a dirasset's access.
        These events are dropped:
        - The synthetic move that watchdog reports for each file below a renamed folder, since the
          folder's own move repaths them.
        - An event below an ignored directory that no watched directory sits at or below.

        A deleted or moved directory first closes the accesses of the dirassets at or below it.
        """
        session = self.session
        seen = time.monotonic()
        session.last = seen
        if (
            event.is_synthetic
            and event.event_type == 'moved'
        ):
            return
        # watchdog types its paths as bytes | str. Every watch is scheduled with a str path, so
        # fsdecode returns each path unchanged
        source = os.fsdecode(event.src_path)
        destination = os.fsdecode(event.dest_path)
        if (
            self.is_ignored_directory(source, event.is_directory)
            and (
                not destination
                or self.is_ignored_directory(destination, event.is_directory)
            )
        ):
            return
        if (
            event.is_directory
            and event.event_type in ('deleted', 'moved')
            and session.enclosing(source) is None
        ):
            # queued ahead of the event that ends it, however far behind the loop is
            session.accesses.close_within(source)
        at = time.time()
        # Trail's own metadata read of a file is not an access
        if (
            event.event_type in ('opened', 'closed_no_write')
            and session.reads.is_own(source, at, at)
        ):
            return
        if self.count(event, source, destination, at, seen):
            return
        arrival = Arrival(at=at, event=event)
        session.put(arrival)

    def count(
            self,
            event: FileSystemEvent,
            source: str,
            destination: str,
            at: float,
            seen: float,
    ) -> bool:
        """
        Count an event inside a dirasset into its access. Returns True if nothing of the event is
        left to queue.
        - A file whose name matches the dirasset's children counts as its own event type.
        - A rename onto a child name, from inside the dirasset, counts as `modified`. This is how
          temporary files land.
        - A rename between two child names counts as `moved`.
        - A rename away from a child name counts as `deleted`.
        - A folder moved counts once as `moved`. Any other folder event inside is dropped, since the
          file events say the same.
        - A file that is not a child is queued like any other.
        """
        session = self.session
        # read once, since the loop may publish new roots meanwhile
        roots = session.roots
        if not roots:
            return False
        source_root = enclosing(source, roots)
        destination_root = None
        if destination:
            destination_root = enclosing(destination, roots)
        if (
            source_root is None
            and destination_root is None
        ):
            return False
        accesses = session.accesses
        if event.event_type != 'moved':
            if event.is_directory:
                return True
            if not self.is_child(roots[source_root], source):
                return False
            # an access closes after it goes quiet, so a dirasset has no use for closes
            if event.event_type in OPS:
                accesses.count(roots[source_root][0], event.event_type, at, seen)
            return True
        if event.is_directory:
            for root in dict.fromkeys((source_root, destination_root)):
                if root is not None:
                    accesses.count(roots[root][0], 'moved', at, seen)
            # an end outside every dirasset is an ordinary folder
            return (
                source_root is not None
                and destination_root is not None
            )
        is_source_child = (
            source_root is not None
            and self.is_child(roots[source_root], source)
        )
        is_destination_child = (
            destination_root is not None
            and self.is_child(roots[destination_root], destination)
        )
        if (
            is_source_child
            and is_destination_child
            and source_root == destination_root
        ):
            accesses.count(roots[source_root][0], 'moved', at, seen)
            return True
        if is_source_child:
            accesses.count(roots[source_root][0], 'deleted', at, seen)
        if is_destination_child:
            accesses.count(roots[destination_root][0], 'modified', at, seen)
        # a file moved from outside every dirasset may be a tracked asset that has to follow
        return (
            source_root is not None
            and is_destination_child
        )

    @staticmethod
    def is_child(
            root: tuple[str, re.Pattern[str]],
            path: str,
    ) -> bool:
        """Whether a path's name matches the children of a dirasset, given as its id and pattern."""
        _, children = root
        name = os.path.basename(path)
        return children.fullmatch(name) is not None

    def is_ignored_directory(
            self,
            path: str,
            is_directory: bool,
    ) -> bool:
        """
        Whether the path's directory is ignored by the published `.offtrail` rules. Watched paths
        and their parents are kept. Events inside directory assets are kept for their accesses.
        """
        session = self.session
        decide = session.ignore_rule
        if decide is None:
            return False
        if session.enclosing(path) is not None:
            return False
        directory = Path(path)
        if not is_directory:
            directory = directory.parent
        rule = decide(directory, True)
        if (
            rule is None
            or rule.startswith('!')
        ):
            return False
        out = not any(
            watched.is_relative_to(directory)
            for watched in session.protected
        )
        return out


class Session(Node):
    is_started = False
    pid: int

    @classmethod
    def descriptor(cls) -> Self:
        """
        Returns a session with no parent, for an owner to hold as `session`. It is never started.
        Each owner's session is kept in its `__dict__` under the same name.

        Reading `watchdog.session` returns the Watchdog's session, made on first read:
        >>> WatchdogSession.__get__

        Deleting it closes the session and drops it:
        >>> WatchdogSession.__delete__
        """
        out = cls.__new__(cls)
        return out

    def __get__(
            self,
            instance: Node | None,
            owner: type,
    ) -> Self:
        """
        The owner's session, made on first read. A forked child inherits a started run but none
        of its threads, so the run is forgotten there without being closed.
        """
        if instance is None:
            return self
        cache = instance.__dict__
        session = cache.get(self.__name__)
        if session is None:
            session = type(self)(instance)
            cache[self.__name__] = session
        elif (
            session.is_started
            and session.pid != os.getpid()
        ):
            session.is_started = False
        return session

    def __delete__(self, instance: Node) -> None:
        """
        Close the session and drop it. The next read makes a new one, with the class's settings.
        """
        session = instance.__dict__.pop(self.__name__, None)
        if session is not None:
            session.close()

    def __bool__(self) -> bool:
        """Whether a run has started in this process and has not closed."""
        out = (
            self.is_started
            and self.pid == os.getpid()
        )
        return out

    def close(self) -> None:
        raise NotImplementedError


class WatchdogSession(Session):
    """
    The watching of one Watchdog. It is made on first read of `watchdog.session` and kept. Each
    run, from `start` to `stop`, gets a new observer, its watches, the queue the observer thread
    fills, and the consumer task that empties it on the loop. `prepare` sets that state, and none
    of it exists before. The consumer applies what the run delivers to the Trail. Only `Handler`
    runs on the observer thread, and it never touches the Trail.

    - `bool(session)` says whether a run has started in this process and has not closed.
    - `is_active` says whether the run's consumer is alive.
    """

    _parent: Watchdog
    # seconds the consumer lets a burst of events grow before it consumes them
    debounce = 0.1
    # seconds without an event after which an access to a dirasset closes and is logged
    idle_gap = 30.0
    # seconds stop() waits after the last event, since watchdog holds a move out of a watch for
    # half a second to pair it
    settle = 0.55
    # seconds a file's session waits after a close, so an indexer's read folds into the write
    fold_settle = 1.0
    # seconds without an event after which a file's session ends without a close, as on platforms
    # that report no closes, or while a writer holds the file open
    fold_quiet = 10.0
    # whether a run has been prepared and not closed since
    is_started = False

    # the state of a run, set by prepare()
    loop: asyncio.AbstractEventLoop
    # guards the queue, the accesses and is_wake_pending, and nothing slow
    lock: threading.RLock
    queue: deque[object]
    is_wake_pending: bool
    # set by stop(), after which nothing may start a new run until it ends
    is_closing: bool
    woken: asyncio.Event
    handler: Handler
    observer: BaseObserver
    watches: Watches
    # monotonic seconds of the last event the observer thread received
    last: float
    # the watched paths and the ignore rules, published by the loop
    protected: frozenset[Path]
    ignore_rule: Callable[[Path, bool], str | None] | None
    # the root of each dirasset, with its id and its children, published by the loop
    roots: dict[str, tuple[str, re.Pattern[str]]]
    accesses: Accesses
    # the open session of each file path, kept by the consumer on the loop
    folds: dict[str, Fold]
    # the last size and mtime recorded for each file asset, by id
    stats: dict[str, tuple[int | None, float | None]]
    # the metadata reads that follow a write
    reads: Reads
    consumer: asyncio.Task[None] | None
    # a forked child inherits the run but none of its threads
    pid: int
    # registered with atexit while the run is open
    hook: Callable[[], None] | None

    @property
    def is_active(self) -> bool:
        """True while a consumer task is alive to drain the queue."""
        if not self:
            return False
        consumer = self.consumer
        out = (
            consumer is not None
            and not consumer.done()
        )
        return out

    def prepare(self, loop: asyncio.AbstractEventLoop) -> None:
        """
        Set the state of a new run on `loop`. No thread or task is started. `open` starts them.
        """
        self.loop = loop
        self.lock = threading.RLock()
        self.queue = deque()
        self.is_wake_pending = False
        self.is_closing = False
        self.woken = asyncio.Event()
        self.handler = Handler(self)
        self.observer = Observer()
        self.watches = Watches(self)
        self.last = 0.0
        self.protected = frozenset()
        self.ignore_rule = None
        self.roots = {}
        self.accesses = Accesses(self)
        self.folds = {}
        self.stats = {}
        self.reads = Reads(self)
        self.consumer = None
        self.pid = os.getpid()
        self.hook = None
        self.is_started = True

    def open(self) -> None:
        """Start the consumer task and the observer thread of a prepared run."""
        # an interpreter that exits without stop() still logs the accesses the run holds
        reference = weakref.ref(self)
        self.hook = partial(_exit, reference)
        atexit.register(self.hook)
        coroutine = self.run()
        self.consumer = self.loop.create_task(coroutine, name='watchdog-consumer')
        # started before the first watch, so a directory that vanished fails only its own
        self.observer.start()

    def start(self) -> bool:
        """
        Start a run on the running loop, and place the watches `desired` names. A Trail kept in a
        notebook cell or a REPL begins tracking the moment an asset is tracked. Returns True if the
        session is active afterward.
        - An active session is left as it is.
        - Nothing is started while the session is closing, or if no asyncio loop is running. The
          observer then stays dormant until the session is started on a loop.
        - A run whose consumer has ended is closed first. If the consumer raised, the exception is
          raised here, and the next call starts a new run.
        """
        if self:
            if self.is_closing:
                return False
            if self.is_active:
                return True
            consumer = self.consumer
            self.close()
            if (
                consumer is not None
                and not consumer.cancelled()
            ):
                failure = consumer.exception()
                if failure is not None:
                    raise failure
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        if not self._trail.lock.acquire(self):
            return False
        try:
            self.prepare(loop)
            self.open()
            self.place_watches()
            self.load_baselines()
        except BaseException:
            self.close()
            self._trail.lock.release(self)
            raise
        return True

    def reconcile(self) -> None:
        """
        Make the watches the ones `desired` names. An active session places them now. An inactive
        one is started if there is something to watch, and starting places them. Nothing is done
        while the session is closing.
        """
        if (
            self
            and self.is_closing
        ):
            return
        if self.is_active:
            self.place_watches()
            return
        if self._parent.desired():
            self.start()

    def load_baselines(self) -> None:
        """
        Check each asset against the baseline the last run kept, after the watches are placed. An
        asset with no reader only needs a stat, which opens nothing, so it is checked now. An
        asset with a reader is read in the background, behind any asset that changes, if it has
        no baseline or its stat changed.
        """
        readable = []
        assets = list(self._trail.assets)
        with (
            self._trail.baselines.batch(),
            self.reads.own(assets),
        ):
            for asset in assets:
                metadata = asset.metadata
                if type(metadata) is Metadata:
                    metadata.observe('startup')
                elif metadata.is_stale():
                    readable.append(asset)
        self.reads.load(readable)

    def place_watches(self) -> None:
        """Publish what the handler reads, then make the watches the ones `desired` names."""
        self.publish()
        desired = self._parent.desired()
        self.watches.match(desired)

    def publish(self) -> None:
        """
        Hand the handler what it reads on the observer thread:

        - `roots`, the root of each dirasset with its id and its children.
        - `protected`, the watched directories and parents of tracked assets.
        - `ignore_rule`, the rules and Dir roots that decide ignored directories.

        Each value is replaced whole, never changed in place.
        """
        self.roots = {
            str(asset.path): (asset.id, re.compile(asset.children, re.IGNORECASE))
            for asset in self._trail.assets
            if isinstance(asset, DirAsset)
        }
        self.protected = frozenset(self._parent.wanted())
        offtrail = self._trail.offtrail
        self.ignore_rule = partial(
            offtrail_decision,
            rules=tuple(offtrail),
            project=offtrail.project,
            roots=frozenset(self._trail.dirs.path2entry),
        )

    async def __aenter__(self) -> Self:
        """
        Start a run, or keep the active one. The context is optional, since `start` already runs
        when an asset is tracked under a running asyncio loop. Use it to drain the queue at a known
        point, not whenever the consumer happens to run. Stage changes after they appear in the log
        or after leaving the context.
        """
        self.start()
        return self

    async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
    ) -> None:
        """Stop the run, draining what it queued. See `stop`."""
        await self.stop()

    async def stop(self) -> None:
        """
        Stop the run and log what it holds. It waits until watchdog has delivered what it already
        read, stops the observer, then lets the consumer drain the queue. A run that is not active,
        or that runs on another loop, is closed without waiting.
        """
        if not self:
            return
        consumer = self.consumer
        if (
            consumer is None
            or consumer.done()
            or self.loop is not asyncio.get_running_loop()
        ):
            self.close()
            return
        self.is_closing = True
        # bounded, since a steady stream of events would otherwise hold it open
        deadline = time.monotonic() + self.settle + 1
        while True:
            now = time.monotonic()
            remaining = min(
                self.last + self.settle - now,
                deadline - now,
            )
            if remaining <= 0:
                break
            await asyncio.sleep(remaining)
        observer = self.observer
        deadline = time.monotonic() + 1
        # what the observer already read is dispatched to the handler before it stops
        while (
            observer.is_alive()
            and observer.event_queue.unfinished_tasks
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.01)
        await asyncio.to_thread(self.halt)
        self.accesses.close()
        self.put(STOP)
        try:
            await consumer
            # the reads that the last write sessions asked for
            await self.reads.drain()
        finally:
            self.close()

    def close(self) -> None:
        """
        End the run, whether or not its loop still runs. The threads are halted and the consumer is
        cancelled. The accesses still held are logged, since a run whose consumer has ended keeps
        counting until it is halted. Nothing is done if no run has started in this process.
        """
        if not self:
            return
        if self.hook is not None:
            atexit.unregister(self.hook)
        self.reads.close()
        self.halt()
        consumer = self.consumer
        if (
            consumer is not None
            and not consumer.done()
        ):
            # the loop the consumer runs on may have closed
            with suppress(RuntimeError):
                consumer.cancel()
        try:
            self.record_open()
            self._trail.baselines.save()
        finally:
            self.is_started = False
            self._trail.lock.release(self)

    async def run(self) -> None:
        """
        Consume what the run queues until STOP, and close accesses that have gone quiet. Each
        wakeup carries a burst of events. The debounce lets the burst grow before it is consumed.
        Accesses still open when the task is cancelled are logged on the way out.
        """
        accesses = self.accesses
        woken = self.woken
        try:
            while True:
                timeouts = []
                deadline = accesses.deadline()
                if deadline is not None:
                    timeouts.append(deadline - time.monotonic())
                deadline = self.fold_deadline()
                if deadline is not None:
                    timeouts.append(deadline - time.time())
                timeout = None
                if timeouts:
                    timeout = max(0.0, min(timeouts))
                with suppress(TimeoutError):
                    await asyncio.wait_for(woken.wait(), timeout)
                woken.clear()
                # stop() queues everything watchdog read before STOP, so there is no burst to
                # wait for
                if (
                    self.debounce
                    and not self.is_closing
                ):
                    await asyncio.sleep(self.debounce)
                accesses.close(before=time.monotonic() - self.idle_gap)
                # a failure in one burst is reported, and the next burst is still consumed
                try:
                    is_stopped = self.consume()
                except Exception as error:  # noqa: BLE001
                    warnings.warn(f"the watchdog consumer failed: {error!r}", stacklevel=2)
                    continue
                if is_stopped:
                    return
        finally:
            # cancelled by asyncio.run's teardown, as a script that never stops is. A consumer
            # cancelled after a later run started leaves that run's accesses alone
            if self.consumer is asyncio.current_task():
                self.record_open()

    def consume(self) -> bool:
        """
        Apply what the run queued, one item at a time, and return True at STOP. A closed access is
        logged. An arrival is applied to the Trail. An exception from the observer thread is raised
        as a warning. An event about a watched directory that arrives twice in one call is applied
        once. Items are taken one at a time so that a `flush` reached from here still finds every
        access queued after it.
        """
        trail = self._trail
        moved = False
        # a watched directory is reported by its own watch and by its parent's, so the same
        # event about it can arrive twice
        seen: set[tuple[str, str, str]] = set()
        stopped = False
        while True:
            item = self.pop()
            if item is None:
                break
            if item is STOP:
                stopped = True
                break
            if isinstance(item, Access):
                self.record(item)
                continue
            if isinstance(item, Exception):
                warnings.warn(f"the watchdog handler failed: {item!r}", stacklevel=2)
                continue
            event = self.arrival2event(item)
            if event.is_directory:
                # an access closed by this deletion or move is logged before it
                self.flush_within(event.src_path)
                key = (event.event_type, event.src_path, event.dest_path)
                if key not in seen:
                    seen.add(key)
                    self.apply_directory(event)
                continue
            if self.fold(event, item.at):
                continue
            entry = event.apply(trail)
            if entry is not None:
                moved |= event.event_type == "moved"
                if (
                    event.event_type == "moved"
                    and isinstance(entry, Asset)
                ):
                    self.reads.add(entry)
                continue
            # a file appearing that is not tracked is tracked if .ontrail takes it
            if event.event_type in ("created", "moved"):
                # the file may already be gone by the time the event is applied
                with suppress(FileNotFoundError):
                    trail.assets.scan(event.dest_path or event.src_path)
        # a file moved into a directory not yet watched needs that directory watched
        if moved:
            self.reconcile()
        self.end_folds(time.time())
        return stopped

    def fold(
            self,
            event: WatchdogEvent,
            at: float,
    ) -> bool:
        """
        Fold a file event into its path's session. Returns True if nothing of the event is left to
        apply.
        - A move or a deletion ends the sessions of its paths first, so they are logged before it.
        - A rename from an untracked path onto a tracked file is folded as a replacement of the
          file, since the file did not move. Its content was replaced.
        - Any other event on a tracked file is folded, as is a creation anywhere. The session of a
          new file decides whether `.ontrail` takes it when it ends.
        - An event on an untracked file that no session holds is dropped, as it was before.
        """
        trail = self._trail
        event_type = event.event_type
        source = Path(event.src_path).resolve()
        if event_type in ('moved', 'deleted'):
            self.end_write(source)
            if (
                event_type != 'moved'
                or not event.dest_path
            ):
                return False
            destination = Path(event.dest_path).resolve()
            self.end_write(destination)
            replaced = trail.assets.get(destination)
            if (
                trail.assets.get(source) is None
                and replaced is not None
                and not replaced.is_directory
            ):
                self.add_fold(str(destination), 'replaced', at)
                return True
            # a part renamed into place rewrites its shapefile
            shapefile = self.shapefile(destination)
            if (
                trail.assets.get(source) is None
                and shapefile is not None
            ):
                self.add_fold(str(shapefile), 'replaced', at)
                return True
            return False
        if event_type not in FOLDED:
            return False
        # trail: shapefile - a part's events belong to the session of its .shp
        shapefile = self.shapefile(source)
        if shapefile is not None:
            if event_type == 'created':
                event_type = 'modified'
            self.add_fold(str(shapefile), event_type, at)
            return True
        path = str(source)
        if path not in self.folds:
            if (
                source in trail.entries.untracked
                or trail.entries.is_metadata(source)
            ):
                return True
            asset = trail.assets.get(source)
            if (
                asset is None
                and event_type != 'created'
            ):
                return True
            if (
                asset is not None
                and asset.is_directory
            ):
                return False
        self.add_fold(path, event_type, at)
        return True

    def shapefile(self, path: Path) -> Path | None:
        """
        The .shp whose session an untracked shapefile part belongs to: a tracked .shp, or one
        whose session is open, as while a new shapefile is written. None for any other path.
        """
        if self._trail.assets.get(path) is not None:
            return None
        shp = sibling_shp(path)
        if shp is None:
            return None
        if (
            self._trail.assets.get(shp) is not None
            or str(shp) in self.folds
        ):
            return shp
        return None

    def flush_within(self, path: str) -> None:
        """Log the writes waiting for a read on assets at or below a directory."""
        trail = self._trail
        root = Path(path)
        for identifier in [*self.reads.taken, *self.reads.writes]:
            asset = trail.assets.id2entry.get(identifier)
            if (
                asset is None
                or asset.path == root
                or asset.path.is_relative_to(root)
            ):
                self.reads.flush(identifier)

    def end_write(self, path: Path) -> None:
        """Log a path's open session, and its write still waiting for a read, before a move or deletion."""
        self.end_fold(str(path))
        asset = self._trail.assets.get(path)
        if asset is not None:
            self.reads.flush(asset.id)

    def add_fold(
            self,
            path: str,
            event_type: str,
            at: float,
    ) -> None:
        """Fold one event into the path's session. An event past the session's deadline ends it first."""
        fold = self.folds.get(path)
        if (
            fold is not None
            and at > fold.deadline(self.fold_settle, self.fold_quiet)
        ):
            self.end_fold(path)
            fold = None
        if fold is None:
            fold = Fold(path=path, started=at, ended=at, last=at)
            self.folds[path] = fold
        fold.add(event_type, at)

    def fold_deadline(self) -> float | None:
        """The epoch time the next file session ends, or None when none is open."""
        deadlines = [
            fold.deadline(self.fold_settle, self.fold_quiet)
            for fold in self.folds.values()
        ]
        if not deadlines:
            return None
        return min(deadlines)

    def end_folds(self, now: float | None = None) -> None:
        """End the file sessions whose deadline has passed at `now`, or every session."""
        due = [
            path
            for path, fold in self.folds.items()
            if now is None
            or fold.deadline(self.fold_settle, self.fold_quiet) <= now
        ]
        for path in due:
            self.end_fold(path)

    def end_fold(self, path: str) -> FileAccessEvent | None:
        """
        End the session of a path and log it as one FileAccessEvent. A created file that is not
        tracked is scanned first, so `.ontrail` decides on the finished file, and its registration
        comes before the session. A session that an observer claims is not logged here.
        """
        fold = self.folds.pop(path, None)
        if fold is None:
            return None
        trail = self._trail
        source = Path(path)
        asset = trail.assets.get(source)
        if asset is None:
            if not fold.is_created:
                return None
            with suppress(FileNotFoundError):
                trail.assets.scan(source)
            asset = trail.assets.get(source)
            if asset is None:
                return None
        if asset.is_directory:
            return None
        try:
            metadata = source.stat()
            st_size = metadata.st_size
            st_mtime = metadata.st_mtime
        except OSError:
            st_size = None
            st_mtime = None
        is_changed = (st_size, st_mtime) != self.last_stat(asset)
        event = FileAccessEvent(
            src_path=path,
            event_type=fold.event_type(is_changed),
            timestamp=datetime.fromtimestamp(fold.ended, UTC),
            started=datetime.fromtimestamp(fold.started, UTC),
            st_size=st_size,
            st_mtime=st_mtime,
        )
        event.entry = asset
        self.stats[asset.id] = (st_size, st_mtime)
        is_write = event.event_type in ('created', 'modified')
        for observer in self._parent.observers:
            if observer(event):
                # the observer logs the write. Its read still follows, as a MetadataEvent
                if is_write:
                    self.reads.add(asset, is_immediate=True)
                return event
        if (
            is_write
            and type(asset.metadata) is not Metadata
        ):
            # the session waited for the writer to finish, so the summary is read now, and the
            # write is logged with what changed
            self.reads.add(asset, is_immediate=True, write=event)
            return event
        event.apply(trail)
        if is_write:
            # a file with no reader is only stat'ed, to keep its baseline current
            self.reads.add(asset, is_immediate=True)
        return event

    def last_stat(self, asset: Asset) -> tuple[int | None, float | None] | None:
        """
        The size and mtime last recorded for a file asset, or None if none was. A MetadataEvent
        keeps the mtime in nanoseconds. It is converted as `os.stat` converts it.
        """
        stat = self.stats.get(asset.id)
        if stat is not None:
            return stat
        events = self._trail.events
        for key in reversed(events.ids):
            event = events.data[key]
            if (
                event.entry is not asset
                or getattr(event, 'st_size', None) is None
            ):
                continue
            if not isinstance(event, MetadataEvent):
                out = (event.st_size, event.st_mtime)
                return out
            if event.st_mtime_ns is None:
                out = (event.st_size, None)
                return out
            seconds, nanoseconds = divmod(event.st_mtime_ns, 10**9)
            out = (event.st_size, seconds + nanoseconds * 1e-9)
            return out
        return None

    def record(self, access: Access) -> DirectoryAccessEvent | None:
        """
        Log a closed access as one DirectoryAccessEvent. The access of a dirasset no longer
        tracked is dropped.
        """
        trail = self._trail
        asset = trail.assets.id2entry.get(access.asset)
        if not isinstance(asset, DirAsset):
            return None
        event = access.to_event(asset)
        if (
            event.event_type == 'modified'
            and type(asset.metadata) is not Metadata
        ):
            # the access already waited for quiet. It is logged with what changed in the store
            self.reads.add(asset, is_immediate=True, write=event)
            return event
        event.apply(trail)
        return event

    def flush(self, asset: Asset) -> None:
        """
        Log a dirasset's accesses that are open or still queued, so they come before the
        deletion, move or untrack about to be logged. Nothing is done if the session is not
        started.
        """
        if not self:
            return
        self.end_fold(str(asset.path))
        for access in self.take(asset.id):
            self.record(access)
        # a write waiting for its read is logged before the event about to follow it
        self.reads.flush(asset.id)

    def record_open(self) -> None:
        """
        Log every file session the run still holds, then every access, open or queued, with one
        write. Nothing else it holds is consumed.
        """
        trail = self._trail
        self.end_folds()
        self.reads.flush()
        events = []
        for access in self.take():
            asset = trail.assets.id2entry.get(access.asset)
            if isinstance(asset, DirAsset):
                events.append(access.to_event(asset))
        if events:
            trail.events.extend(events)

    @staticmethod
    def arrival2event(arrival: Arrival) -> WatchdogEvent:
        """Returns the record of a watchdog event, stamped with the time it arrived."""
        event = arrival.event
        return WatchdogEvent(
            src_path=os.fsdecode(event.src_path),
            dest_path=os.fsdecode(event.dest_path),
            event_type=event.event_type,
            is_directory=event.is_directory,
            is_synthetic=event.is_synthetic,
            timestamp=datetime.fromtimestamp(arrival.at, UTC),
        )

    def apply_directory(self, event: WatchdogEvent) -> None:
        """
        Apply a directory event. The event is logged only if the directory is a Dir or a
        directory asset.
        - A created directory that is a tracked dirasset moved from another watch is applied as that
          move. The dirasset's access at the old path is logged first.
        - The watches are reconciled afterward, since a watched directory may have come or gone.
        - A creation or a move then scans the new path.
        - A move also repaths the assets that were under the old path.

        The handler closed the accesses that a deletion or a move ends when it saw the event. They
        are logged before the event. An access opened after the event is left open.
        """
        trail = self._trail
        source = Path(event.src_path).resolve()
        if event.event_type == "created":
            returned = self.returned(source)
            if returned is not None:
                self.flush(returned)
                event = WatchdogEvent(
                    src_path=str(returned.path),
                    dest_path=str(source),
                    event_type="moved",
                    is_directory=True,
                    is_synthetic=True,
                    timestamp=event.timestamp,
                )
                source = returned.path
        event.apply(trail)
        if event.event_type == "deleted":
            self.reconcile()
            return
        if (
                event.event_type == "moved"
                and event.dest_path
        ):
            destination = Path(event.dest_path).resolve()
            if not trail.entries.is_metadata(source):
                trail._moved(source, destination)
        elif event.event_type == "created":
            destination = source
        else:
            return
        self.reconcile()
        if trail.entries.is_metadata(destination):
            return
        # files written before the watch was placed never produce an event, so they are scanned
        with suppress(FileNotFoundError, NotADirectoryError):
            trail.assets.scan(destination)

    def returned(self, path: Path) -> DirAsset | None:
        """
        Returns the tracked dirasset that a created directory is, if it was moved here from another
        watch. watchdog reports such a move as a creation here and, half a second later, a deletion
        there. The dirasset is matched by its root's inode.
        """
        trail = self._trail
        if path in trail.assets:
            return None
        try:
            metadata = path.stat()
        except OSError:
            return None
        inode = (metadata.st_dev, metadata.st_ino)
        for asset in trail.assets:
            if (
                    isinstance(asset, DirAsset)
                    and asset.inode == inode
                    and asset.path != path
            ):
                return asset
        return None

    def put(self, item: object) -> None:
        """Queue an arrival, a closed access, an exception or STOP, and wake the consumer."""
        with self.lock:
            self.queue.append(item)
            self.wake()

    def pop(self) -> object | None:
        """
        Returns the oldest queued item, or None if the queue is empty. The pending wakeup is cleared
        only when the queue is found empty, so no item waits without a wakeup.
        """
        with self.lock:
            if self.queue:
                return self.queue.popleft()
            self.is_wake_pending = False
            return None

    def wake(self) -> None:
        """Wake the consumer once per burst. The caller holds the lock."""
        if self.is_wake_pending:
            return
        self.is_wake_pending = True
        # the loop may close while the observer thread still delivers
        with suppress(RuntimeError):
            self.loop.call_soon_threadsafe(self.woken.set)

    def enclosing(self, path: str) -> str | None:
        """Returns the root of the dirasset a path is strictly inside of, or None."""
        return enclosing(path, self.roots)

    def take(self, asset: str | None = None) -> list[Access]:
        """
        Close the open access of a dirasset, or of every one, and take its closed accesses out
        of the queue, oldest first.
        """
        with self.lock:
            self.accesses.close(asset)
            taken = [
                item
                for item in self.queue
                if isinstance(item, Access)
                and (
                    asset is None
                    or item.asset == asset
                )
            ]
            if taken:
                identities = {
                    id(item)
                    for item in taken
                }
                kept = [
                    item
                    for item in self.queue
                    if id(item) not in identities
                ]
                self.queue.clear()
                self.queue.extend(kept)
        return taken

    def halt(self) -> None:
        """
        Stop the observer and join its threads. It is safe to call twice, and from a loop that
        has ended.
        """
        self.is_closing = True
        observer = self.observer
        observer.stop()
        if (
            observer.is_alive()
            and observer is not threading.current_thread()
        ):
            observer.join(5)
        if observer.is_alive():
            raise RuntimeError(f"Failed to stop observer {observer}")


def _exit(reference: weakref.ref[WatchdogSession]) -> None:
    """
    Log the accesses a session still holds when the interpreter exits without stop(). A forked
    child, which inherits the session but none of its threads, logs nothing.
    """
    session = reference()
    if (
        session is None
        or not session
    ):
        return
    session.record_open()
    session._trail.baselines.save()
