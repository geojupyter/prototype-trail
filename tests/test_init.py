from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.output.plain_text import PlainTextOutput
from prompt_toolkit.output.vt100 import Vt100_Output

from trail.cli.console import Console
from trail.cli.init import init
from trail.cli.render import Renderer
from trail.offtrail import OffTrail
from trail.ontrail import OnTrail
from trail.trail import Trail


class TestInit:
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
            is_running: bool = False,
    ) -> tuple[Trail, str]:
        """Returns the Trail that `init` opened and the text it printed."""
        stream = StringIO()
        with create_app_session(output=PlainTextOutput(stream)):
            trail = init(root, is_running)
        return trail, stream.getvalue()

    @staticmethod
    def preset(rules: type[OnTrail | OffTrail]) -> str:
        return rules.preset.read_text(encoding="utf-8")

    def test_a_new_trail_starts_from_the_presets(self) -> None:
        with self.workspace() as root:
            trail, _ = self.run(root)
            assert trail.dir == root / ".trail"
            assert (root / ".ontrail").read_text(encoding="utf-8") == self.preset(OnTrail)
            assert (root / ".offtrail").read_text(encoding="utf-8") == self.preset(OffTrail)
            assert trail.dirs[root].root
            assert root / "top.csv" in trail.assets
            assert root / "notes.md" not in trail.assets
            assert not trail.ontrail.imported
            assert not trail.ontrail.warnings

    @staticmethod
    def welcome(trail: Trail) -> str:
        row = Console(trail, trail.dir.parent).renderer.welcome(returning=False)
        return "".join(fragment[1] for fragment in row)

    def test_the_message_opens_with_the_welcome_and_ends_with_the_paths(self) -> None:
        with self.workspace() as root:
            trail, text = self.run(root)
            lines = text.splitlines()
            assert lines[1] == self.welcome(trail)
            assert sum("Welcome" in line for line in lines) == 1
            assert "data/           Check the data folder (relative to project root)" in lines
            # the paths start in the column of the examples' meanings
            header = lines.index("See Also")
            assert lines[header + 2:header + 6] == [
                f"Project         {Renderer.home(root)}",
                f"Ontrail         {root / '.ontrail'}",
                f"Offtrail        {root / '.offtrail'}",
                "Docs            https://geojupyter.github.io/trail",
            ]
            assert "\x1b" not in text

    def test_a_terminal_links_each_path_to_its_uri(self) -> None:
        with self.workspace() as root:
            stream = StringIO()
            output = Vt100_Output(stream, lambda: Size(rows=24, columns=80), term="xterm")
            with (
                patch.dict(os.environ, TERM="xterm"),
                create_app_session(output=output),
            ):
                init(root)
            text = stream.getvalue()
            ontrail = root / ".ontrail"
            assert f"\x1b]8;;{root.as_uri()}\x1b\\{Renderer.home(root)}\x1b]8;;\x1b\\" in text
            assert f"\x1b]8;;{ontrail.as_uri()}\x1b\\{ontrail}\x1b]8;;\x1b\\" in text

    def test_an_existing_ontrail_is_kept(self) -> None:
        with self.workspace() as root:
            mine = "# mine\n./\n*.md\n"
            (root / ".ontrail").write_text(mine, encoding="utf-8")
            trail, _ = self.run(root)
            assert (root / ".ontrail").read_text(encoding="utf-8") == mine
            assert root / "notes.md" in trail.assets
            assert root / "top.csv" not in trail.assets

    def test_reinitializing_keeps_the_rules_and_the_log(self) -> None:
        with self.workspace() as root:
            mine = "# mine\n./\n*.md\n"
            (root / ".ontrail").write_text(mine, encoding="utf-8")
            first = Trail(root)
            ignored = "# mine\n.*\n*.tmp\n"
            (root / ".offtrail").write_text(ignored, encoding="utf-8")
            events = len(first.events)
            trail, text = self.run(root / "folder")
            assert trail.dir == root / ".trail"
            assert trail.id == first.id
            assert len(trail.events) >= events
            assert (root / ".ontrail").read_text(encoding="utf-8") == mine
            assert (root / ".offtrail").read_text(encoding="utf-8") == ignored
            assert root / "notes.md" in trail.assets
            # the project is the one holding .trail, not the directory init was run in
            assert f"Project         {Renderer.home(root)}" in text.splitlines()

    def test_reinitializing_restores_a_missing_ontrail(self) -> None:
        with self.workspace() as root:
            Trail(root, ontrail=None)
            assert not (root / ".ontrail").exists()
            trail, _ = self.run(root)
            assert (root / ".ontrail").read_text(encoding="utf-8") == self.preset(OnTrail)
            assert root / "top.csv" in trail.assets

    @pytest.mark.init_record
    def test_reinitializing_adds_no_initialized_record(self) -> None:
        with self.workspace() as root:
            first = Trail(root)
            [initialized] = first.events.select('cls=InitTrailEvent')
            trail, _ = self.run(root)
            assert trail.events.select('cls=InitTrailEvent').ids == [initialized.id]

    def test_problems_with_the_rules_are_reported(self) -> None:
        with self.workspace() as root:
            (root / ".ontrail").write_text("./\n!\n", encoding="utf-8")
            _, text = self.run(root)
            assert "/.ontrail:2: Not a rule: '!'" in text


if __name__ == "__main__":
    suite = TestInit()
    for method_name in dir(suite):
        if method_name.startswith("test_"):
            getattr(suite, method_name)()
            print(f"  ✓ {method_name.removeprefix('test_').replace('_', ' ')}")
