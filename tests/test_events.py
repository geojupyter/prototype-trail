from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from trail import Trail
from trail.event import Event, WatchdogEvent
from trail.jupyter.event import JupyterEvent


def record(trail: Trail) -> JupyterEvent:
    event = JupyterEvent(
        schema_id='test',
        data={
            'verb': 'updated',
            'path': '/full/path/to/map.jgis',
            'metadata': {'label': 'full attributes'},
        },
    )
    trail.events.append(event)
    return event


def test_old_records_default_to_unaccepted() -> None:
    trail = Trail()
    event = record(trail)
    data = event.to_record()
    del data['is_accepted']
    restored = Event.from_record(trail=trail, **data)
    assert restored.is_accepted is False


def test_rejecting_directory_registration_keeps_remaining_entry_type(tmp_path: Path) -> None:
    trail = Trail(tmp_path)
    added = trail.events.select[0]
    event = WatchdogEvent(
        src_path=str(tmp_path),
        event_type='modified',
        is_directory=True,
    )
    event.apply(trail)
    trail.events.finalize([event.id], [added.id])
    reopened = Trail(tmp_path)
    assert not reopened.assets
    assert reopened.events[event.id].entry is reopened.dirs[tmp_path]
    assert reopened.dirs[tmp_path].id == added.entry.id
    assert added.id not in reopened.events


@pytest.mark.parametrize('mutation', ['delete', 'clear', 'finalize'])
def test_watch_does_not_skip_arrivals_after_removing_events(mutation: str) -> None:
    async def run() -> None:
        trail = Trail()
        first = record(trail)
        record(trail)
        watching = trail.events.watch(timeout=1)
        if mutation == 'delete':
            del trail.events[first.id]
        elif mutation == 'clear':
            trail.events.clear()
        else:
            trail.events.finalize([], [first.id])
        new = record(trail)
        assert await anext(watching) is new
        following = record(trail)
        del trail.events[new.id]
        assert await anext(watching) is following
        await watching.aclose()
        assert not trail.events.watch.waiters

    asyncio.run(run())


def test_selected_events_have_independent_watcher_positions() -> None:
    async def run() -> None:
        trail = Trail()
        first = record(trail)
        record(trail)
        watching = trail.events.watch(timeout=1)
        selected = trail.events.select[:1]
        selected.clear()
        assert first.id in trail.events.positions
        new = record(trail)
        assert await anext(watching) is new
        await watching.aclose()

    asyncio.run(run())
