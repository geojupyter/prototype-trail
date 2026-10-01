from __future__ import annotations

from collections.abc import Iterator, Sequence
from functools import cached_property
from typing import TYPE_CHECKING, ClassVar, Final

from prompt_toolkit.completion import CompleteEvent, Completion, PathCompleter
from prompt_toolkit.document import Document

from trail.cli.command import FORCE, Command, Listing
from trail.cli.node import Node
from trail.entry import Entries

if TYPE_CHECKING:
    from trail.cli.console import Console
    from trail.event import Events

# the key bindings listed by `help`
KEYS: Final[tuple[tuple[str, str], ...]] = (
    ("enter", "run the command"),
    ("tab", "complete a command or a path"),
    ("up / down", "command history"),
    ("ctrl-l", "empty the terminal, scrollback and all; nothing tracked is touched"),
    ("ctrl-c", "quit"),
)

# printed by `help` after the key bindings, one row per line
NOTES: Final[tuple[str, ...]] = (
    "tracking a directory tracks the directory itself: files created inside it",
    "afterwards are picked up automatically, files already inside it are not",
    "the feed is ordinary output, so scrolling, selecting and copying are the",
    "terminal's own and work as they do anywhere else",
    "a listing reads what follows it as a selection: every term names its field, terms",
    "side by side all have to hold, 'or' takes either side, and a slice cuts what the terms",
    "before it left, so 'events (src_path=a.csv or src_path=b.csv) -5:' is the last five",
    "records of either file",
    "every listing takes 'clear': 'assets clear -f' offtrails what it lists and",
    "records the removals, while 'events clear -f' discards the log those were kept in",
    "'clear -f' on its own does both, and ctrl-l empties the terminal instead",
    "'restart' reopens the project in a new process, which is how an edit to the",
    "console's own source takes effect without losing what was recorded",
)

# the narrowest `help` will make its usage column, whatever it is listing
HELP_WIDTH: Final = 20


class TrackCommand(Command):
    """
    Track each path given. Globs are expanded against the filesystem from the project root.
    Paths that are already tracked are reported and skipped.
    """

    name = "track"
    usage = "track PATH..."
    summary = "track files or directories; globs are expanded"

    @cached_property
    def completer(self) -> PathCompleter:
        """Returns a path completer that reads relative paths from the project root."""
        return PathCompleter(
            expanduser=True,
            get_paths=lambda: [str(self._console.root)],
        )

    def complete(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """
        Completes the word under the cursor as a path. Only that word is passed to the path
        completer, so the command name is not read as part of the path.
        """
        word = self.word(document)
        yield from self.completer.get_completions(
            Document(word, len(word)),
            complete_event,
        )

    def __call__(self, arguments: Sequence[str]) -> None:
        if not arguments:
            self._feed.error(f"usage: {self.usage}")
            return
        trail = self._trail
        for path in self.paths(arguments):
            if path in trail.entries:
                self._feed.info(f"already tracked: {self.display(path)}")
                continue
            try:
                trail.track(path)
            except (OSError, ValueError) as error:
                self._feed.error(f"{self.display(path)}: {error}")


class UntrackCommand(Command):
    """
    Offtrail each path given. Globs are matched against the tracked paths, not the filesystem,
    so files that were already deleted can still be untracked.
    """

    name = "untrack"
    usage = "untrack PATH..."
    summary = "stop tracking; later events for the path are ignored"

    def complete(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """Completes only tracked paths, since only those can be untracked."""
        yield from self.offer(self.word(document))

    def __call__(self, arguments: Sequence[str]) -> None:
        if not arguments:
            self._feed.error(f"usage: {self.usage}")
            return
        trail = self._trail
        for path in self.paths(arguments, tracked=True):
            if path not in trail.entries:
                self._feed.error(f"not tracked: {self.display(path)}")
                continue
            trail.offtrail(path)


class AssetsCommand(Listing):
    """List the tracked assets. DirsCommand and EntriesCommand subclass it to reuse `fields`."""

    name = "assets"
    usage = "assets [EXPRESSION]"
    summary = "the tracked files, sliced or filtered"
    fields: ClassVar[tuple[str, ...]] = (
        "id",
        "path",
        "name",
        "cls",
    )

    @property
    def collection(self) -> Entries:
        return self._trail.assets


class DirsCommand(AssetsCommand):
    name = "dirs"
    usage = "dirs [EXPRESSION]"
    summary = "the tracked directories, sliced or filtered"

    @property
    def collection(self) -> Entries:
        return self._trail.dirs


class EntriesCommand(AssetsCommand):
    name = "entries"
    usage = "entries [EXPRESSION]"
    summary = "everything tracked, files and directories alike"

    @property
    def collection(self) -> Entries:
        return self._trail.entries


class EventsCommand(Listing):
    """
    List the recorded events. `events clear` discards the log. Tracked entries stay tracked for
    the rest of this session, but the next session starts with nothing tracked, because the log
    that recorded them is gone.
    """

    name = "events"
    usage = "events [EXPRESSION]"
    summary = "the recorded events, sliced or filtered"
    fields: ClassVar[tuple[str, ...]] = (
        "id",
        "entry",
        "cls",
        "timestamp",
        "src_path",
        "dest_path",
        "event_type",
        "st_size",
    )

    @property
    def collection(self) -> Events:
        return self._trail.events


class ClearCommand(Command):
    """
    Clear the whole project record, like `entries clear` followed by `events clear`. Without
    `-f`, it only prints what would be cleared.
    """

    name = "clear"
    usage = "clear [-f]"
    summary = "discard the project record: offtrail everything, then empty the log"

    def __call__(self, arguments: Sequence[str]) -> None:
        trail = self._trail
        events = len(trail.events)
        entries = len(trail.entries)
        if not events and not entries:
            self._feed.info("clear: the project record is already empty")
            return
        # counted the way the header counts them, which is the way they are addressed
        subject = f"clear: this clears entries {entries}, events {events}"
        if not self.confirmed(arguments, "clear", subject):
            return
        trail.clear()
        self._feed.info(f"clear: cleared entries {entries}, events {events}")


class HelpCommand(Command):
    """Print the commands, the key bindings in `KEYS`, and the notes in `NOTES`."""

    name = "help"
    usage = "help"
    summary = "this list"

    def __call__(self, arguments: Sequence[str]) -> None:
        feed = self._feed
        listed = self._console.commands.listed
        # the longest usage sets the column, so a command with a long one cannot run into its
        # own description the way a fixed width let it
        width = max(
            HELP_WIDTH,
            *(len(command.usage) for command in listed),
            *(len(keys) for keys, _ in KEYS),
        )
        feed.info("commands")
        for command in listed:
            feed.append(
                [
                    ("class:kind", f"  {command.usage:<{width}}  "),
                    ("class:info", command.summary),
                ]
            )
        feed.info("keys")
        for keys, description in KEYS:
            feed.append(
                [
                    ("class:kind", f"  {keys:<{width}}  "),
                    ("class:info", description),
                ]
            )
        for note in NOTES:
            feed.info(note)


class RestartCommand(Command):
    """
    Reopen the project in a new interpreter with the same command line. This is how edits to
    the console's source take effect, since a running session keeps the classes it imported.

    A session saved to PATH/.trail comes back with everything it recorded, but loses its
    command history. With `--nodir`, nothing is on disk, so the log is lost and the restart
    must be confirmed with `-f`.
    """

    name = "restart"
    usage = "restart [-f]"
    summary = "reopen this project in a new process, picking up edits to the console"

    def __call__(self, arguments: Sequence[str]) -> None:
        trail = self._trail
        if trail.dir is None:
            subject = f"restart: nodir, so this discards events {len(trail.events)}"
            if not self.confirmed(arguments, "restart", subject):
                return
        else:
            unexpected = [
                token
                for token in arguments
                if token not in FORCE
            ]
            if unexpected:
                self._feed.error(f"usage: {self.usage}")
                return
        self._console.restart()


class QuitCommand(Command):
    """Exit the session. `main` then prints how many events were recorded."""

    name = "quit"
    aliases = ("exit",)
    usage = "quit"
    summary = "leave the session"

    def __call__(self, arguments: Sequence[str]) -> None:
        self._console.application.exit()


class Commands(Node):
    """
    The commands of one Console, built from every Command subclass in `Command.classes`. It is
    defined in this module so that importing Commands also registers every command class.
    """

    _parent: Console

    @cached_property
    def data(self) -> dict[str, Command]:
        """Returns each command name and alias mapped to its Command instance."""
        out: dict[str, Command] = {}
        for command_type in Command.classes.values():
            command = command_type(self)
            out[command.name] = command
            for alias in command_type.aliases:
                out[alias] = command
        return out

    @property
    def listed(self) -> list[Command]:
        """Returns each command once, in definition order, even if it has aliases."""
        return list(dict.fromkeys(self.data.values()))

    def __getitem__(self, key: str) -> Command:
        return self.data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __repr__(self) -> str:
        return f"{type(self).__name__} ({', '.join(self.data)})"

    def matching(self, name: str) -> list[str]:
        """
        Returns the sorted command names that start with `name`.

        >>> self.matching('e')
        ['entries', 'events', 'exit']
        """
        out = sorted(
            key
            for key in self.data
            if key.startswith(name)
        )
        return out

    def find(self, name: str) -> Command | None:
        """
        Returns the command with this exact name, or the only command it is a prefix of.
        Returns None otherwise.
        """
        command = self.data.get(name)
        if command is not None:
            return command
        matches = {
            self.data[key]
            for key in self.matching(name)
        }
        if len(matches) == 1:
            return matches.pop()
        return None

    def resolve(self, name: str) -> Command | None:
        """Like `find`, but prints an error to the feed when no single command matches."""
        command = self.find(name)
        if command is not None:
            return command
        matches = self.matching(name)
        if matches:
            self._feed.error(f"ambiguous command {name!r}: {', '.join(matches)}")
        else:
            self._feed.error(f"unknown command {name!r}; try 'help'")
        return None
