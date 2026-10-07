from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from functools import cached_property
from os import fsdecode
from pathlib import Path
from typing import TYPE_CHECKING, Final, Self

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
from watchdog.observers.api import BaseObserver, ObservedWatch

from trail.event import WatchdogEvent
from trail.node import Node
from trail.util import list_repr

if TYPE_CHECKING:
    from trail.asset import Asset
    from trail.trail import Trail

STOP: Final = object()


class Handler(FileSystemEventHandler, Node):
    _parent: Watchdog

    def on_any_event(self, event: FileSystemEvent) -> None:
        watchdog = self._parent
        # observer threads outlive the session that scheduled them
        loop = watchdog.loop
        queue = watchdog.queue
        if not loop or not queue or loop.is_closed():
            return
        record = asdict(event)
        record["src_path"] = fsdecode(event.src_path)
        record["dest_path"] = fsdecode(event.dest_path)
        event = WatchdogEvent(**record)
        with suppress(RuntimeError):
            loop.call_soon_threadsafe(queue.put_nowait, event)


class Watchdog(Node):
    """
    Observes the directories `desired` names for filesystem events. Each watch is
    non-recursive, so that `.git`, `.venv` and the inside of directory assets can be left out.

    >>> trail: Trail
    >>> trail.watchdog
    Watchdog
        running: True
        watches: [
            /tmp/tmpp_bl05lq,
            /tmp,
            /tmp/tmpp_bl05lq/data,
        ]
    """

    _parent: Trail
    debounce = 0.1
    # directories listed by __repr__ before the remainder is summarized
    repr_limit = 10

    def __repr__(self) -> str:
        """
        Shows whether the watchdog runs, and its watches. While dormant, it shows the watches
        the next session will place.
        """
        if self.watches is None:
            directories = sorted(self.desired())
        else:
            directories = list(self.watches)
        lines = [
            type(self).__name__,
            f'    running: {self.running!r}',
        ]
        lines.extend(
            list_repr(
                'watches',
                (
                    str(directory)
                    for directory in directories[:self.repr_limit]
                ),
                len(directories),
            )
        )
        return '\n'.join(lines)

    @cached_property
    def handler(self) -> Handler:
        """Converts native filesystem events and schedules enqueueing on the asyncio loop."""
        return Handler(self)

    @cached_property
    def observer(self) -> BaseObserver | None:
        """Manages filesystem monitoring threads and dispatches events to the handler."""
        return None

    @cached_property
    def queue(self) -> asyncio.Queue[WatchdogEvent] | None:
        """Buffers events for the consumer; STOP ends consumption after pending events are applied."""
        return None

    @cached_property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        """Runs the consumer and accepts queue callbacks submitted from observer threads."""
        return None

    @cached_property
    def watches(self) -> dict[Path, ObservedWatch] | None:
        """Maps directories to scheduled watch handles to prevent duplicates and allow unscheduling."""
        return None

    @cached_property
    def consumer(self) -> asyncio.Task[None] | None:
        """Background asyncio task that batches queued events and applies them to the Trail."""
        return None

    def watch(self, directory: Path) -> None:
        """
        Place a non-recursive watch on a directory, starting a session if an asyncio loop is
        running. A missing or already watched directory is skipped. While dormant, nothing is
        placed, and the next session places whatever `desired` names.
        """
        if not directory.is_dir():
            return
        if not self.ensure() or directory in self.watches:
            return
        self.watches[directory] = self.observer.schedule(
            self.handler,
            str(directory),
            recursive=False,
            event_filter=[
                FileCreatedEvent,
                FileModifiedEvent,
                FileDeletedEvent,
                FileMovedEvent,
                FileOpenedEvent,
                DirCreatedEvent,
                DirDeletedEvent,
                DirMovedEvent,
            ],
        )

    def unwatch(self, directory: Path) -> None:
        """Remove the watch on a directory, if there is one."""
        if self.watches is None:
            return
        watch = self.watches.pop(directory, None)
        if watch is not None:
            with suppress(KeyError):
                self.observer.unschedule(watch)

    def invalidate(self, directory: Path) -> None:
        """Remove the watches at or below a directory that was deleted or moved away."""
        if self.watches is None:
            return
        for path in tuple(self.watches):
            if path.is_relative_to(directory):
                self.unwatch(path)

    def desired(self, within: Path | None = None) -> set[Path]:
        """
        Returns the directories to watch. A recursive Dir is watched with its subtree, pruned
        the way a scan prunes it, and any other Dir alone. The parent of each Dir is watched
        too, to see the Dir itself renamed. So is the parent of each asset, and the top level of
        each directory asset. With `within`, only the directories at or below it are returned.

        >>> project: Path
        >>> self.desired(project / 'data')
        {PosixPath('/tmp/tmpp_bl05lq/data')}
        """
        trail = self._trail
        out: set[Path] = set()
        for directory in trail.dirs:
            path = directory.path
            out.add(path.parent)
            if not directory.recursive:
                out.add(path)
            elif (
                within is None
                or path.is_relative_to(within)
            ):
                out.update(self._subtree(path))
            elif (
                within.is_relative_to(path)
                and self._reaches(path, within)
            ):
                out.update(self._subtree(within))
        for asset in trail.assets:
            out.add(asset.directory)
            if asset.is_directory:
                out.add(asset.path)
        return {
            path
            for path in out
            if (
                (
                    within is None
                    or path.is_relative_to(within)
                )
                and not trail._ignored(path)
                and path.is_dir()
            )
        }

    def _subtree(self, root: Path) -> list[Path]:
        """Returns a directory and every subfolder a scan would walk into."""
        descends = self._trail.assets._descends
        out = []
        for directory, dirnames, _ in os.walk(root):
            parent = Path(directory)
            out.append(parent)
            dirnames[:] = [
                name
                for name in dirnames
                if descends(parent / name)
            ]
        return out

    def _reaches(
        self,
        root: Path,
        path: Path,
    ) -> bool:
        """Whether a walk of `root` would descend as far as `path`."""
        descends = self._trail.assets._descends
        while path != root:
            if not descends(path):
                return False
            path = path.parent
        return True

    def reconcile(self) -> None:
        """
        Schedule the directories `desired` names that are not yet watched, and unschedule the
        watches it no longer names. While dormant, the next session schedules them instead.
        """
        desired = self.desired()
        if (
            not desired
            and not self.watches
        ):
            return
        if not self.ensure():
            return
        for path in tuple(self.watches):
            if path not in desired:
                self.unwatch(path)
        self.extend(desired)

    def extend(self, directories: Iterable[Path]) -> None:
        """Watch each directory given. See `watch`."""
        for directory in directories:
            self.watch(directory)

    @property
    def running(self) -> bool:
        """True while a consumer task is alive to drain the queue."""
        consumer = self.consumer
        return bool(consumer) and not consumer.done()

    def ensure(self) -> bool:
        """
        Start observing without a context manager, so a Trail kept alive in a notebook cell or a
        REPL begins tracking the moment an asset is tracked. Returns False when no asyncio loop is
        running, leaving the observer dormant until start() or context() is awaited.
        """
        if self.running:
            return True
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._start_session()
        return True

    def _start_session(self) -> None:
        """Builds the active components a Watchdog session"""
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        self.observer = Observer()
        self.watches = {}
        try:
            # created before the watches so a reentrant watch() sees a running Watchdog
            self.consumer = asyncio.create_task(self.apply(), name="watchdog-consumer")
            self.extend(self.desired())
            self.observer.start()
        except BaseException:
            self.observer.stop()
            if self.observer.is_alive():
                self.observer.join(5)
            if self.consumer:
                self.consumer.cancel()
            self.clear()
            raise

    async def start(self) -> None:
        """Start the observer and drain the queue of events."""
        consumer = self.consumer
        if consumer:
            if not consumer.done():
                return
            # surface whatever killed the consumer, then rebuild the session
            await consumer
            self.clear()
        self._start_session()

    async def stop(self) -> None:
        """Stop the observer and drain the queue of events."""
        if not self.running:
            self.clear()
            return
        observer = self.observer
        observer.stop()
        if observer.is_alive():
            observer.join(5)
        if observer.is_alive():
            raise RuntimeError(f"Failed to stop observer {observer}")
        # Drain callbacks submitted by the observer before closing the queue.
        await asyncio.sleep(0)
        self.queue.put_nowait(STOP)
        try:
            await self.consumer
        finally:
            self.clear()

    @asynccontextmanager
    async def context(self) -> AsyncIterator[Self]:
        """Observe asynchronously, draining queued changes when the context exits.

        Optional: ensure() already starts the observer when assets are tracked under a running
        asyncio loop. Use this context when the queue must be drained deterministically at a
        known point rather than whenever the consumer happens to be scheduled.

        Stage changes after they appear in the log or after leaving this context.
        """
        await self.start()
        try:
            yield self
        finally:
            await self.stop()

    def clear(self) -> None:
        """
        Forget the session. Native observer threads cannot be restarted, so the next start
        builds a new session, which places whatever `desired` names at that point.
        """
        self.consumer = None
        self.loop = None
        self.queue = None
        self.observer = None
        self.watches = None

    async def apply(self) -> None:
        """
        Iterate across the queue of events, applying them to the assets of the Trail so upcoming
        events may find their corresponding Asset.
        """
        # bound to this session's queue so a clear() cannot strand the drain
        queue = self.queue
        while True:
            first = await queue.get()
            if first is STOP:
                return
            if self.debounce:
                await asyncio.sleep(self.debounce)

            stopped = False
            batch = [first]
            while True:
                try:
                    event = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if event is STOP:
                    stopped = True
                    break
                batch.append(event)

            self.consume(batch)
            if stopped:
                return

    def consume(self, batch: list[WatchdogEvent]) -> None:
        """
        Apply a batch of events to the Trail. An event inside a directory asset is recorded
        once per batch against the asset: a read as `opened`, anything else as `modified`. An
        event about a watched directory that arrives twice in a batch is applied once.
        """
        trail = self._trail
        touched: dict[Asset, set[str]] = {}
        moved = False
        # a watched directory is reported by its own watch and by its parent's, so the same
        # event about it can arrive twice
        seen: set[tuple[str, str, str]] = set()
        for event in batch:
            enclosing = self._enclosing(event)
            if enclosing is not None:
                touched.setdefault(enclosing, set()).add(event.event_type)
                continue
            if event.is_directory:
                key = (event.event_type, event.src_path, event.dest_path)
                if key not in seen:
                    seen.add(key)
                    self._directory(event)
                continue
            if event.apply(trail) is not None:
                moved |= event.event_type == "moved"
                continue
            # a file appearing that is not tracked is tracked if .markers indicates it
            if event.event_type in ("created", "moved"):
                # the file may already be gone by the time the batch is applied
                with suppress(FileNotFoundError):
                    trail.assets.scan(event.dest_path or event.src_path)
        for asset, types in touched.items():
            if types == {"opened"}:
                event_type = "opened"
            else:
                event_type = "modified"
            collapsed = WatchdogEvent(
                src_path=str(asset.path),
                event_type=event_type,
                is_directory=True,
                is_synthetic=True,
            )
            collapsed.apply(trail)
        # a file moved into a directory not yet watched needs that directory watched
        if moved:
            self.reconcile()

    def _enclosing(self, event: WatchdogEvent) -> Asset | None:
        """Returns the directory asset an event happened inside of, or None."""
        path2asset = self._trail.assets.path2entry
        for text in (event.src_path, event.dest_path):
            if not text:
                continue
            for parent in Path(text).parents:
                asset = path2asset.get(parent)
                if (
                    asset is not None
                    and asset.is_directory
                ):
                    return asset
        return None

    def _directory(self, event: WatchdogEvent) -> None:
        """
        Apply a directory event. It is logged only if the directory is a directory asset. A
        deletion removes the watches at and below the directory. A creation or a move watches
        what `desired` names below the new path, then scans it. A move also repaths the assets
        that were under the old path.
        """
        trail = self._trail
        event.apply(trail)
        source = Path(event.src_path).resolve()
        if event.event_type == "deleted":
            self.invalidate(source)
            return
        if (
            event.event_type == "moved"
            and event.dest_path
        ):
            destination = Path(event.dest_path).resolve()
            if not trail._ignored(source):
                trail._moved(source, destination)
            self.invalidate(source)
        elif event.event_type == "created":
            destination = source
        else:
            return
        if trail._ignored(destination):
            return
        # files written before the watch was placed never produce an event, so they are scanned
        self.extend(self.desired(destination))
        with suppress(FileNotFoundError, NotADirectoryError):
            trail.assets.scan(destination)
