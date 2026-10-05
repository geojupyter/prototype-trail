from __future__ import annotations

from collections.abc import Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from trail.markers import Scopes, parse
from trail.node import Node
from trail.util import bare_repr, container, list_repr

if TYPE_CHECKING:
    from trail.trail import Trail


@dataclass(frozen=True, kw_only=True, repr=False)
class Dir:
    """
    A directory named by `.markers`. It is not tracked and has no ID. A root takes the root
    scopes at any depth beneath it. Its own scopes are shown relative to it.

    >>> trail.dirs[project / 'data']
    Dir
        path: /tmp/tmpp_bl05lq/data
        root: False
        scopes: [
            ./*.png,
        ]
    """
    path: Path
    root: bool = False
    scopes: Scopes = field(default_factory=Scopes)

    def __repr__(self) -> str:
        """Shows the path, whether the Dir is a root, and its scopes relative to it."""
        lines = [
            type(self).__name__,
            f'    path: {bare_repr(str(self.path))}',
            f'    root: {self.root!r}',
        ]
        lines.extend(
            list_repr(
                'scopes',
                self.scopes.relative,
                len(self.scopes),
            )
        )
        return '\n'.join(lines)

    @property
    def recursive(self) -> bool:
        """
        Whether anything below the directory's own files may be tracked through it. A root is
        recursive, and so is a Dir with a scope such as `./**/*.tif`.

        >>> trail.dirs[project / 'data'].recursive
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

        >>> trail.dirs[project / 'data'].decide(project / 'data' / 'B.PNG')
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
        if (
            base in excluded
            or any(
                container(path)
                for path in (base, *base.parents)
            )
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


class Dirs(
    Node,
    Mapping[Path, Dir],
):
    """
    The directories `.markers` names, keyed by path, and the root scopes, which apply beneath
    every root. A subfolder of a root is covered by its ancestor and has no Dir of its own, so
    this is not the set of watched directories.

    >>> trail.dirs
    Dirs (2)
        0. Dir
            path: /tmp/tmpp_bl05lq
            root: True
            scopes: []
        1. Dir
            path: /tmp/tmpp_bl05lq/data
            root: False
            scopes: [
                ./*.png,
            ]
    """

    _parent: Trail

    def __init__(self, parent: Trail) -> None:
        """
        Build the Dirs and the root scopes from the Trail's markers, leaving out the Trail's
        directory assets.
        """
        Node.__init__(self, parent)
        markers = parent.markers
        excluded = {
            asset.path
            for asset in parent.assets
            if asset.is_directory
        }
        self.data, self.scopes = build(markers, markers.project, excluded)

    def __getitem__(self, key: Path) -> Dir:
        """Returns the Dir at a path."""
        return self.data[key]

    def __iter__(self) -> Iterator[Path]:
        """Iterates over the paths, in the order `.markers` first names them."""
        return iter(self.data)

    def __len__(self) -> int:
        """The number of Dirs."""
        return len(self.data)

    def __repr__(self) -> str:
        """Lists each Dir with its position."""
        lines = [f'{type(self).__name__} ({len(self)})']
        for position, directory in enumerate(self.data.values()):
            head, *body = repr(directory).splitlines()
            lines.append(f'    {position}. {head}')
            lines.extend(
                f'    {line}'
                for line in body
            )
        return '\n'.join(lines)

    def match(self, path: Path) -> str | None:
        """
        Returns the line of `.markers` that decides a path, or None if none does. See `match`.

        >>> trail.dirs.match(project / 'data' / 'b.png')
        'data/*.png'
        """
        return match(path, self.data, self.scopes)

    def tracks(self, path: Path) -> bool:
        """
        Whether `.markers` says to track the path. It does if a line other than an exclusion
        decides it.

        >>> trail.dirs.tracks(project / 'notes.md')
        False
        """
        line = self.match(path)
        return (
            line is not None
            and not line.startswith('!')
        )
