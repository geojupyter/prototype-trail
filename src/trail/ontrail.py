from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from functools import cache, cached_property
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Final

from pathspec import PathSpec

from trail import fmt
from trail.asset import DirAsset
from trail.rule import TXT, Rule, Rules
from trail.util import PathLike

if TYPE_CHECKING:
    from trail.asset import Asset

PRESETS: Final = files('trail') / 'ontrail'
EXTENSION: Final = re.compile(r'\*\.([^*?[/]+)')


@cache
def extensions() -> tuple[str, ...]:
    """
    Returns the extensions that the `*.ext` rules of the presets name. Presets are read in the
    order `OnTrail.available` lists them. Rules are read in file order. Each extension is listed once.
    """
    found: dict[str, None] = {}
    for name in OnTrail.available():
        text = (PRESETS / f'{name}.txt').read_text(encoding='utf-8')
        for rule in TXT.rules(text.splitlines()):
            match = EXTENSION.fullmatch(rule)
            if match is not None:
                found[match.group(1).lower()] = None
    out = tuple(found)
    return out



class Scopes(Sequence[str]):
    """
    The scopes of one directory, or the root scopes, as written in `.ontrail`. Each line is
    held beside the pattern compiled from it. Within one Scopes, the last matching line wins.

    >>> trail: Trail
    >>> trail.scopes
    Scopes (1)
        *.csv
    """

    def __init__(
            self,
            lines: Sequence[str] = (),
            patterns: Sequence[str] = (),
    ) -> None:
        """Hold each line as written in `.ontrail`, beside the pattern compiled from it."""
        self.lines = tuple(
            Rule(line)
            for line in lines
        )
        self.patterns = tuple(
            Rule(pattern)
            for pattern in patterns
        )

    def __getitem__(self, index: int) -> str:
        """Returns the line at a position, as written in `.ontrail`."""
        return self.lines[index]

    def __len__(self) -> int:
        """The number of lines."""
        return len(self.lines)

    def __repr__(self) -> str:
        """Lists the lines as written in `.ontrail`."""
        return fmt.lines(type(self).__name__, self.lines)

    @cached_property
    def spec(self) -> PathSpec:
        """
        The patterns compiled for matching. They are lowercased only here, so the lines keep
        the user's spelling.

        >>> self.spec
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

        >>> self.relative
        ('./*.png',)
        """
        out = [
            f'!.{pattern[1:]}'
            if pattern.startswith('!')
            else f'.{pattern}'
            for pattern in self.patterns
        ]

        return tuple(out)

    @cached_property
    def is_recursive(self) -> bool:
        """
        Whether any pattern reaches below the directory it belongs to. See `Rule.reaches_subfolders`.

        >>> self.is_recursive
        False
        """
        return any(
            pattern.reaches_subfolders
            for pattern in self.patterns
        )

    def decide(self, relative: str) -> str | None:
        """
        Returns the line that decides a relative path, or None if none matches. Case is
        ignored.

        >>> self.decide('sub/X.CSV')
        '*.csv'
        """
        index = self.spec.check_file(relative.lower()).index
        if index is None:
            return None
        return self.lines[index]



class OnTrail(Rules):
    """
    The rules of the `.ontrail` file. `trail.dirs` and `trail.scopes` are built from them and
    are rebuilt after every change. Calling it tracks paths without editing the rules.

    >>> trail: Trail
    >>> trail.ontrail
    OnTrail (3)
        ./
        *.csv
        data/*.png
    """

    filename = '.ontrail'
    presets = PRESETS

    def __call__(self, *paths: PathLike) -> Asset | list[Asset]:
        """
        Track each path as an asset. A directory is tracked whole, as a directory asset, and its
        contents are never walked. One path returns its asset. Several paths return a list.

        >>> project: Path
        >>> self(project / 'notes.md')
        Asset
            id: 80438f85c910427d9001617c47b7507b
            path: /tmp/tmpp_bl05lq/notes.md
            st_size: 2 B
            st_mtime: 2026-10-02 23:39:37
        """
        requested = dict.fromkeys(
            Path(path)
            .expanduser()
            .resolve()
            for path in paths
        )
        tracked = self._trail.assets.add(requested)
        if len(paths) == 1:
            out = tracked[0]
            return out
        return tracked

    def add(
            self,
            *texts: str,
            cwd: PathLike | None = None,
    ) -> list[Rule]:
        """
        Add a rule to `.ontrail` for each text, then track the files the new rules take now. Each
        text is made canonical first, so 'csv' adds '*.csv'. Relative paths are read from `cwd`,
        the project directory by default, as the rules of `.ontrail` are. A rule already in
        `.ontrail` is not added again. Returns the rules added.

        >>> self.add('zarr')
        ['*.zarr']
        """
        rules = self._canonicalize(texts, cwd)
        project = self.project
        # every rule is checked before any is written
        bases = {
            rule: Rule(rule).parse(project)[0]
            for rule in rules
        }
        added = [
            rule
            for rule in dict.fromkeys(rules)
            if rule not in self.data
        ]
        if not added:
            return []
        self.data.extend(added)
        self._changed()
        reached = [
            bases[rule]
            for rule in added
        ]
        assets = self._trail.assets
        # a root scope applies beneath every root, so every Dir is walked
        if None in reached:
            assets.scan()
        else:
            assets.scan(*reached)
        return added

    def remove(
            self,
            *texts: str,
            cwd: PathLike | None = None,
    ) -> list[Rule]:
        """
        Remove the rules of `.ontrail` that each text stands for, compared in canonical form.
        Relative paths are read as `add` reads them. Every copy of a rule is removed. Assets that
        a removed rule took stay tracked, and a warning says how many. `OffTrail.__call__` removes
        them. Returns the rules removed.

        >>> self.remove('.zarr')
        <stdin>:1: UserWarning: removing '*.zarr' leaves 1 asset tracked; offtrail removes it
        ['*.zarr']
        """
        removed = self._matching(texts, cwd)
        if not removed:
            return []
        matched = self._matched(removed)
        self._discard(removed)
        for rule in removed:
            self._left(f'removing {rule!r}', matched[rule])
        return removed

    def _changed(self) -> None:
        """
        Write the rules through to the file, bring `trail.dirs` in line with them, and reconcile
        the watchdog's session with the Dirs. Every change calls it.
        """
        Rules._changed(self)
        trail = self._trail
        trail.dirs.refresh()
        trail.watchdog.session.reconcile()

    def _matched(self, rules: list[str]) -> Counter[str]:
        """
        Returns how many tracked assets each rule accounts for. A scope counts the assets it
        decides. A root counts the assets beneath it.

        >>> self._matched(['./', '*.csv'])
        Counter({'./': 4, '*.csv': 1})
        """
        trail = self._trail
        project = self.project
        assets = trail.assets
        decided = Counter(
            trail.dirs.match(asset.path)
            for asset in assets
        )
        out: Counter[str] = Counter()
        for rule in rules:
            try:
                base, pattern = Rule(rule).parse(project)
            except ValueError:
                continue
            if (
                base is not None
                and not pattern
            ):
                out[rule] = sum(
                    1
                    for asset in assets
                    if asset.path.is_relative_to(base)
                )
            else:
                out[rule] = decided[rule]
        return out

    @property
    def warnings(self) -> list[str]:
        """
        Returns the problems with the rules as written. These are reported:
        - A line that does not parse.
        - A directory written without its trailing '/'.
        - A root that names a dirasset or a directory inside one.
        - Each rule with a directory part other than './', if `.ontrail` was imported. It resolves
          against this project.

        >>> self.warnings
        ["/tmp/tmpp_bl05lq/.ontrail:4: Not a rule: '!'"]
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
                base, pattern = Rule(line).parse(project)
            except ValueError as error:
                out.append(f'{where}: {error}')
                continue
            names_asset = False
            if (
                base is not None
                and not pattern
            ):
                lineage = (base, *base.parents)
                names_asset = any(
                    path in tracked
                    or DirAsset.is_match(path)
                    for path in lineage
                )
            if names_asset:
                out.append(
                    f"{where}: '{line}' names a directory asset, which is never walked; "
                    f"it is left out"
                )
            if Rule(line).ambiguous_directory(project) is not None:
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
