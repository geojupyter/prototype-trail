from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import fields
from typing import TYPE_CHECKING, Any, Protocol, overload

from trail import fmt
from trail.node import Node
from trail.select import Select

if TYPE_CHECKING:
    from trail.trail import Trail


class Record(Protocol):
    # what a collection needs of what it holds: the id it is kept under
    id: str


def attributes(types: Sequence[type]) -> tuple[str, ...]:
    """
    Returns the names a record of these classes can be selected on, in the order the classes
    define them. These are the dataclass fields, `cls`, and the properties a class names in
    `selectable`.

    >>> trail: Trail
    >>> attributes(trail.dirs.types)
    ('id', 'path', 'root', 'scopes', 'cls', 'name', 'directory')
    """
    names = [
        field.name
        for type_ in types
        for field in fields(type_)
        if not field.name.startswith('_')
    ]
    extra = [
        name
        for type_ in types
        for name in getattr(type_, 'selectable', ())
    ]
    # `cls` is not an attribute; Select.held reads it as the record's class
    ordered = dict.fromkeys([*names, 'cls', *extra])
    return tuple(ordered)


class Collection[K, V: Record](Node):
    """
    The records of a Trail, such as its events, its assets or its Dirs, held by id.

    - `data` maps each id to its record.
    - `ids` holds the ids in the order they were recorded.
    - `K` is what a record can be looked up by: its id for events, its id or its path for assets
      and Dirs.
    - `V` is the class of the records.

    Iteration, membership and length count records, not keys.
    """

    _parent: Trail

    def __init__(self, parent: Trail | None = None) -> None:
        Node.__init__(self, parent)
        self.data: dict[str, V] = {}
        self.ids: list[str] = []

    @property
    def types(self) -> Sequence[type[V]]:
        """
        Returns the classes of the records the collection can hold. A listing in the console
        offers their fields as `field=` completions. See `attributes`.
        """
        raise NotImplementedError

    @property
    def select(self) -> Select[Collection[K, V], Any]:
        # a property, since a cached_property leaves Self unbound and `assets.select(...)` would
        # type as Any rather than Assets
        return Select(self)

    def __iter__(self) -> Iterator[V]:
        # the records are taken up front, so that removing one while iterating over them, as
        # offtrailing does, does not take the records after it along with it
        records = [
            self.data[identifier]
            for identifier in self.ids
        ]
        return iter(records)

    def __len__(self) -> int:
        return len(self.data)

    def __contains__(self, key: K | V) -> bool:
        # a record answers for the id it is held under, so `event in events` holds wherever
        # `event.id in events` does
        return getattr(key, "id", key) in self.data

    def __getitem__(self, key: K) -> V:
        return self.data[key]

    @overload
    def get(self, key: K) -> V | None: ...

    @overload
    def get[D](
        self,
        key: K,
        default: D,
    ) -> V | D: ...

    def get[D](
        self,
        key: K,
        default: D | None = None,
    ) -> V | D | None:
        try:
            return self[key]
        except KeyError:
            return default

    def append(self, record: V) -> None:
        """Hold `record` under the id it carries."""
        self[record.id] = record

    def __setitem__(
        self,
        key: str,
        value: V,
    ) -> None:
        raise NotImplementedError

    def __delitem__(self, key: K) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        self.data.clear()
        self.ids.clear()

    def __repr__(self) -> str:
        return fmt.items(type(self).__name__, self)
