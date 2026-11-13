from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar

from trail.event import Event
from trail.jupyter.source import Source
from trail.jupyter.symbology import SymbologyDiff

if TYPE_CHECKING:
    from trail.asset import Asset
    from trail.trail import Trail


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
    else:
        out = value
    return out


@dataclass(kw_only=True, slots=True, repr=False)
class JupyterEvent(Event):
    registry: ClassVar[dict[str, type[JupyterEvent]]] = {}
    schema: ClassVar[str] = ''
    is_shared: ClassVar[bool] = True
    selectable: ClassVar[tuple[str, ...]] = ('verb',)

    schema_id: str
    data: dict[str, Any] = field(repr=False)
    server: str = ''
    src_path: str = ''
    entry: Asset | None = field(default=None, init=False, repr=False)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        if cls.schema:
            known = cls.registry.get(cls.schema)
            if known is not None and not (
                known.__module__ == cls.__module__
                and (
                    known.__qualname__ == cls.__qualname__
                    or (
                        '__slots__' in cls.__dict__
                        and '__dataclass_fields__' in cls.__dict__
                        and cls.__dict__['__dataclass_fields__']
                        is known.__dict__.get('__dataclass_fields__')
                    )
                )
            ):
                raise TypeError(f'{cls.schema} is already registered by {known.__name__}')
        super(JupyterEvent, cls).__init_subclass__(**kwargs)
        if cls.schema:
            cls.registry[cls.schema] = cls

    def __post_init__(self) -> None:
        Event.__post_init__(self)
        self.data = intern(self.data)

    @property
    def verb(self) -> str:
        out = self.data.get('verb', '')
        return out

    # trail: origin - None means the message carries no locality, not that it is remote.
    @property
    def is_local(self) -> bool | None:
        out = self.data.get('local')
        if type(out) is not bool:
            return None
        return out

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield from Event._repr_items(self)
        if self.verb:
            yield 'verb', self.verb
        if self.is_local is not None:
            yield 'local', self.is_local

    def apply(
            self,
            trail: Trail,
            *,
            replay: bool = False,
    ) -> Asset | None:
        if not replay:
            trail.events.append(self)
        return self.entry


@dataclass(kw_only=True, slots=True, repr=False)
class PageEvent(JupyterEvent):
    # trail: mode - one browser page numbers its focus and mode records with one counter.
    @property
    def frontend_id(self) -> str | None:
        out = self.data.get('frontend_id')
        return out

    @property
    def focus_seq(self) -> int | None:
        out = self.data.get('focus_seq')
        return out


@dataclass(kw_only=True, slots=True, repr=False)
class DocumentEvent(PageEvent):
    # trail: focus - each view's lifecycle and focus transitions are independent.
    is_shared: ClassVar[bool] = False
    selectable: ClassVar[tuple[str, ...]] = (
        'verb', 'is_focus', 'tool', 'frontend_id', 'focus_seq', 'widget_id', 'reason',
    )

    @property
    def is_focus(self) -> bool:
        out = self.verb in ('activated', 'deactivated')
        return out

    @property
    def tool(self) -> str:
        return 'JupyterGIS'

    @property
    def widget_id(self) -> str | None:
        out = (
            self.data
            .get('object', {})
            .get('widget_id')
        )
        return out

    @property
    def reason(self) -> str | None:
        out = (
            self.data
            .get('object', {})
            .get('reason')
        )
        return out

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield from JupyterEvent._repr_items(self)
        if self.is_focus:
            yield 'tool', self.tool
            for name in ('frontend_id', 'focus_seq', 'widget_id', 'reason'):
                value = getattr(self, name)
                if value is not None:
                    yield name, value


@dataclass(kw_only=True, slots=True, repr=False)
class DocumentV1Event(DocumentEvent):
    schema: ClassVar[str] = 'https://schema.jupytergis.org/events/document/v1'


@dataclass(kw_only=True, slots=True, repr=False)
class LayerEvent(JupyterEvent):
    selectable: ClassVar[tuple[str, ...]] = (
        'verb', 'layer_id', 'layer', 'is_local', 'source', 'has_filter_change',
    )

    # trail: resources - stored, since the server that resolved the path may be gone on replay.
    resource_id: str | None = field(default=None, repr=False)
    resource_path: str = field(default='', repr=False)

    @property
    def layer_id(self) -> str | None:
        out = (
            self.data
            .get('object', {})
            .get('layer_id')
        )
        return out

    @property
    def layer(self) -> str | None:
        obj = self.data.get('object', {})
        if self.verb == 'removed':
            value = obj.get('old', {})
        else:
            value = obj.get('new', {})
        out = value.get('name')
        return out

    @property
    def source(self) -> Source | None:
        value = (
            self.data
            .get('object', {})
            .get('source')
        )
        out = Source.from_payload(value)
        return out

    # trail: filters - an update's filters are described from the layer before and after.
    @property
    def symbology(self) -> SymbologyDiff | None:
        if self.verb != 'updated':
            return None
        obj = self.data.get('object', {})
        old = (
            obj
            .get('old', {})
            .get('parameters', {})
            .get('symbologyState')
        )
        new = (
            obj
            .get('new', {})
            .get('parameters', {})
            .get('symbologyState')
        )
        if (
            old is None
            and new is None
        ):
            return None
        out = SymbologyDiff(old, new)
        return out

    # trail: no-op - an update's other fields are described from the layer before and after.
    @property
    def changes(self) -> list[str]:
        if self.verb != 'updated':
            return []
        obj = self.data.get('object', {})
        old = obj.get('old', {})
        new = obj.get('new', {})
        out = field_changes(old, new, skip=('parameters',))
        out += field_changes(
            old.get('parameters', {}),
            new.get('parameters', {}),
            skip=('symbologyState', 'source'),
        )
        return out

    @property
    def has_filter_change(self) -> bool:
        diff = self.symbology
        has_change = (
            diff is not None
            and diff.has_filter_change
        )
        return has_change

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield from JupyterEvent._repr_items(self)
        if self.layer_id is not None:
            yield 'layer_id', self.layer_id
        if self.layer is not None:
            yield 'layer', self.layer
        # a slots dataclass has no cached_property, so the comparison is built once here
        diff = self.symbology
        if diff is not None:
            for change in (*diff.predicates, *diff.combinators):
                yield change.label, change.text
            if diff.has_other_changes:
                yield 'symbology', 'other changes'
        for change in self.changes:
            yield 'change', change
        source = self.source
        if source is not None:
            yield 'source', str(source)
        yield from resource_items(self.resource_path, self.resource_id)
        # trail-todo: origin - drop replicas reported by other views once locality is trusted.


PLAIN = (str, int, float, bool, type(None))


def plain(value: object) -> str:
    if isinstance(value, str):
        out = repr(value)
    else:
        out = str(value)
    return out


def field_changes(
        old: dict[str, Any],
        new: dict[str, Any],
        skip: tuple[str, ...] = (),
) -> list[str]:
    """
    One phrase per field that differs between two versions of a layer: the old and new value of a
    plain value, or that a nested value changed. `name` and `visible` come first.
    """

    def order(name: str) -> tuple[bool, str]:
        out = (name not in ('name', 'visible'), name)
        return out

    names = sorted({*old, *new} - {*skip}, key=order)
    out = []
    for name in names:
        before = old.get(name)
        after = new.get(name)
        if before == after:
            continue
        if (
            not isinstance(before, PLAIN)
            or not isinstance(after, PLAIN)
        ):
            out.append(f'{name} changed')
        elif name not in old:
            out.append(f'{name} set to {plain(after)}')
        elif name not in new:
            out.append(f'{name} removed')
        else:
            out.append(f'{name} {plain(before)} → {plain(after)}')
    return out


def resource_items(
        path: str,
        asset_id: str | None,
        prefix: str = '',
) -> Iterator[tuple[str, object]]:
    if not path:
        return
    yield f'{prefix}resource_path', path
    if asset_id is None:
        yield f'{prefix}resource', 'untracked'
    else:
        yield f'{prefix}resource_id', asset_id


@dataclass(kw_only=True, slots=True, repr=False)
class LayerV1Event(LayerEvent):
    schema: ClassVar[str] = 'https://schema.jupytergis.org/events/layer/v1'


@dataclass(kw_only=True, slots=True, repr=False)
class SourceEvent(JupyterEvent):
    selectable: ClassVar[tuple[str, ...]] = ('verb', 'source_id', 'is_local', 'old', 'new')

    resource_id: str | None = field(default=None, repr=False)
    resource_path: str = field(default='', repr=False)
    previous_resource_id: str | None = field(default=None, repr=False)
    previous_resource_path: str = field(default='', repr=False)

    @property
    def source_id(self) -> str | None:
        out = (
            self.data
            .get('object', {})
            .get('source_id')
        )
        return out

    @property
    def old(self) -> Source | None:
        value = (
            self.data
            .get('object', {})
            .get('old')
        )
        out = Source.from_payload(value)
        return out

    @property
    def new(self) -> Source | None:
        value = (
            self.data
            .get('object', {})
            .get('new')
        )
        out = Source.from_payload(value)
        return out

    @property
    def current(self) -> Source | None:
        if self.verb == 'removed':
            out = self.old
        else:
            out = self.new
        return out

    @property
    def previous(self) -> Source | None:
        if self.verb != 'updated':
            return None
        out = self.old
        return out

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield from JupyterEvent._repr_items(self)
        if self.source_id is not None:
            yield 'source_id', self.source_id
        for name in ('old', 'new'):
            source = getattr(self, name)
            if source is not None:
                yield name, str(source)
        yield from resource_items(self.resource_path, self.resource_id)
        yield from resource_items(
            self.previous_resource_path,
            self.previous_resource_id,
            'previous_',
        )


@dataclass(kw_only=True, slots=True, repr=False)
class SourceV1Event(SourceEvent):
    schema: ClassVar[str] = 'https://schema.jupytergis.org/events/source/v1'


@dataclass(kw_only=True, slots=True, repr=False)
class ModeEvent(PageEvent):
    # trail: mode - a mode belongs to one map model in one page and is never synchronized.
    is_shared: ClassVar[bool] = False
    selectable: ClassVar[tuple[str, ...]] = (
        'verb', 'mode', 'previous', 'frontend_id', 'focus_seq',
    )
    labels: ClassVar[dict[str, str]] = {
        'panning': 'none',
        'identifying': 'Identify',
        'marking': 'Add Marker',
        'drawing': 'Edit Features',
    }

    @property
    def mode(self) -> str | None:
        out = (
            self.data
            .get('object', {})
            .get('mode')
        )
        return out

    @property
    def previous(self) -> str | None:
        out = (
            self.data
            .get('object', {})
            .get('previous')
        )
        return out

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield from JupyterEvent._repr_items(self)
        for name in ('mode', 'previous'):
            value = getattr(self, name)
            if value is not None:
                yield name, self.labels.get(value, value)
        for name in ('frontend_id', 'focus_seq'):
            value = getattr(self, name)
            if value is not None:
                yield name, value


@dataclass(kw_only=True, slots=True, repr=False)
class ModeV1Event(ModeEvent):
    schema: ClassVar[str] = 'https://schema.jupytergis.org/events/mode/v1'


@dataclass(kw_only=True, slots=True, repr=False)
class ExecutionEvent(JupyterEvent):
    # trail: execution - a kernel's IOPub messages for one request, as Trail observed them.
    schema: ClassVar[str] = 'trail/notebook-execution/v1'
    is_shared: ClassVar[bool] = False
    selectable: ClassVar[tuple[str, ...]] = (
        'verb', 'execution_count', 'kernel_id', 'request_id', 'status', 'code', 'cell_id',
    )

    @property
    def execution_count(self) -> int | None:
        out = self.data.get('execution_count')
        return out

    @property
    def kernel_id(self) -> str | None:
        out = self.data.get('kernel_id')
        return out

    @property
    def request_id(self) -> str | None:
        out = self.data.get('request_id')
        return out

    @property
    def code(self) -> str:
        out = self.data.get('code', '')
        return out

    @property
    def cell_id(self) -> str | None:
        out = self.data.get('cell_id')
        return out

    @property
    def status(self) -> str | None:
        if self.verb == 'completed':
            return 'ok'
        if self.verb == 'errored':
            return 'error'
        return None

    @property
    def duration(self) -> float | None:
        started = self.data.get('started_at')
        ended = self.data.get('ended_at')
        if (
            not isinstance(started, str)
            or not isinstance(ended, str)
        ):
            return None
        delta = datetime.fromisoformat(ended) - datetime.fromisoformat(started)
        out = delta.total_seconds()
        return out

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield from JupyterEvent._repr_items(self)
        if self.execution_count is not None:
            yield 'execution_count', self.execution_count
        if self.verb in ('started', 'recovered'):
            lines = self.code.splitlines() or ['']
            first = lines[0]
            if len(first) > 72:
                first = f'{first[:69]}...'
            if len(lines) > 1:
                first = f'{first} (+{len(lines) - 1} lines)'
            yield 'code', first
        duration = self.duration
        if duration is not None:
            yield 'duration', f'{duration:.3f} s'
        outputs = self.data.get('outputs')
        if outputs:
            yield 'outputs', ', '.join(
                f'{kind} {count}'
                for kind, count in outputs.items()
            )
        error = self.data.get('error')
        if isinstance(error, dict):
            yield 'error', f"{error.get('ename')}: {error.get('evalue')}"
        if self.cell_id is not None:
            yield 'cell_id', self.cell_id
        if not self.src_path:
            paths = self.data.get('paths')
            if paths:
                yield 'paths', paths
        yield 'kernel_id', self.kernel_id


@dataclass(kw_only=True, slots=True, repr=False)
class KernelSessionEvent(JupyterEvent):
    # trail: execution - which notebook a kernel served, from the server's sessions.
    schema: ClassVar[str] = 'trail/kernel-session/v1'
    is_shared: ClassVar[bool] = False
    selectable: ClassVar[tuple[str, ...]] = ('verb', 'kernel_id', 'kernel_name', 'path')

    @property
    def kernel_id(self) -> str | None:
        out = self.data.get('kernel_id')
        return out

    @property
    def kernel_name(self) -> str | None:
        out = self.data.get('kernel_name')
        return out

    @property
    def path(self) -> str | None:
        out = self.data.get('path')
        return out

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield from JupyterEvent._repr_items(self)
        for name in ('kernel_name', 'kernel_id'):
            value = getattr(self, name)
            if value:
                yield name, value
        if not self.src_path:
            yield 'path', self.path


@dataclass(kw_only=True, slots=True, repr=False)
class ViewportEvent(JupyterEvent):
    is_shared: ClassVar[bool] = False
    selectable: ClassVar[tuple[str, ...]] = ('verb', 'coordinates', 'zoom', 'extent')

    @property
    def coordinates(self) -> dict[str, float] | None:
        out = (
            self.data
            .get('object', {})
            .get('new', {})
            .get('coordinates')
        )
        return out

    @property
    def zoom(self) -> float | None:
        out = (
            self.data
            .get('object', {})
            .get('new', {})
            .get('zoom')
        )
        return out

    @property
    def extent(self) -> list[float] | None:
        out = (
            self.data
            .get('object', {})
            .get('new', {})
            .get('extent')
        )
        return out

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield from JupyterEvent._repr_items(self)
        # trail: viewport - record the movement the emitter reports without another delay.
        for name in ('coordinates', 'zoom', 'extent'):
            value = getattr(self, name)
            if value is not None:
                yield name, value


@dataclass(kw_only=True, slots=True, repr=False)
class ViewportV1Event(ViewportEvent):
    schema: ClassVar[str] = 'https://schema.jupytergis.org/events/viewport/v1'


@dataclass(kw_only=True, slots=True, repr=False)
class ContentsServiceV1Event(JupyterEvent):
    schema: ClassVar[str] = 'https://events.jupyter.org/jupyter_server/contents_service/v1'
    is_shared: ClassVar[bool] = False
    selectable: ClassVar[tuple[str, ...]] = ('verb',)

    # trail: saves - one record holds a run of saves and the file sessions they caused.
    started: datetime | None = None
    saves: int = 1
    writes: int = 0

    def __post_init__(self) -> None:
        JupyterEvent.__post_init__(self)
        if isinstance(self.started, str):
            self.started = datetime.fromisoformat(self.started)

    @property
    def verb(self) -> str:
        if self.data.get('action') == 'save':
            return 'saved'
        out = self.data.get('action', '')
        return out
