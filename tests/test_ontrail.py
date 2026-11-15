from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from trail import Trail
from trail.ontrail import PRESETS, OnTrail
from trail.rule import Rule


class TestOnTrail:
    @staticmethod
    @contextmanager
    def workspace() -> Iterator[Path]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / '.ontrail').write_text('# premade\n./\n*.csv\n', encoding='utf-8')
            (root / 'top.csv').write_text('a\n', encoding='utf-8')
            (root / 'notes.txt').write_text('a\n', encoding='utf-8')
            (root / 'folder').mkdir()
            (root / 'folder' / 'nested.CSV').write_text('a\n', encoding='utf-8')
            (root / '.hidden').mkdir()
            (root / '.hidden' / 'secret.csv').write_text('a\n', encoding='utf-8')
            yield root

    def test_instantiation_tracks_the_files_the_rules_take(self) -> None:
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

    def test_untracked_files_stay_untracked(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            trail.offtrail(root / 'top.csv')
            assert trail.assets.scan() == ()
            assert root / 'top.csv' not in Trail(root).assets

    def test_calls_change_assets_without_editing_rules(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            ontrail = trail.ontrail
            offtrail = trail.offtrail
            included = (root / '.ontrail').read_text(encoding='utf-8')
            excluded = (root / '.offtrail').read_text(encoding='utf-8')
            path = root / 'notes.txt'
            asset = ontrail(path)
            assert trail.assets[path] is asset
            assert trail.events.select[-1].rule is None
            assert offtrail(path) is asset
            assert path in trail.entries.untracked
            assert path not in Trail(root).assets
            assert ontrail(path).path == path
            assert path not in trail.entries.untracked
            assert trail.ontrail is ontrail
            assert trail.offtrail is offtrail
            assert list(ontrail) == ['./', '*.csv']
            assert list(offtrail) == ['.*']
            assert (root / '.ontrail').read_text(encoding='utf-8') == included
            assert (root / '.offtrail').read_text(encoding='utf-8') == excluded
            assert not hasattr(trail, 'track')
            assert not hasattr(trail, 'untrack')

    def test_adding_a_rule_tracks_existing_files(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            assert trail.ontrail.add('txt') == ['*.txt']
            assert root / 'notes.txt' in trail.assets
            assert trail.events.select[-1].rule == '*.txt'
            assert (root / '.ontrail').read_text(encoding='utf-8') == (
                '# premade\n./\n*.csv\n*.txt\n'
            )

    def test_adding_an_existing_rule_does_nothing(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            events = len(trail.events)
            assert trail.ontrail.add('csv', '.csv', '**/*.csv') == []
            assert len(trail.events) == events
            assert list(trail.ontrail) == ['./', '*.csv']

    def test_removing_a_rule_is_not_retroactive(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            with pytest.warns(UserWarning, match="leaves 2 assets tracked; offtrail removes them"):
                assert trail.ontrail.remove('.csv') == ['*.csv']
            assert root / 'top.csv' in trail.assets
            assert list(trail.ontrail) == ['./']
            assert (root / '.ontrail').read_text(encoding='utf-8') == '# premade\n./\n'
            (root / 'later.csv').write_text('a\n', encoding='utf-8')
            assert trail.assets.scan() == ()

    def test_a_change_to_the_rules_rebuilds_the_dirs(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            assert list(trail.dirs.path2entry) == [root]
            assert list(trail.scopes) == ['*.csv']
            trail.ontrail.add('folder/*.txt')
            assert list(trail.dirs.path2entry) == [root, root / 'folder']
            trail.ontrail.replace(['./'])
            assert list(trail.dirs.path2entry) == [root]
            assert not trail.scopes
            assert (root / '.ontrail').read_text(encoding='utf-8') == '# premade\n./\n'

    def test_adding_a_hidden_directory_walks_it(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            assert trail.ontrail.add('.hidden/') == ['.hidden/']
            assert root / '.hidden' / 'secret.csv' in trail.assets

    def test_adding_one_dirasset_tracks_it(self) -> None:
        with self.workspace() as root:
            cube = root / 'folder' / 'cube.zarr'
            cube.mkdir()
            (cube / 'zarr.json').write_text('{}', encoding='utf-8')
            trail = Trail(root)
            assert trail.ontrail.add('folder/cube.zarr') == ['folder/cube.zarr']
            assert trail.assets[cube].is_directory
            assert trail.events.select[-1].rule == 'folder/cube.zarr'
            assert not trail.ontrail.warnings

    def test_a_dir_outside_the_project_can_be_added(self) -> None:
        with self.workspace() as root, TemporaryDirectory() as elsewhere:
            outside = Path(elsewhere).resolve()
            (outside / 'far.csv').write_text('a\n', encoding='utf-8')
            (outside / 'far.txt').write_text('a\n', encoding='utf-8')
            (outside / 'sub').mkdir()
            (outside / 'sub' / 'deep.txt').write_text('a\n', encoding='utf-8')
            trail = Trail(root)
            trail.ontrail.add(f'{outside}/*.txt')
            assert outside / 'far.txt' in trail.assets
            assert outside / 'far.csv' not in trail.assets
            # a dir scope reaches only the files directly in its directory
            assert outside / 'sub' / 'deep.txt' not in trail.assets
            # beyond the roots and the dirs, rules do not reach
            assert trail.assets.scan(outside.parent / 'x.csv') == ()

    def test_created_file_beside_a_tracked_asset_is_taken(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                trail = Trail(root)
                assert trail.watchdog.session.start()
                created = root / 'created.csv'
                ignored = root / 'created.txt'
                ignored.write_text('a\n', encoding='utf-8')
                created.write_text('a\n', encoding='utf-8')
                async with asyncio.timeout(5):
                    while created not in trail.assets:
                        await asyncio.sleep(0.01)
                await trail.watchdog.session.stop()
                assert ignored not in trail.assets
                assert trail.assets[created].id in {
                    event.entry.id
                    for event in trail.events
                    if event.get.rule == '*.csv'
                }

        asyncio.run(run())

    def test_a_root_takes_in_only_the_files_its_rules_take(self) -> None:
        async def run() -> None:
            with self.workspace() as root:
                trail = Trail(root)
                folder = root / 'folder'
                assert trail.watchdog.session.start()
                skipped = folder / 'skipped.txt'
                taken = folder / 'taken.csv'
                skipped.write_text('a\n', encoding='utf-8')
                taken.write_text('a\n', encoding='utf-8')
                # built outside, then moved in whole, so that its contents are found by a scan
                staging = root / '.staging'
                staging.mkdir()
                (staging / 'inner.csv').write_text('a\n', encoding='utf-8')
                (staging / 'inner.txt').write_text('a\n', encoding='utf-8')
                moved = folder / 'moved'
                staging.rename(moved)
                async with asyncio.timeout(5):
                    while (
                            taken not in trail.assets
                            or moved / 'inner.csv' not in trail.assets
                    ):
                        await asyncio.sleep(0.01)
                await trail.watchdog.session.stop()
                assert moved not in trail.dirs
                assert skipped not in trail.assets
                assert moved / 'inner.txt' not in trail.assets

        asyncio.run(run())

    def test_the_default_preset_only_watches_the_project(self) -> None:
        lines = (PRESETS / 'default.txt').read_text(encoding='utf-8').splitlines()
        assert lines == ['./']

    def test_every_other_preset_watches_the_project_and_takes_extensions(self) -> None:
        for name in OnTrail.available():
            if name == 'default':
                continue
            lines = (PRESETS / f'{name}.txt').read_text(encoding='utf-8').splitlines()
            assert lines[0] == './'
            assert lines[1:]
            for line in lines[1:]:
                assert line.startswith('*.')
                assert Rule(line).kind(Path('/')) == 'root scope'

    def test_the_geospatial_preset_tracks_safe_products(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            product = root / 'S2A_MSIL2A_20260101T100000_N0510_R022_T33UUP_20260101T120000.SAFE'
            (product / 'GRANULE').mkdir(parents=True)
            (product / 'manifest.safe').write_text('<xfdu/>\n', encoding='utf-8')
            trail = Trail(root, ontrail='geospatial')
            assert '*.safe' in trail.ontrail
            # matching ignores case, so the line takes the product's upper-case suffix
            asset = trail.assets[product]
            assert asset.is_directory
            assert trail.events.select[-1].rule == '*.safe'
            assert product / 'manifest.safe' not in trail.assets

    def test_a_new_trail_copies_the_default_preset(self) -> None:
        with self.workspace() as root:
            (root / '.ontrail').unlink()
            trail = Trail(root)
            default = (PRESETS / 'default.txt').read_text(encoding='utf-8')
            assert (root / '.ontrail').read_text(encoding='utf-8') == default
            assert list(trail.ontrail) == ['./']
            assert root / 'top.csv' not in trail.assets
            assert not trail.ontrail.warnings

    def test_a_new_trail_copies_rules_from_a_path(self) -> None:
        with self.workspace() as root, TemporaryDirectory() as elsewhere:
            (root / '.ontrail').unlink()
            source = Path(elsewhere) / 'mine.ontrail'
            source.write_text('./\n*.txt\ndata/*.png\n', encoding='utf-8')
            trail = Trail(root, ontrail=source)
            assert (root / '.ontrail').read_text(encoding='utf-8') == './\n*.txt\ndata/*.png\n'
            assert root / 'notes.txt' in trail.assets
            assert root / 'top.csv' not in trail.assets
            # an anchored line was written for the project the file came from
            [warning] = trail.ontrail.warnings
            assert ":3: 'data/*.png' was imported" in warning

    def test_an_existing_ontrail_is_not_overwritten(self) -> None:
        with self.workspace() as root:
            Trail(root, ontrail='default')
            assert (root / '.ontrail').read_text(encoding='utf-8') == '# premade\n./\n*.csv\n'

    def test_a_reopened_trail_does_not_copy_a_preset(self) -> None:
        with self.workspace() as root:
            (root / '.ontrail').unlink()
            Trail(root, ontrail=None)
            Trail(root)
            assert not (root / '.ontrail').exists()

    def test_an_unknown_preset_is_refused(self) -> None:
        with self.workspace() as root:
            (root / '.ontrail').unlink()
            with pytest.raises(FileNotFoundError, match='default'):
                Trail(root, ontrail='nonexistent')

    def test_comments_survive_edits(self) -> None:
        with self.workspace() as root:
            path = root / '.ontrail'
            path.write_text('# data\n./\n\n# tables\n*.csv\n', encoding='utf-8')
            trail = Trail(root)
            trail.ontrail.add('*.tsv')
            with pytest.warns(UserWarning, match='leaves 2 assets tracked'):
                trail.ontrail.remove('*.csv')
            assert path.read_text(encoding='utf-8') == '# data\n./\n\n# tables\n*.tsv\n'
            trail.ontrail.replace(['*.parquet', './'])
            assert path.read_text(encoding='utf-8') == '# data\n./\n\n# tables\n*.parquet\n'
            assert list(trail.ontrail) == ['*.parquet', './']


class TestWarnings:
    @staticmethod
    @contextmanager
    def trail(text: str) -> Iterator[tuple[Trail, Path]]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / 'data').mkdir()
            (root / 'cube.zarr').mkdir()
            (root / '.ontrail').write_text(text, encoding='utf-8')
            yield Trail(root), root

    def test_a_directory_without_its_slash(self) -> None:
        with self.trail('./\ndata\n./data\n*.csv\n') as (trail, root):
            warnings = trail.ontrail.warnings
            assert len(warnings) == 2
            assert warnings[0].startswith(f"{root / '.ontrail'}:2: 'data' names a directory")
            assert "did you mean 'data/'" in warnings[0]
            assert warnings[1].startswith(f"{root / '.ontrail'}:3: './data' names a directory")

    def test_a_dirasset_line_draws_no_slash_warning(self) -> None:
        with self.trail('./\ncube.zarr\n./cube.zarr\n*.csv\n') as (trail, root):
            assert trail.ontrail.warnings == []
            assert trail.assets[root / 'cube.zarr'].is_directory

    def test_a_line_that_does_not_parse(self) -> None:
        with self.trail('# x\n./\n!\n*.csv\n') as (trail, root):
            [warning] = trail.ontrail.warnings
            assert warning.startswith(f"{root / '.ontrail'}:3: Not a rule")
            # the rest of the file still applies
            assert list(trail.scopes) == ['*.csv']

    def test_a_root_naming_a_container(self) -> None:
        with self.trail('./\ncube.zarr/\n') as (trail, root):
            [warning] = trail.ontrail.warnings
            assert "'cube.zarr/' names a directory asset" in warning
            assert list(trail.dirs.path2entry) == [root]

    def test_a_root_naming_a_tracked_directory(self) -> None:
        with self.trail('./\ndata/\n') as (trail, root):
            assert list(trail.dirs.path2entry) == [root, root / 'data']
            trail.ontrail(root / 'data')
            [warning] = trail.ontrail.warnings
            assert "'data/' names a directory asset" in warning
            assert list(trail.dirs.path2entry) == [root]

    def test_well_formed_rules_raise_nothing(self) -> None:
        with self.trail('./\n*.csv\ndata/\ndata/*.png\n!data/tmp_*\n') as (trail, _):
            assert trail.ontrail.warnings == []


if __name__ == "__main__":
    for suite in (TestOnTrail(), TestWarnings()):
        for name in dir(suite):
            if name.startswith('test_'):
                getattr(suite, name)()
                print(f"  ✓ {name.removeprefix('test_').replace('_', ' ')}")
