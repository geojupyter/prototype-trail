from __future__ import annotations

import asyncio
import ssl
from typing import TYPE_CHECKING

from tornado.httpclient import HTTPClientError, HTTPRequest
from tornado.websocket import websocket_connect

if TYPE_CHECKING:
    from trail.jupyter.server import Server


async def subscribe(server: Server) -> None:
    session = server.servers.session
    delay = server.backoff
    while session.is_active and not server.is_stopped:
        server.fail('connecting')
        socket = None
        try:
            request = HTTPRequest(
                server.endpoint,
                headers=server.headers(),
                follow_redirects=False,
                connect_timeout=10,
            )
            socket = await websocket_connect(
                request,
                ping_interval=20,
                ping_timeout=20,
                max_message_size=64 * 1024 * 1024,
            )
            server.socket = socket
            server.fail('subscribed')
            delay = server.backoff
            while True:
                text = await socket.read_message()
                if text is None:
                    break
                try:
                    session.consumer.receive(server, text)
                except Exception as error:  # noqa: BLE001
                    server.counts.skipped['consumer error'] += 1
                    # exception text can contain payloads or credentials from a callback.
                    session._parent.notice(f'jupyter: #{server.number} {type(error).__name__}')
        except asyncio.CancelledError:
            raise
        except HTTPClientError as error:
            if error.code in (401, 403):
                server.fail('refused', f'HTTP {error.code}')
                return
            if error.code == 404:
                server.fail('unsupported', 'HTTP 404')
                return
            if 300 <= error.code < 400:
                server.fail('refused', 'redirect refused')
                return
            server.fail('retrying', f'HTTP {error.code}')
        except ssl.SSLError:
            server.fail('refused', 'TLS verification failed')
            return
        except (OSError, TimeoutError):
            server.fail('retrying', 'connection failed')
        except Exception as error:  # noqa: BLE001
            server.fail('error', type(error).__name__)
            return
        finally:
            if socket is not None:
                socket.close()
            if server.socket is socket:
                server.socket = None
        if server.is_stopped:
            break
        server.fail('retrying', 'connection closed')
        await asyncio.sleep(delay)
        delay = min(delay * 2, server.ceiling)
    server.fail('stopped')
