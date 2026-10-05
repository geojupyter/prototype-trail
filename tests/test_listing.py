from __future__ import annotations

import io
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from trail.cli.console import Console
from trail.trail import Trail


class TestListing:
    """
    The commands that select out of a collection. The feed prints its rows to the terminal;
    the printing is swallowed and the rows are read back off the feed instead.
    """

    @staticmethod
    @contextmanager
    def workspace() -> Iterator[Path]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "folder").mkdir()
            for name in ("a.csv", "b.csv", "c.csv"):
                (root / name).write_text(f"{name}\n", encoding="utf-8")
            yield root

    @classmethod
    @contextmanager
    def session(cls) -> Iterator[Console]:
        with ExitStack() as stack:
            root = stack.enter_context(cls.workspace())
            pipe = stack.enter_context(create_pipe_input())
            stack.enter_context(create_app_session(input=pipe, output=DummyOutput()))
            console = Console(Trail(root, markers=None), root)
            cls.run(console, "track a.csv b.csv c.csv folder")
            yield console

    @staticmethod
    def run(
        console: Console,
        line: str,
    ) -> str:
        """Submit a line and return only what it wrote, with the printing swallowed."""
        before = len(console.feed.rows)
        with redirect_stdout(io.StringIO()):
            console.submit(line)
        return "\n".join(
            "".join(fragment[1] for fragment in row)
            for row in console.feed.rows[before:]
        )

    @staticmethod
    def positions(written: str) -> list[int]:
        return [
            int(line.split(".", 1)[0])
            for line in written.splitlines()
            if line and line[0].isdigit() and ". " in line
        ]

    def test_a_slice_counts_the_way_python_counts(self) -> None:
        with self.session() as console:
            assert self.positions(self.run(console, "events :2")) == [0, 1]
            assert self.positions(self.run(console, "events -2:")) == [2, 3]
            assert self.positions(self.run(console, "events 3:")) == [3]
            assert self.positions(self.run(console, "events 1:3")) == [1, 2]

    def test_a_filter_keeps_the_position_the_collection_addresses(self) -> None:
        with self.session() as console:
            asset = console._trail.assets[console.root / "c.csv"]
            written = self.run(console, f"events asset={asset.id}")
            # c.csv was tracked after two other assets, and keeps that position however few
            # are shown
            assert self.positions(written) == [2]
            assert asset.id in written

    def test_a_path_filter_takes_either_way_of_writing_it(self) -> None:
        with self.session() as console:
            absolute = self.run(console, f"events src_path={console.root / 'b.csv'}")
            relative = self.run(console, "events src_path=b.csv")
            assert self.positions(absolute) == self.positions(relative) == [1]

    def test_every_term_names_its_field(self) -> None:
        with self.session() as console:
            trail = console._trail
            event = trail.events.select[1]
            asset = trail.assets[console.root / "c.csv"]
            assert self.positions(self.run(console, f"events id={event.id}")) == [1]
            assert self.positions(self.run(console, f"events asset={asset.id}")) == [2]
            # a bare value is not guessed at, and the refusal says how to name it
            written = self.run(console, f"events {event.id}")
            assert "not a comparison" in written
            assert f"id={event.id}" in written

    def test_assets_and_dirs_list_their_own_collections(self) -> None:
        with self.session() as console:
            root = console.root
            assert self.positions(self.run(console, "assets :2")) == [0, 1]
            # a tracked directory is an asset, listed with the files
            assert self.positions(self.run(console, "assets 1:")) == [1, 2, 3]
            written = self.run(console, f"assets path={root / 'b.csv'}")
            assert self.positions(written) == [1]
            assert "Asset" in written
            assert self.positions(self.run(console, "assets path=folder")) == [3]
            assert "Dirs (0)" in self.run(console, "dirs")
            (root / "data").mkdir()
            self.run(console, "mark ./ data/*.csv folder/*.csv")
            written = self.run(console, "dirs")
            # a directory asset is never a Dir, so folder is left out
            assert "Dirs (2)" in written
            assert f"path: {root / 'data'}" in written
            assert "./*.csv," in written

    def test_or_takes_either_side(self) -> None:
        with self.session() as console:
            assert self.positions(self.run(console, "assets path=a.csv or path=b.csv")) == [0, 1]
            # side by side, the one field twice has to hold both, which no record does
            assert "nothing matched" in self.run(console, "assets path=a.csv path=b.csv")

    def test_terms_side_by_side_all_have_to_hold(self) -> None:
        with self.session() as console:
            assert self.positions(self.run(console, "assets path=a.csv name=a.csv")) == [0]
            assert self.positions(self.run(console, "assets path=a.csv and name=a.csv")) == [0]
            # the one record cannot hold both, so pairing them keeps nothing
            assert "nothing matched" in self.run(console, "assets path=a.csv name=b.csv")

    def test_a_slice_cuts_what_the_terms_before_it_left(self) -> None:
        with self.session() as console:
            # three records name a.csv, once it has been let go of and taken back
            self.run(console, "offtrail a.csv")
            self.run(console, "track a.csv")
            assert self.positions(self.run(console, "events src_path=a.csv")) == [0, 4, 5]
            assert self.positions(self.run(console, "events src_path=a.csv -2:")) == [4, 5]
            assert self.positions(self.run(console, "events -5: src_path=b.csv")) == [1]
            assert "nothing matched" in self.run(console, "events -4: src_path=b.csv")
            # the position stays the one the collection addresses, not the one among the results
            grouped = self.run(console, "events (src_path=a.csv or src_path=b.csv) -3:")
            assert self.positions(grouped) == [1, 4, 5]
            # ungrouped, the slice binds to the side of the `or` it was written on
            ungrouped = self.run(console, "events src_path=b.csv or src_path=a.csv -1:")
            assert self.positions(ungrouped) == [1, 5]

    def test_slices_cut_in_the_order_they_were_written(self) -> None:
        with self.session() as console:
            assert self.positions(self.run(console, "events :3 1:")) == [1, 2]
            assert self.positions(self.run(console, "events 1: :1")) == [1]

    def test_quotes_and_parentheses_reach_select_as_typed(self) -> None:
        with self.session() as console:
            (console.root / "d (e).csv").write_text("d\n", encoding="utf-8")
            self.run(console, "track 'd (e).csv'")
            assert self.positions(self.run(console, "events src_path='d (e).csv'")) == [4]
            assert self.positions(self.run(console, 'events "src_path=d (e).csv"')) == [4]
            assert self.positions(self.run(console, "events (src_path='d (e).csv')")) == [4]
            either = self.run(console, "events (src_path='d (e).csv' or src_path=a.csv)")
            assert self.positions(either) == [0, 4]

    def test_without_a_slice_only_the_default_is_shown(self) -> None:
        with self.session() as console:
            console.commands["events"].default = 2
            written = self.run(console, "events")
            assert "events (2 of 4)" in written
            assert self.positions(written) == [2, 3]
            assert self.positions(self.run(console, "events not src_path=a.csv")) == [2, 3]
            # any slice says how many, so the default steps aside
            assert self.positions(self.run(console, "events :3")) == [0, 1, 2]

    def test_a_malformed_expression_is_reported_rather_than_raised(self) -> None:
        with self.session() as console:
            assert "events: 'nowhere' is not a comparison" in self.run(console, "events nowhere")
            assert "events: unclosed '('" in self.run(console, "events (src_path=a.csv")
            assert "only by = and !=" in self.run(console, "events cls<Event")
            assert "No closing quotation" in self.run(console, "events src_path='a.csv")
            assert "unbalanced quotes" in self.run(console, "track 'a.csv")
            # a field no record holds is held by none of them, rather than refused
            assert "nothing matched" in self.run(console, "events bogus=1")
            assert "nothing matched" in self.run(console, "events event_type=created")

    def test_the_key_empties_the_rows_but_not_the_tracking(self) -> None:
        """Emptying the terminal is ctrl-l's, now that `clear` discards the record instead."""
        with self.session() as console:
            tracked = len(console._trail.assets)
            assert console.feed.rows
            with redirect_stdout(io.StringIO()):
                console.clear()
            assert not console.feed.rows
            assert len(console._trail.assets) == tracked
            assert self.positions(self.run(console, "events :1")) == [0]

    def test_a_clear_says_what_would_go_before_it_goes(self) -> None:
        with self.session() as console:
            trail = console._trail
            written = self.run(console, "assets clear")
            assert "assets: this clears 4" in written
            assert "'assets clear -f' to confirm" in written
            assert len(trail.assets) == 4
            # an argument that is not the confirmation is not taken for one
            assert "usage: assets clear" in self.run(console, "assets clear now")
            assert len(trail.assets) == 4

    def test_clearing_a_collection_unregisters_and_stays_cleared(self) -> None:
        with self.session() as console:
            trail = console._trail
            root = console.root
            recorded = len(trail.events)
            assert "assets: cleared 4" in self.run(console, "assets clear -f")
            assert not len(trail.assets)
            # the log is untouched, and now carries the removals as well
            assert len(trail.events) == recorded + 4
            # the removals were recorded, so the log replays into the same emptiness
            assert not len(Trail(root).assets)

    def test_clearing_the_events_empties_the_log(self) -> None:
        with self.session() as console:
            trail = console._trail
            root = console.root
            recorded = len(trail.events)
            assert f"events: this clears {recorded}" in self.run(console, "events clear")
            assert f"events: cleared {recorded}" in self.run(console, "events clear -f")
            assert not len(trail.events)
            # a session is rebuilt from the log, so nothing it held comes back with it
            assert not len(Trail(root).assets)
            assert "already empty" in self.run(console, "events clear")

    def test_the_feed_renders_what_is_recorded_after_a_clear(self) -> None:
        with self.session() as console:
            trail = console._trail
            self.run(console, "events clear -f")
            (console.root / "d.csv").write_text("d\n", encoding="utf-8")
            # the feed's cursor was left beyond a log that had shrunk under it
            written = self.run(console, "track d.csv")
            assert "AddAssetEvent" in written, written
            assert len(trail.events) == 1

    def test_clear_discards_the_whole_record(self) -> None:
        with self.session() as console:
            trail = console._trail
            assets = len(trail.assets)
            events = len(trail.events)
            written = self.run(console, "clear")
            assert f"clear: this clears assets {assets}, events {events}" in written
            assert len(trail.assets) == assets
            assert len(trail.events) == events
            confirmed = self.run(console, "clear -f")
            assert f"clear: cleared assets {assets}, events {events}" in confirmed
            assert not len(trail.assets)
            assert not len(trail.events)
            assert "already empty" in self.run(console, "clear")


class TestMarking:
    """The commands that edit `.markers`, and the ones that read it."""

    session = TestListing.session
    run = staticmethod(TestListing.run)

    def test_mark_tracks_now_and_writes_the_line(self) -> None:
        with self.session() as console:
            root = console.root
            (root / "d.csv").write_text("d\n", encoding="utf-8")
            written = self.run(console, "mark ./ csv")
            assert "marked ./ (root); tracked 0 now" in written
            assert "marked *.csv (root scope); tracked 1 now" in written
            assert root / "d.csv" in console._trail.assets
            assert (root / ".markers").read_text(encoding="utf-8") == "./\n*.csv\n"
            assert "already marked: .csv" in self.run(console, "mark .csv")
            # the feed shows the pickup, with the line that made it
            assert "rule: *.csv" in written

    def test_unmark_leaves_what_it_tracked(self) -> None:
        with self.session() as console:
            trail = console._trail
            self.run(console, "mark ./ csv")
            written = self.run(console, "unmark '**/*.csv'")
            assert "unmarked *.csv" in written
            assert "unmarking '*.csv' leaves 3 assets tracked; offtrail removes them" in written
            assert console.root / "a.csv" in trail.assets
            assert list(trail.markers) == ["./"]
            assert "not marked: tif" in self.run(console, "unmark tif")

    def test_markers_lists_each_line_with_its_kind_and_dir(self) -> None:
        with self.session() as console:
            self.run(console, "mark ./ csv folder/*.png")
            written = self.run(console, "markers")
            rows = [
                line.split()
                for line in written.splitlines()[1:]
            ]
            assert rows[0] == ["./", "root", "./"]
            assert rows[1] == ["*.csv", "root", "scope", "every", "root"]
            assert rows[2] == ["folder/*.png", "dir", "scope", "·", "not", "subfolders", "folder/"]
            (console.root / "data").mkdir()
            (console.root / ".markers").write_text("./\ndata\n", encoding="utf-8")
            console._trail.markers.text.read()
            assert "did you mean 'data/'" in self.run(console, "markers")

    def test_offtrail_globs_match_the_way_markers_do(self) -> None:
        with self.session() as console:
            trail = console._trail
            inner = console.root / "folder" / "inner.csv"
            inner.write_text("x\n", encoding="utf-8")
            self.run(console, "offtrail folder")
            self.run(console, "track folder/inner.csv")
            # a star does not cross a slash
            self.run(console, "offtrail '*.csv'")
            assert inner in trail.assets
            assert console.root / "a.csv" not in trail.assets
            self.run(console, "offtrail '**/*.csv'")
            assert inner not in trail.assets

    def test_help_tells_track_from_mark(self) -> None:
        with self.session() as console:
            written = self.run(console, "help")
            assert "'track data/*.csv' tracks the csv files in data now" in written
            assert "mark PATTERN..." in written
            assert "offtrail PATH..." in written
            assert "entries" not in written

    def test_mark_completes_extensions_and_paths(self) -> None:
        with self.session() as console:
            completions = {
                completion.text: completion.display_meta_text
                for completion in console.completer.get_completions(
                    Document("mark cs"),
                    CompleteEvent(completion_requested=True),
                )
            }
            assert completions == {"*.csv": "root scope"}
            completions = {
                completion.text: completion.display_meta_text
                for completion in console.completer.get_completions(
                    Document("mark ./fol"),
                    CompleteEvent(completion_requested=True),
                )
            }
            assert completions == {"der/": "root"}
            offered = {
                completion.text
                for completion in console.completer.get_completions(
                    Document("mark ./"),
                    CompleteEvent(completion_requested=True),
                )
            }
            # the trail directory can never be marked, so it is not offered
            assert "folder/" in offered
            assert ".trail/" not in offered


if __name__ == "__main__":
    test_object = TestListing()
    tests = [
        (
            "test_a_slice_counts_the_way_python_counts",
            "a slice counts the way python counts",
        ),
        (
            "test_a_filter_keeps_the_position_the_collection_addresses",
            "a filter keeps the position the collection addresses",
        ),
        (
            "test_a_path_filter_takes_either_way_of_writing_it",
            "a path filter takes either way of writing it",
        ),
        (
            "test_every_term_names_its_field",
            "every term names its field",
        ),
        (
            "test_assets_and_dirs_list_their_own_collections",
            "assets and dirs list their own collections",
        ),
        (
            "test_or_takes_either_side",
            "or takes either side",
        ),
        (
            "test_terms_side_by_side_all_have_to_hold",
            "terms side by side all have to hold",
        ),
        (
            "test_a_slice_cuts_what_the_terms_before_it_left",
            "a slice cuts what the terms before it left",
        ),
        (
            "test_slices_cut_in_the_order_they_were_written",
            "slices cut in the order they were written",
        ),
        (
            "test_quotes_and_parentheses_reach_select_as_typed",
            "quotes and parentheses reach select as typed",
        ),
        (
            "test_without_a_slice_only_the_default_is_shown",
            "without a slice only the default is shown",
        ),
        (
            "test_a_malformed_expression_is_reported_rather_than_raised",
            "a malformed expression is reported rather than raised",
        ),
        (
            "test_the_key_empties_the_rows_but_not_the_tracking",
            "the key empties the rows but not the tracking",
        ),
        (
            "test_a_clear_says_what_would_go_before_it_goes",
            "a clear says what would go before it goes",
        ),
        (
            "test_clearing_a_collection_unregisters_and_stays_cleared",
            "clearing a collection unregisters and stays cleared",
        ),
        (
            "test_clearing_the_events_empties_the_log",
            "clearing the events empties the log",
        ),
        (
            "test_the_feed_renders_what_is_recorded_after_a_clear",
            "the feed renders what is recorded after a clear",
        ),
        (
            "test_clear_discards_the_whole_record",
            "clear discards the whole record",
        ),
    ]

    for method_name, description in tests:
        getattr(test_object, method_name)()
        print(f"  ✓ {description}")
    marking = TestMarking()
    for method_name in dir(marking):
        if method_name.startswith("test_"):
            getattr(marking, method_name)()
            print(f"  ✓ {method_name.removeprefix('test_').replace('_', ' ')}")
