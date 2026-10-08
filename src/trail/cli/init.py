from __future__ import annotations

from collections.abc import Sequence
from functools import cached_property
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Final

from prompt_toolkit import print_formatted_text
from prompt_toolkit.application import Application, get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition, has_completions
from prompt_toolkit.formatted_text import FormattedText, StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout import (
    BufferControl,
    CompletionsMenu,
    ConditionalContainer,
    Dimension,
    Float,
    FloatContainer,
    FormattedTextControl,
    HSplit,
    Layout,
    VSplit,
    Window,
)
from prompt_toolkit.layout.processors import BeforeInput

from trail.cli.console import Console
from trail.cli.render import Renderer
from trail.cli.scopes import Paths
from trail.cli.theme import STYLE
from trail.markers import (
    MARKERS,
    available,
    kind,
)
from trail.trail import Trail
from trail.util import plural

__all__ = [
    "Paths",
    "Picker",
    "init",
    "offer",
]

WEBSITE: Final = "https://geojupyter.github.io/trail"
DEFAULT_MARKERS: Final = "default"


def say(
        text: str,
        style: str = "",
) -> None:
    """Print one line of the walkthrough in a `STYLE` class."""
    print_formatted_text(FormattedText([(style, text)]), style=STYLE)


def hint(*lines: str) -> None:
    """
    Print each line as a dim bullet under a question.

      - You can always edit the `.markers` text file afterward.
    """
    for line in lines:
        say(f"  - {line}", "class:info")


def tabbing() -> KeyBindings:
    """
    Returns key bindings that make <TAB> accept the highlighted completion, or the first one,
    instead of cycling to the next. Accepting a directory then completes its contents, like a
    shell.
    """
    bindings = KeyBindings()

    @bindings.add("tab", filter=has_completions)
    def _take(event: KeyPressEvent) -> None:
        buffer = event.current_buffer
        state = buffer.complete_state
        completion = state.current_completion
        if completion is None:
            completion = state.completions[0]
        buffer.apply_completion(completion)
        if completion.text.endswith("/"):
            buffer.start_completion(select_first=False)

    return bindings


class Picker:
    """
    Lets the user choose the markers file a new `.markers` starts from. The bundled files are
    listed on the left, with a preview of the selected one on the right. A typed path loads a
    markers file of the user's own.

    <UP> and <DOWN> move through the list. A file longer than the preview is cut off, since
    the editor that follows shows every line. Typing a path previews that file instead. <ENTER>
    chooses the previewed file, and <ESCAPE> chooses none.
    """

    # terminal rows left to the question and its hints above the picker
    margin = 14

    def __init__(
        self,
        options: Sequence[tuple[str, Traversable]],
        root: Path,
    ) -> None:
        self.options = options
        self.root = root
        self.index = 0
        self.error = ""

    @cached_property
    def buffer(self) -> Buffer:
        """Returns the path input below the list. Editing it clears any error."""
        completer = Paths(
            expanduser=True,
            get_paths=lambda: [str(self.root)],
        )
        return Buffer(
            completer=completer,
            multiline=False,
            on_text_changed=self._changed,
        )

    def _changed(self, buffer: Buffer) -> None:
        self.error = ""

    @property
    def typed(self) -> str:
        return self.buffer.text.strip()

    @property
    def custom(self) -> Path | None:
        """
        Returns the markers file at the typed path, or None if there is none. A directory
        means the `.markers` file inside it.
        """
        try:
            path = (self.root / Path(self.typed).expanduser()).resolve()
            if path.is_dir():
                path = path / ".markers"
            if path.is_file():
                return path
        except (RuntimeError, ValueError):
            pass
        return None

    @property
    def source(self) -> Traversable | None:
        """
        Returns the file to preview. This is the typed path if there is one, otherwise the
        selected file in the list.
        """
        if self.typed:
            return self.custom
        if not self.options:
            return None
        return self.options[self.index][1]

    def scroll(self, step: int) -> None:
        """Move the list selection by `step`."""
        # the first press after typing gives the listing back rather than moving through it
        if self.buffer.text:
            self.buffer.text = ""
            return
        self.index = max(0, min(len(self.options) - 1, self.index + step))

    def accept(self) -> tuple[str, list[str]] | None:
        """
        Returns the label and lines of the previewed file. Returns None and sets `error` if
        there is no file or it cannot be read.
        """
        source = self.source
        if source is None:
            if self.typed:
                self.error = f"no markers file at {self.typed}"
            else:
                self.error = "type a path, or press <ESCAPE> to go without"
            return None
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as error:
            self.error = str(error)
            return None
        if self.typed:
            label = Renderer.home(Path(str(source)))
        else:
            label = self.options[self.index][0]
        return label, lines

    def style(self, line: str) -> str:
        """
        Returns the style for one line of a markers file. Comments and blank lines are `info`,
        invalid lines are `error`, and markers are `kind`.
        """
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith("#")
        ):
            return "class:info"
        if kind(stripped, self.root) == "invalid":
            return "class:error"
        return "class:kind"

    def _listing(self) -> StyleAndTextTuples:
        """
        Returns the list of markers files with the selected one highlighted. The highlight is
        hidden while a path is typed.
        """
        out: StyleAndTextTuples = []
        for index, (label, _) in enumerate(self.options):
            if index:
                out.append(("", "\n"))
            if (
                index == self.index
                and not self.typed
            ):
                # scrolls the window to the cursor once there are more files than rows
                out.append(("[SetCursorPosition]", ""))
                out.append(("class:selected-option", f"  > {label}"))
            else:
                out.append(("", f"    {label}"))
        return out

    def _content(self) -> list[tuple[str, str]]:
        """
        Returns the previewed file as (style, text) rows. Returns a single message row if the
        file is missing, empty, or cannot be read.
        """
        source = self.source
        if source is None:
            return [("class:info", "no markers file at this path")]
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as error:
            return [("class:error", str(error))]
        if not lines:
            return [("class:info", "empty")]
        out = [
            (self.style(line), line)
            for line in lines
        ]
        return out

    def _preview(self) -> StyleAndTextTuples:
        """
        Returns the top of the preview, as much as fits. A file that runs on past it ends in a
        count of the lines cut off.
        """
        rows = self._content()
        viewport = self.viewport
        if len(rows) > viewport:
            hidden = len(rows) - viewport + 1
            shown = rows[:viewport - 1]
            shown.append(("class:info", f"… {hidden} more lines"))
        else:
            shown = rows
        # padded to the viewport, so that the rule down the side runs the full height
        shown.extend(
            ("", "")
            for _ in range(viewport - len(shown))
        )
        out: StyleAndTextTuples = []
        for index, (style, text) in enumerate(shown):
            if index:
                out.append(("", "\n"))
            out.append(("class:info", " │ "))
            out.append((style, text))
        return out

    @cached_property
    def depth(self) -> int:
        """
        Returns the line count of the longest bundled file, or the number of files if that is
        larger.
        """
        out = len(self.options)
        for _, source in self.options:
            try:
                count = len(source.read_text(encoding="utf-8").splitlines())
            except (OSError, UnicodeDecodeError):
                continue
            out = max(out, count)
        return out

    @property
    def viewport(self) -> int:
        """
        Returns the height of the list and the preview. It fits the longest file, so the
        picker does not resize while moving through the list. It never exceeds the terminal
        rows left below the question.
        """
        longest = max(self.depth, len(self._content()))
        rows = get_app().output.get_size().rows
        return max(1, min(longest, rows - self.margin))

    @cached_property
    def layout(self) -> Layout:
        """
        Returns the picker layout. The list and the preview sit side by side, with the path
        input and any error below them. The completion menu floats at the cursor.
        """
        width = max(
            (
                len(label)
                for label, _ in self.options
            ),
            default=0,
        )
        # an inline application is handed every row below the cursor; neither of these may
        # take more than its content, or the picker fills the terminal
        listing = Window(
            FormattedTextControl(self._listing),
            width=width + 4,
            height=lambda: Dimension(max=self.viewport),
            dont_extend_height=True,
        )
        preview = Window(
            FormattedTextControl(self._preview),
            height=lambda: Dimension(max=self.viewport),
            wrap_lines=False,
            dont_extend_height=True,
        )
        body = VSplit([listing, preview])
        prompt = BeforeInput("  path: ", style="class:info")
        field = Window(
            BufferControl(
                self.buffer,
                input_processors=[prompt],
            ),
            height=1,
        )
        message = Window(
            FormattedTextControl(lambda: [("class:error", f"  {self.error}")]),
            height=1,
        )
        error = ConditionalContainer(
            message,
            filter=Condition(lambda: bool(self.error)),
        )
        menu = Float(
            xcursor=True,
            ycursor=True,
            content=CompletionsMenu(max_height=8, scroll_offset=1),
        )
        container = FloatContainer(
            HSplit([body, field, error]),
            floats=[menu],
        )
        return Layout(container, focused_element=field)

    @cached_property
    def bindings(self) -> KeyBindings:
        """
        Returns the picker's key bindings. While the completion menu is open, <ENTER> keeps the
        completion and <ESCAPE> reverts it. Ctrl-C and Ctrl-D abort `init`.
        """
        bindings = KeyBindings()
        idle = ~has_completions
        empty = Condition(lambda: not self.buffer.text)

        @bindings.add("up", filter=idle)
        def _up(event: KeyPressEvent) -> None:
            self.scroll(-1)

        @bindings.add("down", filter=idle)
        def _down(event: KeyPressEvent) -> None:
            self.scroll(1)

        # a completion under the cursor already stands in the buffer; enter keeps it
        @bindings.add("enter", filter=has_completions)
        def _keep(event: KeyPressEvent) -> None:
            event.current_buffer.complete_state = None

        # and escape takes it back, rather than turning every markers file down
        @bindings.add("escape", filter=has_completions, eager=True)
        def _revert(event: KeyPressEvent) -> None:
            event.current_buffer.cancel_completion()

        @bindings.add("enter", filter=idle)
        def _accept(event: KeyPressEvent) -> None:
            chosen = self.accept()
            if chosen is not None:
                event.app.exit(result=chosen)

        @bindings.add("escape", filter=idle, eager=True)
        def _reject(event: KeyPressEvent) -> None:
            event.app.exit(result=None)

        @bindings.add("c-c")
        def _interrupt(event: KeyPressEvent) -> None:
            event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

        @bindings.add("c-d", filter=empty)
        def _end(event: KeyPressEvent) -> None:
            event.app.exit(exception=EOFError, style="class:aborting")

        @bindings.add(Keys.BracketedPaste)
        def _paste(event: KeyPressEvent) -> None:
            event.current_buffer.insert_text(Console.pasted(event.data))

        return bindings

    @cached_property
    def application(self) -> Application[tuple[str, list[str]] | None]:
        """
        Returns the picker application. It is erased when it exits, leaving only the line
        `init` prints about the choice.

          markers from ~/Downloads/geospatial.txt
        """
        return Application(
            layout=self.layout,
            key_bindings=merge_key_bindings([self.bindings, tabbing()]),
            style=STYLE,
            erase_when_done=True,
        )

    def run(self) -> tuple[str, list[str]] | None:
        """
        Run the picker. Returns the label and lines of the chosen file, or None if markers
        were rejected.
        """
        return self.application.run()


def offer() -> list[tuple[str, Traversable]]:
    """Returns the bundled markers files for the picker, with the default first."""
    names = sorted(
        available(),
        key=lambda name: (name != DEFAULT_MARKERS, name),
    )
    out: list[tuple[str, Traversable]] = [
        (name, MARKERS / f"{name}.txt")
        for name in names
    ]
    return out


def startup(trail: Trail) -> None:
    """Placeholder: run `trail` in the background on login. Not yet asked or implemented."""


def init(root: Path) -> Trail:
    """
    Walk the user through starting a Trail, or re-initializing one. Relative paths are read
    from `root`. Nothing is written until every question is answered, so an interrupt leaves
    the project untouched.

    Re-initializing rewrites `.markers` and tracks what the new lines indicate. The log is
    kept, and so is everything already tracked.

    Trail started in ~/project 🌲
      markers: ./ *.csv *.txt *.json
      tracked: 2 assets in 1 dir
    Edit /home/user/project/.markers to change the markers.
    """
    existing = Trail.locate(root)
    if existing is None:
        home = root
    else:
        home = existing.parent

    say("")
    say("🌲 Welcome to Trail! 🌳", "bold")
    say("")
    say("Trail leaves breadcrumbs along your data journey: the interactions git never sees,")
    say("such as opening a dataset, viewing it on a map, or carrying it between tools, so you")
    say("can retrace your project's steps.")
    say(f"Read more at {WEBSITE}", "class:info")
    say("")
    say("🎒 Let's start a Trail! 🥾", "bold")
    if existing is not None:
        say(f"Re-initializing the Trail at {Renderer.home(home)}", "class:info")
    say("")

    path = home / ".markers"
    say("Markers say which folders to check and which files to automatically track in them. 🪧", "bold")
    hint(
        "A line ending in `/` is a folder to track.",
        "A line with an extension like `*.csv` tracks those files in the tracked folders.",
        "A combination e.g. `data/*.png` tracks extensions under specific folders.",
        "A line starting with `!` e.g. `!requirements.txt` excludes that pattern from tracking.",
        "A line with `./` tracks the project directory itself.",
    )
    say("")
    say("Let's pick a marker set to start from 🪨:", "bold")
    hint(
        "<UP> and <DOWN> to browse marker sets.",
        "<ENTER> to choose one.",
        "Enter a path to load your own markers file.",
        "<ESCAPE> to start with no markers.",
    )
    say("")
    picker = Picker(offer(), root)
    chosen = picker.run()
    if chosen is None:
        lines = []
        say("  no markers", "class:kind")
    else:
        lines = chosen[1]
        say(f"  markers from {chosen[0]}", "class:kind")
    imported = picker.custom not in (None, path)
    say("")

    # `trail` on startup is asked here once `startup` is implemented

    text = "".join(
        f"{line}\n"
        for line in lines
    )
    path.write_text(text, encoding="utf-8")
    # opening the Trail scans for what the markers indicate; naming .trail itself opens the
    # directory chosen, even nested in another Trail
    trail = Trail(home / ".trail", markers=None)
    # the anchored lines of a file from elsewhere were written for the project it came from,
    # so the warnings name each one
    trail.markers.imported = imported

    say(f"Trail started in {Renderer.home(home)} 🌲", "bold")
    say(f"  markers: {' '.join(trail.markers) or 'none'}", "class:info")
    assets = plural(len(trail.assets), "asset")
    dirs = plural(len(trail.dirs), "dir")
    say(f"  tracked: {assets} in {dirs}", "class:info")
    for warning in trail.markers.warnings:
        say(f"  {warning}", "class:error")
    say(f"Edit {path} to change the markers.")
    say("")
    return trail
