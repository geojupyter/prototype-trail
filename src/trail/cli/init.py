from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from functools import cached_property
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Final

from prompt_toolkit import PromptSession, print_formatted_text
from prompt_toolkit.application import Application, get_app
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import (
    CompleteEvent,
    Completer,
    Completion,
)
from prompt_toolkit.document import Document
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
from trail.cli.scopes import Paths, completer
from trail.cli.theme import PROMPT, STYLE
from trail.dir import build, match
from trail.markers import (
    MARKERS,
    Text,
    available,
    canonical,
    kind,
    parse,
    slashless,
)
from trail.trail import Trail
from trail.util import container, plural, walk

__all__ = [
    "Editor",
    "Paths",
    "Picker",
    "init",
    "offer",
    "tally",
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

    <UP> and <DOWN> move through the list. <RIGHT> moves the arrows into the preview to scroll
    the file, and <LEFT> moves them back. Typing a path previews that file instead. <ENTER>
    chooses the previewed file, and <ESCAPE> chooses none.
    """

    # terminal rows left to the question and its hints above the picker
    margin = 9

    def __init__(
        self,
        options: Sequence[tuple[str, Traversable]],
        root: Path,
    ) -> None:
        self.options = options
        self.root = root
        self.index = 0
        # the first row of the file the preview shows
        self.offset = 0
        # the row of the file the cursor is on while reading it
        self.line = 0
        # whether the arrows move through the file rather than the listing
        self.reading = False
        self.error = ""

    @cached_property
    def buffer(self) -> Buffer:
        """
        Returns the path input below the list. Editing it clears any error and scrolls the
        preview back to the top.
        """
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
        self.offset = 0
        self.line = 0

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

    def move(self, step: int) -> None:
        """Move the cursor in the preview while reading, or in the list otherwise."""
        if self.reading:
            self.browse(step)
        else:
            self.scroll(step)

    def scroll(self, step: int) -> None:
        """Move the list selection by `step`, and show the new file from the top."""
        # the first press after typing gives the listing back rather than moving through it
        if self.buffer.text:
            self.buffer.text = ""
            return
        index = max(0, min(len(self.options) - 1, self.index + step))
        if index != self.index:
            self.index = index
            self.offset = 0
            self.line = 0

    def browse(self, step: int) -> None:
        """Move the preview cursor by `step`, and scroll the preview to keep it in view."""
        rows = len(self._content())
        self.line = max(0, min(rows - 1, self.line + step))
        # the preview follows the cursor out of either end
        viewport = self.viewport
        if self.line < self.offset:
            self.offset = self.line
        elif self.line >= self.offset + viewport:
            self.offset = self.line - viewport + 1

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
        plain bold while reading the preview, and hidden while a path is typed.
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
                if self.reading:
                    out.append(("bold", f"  > {label}"))
                else:
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
        Returns the visible part of the preview. A rule on the left shows ↑ or ↓ when the file
        continues above or below. While reading, the cursor line is drawn in reverse video.
        """
        rows = self._content()
        # the cursor is drawn as wide as the longest line, so that it shows on a blank one too
        width = max(
            len(text)
            for _, text in rows
        )
        viewport = self.viewport
        last = max(0, len(rows) - viewport)
        offset = min(self.offset, last)
        shown = rows[offset:offset + viewport]
        # padded to the viewport, so that the rule down the side runs the full height
        shown.extend(
            ("", "")
            for _ in range(viewport - len(shown))
        )
        out: StyleAndTextTuples = []
        for index, (style, text) in enumerate(shown):
            if index:
                out.append(("", "\n"))
            # the rule doubles as the scrollbar, pointing to where the file runs on
            if (
                index == 0
                and offset > 0
            ):
                out.append(("class:info", " ↑ "))
            elif (
                index == viewport - 1
                and offset < last
            ):
                out.append(("class:info", " ↓ "))
            else:
                out.append(("class:info", " │ "))
            if (
                self.reading
                and offset + index == self.line
            ):
                out.append((f"{style} reverse", text.ljust(max(width, 1))))
            else:
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
        picker does not resize while scrolling. It never exceeds the terminal rows left below
        the question.
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
        body = VSplit(
            [
                Window(
                    FormattedTextControl(self._listing),
                    width=width + 4,
                    height=lambda: Dimension(max=self.viewport),
                    dont_extend_height=True,
                ),
                Window(
                    FormattedTextControl(self._preview),
                    height=lambda: Dimension(max=self.viewport),
                    wrap_lines=False,
                    dont_extend_height=True,
                ),
            ]
        )
        field = Window(
            BufferControl(
                self.buffer,
                input_processors=[BeforeInput("  path: ", style="class:info")],
            ),
            height=1,
        )
        error = ConditionalContainer(
            Window(
                FormattedTextControl(lambda: [("class:error", f"  {self.error}")]),
                height=1,
            ),
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
            self.move(-1)

        @bindings.add("down", filter=idle)
        def _down(event: KeyPressEvent) -> None:
            self.move(1)

        # once a path is typed, left and right move through it instead
        @bindings.add("left", filter=idle & empty)
        def _left(event: KeyPressEvent) -> None:
            self.reading = False

        @bindings.add("right", filter=idle & empty)
        def _right(event: KeyPressEvent) -> None:
            self.reading = True

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


class Asked(Completer):
    """
    Wraps a completer so it offers nothing for an empty word unless <TAB> is pressed. Without
    this, every submission empties the prompt and would pop up the full list again.
    """

    def __init__(self, completer: Completer) -> None:
        self.completer = completer

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterator[Completion]:
        word = document.get_word_before_cursor(WORD=True)
        if (
            not word
            and not complete_event.completion_requested
        ):
            return
        yield from self.completer.get_completions(document, complete_event)


def ask(
        message: Callable[[], StyleAndTextTuples],
        request: Callable[[], str],
        submit: Callable[[str], None],
        completer: Completer,
        multiline: bool = False,
        extra: KeyBindings | None = None,
) -> None:
    """
    Run a prompt that stays open across submissions. Each submission is passed to `submit` and
    the prompt is cleared. `message` shows the current state and is redrawn in place. `request`
    is the instruction shown above the cursor.

    An empty submission closes the prompt. It is also passed to `submit`, so old notes are
    cleared before `message` is printed one last time. Bindings in `extra` take priority over
    the default <ENTER> binding.
    """
    bindings = KeyBindings()

    @bindings.add("enter")
    def _(event: KeyPressEvent) -> None:
        buffer = event.current_buffer
        submit(buffer.text)
        if buffer.text.strip():
            buffer.reset()
        else:
            buffer.validate_and_handle()

    # the last binding matching a key is the one that handles it
    merged = [bindings, tabbing()]
    if extra is not None:
        merged.append(extra)
    bindings = merge_key_bindings(merged)

    def framed() -> StyleAndTextTuples:
        out = message()
        out.append(("", f"  {request()}\n"))
        out.append(("class:echo.prompt", f"  {PROMPT}"))
        return out

    # the request and the cursor are spent once the prompt closes, so the prompt is erased and
    # only what the submissions made is left on screen
    session = PromptSession(erase_when_done=True, style=STYLE)
    # multiline only so that a paste keeps its lines; typed, enter submits
    session.prompt(
        framed,
        key_bindings=bindings,
        completer=Asked(completer),
        multiline=multiline,
        prompt_continuation=" " * (2 + len(PROMPT)),
    )
    print_formatted_text(FormattedText(message()), style=STYLE, end="")


def tally(
        rows: Sequence[str],
        project: Path,
        limit: int,
) -> tuple[Counter[str], bool]:
    """
    Returns how many files each row tracks now, and whether the walk stopped after `limit`
    files. A scope counts the files it decides, and an exclusion counts the files it leaves
    out. A root counts the files tracked beneath it.

    >>> tally(['./', '*.csv', 'data/*.png'], project, 20000)
    (Counter({'./': 3, '*.csv': 2, 'data/*.png': 1}), False)
    """
    dirs, scopes = build(rows, project)
    roots: dict[Path, list[str]] = {}
    for row in rows:
        try:
            base, pattern = parse(row, project)
        except ValueError:
            continue
        if (
            base is not None
            and not pattern
        ):
            roots.setdefault(base, []).append(row)
    metadata = project / ".trail"

    def descends(path: Path) -> bool:
        # the walk a scan makes, without the assets a Trail would already hold
        if (
            path.is_symlink()
            or path.is_relative_to(metadata)
            or container(path)
        ):
            return False
        return (
            not path.name.startswith(".")
            or any(
                directory.is_relative_to(path)
                for directory in dirs
            )
        )

    walks = {
        path: directory.recursive
        for path, directory in dirs.items()
    }
    out: Counter[str] = Counter()
    for seen, path in enumerate(walk(walks, descends), start=1):
        if seen > limit:
            return out, True
        if path.is_relative_to(metadata):
            continue
        line = match(path, dirs, scopes)
        if line is None:
            continue
        out[line] += 1
        if line.startswith("!"):
            continue
        for parent in path.parents:
            for root in roots.get(parent, ()):
                out[root] += 1
    return out, False


class Editor:
    """
    The list of lines the new `.markers` will hold. Each row shows its kind and how many files
    it tracks now.

    <UP> and <DOWN> select a row, and <ENTER> removes it. A typed line is added, or removed if
    it is already listed. Lines are compared in canonical form, so typing `.csv` removes
    `*.csv`. A paste only adds. <TAB> completes paths, extensions and the listed rows.

      ./          root                        3 files now
      *.csv       root scope                  2 files now
      data/       root                        covered by ./
    > data/*.png  dir scope · not subfolders  1 file now
      !*.tmp      root scope · exclude        0 files excluded
    """

    # terminal rows left to the hints, the notes and the prompt around the list
    margin = 14
    # files walked for the live counts before they are shown as a lower bound
    limit = 20000

    def __init__(
        self,
        lines: Iterable[str],
        home: Path,
        root: Path,
        imported: bool = False,
    ) -> None:
        """
        `lines` are the rows to start from. `home` is the project directory the rows resolve
        against, and `root` the directory typed paths are read from. `imported` says the rows
        came from a file of the user's own, so each anchored row gets a note.
        """
        self.home = home
        self.root = root
        self.rows: list[str] = list(dict.fromkeys(lines))
        self.notes: list[tuple[str, str]] = []
        # the index of the row under the cursor; None while at the prompt
        self.cursor: int | None = None
        # the first row shown, once there are more rows than fit
        self.offset = 0
        # whether the text being submitted was pasted, so that it only adds
        self.pasted = False
        # set by the closing submission, so that the whole list is printed once more
        self.done = False
        # the rows the summary was worked out for, and the summary
        self.summarized: tuple[tuple[str, ...], dict[str, tuple[str, str]]] | None = None
        if not imported:
            return
        # an anchored line was written for the project the file came from
        for row in self.rows:
            try:
                base, _ = parse(row, home)
            except ValueError:
                continue
            if (
                base is not None
                and row != "./"
            ):
                self.notes.append(("class:info", f"{row} resolves against {Renderer.home(home)}"))

    def form(self, row: str) -> str:
        """
        Returns a listed row in canonical form, which is how typed lines are compared.

        >>> editor.form('**/*.csv')
        '*.csv'
        """
        try:
            return canonical(row, self.home, self.home)
        except (OSError, RuntimeError, ValueError):
            return row

    def submit(self, text: str) -> None:
        """
        Toggle the typed line, or add each pasted line. An empty submission finishes and
        clears the notes.
        """
        pasted = self.pasted
        self.pasted = False
        self.notes.clear()
        lines = [
            line.strip()
            for line in text.splitlines()
            if line.strip()
        ]
        if not lines:
            self.done = True
            self.cursor = None
            return
        if len(lines) > 1:
            pasted = True
        for line in lines:
            self.enter(line, toggle=not pasted)

    def enter(
        self,
        text: str,
        toggle: bool,
    ) -> None:
        """
        Add a line, or remove it if `toggle` is set and it is already listed. A line that does
        not parse, names Trail metadata, or names a directory without its '/' is refused with a
        note.
        """
        home = self.home
        try:
            line = canonical(text, home, self.root)
            base, _ = parse(line, home)
        except (OSError, RuntimeError, ValueError) as error:
            self.notes.append(("class:error", f"{text}: {error}"))
            return
        listed = [
            index
            for index, row in enumerate(self.rows)
            if (
                row == line
                or self.form(row) == line
            )
        ]
        if listed:
            if toggle:
                for index in reversed(listed):
                    self.remove(index)
            else:
                self.notes.append(("class:info", f"{line} is already listed"))
            return
        if (
            base is not None
            and base.is_relative_to(home / ".trail")
        ):
            self.notes.append(("class:error", f"{line} is Trail metadata"))
            return
        if slashless(line, home) is not None:
            self.notes.append(
                ("class:error", f"{line} is a directory; end it with '/' to watch it")
            )
            return
        self.rows.append(line)

    def remove(self, index: int) -> None:
        """
        Remove the row at `index`, keeping the cursor on a row. Removing './' notes that the
        project directory will no longer be watched.
        """
        row = self.rows.pop(index)
        if self.form(row) == "./":
            self.notes.append(("class:info", "the project directory will no longer be watched"))
        if self.cursor is not None:
            if not self.rows:
                self.cursor = None
            else:
                self.cursor = min(self.cursor, len(self.rows) - 1)

    @property
    def summary(self) -> dict[str, tuple[str, str]]:
        """
        Maps each row to its kind and the count shown beside it. Both are worked out again only
        when the rows change, since counting walks the project.

        >>> editor.summary['data/']
        ('root', 'covered by ./')
        """
        rows = tuple(self.rows)
        if (
            self.summarized is not None
            and self.summarized[0] == rows
        ):
            return self.summarized[1]
        home = self.home
        counts, capped = tally(rows, home, self.limit)
        kinds = {
            row: kind(row, home)
            for row in rows
        }
        roots = {
            row: parse(row, home)[0]
            for row in rows
            if kinds[row] == "root"
        }
        out = {
            row: (kinds[row], self.count(row, kinds[row], counts, capped, roots))
            for row in rows
        }
        self.summarized = (rows, out)
        return out

    @staticmethod
    def count(
            row: str,
            described: str,
            counts: Counter[str],
            capped: bool,
            roots: dict[str, Path],
    ) -> str:
        """
        Returns the count shown beside a row. A root inside another root shows the root that
        covers it instead. Once the walk stopped early, a count is a lower bound and ends in '+'.
        """
        if described == "invalid":
            return ""
        if described == "root":
            base = roots[row]
            # a root inside another adds nothing to it
            for other, ancestor in roots.items():
                if (
                    base != ancestor
                    and base.is_relative_to(ancestor)
                ):
                    return f"covered by {other}"
        if capped:
            files = f"{counts[row]}+ files"
        else:
            files = plural(counts[row], "file")
        if row.startswith("!"):
            return f"{files} excluded"
        return f"{files} now"

    @property
    def viewport(self) -> int:
        """The rows shown at once, from the terminal height, as `Picker.viewport` does."""
        rows = get_app().output.get_size().rows
        return max(1, rows - self.margin)

    def message(self) -> StyleAndTextTuples:
        """
        Returns the rows, each with its kind and count, then the notes. While editing, only the
        rows that fit the terminal are shown, and the window follows the cursor. Once done, every
        row is shown. See the class docstring for an example.
        """
        fragments: StyleAndTextTuples = []
        if not self.rows:
            fragments.append(("class:info", "  none: only what you track yourself is tracked\n"))
        summary = self.summary
        width = max(
            (len(row) for row in self.rows),
            default=0,
        )
        kinds = max(
            (len(described) for described, _ in summary.values()),
            default=0,
        )
        if self.done:
            shown = range(len(self.rows))
        else:
            viewport = self.viewport
            # the window follows the cursor out of either end
            if self.cursor is not None:
                if self.cursor < self.offset:
                    self.offset = self.cursor
                elif self.cursor >= self.offset + viewport:
                    self.offset = self.cursor - viewport + 1
            else:
                self.offset = max(0, len(self.rows) - viewport)
            self.offset = min(self.offset, max(0, len(self.rows) - viewport))
            shown = range(self.offset, min(len(self.rows), self.offset + viewport))
            if self.offset:
                fragments.append(("class:info", f"  ↑ {self.offset} more\n"))
        for index in shown:
            row = self.rows[index]
            described, count = summary[row]
            if index == self.cursor:
                fragments.append(("class:selected-option", f"> {row:<{width}}  "))
            elif described == "invalid":
                fragments.append(("class:error", f"  {row:<{width}}  "))
            else:
                fragments.append(("class:kind", f"  {row:<{width}}  "))
            fragments.append(("class:info", f"{described:<{kinds}}  "))
            fragments.append(("", f"{count}\n"))
        if not self.done:
            below = len(self.rows) - shown.stop
            if below > 0:
                fragments.append(("class:info", f"  ↓ {below} more\n"))
        fragments.extend(
            (style, f"  {note}\n")
            for style, note in self.notes
        )
        return fragments

    def request(self) -> str:
        """Returns the instruction shown above the prompt, which depends on the cursor."""
        if self.cursor is not None:
            return "Press <ENTER> to remove this line, or <ESCAPE> to return:"
        if self.rows:
            return "Type a line to add or remove it, <UP> to pick one, or <ENTER> to continue:"
        return "Type a line to add it (or <ENTER> to continue):"

    @cached_property
    def bindings(self) -> KeyBindings:
        """
        Returns the editor's key bindings. <UP> and <DOWN> move the cursor through the rows,
        and <ENTER> removes the row under it. <ESCAPE> or typing returns to the prompt. A paste
        is marked, so that it only adds.
        """
        bindings = KeyBindings()
        browsing = Condition(lambda: self.cursor is not None)
        idle = ~has_completions & Condition(lambda: not get_app().current_buffer.text)

        @bindings.add("up", filter=idle)
        def _up(event: KeyPressEvent) -> None:
            if not self.rows:
                return
            if self.cursor is None:
                self.cursor = len(self.rows) - 1
            else:
                self.cursor = max(0, self.cursor - 1)
            self.notes.clear()

        @bindings.add("down", filter=idle & browsing)
        def _down(event: KeyPressEvent) -> None:
            if self.cursor == len(self.rows) - 1:
                self.cursor = None
            else:
                self.cursor += 1

        @bindings.add("enter", filter=browsing)
        def _remove(event: KeyPressEvent) -> None:
            self.notes.clear()
            self.remove(self.cursor)

        @bindings.add("escape", filter=browsing, eager=True)
        def _return(event: KeyPressEvent) -> None:
            self.cursor = None

        # typing hands the keys back to the prompt, starting with the one typed
        @bindings.add(Keys.Any, filter=browsing)
        def _type(event: KeyPressEvent) -> None:
            self.cursor = None
            event.current_buffer.insert_text(event.data)

        @bindings.add(Keys.BracketedPaste)
        def _paste(event: KeyPressEvent) -> None:
            self.cursor = None
            self.pasted = True
            text = (
                event.data
                .replace("\r\n", "\n")
                .replace("\r", "\n")
            )
            event.current_buffer.insert_text(text)

        return bindings

    def run(self) -> list[str]:
        """Run the editor. Returns the rows."""
        offered = completer(
            lambda: self.rows,
            lambda: self.home,
            lambda: self.root,
        )
        ask(
            self.message,
            self.request,
            self.submit,
            offered,
            multiline=True,
            extra=self.bindings,
        )
        return self.rows


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
    say("Let's start a Trail! 🥾", "bold")
    if existing is not None:
        say(f"Re-initializing the Trail at {Renderer.home(home)}; its log is kept.", "class:info")
    say("")

    path = home / ".markers"
    say("Markers say which directories to watch and which files to track in them. 🪧", "bold")
    hint(
        "Scroll and press <ENTER> to choose a marker set. 🪨",
        "Press <RIGHT> to scroll through a set's lines, and <LEFT> to return to the sets.",
        "Enter a path to load a markers file of your own.",
        "Press <ESCAPE> to start empty and track only what you add yourself.",
        "Next, you can add and remove lines one by one.",
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
    # the anchored lines of a file from elsewhere were written for the project it came from
    imported = picker.custom not in (None, path)
    say("")

    say("Here's what Trail will watch and track 🏷️:", "bold")
    hint(
        "A line ending in '/' is a directory to watch, subfolders included; './' is this one.",
        "A line like '*.csv' tracks those files in every watched directory, at any depth.",
        "'data/*.png' tracks the png files directly in data; 'data/**/*.png' reaches subfolders.",
        "Start a line with '!' to leave files out, as in '!requirements*.txt'.",
        "Type a line to add it, or type it again to remove it. <TAB> completes.",
        "<UP> picks a line and <ENTER> removes it. <ENTER> on an empty prompt continues.",
        "You can always edit `.markers` afterward, or use `mark` and `unmark` in the console.",
    )
    say("")
    rows = Editor(Text.markers(lines), home, root, imported).run()
    say("")

    # `trail` on startup is asked here once `startup` is implemented

    path.write_text(Text.compose(lines, rows), encoding="utf-8")
    # opening the Trail scans for what the markers indicate; naming .trail itself opens the
    # directory chosen, even nested in another Trail
    trail = Trail(home / ".trail", markers=None)

    say(f"Trail started in {Renderer.home(home)} 🌲", "bold")
    say(f"  markers: {' '.join(trail.markers) or 'none'}", "class:info")
    assets = plural(len(trail.assets), "asset")
    dirs = plural(len(trail.dirs), "dir")
    say(f"  tracked: {assets} in {dirs}", "class:info")
    for warning in trail.markers.warnings:
        say(f"  {warning}", "class:error")
    say("")
    return trail
