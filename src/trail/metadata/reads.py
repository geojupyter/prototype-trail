from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING

from trail.metadata import Unstable
from trail.node import Node

if TYPE_CHECKING:
    from trail.asset import Asset
    from trail.event import WatchdogEvent
    from trail.session import WatchdogSession


class Reads(Node):
    """
    The metadata reads of a watcher's run, reached as `trail.watchdog.session.reads`. The session
    adds an asset when a write session on it ends. A read runs `Metadata.capture` in a worker
    thread and `Metadata.accept` back on the loop.

    - `waiting` holds assets that changed. They are read before the `backlog`.
    - `backlog` holds assets that only need their baseline.
    - `reading` holds the read in flight for each asset, at most `concurrency` at once.
    - `again` holds assets that changed during their read. Each is read once more.
    - `windows` holds when Trail last read each path, so its own read is not logged as an open.
    - `writes` holds the folded record of the write each read follows. It is logged with the
      read's changes, or without them if the read fails or the record has to be logged first.

    Nothing is scheduled unless the run's loop is running.
    """

    _parent: WatchdogSession
    # seconds without a new request before an asset is read
    quiet = 0.25
    # the longest a steady stream of requests can postpone a read
    limit = 2.0
    concurrency = 2
    retries = 3
    # seconds a read waits first, so notifications already on their way are handled before its
    # window opens. Only notifications inside the window are taken for Trail's own read
    lead = 0.05
    # seconds a synchronous window stays open after its block
    grace = 0.2

    def __init__(self, parent: WatchdogSession) -> None:
        super().__init__(parent)
        self.timers: dict[str, asyncio.TimerHandle] = {}
        self.first: dict[str, float] = {}
        self.bases: dict[str, str] = {}
        self.waiting: dict[str, None] = {}
        self.backlog: dict[str, None] = {}
        self.reading: dict[str, asyncio.Task[None]] = {}
        self.again: set[str] = set()
        # epoch seconds a read of each path began and ended. The end is None during the read
        self.windows: dict[str, tuple[float, float | None]] = {}
        self.writes: dict[str, WatchdogEvent] = {}
        # the write each read in flight took when it began. A write that arrives during a read
        # waits in `writes` for the next read
        self.taken: dict[str, WatchdogEvent] = {}
        self.is_closed = False

    @property
    def is_live(self) -> bool:
        loop = self._parent.loop
        out = (
            not self.is_closed
            and isinstance(loop, asyncio.AbstractEventLoop)
            and loop.is_running()
        )
        return out

    def add(
            self,
            asset: Asset,
            basis: str = 'watchdog',
            *,
            is_immediate: bool = False,
            write: WatchdogEvent | None = None,
    ) -> None:
        """
        Read the asset once requests for it stop for `quiet` seconds, or now. `write` is the
        record of the write the read follows. It is logged by the read, so it is logged now if
        nothing will be read.
        """
        if not self.is_live:
            if write is not None:
                write.apply(self._trail)
            return
        if write is not None:
            # a write still waiting goes first, so the log keeps the writes in order
            self.flush(asset.id)
            self.writes[asset.id] = write
        self.bases[asset.id] = basis
        now = time.monotonic()
        first = self.first.setdefault(asset.id, now)
        delay = min(self.quiet, max(0.0, first + self.limit - now))
        if is_immediate:
            delay = 0.0
        timer = self.timers.pop(asset.id, None)
        if timer is not None:
            timer.cancel()
        self.timers[asset.id] = self._parent.loop.call_later(delay, self.due, asset.id)

    def load(self, assets: Iterable[Asset]) -> None:
        """Queue assets for their baselines, behind any asset that changed."""
        if not self.is_live:
            return
        for asset in assets:
            self.bases.setdefault(asset.id, 'startup')
            self.backlog[asset.id] = None
        self.fill()

    def due(self, identifier: str) -> None:
        self.timers.pop(identifier, None)
        self.first.pop(identifier, None)
        if identifier in self.reading:
            # requests arrived during the read, so the asset is read once more after it
            self.again.add(identifier)
            return
        self.backlog.pop(identifier, None)
        self.waiting[identifier] = None
        self.fill()

    def fill(self) -> None:
        if not self.is_live:
            return
        loop = self._parent.loop
        while len(self.reading) < self.concurrency:
            if self.waiting:
                queue = self.waiting
            elif self.backlog:
                queue = self.backlog
            else:
                break
            identifier = next(iter(queue))
            del queue[identifier]
            coroutine = self.read(identifier)
            self.reading[identifier] = loop.create_task(coroutine, name='metadata-read')

    async def read(self, identifier: str) -> None:
        trail = self._trail
        basis = self.bases.pop(identifier, 'watchdog')
        write = self.writes.pop(identifier, None)
        if write is not None:
            self.taken[identifier] = write
        try:
            for attempt in range(self.retries):
                asset = trail.assets.id2entry.get(identifier)
                if asset is None:
                    return
                metadata = asset.metadata
                await self.settle()
                started = time.time()
                # opened before anything is read: listing a format's sources can read its files,
                # as zarr does
                paths = [
                    str(path)
                    for path in (asset.path, *metadata.listing())
                ]
                for path in paths:
                    self.windows[path] = (started, None)
                try:
                    sources = metadata.sources()
                except OSError:
                    for path in paths:
                        self.windows[path] = (started, time.time())
                    return
                for path in sources:
                    paths.append(str(path))
                    self.windows[str(path)] = (started, None)
                try:
                    capture = await asyncio.to_thread(metadata.capture)
                except Unstable:
                    capture = None
                except OSError:
                    return
                except Exception as failure:  # noqa: BLE001
                    metadata.error = repr(failure)
                    return
                finally:
                    # the read's own notifications reach the handler before its window closes
                    with suppress(asyncio.CancelledError):
                        await self.settle()
                    ended = time.time()
                    for path in paths:
                        self.windows[path] = (started, ended)
                if capture is None:
                    await asyncio.sleep(self.quiet * (attempt + 1))
                    continue
                # another caller observed the asset during the read
                if capture.generation != metadata.generation:
                    continue
                # back on the loop: nothing else runs between the check and the append. A write
                # logged meanwhile, before a move or deletion, leaves its changes to a MetadataEvent
                metadata.accept(capture, basis, self.taken.pop(identifier, None))
                return
            metadata.error = 'The file kept changing while it was read'
        finally:
            # a write whose read failed is still logged, without changes
            leftover = self.taken.pop(identifier, None)
            if leftover is not None:
                leftover.apply(trail)
            self.reading.pop(identifier, None)
            if identifier in self.again:
                self.again.discard(identifier)
                self.waiting[identifier] = None
            self.fill()

    @contextmanager
    def own(self, assets: Iterable[Asset]) -> Iterator[None]:
        """
        Take the opens inside the block for Trail's own, on the assets whose format reads files
        to list its sources, as zarr does. A stat check of them opens those files. The window
        stays open `grace` seconds after the block, for notifications still on their way.
        """
        started = time.time()
        paths = [
            str(path)
            for asset in assets
            for path in asset.metadata.listing()
        ]
        for path in paths:
            self.windows[path] = (started, None)
        try:
            yield
        finally:
            ended = time.time() + self.grace
            for path in paths:
                self.windows[path] = (started, ended)

    async def settle(self) -> None:
        """Wait until the notifications already read from the kernel have reached the handler."""
        await asyncio.sleep(self.lead)
        observer = self._parent.observer
        deadline = time.monotonic() + 1
        # a halted observer leaves its queue as it was
        while (
            observer.is_alive()
            and observer.event_queue.unfinished_tasks
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.005)

    def is_own(
            self,
            path: str,
            started: float,
            ended: float,
            margin: float = 0.02,
    ) -> bool:
        """
        Whether events on a path from `started` to `ended` fall inside Trail's own read of it.
        `margin` allows for the notification arriving after the read ended.
        """
        window = self.windows.get(path)
        if window is None:
            return False
        begun, finished = window
        if finished is None:
            finished = time.time()
        out = (
            begun <= started
            and ended <= finished + margin
        )
        return out

    def flush(self, identifier: str | None = None) -> None:
        """
        Log the write records still waiting for an asset's read, or for every asset, including
        one whose read is in flight. Their reads still log what changed, as a MetadataEvent.
        """
        if identifier is None:
            identifiers = [*self.taken, *self.writes]
        else:
            identifiers = [identifier]
        for key in dict.fromkeys(identifiers):
            for pending in (self.taken, self.writes):
                write = pending.pop(key, None)
                if write is not None:
                    write.apply(self._trail)

    def drop(self, identifier: str) -> None:
        """Forget an asset that was untracked."""
        timer = self.timers.pop(identifier, None)
        if timer is not None:
            timer.cancel()
        self.first.pop(identifier, None)
        self.bases.pop(identifier, None)
        self.waiting.pop(identifier, None)
        self.backlog.pop(identifier, None)
        self.again.discard(identifier)

    def hurry(self) -> None:
        """End every wait now."""
        for identifier, timer in tuple(self.timers.items()):
            timer.cancel()
            self.due(identifier)

    async def drain(self) -> None:
        """
        End every wait, then wait for the reads in flight. Baselines not yet read are dropped, since
        a baseline read at the end of a run can log nothing.
        """
        self.backlog.clear()
        self.hurry()
        while self.reading:
            await asyncio.gather(*self.reading.values(), return_exceptions=True)

    def close(self) -> None:
        """Log the writes still waiting, cancel every timer and read, and refuse new work."""
        self.flush()
        self.is_closed = True
        for timer in self.timers.values():
            timer.cancel()
        for task in self.reading.values():
            task.cancel()
        self.timers.clear()
        self.first.clear()
        self.bases.clear()
        self.waiting.clear()
        self.backlog.clear()
        self.again.clear()
