from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from prompt_toolkit.application import Application, create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.formatted_text import fragment_list_to_text
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.key_binding.key_processor import KeyProcessor
from prompt_toolkit.layout import Window
from prompt_toolkit.output import DummyOutput

from trail import Trail, fmt
from trail.cli import main
from trail.cli.console import Console, Run
from trail.cli.triage import ACCEPTED, DECLINED, PENDING, Triage
from trail.event import JSONL, AddDirEvent, Event, WatchdogEvent
from trail.jupyter.event import DocumentV1Event, JupyterEvent


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


def test_decisions_are_tentative_and_keep_original_order() -> None:
    trail = Trail()
    events = [
        record(trail)
        for _ in range(5)
    ]
    triage = Triage(trail)
    assert [pane.name for pane in triage.panes] == [DECLINED, PENDING, ACCEPTED]
    pending = triage.active
    for index in (4, 0, 2):
        pending.selected = events[index].id
        triage.decide(True)
    assert triage.panes[2].ids == [events[0].id, events[2].id, events[4].id]
    assert pending.ids == [events[1].id, events[3].id]
    assert not any(event.is_accepted for event in events)
    assert len(trail.events) == 5

    triage.active = triage.panes[2]
    triage.active.selected = events[2].id
    triage.decide(False)
    assert pending.ids == [events[1].id, events[2].id, events[3].id]
    triage.active = pending
    pending.selected = events[2].id
    triage.decide(False)
    assert triage.panes[0].ids == [events[2].id]
    triage.active = triage.panes[0]
    triage.decide(True)
    assert pending.ids == [events[1].id, events[2].id, events[3].id]

    triage.reset()
    assert pending.ids == trail.events.ids
    assert not triage.decisions
    assert not triage.panes[0].ids
    assert not triage.panes[2].ids


def test_finalized_events_stay_out_after_reopening(tmp_path: Path) -> None:
    trail = Trail(tmp_path, ontrail=None)
    accepted = record(trail)
    declined = record(trail)
    pending = record(trail)
    triage = Triage(trail)
    triage.decisions = {
        accepted.id: ACCEPTED,
        declined.id: DECLINED,
    }
    before = trail.events.jsonl.path.read_bytes()
    triage.sync()
    assert trail.events.jsonl.path.read_bytes() == before
    triage.finalize()
    assert accepted.is_accepted
    assert declined.id not in trail.events
    assert triage.panes[1].ids == [pending.id]
    assert not triage.panes[0].ids
    assert not triage.panes[2].ids
    assert Triage(trail).panes[1].ids == [pending.id]

    lines = (
        trail.events.jsonl.path
        .read_text()
        .splitlines()
    )
    records = [
        json.loads(line)
        for line in lines
    ]
    assert [row['id'] for row in records] == [accepted.id, pending.id]
    assert records[0]['is_accepted'] is True
    reopened = Trail(tmp_path)
    assert reopened.events[accepted.id].is_accepted
    assert declined.id not in reopened.events
    assert Triage(reopened).panes[1].ids == [pending.id]


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


def test_failed_finalization_preserves_choices_and_log(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trail = Trail(tmp_path, ontrail=None)
    accepted = record(trail)
    declined = record(trail)
    triage = Triage(trail)
    triage.decisions = {
        accepted.id: ACCEPTED,
        declined.id: DECLINED,
    }
    before = trail.events.jsonl.path.read_bytes()

    def fail(self: JSONL) -> None:
        raise OSError('disk full')

    monkeypatch.setattr(JSONL, 'write', fail)
    triage.finalize()
    assert 'disk full' in triage.message
    assert not accepted.is_accepted
    assert trail.events.ids == [accepted.id, declined.id]
    assert trail.events.jsonl.path.read_bytes() == before
    assert len(triage.decisions) == 2


def test_pending_follows_tail_but_keeps_an_earlier_selection() -> None:
    trail = Trail()
    first = record(trail)
    triage = Triage(trail)
    pending = triage.active
    second = record(trail)
    triage.sync()
    assert pending.selected == second.id
    pending.move(-1)
    third = record(trail)
    triage.sync()
    assert pending.selected == first.id
    pending.move(2)
    assert pending.selected == third.id
    triage.decide(False)
    assert pending.selected == second.id
    fourth = record(trail)
    triage.sync()
    assert pending.selected == fourth.id


def test_query_and_bulk_moves_keep_a_fixed_set_and_preserve_hidden_decisions() -> None:
    trail = Trail()
    events = [
        record(trail)
        for _ in range(6)
    ]
    events[2].is_accepted = True
    triage = Triage(trail)
    triage.decisions[events[0].id] = DECLINED
    triage.query.text = '-2:'
    triage.apply_query()
    pending = triage.panes[1]
    assert pending.ids == [events[4].id, events[5].id]
    assert pending.total == 4
    assert '2 of 4' in pending.heading()

    triage.decide(True)
    assert pending.ids == [events[4].id]
    triage.decide(True, all_events=True)
    assert not pending.ids
    assert triage.panes[2].ids == [events[4].id, events[5].id]
    assert triage.decisions[events[0].id] == DECLINED
    assert not events[4].is_accepted
    assert not events[5].is_accepted

    triage.focus(triage.panes[2])
    triage.decide(False, all_events=True)
    assert pending.ids == [events[4].id, events[5].id]
    assert triage.decisions == {events[0].id: DECLINED}
    triage.focus(pending)
    triage.decide(False, all_events=True)
    assert triage.panes[0].ids == [events[4].id, events[5].id]

    arriving = record(trail)
    triage.sync()
    assert all(arriving.id not in pane.ids for pane in triage.panes)
    assert not pending.ids
    triage.apply_query()
    assert triage.matches == {events[5].id, arriving.id}
    assert pending.ids == [arriving.id]
    assert triage.panes[0].ids == [events[5].id]

    choices = triage.decisions.copy()
    triage.clear_query()
    assert triage.matches is None
    assert triage.decisions == choices
    assert triage.panes[0].ids == [events[0].id, events[4].id, events[5].id]
    assert pending.ids == [events[1].id, events[3].id, arriving.id]


def test_query_reuses_relative_paths_and_combines_predicates_with_slices(tmp_path: Path) -> None:
    trail = Trail(tmp_path, ontrail=None)
    events = [
        WatchdogEvent(src_path=str(tmp_path / path), event_type=verb)
        for path, verb in (
            ('map.jGIS', 'modified'),
            ('other.jGIS', 'modified'),
            ('map.jGIS', 'opened'),
            ('map.jGIS', 'modified'),
        )
    ]
    trail.events.extend(events)
    triage = Triage(trail)
    triage.query.text = 'src_path=map.jGIS event_type=modified -1:'
    triage.apply_query()
    assert triage.active.ids == [events[3].id]
    triage.decide(False, all_events=True)
    assert triage.decisions == {events[3].id: DECLINED}


@pytest.mark.parametrize('expression', ['verb=updated or', 'is_accepted=invalid', '::0'])
def test_invalid_query_preserves_the_applied_filter_and_decisions(expression: str) -> None:
    trail = Trail()
    events = [
        record(trail)
        for _ in range(3)
    ]
    triage = Triage(trail)
    triage.query.text = '-2:'
    triage.apply_query()
    triage.decide(True)
    choices = triage.decisions.copy()
    triage.query.text = expression
    triage.apply_query()
    assert triage.matches == {events[1].id, events[2].id}
    assert triage.expression == '-2:'
    assert triage.active.ids == [events[1].id]
    assert triage.decisions == choices
    assert triage.message.startswith('Query: ')


def test_finalization_requires_three_enters_and_includes_hidden_decisions() -> None:
    trail = Trail()
    accepted = record(trail)
    declined = record(trail)
    visible = record(trail)
    triage = Triage(trail)
    triage.decisions = {
        accepted.id: ACCEPTED,
        declined.id: DECLINED,
    }
    triage.query.text = '-1:'
    triage.apply_query()
    assert triage.active.ids == [visible.id]
    triage.enter()
    assert triage.confirming == 1
    assert '1 accepted and delete 1' in triage.message
    assert not accepted.is_accepted
    triage.active.move(-1)
    assert triage.confirming == 0
    triage.enter()
    triage.enter()
    assert triage.confirming == 2
    assert not accepted.is_accepted
    assert declined.id in trail.events
    triage.enter()
    assert accepted.is_accepted
    assert declined.id not in trail.events
    assert triage.active.ids == [visible.id]
    assert triage.confirming == 0


@pytest.mark.parametrize('finalize', [False, True])
@pytest.mark.parametrize('accepted', [False, True])
def test_keyboard_query_typing_bulk_actions_and_confirmation(
    finalize: bool,
    accepted: bool,
) -> None:
    async def run() -> None:
        with (
            create_pipe_input() as pipe,
            create_app_session(input=pipe, output=DummyOutput()),
        ):
            trail = Trail()
            events = [
                record(trail)
                for _ in range(6)
            ]
            for event in events[::2]:
                event.data['metadata']['label'] = 'YnNy'
            triage = Triage(trail)
            keys = '/data.metadata.label=YnNy\x1b[H\x1b[F\r'
            if accepted:
                keys += 'Y'
                destination = ACCEPTED
            else:
                keys += 'N'
                destination = DECLINED
            if finalize:
                keys += '\r\r\r\x03'
            else:
                keys += '\x1b\x1b\x1b'
            pipe.send_text(keys)
            async with asyncio.timeout(5):
                await triage.run()
            for index, event in enumerate(events):
                matched = index % 2 == 0
                assert event.is_accepted == (finalize and accepted and matched)
                assert (event.id in trail.events) == (not finalize or accepted or not matched)
            if finalize:
                assert not triage.decisions
            else:
                assert triage.decisions == {
                    event.id: destination
                    for event in events[::2]
                }
                assert triage.matches is None
                assert triage.query.text == ''
            assert not trail.events.watch.waiters
            assert not trail.lock.holders

    asyncio.run(run())


def test_inspector_uses_the_event_view() -> None:
    trail = Trail()
    event = AddDirEvent(src_path='/project/data', rule='data/*.png')
    trail.events.append(event)
    triage = Triage(trail)
    text = fragment_list_to_text(triage.inspector.text())
    assert text == (
        'AddDirEvent\n'
        f'    id: {event.id}\n'
        f'    timestamp: {fmt.clock(event.timestamp)}\n'
        '    src_path: /project/data\n'
        '    rule: data/*.png'
    )
    triage.inspector.move(100)
    assert triage.inspector.row > 0
    record(trail)
    triage.sync()
    triage.inspector.text()
    assert triage.inspector.row == 0


def test_pane_shows_filename_and_inspector_keeps_full_path() -> None:
    trail = Trail()
    event = WatchdogEvent(src_path='/project/data/sample.csv', event_type='modified')
    trail.events.append(event)
    triage = Triage(trail)
    assert triage.active.rows() == [
        ('class:triage.selection.active', '0. modified sample.csv'),
    ]
    text = fragment_list_to_text(triage.inspector.text())
    assert f'    src_path: {event.src_path}' in text


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


@pytest.mark.parametrize('keys', ['y\r\r\r\x1b\x1b', 'y\x1b\x1b', 'n\x03'])
def test_keyboard_finalize_reset_and_exit(keys: str) -> None:
    async def run() -> None:
        with (
            create_pipe_input() as pipe,
            create_app_session(input=pipe, output=DummyOutput()),
        ):
            trail = Trail()
            event = record(trail)
            triage = Triage(trail)
            pipe.send_text(keys)
            async with asyncio.timeout(5):
                await triage.run()
            assert event.is_accepted == ('\r' in keys)
            assert event.id in trail.events
            assert not trail.events.watch.waiters
            assert not trail.lock.holders
            assert triage.application.mouse_support()

    asyncio.run(run())


def test_console_returns_from_triage_without_losing_the_live_feed() -> None:
    async def run() -> None:
        with (
            create_pipe_input() as pipe,
            create_app_session(input=pipe, output=DummyOutput()),
        ):
            trail = Trail()
            event = record(trail)
            console = Console(trail, Path.cwd())

            async def interact() -> None:
                pipe.send_text('triage\r')
                while (
                    console.active_triage is None
                    or not console.active_triage.application.is_running
                ):
                    await asyncio.sleep(0.01)
                pipe.send_text('y\r\r\r\x03')
                while console.active_triage is not None:
                    await asyncio.sleep(0.01)
                assert event.is_accepted
                assert console.run.cursor == len(trail.events)
                pipe.send_text('quit\r')

            interacting = asyncio.create_task(interact())
            try:
                async with asyncio.timeout(5):
                    await console.application.run_async()
                    await interacting
            finally:
                interacting.cancel()

    asyncio.run(run())


def test_live_arrivals_and_equal_panes_survive_resize() -> None:
    class Output(DummyOutput):
        size = Size(rows=30, columns=120)

        def get_size(self) -> Size:
            return self.size

    async def run() -> None:
        output = Output()
        with (
            create_pipe_input() as pipe,
            create_app_session(input=pipe, output=output),
        ):
            trail = Trail()
            first = record(trail)
            triage = Triage(trail)
            rendered = asyncio.Event()

            def after_render(application: Application) -> None:
                rendered.set()

            triage.application.after_render += after_render

            async def interact() -> None:
                await rendered.wait()
                second = record(trail)
                while triage.active.selected != second.id:
                    await asyncio.sleep(0.01)
                pipe.send_text('\x1b[A')
                while triage.active.selected != first.id:
                    await asyncio.sleep(0.01)
                third = record(trail)
                while third.id not in triage.active.ids:
                    await asyncio.sleep(0.01)
                assert triage.active.selected == first.id
                for columns, rows in ((120, 30), (80, 24), (45, 12)):
                    rendered.clear()
                    output.size = Size(rows=rows, columns=columns)
                    triage.application.invalidate()
                    await rendered.wait()
                    widths = [
                        pane.window.render_info.window_width
                        for pane in triage.panes
                    ]
                    assert max(widths) - min(widths) <= 1
                    assert triage.active.selected == first.id
                pipe.send_text('\x03')

            interacting = asyncio.create_task(interact())
            try:
                async with asyncio.timeout(5):
                    await triage.run()
                    await interacting
            finally:
                interacting.cancel()
            assert not trail.events.watch.waiters

    asyncio.run(run())


def test_focus_reports_preserve_choices_and_navigation_during_live_arrivals() -> None:
    async def run() -> None:
        with (
            create_pipe_input() as pipe,
            create_app_session(input=pipe, output=DummyOutput()),
        ):
            trail = Trail()
            events = [
                record(trail)
                for _ in range(40)
            ]
            triage = Triage(trail)
            rendered = asyncio.Event()

            def after_render(application: Application) -> None:
                rendered.set()

            triage.application.after_render += after_render

            async def press(keys: str) -> None:
                rendered.clear()
                pipe.send_text(keys)
                await rendered.wait()

            async def interact() -> None:
                await rendered.wait()
                await press('y')
                assert triage.decisions == {events[-1].id: ACCEPTED}
                await press('\x1b[H')
                for _ in range(4):
                    for report, verb in (('\x1b[O', 'deactivated'), ('\x1b[I', 'activated')):
                        trail.events.append(DocumentV1Event(
                            schema_id=DocumentV1Event.schema,
                            src_path='/project/map.jGIS',
                            data={
                                'verb': verb,
                                'frontend_id': 'test',
                                'object': {
                                    'widget_id': 'map',
                                    'reason': 'window',
                                },
                            },
                        ))
                        await press(report)
                        assert triage.application.is_running
                        assert triage.decisions == {events[-1].id: ACCEPTED}
                        assert not triage.escaping
                        assert triage.active.selected == events[0].id
                    await press('\x1b[B')
                    assert triage.active.selected == events[1].id
                    await press('\x1b[A')
                    assert triage.active.selected == events[0].id
                await press('\x1b[C')
                assert triage.active.name == ACCEPTED
                await press('n')
                assert not triage.decisions
                pipe.send_text('\x03')

            interacting = asyncio.create_task(interact())
            try:
                async with asyncio.timeout(5):
                    await triage.run()
                    await interacting
            finally:
                interacting.cancel()

    asyncio.run(run())


def test_paging_and_boundaries_follow_the_focused_pane_and_terminal_size() -> None:
    class Output(DummyOutput):
        size = Size(rows=30, columns=120)

        def get_size(self) -> Size:
            return self.size

    async def run() -> None:
        output = Output()
        with (
            create_pipe_input() as pipe,
            create_app_session(input=pipe, output=output),
        ):
            trail = Trail()
            events = [
                record(trail)
                for _ in range(100)
            ]
            triage = Triage(trail)
            for event in events[:30]:
                triage.decisions[event.id] = DECLINED
            for event in events[70:]:
                triage.decisions[event.id] = ACCEPTED
            triage.sync()
            rendered = asyncio.Event()
            processed = asyncio.Event()

            def after_render(application: Application) -> None:
                rendered.set()

            def after_key(processor: KeyProcessor) -> None:
                processed.set()

            triage.application.after_render += after_render
            triage.application.key_processor.after_key_press += after_key

            async def press(keys: str) -> None:
                processed.clear()
                pipe.send_text(keys)
                await processed.wait()
                rendered.clear()
                triage.application.invalidate()
                await rendered.wait()

            async def interact() -> None:
                await rendered.wait()
                for rows, columns in ((30, 120), (24, 80), (12, 45)):
                    rendered.clear()
                    output.size = Size(rows=rows, columns=columns)
                    triage.application.invalidate()
                    await rendered.wait()
                    for direction in ('\x1b[D', '\x1b[C', '\x1b[C'):
                        await press(direction)
                        pane = triage.active
                        await press('\x1b[H')
                        assert pane.selected == pane.ids[0]
                        height = pane.window.render_info.window_height
                        assert height > 0
                        await press('\x1b[6~')
                        assert pane.selected == pane.ids[height]
                        assert pane.window.render_info.vertical_scroll == min(
                            height,
                            len(pane.ids) - height,
                        )
                        await press('\x1b[5~')
                        assert pane.selected == pane.ids[0]
                        await press('\x1b[F')
                        assert pane.selected == pane.ids[-1]
                    await press('\x1b[D')
                selected = triage.active.selected
                await press('\t')
                assert triage.focused is triage.inspector
                await press('\x1b[F')
                assert triage.inspector.row > 0
                assert triage.inspector.window.render_info.vertical_scroll > 0
                await press('\x1b[H')
                assert triage.inspector.row == 0
                await press('\x1b[6~')
                assert triage.inspector.row > 0
                await press('\x1b[5~')
                assert triage.inspector.row == 0
                await press('\t')
                assert triage.focused is triage.active
                assert triage.active.selected == selected
                pipe.send_text('\x03')

            interacting = asyncio.create_task(interact())
            running = asyncio.create_task(triage.run())
            tasks = [running, interacting]
            try:
                async with asyncio.timeout(10):
                    await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run())


def test_wheel_targets_the_hovered_pane_and_scrolls_details_independently() -> None:
    class Output(DummyOutput):
        def get_size(self) -> Size:
            out = Size(rows=18, columns=100)
            return out

    async def run() -> None:
        with (
            create_pipe_input() as pipe,
            create_app_session(input=pipe, output=Output()),
        ):
            trail = Trail()
            events = [
                record(trail)
                for _ in range(12)
            ]
            triage = Triage(trail)
            for event in events[:4]:
                triage.decisions[event.id] = DECLINED
            for event in events[8:]:
                triage.decisions[event.id] = ACCEPTED
            triage.sync()
            for pane in triage.panes:
                pane.home()
            rendered = asyncio.Event()
            processed = asyncio.Event()

            def after_render(application: Application) -> None:
                rendered.set()

            def after_key(processor: KeyProcessor) -> None:
                processed.set()

            triage.application.after_render += after_render
            triage.application.key_processor.after_key_press += after_key

            async def wheel(
                window: Window,
                button: int,
                edge: bool = False,
            ) -> None:
                screen = triage.application.renderer.last_rendered_screen
                position = screen.visible_windows_to_write_positions[window]
                x = position.xpos + 1
                y = position.ypos + 1
                if edge:
                    x = position.xpos + position.width
                    y = position.ypos + position.height
                processed.clear()
                pipe.send_text(f'\x1b[<{button};{x};{y}M')
                await processed.wait()
                rendered.clear()
                triage.application.invalidate()
                await rendered.wait()

            async def interact() -> None:
                await rendered.wait()
                decisions = triage.decisions.copy()
                for pane in triage.panes:
                    selected = [
                        other.selected
                        for other in triage.panes
                    ]
                    await wheel(pane.window, 65)
                    assert triage.active is pane
                    assert triage.focused is pane
                    assert pane.selected == pane.ids[1]
                    assert triage.inspector.identifier == pane.selected
                    for index, other in enumerate(triage.panes):
                        if other is not pane:
                            assert other.selected == selected[index]
                    await wheel(pane.header, 64, edge=True)
                    assert pane.selected == pane.ids[0]
                    await wheel(pane.window, 65, edge=True)
                    assert pane.selected == pane.ids[1]
                    await wheel(pane.window, 64)
                    await wheel(pane.window, 64)
                    assert pane.selected == pane.ids[0]
                selected = [
                    pane.selected
                    for pane in triage.panes
                ]
                await wheel(triage.inspector.window, 65)
                assert triage.focused is triage.inspector
                assert triage.inspector.row == 1
                assert triage.inspector.window.render_info.vertical_scroll == 1
                await wheel(triage.inspector.header, 65)
                assert triage.inspector.row == 2
                await wheel(triage.inspector.window, 64, edge=True)
                assert triage.inspector.row == 1
                await wheel(triage.inspector.window, 64)
                await wheel(triage.inspector.window, 64)
                assert triage.inspector.row == 0
                remaining = [
                    pane.selected
                    for pane in triage.panes
                ]
                assert remaining == selected
                assert triage.decisions == decisions
                pipe.send_text('\x03')

            interacting = asyncio.create_task(interact())
            running = asyncio.create_task(triage.run())
            tasks = [running, interacting]
            try:
                async with asyncio.timeout(5):
                    await asyncio.gather(*tasks)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run())


def test_standalone_command_takes_lock_before_loading(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trail = Trail(tmp_path, ontrail=None)
    event = record(trail)
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)

    async def run(
        self: Run,
        triage: bool = False,
    ) -> None:
        assert triage
        assert self._trail.lock.fd is not None
        assert self._trail.events[event.id].id == event.id
        self._trail.events.finalize([event.id], [])

    monkeypatch.setattr(Run, '__call__', run)
    assert main(['triage', str(tmp_path)]) == 0
    assert Trail(tmp_path).events[event.id].is_accepted

    holder = object()
    assert trail.lock.acquire(holder)
    try:
        before = trail.events.jsonl.path.read_bytes()
        with pytest.warns(UserWarning, match='already recording'):
            assert main(['triage', str(tmp_path)]) == 2
        assert trail.events.jsonl.path.read_bytes() == before
    finally:
        trail.lock.release(holder)


def test_standalone_command_requires_a_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr('sys.stdin.isatty', lambda: True)
    assert main(['triage', str(tmp_path)]) == 2
    assert not (tmp_path / '.trail').exists()
