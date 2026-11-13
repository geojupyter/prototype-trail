from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from trail import Trail
from trail.cli.render import Renderer
from trail.jupyter.event import ExecutionEvent, KernelSessionEvent
from trail.jupyter.kernels import Kernel, Kernels
from trail.jupyter.server import Server

FIXTURE = Path(__file__).parent / 'fixtures' / 'iopub.jsonl'


@pytest.fixture
def kernels(
        trail: Trail,
        monkeypatch: pytest.MonkeyPatch,
) -> Kernels:
    # nothing here connects a websocket
    monkeypatch.setattr(Kernels, 'connect', lambda self, kernel: None)
    out = trail.jupyter.session.kernels
    return out


def notebook(
        trail: Trail,
        name: str = 'analysis.ipynb',
) -> Path:
    path = trail.dir.parent / name
    path.write_text('{}', encoding='utf-8')
    trail.assets.add({path: None})
    return path


def executions(trail: Trail) -> list[ExecutionEvent]:
    out = [
        event
        for event in trail.events
        if isinstance(event, ExecutionEvent)
    ]
    return out


def test_a_captured_run_is_logged_per_execution(
        trail: Trail,
        server: Server,
        kernels: Kernels,
) -> None:
    path = notebook(trail)
    kernels.update(server, [{'path': 'analysis.ipynb', 'kernel': {'id': 'K1', 'name': 'python3'}}])
    kernel = kernels.data[server.json.url, 'K1']
    for line in FIXTURE.read_text(encoding='utf-8').splitlines():
        kernels.receive(kernel, line)
    logged = executions(trail)
    assert [
        (event.verb, event.execution_count)
        for event in logged
    ] == [
        ('started', 1), ('completed', 1),
        ('started', 2), ('completed', 2),
        ('started', 3), ('errored', 3),
        ('started', 4), ('completed', 4),
        ('started', 1), ('completed', 1),
    ]
    asset = trail.assets[path]
    assert {event.entry for event in logged} == {asset}
    started, completed = logged[2:4]
    assert started.code == "print('hello from a cell')\nx"
    assert completed.data['outputs'] == {'stream': 1, 'execute_result': 1}
    assert completed.data['started_id'] == started.id
    assert completed.request_id == started.request_id
    errored = logged[5]
    assert errored.status == 'error'
    assert errored.data['error'] == {'ename': 'ZeroDivisionError', 'evalue': 'division by zero'}
    rows = dict(errored._repr_items())
    assert rows['error'] == 'ZeroDivisionError: division by zero'
    assert Renderer.verb(errored) == 'errored'
    slow = logged[7]
    assert slow.duration == pytest.approx(2.0, abs=0.1)
    sessions = trail.events.select('cls=KernelSessionEvent')
    assert [event.verb for event in sessions] == ['attached', 'restarted']
    assert logged[8].data['kernel_session'] != logged[0].data['kernel_session']
    assert trail.events.select('cls=ExecutionEvent verb=errored').ids == [errored.id]
    reopened = Trail(trail.dir)
    for event in logged:
        rebuilt = reopened.events[event.id]
        assert type(rebuilt) is ExecutionEvent
        assert rebuilt.to_record() == event.to_record()
        assert rebuilt.entry is reopened.assets[path]


def test_sessions_attach_and_detach_notebooks(
        trail: Trail,
        server: Server,
        kernels: Kernels,
) -> None:
    first = notebook(trail, 'a.ipynb')
    notebook(trail, 'b.ipynb')
    kernels.update(server, [{'path': 'a.ipynb', 'kernel': {'id': 'K1', 'name': 'python3'}}])
    kernels.update(server, [
        {'path': 'a.ipynb', 'kernel': {'id': 'K1', 'name': 'python3'}},
        {'path': 'b.ipynb', 'kernel': {'id': 'K2', 'name': 'python3'}},
    ])
    # a.ipynb was renamed, so the server reports its new path on the same kernel
    first.rename(first.with_name('renamed.ipynb'))
    kernels.update(server, [
        {'path': 'renamed.ipynb', 'kernel': {'id': 'K1', 'name': 'python3'}},
        {'path': 'b.ipynb', 'kernel': {'id': 'K2', 'name': 'python3'}},
    ])
    kernels.update(server, [{'path': 'renamed.ipynb', 'kernel': {'id': 'K1', 'name': 'python3'}}])
    logged = [
        (event.verb, event.path, event.kernel_id)
        for event in trail.events
        if isinstance(event, KernelSessionEvent)
    ]
    assert logged == [
        ('attached', 'a.ipynb', 'K1'),
        ('attached', 'b.ipynb', 'K2'),
        ('attached', 'renamed.ipynb', 'K1'),
        ('detached', 'a.ipynb', 'K1'),
        ('detached', 'b.ipynb', 'K2'),
    ]
    assert (server.json.url, 'K2') not in kernels.data


def test_a_later_run_does_not_attach_a_logged_session_again(
        trail: Trail,
        server: Server,
        kernels: Kernels,
) -> None:
    notebook(trail, 'a.ipynb')
    notebook(trail, 'b.ipynb')
    sessions = [
        {'path': 'a.ipynb', 'kernel': {'id': 'K1', 'name': 'python3'}},
        {'path': 'b.ipynb', 'kernel': {'id': 'K1', 'name': 'python3'}},
    ]
    kernels.update(server, sessions)
    reopened = Trail(trail.dir)
    later = reopened.jupyter.session.kernels
    # b.ipynb was closed while Trail was not running
    later.update(server, sessions[:1])
    logged = [
        (event.verb, event.path)
        for event in reopened.events
        if isinstance(event, KernelSessionEvent)
    ]
    assert logged == [('attached', 'a.ipynb'), ('attached', 'b.ipynb'), ('detached', 'b.ipynb')]


def test_a_placeholder_session_is_not_a_notebook(
        trail: Trail,
        server: Server,
        kernels: Kernels,
) -> None:
    # JupyterLab starts a new notebook's kernel under a made-up path, then renames the session
    placeholder = '8a00f17e-3036-4608-be85-cd3fceba4b19'
    kernels.update(server, [{'path': placeholder, 'kernel': {'id': 'K1', 'name': 'python3'}}])
    kernel = kernels.data[server.json.url, 'K1']
    kernels.receive(kernel, message('status', execution_state='busy'))
    event = kernels.receive(kernel, message('execute_input', code='x = 1', execution_count=1))
    assert event.src_path == ''
    notebook(trail, 'Untitled.ipynb')
    kernels.update(server, [{'path': 'Untitled.ipynb', 'kernel': {'id': 'K1', 'name': 'python3'}}])
    kernels.update(server, [])
    logged = [
        (event.verb, event.path)
        for event in trail.events
        if isinstance(event, KernelSessionEvent)
    ]
    assert logged == [('attached', 'Untitled.ipynb'), ('detached', 'Untitled.ipynb')]


def message(
        kind: str,
        request: str = 'R1',
        **content: object,
) -> str:
    payload = {
        'channel': 'iopub',
        'header': {'msg_type': kind, 'session': 'S1', 'date': '2026-11-12T10:00:00.000000Z'},
        'parent_header': {'msg_type': 'execute_request', 'msg_id': request},
        'content': content,
    }
    out = json.dumps(payload)
    return out


def test_a_shared_kernel_names_no_notebook(
        trail: Trail,
        server: Server,
        kernels: Kernels,
) -> None:
    notebook(trail, 'a.ipynb')
    kernels.update(server, [
        {'path': 'a.ipynb', 'kernel': {'id': 'K1', 'name': 'python3'}},
        {'path': 'console-1', 'kernel': {'id': 'K1', 'name': 'python3'}},
    ])
    kernel = kernels.data[server.json.url, 'K1']
    kernels.receive(kernel, message('status', execution_state='busy'))
    event = kernels.receive(kernel, message('execute_input', code='x = 1', execution_count=1))
    assert event.entry is None
    assert event.src_path == ''
    assert dict(event._repr_items())['paths'] == ['a.ipynb', 'console-1']


def test_a_missed_start_and_other_messages_are_ignored(
        trail: Trail,
        server: Server,
        kernels: Kernels,
) -> None:
    kernel = Kernel(server=server, id='K1', paths=('a.ipynb',))
    for text in (
        message('execute_input', code='x', execution_count=1),
        message('status', execution_state='idle'),
        'not json',
        json.dumps({'channel': 'shell', 'header': {}, 'content': {}}),
        json.dumps({'channel': 'iopub', 'header': {'msg_type': 'status'}, 'parent_header': {}}),
    ):
        assert kernels.receive(kernel, text) is None
    assert executions(trail) == []


def page_request(
        request: str = 'R1',
        cell: str | None = 'cell-3',
        path: str = 'a.ipynb',
        kernel: str = 'K1',
) -> str:
    obj = {'document_path': path, 'kernel_id': kernel, 'request_id': request}
    if cell is not None:
        obj['cell_id'] = cell
    payload = {
        'schema_id': 'https://schema.jupytergis.org/events/notebook/v1',
        'verb': 'requested',
        'object': obj,
    }
    out = json.dumps(payload)
    return out


def test_the_page_names_the_cell_and_notebook_of_a_shared_kernel(
        trail: Trail,
        server: Server,
        kernels: Kernels,
) -> None:
    path = notebook(trail, 'a.ipynb')
    kernels.update(server, [
        {'path': 'a.ipynb', 'kernel': {'id': 'K1', 'name': 'python3'}},
        {'path': 'console-1', 'kernel': {'id': 'K1', 'name': 'python3'}},
    ])
    received = trail.jupyter.session.consumer.receive(server, page_request())
    assert received.outcome == 'used'
    assert trail.events.select('cls=JupyterEvent').ids == [
        event.id
        for event in trail.events.select('cls=KernelSessionEvent')
    ]
    kernel = kernels.data[server.json.url, 'K1']
    kernels.receive(kernel, message('status', execution_state='busy'))
    started = kernels.receive(kernel, message('execute_input', code='x = 1', execution_count=1))
    kernels.receive(kernel, message('status', execution_state='idle'))
    completed = executions(trail)[-1]
    for event in (started, completed):
        assert event.cell_id == 'cell-3'
        assert event.entry is trail.assets[path]
        assert dict(event._repr_items())['cell_id'] == 'cell-3'
    assert trail.events.select('cls=ExecutionEvent cell_id=cell-3').ids == [started.id, completed.id]


def test_a_started_record_waits_briefly_for_the_page(
        trail: Trail,
        server: Server,
        kernels: Kernels,
) -> None:
    notebook(trail, 'a.ipynb')
    kernels.update(server, [{'path': 'a.ipynb', 'kernel': {'id': 'K1', 'name': 'python3'}}])
    kernel = kernels.data[server.json.url, 'K1']
    consumer = trail.jupyter.session.consumer
    kernels.wait = 0.05

    async def run() -> None:
        # the page's message arrives after the kernel's input
        kernels.receive(kernel, message('status', 'R1', execution_state='busy'))
        assert kernels.receive(kernel, message('execute_input', 'R1', code='a', execution_count=1)) is None
        assert executions(trail) == []
        consumer.receive(server, page_request('R1', 'cell-1'))
        # no message from a page, as for a request sent by another client
        kernels.receive(kernel, message('status', 'R2', execution_state='busy'))
        kernels.receive(kernel, message('execute_input', 'R2', code='b', execution_count=2))
        await asyncio.sleep(0.2)
        kernels.receive(kernel, message('status', 'R2', execution_state='idle'))

    asyncio.run(run())
    assert [
        (event.verb, event.code, event.cell_id)
        for event in executions(trail)
    ] == [
        ('started', 'a', 'cell-1'),
        ('started', 'b', None),
        ('completed', 'b', None),
    ]


def history_reply(
        request: str,
        entries: list[list[object]],
        session: str = 'S1',
) -> str:
    payload = {
        'channel': 'shell',
        'header': {'msg_type': 'history_reply', 'session': session},
        'parent_header': {'msg_type': 'history_request', 'msg_id': request},
        'content': {'status': 'ok', 'history': entries},
    }
    out = json.dumps(payload)
    return out


def test_history_recovers_the_inputs_trail_missed(
        trail: Trail,
        server: Server,
        kernels: Kernels,
) -> None:
    notebook(trail, 'a.ipynb')
    kernels.update(server, [{'path': 'a.ipynb', 'kernel': {'id': 'K1', 'name': 'python3'}}])
    kernel = kernels.data[server.json.url, 'K1']
    kernel.session = 'S1'
    # execution 2 was seen live
    kernels.receive(kernel, message('status', 'R2', execution_state='busy'))
    kernels.receive(kernel, message('execute_input', 'R2', code='b', execution_count=2))
    kernels.receive(kernel, message('status', 'R2', execution_state='idle'))
    kernel.history = 'H1'
    entries = [[0, 1, 'a'], [0, 2, 'b'], [0, 3, 'c'], ['bad']]
    # a reply to another client's request is not Trail's
    assert kernels.receive(kernel, history_reply('other', entries)) is None
    assert len(executions(trail)) == 2
    kernels.receive(kernel, history_reply('H1', entries))
    recovered = trail.events.select('cls=ExecutionEvent verb=recovered')
    assert [(event.execution_count, event.code) for event in recovered] == [(1, 'a'), (3, 'c')]
    for event in recovered:
        # history does not say which notebook ran an input
        assert event.entry is None
        assert event.data['paths'] == ['a.ipynb']
        assert dict(event._repr_items())['code'] == event.code
    # the request was answered once
    kernels.receive(kernel, history_reply('H1', entries))
    assert len(trail.events.select('cls=ExecutionEvent verb=recovered')) == 2
    # a later Trail asking the same kernel finds them in the log
    reopened = Trail(trail.dir)
    later = reopened.jupyter.session.kernels
    later.update(server, [{'path': 'a.ipynb', 'kernel': {'id': 'K1', 'name': 'python3'}}])
    again = later.data[server.json.url, 'K1']
    again.history = 'H2'
    later.receive(again, history_reply('H2', entries))
    assert len(reopened.events.select('cls=ExecutionEvent verb=recovered')) == 2
