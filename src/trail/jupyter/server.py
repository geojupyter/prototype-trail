from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit, urlunsplit

import psutil
from jupyter_core.paths import jupyter_runtime_dir

from trail import fmt

if TYPE_CHECKING:
    from tornado.websocket import WebSocketClientConnection

    from trail.jupyter.session import JupyterSession

State = Literal[
    'available', 'connecting', 'subscribed', 'retrying', 'refused',
    'unsupported', 'error', 'disconnected', 'stopped',
]


def normalize_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme not in ('http', 'https')
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise ValueError('Use an HTTP(S) server URL without credentials, query or fragment')
    out = urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/') + '/', '', ''))
    return out


class Secret:
    __slots__ = ('value',)

    def __init__(self, value: str) -> None:
        self.value = value

    def __repr__(self) -> str:
        return '<secret>'

    def __reduce__(self) -> None:
        raise TypeError('Tokens cannot be pickled')


@dataclass(frozen=True, slots=True, kw_only=True)
class JSON:
    url: str
    root: Path
    token: Secret = field(repr=False, compare=False)
    path: Path | None = None
    pid: int | None = None
    written: float = 0.0
    sock: str = ''
    password: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, 'url', normalize_url(self.url))
        root = (
            Path(self.root)
            .expanduser()
            .resolve()
        )
        object.__setattr__(self, 'root', root)

    @property
    def is_running(self) -> bool:
        if self.path is None:
            return True
        if self.pid is None or self.pid <= 0:
            return False
        try:
            process = psutil.Process(self.pid)
            out = (
                process.is_running()
                and process.status() != psutil.STATUS_ZOMBIE
                and process.create_time() <= self.written + 1
            )
        except psutil.Error:
            return False
        return out


class JSONs(Mapping[Path, JSON]):
    def __init__(self, directory: Path | None = None) -> None:
        if directory is None:
            directory = Path(jupyter_runtime_dir())
        self.directory = directory
        self.data: dict[Path, JSON] = {}

    def __getitem__(self, key: Path) -> JSON:
        out = self.data[key]
        return out

    def __iter__(self) -> Iterator[Path]:
        out = iter(self.data)
        return out

    def __len__(self) -> int:
        out = len(self.data)
        return out

    def refresh(self) -> None:
        found = {}
        for path in sorted(self.directory.glob('jpserver-*.json')):
            try:
                stat = path.stat()
                data = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(data, dict):
                    continue
                root = data['root_dir']
                token = data.get('token', '')
                pid = data.get('pid')
                if (
                    not isinstance(root, str)
                    or not Path(root).is_absolute()
                    or not isinstance(token, str)
                    or type(pid) is not int
                ):
                    continue
                found[path] = JSON(
                    path=path,
                    url=data['url'],
                    root=Path(root),
                    token=Secret(token),
                    pid=pid,
                    written=stat.st_mtime,
                    sock=data.get('sock', ''),
                    password=bool(data.get('password', False)),
                )
            except (OSError, ValueError, KeyError, TypeError):
                continue
        self.data = found

    def __repr__(self) -> str:
        lines = [f'{type(self).__name__} ({len(self)})']
        lines.extend(
            f'    {path}: {info.url} {info.root}'
            for path, info in self.items()
        )
        out = '\n'.join(lines)
        return out


@dataclass(slots=True)
class Counts:
    received: int = 0
    recorded: int = 0
    skipped: Counter[str] = field(default_factory=Counter)
    other: Counter[str] = field(default_factory=Counter)


@dataclass(eq=False, kw_only=True, repr=False)
class Server:
    servers: Servers = field(repr=False)
    number: int
    json: JSON
    state: State = 'available'
    last_error: str = ''
    counts: Counts = field(default_factory=Counts)
    schemas: dict[str, str] = field(default_factory=dict)
    renames: dict[str, str] = field(default_factory=dict)
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    socket: WebSocketClientConnection | None = field(default=None, repr=False)
    backoff: float = 0.5
    ceiling: float = 30.0

    @property
    def is_running(self) -> bool:
        out = self.json.is_running
        return out

    @property
    def is_stopped(self) -> bool:
        path = self.json.path
        out = path is not None and (not path.exists() or not self.is_running)
        return out

    def headers(self) -> dict[str, str]:
        out = {}
        if self.json.token.value:
            out['Authorization'] = f'token {self.json.token.value}'
        return out

    @property
    def endpoint(self) -> str:
        parts = urlsplit(self.json.url)
        if parts.scheme == 'https':
            scheme = 'wss'
        else:
            scheme = 'ws'
        out = urlunsplit((scheme, parts.netloc, parts.path + 'api/events/subscribe', '', ''))
        return out

    def locate(self, document_path: str) -> Path | None:
        path = PurePosixPath(document_path)
        if (
            path.is_absolute()
            or '..' in path.parts
            or '\\' in document_path
            or ':' in document_path
            or '\x00' in document_path
        ):
            return None
        out = (self.json.root / Path(*path.parts)).resolve()
        return out

    def renamed(self, document_path: str) -> str:
        seen = set()
        while document_path not in seen:
            seen.add(document_path)
            path = PurePosixPath(document_path)
            for previous in sorted(self.renames, key=len, reverse=True):
                old = PurePosixPath(previous)
                if path.is_relative_to(old):
                    path = PurePosixPath(self.renames[previous]) / path.relative_to(old)
                    document_path = str(path)
                    break
            else:
                break
        return document_path

    def fail(
            self,
            state: State,
            reason: str = '',
    ) -> None:
        if state == self.state and reason == self.last_error:
            return
        self.state = state
        self.last_error = reason
        self.servers.session._parent.notice(f'jupyter: #{self.number} {state} {reason}'.rstrip())

    def close(
            self,
            state: State = 'disconnected',
            reason: str = '',
    ) -> None:
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        if (
            self.task is not None
            and not self.task.done()
            and not self.task.get_loop().is_closed()
        ):
            self.task.cancel()
        self.fail(state, reason)

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield 'number', self.number
        yield 'state', self.state
        yield 'url', self.json.url
        yield 'root', self.json.root
        yield 'counts', self.counts
        if self.last_error:
            yield 'last_error', self.last_error
        if self.schemas:
            yield 'schemas', self.schemas

    def __repr__(self) -> str:
        out = fmt.items(type(self).__name__, [self])
        return out


class Servers(Mapping[str, Server]):
    def __init__(self, session: JupyterSession) -> None:
        self.session = session
        self.data: dict[str, Server] = {}
        self.number = 0

    def __getitem__(self, url: str) -> Server:
        out = self.data[normalize_url(url)]
        return out

    def __iter__(self) -> Iterator[str]:
        out = iter(self.data)
        return out

    def __len__(self) -> int:
        out = len(self.data)
        return out

    def refresh(self) -> None:
        from trail.jupyter.subscription import subscribe

        def freshness(info: JSON) -> tuple[bool, float]:
            out = (info.is_running, info.written)
            return out

        jupyter = self.session._parent
        jupyter.jsons.refresh()
        found = {
            info.url: info
            for info in sorted(jupyter.jsons.values(), key=freshness)
        }
        found.update(jupyter.manual)
        for url, server in self.data.items():
            if url not in found:
                server.close('stopped')
        for url, info in found.items():
            server = self.data.get(url)
            changed = server is not None and (
                server.json != info
                or server.json.token.value != info.token.value
            )
            if server is None or changed:
                if server is not None:
                    server.close('stopped')
                self.number += 1
                server = Server(servers=self, number=self.number, json=info)
                self.data[url] = server
            if url in jupyter.disconnected:
                server.close()
            elif server.is_stopped:
                server.close('stopped')
            elif info.sock:
                server.fail('available', 'Unix sockets are not supported')
            elif info.password and not info.token.value:
                server.fail('available', 'A token is required for password-protected servers')
            elif url not in jupyter.manual and not jupyter.overlaps(info.root):
                server.close('available', 'outside this project')
            elif self.session.is_active and server.state in ('available', 'stopped'):
                server.fail('connecting')
                coroutine = subscribe(server)
                server.task = asyncio.create_task(coroutine, name=f'jupyter-{server.number}')

    def __repr__(self) -> str:
        out = fmt.items(type(self).__name__, self.values())
        return out
