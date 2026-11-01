from __future__ import annotations

import asyncio
import json

import pytest
from tornado.httpserver import HTTPServer
from tornado.netutil import bind_sockets
from tornado.web import Application, HTTPError
from tornado.websocket import WebSocketHandler

from trail import Trail
from trail.jupyter.event import DocumentV1Event

from .conftest import document


@pytest.mark.parametrize('refused', [False, True])
def test_websocket_auth_delivery_reconnect_and_shutdown(
        trail: Trail,
        refused: bool,
) -> None:
    requests = []
    sockets = []

    class Feed(WebSocketHandler):
        def prepare(self) -> None:
            requests.append(self.request)
            expected = 'token test-token'
            if refused or self.request.headers.get('Authorization') != expected:
                raise HTTPError(403)

        def open(self) -> None:
            sockets.append(self)
            self.write_message(document('activated', frontend_id='page', focus_seq=len(sockets)))

    async def until(predicate) -> None:
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(0.01)

    async def run() -> None:
        application = Application([(r'/base/api/events/subscribe', Feed)])
        http = HTTPServer(application)
        listeners = bind_sockets(0, address='127.0.0.1')
        http.add_sockets(listeners)
        port = listeners[0].getsockname()[1]
        url = f'http://127.0.0.1:{port}/base/'
        jupyter = trail.jupyter
        jupyter.connect(url=url, token='test-token', root=trail.dir.parent)
        server = jupyter.session.servers[url]
        server.backoff = 0.01
        server.ceiling = 0.02
        try:
            async with jupyter.session:
                if refused:
                    await until(lambda: server.state == 'refused')
                    await asyncio.sleep(0.05)
                    assert len(requests) == 1
                    assert not server.counts.recorded
                else:
                    await until(lambda: server.counts.recorded == 1)
                    assert server.state == 'subscribed'
                    sockets[0].close()
                    await until(lambda: server.counts.recorded == 2)
                    assert len(requests) == 2
                    assert DocumentV1Event.schema in server.schemas
                    sockets[-1].write_message('invalid json')
                    text = document('deactivated', frontend_id='page', focus_seq=3)
                    sockets[-1].write_message(text)
                    await until(lambda: server.counts.recorded == 3)
                    assert server.counts.skipped['invalid message'] == 1
                    jupyter.disconnect(url)
                    await until(lambda: server.task.done())
                    assert server.state == 'disconnected'
            assert not jupyter.session.is_active
            assert server.socket is None
            assert trail.lock.fd is None
        finally:
            for socket in sockets:
                socket.close()
            http.stop()
            await http.close_all_connections()
        for request in requests:
            assert request.query == ''
            assert 'test-token' not in request.uri
        if not refused:
            reopened = Trail(trail.dir)
            assert len(reopened.events.select('cls=DocumentEvent')) == 3
            records = [
                json.loads(line)
                for line in reopened.events.jsonl.path.read_text().splitlines()
            ]
            assert 'test-token' not in json.dumps(records)

    asyncio.run(run())
