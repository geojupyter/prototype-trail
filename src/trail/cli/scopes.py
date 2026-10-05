from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Final

from prompt_toolkit.completion import (
    CompleteEvent,
    Completer,
    Completion,
    PathCompleter,
    merge_completers,
)
from prompt_toolkit.document import Document

from trail.markers import MAGIC, canonical, kind

# offered as `*.ext` alongside the lines already in `.markers`
COMMON: Final[tuple[str, ...]] = (
    "csv",
    "tsv",
    "txt",
    "json",
    "geojson",
    "gpkg",
    "shp",
    "parquet",
    "feather",
    "tif",
    "tiff",
    "nc",
    "h5",
    "zarr",
    "xlsx",
    "pkl",
    "npy",
    "png",
    "jpg",
)


class Paths(PathCompleter):
    """
    A PathCompleter that adds a trailing slash to directories, like a shell does. Without it,
    the next thing typed runs into the directory name.
    """

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """Yields the path completions, with a trailing slash on each directory."""
        for completion in super().get_completions(document, complete_event):
            if completion.display_text.endswith("/"):
                yield Completion(
                    f"{completion.text}/",
                    completion.start_position,
                    display=completion.display,
                )
            else:
                yield completion


class Scope(Completer):
    """
    Base of the completers for a line of `.markers`. The word before the cursor is the line
    being completed, and each candidate shows the kind of line it would make.
    """

    def __init__(
        self,
        project: Callable[[], Path],
        cwd: Callable[[], Path],
    ) -> None:
        """
        `project` returns the directory the lines resolve against, and `cwd` the directory
        typed paths are read from. Both are called on every completion.
        """
        self.project = project
        self.cwd = cwd

    def kind(self, text: str) -> str:
        """Returns the kind of line the text would make once canonical, or 'invalid'."""
        project = self.project()
        try:
            line = canonical(text, project, self.cwd())
        except (OSError, RuntimeError, ValueError):
            return "invalid"
        return kind(line, project)

    @staticmethod
    def word(document: Document) -> str:
        """Returns the word before the cursor, which is the line being completed."""
        return document.get_word_before_cursor(WORD=True)


class ScopePaths(Scope):
    """
    Completes the path in a line, which is the part before the first wildcard. A leading '!'
    is set aside while completing and kept in the line.

        mark ./da<TAB>    data/    root
    """

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """
        Yields completions for a word that looks like a path: one starting with '/', '~' or
        '.', or holding a '/'. A word with a wildcard gets none, and neither does `.trail`,
        which can never be marked.
        """
        word = self.word(document)
        body = word.removeprefix("!")
        if (
            MAGIC.search(body)
            or not (
                body.startswith(("/", "~", "."))
                or "/" in body
            )
        ):
            return
        cwd = self.cwd()
        metadata = self.project() / ".trail"
        paths = Paths(
            expanduser=True,
            get_paths=lambda: [str(cwd)],
        )
        for completion in paths.get_completions(Document(body, len(body)), complete_event):
            target = (cwd / Path(body + completion.text).expanduser()).resolve()
            if target.is_relative_to(metadata):
                continue
            yield Completion(
                completion.text,
                completion.start_position,
                display=completion.display,
                display_meta=self.kind(word + completion.text),
            )


class Extensions(Scope):
    """
    Completes a word that is not a path to `*.ext`, for each extension in `COMMON` that starts
    like it. A leading '!' is kept.

        mark !*.t<TAB>    !*.tsv  !*.txt  !*.tif  !*.tiff    root scope · exclude
    """

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """Yields `*.ext` for each common extension the word is a start of."""
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
        for extension in COMMON:
            if extension.startswith(stem):
                line = f"{prefix}*.{extension}"
                yield Completion(
                    line,
                    -len(word),
                    display_meta=kind(line, self.project()),
                )


class Rows(Scope):
    """
    Completes to the lines already listed, so taking one back takes a few keys.

        unmark da<TAB>    data/*.png    dir scope · not subfolders
    """

    def __init__(
        self,
        rows: Callable[[], Iterable[str]],
        project: Callable[[], Path],
        cwd: Callable[[], Path],
    ) -> None:
        """`rows` returns the lines listed now. The other arguments are as for `Scope`."""
        Scope.__init__(self, project, cwd)
        self.rows = rows

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """Yields each listed line that starts with the word and is not the word itself."""
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
                    display_meta=kind(row, project),
                )


def completer(
        rows: Callable[[], Iterable[str]],
        project: Callable[[], Path],
        cwd: Callable[[], Path],
) -> Completer:
    """
    Returns the completer for lines of `.markers`. It completes paths, the common extensions
    as `*.ext`, and the lines in `rows`. Each candidate shows its kind as its `display_meta`.

        mark cs<TAB>      *.csv    root scope
        mark ./da<TAB>    data/    root
    """
    return merge_completers(
        [
            ScopePaths(project, cwd),
            Extensions(project, cwd),
            Rows(rows, project, cwd),
        ],
        deduplicate=True,
    )
