from __future__ import annotations

from typing import Final

from prompt_toolkit.styles import Style

PROMPT: Final = "> "
# how far a parameter line is indented under the line naming its event
INDENT: Final = "    "

# the style classes for everything the cli prints or draws
CLASSES: Final[dict[str, str]] = {
    "welcome": "bold",
    "header": "bg:ansibrightblack ansiwhite",
    "header.name": "bold",
    "header.id": "ansibrightcyan",
    "header.mode": "ansiyellow",
    "header.counts": "ansiwhite",
    "position": "ansibrightblack",
    "day": "ansibrightblack bold",
    "parameter": "ansibrightblack",
    "id": "ansibrightblack",
    "kind": "ansiblue",
    "missing": "ansired",
    "echo": "bold",
    "echo.prompt": "ansicyan bold",
    "info": "ansibrightblack",
    "error": "ansired bold",
    "event": "ansiblue",
    "event.tracked": "ansigreen bold",
    "event.offtrailed": "ansired bold",
    "event.watched": "ansigreen bold",
    "event.unwatched": "ansired bold",
    "event.created": "ansigreen",
    "event.discovered": "ansigreen",
    "event.modified": "ansiyellow",
    "event.deleted": "ansired",
    "event.moved": "ansicyan",
    "event.opened": "ansibrightblack",
    "text-area.prompt": "ansicyan bold",
    "selected-option": "ansicyan bold",
}
STYLE: Final = Style.from_dict(CLASSES)

# the style of a record heading, keyed by the verb from `Renderer.verb`
VERB_STYLES: Final[dict[str, str]] = {
    "tracked": "class:event.tracked",
    "offtrailed": "class:event.offtrailed",
    "watched": "class:event.watched",
    "unwatched": "class:event.unwatched",
    "created": "class:event.created",
    "discovered": "class:event.discovered",
    "modified": "class:event.modified",
    "deleted": "class:event.deleted",
    "moved": "class:event.moved",
    "opened": "class:event.opened",
}

# the glyphs an emblem is drawn from. ⛰️ 🏔️ 🏕️ end in an invisible VS16. ZWJ sequences
# such as the brown mushroom are left out, since terminals that do not join them show the
# parts side by side
EMBLEM_GLYPHS: Final = (
    "⛰️",
    "🌳",
    "🌿",
    "🌲",
    "🍃",
    "🍂",
    "🪵",
    "🏔️",
    "🌱",
    "🏕️",
    "🪻",
    "🌺",
    "🍁",
    "🌼",
    "🌵",
    "🍄",
)
