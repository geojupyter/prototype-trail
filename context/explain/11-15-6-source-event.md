# Source events

## TLDR

Unlabeled statements describe the current code, read at the checkouts in [How this was checked](#how-this-was-checked-tldr). **Observed** marks output from a run. **Deduction** marks my inference. **Planned** marks work that a plan or a `trail-todo` comment describes and the code does not do. A reason is credited to the plan, schema, docstring or comment that states it. Links to Trail code point at the working tree. Links to JupyterGIS code are pinned to a commit.

### What happens when a map's data source changes ([Deep Dive](#what-happens-when-a-maps-data-source-changes-tldr))

You add a layer from a file, point a layer's data at another file, or remove the layer. A **source** is the entry of the map that says where a layer's data comes from. Trail records each change of a source as one [`SourceV1Event`](../../src/trail/jupyter/event.py#L469): `added`, `updated` or `removed`, with the source before and after. When the source reads a file that Trail tracks, the record also links that file's asset. **Observed:** a captured session replayed through Trail's consumer gave an `updated` record whose `old` source read `../data/crossings.geojson` and whose `new` source read `../data/crossings_2026.geojson`, with both files linked ([what each record looks like](#what-each-record-looks-like)).

These terms are needed:

- **JupyterLab** is the browser workspace that holds notebooks and maps.
- **JupyterGIS** is the map editor inside JupyterLab. A map is a `.jGIS` file, which Trail tracks as an asset.
- A **layer** is one drawable item on a map. It reads its data from a **source**: a file such as `data/crossings.geojson`, a web address, or data stored inside the map. Several layers can read one source.
- **Jupyter Server** is the process behind JupyterLab. It serves the files and relays JupyterGIS's messages to anything that subscribes.

Three steps stay distinct:

1. **You act.** You add a layer from a file, change a source's path, or remove a layer.
2. **JupyterGIS reports.** The page sends a `source/v1` message when a source is added, removed or points at other data ([what the browser reports](#what-the-browser-reports-tldr)). The message holds the old and the new reference to the data ([the data reference](#the-data-reference-tldr)). Jupyter Server checks it and relays it.
3. **Trail records.** Trail subscribes to the server. For each message it finds the map's asset, then the data file the source reads ([finding the data file](#finding-the-data-file-tldr)), builds a [`SourceV1Event`](../../src/trail/jupyter/event.py#L469) ([the record classes](#the-record-classes-tldr)) and appends it to its event log ([storage and replay](#storage-and-replay-tldr)).

| Piece | Where | Role |
|---|---|---|
| emitter, [`wireSourceEvents`][ef-wire-source] | JupyterGIS, in the browser | Sends one message per changed source. |
| [`SourceReferences`][ef-source-references], [`sourceReference`][ef-source-reference] | JupyterGIS, in the browser | Describe where a source's data is ([the data reference](#the-data-reference-tldr)). |
| [`Consumer`](../../src/trail/jupyter/consumer.py#L74) | `trail.jupyter.consumer`, reached as [`trail.jupyter.session.consumer`](../../src/trail/jupyter/session.py#L37) | Finds the map's asset, builds the record, links the data file and appends it. |
| [`SourceEvent`](../../src/trail/jupyter/event.py#L388), [`Source`](../../src/trail/jupyter/source.py#L20) | `trail.jupyter.event`, `trail.jupyter.source` | The record, and the data reference it holds ([the record classes](#the-record-classes-tldr)). |

Skeleton of the Trail side, condensed from the source and not runnable as written:

```python
# trail/jupyter/consumer.py
class Consumer(Node):
    def route(self, server, record) -> None:
        event = event_cls(schema_id=..., data=data, ...)     # SourceV1Event
        self.link(server, event)                             # the data files it reads
        event.apply(self._trail)

    def link(self, server, event) -> None:
        found = self.resource(server, event.current)         # the source now
        found = self.resource(server, event.previous)        # the source before an update
```

### What the browser reports ([Deep Dive](#what-the-browser-reports-tldr))

The emitter listens to the map's sources. For each change it builds the data reference of the source before and after. It sends `added` if the source is new, `removed` if it is gone, and `updated` if both exist and the reference differs. A change that leaves the reference as it was sends nothing. That covers the features that the draw tool writes back into a source with embedded data, and the bands that a GeoZarr loader detects. Each message also says whether the change started in the window that reports it, as `local`.

```ts
// python/jupytergis_core/src/eventFeed/plugin.ts
const previous = oldValue ? references.compute(id, oldValue) : undefined;
const current = newValue ? references.remember(id, newValue) : undefined;
if (!previous && current) verb = 'added';
else if (previous && !current) verb = 'removed';
else if (previous && current && !isSameReference(previous, current)) verb = 'updated';
else continue;
```

### The data reference ([Deep Dive](#the-data-reference-tldr))

A reference says where a source's data is, and never holds the data or a secret. It has the source's id and type, and then what fits the source: the file's path as written in the map, the same file's path under the server's root, the tables of a GeoPackage, the web addresses without credentials or query parameters, or, for data stored inside the map, the name of the field that holds it and its size. The path under the server's root is the one Trail uses to find the file.

```ts
// python/jupytergis_core/src/eventFeed/sources.ts
export function sourceReference(id, source, documentPath): ISourceReference | undefined { ... }
// { id, type, path?, resolved_path?, tables?, urls?, inline?: { field, size } }
```

### Finding the data file ([Deep Dive](#finding-the-data-file-tldr))

[`Consumer.link`](../../src/trail/jupyter/consumer.py#L313) finds the data file that the source reads now, and the one it read before an update. It turns the reference's `resolved_path` into a local path, follows a rename that Jupyter Server reported, and looks the file up among Trail's assets. A tracked file gives its path and asset id. An untracked file inside the project gives its path alone. A web address, embedded data and a file outside the project give nothing. The map stays the record's `entry`.

```python
# trail/jupyter/consumer.py
def resource(self, server, source: Source | None) -> Resource:
    path = self.locate_source(server, source)
    asset = self._trail.assets.get(path)
    if asset is not None:
        out = Resource(path=str(path), asset_id=asset.id)
```

### The record classes ([Deep Dive](#the-record-classes-tldr))

[`Event`](../../src/trail/event.py#L59) → [`JupyterEvent`](../../src/trail/jupyter/event.py#L36) → `SourceEvent` → `SourceV1Event`. `JupyterEvent` keeps the message in `data`. `SourceEvent` reads the source's id and the sources before and after from `data`, and stores the links to the two data files. `SourceV1Event` only names its schema, so a `source/v1` is not the last version Trail could meet. `Source` is a small frozen dataclass that holds one data reference. A record prints [`source_id`](../../src/trail/jupyter/event.py#L405), `old`, `new`, and the rows of the data files, `resource_path` with `resource_id` or `resource: untracked`.

```python
# trail/jupyter/event.py
class SourceEvent(JupyterEvent):
    resource_id: str | None
    resource_path: str
    previous_resource_id: str | None
    previous_resource_path: str

    @property
    def current(self) -> Source | None: ...    # old for a removal, new otherwise
```

### Storage and replay ([Deep Dive](#storage-and-replay-tldr))

Each record is one JSON line in the log. The line holds the message `data`, `schema_id`, `server`, [`src_path`](../../src/trail/jupyter/event.py#L58), the four links to the data files, and the record's `id` and `timestamp`. The `source_id`, `old`, `new` and the printed rows are computed from `data` when the record is read back. The links are stored because the server that found the path may be gone on replay. **Observed:** after reopening the project, every source record of a test run rebuilt to an equal record, with its links.

```python
# trail/event.py
class Event:
    def to_record(self) -> dict: ...                  # one JSON line of the log
    @classmethod
    def from_record(cls, /, trail, resolved_entry=None, **record) -> Event: ...
```

### Limits today ([Deep Dive](#limits-today-tldr))

**Current behavior:**

- **Only a local file is linked.** A web address, embedded data and a file outside the project give a record without a link ([finding the data file](#finding-the-data-file-tldr)).
- **A source change names no layer.** Several layers can read one source, and the record says only which source changed ([what the browser reports](#what-the-browser-reports-tldr)).
- **Edits inside embedded data are not source events** ([what the browser reports](#what-the-browser-reports-tldr)).
- **A query on `old` or `new` compares the source's id, not the printed text** ([the record classes](#the-record-classes-tldr)).
- **Each window that shows the map reports a change.** A change can give several records, and nothing drops the copies ([what the browser reports](#what-the-browser-reports-tldr)).

### How this was checked ([Deep Dive](#how-this-was-checked-tldr))

The consumer and event tests pass: 76 passed and 1 skipped, together with the kernel and subscription tests. The emitter's tests pass: 49 in the event feed. **Observed:** a scenario run replayed a captured session through the real consumer, and further runs sent sources of every kind: tracked, untracked, outside the project, outside the server's root, a web address and embedded data. No browser was run. Everything about the emitter comes from reading its source, its tests and its docs.

## Deep Dive

### What happens when a map's data source changes ([TLDR](#what-happens-when-a-maps-data-source-changes-deep-dive))

This section lists every stage between a change of a map's source and a line in the log. Later sections cover each stage in depth.

| # | Where | What happens | What can stop it |
|---|---|---|---|
| 1 | JupyterGIS document, [`_sourcesObserver`][ef-sources-observer] | The shared document reports each changed source key with its old value, new value and `local`. | |
| 2 | emitter, [`wireSourceEvents`][ef-wire-source] | Builds the references before and after, picks the verb and sends `source/v1` with `events.emit`. | A change that leaves the reference as it was. |
| 3 | Jupyter Server, `POST /api/events` | Validates the message against the schema registered under its `schema_id` and puts it on the event bus. | A rejected message gives a `400` that only the browser console shows. |
| 4 | Trail, [`subscribe`](../../src/trail/jupyter/subscription.py#L14) | Reads the server's WebSocket feed at [`Server.endpoint`](../../src/trail/jupyter/server.py#L288) and passes each text message to the consumer. | A refused, unsupported or dropped connection. |
| 5 | [`Consumer.receive`](../../src/trail/jupyter/consumer.py#L96) | Parses the message and keeps JupyterGIS messages. | Invalid JSON, or another schema. |
| 6 | [`Consumer.route`](../../src/trail/jupyter/consumer.py#L166) and [`resolve`](../../src/trail/jupyter/consumer.py#L238) | Finds the map's asset and builds the record. | Unsaved, outside root, outside project, offtrailed, file missing. |
| 7 | [`Consumer.link`](../../src/trail/jupyter/consumer.py#L313) | Finds the data files the source reads now and read before. | |
| 8 | [`JupyterEvent.apply`](../../src/trail/jupyter/event.py#L114) | Appends the record to `trail.events`, which writes one line to the log. | |

The Trail side runs in a session: [`JupyterSession`](../../src/trail/jupyter/session.py#L21) creates the consumer as [`session.consumer`](../../src/trail/jupyter/session.py#L37). The classes [`Event`](../../src/trail/event.py#L59) and [`JupyterEvent`](../../src/trail/jupyter/event.py#L36), the consumer's checks and the log are shared with the other event families. [Layer events](11-15-1-layer-event.md#making-the-record-tldr) explains them in full. A layer's own record also carries a data reference, found by the same `link`. This note covers what is specific to sources.

Adding a layer from a file gives two records. Every way of adding a layer from data adds the source first and the layer second, each in its own transaction, according to the plan for the remaining events ([Showing where a map layer gets its data](../plan/11-4-2-remaining-baseline-events.md)). **Observed:** in the captured session, a source `added` record came directly before the layer `added` record that reads it.

### What the browser reports ([TLDR](#what-the-browser-reports-deep-dive))

This section covers what the emitter sends and when. The emitter lives in JupyterGIS, not in Trail. Its code is in the clone `../eventfeed`, a prototype that adds the event feed to JupyterGIS. The source message is one of the prototype's additions, and the event coverage report lists the new message types for sources among the changes still to propose upstream ([event coverage report](../../scratch/11-12-2-event-coverage.md)).

#### Where a change comes from

JupyterGIS keeps a map's sources in its shared document, which every open window and the Python API edit. Yjs is the library that keeps those copies in step. [`_sourcesObserver`][ef-sources-observer] runs on every change to the document's `sources` map. For each changed key it builds `{id, oldValue, newValue, local}`. `oldValue` is the value the key held before, and is missing for a new key. `newValue` is a deep copy of the value now, and is missing for a deleted key. `local` is `event.transaction.local`. The model re-exposes the signal as [`sharedSourcesChanged`][ef-shared-sources-changed].

The plan notes why an observer is needed: the document reported source changes with their new values only, and the emitter did not subscribe to them, so Trail could not say which file a new layer shows. The observer keeps the old value, as the layer observer does.

#### `wireSourceEvents`

[`wireSourceEvents`][ef-wire-source] turns each change into a message, or into nothing:

```ts
export const wireSourceEvents = (
  { widget, clientId, emit }: IEventWiringProps,
  references: SourceReferences,
): void => {
  widget.model.sharedSourcesChanged.connect((_, change) => {
    for (const { id, oldValue, newValue, local } of change.sourceChange ?? []) {
      // trail: resources - the old value comes from Yjs, not the cache another handler may update.
      const previous = oldValue ? references.compute(id, oldValue) : undefined;
      const current = newValue ? references.remember(id, newValue) : undefined;
      let verb: 'added' | 'updated' | 'removed';
      if (!previous && current) {
        verb = 'added';
      } else if (previous && !current) {
        verb = 'removed';
      } else if (previous && current && !isSameReference(previous, current)) {
        verb = 'updated';
      } else {
        // trail: resources - edits inside inline data or detected bands are not source events.
        continue;
      }

      emit({
        schema_id: SOURCE_SCHEMA,
        version: '1',
        data: {
          subject: clientId,
          verb,
          ...(local === undefined ? {} : { local }),
          object: {
            document_path: widget.model.filePath || null,
            source_id: id,
            ...(previous ? { old: previous as unknown as JSONObject } : {}),
            ...(current ? { new: current as unknown as JSONObject } : {}),
          },
        },
      });
    }
  });
};
```

| `previous` | `current` | Same reference | Verb |
|---|---|---|---|
| missing | present | | `added` |
| present | missing | | `removed` |
| present | present | no | `updated` |
| present | present | yes | nothing is sent |

The old reference is computed from the old value that Yjs gives, not read from the cache, because another handler may already have updated the cache. The new reference is computed and cached. The comment on the last branch names the rule: edits inside inline data or detected bands are not source events. [`isSameReference`][ef-same-reference] compares the type, the path, the resolved path, the tables, the URLs and the name of the inline field, and leaves out the size of inline data:

```ts
export function isSameReference(
  a: ISourceReference | undefined,
  b: ISourceReference | undefined,
): boolean {
  const key = (reference: ISourceReference | undefined) =>
    reference &&
    JSON.stringify([
      reference.type,
      reference.path ?? null,
      reference.resolved_path ?? null,
      reference.tables ?? [],
      reference.urls ?? [],
      reference.inline?.field ?? null,
    ]);
  return key(a) === key(b);
}
```

The docstring gives the reason: the size of inline data is display metadata, so edits inside inline data do not count as a change. The plan names the cases. The draw tool writes inline features back after every drawn feature. Loading a GeoZarr source writes its detected bands in every view that loads it. Neither is a change of where the data is. The emitter's docs and the schema say the same ([Layer and source references][ef-events-md-references]).

The plugin wires this function once for each model, after the model's first synchronization, with the other emitters that read the document. Connecting earlier would report every existing source as new when a map opens:

```ts
      const model = widget.model;
      void model.sharedModel.initialSyncReady.then(() => {
        if (model.isDisposed) {
          return;
        }
        const references = new SourceReferences(model);
        references.seed();
        wireLayerEvents(eventWiringProps, references);
        wireSourceEvents(eventWiringProps, references);
        wireViewportEvents(model, emit);
      });
```

[`seed`][ef-seed] caches the reference of every source of the map once it has synchronized. The comment in [`eventFeedPlugin`][ef-plugin] states the reason for the wait: when a document loads, all its existing layers come through as `added`, and connecting before that gives a burst of `added` messages. Two views of one map share one model, so they share one wiring. Two windows have two models, so each reports a change. **Deduction:** a source change shown in two windows gives two records, as a layer change does ([Layer events](11-15-1-layer-event.md#limits-today-tldr)). `local` is `true` in the window that made the change and `false` in the other, and nothing in Trail drops the copy.

The emitter's tests cover the wiring and the references: [`reports source additions, reference changes and removals only`][ef-test-sources], [`adds locality and the source reference to layer messages`][ef-test-locality] and [`names the new source when a layer switches sources`][ef-test-switch].

#### The messages

The schema is [`source_v1.yaml`][ef-schema-source]. Trail keeps a copy for its tests as [`tests/jupyter/schemas/source_v1.yaml`](../../tests/jupyter/schemas/source_v1.yaml). **Observed:** a manual `diff` found no difference. The schema requires `verb` and `object`, and the object requires `document_path` and [`source_id`](../../src/trail/jupyter/event.py#L405).

| Field | Content |
|---|---|
| `verb` | `added`, `updated` or `removed`. The schema says `updated` means the data reference changed. |
| `subject` | The client id of the map copy that sent the message. |
| `local` | `true` when the change started in the copy that sent it. Left out when unknown. |
| `object.document_path` | The `.jGIS` path relative to the server root, or `null` for an unsaved map. |
| `object.source_id` | The source's id. |
| `object.old` | The reference before the change. Absent for `added`. |
| `object.new` | The reference after the change. Absent for `removed`. |

The schema's `sourceReference` definition has `additionalProperties: false` and requires `id` and `type`. [The data reference](#the-data-reference-tldr) covers its fields.

#### Delivery to Trail

The browser sends the message with `events.emit`, which posts it to Jupyter Server. The server validates it against the schema registered under its `schema_id` by [`register_event_schemas`][ef-register]. It rejects an unregistered id or a mismatched version with a `400`. The rejection reaches only the browser console ([Silent failures][ef-events-md-silent]).

### The data reference ([TLDR](#the-data-reference-deep-dive))

This section covers what a reference holds and how the emitter builds it. A layer's message carries the same reference for its source, and [Layer events](11-15-1-layer-event.md#what-the-browser-reports-tldr) covers it from the layer's side.

#### `sourceReference`

[`sourceReference`][ef-source-reference] reads the source's parameters:

```ts
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
  if (typeof parameters.tables === 'string' && parameters.tables.trim()) {
    out.tables = parameters.tables
      .split(',')
      .map((table: string) => table.trim())
      .filter(Boolean);
  }
  for (const field of URL_FIELDS) {
    const value = parameters[field];
    if (typeof value === 'string' && value) {
      urls.push(value);
    }
  }
  if (Array.isArray(parameters.urls)) {
    for (const item of parameters.urls) {
      if (typeof item?.url === 'string' && item.url) {
        urls.push(item.url);
      }
    }
  }
  if (urls.length) {
    out.urls = urls.map(url => sanitizeUrl(url));
  }
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

| Field | Content |
|---|---|
| `id`, `type` | The source's id and type, such as `GeoJSONSource`. |
| `path` | The path as written in the source, relative to the map. `null` if the path is a web address. |
| `resolved_path` | The path JupyterGIS loads, relative to the server root, from [`resolvePath`][ef-resolve-path]. `null` for a web address, and absent when the source has no `path`. |
| `tables` | The tables of a GeoPackage source, from its comma separated `tables`. |
| `urls` | The web addresses of the source, from `path` when it is one, from `url` and `serverUrl`, and from the `url` of each entry of `urls`. Each goes through [`sanitizeUrl`][ef-sanitize-url]. |
| `inline` | Set only when the source has no `path`. The name of the field that holds the data, `data`, `feature` or `processGraph`, and its size from [`inlineSize`][ef-inline-size]. The data is never sent. |

[`resolvePath`][ef-resolve-path] joins the directory of the map's path with the source's path, and a leading `/` means the server root:

```ts
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

The doc comment says the result is the Contents path that JupyterGIS loads, relative to the map, and never climbs above the server root. For a map at `maps/city.jGIS` with the path `../data/crossings.geojson`, the resolved path is `data/crossings.geojson`.

[`sanitizeUrl`][ef-sanitize-url] removes the credentials in the web address, the fragment and every query parameter that is not in [`SAFE_QUERY_PARAMETERS`][ef-safe-query]. That set is empty, so every query parameter is removed. The comment says it stays empty until specific parameters are known to be harmless. It works on the string, so tile templates such as `{z}` stay as written. The reference never reads `httpHeaders`, `urlParameters`, an OpenEO token or the contents of a process graph. The plan gives the reason: those can hold secrets, and the schema says a reference never holds credentials, unsafe query parameters or inline data.

#### `SourceReferences`

[`SourceReferences`][ef-source-references] holds the last known reference of each source of one map. A layer removal can be observed after the source it leaves behind is gone, so the cache lets the layer's message still name the data. [`seed`][ef-seed], [`remember`][ef-remember] and [`get`][ef-get] keep it:

```ts
export class SourceReferences {
  constructor(private readonly _model: IJupyterGISModel) {}

  /**
   * Cache the references of every source in the map, after its initial synchronization.
   */
  seed(): void {
    const sources = this._model.sharedModel.sources ?? {};
    for (const [id, source] of Object.entries(sources)) {
      this.remember(id, source);
    }
  }
  ...
  /**
   * Compute and cache the reference of a source value. Returns it.
   */
  remember(
    id: string,
    source: IJGISSource | Record<string, any> | undefined,
  ): ISourceReference | undefined {
    const reference = this.compute(id, source);
    if (reference) {
      this._cache.set(id, reference);
    }
    return reference;
  }

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
```

[`compute`][ef-compute] builds a reference without caching it. `wireSourceEvents` uses `compute` for the old value and `remember` for the new one. The layer emitter uses `get`. The tests for the references are [`reports a relative path and the path JupyterGIS loads`][ef-test-reference], [`describes inline data by field and size and omits tokens`][ef-test-inline] and [`falls back to the last reference after the source is removed`][ef-test-cache]. **Observed:** all 49 tests of the emitter's event feed pass ([How this was checked](#how-this-was-checked-tldr)).

### Finding the data file ([TLDR](#finding-the-data-file-deep-dive))

This section covers how Trail goes from a reference to a data file and an asset. It is the same for a layer record and a source record. [`Consumer`](../../src/trail/jupyter/consumer.py#L74) lives in `trail.jupyter.consumer`, and the session creates it as [`trail.jupyter.session.consumer`](../../src/trail/jupyter/session.py#L37). [Layer events](11-15-1-layer-event.md#making-the-record-tldr) covers `receive`, `route` and `resolve`, which find the map. This section starts after the record is built.

#### `Consumer.link`

[`link`](../../src/trail/jupyter/consumer.py#L313) sets the data files on the record. The map stays the entry, and the files are stored by id and path:

```python
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
        found = self.resource(server, event.current)
        event.resource_path = found.path
        event.resource_id = found.asset_id
        found = self.resource(server, event.previous)
        event.previous_resource_path = found.path
        event.previous_resource_id = found.asset_id
```

For a `SourceEvent`, `current` is the source the change leaves in the map, and `previous` is the one an update replaced ([the record classes](#the-record-classes-tldr)). `link` links both. The reason for storing the link is in the source comment and the docstring: the server that found the path may be gone on replay. The plan adds why the map stays the entry: so that a query can select every layer or source that uses one file.

#### `Consumer.resource` and `Consumer.locate_source`

[`resource`](../../src/trail/jupyter/consumer.py#L334) returns a [`Resource`](../../src/trail/jupyter/consumer.py#L65), which holds a `path` and an `asset_id`:

```python
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
```

[`locate_source`](../../src/trail/jupyter/consumer.py#L357) goes from `Source.resolved_path` to a local path:

```python
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

A source with no `resolved_path`, which is a web address or embedded data, gives `None` and an empty `Resource`. [`Server.locate`](../../src/trail/jupyter/server.py#L298) joins the path to the server's root and refuses a path that is absolute or holds `..`, a backslash, a colon or a NUL byte. If the resolved file does not exist, [`Server.renamed`](../../src/trail/jupyter/server.py#L314) follows the renames Trail learned from Jupyter Server. A rename of a folder moves every path under it, and the longest renamed prefix applies first. [`Consumer.observe`](../../src/trail/jupyter/consumer.py#L376) learns the renames from the contents service's `rename` messages and keeps them in `server.renames`. **Observed:** a rename of a file inside the project was kept as a `used` message, and a rename of a file outside the project was dropped by `receive` with no change to the skipped counts. Both renames were added to `server.renames`. Only the message of the second is missing from `session.messages`.

| The data file | `Resource` | Rows printed |
|---|---|---|
| is a tracked asset | path and asset id | `resource_path`, `resource_id` |
| is inside the project but untracked | path only | `resource_path`, `resource: untracked` |
| is outside the project, outside the server's root, a web address, or embedded data | empty | none |

**Observed results.** A scenario run, at the checkouts in [How this was checked](#how-this-was-checked-tldr), sent nine sources through the real consumer. The project held `maps/city.jGIS`, the tracked files `data/crossings.geojson` and `data/crossings_2026.geojson`, and the untracked `data/loose.geojson`. The server's root was the folder above the project, so that a file could lie outside it. Every message was recorded. A tracked file, a replaced file and a removed source gave links. A web address and embedded data gave none. A file in the project that is not tracked gave `resource: untracked`. A file in the server's root but outside the project gave none, and so did a path that climbs out of the root. A GeoPackage source with tables, given the path of a tracked file as a stand-in, gave a link to that file and printed its tables. A known rename of `data/before.geojson` to `data/crossings_2026.geojson` was followed to the tracked asset.

[`test_source_update_links_old_and_new`](../../tests/jupyter/test_consumer.py#L290) covers a replaced file and a removed source. [`test_source_follows_a_known_rename`](../../tests/jupyter/test_consumer.py#L328) covers the rename. [`test_unlinked_references_are_still_recorded`](../../tests/jupyter/test_consumer.py#L354) covers a web address, inline data, a path outside the root, and malformed references. [`test_untracked_file_inside_the_project_is_shown_untracked`](../../tests/jupyter/test_consumer.py#L391) and [`test_untracked_file_outside_the_project_is_not_linked`](../../tests/jupyter/test_consumer.py#L368) cover the other two rows.

The link is set before the record is applied, so the stored record already holds it. An old emitter whose messages lack `resolved_path` is still recorded, without a link. The plan asks for it: a message from an older emitter without `resolved_path` should still be recorded without a resource link.

### The record classes ([TLDR](#the-record-classes-deep-dive))

This section introduces the classes that hold a source record. [`Event`](../../src/trail/event.py#L59) is the base of every record in `trail.events`. [`JupyterEvent`](../../src/trail/jupyter/event.py#L36) is the base of the records made from JupyterGIS messages, and it holds `schema_id`, `data`, `server`, [`src_path`](../../src/trail/jupyter/event.py#L58) and `entry`. [Layer events](11-15-1-layer-event.md#the-record-classes-tldr) describes both.

#### `Source` and `Inline`

[`Source`](../../src/trail/jupyter/source.py#L20) is a frozen dataclass in `trail.jupyter.source`. [`LayerEvent`](../../src/trail/jupyter/event.py#L200) and `SourceEvent` build it from their message, and `Consumer.locate_source` reads its `resolved_path`. [`Inline`](../../src/trail/jupyter/source.py#L10) holds the field name and the size of data that sits in the map file.

```python
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
```

[`Source.from_payload`](../../src/trail/jupyter/source.py#L39) returns `None` unless the value is a mapping with a string `id` and a string `type`. It drops a field of the wrong type and does not raise. [`Source.__str__`](../../src/trail/jupyter/source.py#L87) joins the type, the path, the tables in parentheses, the URLs, and `inline <field> <size>`:

```text
GeoJSONSource ../data/crossings.geojson
GeoPackageVectorSource ../data/crossings.geojson (roads, bridges)
RasterSource https://tile.example.org/{z}/{x}/{y}.png
GeoJSONSource inline data 374 B
```

[`test_source_from_payload`](../../tests/jupyter/test_events.py#L360) covers the parsing, including malformed fields. The listing above is the printed form from the scenario run.

#### `SourceEvent` and `SourceV1Event`

[`SourceEvent`](../../src/trail/jupyter/event.py#L388) is the source record. [`SourceV1Event`](../../src/trail/jupyter/event.py#L469) extends it and only names the schema `https://schema.jupytergis.org/events/source/v1`, so a `source/v2` would get a sibling class. **Observed:** a message of another schema version is recorded as a plain `JupyterEvent`, as [Layer events](11-15-1-layer-event.md#the-record-classes-tldr) shows for layers.

```python
@dataclass(kw_only=True, slots=True, repr=False)
class SourceEvent(JupyterEvent):
    """
    A source added, updated or removed in a map. A source is the data a layer reads.
    """

    selectable: ClassVar[tuple[str, ...]] = ('verb', 'source_id', 'is_local', 'old', 'new')

    # asset id of the source's data file, or None
    resource_id: str | None = field(default=None, repr=False)
    # path of the source's data file, or empty
    resource_path: str = field(default='', repr=False)
    # asset id of the previous data file, or None
    previous_resource_id: str | None = field(default=None, repr=False)
    # path of the previous data file, or empty
    previous_resource_path: str = field(default='', repr=False)
```

The four fields are stored, and the properties read `data`:

| Name | Kind | Meaning |
|---|---|---|
| [`SourceEvent.resource_id`](../../src/trail/jupyter/event.py#L396), [`SourceEvent.resource_path`](../../src/trail/jupyter/event.py#L398) | stored | The data file the source reads now. |
| [`previous_resource_id`](../../src/trail/jupyter/event.py#L400), [`previous_resource_path`](../../src/trail/jupyter/event.py#L402) | stored | The data file the source read before an update. |
| [`source_id`](../../src/trail/jupyter/event.py#L405) | property | `object.source_id`. |
| [`old`](../../src/trail/jupyter/event.py#L414), [`new`](../../src/trail/jupyter/event.py#L425) | property | The source before and after, as a `Source`. `old` is `None` for `added`. `new` is `None` for `removed`. |
| [`current`](../../src/trail/jupyter/event.py#L436) | property | The source the change leaves in the map: `old` for a removal, `new` otherwise. |
| [`SourceEvent.previous`](../../src/trail/jupyter/event.py#L445) | property | The source an update replaced: `old` for `updated`, and `None` for any other verb. |

`current` and `previous` are what `Consumer.link` reads:

```python
@property
def current(self) -> Source | None:
    """The source the change leaves in the map."""
    if self.verb == 'removed':
        out = self.old
    else:
        out = self.new
    return out
```

A removed source therefore links its old file as the current one, and has no previous file. **Observed:** in the scenario run, a `removed` record held `old`, `resource_path` and `resource_id` of the file it read, and no `previous_` rows.

#### Printing

[`SourceEvent._repr_items`](../../src/trail/jupyter/event.py#L452) yields the rows the console prints:

```python
def _repr_items(self) -> Iterator[tuple[str, object]]:
    yield from JupyterEvent._repr_items(self)
    if self.source_id is not None:
        yield 'source_id', self.source_id
    for name in ('old', 'new'):
        source = getattr(self, name)
        if source is not None:
            yield name, str(source)
    yield from resource_items(self.resource_path, self.resource_id)
    yield from resource_items(
        self.previous_resource_path,
        self.previous_resource_id,
        'previous_',
    )
```

The rows follow the [`JupyterEvent` rows](../../src/trail/jupyter/event.py#L107): `id`, `timestamp`, `schema_id`, `server`, `src_path`, `verb` and `local`. [`resource_items`](../../src/trail/jupyter/event.py#L367) yields `resource_path` and `resource_id` for a tracked file, `resource_path` and `resource: untracked` for an untracked file inside the project, and nothing otherwise. With the prefix `previous_` it gives the rows of the file before an update. The console adds an `entry` row with the map's asset id last, through [`Renderer.parameters`](../../src/trail/cli/render.py#L111). [`Renderer.verb`](../../src/trail/cli/render.py#L254) returns the verb for the heading, and [`VERB_STYLES`](../../src/trail/cli/theme.py#L46) colors `added` like `created`, `updated` like `modified` and `removed` like `deleted`.

#### What each record looks like

**Observed results.** A scenario run, at the checkouts in [How this was checked](#how-this-was-checked-tldr), sent nine sources through the real consumer. The listings are `print(trail.events.select('cls=SourceEvent'))`, with paths shortened to the project root as the console does, and without the `id`, `timestamp`, `schema_id` and `server` rows. The console would add an `entry` row to each record. The scenario reused the source id `S1` for several messages, which does not matter to Trail.

A source added from a tracked file. Both resource rows appear:

```text
0. SourceV1Event
    src_path: maps/city.jGIS
    verb: added
    local: True
    source_id: S1
    new: GeoJSONSource ../data/crossings.geojson
    resource_path: data/crossings.geojson
    resource_id: 398e23557a2d4fb6bc34dd5902e370e6
```

A source pointed at another file. The new file is linked as `resource_*`, and the old one as `previous_resource_*`:

```text
1. SourceV1Event
    src_path: maps/city.jGIS
    verb: updated
    local: True
    source_id: S1
    old: GeoJSONSource ../data/crossings.geojson
    new: GeoJSONSource ../data/crossings_2026.geojson
    resource_path: data/crossings_2026.geojson
    resource_id: ce24fee47d514e978a81d87b5c734038
    previous_resource_path: data/crossings.geojson
    previous_resource_id: 398e23557a2d4fb6bc34dd5902e370e6
```

A removed source. `old` is the source that was removed, and its file is still linked:

```text
2. SourceV1Event
    src_path: maps/city.jGIS
    verb: removed
    local: True
    source_id: S1
    old: GeoJSONSource ../data/crossings_2026.geojson
    resource_path: data/crossings_2026.geojson
    resource_id: ce24fee47d514e978a81d87b5c734038
```

A web address and embedded data have no file, so no resource rows. A change from Python arrives with `local: False`:

```text
3. SourceV1Event
    src_path: maps/city.jGIS
    verb: added
    local: True
    source_id: S1
    new: RasterSource https://tile.example.org/{z}/{x}/{y}.png
4. SourceV1Event
    src_path: maps/city.jGIS
    verb: added
    local: False
    source_id: S1
    new: GeoJSONSource inline data 374 B
```

A file in the project that Trail does not track:

```text
5. SourceV1Event
    src_path: maps/city.jGIS
    verb: added
    local: True
    source_id: S1
    new: GeoJSONSource ../data/loose.geojson
    resource_path: data/loose.geojson
    resource: untracked
```

A GeoPackage source prints its tables in parentheses after the path:

```text
8. SourceV1Event
    src_path: maps/city.jGIS
    verb: added
    local: True
    source_id: S1
    new: GeoPackageVectorSource ../data/crossings.geojson (roads, bridges)
    resource_path: data/crossings.geojson
    resource_id: 398e23557a2d4fb6bc34dd5902e370e6
```

The captured session [`baseline.json`](../../tests/jupyter/fixtures/baseline.json), replayed through the same consumer, gave 10 source records. They include the replaced file above with both files linked, a web address, a marker source with embedded data that follows the switch to Add Marker in the session, an embedded source of 42 bytes, and an embedded source of 374 bytes that was added and removed from Python with `local: False`. Records of earlier recorded sessions, as the console printed them, are in the event coverage report's [layer's data file replaced](../../scratch/11-12-2-event-coverage.md#4-a-layers-data-file-replaced).

#### Selecting

A query can read the names in [`SourceEvent.selectable`](../../src/trail/jupyter/event.py#L393), which the console offers as completions. It can read the stored fields, and any other attribute of the record or a path into `data`, through [`Select.held`](../../src/trail/select.py#L264). `cls=SourceEvent` also selects records of later schema versions, because [`Select.equals`](../../src/trail/select.py#L241) matches a class by the names of its bases.

| Query | Selects | Observed in the scenario run |
|---|---|---|
| `events cls=SourceEvent verb=updated` | sources that pointed at other data | 1 record |
| `events cls=SourceEvent is_local=false` | changes that started in another window or in Python | 1 record |
| `events cls=SourceEvent resource_id=<asset id>` | every source that reads one tracked file now | 2 records |
| `events cls=SourceEvent previous_resource_id=<asset id>` | every source that stopped reading one file | 1 record |
| `events cls=SourceEvent new=S2` | records whose new source has the id `S2` | 1 record |
| `events cls=SourceEvent new=RasterSource` | the printed type | 0 records |
| `events cls=SourceEvent data.object.new.type=RasterSource` | records whose new source has that type | 1 record |
| `events cls=SourceEvent data.object.new.inline.size>100` | embedded data above 100 bytes | 1 record |

`old` and `new` are properties that return a `Source`, and `Select.held` returns the `id` of an object that has one, so a comparison on `new` or `old` is a comparison with the source's id. The printed text cannot be queried that way. A query on the type goes through `data`.

### Storage and replay ([TLDR](#storage-and-replay-deep-dive))

This section covers what the log keeps for a source record and how it is rebuilt. The log is one JSON line per record in `events.jsonl`. The writing and reading paths are shared with the other families ([Layer events](11-15-1-layer-event.md#storage-and-replay-tldr)). This section covers what differs.

[`Event.to_record`](../../src/trail/event.py#L169) writes every dataclass field except the entry, then the class name, then the entry's id and type. A `SourceEvent` has four fields of its own. **Observed:** a stored record has these keys, in this order: `id`, `timestamp`, `is_accepted`, `schema_id`, `data`, `server`, `src_path`, `resource_id`, `resource_path`, `previous_resource_id`, `previous_resource_path`, `cls`, `entry`, `entry_type`. The paths are absolute. The console shortens them when it prints.

| Stored | Computed from `data` on each access |
|---|---|
| `schema_id`, `data`, `server`, `src_path` | `verb`, [`is_local`](../../src/trail/jupyter/event.py#L98), `source_id`, `old`, `new`, `current`, `previous` |
| `resource_id`, `resource_path`, `previous_resource_id`, `previous_resource_path` | every printed row except the resource rows |
| `id`, `timestamp`, `is_accepted`, `cls`, `entry`, `entry_type` | |

The resource fields are stored because the server that resolved the path may be gone on replay, as the source comment says. [`JSONL.read`](../../src/trail/event.py#L625) replays the log when a project opens, and [`Event.from_record`](../../src/trail/event.py#L128) finds the class by its stored name and passes the stored fields to the constructor, so the links come back as stored.

**Observed results.** Reopening the project in the scenario run rebuilt all 10 source records to equal records, with their links. [`test_baseline_history_replays`](../../tests/jupyter/test_events.py#L411) does this for the captured session and checks that the replaced file links to the new and the old asset. [`test_schema_registry_and_roundtrip`](../../tests/jupyter/test_events.py#L37) does it for a `source/v1` message.

### Limits today ([TLDR](#limits-today-deep-dive))

These are the limits of today's source records. Each is labeled by how it was established. The last part lists where the code differs from the [event coverage report](../../scratch/11-12-2-event-coverage.md) and the plan.

**Current behavior: only a local file inside the project is linked.** A web address, embedded data, a path outside the server's root and a file outside the project give a record with no link. **Observed**, as shown under [finding the data file](#finding-the-data-file-tldr).

**Current behavior: the link names the file in the reference, not the file that was loaded.** The path comes from the source's own parameters. The plan lists cases where the file differs: desktop drag and drop stores only the file's name, and JupyterGIS looks for that name beside the map, so the link would point to the file at that path, which could differ from the file dragged. The shapefile loader does not read `additionalFiles`, so the link covers the named `.shp` or `.zip` only. These are the plan's statements, and none was run.

**Current behavior: the Python helper embeds local GeoJSON.** The plan says the Python API's `add_geojson_layer` reads a local file and embeds its contents instead of its path. The record then shows embedded data and no link. Linking it would need upstream to keep the original path as metadata that the loader ignores. This was not run.

**Current behavior: a source change names no layer.** A source is shared by the layers that read it. The record says which source changed. A layer that reads a replaced source gets no record of its own, since the layer did not change. **Deduction** from the emitter, which watches sources and layers separately.

**Current behavior: edits inside embedded data and detected bands are not source events.** The reference is the same, so the emitter sends nothing. **Observed** in the emitter's tests ([`reports source additions, reference changes and removals only`][ef-test-sources]).

**Current behavior: a data file replaced in place gives no source record.** The reference holds the same path, so the emitter sends nothing. A change of the file's content shows as a file record. **Deduction.**

**Current behavior: the processing tools make sources from layers.** The plan says tracking that lineage would need metadata from each tool, which is outside its scope.

**Current behavior: one change can give several records.** Each window runs its own emitter, so a change shown in two windows is reported twice. `local` is stored and selectable, and nothing drops the copies. **Deduction**, from the same wiring as the layer records ([Layer events](11-15-1-layer-event.md#limits-today-tldr)). The captured session has no pair of source records for one change.

**Current behavior: a query on `old` or `new` compares the source's id.** **Observed**, as shown under [selecting](#selecting).

**Current behavior: a failed emit leaves no trace.** The browser's `events.emit` call can be rejected, for example for an unregistered schema. The rejection reaches only the browser console ([Silent failures][ef-events-md-silent]).

**Current behavior: a new schema version is recorded without source rows.** A `source/v2` message is stored as a plain `JupyterEvent` until a class is registered for it.

#### Differences from the event coverage report and the plan

The [event coverage report](../../scratch/11-12-2-event-coverage.md) describes this record in its section on a layer's data file replaced. The code agrees with the report's record, field by field. These details are not in the report:

- The report's title says "A layer's data file replaced", and the record is about a source. A source can be shared by several layers.
- The report says that layer and source records "link the data file to the tracked asset". That holds for a tracked local file. An untracked file inside the project gets its path and `resource: untracked`, and a web address, embedded data or a file outside the project gets no link.
- An `updated` record means the reference changed. A change inside embedded data or detected bands is not a source event.
- A query on `old` or `new` selects by the source's id.

The report lists "the new message types for sources" among the changes still to propose upstream, so `source/v1` exists only in the prototype until the maintainers review it. The plan for the remaining events ([11-4-2](../plan/11-4-2-remaining-baseline-events.md)) describes this design, and the code follows it on the message fields, the old value from Yjs, the cache, the `local` flag from the first version, and the three display rules for tracked, untracked and outside files. The plan's note that Trail's layer records mark the missing link as unfinished is out of date: the link exists.

### How this was checked ([TLDR](#how-this-was-checked-deep-dive))

**Checkouts.**

- **Trail:** commit `25d97ba` on `stable`. `stable` is two commits ahead of `origin/stable`, so Trail links are relative to the working tree. The working tree held uncommitted docstrings and comments in `src/trail/jupyter`, which another session added while these notes were written. No code changed. Links and code excerpts were checked against those files at 15:33 on 15 November 2026, and line anchors move if the files change.
- **JupyterGIS emitter:** commit [`932c09ae`](https://github.com/hodczak/prototype-jupytergis-eventfeed/commit/932c09aef44cfce398c3c4db41af8e56d552d9fe) of `hodczak/prototype-jupytergis-eventfeed`, branch `stable`, equal to `origin/stable`. Its links are pinned to that commit.

**Observed results.**

- **Trail tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/jupyter/test_kernels.py tests/jupyter/test_consumer.py tests/jupyter/test_events.py tests/jupyter/test_subscription.py` gave 76 passed and 1 skipped. The skipped test is [`test_schema_copies_match_installed_eventfeed`](../../tests/jupyter/test_events.py#L289), because `jupytergis_core` is not installed in that environment.
- **Emitter tests.** `npx jest src/eventFeed --ci --coverage=false` in `python/jupytergis_core` of the clone gave 6 suites and 49 tests passed. The clone stayed clean.
- **Schema copy.** A manual `diff` of `tests/jupyter/schemas/source_v1.yaml` against the emitter's [`source_v1.yaml`][ef-schema-source] showed no difference.
- **Scenario runs.** Throwaway scripts built a project in a scratch folder with `.ontrail` holding `./` and `*.jgis`, a map, two tracked data files and one untracked. They built a [`Server`](../../src/trail/jupyter/server.py#L233) as the [`server` fixture](../../tests/jupyter/conftest.py#L130) does, with its root one folder above the project for one run, and sent messages through `Consumer.receive`: the captured session [`baseline.json`](../../tests/jupyter/fixtures/baseline.json) and nine sources of different kinds. They then reopened the project and ran the selections above. The scripts were not kept in the repo.

**Not checked.** No browser was run. The emitter's behavior comes from its source, its unit tests and its docs. The Python helper's embedding of GeoJSON, desktop drag and drop, the shapefile loader, and a change shown in two windows were not run, and their statements come from the plan or are deductions.

[ef-events-md-references]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/docs/contributor_guide/explanation/events.md#layer-and-source-references
[ef-events-md-silent]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/docs/contributor_guide/explanation/events.md#silent-failures
[ef-plugin]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/plugin.ts#L152
[ef-wire-source]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/plugin.ts#L112
[ef-same-reference]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/sources.ts#L183
[ef-source-references]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/sources.ts#L205
[ef-seed]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/sources.ts#L211
[ef-compute]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/sources.ts#L221
[ef-remember]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/sources.ts#L232
[ef-get]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/sources.ts#L246
[ef-source-reference]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/sources.ts#L123
[ef-resolve-path]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/sources.ts#L81
[ef-sanitize-url]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/sources.ts#L35
[ef-safe-query]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/sources.ts#L22
[ef-inline-size]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/sources.ts#L99
[ef-sources-observer]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/schema/src/doc.ts#L625
[ef-shared-sources-changed]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/schema/src/model.ts#L344
[ef-schema-source]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/jupytergis_core/event_schemas/source_v1.yaml
[ef-register]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/jupytergis_core/events.py#L14
[ef-test-sources]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/plugin.spec.ts#L169
[ef-test-locality]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/plugin.spec.ts#L80
[ef-test-switch]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/plugin.spec.ts#L141
[ef-test-reference]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/sources.spec.ts#L50
[ef-test-inline]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/sources.spec.ts#L120
[ef-test-cache]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/sources.spec.ts#L175
