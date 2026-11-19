# Layer Added in Trail

You add a layer called `crossings` to a map. JupyterLab is the browser workspace that holds notebooks and maps. JupyterGIS is the map editor inside it. A layer is the drawable item on the map. Its source is the data the layer reads, here the file `data/crossings.geojson`. JupyterGIS reports the addition to Jupyter Server, and Jupyter Server relays it to Trail. Trail records one `LayerV1Event` on the map. If the project tracks the geojson file, the event links it too.

**Observed** Replaying the captured messages for this action through Trail's `Consumer` gives this event. The repr is wrapped, and `id`, `timestamp`, `schema_id` and `server` are left out.

```text
LayerV1Event(
    src_path=<project>/maps/city.jGIS, verb=added, local=True,
    layer_id=layer-crossings, layer=crossings,
    source=GeoJSONSource ../data/crossings.geojson,
    resource_path=<project>/data/crossings.geojson,
    resource_id=1e9fb00e90ae4b6b88ec42bbb5c649b0)
```

A statement with no label describes the code in the working trees. **Observed** marks something I ran or captured, **Deduction** a conclusion from the code that I did not run, and **Planned** work the code only mentions. The JupyterGIS code is the prototype in `../eventfeed` (branch `review`, HEAD `abecc972` plus staged changes). The Trail code is this repository's working tree. Paths are relative to the checkout they belong to.

## The path and what the feature changed

The feature is the `local` flag and the `source` data reference on layer messages, and Trail's use of them. The rest of the path was there before. This note shows the code the feature added or changed, and names the existing steps in a sentence.

| Step | Code | Status |
| --- | --- | --- |
| 1. You press **OK** in the layer dialog | `creationform.tsx`, `JupyterGISModel.addLayer`, `JupyterGISDoc.addLayer` | existing |
| 2. Yjs reports the new key | `JupyterGISDoc._layersObserver` | changed, 2 lines |
| 3. The browser builds the message | `wireLayerEvents`, `SourceReferences` | changed, and `sources.ts` is new |
| 4. Jupyter Server validates and relays it | the `layer/v1` schema | changed, 50 lines |
| 5. Trail reads the socket | `subscribe`, `Consumer.receive` | existing |
| 6. Trail picks the map and the event class | `Consumer.route`, `Consumer.resolve` | existing, plus one new call |
| 7. Trail links the data file | `Consumer.link`, `Consumer.resource`, `Source` | new |
| 8. The event describes the layer | `LayerEvent`, `JupyterEvent.is_local` | changed |
| 9. The log keeps it | `Events`, `JSONL.append`, `Event.to_record` | existing |

## Steps 1 and 2: the edit and the change report

The dialog adds the source first and the layer second. Both writes go into Yjs, the library that keeps every copy of a map in sync. The layer is stored whole under its ID in the Yjs map `_layers`.

```ts
// packages/base/src/shared/formbuilder/creationform.tsx
currentModel.sharedModel.addSource(currentSourceId, sourceModel);
...
currentModel.addLayer(UUID.uuid4(), layerModel);

// packages/schema/src/doc.ts
addLayer(id: string, value: IJGISLayer): void {
  this.transact(() => {
    this._layers.set(id, value);
  });
}
```

An observer on `_layers` reports each changed key with its old and new value. An added layer has no old value. The feature adds one field to what the observer reports: `local`, from Yjs's `Transaction.local`, which Yjs documents as "Whether this change originates from this doc". It is `true` when this copy of the map made the edit, and `false` when the change arrived from another copy.

```diff
 // packages/schema/src/doc.ts, JupyterGISDoc._layersObserver
       id: string;
       oldValue: IDict;
       newValue: IJGISLayer;
+      local: boolean;
     }> = [];
 ...
           id: key,
           oldValue: change.oldValue,
           newValue: raw === undefined ? undefined : JSONExt.deepCopy(raw),
+          local: event.transaction.local,
         });
```

`IJGISLayerDocChange` in `packages/schema/src/interfaces.ts` gains the matching `local?: boolean`.

## Step 3: the browser builds the message

`wireLayerEvents` listens to the signal that carries those changes. It was already choosing the verb, so no old value gives `added`. The feature adds three things: the `references` argument, the source lookup, and the `local` and `source` fields of the message.

```diff
 // python/jupytergis_core/src/eventFeed/plugin.ts
-const wireLayerEvents = ({
-  widget,
-  clientId,
-  emit,
-}: IEventWiringProps): void => {
-  const LAYER_SCHEMA = 'https://schema.jupytergis.org/events/layer/v1';
-
+export const wireLayerEvents = (
+  { widget, clientId, emit }: IEventWiringProps,
+  references: SourceReferences,
+): void => {
   widget.model.sharedLayersChanged.connect((_, change) => {
-    for (const { id, oldValue, newValue } of change.layerChange ?? []) {
+    for (const { id, oldValue, newValue, local } of change.layerChange ?? []) {
       const verb = !oldValue ? 'added' : !newValue ? 'removed' : 'updated';
+      ...
+      const oldSource = oldValue?.parameters?.source as string | undefined;
+      const newSource = newValue?.parameters?.source as string | undefined;
+      // trail: resources - an update names its source only when the layer switched sources.
+      let source;
+      if (verb === 'added') {
+        source = references.get(newSource);
+      } else if (verb === 'removed') {
+        source = references.get(oldSource);
+      } else if (oldSource !== newSource) {
+        source = references.get(newSource);
+      }

       emit({
         schema_id: LAYER_SCHEMA,
         version: '1',
         data: {
           subject: clientId,
           verb,
+          ...(local === undefined ? {} : { local }),
           object: {
             document_path: widget.model.filePath || null,
             layer_id: id,
             ...(oldValue ? { old: oldValue as unknown as JSONObject } : {}), // TODO: Better typing
             ...(newValue ? { new: newValue as unknown as JSONObject } : {}), // TODO: Better typing
+            ...(source ? { source: source as unknown as JSONObject } : {}),
           },
         },
       });
```

The `...` in the added lines stands for a block that skips an `updated` write whose old and new values are equal. It does not apply to an addition. `LAYER_SCHEMA` moved to module scope, so the source handler can share it. The `subject` is the Yjs client ID of this copy of the map.

The plugin builds one `SourceReferences` per map and passes it to the handlers. The handlers connect only after the map's first sync. Otherwise every layer already in the file would arrive as an addition, which the comment in the plugin says.

```diff
 // python/jupytergis_core/src/eventFeed/plugin.ts, eventFeedPlugin.activate
-      void widget.model.sharedModel.initialSyncReady.then(() => {
-        wireLayerEvents(eventWiringProps);
-        wireViewportEvents(eventWiringProps);
+      const model = widget.model;
+      void model.sharedModel.initialSyncReady.then(() => {
+        if (model.isDisposed) {
+          return;
+        }
+        const references = new SourceReferences(model);
+        references.seed();
+        wireLayerEvents(eventWiringProps, references);
+        wireSourceEvents(eventWiringProps, references);
+        wireViewportEvents(model, emit);
       });
```

## Step 3: the source reference

`SourceReferences` is new, in `python/jupytergis_core/src/eventFeed/sources.ts`. The layer and source handlers share it. It turns a source in the map into a description of the data it reads. `seed()` caches the references of the sources already in the map. `compute` and `remember` build and cache one reference. `get` is what `wireLayerEvents` calls.

```ts
/**
 * The last known reference of each source in one map. A layer removal can be observed after its
 * orphaned source is gone, so `get` falls back to the cached reference.
 */
// trail: resources - layer messages carry their data reference without waiting for a save.
export class SourceReferences {
  constructor(private readonly _model: IJupyterGISModel) {}
  ...
  /**
   * The source's current reference, or its last known one if it was removed.
   */
  get(id: string | undefined): ISourceReference | undefined {
    if (!id) {
      return undefined;
    }
    const live = this._model.sharedModel.getLayerSource(id);
    if (live) {
      return this.remember(id, live);
    }
    return this._cache.get(id);
  }
  ...
}
```

For an addition, `get` reads the source from the shared map. That works because the dialog wrote the source before the layer. The cache serves removals, where the source may already be gone. `sourceReference` builds the description:

```ts
/**
 * The reference that a source holds. `documentPath` is the map's path, used to resolve a
 * relative `path`.
 */
export function sourceReference(
  id: string,
  source: IJGISSource | Record<string, any> | undefined,
  documentPath: string,
): ISourceReference | undefined {
  if (!source || typeof source.type !== 'string') {
    return undefined;
  }
  const parameters: Record<string, any> = source.parameters ?? {};
  const out: ISourceReference = { id, type: source.type };
  const urls: string[] = [];
  const path = parameters.path;
  if (typeof path === 'string' && path) {
    if (isUrl(path)) {
      out.path = null;
      out.resolved_path = null;
      urls.push(path);
    } else {
      out.path = path;
      out.resolved_path = documentPath ? resolvePath(documentPath, path) : null;
    }
  }
  ...
  if (out.path === undefined) {
    for (const field of INLINE_FIELDS) {
      const value = parameters[field];
      if (value !== undefined && value !== null) {
        out.inline = { field, size: inlineSize(value) };
        break;
      }
    }
  }
  return out;
}
```

A local file keeps its path twice. `path` is as written in the map. `resolved_path` is the same file relative to the server root. A URL goes into `urls` with its credentials, fragment and query removed by `sanitizeUrl`. Inline data is reported only as its field name and its size in bytes, never its content. The elided lines read `tables` and the `url`, `serverUrl` and `urls` parameters.

```ts
/**
 * The Contents path that JupyterGIS loads for `path`, relative to the map at `documentPath`. A
 * leading `/` means the server root. The result never climbs above the root.
 */
export function resolvePath(documentPath: string, path: string): string | null {
  let joined: string;
  if (path.startsWith('/')) {
    joined = path;
  } else {
    joined = PathExt.joinWithLeadingSlash(
      '/',
      PathExt.dirname(documentPath),
      path,
    );
  }
  const out = PathExt.normalize(joined);
  return out || null;
}
```

For the map `maps/city.jGIS` and the path `../data/crossings.geojson`, the join is `/maps/../data/crossings.geojson` and the result is `data/crossings.geojson`. That result is what Trail later locates on disk.

The source is a separate message. The dialog's `addSource` call fires the source signal, and `wireSourceEvents` emits a `source/v1` event. Each message is its own request. **Deduction** Nothing in the code guarantees that the two arrive in order. In the captured run the source came first.

## Step 4: the message contract

Jupyter Server validates each message against the schema registered for it. `layer_v1.yaml` gained two properties.

```yaml
# python/jupytergis_core/jupytergis_core/event_schemas/layer_v1.yaml
properties:
  ...
  # trail: origin - lets consumers tell the copy that made a change from replicas.
  local:
    title: "Local"
    description: "True when the change originated in the document copy that reported it."
    type: "boolean"
  ...
  object:
    properties:
      ...
      # trail: resources - the layer's data reference, kept for removals of orphaned sources.
      source:
        description: "The reference of the layer's source. Present on added and removed, and on updated when the layer switched sources."
        title: "Source reference"
        type: "object"
        required:
          - "id"
          - "type"
        additionalProperties: false
        properties:
          id: ...
          type: ...
          path: ...
          resolved_path: ...
          tables: ...
          urls: ...
          inline: ...
```

`source` is closed to unknown keys, so a message with a misspelled reference field is rejected by the server and never reaches Trail. **Deduction** from `additionalProperties: false` and the server's handler, which turns any validation error into an HTTP 400. The relay itself is Jupyter's code and did not change. It joins `schema_id` and the fields of `data` and writes them to every websocket subscriber.

```python
# jupyter_server/services/events/handlers.py, SubscribeWebsocket.event_listener
capsule = dict(schema_id=schema_id, **data)
try:
    self.write_message(json.dumps(capsule))
```

This is the message Trail receives, captured as entry 14 of `tests/jupyter/fixtures/baseline.json`:

```json
{
  "schema_id": "https://schema.jupytergis.org/events/layer/v1",
  "subject": "1928399274",
  "verb": "added",
  "local": true,
  "object": {
    "document_path": "maps/city.jGIS",
    "layer_id": "layer-crossings",
    "new": {
      "name": "crossings",
      "type": "VectorLayer",
      "visible": true,
      "parameters": {
        "source": "src-crossings",
        "color": { "circle-fill-color": "#ff0000" }
      }
    },
    "source": {
      "id": "src-crossings",
      "type": "GeoJSONSource",
      "path": "../data/crossings.geojson",
      "resolved_path": "data/crossings.geojson"
    }
  }
}
```

## Steps 5 and 6: Trail receives and routes it

These steps existed before the feature. `subscribe` reads the server's websocket at `api/events/subscribe` and hands each text message to `Consumer.receive`. `receive` parses it, removes `schema_id`, and builds a `Message`, which is one received message together with what became of it. The message's timestamp is the time Trail received it. Messages whose schema starts with `https://schema.jupytergis.org/events/` go to `Consumer.route`.

`route` checks the message shape. `resolve` finds the map: it locates `maps/city.jGIS` under the server's root and returns the map's asset, or a reason to skip. The event class comes from `JupyterEvent.registry`, which maps each schema ID to a class. The feature adds one line to `route`, between building the event and applying it:

```python
# src/trail/jupyter/consumer.py, Consumer.route
event_cls = JupyterEvent.registry.get(record.schema_id, JupyterEvent)
event = event_cls(
    schema_id=record.schema_id,
    data=data,
    server=record.server,
    src_path=found.src_path,
    timestamp=record.timestamp,
)
event.entry = found.asset
self.link(server, event)
# trail: focus - activation and deactivation are recorded without save confirmation.
# trail-todo: delivery - add burst and save handling for non-focus events separately.
event.apply(self._trail)
```

`resolve` can skip the message. **Observed** in my replays: an unsaved map (`document_path` null) and a path outside the server root are dropped without a record. A map whose file is missing is kept as a skipped `Message` with the reason `file missing`.

## Step 7: Trail links the data file

The map stays the event's `entry`. `Consumer.link` adds the data file as two fields on the event. `Consumer.resource` and `Consumer.locate_source` find it, and `Resource` carries the answer.

```python
# src/trail/jupyter/consumer.py
@dataclass(frozen=True, slots=True, kw_only=True)
class Resource:
    """The data file a layer or source reads."""

    # absolute path, or empty if outside the project
    path: str = ''
    # asset id, or None if untracked
    asset_id: str | None = None

# trail: resources - the map stays the entry; the data file it reads is linked by id.
def link(
        self,
        server: Server,
        event: JupyterEvent,
) -> None:
    """
    Set the data file a layer or source event reads. It is stored because the server may be gone
    on replay.
    """
    if isinstance(event, LayerEvent):
        found = self.resource(server, event.source)
        event.resource_path = found.path
        event.resource_id = found.asset_id
    elif isinstance(event, SourceEvent):
        ...

def resource(
        self,
        server: Server,
        source: Source | None,
) -> Resource:
    """Find the data file a source reads."""
    path = self.locate_source(server, source)
    if path is None:
        out = Resource()
        return out
    asset = self._trail.assets.get(path)
    if asset is not None:
        out = Resource(
            path=str(path),
            asset_id=asset.id,
        )
    elif self._parent._parent.is_inside(path.parent):
        out = Resource(path=str(path))
    else:
        out = Resource()
    return out

@staticmethod
def locate_source(
        server: Server,
        source: Source | None,
) -> Path | None:
    """The local path of the file a source reads, or None."""
    if (
        source is None
        or not source.resolved_path
    ):
        return None
    path = server.locate(source.resolved_path)
    if (
        path is not None
        and not path.exists()
    ):
        renamed = server.renamed(source.resolved_path)
        path = server.locate(renamed)
    return path
```

`locate_source` uses only `resolved_path`, so only a source that reads a file on the server gets a link. `Server.locate` turns the server-relative path into a local one and refuses absolute paths, `..`, backslashes, colons and null bytes. If the file is gone, `Server.renamed` applies the renames Trail saw in earlier contents-service messages. The three outcomes:

| The file | `resource_path` | `resource_id` | Printed as |
| --- | --- | --- | --- |
| tracked by the project | absolute path | asset id | `resource_id` |
| inside the project, untracked | absolute path | none | `resource: untracked` |
| outside the project, or no file | empty | none | nothing |

**Observed** with the replay: the tracked file gave a `resource_id` equal to its asset's id. In synthetic variants, an untracked file printed `resource=untracked`. Inline data, a URL source and a message with no `source` printed no resource rows.

`link` reads `event.source`, which parses the message's `object.source` into a `Source`. `Source` is new, in `src/trail/jupyter/source.py`. It is a frozen dataclass with one field per key of the reference. `from_payload` returns `None` for anything malformed, so a bad `source` gives an event without a link.

```python
# src/trail/jupyter/source.py
@dataclass(frozen=True, slots=True, kw_only=True)
class Source:
    """A reference to the data a layer reads, as the event feed reports it."""

    # source id in the map
    id: str
    # kind of source
    type: str
    # data file path as written in the map
    path: str | None = None
    # same path relative to the server root
    resolved_path: str | None = None
    # tables read
    tables: tuple[str, ...] = ()
    # urls read, without credentials or query
    urls: tuple[str, ...] = ()
    # inline data, if there is no path
    inline: Inline | None = None

    @classmethod
    def from_payload(cls, value: Any) -> Source | None:
        """Build a Source from a payload, or None if it is malformed."""
        if (
            not isinstance(value, dict)
            or not isinstance(value.get('id'), str)
            or not isinstance(value.get('type'), str)
        ):
            return None
        ...

    def __str__(self) -> str:
        parts = [self.type]
        if self.path:
            parts.append(self.path)
        ...
        out = ' '.join(parts)
        return out
```

## Step 8: the event describes the layer

`LayerV1Event` records one layer message. `JupyterEvent` holds the message as received and registers each subclass by its schema ID. `LayerEvent` already had `layer_id` and `layer`, which read the message on demand. The feature adds the two stored resource fields, the `source` property, and the rows the repr shows for them.

```python
# src/trail/jupyter/event.py
class LayerEvent(JupyterEvent):
    """A layer added, updated or removed in a map."""

    # trail: resources - stored, since the server that resolved the path may be gone on replay.
    # asset id of the layer's data file, or None
    resource_id: str | None = field(default=None, repr=False)
    # path of the layer's data file, or empty
    resource_path: str = field(default='', repr=False)

    @property
    def source(self) -> Source | None:
        """The data source of the layer, if the message has one."""
        value = (
            self.data
            .get('object', {})
            .get('source')
        )
        out = Source.from_payload(value)
        return out

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield from JupyterEvent._repr_items(self)
        ...
        source = self.source
        if source is not None:
            yield 'source', str(source)
        yield from resource_items(self.resource_path, self.resource_id)
```

`resource_id` and `resource_path` are fields because they are stored with the event. The consumer computes them once, when the message arrives. `source` and the layer name are properties that read the stored message each time. `resource_items` produces the rows in the table above:

```python
def resource_items(
        path: str,
        asset_id: str | None,
        prefix: str = '',
) -> Iterator[tuple[str, object]]:
    """Rows that describe a data file. Nothing if there is no path."""
    if not path:
        return
    yield f'{prefix}resource_path', path
    if asset_id is None:
        yield f'{prefix}resource', 'untracked'
    else:
        yield f'{prefix}resource_id', asset_id
```

`JupyterEvent.is_local` reads the new `local` field. It is `None` if the message has no boolean `local`. `_repr_items` shows it as `local`, and `trail.events.select` can filter on it.

```python
# trail: origin - None means the message carries no locality, not that it is remote.
@property
def is_local(self) -> bool | None:
    """
    Whether the reporting view made the change, or None if the message does not say.
    """
    out = self.data.get('local')
    if type(out) is not bool:
        return None
    return out
```

## Step 9: the log

`event.apply(trail)` appends the event to `trail.events`. `Events.__setitem__` writes one line to `events.jsonl` through `JSONL.append`. `Event.to_record` builds that line from the dataclass fields, so the two resource fields are stored without any new code. The map's asset is held by its ID, and the class by name.

**Observed** The record for the captured addition, with `data` left out:

```json
{
  "id": "cfcc165ef97f41319afa95425efa0f65",
  "timestamp": "2026-11-18T09:35:52.678080+00:00",
  "is_accepted": false,
  "schema_id": "https://schema.jupytergis.org/events/layer/v1",
  "server": "http://localhost:8888/",
  "src_path": "/tmp/tmpr_dg43v0/maps/city.jGIS",
  "resource_id": "1e9fb00e90ae4b6b88ec42bbb5c649b0",
  "resource_path": "/tmp/tmpr_dg43v0/data/crossings.geojson",
  "cls": "LayerV1Event",
  "entry": "f2c9e604e4e8490ab5f04cb110d6a41e",
  "entry_type": "Asset"
}
```

`data` holds the message as received. On replay, `Event.from_record` looks the class up by `cls`, rebuilds the event and applies it with `replay=True`, which does not append it again. The resource fields come back as stored. The layer name and source description are computed again from `data`. **Observed** Reopening the log in a new `Trail` gave an event with an identical repr, and `to_record()` of both compared equal. In the console, `select('cls=LayerEvent verb=added')`, `select('cls=LayerEvent resource_id=<asset id>')` and `select('cls=LayerEvent is_local=true')` each returned the one event.

## Added from Python

A notebook can call `GISDocument.add_geojson_layer`. The kernel writes the layer into its own copy of the map, and the browser receives the change through synchronization. The observer sees a transaction that began in another copy, so `local` is `false`. Python embeds the file's content in the source, because the browser may not be able to read the kernel's file. The source therefore has no `path`, and `sourceReference` takes its last branch, which reports `{ field: 'data', size: 374 }`. The size is the UTF-8 byte length of the value's JSON.

**Observed** Replaying the captured Python addition, entry 58 of `baseline.json`, gives this event. It has no resource fields, because `Source.resolved_path` is empty and `locate_source` returns `None`.

```text
LayerV1Event(
    src_path=<project>/maps/city.jGIS, verb=added, local=False,
    layer_id=f7c35f9c-9923-4b29-9ea9-0c82806d660a, layer=from python,
    source=GeoJSONSource inline data 374 B)
```

## Limits today

- **Retries.** `Consumer.route` skips a repeated `(server, stream, seq)`, but layer messages carry no `stream` or `seq`. The emitter does not set them, the schema does not define them, and no captured layer message has them. **Deduction** The check never applies to layer events, and two identical messages give two events.
- **Several copies.** `wiredModels` joins widgets that share one model. It does not join separate copies of a map, such as a notebook's copy and a browser tab, and each reports its own addition. `local` tells the editing copy from the others. **Planned** The `trail-todo: origin` comment in `LayerEvent._repr_items` calls for dropping replica reports once Trail can identify them. **Deduction** Keeping only `local=True` would also drop the Python additions above.
- **Time.** The event time is when Trail received the message. The captured layer messages have no time field. **Observed**
- **Tracking.** `link` runs once, when the message arrives. **Deduction** A file tracked later does not change an earlier event's `resource_id`.
- **What is linked.** Only a source with a `resolved_path` gives a link. A URL, a geopackage table list or inline data gives a source description and no resource. **Observed**
- **What is not reported.** The layer's place in the layer tree, and layers already in the file when it opens. **Deduction** The plugin wires no handler for tree changes, and the handlers connect after the first sync.

## How this was checked

On 18 November 2026 I read the files quoted above in both checkouts. Excerpts are copied from the working trees, with `...` for omitted lines. Diff blocks show the added and removed lines against `abecc972` for JupyterGIS. The browser code was read, not run.

I replayed entries 13, 14 and 58 of `tests/jupyter/fixtures/baseline.json` through `Consumer.receive` against a temporary project with `maps/city.jGIS` and a tracked `data/crossings.geojson`. I checked the printed events, the `events.jsonl` record, the event rebuilt by reopening the log, and three `select` queries. I then sent 14 synthetic messages covering tracked and untracked files, inline data, a URL source, no source, an unsaved map, a path outside the root, a missing map, a repeated delivery, a malformed payload, an unknown layer version and another schema.
