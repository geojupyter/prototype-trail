from __future__ import annotations

import ast
import csv
import io
import struct
import zipfile
from contextlib import suppress
from typing import BinaryIO, ClassVar

from trail.metadata import JSONValue, Metadata, Summary
from trail.metadata.geospatial import capped, exact


class DelimitedMetadata(Metadata):
    name: ClassVar[str] = 'delimited'
    suffixes: ClassVar[tuple[str, ...]] = ('.csv', '.tsv')
    # only the start of the file is read
    head: ClassVar[int] = 64 * 2**10

    def read(self) -> Summary:
        with self._asset.path.open('rb') as file:
            raw = file.read(self.head)
        text = raw.decode('utf-8-sig', 'replace')
        first = text.split('\n', 1)[0]
        delimiter = '\t'
        if self._asset.path.suffix.lower() == '.csv':
            delimiter = ','
            # a header of one column has no delimiter to find
            with suppress(csv.Error):
                delimiter = (
                    csv.Sniffer()
                    .sniff(first, delimiters=',;|\t')
                    .delimiter
                )
        columns = next(csv.reader(io.StringIO(first), delimiter=delimiter))
        out = {
            'delimiter': delimiter,
            'columns': columns,
        }
        return out


class NumpyMetadata(Metadata):
    name: ClassVar[str] = 'numpy'
    suffixes: ClassVar[tuple[str, ...]] = ('.npy', '.npz')
    max_arrays: ClassVar[int] = 500

    @staticmethod
    def header(file: BinaryIO) -> dict[str, JSONValue]:
        magic = exact(file, 8)
        if magic[:6] != b'\x93NUMPY':
            raise ValueError('Not a npy file')
        # version 1 holds the header length in two bytes, later versions in four
        if magic[6] == 1:
            size = struct.unpack('<H', exact(file, 2))[0]
        else:
            size = struct.unpack('<I', exact(file, 4))[0]
        text = (
            exact(file, size)
            .decode('latin1')
        )
        header = ast.literal_eval(text)
        out = {
            'shape': list(header['shape']),
            'dtype': str(header['descr']),
            'is_fortran': header['fortran_order'],
        }
        return out

    def read(self) -> Summary:
        path = self._asset.path
        if path.suffix.lower() == '.npy':
            with path.open('rb') as file:
                out = self.header(file)
            return out
        arrays = {}
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                name = info.filename.removesuffix('.npy')
                with archive.open(info) as member:
                    arrays[name] = self.header(member)
        out = {'arrays': capped(arrays, self.max_arrays)}
        return out


class HDF5Metadata(Metadata):
    name: ClassVar[str] = 'hdf5'
    suffixes: ClassVar[tuple[str, ...]] = ('.h5', '.hdf5')
    max_datasets: ClassVar[int] = 500
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('datasets', '*'): "dataset '{1}' {verb}",
        ('datasets', '*', 'shape'): "dataset '{1}' shape {old} → {new}",
    }

    def read(self) -> Summary:
        import h5py

        datasets = {}

        def visit(
                name: str,
                node: object,
        ) -> None:
            if isinstance(node, h5py.Dataset):
                datasets[name] = {
                    'shape': list(node.shape),
                    'dtype': str(node.dtype),
                }

        with h5py.File(self._asset.path, 'r') as file:
            file.visititems(visit)
        out = {'datasets': capped(datasets, self.max_datasets)}
        return out
