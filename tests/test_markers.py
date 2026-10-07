from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from trail import Trail
from trail.markers import MARKERS, available, kind, parse


class TestMarkers:
    @staticmethod
    @contextmanager
    def workspace() -> Iterator[Path]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / '.markers').write_text('# premade\n./\n*.csv\n', encoding='utf-8')
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
            # patterns and paths are both lowercased to match
            assert root / 'folder' / 'nested.CSV' in trail.assets
            assert root / 'notes.txt' not in trail.assets
            assert root / '.hidden' / 'secret.csv' not in trail.assets

    def test_scanning_is_idempotent_and_survives_reload(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            assert trail.assets.scan() == ()
            events = len(trail.events)
            reloaded = Trail(root)
            assert len(reloaded.events) == events
            assert root / 'top.csv' in reloaded.assets

    def test_offtrailed_files_stay_offtrailed(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            trail.offtrail(root / 'top.csv')
            assert trail.assets.scan() == ()
            assert root / 'top.csv' not in Trail(root).assets

    def test_marking_tracks_existing_files(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            assert trail.mark('txt') == ['*.txt']
            assert root / 'notes.txt' in trail.assets
            assert trail.events.select[-1].rule == '*.txt'
            assert (root / '.markers').read_text(encoding='utf-8') == (
                '# premade\n./\n*.csv\n*.txt\n'
            )

    def test_marking_an_existing_line_does_nothing(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            events = len(trail.events)
            assert trail.mark('csv', '.csv', '**/*.csv') == []
            assert len(trail.events) == events
            assert list(trail.markers) == ['./', '*.csv']

    def test_unmarking_is_not_retroactive(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            with pytest.warns(UserWarning, match="leaves 2 assets tracked; offtrail removes them"):
                assert trail.unmark('.csv') == ['*.csv']
            assert root / 'top.csv' in trail.assets
            assert list(trail.markers) == ['./']
            assert (root / '.markers').read_text(encoding='utf-8') == '# premade\n./\n'
            (root / 'later.csv').write_text('a\n', encoding='utf-8')
            assert trail.assets.scan() == ()

    def test_a_change_to_the_markers_rebuilds_the_dirs(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            assert list(trail.dirs.path2entry) == [root]
            assert list(trail.scopes) == ['*.csv']
            trail.mark('folder/*.txt')
            assert list(trail.dirs.path2entry) == [root, root / 'folder']
            trail.markers.replace(['./'])
            assert list(trail.dirs.path2entry) == [root]
            assert not trail.scopes
            assert (root / '.markers').read_text(encoding='utf-8') == '# premade\n./\n'

    def test_marking_a_hidden_directory_walks_it(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            assert trail.mark('.hidden/') == ['.hidden/']
            assert root / '.hidden' / 'secret.csv' in trail.assets

    def test_a_dir_outside_the_project_is_marked(self) -> None:
        with self.workspace() as root, TemporaryDirectory() as elsewhere:
            outside = Path(elsewhere).resolve()
            (outside / 'far.csv').write_text('a\n', encoding='utf-8')
            (outside / 'far.txt').write_text('a\n', encoding='utf-8')
            (outside / 'sub').mkdir()
            (outside / 'sub' / 'deep.txt').write_text('a\n', encoding='utf-8')
            trail = Trail(root)
            trail.mark(f'{outside}/*.txt')
            assert outside / 'far.txt' in trail.assets
            assert outside / 'far.csv' not in trail.assets
            # a dir scope reaches only the files directly in its directory
            assert outside / 'sub' / 'deep.txt' not in trail.assets
            # beyond the roots and the dirs, markers do not reach
            assert trail.assets.scan(outside.parent / 'x.csv') == ()

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
                assert trail.assets[created].id in {
                    event.entry.id
                    for event in trail.events
                    if event.get.rule == '*.csv'
                }

        asyncio.run(run())

    def test_a_root_takes_in_only_marked_files(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                trail = Trail(root)
                folder = root / 'folder'
                assert trail.watchdog.ensure()
                unmarked = folder / 'unmarked.txt'
                marked = folder / 'marked.csv'
                unmarked.write_text('a\n', encoding='utf-8')
                marked.write_text('a\n', encoding='utf-8')
                # built outside, then moved in whole, so that its contents are found by a scan
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
                assert moved not in trail.dirs
                assert unmarked not in trail.assets
                assert moved / 'inner.txt' not in trail.assets

        asyncio.run(run())

    def test_the_default_preset_only_watches_the_project(self) -> None:
        lines = (MARKERS / 'default.txt').read_text(encoding='utf-8').splitlines()
        assert lines == ['./']

    def test_every_other_preset_watches_the_project_and_marks_extensions(self) -> None:
        for name in available():
            if name == 'default':
                continue
            lines = (MARKERS / f'{name}.txt').read_text(encoding='utf-8').splitlines()
            assert lines[0] == './'
            assert lines[1:]
            for line in lines[1:]:
                assert line.startswith('*.')
                assert kind(line, Path('/')) == 'root scope'

    def test_a_new_trail_copies_the_default_markers(self) -> None:
        with self.workspace() as root:
            (root / '.markers').unlink()
            trail = Trail(root)
            default = (MARKERS / 'default.txt').read_text(encoding='utf-8')
            assert (root / '.markers').read_text(encoding='utf-8') == default
            assert list(trail.markers) == ['./']
            assert root / 'top.csv' not in trail.assets
            assert not trail.markers.warnings

    def test_a_new_trail_copies_markers_from_a_path(self) -> None:
        with self.workspace() as root, TemporaryDirectory() as elsewhere:
            (root / '.markers').unlink()
            source = Path(elsewhere) / 'mine.markers'
            source.write_text('./\n*.txt\ndata/*.png\n', encoding='utf-8')
            trail = Trail(root, markers=source)
            assert (root / '.markers').read_text(encoding='utf-8') == './\n*.txt\ndata/*.png\n'
            assert root / 'notes.txt' in trail.assets
            assert root / 'top.csv' not in trail.assets
            # an anchored line was written for the project the file came from
            [warning] = trail.markers.warnings
            assert ":3: 'data/*.png' was imported" in warning

    def test_existing_markers_are_not_overwritten(self) -> None:
        with self.workspace() as root:
            Trail(root, markers='default')
            assert (root / '.markers').read_text(encoding='utf-8') == '# premade\n./\n*.csv\n'

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

    def test_comments_survive_edits(self) -> None:
        with self.workspace() as root:
            path = root / '.markers'
            path.write_text('# data\n./\n\n# tables\n*.csv\n', encoding='utf-8')
            trail = Trail(root)
            trail.markers.add('*.tsv')
            trail.markers.discard('*.csv')
            assert path.read_text(encoding='utf-8') == '# data\n./\n\n# tables\n*.tsv\n'
            trail.markers.replace(['*.parquet', './'])
            assert path.read_text(encoding='utf-8') == '# data\n./\n\n# tables\n*.parquet\n'
            assert list(trail.markers) == ['*.parquet', './']


class TestParse:
    project = Path('/home/user/project')

    def test_directory_markers(self) -> None:
        project = self.project
        assert parse('./', project) == (project, '')
        assert parse('data/', project) == (project / 'data', '')
        assert parse('/mnt/data/', project) == (Path('/mnt/data'), '')
        assert parse('~/Downloads/', project) == (Path.home() / 'Downloads', '')

    def test_root_scopes(self) -> None:
        for line in ('*.csv', '!requirements*.txt', 'Makefile'):
            assert parse(line, self.project) == (None, line)

    def test_dir_scopes_belong_to_the_directory_before_the_first_wildcard(self) -> None:
        project = self.project
        assert parse('data/*.png', project) == (project / 'data', '/*.png')
        assert parse('./a.csv', project) == (project, '/a.csv')
        assert parse('./*', project) == (project, '/*')
        assert parse('data/raw/b.tif', project) == (project / 'data' / 'raw', '/b.tif')
        assert parse('data/*/b.tif', project) == (project / 'data', '/*/b.tif')
        assert parse('/mnt/data/**/*.tif', project) == (Path('/mnt/data'), '/**/*.tif')
        assert parse('../dir/*.png', project) == (project.parent / 'dir', '/*.png')
        assert parse('~/Downloads/prefix_*.png', project) == (
            Path.home() / 'Downloads',
            '/prefix_*.png',
        )

    def test_an_exclusion_keeps_its_bang_in_the_pattern(self) -> None:
        project = self.project
        assert parse('!outputs/**', project) == (project / 'outputs', '!/**')
        # without an anchor, an excluded directory is excluded at any depth, as in gitignore
        assert parse('!data/', project) == (None, '!data/')
        assert parse('!./data/', project) == (project, '!/data/')

    def test_a_line_that_is_not_a_marker_is_refused(self) -> None:
        for line in ('!', '! ', 'data/\\'):
            with pytest.raises(ValueError):
                parse(line, self.project)

    def test_kinds(self) -> None:
        project = self.project
        assert kind('./', project) == 'root'
        assert kind('*.csv', project) == 'root scope'
        assert kind('!*.tmp', project) == 'root scope · exclude'
        assert kind('data/*.png', project) == 'dir scope · not subfolders'
        assert kind('data/**/*.png', project) == 'dir scope'
        assert kind('!outputs/**', project) == 'dir scope · exclude'
        assert kind('!', project) == 'invalid'


class TestWarnings:
    @staticmethod
    @contextmanager
    def trail(text: str) -> Iterator[tuple[Trail, Path]]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'data').mkdir()
            (root / 'cube.zarr').mkdir()
            (root / '.markers').write_text(text, encoding='utf-8')
            yield Trail(root), root

    def test_a_directory_without_its_slash(self) -> None:
        with self.trail('./\ndata\n./data\n*.csv\n') as (trail, root):
            warnings = trail.markers.warnings
            assert len(warnings) == 2
            assert warnings[0].startswith(f"{root / '.markers'}:2: 'data' names a directory")
            assert "did you mean 'data/'" in warnings[0]
            assert warnings[1].startswith(f"{root / '.markers'}:3: './data' names a directory")

    def test_a_line_that_does_not_parse(self) -> None:
        with self.trail('# x\n./\n!\n*.csv\n') as (trail, root):
            [warning] = trail.markers.warnings
            assert warning.startswith(f"{root / '.markers'}:3: Not a marker")
            # the rest of the file still applies
            assert list(trail.scopes) == ['*.csv']

    def test_a_directory_marker_naming_a_container(self) -> None:
        with self.trail('./\ncube.zarr/\n') as (trail, root):
            [warning] = trail.markers.warnings
            assert "'cube.zarr/' names a directory asset" in warning
            assert list(trail.dirs.path2entry) == [root]

    def test_a_directory_marker_naming_a_tracked_directory(self) -> None:
        with self.trail('./\ndata/\n') as (trail, root):
            assert list(trail.dirs.path2entry) == [root, root / 'data']
            trail.track(root / 'data')
            [warning] = trail.markers.warnings
            assert "'data/' names a directory asset" in warning
            assert list(trail.dirs.path2entry) == [root]

    def test_well_formed_markers_raise_nothing(self) -> None:
        with self.trail('./\n*.csv\ndata/\ndata/*.png\n!data/tmp_*\n') as (trail, _):
            assert trail.markers.warnings == []


if __name__ == "__main__":
    for suite in (TestMarkers(), TestParse(), TestWarnings()):
        for name in dir(suite):
            if name.startswith('test_'):
                getattr(suite, name)()
                print(f"  ✓ {name.removeprefix('test_').replace('_', ' ')}")
