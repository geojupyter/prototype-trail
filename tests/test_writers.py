from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from trail import Trail
from trail.event import (
    AddAssetEvent,
    Event,
    FileAccessEvent,
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


def logged(
        trail: Trail,
        name: str,
) -> list[str]:
    # the records about one file, after the project's own setup
    out = []
    for event in trail.events:
        if (
            not isinstance(event, AddAssetEvent | WatchdogEvent)
            or Path(event.src_path).name != name
        ):
            continue
        if isinstance(event, AddAssetEvent):
            out.append('discovered')
        else:
            out.append(f'{type(event).__name__} {event.event_type}')
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
            await asyncio.sleep(0.5)
            streets.assign(speed=25).to_crs(3857).to_file(path, driver='GPKG')
            await until(trail, 2, FileAccessEvent)

    asyncio.run(run())
    # one record per write; SQLite's journal is never tracked
    assert logged(trail, 'streets.gpkg') == [
        'discovered',
        'FileAccessEvent created',
        'FileAccessEvent modified',
    ]
    assert not [asset for asset in trail.assets if asset.path.name.endswith('-journal')]


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
            await until(trail, 3, FileAccessEvent)

    asyncio.run(run())
    # GDAL reads the old file, deletes it and writes a new one
    assert logged(trail, 'scene.tif') == [
        'discovered',
        'FileAccessEvent created',
        'FileAccessEvent opened',
        'WatchdogEvent deleted',
        'FileAccessEvent created',
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
            await until(trail, 2, FileAccessEvent)

    asyncio.run(run())
    # five parts on disk, one asset and one record per write
    assert sorted(item.name for item in trail.dir.parent.iterdir() if item.name.startswith('roads')) == [
        'roads.cpg', 'roads.dbf', 'roads.prj', 'roads.shp', 'roads.shx',
    ]
    assert [asset.path.name for asset in trail.assets] == ['roads.shp']
    # GDAL deletes the old shapefile and writes a new one
    assert logged(trail, 'roads.shp') == [
        'discovered',
        'FileAccessEvent created',
        'WatchdogEvent deleted',
        'FileAccessEvent created',
    ]
    for name in ('roads.shx', 'roads.dbf', 'roads.prj', 'roads.cpg'):
        assert logged(trail, name) == []
