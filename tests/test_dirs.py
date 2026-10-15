from __future__ import annotations

import asyncio
import shutil
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from trail import Trail
from trail.dir import Dir
from trail.event import AddDirEvent, Event, RemoveDirEvent, WatchdogEvent


class TestDirs:
    """A Dir's identity and history in the log. `.markers` decides which Dirs are live."""

    @staticmethod
    @contextmanager
    def workspace() -> Iterator[Path]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / '.markers').write_text('./\n*.csv\ndata/\n', encoding='utf-8')
            (root / 'data').mkdir()
            (root / 'data' / 'a.csv').write_text('a\n', encoding='utf-8')
            (root / 'raw').mkdir()
            yield root

    @staticmethod
    def kinds(events: list[Event]) -> list[tuple[str, str]]:
        return [
            (type(event).__name__, Path(event.src_path).name)
            for event in events
            if isinstance(event, (AddDirEvent, RemoveDirEvent))
        ]

    def test_a_dir_keeps_its_id_across_a_reload(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            assert self.kinds(list(trail.events)) == [
                ('AddDirEvent', root.name),
                ('AddDirEvent', 'data'),
            ]
            ids = trail.dirs.ids.copy()
            reloaded = Trail(root)
            assert reloaded.dirs.ids == ids
            # nothing changed in .markers, so the reload logs nothing
            assert len(reloaded.events) == len(trail.events)
            assert isinstance(reloaded.dirs[root / 'data'], Dir)

    def test_marking_and_unmarking_a_directory_logs_its_dir(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            assert trail.mark('raw') == ['raw/']
            addition = trail.events.select[-1]
            assert isinstance(addition, AddDirEvent)
            assert addition.entry is trail.dirs[root / 'raw']
            assert addition.rule == 'raw/'
            first = addition.entry.id
            trail.unmark('raw/')
            removal = trail.events.select[-1]
            assert isinstance(removal, RemoveDirEvent)
            assert removal.entry is addition.entry
            assert root / 'raw' not in trail.dirs
            # marked again, it is a new Dir, as an offtrailed file tracked again is a new asset
            trail.mark('raw/')
            assert trail.dirs[root / 'raw'].id != first
            assert Trail(root).dirs[root / 'raw'].id == trail.dirs[root / 'raw'].id

    def test_a_scope_of_a_live_dir_logs_no_dir_event(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            data = trail.dirs[root / 'data']
            count = len(trail.events)
            trail.mark('data/*.png')
            assert trail.dirs[root / 'data'] is data
            assert data.scopes.relative == ('./*.png',)
            assert not self.kinds(trail.events.select[count:])

    def test_a_dir_named_only_by_scopes_records_its_first_line(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            trail.mark('raw/*.tif')
            addition = trail.events.select[-1]
            assert isinstance(addition, AddDirEvent)
            assert addition.rule == 'raw/*.tif'
            assert not trail.dirs[root / 'raw'].root

    def test_a_hand_edit_is_read_on_the_next_start(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            data = trail.dirs[root / 'data'].id
            count = len(trail.events)
            (root / '.markers').write_text('./\n*.csv\nraw/\n', encoding='utf-8')
            edited = Trail(root)
            # a rename by hand is not guessed at: one Dir goes and another comes
            assert self.kinds(edited.events.select[count:]) == [
                ('RemoveDirEvent', 'data'),
                ('AddDirEvent', 'raw'),
            ]
            assert root / 'data' not in edited.dirs
            assert edited.dirs[root / 'raw'].id != data

    def test_clearing_the_log_keeps_the_dirs(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            ids = trail.dirs.ids.copy()
            trail.clear()
            assert self.kinds(list(trail.events)) == [
                ('AddDirEvent', root.name),
                ('AddDirEvent', 'data'),
            ]
            assert Trail(root).dirs.ids == ids

    def test_a_directory_asset_is_never_a_dir(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            data = trail.dirs[root / 'data']
            with pytest.warns(UserWarning, match='tracking data whole offtrails 1 asset inside it'):
                table = trail.track(root / 'data')
            assert table.is_directory
            assert root / 'data' / 'a.csv' not in trail.assets
            assert root / 'data' not in trail.dirs
            removal = trail.events.select[-1]
            assert isinstance(removal, RemoveDirEvent)
            assert removal.entry is data
            assert "'data/' names a directory asset" in trail.markers.warnings[0]
            # offtrailed, the line in .markers makes it a Dir again, under a new ID
            trail.offtrail(root / 'data')
            assert trail.dirs[root / 'data'].id != data.id

    def test_renaming_a_dirs_directory_is_logged_against_the_dir(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                trail = Trail(root)
                data = trail.dirs[root / 'data']
                asset = trail.assets[root / 'data' / 'a.csv']
                assert trail.watchdog.session.start()
                count = len(trail.events)
                renamed = root / 'renamed'
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter('always')
                    (root / 'data').rename(renamed)
                    async with asyncio.timeout(5):
                        while renamed / 'a.csv' not in trail.assets:
                            await asyncio.sleep(0.01)
                    await trail.watchdog.session.stop()
                # the Dir's line in .markers no longer names an existing directory, said once
                moves = [
                    str(warning.message)
                    for warning in caught
                    if 'is named by .markers but was moved' in str(warning.message)
                ]
                assert moves == [f'{root / "data"} is named by .markers but was moved to {renamed}']

                [moved] = [
                    event
                    for event in trail.events.select[count:]
                    if event.entry is data
                ]
                assert isinstance(moved, WatchdogEvent)
                assert moved.event_type == 'moved'
                assert moved.dest_path == str(renamed)
                # the line in .markers still names data/, so the Dir keeps its path
                assert data.path == root / 'data'
                assert trail.dirs[root / 'data'] is data
                assert trail.assets[renamed / 'a.csv'] is asset

                restored = Trail(root)
                assert restored.dirs[root / 'data'].id == data.id
                assert restored.dirs[root / 'data'].path == root / 'data'
                assert restored.assets[renamed / 'a.csv'].id == asset.id
                await restored.watchdog.session.stop()

        asyncio.run(run())

    def test_deleting_a_dirs_directory_is_logged_once(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                trail = Trail(root)
                data = trail.dirs[root / 'data']
                assert trail.watchdog.session.start()
                count = len(trail.events)
                shutil.rmtree(root / 'data')
                async with asyncio.timeout(5):
                    while not any(
                            event.entry is data
                            for event in trail.events.select[count:]
                    ):
                        await asyncio.sleep(0.01)
                await asyncio.sleep(trail.watchdog.session.debounce * 3)
                await trail.watchdog.session.stop()
                # the Dir's own watch and its parent's both see the deletion
                deletions = [
                    event
                    for event in trail.events.select[count:]
                    if event.entry is data
                ]
                assert [event.event_type for event in deletions] == ['deleted']
                assert trail.dirs[root / 'data'] is data

        asyncio.run(run())

    def test_a_new_subfolder_is_not_a_dir(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                trail = Trail(root)
                assert trail.watchdog.session.start()
                nested = root / 'nested'
                nested.mkdir()
                (nested / 'b.csv').write_text('b\n', encoding='utf-8')
                async with asyncio.timeout(5):
                    while nested / 'b.csv' not in trail.assets:
                        await asyncio.sleep(0.01)
                await trail.watchdog.session.stop()
                assert nested not in trail.dirs
                assert not any(
                    event.src_path == str(nested)
                    for event in trail.events
                )

        asyncio.run(run())


if __name__ == "__main__":
    suite = TestDirs()
    for name in dir(suite):
        if name.startswith('test_'):
            getattr(suite, name)()
            print(f"  ✓ {name.removeprefix('test_').replace('_', ' ')}")
