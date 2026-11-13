from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from tornado.httpclient import AsyncHTTPClient, HTTPRequest
from tornado.websocket import websocket_connect

from trail.jupyter.event import ExecutionEvent, KernelSessionEvent
from trail.node import Node

if TYPE_CHECKING:
    from tornado.websocket import WebSocketClientConnection

    from trail.asset import Asset
    from trail.jupyter.message import Message
    from trail.jupyter.server import Server
    from trail.jupyter.session import JupyterSession

OUTPUTS = ('stream', 'display_data', 'execute_result', 'update_display_data')


def moment(header: dict[str, Any]) -> datetime:
    # the kernel stamps each message. A message without a stamp is dated when it arrives
    value = header.get('date')
    if isinstance(value, str):
        try:
            out = datetime.fromisoformat(value)
        except ValueError:
            pass
        else:
            if out.tzinfo is None:
                out = out.replace(tzinfo=UTC)
            return out
    out = datetime.now(UTC)
    return out


def history_request(identifier: str) -> str:
    # the inputs of the kernel's current session, as typed
    header = {
        'msg_id': identifier,
        'msg_type': 'history_request',
        'session': uuid4().hex,
        'username': 'trail',
        'version': '5.3',
        'date': datetime.now(UTC).isoformat(),
    }
    message = {
        'header': header,
        'parent_header': {},
        'metadata': {},
        'content': {
            'output': False,
            'raw': True,
            'hist_access_type': 'range',
            'session': 0,
            'start': 1,
            'stop': None,
        },
        'channel': 'shell',
    }
    out = json.dumps(message)
    return out


@dataclass(frozen=True, slots=True, kw_only=True)
class Request:
    # what the page that sent an execute request said about it
    kernel_id: str
    document_path: str | None = None
    cell_id: str | None = None


@dataclass(slots=True, kw_only=True, eq=False)
class Execution:
    request_id: str
    started: datetime
    code: str = ''
    execution_count: int | None = None
    # when the code arrived, which a deferred started record keeps
    submitted: datetime | None = None
    outputs: dict[str, int] = field(default_factory=dict)
    error: dict[str, str] | None = None
    event: ExecutionEvent | None = None
    timer: asyncio.TimerHandle | None = None


@dataclass(slots=True, kw_only=True, eq=False)
class Kernel:
    server: Server
    id: str
    name: str = ''
    # the notebooks and consoles the server says use this kernel
    paths: tuple[str, ...] = ()
    # the paths that name a file, which attaching and detaching are logged for. JupyterLab starts
    # a new notebook's kernel under a placeholder name before the file exists
    documents: tuple[str, ...] = ()
    # the kernel process's message session, which changes when the kernel restarts
    session: str = ''
    executions: dict[str, Execution] = field(default_factory=dict)
    # the id of Trail's own history request, whose reply lists the inputs Trail missed
    history: str = ''
    task: asyncio.Task[None] | None = None
    socket: WebSocketClientConnection | None = None


class Kernels(Node):
    _parent: JupyterSession
    # seconds a websocket waits before it connects again
    backoff = 2.0
    # trail: execution - seconds a started record waits for the page to name its cell
    wait = 0.5
    request_limit = 10000

    def __init__(self, parent: JupyterSession) -> None:
        super().__init__(parent)
        self.data: dict[tuple[str, str], Kernel] = {}
        self.polls: dict[str, asyncio.Task[None]] = {}
        self.requests: OrderedDict[str, Request] = OrderedDict()

    def request(
            self,
            server: Server,
            record: Message,
    ) -> None:
        obj = record.data.get('object')
        if not isinstance(obj, dict):
            raise TypeError('Expected an object')
        request_id = obj.get('request_id')
        kernel_id = obj.get('kernel_id')
        if (
            not isinstance(request_id, str)
            or not isinstance(kernel_id, str)
        ):
            raise TypeError('Expected a request and a kernel id')
        document_path = obj.get('document_path')
        if not isinstance(document_path, str):
            document_path = None
        cell_id = obj.get('cell_id')
        if not isinstance(cell_id, str):
            cell_id = None
        self.requests[request_id] = Request(
            kernel_id=kernel_id,
            document_path=document_path,
            cell_id=cell_id,
        )
        while len(self.requests) > self.request_limit:
            self.requests.popitem(last=False)
        record.src_path = document_path or ''
        record.outcome = 'used'
        kernel = self.data.get((server.json.url, kernel_id))
        if kernel is None:
            return
        execution = kernel.executions.get(request_id)
        if (
            execution is not None
            and execution.submitted is not None
        ):
            self.start(kernel, execution)

    def refresh(self) -> None:
        for url, server in self._parent.servers.items():
            poll = self.polls.get(url)
            if (
                server.state != 'subscribed'
                or (
                    poll is not None
                    and not poll.done()
                )
            ):
                continue
            coroutine = self.poll(server)
            self.polls[url] = asyncio.create_task(coroutine, name=f'jupyter-sessions-{server.number}')

    async def poll(self, server: Server) -> None:
        url = f'{server.json.url}api/sessions'
        request = HTTPRequest(
            url,
            headers=server.headers(),
            follow_redirects=False,
            request_timeout=10,
        )
        try:
            response = await AsyncHTTPClient().fetch(request)
            sessions = json.loads(response.body)
        except Exception:  # noqa: BLE001
            return
        self.update(server, sessions)

    def update(
            self,
            server: Server,
            sessions: list[dict[str, Any]],
    ) -> None:
        found: dict[str, Kernel] = {}
        paths: dict[str, list[str]] = {}
        for session in sessions:
            kernel = session.get('kernel') or {}
            identifier = kernel.get('id')
            path = session.get('path')
            if (
                not isinstance(identifier, str)
                or not isinstance(path, str)
            ):
                continue
            paths.setdefault(identifier, []).append(path)
            key = (server.json.url, identifier)
            known = self.data.get(key)
            if known is None:
                # an earlier run of Trail may have logged the kernel's sessions already
                known = Kernel(
                    server=server,
                    id=identifier,
                    name=str(kernel.get('name', '')),
                    documents=self.logged_documents(server, identifier),
                )
            found[identifier] = known
        for identifier, kernel in found.items():
            current = tuple(sorted(paths[identifier]))
            documents = tuple(
                path
                for path in current
                if path in kernel.documents
                or self.is_document(server, path)
            )
            for path in documents:
                if path not in kernel.documents:
                    self.log_session(kernel, 'attached', path)
            for path in kernel.documents:
                if path not in documents:
                    self.log_session(kernel, 'detached', path)
            kernel.paths = current
            kernel.documents = documents
            key = (server.json.url, identifier)
            if key not in self.data:
                self.data[key] = kernel
                self.connect(kernel)
        for key, kernel in list(self.data.items()):
            if (
                key[0] == server.json.url
                and key[1] not in found
            ):
                for path in kernel.documents:
                    self.log_session(kernel, 'detached', path)
                self.drop(key)

    def logged_documents(
            self,
            server: Server,
            identifier: str,
    ) -> tuple[str, ...]:
        """The paths whose last logged session event for a kernel is not a detach."""
        verbs: dict[str, str] = {}
        for event in self._trail.events:
            if (
                isinstance(event, KernelSessionEvent)
                and event.server == server.json.url
                and event.kernel_id == identifier
                and event.path is not None
            ):
                verbs[event.path] = event.verb
        attached = [
            path
            for path, verb in verbs.items()
            if verb != 'detached'
        ]
        out = tuple(sorted(attached))
        return out

    @staticmethod
    def is_document(
            server: Server,
            path: str,
    ) -> bool:
        located = server.locate(path)
        is_file = (
            located is not None
            and located.is_file()
        )
        return is_file

    def connect(self, kernel: Kernel) -> None:
        coroutine = self.listen(kernel)
        kernel.task = asyncio.create_task(coroutine, name=f'jupyter-kernel-{kernel.id[:8]}')

    async def listen(self, kernel: Kernel) -> None:
        server = kernel.server
        parts = urlsplit(server.json.url)
        if parts.scheme == 'https':
            scheme = 'wss'
        else:
            scheme = 'ws'
        path = f'{parts.path}api/kernels/{kernel.id}/channels'
        query = f'session_id={uuid4().hex}'
        url = urlunsplit((scheme, parts.netloc, path, query, ''))
        while self.data.get((server.json.url, kernel.id)) is kernel:
            socket = None
            try:
                request = HTTPRequest(
                    url,
                    headers=server.headers(),
                    follow_redirects=False,
                    connect_timeout=10,
                )
                socket = await websocket_connect(request, max_message_size=64 * 1024 * 1024)
                kernel.socket = socket
                # trail: execution - ask for the inputs that ran before Trail was listening
                kernel.history = uuid4().hex
                socket.write_message(history_request(kernel.history))
                while True:
                    text = await socket.read_message()
                    if text is None:
                        break
                    # a binary frame carries buffers, such as a widget's, and no execution
                    if isinstance(text, str):
                        self.receive(kernel, text)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                server.counts.skipped['kernel connection'] += 1
            finally:
                if socket is not None:
                    socket.close()
                kernel.socket = None
            await asyncio.sleep(self.backoff)

    def receive(
            self,
            kernel: Kernel,
            text: str,
    ) -> ExecutionEvent | None:
        try:
            message = json.loads(text)
        except ValueError:
            return None
        if not isinstance(message, dict):
            return None
        header = message.get('header') or {}
        parent = message.get('parent_header') or {}
        if (
            message.get('channel') == 'shell'
            and header.get('msg_type') == 'history_reply'
            and kernel.history
            and parent.get('msg_id') == kernel.history
        ):
            kernel.history = ''
            self.recover(kernel, message)
            return None
        if message.get('channel') != 'iopub':
            return None
        session = header.get('session')
        if (
            isinstance(session, str)
            and session != kernel.session
        ):
            if kernel.session:
                for path in kernel.documents:
                    self.log_session(kernel, 'restarted', path)
                kernel.executions.clear()
            kernel.session = session
        if parent.get('msg_type') != 'execute_request':
            return None
        request_id = parent.get('msg_id')
        if not isinstance(request_id, str):
            return None
        kind = header.get('msg_type')
        content = message.get('content') or {}
        at = moment(header)
        execution = kernel.executions.get(request_id)
        if execution is None:
            # an execution whose start was missed is not logged
            if (
                kind != 'status'
                or content.get('execution_state') != 'busy'
            ):
                return None
            execution = Execution(request_id=request_id, started=at)
            kernel.executions[request_id] = execution
            return None
        if kind == 'execute_input':
            execution.code = str(content.get('code', ''))
            count = content.get('execution_count')
            if type(count) is int:
                execution.execution_count = count
            execution.submitted = at
            if request_id in self.requests:
                out = self.start(kernel, execution)
                return out
            # the page's message about the request may still be on its way
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                out = self.start(kernel, execution)
                return out
            execution.timer = loop.call_later(self.wait, self.start, kernel, execution)
            return None
        if kind in OUTPUTS:
            execution.outputs[kind] = execution.outputs.get(kind, 0) + 1
            return None
        if kind == 'error':
            execution.error = {
                'ename': str(content.get('ename', '')),
                'evalue': str(content.get('evalue', '')),
            }
            return None
        if (
            kind == 'status'
            and content.get('execution_state') == 'idle'
        ):
            del kernel.executions[request_id]
            if execution.submitted is None:
                return None
            self.start(kernel, execution)
            if execution.error is None:
                verb = 'completed'
            else:
                verb = 'errored'
            out = self.log(kernel, execution, verb, at)
            return out
        return None

    def recover(
            self,
            kernel: Kernel,
            reply: dict[str, Any],
    ) -> list[ExecutionEvent]:
        # history keeps no time, outcome, notebook or cell. The kernel's current documents are
        # kept as context, but they do not show which one ran an input
        session = (reply.get('header') or {}).get('session')
        entries = (reply.get('content') or {}).get('history') or []
        if not isinstance(session, str):
            return []
        logged = {
            event.execution_count
            for event in self._trail.events
            if isinstance(event, ExecutionEvent)
            and event.data.get('kernel_session') == session
        }
        running = {
            execution.execution_count
            for execution in kernel.executions.values()
        }
        recovered = []
        for entry in entries:
            if (
                not isinstance(entry, list)
                or len(entry) < 3
                or type(entry[1]) is not int
            ):
                continue
            count = entry[1]
            if (
                count in logged
                or count in running
            ):
                continue
            data = {
                'verb': 'recovered',
                'kernel_id': kernel.id,
                'kernel_session': session,
                'request_id': None,
                'execution_count': count,
                'code': str(entry[2]),
                'paths': list(kernel.paths),
                'cell_id': None,
            }
            event = ExecutionEvent(
                schema_id=ExecutionEvent.schema,
                data=data,
                server=kernel.server.json.url,
            )
            event.apply(self._trail)
            recovered.append(event)
        return recovered

    def start(
            self,
            kernel: Kernel,
            execution: Execution,
    ) -> ExecutionEvent | None:
        if execution.timer is not None:
            execution.timer.cancel()
            execution.timer = None
        if (
            execution.event is not None
            or execution.submitted is None
        ):
            return None
        out = self.log(kernel, execution, 'started', execution.submitted)
        execution.event = out
        return out

    def resolve(
            self,
            kernel: Kernel,
            path: str | None = None,
    ) -> tuple[str, Asset | None]:
        if path is None:
            # a kernel names its document only when that document is its one session
            if (
                len(kernel.paths) != 1
                or len(kernel.documents) != 1
            ):
                return '', None
            path = kernel.documents[0]
        located = kernel.server.locate(path)
        if located is None:
            return '', None
        trail = self._trail
        asset = trail.assets.get(located)
        if (
            asset is None
            and located.is_file()
            and self._parent._parent.is_inside(located.parent)
        ):
            trail.assets.scan(located)
            asset = trail.assets.get(located)
        out = (str(located), asset)
        return out

    def log(
            self,
            kernel: Kernel,
            execution: Execution,
            verb: str,
            at: datetime,
    ) -> ExecutionEvent:
        # the page that sent the request names its document, even on a kernel shared by several
        request = self.requests.get(execution.request_id)
        if (
            request is not None
            and request.document_path
        ):
            src_path, asset = self.resolve(kernel, request.document_path)
        else:
            src_path, asset = self.resolve(kernel)
        data: dict[str, Any] = {
            'verb': verb,
            'kernel_id': kernel.id,
            'kernel_session': kernel.session,
            'request_id': execution.request_id,
            'execution_count': execution.execution_count,
            'code': execution.code,
            'paths': list(kernel.paths),
            'started_at': execution.started.isoformat(),
        }
        if request is not None:
            data['cell_id'] = request.cell_id
            data['document_path'] = request.document_path
        if verb != 'started':
            data['ended_at'] = at.isoformat()
            data['outputs'] = dict(execution.outputs)
            data['error'] = execution.error
            if execution.event is not None:
                data['started_id'] = execution.event.id
        event = ExecutionEvent(
            schema_id=ExecutionEvent.schema,
            data=data,
            server=kernel.server.json.url,
            src_path=src_path,
            timestamp=at,
        )
        event.entry = asset
        event.apply(self._trail)
        return event

    def log_session(
            self,
            kernel: Kernel,
            verb: str,
            path: str,
    ) -> KernelSessionEvent:
        src_path, asset = self.resolve(kernel, path)
        data = {
            'verb': verb,
            'kernel_id': kernel.id,
            'kernel_name': kernel.name,
            'kernel_session': kernel.session,
            'path': path,
        }
        event = KernelSessionEvent(
            schema_id=KernelSessionEvent.schema,
            data=data,
            server=kernel.server.json.url,
            src_path=src_path,
        )
        event.entry = asset
        event.apply(self._trail)
        return event

    def drop(self, key: tuple[str, str]) -> None:
        kernel = self.data.pop(key, None)
        if kernel is None:
            return
        for execution in kernel.executions.values():
            if execution.timer is not None:
                execution.timer.cancel()
        if kernel.socket is not None:
            kernel.socket.close()
        if (
            kernel.task is not None
            and not kernel.task.done()
            and not kernel.task.get_loop().is_closed()
        ):
            kernel.task.cancel()

    def close(self) -> None:
        for key in list(self.data):
            self.drop(key)
        for poll in self.polls.values():
            if (
                not poll.done()
                and not poll.get_loop().is_closed()
            ):
                poll.cancel()
        self.polls.clear()
