from __future__ import annotations

import copy
import json
import sys
import time
import warnings
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, Self

from trail.node import Node

if TYPE_CHECKING:
    from trail.asset import Asset
    from trail.event import Event, MetadataEvent, WatchdogEvent

type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
type Summary = dict[str, JSONValue]
# aliases, since the `dict` property of Metadata shadows the builtin in its class body
type Phrases = dict[tuple[str, ...], str]
type Registry = dict[str, type[Metadata]]


class Stat(NamedTuple):
    """The size, mtime and inode of a file when its summary was read."""

    st_size: int
    st_mtime_ns: int
    st_ino: int
    st_dev: int

    @classmethod
    def take(cls, paths: Iterable[Path]) -> Self | None:
        """
        Returns the stat of the paths. Sizes are summed and the newest mtime is kept. The inode is
        the first path's. Returns None for no path.
        """
        size = 0
        newest = 0
        inode = None
        device = None
        for path in paths:
            stat = path.stat()
            size += stat.st_size
            newest = max(newest, stat.st_mtime_ns)
            if inode is None:
                inode = stat.st_ino
                device = stat.st_dev
        if inode is None:
            return None
        out = cls(
            st_size=size,
            st_mtime_ns=newest,
            st_ino=inode,
            st_dev=device,
        )
        return out


class Change(NamedTuple):
    """
    One difference between two summaries.

    - `path` is the keys from the top of the summary down to what changed.
    - `verb` is `added`, `removed`, `updated` or `reordered`.
    - `old` and `new` are the values. `added` has no `old`, and `removed` has no `new`.
    - `label` is the display name of the entity the path points into, such as a layer's name.
    """

    path: tuple[str, ...]
    verb: str
    old: JSONValue = None
    new: JSONValue = None
    label: str = ''

    def to_record(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            'path': list(self.path),
            'verb': self.verb,
        }
        if self.verb != 'added':
            out['old'] = self.old
        if self.verb != 'removed':
            out['new'] = self.new
        if self.label:
            out['label'] = self.label
        return out

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Self:
        out = cls(
            path=tuple(record['path']),
            verb=record['verb'],
            old=record.get('old'),
            new=record.get('new'),
            label=record.get('label', ''),
        )
        return out


def canonical(value: JSONValue) -> str:
    out = json.dumps(value, sort_keys=True)
    return out


def diff(
        old: JSONValue,
        new: JSONValue,
        path: tuple[str, ...] = (),
) -> Iterator[Change]:
    """
    Yields the changes from `old` to `new`. Dicts are compared key by key. Anything else is
    compared as JSON text, which tells True from 1 and 1 from 1.0. Two lists with the same members
    in another order are `reordered`. Nothing inside a list is compared.
    """
    if (
        isinstance(old, dict)
        and isinstance(new, dict)
    ):
        for key, value in old.items():
            if key in new:
                yield from diff(value, new[key], (*path, key))
            else:
                yield Change(path=(*path, key), verb='removed', old=value)
        for key, value in new.items():
            if key not in old:
                yield Change(path=(*path, key), verb='added', new=value)
        return
    if canonical(old) == canonical(new):
        return
    verb = 'updated'
    if (
        isinstance(old, list)
        and isinstance(new, list)
        and sorted(map(canonical, old)) == sorted(map(canonical, new))
    ):
        verb = 'reordered'
    yield Change(path=path, verb=verb, old=old, new=new)


def brief(value: JSONValue) -> str:
    out = repr(value)
    if len(out) > 60:
        out = f'{out[:57]}...'
    return out


def intern(value: Any) -> Any:
    if isinstance(value, dict):
        out = {
            sys.intern(key): intern(item)
            for key, item in value.items()
        }
    elif isinstance(value, list):
        out = [
            intern(item)
            for item in value
        ]
    elif isinstance(value, str):
        out = sys.intern(value)
    else:
        out = value
    return out


class Unstable(Exception):
    """The file changed while it was read."""


class Capture(NamedTuple):
    """What a read found, passed from `Metadata.capture` to `Metadata.accept`."""

    stat: Stat | None
    summary: Summary | None
    at: float
    generation: int
    error: str = ''


class Metadata(Node):
    """
    A summary of a tracked file, reached as `asset.metadata`. Assets of a supported format get a
    subclass, chosen by suffix. Every other asset gets `Metadata` itself, which has a stat and an
    empty summary.

    - `stat` is the `Stat` at the last accepted read.
    - `dict` is a copy of the summary.
    - `observe` reads the file if its stat changed, and logs a `MetadataEvent` for a difference.

    A format is a subclass with `suffixes` and `read`. The last definition of a suffix wins.
    """

    name: ClassVar[str] = ''
    suffixes: ClassVar[tuple[str, ...]] = ()
    phrases: ClassVar[Phrases] = {}
    # the top-level collections whose entries are keyed by id and carry a `name`
    named: ClassVar[tuple[str, ...]] = ()
    max_bytes: ClassVar[int | None] = None
    max_changes: ClassVar[int] = 50
    is_available: ClassVar[bool] = True
    # the files below a directory asset that `sources()` reads to list the others
    listed: ClassVar[tuple[str, ...]] = ()
    ext2cls: ClassVar[Registry] = {}
    name2cls: ClassVar[Registry] = {}

    _parent: Asset
    stat: Stat | None = None
    error: str = ''
    is_observed: bool = False
    # counts the captures accepted, so a capture that began before the last one is refused
    generation: int = 0
    _summary: Summary | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        suffixes = cls.__dict__.get('suffixes', ())
        for suffix in suffixes:
            if (
                not suffix.startswith('.')
                or suffix != suffix.lower()
            ):
                raise TypeError(f'Invalid file suffix: {suffix!r}')
        if 'name' not in cls.__dict__:
            cls.name = (
                cls.__name__
                .removesuffix('Metadata')
                .lower()
            )
        # the last definition wins, so a notebook cell can be run again
        for suffix in suffixes:
            Metadata.ext2cls[suffix] = cls
        Metadata.name2cls[cls.name] = cls

    @classmethod
    def descriptor(cls) -> Self:
        out = cls.__new__(cls)
        return out

    def __get__(
            self,
            instance: Asset | None,
            owner: type,
    ) -> Self:
        """
        The asset's Metadata, made on first access. The class follows the asset's current suffix.
        A move to another format, or a format defined since, starts over with no baseline.
        """
        if instance is None:
            return self
        cache = instance.__dict__
        suffix = instance.path.suffix.lower()
        cls = Metadata.ext2cls.get(suffix, Metadata)
        metadata = cache.get(self.__name__)
        if type(metadata) is not cls:
            metadata = cls(instance)
            cache[self.__name__] = metadata
            metadata.restore()
        return metadata

    def __delete__(self, instance: Asset) -> None:
        instance.__dict__.pop(self.__name__, None)

    def read(self) -> Summary:
        """Returns the summary of the file. A format overrides it."""
        return {}

    def restore(self) -> None:
        """
        Take the baseline the last run kept for this asset, if it was read by this format. The
        next observation then compares with it, so a change made while Trail was not running is
        logged.
        """
        record = self._trail.baselines.get(self._asset.id)
        if (
            record is None
            or record.get('format') != self.name
        ):
            return
        stat = record.get('stat')
        if stat is not None:
            self.stat = Stat(*stat)
        summary = record.get('summary')
        if summary is not None:
            self._summary = intern(summary)
        self.is_observed = True

    def listing(self) -> list[Path]:
        """The files `sources()` may read, so a stat check of the asset opens them."""
        root = self._asset.path
        out = [
            root / name
            for name in self.listed
        ]
        return out

    def sources(self) -> list[Path]:
        """The files whose stat decides whether the summary may have changed."""
        asset = self._asset
        # a directory's own stat does not change when a file inside it is rewritten
        if asset.is_directory:
            return []
        out = [asset.path]
        return out

    def label(
            self,
            path: tuple[str, ...],
            old: Summary,
            new: Summary,
    ) -> str:
        """The `name` of the entity a change points into, for a collection named in `named`."""
        if (
            len(path) < 2
            or path[0] not in self.named
        ):
            return ''
        for summary in (new, old):
            entry = (
                summary
                .get(path[0], {})
                .get(path[1])
            )
            if isinstance(entry, dict):
                name = entry.get('name')
                if isinstance(name, str):
                    return name
        return ''

    def compare(
            self,
            old: Summary,
            new: Summary,
    ) -> list[Change]:
        out = [
            change._replace(label=self.label(change.path, old, new))
            for change in diff(old, new)
        ]
        return out

    @classmethod
    def describe(cls, change: Change) -> str:
        """Returns a change as one line of text, from a phrase of the format or a generic line."""
        for pattern, phrase in cls.phrases.items():
            is_match = (
                len(pattern) == len(change.path)
                and all(
                    part in ('*', key)
                    for part, key in zip(pattern, change.path, strict=True)
                )
            )
            if is_match:
                out = phrase.format(
                    *change.path,
                    verb=change.verb,
                    old=change.old,
                    new=change.new,
                    label=change.label,
                )
                return out
        where = '.'.join(change.path)
        if change.label:
            where = f'{where} ({change.label})'
        if change.verb == 'updated':
            out = f'{where}: {brief(change.old)} → {brief(change.new)}'
        else:
            out = f'{where} {change.verb}'
        return out

    def is_stale(self) -> bool:
        """
        Whether the file needs reading: before the first observation, or when its stat changed. A
        missing file is not stale. An asset with nothing to stat is never stale after its baseline.
        """
        if not self.is_observed:
            return True
        try:
            stat = Stat.take(self.sources())
        except OSError:
            return False
        is_changed = (
            stat is not None
            and stat != self.stat
        )
        return is_changed

    def capture(self) -> Capture:
        """
        Read the file's summary between two stats. Touches only the filesystem, so it may run in a
        worker thread. Raises Unstable if the stats differ, and OSError if the file is gone. A
        reader's exception is kept as `error`, with no summary.
        """
        generation = self.generation
        before = Stat.take(self.sources())
        summary = None
        error = ''
        is_readable = (
            type(self).is_available
            and (
                self.max_bytes is None
                or before is None
                or before.st_size <= self.max_bytes
            )
        )
        if is_readable:
            try:
                summary = self.read()
            except ImportError as missing:
                type(self).is_available = False
                warnings.warn(f'{self.name} metadata is not read: {missing}', stacklevel=2)
            # a reader fails in its own ways on a damaged file
            except Exception as failure:  # noqa: BLE001
                error = repr(failure)
        after = Stat.take(self.sources())
        if before != after:
            raise Unstable(str(self._asset.path))
        out = Capture(
            stat=after,
            summary=summary,
            at=time.time(),
            generation=generation,
            error=error,
        )
        return out

    def accept(
            self,
            capture: Capture,
            basis: str,
            write: WatchdogEvent | None = None,
    ) -> Event | None:
        """
        Compare a capture with the last summary, keep it, and log a MetadataEvent for a difference.
        Runs on the thread that owns the Trail. With `write`, the folded record of the write the
        read follows, the changes go into that record and it is logged, changes or not.

        - A capture for an asset untracked, moved to another format, or observed since is refused.
        - The first observation is the baseline and logs nothing.
        - A capture with changes is logged. One without is logged only if the stat changed and no
          watcher runs in this process, since a watcher's events already say the file changed.
        """
        from trail.event import MetadataEvent

        asset = self._asset
        trail = self._trail
        if (
            asset.metadata is not self
            or trail.assets.id2entry.get(asset.id) is not asset
            or capture.generation != self.generation
        ):
            return None
        was_observed = self.is_observed
        previous = self.stat
        old = self._summary
        new = capture.summary
        changes: list[Change] = []
        if (
            old is not None
            and new is not None
        ):
            changes = self.compare(old, new)
        self.generation += 1
        self.is_observed = True
        self.stat = capture.stat
        self.error = capture.error
        if new is not None:
            self._summary = intern(new)
        elif not capture.error:
            # too large, or no reader: nothing to compare the next read with
            self._summary = None
        trail.baselines.mark(self)
        if write is not None:
            kept = changes[:self.max_changes]
            write.format = self.name
            write.changes = kept
            write.more = len(changes) - len(kept)
            write.apply(trail)
            return write
        if not was_observed:
            return None
        # a watcher's events already say the file changed, except for a change made before it ran
        is_watched = (
            trail.watchdog.session.is_active
            and basis != 'startup'
        )
        if (
            not changes
            and (
                capture.stat is None
                or capture.stat == previous
                or is_watched
            )
        ):
            return None
        kept = changes[:self.max_changes]
        size = None
        mtime = None
        if capture.stat is not None:
            size = capture.stat.st_size
            mtime = capture.stat.st_mtime_ns
        event = MetadataEvent(
            src_path=str(asset.path),
            format=self.name,
            basis=basis,
            st_size=size,
            st_mtime_ns=mtime,
            changes=kept,
            more=len(changes) - len(kept),
            timestamp=datetime.fromtimestamp(capture.at, UTC),
        )
        event.entry = asset
        event.apply(trail)
        return event

    def observe(
            self,
            basis: str = 'manual',
            *,
            is_forced: bool = False,
    ) -> MetadataEvent | None:
        """Read the file if it is stale, or if `is_forced`, and log what changed."""
        if (
            not is_forced
            and not self.is_stale()
        ):
            return None
        try:
            capture = self.capture()
        except (OSError, Unstable):
            return None
        out = self.accept(capture, basis)
        return out

    @property
    def dict(self) -> Summary:
        """A copy of the summary. A file never observed is observed first, which logs nothing."""
        if not self.is_observed:
            self.observe('access')
        out = copy.deepcopy(self._summary)
        if out is None:
            out = {}
        return out


# the readers register their suffixes when imported
from trail.metadata import general, geospatial

__all__ = [
    'Capture',
    'Change',
    'JSONValue',
    'Metadata',
    'Stat',
    'Summary',
    'Unstable',
    'canonical',
    'diff',
    'general',
    'geospatial',
]
