from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from trail import fmt
from trail.collection import Collection

Outcome = Literal['pending', 'recorded', 'used', 'skipped']


@dataclass(kw_only=True, slots=True, repr=False)
class Message:
    number: int
    timestamp: datetime
    server: str
    schema_id: str
    data: dict[str, Any] = field(repr=False)
    id: str = field(default_factory=lambda: uuid4().hex)
    src_path: str = ''
    outcome: Outcome = 'pending'
    reason: str = ''
    event: str = ''

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        for name in ('number', 'timestamp', 'server', 'schema_id', 'src_path',
                     'outcome', 'reason', 'event'):
            value = getattr(self, name)
            if isinstance(value, datetime):
                value = fmt.clock(value)
            if value != '':
                yield name, value

    def __repr__(self) -> str:
        out = fmt.items(type(self).__name__, [self])
        return out


class Messages(Collection[str, Message]):
    limit = 1000

    @property
    def types(self) -> list[type[Message]]:
        return [Message]

    def __setitem__(
            self,
            key: str,
            value: Message,
    ) -> None:
        if key not in self.data:
            self.ids.append(key)
        self.data[key] = value
        while len(self.ids) > self.limit:
            del self.data[self.ids.pop(0)]

    def __delitem__(self, key: str) -> None:
        del self.data[key]
        self.ids.remove(key)
