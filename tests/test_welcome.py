from __future__ import annotations

from pathlib import Path

import pytest
from prompt_toolkit.formatted_text import StyleAndTextTuples, fragment_list_to_text

from trail.cli.console import Console
from trail.trail import Trail

ID = "1ff756ca11b54b3994c7b26bbda99c0a"


class TestWelcome:
    """
    The welcome at the top of the banner. Its emblem is picked by the Trail id, so a Trail
    shows the same glyphs every time it is opened.
    """

    @staticmethod
    def welcome(
        identifier: str,
        returning: bool,
    ) -> str:
        trail = Trail()
        trail.id = identifier
        console = Console(trail, Path.cwd())
        row = console.renderer.welcome(returning)
        return "".join(fragment[1] for fragment in row)

    def test_the_emblem_is_fixed_by_the_id(self) -> None:
        """A salted hash would agree with itself within one process, so the glyphs are spelled out."""
        assert self.welcome(ID, True) == "🍃🍄🌲 Welcome back to Trail! 🌲🍄🍃"

    def test_a_new_trail_is_not_welcomed_back(self) -> None:
        assert self.welcome(ID, False) == "🍃🍄🌲 Welcome to Trail! 🌲🍄🍃"

    @pytest.mark.parametrize("is_welcomed", [False, True])
    def test_the_banner_welcomes_only_if_init_did_not(
            self,
            is_welcomed: bool,
            tmp_path: Path,
            monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def show(rows: list[StyleAndTextTuples]) -> None:
            return None

        console = Console(Trail(tmp_path, ontrail=None), tmp_path, is_welcomed=is_welcomed)
        monkeypatch.setattr(console.feed, "show", show)
        console.run.banner()
        lines = [
            fragment_list_to_text(row)
            for row in console.feed.rows
        ]
        is_shown = any("Welcome to Trail!" in line for line in lines)
        assert is_shown is not is_welcomed
        assert lines[0].startswith("trail #") is is_welcomed
