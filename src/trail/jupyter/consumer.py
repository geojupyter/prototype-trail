from __future__ import annotations

import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from trail.jupyter.event import ContentsServiceV1Event, JupyterEvent
from trail.jupyter.message import Message
from trail.node import Node

if TYPE_CHECKING:
    from trail.asset import Asset
    from trail.jupyter.server import Server
    from trail.jupyter.session import JupyterSession

PREFIX = 'https://schema.jupytergis.org/events/'


@dataclass(frozen=True, slots=True, kw_only=True)
class Map:
    asset: Asset | None = None
    path: Path | None = None
    reason: str | None = None

    @property
    def src_path(self) -> str:
        if self.asset is not None:
            out = str(self.asset.path)
        elif self.path is not None:
            out = str(self.path)
        else:
            out = ''
        return out


class Consumer(Node):
    _parent: JupyterSession
    delivery_limit = 10000

    def __init__(self, parent: JupyterSession) -> None:
        super().__init__(parent)
        self.number = 0
        self.subjects: dict[tuple[Server, str], Asset] = {}
        self.deliveries: OrderedDict[tuple[str, str, int], None] = OrderedDict()

    def receive(
            self,
            server: Server,
            text: str,
            *,
            at: float | None = None,
    ) -> Message | None:
        server.counts.received += 1
        self.number += 1
        try:
            payload = json.loads(text)
            if not isinstance(payload, dict):
                raise TypeError
            schema_id = payload.pop('schema_id')
            if not isinstance(schema_id, str):
                raise TypeError
        except (ValueError, TypeError, KeyError):
            server.counts.skipped['invalid message'] += 1
            return None
        is_gis = schema_id.startswith(PREFIX)
        if not is_gis and schema_id != ContentsServiceV1Event.schema:
            server.counts.other[schema_id] += 1
            return None
        if at is None:
            timestamp = datetime.now(UTC)
        else:
            timestamp = datetime.fromtimestamp(at, UTC)
        record = Message(
            number=self.number,
            timestamp=timestamp,
            server=server.json.url,
            schema_id=schema_id,
            data=payload,
        )
        if is_gis:
            server.schemas[schema_id] = schema_id.rsplit('/', 1)[-1]
        try:
            if is_gis:
                self.route(server, record)
            else:
                self.observe(server, record)
        except (ValueError, TypeError, KeyError, AttributeError):
            record.outcome = 'skipped'
            record.reason = 'invalid payload'
        if record.outcome == 'skipped':
            server.counts.skipped[record.reason] += 1
        if record.reason in ('outside project', 'outside root', 'unsaved', 'untracked contents'):
            return None
        self._parent.messages.append(record)
        self._parent._parent.message(record)
        return record

    def route(
            self,
            server: Server,
            record: Message,
    ) -> None:
        data = record.data
        if not isinstance(data.get('verb'), str) or not isinstance(data.get('object'), dict):
            raise TypeError('Expected a verb and object')
        obj = data['object']
        document_path = obj['document_path']
        if document_path is not None and not isinstance(document_path, str):
            raise ValueError('Expected a document path or null')
        if 'subject' in data and not isinstance(data['subject'], str):
            raise ValueError('Expected a subject string')
        found = self.resolve(server, data)
        record.src_path = found.src_path
        if found.reason is not None:
            record.outcome = 'skipped'
            record.reason = found.reason
            return
        # trail: delivery - only stream identity identifies a retried delivery across views.
        stream = data.get('stream')
        seq = data.get('seq')
        delivery = None
        if isinstance(stream, str) and stream and type(seq) is int and seq >= 0:
            delivery = (server.json.url, stream, seq)
            if delivery in self.deliveries:
                record.outcome = 'skipped'
                record.reason = 'duplicate delivery'
                return
        event_cls = JupyterEvent.registry.get(record.schema_id, JupyterEvent)
        event = event_cls(
            schema_id=record.schema_id,
            data=data,
            server=record.server,
            src_path=found.src_path,
            timestamp=record.timestamp,
        )
        event.entry = found.asset
        # trail: focus - activation and deactivation are recorded without save confirmation.
        # trail-todo: delivery - add burst and save handling for non-focus events separately.
        event.apply(self._trail)
        record.event = event.id
        record.outcome = 'recorded'
        server.counts.recorded += 1
        if delivery is not None:
            self.deliveries[delivery] = None
            while len(self.deliveries) > self.delivery_limit:
                self.deliveries.popitem(last=False)

    def resolve(
            self,
            server: Server,
            data: dict[str, Any],
    ) -> Map:
        trail = self._trail
        document_path = data['object']['document_path']
        # trail: focus - an unsaved map has no project association, even on a home-root server.
        if not document_path or PurePosixPath(document_path).name == 'unsaved_project':
            out = Map(reason='unsaved')
            return out
        path = server.locate(document_path)
        if path is None:
            out = Map(reason='outside root')
            return out
        subject = data.get('subject')
        key = (server, subject)
        asset = self.subjects.get(key)
        # trail-todo: document-identity - replace subject links when room IDs are available.
        if asset is not None and trail.assets.id2entry.get(asset.id) is not asset:
            out = Map(path=path, reason='offtrailed')
            return out
        if asset is not None:
            path = asset.path
        else:
            if not path.exists():
                renamed = server.renamed(document_path)
                path = server.locate(renamed)
                if path is None:
                    out = Map(reason='outside root')
                    return out
            asset = trail.assets.get(path)
        if (
            path in trail.entries.untracked
            or trail.entries.is_metadata(path)
            or (asset is None and trail.offtrail.ignores(path))
        ):
            out = Map(path=path, reason='offtrailed')
            return out
        if asset is None and not self._parent._parent.is_inside(path.parent):
            out = Map(path=path, reason='outside project')
            return out
        if not path.is_file():
            out = Map(path=path, reason='file missing')
            return out
        if asset is None:
            trail.assets.scan(path)
            asset = trail.assets.get(path)
        if asset is not None and subject:
            self.subjects[key] = asset
        out = Map(asset=asset, path=path)
        return out

    def observe(
            self,
            server: Server,
            record: Message,
    ) -> None:
        data = record.data
        action = data.get('action')
        document_path = data.get('path')
        if not isinstance(document_path, str):
            raise TypeError('Expected a contents path')
        path = server.locate(document_path)
        if path is None:
            record.outcome = 'skipped'
            record.reason = 'outside root'
            return
        if action == 'rename':
            previous = data.get('source_path')
            if not isinstance(previous, str) or server.locate(previous) is None:
                raise ValueError('Expected a rename source path')
            server.renames[previous] = document_path
            record.outcome = 'used'
        else:
            record.outcome = 'skipped'
            record.reason = 'untracked contents'
        asset = self._trail.assets.get(path)
        if asset is None:
            if record.outcome == 'used' and self._parent._parent.is_inside(path.parent):
                record.src_path = str(path)
                return
            record.reason = 'untracked contents'
            return
        record.src_path = str(path)
        if action == 'save':
            event = ContentsServiceV1Event(
                schema_id=record.schema_id,
                data=data,
                server=record.server,
                src_path=record.src_path,
                timestamp=record.timestamp,
            )
            event.entry = asset
            event.apply(self._trail)
            record.event = event.id
            record.outcome = 'recorded'
            record.reason = ''
            server.counts.recorded += 1
