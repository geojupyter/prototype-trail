from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from trail.event import Event

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

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield from Event._repr_items(self)
        if self.verb:
            yield 'verb', self.verb

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
class DocumentEvent(JupyterEvent):
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
    def frontend_id(self) -> str | None:
        out = self.data.get('frontend_id')
        return out

    @property
    def focus_seq(self) -> int | None:
        out = self.data.get('focus_seq')
        return out

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
    selectable: ClassVar[tuple[str, ...]] = ('verb', 'layer_id', 'layer')

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

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield from JupyterEvent._repr_items(self)
        if self.layer_id is not None:
            yield 'layer_id', self.layer_id
        if self.layer is not None:
            yield 'layer', self.layer
        # trail-todo: resources - resolve source references when the feed supplies them.
        # trail-todo: origin - use locality metadata before attributing edits to a user.


@dataclass(kw_only=True, slots=True, repr=False)
class LayerV1Event(LayerEvent):
    schema: ClassVar[str] = 'https://schema.jupytergis.org/events/layer/v1'


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

    @property
    def verb(self) -> str:
        if self.data.get('action') == 'save':
            return 'saved'
        out = self.data.get('action', '')
        return out
