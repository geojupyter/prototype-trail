from __future__ import annotations

from pathlib import Path

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
