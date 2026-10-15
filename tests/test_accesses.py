from __future__ import annotations

import asyncio
import io
import json
import sys
import threading
import time
import warnings
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import ExitStack, asynccontextmanager, contextmanager, redirect_stdout
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileOpenedEvent,
)

from trail import Trail, fmt
from trail.asset import DirAsset
from trail.cli.console import Console
from trail.event import DirectoryAccessEvent, Event, RemoveAssetEvent, WatchdogEvent
from trail.playground import GDB, Playground, Store, ZarrV3
from trail.session import Session
from trail.util import OPS

# no watchdog backend other than inotify reports a file being opened
needs_opens = pytest.mark.skipif(sys.platform != 'linux', reason='only inotify reports opens')
# the temporary name zarr writes a key to before renaming it into place
PARTIAL = '.0123456789abcdef0123456789abcdef.partial'


class StubLoop:
    """Stands in for the loop a session wakes."""

    def call_soon_threadsafe(
            self,
            callback: Callable[..., object],
            *args: object,
    ) -> None:
        pass


class TestDirectoryAccessEvent:
    """One access to a dirasset, as the log, a reload and the feed hold it."""

    @staticmethod
    @contextmanager
    def workspace() -> Iterator[tuple[Trail, DirAsset]]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            cube = root / 'cube.zarr'
            cube.mkdir()
            (cube / 'zarr.json').write_text('{}', encoding='utf-8')
            trail = Trail(root, markers=None)
            yield trail, trail.track(cube)

    @staticmethod
    def access(asset: DirAsset) -> DirectoryAccessEvent:
        ended = datetime.now(UTC)
        count = dict.fromkeys(OPS, 0)
        count['opened'] = 1
        count['modified'] = 72
        event = DirectoryAccessEvent(
            src_path=str(asset.path),
            event_type='modified',
            timestamp=ended,
            started=ended - timedelta(milliseconds=242),
            count=count,
        )
        event.entry = asset
        return event

    def test_an_access_survives_a_reload(self) -> None:
        with self.workspace() as (trail, asset):
            event = self.access(asset)
            assert event.apply(trail) is asset
            restored = Trail(trail.dir)
            replayed = restored.events[event.id]
            assert isinstance(replayed, DirectoryAccessEvent)
            assert replayed.entry is restored.assets[asset.id]
            assert replayed.started == event.started
            assert replayed.timestamp == event.timestamp
            assert replayed.count == event.count
            assert replayed.is_directory
            assert replayed.is_synthetic
            assert replayed.to_record() == event.to_record()

    def test_an_access_is_one_line_of_the_log(self) -> None:
        with self.workspace() as (trail, asset):
            event = self.access(asset)
            event.apply(trail)
            lines = trail.events.jsonl.path.read_text(encoding='utf-8').splitlines()
            record = json.loads(lines[-1])
            assert len(lines) == 2
            assert record == {
                'id': event.id,
                'timestamp': event.timestamp.isoformat(),
                'src_path': str(asset.path),
                'dest_path': '',
                'event_type': 'modified',
                'is_directory': True,
                'is_synthetic': True,
                'started': event.started.isoformat(),
                'count': {'opened': 1, 'created': 0, 'modified': 72, 'moved': 0, 'deleted': 0},
                'cls': 'DirectoryAccessEvent',
                'entry': asset.id,
            }

    def test_the_feed_renders_an_access(self) -> None:
        with ExitStack() as stack:
            trail, asset = stack.enter_context(self.workspace())
            pipe = stack.enter_context(create_pipe_input())
            stack.enter_context(create_app_session(input=pipe, output=DummyOutput()))
            console = Console(trail, asset.directory)
            event = self.access(asset)
            event.apply(trail)
            with redirect_stdout(io.StringIO()):
                console.run.drain()
            rows = [
                ''.join(
                    fragment[1]
                    for fragment in row
                )
                for row in console.feed.rows
            ]
            assert '1. DirectoryAccessEvent' in rows
            assert '    src_path: cube.zarr' in rows
            assert '    event_type: modified' in rows
            assert f'    started: {fmt.clock(event.started)}' in rows
            assert '    count: opened 1, modified 72' in rows
            assert repr(event).endswith(
                f'started={fmt.clock(event.started)}, count=opened 1, modified 72)'
            )

    def test_cls_finds_accesses_and_their_base(self) -> None:
        with self.workspace() as (trail, asset):
            event = self.access(asset)
            event.apply(trail)
            events = trail.events
            assert list(events.select('cls=DirectoryAccessEvent')) == [event]
            assert list(events.select('cls=WatchdogEvent')) == [event]
            assert len(events.select('cls=Event')) == 2
            assert list(events.select('event_type=modified')) == [event]
            assert list(events.select(f'started<{event.timestamp.isoformat()}')) == [event]


class TestAccesses:
    """
    Accesses counted and closed with explicit times, then logged by `consume`. No thread is
    involved, so counts and log order are exact.
    """

    @staticmethod
    @contextmanager
    def session(store: type[Store] | None = None) -> Iterator[tuple[Trail, DirAsset, Session]]:
        with Playground() as playground:
            if store is None:
                node = playground.zarr
            else:
                node = store(playground, 'store')
            node.create()
            trail = Trail(playground.path, markers=None)
            asset = trail.track(node.path)
            session = trail.watchdog.session
            session.prepare(StubLoop())
            session.publish()
            try:
                yield trail, asset, session
            finally:
                session.close()

    @staticmethod
    def accesses(trail: Trail) -> list[DirectoryAccessEvent]:
        return [
            event
            for event in trail.events
            if isinstance(event, DirectoryAccessEvent)
        ]

    def test_an_access_is_recorded_once(self) -> None:
        with self.session() as (trail, asset, session):
            session.accesses.count(asset.id, 'modified', 1000.0, 10.0)
            session.accesses.count(asset.id, 'modified', 1001.0, 11.0)
            session.accesses.close()
            assert not session.consume()
            [access] = self.accesses(trail)
            assert access.entry is asset
            assert access.count['modified'] == 2
            session.accesses.close()
            session.consume()
            assert self.accesses(trail) == [access]

    def test_an_access_closes_after_the_idle_gap(self) -> None:
        with self.session() as (trail, asset, session):
            idle_gap = trail.watchdog.session.idle_gap
            now = time.monotonic()
            session.accesses.count(asset.id, 'modified', 1000.0, now - idle_gap - 1)
            # closed by the consumer's deadline, as `run` closes it
            session.accesses.close(before=now - idle_gap)
            session.consume()
            assert len(self.accesses(trail)) == 1
            session.accesses.count(asset.id, 'modified', 2000.0, now)
            session.accesses.close(before=now - idle_gap)
            session.consume()
            assert len(self.accesses(trail)) == 1
            assert session.accesses.deadline() == now + idle_gap

    def test_a_gap_splits_an_access_even_when_the_loop_was_blocked(self) -> None:
        with self.session() as (trail, asset, session):
            idle_gap = trail.watchdog.session.idle_gap
            # the loop consumes nothing while all four are counted
            session.accesses.count(asset.id, 'modified', 1000.0, 10.0)
            session.accesses.count(asset.id, 'modified', 1001.0, 11.0)
            session.accesses.count(asset.id, 'opened', 1100.0, 12.0 + idle_gap)
            session.accesses.count(asset.id, 'opened', 1101.0, 13.0 + idle_gap)
            session.consume()
            [first] = self.accesses(trail)
            assert first.count['modified'] == 2
            assert first.count['opened'] == 0
            assert first.timestamp == datetime.fromtimestamp(1001.0, UTC)
            assert session.accesses[asset.id].count['opened'] == 2

    def test_an_access_starts_at_its_first_event_and_ends_at_its_last(self) -> None:
        with self.session() as (trail, asset, session):
            for at in (1000.0, 1002.5, 1005.0):
                session.accesses.count(asset.id, 'opened', at, at)
            session.accesses.close()
            session.consume()
            [access] = self.accesses(trail)
            assert access.started == datetime.fromtimestamp(1000.0, UTC)
            assert access.timestamp == datetime.fromtimestamp(1005.0, UTC)

    def test_a_temp_file_renamed_into_place_is_a_write(self) -> None:
        with self.session() as (trail, asset, session):
            chunk = asset.path / 'arr' / 'c' / '0' / '1'
            temporary = f'{chunk}{PARTIAL}'
            handler = session.handler
            handler.handle(FileCreatedEvent(temporary))
            handler.handle(FileModifiedEvent(temporary))
            handler.handle(FileMovedEvent(temporary, str(chunk)))
            session.accesses.close()
            session.consume()
            [access] = self.accesses(trail)
            assert access.event_type == 'modified'
            assert access.count == {
                'opened': 0,
                'created': 0,
                'modified': 1,
                'moved': 0,
                'deleted': 0,
            }
            # the temporary file is an ordinary file, and nothing tracks it
            assert len(trail.assets) == 1

    def test_lock_files_leave_a_read_a_read(self) -> None:
        with self.session(GDB) as (trail, asset, session):
            lock = str(asset.path / '_gdb.host.1234.5678.sr.lock')
            handler = session.handler
            handler.handle(FileCreatedEvent(lock))
            handler.handle(FileOpenedEvent(str(asset.path / 'a00000009.gdbtable')))
            handler.handle(FileOpenedEvent(str(asset.path / 'a00000009.gdbtablx')))
            handler.handle(FileDeletedEvent(lock))
            session.accesses.close()
            session.consume()
            [access] = self.accesses(trail)
            assert access.event_type == 'opened'
            assert access.count['opened'] == 2
            assert access.count['created'] == 0

    def test_an_access_of_only_opens_is_opened(self) -> None:
        with self.session() as (trail, asset, session):
            for name in ('zarr.json', 'arr/zarr.json', 'arr/c/0/0'):
                session.handler.handle(FileOpenedEvent(str(asset.path / name)))
            session.accesses.close()
            session.consume()
            [access] = self.accesses(trail)
            assert access.event_type == 'opened'
            assert access.count['opened'] == 3

    def test_an_empty_access_is_never_logged(self) -> None:
        with self.session() as (trail, asset, session):
            access = asset.access
            assert access.seen is None
            assert sum(access.count.values()) == 0
            assert session.accesses.deadline() is None
            session.accesses.close()
            assert not session.queue
            session.consume()
            assert not self.accesses(trail)

    def test_offtrailing_a_dirasset_records_its_access_first(self) -> None:
        with self.session() as (trail, asset, session):
            session.accesses.count(asset.id, 'modified', 1000.0, 10.0)
            trail.offtrail(asset.path)
            access = trail.events.select[-2]
            removal = trail.events.select[-1]
            assert isinstance(access, DirectoryAccessEvent)
            assert access.entry is asset
            assert isinstance(removal, RemoveAssetEvent)
            assert removal.entry is asset

    def test_clearing_the_events_drops_an_open_access(self) -> None:
        with self.session() as (trail, asset, session):
            session.accesses.count(asset.id, 'modified', 1000.0, 10.0)
            session.accesses.close()
            session.accesses.count(asset.id, 'modified', 2000.0, 20.0)
            trail.events.clear()
            assert not session.accesses
            assert not session.queue
            session.accesses.close()
            session.consume()
            assert not self.accesses(trail)


class TestAccessesWatched:
    """Accesses counted from real filesystem events. Counts vary, so they are only checked > 0."""

    @staticmethod
    @asynccontextmanager
    async def watching() -> AsyncIterator[Callable[..., Trail]]:
        """Opens Trails that react at once, and stops every one of them on the way out."""
        trails: list[Trail] = []

        def open(*args: object, **kwargs: object) -> Trail:
            trail = Trail(*args, **kwargs)
            trail.watchdog.session.idle_gap = 0.1
            trail.watchdog.session.debounce = 0
            trails.append(trail)
            return trail

        try:
            yield open
        finally:
            for trail in trails:
                await trail.watchdog.session.stop()

    @staticmethod
    async def until(predicate: Callable[[], bool]) -> None:
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(0.01)

    @staticmethod
    def accesses(trail: Trail, previous: int = 0) -> list[DirectoryAccessEvent]:
        return [
            event
            for event in trail.events.select[previous:]
            if isinstance(event, DirectoryAccessEvent)
        ]

    @staticmethod
    def mark(playground: Playground) -> None:
        (playground.path / '.markers').write_text('./\n*.zarr\n*.safe\n', encoding='utf-8')

    @needs_opens
    def test_reading_a_dirasset_records_one_opened_access(self) -> None:
        with Playground() as playground:
            self.mark(playground)
            playground.zarr.create()

            async def run() -> None:
                async with self.watching() as open:
                    trail = open(playground.path)
                    asset = trail.assets[playground.zarr.path]
                    previous = len(trail.events)
                    playground.zarr.read()
                    await self.until(lambda: self.accesses(trail, previous))
                    await asyncio.sleep(0.2)
                    [access] = self.accesses(trail, previous)
                    assert access.entry is asset
                    assert access.event_type == 'opened'
                    assert access.count['opened'] > 0
                    assert access.started <= access.timestamp

            asyncio.run(run())

    def test_writing_below_the_top_level_is_counted(self) -> None:
        with Playground() as playground:
            self.mark(playground)
            playground.zarr.create()

            async def run() -> None:
                async with self.watching() as open:
                    trail = open(playground.path)
                    previous = len(trail.events)
                    playground.zarr.write('arr/c/0/0', 'arr/c/1/0')
                    await self.until(lambda: self.accesses(trail, previous))
                    [access] = self.accesses(trail, previous)
                    assert access.event_type == 'modified'
                    assert access.count['modified'] + access.count['created'] > 0
                    # nothing inside reached the log as an event of its own
                    assert len(trail.events) == previous + 1

            asyncio.run(run())

    @needs_opens
    def test_concurrent_reads_merge_into_one_access(self) -> None:
        with Playground() as playground:
            self.mark(playground)
            playground.zarr.create()

            async def run() -> None:
                async with self.watching() as open:
                    trail = open(playground.path)
                    previous = len(trail.events)
                    readers = [
                        threading.Thread(target=playground.zarr.read, args=(name,))
                        for name in playground.zarr.files
                        for _ in range(3)
                    ]
                    for reader in readers:
                        reader.start()
                    for reader in readers:
                        reader.join()
                    await self.until(lambda: self.accesses(trail, previous))
                    await asyncio.sleep(0.2)
                    [access] = self.accesses(trail, previous)
                    assert access.event_type == 'opened'

            asyncio.run(run())

    @needs_opens
    def test_reading_a_band_of_a_safe_product_is_recorded(self) -> None:
        with Playground() as playground:
            self.mark(playground)
            safe = playground.safe
            safe.create()

            async def run() -> None:
                async with self.watching() as open:
                    trail = open(playground.path)
                    asset = trail.assets[safe.path]
                    previous = len(trail.events)
                    safe.read('GRANULE/L2A_T33UUP/IMG_DATA/R10m/T33UUP_B04_10m.jp2')
                    await self.until(lambda: self.accesses(trail, previous))
                    [access] = self.accesses(trail, previous)
                    assert access.entry is asset
                    assert access.event_type == 'opened'

            asyncio.run(run())

    def test_stopping_records_every_open_access(self) -> None:
        with Playground() as playground:
            self.mark(playground)
            playground.zarr.create()
            playground.gdb.create()

            async def run() -> None:
                trail = Trail(playground.path)
                trail.track(playground.gdb.path)
                trail.watchdog.session.debounce = 0
                # nothing closes by itself before stop()
                trail.watchdog.session.idle_gap = 60
                previous = len(trail.events)
                playground.zarr.write('arr/c/0/0')
                playground.gdb.write('a00000009.gdbtable')
                assets = [
                    trail.assets[playground.zarr.path],
                    trail.assets[playground.gdb.path],
                ]
                await self.until(
                    lambda: all(
                        asset.access.seen is not None
                        for asset in assets
                    )
                )
                await trail.watchdog.session.stop()
                entries = [
                    access.entry
                    for access in self.accesses(trail, previous)
                ]
                assert entries == assets

            asyncio.run(run())

    def test_a_session_left_running_logs_its_accesses_when_replaced(self) -> None:
        with Playground() as playground:
            self.mark(playground)
            playground.zarr.create()
            trail = Trail(playground.path)
            asset = trail.assets[playground.zarr.path]
            held = []

            async def leave() -> None:
                assert trail.watchdog.session.start()
                trail.watchdog.session.debounce = 0
                trail.watchdog.session.idle_gap = 60
                held.append(trail.watchdog.session.accesses)

            asyncio.run(leave())
            [left] = held
            # its consumer has ended, but its observer still counts
            playground.zarr.write('arr/c/0/0')
            deadline = time.monotonic() + 5
            while getattr(left.get(asset.id), 'seen', None) is None:
                assert time.monotonic() < deadline
                time.sleep(0.01)
            previous = len(trail.events)

            async def run() -> None:
                assert trail.watchdog.session.start()
                await trail.watchdog.session.stop()

            asyncio.run(run())
            [access] = self.accesses(trail, previous)
            assert access.entry is asset
            assert access.event_type == 'modified'

    def test_a_cancelled_consumer_records_its_accesses(self) -> None:
        with Playground() as playground:
            self.mark(playground)
            playground.zarr.create()
            trail = Trail(playground.path)
            asset = trail.assets[playground.zarr.path]
            previous = len(trail.events)

            async def run() -> None:
                assert trail.watchdog.session.start()
                trail.watchdog.session.debounce = 0
                trail.watchdog.session.idle_gap = 60
                playground.zarr.write('arr/c/0/0')
                await self.until(lambda: asset.access.seen is not None)

            # a script that never stops: asyncio.run cancels the consumer as it returns
            asyncio.run(run())
            try:
                [access] = self.accesses(trail, previous)
                assert access.entry is asset
                assert access.event_type == 'modified'
            finally:
                trail.watchdog.session.close()


class TestLifecycle:
    """A dirasset deleted, rewritten and moved while it is watched, and the accesses around it."""

    watching = staticmethod(TestAccessesWatched.watching)
    until = staticmethod(TestAccessesWatched.until)

    @staticmethod
    def kinds(events: list[Event]) -> list[str]:
        out = []
        for event in events:
            if isinstance(event, DirectoryAccessEvent):
                out.append('access')
            elif isinstance(event, WatchdogEvent):
                out.append(event.event_type)
            else:
                out.append(type(event).__name__)
        return out

    @staticmethod
    def mark(
            playground: Playground,
            *markers: str,
    ) -> None:
        text = ''.join(
            f'{marker}\n'
            for marker in markers
        )
        (playground.path / '.markers').write_text(text, encoding='utf-8')

    def test_an_access_is_recorded_before_the_dirasset_is_deleted(self) -> None:
        with Playground() as playground:
            self.mark(playground, './', '*.zarr')
            store = playground.zarr
            store.create()

            async def run() -> None:
                async with self.watching() as open:
                    trail = open(playground.path)
                    asset = trail.assets[store.path]
                    previous = len(trail.events)
                    store.write('arr/c/0/0')
                    store.remove()
                    await self.until(
                        lambda: 'deleted' in self.kinds(trail.events.select[previous:])
                    )
                    await asyncio.sleep(0.2)
                    recorded = list(trail.events.select[previous:])
                    assert self.kinds(recorded) == ['access', 'deleted']
                    assert all(
                        event.entry is asset
                        for event in recorded
                    )
                    assert recorded[0].count['deleted'] > 0
                    # it stays tracked, and missing
                    assert trail.assets[store.path] is asset

            asyncio.run(run())

    def test_rewriting_a_dirasset_in_place_keeps_its_id(self) -> None:
        with Playground() as playground:
            self.mark(playground, './', '*.zarr')
            store = playground.zarr
            store.create()

            async def run() -> None:
                async with self.watching() as open:
                    trail = open(playground.path)
                    asset = trail.assets[store.path]
                    previous = len(trail.events)
                    # as to_zarr(mode='w') does
                    store.rewrite()
                    await self.until(lambda: len(trail.events.select[previous:]) >= 4)
                    await asyncio.sleep(0.2)
                    recorded = list(trail.events.select[previous:])
                    assert self.kinds(recorded) == ['access', 'deleted', 'created', 'access']
                    assert all(
                        event.entry is asset
                        for event in recorded
                    )
                    assert recorded[0].count['deleted'] > 0
                    assert recorded[-1].count['created'] > 0
                    assert recorded[-1].count['deleted'] == 0
                    assert trail.assets[store.path] is asset

            asyncio.run(run())

    def test_a_dirasset_renamed_in_its_parent_keeps_its_id(self) -> None:
        with Playground() as playground:
            self.mark(playground, './', '*.zarr')
            store = playground.zarr
            store.create()
            renamed = playground.path / 'renamed.zarr'

            async def run() -> None:
                async with self.watching() as open:
                    trail = open(playground.path)
                    asset = trail.assets[store.path]
                    previous = len(trail.events)
                    store.write('arr/c/0/0')
                    store.path.rename(renamed)
                    await self.until(lambda: asset.path == renamed)
                    recorded = list(trail.events.select[previous:])
                    assert self.kinds(recorded) == ['access', 'moved']
                    assert recorded[0].src_path == str(store.path)
                    assert recorded[1].dest_path == str(renamed)
                    assert trail.assets[renamed] is asset
                restored = Trail(playground.path)
                assert restored.assets[renamed].id == asset.id

            asyncio.run(run())

    def test_a_dirasset_moved_between_watches_keeps_its_id(self) -> None:
        with Playground() as playground:
            # two Dirs and no project root, so each is a watch of its own
            self.mark(playground, 'a/', 'b/', '*.zarr')
            source = playground.subdir('a')
            source.create()
            playground.subdir('b').create()
            store = ZarrV3(source, 'cube')
            store.create()
            moved = playground.path / 'b' / 'cube.zarr'

            async def run() -> None:
                async with self.watching() as open:
                    trail = open(playground.path)
                    assert list(trail.watchdog.session.watches) == [
                        source.path,
                        playground.path / 'b',
                        playground.path,
                    ]
                    # the deletion at the source comes half a second after the creation
                    trail.watchdog.session.settle = 0.55
                    asset = trail.assets[store.path]
                    previous = len(trail.events)
                    store.path.rename(moved)
                    await self.until(lambda: asset.path == moved)
                    await trail.watchdog.session.stop()
                    recorded = list(trail.events.select[previous:])
                    # watchdog also reports each file of the store created at the destination,
                    # and those it reports after the loop has handed the new root to the handler
                    # are counted into an access there
                    kinds = self.kinds(recorded)
                    assert kinds[0] == 'moved'
                    assert set(kinds[1:]) <= {'access'}
                    assert all(
                        event.entry is asset
                        for event in recorded
                    )
                    for access in recorded[1:]:
                        assert access.src_path == str(moved)
                        assert access.count['deleted'] == 0
                    assert trail.assets[moved] is asset
                    assert asset.inode == (moved.stat().st_dev, moved.stat().st_ino)
                restored = Trail(playground.path)
                assert restored.assets[moved].id == asset.id

            asyncio.run(run())

    def test_an_ancestor_rename_records_the_access_at_the_old_path(self) -> None:
        with Playground() as playground:
            self.mark(playground, './', 'data/', '*.zarr')
            data = playground.subdir('data')
            data.create()
            store = ZarrV3(data, 'cube')
            store.create()
            old = store.path
            renamed = playground.path / 'renamed'

            async def run() -> None:
                async with self.watching() as open:
                    trail = open(playground.path)
                    asset = trail.assets[old]
                    previous = len(trail.events)
                    with warnings.catch_warnings(record=True):
                        warnings.simplefilter('always')
                        store.write('arr/c/0/0')
                        data.path.rename(renamed)
                        await self.until(lambda: asset.path == renamed / 'cube.zarr')
                    recorded = list(trail.events.select[previous:])
                    assert self.kinds(recorded)[0] == 'access'
                    assert recorded[0].src_path == str(old)
                    assert recorded[0].entry is asset
                    assert 'access' not in self.kinds(recorded)[1:]
                    assert recorded[-1].entry is asset
                    assert recorded[-1].dest_path == str(renamed / 'cube.zarr')

            asyncio.run(run())


class TestConsole:
    """What the console shows of accesses: the header's count, and the ones stop() logs."""

    until = staticmethod(TestAccessesWatched.until)

    @staticmethod
    def header(console: Console) -> str:
        return ''.join(
            fragment[1]
            for fragment in console.renderer.header()
        )

    def test_the_header_counts_open_accesses(self) -> None:
        with ExitStack() as stack:
            trail, asset, session = stack.enter_context(TestAccesses.session())
            pipe = stack.enter_context(create_pipe_input())
            stack.enter_context(create_app_session(input=pipe, output=DummyOutput()))
            console = Console(trail, asset.directory)
            assert 'accesses' not in self.header(console)
            # an empty access, made by reading one, is not open
            assert asset.access.seen is None
            assert 'accesses' not in self.header(console)
            session.accesses.count(asset.id, 'opened', 1000.0, 10.0)
            assert '  events 1  accesses 1  idle' in self.header(console)
            session.accesses.close()
            assert 'accesses' not in self.header(console)

    def test_the_feed_prints_the_accesses_stop_closed(self) -> None:
        with ExitStack() as stack:
            playground = stack.enter_context(Playground())
            store = playground.zarr
            store.create()
            pipe = stack.enter_context(create_pipe_input())
            stack.enter_context(create_app_session(input=pipe, output=DummyOutput()))
            trail = Trail(playground.path, markers=None)
            asset = trail.track(store.path)
            trail.watchdog.session.debounce = 0
            # nothing closes by itself, so stop() closes it as the console exits
            trail.watchdog.session.idle_gap = 60
            console = Console(trail, playground.path)

            async def run() -> None:
                running = asyncio.create_task(console.run())
                await self.until(
                    lambda: trail.watchdog.session.is_active
                    and console.application.is_running
                )
                store.write('arr/c/0/0')
                await self.until(lambda: asset.access.seen is not None)
                console.application.exit()
                async with asyncio.timeout(5):
                    await running

            with redirect_stdout(io.StringIO()):
                asyncio.run(run())
            rows = [
                ''.join(
                    fragment[1]
                    for fragment in row
                )
                for row in console.feed.rows
            ]
            assert '1. DirectoryAccessEvent' in rows
            assert '    event_type: modified' in rows


if __name__ == "__main__":
    suite_types = (
        TestDirectoryAccessEvent,
        TestAccesses,
        TestAccessesWatched,
        TestLifecycle,
        TestConsole,
    )
    for suite in (suite_type() for suite_type in suite_types):
        for name in dir(suite):
            if name.startswith('test_'):
                getattr(suite, name)()
                print(f"  ✓ {name.removeprefix('test_').replace('_', ' ')}")
