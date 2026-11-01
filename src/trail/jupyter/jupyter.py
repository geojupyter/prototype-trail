from __future__ import annotations

import warnings
from collections.abc import Callable
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from trail.jupyter.event import DocumentEvent
from trail.jupyter.server import JSON, JSONs, Secret, normalize_url
from trail.jupyter.session import JupyterSession
from trail.node import Node

if TYPE_CHECKING:
    from trail.event import Events
    from trail.jupyter.message import Message


class Jupyter(Node):
    session = JupyterSession.descriptor()
    on_notice: Callable[[str], None] | None = None
    on_message: Callable[[Message], None] | None = None

    @cached_property
    def jsons(self) -> JSONs:
        out = JSONs()
        return out

    @cached_property
    def manual(self) -> dict[str, JSON]:
        out = {}
        return out

    @cached_property
    def disconnected(self) -> set[str]:
        out = set()
        return out

    def connect(
            self,
            *,
            url: str,
            token: str,
            root: str | Path,
    ) -> None:
        info = JSON(url=url, token=Secret(token), root=Path(root))
        self.manual[info.url] = info
        self.disconnected.discard(info.url)
        server = self.session.servers.data.get(info.url)
        if server is not None:
            server.close('available')
        self.session.servers.refresh()

    def disconnect(self, url: str) -> None:
        url = normalize_url(url)
        self.disconnected.add(url)
        server = self.session.servers.data.get(url)
        if server is not None:
            server.close()

    def overlaps(self, root: Path) -> bool:
        out = any(
            root == entry.path
            or entry.path.is_relative_to(root)
            or (entry.is_recursive and root.is_relative_to(entry.path))
            for entry in self._trail.dirs
        )
        if not out:
            out = any(
                asset.path.is_relative_to(root)
                for asset in self._trail.assets
            )
        return out

    def is_inside(self, folder: Path) -> bool:
        out = any(
            folder == entry.path
            or (entry.is_recursive and folder.is_relative_to(entry.path))
            for entry in self._trail.dirs
        )
        return out

    def focus(
            self,
            *,
            server: str,
            frontend_id: str,
    ) -> Events:
        url = normalize_url(server)
        events = [
            event
            for event in self._trail.events
            if isinstance(event, DocumentEvent)
            and event.is_focus
            and event.server == url
            and event.frontend_id == frontend_id
        ]

        def order(event: DocumentEvent) -> tuple[bool, int, object]:
            sequence = event.focus_seq
            if type(sequence) is int:
                out = (False, sequence, event.timestamp)
            else:
                out = (True, 0, event.timestamp)
            return out

        # trail: focus - page order can differ from arrival order across document streams.
        events.sort(key=order)
        keys = [
            event.id
            for event in events
        ]
        out = self._trail.events.select.subset(keys)
        return out

    def notice(self, text: str) -> None:
        if self.on_notice is not None:
            try:
                self.on_notice(text)
            except Exception:  # noqa: BLE001
                warnings.warn('Jupyter notice callback failed', stacklevel=2)

    def message(self, message: Message) -> None:
        if self.on_message is not None:
            try:
                self.on_message(message)
            except Exception:  # noqa: BLE001
                warnings.warn('Jupyter message callback failed', stacklevel=2)

    def __repr__(self) -> str:
        out = repr(self.session.servers)
        return out
