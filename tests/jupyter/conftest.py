from __future__ import annotations

import json
from pathlib import Path

import pytest

from trail import Trail
from trail.jupyter.event import DocumentV1Event
from trail.jupyter.server import JSON, Secret, Server
from trail.session import WatchdogSession


def document(
        verb: str = 'opened',
        path: str | None = 'map.jGIS',
        subject: str = '42',
        **extra: object,
) -> str:
    payload = dict(
        schema_id=DocumentV1Event.schema,
        subject=subject,
        verb=verb,
        object={'document_path': path},
        **extra,
    )
    out = json.dumps(payload)
    return out


@pytest.fixture
def trail(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> Trail:
    monkeypatch.setattr(WatchdogSession, 'reconcile', lambda self: None)
    (tmp_path / '.ontrail').write_text('./\n*.jgis\n', encoding='utf-8')
    (tmp_path / 'map.jGIS').write_text('{}', encoding='utf-8')
    out = Trail(tmp_path)
    return out


@pytest.fixture
def server(trail: Trail) -> Server:
    info = JSON(
        url='http://localhost:8888/',
        root=trail.dir.parent,
        token=Secret('test-token'),
    )
    out = Server(servers=trail.jupyter.session.servers, number=1, json=info)
    return out
