from __future__ import annotations

import asyncio
import warnings
from collections.abc import AsyncIterator
from contextlib import suppress
from functools import cached_property
from pathlib import Path

from prompt_toolkit.application import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import (
    FormattedTextControl,
    HSplit,
    Layout,
    Window,
)
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.widgets import TextArea

from trail.cli.command import CommandCompleter
from trail.cli.commands import Commands
from trail.cli.feed import Feed
from trail.cli.node import Node
from trail.cli.render import Renderer
from trail.cli.theme import PROMPT, STYLE
from trail.event import Event
from trail.trail import Trail


class Console(Node):
    """
    The interactive session. Events the watchdog records are printed to the feed as they happen. The
    command bar at the bottom runs commands such as `track` and `events`.

    prompt_toolkit draws only the header and the command bar. The feed above them is ordinary
    terminal output, so scrollback, text selection and the scrollbar work as usual. The alternate
    screen is not used, since it would disable all three.
    """

    _parent: Trail
    # redraws arriving within this interval are coalesced into one
    interval = 0.05

    def __init__(
        self,
        trail: Trail,
        root: Path,
        returning: bool = False,
    ) -> None:
        Node.__init__(self, trail)
        self.root = root
        # whether the Trail existed before this session, so the banner welcomes the user back
        self.returning = returning
        self.restarting = False

    @cached_property
    def run(self) -> Run:
        """
        Returns a Run instance that runs the live session. It prints the banner, then prints new
        events as the watchdog records them until the application exits. Events from earlier
        sessions are not printed. `events` lists them. Lines submitted in the command bar also go
        through it.

        $ trail
        🍃🍄🌲 Welcome back to Trail! 🌲🍄🍃

        trail #1ff756ca11b54b3994c7b26bbda99c0a on ~/project
        assets 2  dirs 1  events 14
        type 'help' for help
        """
        return Run(self)

    @cached_property
    def feed(self) -> Feed:
        """
        Returns a Feed instance that prints rows to the terminal above the command bar. A row is an
        echoed command, an info or error message, or a rendered record. Each row is printed once and
        never redrawn. Only the last `limit` rows are kept in memory.

        > events -2: st_size=0
        events (1 of 14)
        13. AddAssetEvent
            id: 815694f873604da5b63cd4c65900ccf4
        """
        return Feed(self)

    @cached_property
    def renderer(self) -> Renderer:
        """
        Returns a Renderer instance that turns events and assets into rows for the feed. A record is
        a heading with its position and class name, then one line per field. Paths under the project
        root are shown relative to it. The Renderer also builds the header above the command bar.

        4. WatchdogEvent
            id: d1e59e9d9c6041978d9b0cd8b0179abf
            timestamp: 19:28:05.679
            src_path: a.csv

         trail #1ff756ca11b54b3994c7b26bbda99c0a  ~/project  assets 2  dirs 1  events 14  watching
        """
        return Renderer(self)

    @cached_property
    def commands(self) -> Commands:
        """
        Returns a Commands instance that maps each command name and alias to its Command. It is
        built from every subclass in `Command.classes`. Any prefix that matches only one command
        calls it.

        >>> self.commands
        Commands (track, offtrail, mark, unmark, markers, assets, dirs, events, clear, help, ...)
        >>> self.commands.find('ev')
        EventsCommand('events')
        """
        return Commands(self)

    @cached_property
    def completer(self) -> CommandCompleter:
        """
        Returns a CommandCompleter that handles <TAB> completion in the command bar. The first word
        is completed against the command names. The rest of the line goes to that command's
        `complete`. For example, `track` completes filesystem paths, `offtrail` completes tracked
        paths, `mark` completes paths and extensions, and listings complete field names.
        # no: should be bullet points for tarck, offtrail, mark, listings

        CommandCompleter is a prompt_toolkit Completer, not a Node, so it holds the console
        directly.

        e<TAB>             events, exit
        events src<TAB>    src_path=
        """
        return CommandCompleter(self)

    @cached_property
    def input(self) -> TextArea:
        """
        Returns the command bar, a single-line TextArea with in-memory history. Completion only
        runs when <TAB> is pressed. Pressing <ENTER> passes the line to `run.accept`.
        """
        return TextArea(
            height=1,
            prompt=PROMPT,
            multiline=False,
            wrap_lines=False,
            history=InMemoryHistory(),
            completer=self.completer,
            complete_while_typing=False,
            accept_handler=self.run.accept,
        )

    @cached_property
    def bindings(self) -> KeyBindings:
        """
        Returns the console's key bindings. Ctrl-C quits, and so does Ctrl-D when the command
        bar is empty. Ctrl-L clears the terminal. A paste is flattened onto one line by
        `pasted`.
        """
        bindings = KeyBindings()
        empty = Condition(lambda: not self.input.text)

        @bindings.add("c-c")
        @bindings.add("c-d", filter=empty)
        def _quit(event: KeyPressEvent) -> None:
            event.app.exit()

        @bindings.add(Keys.BracketedPaste)
        def _paste(event: KeyPressEvent) -> None:
            event.current_buffer.insert_text(self.pasted(event.data))

        @bindings.add("c-l")
        def _clear(event: KeyPressEvent) -> None:
            self.clear()

        return bindings

    def clear(self) -> None:
        """
        Empty the terminal and its scrollback. Nothing tracked is changed. The feed drops its rows
        too, so it still matches the screen.
        """
        self.feed.clear()
        renderer = self.application.renderer
        renderer.clear()
        # the renderer erases the screen but not the scrollback behind it, and the scrollback
        # is where everything the console printed has gone
        output = self.application.output
        output.write_raw("\x1b[3J")
        output.flush()

    def restart(self) -> None:
        """
        Exit the session and mark it for a restart.

        A running session cannot pick up edits to its own source, since its classes were loaded at
        import. Only a new interpreter can. `main` does the exec after `run` returns, once the
        terminal is restored and the watchdog has stopped.
        """
        self.restarting = True
        self.feed.info("restart: reopening on the same command line")
        self.application.exit()

    @staticmethod
    def pasted(data: str) -> str:
        """
        Flatten a paste onto one line, since the command bar is a single row.

        Text copied from a terminal often has trailing spaces and a final newline. Trailing spaces
        push the text out of view. A newline moves the cursor to a row that is not drawn. Either way
        the bar looks empty.

        Each line is stripped at both ends, and the lines are joined with spaces. Spaces inside a
        line are kept, so paths with spaces still work.
        """
        lines = [
            line.strip()
            for line in data.splitlines()
        ]
        return " ".join(
            line
            for line in lines
            if line
        )

    @cached_property
    def layout(self) -> Layout:
        """
        Returns the layout. It holds the header and the command bar. The feed is printed above them
        and is not part of the layout.
        """
        header = Window(
            FormattedTextControl(self.renderer.header),
            height=1,
            style="class:header",
        )
        container = HSplit([header, self.input])
        return Layout(container, focused_element=self.input)

    @cached_property
    def application(self) -> Application:
        """
        Returns the prompt_toolkit Application. It runs inline below the feed, not on the
        alternate screen.
        """
        return Application(
            layout=self.layout,
            key_bindings=self.bindings,
            style=STYLE,
            # coalesce the redraws of a burst of filesystem events into one
            min_redraw_interval=self.interval,
        )

    def submit(self, line: str) -> None:
        """
        Run one line from the command bar. The line is echoed to the feed, then passed to the
        command that its first word names. Events the command recorded are printed right after.
        Errors and warnings from the command are printed to the feed, and errors do not propagate.
        For example, `unmark` warns about what stays tracked.

        > assets
        assets (2 of 2)
        0. Asset
            id: 9b0c596b228d425bbefdce16a82fe3dd
        """
        text = line.strip()
        if not text:
            return
        self.feed.echo(text)
        name = text.split(maxsplit=1)[0]
        command = self.commands.resolve(name)
        if command is not None:
            # a REPL outlives a mistyped argument; anything raised belongs in the feed
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                try:
                    command.submit(text[len(name):].lstrip())
                except Exception as error:  # noqa: BLE001
                    self.feed.error(f"{type(error).__name__}: {error}")
            for warning in caught:
                self.feed.info(str(warning.message))
        self.run.drain()


class Run(Node):
    """
    The console's live session. While the application runs, it prints new events to the feed
    and checks that the watchdog is still alive.
    """

    _parent: Console
    # seconds between checks that the observer is still alive; its death announces itself
    # in no event, so it has to be looked for
    heartbeat = 1.0
    # how far into the event log the feed has written
    cursor = 0
    # whether the watchdog's death has already been reported
    failed = False

    async def __call__(self) -> None:
        """
        Run the application until it exits, printing new events to the feed as they arrive.
        See `banner` and `stream` for what is printed. What the watchdog logs as it stops is
        printed after the application exits.
        """
        trail = self._trail
        application = self._console.application
        self.banner()
        # the events already logged belong to earlier sessions, so the feed starts after them
        self.cursor = len(trail.events.ids)
        # taken before the observer starts, so that nothing recorded between the banner and
        # the first wakeup slips through the gap
        watching = trail.events.watch()
        async with trail.watchdog.session:
            streaming = asyncio.create_task(
                self.stream(watching),
                name="trail-console-stream",
            )
            monitoring = asyncio.create_task(
                self.monitor(),
                name="trail-console-monitor",
            )
            tasks = [streaming, monitoring]
            try:
                # a warning raised while events are applied, such as a Dir that was renamed,
                # would otherwise be written over the command bar
                with (
                    patch_stdout(raw=True),
                    warnings.catch_warnings(),
                ):
                    warnings.showwarning = self.warn
                    await application.run_async()
            finally:
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    with suppress(asyncio.CancelledError):
                        await task
                await watching.aclose()
        # stop() logs the accesses still open as the context exits, after the stream has ended
        self.drain()

    def warn(
        self,
        message: Warning | str,
        *args: object,
        **kwargs: object,
    ) -> None:
        """Print a warning to the feed. Installed as `warnings.showwarning` while running."""
        self._feed.info(str(message))
        self._console.application.invalidate()

    async def stream(self, watching: AsyncIterator[Event]) -> None:
        """
        Print new events to the feed each time events are logged.

        1. WatchdogEvent
            id: 5b54e690e99b4b39a49da21d9f718d1b
            timestamp: 19:27:45.122
            src_path: a.csv
        """
        async for _event in watching:
            if self.drain():
                self._console.application.invalidate()

    async def monitor(self) -> None:
        """Check every `heartbeat` seconds whether the watchdog has died."""
        watchdog = self._trail.watchdog
        while True:
            await asyncio.sleep(self.heartbeat)
            session = watchdog.session
            if session:
                self.diagnose(session.consumer)

    def diagnose(self, consumer: asyncio.Task[None] | None) -> None:
        """
        Print an error to the feed, once, if the watchdog's consumer died with an exception.

        watchdog stopped: <repr of the exception>
        """
        if (
            self.failed
            or consumer is None
            or not consumer.done()
            or consumer.cancelled()
        ):
            return
        self.failed = True
        failure = consumer.exception()
        if failure is not None:
            self._feed.error(f"watchdog stopped: {failure!r}")
            self._console.application.invalidate()

    def drain(self) -> bool:
        """
        Print the events logged since the last drain. Returns True if there were any.

        13. AddAssetEvent
            id: 815694f873604da5b63cd4c65900ccf4
            timestamp: 19:32:48.068
            src_path: /home/user/Downloads/asset.txt
        """
        events = self._trail.events
        identifiers = events.ids
        # a cleared log leaves the cursor past the end, and anything appended afterwards would
        # go unrendered until the log grew back to where the cursor was
        self.cursor = min(self.cursor, len(identifiers))
        if len(identifiers) <= self.cursor:
            return False
        start = self.cursor
        pending = [
            events[identifier]
            for identifier in identifiers[start:]
        ]
        self.cursor = len(identifiers)
        self._feed.write(pending, start)
        return True

    def banner(self) -> None:
        """
        Print the banner. It prints, in order:
        - The welcome, then a blank line.
        - The Trail id and the project root.
        - With `--nodir`, a line saying events are held in memory.
        - The asset, Dir and event counts.
        - Any warnings about `.markers`.
        - A usage hint, then a blank line before the events.

        🍃🍄🌲 Welcome back to Trail! 🌲🍄🍃

        trail #1ff756ca11b54b3994c7b26bbda99c0a on ~/project
        assets 2  dirs 1  events 14
        type 'help' for help
        """
        trail = self._trail
        renderer = self._renderer
        feed = self._feed
        feed.append(renderer.welcome(self._console.returning))
        feed.blank()
        feed.info(f"trail #{trail.id} on {renderer.home(self._console.root)}")
        if trail.dir is None:
            feed.info("nodir: events are held in memory and discarded on exit")
        feed.info(
            f"assets {len(trail.assets)}"
            f"  dirs {len(trail.dirs)}"
            f"  events {len(trail.events)}"
        )
        for warning in trail.markers.warnings:
            feed.error(warning)
        feed.info("type 'help' for help")
        feed.blank()

    def accept(self, buffer: Buffer) -> bool:
        """Submit the command bar's line. Returning False tells prompt_toolkit to clear the bar."""
        self._console.submit(buffer.text)
        return False
