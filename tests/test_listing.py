from __future__ import annotations

import io
import os
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory

from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from trail.cli import main
from trail.cli.console import Console
from trail.trail import Trail


class TestListing:
    """
    The commands that select from a collection. The feed prints its rows to the terminal. The
    printing is discarded and the rows are read back from the feed.
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
            console = Console(Trail(root, ontrail=None), root)
            cls.run(console, "ontrail a.csv b.csv c.csv folder")
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
            written = self.run(console, f"events entry={asset.id}")
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
            assert self.positions(self.run(console, f"events entry={asset.id}")) == [2]
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
            assert "dirs: nothing matched" in self.run(console, "dirs")
            (root / "data").mkdir()
            (root / ".ontrail").write_text("./\ndata/*.csv\nfolder/*.csv\n", encoding="utf-8")
            console._trail.ontrail.txt.read()
            console._trail.dirs.refresh()
            written = self.run(console, "dirs")
            # a directory asset is never a Dir, so folder is left out
            assert "dirs (2 of 2)" in written
            assert "scopes: ./*.csv" in written
            assert self.positions(self.run(console, "dirs path=data")) == [1]
            assert self.positions(self.run(console, "dirs root=true")) == [0]
            # a Dir comes from .ontrail, so clearing the Dirs is refused
            assert "edit .ontrail to remove its rule" in self.run(console, "dirs clear -f")
            assert len(console._trail.dirs) == 2
            data = console._trail.dirs[root / "data"]
            written = self.run(console, f"events entry={data.id}")
            assert "AddDirEvent" in written
            assert "rule: data/*.csv" in written

    def test_a_listing_completes_the_fields_its_records_hold(self) -> None:
        with self.session() as console:

            def offered(text: str) -> set[str]:
                completions = console.completer.get_completions(
                    Document(text),
                    CompleteEvent(completion_requested=True),
                )
                return {
                    completion.text
                    for completion in completions
                }

            # a property an asset names in `selectable`
            assert offered("assets st_") == {"st_size=", "st_mtime="}
            assert "is_directory=" in offered("assets is_")
            assert offered("dirs sc") == {"scopes="}
            # a field only one event class defines
            assert offered("events is_s") == {"is_synthetic="}
            assert offered("events (cl") == {"cls="}

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
            self.run(console, "ontrail a.csv")
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
            self.run(console, "ontrail 'd (e).csv'")
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
            assert "unbalanced quotes" in self.run(console, "ontrail 'a.csv")
            # a field no record holds is held by none of them, rather than refused
            assert "nothing matched" in self.run(console, "events bogus=1")
            assert "nothing matched" in self.run(console, "events event_type=created")

    def test_the_key_empties_the_rows_but_not_the_tracking(self) -> None:
        """Ctrl-L empties the terminal. `clear` discards the record instead."""
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
            written = self.run(console, "ontrail d.csv")
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


class TestOnTrailCommand:
    """The `ontrail` command, which lists the rules of `.ontrail` and adds or removes them."""

    session = TestListing.session
    run = staticmethod(TestListing.run)

    def test_ontrail_tracks_now_without_editing_rule_file(self) -> None:
        with self.session() as console:
            root = console.root
            (root / "d.csv").write_text("d\n", encoding="utf-8")
            self.run(console, "ontrail d.csv")
            assert root / "d.csv" in console._trail.assets
            assert not (root / ".ontrail").exists()
            assert "already tracked: d.csv" in self.run(console, "ontrail d.csv")

    def test_ontrail_lists_each_rule_with_its_kind_and_dir(self) -> None:
        with self.session() as console:
            (console.root / ".ontrail").write_text("./\n*.csv\nfolder/*.png\n", encoding="utf-8")
            console._trail.ontrail.txt.read()
            written = self.run(console, "ontrail")
            rows = [
                line.split()
                for line in written.splitlines()[1:]
            ]
            assert rows[0] == ["./", "root", "./"]
            assert rows[1] == ["*.csv", "root", "scope", "every", "root"]
            assert rows[2] == ["folder/*.png", "dir", "scope", "·", "not", "subfolders", "folder/"]
            (console.root / "data").mkdir()
            (console.root / ".ontrail").write_text("./\ndata\n", encoding="utf-8")
            console._trail.ontrail.txt.read()
            assert "did you mean 'data/'" in self.run(console, "ontrail")

    def test_offtrail_globs_match_the_way_rules_do(self) -> None:
        with self.session() as console:
            trail = console._trail
            inner = console.root / "folder" / "inner.csv"
            inner.write_text("x\n", encoding="utf-8")
            self.run(console, "offtrail folder")
            self.run(console, "ontrail folder/inner.csv")
            # a star does not cross a slash
            self.run(console, "offtrail '*.csv'")
            assert inner in trail.assets
            assert console.root / "a.csv" not in trail.assets
            self.run(console, "offtrail '**/*.csv'")
            assert inner not in trail.assets

    def test_help_tells_paths_from_rules(self) -> None:
        with self.session() as console:
            written = self.run(console, "help")
            assert "'ontrail data/*.csv' tracks the csv files in data now" in written
            assert "ontrail [PATH...]" in written
            assert "offtrail [PATH...]" in written
            assert "entries" not in written

    def test_paths_change_assets_without_editing_rule_files(self) -> None:
        with self.session() as console:
            trail = console._trail
            root = console.root
            included = list(trail.ontrail)
            excluded = list(trail.offtrail)
            for name in ('add', 'remove'):
                path = root / name
                path.write_text('asset\n', encoding='utf-8')
                self.run(console, f'ontrail ./{name}')
                assert path in trail.assets
                self.run(console, f'offtrail ./{name}')
                assert path not in trail.assets
            assert list(trail.ontrail) == included
            assert list(trail.offtrail) == excluded
            assert console.commands.find('track') is None
            assert console.commands.find('untrack') is None

    def test_paths_and_rule_actions_complete_together(self) -> None:
        with self.session() as console:
            (console.root / 'later.csv').write_text('later\n', encoding='utf-8')
            available = {
                completion.text
                for completion in console.completer.get_completions(
                    Document('ontrail la'),
                    CompleteEvent(completion_requested=True),
                )
            }
            assert available == {'ter.csv'}
            following = {
                completion.text
                for completion in console.completer.get_completions(
                    Document('ontrail a.csv '),
                    CompleteEvent(completion_requested=True),
                )
            }
            assert 'add' not in following
            assert 'remove' not in following
            tracked = {
                completion.text
                for completion in console.completer.get_completions(
                    Document('offtrail a'),
                    CompleteEvent(completion_requested=True),
                )
            }
            assert tracked == {'a.csv'}
            absent = list(console.completer.get_completions(
                Document('offtrail la'),
                CompleteEvent(completion_requested=True),
            ))
            assert not absent

    def test_ontrail_completes_paths(self) -> None:
        with self.session() as console:
            offered = {
                completion.text
                for completion in console.completer.get_completions(
                    Document("ontrail ./"),
                    CompleteEvent(completion_requested=True),
                )
            }
            # the trail directory can never be added, so it is not offered
            assert "folder" in offered
            assert ".trail/" not in offered

    def test_offtrail_lists_adds_and_removes_its_rules(self) -> None:
        with self.session() as console:
            assert "  .*  root scope  every root" in self.run(console, "offtrail")
            (console.root / "file.tmp").write_text("tmp\n", encoding="utf-8")
            self.run(console, "ontrail file.tmp")
            assert console.root / "file.tmp" in console._trail.assets
            self.run(console, "offtrail file.tmp")
            assert console.root / "file.tmp" not in console._trail.assets
            assert list(console._trail.offtrail) == [".*"]
            assert (console.root / ".offtrail").read_text(encoding="utf-8") == ".*\n"


class TestCliSubcommands:
    def test_ontrail_and_offtrail_subcommands(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            (root / "project").mkdir()
            project = root / "project"
            Trail(project, ontrail="default")
            (project / "file.csv").write_text("data\n", encoding="utf-8")

            # test outside project directory
            previous_dir = os.getcwd()
            try:
                os.chdir(root)
                stderr_buffer = io.StringIO()
                with redirect_stderr(stderr_buffer):
                    code = main(["ontrail"])
                assert code == 2
                assert "not in a Trail project" in stderr_buffer.getvalue()

                # test inside project directory
                os.chdir(project)
                stdout_buffer = io.StringIO()
                with redirect_stdout(stdout_buffer):
                    code = main(["ontrail"])
                assert code == 0
                assert "./" in stdout_buffer.getvalue()

                # track file via shell subcommand
                stdout_buffer = io.StringIO()
                with redirect_stdout(stdout_buffer):
                    code = main(["ontrail", "file.csv"])
                assert code == 0
                assert "tracked: file.csv" in stdout_buffer.getvalue()
                assert project / "file.csv" in Trail(project).assets

                # already tracked
                stdout_buffer = io.StringIO()
                with redirect_stdout(stdout_buffer):
                    code = main(["ontrail", "file.csv"])
                assert code == 0
                assert "already tracked: file.csv" in stdout_buffer.getvalue()

                # untrack file via shell subcommand
                stdout_buffer = io.StringIO()
                with redirect_stdout(stdout_buffer):
                    code = main(["offtrail", "file.csv"])
                assert code == 0
                assert "untracked: file.csv" in stdout_buffer.getvalue()
                assert project / "file.csv" not in Trail(project).assets

                # show offtrail rules
                stdout_buffer = io.StringIO()
                with redirect_stdout(stdout_buffer):
                    code = main(["offtrail"])
                assert code == 0
                assert ".*" in stdout_buffer.getvalue()
            finally:
                os.chdir(previous_dir)


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
            "test_a_listing_completes_the_fields_its_records_hold",
            "a listing completes the fields its records hold",
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
    command = TestOnTrailCommand()
    for method_name in dir(command):
        if method_name.startswith("test_"):
            getattr(command, method_name)()
            print(f"  ✓ {method_name.removeprefix('test_').replace('_', ' ')}")
