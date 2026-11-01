from __future__ import annotations

import json
from pathlib import Path

import pytest

from trail import Trail
from trail.event import AddAssetEvent, WatchdogEvent
from trail.jupyter.event import ContentsServiceV1Event, DocumentV1Event, LayerV1Event
from trail.jupyter.server import JSON, Secret, Server

from .conftest import document


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
