from __future__ import annotations

import os
from pathlib import Path
from typing import Final
from uuid import UUID

# anything a path can be made from, including any object implementing __fspath__
PathLike = str | os.PathLike[str] | Path
# the names of the files that are part of a dirasset, by the dirasset's suffix. A name is matched
# whole, ignoring case. Anything else inside, such as a temporary or lock file, is not part of it
CHILDREN: Final[dict[str, str]] = {
    '.zarr': r'\d+(?:\.\d+)*|zarr\.json|\.z(?:array|attrs|group|metadata)',
    '.n5': r'\d+|attributes\.json',
    '.gdb': (
        r'a[0-9a-f]{8}(?:\.(?!_compress\.)[^.]+)?'
        r'\.(?:gdbtable|gdbtablx|gdbindexes|spx|atx|freelist|horizon)'
        r'|gdb|timestamps'
    ),
    '.safe': r'.+(?<!\.aux)\.(?:jp2|xml|safe|tiff?|gml|html?|xsd|png|jpe?g|kml|json)',
}
# a directory tracked with `track DIR` holds everything in it
EVERYTHING: Final = r'.*'
# the event types an access counts, in the order a repr lists them
OPS: Final = ('opened', 'created', 'modified', 'moved', 'deleted')


def normalize_id(identifier: str | int) -> str:
    if isinstance(identifier, int):
        return UUID(int=identifier).hex
    return UUID(hex=identifier).hex
