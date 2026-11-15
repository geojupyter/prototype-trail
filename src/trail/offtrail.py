from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Sequence
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Final

from pathspec import PathSpec

from trail.event import RemoveAssetEvent
from trail.rule import Rule, Rules
from trail.util import PathLike

if TYPE_CHECKING:
    from trail.asset import Asset

PRESETS: Final = files('trail') / 'offtrail'
DEFAULT_PRESET: Final = 'default'


@cache
def compiled(
        rule: str,
        project: Path,
) -> tuple[Path | None, PathSpec | None]:
    """
    Returns the directory a rule of `.offtrail` is anchored to, and the pattern that matches below
    it, lowercased and without its '!'. The directory is None for a rule without a directory part.
    The pattern is None for a root such as `data/`, which takes everything below it. Raises
    ValueError for a line that is not a valid rule.
    """
    base, pattern = Rule(rule).parse(project)
    if (
        base is not None
        and not pattern
    ):
        return base, None
    positive = (
        pattern
        .removeprefix('!')
        .lower()
    )
    spec = PathSpec.from_lines('gitignore', [positive])
    return base, spec


def decide(
        path: Path,
        directory: bool,
        rules: Sequence[str],
        project: Path,
        roots: Collection[Path],
) -> str | None:
    anchor = next(
        (
            parent
            for parent in path.parents
            if parent in roots
        ),
        path.parent,
    )
    for rule in reversed(rules):
        try:
            base, spec = compiled(rule, project)
        except ValueError:
            continue
        if base is None:
            base = anchor
        if not path.is_relative_to(base):
            continue
        if spec is None:
            return rule
        if path == base:
            continue
        relative = (
            path
            .relative_to(base)
            .as_posix()
            .lower()
        )
        if directory:
            relative += '/'
        if spec.match_file(relative):
            return rule
    return None


class OffTrail(Rules):
    """
    The rules of `.offtrail`, which exclude paths from automatic tracking before `.ontrail`. A
    path is decided here first: the last rule that matches it decides, and a rule that starts with
    '!' takes the path back. Rules are written as in `.ontrail`. A project without the file gets
    the default preset, so a deleted file comes back. Empty it to ignore nothing. Calling it removes
    assets without editing the rules.
    """

    filename = '.offtrail'
    presets = PRESETS

    def __call__(self, *paths: PathLike) -> Asset | list[Asset]:
        """
        Stop tracking each path. Each removal is logged, and the path goes into
        `trail.entries.untracked`, so `.ontrail` does not take it again. One path returns its
        asset. Several paths, or a path that is not tracked, return a list.
        """
        trail = self._trail
        requested = dict.fromkeys(
            Path(path)
            .expanduser()
            .resolve()
            for path in paths
        )
        untracked = []
        for path in requested:
            asset = trail.assets.get(path)
            if asset is None:
                continue
            event = RemoveAssetEvent(src_path=str(path))
            result = event.apply(trail)
            if result is not None:
                trail.events.append(event)
                untracked.append(result)
                # a Dir may be named at the path the directory asset held
                if result.is_directory:
                    trail.dirs.refresh()
        if untracked:
            trail.watchdog.session.reconcile()
        if len(paths) == 1 and untracked:
            out = untracked[0]
            return out
        return untracked

    def default(self) -> None:
        """
        Copy the default preset to `.offtrail` if the project has no such file. A Trail without a
        directory holds the default rules in memory.
        """
        if self.txt.path is None:
            text = (self.presets / f'{DEFAULT_PRESET}.txt').read_text(encoding='utf-8')
            self.data[:] = self.txt.rules(text.splitlines())
        else:
            self.txt.create(DEFAULT_PRESET)

    def add(
            self,
            *texts: str,
            cwd: PathLike | None = None,
    ) -> list[str]:
        """
        Add a rule to `.offtrail` for each text, made canonical as `OnTrail.add` makes it. A rule
        already in `.offtrail` is not added again. Assets that a new rule ignores stay tracked, and
        a warning says how many. `OffTrail.__call__` removes them. Returns the rules added.
        """
        rules = self._canonicalize(texts, cwd)
        project = self.project
        # every rule is checked before any is written
        for rule in rules:
            Rule(rule).parse(project)
        added = [
            rule
            for rule in dict.fromkeys(rules)
            if rule not in self.data
        ]
        if not added:
            return []
        self.data.extend(added)
        self._changed()
        decided = Counter(
            self.decide(asset.path, asset.is_directory)
            for asset in self._trail.assets
        )
        for rule in added:
            # an exclusion takes paths back, so it leaves nothing ignored
            if not rule.startswith('!'):
                self._left(f'adding {rule!r} to .offtrail', decided[rule])
        if any(
            rule.startswith('!')
            for rule in added
        ):
            self._trail.assets.scan()
        return added

    def remove(
            self,
            *texts: str,
            cwd: PathLike | None = None,
    ) -> list[str]:
        """
        Remove the rules of `.offtrail` that each text stands for, compared in canonical form, then
        track what `.ontrail` takes among the paths they ignored. Returns the rules removed.
        """
        removed = self._matching(texts, cwd)
        if not removed:
            return []
        self._discard(removed)
        self._trail.assets.scan()
        return removed

    def _changed(self) -> None:
        Rules._changed(self)
        self._trail.watchdog.session.reconcile()

    def decide(
            self,
            path: Path,
            directory: bool = False,
    ) -> str | None:
        """
        Returns the last rule that matches a path, or None. Case is ignored. `directory` says
        whether the path is a directory, which a rule ending in '/' needs.

        - A rule without a directory part, such as `.*`, matches at any depth below the deepest Dir
          above the path. The folders above that Dir never decide, so a Dir named inside a hidden
          folder keeps its files.
        - A rule with a directory part, such as `data/secrets/`, is anchored as in `.ontrail`.
        """
        out = decide(
            path=path,
            directory=directory,
            rules=self.data,
            project=self.project,
            roots=self._trail.dirs.path2entry,
        )
        return out

    def ignores(
            self,
            path: Path,
            directory: bool = False,
    ) -> bool:
        """Whether the rule that decides a path is not an exclusion. See `decide`."""
        rule = self.decide(path, directory)
        out = (
            rule is not None
            and not rule.startswith('!')
        )
        return out

    @property
    def warnings(self) -> list[str]:
        """Returns a warning for each line that does not parse."""
        project = self.project
        out = []
        for where, rule in self._numbered():
            try:
                Rule(rule).parse(project)
            except ValueError as error:
                out.append(f'{where}: {error}')
        return out
