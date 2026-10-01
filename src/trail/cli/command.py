from __future__ import annotations

import shlex
from collections.abc import Iterator, Sequence
from fnmatch import fnmatch
from glob import iglob
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Self

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document

from trail.cli.node import Node
from trail.dir import Dir
from trail.select import SLICE, Select, Selectable

if TYPE_CHECKING:
    from trail.cli.commands import Commands
    from trail.cli.console import Console
    from trail.entry import Entries, Entry
    from trail.event import Events

# the characters that make a command argument a pattern rather than a path
MAGIC = ("*", "?", "[")
# what confirms an action that discards the record rather than adding to it
FORCE = ("-f", "--force")
# the action that empties a listing in place of listing it, when it is the first word
CLEAR = "clear"


class Command(Node):
    """
    A command in the command bar. Each subclass with a `name` registers itself in `classes`,
    so adding a command only takes writing the class. Dispatch, `help` and completion all read
    from `classes`.
    """

    _parent: Commands
    classes: ClassVar[dict[str, type[Command]]] = {}
    name: ClassVar[str] = ""
    aliases: ClassVar[tuple[str, ...]] = ()
    usage: ClassVar[str] = ""
    summary: ClassVar[str] = ""

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        if cls.name:
            cls.classes[cls.name] = cls

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.name!r})"

    def __call__(self, arguments: Sequence[str]) -> None:
        """Run the command with its arguments."""
        raise NotImplementedError

    def submit(self, text: str) -> None:
        """
        Split the text after the command name like a shell would, then run the command with
        it. Listings override this to read the text unsplit.
        """
        try:
            arguments = shlex.split(text)
        except ValueError as error:
            self._feed.error(f"unbalanced quotes: {error}")
            return
        self(arguments)

    def complete(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """Yields completions for the command's arguments. The default yields none."""
        return iter(())

    def display(
        self,
        path: str | Path,
        directory: bool = False,
    ) -> str:
        """Returns a path as the console shows it. Same as `Renderer.display`."""
        return self._renderer.display(path, directory)

    def word(self, document: Document) -> str:
        """Returns the word before the cursor, which is the argument being completed."""
        return document.get_word_before_cursor(WORD=True)

    def offer(self, word: str) -> Iterator[Completion]:
        """Yields completions from the tracked paths instead of the filesystem."""
        for entry in self._trail.entries:
            display = self.display(entry.path, isinstance(entry, Dir))
            if display.startswith(word):
                yield Completion(display, start_position=-len(word))

    def entry(self, token: str) -> Entry | None:
        """
        Returns the tracked entry an argument names, by id or by path. A leading `#` forces the
        token to be read as an id, in case a file name would match first.
        """
        entries = self._trail.entries
        if token.startswith("#"):
            return entries.id2entry.get(token[1:])
        found = entries.get(token)
        if found is not None:
            return found
        return entries.get(self.absolute(Path(token).expanduser()))

    def paths(
        self,
        arguments: Sequence[str],
        tracked: bool = False,
    ) -> list[Path]:
        """
        Resolve a command's arguments against the project root. A token with glob characters
        is matched against the filesystem, or against the tracked paths when `tracked` is True.
        Matching tracked paths lets a glob name files that were already deleted.
        """
        selected: dict[Path, None] = {}
        for token in arguments:
            expanded = Path(token).expanduser()
            if not any(char in token for char in MAGIC):
                selected[self.absolute(expanded)] = None
                continue
            if tracked:
                matches = self.tracked(expanded)
            else:
                matches = sorted(
                    self.absolute(Path(match))
                    for match in iglob(str(expanded), root_dir=self._console.root, recursive=True)
                )
            if not matches:
                self._feed.error(f"no matches: {token}")
            for match in matches:
                selected[match] = None
        return list(selected)

    def tracked(self, pattern: Path) -> list[Path]:
        """Returns the tracked paths that match a glob."""
        expanded = str(self.absolute(pattern))
        out = sorted(
            entry.path
            for entry in self._trail.entries
            if fnmatch(str(entry.path), expanded)
        )
        return out

    def confirmed(
        self,
        arguments: Sequence[str],
        command: str,
        subject: str,
    ) -> bool:
        """
        Returns True if a destructive command may go ahead. Without `-f`, it prints what would
        be discarded and returns False. Any other argument prints the usage and returns False.
        These are the only commands that remove anything from the log.
        """
        unexpected = [
            token
            for token in arguments
            if token not in FORCE
        ]
        if unexpected:
            self._feed.error(f"usage: {command} [-f]")
            return False
        if not arguments:
            self._feed.info(f"{subject}; '{command} -f' to confirm")
            return False
        return True

    def absolute(self, path: Path) -> Path:
        """Returns the path resolved against the project root."""
        if not path.is_absolute():
            path = self._console.root / path
        return path.resolve()


class Query[T: Selectable[Any], V](Select[T, V]):
    """
    The Select that listings use to parse their expressions. Path values are read against the
    project root, so a path copied from a printed record can be pasted back as it is.
    """

    _parent: T

    def __init__(
        self,
        parent: T,
        root: Path,
    ) -> None:
        Select.__init__(self, parent)
        self.root = root

    def within(self, collection: T) -> Self:
        """Returns a Query over another collection with the same root."""
        return type(self)(collection, self.root)

    def comparison(self, item: str) -> T:
        """Returns the records one term selects. Path values are resolved against the root."""
        match = self.parse(item)
        field = match["field"]
        value = match["value"]
        if (
            not field.endswith("path")
            or not value
        ):
            return super().comparison(item)
        # read against the project root, the way the console words a path, rather than against
        # the working directory; resolved once here rather than once for every record
        path = self.root / Path(value).expanduser()
        return self.where(field, match["operator"], str(path.resolve()))


class Listing(Command):
    """
    A command that lists one of the Trail's collections. Records are printed the same way the
    feed prints events. A subclass defines `collection` and `fields`. The text after the
    command name is parsed by `Query`, so a listing accepts the same expressions as
    `events.select(...)`:

        events                                    the last `default` records
        events :5    -5:    2:7                   a slice, counted the way Python counts
        events src_path=a.csv -5:                 the last five of that file's records
        events src_path=a.csv or src_path=b.csv   either file's records
        events cls=WatchdogEvent not event_type=opened

    Paths are read against the project root, so a path copied from a printed record can be
    pasted back. Without a slice, only the last `default` matches are shown.
    """

    # the fields offered as completions; a record can be selected on any other it holds
    fields: ClassVar[tuple[str, ...]] = ()
    # records shown when the expression does not say how many
    default = 20

    @property
    def collection(self) -> Entries | Events:
        """Returns the collection this command lists."""
        raise NotImplementedError

    def complete(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """Completes `clear` as the first word, and field names as `field=` terms."""
        word = self.word(document)
        if "=" in word:
            return
        tokens = document.text_before_cursor.split()
        first = len(tokens) <= 1 or (len(tokens) == 2 and word)
        # offered only where it is the action, and not where it would be a term
        if first and CLEAR.startswith(word):
            yield Completion(CLEAR, start_position=-len(word))
        # a term that opens a group is completed past its parentheses
        stem = word.lstrip("(")
        for name in self.fields:
            if name.startswith(stem):
                yield Completion(f"{name}=", start_position=-len(stem))

    def submit(self, text: str) -> None:
        """
        Print the records the expression selects, or clear the collection if the first word is
        `clear`.

        > dirs
        dirs (1 of 1)
        0. Dir
            id: 25699925cd634e51b7c3541aebd5c5f2
        """
        # taken unsplit, since once the console's split has dropped the quotes, the parentheses
        # of a quoted path can no longer be told from the ones grouping terms
        words = text.split()
        if (
            words
            and words[0] == CLEAR
        ):
            self.discard(words[1:])
            return
        collection = self.collection
        query = Query(collection, self._console.root)
        try:
            selection = query(text)
        except (TypeError, ValueError) as error:
            self._feed.error(f"{self.name}: {error}")
            return
        sliced = any(
            SLICE.fullmatch(token)
            for token in Select.lex(text)
        )
        if not sliced:
            selection = selection.select[-self.default:]
        if not selection:
            self._feed.info(f"{self.name}: nothing matched")
            return
        self._feed.info(f"{self.name} ({len(selection)} of {len(collection)})")
        # the position the whole collection gives a record rather than its place among what
        # matched, since that is the number it is addressed by
        positions = {
            key: position
            for position, key in enumerate(collection.ids)
        }
        renderer = self._renderer
        for key in selection.ids:
            rows = renderer.record(selection.data[key], positions[key])
            self._feed.extend(rows)

    def discard(self, arguments: Sequence[str]) -> None:
        """Clear the listed collection. Requires `-f` to confirm."""
        collection = self.collection
        total = len(collection)
        if not total:
            self._feed.info(f"{self.name}: already empty")
            return
        subject = f"{self.name}: this clears {total}"
        if not self.confirmed(arguments, f"{self.name} {CLEAR}", subject):
            return
        collection.clear()
        self._feed.info(f"{self.name}: cleared {total}")


class CommandCompleter(Completer):
    """Completes the command name, then passes the rest of the line to that command's `complete`."""

    def __init__(self, console: Console) -> None:
        self.console = console

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        head = document.text_before_cursor.lstrip()
        if " " not in head:
            for name in self.console.commands:
                if name.startswith(head):
                    yield Completion(name, start_position=-len(head))
            return
        command = self.console.commands.find(head.split(" ", 1)[0])
        if command is None:
            return
        yield from command.complete(document, complete_event)
