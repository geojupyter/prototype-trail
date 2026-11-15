from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

from prompt_toolkit.completion import (
    CompleteEvent,
    Completer,
    Completion,
    PathCompleter,
    merge_completers,
)
from prompt_toolkit.document import Document

from trail.asset import DirAsset
from trail.ontrail import extensions
from trail.rule import MAGIC, Rule


class Paths(PathCompleter):
    """
    A PathCompleter that adds a trailing slash to directories, like a shell does. Without it,
    the next thing typed runs into the directory name. A dirasset gets no slash, since its rule
    names it without one.
    """

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """Yields the path completions, with a trailing slash on each directory but a dirasset."""
        for completion in super().get_completions(document, complete_event):
            name = completion.display_text
            if not name.endswith("/"):
                yield completion
            elif DirAsset.is_match(Path(name.rstrip("/"))):
                yield Completion(
                    completion.text,
                    completion.start_position,
                    display=name.rstrip("/"),
                )
            else:
                yield Completion(
                    f"{completion.text}/",
                    completion.start_position,
                    display=completion.display,
                )


class RuleCompleter(Completer):
    """
    Base of the completers for a rule of `.ontrail`. The word before the cursor is the rule
    being completed, and each candidate shows the kind of rule it would make.
    """

    def __init__(
        self,
        project: Callable[[], Path],
        cwd: Callable[[], Path],
    ) -> None:
        """
        `project` returns the directory the rules resolve against, and `cwd` the directory
        typed paths are read from. Both are called on every completion.
        """
        self.project = project
        self.cwd = cwd

    def kind(self, text: str) -> str:
        """Returns the kind of rule the text would make once canonical, or 'invalid'."""
        project = self.project()
        try:
            line = Rule(text).canonicalize(project, self.cwd())
        except (OSError, RuntimeError, ValueError):
            return "invalid"
        return Rule(line).kind(project)

    @staticmethod
    def word(document: Document) -> str:
        """Returns the word before the cursor, which is the rule being completed."""
        return document.get_word_before_cursor(WORD=True)


class RulePaths(RuleCompleter):
    """
    Completes the path in a rule, which is the part before the first wildcard. A leading '!'
    is set aside while completing and kept in the rule.

        ontrail add ./da<TAB>    data/    root
    """

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """
        Yields completions for a word that looks like a path: one starting with '/', '~' or
        '.', or holding a '/'. A word with a wildcard gets none, and neither does `.trail`,
        which can never be added to `.ontrail`.
        """
        word = self.word(document)
        body = word.removeprefix("!")
        pathlike = (
            body.startswith(("/", "~", "."))
            or "/" in body
        )
        if (
            MAGIC.search(body)
            or not pathlike
        ):
            return
        cwd = self.cwd()
        metadata = self.project() / ".trail"
        paths = Paths(
            expanduser=True,
            get_paths=lambda: [str(cwd)],
        )
        typed = Document(body, len(body))
        for completion in paths.get_completions(typed, complete_event):
            target = (cwd / Path(body + completion.text).expanduser()).resolve()
            if target.is_relative_to(metadata):
                continue
            yield Completion(
                completion.text,
                completion.start_position,
                display=completion.display,
                display_meta=self.kind(word + completion.text),
            )


class Extensions(RuleCompleter):
    """
    Completes a word that is not a path to `*.ext`, for each extension that a preset names and
    that starts like it. See `extensions`. A leading '!' is kept.

        ontrail add !*.t<TAB>    !*.tsv  !*.txt  !*.tif  !*.tiff    root scope · exclude
    """

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """Yields `*.ext` for each bundled extension the word is a start of."""
        word = self.word(document)
        negated = word.startswith("!")
        body = word.removeprefix("!")
        if (
            "/" in body
            or body.startswith("~")
        ):
            return
        stem = (
            body
            .removeprefix("*")
            .removeprefix(".")
            .lower()
        )
        if negated:
            prefix = "!"
        else:
            prefix = ""
        for extension in extensions():
            if extension.startswith(stem):
                line = f"{prefix}*.{extension}"
                yield Completion(
                    line,
                    -len(word),
                    display_meta=Rule(line).kind(self.project()),
                )


class Rows(RuleCompleter):
    """
    Completes to the rules already listed, so taking one back takes a few keys.

        ontrail remove da<TAB>    data/*.png    dir scope · not subfolders
    """

    def __init__(
        self,
        rows: Callable[[], Iterable[str]],
        project: Callable[[], Path],
        cwd: Callable[[], Path],
    ) -> None:
        """`rows` returns the rules listed now. See `RuleCompleter` for other arguments."""
        RuleCompleter.__init__(self, project, cwd)
        self.rows = rows

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """Yields each listed rule that starts with the word and is not the word itself."""
        word = self.word(document)
        project = self.project()
        for row in self.rows():
            if (
                row.startswith(word)
                and row != word
            ):
                yield Completion(
                    row,
                    -len(word),
                    display_meta=Rule(row).kind(project),
                )


def completer(
        rows: Callable[[], Iterable[str]],
        project: Callable[[], Path],
        cwd: Callable[[], Path],
) -> Completer:
    """
    Returns the completer for rules of `.ontrail`. It completes paths, the extensions of the
    presets as `*.ext`, and the rules in `rows`. Each candidate shows its kind as its
    `display_meta`.

        ontrail add cs<TAB>      *.csv    root scope
        ontrail add ./da<TAB>    data/    root
    """
    completers = [
        RulePaths(project, cwd),
        Extensions(project, cwd),
        Rows(rows, project, cwd),
    ]
    return merge_completers(completers, deduplicate=True)
