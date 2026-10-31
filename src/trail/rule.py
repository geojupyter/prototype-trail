from __future__ import annotations

import os
import re
import warnings
from collections.abc import Iterable, Iterator, Sequence
from functools import cached_property
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Final

from pathspec import PathSpec

from trail import fmt
from trail.asset import DirAsset
from trail.node import Node
from trail.util import PathLike

if TYPE_CHECKING:
    from trail.trail import Trail

MAGIC: Final = re.compile(r'[*?[]')
ANCHORS: Final = ('/', './', '../', '~/')


class Rule(str):
    __slots__ = ()

    def parse(self, project: Path) -> tuple[Path | None, str]:
        """
        Returns the directory a rule belongs to, and its matching pattern. The
        directory is None for a root scope. The pattern is empty for a root, such as `data/`. Raises
        ValueError for a line that is not a valid rule.

        >>> project: Path
        >>> parse('data/*.png', project)
        (PosixPath('/tmp/tmpp_bl05lq/data'), '/*.png')
        >>> parse('*.csv', project)
        (None, '*.csv')
        """
        line = self
        negated = line.startswith('!')
        body = line.removeprefix('!')
        if not body.strip():
            msg = f'Not a rule: {line!r}'
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
            line.validate()
            out = None, line
            return out
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
            wildcards = (
                i
                for i, part in enumerate(parts[:-1])
                if MAGIC.search(part)
            )
            index = next(wildcards, len(parts) - 1)
        base = (
            anchor
            .joinpath(*parts[:index])
            .resolve()
        )
        rest = parts[index:]
        if not rest:
            out = base, ''
            return out
        pattern = f'{prefix}/{"/".join(rest)}'
        if body.endswith('/'):
            pattern += '/'
        Rule(pattern).validate()
        out = base, pattern
        return out

    def validate(self) -> None:
        """
        Raises ValueError if pathspec refuses the pattern. A lone '!' and a trailing backslash are
        refused, for example.
        """
        pattern = self
        try:
            PathSpec.from_lines('gitignore', [pattern])
        except ValueError as error:
            msg = f'Not a valid pattern: {pattern!r} ({error})'
            raise ValueError(msg) from None

    @property
    def reaches_subfolders(self) -> bool:
        """
        Whether a dir scope pattern reaches below the directory it belongs to, through a '/' or a
        '**'.

        >>> reaches_subfolders('/*.png')
        False
        >>> reaches_subfolders('/**/*.tif')
        True
        """
        pattern = self
        out = (
            '/' in pattern.lstrip('!').strip('/')
            or '**' in pattern
        )
        return out

    def kind(self, project: Path) -> str:
        """
        Returns how a rule is read:
        - 'root'
        - 'root scope'
        - 'dir scope'
        - 'dir scope · not subfolders'
        - 'invalid', for a line that does not parse

        A scope that starts with '!' gets ' · exclude' after it.

        >>> project: Path
        >>> kind('data/*.png', project)
        'dir scope · not subfolders'
        >>> kind('!*.tmp', project)
        'root scope · exclude'
        """
        line = self
        try:
            base, pattern = line.parse(project)
        except ValueError:
            return 'invalid'
        if base is None:
            out = 'root scope'
        elif not pattern:
            return 'root'
        elif Rule(pattern).reaches_subfolders:
            out = 'dir scope'
        else:
            out = 'dir scope · not subfolders'
        if line.startswith('!'):
            out += ' · exclude'
        return out

    def ambiguous_directory(self, project: Path) -> Path | None:
        """
        Returns the directory that a line without wildcards names, if the line has no trailing '/'. Such
        a line tracks everything under the directory, of every type. A dirasset is left out, since its
        line has no '/'.

        >>> project: Path
        >>> slashless('data', project)
        PosixPath('/tmp/tmpp_bl05lq/data')
        >>> slashless('data/', project)
        >>> slashless('cube.zarr', project)
        """
        line = self
        if (
            line.startswith('!')
            or line.endswith('/')
            or MAGIC.search(line)
        ):
            return None
        try:
            base, pattern = line.parse(project)
        except ValueError:
            return None
        if base is None:
            target = project / line
        else:
            target = base / pattern.lstrip('/')
        if (
            target.is_dir()
            and not DirAsset.is_match(target)
        ):
            return target
        return None

    def canonicalize(
            self,
            project: Path,
            cwd: Path,
    ) -> Rule:
        """
        Returns the rule that typed text stands for. Relative paths are read from `cwd`
        and written relative to `project`.

        'csv', '.csv' and '**/*.csv' all become '*.csv'. An existing directory gets a trailing '/'. A
        dirasset such as `cube.zarr` does not, since its line tracks it whole. A path inside the project
        is written relative to it. A path under the home directory starts with '~/'. Any other path
        stays absolute. A leading '!' is kept.

        A single word that names an existing path is read as that path. Any other single word is read as
        an extension. './x' forces the path reading. '.x' or '*.x' forces the extension.

        >>> project: Path
        >>> canonical('**/*.csv', project, project)
        '*.csv'
        >>> canonical('notes.md', project, project)
        './notes.md'
        >>> canonical('!data', project, project)
        '!./data/'
        """
        text = self
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
            msg = f'Not a rule: {text!r}'
            raise ValueError(msg)
        if (
            '/' not in body
            and body not in ('.', '..', '~')
        ):
            if MAGIC.search(body):
                out = Rule(prefix + body)
                return out
            if body.startswith('.'):
                out = Rule(f'{prefix}*{body}')
                return out
            if not (cwd / body).exists():
                out = Rule(f'{prefix}*.{body}')
                return out
        out = Rule(prefix + Rule(body)._canonical_path(negated, project, cwd))
        return out

    def _canonical_path(
            self,
            negated: bool,
            project: Path,
            cwd: Path,
    ) -> str:
        """
        Returns the line for text that is read as a path. The part before the first wildcard is resolved
        from `cwd`. The rest is kept as typed. `negated` says whether the rule starts with '!'. The
        text no longer holds the '!'.

        >>> project: Path
        >>> canonical_path('data', False, project, project)
        'data/'
        >>> canonical_path('cube.zarr', False, project, project)
        './cube.zarr'
        """
        body = self
        trailing = body.endswith('/')
        parts = body.rstrip('/').split('/')
        wildcards = (
            i
            for i, part in enumerate(parts)
            if MAGIC.search(part)
        )
        index = next(wildcards, len(parts))
        literal = '/'.join(parts[:index])
        rest = parts[index:]
        if (
            body.startswith('/')
            and not literal
        ):
            literal = '/'
        base = (cwd / Path(literal or '.').expanduser()).resolve()
        # a dirasset is tracked whole, so its line names it without the '/' of a directory to watch
        bare = (
            not rest
            and base.is_dir()
            and not DirAsset.is_match(base)
        )
        directory = trailing or bare
        if base.is_relative_to(project):
            segments = [*base.relative_to(project).parts, *rest]
            if not segments:
                return './'
            line = '/'.join(segments)
            if directory:
                line += '/'
            root = (
                line.endswith('/')
                and not negated
                and not MAGIC.search(line)
            )
            alone = '/' in line.rstrip('/') or root
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


class TXT(Node):
    """
    The file of rules beside the trail directory, `.ontrail` or `.offtrail`. Every change
    rewrites it with `compose`. `compose` keeps the user's comments and blank lines.

    >>> trail: Trail
    >>> trail.ontrail.txt
    TXT
        path: /tmp/tmpp_bl05lq/.ontrail
        tail:
            ./
            *.csv
            data/*.png
    """

    _parent: Rules

    def __repr__(self) -> str:
        """Shows the path and the last lines of the file."""
        out = fmt.asset(type(self).__name__, self.path)
        return out

    @cached_property
    def path(self) -> Path | None:
        """The file beside the trail directory. None for a Trail without a directory."""
        path = self._trail.dir
        if path:
            out = path.parent / self._parent.filename
            return out
        return None

    def lines(self) -> list[str]:
        """Returns the lines of the file, comments included. A missing file has none."""
        path = self.path
        if (
            path is None
            or not path.exists()
        ):
            out = []
            return out
        out = (
            path
            .read_text(encoding='utf-8')
            .splitlines()
        )
        return out

    @staticmethod
    def line2rule(line: str) -> Rule | None:
        """Returns the rule a line says, stripped, or None for a blank line or a comment."""
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith('#')
        ):
            return None
        out = Rule(stripped)
        return out

    @classmethod
    def rules(cls, lines: Iterable[str]) -> list[Rule]:
        """Returns the rules among the lines of a file, stripped and in order."""
        out = []
        for line in lines:
            rule = cls.line2rule(line)
            if rule is not None:
                out.append(rule)
        return out

    def numbered(self) -> Iterator[tuple[int, Rule]]:
        """Yields each rule in the file with its line number."""
        for number, line in enumerate(self.lines(), start=1):
            rule = self.line2rule(line)
            if rule is not None:
                yield number, rule

    def read(self) -> None:
        """Load the rules of the file into its Rules."""
        self._parent.data[:] = self.rules(self.lines())

    def create(self, preset: os.PathLike[str] | str) -> None:
        """
        Create the file by copying one of its owner's presets. Does nothing if it already exists.

        preset:
            str:
                The name of a preset from the owning collection's preset directory.
            Path:
                A file of rules on disk.
        """
        path = self.path
        if (
            path is None
            or path.exists()
        ):
            return
        if isinstance(preset, os.PathLike):
            source = Path(preset).expanduser()
            if not source.is_file():
                msg = f'No file of rules at {source}'
                raise FileNotFoundError(msg)
            # its anchored lines were written for the project it came from
            self._parent.imported = True
        else:
            source = self._parent.presets / f'{preset}.txt'
            if not source.is_file():
                msg = f'No preset {preset!r}; available: {self._parent.available()}'
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

    def replace(self, rules: Sequence[str]) -> None:
        """Rewrite the file to hold `rules`, keeping its comments. See `compose`."""
        self.write(self.compose(self.lines(), rules))

    @classmethod
    def compose(
            cls,
            lines: Iterable[str],
            rules: Sequence[str],
    ) -> str:
        """
        Returns the text of a file that holds `rules`. It starts from `lines`, so comments and the
        rules that stay are kept as written and in order. A rule written twice stays twice, since a
        later copy can decide differently. Rules not in `lines` go at the end.
        """
        wanted = set(rules)
        kept = []
        present = set()
        for line in lines:
            rule = cls.line2rule(line)
            if rule is None:
                kept.append(line)
            elif rule in wanted:
                kept.append(line)
                present.add(rule)
        kept.extend(
            rule
            for rule in dict.fromkeys(rules)
            if rule not in present
        )

        out = ''.join(
            f'{line}\n'
            for line in kept
        )
        return out


class Rules(
    Node,
    Sequence[Rule],
):
    """
    The rules of one file beside the trail directory, in file order: its lines without comments
    or blank lines. Every change is written through to the file. `OnTrail` and `OffTrail` hold
    inclusion and exclusion rules.
    """

    _parent: Trail
    # the name of the file in the project directory
    filename: ClassVar[str] = ''
    presets: ClassVar[Traversable]

    def __init__(self, parent: Trail | None = None) -> None:
        """
        Start with no rules. `TXT.read` loads them from the file. `imported` says whether the
        file was copied from a file of the user's own.
        """
        Node.__init__(self, parent)
        self.data: list[Rule] = []
        self.imported = False

    @classmethod
    def available(cls) -> list[str]:
        out = sorted(
            entry.name.removesuffix('.txt')
            for entry in cls.presets.iterdir()
            if entry.name.endswith('.txt')
        )
        return out

    @cached_property
    def txt(self) -> TXT:
        """Returns the TXT instance that wraps the text file."""
        out = TXT(self)
        return out

    @property
    def project(self) -> Path:
        """
        The directory that relative rules resolve against. It holds the file. A Trail without a
        directory uses the working directory.

        >>> self.project
        PosixPath('/tmp/tmpp_bl05lq')
        """
        directory = self._trail.dir
        if directory is None:
            out = Path.cwd().resolve()
            return out
        out = directory.parent
        return out

    def __getitem__(self, index: int) -> Rule:
        """Returns the line at a position."""
        out = self.data[index]
        return out

    def __len__(self) -> int:
        """The number of lines."""
        out = len(self.data)
        return out

    def __contains__(self, value: object) -> bool:
        """Whether a line is in the file, as written."""
        out = value in self.data
        return out

    def __iter__(self) -> Iterator[Rule]:
        """Iterates over the lines in file order."""
        out = iter(self.data)
        return out

    def __repr__(self) -> str:
        """Lists the rules in file order."""
        out = fmt.lines(type(self).__name__, self.data)
        return out

    def replace(self, rules: Iterable[str]) -> None:
        """Make the file hold exactly `rules`, keeping its comments."""
        self.data[:] = dict.fromkeys(
            Rule(rule)
            for rule in rules
        )
        self._changed()

    def _changed(self) -> None:
        """Write the rules through to the file. Every change calls it."""
        self.txt.replace(self.data)

    def _canonicalize(
            self,
            texts: Iterable[str],
            cwd: PathLike | None,
    ) -> list[Rule]:
        """
        Returns the rule each text stands for. Relative paths are read from `cwd`, the project
        directory by default, as the rules in the file are.
        """
        project = self.project
        if cwd is None:
            cwd = project
        cwd = Path(cwd).expanduser().resolve()
        out = [
            Rule(text).canonicalize(project, cwd)
            for text in texts
        ]
        return out

    def _matching(
            self,
            texts: Iterable[str],
            cwd: PathLike | None,
    ) -> list[Rule]:
        """
        Returns the rules in the file that each text stands for, compared in canonical form, in
        file order and each once.
        """
        project = self.project
        wanted = set(self._canonicalize(texts, cwd))
        matching = []
        for rule in self.data:
            try:
                form = rule.canonicalize(project, project)
            except ValueError:
                form = rule
            if (
                rule in wanted
                or form in wanted
            ):
                matching.append(rule)
        out = list(dict.fromkeys(matching))
        return out

    def _discard(self, rules: list[Rule]) -> None:
        """Remove every copy of each rule, and write the file."""
        self.data[:] = [
            rule
            for rule in self.data
            if rule not in rules
        ]
        self._changed()

    @staticmethod
    def _left(
            action: str,
            count: int,
    ) -> None:
        """
        Warn that `count` assets stay tracked after `action`, such as "removing '*.zarr'". Nothing
        is said for none.
        """
        if not count:
            return
        if count == 1:
            pronoun = 'it'
        else:
            pronoun = 'them'
        left = fmt.plural(count, 'asset')
        message = f'{action} leaves {left} tracked; offtrail removes {pronoun}'
        # the caller of the public method that called this
        warnings.warn(message, stacklevel=3)

    def _numbered(self) -> Iterator[tuple[str, Rule]]:
        """
        Yields each rule with where it is written, as 'path:number'. The rules of a Trail
        without a file are numbered by position.
        """
        path = self.txt.path
        if (
            path is not None
            and path.exists()
        ):
            for number, line in self.txt.numbered():
                yield f'{path}:{number}', line
            return
        for number, line in enumerate(self.data, start=1):
            yield f'{self.filename}:{number}', line
