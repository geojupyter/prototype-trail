from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from trail import Trail
from trail.asset import Asset, Assets
from trail.checkpoint import Checkpoint
from trail.event import (
    AddAssetEvent,
    AddDirEvent,
    Events,
    RemoveAssetEvent,
    RemoveDirEvent,
    WatchdogEvent,
)


class TestTrail:
    @staticmethod
    @contextmanager
    def workspace() -> Iterator[Path]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            csv = root / "dataset.csv"
            csv.write_text("name,value\nexample,42\n", encoding="utf-8")
            yield root

    @staticmethod
    def records(collection: Events | Assets) -> list:
        return list(collection)

    @staticmethod
    def mark(
            root: Path,
            *markers: str,
    ) -> None:
        text = ''.join(
            f'{marker}\n'
            for marker in markers
        )
        (root / '.markers').write_text(text, encoding='utf-8')

    @staticmethod
    def opened_event(
            trail: Trail,
            csv: Path,
            previous: int,
    ) -> WatchdogEvent | None:
        # the callers poll until this is not None
        return next((
            event
            for event in trail.events.select[previous:]
            if isinstance(event, WatchdogEvent)
               and event.get.event_type == "opened"
               and event.get.src_path == str(csv)
        ), None)

    def test_pathless_track_and_open(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                csv = root / "dataset.csv"
                trail = Trail()
                entry = trail.track(csv)
                addition = trail.events.select[0]
                assert isinstance(addition, AddAssetEvent)
                assert addition.entry is entry
                assert trail.watchdog.observer.is_alive()
                previous = len(trail.events)
                with csv.open(encoding="utf-8") as stream:
                    assert stream.read() == "name,value\nexample,42\n"
                async with asyncio.timeout(5):
                    while (
                            opened := self.opened_event(trail, csv, previous)
                    ) is None:
                        await asyncio.sleep(0.01)
                await trail.watchdog.stop()
                assert opened.entry is entry
                assert trail.dir is None
                assert trail.events.jsonl.path is None
                assert list(root.iterdir()) == [csv]

        asyncio.run(run())

    def test_passive_instantiation_observes_without_a_context(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                csv = root / "dataset.csv"
                trail = Trail()
                entry = trail.track(csv)
                assert trail.watchdog.running
                assert trail.watchdog.observer.is_alive()
                previous = len(trail.events)
                with csv.open(encoding="utf-8") as stream:
                    assert stream.read() == "name,value\nexample,42\n"
                async with asyncio.timeout(5):
                    while (opened := self.opened_event(trail, csv, previous)) is None:
                        await asyncio.sleep(0.01)
                await trail.watchdog.stop()
                assert opened.entry is entry
                assert not trail.watchdog.running

        asyncio.run(run())

    def test_passive_instantiation_is_dormant_without_a_loop(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            trail = Trail()
            entry = trail.track(csv)
            assert not trail.watchdog.ensure()
            assert not trail.watchdog.running
            assert root in trail.watchdog.desired()

            async def run() -> None:
                # the dormant membership becomes a live watch once a loop exists
                assert trail.watchdog.ensure()
                assert trail.watchdog.observer.is_alive()
                previous = len(trail.events)
                with csv.open(encoding='utf-8') as stream:
                    assert stream.read() == 'name,value\nexample,42\n'
                async with asyncio.timeout(5):
                    while (
                            opened := self.opened_event(trail, csv, previous)
                    ) is None:
                        await asyncio.sleep(0.01)
                await trail.watchdog.stop()
                assert opened.entry is entry

            asyncio.run(run())

    def test_watchdog_context_starts_and_stops_the_session(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            trail = Trail()
            entry = trail.track(csv)
            assert not trail.watchdog.running

            async def run() -> None:
                async with trail.watchdog.context():
                    assert trail.watchdog.running
                    observer = trail.watchdog.observer
                    assert observer.is_alive()
                    assert root in trail.watchdog.watches
                    previous = len(trail.events)
                    with csv.open(encoding='utf-8') as stream:
                        assert stream.read() == 'name,value\nexample,42\n'
                    async with asyncio.timeout(5):
                        while (
                                opened := self.opened_event(trail, csv, previous)
                        ) is None:
                            await asyncio.sleep(0.01)

                assert opened.entry is entry
                # the session is torn down, and the next one schedules what is desired again
                assert not trail.watchdog.running
                assert not observer.is_alive()
                assert trail.watchdog.observer is None
                assert trail.watchdog.queue is None
                assert trail.watchdog.watches is None
                assert root in trail.watchdog.desired()

                # native observers cannot restart, so a second context builds a new one
                async with trail.watchdog.context():
                    assert trail.watchdog.running
                    assert trail.watchdog.observer is not observer
                    assert trail.watchdog.observer.is_alive()
                    assert root in trail.watchdog.watches
                assert not trail.watchdog.running

            asyncio.run(run())

    def test_watchdog_context_adopts_a_passive_session(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                csv = root / 'dataset.csv'
                trail = Trail()
                trail.track(csv)
                assert trail.watchdog.running
                observer = trail.watchdog.observer

                # entering the context must adopt the running session, not build a second one
                async with trail.watchdog.context():
                    assert trail.watchdog.observer is observer
                    assert trail.watchdog.running

                assert not trail.watchdog.running
                assert not observer.is_alive()

        asyncio.run(run())

    def test_persistent_track_open_and_new_session(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                csv = root / "dataset.csv"
                trail = Trail(root)
                entry = trail.track(csv)
                previous = len(trail.events)
                with csv.open(encoding="utf-8") as stream:
                    assert stream.read() == "name,value\nexample,42\n"
                async with asyncio.timeout(5):
                    while (
                            opened := self.opened_event(trail, csv, previous)
                    ) is None:
                        await asyncio.sleep(0.01)
                # quiet this trail so the reloaded ones are the only writers
                await trail.watchdog.stop()
                assert opened.entry is entry
                records = [
                    event.to_record()
                    for event in trail.events
                ]
                stored = [
                    json.loads(line)
                    for line in trail.events.jsonl.path.read_text(
                        encoding="utf-8"
                    ).splitlines()
                ]
                assert stored == records
                process = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-c",
                    "import json, sys; from trail import Trail; "
                    "trail = Trail(sys.argv[1]); "
                    'print(json.dumps({"id": trail.id, '
                    '"events": [event.to_record() for event in trail.events], '
                    '"entry_id": trail.assets[sys.argv[2]].id}))',
                    str(root),
                    str(csv),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=5,
                )
                assert process.returncode == 0, stderr.decode()
                snapshot = json.loads(stdout)
                assert snapshot["id"] == trail.id
                assert snapshot["events"] == records
                assert snapshot["entry_id"] == entry.id
                restored = Trail(root)
                assert restored.id == trail.id
                assert [
                           event.to_record()
                           for event in restored.events
                       ] == records
                restored_entry = restored.assets[csv]
                assert restored_entry.id == entry.id
                # the first event adds the project directory's Dir
                assert all(
                    event.entry is restored_entry
                    for event in restored.events.select[1:]
                )
                previous = len(restored.events)
                with csv.open(encoding="utf-8") as stream:
                    assert stream.read() == "name,value\nexample,42\n"
                async with asyncio.timeout(5):
                    while (
                            reopened := self.opened_event(restored, csv, previous)
                    ) is None:
                        await asyncio.sleep(0.01)
                await restored.watchdog.stop()
                assert reopened.entry is restored_entry
                previous_ids = {
                    record["id"]
                    for record in records
                }
                assert reopened.id not in previous_ids
                third = Trail(root)
                assert reopened.id in third.events
                await third.watchdog.stop()

        asyncio.run(run())

    def test_offtrailed_file_stays_offtrailed_after_new_session(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                csv = root / 'dataset.csv'
                other_csv = root / 'other.csv'
                other_csv.write_text('name,value\nother,7\n', encoding='utf-8')
                trail = Trail(root)
                entry = trail.track(csv)
                other_entry = trail.track(other_csv)
                trail.offtrail(csv)
                await trail.watchdog.stop()
                restored = Trail(root)
                assert csv not in restored.assets
                removal = restored.events.select[-1]
                assert isinstance(removal, RemoveAssetEvent)
                assert removal.entry.id == entry.id
                previous = len(restored.events)

                with csv.open(encoding='utf-8') as stream:
                    assert stream.read() == 'name,value\nexample,42\n'
                with other_csv.open(encoding='utf-8') as stream:
                    assert stream.read() == 'name,value\nother,7\n'
                async with asyncio.timeout(5):
                    while (
                            opened := self.opened_event(restored, other_csv, previous)
                    ) is None:
                        await asyncio.sleep(0.01)
                await restored.watchdog.stop()

                assert opened.entry.id == other_entry.id
                assert csv not in restored.assets
                assert self.records(restored.events.select[previous:]) == [opened]
                reloaded = Trail(root)
                assert csv not in reloaded.assets
                await reloaded.watchdog.stop()

        asyncio.run(run())

    def test_a_new_trail_writes_its_project_directory_marker(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            assert trail.markers[0] == './'
            assert trail.dirs[root].root
            assert root / 'dataset.csv' in trail.assets
            # the project directory is a Dir, not an asset, and it is the first thing logged
            assert root not in trail.assets
            addition = trail.events.select[0]
            assert isinstance(addition, AddDirEvent)
            assert addition.entry is trail.dirs[root]
            assert addition.rule == './'
            # unmarking it is logged, and is not retroactive
            with pytest.warns(UserWarning, match="leaves 1 asset tracked; offtrail removes it"):
                assert trail.unmark('./') == ['./']
            removal = trail.events.select[-1]
            assert isinstance(removal, RemoveDirEvent)
            assert removal.entry is addition.entry
            assert root / 'dataset.csv' in trail.assets
            reloaded = Trail(root)
            assert root not in reloaded.dirs
            assert len(reloaded.events) == len(trail.events)

    def test_a_trail_without_markers_watches_nothing(self) -> None:
        with self.workspace() as root:
            trail = Trail(root, markers=None)
            assert not (root / '.markers').exists()
            assert not trail.dirs
            assert not trail.assets
            assert not trail.watchdog.desired()

    def test_a_subdirectory_opens_the_enclosing_trail(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            nested = root / 'a' / 'b'
            nested.mkdir(parents=True)
            opened = Trail(nested)
            assert opened.dir == root / '.trail'
            assert opened.id == trail.id
            assert not (nested / '.trail').exists()

    def test_the_nearest_trail_wins(self) -> None:
        with self.workspace() as root:
            inner = root / 'inner'
            inner.mkdir()
            Trail(root)
            Trail(inner / '.trail')
            nested = inner / 'deeper'
            nested.mkdir()
            assert Trail(nested).dir == inner / '.trail'

    def test_the_search_stops_at_a_ceiling_directory(self) -> None:
        with (
            self.workspace() as root,
            pytest.MonkeyPatch.context() as monkeypatch,
        ):
            Trail(root)
            ceiling = root / 'ceiling'
            nested = ceiling / 'project'
            nested.mkdir(parents=True)
            monkeypatch.setenv('TRAIL_CEILING_DIRECTORIES', str(ceiling))
            assert Trail(nested).dir == nested / '.trail'
            # the starting directory is searched even when it is a ceiling
            assert Trail(ceiling / '.trail').dir == ceiling / '.trail'
            assert Trail(ceiling).dir == ceiling / '.trail'

    def test_track_untrack_and_retrack_history_can_be_restored(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            trail = Trail(root)
            original_entry = trail.track(csv)
            trail.offtrail(csv)
            current_entry = trail.track(csv)
            assert current_entry.id != original_entry.id
            history = trail.events.jsonl.path.read_bytes()
            csv.unlink()

            restored = Trail(root)
            # the first event adds the project directory's Dir
            addition = restored.events.select[1]
            removal = restored.events.select[2]
            readdition = restored.events.select[3]
            assert isinstance(addition, AddAssetEvent)
            assert isinstance(removal, RemoveAssetEvent)
            assert isinstance(readdition, AddAssetEvent)
            # the first was picked up by the default markers, the second tracked by hand
            assert addition.rule == '*.csv'
            assert readdition.rule is None
            assert addition.entry is removal.entry
            assert addition.entry.id == original_entry.id
            assert readdition.entry is restored.assets[csv]
            assert readdition.entry.id == current_entry.id
            assert restored.events.jsonl.path.read_bytes() == history

    def test_deleted_csv_history_can_be_restored(self) -> None:
        async def run() -> None:
            # open a workspace
            with self.workspace() as root:
                # track the dataset.csv inside th workspace
                csv = root / "dataset.csv"
                trail = Trail(root)
                entry = trail.track(csv)
                previous = len(trail.events)
                with csv.open(encoding="utf-8") as stream:
                    assert stream.read() == "name,value\nexample,42\n"
                async with asyncio.timeout(5):
                    while self.opened_event(trail, csv, previous) is None:
                        await asyncio.sleep(0.01)
                # the deletion below must not be recorded
                await trail.watchdog.stop()
                records = [
                    event.to_record()
                    for event in trail.events
                ]
                csv.unlink()
                restored = Trail(root)
                assert restored.assets[csv].id == entry.id
                assert [
                           event.to_record()
                           for event in restored.events
                       ] == records
                await restored.watchdog.stop()

        asyncio.run(run())

    def test_select_slicing_after_tracking_changes(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            other_csv = root / 'other.csv'
            other_csv.write_text('name,value\nother,7\n', encoding='utf-8')
            trail = Trail()
            positions = trail.events.select
            assert self.records(positions[:]) == []
            trail.track(csv)
            first = positions[-1]
            trail.track(other_csv)
            second = positions[-1]
            trail.offtrail(csv)
            third = positions[-1]
            trail.track(csv)
            fourth = positions[-1]

            assert positions[0] is first
            assert positions[-1] is fourth
            assert self.records(positions[:]) == [first, second, third, fourth]
            assert self.records(positions[1:3]) == [second, third]
            assert self.records(positions[:-1]) == [first, second, third]
            assert self.records(positions[::2]) == [first, third]
            assert self.records(positions[::-1]) == [fourth, third, second, first]
            assert self.records(positions[10:]) == []
            assert len(positions[:]) == len(trail.events)
            assert trail.events.ids == [first.id, second.id, third.id, fourth.id]
            assert trail.events[first.id] is positions[0]
            assert isinstance(third, RemoveAssetEvent)

            trail.track(csv)
            assert self.records(positions[:]) == [first, second, third, fourth]
            trail.offtrail(other_csv)
            assert len(positions[:]) == 5
            assert isinstance(positions[-1], RemoveAssetEvent)
            assert positions[-1].entry is second.entry
            assert trail.events.ids[-1] == positions[-1].id

            try:
                positions[10]
            except IndexError:
                pass
            else:
                raise AssertionError('out-of-range positions must raise IndexError')

            try:
                positions[::0]
            except ValueError:
                pass
            else:
                raise AssertionError('zero slice steps must raise ValueError')

    def test_track_and_offtrail_return_assets_or_lists(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            other_csv = root / 'other.csv'
            other_csv.write_text('name,value\nother,7\n', encoding='utf-8')
            missing_csv = root / 'missing.csv'
            trail = Trail()

            assert trail.track() == []
            assert trail.offtrail() == []
            assert trail.offtrail(missing_csv) == []
            first = trail.track(csv)
            assert first is trail.assets[csv]
            assert trail.track(csv) is first
            assert len(trail.events) == 1
            assert trail.track(csv, csv) == [first]
            assert len(trail.events) == 1

            added = trail.track(other_csv, csv, other_csv)
            second = trail.assets[other_csv]
            assert isinstance(added, list)
            assert added == [second, first]
            assert len(trail.events) == 2
            assert trail.offtrail(csv) is first
            assert trail.offtrail(csv) == []
            assert len(trail.events) == 3
            assert trail.offtrail(missing_csv, other_csv) == [second]
            assert len(trail.assets) == 0

            readded = trail.track(csv, other_csv)
            assert isinstance(readded, list)
            assert readded == [trail.assets[csv], trail.assets[other_csv]]
            removed = trail.offtrail(other_csv, csv, other_csv)
            assert isinstance(removed, list)
            assert removed == readded[::-1]
            assert len(trail.assets) == 0

    def test_select_survives_file_changes_and_reload(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                csv = root / 'dataset.csv'
                trail = Trail(root)
                entry = trail.track(csv)
                previous = len(trail.events)
                with csv.open(encoding='utf-8') as stream:
                    assert stream.read() == 'name,value\nexample,42\n'
                async with asyncio.timeout(5):
                    while self.opened_event(trail, csv, previous) is None:
                        await asyncio.sleep(0.01)
                previous = len(trail.events)
                csv.write_text('name,value\nexample,43\n', encoding='utf-8')
                async with asyncio.timeout(5):
                    while not any(
                            event.event_type == 'modified'
                            and event.src_path == str(csv)
                            for event in trail.events.select[previous:]
                    ):
                        await asyncio.sleep(0.01)
                previous = len(trail.events)
                csv.unlink()
                async with asyncio.timeout(5):
                    while not any(
                            event.event_type == 'deleted'
                            and event.src_path == str(csv)
                            for event in trail.events.select[previous:]
                    ):
                        await asyncio.sleep(0.01)
                await trail.watchdog.stop()

                assert trail.events.select[-1].event_type == 'deleted'
                # the first event adds the project directory's Dir
                assert all(
                    event.entry is entry
                    for event in trail.events.select[1:]
                )
                history = trail.events.jsonl.path.read_bytes()
                expected_ids = trail.events.ids.copy()
                restored = Trail(root)
                assert restored.events.ids == expected_ids
                assert restored.events.select[0].id == expected_ids[0]
                assert restored.events.select[-1].id == expected_ids[-1]
                assert [
                           event.id
                           for event in restored.events.select[::-1]
                       ] == expected_ids[::-1]
                assert restored.assets[csv].id == entry.id
                restored.events.jsonl.read()
                assert restored.events.ids == expected_ids
                assert restored.events.jsonl.path.read_bytes() == history
                await restored.watchdog.stop()

        asyncio.run(run())

    def test_asset_positions_follow_tracking_changes(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            other_csv = root / 'other.csv'
            other_csv.write_text('name,value\nother,7\n', encoding='utf-8')
            directory = root / 'subdirectory'
            directory.mkdir()
            trail = Trail()
            assets = trail.assets.select
            first = trail.track(csv)
            second = trail.track(other_csv)
            tracked_directory = trail.track(directory)

            assert tracked_directory.is_directory
            assert not first.is_directory
            assert self.records(assets[:]) == [first, second, tracked_directory]
            assert self.records(assets[::-1]) == [tracked_directory, second, first]
            assert trail.assets.ids == [first.id, second.id, tracked_directory.id]
            assert trail.assets[first.id] is assets[0]
            assert trail.assets[csv] is assets[0]
            assert trail.track(csv) is first
            assert self.records(assets[:]) == [first, second, tracked_directory]

            trail.offtrail(csv)
            assert self.records(assets[:]) == [second, tracked_directory]
            readded = trail.track(csv)
            assert readded.id != first.id
            # files and directories are interleaved in the order they were tracked
            assert self.records(assets[:]) == [second, tracked_directory, readded]
            assert trail.assets.ids == [second.id, tracked_directory.id, readded.id]
            trail.offtrail(other_csv, csv, directory)
            assert self.records(assets[:]) == []
            assert trail.assets.ids == []

    def test_assets_follow_a_directory_move(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            folder = root / 'folder'
            inner = folder / 'inner.csv'
            folder.mkdir()
            inner.write_text('name,value\ninner,1\n', encoding='utf-8')
            trail = Trail(root, markers=None)
            tracked = trail.track(inner, csv)

            renamed = root / 'renamed'
            folder.rename(renamed)
            trail._moved(folder, renamed)

            assert self.records(trail.assets) == tracked
            assert trail.assets[renamed / 'inner.csv'] is tracked[0]
            assert tracked[0].path == renamed / 'inner.csv'
            assert inner not in trail.assets
            # each repath is logged, so the move survives a reload
            restored = Trail(root)
            assert restored.assets.ids == trail.assets.ids
            assert restored.assets[renamed / 'inner.csv'].id == tracked[0].id

    def test_a_path_holds_one_asset_whichever_its_kind(self) -> None:
        with self.workspace() as root:
            path = root / 'subdirectory'
            path.mkdir()
            trail = Trail()
            directory = trail.track(path)
            assert directory.is_directory
            path.rmdir()
            path.write_text('name,value\nnow,1\n', encoding='utf-8')
            # the directory asset still holds the path, and tracking the file in its place
            # offtrails it
            asset = Asset.from_path(path, trail=trail).track()

            assert not asset.is_directory
            assert trail.assets[path] is asset
            assert self.records(trail.assets) == [asset]
            assert trail.assets.get(directory.id) is None

    def test_discovered_assets_survive_reload(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                self.mark(root, './', '*.tsv')
                trail = Trail(root)
                tsv = root / 'discovered.tsv'
                directory = root / 'discovered_directory'
                nested = directory / 'nested.tsv'
                tsv.write_text('name,value\nother,7\n', encoding='utf-8')
                directory.mkdir()
                nested.write_text('name,value\nnested,8\n', encoding='utf-8')
                async with asyncio.timeout(5):
                    while (
                            tsv not in trail.assets
                            or nested not in trail.assets
                    ):
                        await asyncio.sleep(0.01)
                # the removals below must not be recorded
                await trail.watchdog.stop()

                file_entry = trail.assets[tsv]
                nested_entry = trail.assets[nested]
                assert self.records(trail.assets) == [file_entry, nested_entry]
                # a new subfolder of a root is watched, but it is not a Dir
                assert list(trail.dirs.path2entry) == [root]
                assert directory in trail.watchdog.desired()
                rules = [
                    event.rule
                    for event in trail.events
                    if isinstance(event, AddAssetEvent)
                ]
                assert rules == ['*.tsv', '*.tsv']
                nested.unlink()
                tsv.unlink()
                directory.rmdir()
                history = trail.events.jsonl.path.read_bytes()
                restored = Trail(root)
                assert restored.assets.ids == trail.assets.ids
                assert restored.assets.select[0] is restored.assets[tsv]
                assert restored.assets.select[0].id == file_entry.id
                assert restored.assets.select[-1].id == nested_entry.id
                assert restored.events.jsonl.path.read_bytes() == history
                restored.offtrail(tsv, nested)
                assert restored.assets.ids == []
                reloaded = Trail(root)
                assert self.records(reloaded.assets) == []
                await restored.watchdog.stop()
                await reloaded.watchdog.stop()

        asyncio.run(run())

    def test_hex_ids_support_lookup_and_persistence(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            folder = root / 'folder'
            folder.mkdir()
            trail = Trail(root)
            entry = trail.track(csv)
            directory = trail.track(folder)
            checkpoint = Checkpoint(events=self.records(trail.events))
            identifiers = [
                trail.id,
                entry.id,
                directory.id,
                checkpoint.id,
                *trail.events.ids,
            ]
            for identifier in identifiers:
                assert isinstance(identifier, str)
                assert len(identifier) == 32
                assert set(identifier) <= set('0123456789abcdef')

            assert trail.assets[entry.id] is entry
            assert trail.assets[directory.id] is directory
            assert trail.assets[entry.id] is trail.assets[str(csv)]
            assert trail.assets[directory.id] is trail.assets[folder]
            assert entry.id in trail.assets
            assert directory.id in trail.assets
            assert trail.assets[[entry.id, directory.id]] == (entry, directory)
            checkpoint_record = checkpoint.to_record()
            assert checkpoint_record['events'] == trail.events.ids
            restored_checkpoint = Checkpoint.from_record(**checkpoint_record)
            assert restored_checkpoint.to_record() == checkpoint_record

            restored = Trail(root)
            assert restored.id == trail.id
            assert restored.assets[entry.id].path == csv
            assert restored.assets[directory.id].path == folder
            assert restored.assets[directory.id].is_directory
            assert restored.events.ids == trail.events.ids

    def test_legacy_integer_ids_are_normalized_on_reload(self) -> None:
        with self.workspace() as root:
            csv = root / 'dataset.csv'
            # without markers, the asset's event is the only one
            trail = Trail(root, markers=None)
            entry = trail.track(csv)
            event = trail.events.select[0]
            legacy_metadata = {'id': int(trail.id, 16)}
            trail.json.path.write_text(json.dumps(legacy_metadata), encoding='utf-8')
            legacy_event = event.to_record()
            legacy_event['id'] = int(event.id, 16)
            legacy_event['entry'] = int(entry.id, 16)
            trail.events.jsonl.path.write_text(
                json.dumps(legacy_event) + '\n',
                encoding='utf-8',
            )

            restored = Trail(root)
            assert restored.id == trail.id
            assert restored.assets.ids == [entry.id]
            assert restored.events.ids == [event.id]
            assert restored.assets[entry.id] is restored.assets.select[0]
            assert restored.events[event.id].entry is restored.assets[entry.id]
            restored.offtrail(csv)
            reloaded = Trail(root)
            assert reloaded.id == trail.id
            assert csv not in reloaded.assets
            assert reloaded.events.ids == restored.events.ids

    def test_folder_auto_tracks_created_files_and_nested_subdirectories(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                self.mark(root, '*.csv')
                trail = Trail(root)
                folder = root / 'folder'
                folder.mkdir()
                assert trail.mark('folder') == ['folder/']

                new_file = folder / 'new.csv'
                new_file.write_text('name,value\nnew,1\n', encoding='utf-8')
                async with asyncio.timeout(5):
                    while new_file not in trail.assets:
                        await asyncio.sleep(0.01)
                new_file_entry = trail.assets[new_file]

                previous = len(trail.events)
                with new_file.open(encoding='utf-8') as stream:
                    assert stream.read() == 'name,value\nnew,1\n'
                async with asyncio.timeout(5):
                    while (
                            opened := self.opened_event(trail, new_file, previous)
                    ) is None:
                        await asyncio.sleep(0.01)
                assert opened.entry is new_file_entry

                subdirectory = folder / 'nested'
                subdirectory.mkdir()
                nested_file = subdirectory / 'nested.csv'
                nested_file.write_text('name,value\nnested,2\n', encoding='utf-8')
                async with asyncio.timeout(5):
                    while nested_file not in trail.assets:
                        await asyncio.sleep(0.01)
                nested_file_entry = trail.assets[nested_file]
                await trail.watchdog.stop()

                # the dataset.csv beside the folder is outside every root
                assert root / 'dataset.csv' not in trail.assets
                assert new_file_entry.path == new_file
                assert nested_file_entry.path == nested_file
                # the new subfolder is watched, but gets no Dir
                assert list(trail.dirs.path2entry) == [folder]
                assert subdirectory in trail.watchdog.desired()

        asyncio.run(run())

    def test_renamed_file_keeps_its_resource_id(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                csv = root / 'dataset.csv'
                trail = Trail(root)
                entry = trail.track(csv)
                identifier = entry.id
                renamed = root / 'renamed.csv'
                previous = len(trail.events)
                csv.rename(renamed)
                async with asyncio.timeout(5):
                    while not any(
                            event.event_type == 'moved'
                            and event.dest_path == str(renamed)
                            for event in trail.events.select[previous:]
                    ):
                        await asyncio.sleep(0.01)
                await trail.watchdog.stop()

                assert trail.assets[renamed] is entry
                assert entry.id == identifier
                assert entry.path == renamed
                assert csv not in trail.assets
                assert trail.assets.ids == [identifier]

                restored = Trail(root)
                assert restored.assets.ids == [identifier]
                assert restored.assets[renamed].id == identifier
                assert csv not in restored.assets
                await restored.watchdog.stop()

        asyncio.run(run())

    def test_renamed_folder_repaths_its_contents(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                self.mark(root, './', '*.csv')
                folder = root / 'folder'
                nested = folder / 'nested'
                nested.mkdir(parents=True)
                inner = nested / 'inner.csv'
                inner.write_text('name,value\ninner,1\n', encoding='utf-8')
                trail = Trail(root)
                assert trail.watchdog.ensure()
                inner_entry = trail.assets[inner]
                identifiers = trail.assets.ids.copy()

                renamed = root / 'renamed'
                moved = renamed / 'nested' / 'inner.csv'
                folder.rename(renamed)
                async with asyncio.timeout(5):
                    while moved not in trail.assets:
                        await asyncio.sleep(0.01)
                await trail.watchdog.stop()

                assert trail.assets[moved] is inner_entry
                assert inner not in trail.assets
                assert trail.assets.ids == identifiers
                assert renamed / 'nested' in trail.watchdog.desired()
                assert nested not in trail.watchdog.desired()

                restored = Trail(root)
                assert restored.assets.ids == identifiers
                assert restored.assets[moved].id == inner_entry.id
                await restored.watchdog.stop()

        asyncio.run(run())

    def test_a_container_is_tracked_whole(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                self.mark(root, './', '*.zarr', '*.json')
                cube = root / 'cube.zarr'
                (cube / 'band').mkdir(parents=True)
                (cube / 'zarr.json').write_text('{}', encoding='utf-8')
                (cube / 'band' / '0').write_bytes(b'abc')
                trail = Trail(root)
                asset = trail.assets[cube]
                assert asset.is_directory
                assert asset.fingerprint[:2] == (2, 5)
                # its inside is never walked, whatever the markers say
                assert cube / 'zarr.json' not in trail.assets
                addition = trail.events.select[1]
                assert addition.rule == '*.zarr'
                assert addition.st_size == 5
                assert cube in trail.watchdog.desired()
                assert cube / 'band' not in trail.watchdog.desired()

                assert trail.watchdog.ensure()
                previous = len(trail.events)
                (cube / 'zarr.json').write_text('{"a": 1}', encoding='utf-8')
                (cube / '1').write_bytes(b'd')
                async with asyncio.timeout(5):
                    while not any(
                            event.event_type == 'modified'
                            for event in trail.events.select[previous:]
                    ):
                        await asyncio.sleep(0.01)
                await trail.watchdog.stop()
                recorded = self.records(trail.events.select[previous:])
                # the writes inside are recorded against the container, not file by file
                assert all(
                    event.entry is asset
                    and event.src_path == str(cube)
                    for event in recorded
                )
                assert cube / '1' not in trail.assets

                restored = Trail(root)
                assert restored.assets[cube].id == asset.id
                assert restored.assets[cube].is_directory

        asyncio.run(run())

    def test_a_tracked_directory_is_an_asset(self) -> None:
        with self.workspace() as root:
            self.mark(root, './', '*.csv', '*.parquet')
            table = root / 'table'
            partition = table / 'year=2026'
            partition.mkdir(parents=True)
            (partition / 'part-0.parquet').write_bytes(b'x')
            (partition / 'part-0.csv').write_text('a\n', encoding='utf-8')
            trail = Trail(root)
            assert partition / 'part-0.parquet' in trail.assets
            hive = trail.track(table)
            assert hive.is_directory
            assert trail.events.select[-1].rule is None
            # files already tracked inside stay tracked, but nothing new inside is walked
            assert partition / 'part-0.parquet' in trail.assets
            (partition / 'part-1.parquet').write_bytes(b'y')
            assert trail.assets.scan() == ()
            assert table not in trail.dirs

if __name__ == "__main__":
    if sys.platform != "linux":
        raise SystemExit("file opening requires Linux inotify")

    test_object = TestTrail()
    tests = [
        ("test_pathless_track_and_open", "pathless trail tracks an opened CSV"),
        (
            'test_passive_instantiation_observes_without_a_context',
            'passive instantiation observes without a context',
        ),
        (
            'test_passive_instantiation_is_dormant_without_a_loop',
            'passive instantiation stays dormant without a loop',
        ),
        (
            'test_watchdog_context_starts_and_stops_the_session',
            'the watchdog context starts and stops the session',
        ),
        (
            'test_watchdog_context_adopts_a_passive_session',
            'the watchdog context adopts a passive session',
        ),
        (
            "test_persistent_track_open_and_new_session",
            "persistent trail survives new sessions",
        ),
        (
            "test_offtrailed_file_stays_offtrailed_after_new_session",
            "offtrailed files stay offtrailed",
        ),
        (
            "test_deleted_csv_history_can_be_restored",
            "deleted CSV history is restored",
        ),
        (
            "test_track_untrack_and_retrack_history_can_be_restored",
            "tracking changes replay with stable entry identities",
        ),
        (
            'test_select_slicing_after_tracking_changes',
            'positional slicing follows real tracking changes',
        ),
        (
            'test_select_survives_file_changes_and_reload',
            'positional indexing survives real file changes and reload',
        ),
        (
            'test_track_and_offtrail_return_assets_or_lists',
            'single paths return assets and multiple paths return lists',
        ),
        (
            'test_asset_positions_follow_tracking_changes',
            'asset positions follow tracking changes',
        ),
        (
            'test_assets_follow_a_directory_move',
            'assets follow a directory move without losing their places',
        ),
        (
            'test_a_path_holds_one_asset_whichever_its_kind',
            'a path holds one asset whichever its kind',
        ),
        (
            'test_discovered_assets_survive_reload',
            'discovered assets retain positions and identities after reload',
        ),
        (
            'test_hex_ids_support_lookup_and_persistence',
            'hexadecimal IDs support lookup and persistence',
        ),
        (
            'test_legacy_integer_ids_are_normalized_on_reload',
            'legacy integer IDs normalize to hexadecimal strings on reload',
        ),
        (
            'test_folder_auto_tracks_created_files_and_nested_subdirectories',
            'folders auto-track created files and nested subdirectories',
        ),
        (
            'test_renamed_file_keeps_its_resource_id',
            'renamed files keep their resource ID across a reload',
        ),
        (
            'test_renamed_folder_repaths_its_contents',
            'renamed folders repath their tracked contents',
        ),
        (
            'test_a_container_is_tracked_whole',
            'a container is tracked whole',
        ),
        (
            'test_a_tracked_directory_is_an_asset',
            'a tracked directory is an asset',
        ),
        (
            'test_the_search_stops_at_a_ceiling_directory',
            'test_the_search_stops_at_a_ceiling_directory'
        )
    ]

    for method_name, description in tests:
        getattr(test_object, method_name)()
        print(f"  ✓ {description}")
