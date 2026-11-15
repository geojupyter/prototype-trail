# The event feed's Python code, from Trail's side

This note collects the Python code in ../eventfeed that matters for connecting Trail to the
JupyterGIS event feed. It describes the `prototyping` branch at 413d069b, the head of PR #4. Paths
are relative to ../eventfeed.

Most of the event feed is not Python. The code that decides when an event fires is a TypeScript
plugin that runs in the browser, `python/jupytergis_core/src/eventFeed/plugin.ts`. The Python side is
small. It holds the two event schemas, the code that registers them with Jupyter Server, their tests,
and a reference consumer. The table lists each file and what Trail takes from it.

| File | What it does | What Trail takes from it |
|---|---|---|
| `examples/event_feed_consumer/consumer.py` | listens to the event feed and prints JupyterGIS events | the listening loop Trail has to reproduce |
| `python/jupytergis_core/jupytergis_core/event_schemas/*.yaml` | defines the two event types | the fields Trail will receive |
| `python/jupytergis_core/jupytergis_core/events.py` | registers the schemas with Jupyter Server | the schema prefix, and when registration happens |
| `python/jupytergis_core/jupytergis_core/__init__.py` | calls the registration when the server extension loads | why the schemas exist before any listener connects |
| `python/jupytergis_core/jupytergis_core/tests/test_events.py` | emits and validates events without a server | sample payloads, and a way to test against the real schemas |

One file outside the PR also matters. JupyterGIS's Python API writes layers straight into the shared
document, and the browser plugin can report those writes as GUI events. The last section shows the
code.

## The reference consumer

`examples/event_feed_consumer/consumer.py` is the closest thing to what Trail needs. It connects to
Jupyter Server's event WebSocket, reads every message, keeps those whose `schema_id` starts with the
JupyterGIS prefix, and prints them. It is an inline-script file, so `uv run consumer.py` installs
`websockets` on the fly.

```python
# /// script
# dependencies = ["websockets"]
# ///

"""Minimal Reference consumer of the JupyterGIS GUI event feed.

Subscribes to the Jupyter Server event feed and prints JupyterGIS events.
"""

import asyncio
import json
import os

import websockets

# TODO: Import this from installed (dev?) version of JupyterGIS
# (`jupytergis_core.events:EVENT_SCHEMA_PREFIX`)
EVENT_SCHEMA_PREFIX = "https://schema.jupytergis.org/"


async def stream_events(*, base_url: str, token: str) -> None:
    uri = f"ws://{base_url}/api/events/subscribe?token={token}"
    async with websockets.connect(uri) as websocket:
        print(f"Subscribed to {uri}")
        async for message in websocket:
            event = json.loads(message)
            schema_id = event.get("schema_id", "")
            if not schema_id.startswith(EVENT_SCHEMA_PREFIX):
                continue

            print(json.dumps(event, indent=2))
            print()


if __name__ == "__main__":
    asyncio.run(
        stream_events(
            base_url=os.environ.get("JUPYTER_BASE_URL", "localhost:8888"),
            token=os.environ["JUPYTER_TOKEN"],
        ),
    )
```

The loop is the core of what Trail needs. Trail's console already runs on an asyncio event loop, so
a loop like this can run beside Trail's file watcher without a thread. The consumer leaves out the
rest of what Trail needs:

- It takes the host and token from environment variables. Trail would read them from Jupyter's
  runtime files instead.
- It connects once. When Jupyter Server restarts, the loop ends and the consumer exits. Trail has to
  reconnect.
- It sends the token in the URL and prints that URL at line 24, so the token lands in the terminal.
  Trail must never show the token. An `Authorization: token <TOKEN>` header works as well and keeps
  the token out of the URL.
- It builds `ws://` from `host:port` alone. A server behind a base URL, such as JupyterHub's
  `/user/<name>/`, or a server on https, needs the full URL from the runtime file and `wss://`.
- It prints raw json. Trail has to tie each event to a tracked file and store it in
  `.trail/events.jsonl`.

The TODO at lines 16-17 says the prefix should come from `jupytergis_core.events`. Trail should not
import it, since Trail does not depend on JupyterGIS and will often run in another environment. The
prefix is part of the event feed's public contract, so copying the string into Trail is safe.

## The two schemas

The schemas are what Trail will receive. Each has `subject`, `verb` and `object`. Jupyter Server
rejects any event that does not match its schema, so every message that reaches Trail has this
shape.

`python/jupytergis_core/jupytergis_core/event_schemas/document_v1.yaml`:

```yaml
"$id": "https://schema.jupytergis.org/events/document/v1"
version: "1"
title: "JupyterGIS document event"
description: "A JupyterGIS document was opened or closed (either as standalone document or in a Notebook widget) in the GUI."
type: "object"
required:
  - "verb"
  - "object"
properties:
  subject:
    title: "Subject"
    description: "The performer of `verb`. Client ID of acting user, if available."
    type: "string"
  verb:
    title: "Verb"
    description: "Document lifecycle action"
    enum:
      - "opened"
      - "closed"
  object:
    title: "Object"
    description: "The thing that `verb` was performed on."
    type: "object"
    required:
      - "document_path"
    properties:
      document_path:
        title: "Document path"
        description: "The path of the `.jGIS` document, or `null` if ephemeral."
        type: ["string", "null"]
```

`python/jupytergis_core/jupytergis_core/event_schemas/layer_v1.yaml`:

```yaml
"$id": "https://schema.jupytergis.org/events/layer/v1"
version: "1"
title: "JupyterGIS layer event"
description: "A JupyterGIS layer was created, deleted, or updated in the GUI."
type: "object"
required:
  - "verb"
  - "object"
properties:
  subject:
    title: "Subject"
    description: "The performer of `verb`. Client ID of acting user, if available."
    type: "string"
  verb:
    title: "Verb"
    description: "Layer action"
    enum:
      - "added"
      - "updated"
      - "removed"
  object:
    title: "Object"
    description: "The thing that `verb` was performed on."
    type: "object"
    required:
      - "document_path"
      - "layer_id"
    properties:
      document_path:
        title: "Document path"
        description: "The path of the `.jGIS` document, or `null` if ephemeral."
        type: ["string", "null"]
      layer_id:
        title: "Layer ID"
        description: "Unique ID of the layer."
        type: "string"
      old:
        title: "Old value"
        description: "The layer object as it was before `verb`. Absent for 'added' `verb`."
        type: "object"
        # TODO: The actual shape of a layer object! How to "import" that knowledge from packages/schemas?
        # properties:
      new:
        title: "New value"
        description: "The layer object as it is after `verb`. Absent for 'deleted' `verb`."
        type: "object"
        # TODO: The actual shape of a layer object! How to "import" that knowledge from packages/schemas?
        # properties:
```

These points in the schemas matter for Trail:

- `subject` is optional. The browser plugin always sends it, as the client ID of the browser's copy
  of the map. It names one open copy of a map, not a person, although the description says "acting
  user".
- `document_path` may be null by the schema. The browser plugin never sends null in practice. A map
  made in Python without a file name gets a path ending in `unsaved_project` instead.
- `old` and `new` are only typed as objects. The TODOs at lines 41 and 47 say the layer's shape is
  not defined yet. Trail should treat the layer as opaque json and read only the keys it needs, such
  as `name`, `type` and `parameters.source`.
- Line 45 says `new` is absent for the "deleted" verb, but the verb is `removed`.
- `version: "1"` is quoted on purpose. The jupyter_events library requires a string, and yaml would
  read an unquoted 1 as a number.

## The registration

`python/jupytergis_core/jupytergis_core/events.py` registers every yaml file in `event_schemas/` with
Jupyter Server's event logger.

```python
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jupyter_server.serverapp import ServerApp

EVENT_SCHEMA_PREFIX = "https://schema.jupytergis.org/"
EVENT_SCHEMAS_DIR = Path(__file__).parent / "event_schemas"
EVENT_SCHEMAS = tuple(EVENT_SCHEMAS_DIR.glob("*.yaml"))


def register_event_schemas(*, server_app: ServerApp) -> None:
    """Register all JupyterGIS event schemas with the server's EventLogger."""
    event_logger = server_app.event_logger
    if event_logger is None:
        server_app.log.warning(
            "Server app has no event logger; JupyterGIS event schemas were not registered.",
        )
        return

    for schema_path in sorted(EVENT_SCHEMAS):
        event_logger.register_event_schema(schema_path)
```

`python/jupytergis_core/jupytergis_core/__init__.py` calls it when Jupyter Server loads the
`jupytergis_core` server extension, at lines 33 and 37.

```python
def _load_jupyter_server_extension(server_app):
    """Registers the API handler to receive HTTP requests from the frontend extension.

    Parameters
    ----------
    server_app: jupyterlab.labapp.LabApp
        JupyterLab application instance

    """
    from .events import register_event_schemas
    from .handler import setup_handlers

    setup_handlers(server_app.web_app)
    register_event_schemas(server_app=server_app)
    name = "jupytergis_core"
    print(f"Registered {name} server extension")
```

The extension is turned on by `python/jupytergis_core/jupyter-config/server-config/jupytergis_core.json`,
which the package installs into Jupyter's config directory. So the schemas are registered whenever
Jupyter Server starts with this JupyterGIS installed.

The timing matters for Trail. A WebSocket listener receives only the schemas that were registered
when it connected. Registration happens at server start, before any listener can connect, so Trail
always receives JupyterGIS events. If the extension did not load, the browser plugin's POSTs fail
with a 400 that only shows in the browser console, and Trail receives nothing. Trail can detect
this by posting a deliberately incomplete test event while it is connected, as
`notes/10-20-2-jupytergis-event-feed.md` describes.

## The tests

`python/jupytergis_core/jupytergis_core/tests/test_events.py` validates events against the real
schemas without starting Jupyter Server. It builds a bare jupyter_events `EventLogger`, registers the
schemas through a stand-in for the server app, and emits events into a log handler.

```python
def _fake_server_app(*, event_logger: EventLogger) -> ServerApp:
    return cast("ServerApp", SimpleNamespace(event_logger=event_logger))


def _make_logger() -> tuple[EventLogger, io.StringIO]:
    sink = io.StringIO()
    logger = EventLogger(handlers=[logging.StreamHandler(sink)])
    register_event_schemas(server_app=_fake_server_app(event_logger=logger))
    return logger, sink
```

The cases at lines 43-74 are realistic payloads. Trail's tests can feed them to Trail's ingestion as
if they came from the socket.

```python
EVENT_SCHEMA_CASES: tuple[EventSchemaCase, ...] = (
    EventSchemaCase(
        id=DOCUMENT_SCHEMA_ID,
        event={
            "subject": "42",
            "verb": "opened",
            "object": {
                "document_path": "example.jgis",
            },
        },
    ),
    EventSchemaCase(
        id=LAYER_SCHEMA_ID,
        event={
            "subject": "42",
            "verb": "added",
            "object": {
                "document_path": "earthquakes.jgis",
                "layer_id": "layer-1",
                "new": {
                    "type": "VectorLayer",
                    "name": "earthquakes",
                    "visible": True,
                    "parameters": {
                        "source": "source-1",
                        "opacity": 1,
                    },
                },
            },
        },
    ),
)
```

A socket message is one of these events with `schema_id` added at the top level, for example
`{"schema_id": "https://schema.jupytergis.org/events/document/v1", "subject": "42", "verb": "opened",
"object": {"document_path": "example.jgis"}}`.

The `EventLogger` pattern only checks that events match the schemas. It does not exercise the
WebSocket, so it cannot test Trail's connection. That needs a real Jupyter Server, or a small fake
WebSocket server that sends the same messages.

## The Python API, which sends no events

The Python API in `python/jupytergis_lab/jupytergis_lab/notebook/gis_document.py` is upstream code,
not part of the PR. It sends no events, by design. events.md says changes made through the Python
API are notebook events, not JupyterGIS events. `GISDocument` still matters to Trail, because it
writes layers straight into the shared Yjs document that the browser also holds.

```python
        self._layers: Map = Map()
        self.ydoc["layers"] = self._layers
```

```python
    def _add_layer(
        self,
        new_object,
        id: str | None = None,
        zoom_to: bool = False,
    ) -> str:
        self._ensure_qgis_supported(new_object.type)
        _id = str(uuid4()) if id is None else id
        obj_dict = json.loads(new_object.json())
        self._ensure_symbology_qgis_supported(
            obj_dict.get("parameters", {}).get("symbologyState"),
        )
        self._layers[_id] = obj_dict
        self._layerTree.append(_id)
```

Every `add_*_layer` method ends in `_add_layer` (line 1312), which sets the layer in the shared `layers`
map at line 1324. `remove_layer` deletes it at line 1288, and `apply_symbology` rewrites it at
line 1404. These would look like `added`, `removed` and `updated` to the browser plugin. The browser's copy of a map shown in a
notebook syncs with this document. The browser plugin reacts to every change of that map, local or
remote. So from reading the code, a `doc.add_layer(...)` call in a notebook would likely reach Trail
as a layer `added` event with the browser's subject, as if the user had clicked. Nobody has observed
this yet. Until JupyterGIS emits only local changes, Trail cannot tell these events from GUI events.
