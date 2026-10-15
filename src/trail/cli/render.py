from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit.formatted_text import StyleAndTextTuples

from trail import fmt
from trail.cli.node import Node
from trail.cli.theme import EMBLEM_GLYPHS, INDENT, VERB_STYLES
from trail.event import (
    AddAssetEvent,
    AddDirEvent,
    Event,
    RemoveAssetEvent,
    RemoveDirEvent,
    WatchdogEvent,
)

if TYPE_CHECKING:
    from trail.cli.console import Console


class Renderer(Node):
    """
    Turns the Trail's events, assets and Dirs into feed rows. Wording choices such as an event's
    verb are presentation only, so a later provenance export can word the same events differently.
    The Renderer holds no state. The feed tracks the current date itself.
    """

    _parent: Console
    # glyphs on each side of the welcome
    emblem_length = 3

    @property
    def root(self) -> Path:
        """The project root. Paths under it are shown relative to it."""
        return self._console.root

    @staticmethod
    def day_row(
        local: datetime,
        pattern: str,
    ) -> StyleAndTextTuples:
        """
        Returns the date row printed when events cross into a new day.

        Thursday 01 October 2026
        """
        return [("class:day", f"{local:{pattern}}")]

    def record(
        self,
        item: fmt.Repr,
        position: int,
    ) -> list[StyleAndTextTuples]:
        """
        Returns the rows for an event or asset: a heading, then one line per field. Fields come from
        the item's `_repr_items`, so a new field shows up here automatically.

        0. Asset
            id: 9b0c596b228d425bbefdce16a82fe3dd
            path: a.txt
            st_size: 12 B
        """
        out = [self.heading(item, position)]
        out.extend(
            self.parameter(name, value)
            for name, value in self.parameters(item)
        )
        return out

    def render(
        self,
        event: Event,
        position: int,
    ) -> list[StyleAndTextTuples]:
        """Returns the rows the feed prints for an event. Same as `record`."""
        return self.record(event, position)

    def heading(
        self,
        item: fmt.Repr,
        position: int,
    ) -> StyleAndTextTuples:
        """
        Returns the record's position and class name. The class name is coloured by the event's verb.

        4. WatchdogEvent
        """
        out: StyleAndTextTuples = [
            ("class:position", f"{position}. "),
            (self.style(item), type(item).__name__),
        ]
        return out

    def style(self, item: fmt.Repr) -> str:
        """
        Returns the style class for a heading. Events are coloured by their verb, or
        `class:event` if the verb has no style. Assets use `class:kind`.
        """
        if isinstance(item, Event):
            return VERB_STYLES.get(self.verb(item), "class:event")
        return "class:kind"

    def parameters(self, item: fmt.Repr) -> Iterator[tuple[str, str]]:
        """
        Yields each field's name and display value. For an event, the id of the asset or the
        Dir it was recorded against comes last.
        """
        for name, value in item._repr_items():
            yield name, self.value(name, value)
        # the repr leaves the resource out, but the stored record keeps it, and that id is the
        # identity the whole project turns on, so the feed states it last
        entry = getattr(item, "entry", None)
        if entry is None:
            return
        yield "entry", fmt.bare(entry.id)

    def parameter(
        self,
        name: str,
        value: str,
    ) -> StyleAndTextTuples:
        return [
            ("class:parameter", f"{INDENT}{name}: "),
            ("", value),
        ]

    def value(
        self,
        name: str,
        value: object,
    ) -> str:
        """
        Returns a field value as text. Path fields go through `display`. Other values use
        `fmt.bare`.
        """
        if name.endswith("path"):
            value = self.display(value)
        return fmt.bare(value)

    def emblem(self) -> list[str]:
        """
        Returns `emblem_length` glyphs chosen by the Trail id. A Trail always gets the same glyphs
        in the same order. Two Trails can share an emblem, but this is unlikely.

        >>> self._trail.id
        '1ff756ca11b54b3994c7b26bbda99c0a'
        >>> self.emblem()
        ['🍃', '🍄', '🌲']
        """
        # the builtin hash is salted per process, so it would pick new glyphs every session
        digest = hashlib.sha256(self._trail.id.encode()).digest()
        return [
            EMBLEM_GLYPHS[byte % len(EMBLEM_GLYPHS)]
            for byte in digest[:self.emblem_length]
        ]

    def welcome(self, returning: bool) -> StyleAndTextTuples:
        """
        Returns the welcome row printed at the top of the banner. The emblem is on the left and
        its mirror image is on the right. A returning session is welcomed back.

        🍃🍄🌲 Welcome back to Trail! 🌲🍄🍃
        """
        emblem = self.emblem()
        if returning:
            text = "Welcome back to Trail!"
        else:
            text = "Welcome to Trail!"
        left = "".join(emblem)
        right = "".join(reversed(emblem))
        return [
            ("", f"{left} "),
            ("class:welcome", text),
            ("", f" {right}"),
        ]

    def header(self) -> StyleAndTextTuples:
        """
        Returns the header row above the command bar. It shows the Trail id, the project root,
        `(nodir)` if nothing is saved to disk, the asset, Dir and event counts, the open dirasset
        accesses if there are any, and whether the watchdog is running. It is redrawn on every
        refresh.

         trail #1ff756ca11b54b3994c7b26bbda99c0a  ~/project  assets 2  dirs 1  events 14  watching
        """
        trail = self._trail
        session = trail.watchdog.session
        if session.is_active:
            state = "watching"
        else:
            state = "idle"
        accesses = ""
        if session:
            count = session.accesses.open
            if count:
                accesses = f"  accesses {count}"
        row: StyleAndTextTuples = [
            ("class:header.name", " trail "),
            ("class:header.id", f"#{trail.id}  "),
            ("", self.home(self.root)),
        ]
        if trail.dir is None:
            row.append(("class:header.mode", "  (nodir)"))
        counts = (
            f"  assets {len(trail.assets)}"
            f"  dirs {len(trail.dirs)}"
            f"  events {len(trail.events)}"
            f"{accesses}"
            f"  {state}"
        )
        row.append(("class:header.counts", counts))
        return row

    def display(
        self,
        path: str | Path,
        directory: bool = False,
    ) -> str:
        """
        Returns a path as the console shows it. Paths under the root are relative to it, and
        other paths stay absolute. A trailing slash is added when `directory` is True.

        >>> self.display('/home/user/project/a.csv')
        'a.csv'
        >>> self.display('/home/user/Downloads/asset.txt')
        '/home/user/Downloads/asset.txt'
        """
        path = Path(path)
        try:
            text = str(path.relative_to(self.root))
        except ValueError:
            text = str(path)
        if directory:
            text += "/"
        return text

    @staticmethod
    def verb(event: Event) -> str:
        """
        Returns the verb for an event. The verb sets the heading colour.
        - AddAssetEvent is `tracked`, or `discovered` if a line of `.markers` picked the asset up.
        - RemoveAssetEvent is `offtrailed`.
        - AddDirEvent is `watched`.
        - RemoveDirEvent is `unwatched`.
        - WatchdogEvent uses its `event_type`. This includes an access to a dirasset.
        - Any other event is named after its class.
        """
        if isinstance(event, AddAssetEvent):
            if event.rule is not None:
                return "discovered"
            return "tracked"
        if isinstance(event, RemoveAssetEvent):
            return "offtrailed"
        if isinstance(event, AddDirEvent):
            return "watched"
        if isinstance(event, RemoveDirEvent):
            return "unwatched"
        if isinstance(event, WatchdogEvent):
            out = event.event_type or "event"
            return out
        out = (
            type(event).__name__
            .removesuffix("Event")
            .lower()
        )
        return out

    @staticmethod
    def home(path: Path) -> str:
        """
        Returns the path with the home directory shortened to `~`. Paths outside the home
        directory are returned unchanged.

        >>> Renderer.home(Path('/home/user/project'))
        '~/project'
        """
        try:
            return f"~/{path.relative_to(Path.home())}"
        except (RuntimeError, ValueError):
            return str(path)
