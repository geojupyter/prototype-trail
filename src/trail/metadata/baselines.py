from __future__ import annotations

import asyncio
import json
import os
import warnings
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import TYPE_CHECKING, Any

from trail.node import Node

if TYPE_CHECKING:
    from trail.metadata import Metadata
    from trail.trail import Trail


class Baselines(Node):
    """
    The last accepted stat and summary of each asset, kept in `.trail/metadata.json` so the next
    run can tell what changed while Trail was not running. Reached as `trail.baselines`.

    - `data` maps each asset id to its format, stat and summary.
    - A change is written after `delay` seconds when an event loop runs, at once otherwise, and
      when the watcher's run closes.

    A Trail without a directory keeps nothing.
    """

    _parent: Trail
    version = 1
    # seconds a change waits before it is written, so a burst of reads is one write
    delay = 1.0

    def __init__(self, parent: Trail) -> None:
        super().__init__(parent)
        self.data: dict[str, dict[str, Any]] = {}
        self.is_loaded = False
        self.is_dirty = False
        self.depth = 0
        self.timer: asyncio.TimerHandle | None = None

    @property
    def path(self) -> Path | None:
        directory = self._parent.dir
        if directory is None:
            return None
        out = directory / 'metadata.json'
        return out

    def load(self) -> None:
        """Read the file once. A file that cannot be read is ignored with a warning."""
        if self.is_loaded:
            return
        self.is_loaded = True
        path = self.path
        if (
            path is None
            or not path.exists()
        ):
            return
        try:
            document = json.loads(path.read_text(encoding='utf-8'))
            assets = document['assets']
            if not isinstance(assets, dict):
                raise TypeError('Expected a mapping of assets')
        except (OSError, ValueError, KeyError, TypeError) as error:
            warnings.warn(f'{path} is ignored: {error}', stacklevel=2)
            return
        self.data = assets

    def get(self, identifier: str) -> dict[str, Any] | None:
        self.load()
        out = self.data.get(identifier)
        return out

    def mark(self, metadata: Metadata) -> None:
        """Keep a Metadata's accepted state, to be written soon."""
        self.load()
        stat = metadata.stat
        if stat is not None:
            stat = list(stat)
        self.data[metadata._asset.id] = {
            'format': metadata.name,
            'stat': stat,
            'summary': metadata._summary,
        }
        self.changed()

    def drop(self, identifier: str) -> None:
        """Forget an asset that was untracked."""
        self.load()
        if self.data.pop(identifier, None) is not None:
            self.changed()

    def changed(self) -> None:
        self.is_dirty = True
        if self.depth:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.save()
            return
        if self.timer is None:
            self.timer = loop.call_later(self.delay, self.save)

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Write once for every change made inside the block."""
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1
            if (
                not self.depth
                and self.is_dirty
            ):
                self.save()

    def save(self) -> None:
        """Write the file if anything changed. The write replaces the file whole."""
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        path = self.path
        if (
            not self.is_dirty
            or path is None
            or not path.parent.exists()
        ):
            return
        document = {
            'version': self.version,
            'assets': self.data,
        }
        with NamedTemporaryFile(
            'w',
            encoding='utf-8',
            dir=path.parent,
            prefix='.metadata.',
            delete=False,
        ) as handle:
            json.dump(document, handle)
        try:
            os.replace(handle.name, path)
        except OSError:
            with suppress(OSError):
                os.unlink(handle.name)
            raise
        self.is_dirty = False
