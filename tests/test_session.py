from __future__ import annotations

import asyncio
import threading
import time
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from watchdog.events import FileModifiedEvent, FileOpenedEvent

from trail import Trail
from trail.event import AddAssetEvent, Event, WatchdogEvent
from trail.session import Arrival


class StubLoop:
    """Stands in for the loop a session wakes, and counts the wakeups."""

    def __init__(self) -> None:
        self.wakeups = 0

    def call_soon_threadsafe(
            self,
            callback: Callable[..., object],
            *args: object,
    ) -> None:
        self.wakeups += 1


class TestSession:
    """How events reach the loop from watchdog's thread, and how a session ends."""

    @staticmethod
    @contextmanager
    def workspace() -> Iterator[Path]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for name in ('a.csv', 'b.csv', 'c.csv'):
                (root / name).write_text(f'{name}\n', encoding='utf-8')
            yield root

    @staticmethod
    async def until(predicate: Callable[[], bool]) -> None:
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(0.01)

    @staticmethod
    def deletions(events: list[Event]) -> list[Event]:
        return [
            event
            for event in events
            if isinstance(event, WatchdogEvent)
            and event.event_type == 'deleted'
        ]

    def test_an_event_is_stamped_when_it_arrives(self) -> None:
        with self.workspace() as root:
            csv = root / 'a.csv'
            trail = Trail(root, markers=None)
            entry = trail.track(csv)
            session = trail.watchdog.session
            session.prepare(StubLoop())
            moment = datetime(2026, 1, 2, 3, 4, 5, 678000, UTC)
            arrival = Arrival(at=moment.timestamp(), event=FileOpenedEvent(str(csv)))
            session.put(arrival)
            assert not session.consume()
            event = trail.events.select[-1]
            assert isinstance(event, WatchdogEvent)
            assert event.entry is entry
            assert event.event_type == 'opened'
            assert event.timestamp == moment

    def test_one_wakeup_carries_a_burst(self) -> None:
        with self.workspace() as root:
            trail = Trail(root, markers=None)
            loop = StubLoop()
            session = trail.watchdog.session
            session.prepare(loop)
            for _ in range(100):
                session.handler.on_any_event(FileModifiedEvent(str(root / 'a.csv')))
            assert loop.wakeups == 1
            drained = []
            while (item := session.pop()) is not None:
                drained.append(item)
            assert len(drained) == 100
            # the wakeup is spent once the queue is found empty, so the next burst gets its own
            session.handler.on_any_event(FileModifiedEvent(str(root / 'a.csv')))
            assert loop.wakeups == 2

    def test_a_session_left_running_is_stopped_before_the_next(self) -> None:
        with self.workspace() as root:
            csv = root / 'a.csv'
            trail = Trail(root, markers=None)
            trail.track(csv)
            observers = []

            async def leave() -> None:
                # a script that never stops leaves its run behind when asyncio.run returns
                assert trail.watchdog.session.start()
                observers.append(trail.watchdog.session.observer)

            asyncio.run(leave())
            [left] = observers
            assert left.is_alive()

            async def run() -> None:
                assert trail.watchdog.session.start()
                assert trail.watchdog.session.observer is not left
                assert not left.is_alive()
                previous = len(trail.events)
                csv.unlink()
                await self.until(lambda: self.deletions(trail.events.select[previous:]))
                await asyncio.sleep(0.2)
                await trail.watchdog.session.stop()
                assert len(self.deletions(trail.events.select[previous:])) == 1

            asyncio.run(run())

    def test_nothing_after_stop_starts_a_session(self) -> None:
        with self.workspace() as root:
            (root / '.markers').write_text('./\n*.csv\n', encoding='utf-8')

            async def run() -> None:
                trail = Trail(root)
                session = trail.watchdog.session
                assert session.is_active
                observer = session.observer
                # a new folder is applied while stop() drains, which places watches
                (root / 'folder').mkdir()
                (root / 'folder' / 'd.csv').write_text('d\n', encoding='utf-8')
                stopping = asyncio.create_task(session.stop())
                await asyncio.sleep(0)
                assert session.is_closing
                assert not session.start()
                session.reconcile()
                assert session.observer is observer
                await stopping
                assert not session
                assert not session.is_active
                assert not observer.is_alive()

            asyncio.run(run())

    def test_stop_keeps_the_events_watchdog_already_read(self) -> None:
        with self.workspace() as root:
            paths = sorted(root.iterdir())

            async def run() -> None:
                trail = Trail(root, markers=None)
                trail.track(*paths)
                session = trail.watchdog.session
                handle = session.handler.handle

                def slow(event: object) -> None:
                    time.sleep(0.1)
                    handle(event)

                # the first deletion is being handled when stop() begins, and the rest wait in
                # watchdog's own queue
                session.handler.handle = slow
                previous = len(trail.events)
                for path in paths:
                    path.unlink()
                await asyncio.sleep(0.05)
                await trail.watchdog.session.stop()
                assert len(self.deletions(trail.events.select[previous:])) == 3

            asyncio.run(run())

    def test_a_handler_exception_does_not_stop_dispatch(self) -> None:
        with self.workspace() as root:
            first = root / 'a.csv'
            second = root / 'b.csv'

            async def run() -> None:
                trail = Trail(root, markers=None)
                trail.track(first, second)
                trail.watchdog.session.debounce = 0
                session = trail.watchdog.session
                handle = session.handler.handle
                failed = []

                def flaky(event: object) -> None:
                    if not failed:
                        failed.append(event)
                        raise RuntimeError('the handler broke')
                    handle(event)

                session.handler.handle = flaky
                previous = len(trail.events)
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter('always')
                    first.unlink()
                    await self.until(lambda: bool(failed))
                    second.unlink()
                    await self.until(lambda: self.deletions(trail.events.select[previous:]))
                    await trail.watchdog.session.stop()
                [deleted] = self.deletions(trail.events.select[previous:])
                assert deleted.src_path == str(second)
                assert any(
                    'the handler broke' in str(warning.message)
                    for warning in caught
                )

            asyncio.run(run())

    def test_notifying_from_another_thread_wakes_a_watcher(self) -> None:
        with self.workspace() as root:
            csv = root / 'a.csv'

            async def run() -> None:
                trail = Trail()
                watching = trail.events.watch(timeout=5)

                async def wait() -> Event | None:
                    async for event in watching:
                        return event
                    return None

                waiting = asyncio.create_task(wait())
                # the watcher is suspended on its flag before the other thread appends
                await asyncio.sleep(0.05)
                thread = threading.Thread(target=trail.track, args=(csv,))
                thread.start()
                event = await asyncio.wait_for(waiting, 1)
                thread.join()
                assert isinstance(event, AddAssetEvent)
                assert event.entry is trail.assets[csv]

            asyncio.run(run())

    def test_notifying_a_closed_loop_raises_nothing(self) -> None:
        trail = Trail()
        loop = asyncio.new_event_loop()
        waiter = asyncio.Event()
        trail.events.watch.waiters[waiter] = loop
        loop.close()
        trail.events.watch.notify()
        assert not waiter.is_set()


if __name__ == "__main__":
    suite = TestSession()
    for name in dir(suite):
        if name.startswith('test_'):
            getattr(suite, name)()
            print(f"  ✓ {name.removeprefix('test_').replace('_', ' ')}")
