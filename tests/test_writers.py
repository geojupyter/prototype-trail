from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from trail import Trail
from trail.event import (
    AddAssetEvent,
    DirectoryAccessEvent,
    Event,
    FileAccessEvent,
    MetadataEvent,
    WatchdogEvent,
)

pytestmark = pytest.mark.skipif(sys.platform != 'linux', reason='only inotify reports closes')


async def until(
        trail: Trail,
        count: int,
        kind: type[Event],
        timeout: float = 15,
) -> None:
    async with asyncio.timeout(timeout):
        while len([event for event in trail.events if type(event) is kind]) < count:
            await asyncio.sleep(0.05)


async def changed(
        trail: Trail,
        kind: type[Event],
        timeout: float = 15,
) -> Event:
    # the record of a write that carries what changed in the file
    async with asyncio.timeout(timeout):
        while True:
            found = [
                event
                for event in trail.events
                if type(event) is kind
                and getattr(event, 'changes', None)
            ]
            if found:
                return found[-1]
            await asyncio.sleep(0.05)


def logged(
        trail: Trail,
        name: str,
) -> list[str]:
    # the records about one file, after the project's own setup
    out = []
    for event in trail.events:
        if (
            not isinstance(event, AddAssetEvent | WatchdogEvent | MetadataEvent)
            or Path(event.src_path).name != name
        ):
            continue
        if isinstance(event, MetadataEvent):
            out.append(f'changed {event.basis}')
        elif isinstance(event, AddAssetEvent):
            out.append('discovered')
        elif getattr(event, 'changes', None):
            out.append(f'{type(event).__name__} {event.event_type} with changes')
        else:
            out.append(f'{type(event).__name__} {event.event_type}')
    return out


def described(trail: Trail) -> list[Event]:
    out = [
        event
        for event in trail.events
        if isinstance(event, DirectoryAccessEvent | MetadataEvent)
        and event.changes
    ]
    return out


def project(tmp_path: Path, rules: str) -> Trail:
    root = tmp_path.resolve()
    (root / '.ontrail').write_text(f'./\n{rules}', encoding='utf-8')
    out = Trail(root)
    return out


def test_a_geopackage_written_by_gdal(tmp_path: Path) -> None:
    gpd = pytest.importorskip('geopandas')
    shapely = pytest.importorskip('shapely.geometry')
    trail = project(tmp_path, '*.gpkg\n')
    path = trail.dir.parent / 'streets.gpkg'
    points = [shapely.Point(i * 0.01, i * 0.01) for i in range(500)]
    streets = gpd.GeoDataFrame({'name': [f's{i}' for i in range(500)]}, geometry=points, crs='EPSG:4326')

    async def run() -> None:
        session = trail.watchdog.session
        session.fold_settle = 0.3
        async with session:
            streets.to_file(path, driver='GPKG')
            await until(trail, 1, FileAccessEvent)
            # the new file's baseline is read in the background before the next write
            await asyncio.sleep(0.5)
            streets.assign(speed=25).to_crs(3857).to_file(path, driver='GPKG')
            await changed(trail, FileAccessEvent)

    asyncio.run(run())
    # one record per write, with what changed; SQLite's journal is never tracked
    assert logged(trail, 'streets.gpkg') == [
        'discovered',
        'FileAccessEvent created',
        'FileAccessEvent modified with changes',
    ]
    assert not [asset for asset in trail.assets if asset.path.name.endswith('-journal')]
    [event] = trail.events.select('cls=FileAccessEvent event_type=modified')
    changes = [
        value
        for name, value in event._repr_items()
        if name == 'change'
    ]
    assert changes[0] == "layer 'streets' CRS EPSG:4326 → EPSG:3857"
    assert changes[1].startswith('layers.streets.bounds: [0.0, 0.0, 4.99, 4.99] → ')
    assert changes[2:] == ["column 'speed' added in layer 'streets'"]


def test_a_geotiff_rewritten_by_rasterio(tmp_path: Path) -> None:
    rasterio = pytest.importorskip('rasterio')
    numpy = pytest.importorskip('numpy')
    from rasterio.transform import from_origin

    trail = project(tmp_path, '*.tif\n')
    path = trail.dir.parent / 'scene.tif'

    def write(bands: int, crs: str) -> None:
        profile = {
            'driver': 'GTiff',
            'width': 64,
            'height': 64,
            'count': bands,
            'dtype': 'uint8',
            'crs': crs,
            'transform': from_origin(500000, 4200000, 10, 10),
        }
        with rasterio.open(path, 'w', **profile) as dataset:
            dataset.write(numpy.zeros((bands, 64, 64), 'uint8'))

    async def run() -> None:
        session = trail.watchdog.session
        session.fold_settle = 0.3
        async with session:
            write(3, 'EPSG:32633')
            await until(trail, 1, FileAccessEvent)
            await asyncio.sleep(0.5)
            write(4, 'EPSG:32634')
            await changed(trail, FileAccessEvent)

    asyncio.run(run())
    event = trail.events.select('cls=FileAccessEvent').select[-1]
    assert [change for change in event._repr_items() if change[0] == 'change'] == [
        ('change', 'bands 3 → 4'),
        ('change', 'CRS EPSG:32633 → EPSG:32634'),
    ]
    # GDAL reads the old file, deletes it and writes a new one. Trail's own reads are not logged
    assert logged(trail, 'scene.tif') == [
        'discovered',
        'FileAccessEvent created',
        'FileAccessEvent opened',
        'WatchdogEvent deleted',
        'FileAccessEvent created with changes',
    ]


def test_a_zarr_store_rewritten_by_xarray(tmp_path: Path) -> None:
    xarray = pytest.importorskip('xarray')
    numpy = pytest.importorskip('numpy')
    pytest.importorskip('zarr')
    trail = project(tmp_path, '*.zarr\n')
    path = trail.dir.parent / 'cube.zarr'

    def write(times: int, extra: bool) -> None:
        data = {'temperature': (('time', 'y', 'x'), numpy.zeros((times, 8, 8), 'float32'))}
        if extra:
            data['pressure'] = (('time',), numpy.zeros(times))
        xarray.Dataset(data).to_zarr(path, zarr_format=3, consolidated=False, mode='w')

    write(4, False)
    trail.assets.scan(path)

    async def run() -> None:
        session = trail.watchdog.session
        # a dirasset's access closes once the store is quiet
        session.idle_gap = 0.5
        async with session:
            await asyncio.sleep(0.5)
            write(3, True)
            async with asyncio.timeout(15):
                while not described(trail):
                    await asyncio.sleep(0.05)

    asyncio.run(run())
    # xarray deletes the store and writes it again. The deletion logs the access before the
    # store is read, so its changes follow as their own record
    [event] = described(trail)
    assert isinstance(event, DirectoryAccessEvent | MetadataEvent)
    # Trail's own reads of the store are not logged as accesses
    assert [
        event.event_type
        for event in trail.events
        if isinstance(event, DirectoryAccessEvent)
        and event.event_type == 'opened'
    ] == []
    assert [change for change in event._repr_items() if change[0] == 'change'] == [
        ('change', "array 'temperature' shape [4, 8, 8] → [3, 8, 8]"),
        ('change', "array 'temperature' chunks [4, 8, 8] → [3, 8, 8]"),
        ('change', "array 'pressure' added"),
    ]



def test_a_shapefile_written_by_gdal(tmp_path: Path) -> None:
    gpd = pytest.importorskip('geopandas')
    shapely = pytest.importorskip('shapely.geometry')
    trail = project(tmp_path, '*.shp\n')
    path = trail.dir.parent / 'roads.shp'
    points = [shapely.Point(i * 0.01, i * 0.01) for i in range(200)]
    roads = gpd.GeoDataFrame({'name': [f'r{i}' for i in range(200)]}, geometry=points, crs='EPSG:4326')

    async def run() -> None:
        session = trail.watchdog.session
        session.fold_settle = 0.3
        async with session:
            roads.to_file(path)
            await until(trail, 1, FileAccessEvent)
            await asyncio.sleep(0.5)
            roads.assign(lanes=2).to_crs(3857).to_file(path)
            await changed(trail, FileAccessEvent)

    asyncio.run(run())
    # five parts on disk, one asset and one record per write
    assert sorted(item.name for item in trail.dir.parent.iterdir() if item.name.startswith('roads')) == [
        'roads.cpg', 'roads.dbf', 'roads.prj', 'roads.shp', 'roads.shx',
    ]
    assert [asset.path.name for asset in trail.assets] == ['roads.shp']
    records = [
        line
        for line in logged(trail, 'roads.shp')
        if line != 'discovered'
    ]
    assert records[0] == 'FileAccessEvent created'
    assert records[-1].endswith('with changes')
    for name in ('roads.shx', 'roads.dbf', 'roads.prj', 'roads.cpg'):
        assert logged(trail, name) == []
    event = trail.events.select('cls=FileAccessEvent').select[-1]
    changes = [
        value
        for name, value in event._repr_items()
        if name == 'change'
    ]
    assert "field 'lanes' added" in changes
    assert 'CRS GCS_WGS_1984 → WGS_1984_Web_Mercator_Auxiliary_Sphere' in changes
