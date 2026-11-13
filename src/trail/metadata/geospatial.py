from __future__ import annotations

import hashlib
import json
import os
import re
import struct
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO, ClassVar

from trail.metadata import Change, JSONValue, Metadata, Summary, canonical


def digest(value: JSONValue) -> str:
    text = canonical(value)
    out = (
        hashlib
        .blake2b(text.encode('utf-8'), digest_size=8)
        .hexdigest()
    )
    return out


def capped(
        mapping: dict[str, JSONValue],
        cap: int,
) -> dict[str, JSONValue]:
    if len(mapping) <= cap:
        return mapping
    out = {
        'count': len(mapping),
        'digest': digest(mapping),
    }
    return out


def crs_code(crs: JSONValue) -> str | None:
    if crs is None:
        return None
    if isinstance(crs, dict):
        identifier = crs.get('id')
        if isinstance(identifier, dict):
            out = f"{identifier.get('authority')}:{identifier.get('code')}"
            return out
    out = f'digest:{digest(crs)}'
    return out


def exact(
        file: BinaryIO,
        size: int,
) -> bytes:
    out = file.read(size)
    if len(out) != size:
        raise ValueError(f'Truncated: wanted {size} bytes, got {len(out)}')
    return out


def local(tag: str) -> str:
    # an XML tag without its namespace
    out = tag.rpartition('}')[2]
    return out


class ParquetMetadata(Metadata):
    name: ClassVar[str] = 'parquet'
    suffixes: ClassVar[tuple[str, ...]] = ('.parquet', '.geoparquet')
    max_columns: ClassVar[int] = 200
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('num_rows',): 'rows {old} → {new}',
        ('columns', '*'): "column '{1}' {verb}",
        ('geo', 'columns', '*', 'crs'): "geometry column '{2}' CRS {old} → {new}",
    }

    @staticmethod
    def geo(raw: bytes | None) -> JSONValue:
        if raw is None:
            return None
        declared = json.loads(raw)
        columns = {}
        declared_columns = declared.get('columns', {})
        for name, column in declared_columns.items():
            # an absent crs means OGC:CRS84; a null crs means unknown
            crs = 'OGC:CRS84'
            if 'crs' in column:
                crs = crs_code(column['crs'])
            columns[name] = {
                'encoding': column.get('encoding'),
                'geometry_types': column.get('geometry_types'),
                'crs': crs,
                'bbox': column.get('bbox'),
            }
        out = {
            'primary_column': declared.get('primary_column'),
            'columns': columns,
        }
        return out

    def read(self) -> Summary:
        import pyarrow.parquet as pq

        with pq.ParquetFile(self._asset.path) as file:
            metadata = file.metadata
            schema = file.schema_arrow
        columns = {
            field.name: str(field.type)
            for field in schema
        }
        declared = None
        if schema.metadata is not None:
            declared = schema.metadata.get(b'geo')
        out = {
            'num_rows': metadata.num_rows,
            'num_row_groups': metadata.num_row_groups,
            'columns': capped(columns, self.max_columns),
            'geo': self.geo(declared),
        }
        return out


class FeatherMetadata(Metadata):
    name: ClassVar[str] = 'feather'
    suffixes: ClassVar[tuple[str, ...]] = ('.feather', '.arrow')
    max_columns: ClassVar[int] = 200
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('columns', '*'): "column '{1}' {verb}",
        ('geo', 'columns', '*', 'crs'): "geometry column '{2}' CRS {old} → {new}",
    }

    def read(self) -> Summary:
        import pyarrow as pa
        from pyarrow import ipc

        with pa.memory_map(str(self._asset.path)) as source:
            reader = ipc.open_file(source)
            schema = reader.schema
            batches = reader.num_record_batches
        columns = {
            field.name: str(field.type)
            for field in schema
        }
        declared = None
        if schema.metadata is not None:
            declared = schema.metadata.get(b'geo')
        out = {
            'num_record_batches': batches,
            'columns': capped(columns, self.max_columns),
            'geo': ParquetMetadata.geo(declared),
        }
        return out


class GeoTIFFMetadata(Metadata):
    name: ClassVar[str] = 'geotiff'
    suffixes: ClassVar[tuple[str, ...]] = ('.tif', '.tiff')
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('crs',): 'CRS {old} → {new}',
        ('width',): 'width {old} → {new} px',
        ('height',): 'height {old} → {new} px',
        ('bands',): 'bands {old} → {new}',
        ('nodata',): 'nodata {old} → {new}',
    }
    # struct codes of the TIFF field types
    types: ClassVar[dict[int, str]] = {
        1: 'B', 2: 'c', 3: 'H', 4: 'I', 5: 'II', 6: 'b', 7: 'B', 8: 'h', 9: 'i',
        10: 'ii', 11: 'f', 12: 'd', 13: 'I', 16: 'Q', 17: 'q', 18: 'Q',
    }
    wanted: ClassVar[frozenset[int]] = frozenset({
        254, 256, 257, 258, 259, 277, 322, 323, 339, 33550, 33922, 34264, 34735, 42113,
    })
    compressions: ClassVar[dict[int, str]] = {
        1: 'none', 5: 'lzw', 7: 'jpeg', 8: 'deflate', 32773: 'packbits', 32946: 'deflate',
        34712: 'jp2k', 34887: 'lerc', 50000: 'zstd', 50001: 'webp',
    }

    def ifds(self, file: BinaryIO) -> Iterator[dict[int, tuple]]:
        head = exact(file, 16)
        orders = {b'II': '<', b'MM': '>'}
        order = orders.get(head[:2])
        if order is None:
            raise ValueError('Not a TIFF')
        magic = struct.unpack_from(order + 'H', head, 2)[0]
        if magic == 42:
            count_code = 'H'
            entry_code = 'HHI4s'
            pointer = 'I'
            offset = struct.unpack_from(order + 'I', head, 4)[0]
        elif magic == 43:
            count_code = 'Q'
            entry_code = 'HHQ8s'
            pointer = 'Q'
            offset = struct.unpack_from(order + 'Q', head, 8)[0]
        else:
            raise ValueError('Not a TIFF')
        entry_size = struct.calcsize(order + entry_code)
        seen = set()
        while offset:
            if offset in seen:
                raise ValueError('The IFDs form a loop')
            seen.add(offset)
            file.seek(offset)
            raw = exact(file, struct.calcsize(order + count_code))
            count = struct.unpack(order + count_code, raw)[0]
            block = exact(file, count * entry_size + struct.calcsize(order + pointer))
            tags = {}
            for index in range(count):
                tag, kind, length, value = struct.unpack_from(
                    order + entry_code,
                    block,
                    index * entry_size,
                )
                if (
                    tag not in self.wanted
                    or kind not in self.types
                ):
                    continue
                code = self.types[kind]
                size = struct.calcsize('<' + code) * length
                # a value too long for the entry is stored elsewhere in the file
                if size > len(value):
                    file.seek(struct.unpack(order + pointer, value)[0])
                    value = exact(file, size)
                if code == 'c':
                    tags[tag] = (value[:length],)
                else:
                    tags[tag] = struct.unpack_from(order + code * length, value)
            offset = struct.unpack_from(order + pointer, block, count * entry_size)[0]
            yield tags

    @staticmethod
    def crs(keys: tuple | None) -> str | None:
        if keys is None:
            return None
        values = {
            keys[index]: keys[index + 3]
            for index in range(4, len(keys) - 3, 4)
            if keys[index + 1] == 0
        }
        # the projected code, else the geographic one
        code = (
            values.get(3072)
            or values.get(2048)
        )
        if code is None:
            return None
        if code == 32767:
            return 'user-defined'
        out = f'EPSG:{code}'
        return out

    @staticmethod
    def transform(tags: dict[int, tuple]) -> list[float] | None:
        matrix = tags.get(34264)
        if matrix is not None:
            out = [matrix[0], matrix[1], matrix[3], matrix[4], matrix[5], matrix[7]]
            return out
        scale = tags.get(33550)
        tie = tags.get(33922)
        if (
            scale is None
            or tie is None
        ):
            return None
        out = [
            scale[0],
            0.0,
            tie[3] - tie[0] * scale[0],
            0.0,
            -scale[1],
            tie[4] + tie[1] * scale[1],
        ]
        return out

    def read(self) -> Summary:
        with self._asset.path.open('rb') as file:
            pages = list(self.ifds(file))
        if not pages:
            raise ValueError('The TIFF has no image')
        first = pages[0]
        kinds = {1: 'uint', 2: 'int', 3: 'float'}
        kind = kinds.get(first.get(339, (1,))[0], 'unknown')
        bits = first.get(258, (1,))[0]
        tile = None
        if 322 in first:
            tile = [first[322][0], first[323][0]]
        nodata = None
        if 42113 in first:
            nodata = (
                first[42113][0]
                .rstrip(b'\x00')
                .decode('ascii', 'replace')
            )
        overviews = 0
        for page in pages[1:]:
            subfile = page.get(254, (0,))[0]
            # a reduced-resolution image that is not a mask
            if (
                subfile & 1
                and not subfile & 4
            ):
                overviews += 1
        compression = first.get(259, (1,))[0]
        out = {
            'width': first[256][0],
            'height': first[257][0],
            'bands': first.get(277, (1,))[0],
            'dtype': f'{kind}{bits}',
            'compression': self.compressions.get(compression, str(compression)),
            'tile': tile,
            'overviews': overviews,
            'crs': self.crs(first.get(34735)),
            'transform': self.transform(first),
            'nodata': nodata,
        }
        return out


class GeoPackageMetadata(Metadata):
    name: ClassVar[str] = 'geopackage'
    suffixes: ClassVar[tuple[str, ...]] = ('.gpkg',)
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('layers', '*'): "layer '{1}' {verb}",
        ('layers', '*', 'num_features'): "layer '{1}' features {old} → {new}",
        ('layers', '*', 'crs'): "layer '{1}' CRS {old} → {new}",
        ('layers', '*', 'columns', '*'): "column '{3}' {verb} in layer '{1}'",
    }

    def read(self) -> Summary:
        import sqlite3
        from urllib.parse import quote

        uri = f'file:{quote(str(self._asset.path))}?mode=ro'
        connection = sqlite3.connect(uri, uri=True)
        try:
            rows = connection.execute(
                'SELECT srs_id, organization, organization_coordsys_id FROM gpkg_spatial_ref_sys'
            )
            srs = {
                identifier: f'{organization}:{code}'
                for identifier, organization, code in rows
            }
            rows = connection.execute(
                'SELECT table_name, geometry_type_name FROM gpkg_geometry_columns'
            )
            geometry = dict(rows)
            counts = {}
            has_counts = (
                connection
                .execute("SELECT 1 FROM sqlite_master WHERE name = 'gpkg_ogr_contents'")
                .fetchone()
            )
            if has_counts:
                rows = connection.execute(
                    'SELECT table_name, feature_count FROM gpkg_ogr_contents'
                )
                counts = dict(rows)
            query = (
                'SELECT table_name, data_type, identifier, srs_id, min_x, min_y, max_x, max_y '
                'FROM gpkg_contents ORDER BY table_name'
            )
            contents = (
                connection
                .execute(query)
                .fetchall()
            )
            layers = {}
            for table, kind, identifier, srs_id, *bounds in contents:
                quoted = table.replace('"', '""')
                columns = {
                    row[1]: row[2]
                    for row in connection.execute(f'PRAGMA table_info("{quoted}")')
                }
                layers[table] = {
                    'name': identifier,
                    'type': kind,
                    'geometry': geometry.get(table),
                    'crs': srs.get(srs_id),
                    'bounds': bounds,
                    'num_features': counts.get(table),
                    'columns': columns,
                }
        finally:
            connection.close()
        out = {'layers': layers}
        return out


class ZarrMetadata(Metadata):
    name: ClassVar[str] = 'zarr'
    suffixes: ClassVar[tuple[str, ...]] = ('.zarr',)
    # the root zarr.json is read to find consolidated metadata
    listed: ClassVar[tuple[str, ...]] = ('.zmetadata', 'zarr.json')
    max_nodes: ClassVar[int] = 500
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('arrays', '*'): "array '{1}' {verb}",
        ('arrays', '*', 'shape'): "array '{1}' shape {old} → {new}",
        ('arrays', '*', 'chunks'): "array '{1}' chunks {old} → {new}",
        ('arrays', '*', 'dtype'): "array '{1}' dtype {old} → {new}",
    }

    def files(self) -> list[Path]:
        root = self._asset.path
        for name in ('.zmetadata', 'zarr.json'):
            path = root / name
            if not path.is_file():
                continue
            if name == 'zarr.json':
                # a store being written can hold a partial zarr.json
                try:
                    node = json.loads(path.read_text(encoding='utf-8'))
                except ValueError:
                    continue
                if (
                    not isinstance(node, dict)
                    or 'consolidated_metadata' not in node
                ):
                    continue
            # consolidated metadata describes the whole store in one file
            out = [path]
            return out
        found = []
        pending = [root]
        while (
            pending
            and len(found) < self.max_nodes
        ):
            directory = pending.pop()
            names = ('zarr.json', '.zarray', '.zgroup', '.zattrs')
            here = [
                directory / name
                for name in names
                if (directory / name).is_file()
            ]
            found.extend(here)
            is_array = (
                (directory / '.zarray').is_file()
                or (
                    (directory / 'zarr.json').is_file()
                    and (directory / 'c').is_dir()
                )
            )
            # an array holds chunks, not nodes
            if is_array:
                continue
            with os.scandir(directory) as entries:
                children = [
                    Path(entry.path)
                    for entry in entries
                    if entry.is_dir(follow_symlinks=False)
                ]
            pending.extend(sorted(children))
        return found

    def sources(self) -> list[Path]:
        out = self.files()
        return out

    @staticmethod
    def array(
            node: dict,
            attrs: dict,
    ) -> dict[str, JSONValue]:
        if node.get('zarr_format') == 3:
            chunks = (
                node.get('chunk_grid', {})
                .get('configuration', {})
                .get('chunk_shape')
            )
            codecs = [
                codec.get('name')
                for codec in node.get('codecs', [])
            ]
            out = {
                'shape': node.get('shape'),
                'chunks': chunks,
                'dtype': node.get('data_type'),
                'dims': node.get('dimension_names'),
                'codecs': codecs,
            }
            return out
        compressor = node.get('compressor')
        codecs = []
        if compressor is not None:
            codecs = [compressor.get('id')]
        out = {
            'shape': node.get('shape'),
            'chunks': node.get('chunks'),
            'dtype': node.get('dtype'),
            'dims': attrs.get('_ARRAY_DIMENSIONS'),
            'codecs': codecs,
        }
        return out

    def nodes(self) -> dict[str, dict]:
        root = self._asset.path
        files = self.files()
        documents = {}
        if (
            len(files) == 1
            and files[0].name == '.zmetadata'
        ):
            consolidated = json.loads(files[0].read_text(encoding='utf-8'))
            documents = consolidated['metadata']
        elif (
            len(files) == 1
            and files[0].parent == root
            and files[0].name == 'zarr.json'
        ):
            node = json.loads(files[0].read_text(encoding='utf-8'))
            documents['zarr.json'] = node
            inner = (
                node.get('consolidated_metadata', {})
                .get('metadata', {})
            )
            for key, value in inner.items():
                documents[f'{key}/zarr.json'] = value
        if not documents:
            for path in files:
                key = str(path.relative_to(root))
                documents[key] = json.loads(path.read_text(encoding='utf-8'))
        return documents

    def read(self) -> Summary:
        documents = self.nodes()
        if not documents:
            raise ValueError('The store has no metadata')
        arrays = {}
        groups = []
        version = None
        for key, node in sorted(documents.items()):
            directory, _, name = key.rpartition('/')
            location = directory or '/'
            if name == '.zarray':
                attrs = documents.get(f'{directory}/.zattrs'.lstrip('/'), {})
                arrays[location] = self.array(node, attrs)
                version = 2
            elif name == '.zgroup':
                groups.append(location)
                version = 2
            elif name == 'zarr.json':
                version = 3
                if node.get('node_type') == 'array':
                    arrays[location] = self.array(node, {})
                else:
                    groups.append(location)
        out = {
            'zarr_format': version,
            'groups': groups,
            'arrays': capped(arrays, self.max_nodes),
        }
        return out


# the files that make one shapefile with its .shp, by suffix
SHAPEFILE_PARTS: tuple[str, ...] = ('.shx', '.dbf', '.prj', '.cpg', '.qix', '.sbn', '.sbx')


def sibling_shp(path: Path) -> Path | None:
    """The .shp a shapefile part belongs to, matched by its stem in any case, or None."""
    if path.suffix.lower() not in SHAPEFILE_PARTS:
        return None
    for candidate in (path.with_suffix('.shp'), path.with_suffix('.SHP')):
        if candidate.exists():
            return candidate
    out = path.with_suffix('.shp')
    return out


def shapefile_parts(shp: Path) -> list[Path]:
    """The parts beside a .shp that exist, matched by stem in any case."""
    stem = shp.stem.lower()
    try:
        siblings = list(shp.parent.iterdir())
    except OSError:
        return []
    out = sorted(
        sibling
        for sibling in siblings
        if sibling.stem.lower() == stem
        and sibling.suffix.lower() in SHAPEFILE_PARTS
    )
    return out


class ShapefileMetadata(Metadata):
    """
    A shapefile. A .shp asset stands for the whole shapefile: its summary takes the geometry and
    bounds from the .shp, the records and fields from the .dbf, the CRS from the .prj and the
    encoding from the .cpg, and every part's stat decides whether it changed. A part tracked on
    its own is read on its own.
    """

    name: ClassVar[str] = 'shapefile'
    suffixes: ClassVar[tuple[str, ...]] = ('.shp', '.shx', '.dbf', '.prj', '.cpg')
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('num_records',): 'records {old} → {new}',
        ('shape_type',): 'geometry {old} → {new}',
        ('fields', '*'): "field '{1}' {verb}",
        ('crs', 'authority'): 'CRS {old} → {new}',
        # a .prj written by GDAL is ESRI WKT, which names its CRS without an authority
        ('crs', 'name'): 'CRS {old} → {new}',
        ('parts',): 'parts {old} → {new}',
    }
    shapes: ClassVar[dict[int, str]] = {
        0: 'Null', 1: 'Point', 3: 'PolyLine', 5: 'Polygon', 8: 'MultiPoint',
        11: 'PointZ', 13: 'PolyLineZ', 15: 'PolygonZ', 18: 'MultiPointZ',
        21: 'PointM', 23: 'PolyLineM', 25: 'PolygonM', 28: 'MultiPointM', 31: 'MultiPatch',
    }

    def header(self, path: Path) -> Summary:
        with path.open('rb') as file:
            head = exact(file, 100)
        code, words = struct.unpack_from('>i20xi', head, 0)
        if code != 9994:
            raise ValueError('Not a shapefile')
        size = path.stat().st_size
        if words * 2 != size:
            raise ValueError(f'The header says {words * 2} bytes, the file has {size}')
        shape = struct.unpack_from('<i', head, 32)[0]
        bbox = list(struct.unpack_from('<4d', head, 36))
        out = {
            'shape_type': self.shapes.get(shape, str(shape)),
            'bbox': bbox,
        }
        if path.suffix.lower() == '.shx':
            out['num_records'] = (size - 100) // 8
        return out

    def table(self, path: Path) -> Summary:
        with path.open('rb') as file:
            head = exact(file, 32)
            records, header_size, record_size = struct.unpack_from('<IHH', head, 4)
            body = exact(file, header_size - 32)
        if path.stat().st_size < header_size + records * record_size:
            raise ValueError('The table is shorter than its header says')
        fields = {}
        for start in range(0, len(body) - 1, 32):
            descriptor = body[start:start + 32]
            if descriptor[:1] == b'\r':
                break
            name = (
                descriptor[:11]
                .split(b'\x00')[0]
                .decode('ascii', 'replace')
            )
            kind = chr(descriptor[11])
            fields[name] = f'{kind}({descriptor[16]},{descriptor[17]})'
        out = {
            'num_records': records,
            'fields': fields,
        }
        return out

    def projection(self, path: Path) -> Summary:
        text = (
            path
            .read_text(encoding='utf-8')
            .strip()
        )
        name = re.search(r'^\w+\["([^"]*)"', text)
        authority = re.search(r'(?:AUTHORITY|ID)\["(\w+)",\s*"?(\d+)"?\]\]$', text)
        out = {
            'name': None,
            'authority': None,
            'digest': digest(text),
        }
        if name:
            out['name'] = name.group(1)
        if authority:
            out['authority'] = f'{authority.group(1)}:{authority.group(2)}'
        return out

    def compare(
            self,
            old: Summary,
            new: Summary,
    ) -> list[Change]:
        changes = Metadata.compare(self, old, new)
        # the digest of the projection's text only matters when nothing else says the CRS changed
        is_named = any(
            change.path in (('crs', 'name'), ('crs', 'authority'))
            for change in changes
        )
        if is_named:
            changes = [
                change
                for change in changes
                if change.path != ('crs', 'digest')
            ]
        return changes

    def sources(self) -> list[Path]:
        path = self._asset.path
        if path.suffix.lower() != '.shp':
            out = [path]
            return out
        out = [path, *shapefile_parts(path)]
        return out

    def read(self) -> Summary:
        path = self._asset.path
        suffix = path.suffix.lower()
        if suffix == '.shp':
            out = self.header(path)
            parts = {
                part.suffix.lower(): part
                for part in shapefile_parts(path)
            }
            out['parts'] = sorted(parts)
            out['num_records'] = None
            out['fields'] = None
            out['crs'] = None
            out['encoding'] = None
            if '.dbf' in parts:
                out.update(self.table(parts['.dbf']))
            if '.prj' in parts:
                out['crs'] = self.projection(parts['.prj'])
            if '.cpg' in parts:
                out['encoding'] = (
                    parts['.cpg']
                    .read_text(encoding='ascii')
                    .strip()
                )
        elif suffix == '.shx':
            out = self.header(path)
        elif suffix == '.dbf':
            out = self.table(path)
        elif suffix == '.prj':
            out = self.projection(path)
        else:
            text = path.read_text(encoding='ascii')
            out = {'encoding': text.strip()}
        return out


class GeoJSONMetadata(Metadata):
    name: ClassVar[str] = 'geojson'
    suffixes: ClassVar[tuple[str, ...]] = ('.geojson',)
    # the whole file is parsed, so large files keep only their stat
    max_bytes: ClassVar[int | None] = 32 * 2**20
    max_properties: ClassVar[int] = 200
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('num_features',): 'features {old} → {new}',
        ('properties', '*'): "property '{1}' {verb}",
    }

    def read(self) -> Summary:
        text = self._asset.path.read_text(encoding='utf-8')
        document = json.loads(text)
        kind = document.get('type')
        if kind == 'FeatureCollection':
            features = document.get('features', [])
        elif kind == 'Feature':
            features = [document]
        else:
            features = []
        geometries = set()
        properties = {}
        for feature in features:
            geometry = feature.get('geometry')
            if geometry is not None:
                geometries.add(geometry.get('type'))
            for key in feature.get('properties') or ():
                properties[key] = True
        crs = None
        declared = document.get('crs')
        if declared is not None:
            crs = (
                declared
                .get('properties', {})
                .get('name')
            )
        out = {
            'type': kind,
            'num_features': len(features),
            'geometry_types': sorted(geometries),
            'properties': capped(properties, self.max_properties),
            'crs': crs,
            'bbox': document.get('bbox'),
        }
        return out


class FlatGeobufMetadata(Metadata):
    name: ClassVar[str] = 'flatgeobuf'
    suffixes: ClassVar[tuple[str, ...]] = ('.fgb',)
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('num_features',): 'features {old} → {new}',
        ('columns', '*'): "column '{1}' {verb}",
        ('crs',): 'CRS {old} → {new}',
    }
    geometries: ClassVar[tuple[str, ...]] = (
        'Unknown', 'Point', 'LineString', 'Polygon', 'MultiPoint', 'MultiLineString',
        'MultiPolygon', 'GeometryCollection', 'CircularString', 'CompoundCurve', 'CurvePolygon',
        'MultiCurve', 'MultiSurface', 'Curve', 'Surface', 'PolyhedralSurface', 'TIN', 'Triangle',
    )
    kinds: ClassVar[tuple[str, ...]] = (
        'Byte', 'UByte', 'Bool', 'Short', 'UShort', 'Int', 'UInt', 'Long', 'ULong', 'Float',
        'Double', 'String', 'Json', 'DateTime', 'Binary',
    )

    @staticmethod
    def field(
            buffer: bytes,
            table: int,
            index: int,
    ) -> int | None:
        vtable = table - struct.unpack_from('<i', buffer, table)[0]
        size = struct.unpack_from('<H', buffer, vtable)[0]
        slot = 4 + 2 * index
        if slot >= size:
            return None
        offset = struct.unpack_from('<H', buffer, vtable + slot)[0]
        if not offset:
            return None
        out = table + offset
        return out

    @staticmethod
    def follow(
            buffer: bytes,
            position: int,
    ) -> int:
        out = position + struct.unpack_from('<I', buffer, position)[0]
        return out

    def string(
            self,
            buffer: bytes,
            table: int,
            index: int,
    ) -> str | None:
        position = self.field(buffer, table, index)
        if position is None:
            return None
        start = self.follow(buffer, position)
        length = struct.unpack_from('<I', buffer, start)[0]
        out = (
            buffer[start + 4:start + 4 + length]
            .decode('utf-8')
        )
        return out

    def scalar(
            self,
            buffer: bytes,
            table: int,
            index: int,
            code: str,
            default: int,
    ) -> int:
        position = self.field(buffer, table, index)
        if position is None:
            return default
        out = struct.unpack_from('<' + code, buffer, position)[0]
        return out

    def read(self) -> Summary:
        with self._asset.path.open('rb') as file:
            magic = exact(file, 8)
            if (
                magic[:3] != b'fgb'
                or magic[4:7] != b'fgb'
            ):
                raise ValueError('Not a FlatGeobuf file')
            size = struct.unpack('<I', exact(file, 4))[0]
            buffer = exact(file, size)
        header = self.follow(buffer, 0)
        columns = {}
        position = self.field(buffer, header, 7)
        if position is not None:
            start = self.follow(buffer, position)
            count = struct.unpack_from('<I', buffer, start)[0]
            for index in range(count):
                column = self.follow(buffer, start + 4 + 4 * index)
                kind = self.scalar(buffer, column, 1, 'B', 0)
                columns[self.string(buffer, column, 0)] = self.kinds[kind]
        envelope = None
        position = self.field(buffer, header, 1)
        if position is not None:
            start = self.follow(buffer, position)
            count = struct.unpack_from('<I', buffer, start)[0]
            envelope = list(struct.unpack_from(f'<{count}d', buffer, start + 4))
        crs = None
        position = self.field(buffer, header, 10)
        if position is not None:
            table = self.follow(buffer, position)
            crs = f"{self.string(buffer, table, 0)}:{self.scalar(buffer, table, 1, 'i', 0)}"
        geometry = self.scalar(buffer, header, 2, 'B', 0)
        out = {
            'name': self.string(buffer, header, 0),
            'geometry': self.geometries[geometry],
            'num_features': self.scalar(buffer, header, 8, 'Q', 0),
            'columns': capped(columns, 200),
            'crs': crs,
            'envelope': envelope,
        }
        return out


class NetCDFMetadata(Metadata):
    name: ClassVar[str] = 'netcdf'
    suffixes: ClassVar[tuple[str, ...]] = ('.nc', '.nc4')
    max_variables: ClassVar[int] = 500
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('dimensions', '*'): "dimension '{1}' {old} → {new}",
        ('variables', '*'): "variable '{1}' {verb}",
        ('variables', '*', 'dims'): "variable '{1}' dimensions {old} → {new}",
    }

    def read(self) -> Summary:
        import netCDF4

        with netCDF4.Dataset(self._asset.path, mode='r') as dataset:
            dimensions = {
                name: len(dimension)
                for name, dimension in dataset.dimensions.items()
            }
            variables = {
                name: {
                    'dtype': str(variable.dtype),
                    'dims': list(variable.dimensions),
                }
                for name, variable in dataset.variables.items()
            }
            conventions = getattr(dataset, 'Conventions', None)
            model = dataset.data_model
        out = {
            'format': model,
            'conventions': conventions,
            'dimensions': dimensions,
            'variables': capped(variables, self.max_variables),
        }
        return out
