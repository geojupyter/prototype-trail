from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from trail.entry import Entries, Entry
from trail.event import AddDirEvent, RemoveDirEvent
from trail.markers import Scopes, parse
from trail.util import bare_repr, container, list_repr

if TYPE_CHECKING:
    from trail.trail import Trail


@dataclass(kw_only=True, eq=False, repr=False)
class Dir(Entry):
    """
    A directory named by `.markers`. The log holds its ID and what happened to it, and its root
    and scopes are taken from `.markers` each time the lines change. A root takes the root
    scopes at any depth beneath it. Its own scopes are shown relative to it.

    >>> trail: Trail
    >>> project: Path
    >>> trail.dirs[project / 'data']
    Dir
        id: 3a654b91eb2047b3a2718d7f1d6a03e1
        path: /tmp/tmpp_bl05lq/data
        root: False
        scopes: [
            ./*.png,
        ]
    """
    # a Dir is always a directory, as a directory asset is
    is_directory: ClassVar[bool] = True
    root: bool = False
    scopes: Scopes = field(default_factory=Scopes)

    if TYPE_CHECKING:
        _parent: Dirs

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        """
        Yields the fields a record in the feed shows. The scopes are listed on one line,
        relative to the Dir.
        """
        yield from super()._repr_items()
        yield 'root', self.root
        if self.scopes:
            yield 'scopes', ', '.join(self.scopes.relative)

    def __repr__(self) -> str:
        """Shows the id, the path, whether the Dir is a root, and its scopes relative to it."""
        lines = [
            type(self).__name__,
            f'    id: {bare_repr(self.id)}',
            f'    path: {bare_repr(str(self.path))}',
            f'    root: {self.root!r}',
        ]
        rendered = list_repr('scopes', self.scopes.relative, len(self.scopes))
        lines.extend(rendered)
        return '\n'.join(lines)

    @property
    def recursive(self) -> bool:
        """
        Whether anything below the directory's own files may be tracked through it. A root is
        recursive, and so is a Dir with a scope such as `./**/*.tif`.

        >>> self.recursive
        False
        """
        return (
            self.root
            or self.scopes.recursive
        )

    def decide(self, path: Path) -> str | None:
        """
        Returns the line among the Dir's own scopes that decides a path below it, or None.
        Case is ignored.

        >>> project: Path
        >>> self.decide(project / 'data' / 'B.PNG')
        'data/*.png'
        """
        return self.scopes.decide(path.relative_to(self.path).as_posix())


def build(
        lines: Iterable[str],
        project: Path,
        excluded: Collection[Path] = (),
) -> tuple[dict[Path, Dir], Scopes]:
    """
    Returns the directories the lines of `.markers` name, in the order they are first named,
    and the root scopes among the lines. Lines that do not parse are left out. So are the
    directories in `excluded`, containers and directories inside containers, since the inside
    of a directory asset is never walked.

    >>> project: Path
    >>> build(['./', '*.csv', 'data/*.png'], project)[1]
    Scopes (1)
        *.csv
    """
    # in the order .markers first names each directory, so reprs and walks are stable
    order: dict[Path, None] = {}
    roots: set[Path] = set()
    scoped: dict[Path, list[str]] = {}
    patterns: dict[Path, list[str]] = {}
    unanchored: list[str] = []
    for line in lines:
        try:
            base, pattern = parse(line, project)
        except ValueError:
            continue
        if base is None:
            unanchored.append(line)
            continue
        lineage = (base, *base.parents)
        inside = any(
            container(path)
            for path in lineage
        )
        if (
            base in excluded
            or inside
        ):
            continue
        order[base] = None
        if pattern:
            scoped.setdefault(base, []).append(line)
            patterns.setdefault(base, []).append(pattern)
        else:
            roots.add(base)
    dirs = {
        path: Dir(
            path=path,
            root=path in roots,
            scopes=Scopes(
                scoped.get(path, ()),
                patterns.get(path, ()),
            ),
        )
        for path in order
    }
    # a root scope's pattern is its line
    scopes = Scopes(unanchored, unanchored)
    return dirs, scopes


def match(
        path: Path,
        dirs: Mapping[Path, Dir],
        scopes: Scopes,
) -> str | None:
    """
    Returns the line of `.markers` that decides a path, or None if none does. The dir scopes of
    the path's parents are checked first, deepest first, the way git ranks nested .gitignore
    files. If none decides, the root scopes are checked beneath the nearest root.

    >>> trail: Trail
    >>> project: Path
    >>> dirs = trail.dirs.path2entry
    >>> scopes = trail.dirs.scopes
    >>> match(project / 'data' / 'a.png', dirs, scopes)
    'data/*.png'
    """
    root = None
    for parent in path.parents:
        directory = dirs.get(parent)
        if directory is None:
            continue
        line = directory.decide(path)
        if line is not None:
            return line
        if (
            directory.root
            and root is None
        ):
            root = parent
    if root is None:
        return None
    return scopes.decide(path.relative_to(root).as_posix())


class Dirs(Entries[Dir]):
    """
    The directories `.markers` names, by id and by path, and the root scopes, which apply
    beneath every root. A subfolder of a root is covered by its ancestor and has no Dir of its
    own, so this is not the set of watched directories. `refresh` keeps the Dirs in line with
    `.markers`, logging each Dir that comes or goes.

    >>> trail: Trail
    >>> trail.dirs
    Dirs (2)
        0. Dir
            id: 04e15251538241309989553fb4672653
            path: /tmp/tmpp_bl05lq
            root: True
            scopes: []
        1. Dir
            id: 3a654b91eb2047b3a2718d7f1d6a03e1
            path: /tmp/tmpp_bl05lq/data
            root: False
            scopes: [
                ./*.png,
            ]
    """

    _parent: Trail
    entry_type = Dir

    def __init__(self, parent: Trail | None = None) -> None:
        """Start with no Dirs and no root scopes. `refresh` takes them from `.markers`."""
        Entries.__init__(self, parent)
        self.scopes = Scopes()

    def __repr__(self) -> str:
        """Lists each Dir with its position."""
        lines = [f'{type(self).__name__} ({len(self)})']
        for position, directory in enumerate(self):
            head, *body = repr(directory).splitlines()
            lines.append(f'    {position}. {head}')
            lines.extend(
                f'    {line}'
                for line in body
            )
        return '\n'.join(lines)

    def clear(self) -> None:
        """Refuses, since the Dirs come from `.markers`. `Trail.unmark` removes one."""
        msg = 'Dirs come from .markers, so a Dir is removed by unmarking its lines'
        raise TypeError(msg)

    def refresh(self) -> None:
        """
        Bring the live Dirs in line with `.markers`. A directory the lines name without a Dir
        gets one, logged as an AddDirEvent. A Dir no line names any more is logged as a
        RemoveDirEvent. A Dir that stays keeps its ID and takes its root and scopes from the
        lines. Directory assets are never Dirs.
        """
        trail = self._trail
        markers = trail.markers
        excluded = {
            asset.path
            for asset in trail.assets
            if asset.is_directory
        }
        built, self.scopes = build(markers, markers.project, excluded)
        for directory in self:
            if directory.path not in built:
                self._remove(directory)
        for path, fresh in built.items():
            current = self.path2entry.get(path)
            if current is None:
                fresh._parent = self
                self._add(fresh)
            else:
                current.root = fresh.root
                current.scopes = fresh.scopes

    def relog(self) -> None:
        """
        Log each live Dir again, keeping its ID. `Events.clear` calls it, since the emptied log
        no longer holds the events that added the Dirs.
        """
        for directory in self:
            self._add(directory)

    def _add(self, directory: Dir) -> None:
        """Hold a Dir and log an AddDirEvent for it."""
        trail = self._trail
        event = AddDirEvent(
            src_path=str(directory.path),
            rule=self._rule(directory.path),
        )
        event.entry = directory
        event.apply(trail)
        trail.events.append(event)

    def _remove(self, directory: Dir) -> None:
        """Let go of a Dir and log a RemoveDirEvent for it."""
        trail = self._trail
        event = RemoveDirEvent(src_path=str(directory.path))
        event.apply(trail)
        trail.events.append(event)

    def _rule(self, path: Path) -> str | None:
        """Returns the first line of `.markers` that names a directory, or None."""
        markers = self._trail.markers
        project = markers.project
        for line in markers:
            try:
                base, _ = parse(line, project)
            except ValueError:
                continue
            if base == path:
                return line
        return None

    def match(self, path: Path) -> str | None:
        """
        Returns the line of `.markers` that decides a path, or None if none does. See `match`.

        >>> project: Path
        >>> self.match(project / 'data' / 'b.png')
        'data/*.png'
        """
        return match(path, self.path2entry, self.scopes)

    def tracks(self, path: Path) -> bool:
        """
        Whether `.markers` says to track the path. It does if a line other than an exclusion
        decides it.

        >>> project: Path
        >>> self.tracks(project / 'notes.md')
        False
        """
        line = self.match(path)
        return (
            line is not None
            and not line.startswith('!')
        )
