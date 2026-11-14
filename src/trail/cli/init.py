from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Final

from prompt_toolkit import print_formatted_text
from prompt_toolkit.application import get_app_session
from prompt_toolkit.formatted_text import FormattedText, StyleAndTextTuples
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.output.plain_text import PlainTextOutput
from prompt_toolkit.utils import is_dumb_terminal

from trail.cli.console import Console
from trail.cli.render import Renderer
from trail.cli.theme import STYLE
from trail.ontrail import OnTrail
from trail.trail import Trail

__all__ = [
    "init",
]

WEBSITE: Final = "https://geojupyter.github.io/trail"
# rules of `.ontrail`, each beside what it does
EXAMPLES: Final = (
    ("data/", "Check the data folder (relative)"),
    ("/mnt/shared/", "Check the shared folder (absolute)"),
    ("*.csv", "Track CSV files in checked folders"),
    ("data/*.png", "Track PNG files in the data folder"),
)


def say(
        text: str = "",
        style: str = "",
) -> None:
    """Print one line in a `STYLE` class."""
    print_formatted_text(FormattedText([(style, text)]), style=STYLE)


def columns(
        rows: Sequence[tuple[str, StyleAndTextTuples]],
        width: int,
) -> None:
    """Print each label in `class:kind` beside its value. The values start at column `width`."""
    for label, value in rows:
        fragments = [
            ("class:kind", label.ljust(width)),
            *value,
        ]
        text = FormattedText(fragments)
        print_formatted_text(text, style=STYLE)


def link(
        path: Path,
        text: str,
) -> StyleAndTextTuples:
    """
    Returns `text` as an OSC 8 hyperlink to the file:// URI of `path`. Outside a terminal it
    returns `text` only. A terminal without OSC 8 shows `text` only.
    """
    output = get_app_session().output
    if (
        isinstance(output, (DummyOutput, PlainTextOutput))
        or is_dumb_terminal()
    ):
        out = [("", text)]
    else:
        out = [
            ("[ZeroWidthEscape]", f"\x1b]8;;{path.as_uri()}\x1b\\"),
            ("", text),
            ("[ZeroWidthEscape]", "\x1b]8;;\x1b\\"),
        ]
    return out


def startup(trail: Trail) -> None:
    """Placeholder: run `trail` in the background on login. Not yet asked or implemented."""


def init(
        root: Path,
        is_running: bool = False,
) -> Trail:
    """
    Start a Trail in `root`, or re-initialize the Trail that `root` is in. Print how its rules
    work and where to edit them.

    A missing `.ontrail` or `.offtrail` is copied from its preset. Existing files, the log and
    everything already tracked are kept. `is_running` says that the console opens next. The
    message then says the Trail is running, not initialized.

    Trail started in ~/project 🌲
      ontrail: ./ *.csv *.txt *.json
      offtrail: .*
      tracked: 2 assets in 1 dir
    Edit .ontrail and .offtrail in ~/project to change the rules.
    """
    existing = Trail.locate(root)
    if existing is None:
        home = root
    else:
        home = existing.parent
    ontrail = home / ".ontrail"
    offtrail = home / ".offtrail"
    if not ontrail.exists():
        text = OnTrail.preset.read_text(encoding="utf-8")
        ontrail.write_text(text, encoding="utf-8")
    # opening the Trail writes a missing .offtrail and scans for what the rules take. Naming
    # .trail itself opens the directory chosen, even nested in another Trail
    trail = Trail(home / ".trail", ontrail=None)
    # the emblem around the welcome is picked by the Trail id
    welcome = (
        Console(trail, home)
        .renderer
        .welcome(returning=False)
    )

    say()
    print_formatted_text(FormattedText(welcome), style=STYLE)
    say()
    say("Trail records how you work with project assets across notebooks, maps, and other tools.")
    say("When you publish, you can share your trail to help others follow the same steps you took.")
    say()
    say("Tracking Rules", "bold")
    say()
    say(".ontrail tells Trail which folders to check and which assets to track, for example:")
    say()
    examples = [
        (rule, [("", meaning)])
        for rule, meaning in EXAMPLES
    ]
    paths = [
        ("Project", link(home, Renderer.home(home))),
        ("Ontrail", link(ontrail, str(ontrail))),
        ("Offtrail", link(offtrail, str(offtrail))),
        ("Docs", [("", WEBSITE)]),
    ]
    # the examples and the paths start their values in one column
    width = max(
        len(label)
        for label, _ in (*examples, *paths)
    )
    width += 4
    columns(examples, width)
    say()
    say(".offtrail overrides .ontrail, keeping assets and folders off the trail.")

    say()

    # if is_running:
    #     say("Trail is running", "bold")
    # else:
    #     say("Trail initialized", "bold")
    # say()
    say("See Also", "bold")
    say()
    columns(paths, width)
    warnings = (*trail.ontrail.warnings, *trail.offtrail.warnings)
    if warnings:
        say()
    for warning in warnings:
        say(warning, "class:error")
    say()
    return trail
