from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import pytest
import yaml
from prompt_toolkit.formatted_text import StyleAndTextTuples, fragment_list_to_text

from trail import Trail
from trail.cli.command import Query
from trail.cli.console import Console
from trail.cli.render import Renderer
from trail.event import Event
from trail.jupyter.event import (
    ContentsServiceV1Event,
    DocumentV1Event,
    JupyterEvent,
    LayerEvent,
    LayerV1Event,
    ModeV1Event,
    SourceV1Event,
    ViewportV1Event,
)
from trail.jupyter.server import Server
from trail.jupyter.source import Inline, Source

from .conftest import document, layer, mode, reference


@pytest.mark.parametrize(
    'name',
    ['document', 'layer', 'source', 'mode', 'viewport', 'contents_service'],
)
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
        elif name == 'source':
            verb = 'updated'
            obj.update(
                source_id='S1',
                old=reference('a.geojson'),
                new=reference('b.geojson'),
            )
        elif name == 'mode':
            verb = 'changed'
            obj.update(mode='identifying', previous='panning')
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
    consumer = trail.jupyter.session.consumer
    message = consumer.receive(server, text, at=1000)
    if name == 'contents_service':
        # a save joins a run, which is logged once the saves stop
        assert message.outcome == 'used'
        consumer.close_runs()
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


def test_consecutive_viewports_record_render_and_reopen(
        trail: Trail,
        server: Server,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    printed: list[StyleAndTextTuples] = []

    def show(rows: list[StyleAndTextTuples]) -> None:
        printed.extend(rows)

    console = Console(trail, trail.dir.parent)
    monkeypatch.setattr(console.feed, 'show', show)
    console.run.cursor = len(trail.events)
    snapshots = [
        {
            'coordinates': {
                'x': 0,
                'y': 0,
            },
            'zoom': 0,
            'extent': [-180, -90, 180, 90],
        },
        {
            'coordinates': {
                'x': 10,
                'y': 20,
            },
            'zoom': 4.25,
            'extent': [5, 15, 15, 25],
        },
    ]
    recorded: list[ViewportV1Event] = []
    first_rows: list[StyleAndTextTuples] = []
    for snapshot in snapshots:
        obj = {
            'document_path': 'map.jGIS',
            'new': snapshot,
        }
        payload = {
            'schema_id': ViewportV1Event.schema,
            'subject': '42',
            'verb': 'moved',
            'object': obj,
        }
        text = json.dumps(payload)
        message = trail.jupyter.session.consumer.receive(server, text)
        assert message.outcome == 'recorded'
        event = trail.events[message.event]
        assert isinstance(event, ViewportV1Event)
        assert event.data['object']['new'] == snapshot
        recorded.append(event)
        assert len(trail.events.select('cls=ViewportV1Event')) == len(recorded)
        assert console.run.drain()
        assert printed[:len(first_rows)] == first_rows
        if len(recorded) == 1:
            first_rows = list(printed)

    assert recorded[0].id != recorded[1].id
    assert not console.run.drain()
    output = '\n'.join(
        fragment_list_to_text(row)
        for row in printed
    )
    assert output.count('ViewportV1Event') == 2
    assert 'zoom: 0' in output
    assert 'zoom: 4.25' in output
    reopened = Trail(trail.dir)
    for event, snapshot in zip(recorded, snapshots, strict=True):
        rebuilt = reopened.events[event.id]
        assert rebuilt.to_record() == event.to_record()
        assert rebuilt.data['object']['new'] == snapshot


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
    assert Event.classes['SourceV1Event'] is SourceV1Event
    assert Event.classes['ModeV1Event'] is ModeV1Event


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


@pytest.mark.parametrize(('local', 'expected'), [(True, True), (False, False), (None, None)])
def test_local_is_shown_and_selectable(
        local: bool | None,
        expected: bool | None,
        trail: Trail,
        server: Server,
) -> None:
    extra = {}
    if local is not None:
        extra['local'] = local
    message = trail.jupyter.session.consumer.receive(server, layer(**extra))
    event = trail.events[message.event]
    assert event.is_local is expected
    rows = dict(event._repr_items())
    if expected is None:
        assert 'local' not in rows
    else:
        assert rows['local'] is expected
    selected = trail.events.select('is_local=false')
    assert (event in selected) is (expected is False)


def test_source_from_payload() -> None:
    path = Source.from_payload(reference('data/x.geojson', '../data/x.geojson'))
    assert path == Source(
        id='S1',
        type='GeoJSONSource',
        path='../data/x.geojson',
        resolved_path='data/x.geojson',
    )
    assert str(path) == 'GeoJSONSource ../data/x.geojson'
    url = Source.from_payload(
        {'id': 'S2', 'type': 'RasterSource', 'urls': ['https://tile.org/{z}/{x}/{y}.png']},
    )
    assert url.urls == ('https://tile.org/{z}/{x}/{y}.png',)
    assert str(url) == 'RasterSource https://tile.org/{z}/{x}/{y}.png'
    package = Source.from_payload(
        reference('roads.gpkg', type='GeoPackageVectorSource', tables=['roads', 'bridges']),
    )
    assert str(package) == 'GeoPackageVectorSource roads.gpkg (roads, bridges)'
    inline = Source.from_payload(
        {'id': 'S3', 'type': 'GeoJSONSource', 'inline': {'field': 'data', 'size': 48213}},
    )
    assert inline.inline == Inline(field='data', size=48213)
    assert str(inline) == 'GeoJSONSource inline data 47.1 KiB'
    for value in (None, 'S1', {'id': 'S1'}, {'id': 1, 'type': 'GeoJSONSource'}):
        assert Source.from_payload(value) is None
    malformed = Source.from_payload(
        {'id': 'S1', 'type': 'GeoJSONSource', 'path': 3, 'urls': 'x', 'inline': {'field': 'data'}},
    )
    assert malformed == Source(id='S1', type='GeoJSONSource')


def test_mode_labels_and_selection(
        trail: Trail,
        server: Server,
) -> None:
    consumer = trail.jupyter.session.consumer
    first = consumer.receive(server, mode(frontend_id='page-1', focus_seq=2))
    second = consumer.receive(server, mode('panning', 'identifying'))
    event = trail.events[first.event]
    assert isinstance(event, ModeV1Event)
    assert not event.is_shared
    rows = dict(event._repr_items())
    assert rows['verb'] == 'changed'
    assert rows['mode'] == 'Identify'
    assert rows['previous'] == 'none'
    assert rows['focus_seq'] == 2
    assert Renderer.verb(event) == 'changed'
    assert list(trail.events.select('mode=identifying')) == [event]
    assert trail.events.select('previous=identifying').ids == [second.event]


def test_baseline_history_replays(
        trail: Trail,
        server: Server,
) -> None:
    root = trail.dir.parent
    (root / 'maps').mkdir()
    (root / 'data').mkdir()
    for name in ('untitled.jGIS', 'untitled1.jGIS', 'maps/untitled.jGIS', 'maps/untitled1.jGIS', 'maps/city.jGIS'):
        (root / name).write_text('{}', encoding='utf-8')
    tracked = {}
    for name in ('crossings.geojson', 'crossings_2026.geojson'):
        path = root / 'data' / name
        path.write_text('{}', encoding='utf-8')
        [tracked[name]] = trail.assets.add({path: None})
    schemas = {}
    for path in (Path(__file__).parent / 'schemas').glob('*.yaml'):
        schema = yaml.safe_load(path.read_text(encoding='utf-8'))
        schemas[schema['$id']] = schema
    path = Path(__file__).parent / 'fixtures' / 'baseline.jsonl'
    consumer = trail.jupyter.session.consumer
    for text in path.read_text(encoding='utf-8').splitlines():
        payload = json.loads(text)
        schema_id = payload.pop('schema_id')
        jsonschema.validate(payload, schemas[schema_id], format_checker=jsonschema.FormatChecker())
        message = consumer.receive(server, text)
        assert message.outcome == 'recorded'
    assert len(trail.events.select('cls=DocumentEvent verb=created')) == 4
    assert len(trail.events.select('cls=SourceEvent verb=updated')) == 1
    crossings = tracked['crossings.geojson'].id
    renamed = tracked['crossings_2026.geojson'].id
    assert len(trail.events.select(f'cls=LayerEvent resource_id={crossings}')) == 4
    assert len(trail.events.select(f'cls=LayerEvent resource_id={renamed}')) == 2
    [update] = trail.events.select('cls=SourceEvent verb=updated')
    assert update.resource_id == renamed
    assert update.previous_resource_id == crossings
    remote = trail.events.select('cls=LayerEvent is_local=false')
    assert [event.verb for event in remote] == ['added', 'removed']
    assert all(event.source.inline is not None for event in remote)
    [page] = {
        event.frontend_id
        for event in trail.events.select('cls=ModeEvent mode=marking')
    }
    timeline = trail.jupyter.focus(server=server.json.url, frontend_id=page)
    assert [event.focus_seq for event in timeline] == list(range(1, 11))
    assert [
        event.mode
        for event in timeline
        if isinstance(event, ModeV1Event)
    ] == ['marking', 'panning']
    reopened = Trail(trail.dir)
    assert reopened.events.ids == trail.events.ids
    for event in trail.events.select('cls=JupyterEvent'):
        assert reopened.events[event.id].to_record() == event.to_record()


def captured_updates() -> list[LayerV1Event]:
    # grammar-panel edits made in the symbology dialog of examples/earthquakes.jGIS, each closed
    # with Ok: add mag > 5, add has felt and switch to any, remove both, Ok without a change
    path = Path(__file__).parent / 'fixtures' / 'symbology.jsonl'
    out = []
    for text in path.read_text(encoding='utf-8').splitlines():
        data = json.loads(text)
        schema_id = data.pop('schema_id')
        out.append(LayerV1Event(schema_id=schema_id, data=data))
    return out


def described(event: LayerEvent) -> list[tuple[str, object]]:
    out = [
        (name, value)
        for name, value in event._repr_items()
        if name.endswith(('added', 'removed', 'changed')) or name == 'symbology'
    ]
    return out


def test_filter_added_and_removed_are_described() -> None:
    added, _, removed, _ = captured_updates()
    assert described(added) == [('filter added', 'mag > 5')]
    assert described(removed) == [('filter removed', 'mag > 5'), ('filter removed', 'has felt')]
    assert added.has_filter_change


def test_combinator_change_is_described() -> None:
    _, combined, _, _ = captured_updates()
    assert described(combined) == [('filter added', 'has felt'), ('combinator changed', 'all -> any')]


def test_restyle_has_no_filter_description(trail: Trail) -> None:
    *_, unchanged = captured_updates()
    assert described(unchanged) == []
    assert not unchanged.has_filter_change
    data = json.loads(json.dumps(unchanged.data))
    rule = data['object']['new']['parameters']['symbologyState']['layers'][0]['rules'][0]
    rule['mappings'][0]['scale']['params']['value'] = 4
    restyled = LayerV1Event(schema_id=LayerV1Event.schema, data=data)
    assert described(restyled) == [('symbology', 'other changes')]
    for event in (*captured_updates(), restyled):
        event.apply(trail)
    assert len(trail.events.select('cls=LayerEvent has_filter_change=true')) == 3


def test_other_layer_fields_are_described() -> None:
    *_, unchanged = captured_updates()
    data = json.loads(json.dumps(unchanged.data))
    new = data['object']['new']
    new['name'] = 'Large earthquakes'
    new['visible'] = False
    new['filters'] = {'logicalOp': 'any', 'appliedFilters': []}
    new['parameters']['opacity'] = 0.4
    event = LayerV1Event(schema_id=LayerV1Event.schema, data=data)
    changes = [value for name, value in event._repr_items() if name == 'change']
    assert changes == [
        "name 'Earthquakes' → 'Large earthquakes'",
        'visible True → False',
        'filters changed',
        'opacity 1 → 0.4',
    ]
    # the symbology and the source are described by their own rows
    assert unchanged.changes == []


def test_rule_geometry_and_range_conditions_are_worded() -> None:
    *_, unchanged = captured_updates()
    data = json.loads(json.dumps(unchanged.data))
    grammar = data['object']['new']['parameters']['symbologyState']['layers'][0]
    grammar['when'] = [
        {'type': 'geometryType', 'value': 'Polygon'},
        {'type': 'between', 'field': 'mag', 'min': 3.0, 'max': 9},
    ]
    grammar['rules'][1]['when'] = [{'type': 'fieldEquals', 'field': 'type', 'value': 'volcano'}]
    event = LayerV1Event(schema_id=LayerV1Event.schema, data=data)
    assert described(event) == [
        ('geometry condition added', 'Polygon'),
        ('filter added', '3 <= mag <= 9'),
        ('rule condition added', 'type = volcano'),
    ]
