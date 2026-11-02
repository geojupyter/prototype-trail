from __future__ import annotations

from collections.abc import Iterator, Sequence
from functools import cached_property
from textwrap import dedent
from typing import TYPE_CHECKING, Final

from prompt_toolkit.completion import CompleteEvent, Completion, PathCompleter
from prompt_toolkit.document import Document

from trail.cli.command import FORCE, Command, Listing
from trail.cli.node import Node
from trail.rule import Rule

if TYPE_CHECKING:
    from trail.asset import Assets
    from trail.cli.console import Console
    from trail.dir import Dirs
    from trail.event import Events
    from trail.offtrail import OffTrail
    from trail.ontrail import OnTrail
    from trail.rule import Rules

# the key bindings listed by `help`
KEYS: Final[tuple[tuple[str, str], ...]] = (
    ("enter", "run the command"),
    ("tab", "complete a command or a path"),
    ("up / down", "command history"),
    ("ctrl-l", "empty the terminal, scrollback and all; nothing tracked is touched"),
    ("ctrl-c", "quit"),
)

# printed by `help` after the key bindings, one row per line
NOTES: Final = dedent(
    """\
    'ontrail data/*.csv' tracks the csv files in data now, while editing .ontrail
    to include 'data/*.csv' tracks them automatically when they appear
    tracking a directory tracks it whole, as one asset
    a dirasset such as a .zarr store is added without its '/', so 'ontrail data/cube.zarr'
    tracks it whole, and the reads and writes inside it are logged as one access
    .ontrail and .offtrail are text files; edit them directly to change rules
    .offtrail excludes paths from automatic tracking before .ontrail; it starts with '.*', which
    skips dotfiles and hidden folders
    the feed is ordinary output, so scrolling, selecting and copying are the
    terminal's own and work as they do anywhere else
    a listing reads what follows it as a selection: every term names its field, terms
    side by side all have to hold, 'or' takes either side, and a slice cuts what the terms
    before it left, so 'events (src_path=a.csv or src_path=b.csv) -5:' is the last five
    records of either file
    every listing takes 'clear': 'assets clear -f' untracks what it lists and
    records the removals, while 'events clear -f' discards the log those were kept in
    'clear -f' on its own does both, and ctrl-l empties the terminal instead
    'restart' reopens the project in a new process, which is how an edit to the
    console's own source takes effect without losing what was recorded
    """
)

# the narrowest `help` will make its usage column, whatever it is listing
HELP_WIDTH: Final = 20


class RulesCommand(Command):
    """
    Base of `ontrail` and `offtrail`. Alone, the command lists the rules of its file, each with its
    kind and the Dir it belongs to, then any warnings about them. Paths change tracked assets.
    """

    @property
    def rules(self) -> Rules:
        """Returns the rules the command lists."""
        raise NotImplementedError

    def complete(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        yield from self.complete_paths(document, complete_event)

    def complete_paths(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        raise NotImplementedError

    def call(self, arguments: Sequence[str]) -> None:
        raise NotImplementedError

    def __call__(self, arguments: Sequence[str]) -> None:
        """List rules with no arguments. Paths or patterns change tracked assets."""
        if not arguments:
            self.show()
            return
        self.call(arguments)

    def show(self) -> None:
        """Print a row for each rule of the file, then the warnings about them."""
        rules = self.rules
        if not rules:
            self._feed.info(f"{rules.filename}: none; edit {rules.filename} to add rules")
        project = rules.project
        rows = []
        for rule in rules:
            try:
                base, _ = Rule(rule).parse(project)
            except ValueError:
                owner = ""
            else:
                if base is None:
                    owner = "every root"
                elif base == project:
                    owner = "./"
                else:
                    owner = self.display(base, directory=True)
            rows.append((rule, Rule(rule).kind(project), owner))
        width = max(
            (len(rule) for rule, _, _ in rows),
            default=0,
        )
        kinds = max(
            (len(described) for _, described, _ in rows),
            default=0,
        )
        for rule, described, owner in rows:
            if described == "invalid":
                style = "class:error"
            else:
                style = "class:kind"
            row = [
                (style, f"  {rule:<{width}}  "),
                ("class:info", f"{described:<{kinds}}  "),
                ("", owner),
            ]
            self._feed.append(row)
        for warning in rules.warnings:
            self._feed.error(warning)


class OnTrailCommand(RulesCommand):
    """
    List the rules of `.ontrail`, or track paths now. `ontrail` alone lists each rule with its
    kind and the Dir it belongs to, then any warnings about them. Paths and patterns are tracked
    immediately without editing `.ontrail`.

    > ontrail
      ./          root                        ./
      *.csv       root scope                  every root
      data/*.png  dir scope · not subfolders  data/
    """

    name = "ontrail"
    usage = "ontrail [PATH...]"
    summary = "track paths now; list .ontrail rules"

    @property
    def rules(self) -> OnTrail:
        """Returns the Trail's `.ontrail`."""
        return self._trail.ontrail

    def call(self, arguments: Sequence[str]) -> None:
        """Track each path, skipping the ones already tracked. Errors are printed to the feed."""
        trail = self._trail
        for path in self.paths(arguments):
            if path in trail.assets:
                self._feed.info(f"already tracked: {self.display(path)}")
                continue
            try:
                trail.ontrail(path)
            except (OSError, ValueError) as error:
                self._feed.error(f"{self.display(path)}: {error}")

    @cached_property
    def path_completer(self) -> PathCompleter:
        """Returns a path completer that reads relative paths from the project root."""
        return PathCompleter(
            expanduser=True,
            get_paths=lambda: [str(self._console.root)],
        )

    def complete_paths(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """
        Completes the word under the cursor as a path. Only that word is passed to the path
        completer, so the command name is not read as part of the path.
        """
        word = self.word(document)
        yield from self.path_completer.get_completions(
            Document(word, len(word)),
            complete_event,
        )


class OffTrailCommand(RulesCommand):
    """
    List the rules of `.offtrail`, or stop tracking paths. `.offtrail` excludes paths from automatic
    tracking before `.ontrail`. Paths and patterns are removed from tracking without editing `.offtrail`.
    """

    name = "offtrail"
    usage = "offtrail [PATH...]"
    summary = "stop tracking paths; list .offtrail rules"

    @property
    def rules(self) -> OffTrail:
        """Returns the Trail's `.offtrail`."""
        return self._trail.offtrail

    def call(self, arguments: Sequence[str]) -> None:
        """Untrack each tracked path the arguments name, and report the ones not tracked."""
        trail = self._trail
        for path in self.paths(arguments, tracked=True):
            if path not in trail.assets:
                self._feed.error(f"not tracked: {self.display(path)}")
                continue
            trail.offtrail(path)

    def complete_paths(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        """Completes only tracked paths, since only those can be untracked."""
        word = self.word(document)
        yield from self.offer(word)


class AssetsCommand(Listing):
    """List the tracked assets, files and directories alike."""

    name = "assets"
    usage = "assets [EXPRESSION]"
    summary = "the tracked files, sliced or filtered"

    @property
    def collection(self) -> Assets:
        """Returns the Trail's assets."""
        return self._trail.assets


class DirsCommand(Listing):
    """
    List the directories `.ontrail` names. A Dir exists as long as a rule names it. `dirs clear` is
    refused. `ontrail remove` removes a Dir.

    > dirs
    dirs (2 of 2)
    0. Dir
        id: 04e15251538241309989553fb4672653
        path: .
        root: True
    1. Dir
        id: 3a654b91eb2047b3a2718d7f1d6a03e1
        path: data
        root: False
        scopes: ./*.png
    """

    name = "dirs"
    usage = "dirs [EXPRESSION]"
    summary = "the directories .ontrail names, sliced or filtered"

    @property
    def collection(self) -> Dirs:
        """Returns the Trail's Dirs."""
        return self._trail.dirs

    def discard(self, arguments: Sequence[str]) -> None:
        """Refuse to clear the Dirs, since they come from `.ontrail`."""
        message = "dirs: a Dir comes from .ontrail, so edit .ontrail to remove its rule"
        self._feed.error(message)


class EventsCommand(Listing):
    """
    List the recorded events. `events clear` discards the log. Tracked assets stay tracked for the
    rest of this session. The next session starts with only the assets `.ontrail` takes, since
    the log that recorded the others is gone.
    """

    name = "events"
    usage = "events [EXPRESSION]"
    summary = "the recorded events, sliced or filtered"

    @property
    def collection(self) -> Events:
        return self._trail.events


class ClearCommand(Command):
    """
    Clear the whole project record. Same as `assets clear` then `events clear`. Without `-f`, it
    prints what would be cleared and does nothing. `.ontrail` is not changed.
    """

    name = "clear"
    usage = "clear [-f]"
    summary = "discard the project record: untrack everything, then empty the log"

    def __call__(self, arguments: Sequence[str]) -> None:
        """Untrack every asset and empty the log, once `-f` confirms it."""
        trail = self._trail
        events = len(trail.events)
        assets = len(trail.assets)
        if not events and not assets:
            self._feed.info("clear: the project record is already empty")
            return
        # counted the way the header counts them, which is the way they are addressed
        subject = f"clear: this clears assets {assets}, events {events}"
        if not self.confirmed(arguments, "clear", subject):
            return
        trail.clear()
        self._feed.info(f"clear: cleared assets {assets}, events {events}")


class TriageCommand(Command):
    name = 'triage'
    usage = 'triage'
    summary = 'review pending events; accept or delete them'

    def __call__(self, arguments: Sequence[str]) -> None:
        if arguments:
            self._feed.error(f'usage: {self.usage}')
            return
        console = self._console
        console.application.create_background_task(console.triage())


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
        usages = [
            len(command.usage)
            for command in listed
        ]
        bindings = [
            len(keys)
            for keys, _ in KEYS
        ]
        width = max(HELP_WIDTH, *usages, *bindings)
        feed.info("commands")
        for command in listed:
            row = [
                ("class:kind", f"  {command.usage:<{width}}  "),
                ("class:info", command.summary),
            ]
            feed.append(row)
        feed.info("keys")
        for keys, description in KEYS:
            row = [
                ("class:kind", f"  {keys:<{width}}  "),
                ("class:info", description),
            ]
            feed.append(row)
        for note in NOTES.splitlines():
            feed.info(note)


class RestartCommand(Command):
    """
    Reopen the project in a new interpreter with the same command line. Edits to the console's
    source take effect only after a restart, since a running session keeps the classes it imported.

    A session saved to PATH/.trail comes back with everything it recorded, except its command
    history. With `--nodir`, nothing is on disk. The log is lost, so the restart needs `-f`.
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
    The commands of one Console, built from every Command subclass in `Command.classes`. Defined
    in this module; importing Commands registers every command class.
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
        ['events', 'exit']
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
