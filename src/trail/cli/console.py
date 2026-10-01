from __future__ import annotations

import asyncio
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
    The interactive session. Events recorded by the watchdog are printed to the feed as they
    happen. The command bar at the bottom runs commands such as `track` and `events`.

    Only the header and the command bar are drawn by prompt_toolkit. The feed above them is
    ordinary terminal output, so scrollback, text selection and the scrollbar work as usual.
    The alternate screen is not used because it would disable all three.
    """

    _parent: Trail
    # redraws arriving within this interval are coalesced into one
    interval = 0.05

    def __init__(
        self,
        trail: Trail,
        root: Path,
    ) -> None:
        Node.__init__(self, trail)
        self.root = root
        self.restarting = False

    @cached_property
    def run(self) -> Run:
        """
        Returns a Run instance, which runs the live session. It prints the banner and the last
        `backlog` events, then prints new events as the watchdog records them until the
        application exits. Lines submitted in the command bar also go through it.

        $ trail
        trail #1ff756ca11b54b3994c7b26bbda99c0a on ~/project
        recording to ~/project/.trail
        nothing is tracked until you track it; 'help' lists the commands
        """
        return Run(self)

    @cached_property
    def feed(self) -> Feed:
        """
        Returns a Feed instance, which prints rows to the terminal above the command bar. Rows
        include the echoed command, info and error messages, and rendered records. Each row is
        printed once and never redrawn. Only the last `limit` rows are kept in memory.

        > events -2: st_size=0
        events (1 of 14)
        13. AddEntryEvent
            id: 815694f873604da5b63cd4c65900ccf4
        """
        return Feed(self)

    @cached_property
    def renderer(self) -> Renderer:
        """
        Returns a Renderer instance, which turns events and entries into rows for the feed.
        Each record is a heading with its position and class name, followed by one line per
        field. Paths under the project root are shown relative to it. The Renderer also builds
        the header above the command bar.

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
        Returns a Commands instance, which maps each command name and alias to its Command. It
        is built from every subclass registered in `Command.classes`. A command can be called
        by any prefix that matches only that command.

        >>> self.commands
        Commands (track, untrack, assets, dirs, entries, events, clear, help, restart, quit, exit)
        >>> self.commands.find('ev')
        EventsCommand('events')
        """
        return Commands(self)

    @cached_property
    def completer(self) -> CommandCompleter:
        """
        Returns a CommandCompleter, which handles <TAB> completion in the command bar. The
        first word is completed against the command names. The rest of the line is passed to
        that command's `complete`. For example, `track` completes filesystem paths, `untrack`
        completes tracked paths, and listings complete field names.

        CommandCompleter is a prompt_toolkit Completer, not a Node, so it stores the console
        directly.

        e<TAB>             entries, events, exit
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
        Empty the terminal and its scrollback. Nothing tracked is touched. The feed drops its
        rows too, so what it holds still matches what is on the screen.
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

        A running session cannot pick up edits to its own source, because its classes were
        loaded at import. Only a new interpreter can. `main` does the exec after `run` returns,
        because by then the terminal has been restored and the watchdog has stopped.
        """
        self.restarting = True
        self.feed.info("restart: reopening on the same command line")
        self.application.exit()

    @staticmethod
    def pasted(data: str) -> str:
        """
        Flatten a paste onto one line, since the command bar is a single row.

        Text copied from a terminal is often padded with trailing spaces and ends with a
        newline. In a one-row bar, the padding pushes the text out of view, and a newline moves
        the cursor to a row that is not drawn. Either way the bar looks empty.

        Each line is stripped at both ends and the lines are joined with spaces. Spaces inside
        a line are kept, so paths containing spaces still work.
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
        Returns the layout, which is the header above the command bar. The feed is printed
        above these two rows and is not part of the layout.
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
        command named by its first word. Events the command recorded are printed right after
        it. An error raised by the command is printed to the feed instead of propagating.

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
            try:
                command.submit(text[len(name):].lstrip())
            except Exception as error:  # noqa: BLE001
                self.feed.error(f"{type(error).__name__}: {error}")
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
    # events from previous sessions replayed on startup
    backlog = 20
    # how far into the event log the feed has written
    cursor = 0
    # whether the watchdog's death has already been reported
    failed = False

    async def __call__(self) -> None:
        """
        Run the application until it exits, printing new events to the feed as they arrive.
        See `banner`, `replay` and `stream` for what is printed.
        """
        trail = self._trail
        application = self._console.application
        self.banner()
        self.replay()
        # taken before the observer starts, so that nothing recorded between the replayed
        # backlog and the first wakeup slips through the gap
        watching = trail.events.watch()
        async with trail.watchdog.context():
            tasks = [
                asyncio.create_task(self.stream(watching), name="trail-console-stream"),
                asyncio.create_task(self.monitor(), name="trail-console-monitor"),
            ]
            try:
                with patch_stdout(raw=True):
                    await application.run_async()
            finally:
                for task in tasks:
                    task.cancel()
                for task in tasks:
                    with suppress(asyncio.CancelledError):
                        await task
                await watching.aclose()

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
            self.diagnose(watchdog.consumer)

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

        13. AddEntryEvent
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
        Print the Trail id, the project root, where events are saved, and a usage hint. With
        `--nodir`, the second line says events are held in memory instead.

        trail #1ff756ca11b54b3994c7b26bbda99c0a on ~/project
        recording to ~/project/.trail
        nothing is tracked until you track it; 'help' lists the commands
        """
        trail = self._trail
        renderer = self._renderer
        feed = self._feed
        feed.info(f"trail #{trail.id} on {renderer.home(self._console.root)}")
        if trail.dir is None:
            feed.info("nodir: events are held in memory and discarded on exit")
        else:
            feed.info(f"recording to {renderer.home(trail.dir)}")
        feed.info("nothing is tracked until you track it; 'help' lists the commands")

    def replay(self) -> None:
        """
        Print the last `backlog` events already in the log at startup. If the log holds more,
        `... N earlier events` is printed first.

        Thursday 01 October 2026
        0. AddEntryEvent
            id: 7960adcac6b04dbb98cf1ed8562f7d6f
            timestamp: 19:26:04.997
        """
        identifiers = self._trail.events.ids
        hidden = len(identifiers) - self.backlog
        if hidden > 0:
            self._feed.info(f"... {hidden} earlier events")
            self.cursor = hidden
        self.drain()

    def accept(self, buffer: Buffer) -> bool:
        """Submit the command bar's line. Returning False tells prompt_toolkit to clear the bar."""
        self._console.submit(buffer.text)
        return False
