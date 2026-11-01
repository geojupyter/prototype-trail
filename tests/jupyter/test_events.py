from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import pytest
import yaml

from trail import Trail
from trail.cli.command import Query
from trail.cli.render import Renderer
from trail.event import Event
from trail.jupyter.event import (
    ContentsServiceV1Event,
    DocumentV1Event,
    JupyterEvent,
    LayerEvent,
    LayerV1Event,
    ViewportV1Event,
)
from trail.jupyter.server import Server

from .conftest import document


@pytest.mark.parametrize('name', ['document', 'layer', 'viewport', 'contents_service'])
def test_schema_registry_and_roundtrip(
        name: str,
        trail: Trail,
        server: Server,
) -> None:
    path = Path(__file__).parent / 'schemas' / f'{name}_v1.yaml'
    schema = yaml.safe_load(path.read_text(encoding='utf-8'))
    if name == 'contents_service':
        payload = {
            'action': 'save',
            'path': 'map.jGIS',
        }
    else:
        obj = {'document_path': 'map.jGIS'}
        if name == 'document':
            verb = 'opened'
        elif name == 'layer':
            verb = 'updated'
            obj.update(layer_id='layer-1', old={'name': 'old'}, new={'name': 'new'})
        else:
            verb = 'moved'
            obj['new'] = {
                'coordinates': {
                    'x': 10,
                    'y': 20,
                },
                'zoom': 3.5,
                'extent': [1, 2, 3, 4],
            }
        payload = {
            'subject': '42',
            'verb': verb,
            'object': obj,
        }
    payload['future_field'] = {'nested': [1, 2, 3]}
    jsonschema.validate(payload, schema)
    capsule = dict(
        schema_id=schema['$id'],
        **payload,
    )
    text = json.dumps(capsule)
    message = trail.jupyter.session.consumer.receive(server, text, at=1000)
    assert message.outcome == 'recorded'
    event = trail.events[message.event]
    assert type(event) is JupyterEvent.registry[schema['$id']]
    assert event.data == payload
    assert event.timestamp.timestamp() == 1000
    reopened = Trail(trail.dir)
    rebuilt = reopened.events[event.id]
    assert type(rebuilt) is type(event)
    assert rebuilt.to_record() == event.to_record()
    assert rebuilt.entry is reopened.assets[event.entry.id]
    if name == 'layer':
        assert isinstance(event, LayerEvent)
        assert event.layer == 'new'
        assert event.layer_id == 'layer-1'
    elif name == 'viewport':
        assert event.coordinates == {
            'x': 10,
            'y': 20,
        }
        assert event.zoom == 3.5
        assert event.extent == [1, 2, 3, 4]


def test_null_entry_and_unknown_schema_survive_replay(
        trail: Trail,
        server: Server,
) -> None:
    path = trail.dir.parent / 'untracked.txt'
    path.write_text('{}', encoding='utf-8')
    data = json.loads(document(path=path.name))
    data['schema_id'] = 'https://schema.jupytergis.org/events/future/v7'
    message = trail.jupyter.session.consumer.receive(server, json.dumps(data))
    event = trail.events[message.event]
    assert type(event) is JupyterEvent
    assert event.entry is None
    assert event.src_path == str(path)
    reopened = Trail(trail.dir)
    assert reopened.events[event.id].to_record() == event.to_record()
    assert reopened.events[event.id].entry is None


def test_payload_is_copied_and_selectable(trail: Trail) -> None:
    payload = {
        'verb': 'updated',
        'object': {
            'document_path': 'maps/city.jGIS',
            'layer_id': 'L1',
            'new': {
                'visible': False,
                'parameters': {'circle-fill-color': '#dc8add', 'opacity': 0.35},
            },
        },
    }
    event = LayerV1Event(schema_id=LayerV1Event.schema, data=payload)
    event.apply(trail)
    payload['object']['new']['visible'] = True
    assert event.data['object']['new']['visible'] is False
    selection = trail.events.select(
        'cls=LayerEvent data.object.new.visible=false '
        'data.object.new.parameters.opacity<0.5 '
        'data.object.new.parameters.circle-fill-color=#dc8add'
    )
    assert list(selection) == [event]


def test_jupyter_record_rebuilds_the_asset_after_log_clear(
        trail: Trail,
        server: Server,
) -> None:
    asset = trail.assets[trail.dir.parent / 'map.jGIS']
    trail.events.clear()
    message = trail.jupyter.session.consumer.receive(server, document('activated'))
    reopened = Trail(trail.dir)
    event = reopened.events[message.event]
    assert event.entry.id == asset.id
    assert reopened.assets[asset.id] is event.entry


@pytest.mark.parametrize('verb', ['activated', 'deactivated'])
def test_focus_schema_metadata_and_repr(verb: str) -> None:
    payload = {
        'verb': verb,
        'subject': '42',
        'time': '2026-10-31T18:00:00.000Z',
        'frontend_id': '91303ad1-d309-44aa-9679-f660356dc881',
        'focus_seq': 2,
        'object': {
            'document_path': 'map.jGIS',
            'widget_id': 'map-widget',
            'reason': 'window_blurred',
        },
    }
    path = Path(__file__).parent / 'schemas' / 'document_v1.yaml'
    schema = yaml.safe_load(path.read_text(encoding='utf-8'))
    jsonschema.validate(payload, schema, format_checker=jsonschema.FormatChecker())
    event = DocumentV1Event(schema_id=DocumentV1Event.schema, data=payload)
    assert event.is_focus
    assert not event.is_shared
    assert event.focus_seq == 2
    assert event.widget_id == 'map-widget'
    assert event.reason == 'window_blurred'
    assert event.tool == 'JupyterGIS'
    assert dict(event._repr_items())['tool'] == 'JupyterGIS'
    assert Renderer.verb(event) == verb


def test_registered_names_cannot_be_replaced() -> None:
    with pytest.raises(TypeError, match='already an event class'):
        type('DocumentV1Event', (Event,), {'__module__': 'another.module'})
    with pytest.raises(TypeError, match='already registered'):
        type('ConflictingSchemaEvent', (JupyterEvent,), {'schema': DocumentV1Event.schema})
    Event.classes.pop('ConflictingSchemaEvent', None)
    assert JupyterEvent.registry[DocumentV1Event.schema] is DocumentV1Event

    @dataclass(kw_only=True, slots=True, repr=False)
    class SlotsTestEvent(Event):
        pass

    assert Event.classes['SlotsTestEvent'] is SlotsTestEvent
    Event.classes.pop('SlotsTestEvent')


def test_schema_copies_match_installed_eventfeed() -> None:
    core = pytest.importorskip('jupytergis_core')
    upstream = Path(core.__file__).parent / 'event_schemas'
    copies = Path(__file__).parent / 'schemas'
    for path in upstream.glob('*.yaml'):
        source = yaml.safe_load(path.read_text(encoding='utf-8'))
        copied = yaml.safe_load((copies / path.name).read_text(encoding='utf-8'))
        assert copied == source


def test_classes_are_registered_before_replay() -> None:
    assert Event.classes['DocumentV1Event'] is DocumentV1Event
    assert Event.classes['LayerV1Event'] is LayerV1Event
    assert Event.classes['ViewportV1Event'] is ViewportV1Event
    assert Event.classes['ContentsServiceV1Event'] is ContentsServiceV1Event


def test_query_preserves_payload_paths_and_resolves_entry_paths(
        trail: Trail,
        server: Server,
) -> None:
    message = trail.jupyter.session.consumer.receive(server, document())
    query = Query(trail.events, trail.dir.parent)
    assert query('data.object.document_path=map.jGIS').ids == [message.event]
    assert query('cls=JupyterEvent entry.path=map.jGIS').ids == [message.event]


def test_captured_server_feed_replays_in_trail(
        trail: Trail,
        server: Server,
) -> None:
    path = Path(__file__).parent / 'fixtures' / 'focus.jsonl'
    messages = path.read_text(encoding='utf-8').splitlines()
    for text in messages:
        message = trail.jupyter.session.consumer.receive(server, text)
        assert message.outcome == 'recorded'
        payload = json.loads(text)
        schema_id = payload.pop('schema_id')
        event = trail.events[message.event]
        assert event.schema_id == schema_id
        assert event.data == payload
    assert len(trail.events.select('cls=JupyterEvent')) == 10
    assert len(trail.events.select('cls=DocumentEvent is_focus=true')) == 5
    reopened = Trail(trail.dir)
    assert reopened.events.ids == trail.events.ids
