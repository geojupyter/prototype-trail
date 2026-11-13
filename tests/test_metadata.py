from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import struct
import sys
import time
import zipfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from trail import Trail
from trail.cli.render import Renderer
from trail.event import FileAccessEvent, MetadataEvent
from trail.jupyter.metadata import JGISMetadata, QGISMetadata, sanitize
from trail.metadata import Change, Metadata, Summary, diff
from trail.metadata.general import DelimitedMetadata, NumpyMetadata
from trail.metadata.geospatial import (
    GeoPackageMetadata,
    GeoTIFFMetadata,
    ShapefileMetadata,
    ZarrMetadata,
)

FIXTURES = Path(__file__).parent / 'fixtures' / 'metadata'


@contextmanager
def project(ontrail: str = './\n*.qgs\n*.qgz\n*.jgis\n*.gpkg\n*.tif\n*.zarr\n*.txt\n') -> Iterator[Trail]:
    with TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        (root / '.ontrail').write_text(ontrail, encoding='utf-8')
        yield Trail(root)


def track(
        trail: Trail,
        name: str,
        content: str | bytes,
) -> Path:
    path = trail.dir.parent / name
    write(path, content)
    trail.assets.scan(path)
    return path


def write(
        path: Path,
        content: str | bytes,
) -> None:
    # a rewrite in the same timestamp tick with the same size would be invisible to a stat
    time.sleep(0.02)
    if isinstance(content, str):
        content = content.encode('utf-8')
    path.write_bytes(content)


def lines(event: MetadataEvent) -> list[str]:
    out = [
        value
        for name, value in event._repr_items()
        if name == 'change'
    ]
    return out


def qgs(
        opacity: float = 1.0,
        crs: str = 'EPSG:4326',
        extra: str = '',
) -> str:
    layers = f'''
    <maplayer type="vector">
      <id>roads_1</id><layername>Roads</layername><provider>ogr</provider>
      <datasource>dbname='gis' user='me' password='hunter2' table="roads"</datasource>
      <srs><spatialrefsys><authid>EPSG:4326</authid></spatialrefsys></srs>
      <layerOpacity>{opacity}</layerOpacity>
    </maplayer>{extra}'''
    tree = '<layer-tree-layer id="roads_1" checked="Qt::Checked"/>'
    if extra:
        tree += '<layer-tree-layer id="rivers_2" checked="Qt::Unchecked"/>'
    out = f'''<qgis version="3.34">
  <projectCrs><spatialrefsys><authid>{crs}</authid></spatialrefsys></projectCrs>
  <layer-tree-group>{tree}</layer-tree-group>
  <projectlayers>{layers}
  </projectlayers>
</qgis>'''
    return out


RIVERS = '''
    <maplayer type="vector">
      <id>rivers_2</id><layername>Rivers</layername><provider>ogr</provider>
      <datasource>/data/rivers.gpkg|layername=rivers</datasource>
      <srs><spatialrefsys><authid>EPSG:3857</authid></spatialrefsys></srs>
    </maplayer>'''


def jgis(
        layers: dict | None = None,
        path: str = 'data/crossings.geojson',
        projection: str = 'EPSG:3857',
) -> str:
    if layers is None:
        layers = {
            'L1': {
                'name': 'crossings',
                'type': 'VectorLayer',
                'visible': True,
                'parameters': {'source': 'S1', 'opacity': 1},
            },
        }
    project = {
        'schemaVersion': '0.5.0',
        'layers': layers,
        'sources': {
            'S1': {'name': 'crossings source', 'type': 'GeoJSONSource', 'parameters': {'path': path}},
        },
        'layerTree': list(layers),
        'options': {'projection': projection},
    }
    out = json.dumps(project)
    return out


class TestDiff:
    def test_dicts_are_compared_by_key(self) -> None:
        old = {'a': 1, 'b': {'c': 1}, 'gone': 0}
        new = {'a': 2, 'b': {'c': 1}, 'new': [1]}
        changes = list(diff(old, new))
        assert changes == [
            Change(path=('a',), verb='updated', old=1, new=2),
            Change(path=('gone',), verb='removed', old=0),
            Change(path=('new',), verb='added', new=[1]),
        ]

    def test_lists_and_types(self) -> None:
        assert [change.verb for change in diff([1, 2], [2, 1])] == ['reordered']
        assert [change.verb for change in diff([1, 2], [1, 3])] == ['updated']
        assert list(diff(True, 1)) != []
        assert list(diff(1, 1.0)) != []
        assert list(diff({'a': [1]}, {'a': [1]})) == []

    def test_a_change_survives_its_record(self) -> None:
        change = Change(path=('layers', 'L1'), verb='removed', old={'name': 'x'}, label='x')
        assert Change.from_record(change.to_record()) == change
        assert 'new' not in change.to_record()


class TestRegistry:
    def test_suffixes_pick_their_reader(self) -> None:
        assert Metadata.ext2cls['.qgz'] is QGISMetadata
        assert Metadata.ext2cls['.jgis'] is JGISMetadata
        assert Metadata.ext2cls['.gpkg'] is GeoPackageMetadata
        assert Metadata.name2cls['geotiff'] is GeoTIFFMetadata

    def test_an_invalid_suffix_is_refused(self) -> None:
        with pytest.raises(TypeError, match='Invalid file suffix'):
            type('BadMetadata', (Metadata,), {'suffixes': ('TXT',)})

    def test_an_asset_gets_the_class_of_its_suffix(self) -> None:
        with project() as trail:
            path = track(trail, 'map.jGIS', jgis())
            notes = track(trail, 'notes.txt', 'a')
            assert type(trail.assets[path].metadata) is JGISMetadata
            assert type(trail.assets[notes].metadata) is Metadata
            assert trail.assets[path].metadata is trail.assets[path].metadata


class TestObserve:
    def test_the_first_observation_is_a_baseline(self) -> None:
        with project() as trail:
            path = track(trail, 'map.jGIS', jgis())
            asset = trail.assets[path]
            assert asset.metadata.observe() is None
            assert asset.metadata.is_observed
            assert asset.metadata.observe() is None

    def test_a_jgis_change_logs_rows(self) -> None:
        with project() as trail:
            path = track(trail, 'map.jGIS', jgis())
            trail.assets.observe()
            layers = json.loads(jgis())['layers']
            layers['L2'] = {'name': 'signals', 'type': 'VectorLayer', 'visible': False, 'parameters': {'source': 'S1'}}
            layers['L1']['parameters']['opacity'] = 0.5
            write(path, jgis(layers, path='data/crossings_2026.geojson', projection='EPSG:4326'))
            [event] = trail.assets.observe('cell')
            assert event.format == 'jgis'
            assert event.basis == 'cell'
            assert lines(event) == [
                'map projection EPSG:3857 → EPSG:4326',
                "layer 'crossings' opacity 1 → 0.5",
                "layer 'signals' added",
                "source 'crossings source' path changed",
                'layer order changed',
            ]
            assert Renderer.verb(event) == 'changed'
            reopened = Trail(trail.dir)
            rebuilt = reopened.events[event.id]
            assert rebuilt.to_record() == event.to_record()
            assert lines(rebuilt) == lines(event)
            assert rebuilt.entry is reopened.assets[path]

    def test_a_file_with_no_reader_logs_its_stat(self) -> None:
        with project() as trail:
            path = track(trail, 'notes.txt', 'a')
            trail.assets.observe()
            write(path, 'longer')
            [event] = trail.assets.observe()
            assert event.format == ''
            assert event.changes == []
            assert event.st_size == 6
            assert trail.assets.observe() == []

    def test_a_damaged_file_keeps_the_last_good_summary(self) -> None:
        with project() as trail:
            path = track(trail, 'map.jGIS', jgis())
            metadata = trail.assets[path].metadata
            metadata.observe()
            write(path, '{broken')
            [event] = trail.assets.observe()
            assert event.changes == []
            assert 'JSONDecodeError' in metadata.error
            assert metadata.dict['layers']['L1']['name'] == 'crossings'
            write(path, jgis(projection='EPSG:4326'))
            [event] = trail.assets.observe()
            assert lines(event) == ['map projection EPSG:3857 → EPSG:4326']
            assert metadata.error == ''

    def test_a_late_capture_is_refused(self) -> None:
        with project() as trail:
            path = track(trail, 'map.jGIS', jgis())
            metadata = trail.assets[path].metadata
            metadata.observe()
            write(path, jgis(projection='EPSG:4326'))
            late = metadata.capture()
            metadata.observe()
            assert metadata.accept(late, 'watchdog') is None

    def test_dict_is_a_copy_and_logs_nothing(self) -> None:
        with project() as trail:
            path = track(trail, 'map.jGIS', jgis())
            before = len(trail.events)
            summary = trail.assets[path].metadata.dict
            summary['layers'].clear()
            assert trail.assets[path].metadata.dict['layers']
            assert len(trail.events) == before


class TestProjects:
    def test_a_qgis_project_change(self) -> None:
        with project() as trail:
            path = track(trail, 'parks.qgs', qgs())
            metadata = trail.assets[path].metadata
            summary: Summary = metadata.dict
            assert summary['layers']['roads_1']['source'] == "dbname='gis' user=*** password=*** table=\"roads\""
            write(path, qgs(opacity=0.5, crs='EPSG:3857', extra=RIVERS))
            [event] = trail.assets.observe()
            assert event.format == 'qgis'
            assert lines(event) == [
                'project CRS EPSG:4326 → EPSG:3857',
                "layer 'Roads' opacity 1.0 → 0.5",
                "layer 'Rivers' added",
                'layer order changed',
            ]

    def test_a_qgz_is_read_from_its_archive(self) -> None:
        with project() as trail:
            path = trail.dir.parent / 'parks.qgz'

            def pack(text: str) -> bytes:
                with TemporaryDirectory() as temporary:
                    archive = Path(temporary) / 'parks.qgz'
                    with zipfile.ZipFile(archive, 'w') as handle:
                        handle.writestr('parks.qgs', text)
                        handle.writestr('parks.qgd', b'')
                    out = archive.read_bytes()
                return out

            write(path, pack(qgs()))
            trail.assets.scan(path)
            trail.assets.observe()
            write(path, pack(qgs(crs='EPSG:3857')))
            [event] = trail.assets.observe()
            assert lines(event) == ['project CRS EPSG:4326 → EPSG:3857']

    @pytest.mark.parametrize('suffix', ['.qgs', '.qgz'])
    def test_projects_written_by_qgis(self, suffix: str) -> None:
        # written by QGIS 4.2 through PyQGIS: a project with one layer, then the same session after
        # a CRS change, an opacity change and two added layers, then saved again unchanged
        with project() as trail:
            path = trail.dir.parent / f'parks{suffix}'
            shutil.copyfile(FIXTURES / f'parks_a{suffix}', path)
            trail.assets.scan(path)
            metadata = trail.assets[path].metadata
            assert [layer['name'] for layer in metadata.dict['layers'].values()] == ['Roads']
            time.sleep(0.02)
            shutil.copyfile(FIXTURES / f'parks_b{suffix}', path)
            [event] = trail.assets.observe()
            assert lines(event) == [
                'project CRS EPSG:4326 → EPSG:3857',
                "layer 'Roads' opacity 1.0 → 0.5",
                "layer 'Rivers' added",
                "layer 'Tiles' added",
                'layer order changed',
            ]
            layers = {
                layer['name']: layer
                for layer in metadata.dict['layers'].values()
            }
            assert layers['Rivers']['is_visible'] is False
            assert layers['Tiles']['source'] == (
                'type=xyz&url=https://***@tile.example.org/{z}/{x}/{y}.png?apikey=***'
            )
            if suffix == '.qgs':
                time.sleep(0.02)
                shutil.copyfile(FIXTURES / 'parks_c.qgs', path)
                [resaved] = trail.assets.observe()
                assert resaved.changes == []

    def test_sanitize_strips_credentials(self) -> None:
        assert sanitize('https://user:pw@host/x?token=abc&v=1') == 'https://***@host/x?token=***&v=1'
        assert sanitize('password%3Dsecret') == 'password=***'


class TestDatasets:
    @staticmethod
    def gpkg(
            path: Path,
            srs: int,
            count: int,
            columns: tuple[str, ...],
    ) -> None:
        path.unlink(missing_ok=True)
        connection = sqlite3.connect(path)
        with connection:
            connection.executescript('''
                CREATE TABLE gpkg_spatial_ref_sys (srs_name TEXT, srs_id INTEGER, organization TEXT,
                    organization_coordsys_id INTEGER, definition TEXT);
                CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT, identifier TEXT,
                    description TEXT, last_change TEXT, min_x REAL, min_y REAL, max_x REAL,
                    max_y REAL, srs_id INTEGER);
                CREATE TABLE gpkg_geometry_columns (table_name TEXT, column_name TEXT,
                    geometry_type_name TEXT, srs_id INTEGER, z INTEGER, m INTEGER);
                CREATE TABLE gpkg_ogr_contents (table_name TEXT, feature_count INTEGER);
            ''')
            connection.executemany(
                'INSERT INTO gpkg_spatial_ref_sys VALUES (?, ?, ?, ?, ?)',
                [('WGS 84', 4326, 'EPSG', 4326, ''), ('Pseudo-Mercator', 3857, 'EPSG', 3857, '')],
            )
            connection.execute(
                "INSERT INTO gpkg_contents VALUES ('cities', 'features', 'cities', '', '', 0, 0, 1, 1, ?)",
                (srs,),
            )
            connection.execute("INSERT INTO gpkg_geometry_columns VALUES ('cities', 'geom', 'POINT', ?, 0, 0)", (srs,))
            connection.execute("INSERT INTO gpkg_ogr_contents VALUES ('cities', ?)", (count,))
            definition = ', '.join(f'{column} TEXT' for column in columns)
            connection.execute(f'CREATE TABLE cities (fid INTEGER PRIMARY KEY, geom BLOB, {definition})')
        connection.close()

    def test_a_geopackage_change(self) -> None:
        with project() as trail:
            path = trail.dir.parent / 'cities.gpkg'
            self.gpkg(path, 4326, 500, ('name',))
            trail.assets.scan(path)
            trail.assets.observe()
            time.sleep(0.02)
            self.gpkg(path, 3857, 525, ('name', 'country'))
            [event] = trail.assets.observe()
            assert event.format == 'geopackage'
            assert lines(event) == [
                "layer 'cities' CRS EPSG:4326 → EPSG:3857",
                "layer 'cities' features 500 → 525",
                "column 'country' added in layer 'cities'",
            ]

    def test_a_geotiff_written_by_rasterio(self) -> None:
        with project() as trail:
            path = trail.dir.parent / 'scene.tif'
            shutil.copyfile(FIXTURES / 'scene_a.tif', path)
            trail.assets.scan(path)
            summary = trail.assets[path].metadata.dict
            assert summary['crs'] == 'EPSG:32633'
            assert summary['tile'] == [32, 32]
            assert summary['compression'] == 'deflate'
            assert summary['transform'] == [10.0, 0.0, 500000.0, 0.0, -10.0, 4200000.0]
            time.sleep(0.02)
            shutil.copyfile(FIXTURES / 'scene_b.tif', path)
            [event] = trail.assets.observe()
            assert lines(event) == [
                'bands 3 → 4',
                'overviews: 2 → 0',
                'CRS EPSG:32633 → EPSG:32634',
                'nodata 0 → 255',
            ]

    def test_a_zarr_v3_store(self) -> None:
        with project() as trail:
            store = trail.dir.parent / 'cube.zarr'

            def node(path: Path, document: dict) -> None:
                path.mkdir(parents=True, exist_ok=True)
                write(path / 'zarr.json', json.dumps(document))

            def array(shape: list[int]) -> dict:
                out = {
                    'zarr_format': 3,
                    'node_type': 'array',
                    'shape': shape,
                    'data_type': 'float32',
                    'chunk_grid': {'name': 'regular', 'configuration': {'chunk_shape': shape}},
                    'codecs': [{'name': 'bytes'}, {'name': 'zstd'}],
                    'dimension_names': ['time', 'y', 'x'][-len(shape):],
                }
                return out

            node(store, {'zarr_format': 3, 'node_type': 'group'})
            node(store / 'temperature', array([4, 8, 8]))
            (store / 'temperature' / 'c').mkdir()
            trail.assets.scan(store)
            asset = trail.assets[store]
            assert type(asset.metadata) is ZarrMetadata
            trail.assets.observe()
            node(store / 'temperature', array([3, 8, 8]))
            node(store / 'pressure', array([3]))
            (store / 'pressure' / 'c').mkdir()
            [event] = trail.assets.observe()
            assert lines(event) == [
                "array 'temperature' shape [4, 8, 8] → [3, 8, 8]",
                "array 'temperature' chunks [4, 8, 8] → [3, 8, 8]",
                "array 'pressure' added",
            ]
            # a store caught in the middle of a write is a failed read, not a crash
            (store / 'zarr.json').write_text('{', encoding='utf-8')
            asset.metadata.observe(is_forced=True)
            assert 'JSONDecodeError' in asset.metadata.error

    def test_csv_numpy_and_shapefile_headers(self) -> None:
        with project('./\n*.csv\n*.npy\n*.dbf\n') as trail:
            csv = track(trail, 'a.csv', 'name\nx\n')
            assert trail.assets[csv].metadata.dict == {'delimiter': ',', 'columns': ['name']}
            assert type(trail.assets[csv].metadata) is DelimitedMetadata
            header = "{'descr': '<f4', 'fortran_order': False, 'shape': (4, 3), }"
            header = header.ljust(118) + '\n'
            npy = track(trail, 'a.npy', b'\x93NUMPY\x01\x00' + struct.pack('<H', len(header)) + header.encode('latin1'))
            assert type(trail.assets[npy].metadata) is NumpyMetadata
            assert trail.assets[npy].metadata.dict == {'shape': [4, 3], 'dtype': '<f4', 'is_fortran': False}
            fields = b'name' + b'\x00' * 7 + b'C' + b'\x00' * 4 + bytes([20, 0]) + b'\x00' * 14
            head = struct.pack('<B3BIHH', 3, 26, 1, 1, 2, 65, 21) + b'\x00' * 20
            dbf = track(trail, 'a.dbf', head + fields + b'\r' + b' ' * 42)
            assert type(trail.assets[dbf].metadata) is ShapefileMetadata
            assert trail.assets[dbf].metadata.dict == {'num_records': 2, 'fields': {'name': 'C(20,0)'}}


def test_a_shapefile_is_read_from_all_its_parts() -> None:
    # written by geopandas: five points in EPSG:4326, then six with a new column in EPSG:3857
    with project('./\n*.shp\n') as trail:
        root = trail.dir.parent
        for part in (FIXTURES / 'shapefile_a').iterdir():
            shutil.copyfile(part, root / part.name)
        trail.assets.scan(root)
        assert [asset.path.name for asset in trail.assets] == ['cities.shp']
        metadata = trail.assets[root / 'cities.shp'].metadata
        summary = metadata.dict
        assert summary['parts'] == ['.cpg', '.dbf', '.prj', '.shx']
        assert summary['num_records'] == 5
        # GDAL writes the projection as ESRI WKT, which names the CRS without an authority
        assert summary['crs']['name'] == 'GCS_WGS_1984'
        assert summary['encoding'] == 'UTF-8'
        time.sleep(0.02)
        # only the table and the projection change on disk, and the shapefile's stat covers them
        for name in ('cities.dbf', 'cities.prj', 'cities.shp', 'cities.shx'):
            shutil.copyfile(FIXTURES / 'shapefile_b' / name, root / name)
        [event] = trail.assets.observe()
        assert 'records 5 → 6' in lines(event)
        assert "field 'country' added" in lines(event)
        assert 'CRS GCS_WGS_1984 → WGS_1984_Web_Mercator_Auxiliary_Sphere' in lines(event)
        assert not [line for line in lines(event) if line.startswith('crs.digest')]


@pytest.mark.parametrize(
    ('stem', 'suffix', 'package', 'expected'),
    [
        ('cities', '.parquet', 'pyarrow', [
            'rows 5 → 6',
            "column 'country' added",
            "geometry column 'geometry' CRS EPSG:4326 → EPSG:3857",
        ]),
        ('cities', '.feather', 'pyarrow', [
            "column 'country' added",
            "geometry column 'geometry' CRS EPSG:4326 → EPSG:3857",
        ]),
        ('cities', '.fgb', None, [
            "name: 'cities_a' → 'cities_b'",
            'features 5 → 6',
            "column 'country' added",
            'CRS EPSG:4326 → EPSG:3857',
        ]),
        ('cities', '.geojson', None, [
            'features 5 → 6',
            "property 'country' added",
            "crs: 'urn:ogc:def:crs:OGC:1.3:CRS84' → 'urn:ogc:def:crs:EPSG::3857'",
        ]),
        ('climate', '.nc', 'netCDF4', [
            "dimension 'time' 4 → 3",
            "variable 'pressure' added",
        ]),
        ('weights', '.h5', 'h5py', [
            "dataset 'layer2/kernel' added",
        ]),
    ],
)
def test_files_written_by_real_writers(
        stem: str,
        suffix: str,
        package: str | None,
        expected: list[str],
) -> None:
    # the fixtures were written by geopandas, netCDF4 and h5py. A reader whose package is missing
    # leaves its format unread
    if package is not None:
        pytest.importorskip(package)
    with project(f'./\n*{suffix}\n') as trail:
        path = trail.dir.parent / f'data{suffix}'
        shutil.copyfile(FIXTURES / f'{stem}_a{suffix}', path)
        trail.assets.scan(path)
        trail.assets.observe()
        time.sleep(0.02)
        shutil.copyfile(FIXTURES / f'{stem}_b{suffix}', path)
        [event] = trail.assets.observe()
        assert trail.assets[path].metadata.error == ''
        described = [
            line
            for line in lines(event)
            if not line.startswith(('geo.', 'envelope'))
        ]
        assert described == expected


@pytest.mark.skipif(sys.platform != 'linux', reason='only inotify reports closes')
def test_the_watcher_reads_a_file_after_its_write_session() -> None:
    async def run() -> None:
        with project() as trail:
            path = track(trail, 'map.jGIS', jgis())
            session = trail.watchdog.session
            session.fold_settle = 0.2
            async with session:
                async with asyncio.timeout(5):
                    while not session.reads.windows:
                        await asyncio.sleep(0.02)
                await asyncio.sleep(0.3)
                write(path, jgis(projection='EPSG:4326'))
                async with asyncio.timeout(5):
                    while not trail.events.select('cls=FileAccessEvent event_type=modified'):
                        await asyncio.sleep(0.02)
                await asyncio.sleep(0.5)
            # the write and what changed are one record
            [event] = trail.events.select('cls=FileAccessEvent event_type=modified')
            assert event.format == 'jgis'
            assert lines(event) == ['map projection EPSG:3857 → EPSG:4326']
            assert len(trail.events.select('cls=MetadataEvent')) == 0
            folded = [
                event.event_type
                for event in trail.events
                if isinstance(event, FileAccessEvent)
            ]
            # the session starts with the Trail, so the file's creation is its first session.
            # Trail's own baseline read and its read after the write are not logged as opens
            assert folded == ['created', 'modified']
            reopened = Trail(trail.dir)
            assert lines(reopened.events[event.id]) == lines(event)

    asyncio.run(run())


class TestBaselines:
    """Changes made while Trail was not running, found against the baseline the last run kept."""

    def test_a_check_finds_a_change_made_between_runs(self) -> None:
        with project() as trail:
            path = track(trail, 'map.jGIS', jgis())
            trail.assets.observe()
            cache = json.loads((trail.dir / 'metadata.json').read_text(encoding='utf-8'))
            asset = trail.assets[path]
            assert cache['assets'][asset.id]['format'] == 'jgis'
            # Trail is not running while the map changes
            write(path, jgis(projection='EPSG:4326'))
            reopened = Trail(trail.dir)
            [event] = reopened.assets.observe()
            assert lines(event) == ['map projection EPSG:3857 → EPSG:4326']
            assert reopened.assets.observe() == []
            # the next run starts from what this one saw
            assert Trail(trail.dir).assets.observe() == []

    @pytest.mark.skipif(sys.platform != 'linux', reason='only inotify reports closes')
    def test_the_watcher_logs_changes_made_while_it_was_stopped(self) -> None:
        # the first run has no event loop, so no watcher runs while the files change
        with project() as trail:
            path = track(trail, 'map.jGIS', jgis())
            notes = track(trail, 'notes.txt', 'a')
            untouched = track(trail, 'other.jGIS', jgis())
            trail.assets.observe()
            write(path, jgis(projection='EPSG:4326'))
            write(notes, 'longer')

            async def run() -> Trail:
                reopened = Trail(trail.dir)
                session = reopened.watchdog.session
                async with session:
                    async with asyncio.timeout(5):
                        while len(reopened.events.select('cls=MetadataEvent')) < 2:
                            await asyncio.sleep(0.02)
                    await asyncio.sleep(0.3)
                    # an unchanged file with a baseline is not read again
                    assert str(untouched) not in session.reads.windows
                return reopened

            reopened = asyncio.run(run())
            changed = {
                Path(event.src_path).name: event
                for event in reopened.events.select('cls=MetadataEvent')
            }
            assert set(changed) == {'map.jGIS', 'notes.txt'}
            assert {event.basis for event in changed.values()} == {'startup'}
            assert lines(changed['map.jGIS']) == ['map projection EPSG:3857 → EPSG:4326']
            assert changed['notes.txt'].changes == []
            assert changed['notes.txt'].st_size == 6

    @pytest.mark.skipif(sys.platform != 'linux', reason='only inotify reports closes')
    def test_a_change_seen_while_running_is_not_logged_again(self) -> None:
        async def run() -> None:
            with project() as trail:
                path = track(trail, 'map.jGIS', jgis())
                session = trail.watchdog.session
                session.fold_settle = 0.2
                async with session:
                    async with asyncio.timeout(5):
                        while not session.reads.windows:
                            await asyncio.sleep(0.02)
                    await asyncio.sleep(0.3)
                    write(path, jgis(projection='EPSG:4326'))
                    async with asyncio.timeout(5):
                        while not trail.events.select('cls=FileAccessEvent event_type=modified'):
                            await asyncio.sleep(0.02)
                    await asyncio.sleep(0.3)
                [written] = trail.events.select('cls=FileAccessEvent event_type=modified')
                assert written.changes
                reopened = Trail(trail.dir)
                async with reopened.watchdog.session:
                    await asyncio.sleep(0.5)
                assert len(reopened.events.select('cls=MetadataEvent')) == 0

        asyncio.run(run())

    def test_untracking_forgets_the_baseline(self) -> None:
        with project() as trail:
            path = track(trail, 'map.jGIS', jgis())
            trail.assets.observe()
            identifier = trail.assets[path].id
            trail.offtrail(path)
            cache = json.loads((trail.dir / 'metadata.json').read_text(encoding='utf-8'))
            assert identifier not in cache['assets']

    def test_a_damaged_cache_is_ignored(self) -> None:
        with project() as trail:
            path = track(trail, 'map.jGIS', jgis())
            trail.assets.observe()
            (trail.dir / 'metadata.json').write_text('{', encoding='utf-8')
            write(path, jgis(projection='EPSG:4326'))
            reopened = Trail(trail.dir)
            with pytest.warns(UserWarning, match='metadata.json is ignored'):
                assert reopened.assets.observe() == []
            # the read became the new baseline and was written back
            cache = json.loads((trail.dir / 'metadata.json').read_text(encoding='utf-8'))
            assert reopened.assets[path].id in cache['assets']
