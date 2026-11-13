from __future__ import annotations

import json
from pathlib import Path

import pytest

from trail import Trail
from trail.jupyter.event import DocumentV1Event, LayerV1Event, ModeV1Event, SourceV1Event
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


def layer(
        verb: str = 'added',
        path: str | None = 'map.jGIS',
        subject: str = '42',
        **extra: object,
) -> str:
    obj = {
        'document_path': path,
        'layer_id': extra.pop('layer_id', 'L1'),
    }
    for name in ('old', 'new', 'source'):
        if name in extra:
            obj[name] = extra.pop(name)
    payload = dict(
        schema_id=LayerV1Event.schema,
        subject=subject,
        verb=verb,
        object=obj,
        **extra,
    )
    out = json.dumps(payload)
    return out


def source(
        verb: str = 'updated',
        path: str | None = 'map.jGIS',
        subject: str = '42',
        **extra: object,
) -> str:
    obj = {
        'document_path': path,
        'source_id': extra.pop('source_id', 'S1'),
    }
    for name in ('old', 'new'):
        if name in extra:
            obj[name] = extra.pop(name)
    payload = dict(
        schema_id=SourceV1Event.schema,
        subject=subject,
        verb=verb,
        object=obj,
        **extra,
    )
    out = json.dumps(payload)
    return out


def mode(
        mode: str = 'identifying',
        previous: str = 'panning',
        path: str | None = 'map.jGIS',
        **extra: object,
) -> str:
    payload = dict(
        schema_id=ModeV1Event.schema,
        subject='42',
        verb='changed',
        object={
            'document_path': path,
            'mode': mode,
            'previous': previous,
        },
        **extra,
    )
    out = json.dumps(payload)
    return out


def reference(
        resolved_path: str | None = 'data/crossings.geojson',
        path: str | None = None,
        **extra: object,
) -> dict[str, object]:
    if path is None:
        path = resolved_path
    out = dict(
        id=extra.pop('id', 'S1'),
        type=extra.pop('type', 'GeoJSONSource'),
        path=path,
        resolved_path=resolved_path,
        **extra,
    )
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
