from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.websocket import websocket_connect

from trail import Trail
from trail.event import FileAccessEvent
from trail.jupyter.event import ContentsServiceV1Event, ExecutionEvent, KernelSessionEvent
from trail.jupyter.session import JupyterSession

# a token per test process, so a test never talks to another process's server
TOKEN = f'trail-{uuid4().hex}'
NOTEBOOK = {'cells': [], 'metadata': {}, 'nbformat': 4, 'nbformat_minor': 5}


async def call(
        url: str,
        method: str = 'GET',
        body: dict | None = None,
) -> dict | list:
    data = None
    if body is not None:
        data = json.dumps(body)
    request = HTTPRequest(
        url,
        method=method,
        body=data,
        headers={'Authorization': f'token {TOKEN}'},
        allow_nonstandard_methods=True,
    )
    response = await AsyncHTTPClient().fetch(request)
    if not response.body:
        return {}
    out = json.loads(response.body)
    return out


@contextmanager
def served(root: Path) -> Iterator[str]:
    pytest.importorskip('jupyter_server')
    # the server picks a free port and writes its url to its runtime file
    server = subprocess.Popen(
        [
            sys.executable, '-m', 'jupyter_server',
            '--ServerApp.port=0',
            '--ServerApp.ip=127.0.0.1',
            f'--IdentityProvider.token={TOKEN}',
            f'--ServerApp.root_dir={root}',
            '--ServerApp.open_browser=False',
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    runtime = Path(os.environ['JUPYTER_RUNTIME_DIR']) / f'jpserver-{server.pid}.json'
    try:
        deadline = time.monotonic() + 30
        url = None
        # the file can exist before the server has written it
        while url is None:
            assert server.poll() is None, 'the server exited'
            assert time.monotonic() < deadline, 'the server wrote no runtime file'
            with suppress(OSError, ValueError, KeyError):
                url = json.loads(runtime.read_text(encoding='utf-8'))['url']
            time.sleep(0.1)
        yield url
    finally:
        server.terminate()
        server.wait(10)


async def until(
        predicate: Callable[[], bool],
        timeout: float = 30,
) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.05)


async def ready(url: str) -> None:
    async with asyncio.timeout(30):
        while True:
            try:
                await call(f'{url}api/status')
                return
            except Exception:  # noqa: BLE001
                await asyncio.sleep(0.2)


def project(tmp_path: Path) -> Path:
    root = tmp_path.resolve() / 'project'
    root.mkdir()
    (root / '.ontrail').write_text('./\n*.ipynb\n', encoding='utf-8')
    (root / 'analysis.ipynb').write_text(json.dumps(NOTEBOOK), encoding='utf-8')
    return root


def execute(
        code: str,
        cell: str,
        request: str,
) -> str:
    header = {
        'msg_id': request,
        'msg_type': 'execute_request',
        'session': uuid4().hex,
        'username': 'test',
        'version': '5.3',
        'date': datetime.now(UTC).isoformat(),
    }
    message = {
        'header': header,
        'parent_header': {},
        'metadata': {'cellId': cell},
        'content': {
            'code': code,
            'silent': False,
            'store_history': True,
            'user_expressions': {},
            'allow_stdin': False,
        },
        'channel': 'shell',
    }
    out = json.dumps(message)
    return out


def page(
        request: str,
        cell: str,
        kernel: str,
) -> str:
    # what JupyterLab's event feed sends for the request, as the server forwards it
    payload = {
        'schema_id': 'https://schema.jupytergis.org/events/notebook/v1',
        'verb': 'requested',
        'object': {
            'document_path': 'analysis.ipynb',
            'kernel_id': kernel,
            'request_id': request,
            'cell_id': cell,
        },
    }
    out = json.dumps(payload)
    return out


# each kernel's code: a cell with output, then a slow failing cell, and what the kernel publishes
KERNELS = {
    'python3': (
        "print('hi')\n41 + 1",
        'import time\ntime.sleep(1)\n1 / 0',
        {'stream': 1, 'execute_result': 1},
        'ZeroDivisionError',
    ),
    # bash_kernel reports a failing command with an empty error name and its exit code
    'bash': ('echo hi', 'sleep 1; ls /does-not-exist', {'stream': 1}, ''),
}


def kernelspecs() -> set[str]:
    try:
        from jupyter_client.kernelspec import KernelSpecManager
    except ImportError:
        return set()
    out = set(KernelSpecManager().find_kernel_specs())
    return out


@pytest.mark.parametrize('name', sorted(KERNELS))
def test_a_real_kernel_is_followed_from_attach_to_detach(
        name: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip('jupyter_server')
    if name not in kernelspecs():
        pytest.skip(f'no {name} kernel is installed')
    working, failing, outputs, ename = KERNELS[name]
    root = project(tmp_path)
    monkeypatch.setattr(JupyterSession, 'interval', 0.2)
    trail = Trail(root)

    async def run(url: str) -> None:
        await ready(url)
        trail.jupyter.connect(url=url, token=TOKEN, root=root)
        async with trail.jupyter.session:
            session = await call(
                f'{url}api/sessions',
                'POST',
                {'path': 'analysis.ipynb', 'type': 'notebook', 'name': '', 'kernel': {'name': name}},
            )
            kernel_id = session['kernel']['id']
            kernels = trail.jupyter.session.kernels
            await until(lambda: getattr(kernels.data.get((url, kernel_id)), 'socket', None) is not None)
            # the listener is connected once the kernel's first broadcast reached it
            await asyncio.sleep(1)
            host = url.removeprefix('http://')
            channels = f'ws://{host}api/kernels/{kernel_id}/channels?session_id={uuid4().hex}'
            request = HTTPRequest(channels, headers={'Authorization': f'token {TOKEN}'})
            client = await websocket_connect(request)
            consumer = trail.jupyter.session.consumer
            server = trail.jupyter.session.servers[url]
            first = uuid4().hex
            second = uuid4().hex
            # the page's message for the first request arrives before the kernel's input, and
            # for the second one after it
            consumer.receive(server, page(first, 'cell-a', kernel_id))
            client.write_message(execute(working, 'cell-a', first))
            client.write_message(execute(failing, 'cell-b', second))
            kernel = kernels.data[url, kernel_id]

            def submitted() -> bool:
                execution = kernel.executions.get(second)
                is_submitted = (
                    execution is not None
                    and execution.submitted is not None
                )
                return is_submitted

            await until(submitted)
            consumer.receive(server, page(second, 'cell-b', kernel_id))

            def finished() -> bool:
                out = len(trail.events.select('cls=ExecutionEvent not verb=started')) >= 2
                return out

            await until(finished)
            client.close()
            await call(f"{url}api/sessions/{session['id']}", 'DELETE')
            await until(lambda: len(trail.events.select('cls=KernelSessionEvent verb=detached')) == 1)

    with served(root) as url:
        asyncio.run(run(url))
    asset = trail.assets[root / 'analysis.ipynb']
    executions = [
        event
        for event in trail.events
        if isinstance(event, ExecutionEvent)
    ]
    assert [
        (event.verb, event.execution_count)
        for event in executions
    ] == [('started', 1), ('completed', 1), ('started', 2), ('errored', 2)]
    assert {event.entry for event in executions} == {asset}
    assert [event.cell_id for event in executions] == ['cell-a', 'cell-a', 'cell-b', 'cell-b']
    assert executions[1].data['outputs'] == outputs
    assert executions[3].data['error']['ename'] == ename
    sessions = [
        (event.verb, event.path)
        for event in trail.events
        if isinstance(event, KernelSessionEvent)
    ]
    assert sessions == [('attached', 'analysis.ipynb'), ('detached', 'analysis.ipynb')]



@pytest.mark.skipif(sys.platform != 'linux', reason='only inotify reports closes')
def test_saves_through_the_server_are_one_record_with_their_writes(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = project(tmp_path)
    monkeypatch.setattr(JupyterSession, 'interval', 0.2)
    trail = Trail(root)
    path = root / 'analysis.ipynb'

    async def run(url: str) -> None:
        await ready(url)
        trail.jupyter.connect(url=url, token=TOKEN, root=root)
        session = trail.watchdog.session
        session.fold_settle = 0.2
        async with session, trail.jupyter.session:
            server = trail.jupyter.session.servers[url]
            await until(lambda: server.state == 'subscribed')
            for count in range(3):
                cells = [{'cell_type': 'markdown', 'id': f'c{count}', 'metadata': {}, 'source': 'x' * count}]
                body = {'type': 'notebook', 'format': 'json', 'content': {**NOTEBOOK, 'cells': cells}}
                await call(f'{url}api/contents/analysis.ipynb', 'PUT', body)
                await asyncio.sleep(0.4)
            # the last save's file session ends before the run is logged
            await asyncio.sleep(0.5)

    with served(root) as url:
        asyncio.run(run(url))
    [saved] = [
        event
        for event in trail.events
        if isinstance(event, ContentsServiceV1Event)
    ]
    assert saved.saves == 3
    assert saved.writes == 3
    assert saved.entry is trail.assets[path]
    rewrites = [
        event
        for event in trail.events
        if isinstance(event, FileAccessEvent)
        and event.src_path == str(path)
        and event.event_type == 'modified'
    ]
    assert rewrites == []


def test_executions_before_trail_listened_are_recovered(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = project(tmp_path)
    monkeypatch.setattr(JupyterSession, 'interval', 0.2)
    trail = Trail(root)

    async def run(url: str) -> None:
        await ready(url)
        session = await call(
            f'{url}api/sessions',
            'POST',
            {'path': 'analysis.ipynb', 'type': 'notebook', 'name': '', 'kernel': {'name': 'python3'}},
        )
        kernel_id = session['kernel']['id']
        host = url.removeprefix('http://')
        channels = f'ws://{host}api/kernels/{kernel_id}/channels?session_id={uuid4().hex}'
        request = HTTPRequest(channels, headers={'Authorization': f'token {TOKEN}'})
        client = await websocket_connect(request)
        # two cells run before Trail listens to the kernel
        for code in ('x = 1', 'y = x + 1'):
            client.write_message(execute(code, 'cell', uuid4().hex))
            while True:
                reply = json.loads(await client.read_message())
                if reply['header']['msg_type'] == 'execute_reply':
                    break
        trail.jupyter.connect(url=url, token=TOKEN, root=root)
        async with trail.jupyter.session:
            await until(lambda: len(trail.events.select('cls=ExecutionEvent verb=recovered')) == 2)
            client.write_message(execute('z = y * 2', 'cell-c', uuid4().hex))
            await until(lambda: len(trail.events.select('cls=ExecutionEvent verb=completed')) == 1)
        # a later run of Trail asks again and logs nothing twice
        reopened = Trail(root)
        reopened.jupyter.connect(url=url, token=TOKEN, root=root)
        async with reopened.jupyter.session:
            kernels = reopened.jupyter.session.kernels
            await until(lambda: getattr(kernels.data.get((url, kernel_id)), 'socket', None) is not None)
            await until(lambda: not kernels.data[url, kernel_id].history)
        client.close()

    with served(root) as url:
        asyncio.run(run(url))
    reopened = Trail(root)
    logged = [
        (event.verb, event.execution_count, event.code)
        for event in reopened.events
        if isinstance(event, ExecutionEvent)
    ]
    assert logged == [
        ('recovered', 1, 'x = 1'),
        ('recovered', 2, 'y = x + 1'),
        ('started', 3, 'z = y * 2'),
        ('completed', 3, 'z = y * 2'),
    ]
