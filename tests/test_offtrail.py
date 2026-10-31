from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from trail import Trail


class TestOffTrail:
    """`.offtrail`, the rules of what is never tracked. They are decided before `.ontrail`."""

    @staticmethod
    @contextmanager
    def workspace(offtrail: str | None = None) -> Iterator[Path]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / '.ontrail').write_text('./\n*.csv\n*.env\ndata/*.csv\n', encoding='utf-8')
            (root / 'a.csv').write_text('a\n', encoding='utf-8')
            (root / '.env').write_text('KEY=1\n', encoding='utf-8')
            (root / 'data').mkdir()
            (root / 'data' / 'b.csv').write_text('b\n', encoding='utf-8')
            # the copy JupyterLab keeps while it saves data/b.csv
            (root / 'data' / '.~b.csv').write_text('b\n', encoding='utf-8')
            (root / 'node_modules').mkdir()
            (root / 'node_modules' / 'c.csv').write_text('c\n', encoding='utf-8')
            if offtrail is not None:
                (root / '.offtrail').write_text(offtrail, encoding='utf-8')
            yield root

    def test_a_new_project_gets_the_default_and_a_deleted_one_comes_back(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            assert (root / '.offtrail').read_text(encoding='utf-8') == '.*\n'
            assert list(trail.offtrail) == ['.*']
            (root / '.offtrail').unlink()
            Trail(root)
            assert (root / '.offtrail').read_text(encoding='utf-8') == '.*\n'

    def test_a_trail_without_a_directory_holds_the_default(self) -> None:
        assert list(Trail().offtrail) == ['.*']

    def test_dotfiles_are_not_tracked(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            assert root / 'a.csv' in trail.assets
            assert root / 'data' / 'b.csv' in trail.assets
            assert root / '.env' not in trail.assets
            # the rule beats the deeper dir scope `data/*.csv`
            assert root / 'data' / '.~b.csv' not in trail.assets

    def test_an_exclusion_hands_a_dotfile_back(self) -> None:
        with self.workspace('.*\n!.env\n') as root:
            trail = Trail(root)
            assert root / '.env' in trail.assets
            assert root / 'data' / '.~b.csv' not in trail.assets

    def test_adding_an_exclusion_tracks_what_it_hands_back(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            assert root / '.env' not in trail.assets
            assert trail.offtrail.add('!.env') == ['!*.env']
            assert root / '.env' in trail.assets
            assert root / 'data' / '.~b.csv' not in trail.assets

    def test_an_empty_offtrail_ignores_nothing(self) -> None:
        with self.workspace('') as root:
            trail = Trail(root)
            assert root / '.env' in trail.assets
            assert root / 'data' / '.~b.csv' in trail.assets

    def test_a_walk_skips_an_ignored_folder(self) -> None:
        with self.workspace('.*\nnode_modules/\n') as root:
            trail = Trail(root)
            assert root / 'node_modules' / 'c.csv' not in trail.assets
            assert not trail.entries.is_walkable(root / 'node_modules')
        with self.workspace() as root:
            assert root / 'node_modules' / 'c.csv' in Trail(root).assets

    def test_a_dir_inside_a_hidden_folder_keeps_its_files(self) -> None:
        with self.workspace() as root:
            (root / '.hidden' / 'data').mkdir(parents=True)
            (root / '.hidden' / 'loose.csv').write_text('l\n', encoding='utf-8')
            (root / '.hidden' / 'data' / 'kept.csv').write_text('k\n', encoding='utf-8')
            trail = Trail(root)
            trail.ontrail.add('.hidden/data/')
            assert root / '.hidden' / 'data' / 'kept.csv' in trail.assets
            assert root / '.hidden' / 'loose.csv' not in trail.assets

    def test_a_directory_rule_ignores_directory_assets_only(self) -> None:
        with self.workspace('*.zarr/\n') as root:
            (root / 'cube.zarr').mkdir()
            (root / 'file.zarr').write_text('a\n', encoding='utf-8')
            with (root / '.ontrail').open('a', encoding='utf-8') as stream:
                stream.write('*.zarr\n')
            trail = Trail(root)
            assert root / 'cube.zarr' not in trail.assets
            assert root / 'file.zarr' in trail.assets

    def test_a_directory_exclusion_takes_a_directory_asset_back(self) -> None:
        with self.workspace('*.zarr\n!*.zarr/\n') as root:
            (root / 'cube.zarr').mkdir()
            (root / 'file.zarr').write_text('a\n', encoding='utf-8')
            with (root / '.ontrail').open('a', encoding='utf-8') as stream:
                stream.write('*.zarr\n')
            trail = Trail(root)
            assert root / 'cube.zarr' in trail.assets
            assert root / 'file.zarr' not in trail.assets

    def test_adding_a_rule_leaves_what_it_ignores_tracked(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            with pytest.warns(UserWarning, match="adding './a.csv' to .offtrail leaves 1 asset"):
                assert trail.offtrail.add('a.csv') == ['./a.csv']
            assert root / 'a.csv' in trail.assets
            assert (root / '.offtrail').read_text(encoding='utf-8') == '.*\n./a.csv\n'
            (root / 'later.csv').write_text('l\n', encoding='utf-8')
            trail.offtrail.add('later.csv')
            assert trail.assets.scan(root / 'later.csv') == ()

    def test_removing_a_rule_tracks_what_it_ignored(self) -> None:
        with self.workspace('# dotfiles\n.*\n') as root:
            trail = Trail(root)
            assert root / '.env' not in trail.assets
            assert trail.offtrail.remove('.*') == ['.*']
            assert root / '.env' in trail.assets
            assert root / 'data' / '.~b.csv' in trail.assets
            assert (root / '.offtrail').read_text(encoding='utf-8') == '# dotfiles\n'

    def test_an_explicit_track_wins(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            asset = trail.ontrail(root / '.env')
            assert asset.path == root / '.env'
            assert root / '.env' in Trail(root).assets

    def test_a_line_that_does_not_parse_is_reported(self) -> None:
        with self.workspace('.*\n!\n') as root:
            trail = Trail(root)
            [warning] = trail.offtrail.warnings
            assert warning == f"{root / '.offtrail'}:2: Not a rule: '!'"
            assert root / '.env' not in trail.assets
