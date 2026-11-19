from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trail import fmt


@dataclass(frozen=True, slots=True, kw_only=True)
class Inline:
    """Data held inside the map file. Only its field name and size are reported."""

    # parameter that holds the data
    field: str
    # size in bytes
    size: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Source:
    """A reference to the data a layer reads, as the event feed reports it."""

    # source id in the map
    id: str
    # kind of source
    type: str
    # data file path as written in the map
    path: str | None = None
    # same path relative to the server root
    resolved_path: str | None = None
    # tables read
    tables: tuple[str, ...] = ()
    # urls read, without credentials or query
    urls: tuple[str, ...] = ()
    # inline data, if there is no path
    inline: Inline | None = None

    @classmethod
    def from_payload(cls, value: Any) -> Source | None:
        """Build a Source from a payload, or None if it is malformed."""
        if (
            not isinstance(value, dict)
            or not isinstance(value.get('id'), str)
            or not isinstance(value.get('type'), str)
        ):
            return None

        def text(name: str) -> str | None:
            item = value.get(name)
            if isinstance(item, str):
                return item
            return None

        def texts(name: str) -> tuple[str, ...]:
            items = value.get(name)
            if not isinstance(items, list):
                return ()
            out = tuple(
                item
                for item in items
                if isinstance(item, str)
            )
            return out

        inline = None
        item = value.get('inline')
        if (
            isinstance(item, dict)
            and isinstance(item.get('field'), str)
            and type(item.get('size')) is int
        ):
            inline = Inline(
                field=item['field'],
                size=item['size'],
            )
        out = cls(
            id=value['id'],
            type=value['type'],
            path=text('path'),
            resolved_path=text('resolved_path'),
            tables=texts('tables'),
            urls=texts('urls'),
            inline=inline,
        )
        return out

    def __str__(self) -> str:
        parts = [self.type]
        if self.path:
            parts.append(self.path)
        if self.tables:
            tables = ', '.join(self.tables)
            parts.append(f'({tables})')
        parts.extend(self.urls)
        if self.inline is not None:
            size = fmt.st_size(self.inline.size)
            parts.append(f'inline {self.inline.field} {size}')
        out = ' '.join(parts)
        return out
