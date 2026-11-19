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

# state of a server connection
State = Literal[
    # found, not connected
    'available',
    # opening the feed
    'connecting',
    # feed open
    'subscribed',
    # feed dropped, trying again
    'retrying',
    # token, redirect or certificate refused
    'refused',
    # server has no event feed
    'unsupported',
    # the feed task crashed
    'error',
    # stopped by disconnect
    'disconnected',
    # runtime file or process gone
    'stopped',
]


def normalize_url(url: str) -> str:
    """
    The url in the form that keys a server. Raises ValueError unless it is a plain http or https
    url.
    """
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
    """A token that hides itself in reprs and cannot be pickled."""

    __slots__ = ('value',)

    def __init__(self, value: str) -> None:
        self.value = value

    def __repr__(self) -> str:
        return '<secret>'

    def __reduce__(self) -> None:
        raise TypeError('Tokens cannot be pickled')


@dataclass(frozen=True, slots=True, kw_only=True)
class JSON:
    """A Jupyter runtime file, or the same details given for a server with none."""

    # where the server listens
    url: str
    # folder the server serves
    root: Path
    # access token, hidden from reprs
    token: Secret = field(repr=False, compare=False)
    # the runtime file, or None
    path: Path | None = None
    # server process id
    pid: int | None = None
    # file modification time
    written: float = 0.0
    # unix socket path, or empty
    sock: str = ''
    # whether a password is required
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
        """Whether the server process is alive and wrote the file."""
        if self.path is None:
            return True
        if (
            self.pid is None
            or self.pid <= 0
        ):
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
    """The runtime files in Jupyter's runtime folder, by path."""

    def __init__(self, directory: Path | None = None) -> None:
        if directory is None:
            directory = Path(jupyter_runtime_dir())
        # the runtime folder
        self.directory = directory
        # files found by the last refresh
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
        """
        Read the runtime folder again, skipping files that are unreadable or incomplete.
        """
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
    """Message counts for one server."""

    # messages received
    received: int = 0
    # events recorded
    recorded: int = 0
    # messages skipped, by reason
    skipped: Counter[str] = field(default_factory=Counter)
    # messages of other schemas, by schema id
    other: Counter[str] = field(default_factory=Counter)



# no: use Counter[Reason] for explicit reasons:
#     class Reason(StrEnum):
#         INVALID_MESSAGE = 'invalid message'
#         INVALID_PAYLOAD = 'invalid payload'
#         UNSAVED = 'unsaved'
#         OUTSIDE_ROOT = 'outside root'
#         OUTSIDE_PROJECT = 'outside project'
#         OFFTRAILED = 'offtrailed'
#         FILE_MISSING = 'file missing'
#         UNTRACKED_CONTENTS = 'untracked contents'
#         DUPLICATE_DELIVERY = 'duplicate delivery'
#         CONSUMER_ERROR = 'consumer error'
#         KERNEL_CONNECTION = 'kernel connection'
#
#     skipped: Counter[Reason] = field(default_factory=Counter)


@dataclass(eq=False, kw_only=True, repr=False)
class Server:
    """Trail's handle on one Jupyter server during a run."""

    # the run's servers
    servers: Servers = field(repr=False)
    # number shown in notices
    number: int
    # runtime file details
    json: JSON
    # connection state
    state: State = 'available'
    # reason for the last state change
    last_error: str = ''
    # message counts
    counts: Counts = field(default_factory=Counts)
    # schema versions seen, by schema id
    schemas: dict[str, str] = field(default_factory=dict)
    # renamed paths, by old path
    renames: dict[str, str] = field(default_factory=dict)
    # task reading the feed
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    # open feed connection
    socket: WebSocketClientConnection | None = field(default=None, repr=False)
    # seconds before the first retry
    backoff: float = 0.5
    # longest wait between retries
    ceiling: float = 30.0

    @property
    def is_running(self) -> bool:
        """Whether the server process is alive."""
        out = self.json.is_running
        return out

    @property
    def is_stopped(self) -> bool:
        """Whether the runtime file or process is gone."""
        path = self.json.path
        out = (
            path is not None
            and (
                not path.exists()
                or not self.is_running
            )
        )
        return out

    def headers(self) -> dict[str, str]:
        """Request headers, including the token."""
        out = {}
        if self.json.token.value:
            out['Authorization'] = f'token {self.json.token.value}'
        return out

    @property
    def endpoint(self) -> str:
        """Websocket address of the event feed."""
        parts = urlsplit(self.json.url)
        if parts.scheme == 'https':
            scheme = 'wss'
        else:
            scheme = 'ws'
        out = urlunsplit((scheme, parts.netloc, parts.path + 'api/events/subscribe', '', ''))
        return out

    def locate(self, document_path: str) -> Path | None:
        """
        The local path of a file the server names, or None if the path is not allowed.
        """
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
        """Apply the renames the server reported to a path."""
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
        """Set the state and reason, and send a notice."""
        if (
            state == self.state
            and reason == self.last_error
        ):
            return
        self.state = state
        self.last_error = reason
        self.servers.session._parent.notice(f'jupyter: #{self.number} {state} {reason}'.rstrip())

    def close(
            self,
            state: State = 'disconnected',
            reason: str = '',
    ) -> None:
        """Close the feed and set the state."""
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
    """The servers of a run, by url."""

    def __init__(self, session: JupyterSession) -> None:
        # the run that holds the servers
        self.session = session
        # servers by url
        self.data: dict[str, Server] = {}
        # number of the latest server
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
        """
        Match the servers to the runtime files and added servers, and start feeds for those that
        should connect.
        """
        from trail.jupyter.subscription import subscribe

        def freshness(info: JSON) -> tuple[bool, float]:
            out = (info.is_running, info.written)
            return out

        jupyter = self.session._parent
        jupyter.jsons.refresh()
        # the last file for a url wins
        found = {
            info.url: info
            for info in sorted(jupyter.jsons.values(), key=freshness)
        }
        # added servers replace found ones
        found.update(jupyter.manual)
        for url, server in self.data.items():
            if url not in found:
                server.close('stopped')
        for url, info in found.items():
            server = self.data.get(url)
            # JSON does not compare the token
            changed = (
                server is not None
                and (
                    server.json != info
                    or server.json.token.value != info.token.value
                )
            )
            if (
                server is None
                or changed
            ):
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
            elif (
                info.password
                and not info.token.value
            ):
                server.fail('available', 'A token is required for password-protected servers')
            elif (
                url not in jupyter.manual
                and not jupyter.overlaps(info.root)
            ):
                server.close('available', 'outside this project')
            elif (
                self.session.is_active
                and server.state in ('available', 'stopped')
            ):
                server.fail('connecting')
                coroutine = subscribe(server)
                server.task = asyncio.create_task(coroutine, name=f'jupyter-{server.number}')

    def __repr__(self) -> str:
        out = fmt.items(type(self).__name__, self.values())
        return out
