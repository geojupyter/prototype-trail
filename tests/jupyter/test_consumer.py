from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trail import Trail
from trail.cli.render import Renderer
from trail.event import AddAssetEvent, FileAccessEvent, WatchdogEvent
from trail.jupyter.event import (
    ContentsServiceV1Event,
    DocumentV1Event,
    LayerV1Event,
    ModeV1Event,
    SourceV1Event,
)
from trail.jupyter.server import JSON, Secret, Server

from .conftest import document, layer, mode, reference, source


def test_focus_records_immediately_and_orders_each_page(
        trail: Trail,
        server: Server,
) -> None:
    consumer = trail.jupyter.session.consumer
    for sequence, verb in [(3, 'activated'), (1, 'activated'), (2, 'deactivated')]:
        message = consumer.receive(
            server,
            document(verb, frontend_id='page-1', focus_seq=sequence),
        )
        assert message.outcome == 'recorded'
        assert isinstance(trail.events[message.event], DocumentV1Event)
    consumer.receive(server, document('activated', frontend_id='page-2', focus_seq=1))
    events = trail.jupyter.focus(server=server.json.url, frontend_id='page-1')
    assert [
        event.focus_seq
        for event in events
    ] == [1, 2, 3]
    reopened = Trail(trail.dir)
    replayed = reopened.jupyter.focus(server=server.json.url, frontend_id='page-1')
    assert replayed.ids == events.ids
    assert server.counts.recorded == 4


def test_only_stream_retries_are_deduplicated(
        trail: Trail,
        server: Server,
) -> None:
    consumer = trail.jupyter.session.consumer
    text = document('activated', stream='stream-a', seq=1)
    first = consumer.receive(server, text)
    duplicate = consumer.receive(server, text)
    text = document('activated', subject='99', stream='stream-b', seq=1)
    other_view = consumer.receive(server, text)
    assert first.outcome == other_view.outcome == 'recorded'
    assert duplicate.reason == 'duplicate delivery'
    legacy = document('activated')
    assert consumer.receive(server, legacy).outcome == 'recorded'
    assert consumer.receive(server, legacy).outcome == 'recorded'
    assert server.counts.recorded == 4


@pytest.mark.parametrize('path', [None, '', 'unsaved_project', 'maps/unsaved_project'])
def test_unsaved_focus_is_not_attributed(
        path: str | None,
        trail: Trail,
        server: Server,
) -> None:
    message = trail.jupyter.session.consumer.receive(server, document('activated', path))
    assert message is None
    assert server.counts.skipped['unsaved'] == 1
    assert not trail.jupyter.session.messages


@pytest.mark.parametrize('path', ['../map.jGIS', '/map.jGIS', 'drive:map.jGIS', 'a/../../map.jGIS'])
def test_paths_outside_server_root_are_rejected(
        path: str,
        trail: Trail,
        server: Server,
) -> None:
    assert trail.jupyter.session.consumer.receive(server, document(path=path)) is None
    assert server.counts.skipped['outside root'] == 1


def test_home_root_does_not_attribute_another_project(
        trail: Trail,
        server: Server,
        tmp_path: Path,
) -> None:
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'other.jGIS').write_text('{}', encoding='utf-8')
    server.json = JSON(url=server.json.url, root=home, token=Secret('token'))
    with pytest.warns(UserWarning, match='leaves 1 asset tracked'):
        trail.ontrail.remove('./')
    trail.ontrail.add('project/')
    assert trail.jupyter.session.consumer.receive(server, document(path='other.jGIS')) is None
    assert server.counts.skipped['outside project'] == 1
    assert not trail.jupyter.session.messages


def test_missing_and_offtrailed_maps_are_skipped(
        trail: Trail,
        server: Server,
) -> None:
    consumer = trail.jupyter.session.consumer
    missing = consumer.receive(server, document(path='missing.jGIS'))
    assert missing.reason == 'file missing'
    path = trail.dir.parent / 'map.jGIS'
    trail.offtrail(path)
    removed = consumer.receive(server, document())
    assert removed.reason == 'offtrailed'
    hidden = trail.dir.parent / '.hidden.jGIS'
    hidden.write_text('{}', encoding='utf-8')
    ignored = consumer.receive(server, document(path=hidden.name))
    assert ignored.reason == 'offtrailed'


def test_new_matching_map_is_registered_before_its_event(
        trail: Trail,
        server: Server,
) -> None:
    path = trail.dir.parent / 'new.jGIS'
    path.write_text('{}', encoding='utf-8')
    message = trail.jupyter.session.consumer.receive(server, document(path=path.name))
    assert isinstance(trail.events.select[-2], AddAssetEvent)
    event = trail.events[message.event]
    assert event.entry is trail.assets[path]
    assert event.src_path == str(path)


def test_subject_follows_a_renamed_asset_and_not_a_replacement(
        trail: Trail,
        server: Server,
) -> None:
    consumer = trail.jupyter.session.consumer
    path = trail.dir.parent / 'map.jGIS'
    first = consumer.receive(server, document())
    asset = trail.events[first.event].entry
    moved = path.with_name('renamed.jGIS')
    path.rename(moved)
    event = WatchdogEvent(src_path=str(path), dest_path=str(moved), event_type='moved')
    event.apply(trail)
    path.write_text('{}', encoding='utf-8')
    trail.assets.scan(path)
    focus = consumer.receive(server, document('deactivated'))
    assert trail.events[focus.event].entry is asset
    assert focus.src_path == str(moved)
    assert trail.assets[path] is not asset
    closed = consumer.receive(server, document('closed'))
    assert trail.events[closed.event].entry is asset


def test_contents_folder_rename_resolves_stale_paths(
        trail: Trail,
        server: Server,
) -> None:
    root = trail.dir.parent
    folder = root / 'after'
    folder.mkdir()
    (folder / 'map.jGIS').write_text('{}', encoding='utf-8')
    renamed = {
        'schema_id': ContentsServiceV1Event.schema,
        'action': 'rename',
        'source_path': 'before',
        'path': 'after',
    }
    consumer = trail.jupyter.session.consumer
    consumer.receive(server, json.dumps(renamed))
    result = consumer.receive(server, document(path='before/map.jGIS'))
    assert result.outcome == 'recorded'
    assert result.src_path == str(folder / 'map.jGIS')


def test_symlink_inside_server_can_resolve_into_project(
        trail: Trail,
        server: Server,
        tmp_path: Path,
) -> None:
    root = tmp_path / 'served'
    root.mkdir()
    (root / 'link.jGIS').symlink_to(tmp_path / 'map.jGIS')
    server.json = JSON(url=server.json.url, root=root, token=Secret('token'))
    result = trail.jupyter.session.consumer.receive(server, document(path='link.jGIS'))
    assert result.outcome == 'recorded'
    assert result.src_path == str(tmp_path / 'map.jGIS')


def test_unknown_and_bad_messages_do_not_end_the_feed(
        trail: Trail,
        server: Server,
) -> None:
    consumer = trail.jupyter.session.consumer
    for text in ('broken', '[]', '{}', '{"schema_id": null}'):
        assert consumer.receive(server, text) is None
    payload = {
        'schema_id': 'https://other.invalid/v1',
        'action': 'anything',
    }
    assert consumer.receive(server, json.dumps(payload)) is None
    assert server.counts.other[payload['schema_id']] == 1
    payload = {
        'schema_id': DocumentV1Event.schema,
        'verb': 'activated',
        'object': [],
    }
    invalid = consumer.receive(server, json.dumps(payload))
    assert invalid.reason == 'invalid payload'
    assert consumer.receive(server, document()).outcome == 'recorded'


def test_messages_are_bounded_and_keep_arrival_numbers(
        trail: Trail,
        server: Server,
) -> None:
    session = trail.jupyter.session
    session.messages.limit = 2
    for _ in range(4):
        session.consumer.receive(server, document())
    assert [
        message.number
        for message in session.messages
    ] == [3, 4]
    assert len(trail.events.select('cls=DocumentEvent')) == 4


def test_layer_payload_including_filters_is_preserved(
        trail: Trail,
        server: Server,
) -> None:
    payload = {
        'schema_id': LayerV1Event.schema,
        'subject': '42',
        'verb': 'updated',
        'object': {
            'document_path': 'map.jGIS',
            'layer_id': 'L1',
            'old': {'filters': {}},
            'new': {'filters': {'logicalOp': 'all', 'appliedFilters': [{
                'feature': 'speed',
                'value': 20,
            }]}},
        },
    }
    message = trail.jupyter.session.consumer.receive(server, json.dumps(payload))
    event = trail.events[message.event]
    assert event.data['object'] == payload['object']
    assert isinstance(event, LayerV1Event)


def test_layers_link_a_tracked_resource(
        trail: Trail,
        server: Server,
) -> None:
    root = trail.dir.parent
    (root / 'data').mkdir()
    data = root / 'data' / 'crossings.geojson'
    data.write_text('{}', encoding='utf-8')
    [asset] = trail.assets.add({data: None})
    (root / 'maps').mkdir()
    (root / 'maps' / 'city.jGIS').write_text('{}', encoding='utf-8')
    consumer = trail.jupyter.session.consumer
    shared = reference('data/crossings.geojson', '../data/crossings.geojson')
    ids = []
    for layer_id in ('L1', 'L2'):
        text = layer(path='maps/city.jGIS', layer_id=layer_id, local=True, source=shared)
        message = consumer.receive(server, text)
        assert message.outcome == 'recorded'
        ids.append(message.event)
    for event_id in ids:
        event = trail.events[event_id]
        assert event.entry.path == root / 'maps' / 'city.jGIS'
        assert event.resource_id == asset.id
        assert event.resource_path == str(data)
        rows = dict(event._repr_items())
        assert rows['source'] == 'GeoJSONSource ../data/crossings.geojson'
        assert rows['resource_id'] == asset.id
    assert trail.events.select(f'resource_id={asset.id}').ids == ids
    reopened = Trail(trail.dir)
    for event_id in ids:
        rebuilt = reopened.events[event_id]
        assert rebuilt.resource_id == asset.id
        assert rebuilt.to_record() == trail.events[event_id].to_record()


def test_source_update_links_old_and_new(
        trail: Trail,
        server: Server,
) -> None:
    root = trail.dir.parent
    old = root / 'old.geojson'
    new = root / 'new.geojson'
    for path in (old, new):
        path.write_text('{}', encoding='utf-8')
    [old_asset] = trail.assets.add({old: None})
    text = source(
        'updated',
        local=False,
        old=reference('old.geojson'),
        new=reference('new.geojson'),
    )
    message = trail.jupyter.session.consumer.receive(server, text)
    event = trail.events[message.event]
    assert isinstance(event, SourceV1Event)
    assert event.is_local is False
    assert event.resource_path == str(new)
    assert event.resource_id is None
    assert event.previous_resource_id == old_asset.id
    rows = dict(event._repr_items())
    assert rows['old'] == 'GeoJSONSource old.geojson'
    assert rows['new'] == 'GeoJSONSource new.geojson'
    assert rows['resource'] == 'untracked'
    assert rows['previous_resource_id'] == old_asset.id
    assert Renderer.verb(event) == 'updated'
    removed = trail.jupyter.session.consumer.receive(
        server,
        source('removed', old=reference('old.geojson')),
    )
    event = trail.events[removed.event]
    assert event.resource_id == old_asset.id
    assert event.previous_resource_path == ''


def test_source_follows_a_known_rename(
        trail: Trail,
        server: Server,
) -> None:
    root = trail.dir.parent
    moved = root / 'moved.geojson'
    moved.write_text('{}', encoding='utf-8')
    [asset] = trail.assets.add({moved: None})
    server.renames['before.geojson'] = 'moved.geojson'
    text = layer(source=reference('before.geojson'))
    message = trail.jupyter.session.consumer.receive(server, text)
    assert trail.events[message.event].resource_id == asset.id


@pytest.mark.parametrize(
    'value',
    [
        {'id': 'S1', 'type': 'RasterSource', 'urls': ['https://tile.org/{z}/{x}/{y}.png']},
        {'id': 'S1', 'type': 'GeoJSONSource', 'inline': {'field': 'data', 'size': 12}},
        reference('../outside.geojson'),
        reference('/etc/passwd'),
        {'id': 'S1', 'type': 'GeoJSONSource', 'path': 'x.geojson'},
        'not a reference',
        None,
    ],
)
def test_unlinked_references_are_still_recorded(
        value: object,
        trail: Trail,
        server: Server,
) -> None:
    message = trail.jupyter.session.consumer.receive(server, layer(source=value))
    assert message.outcome == 'recorded'
    event = trail.events[message.event]
    assert event.resource_path == ''
    assert event.resource_id is None
    rows = dict(event._repr_items())
    assert 'resource_path' not in rows


def test_untracked_file_outside_the_project_is_not_linked(
        tmp_path_factory: pytest.TempPathFactory,
        trail: Trail,
) -> None:
    root = tmp_path_factory.mktemp('server')
    (root / 'elsewhere').mkdir()
    (root / 'elsewhere' / 'x.geojson').write_text('{}', encoding='utf-8')
    project = trail.dir.parent
    found = trail.jupyter.session.consumer.resource(
        Server(
            servers=trail.jupyter.session.servers,
            number=2,
            json=JSON(url='http://localhost:9999/', root=root, token=Secret('')),
        ),
        LayerV1Event(
            schema_id=LayerV1Event.schema,
            data=json.loads(layer(source=reference('elsewhere/x.geojson'))),
        ).source,
    )
    assert found.path == ''
    assert not str(root).startswith(str(project))


def test_untracked_file_inside_the_project_is_shown_untracked(
        trail: Trail,
        server: Server,
) -> None:
    (trail.dir.parent / 'loose.geojson').write_text('{}', encoding='utf-8')
    message = trail.jupyter.session.consumer.receive(
        server,
        layer(source=reference('loose.geojson')),
    )
    event = trail.events[message.event]
    assert event.resource_path == str(trail.dir.parent / 'loose.geojson')
    assert dict(event._repr_items())['resource'] == 'untracked'
    assert 'loose.geojson' not in {
        asset.path.name
        for asset in trail.assets
    }


def test_created_map_is_recorded_as_created(
        trail: Trail,
        server: Server,
) -> None:
    consumer = trail.jupyter.session.consumer
    message = consumer.receive(server, document('created'))
    event = trail.events[message.event]
    assert isinstance(event, DocumentV1Event)
    assert Renderer.verb(event) == 'created'
    assert not event.is_focus
    assert trail.events.select('verb=created').ids == [event.id]
    consumer.receive(server, document('activated', frontend_id='page-1', focus_seq=1))
    focus = trail.jupyter.focus(server=server.json.url, frontend_id='page-1')
    assert event.id not in focus.ids


def test_created_map_is_discovered_by_the_preset(
        trail: Trail,
        server: Server,
) -> None:
    created = trail.dir.parent / 'untitled.jGIS'
    created.write_text('{}', encoding='utf-8')
    message = trail.jupyter.session.consumer.receive(server, document('created', 'untitled.jGIS'))
    assert message.outcome == 'recorded'
    asset = trail.assets[created]
    assert trail.events[message.event].entry is asset
    added = trail.events.select('cls=AddAssetEvent').select[-1]
    assert added.rule == '*.jgis'
    assert trail.events.ids.index(added.id) < trail.events.ids.index(message.event)


def test_mode_and_focus_share_page_order(
        trail: Trail,
        server: Server,
) -> None:
    consumer = trail.jupyter.session.consumer
    page = {'frontend_id': 'page-1'}
    arrivals = [
        mode('panning', 'identifying', focus_seq=4, **page),
        document('activated', focus_seq=1, **page),
        mode('identifying', 'panning', focus_seq=2, **page),
        document('deactivated', focus_seq=3, **page),
        mode('marking', 'panning', focus_seq=1, frontend_id='page-2'),
    ]
    for text in arrivals:
        assert consumer.receive(server, text).outcome == 'recorded'
    events = trail.jupyter.focus(server=server.json.url, frontend_id='page-1')
    assert [
        (type(event), event.focus_seq)
        for event in events
    ] == [
        (DocumentV1Event, 1),
        (ModeV1Event, 2),
        (DocumentV1Event, 3),
        (ModeV1Event, 4),
    ]
    reopened = Trail(trail.dir)
    replayed = reopened.jupyter.focus(server=server.json.url, frontend_id='page-1')
    assert replayed.ids == events.ids


def save(path: str = 'map.jGIS') -> str:
    payload = {
        'schema_id': ContentsServiceV1Event.schema,
        'action': 'save',
        'path': path,
    }
    out = json.dumps(payload)
    return out


def session_of(
        trail: Trail,
        event_type: str,
        started: float,
        ended: float,
) -> FileAccessEvent:
    path = trail.dir.parent / 'map.jGIS'
    event = FileAccessEvent(
        src_path=str(path),
        event_type=event_type,
        timestamp=datetime.fromtimestamp(ended, UTC),
        started=datetime.fromtimestamp(started, UTC),
    )
    event.entry = trail.assets[path]
    return event


def test_saves_of_one_file_are_one_run(
        trail: Trail,
        server: Server,
) -> None:
    consumer = trail.jupyter.session.consumer
    messages = [
        consumer.receive(server, save(), at=at)
        for at in (1000, 1001, 1020)
    ]
    assert {message.outcome for message in messages} == {'used'}
    assert len(trail.events.select('cls=ContentsServiceV1Event')) == 0
    late = consumer.receive(server, save(), at=1020 + consumer.run_gap + 1)
    [first] = trail.events.select('cls=ContentsServiceV1Event')
    assert first.saves == 3
    assert first.started.timestamp() == 1000
    assert first.timestamp.timestamp() == 1020
    assert {message.outcome for message in messages} == {'recorded'}
    assert {message.event for message in messages} == {first.id}
    assert late.outcome == 'used'
    consumer.close_runs()
    assert [event.saves for event in trail.events.select('cls=ContentsServiceV1Event')] == [3, 1]
    assert server.counts.recorded == 2
    rows = dict(first._repr_items())
    assert rows['verb'] == 'saved'
    assert rows['saves'] == 3
    reopened = Trail(trail.dir)
    assert reopened.events[first.id].to_record() == first.to_record()


def test_a_file_session_a_save_explains_joins_its_run(
        trail: Trail,
        server: Server,
) -> None:
    consumer = trail.jupyter.session.consumer
    consumer.receive(server, save(), at=1000)
    assert consumer.claim(session_of(trail, 'modified', 999.998, 1000.002))
    # a read back by the room a moment later belongs to the same save
    assert consumer.claim(session_of(trail, 'opened', 1000.1, 1000.1))
    # another program's write five seconds later stays its own record
    assert not consumer.claim(session_of(trail, 'modified', 1005, 1005.1))
    # a creation is never folded into a save
    assert not consumer.claim(session_of(trail, 'created', 999.9, 1000))
    consumer.close_runs()
    [run] = trail.events.select('cls=ContentsServiceV1Event')
    assert run.writes == 2


def test_the_watchdog_offers_file_sessions_to_save_runs(
        trail: Trail,
        server: Server,
) -> None:
    consumer = trail.jupyter.session.consumer
    trail.watchdog.observers.append(consumer.claim)
    consumer.receive(server, save(), at=1000)
    session = trail.watchdog.session
    session.prepare(asyncio.new_event_loop())
    path = str(trail.dir.parent / 'map.jGIS')
    for offset, event_type in [(0, 'opened'), (0.001, 'modified'), (0.002, 'closed')]:
        session.add_fold(path, event_type, 1000 + offset)
    session.add_fold(path, 'opened', 1050)
    session.add_fold(path, 'modified', 1050.1)
    session.end_folds()
    session.loop.close()
    consumer.close_runs()
    folded = trail.events.select('cls=FileAccessEvent')
    assert [event.timestamp.timestamp() for event in folded] == [pytest.approx(1050.1)]
    [run] = trail.events.select('cls=ContentsServiceV1Event')
    assert run.writes == 1


def test_a_run_of_an_untracked_file_is_dropped(
        trail: Trail,
        server: Server,
) -> None:
    consumer = trail.jupyter.session.consumer
    consumer.receive(server, save(), at=1000)
    trail.offtrail(trail.dir.parent / 'map.jGIS')
    consumer.close_runs()
    assert len(trail.events.select('cls=ContentsServiceV1Event')) == 0
