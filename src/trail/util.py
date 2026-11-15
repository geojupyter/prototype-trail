from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

# anything a path can be made from, including any object implementing __fspath__
PathLike = str | os.PathLike[str] | Path


def normalize_id(identifier: str | int) -> str:
    if isinstance(identifier, int):
        return UUID(int=identifier).hex
    return UUID(hex=identifier).hex
