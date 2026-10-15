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

from trail.cli.init import Paths, Picker, init, offer
from trail.markers import MARKERS, available
from trail.trail import Trail

ENTER = "\r"
UP = "\x1b[A"
DOWN = "\x1b[B"
ESCAPE = "\x1b"
TAB = "\t"
INTERRUPT = "\x03"


class TestInit:
    """
    The walkthrough driven the way a terminal drives it. The keys are sent up front. Each prompt
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

    def test_accepting_the_default_starts_a_trail_with_the_default_markers(self) -> None:
        with self.workspace() as root:
            trail = self.run(root, ENTER)
            assert trail.dir == root / ".trail"
            default = (MARKERS / "default.txt").read_text(encoding="utf-8")
            assert (root / ".markers").read_text(encoding="utf-8") == default
            assert list(trail.markers) == self.default()
            assert trail.dirs[root].root
            # the default watches the project but marks no files
            assert not trail.assets

    def test_the_arrows_choose_another_marker_set(self) -> None:
        with self.workspace() as root:
            trail = self.run(root, DOWN, ENTER)
            assert list(trail.markers) == (
                (MARKERS / "general.txt").read_text(encoding="utf-8").splitlines()
            )
            assert root / "top.csv" in trail.assets

    def test_escape_leaves_a_blank_markers_file(self) -> None:
        with self.workspace() as root:
            (root / ".markers").write_text("# mine\n./\n*.md\n", encoding="utf-8")
            trail = self.run(root, ESCAPE)
            assert (root / ".markers").read_text(encoding="utf-8") == ""
            assert not trail.markers
            assert not trail.dirs
            assert not trail.assets

    def test_a_typed_path_chooses_a_markers_file_of_ones_own(self) -> None:
        with self.workspace() as root, TemporaryDirectory() as elsewhere:
            source = Path(elsewhere) / "mine.markers"
            source.write_text("# mine\n./\n*.md\nfolder/*.csv\n", encoding="utf-8")
            trail = self.run(root, str(source), ENTER)
            text = (root / ".markers").read_text(encoding="utf-8")
            assert text == "# mine\n./\n*.md\nfolder/*.csv\n"
            assert root / "notes.md" in trail.assets
            assert root / "folder" / "nested.csv" in trail.assets
            assert root / "top.csv" not in trail.assets
            # an anchored line was written for the project the file came from
            [warning] = trail.markers.warnings
            assert "'folder/*.csv' was imported" in warning

    def test_a_typed_directory_offers_the_markers_inside_it(self) -> None:
        with self.workspace() as root, TemporaryDirectory() as elsewhere:
            (Path(elsewhere) / ".markers").write_text("./\n*.md\n", encoding="utf-8")
            trail = self.run(root, elsewhere, ENTER)
            assert list(trail.markers) == ["./", "*.md"]

    def test_a_completed_directory_is_typed_on_into(self) -> None:
        with self.workspace() as root:
            (root / "presets").mkdir()
            (root / "presets" / "mine.markers").write_text("./\n*.md\n", encoding="utf-8")
            trail = self.run(root, "./pre", TAB, TAB, ENTER, pace=0.1)
            assert list(trail.markers) == ["./", "*.md"]

    def test_an_arrow_after_typing_returns_to_the_markers_files(self) -> None:
        with self.workspace() as root:
            trail = self.run(root, "missing", UP, ENTER)
            assert list(trail.markers) == self.default()

    def test_reinitializing_starts_from_the_default_markers(self) -> None:
        with self.workspace() as root:
            (root / ".markers").write_text("# mine\n./\n*.md\n", encoding="utf-8")
            first = Trail(root)
            events = len(first.events)
            trail = self.run(root / "folder", ENTER)
            assert trail.dir == root / ".trail"
            assert trail.id == first.id
            assert len(trail.events) >= events
            default = (MARKERS / "default.txt").read_text(encoding="utf-8")
            assert (root / ".markers").read_text(encoding="utf-8") == default
            # what was tracked before stays tracked
            assert root / "notes.md" in trail.assets

    def test_reinitializing_keeps_the_project_markers_when_they_are_named(self) -> None:
        with self.workspace() as root:
            mine = "# mine\n./\n*.md\nfolder/*.csv\n"
            (root / ".markers").write_text(mine, encoding="utf-8")
            Trail(root)
            trail = self.run(root, ".markers", ENTER)
            assert (root / ".markers").read_text(encoding="utf-8") == mine
            # its anchored lines belong to this project, so nothing is reported as imported
            assert not trail.markers.warnings

    def test_an_interrupt_writes_nothing(self) -> None:
        with self.workspace() as root:
            with pytest.raises(KeyboardInterrupt):
                self.run(root, INTERRUPT)
            assert not (root / ".trail").exists()
            assert not (root / ".markers").exists()


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
    for suite in (TestInit(), TestPaths(), TestPicker()):
        for method_name in dir(suite):
            if method_name.startswith("test_"):
                getattr(suite, method_name)()
                print(f"  ✓ {method_name.removeprefix('test_').replace('_', ' ')}")
