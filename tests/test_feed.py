from __future__ import annotations

from pathlib import Path

import pytest
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.formatted_text import StyleAndTextTuples, fragment_list_to_text

from trail.cli.console import Console
from trail.jupyter.event import (
    DocumentV1Event,
    JupyterEvent,
    LayerV1Event,
    ViewportV1Event,
)
from trail.trail import Trail


@pytest.fixture
def console(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Console:
    def show(rows: list[StyleAndTextTuples]) -> None:
        return None

    out = Console(Trail(tmp_path, ontrail=None), tmp_path)
    monkeypatch.setattr(out.feed, "show", show)
    return out


@pytest.fixture
def sample(console: Console) -> list[JupyterEvent]:
    path = str(console.root / "map (v1).jgis")
    out = [
        ViewportV1Event(
            schema_id=ViewportV1Event.schema,
            src_path=path,
            data={
                "verb": "updated",
                "object": {"new": {"zoom": 4}},
            },
        ),
        LayerV1Event(
            schema_id=LayerV1Event.schema,
            src_path=path,
            data={
                "verb": "removed",
                "object": {"layer_id": "roads"},
            },
        ),
        DocumentV1Event(
            schema_id=DocumentV1Event.schema,
            src_path=path,
            data={"verb": "activated"},
        ),
        ViewportV1Event(
            schema_id=ViewportV1Event.schema,
            src_path=path,
            data={
                "verb": "updated",
                "object": {"new": {"zoom": 9}},
            },
        ),
    ]
    return out


def positions(console: Console) -> list[int]:
    lines = [
        fragment_list_to_text(row)
        for row in console.feed.rows
    ]
    out = [
        int(line.split(".", 1)[0])
        for line in lines
        if line and line[0].isdigit() and ". " in line
    ]
    return out


def output(console: Console) -> str:
    out = "\n".join(
        fragment_list_to_text(row)
        for row in console.feed.rows
    )
    return out


def test_unfiltered_feed_prints_every_event(
    console: Console,
    sample: list[JupyterEvent],
) -> None:
    console._trail.events.extend(sample)
    assert console.run.drain()
    assert positions(console) == [0, 1, 2, 3]


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("cls=ViewportV1Event or cls=LayerV1Event", [0, 1, 3]),
        ("cls=ViewportEvent zoom>=5", [3]),
        ("(cls=ViewportV1Event or cls=LayerV1Event) not verb=removed", [0, 3]),
        ("data.object.new.zoom>=5", [3]),
        ("is_accepted=false timestamp>=2000-01-01", [0, 1, 2, 3]),
        ("cls=JupyterEvent 1:3", [1, 2]),
        ("src_path='map (v1).jgis'", [0, 1, 2, 3]),
        ('"src_path=map (v1).jgis"', [0, 1, 2, 3]),
    ],
)
def test_feed_uses_the_selection_dsl(
    console: Console,
    sample: list[JupyterEvent],
    expression: str,
    expected: list[int],
) -> None:
    console.submit(f"feed {expression}")
    console._trail.events.extend(sample)
    history = console._trail.events.jsonl.path.read_bytes()
    assert console.run.drain()
    assert positions(console) == expected
    assert console.run.cursor == len(sample)
    assert list(console._trail.events) == sample
    assert console._trail.events.jsonl.path.read_bytes() == history


def test_replacing_and_resetting_a_filter_do_not_replay_events(
    console: Console,
    sample: list[JupyterEvent],
) -> None:
    console.submit("feed cls=ViewportV1Event")
    console._trail.events.extend(sample)
    console.run.drain()
    assert positions(console) == [0, 3]
    console.feed.rows.clear()

    console.submit("feed cls=LayerV1Event")
    assert positions(console) == []
    viewport = ViewportV1Event(schema_id=ViewportV1Event.schema, data={})
    layer = LayerV1Event(schema_id=LayerV1Event.schema, data={})
    console._trail.events.extend([viewport, layer])
    console.run.drain()
    assert positions(console) == [5]
    console.feed.rows.clear()

    console.submit("feed")
    assert "feed: all events" in output(console)
    assert positions(console) == []
    document = DocumentV1Event(schema_id=DocumentV1Event.schema, data={})
    console._trail.events.append(document)
    console.run.drain()
    assert positions(console) == [6]


@pytest.mark.parametrize(
    "expression",
    [
        "cls=ViewportV1Event or LayerV1Event",
        "(cls=ViewportV1Event",
        "src_path='map.jgis",
        "cls<Event",
        "zoom>=wrong",
        "::0",
    ],
)
def test_invalid_expression_keeps_the_previous_filter(
    console: Console,
    sample: list[JupyterEvent],
    expression: str,
) -> None:
    console._trail.events.extend(sample)
    console.run.drain()
    console.submit("feed cls=LayerV1Event")
    console.feed.rows.clear()
    console.submit(f"feed {expression}")
    assert any(
        style == "class:error" and text.startswith("feed:")
        for row in console.feed.rows
        for style, text in row
    )
    assert console.feed.expression == "cls=LayerV1Event"
    assert positions(console) == []


def test_slices_count_the_log_across_drains(console: Console) -> None:
    console.submit("feed cls=ViewportV1Event :1")
    for _ in range(3):
        event = ViewportV1Event(schema_id=ViewportV1Event.schema, data={})
        console._trail.events.append(event)
        console.run.drain()
    assert positions(console) == [0]
    assert console.run.cursor == 3


def test_listings_and_messages_ignore_the_live_filter(
    console: Console,
    sample: list[JupyterEvent],
) -> None:
    console.submit("feed cls=MissingEvent")
    console._trail.events.extend(sample)
    console.run.drain()
    assert positions(console) == []
    assert console.feed.day is None
    assert not console.run.drain()
    console.submit("events")
    assert positions(console) == [0, 1, 2, 3]
    console.submit("help")
    assert "feed [EXPRESSION]" in output(console)
    console.submit("missing")
    assert "unknown command" in output(console)


def test_filter_errors_from_future_fields_leave_the_feed_running(console: Console) -> None:
    console.submit("feed zoom>=wrong")
    event = ViewportV1Event(
        schema_id=ViewportV1Event.schema,
        data={"object": {"new": {"zoom": 5}}},
    )
    console._trail.events.append(event)
    assert console.run.drain()
    assert "feed: could not convert string to float" in output(console)
    assert console.run.cursor == 1
    console.submit("feed")
    following = ViewportV1Event(schema_id=ViewportV1Event.schema, data={})
    console._trail.events.append(following)
    console.run.drain()
    assert positions(console) == [1]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("fe", {"feed"}),
        ("feed (cl", {"cls="}),
        ("feed zoom", {"zoom="}),
        ("feed verb=updated or is_f", {"is_focus="}),
        ("feed cls=ViewportV1Event", set()),
        ("feed clear", set()),
    ],
)
def test_feed_completes_event_fields(
    console: Console,
    text: str,
    expected: set[str],
) -> None:
    completions = console.completer.get_completions(
        Document(text),
        CompleteEvent(completion_requested=True),
    )
    offered = {
        completion.text
        for completion in completions
    }
    assert offered == expected
