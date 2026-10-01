from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from trail import Trail
from trail.markers import MARKERS


class TestMarkers:
    @staticmethod
    @contextmanager
    def workspace() -> Iterator[Path]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / '.markers').write_text('# premade\ncsv\n', encoding='utf-8')
            (root / 'top.csv').write_text('a\n', encoding='utf-8')
            (root / 'notes.txt').write_text('a\n', encoding='utf-8')
            (root / 'folder').mkdir()
            (root / 'folder' / 'nested.CSV').write_text('a\n', encoding='utf-8')
            (root / '.hidden').mkdir()
            (root / '.hidden' / 'secret.csv').write_text('a\n', encoding='utf-8')
            yield root

    def test_instantiation_tracks_marked_files(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            assert root / 'top.csv' in trail.assets
            assert root / 'folder' / 'nested.CSV' in trail.assets
            assert root / 'notes.txt' not in trail.assets
            assert root / '.hidden' / 'secret.csv' not in trail.assets

    def test_marking_is_idempotent_and_survives_reload(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            assert trail.entries.mark() == ()
            events = len(trail.events)
            reloaded = Trail(root)
            assert len(reloaded.events) == events
            assert root / 'top.csv' in reloaded.assets

    def test_offtrailed_files_stay_offtrailed(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            trail.offtrail(root / 'top.csv')
            assert trail.entries.mark() == ()
            assert root / 'top.csv' not in Trail(root).assets

    def test_adding_a_marker_tracks_existing_files(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            trail.markers.add('txt')
            assert root / 'notes.txt' in trail.assets

    def test_tracking_a_hidden_directory_tracks_its_marked_files(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            trail.track(root / '.hidden')
            assert root / '.hidden' / 'secret.csv' in trail.assets

    def test_tracked_directory_outside_the_project_is_marked(self) -> None:
        with self.workspace() as root, TemporaryDirectory() as elsewhere:
            outside = Path(elsewhere).resolve()
            (outside / 'far.csv').write_text('a\n', encoding='utf-8')
            (outside / 'far.txt').write_text('a\n', encoding='utf-8')
            trail = Trail(root)
            trail.track(outside)
            assert outside / 'far.csv' in trail.assets
            assert outside / 'far.txt' not in trail.assets
            # beyond the project and the tracked directories, markers do not reach
            assert trail.entries.mark(outside.parent / 'x.csv') == ()

    def test_created_file_beside_a_tracked_asset_is_marked(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                trail = Trail(root)
                assert trail.watchdog.ensure()
                created = root / 'created.csv'
                ignored = root / 'created.txt'
                ignored.write_text('a\n', encoding='utf-8')
                created.write_text('a\n', encoding='utf-8')
                async with asyncio.timeout(5):
                    while created not in trail.assets:
                        await asyncio.sleep(0.01)
                await trail.watchdog.stop()
                assert ignored not in trail.assets

        asyncio.run(run())

    def test_tracked_directory_takes_in_only_marked_files(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                trail = Trail(root)
                folder = trail.track(root / 'folder').path
                assert trail.watchdog.ensure()
                unmarked = folder / 'unmarked.txt'
                marked = folder / 'marked.csv'
                unmarked.write_text('a\n', encoding='utf-8')
                marked.write_text('a\n', encoding='utf-8')
                # built outside, then moved in whole, so that its contents are discovered by walk
                staging = root / '.staging'
                staging.mkdir()
                (staging / 'inner.csv').write_text('a\n', encoding='utf-8')
                (staging / 'inner.txt').write_text('a\n', encoding='utf-8')
                moved = folder / 'moved'
                staging.rename(moved)
                async with asyncio.timeout(5):
                    while (
                            marked not in trail.assets
                            or moved / 'inner.csv' not in trail.assets
                    ):
                        await asyncio.sleep(0.01)
                await trail.watchdog.stop()
                assert moved in trail.dirs
                assert unmarked not in trail.assets
                assert moved / 'inner.txt' not in trail.assets

        asyncio.run(run())

    def test_a_new_trail_copies_the_default_markers(self) -> None:
        with self.workspace() as root:
            (root / '.markers').unlink()
            trail = Trail(root)
            default = (MARKERS / 'default.txt').read_text(encoding='utf-8')
            assert (root / '.markers').read_text(encoding='utf-8') == default
            assert '.csv' in trail.markers
            assert root / 'top.csv' in trail.assets

    def test_a_new_trail_copies_markers_from_a_path(self) -> None:
        with self.workspace() as root, TemporaryDirectory() as elsewhere:
            (root / '.markers').unlink()
            source = Path(elsewhere) / 'mine.markers'
            source.write_text('txt\n', encoding='utf-8')
            trail = Trail(root, markers=source)
            assert (root / '.markers').read_text(encoding='utf-8') == 'txt\n'
            assert root / 'notes.txt' in trail.assets
            assert root / 'top.csv' not in trail.assets

    def test_existing_markers_are_not_overwritten(self) -> None:
        with self.workspace() as root:
            Trail(root, markers='default')
            assert (root / '.markers').read_text(encoding='utf-8') == '# premade\ncsv\n'

    def test_a_reopened_trail_does_not_copy_markers(self) -> None:
        with self.workspace() as root:
            (root / '.markers').unlink()
            Trail(root, markers=None)
            Trail(root)
            assert not (root / '.markers').exists()

    def test_an_unknown_markers_name_is_refused(self) -> None:
        with self.workspace() as root:
            (root / '.markers').unlink()
            with pytest.raises(FileNotFoundError, match='default'):
                Trail(root, markers='nonexistent')


if __name__ == "__main__":
    suites = [
        (
            TestMarkers(),
            [
                (
                    "test_instantiation_tracks_marked_files",
                    "instantiation tracks marked files",
                ),
                (
                    "test_marking_is_idempotent_and_survives_reload",
                    "marking is idempotent and survives reload",
                ),
                (
                    "test_offtrailed_files_stay_offtrailed",
                    "offtrailed files stay offtrailed",
                ),
                (
                    "test_adding_a_marker_tracks_existing_files",
                    "adding a marker tracks existing files",
                ),
                (
                    "test_tracking_a_hidden_directory_tracks_its_marked_files",
                    "tracking a hidden directory tracks its marked files",
                ),
                (
                    "test_tracked_directory_outside_the_project_is_marked",
                    "tracked directory outside the project is marked",
                ),
                (
                    "test_created_file_beside_a_tracked_asset_is_marked",
                    "created file beside a tracked asset is marked",
                ),
                (
                    "test_tracked_directory_takes_in_only_marked_files",
                    "tracked directory takes in only marked files",
                ),
                (
                    "test_a_new_trail_copies_the_default_markers",
                    "a new trail copies the default markers",
                ),
                (
                    "test_a_new_trail_copies_markers_from_a_path",
                    "a new trail copies markers from a path",
                ),
                (
                    "test_existing_markers_are_not_overwritten",
                    "existing markers are not overwritten",
                ),
                (
                    "test_a_reopened_trail_does_not_copy_markers",
                    "a reopened trail does not copy markers",
                ),
                (
                    "test_an_unknown_markers_name_is_refused",
                    "an unknown markers name is refused",
                ),
            ],
        ),
    ]

    for test_object, tests in suites:
        for method_name, description in tests:
            getattr(test_object, method_name)()
            print(f"  ✓ {description}")
