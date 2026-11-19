from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from trail import fmt
from trail.collection import Collection

Outcome = Literal[
    # not decided yet
    'pending',
    # part of an event in the log
    'recorded',
    # acted on, but not part of an event
    'used',
    # not recorded, see the reason
    'skipped',
]


@dataclass(kw_only=True, slots=True, repr=False)
class Message:
    """One message from a server's feed, with what became of it."""

    # arrival number, unchanged when older messages drop
    number: int
    # arrival time
    timestamp: datetime
    # url of the sending server
    server: str
    # schema id of the payload
    schema_id: str
    # the payload without its schema id
    data: dict[str, Any] = field(repr=False)
    # key in Messages
    id: str = field(default_factory=lambda: uuid4().hex)
    # absolute file path, or the page's own path for notebook messages
    src_path: str = ''
    # what became of it
    outcome: Outcome = 'pending'
    # why it was skipped
    reason: str = ''
    # id of the event it became
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
    """The latest messages the consumer received, kept in memory."""

    # most messages kept
    limit = 1000

    @property
    def types(self) -> list[type[Message]]:
        return [Message]

    def __setitem__(
            self,
            key: str,
            value: Message,
    ) -> None:
        """Hold a message, dropping the oldest beyond the limit."""
        if key not in self.data:
            self.ids.append(key)
        self.data[key] = value
        while len(self.ids) > self.limit:
            del self.data[self.ids.pop(0)]

    def __delitem__(self, key: str) -> None:
        del self.data[key]
        self.ids.remove(key)
