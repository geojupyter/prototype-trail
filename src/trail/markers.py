from __future__ import annotations

import os
import re
from collections.abc import Iterable, Iterator, Sequence
from functools import cached_property
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Final

from pathspec import PathSpec

from trail.node import Node
from trail.util import asset_repr, container, lines_repr

if TYPE_CHECKING:
    from trail.trail import Trail

# the characters that make a segment a wildcard rather than a name
MAGIC: Final = re.compile(r'[*?[]')
# the prefixes that anchor a line to a directory rather than to every root
ANCHORS: Final = ('/', './', '../', '~/')

# directory of the markers files that ship with trail
MARKERS: Final = files('trail') / 'markers'


def available() -> list[str]:
    """The names of the markers files that ship with trail, e.g. 'default'."""
    return sorted(
        entry.name.removesuffix('.txt')
        for entry in MARKERS.iterdir()
        if entry.name.endswith('.txt')
    )


def parse(
        line: str,
        project: Path,
) -> tuple[Path | None, str]:
    """
    Returns the directory a line of `.markers` belongs to and the pattern compiled from it. The
    directory is None for a root scope, and the pattern is empty for a directory marker. Raises
    ValueError for a line that is not a valid marker.

    >>> parse('data/*.png', project)
    (PosixPath('/tmp/tmpp_bl05lq/data'), '/*.png')
    >>> parse('*.csv', project)
    (None, '*.csv')
    """
    negated = line.startswith('!')
    body = line.removeprefix('!')
    if not body.strip():
        msg = f'Not a marker: {line!r}'
        raise ValueError(msg)
    if negated:
        prefix = '!'
    else:
        prefix = ''
    root = (
        body.endswith('/')
        and not negated
        and not MAGIC.search(body)
    )
    anchored = (
        root
        or body.startswith(ANCHORS)
        or '/' in body.rstrip('/')
    )
    if not anchored:
        validate(line)
        return None, line
    if body.startswith('~/'):
        anchor = Path.home()
        body = body.removeprefix('~/')
    elif body.startswith('/'):
        anchor = Path('/')
        body = body.removeprefix('/')
    else:
        anchor = project
    parts = body.rstrip('/').split('/')
    if root:
        index = len(parts)
    else:
        # the last segment names files, so it never joins the base even without a wildcard
        index = next(
            (
                i
                for i, part in enumerate(parts[:-1])
                if MAGIC.search(part)
            ),
            len(parts) - 1,
        )
    base = anchor.joinpath(*parts[:index]).resolve()
    rest = parts[index:]
    if not rest:
        return base, ''
    pattern = f'{prefix}/{"/".join(rest)}'
    if body.endswith('/'):
        pattern += '/'
    validate(pattern)
    return base, pattern


def validate(pattern: str) -> None:
    """
    Raises ValueError if pathspec refuses the pattern. A lone '!' and a trailing backslash are
    refused, for example.
    """
    try:
        PathSpec.from_lines('gitignore', [pattern])
    except ValueError as error:
        msg = f'Not a valid pattern: {pattern!r} ({error})'
        raise ValueError(msg) from None


def reaches_subfolders(pattern: str) -> bool:
    """
    Whether a dir scope pattern reaches below the directory it belongs to, through a '/' or a
    '**'.

    >>> reaches_subfolders('/*.png')
    False
    >>> reaches_subfolders('/**/*.tif')
    True
    """
    return (
        '/' in pattern.lstrip('!').strip('/')
        or '**' in pattern
    )


def kind(
        line: str,
        project: Path,
) -> str:
    """
    Returns how a line of `.markers` is read: 'root', 'root scope', 'dir scope' or
    'dir scope · not subfolders'. A scope starting with '!' gets ' · exclude' after it. A line
    that does not parse is 'invalid'.

    >>> kind('data/*.png', project)
    'dir scope · not subfolders'
    >>> kind('!*.tmp', project)
    'root scope · exclude'
    """
    try:
        base, pattern = parse(line, project)
    except ValueError:
        return 'invalid'
    if base is None:
        out = 'root scope'
    elif not pattern:
        return 'root'
    elif reaches_subfolders(pattern):
        out = 'dir scope'
    else:
        out = 'dir scope · not subfolders'
    if line.startswith('!'):
        out += ' · exclude'
    return out


def slashless(
        line: str,
        project: Path,
) -> Path | None:
    """
    Returns the directory a line without wildcards names, if it lacks its trailing '/'. Such a
    line tracks everything under the directory, of every type.

    >>> slashless('data', project)
    PosixPath('/tmp/tmpp_bl05lq/data')
    >>> slashless('data/', project)
    """
    if (
        line.startswith('!')
        or line.endswith('/')
        or MAGIC.search(line)
    ):
        return None
    try:
        base, pattern = parse(line, project)
    except ValueError:
        return None
    if base is None:
        target = project / line
    else:
        target = base / pattern.lstrip('/')
    if target.is_dir():
        return target
    return None


def canonical(
        text: str,
        project: Path,
        cwd: Path,
) -> str:
    """
    Returns the line of `.markers` that typed text stands for. Relative paths are read from
    `cwd` and written relative to `project`.

    'csv', '.csv' and '**/*.csv' all become '*.csv'. An existing directory gets its trailing
    '/'. A path inside the project is written relative to it, a path under the home directory
    starts with '~/', and any other path stays absolute. A leading '!' is kept.

    A single word that names an existing path is read as that path, and any other single word
    as an extension. './x' forces the path reading, and '.x' or '*.x' force the extension.

    >>> canonical('**/*.csv', project, project)
    '*.csv'
    >>> canonical('notes.md', project, project)
    './notes.md'
    >>> canonical('!data', project, project)
    '!./data/'
    """
    stripped = text.strip()
    negated = stripped.startswith('!')
    body = (
        stripped
        .removeprefix('!')
        .strip()
    )
    if negated:
        prefix = '!'
    else:
        prefix = ''
    # a line without a slash already applies at every depth beneath a root
    if (
        body.startswith('**/')
        and '/' not in body[3:]
    ):
        body = body[3:]
    if not body:
        msg = f'Not a marker: {text!r}'
        raise ValueError(msg)
    if (
        '/' not in body
        and body not in ('.', '..', '~')
    ):
        if MAGIC.search(body):
            return prefix + body
        if body.startswith('.'):
            return f'{prefix}*{body}'
        if not (cwd / body).exists():
            return f'{prefix}*.{body}'
    return prefix + canonical_path(body, negated, project, cwd)


def canonical_path(
        body: str,
        negated: bool,
        project: Path,
        cwd: Path,
) -> str:
    """
    Returns the line for text that is read as a path. The part before the first wildcard is
    resolved from `cwd`, and the rest is kept as typed. `negated` says whether the line starts
    with '!', which `body` no longer holds.

    >>> canonical_path('data', False, project, project)
    'data/'
    """
    trailing = body.endswith('/')
    parts = body.rstrip('/').split('/')
    index = next(
        (
            i
            for i, part in enumerate(parts)
            if MAGIC.search(part)
        ),
        len(parts),
    )
    literal = '/'.join(parts[:index])
    rest = parts[index:]
    if (
        body.startswith('/')
        and not literal
    ):
        literal = '/'
    base = (cwd / Path(literal or '.').expanduser()).resolve()
    directory = (
        trailing
        or (
            not rest
            and base.is_dir()
        )
    )
    if base.is_relative_to(project):
        segments = [*base.relative_to(project).parts, *rest]
        if not segments:
            return './'
        line = '/'.join(segments)
        if directory:
            line += '/'
        alone = (
            '/' in line.rstrip('/')
            or (
                line.endswith('/')
                and not negated
                and not MAGIC.search(line)
            )
        )
        # without its './', a line naming something directly in the project would be a root
        # scope
        if not alone:
            line = f'./{line}'
        return line
    home = Path.home()
    if base.is_relative_to(home):
        line = '/'.join(['~', *base.relative_to(home).parts, *rest])
    else:
        line = '/'.join([base.as_posix().rstrip('/'), *rest])
    if (
        directory
        and not line.endswith('/')
    ):
        line += '/'
    return line


class Scopes(Sequence[str]):
    """
    The scopes of one directory, or the root scopes, as written in `.markers`. Each line is
    held beside the pattern compiled from it. Within one Scopes, the last matching line wins.

    >>> trail.scopes
    Scopes (1)
        *.csv
    """

    def __init__(
            self,
            lines: Sequence[str] = (),
            patterns: Sequence[str] = (),
    ) -> None:
        """Hold each line as written in `.markers`, beside the pattern compiled from it."""
        self.lines = tuple(lines)
        self.patterns = tuple(patterns)

    def __getitem__(self, index: int) -> str:
        """Returns the line at a position, as written in `.markers`."""
        return self.lines[index]

    def __len__(self) -> int:
        """The number of lines."""
        return len(self.lines)

    def __repr__(self) -> str:
        """Lists the lines as written in `.markers`."""
        return lines_repr(type(self).__name__, self.lines)

    @cached_property
    def spec(self) -> PathSpec:
        """
        The patterns compiled for matching. They are lowercased only here, so the lines keep
        the user's spelling.

        >>> trail.dirs[project / 'data'].scopes.spec
        PathSpec(patterns=[GitIgnoreBasicPattern(pattern='/*.png', include=True)], backend='best')
        """
        lowered = (
            pattern.lower()
            for pattern in self.patterns
        )
        return PathSpec.from_lines('gitignore', lowered)

    @cached_property
    def relative(self) -> tuple[str, ...]:
        """
        Each pattern as seen from its Dir. '/*.png' reads as './*.png', and '!/a.csv' as
        '!./a.csv'.

        >>> trail.dirs[project / 'data'].scopes.relative
        ('./*.png',)
        """
        out = []
        for pattern in self.patterns:
            if pattern.startswith('!'):
                out.append(f'!.{pattern[1:]}')
            else:
                out.append(f'.{pattern}')
        return tuple(out)

    @cached_property
    def recursive(self) -> bool:
        """
        Whether any pattern reaches below the directory it belongs to. See `reaches_subfolders`.

        >>> trail.dirs[project / 'data'].scopes.recursive
        False
        """
        return any(
            reaches_subfolders(pattern)
            for pattern in self.patterns
        )

    def decide(self, relative: str) -> str | None:
        """
        Returns the line that decides a relative path, or None if none matches. Case is
        ignored.

        >>> trail.scopes.decide('sub/X.CSV')
        '*.csv'
        """
        index = self.spec.check_file(relative.lower()).index
        if index is None:
            return None
        return self.lines[index]


class Text(Node):
    """
    The `.markers` file beside the trail directory. Every change rewrites it with `compose`,
    which keeps the comments and blank lines a user writes into it.

    >>> trail.markers.text
    Text
        path: /tmp/tmpp_bl05lq/.markers
        tail:
            ./
            *.csv
            data/*.png
    """

    _parent: Markers

    def __repr__(self) -> str:
        """Shows the path and the last lines of the file."""
        return asset_repr(type(self).__name__, self.path)

    @cached_property
    def path(self) -> Path | None:
        """The `.markers` file beside the trail directory. None for a Trail without a directory."""
        path = self._trail.dir
        if path:
            return path.parent / '.markers'
        return None

    def _lines(self) -> list[str]:
        """Returns the lines of the file, comments included. A missing file has none."""
        path = self.path
        if (
            path is None
            or not path.exists()
        ):
            return []
        return path.read_text(encoding='utf-8').splitlines()

    @staticmethod
    def _marker(line: str) -> str | None:
        """Returns a line stripped, or None for a blank line or a comment."""
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith('#')
        ):
            return None
        return stripped

    @classmethod
    def markers(cls, lines: Iterable[str]) -> list[str]:
        """Returns the markers among the lines of a markers file, stripped and in order."""
        out = []
        for line in lines:
            marker = cls._marker(line)
            if marker is not None:
                out.append(marker)
        return out

    def numbered(self) -> Iterator[tuple[int, str]]:
        """Yields each marker in the file with its line number."""
        for number, line in enumerate(self._lines(), start=1):
            marker = self._marker(line)
            if marker is not None:
                yield number, marker

    def read(self) -> None:
        """Load the lines of the file into the Markers, without comments or blank lines."""
        self._parent.data[:] = self.markers(self._lines())

    def create(self, markers: os.PathLike[str] | str) -> None:
        """
        Create the project's .markers file by copying a markers file. Does nothing if
        .markers already exists.

        markers:
            str:
                The name of a markers file, e.g. 'default' copies
                `trail/markers/default.txt`.
            Path:
                A markers file on disk.
        """
        path = self.path
        if (
            path is None
            or path.exists()
        ):
            return
        if isinstance(markers, os.PathLike):
            source = Path(markers).expanduser()
            if not source.is_file():
                msg = f'No markers file at {source}'
                raise FileNotFoundError(msg)
            # its anchored lines were written for the project it came from
            self._parent.imported = True
        else:
            source = MARKERS / f'{markers}.txt'
            if not source.is_file():
                msg = f'No markers file {markers!r}; available: {available()}'
                raise FileNotFoundError(msg)
        self.write(source.read_text(encoding='utf-8'))
        self.read()

    def write(self, text: str) -> None:
        """
        Replace the text of the file. It is written aside and swapped in, so a reader never sees
        a half-written file.
        """
        path = self.path
        if path is None:
            return
        staged = path.with_name(f'{path.name}.tmp')
        staged.write_text(text, encoding='utf-8')
        staged.replace(path)

    def replace(self, markers: Sequence[str]) -> None:
        """Rewrite the file to hold `markers`, keeping its comments. See `compose`."""
        self.write(self.compose(self._lines(), markers))

    @classmethod
    def compose(
            cls,
            lines: Iterable[str],
            markers: Sequence[str],
    ) -> str:
        """
        Returns the text of a markers file holding `markers`. It starts from `lines`, so the
        comments, and the lines that stay, are kept as written and in order. A line written
        twice stays twice, since a later copy can decide differently. Markers not in `lines`
        go to the end.
        """
        wanted = set(markers)
        kept = []
        present = set()
        for line in lines:
            marker = cls._marker(line)
            if marker is None:
                kept.append(line)
            elif marker in wanted:
                kept.append(line)
                present.add(marker)
        kept.extend(
            marker
            for marker in dict.fromkeys(markers)
            if marker not in present
        )
        return ''.join(
            f'{line}\n'
            for line in kept
        )


class Markers(
    Node,
    Sequence[str],
):
    """
    The lines of the `.markers` file, in file order, without comments or blank lines. Every
    change is written through to the file. `trail.dirs` and `trail.scopes` are built from these
    lines and are rebuilt after every change.

    >>> trail.markers
    Markers (3)
        ./
        *.csv
        data/*.png
    """

    _parent: Trail

    def __init__(self, parent: Trail | None = None) -> None:
        """
        Start with no lines. `Text.read` loads them from the file. `imported` says whether
        `.markers` was copied from a file of the user's own.
        """
        Node.__init__(self, parent)
        self.data: list[str] = []
        self.imported = False

    @cached_property
    def text(self) -> Text:
        """Returns the Text that reads and writes the `.markers` file."""
        return Text(self)

    @property
    def project(self) -> Path:
        """
        The directory relative lines resolve against, which is the one holding `.markers`. A
        Trail without a directory uses the working directory.

        >>> trail.markers.project
        PosixPath('/tmp/tmpp_bl05lq')
        """
        directory = self._trail.dir
        if directory is None:
            return Path.cwd().resolve()
        return directory.parent

    def __getitem__(self, index: int) -> str:
        """Returns the line at a position."""
        return self.data[index]

    def __len__(self) -> int:
        """The number of lines."""
        return len(self.data)

    def __contains__(self, value: object) -> bool:
        """Whether a line is in the file, as written."""
        return value in self.data

    def __iter__(self) -> Iterator[str]:
        """Iterates over the lines in file order."""
        return iter(self.data)

    def add(self, *lines: str) -> list[str]:
        """
        Append the lines not yet in the file. Returns the lines added. The lines are taken as
        written. `Trail.mark` makes them canonical first.

        >>> trail.markers.add('*.tif')
        ['*.tif']
        """
        added = [
            line
            for line in dict.fromkeys(lines)
            if line not in self.data
        ]
        if added:
            self.data.extend(added)
            self._changed()
        return added

    def discard(self, *lines: str) -> list[str]:
        """
        Remove every copy of each line. Returns the lines removed.

        >>> trail.markers.discard('*.tif', '*.json')
        ['*.tif']
        """
        removed = [
            line
            for line in dict.fromkeys(lines)
            if line in self.data
        ]
        if removed:
            self.data[:] = [
                line
                for line in self.data
                if line not in removed
            ]
            self._changed()
        return removed

    def replace(self, lines: Iterable[str]) -> None:
        """Make the file hold exactly `lines`, keeping its comments."""
        self.data[:] = dict.fromkeys(lines)
        self._changed()

    def _changed(self) -> None:
        """
        Write the lines through to the file, then drop `trail.dirs`, which is built from them,
        and reconcile the watchdog with the Dirs built in its place. Every change calls it.
        """
        self.text.replace(self.data)
        trail = self._trail
        vars(trail).pop('dirs', None)
        trail.watchdog.reconcile()

    def _numbered(self) -> Iterator[tuple[str, str]]:
        """
        Yields each line with where it is written, as 'path:number'. The lines of a Trail
        without a file are numbered by position.
        """
        path = self.text.path
        if (
            path is not None
            and path.exists()
        ):
            for number, line in self.text.numbered():
                yield f'{path}:{number}', line
            return
        for number, line in enumerate(self.data, start=1):
            yield f'markers:{number}', line

    @property
    def warnings(self) -> list[str]:
        """
        Returns the problems with the lines as written. A line that does not parse is reported.
        So is a directory written without its trailing '/', and a directory marker that names
        a directory asset. When `.markers` was imported, each anchored line is reported too,
        since it resolves against this project.

        >>> trail.markers.warnings
        ["/tmp/tmpp_bl05lq/.markers:4: Not a marker: '!'"]
        """
        project = self.project
        tracked = {
            asset.path
            for asset in self._trail.assets
            if asset.is_directory
        }
        out = []
        for where, line in self._numbered():
            try:
                base, pattern = parse(line, project)
            except ValueError as error:
                out.append(f'{where}: {error}')
                continue
            if (
                base is not None
                and not pattern
                and (
                    base in tracked
                    or any(
                        container(path)
                        for path in (base, *base.parents)
                    )
                )
            ):
                out.append(
                    f"{where}: '{line}' names a directory asset, which is never walked; "
                    f"it is left out"
                )
            if slashless(line, project) is not None:
                out.append(
                    f"{where}: '{line}' names a directory without its trailing '/', so it "
                    f"tracks everything under it; did you mean '{line}/'?"
                )
            if (
                self.imported
                and base is not None
                and line != './'
            ):
                out.append(f"{where}: '{line}' was imported and resolves against {project}")
        return out

    def __repr__(self) -> str:
        """Lists the lines in file order."""
        return lines_repr(type(self).__name__, self.data)
