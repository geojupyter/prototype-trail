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
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileOpenedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from trail.asset import DirAsset
from trail.event import DirectoryAccessEvent, WatchdogEvent
from trail.node import Node
from trail.util import OPS

if TYPE_CHECKING:
    from types import TracebackType

    from watchdog.observers.api import BaseObserver, ObservedWatch

    from trail.asset import Asset
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
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
]


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


class Accesses(UserDict[str, Access]):
    """
    The open access of each dirasset, by asset id. The observer thread counts into it and the loop
    closes it, so every change holds the session's lock. Reading a dirasset with no open access
    makes an empty one. An empty access is never logged.
    """

    def __init__(self, session: Session) -> None:
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

    def __init__(self, session: Session) -> None:
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

    def __init__(self, session: Session) -> None:
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
        - An event below a hidden directory that no watched directory sits at or below.

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
            self.is_hidden(source, event.is_directory)
            and (
                not destination
                or self.is_hidden(destination, event.is_directory)
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

    def is_hidden(
            self,
            path: str,
            is_directory: bool,
    ) -> bool:
        """
        Whether a path is a hidden directory, or is below one, that no watched directory sits at or
        below. A recursive watch sees inside `.git` and `.venv`, which a walk never enters. Without
        this check, a `pip install` would track every `*.json` it writes.
        """
        if is_directory:
            directory = path
        else:
            directory = path[:path.rfind(os.sep)]
        marker = os.sep + '.'
        start = directory.find(marker)
        # most paths have no hidden directory at all
        if start == -1:
            return False
        covered = self.session.hidden
        while start != -1:
            end = directory.find(os.sep, start + 1)
            if end == -1:
                end = len(directory)
            hidden = directory[:end]
            below = hidden + os.sep
            if not any(
                entry == hidden
                or entry.startswith(below)
                for entry in covered
            ):
                return True
            start = directory.find(marker, end)
        return False


class Session(Node):
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
    # the watched directories at or below a hidden directory, published by the loop
    hidden: frozenset[str]
    # the root of each dirasset, with its id and its children, published by the loop
    roots: dict[str, tuple[str, re.Pattern[str]]]
    accesses: Accesses
    consumer: asyncio.Task[None] | None
    # a forked child inherits the run but none of its threads
    pid: int
    # registered with atexit while the run is open
    hook: Callable[[], None] | None

    @classmethod
    def descriptor(cls) -> Self:
        """
        Returns a Session with no parent, for Watchdog to hold as `session`. It is never started.
        Each Watchdog's own session is kept in its `__dict__` under the same name.

        Reading `watchdog.session` returns the Watchdog's session, made on first read:
        >>> Session.__get__

        Deleting it closes the session and drops it:
        >>> Session.__delete__
        """
        out = cls.__new__(cls)
        return out

    def __get__(
            self,
            instance: Watchdog | None,
            owner: type,
    ) -> Session:
        """
        The Watchdog's session, made on first read. A forked child inherits a started run but none
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

    def __delete__(self, instance: Watchdog) -> None:
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
        self.hidden = frozenset()
        self.roots = {}
        self.accesses = Accesses(self)
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
        self.prepare(loop)
        try:
            self.open()
            self.place_watches()
        except BaseException:
            self.close()
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

    def place_watches(self) -> None:
        """Publish what the handler reads, then make the watches the ones `desired` names."""
        self.publish()
        desired = self._parent.desired()
        self.watches.match(desired)

    def publish(self) -> None:
        """
        Hand the handler what it reads on the observer thread:

        - `roots`, the root of each dirasset with its id and its children.
        - `hidden`, the watched directories at or below a hidden directory.

        Each value is replaced whole, never changed in place.
        """
        def is_dotted(path: Path) -> bool:
            out = any(
                part.startswith('.')
                for part in path.parts
            )
            return out

        self.roots = {
            str(asset.path): (asset.id, re.compile(asset.children, re.IGNORECASE))
            for asset in self._trail.assets
            if isinstance(asset, DirAsset)
        }
        self.hidden = frozenset(
            str(path)
            for path in self._parent.wanted()
            if is_dotted(path)
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
        self.halt()
        consumer = self.consumer
        if (
            consumer is not None
            and not consumer.done()
        ):
            # the loop the consumer runs on may have closed
            with suppress(RuntimeError):
                consumer.cancel()
        self.record_open()
        self.is_started = False

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
                timeout = None
                deadline = accesses.deadline()
                if deadline is not None:
                    timeout = max(0.0, deadline - time.monotonic())
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
                if self.consume():
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
                key = (event.event_type, event.src_path, event.dest_path)
                if key not in seen:
                    seen.add(key)
                    self.apply_directory(event)
                continue
            if event.apply(trail) is not None:
                moved |= event.event_type == "moved"
                continue
            # a file appearing that is not tracked is tracked if .markers indicates it
            if event.event_type in ("created", "moved"):
                # the file may already be gone by the time the event is applied
                with suppress(FileNotFoundError):
                    trail.assets.scan(event.dest_path or event.src_path)
        # a file moved into a directory not yet watched needs that directory watched
        if moved:
            self.reconcile()
        return stopped

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
        event.apply(trail)
        return event

    def flush(self, asset: Asset) -> None:
        """
        Log a dirasset's accesses that are open or still queued, so they come before the
        deletion, move or offtrail about to be logged. Nothing is done if the session is not
        started.
        """
        if not self:
            return
        for access in self.take(asset.id):
            self.record(access)

    def record_open(self) -> None:
        """
        Log every access the run still holds, open or queued, with one write. Nothing else it holds
        is consumed.
        """
        trail = self._trail
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
            if not trail._is_ignored(source):
                trail._moved(source, destination)
        elif event.event_type == "created":
            destination = source
        else:
            return
        self.reconcile()
        if trail._is_ignored(destination):
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


def _exit(reference: weakref.ref[Session]) -> None:
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
