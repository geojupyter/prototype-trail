from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from trail.cli.init import Editor, Paths, Picker, init, offer
from trail.markers import MARKERS, available
from trail.trail import Trail

ENTER = "\r"
UP = "\x1b[A"
DOWN = "\x1b[B"
ESCAPE = "\x1b"
TAB = "\t"
INTERRUPT = "\x03"
# emacs' unix-line-discard, which empties a prompt that came filled in
DISCARD = "\x15"


class TestInit:
    """
    The walkthrough driven the way a terminal drives it. The keys are sent up front; each prompt
    takes what it needs and leaves the rest as typeahead for the next one.
    """

    @staticmethod
    @contextmanager
    def workspace() -> Iterator[Path]:
        with TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            root = base / "project"
            root.mkdir()
            (root / "top.csv").write_text("a\n", encoding="utf-8")
            (root / "notes.md").write_text("a\n", encoding="utf-8")
            (root / "folder").mkdir()
            (root / "folder" / "nested.csv").write_text("a\n", encoding="utf-8")
            yield root

    @staticmethod
    def run(
            root: Path,
            *keys: str,
            pace: float = 0.0,
    ) -> Trail:
        """
        `pace` sends the keys one at a time, that many seconds apart, the way they are typed. A
        burst merges tab's request for completions into the one typing already started, and the
        request is lost.
        """
        with (
            create_pipe_input() as pipe,
            create_app_session(input=pipe, output=DummyOutput()),
        ):
            if not pace:
                pipe.send_text("".join(keys))
                return init(root)

            def keystrokes() -> None:
                for key in keys:
                    time.sleep(pace)
                    pipe.send_text(key)

            typist = threading.Thread(target=keystrokes, daemon=True)
            typist.start()
            try:
                return init(root)
            finally:
                typist.join()

    @staticmethod
    def default() -> list[str]:
        return (MARKERS / "default.txt").read_text(encoding="utf-8").splitlines()

    def test_accepting_every_default_starts_a_trail_with_the_default_markers(self) -> None:
        with self.workspace() as root:
            trail = self.run(root, ENTER, ENTER)
            assert trail.dir == root / ".trail"
            default = (MARKERS / "default.txt").read_text(encoding="utf-8")
            assert (root / ".markers").read_text(encoding="utf-8") == default
            assert list(trail.markers) == self.default()
            assert trail.dirs[root].root
            # the default watches the project but marks no files
            assert not trail.assets

    def test_escape_turns_down_the_default_markers(self) -> None:
        with self.workspace() as root:
            trail = self.run(root, ESCAPE, "./", ENTER, "md", ENTER, ENTER)
            assert list(trail.markers) == ["./", "*.md"]
            assert (root / ".markers").read_text(encoding="utf-8") == "./\n*.md\n"
            assert root / "notes.md" in trail.assets
            assert root / "top.csv" not in trail.assets

    def test_a_typed_path_chooses_a_markers_file_of_ones_own(self) -> None:
        with self.workspace() as root, TemporaryDirectory() as elsewhere:
            source = Path(elsewhere) / "mine.markers"
            source.write_text("# mine\n./\n*.md\n", encoding="utf-8")
            trail = self.run(root, str(source), ENTER, ENTER)
            assert (root / ".markers").read_text(encoding="utf-8") == "# mine\n./\n*.md\n"
            assert root / "notes.md" in trail.assets
            assert root / "top.csv" not in trail.assets

    def test_a_typed_directory_offers_the_markers_inside_it(self) -> None:
        with self.workspace() as root, TemporaryDirectory() as elsewhere:
            (Path(elsewhere) / ".markers").write_text("./\n*.md\n", encoding="utf-8")
            trail = self.run(root, elsewhere, ENTER, ENTER)
            assert list(trail.markers) == ["./", "*.md"]

    def test_an_arrow_after_typing_returns_to_the_markers_files(self) -> None:
        with self.workspace() as root:
            trail = self.run(root, "missing", UP, ENTER, ENTER)
            assert list(trail.markers) == self.default()

    def test_lines_toggle_in_canonical_form(self) -> None:
        with self.workspace() as root:
            trail = self.run(
                root,
                ESCAPE,
                "./",
                ENTER,
                "csv",
                ENTER,
                "md",
                ENTER,
                # the same line written another way takes it back
                "**/*.md",
                ENTER,
                ".tif",
                ENTER,
                ENTER,
            )
            assert list(trail.markers) == ["./", "*.csv", "*.tif"]

    def test_an_invalid_line_is_refused_without_ending_the_step(self) -> None:
        with self.workspace() as root:
            trail = self.run(root, ESCAPE, "!", ENTER, "./", ENTER, "md", ENTER, ENTER)
            assert list(trail.markers) == ["./", "*.md"]

    def test_entered_paths_become_lines(self) -> None:
        with self.workspace() as root, TemporaryDirectory() as elsewhere:
            outside = Path(elsewhere).resolve()
            (outside / "far.csv").write_text("a\n", encoding="utf-8")
            (outside / "kept.md").write_text("a\n", encoding="utf-8")
            trail = self.run(
                root,
                DOWN,
                ENTER,
                "notes.md",
                ENTER,
                str(outside / "kept.md"),
                ENTER,
                str(outside),
                ENTER,
                ENTER,
            )
            assert list(trail.markers)[-3:] == [
                "./notes.md",
                f"{outside}/kept.md",
                f"{outside}/",
            ]
            assert root / "notes.md" in trail.assets
            assert outside / "kept.md" in trail.assets
            assert outside in trail.dirs
            assert outside / "far.csv" in trail.assets

    def test_the_line_under_the_cursor_is_removed(self) -> None:
        with self.workspace() as root, TemporaryDirectory() as elsewhere:
            outside = Path(elsewhere).resolve()
            (outside / "far.csv").write_text("a\n", encoding="utf-8")
            (outside / "kept.md").write_text("a\n", encoding="utf-8")
            trail = self.run(
                root,
                DOWN,
                ENTER,
                str(outside / "kept.md"),
                ENTER,
                str(outside),
                ENTER,
                # the cursor starts on the last line listed
                UP,
                ENTER,
                ESCAPE,
                ENTER,
            )
            assert outside / "kept.md" in trail.assets
            # the line for kept.md still names the directory, but no longer as a root
            assert not trail.dirs[outside].root
            assert outside / "far.csv" not in trail.assets

    def test_a_pasted_list_only_adds(self) -> None:
        with self.workspace() as root:
            (root / "other.md").write_text("a\n", encoding="utf-8")
            paste = "\x1b[200~notes.md   \r\nother.md\r\n*.csv\r\n\x1b[201~"
            single = "\x1b[200~*.txt\x1b[201~"
            trail = self.run(root, DOWN, ENTER, paste, ENTER, single, ENTER, ENTER)
            assert root / "notes.md" in trail.assets
            assert root / "other.md" in trail.assets
            # the lines already listed stay, rather than being toggled off
            assert "*.csv" in trail.markers
            assert "*.txt" in trail.markers

    def test_the_arrows_choose_another_marker_set(self) -> None:
        with self.workspace() as root:
            trail = self.run(root, DOWN, ENTER, ENTER)
            assert list(trail.markers) == (
                (MARKERS / "general.txt").read_text(encoding="utf-8").splitlines()
            )

    def test_reinitializing_starts_from_the_default_markers(self) -> None:
        with self.workspace() as root:
            (root / ".markers").write_text("# mine\n./\n*.md\n", encoding="utf-8")
            first = Trail(root)
            events = len(first.events)
            trail = self.run(root / "folder", ENTER, ENTER)
            assert trail.dir == root / ".trail"
            assert trail.id == first.id
            assert len(trail.events) >= events
            default = (MARKERS / "default.txt").read_text(encoding="utf-8")
            assert (root / ".markers").read_text(encoding="utf-8") == default
            # what was tracked before stays tracked
            assert root / "notes.md" in trail.assets

    def test_reinitializing_keeps_the_project_markers_when_they_are_named(self) -> None:
        with self.workspace() as root:
            (root / ".markers").write_text("# mine\n./\n*.md\n", encoding="utf-8")
            Trail(root)
            self.run(root, ".markers", ENTER, ENTER)
            assert (root / ".markers").read_text(encoding="utf-8") == "# mine\n./\n*.md\n"

    def test_escape_with_nothing_added_leaves_a_blank_markers_file(self) -> None:
        with self.workspace() as root:
            (root / ".markers").write_text("# mine\n./\n*.md\n", encoding="utf-8")
            trail = self.run(root, ESCAPE, ENTER)
            assert (root / ".markers").read_text(encoding="utf-8") == ""
            assert not trail.markers
            assert not trail.dirs
            assert root / "top.csv" not in trail.assets

    def test_a_completed_directory_is_typed_on_into(self) -> None:
        with self.workspace() as root:
            (root / "folder" / "deep.md").write_text("a\n", encoding="utf-8")
            trail = self.run(
                root,
                ESCAPE,
                "./fol",
                TAB,
                "*.md",
                ENTER,
                ENTER,
                pace=0.1,
            )
            assert list(trail.markers) == ["folder/*.md"]
            assert root / "folder" / "deep.md" in trail.assets

    def test_an_interrupt_writes_nothing(self) -> None:
        with self.workspace() as root:
            with pytest.raises(KeyboardInterrupt):
                self.run(root, ENTER, "md", ENTER, INTERRUPT)
            assert not (root / ".trail").exists()
            assert not (root / ".markers").exists()

    def test_an_interrupt_in_the_picker_writes_nothing(self) -> None:
        with self.workspace() as root:
            with pytest.raises(KeyboardInterrupt):
                self.run(root, INTERRUPT)
            assert not (root / ".trail").exists()


class TestEditor:
    """The list editor of `init`, driven through its methods rather than its keys."""

    @staticmethod
    @contextmanager
    def editor(*lines: str) -> Iterator[Editor]:
        with (
            TemporaryDirectory() as temporary,
            create_pipe_input() as pipe,
            create_app_session(input=pipe, output=DummyOutput()),
        ):
            home = Path(temporary).resolve()
            (home / "data").mkdir()
            (home / "a.csv").write_text("a\n", encoding="utf-8")
            (home / "data" / "b.csv").write_text("a\n", encoding="utf-8")
            (home / "data" / "c.png").write_text("a\n", encoding="utf-8")
            (home / "data" / "d.tmp").write_text("a\n", encoding="utf-8")
            yield Editor(lines, home, home)

    @staticmethod
    def text(editor: Editor) -> list[str]:
        return "".join(
            fragment[1]
            for fragment in editor.message()
        ).splitlines()

    def test_rows_show_their_kind_and_count(self) -> None:
        with self.editor("./", "*.csv", "data/", "data/*.png", "!data/*.tmp") as editor:
            rows = [
                line.split()
                for line in self.text(editor)
            ]
            # the excluded d.tmp is not among them
            assert rows[0] == ["./", "root", "3", "files", "now"]
            assert rows[1] == ["*.csv", "root", "scope", "2", "files", "now"]
            assert rows[2] == ["data/", "root", "covered", "by", "./"]
            assert rows[3][-3:] == ["1", "file", "now"]
            assert "not" in rows[3]
            assert rows[4][-3:] == ["1", "file", "excluded"]

    def test_a_typed_line_toggles_in_canonical_form(self) -> None:
        with self.editor("./", "*.csv") as editor:
            editor.submit(".csv")
            assert editor.rows == ["./"]
            editor.submit("csv")
            assert editor.rows == ["./", "*.csv"]
            editor.submit("data")
            assert editor.rows == ["./", "*.csv", "data/"]
            editor.submit("./data/")
            assert editor.rows == ["./", "*.csv"]

    def test_a_paste_only_adds(self) -> None:
        with self.editor("./", "*.csv") as editor:
            editor.pasted = True
            editor.submit("*.csv")
            assert editor.rows == ["./", "*.csv"]
            assert editor.notes == [("class:info", "*.csv is already listed")]
            editor.submit("*.csv\n*.png")
            assert editor.rows == ["./", "*.csv", "*.png"]

    def test_removing_the_project_directory_says_so(self) -> None:
        with self.editor("./", "*.csv") as editor:
            editor.submit(".")
            assert editor.rows == ["*.csv"]
            assert editor.notes == [
                ("class:info", "the project directory will no longer be watched"),
            ]

    def test_invalid_lines_and_trail_metadata_are_refused(self) -> None:
        with self.editor("./") as editor:
            editor.submit("!")
            assert editor.rows == ["./"]
            assert editor.notes[0][0] == "class:error"
            editor.submit(".trail/")
            assert editor.rows == ["./"]
            assert editor.notes == [("class:error", ".trail/ is Trail metadata")]

    def test_the_list_is_held_to_the_terminal(self) -> None:
        lines = [
            f"*.x{index}"
            for index in range(60)
        ]
        with self.editor(*lines) as editor:
            text = self.text(editor)
            viewport = editor.viewport
            assert viewport < 60
            assert text[0] == f"  ↑ {60 - viewport} more"
            assert len(text) == viewport + 1
            # the cursor pulls the window back up
            editor.cursor = 0
            text = self.text(editor)
            assert text[0].startswith("> *.x0")
            assert text[-1] == f"  ↓ {60 - viewport} more"
            editor.submit("")
            assert len(self.text(editor)) == 60

    def test_counts_past_the_limit_are_a_lower_bound(self) -> None:
        with self.editor("./", "*.csv") as editor:
            editor.limit = 1
            rows = self.text(editor)
            assert "1+ files now" in rows[1]


class TestPaths:
    def test_a_directory_completes_through_its_slash(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "folder").mkdir()
            (root / "file.csv").write_text("a\n", encoding="utf-8")
            completer = Paths(get_paths=lambda: [str(root)])
            completions = {
                completion.text
                for completion in completer.get_completions(
                    Document("f"),
                    CompleteEvent(completion_requested=True),
                )
            }
            assert completions == {"older/", "ile.csv"}

class TestPicker:
    def test_the_preview_follows_the_cursor_and_then_the_typed_path(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "a.markers").write_text("*.csv\n", encoding="utf-8")
            (root / "b.markers").write_text("# b\n*.tif\n!\n", encoding="utf-8")
            (root / "c.markers").write_text("*.md\n", encoding="utf-8")
            options = [
                ("a", root / "a.markers"),
                ("b", root / "b.markers"),
            ]
            picker = Picker(options, root)
            assert picker._content() == [("class:kind", "*.csv")]
            # the rule runs as deep as the longest file offered, whichever is under the cursor
            assert picker._preview().count(("class:info", " │ ")) == 3
            picker.scroll(1)
            picker.scroll(1)
            assert picker.index == 1
            assert picker._content() == [
                ("class:info", "# b"),
                ("class:kind", "*.tif"),
                ("class:error", "!"),
            ]
            picker.buffer.text = "c.markers"
            assert picker.source == root / "c.markers"
            assert picker.accept()[1] == ["*.md"]
            picker.buffer.text = "missing"
            assert picker.accept() is None
            assert "missing" in picker.error

    def test_a_file_longer_than_the_preview_is_cut_off(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            long = root / "long.markers"
            long.write_text("".join(f"x{i}\n" for i in range(200)), encoding="utf-8")
            picker = Picker([("long", long)], root)
            viewport = picker.viewport
            # held to the terminal, which the file outruns
            assert viewport < 200
            texts = [
                text
                for _, text in picker._preview()
                if text not in (" │ ", "\n")
            ]
            assert len(texts) == viewport
            assert texts[0] == "x0"
            assert texts[-1] == f"… {200 - viewport + 1} more lines"

    def test_only_the_markers_files_are_offered_and_the_default_first(self) -> None:
        labels = [
            label
            for label, _ in offer()
        ]
        assert labels[0] == "default"
        assert sorted(labels) == available()


if __name__ == "__main__":
    for suite in (TestInit(), TestEditor(), TestPaths(), TestPicker()):
        for method_name in dir(suite):
            if method_name.startswith("test_"):
                getattr(suite, method_name)()
                print(f"  ✓ {method_name.removeprefix('test_').replace('_', ' ')}")
