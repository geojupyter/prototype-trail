# Layer Added, Layer Updated, and Layer Removed in Trail

## TLDR

Add `crossings` to a map, edit a layer, or remove it. JupyterGIS, the map editor in JupyterLab, runs [`wireLayerEvents`](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L63) to report the change through Jupyter Server. Trail turns each eligible layer report into a [`LayerV1Event`](../../src/trail/jupyter/event.py#L383). A layer is the drawable item in the map. Its source describes the data it reads. The event's [`verb`](../../src/trail/jupyter/event.py#L92) says what happened to the layer.

Each section opens with an **observed** record from the [12 November coverage runs](../report/11-12-3-event-coverage-summary.md#L89). The TLDR uses the coverage summary's shorthand. The Deep Dive shows selected console rows. The skeletons outline **current behavior**.

<a id="what-the-browser-reports-tldr"></a>

### Layer Added ([Deep Dive](#layer-added-tldr))

```text
LayerV1Event(verb='added', src_path='maps/city.jGIS', layer='crossings',
             resource_path='data/crossings.geojson')
```

The browser sees a new layer ID and [looks up the layer's source](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L85) before sending the message. Trail's `Consumer` [finds the map](../../src/trail/jupyter/consumer.py#L238), [picks the record class by schema ID](../../src/trail/jupyter/consumer.py#L214), and [links the GeoJSON file](../../src/trail/jupyter/consumer.py#L322). The map is the event's [`entry`](../../src/trail/jupyter/consumer.py#L222). The GeoJSON path is its [`resource_path`](../../src/trail/jupyter/event.py#L211).

```python
found = self.resolve(server, data)
if found.reason is not None:
    record.outcome = 'skipped'
    record.reason = found.reason
    return

event_cls = JupyterEvent.registry.get(record.schema_id, JupyterEvent)
event = event_cls(...)
event.entry = found.asset
self.link(server, event)
event.apply(self._trail)
record.outcome = 'recorded'
```

### Layer Updated ([Deep Dive](#layer-updated-tldr))

```text
LayerV1Event(verb='updated', src_path='maps/city.jGIS', layer='streets',
             change=['visible False → True', 'opacity 0.7 → 0.3'])
```

The same layer ID has a before and an after value. The browser [skips an unchanged write](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L72) and otherwise [sends both values](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L102). Trail's [`changes`](../../src/trail/jupyter/event.py#L273) property compares the layer fields and then the fields inside `parameters`. Here `visible` is on the layer and `opacity` is inside `parameters`.

```python
old = obj.get('old', {})
new = obj.get('new', {})
out = field_changes(old, new, skip=('parameters',))
out += field_changes(
    old.get('parameters', {}),
    new.get('parameters', {}),
    skip=('symbologyState', 'source'),
)
```

### Layer Removed ([Deep Dive](#layer-removed-tldr))

```text
LayerV1Event(verb='removed', src_path='maps/city.jGIS', layer='crossings',
             resource_path='data/crossings_2026.geojson')
```

The editor [deletes the layer ID from the map](../../../eventfeed/packages/schema/src/doc.ts#L331). The browser [looks up the old layer's source ID](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L87) and [falls back to the cached reference](../../../eventfeed/python/jupytergis_core/src/eventFeed/sources.ts#L254) if the source has already been removed. Trail [reads the name from the old layer](../../src/trail/jupyter/event.py#L223).

```ts
const oldSource = oldValue?.parameters?.source as string | undefined;
// in the removed branch
source = references.get(oldSource);
```

### Layer Added from Python ([Deep Dive](#layer-added-from-python-tldr))

```text
LayerV1Event(verb='added', src_path='maps/city.jGIS', local=False, layer='from python')
```

A notebook calls [`GISDocument.add_geojson_layer`](../../../eventfeed/python/jupytergis_lab/jupytergis_lab/notebook/gis_document.py#L486) to [write a layer](../../../eventfeed/python/jupytergis_lab/jupytergis_lab/notebook/gis_document.py#L1324) to its copy of the shared map. The browser receives the synchronized change and [includes its transaction locality](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L98) in the usual `added` message. Trail's [`is_local`](../../src/trail/jupyter/event.py#L98) is `False` when the edit came from another copy, including another browser view.

```ts
// the document observer takes locality from this copy's transaction
local: event.transaction.local,
// the layer message includes it when supplied
...(local === undefined ? {} : { local }),
```

### Filter Added ([Deep Dive](#filter-added-tldr))

```text
LayerV1Event(verb='updated', src_path='maps/earthquakes.jGIS', layer='Earthquakes',
             filter_added='mag > 5')
```

[Saving the edited style](../../../eventfeed/packages/base/src/features/layers/symbology/symbologyUtils.ts#L162) updates the layer. Trail [extracts the before and after `symbologyState`](../../src/trail/jupyter/event.py#L246), [compares conditions within each style entry or rule](../../src/trail/jupyter/symbology.py#L180), and [prints a `filter added` row](../../src/trail/jupyter/event.py#L305) on the `updated` event.

```python
out = SymbologyDiff(old, new)
# rendering reads the comparison through the layer's symbology property
diff = self.symbology
if diff is not None:
    for change in (*diff.predicates, *diff.combinators):
        yield change.label, change.text
```

## Deep Dive

JupyterGIS commit `932c09ae` extended the layer messages. Trail commit `2c6f123` added resource links and human-readable descriptions. The code links open files in the Trail checkout and its sibling `../eventfeed` checkout.

### Layer Added ([TLDR](#layer-added-deep-dive))

```text
LayerV1Event
    src_path: maps/city.jGIS
    verb: added
    local: True
    layer_id: layer-crossings
    layer: crossings
    source: GeoJSONSource ../data/crossings.geojson
    resource_path: data/crossings.geojson
```

#### How JupyterGIS emits Layer Added

In the [recorded payload](../../tests/jupyter/fixtures/baseline.json#L169), `object.new.name` is the display name `crossings` and `object.layer_id` is the ID `layer-crossings`. Trail reads them separately through [`LayerEvent.layer`](../../src/trail/jupyter/event.py#L223) and [`LayerEvent.layer_id`](../../src/trail/jupyter/event.py#L214).

The [layer creation form](../../../eventfeed/packages/base/src/shared/formbuilder/creationform.tsx#L300) calls [`JupyterGISModel.addLayer`](../../../eventfeed/packages/schema/src/model.ts#L596) with a generated ID and a layer object. The model calls [`JupyterGISDoc.addLayer`](../../../eventfeed/packages/schema/src/doc.ts#L337), which stores the object under its ID in [`_layers`](../../../eventfeed/packages/schema/src/doc.ts#L65), the Yjs map holding the shared layer data. The write is [`_layers.set(id, value)`](../../../eventfeed/packages/schema/src/doc.ts#L339).

```ts
addLayer(id: string, value: IJGISLayer): void {
  this.transact(() => {
    this._layers.set(id, value);
  });
}
```

JupyterGIS [registers `_layersObserver` on `_layers`](../../../eventfeed/packages/schema/src/doc.ts#L83), so each write reports the ID and the [before and after values](../../../eventfeed/packages/schema/src/doc.ts#L605). When only an after value exists, the emitter [chooses `added`](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L69) and puts the layer in [`object.new`](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L103).

The coverage commit [added a source lookup for newly added layers](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L85). The emitter passes the ID from `newValue.parameters.source` to [`SourceReferences.get`](../../../eventfeed/python/jupytergis_core/src/eventFeed/sources.ts#L246), which returns a data reference for the layer's source. In the [example source reference](../../tests/jupyter/fixtures/baseline.json#L181), `src-crossings` has `path: ../data/crossings.geojson` relative to the map directory. The reference builder [resolves that path against the map's location](../../../eventfeed/python/jupytergis_core/src/eventFeed/sources.ts#L141) to get `data/crossings.geojson` relative to the server root. The emitter includes it as [`object.source`](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L104).

[`eventFeedPlugin.activate`](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L156) uses [`wiredModels`](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L175) to give two views of the same model a single set of handlers. It waits for the model's [`initialSyncReady`](../../../eventfeed/packages/schema/src/doc.ts#L94) promise, then [seeds the source cache](../../../eventfeed/python/jupytergis_core/src/eventFeed/sources.ts#L211) and [connects the layer, source, and viewport handlers](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L203). The [comment above the wait](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L190) explains that initial synchronization would otherwise report existing layers as additions.

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

The plugin [sends the message through JupyterLab's event service](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L160) and logs a browser warning if sending fails. The prototype [registers](../../../eventfeed/python/jupytergis_core/jupytergis_core/events.py#L14) the [`layer/v1` schema](../../tests/jupyter/schemas/layer_v1.yaml#L1) so Jupyter Server can validate the message before relaying it. Trail's [`subscribe`](../../src/trail/jupyter/subscription.py#L14) coroutine reads the feed at the [server's WebSocket endpoint](../../src/trail/jupyter/server.py#L288) and [hands the message to `Consumer.receive`](../../src/trail/jupyter/subscription.py#L51).

<a id="the-record-classes-tldr"></a>
<a id="making-the-record-tldr"></a>

#### How Trail records Layer Added

The Trail session [creates a `Consumer`](../../src/trail/jupyter/session.py#L37) to receive feed messages. [`Consumer.receive`](../../src/trail/jupyter/consumer.py#L96) [extracts the schema ID from JSON](../../src/trail/jupyter/consumer.py#L115) and [builds a `Message`](../../src/trail/jupyter/consumer.py#L136) from the remaining data, the receipt timestamp, and the server URL. For a layer message it [calls `route`](../../src/trail/jupyter/consumer.py#L151), which sets the [outcome and event ID](../../src/trail/jupyter/consumer.py#L228).

[`Event`](../../src/trail/event.py#L59) supplies the record ID and timestamp. [`JupyterEvent`](../../src/trail/jupyter/event.py#L36) adds the server address, map path, and message data. It [registers subclasses by schema ID](../../src/trail/jupyter/event.py#L62). [`LayerEvent`](../../src/trail/jupyter/event.py#L200) adds the layer properties and resolved resource fields, and [`LayerV1Event`](../../src/trail/jupyter/event.py#L383) associates them with `layer/v1`. [`Consumer.route`](../../src/trail/jupyter/consumer.py#L166) uses that registry to [instantiate the selected class](../../src/trail/jupyter/consumer.py#L214).

```python
def route(
        self,
        server: Server,
        record: Message,
) -> None:
    """Record a JupyterGIS message as an event, or skip it with a reason."""
    data = record.data
    ...
    found = self.resolve(server, data)
    record.src_path = found.src_path
    if found.reason is not None:
        record.outcome = 'skipped'
        record.reason = found.reason
        return
    ...
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
    ...
    event.apply(self._trail)
    ...
```

<a id="resolve-finding-the-map"></a>

- [`Consumer.resolve`](../../src/trail/jupyter/consumer.py#L238) reads `object.document_path` and calls [`Server.locate`](../../src/trail/jupyter/server.py#L298) to resolve it under the server's root. It skips unsaved or outside-root maps and [checks excluded, outside-project, and missing paths](../../src/trail/jupyter/consumer.py#L281). Trail can [track an eligible map and remember its asset](../../src/trail/jupyter/consumer.py#L300). If an eligible file remains untracked, its `entry` is empty. Trail [reuses the reporting client's asset](../../src/trail/jupyter/consumer.py#L259) on later messages, including [after a rename](../../tests/jupyter/test_consumer.py#L136).
- The Trail coverage commit [added `Consumer.link`](../../src/trail/jupyter/consumer.py#L313). The [`LayerEvent.source`](../../src/trail/jupyter/event.py#L234) property reads `object.source` and [parses the reference](../../src/trail/jupyter/source.py#L39). [`Consumer.locate_source`](../../src/trail/jupyter/consumer.py#L357) resolves the path and [`Consumer.resource`](../../src/trail/jupyter/consumer.py#L334) looks up the asset when there is a local path. The consumer [stores `resource_path` and `resource_id`](../../src/trail/jupyter/consumer.py#L322). The [tracked-resource test](../../tests/jupyter/test_consumer.py#L255) verifies that those fields survive reopening the log. An untracked file inside the project gets a path and [prints `resource: untracked`](../../src/trail/jupyter/event.py#L367). Inline data and URL-only sources can print a source description with empty resource fields.
- The [`JupyterEvent` fallback](../../src/trail/jupyter/consumer.py#L214) keeps an accepted but unrecognized JupyterGIS schema as a generic record, as shown by the [unknown-schema replay test](../../tests/jupyter/test_events.py#L190). A layer schema gets layer-specific rows once a class is registered for that ID.

<a id="storage-and-replay-tldr"></a>

#### How the log keeps Layer Added

[`JupyterEvent.apply`](../../src/trail/jupyter/event.py#L114) appends the live record to `trail.events`. [`JSONL.append`](../../src/trail/event.py#L722) writes it to [`events.jsonl`](../../src/trail/event.py#L614). [`Event.to_record`](../../src/trail/event.py#L169) preserves the message data, the recorded class name, the map's entry ID, and the [stored resource fields](../../src/trail/jupyter/event.py#L207). [`JSONL.read`](../../src/trail/event.py#L625) uses [`Event.from_record`](../../src/trail/event.py#L128) to rebuild the event. The retained resource fields work after Jupyter Server has gone away. Trail computes the [`layer` name](../../src/trail/jupyter/event.py#L223) and [`change` rows](../../src/trail/jupyter/event.py#L311) from the retained message.

### Layer Updated ([TLDR](#layer-updated-deep-dive))

```text
LayerV1Event
    src_path: maps/city.jGIS
    verb: updated
    layer_id: layer-streets
    layer: streets
    change: visible False → True
    change: opacity 0.7 → 0.3
```

<a id="what-the-browser-reports-deep-dive"></a>

#### How the browser detects Layer Updated

The [recorded `layer-streets` update](../../scratch/11-12-2-event-coverage.md#L86) retains the same key and changes the layer's values. In the Layers panel, the [visibility toggle](../../../eventfeed/packages/base/src/workspace/panels/components/layers.tsx#L820) calls [`updateLayer`](../../../eventfeed/packages/schema/src/doc.ts#L343). The [opacity slider](../../../eventfeed/packages/base/src/workspace/panels/components/layers.tsx#L838) calls [`updateObjectParameters`](../../../eventfeed/packages/schema/src/doc.ts#L376), which merges the new parameters and updates the layer at the same ID.

Yjs, the library that synchronizes copies of the map, reports the changed keys to [`JupyterGISDoc`](../../../eventfeed/packages/schema/src/doc.ts#L57). Its [`_layersObserver`](../../../eventfeed/packages/schema/src/doc.ts#L592) reads `change.oldValue` and [looks up and copies the current value under the same key](../../../eventfeed/packages/schema/src/doc.ts#L605). It [reads `local` from the transaction](../../../eventfeed/packages/schema/src/doc.ts#L610) that applied the change in the reporting document.

```ts
private _layersObserver(events: Y.YEvent<any>[]): void {
  const changes: Array<{
    id: string;
    oldValue: IDict;
    newValue: IJGISLayer;
    local: boolean;
  }> = [];
  let needEmit = false;
  events.forEach(event => {
    event.keys.forEach((change, key) => {
      if (!needEmit) {
        needEmit = true;
      }
      const raw = event.target.toJSON()[key];
      changes.push({
        id: key,
        oldValue: change.oldValue,
        newValue: raw === undefined ? undefined : JSONExt.deepCopy(raw),
        local: event.transaction.local,
      });
    });
  });
  needEmit = changes.length === 0 ? true : needEmit;
  if (needEmit) {
    this._layersChanged.emit({ layerChange: changes });
  }
}
```

The editor model exposes that signal as [`sharedLayersChanged`](../../../eventfeed/packages/schema/src/model.ts#L333). [`wireLayerEvents`](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L63) loops over `layerChange` and [chooses the verb from the two values](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L69). A new key gives `added`, a deleted key gives `removed`, and both values give `updated`. The coverage commit [added the equality check for updates](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L72) because pressing **OK** in a dialog can write the same layer back. The [emitter test](../../../eventfeed/python/jupytergis_core/src/eventFeed/__tests__/plugin.spec.ts#L80) covers this alongside all three verbs.

```ts
export const wireLayerEvents = (
  { widget, clientId, emit }: IEventWiringProps,
  references: SourceReferences,
): void => {
  widget.model.sharedLayersChanged.connect((_, change) => {
    for (const { id, oldValue, newValue, local } of change.layerChange ?? []) {
      const verb = !oldValue ? 'added' : !newValue ? 'removed' : 'updated';
      // trail: no-op - pressing OK in a dialog without edits writes the same layer again.
      if (
        verb === 'updated' &&
        JSONExt.deepEqual(
          oldValue as unknown as JSONObject,
          newValue as unknown as JSONObject,
        )
      ) {
        continue;
      }
      ...
    }
  });
};
```

The [emitted `object`](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L99) carries `document_path`, `layer_id`, and the complete layer values as [`old` and `new`](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L102). If `parameters.source` [switches to a different ID](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L88) and the reference is available, the browser includes `object.source`, as the [source-switch test](../../../eventfeed/python/jupytergis_core/src/eventFeed/__tests__/plugin.spec.ts#L141) shows. Changing the file behind the same source ID produces a [Source Replaced record](11-15-6-source-event.md).

#### How Trail prints Layer Updated

The Trail coverage commit [added `LayerEvent.changes`](../../src/trail/jupyter/event.py#L273) to describe an update. The [property](../../src/trail/jupyter/event.py#L273) compares the top-level fields and then `parameters`, leaving the style and source ID to dedicated rows. [`field_changes`](../../src/trail/jupyter/event.py#L332) skips equal values and [chooses the wording](../../src/trail/jupyter/event.py#L349). Scalars get before and after values, `set to`, or `removed`. A nested field can produce `filters changed`. The [layer-field test](../../tests/jupyter/test_events.py#L513) checks those descriptions.

```python
@property
def changes(self) -> list[str]:
    """A phrase for each other field an update changed."""
    if self.verb != 'updated':
        return []
    obj = self.data.get('object', {})
    old = obj.get('old', {})
    new = obj.get('new', {})
    out = field_changes(old, new, skip=('parameters',))
    out += field_changes(
        old.get('parameters', {}),
        new.get('parameters', {}),
        skip=('symbologyState', 'source'),
    )
    return out
```

| Field | Before | After | Console row |
| --- | --- | --- | --- |
| `visible` | `False` | `True` | `change: visible False → True` |
| `parameters.opacity` | `0.7` | `0.3` | `change: opacity 0.7 → 0.3` |

**Observed** The [opacity run](../../scratch/maps.md#L43) shows the records for an opacity edit and explains how typing can produce multiple updates. **Planned** The [delivery TODO](../../src/trail/jupyter/consumer.py#L225) calls for grouping edits and relating them to saves. Today Trail [appends eligible layer messages as they arrive](../../src/trail/jupyter/consumer.py#L226).

### Layer Removed ([TLDR](#layer-removed-deep-dive))

```text
LayerV1Event
    src_path: maps/city.jGIS
    verb: removed
    layer_id: layer-crossings
    layer: crossings
    source: GeoJSONSource ../data/crossings_2026.geojson
    resource_path: data/crossings_2026.geojson
```

The [remove command](../../../eventfeed/packages/base/src/commands/index.ts#L2253) calls [`JupyterGISModel.removeLayer`](../../../eventfeed/packages/schema/src/model.ts#L623) with the selected ID. [`JupyterGISDoc.removeLayer`](../../../eventfeed/packages/schema/src/doc.ts#L331) deletes the key from `_layers`. The observer supplies the old layer and the emitter [uses its source ID](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L87). The coverage commit [added a cache fallback](../../../eventfeed/python/jupytergis_core/src/eventFeed/sources.ts#L254) so [`SourceReferences.get`](../../../eventfeed/python/jupytergis_core/src/eventFeed/sources.ts#L246) can still return the reference after an orphaned source is removed.

[`LayerEvent.layer`](../../src/trail/jupyter/event.py#L223) reads the name from [`object.old` in the removal payload](../../tests/jupyter/fixtures/baseline.json#L483).

```python
@property
def layer(self) -> str | None:
    """The layer name after the change, or before it for a removal."""
    obj = self.data.get('object', {})
    if self.verb == 'removed':
        value = obj.get('old', {})
    else:
        value = obj.get('new', {})
    out = value.get('name')
    return out
```

**Observed** `layer-crossings` pointed to `src-crossings` both [when added](../../tests/jupyter/fixtures/baseline.json#L163) and [when removed](../../tests/jupyter/fixtures/baseline.json#L477). In between, a [source update](../../tests/jupyter/fixtures/baseline.json#L214) changed the file to `crossings_2026.geojson`. The removal therefore links the file it used at removal time through the same resolution and log path as [Layer Added](#layer-added-tldr).

<a id="limits-today-tldr"></a>

### Layer Added from Python ([TLDR](#layer-added-from-python-deep-dive))

```text
LayerV1Event
    src_path: maps/city.jGIS
    verb: added
    local: False
    layer: from python
    source: GeoJSONSource inline data 374 B
```

A notebook opens the map through [`GISDocument`](../../../eventfeed/python/jupytergis_lab/jupytergis_lab/notebook/gis_document.py#L156), JupyterGIS's Python API. [`add_geojson_layer`](../../../eventfeed/python/jupytergis_lab/jupytergis_lab/notebook/gis_document.py#L486) [reads the local GeoJSON into the source's `data` field](../../../eventfeed/python/jupytergis_lab/jupytergis_lab/notebook/gis_document.py#L523) because the browser may lack access to the kernel's filesystem. The API [inserts the source](../../../eventfeed/python/jupytergis_lab/jupytergis_lab/notebook/gis_document.py#L1309) and [writes the layer](../../../eventfeed/python/jupytergis_lab/jupytergis_lab/notebook/gis_document.py#L1324) to the shared document. JupyterGIS [builds the inline reference](../../../eventfeed/python/jupytergis_core/src/eventFeed/sources.ts#L171) with the field name and its [serialized UTF-8 size](../../../eventfeed/python/jupytergis_core/src/eventFeed/sources.ts#L99), 374 bytes in the [captured Python addition](../../tests/jupyter/fixtures/baseline.json#L912).

The change starts in the kernel's copy of the map. JupyterGIS added code to [capture the observer's transaction locality](../../../eventfeed/packages/schema/src/doc.ts#L610) and [include it in the message](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L98). Since the browser receives the change from another copy, it reports `local: false`. Trail added [`is_local`](../../src/trail/jupyter/event.py#L98) to read that value for [display](../../src/trail/jupyter/event.py#L111) and [selection](../../src/trail/jupyter/event.py#L203). An absent locality value yields `None`.

```python
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

**Observed** In the [two-view run](../../scratch/maps.md#L43), a browser edit yielded one `local: True` record and one `local: False` record. A notebook edit yielded two `local: False` layer records. Independent synchronized views can report the same action, including a [map tab and a notebook output showing that map](../../bugs/duplicate-map-records.ipynb).

**Current behavior** Trail [deduplicates retries by `(server URL, stream, seq)`](../../src/trail/jupyter/consumer.py#L197). Each observing model emits its own [layer message with client ID and locality](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L92). **Planned** The [origin TODO](../../src/trail/jupyter/event.py#L317) calls for dropping reports from replica views once Trail can identify them reliably. **Deduction** Keeping only `local=True` would also drop the observed Python edits.

### Filter Added ([TLDR](#filter-added-deep-dive))

```text
LayerV1Event
    src_path: maps/earthquakes.jGIS
    verb: updated
    layer: Earthquakes
    filter added: mag > 5
```

The style editor [adds a condition to the entry's `when` list](../../../eventfeed/packages/base/src/features/layers/symbology/Grammar.tsx#L241). The condition restricts the style entry to features whose `mag` exceeds 5. The dialog [includes `when` in the saved `symbologyState`](../../../eventfeed/packages/base/src/features/layers/symbology/Grammar.tsx#L663) and [`saveSymbology`](../../../eventfeed/packages/base/src/features/layers/symbology/symbologyUtils.ts#L162) [writes the changed layer](../../../eventfeed/packages/base/src/features/layers/symbology/symbologyUtils.ts#L184). The browser sends both layer values through the [Layer Updated](#layer-updated-tldr) path. [`LayerEvent.symbology`](../../src/trail/jupyter/event.py#L246) supplies the before and after style values to [`SymbologyDiff`](../../src/trail/jupyter/symbology.py#L130).

In the [captured after value](../../tests/jupyter/fixtures/symbology.json#L184), the grammar entry's `when` list includes this condition.

```json
{"type": "fieldCompare", "field": "mag", "op": ">", "value": 5}
```

[`SymbologyDiff.conditions`](../../src/trail/jupyter/symbology.py#L144) groups conditions by [scope](../../src/trail/jupyter/symbology.py#L10), a pair of grammar entry ID and rule ID. An entry's own conditions use `None` as the rule ID. [`Conditions.of`](../../src/trail/jupyter/symbology.py#L41) [encodes each predicate as sorted-key JSON](../../src/trail/jupyter/symbology.py#L13). The coverage commit [added a diff for added and removed predicates](../../src/trail/jupyter/symbology.py#L174) that [subtracts the old and new counts within each scope](../../src/trail/jupyter/symbology.py#L180). The edited scope has zero copies of `mag > 5` before and one after, so `new - old` yields one addition. Another grammar entry already had the same condition and its count stays at one.

```python
@cached_property
def predicates(self) -> tuple[PredicateChange, ...]:
    """The predicates added or removed in each scope."""
    empty = Conditions()
    changes = []
    scopes = dict.fromkeys([*self.before, *self.after])
    for scope in scopes:
        old = Counter(self.before.get(scope, empty).when)
        new = Counter(self.after.get(scope, empty).when)
        for verb, texts in (('removed', old - new), ('added', new - old)):
            changes.extend(
                PredicateChange(
                    verb=verb,
                    scope=scope,
                    predicate=json.loads(text),
                )
                for text in texts.elements()
            )
    out = tuple(changes)
    return out
```

[`PredicateChange.label`](../../src/trail/jupyter/symbology.py#L76) gives this condition the label `filter added`. Rule and geometry conditions get their own labels. [`PredicateChange.text`](../../src/trail/jupyter/symbology.py#L89) formats the comparison as `mag > 5`. The diff also [detects a switch between matching all or any conditions](../../src/trail/jupyter/symbology.py#L195) and [checks the style with the conditions stripped](../../src/trail/jupyter/symbology.py#L240) to spot other edits such as a recolor. [`LayerEvent._repr_items`](../../src/trail/jupyter/event.py#L305) yields those rows for display.

**Observed in fixture-based tests** The first edit gives [`filter added: mag > 5`](../../tests/jupyter/test_events.py#L487). The second gives [`filter added: has felt` and `combinator changed: all -> any`](../../tests/jupyter/test_events.py#L494). Removing both then gives two `filter removed` rows. A style-only edit gives [`symbology: other changes`](../../tests/jupyter/test_events.py#L499). Using `cls=LayerEvent has_filter_change=true` in [`trail.events.select`](../../src/trail/collection.py#L75) finds [predicate or combinator changes](../../src/trail/jupyter/event.py#L289), including rule and geometry conditions.

**Verification** On 17 November, the Trail event and consumer tests ran against the working tree (65 passed, one skipped because `jupytergis_core` was absent). The [baseline replay test](../../tests/jupyter/test_events.py#L410) feeds captured messages through the consumer, reopens the log, and compares the rebuilt records. Browser behavior here comes from the emitter source and the saved captures.
