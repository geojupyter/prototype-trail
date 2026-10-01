from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_toolkit.formatted_text import StyleAndTextTuples

from trail.cli.node import Node
from trail.cli.theme import INDENT, VERB_STYLES
from trail.dir import Dir
from trail.event import AddEntryEvent, Event, RemoveEntryEvent, WatchdogEvent
from trail.util import Repr, bare_repr

if TYPE_CHECKING:
    from trail.cli.console import Console


class Renderer(Node):
    """
    Turns the Trail's events and entries into feed rows. Wording choices such as an event's
    verb are presentation only. They live here so a later provenance export can word the same
    events differently. The Renderer holds no state. The feed tracks the current date itself.
    """

    _parent: Console

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
        item: Repr,
        position: int,
    ) -> list[StyleAndTextTuples]:
        """
        Returns the rows for an event or entry, which are a heading and then one line per
        field. Fields come from the item's `_repr_items`, so a new field on the class shows up
        here automatically.

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
        item: Repr,
        position: int,
    ) -> StyleAndTextTuples:
        """
        Returns the record's position and class name. The class name is coloured by the
        event's verb, so a deletion and a creation look different at a glance.

        4. WatchdogEvent
        """
        out: StyleAndTextTuples = [
            ("class:position", f"{position}. "),
            (self.style(item), type(item).__name__),
        ]
        return out

    def style(self, item: Repr) -> str:
        """
        Returns the style class for a heading. Events are coloured by their verb, or
        `class:event` if the verb has no style. Entries use `class:kind`.
        """
        if isinstance(item, Event):
            return VERB_STYLES.get(self.verb(item), "class:event")
        return "class:kind"

    def parameters(self, item: Repr) -> Iterator[tuple[str, str]]:
        """
        Yields each field's name and display value. For an event, the id of the entry it was
        recorded against comes last.
        """
        for name, value in item._repr_items():
            yield name, self.value(name, value)
        # the repr leaves the resource out, but the stored record keeps it, and that id is the
        # identity the whole project turns on, so the feed states it last
        entry = getattr(item, "entry", None)
        if entry is None:
            return
        yield "entry", bare_repr(entry.id)

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
        `bare_repr`.
        """
        if name.endswith("path"):
            value = self.display(value)
        return bare_repr(value)

    def header(self) -> StyleAndTextTuples:
        """
        Returns the header row above the command bar. It shows the Trail id, the project root,
        `(nodir)` when nothing is saved to disk, the entry and event counts, and whether the
        watchdog is running. It is redrawn on every refresh, so the counts stay current.

         trail #1ff756ca11b54b3994c7b26bbda99c0a  ~/project  assets 2  dirs 1  events 14  watching
        """
        trail = self._trail
        if trail.watchdog.running:
            state = "watching"
        else:
            state = "idle"
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
        Returns the verb for an event, which sets its heading colour. AddEntryEvent is
        `tracked` and RemoveEntryEvent is `offtrailed`. A WatchdogEvent uses its `event_type`,
        except a synthetic creation, which is `discovered`. Any other event is named after its
        class.
        """
        if isinstance(event, AddEntryEvent):
            return "tracked"
        if isinstance(event, RemoveEntryEvent):
            return "offtrailed"
        if isinstance(event, WatchdogEvent):
            # a synthetic creation is a resource the walk of a new directory turned up
            if event.is_synthetic and event.event_type == "created":
                return "discovered"
            return event.event_type or "event"
        return type(event).__name__.removesuffix("Event").lower()

    @staticmethod
    def directory(event: Event) -> bool:
        """Returns True if the event is about a directory."""
        if getattr(event, "is_directory", False):
            return True
        return isinstance(event.entry, Dir)

    @staticmethod
    def home(path: Path) -> str:
        """
        Returns the path with the home directory shortened to `~`. Paths outside the home
        directory are returned unchanged.

        >>> self.home(Path('/home/user/project'))
        '~/project'
        """
        try:
            return f"~/{path.relative_to(Path.home())}"
        except (RuntimeError, ValueError):
            return str(path)
