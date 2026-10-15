from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from trail import Trail
from trail.asset import Asset, DirAsset
from trail.event import AddAssetEvent, RemoveAssetEvent
from trail.util import EVERYTHING

# the temporary name zarr writes a key to before renaming it into place
PARTIAL = '.0123456789abcdef0123456789abcdef.partial'


class TestDirAssets:
    """A directory tracked whole, and the names of the files that are part of it."""

    @staticmethod
    @contextmanager
    def workspace() -> Iterator[Path]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            cube = root / 'cube.zarr'
            (cube / 'arr' / 'c' / '0').mkdir(parents=True)
            (cube / 'zarr.json').write_text('{}', encoding='utf-8')
            (cube / 'arr' / 'zarr.json').write_text('{}', encoding='utf-8')
            (cube / 'arr' / 'c' / '0' / '0').write_bytes(b'abc')
            (root / 'table').mkdir()
            (root / 'table' / 'part-0.csv').write_text('a\n', encoding='utf-8')
            (root / 'dataset.csv').write_text('a\n', encoding='utf-8')
            yield root

    @staticmethod
    def children(
            asset: DirAsset,
            names: list[str],
    ) -> list[str]:
        pattern = re.compile(asset.children, re.IGNORECASE)
        return [
            name
            for name in names
            if pattern.fullmatch(name)
        ]

    def test_a_tracked_directory_is_a_dirasset(self) -> None:
        with self.workspace() as root:
            trail = Trail(root, markers=None)
            cube = trail.track(root / 'cube.zarr')
            table = trail.track(root / 'table')
            csv = trail.track(root / 'dataset.csv')
            assert isinstance(cube, DirAsset)
            assert isinstance(table, DirAsset)
            assert type(csv) is Asset
            assert cube.is_directory
            assert not csv.is_directory
            assert repr(cube).startswith('DirAsset\n')
            assert '    files: 3' in repr(cube)

    def test_a_dirasset_survives_a_reload(self) -> None:
        with self.workspace() as root:
            trail = Trail(root, markers=None)
            cube = trail.track(root / 'cube.zarr')
            csv = trail.track(root / 'dataset.csv')
            restored = Trail(root)
            assert isinstance(restored.assets[cube.id], DirAsset)
            assert restored.assets[root / 'cube.zarr'].id == cube.id
            assert type(restored.assets[csv.id]) is Asset

    def test_children_match_the_files_of_each_format(self) -> None:
        zarr = DirAsset(path=Path('/data/cube.zarr'))
        names = [
            '0',
            '0.0.0',
            '12.3',
            'zarr.json',
            'ZARR.JSON',
            '.zarray',
            '.zattrs',
            '.zgroup',
            '.zmetadata',
        ]
        leftovers = [
            f'0.0.0{PARTIAL}',
            f'zarr.json{PARTIAL}',
            '.DS_Store',
            'notes.md',
        ]
        assert self.children(zarr, names + leftovers) == names

        n5 = DirAsset(path=Path('/data/cube.n5'))
        names = ['0', '17', 'attributes.json']
        leftovers = [f'attributes.json{PARTIAL}', '0.0']
        assert self.children(n5, names + leftovers) == names

        gdb = DirAsset(path=Path('/data/roads.gdb'))
        names = [
            'gdb',
            'timestamps',
            'a00000001.gdbtable',
            'a00000001.gdbtablx',
            'A00000001.GDBTABLE',
            'a00000004.gdbindexes',
            'a00000004.spx',
            'a00000004.freelist',
            'a00000004.horizon',
            'a0000000a.FDO_OBJECTID.atx',
        ]
        leftovers = [
            'a00000009._compress.gdbtable',
            'a00000009._compress.gdbtablx',
            '_gdb.host.1234.5678.sr.lock',
            'a00000004.host.1234.5678.sr.lock',
            'a00000004.host.1234.5678.rd.lock',
            '_gdb.host.1234.5678.wr.lock',
            '_gdb.host.1234.5678.ed.lock',
        ]
        assert self.children(gdb, names + leftovers) == names

        safe = DirAsset(path=Path('/data/S2A_MSIL2A.SAFE'))
        names = [
            'manifest.safe',
            'MTD_MSIL2A.xml',
            'INSPIRE.xml',
            'T33UUP_20260101T100000_B04_10m.jp2',
            'T33UUP_20260101T100000_PVI.jp2',
            'MSK_DETFOO_B04.gml',
            'S2_User_Product_Level-2A_Metadata.xsd',
            'UserProduct_index.html',
            'preview.png',
        ]
        leftovers = [
            'T33UUP_20260101T100000_B04_10m.jp2.aux.xml',
            'MTD_MSIL2A.xml.aux.xml',
            '.DS_Store',
        ]
        assert self.children(safe, names + leftovers) == names

        table = DirAsset(path=Path('/data/table'))
        assert table.children == EVERYTHING
        assert self.children(table, leftovers) == leftovers

    def test_tracking_a_child_of_a_dirasset_is_refused(self) -> None:
        with self.workspace() as root:
            cube = root / 'cube.zarr'
            trail = Trail(root, markers=None)
            # a directory whose suffix makes it a dirasset holds its children before it is tracked
            with pytest.raises(ValueError, match='part of'):
                trail.track(cube / 'zarr.json')
            trail.track(cube)
            for path in (cube / 'zarr.json', cube / 'arr' / 'c' / '0' / '0'):
                with pytest.raises(ValueError, match=f'part of {cube}'):
                    trail.track(path)
            table = trail.track(root / 'table')
            with pytest.raises(ValueError, match='part of'):
                trail.track(table.path / 'part-0.csv')
            assert len(trail.assets) == 2

    def test_a_file_that_is_not_a_child_can_be_tracked(self) -> None:
        with self.workspace() as root:
            cube = root / 'cube.zarr'
            notes = cube / 'notes.md'
            notes.write_text('a\n', encoding='utf-8')
            trail = Trail(root, markers=None)
            trail.track(cube)
            asset = trail.track(notes)
            assert type(asset) is Asset
            assert trail.assets[notes] is asset

    def test_tracking_a_directory_offtrails_its_children(self) -> None:
        with self.workspace() as root:
            table = root / 'table'
            trail = Trail(root, markers=None)
            part = trail.track(table / 'part-0.csv')
            outside = trail.track(root / 'dataset.csv')
            absorbed = 'tracking table whole offtrails 1 asset inside it'
            with pytest.warns(UserWarning, match=absorbed):
                trail.track(table)
            assert table / 'part-0.csv' not in trail.assets
            assert trail.assets[root / 'dataset.csv'] is outside
            removal = trail.events.select[-2]
            addition = trail.events.select[-1]
            assert isinstance(removal, RemoveAssetEvent)
            assert removal.entry is part
            assert isinstance(addition, AddAssetEvent)
            assert addition.src_path == str(table)

    def test_the_project_directory_cannot_be_tracked_whole(self) -> None:
        with self.workspace() as root:
            trail = Trail(root, markers=None)
            for path in (root, root.parent):
                with pytest.raises(ValueError, match='holds'):
                    trail.track(path)
            assert not trail.assets
            # a pathless Trail keeps its log in memory, so any directory can be tracked
            assert Trail().track(root).is_directory

    def test_marking_inside_a_dirasset_tracks_no_chunk(self) -> None:
        with self.workspace() as root:
            cube = root / 'cube.zarr'
            (root / '.markers').write_text('./\n*.json\n', encoding='utf-8')
            trail = Trail(root)
            assert not trail.assets
            trail.mark('cube.zarr/arr/')
            assert cube / 'arr' / 'zarr.json' not in trail.assets
            assert cube / 'arr' / 'c' / '0' / '0' not in trail.assets
            assert not trail.assets
            # the line names a directory inside a dirasset, so it makes no Dir
            assert list(trail.dirs.path2entry) == [root]
            [warning] = trail.markers.warnings
            assert "'cube.zarr/arr/' names a directory asset" in warning


if __name__ == "__main__":
    suite = TestDirAssets()
    for name in dir(suite):
        if name.startswith('test_'):
            getattr(suite, name)()
            print(f"  ✓ {name.removeprefix('test_').replace('_', ' ')}")
