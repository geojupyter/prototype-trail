from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from watchdog.events import (
    FileClosedEvent,
    FileClosedNoWriteEvent,
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileOpenedEvent,
    FileSystemEvent,
)

from trail import Trail
from trail.event import (
    AddAssetEvent,
    Event,
    FileAccessEvent,
    MetadataEvent,
    RemoveAssetEvent,
    WatchdogEvent,
)
from trail.session import Arrival, WatchdogSession

needs_closes = pytest.mark.skipif(sys.platform != 'linux', reason='only inotify reports closes')
# an arbitrary moment in the past, so every session's deadline has passed when consume() ends
T0 = datetime(2026, 11, 4, 22, 30, 27, tzinfo=UTC).timestamp()


class StubLoop:
    def call_soon_threadsafe(
            self,
            callback: Callable[..., object],
            *args: object,
    ) -> None:
        pass


@contextmanager
def workspace(ontrail: str = './\n*.gpkg\n*.csv\n') -> Iterator[tuple[Path, Trail]]:
    with TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        (root / '.ontrail').write_text(ontrail, encoding='utf-8')
        trail = Trail(root)
        yield root, trail


def prepared(trail: Trail) -> WatchdogSession:
    # tracking a file reconciles the watches, which closes a run started without a loop
    session = trail.watchdog.session
    if not session:
        session.prepare(StubLoop())
    return session


def feed(
        trail: Trail,
        arrivals: list[tuple[float, FileSystemEvent]],
) -> None:
    session = prepared(trail)
    for offset, event in arrivals:
        session.put(Arrival(at=T0 + offset, event=event))
    session.consume()


def track(
        trail: Trail,
        path: Path,
        content: bytes = b'a\n',
) -> Path:
    path.write_bytes(content)
    trail.assets.scan(path)
    return path


def logged(trail: Trail) -> list[Event]:
    out = [
        event
        for event in trail.events
        if isinstance(event, AddAssetEvent | WatchdogEvent | RemoveAssetEvent)
        and not getattr(event, 'is_directory', False)
    ]
    return out


def write_gpkg(path: Path) -> list[tuple[float, FileSystemEvent]]:
    """The traced `to_file` of a new gpkg, with two of its twelve journal transactions."""
    journal = f'{path}-journal'
    name = str(path)
    out: list[tuple[float, FileSystemEvent]] = [
        (0.000, FileCreatedEvent(name)),
        (0.000, FileOpenedEvent(name)),
    ]
    for start in (0.009, 0.012):
        out.extend([
            (start, FileCreatedEvent(journal)),
            (start, FileOpenedEvent(journal)),
            (start, FileModifiedEvent(journal)),
            (start + 0.001, FileModifiedEvent(name)),
            (start + 0.002, FileClosedEvent(journal)),
            (start + 0.002, FileDeletedEvent(journal)),
        ])
    out.extend(
        (0.015 + step / 1000, FileModifiedEvent(name))
        for step in range(3)
    )
    out.append((0.018, FileClosedEvent(name)))
    return out


def write_shapefile(
        stem: Path,
        start: float,
        parts: tuple[str, ...] = ('.shp', '.shx', '.dbf', '.prj', '.cpg'),
        is_new: bool = True,
) -> list[tuple[float, FileSystemEvent]]:
    """A GDAL shapefile write: each part opened, written and closed in turn."""
    out: list[tuple[float, FileSystemEvent]] = []
    for offset, suffix in enumerate(parts):
        name = f'{stem}{suffix}'
        at = start + offset / 1000
        if is_new:
            out.append((at, FileCreatedEvent(name)))
        out.extend([
            (at, FileOpenedEvent(name)),
            (at + 0.0001, FileModifiedEvent(name)),
            (at + 0.0002, FileClosedEvent(name)),
        ])
    return out


class TestFolds:
    def test_a_new_gpkg_write_is_one_registration_and_one_created(self) -> None:
        with workspace() as (root, trail):
            path = root / 'streets_projected.gpkg'
            path.write_bytes(b'\0' * 4096)
            baloo = [
                (0.730, FileOpenedEvent(str(path))),
                (0.731, FileClosedNoWriteEvent(str(path))),
            ]
            feed(trail, write_gpkg(path) + baloo)
            added, folded = logged(trail)
            assert isinstance(added, AddAssetEvent)
            assert added.rule == '*.gpkg'
            assert added.st_size == 4096
            assert isinstance(folded, FileAccessEvent)
            assert folded.event_type == 'created'
            assert folded.entry is trail.assets[path]
            assert folded.started.timestamp() == pytest.approx(T0)
            # the indexer's read does not move the end of the write
            assert folded.timestamp.timestamp() == pytest.approx(T0 + 0.018)
            assert folded.st_size == 4096
            assert f'{path}-journal' not in {
                str(asset.path)
                for asset in trail.assets
            }

    def test_overwriting_a_tracked_file_is_one_modified(self) -> None:
        with workspace() as (root, trail):
            path = track(trail, root / 'streets.gpkg', b'old')
            name = str(path)
            arrivals = [
                (0.000, FileOpenedEvent(name)),
                (0.001, FileClosedEvent(name)),
                (0.002, FileOpenedEvent(name)),
            ]
            arrivals.extend(
                (0.003 + step / 1000, FileModifiedEvent(name))
                for step in range(8)
            )
            arrivals.append((0.012, FileClosedEvent(name)))
            path.write_bytes(b'new content')
            feed(trail, arrivals)
            [folded] = logged(trail)[1:]
            assert folded.event_type == 'modified'
            assert folded.st_size == len(b'new content')

    def test_a_read_is_one_opened(self) -> None:
        with workspace() as (root, trail):
            path = track(trail, root / 'a.csv')
            feed(trail, [
                (0.0, FileOpenedEvent(str(path))),
                (0.1, FileClosedNoWriteEvent(str(path))),
            ])
            [folded] = logged(trail)[1:]
            assert folded.event_type == 'opened'
            assert folded.started == folded.timestamp

    def test_a_write_close_without_a_change_is_opened(self) -> None:
        with workspace() as (root, trail):
            path = track(trail, root / 'a.csv')
            feed(trail, [
                (0.0, FileOpenedEvent(str(path))),
                (0.1, FileClosedEvent(str(path))),
            ])
            assert logged(trail)[-1].event_type == 'opened'
            # a write through a memory map raises no modification, only the close
            path.write_text('changed\n', encoding='utf-8')
            feed(trail, [
                (5.0, FileOpenedEvent(str(path))),
                (5.1, FileClosedEvent(str(path))),
            ])
            assert logged(trail)[-1].event_type == 'modified'

    def test_writes_after_the_settle_period_start_another_session(self) -> None:
        with workspace() as (root, trail):
            path = track(trail, root / 'a.csv')
            name = str(path)
            feed(trail, [
                (0.0, FileOpenedEvent(name)),
                (0.1, FileModifiedEvent(name)),
                (0.2, FileClosedEvent(name)),
                (0.9, FileOpenedEvent(name)),
                (1.0, FileModifiedEvent(name)),
                (1.1, FileClosedEvent(name)),
                (2.5, FileOpenedEvent(name)),
                (2.6, FileModifiedEvent(name)),
                (2.7, FileClosedEvent(name)),
            ])
            folded = logged(trail)[1:]
            assert [event.event_type for event in folded] == ['modified', 'modified']
            assert [event.timestamp.timestamp() for event in folded] == pytest.approx(
                [T0 + 1.1, T0 + 2.7],
            )

    def test_a_writer_that_keeps_the_file_open_ends_at_the_quiet_period(self) -> None:
        with workspace() as (root, trail):
            path = track(trail, root / 'a.csv')
            name = str(path)
            quiet = trail.watchdog.session.fold_quiet
            feed(trail, [
                (0.0, FileOpenedEvent(name)),
                (0.1, FileModifiedEvent(name)),
                (quiet - 1, FileModifiedEvent(name)),
                (2 * quiet + 1, FileModifiedEvent(name)),
            ])
            folded = logged(trail)[1:]
            assert [event.event_type for event in folded] == ['modified', 'modified']

    def test_a_rename_onto_a_tracked_file_is_modified(self) -> None:
        with workspace() as (root, trail):
            path = track(trail, root / 'a.csv')
            temporary = root / '.syncthing.a.csv.tmp'
            feed(trail, [
                (0.0, FileCreatedEvent(str(temporary))),
                (0.0, FileOpenedEvent(str(temporary))),
                (0.1, FileModifiedEvent(str(temporary))),
                (0.2, FileClosedEvent(str(temporary))),
                (0.3, FileMovedEvent(str(temporary), str(path))),
            ])
            folded = logged(trail)[1:]
            assert [event.event_type for event in folded] == ['modified']
            assert folded[0].entry is trail.assets[path]
            assert folded[0].src_path == str(path)

    def test_a_file_moved_in_from_outside_ends_at_the_settle_period(self) -> None:
        # inotify reports a rename from an unwatched folder as a creation with no open. QGIS
        # saves a qgz this way, from the temporary folder
        with workspace() as (root, trail):
            path = root / 'a.csv'
            path.write_bytes(b'a\n')
            name = str(path)
            settle = trail.watchdog.session.fold_settle
            feed(trail, [
                (0.0, FileCreatedEvent(name)),
                (settle + 0.5, FileCreatedEvent(name)),
            ])
            folded = [
                event
                for event in logged(trail)
                if isinstance(event, FileAccessEvent)
            ]
            assert [event.timestamp.timestamp() for event in folded] == pytest.approx(
                [T0, T0 + settle + 0.5],
            )

    def test_a_reopened_trail_compares_with_a_logged_metadata_record(self) -> None:
        # a MetadataEvent can be a file's last record. It keeps the mtime in nanoseconds
        with workspace() as (root, trail):
            path = track(trail, root / 'a.csv')
            stat = path.stat()
            event = MetadataEvent(
                src_path=str(path),
                format='csv',
                basis='startup',
                st_size=stat.st_size,
                st_mtime_ns=stat.st_mtime_ns,
            )
            event.entry = trail.assets[path]
            event.apply(trail)
            reopened = Trail(root)
            name = str(path)
            feed(reopened, [
                (0.0, FileOpenedEvent(name)),
                (0.1, FileClosedEvent(name)),
            ])
            # the close changed nothing, so it is a read
            assert logged(reopened)[-1].event_type == 'opened'

    def test_a_deletion_logs_the_open_session_first(self) -> None:
        with workspace() as (root, trail):
            path = track(trail, root / 'a.csv')
            name = str(path)
            feed(trail, [
                (0.0, FileOpenedEvent(name)),
                (0.1, FileModifiedEvent(name)),
                (0.2, FileDeletedEvent(name)),
            ])
            folded = logged(trail)[1:]
            assert [event.event_type for event in folded] == ['modified', 'deleted']

    def test_a_move_logs_the_open_session_at_the_old_path_first(self) -> None:
        with workspace() as (root, trail):
            path = track(trail, root / 'a.csv')
            asset = trail.assets[path]
            moved = root / 'b.csv'
            feed(trail, [
                (0.0, FileOpenedEvent(str(path))),
                (0.1, FileModifiedEvent(str(path))),
                (0.2, FileMovedEvent(str(path), str(moved))),
            ])
            folded = logged(trail)[1:]
            assert [event.event_type for event in folded] == ['modified', 'moved']
            assert folded[0].src_path == str(path)
            assert asset.path == moved

    def test_untracking_logs_the_open_session_first(self) -> None:
        with workspace() as (root, trail):
            path = track(trail, root / 'a.csv')
            session = prepared(trail)
            session.add_fold(str(path), 'opened', T0)
            session.add_fold(str(path), 'modified', T0 + 0.1)
            trail.offtrail(path)
            folded = logged(trail)[1:]
            assert [type(event) for event in folded] == [FileAccessEvent, RemoveAssetEvent]

    def test_the_end_of_a_run_logs_open_sessions(self) -> None:
        with workspace() as (root, trail):
            path = track(trail, root / 'a.csv')
            session = prepared(trail)
            now = datetime.now(UTC).timestamp()
            session.add_fold(str(path), 'opened', now)
            session.add_fold(str(path), 'modified', now)
            session.consume()
            assert len(logged(trail)) == 1
            session.record_open()
            assert logged(trail)[-1].event_type == 'modified'

    def test_an_untracked_file_is_dropped_and_a_rejected_creation_logs_nothing(self) -> None:
        with workspace() as (root, trail):
            notes = root / 'notes.txt'
            notes.write_text('a\n', encoding='utf-8')
            feed(trail, [
                (0.0, FileOpenedEvent(str(notes))),
                (0.1, FileCreatedEvent(str(root / 'b.txt'))),
            ])
            assert logged(trail) == []
            assert trail.watchdog.session.folds == {}

    def test_an_observer_can_take_a_session(self) -> None:
        with workspace() as (root, trail):
            path = track(trail, root / 'a.csv')
            taken = []

            def take(event: FileAccessEvent) -> bool:
                taken.append(event)
                return True

            trail.watchdog.observers.append(take)
            feed(trail, [
                (0.0, FileOpenedEvent(str(path))),
                (0.1, FileModifiedEvent(str(path))),
                (0.2, FileClosedEvent(str(path))),
            ])
            assert [event.event_type for event in taken] == ['modified']
            assert logged(trail)[1:] == []

    def test_a_session_survives_reload(self) -> None:
        with workspace() as (root, trail):
            path = root / 'new.csv'
            path.write_text('a\n', encoding='utf-8')
            feed(trail, [
                (0.0, FileCreatedEvent(str(path))),
                (0.1, FileModifiedEvent(str(path))),
                (0.2, FileClosedEvent(str(path))),
            ])
            folded = logged(trail)[-1]
            restored = Trail(root)
            rebuilt = restored.events[folded.id]
            assert isinstance(rebuilt, FileAccessEvent)
            assert rebuilt.to_record() == folded.to_record()
            assert rebuilt.entry is restored.assets[path]
            rows = dict(rebuilt._repr_items())
            assert rows['event_type'] == 'created'
            assert 'st_size' in rows


@needs_closes
def test_a_real_write_is_folded() -> None:
    async def run() -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / '.ontrail').write_text('./\n*.csv\n', encoding='utf-8')
            trail = Trail(root)
            session = trail.watchdog.session
            session.fold_settle = 0.2
            async with session:
                path = root / 'out.csv'
                with path.open('w', encoding='utf-8') as handle:
                    for row in range(200):
                        handle.write(f'{row},{row * 2}\n')
                        handle.flush()
                async with asyncio.timeout(5):
                    while not any(
                        isinstance(event, FileAccessEvent)
                        for event in trail.events
                    ):
                        await asyncio.sleep(0.05)
                # after Trail's own read of the new file, which is not logged
                await asyncio.sleep(0.3)
                path.read_text(encoding='utf-8')
                await asyncio.sleep(0.6)
            folded = [
                event
                for event in trail.events
                if isinstance(event, WatchdogEvent)
            ]
            assert [event.event_type for event in folded] == ['created', 'opened']
            assert folded[0].st_size == path.stat().st_size

    asyncio.run(run())



def test_a_shapefile_write_is_one_record_for_its_shp() -> None:
    with workspace('./\n*.shp\n') as (root, trail):
        stem = root / 'roads'
        for suffix in ('.shp', '.shx', '.dbf', '.prj', '.cpg'):
            Path(f'{stem}{suffix}').write_bytes(b'x')
        feed(trail, write_shapefile(stem, 0.0))
        assert [asset.path.name for asset in trail.assets] == ['roads.shp']
        added, created = logged(trail)
        assert isinstance(added, AddAssetEvent)
        assert (created.event_type, Path(created.src_path).name) == ('created', 'roads.shp')
        # a later write that only rewrites the table is a write of the shapefile
        feed(trail, write_shapefile(stem, 5.0, parts=('.dbf',), is_new=False))
        rewritten = logged(trail)[-1]
        assert (rewritten.event_type, Path(rewritten.src_path).name) == ('modified', 'roads.shp')
        # a part deleted on its own does not stand for the shapefile
        feed(trail, [(9.0, FileDeletedEvent(f'{stem}.cpg'))])
        assert len(logged(trail)) == 3
