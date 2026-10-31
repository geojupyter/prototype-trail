from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from trail import Trail


class TestEntries:
    """`trail.entries`, which looks up the assets and the Dirs together."""

    @staticmethod
    @contextmanager
    def workspace() -> Iterator[Path]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / '.markers').write_text('./\n*.csv\ndata/\n', encoding='utf-8')
            (root / 'data').mkdir()
            (root / 'data' / 'a.csv').write_text('a\n', encoding='utf-8')
            (root / 'new.csv').write_text('b\n', encoding='utf-8')
            yield root

    def test_a_lookup_finds_either_kind_by_id_and_by_path(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            directory = trail.dirs[root / 'data']
            asset = trail.assets[root / 'new.csv']
            assert trail.entries[directory.id] is directory
            assert trail.entries[root / 'data'] is directory
            assert trail.entries[asset.id] is asset
            assert trail.entries[str(root / 'new.csv')] is asset
            assert trail.entries[asset.id, root / 'data'] == (asset, directory)

    def test_a_key_that_is_neither_raises_or_gets_the_default(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            with pytest.raises(KeyError):
                trail.entries['0' * 32]
            with pytest.raises(KeyError):
                trail.entries[root / 'missing.csv']
            assert trail.entries.get('0' * 32) is None
            assert trail.entries.get(root / 'missing.csv', 'missing') == 'missing'

    def test_in_holds_for_an_entry_an_id_and_a_path(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            directory = trail.dirs[root / 'data']
            asset = trail.assets[root / 'new.csv']
            assert directory in trail.entries
            assert asset in trail.entries
            assert asset.id in trail.entries
            assert root / 'data' in trail.entries
            assert '0' * 32 not in trail.entries
            trail.offtrail(root / 'new.csv')
            assert asset not in trail.entries

    def test_iteration_yields_the_dirs_then_the_assets(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            paths = [entry.path for entry in trail.entries]
            assert paths == [root, root / 'data', root / 'new.csv', root / 'data' / 'a.csv']
            assert len(trail.entries) == 4
            assert repr(trail.entries).startswith('Entries (4)\n    0. Dir\n')

    def test_an_untracked_path_stays_untracked_after_a_reload(self) -> None:
        with self.workspace() as root:
            csv = root / 'new.csv'
            trail = Trail(root)
            trail.offtrail(csv)
            assert csv in trail.entries.untracked
            restored = Trail(root)
            assert csv in restored.entries.untracked
            assert csv not in restored.assets
            # tracked again, the path is no longer untracked
            restored.track(csv)
            assert csv not in restored.entries.untracked

    def test_metadata_is_the_trail_directory_and_what_it_holds(self) -> None:
        with self.workspace() as root:
            trail = Trail(root)
            assert trail.entries.is_metadata(root / '.trail')
            assert trail.entries.is_metadata(root / '.trail' / 'events.jsonl')
            assert not trail.entries.is_metadata(root / 'new.csv')
            # a Trail with no directory has no metadata
            assert not Trail().entries.is_metadata(root / '.trail')
