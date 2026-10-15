from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Self, overload
from uuid import uuid4

from trail import fmt
from trail.collection import Collection
from trail.node import Node
from trail.util import PathLike, normalize_id

if TYPE_CHECKING:
    from trail.event import Event

# an entry is addressed by its hexadecimal ID or by its path
EntryKey = PathLike


@dataclass(kw_only=True, eq=False, repr=False)
class Entry(Node):
    """
    Anything the log holds an ID for: a file or directory asset, or a Dir. It belongs to one
    collection, `trail.assets` or `trail.dirs`.
    """
    # properties a listing offers as completions, besides the dataclass fields
    selectable: ClassVar[tuple[str, ...]] = (
        'name',
        'directory',
    )

    id: str = field(default_factory=lambda: uuid4().hex)
    path: Path

    if TYPE_CHECKING:
        _parent: Entries[Self] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        """Normalize the id and resolve the path."""
        Node.__init__(self)
        self.id = normalize_id(self.id)
        self.path = Path(self.path).expanduser().resolve()

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        """Yields the fields a repr shows, starting with the id and the path."""
        yield 'id', self.id
        yield 'path', str(self.path)

    def __repr__(self) -> str:
        """Shows the class name, then each field on its own line."""
        lines = [type(self).__name__]
        lines.extend(
            f'    {name}: {fmt.bare(value)}'
            for name, value in self._repr_items()
        )
        return '\n'.join(lines)

    @property
    def name(self) -> str:
        """Name of the entry (last component of the path)."""
        return self.path.name

    @property
    def directory(self) -> Path:
        """Directory containing the entry."""
        return self.path.parent

    @cached_property
    def events(self) -> dict[str, Event]:
        """Events associated with the entry."""
        return {}

    def track(self) -> Self:
        """
        Hold the entry in its collection and return it. Nothing is logged. An older copy with
        the same id, or another entry at the same path, is offtrailed first.
        """
        collection = self._parent
        if collection is None:
            raise ValueError(f"{type(self).__name__} has no Trail")
        trail = collection._trail
        if self._trail is not trail:
            raise ValueError(f"{type(self).__name__} already belongs to another Trail")
        if trail._is_ignored(self.path):
            raise ValueError(f"Cannot track Trail metadata: {self.path}")
        previous = collection.id2entry.get(self.id)
        # a path holds one entry of each kind
        occupant = collection.path2entry.get(self.path)
        for old in (previous, occupant):
            if (
                old is None
                or old is self
            ):
                continue
            old.offtrail()
        collection[self.id] = self
        return self

    def offtrail(self) -> None:
        """Remove the entry from the collection holding it. Nothing is logged."""
        collection = self._parent
        if (
            collection is None
            or collection.id2entry.get(self.id) is not self
        ):
            return
        del collection[self.id]


class Entries[E: Entry](Collection[EntryKey, E]):
    """
    Entries by id and by path, in the order they were added. `Assets` and `Dirs` are the two
    kinds.
    """

    # the class of what the collection holds
    entry_type: ClassVar[type[Entry]] = Entry

    @property
    def types(self) -> list[type[Entry]]:
        """Returns `entry_type` and its subclasses at any depth."""
        out = [self.entry_type]
        # the list grows as it is walked, so each subclass is walked in turn
        for type_ in out:
            out.extend(type_.__subclasses__())
        return out

    @cached_property
    def path2entry(self) -> dict[Path, E]:
        """
        Maps each path to its entry. It is built from `data`, not stored separately, so a selection
        builds its own.
        """
        return {
            entry.path: entry
            for entry in self.data.values()
        }

    @property
    def id2entry(self) -> dict[str, E]:
        """Maps each id to its entry. This is `data` itself."""
        return self.data

    @overload
    def __getitem__(self, key: EntryKey) -> E: ...

    @overload
    def __getitem__(self, key: Iterable[EntryKey]) -> tuple[E, ...]: ...

    def __getitem__(
            self,
            key: EntryKey | Iterable[EntryKey],
    ) -> E | tuple[E, ...]:
        """
        Returns the entry with an id or at a path. Several keys return a tuple. Raises KeyError
        for a key that is neither.
        """
        if isinstance(key, str) and key in self.id2entry:
            return self.id2entry[key]
        if isinstance(key, (str, os.PathLike)):
            return self.path2entry[Path(key).expanduser().resolve()]
        selected = []
        for value in key:
            if not isinstance(value, (str, os.PathLike)):
                raise TypeError("Expected a path or an ID")
            selected.append(self[value])
        return tuple(selected)

    def __setitem__(
            self,
            key: str,
            value: E,
    ) -> None:
        """Hold an entry under its id and its path. The key must be the entry's id."""
        entry_type = self.entry_type
        if not isinstance(value, entry_type):
            raise TypeError(f"Expected {entry_type.__name__}, got {type(value).__name__}")
        if key != value.id:
            raise ValueError(f"{entry_type.__name__} ID does not match its key: {key}")
        if key not in self.id2entry:
            self.ids.append(key)
        self.id2entry[key] = value
        self.path2entry[value.path] = value

    def __delitem__(self, key: EntryKey) -> None:
        """Remove an entry without logging it. Each kind has its own event for that."""
        entry = self[key]
        del self.id2entry[entry.id]
        self.ids.remove(entry.id)
        if self.path2entry.get(entry.path) is entry:
            del self.path2entry[entry.path]

    def repath(
            self,
            entry: E,
            previous: Path,
    ) -> None:
        """Move an entry from its previous path to its current one in `path2entry`."""
        if self.path2entry.get(previous) is entry:
            del self.path2entry[previous]
        self[entry.id] = entry

    def __contains__(self, key: EntryKey | E) -> bool:
        """Whether an entry, an id or a path is held."""
        if isinstance(key, Entry):
            return key.id in self.id2entry
        if isinstance(key, str) and key in self.id2entry:
            return True
        return Path(key).expanduser().resolve() in self.path2entry
