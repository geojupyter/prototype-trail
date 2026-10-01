from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from functools import cached_property

from prompt_toolkit.formatted_text import FormattedText, StyleAndTextTuples
from prompt_toolkit.shortcuts import print_formatted_text

from trail.cli.node import Node
from trail.cli.theme import PROMPT, STYLE
from trail.event import Event


class Feed(Node):
    """
    The console's scrollback. Rows arrive already rendered and are printed once. They are
    never redrawn. Only the last `limit` rows are kept in `rows`, so memory stays bounded in
    long sessions.
    """

    # rows retained before the head of the feed is discarded
    limit = 2000
    # the date of the last row written, so a change of day is announced once
    day: date | None = None
    # how that change is announced
    day_format = "%A %d %B %Y"

    @cached_property
    def rows(self) -> list[StyleAndTextTuples]:
        """Returns the rows printed so far, at most `limit` of them."""
        return []

    def write(
        self,
        events: Iterable[Event],
        start: int = 0,
    ) -> None:
        """
        Print `events`, with a date row whenever the day changes.

        `start` is the position of the first event in the log. Records are numbered from it,
        so each one shows its position in the log rather than in this batch.

        Thursday 01 October 2026
        0. AddEntryEvent
            id: 7960adcac6b04dbb98cf1ed8562f7d6f
        """
        rows: list[StyleAndTextTuples] = []
        for position, event in enumerate(events, start):
            local = event.timestamp.astimezone()
            if local.date() != self.day:
                self.day = local.date()
                rows.append(self._renderer.day_row(local, self.day_format))
            rows.extend(self._renderer.render(event, position))
        self.extend(rows)

    def extend(self, rows: Iterable[StyleAndTextTuples]) -> None:
        """
        Print `rows` and keep them, dropping the oldest rows past `limit`. Newlines inside a
        row are replaced with spaces so each row stays on one line. Tabs are replaced with four
        spaces.
        """
        appended = [
            [
                (fragment[0], fragment[1].replace("\n", " ").replace("\t", "    "))
                for fragment in row
            ]
            for row in rows
        ]
        if not appended:
            return
        self.rows.extend(appended)
        overflow = len(self.rows) - self.limit
        if overflow > 0:
            del self.rows[:overflow]
        self.show(appended)

    def show(self, rows: list[StyleAndTextTuples]) -> None:
        """Print the rows as ordinary terminal output, in a single write."""
        # one write for the whole batch, since the bar below is erased and redrawn around
        # every write: a burst of events then costs one redraw rather than one per line
        fragments: StyleAndTextTuples = []
        for row in rows:
            if fragments:
                fragments.append(("", "\n"))
            fragments.extend(row)
        print_formatted_text(FormattedText(fragments), style=STYLE)

    def append(self, row: StyleAndTextTuples) -> None:
        self.extend([row])

    def echo(self, text: str) -> None:
        """Print a submitted command line with its prompt."""
        self.append([("class:echo.prompt", PROMPT), ("class:echo", text)])

    def info(self, text: str) -> None:
        self.append([("class:info", text)])

    def error(self, text: str) -> None:
        self.append([("class:error", text)])

    def clear(self) -> None:
        """Forget the printed rows and the current date, so the next event gets a date row."""
        self.rows.clear()
        self.day = None
