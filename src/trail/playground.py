from __future__ import annotations

import json
import os
import shutil
import tempfile
import weakref
from collections.abc import Iterable, Iterator
from functools import cached_property
from itertools import chain, count
from pathlib import Path
from typing import Self

from trail import fmt
from trail.asset import DirAsset


class Node:
    """
    A member of a playground tree. A node holds only its parent and its own filename. The path comes
    from the chain of parents, so a moved ancestor repaths everything beneath it.
    """

    suffix: str = ''

    def __init__(
        self,
        parent: Dir | None = None,
        stem: str = '',
        suffix: str | None = None,
    ) -> None:
        self._parent = parent
        self.stem = stem
        if suffix is not None:
            self.suffix = suffix

    @property
    def _playground(self) -> Playground:
        parent = self._parent
        if isinstance(parent, Playground):
            return parent
        return parent._playground

    @cached_property
    def path(self) -> Path:
        return self._parent.path / f'{self.stem}{self.suffix}'

    @property
    def filename(self) -> str:
        """The current name on disk, which a move may have changed."""
        return self.path.name

    @property
    def exists(self) -> bool:
        return self.path.exists()

    @cached_property
    def _numbers(self) -> Iterator[int]:
        """generated payloads count up, so repeated appends stay distinguishable"""
        return count(1)

    @cached_property
    def _adhoc(self) -> list[Node]:
        """members addressed by filename, not declared as attributes"""
        return []

    def children(self) -> Iterator[Node]:
        """
        The members that have been reached for. The rest derive their path from this node when first
        accessed. Only these members can hold a stale path.
        """
        adhoc = self._adhoc
        values = chain(tuple(self.__dict__.values()), adhoc)
        for value in values:
            if (
                isinstance(value, Node)
                and value._parent is self
            ):
                yield value

    def create(self) -> Self:
        raise NotImplementedError

    def remove(self) -> Self:
        raise NotImplementedError

    def move(self, destination: str | Path | Node | None = None) -> Self:
        """
        Rename the resource and follow it, so this node and the members beneath it point at the
        moved tree. Without a destination, the node takes its next numbered sibling name. A
        directory destination keeps the current filename.
        """
        self.create()
        previous = self.path
        current = self._destination(destination)
        current.parent.mkdir(parents=True, exist_ok=True)
        previous.rename(current)
        self.path = current
        for child in self.children():
            child._repath(previous, current)
        return self

    def _destination(self, destination: str | Path | Node | None) -> Path:
        if destination is None:
            return self._numbered()
        if isinstance(destination, Dir):
            destination.create()
            return destination.path / self.filename
        if isinstance(destination, Node):
            return destination.path
        out = Path(destination).expanduser()
        if not out.is_absolute():
            out = self.path.parent / out
        if out.is_dir():
            out /= self.filename
        return out

    def _numbered(self) -> Path:
        """the next free `<stem>-<number>` sibling"""
        number = 1
        while True:
            candidate = self.path.with_name(f'{self.stem}-{number}{self.suffix}')
            if not candidate.exists():
                return candidate
            number += 1

    def _repath(
        self,
        previous: Path,
        current: Path,
    ) -> None:
        """follow an ancestor that moved, keeping any rename this node has"""
        if not self.path.is_relative_to(previous):
            return
        self.path = current / self.path.relative_to(previous)
        for child in self.children():
            child._repath(previous, current)

    def __fspath__(self) -> str:
        return str(self.path)

    def __str__(self) -> str:
        return str(self.path)

    def __repr__(self) -> str:
        return repr(str(self.path))


class Asset(Node):
    """A file in the playground tree."""

    @property
    def content(self) -> str:
        """the payload written when a missing asset is created"""
        return f'{self._line()}\n'

    def _line(self) -> str:
        return f'line {next(self._numbers)}'

    def create(self, content: str | None = None) -> Self:
        """Write the default payload unless the asset is already there."""
        if self.exists:
            return self
        if content is None:
            content = self.content
        return self.write(content)

    def write(self, content: str) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(content, encoding='utf-8')
        return self

    def open(self) -> str:
        """
        Read the asset and return its text. Reading produces an `opened` event. A missing asset is
        created first, so a test can go straight to the event it wants.
        """
        self.create()
        return self.path.read_text(encoding='utf-8')

    def append(self, *lines: str) -> Self:
        """Append lines, defaulting to one generated line."""
        self.create()
        if not lines:
            lines = (self._line(),)
        with self.path.open('a', encoding='utf-8') as stream:
            for line in lines:
                stream.write(f'{line}\n')
        return self

    def remove(self) -> Self:
        self.path.unlink(missing_ok=True)
        return self

class CSV(Asset):
    suffix = '.csv'
    header = 'name,value'

    @property
    def content(self) -> str:
        return f'{self.header}\n{self.row()}\n'

    def row(self) -> str:
        number = next(self._numbers)
        return f'row{number},{number}'

    def append(self, *rows: str | Iterable[object]) -> Self:
        """Append rows, defaulting to one generated row. A non-string row joins on commas."""
        self.create()
        if not rows:
            rows = (self.row(),)
        rendered = [
            self._render(row)
            for row in rows
        ]
        return super().append(*rendered)

    @staticmethod
    def _render(row: str | Iterable[object]) -> str:
        if isinstance(row, str):
            return row
        return ','.join(
            str(value)
            for value in row
        )


class GeoJSON(Asset):
    suffix = '.geojson'

    @property
    def content(self) -> str:
        return self._dump([self.feature()])

    def feature(self, **properties) -> dict:
        """a point feature whose coordinates and name count up with each call"""
        number = next(self._numbers)
        geometry = {
            'type': 'Point',
            'coordinates': [float(number), float(number)],
        }
        named = {'name': f'point{number}'}
        out = {
            'type': 'Feature',
            'geometry': geometry,
            'properties': named | properties,
        }
        return out

    def features(self) -> list[dict]:
        """The features on disk. Reading them produces an `opened` event."""
        return json.loads(self.open())['features']

    def append(self, *features: dict) -> Self:
        """
        Rewrite the collection with `features` added, defaulting to one generated point. Appending
        reads and then writes the file, so the asset is opened before it is modified.
        """
        collection = self.features()
        if not features:
            features = (self.feature(),)
        collection.extend(features)
        return self.write(self._dump(collection))

    @staticmethod
    def _dump(features: list[dict]) -> str:
        collection = {
            'type': 'FeatureCollection',
            'features': features,
        }
        return f'{json.dumps(collection, indent=2)}\n'


class Store(Node):
    """
    A dirasset in the playground tree: a directory written as one dataset. `files` are the paths
    inside it, relative to it, that `create` writes.
    """

    files: tuple[str, ...] = ()

    def create(self) -> Self:
        """Write every file of the store unless the store is already there."""
        if self.exists:
            return self
        return self.write()

    def read(self, *names: str) -> Self:
        """
        Read files inside the store, all of them by default. Reading produces `opened` events. A
        missing store is created first.
        """
        self.create()
        for name in names or self.files:
            (self.path / name).read_bytes()
        return self

    def write(self, *names: str) -> Self:
        """
        Write files inside the store, all of them by default. Writing produces `created` and
        `modified` events.
        """
        for name in names or self.files:
            path = self.path / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f'{name}\n'.encode())
        return self

    def rewrite(self) -> Self:
        """Remove the store and write it again, as `to_zarr(mode='w')` does."""
        self.remove()
        return self.write()

    def remove(self) -> Self:
        shutil.rmtree(self.path, ignore_errors=True)
        return self


class ZarrV3(Store):
    """A zarr v3 store with one array of one chunk."""

    suffix = '.zarr'
    files = ('zarr.json', 'arr/zarr.json', 'arr/c/0/0')


class ZarrV2(Store):
    """A zarr v2 store with consolidated metadata and one array of one chunk."""

    suffix = '.zarr'
    files = ('.zgroup', '.zmetadata', 'arr/.zarray', 'arr/0.0')


class GDB(Store):
    """A flat file geodatabase with its system tables and one layer."""

    suffix = '.gdb'
    files = (
        'gdb',
        'timestamps',
        'a00000001.gdbtable',
        'a00000001.gdbtablx',
        'a00000009.gdbtable',
        'a00000009.gdbtablx',
    )


class SAFE(Store):
    """A Sentinel-2 product with its manifest and two bands of one granule."""

    suffix = '.SAFE'
    files = (
        'manifest.safe',
        'MTD_MSIL2A.xml',
        'GRANULE/L2A_T33UUP/IMG_DATA/R10m/T33UUP_B04_10m.jp2',
        'GRANULE/L2A_T33UUP/IMG_DATA/R10m/T33UUP_B08_10m.jp2',
    )


class Dir(Node):
    """A directory in the playground tree, holding the same members as the playground itself."""

    @cached_property
    def csv(self) -> CSV:
        return CSV(self, 'csv')

    @cached_property
    def geojson(self) -> GeoJSON:
        return GeoJSON(self, 'geojson')

    @cached_property
    def dir(self) -> Dir:
        return Dir(self, 'dir')

    @cached_property
    def zarr(self) -> ZarrV3:
        return ZarrV3(self, 'zarr')

    @cached_property
    def zarr2(self) -> ZarrV2:
        return ZarrV2(self, 'zarr2')

    @cached_property
    def gdb(self) -> GDB:
        return GDB(self, 'gdb')

    @cached_property
    def safe(self) -> SAFE:
        return SAFE(self, 'safe')

    def asset(self, filename: str) -> Asset:
        return self._child(filename, self._asset_type(filename))

    def subdir(self, filename: str) -> Dir:
        return self._child(filename, Dir)

    def _child[N: Node](
        self,
        filename: str,
        node_type: type[N],
    ) -> N:
        """
        An ad-hoc member addressed by filename, not declared as an attribute. A member that already
        points at that path wins, so a declared `csv` and an `asset('csv.csv')` are the same node.
        """
        path = self.path / filename
        for child in self.children():
            if (
                child.path == path
                and isinstance(child, node_type)
            ):
                return child
        name = Path(filename)
        node = node_type(self, name.stem, name.suffix)
        self._adhoc.append(node)
        return node

    @staticmethod
    def _asset_type(filename: str) -> type[Asset]:
        types = {
            CSV.suffix: CSV,
            GeoJSON.suffix: GeoJSON,
        }
        return types.get(Path(filename).suffix, Asset)

    def create(self) -> Self:
        self.path.mkdir(parents=True, exist_ok=True)
        return self

    def remove(self) -> Self:
        shutil.rmtree(self.path, ignore_errors=True)
        return self


class Playground(Dir):
    """
    A disposable filesystem tree for exercising a Trail. A test does not need to create its own
    temporary directories, files, appends and renames. The playground is the root directory, so it
    has the same members as any directory beneath it.

    Members are lazy. A node is only a name and a parent until something is created, opened,
    appended, moved or removed. A test names the resource it wants, then triggers only the events it
    needs.

        playground = Playground()
        trail = Trail(playground.path)
        playground.csv.create()
        trail.track(playground.csv)
        playground.csv.open()
        playground.dir.geojson.append()
        playground.dir.move()

    `playground.csv` is `<root>/csv.csv`. `playground.dir.geojson` is `<root>/dir/geojson.geojson`.
    `dir` nests without limit. Nodes are os.PathLike, so one can be passed to anything that takes a
    path. A playground that made its own directory removes it when it is collected or when the
    process exits. A playground given a directory leaves it alone until asked to remove it.

    >>> playground: Playground
    >>> playground
    Playground
        path: '/dev/shm/playground-_6oh8mig'
        tree: [
            'csv.csv',
            'dir/',
            'dir/geojson.geojson',
        ]
    """

    # paths listed by __repr__ before the remainder is summarized
    repr_limit = 20

    def __init__(
        self,
        dir: str | Path | None = None,
        cleanup: bool | None = None,
    ) -> None:
        if cleanup is None:
            # a directory handed to the playground is not the playground's to delete unasked
            cleanup = dir is None
        if dir is None:
            shared = Path('/dev/shm')
            if os.access(shared, os.W_OK):
                tempdir = shared
            else:
                tempdir = Path(tempfile.gettempdir())
            dir = tempfile.mkdtemp(dir=tempdir)
        path = Path(dir).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        super().__init__(stem=path.name)
        self.path = path
        self._cleanup = cleanup
        self._finalizer = None
        self._arm()

    @property
    def _playground(self) -> Playground:
        return self

    def __repr__(self) -> str:
        lines = [
            type(self).__name__,
            f'    path: {str(self.path)!r}',
        ]
        lines.extend(self._tree())
        return '\n'.join(lines)

    def __str__(self) -> str:
        return repr(self)

    def _tree(self) -> list[str]:
        """
        The tree as it is on disk, which can differ from the members that exist as nodes. A dirasset
        is one line with its file count, as a Trail tracks it.
        """
        if not self.exists:
            return ['    tree: <missing>']
        entries = []
        for directory, dirnames, filenames in os.walk(self.path):
            parent = Path(directory)
            walked = []
            for name in dirnames:
                path = parent / name
                relative = path.relative_to(self.path)
                if DirAsset.is_match(path):
                    count = sum(
                        len(files)
                        for _, _, files in os.walk(path)
                    )
                    entries.append((path, f'{relative}/ ({fmt.plural(count, "file")})'))
                else:
                    entries.append((path, f'{relative}/'))
                    walked.append(name)
            dirnames[:] = walked
            for name in filenames:
                path = parent / name
                entries.append((path, str(path.relative_to(self.path))))
        if not entries:
            return ['    tree: []']
        entries.sort()
        lines = ['    tree: [']
        for _, relative in entries[:self.repr_limit]:
            lines.append(f'        {relative!r},')
        hidden = len(entries) - self.repr_limit
        if hidden > 0:
            lines.append(f'        <{hidden} others>')
        lines.append('    ]')
        return lines

    def _arm(self) -> None:
        """aim the sweep at the current path, so a playground that moves still cleans up"""
        if self._finalizer is not None:
            self._finalizer.detach()
        if self._cleanup:
            self._finalizer = weakref.finalize(self, shutil.rmtree, self.path, True)

    def move(self, destination: str | Path | Node | None = None) -> Self:
        super().move(destination)
        self._arm()
        return self

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exception) -> None:
        self.remove()


if __name__ == '__main__':
    with Playground() as playground:
        print(f'playground: {playground.path}')

        # a member is only a path until it is asked to exist
        print(f'csv:        {playground.csv.path} (exists={playground.csv.exists})')
        playground.csv.create()
        playground.csv.append()
        playground.csv.append('explicit,7')
        print(playground.csv.open(), end='')

        # appending to a GeoJSON rewrites the collection rather than the file's last line
        playground.geojson.append()
        names = [
            feature['properties']['name']
            for feature in playground.geojson.features()
        ]
        print(f'features:   {names}')

        # members nest, and a directory which moves takes the members beneath it along
        playground.dir.csv.open()
        playground.dir.geojson.create()
        playground.dir.move()
        print(f'moved dir:  {playground.dir.path}')
        print(f'moved csv:  {playground.dir.csv.path}')

        # a directory as the destination means "move into", and it is made on demand
        playground.csv.move(playground.subdir('archive'))
        print(f'moved into: {playground.csv.path}')
        print(playground)

    print(f'swept up:   {not playground.exists}')
