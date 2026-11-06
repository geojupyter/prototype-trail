# Plan: the remaining events of the example timeline

The supervisor's example timeline shows a person's work across Trail and JupyterGIS. The baseline table in the [focus plan](/home/arstneio/PyCharmProjects/trail/notes/10-31-1-jupytergis-focus-events-plan.md) lists the eight behaviors it needs. Two are implemented in the eventfeed working tree, and Trail records both: switching into and out of JupyterGIS, and panning or zooming, which the [viewport plan](/home/arstneio/PyCharmProjects/trail/notes/11-1-1-viewport-moveend-plan.md) changed to report each completed movement. This plan covers the other six: initializing a Trail project, creating versus opening a map, registering a resource, loading a resource as a layer, applying or removing a filter, and selecting a map tool.

Everything below was checked by reading source on 2026-11-04: `../eventfeed` on `focus` at `fb9b71fd`, including its uncommitted focus and viewport work, and Trail on `triage` at `782a857`. No browser or server was run for this plan. The sections propose work. None of it is implemented.

## Where the events come from

Every new message uses the transport that focus and viewport events already use. JupyterGIS's browser emitter, the [event-feed plugin](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts:82), subscribes to the signals of each map's model and posts messages to Jupyter Server's `/api/events`. Jupyter Server validates each message against its registered schemas and forwards it to WebSocket subscribers. Trail's consumer receives the message and appends a record to the project's log. A message is named by its schema and verb, so `document/opened` is a `document/v1` message with the verb `opened`.

Three parts of this path matter when adding events:

- JupyterGIS's [schema registrar](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/jupytergis_core/events.py:14) registers every YAML file in `event_schemas/` when the server starts. A new kind of message needs a new YAML file and a server restart.
- JupyterGIS's [focus controller](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/focus.ts:145) gives each browser page a `frontend_id` and numbers the page's focus transitions with `focus_seq`. The mode messages proposed below reuse this counter.
- Trail's [consumer](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/consumer.py:101) accepts any `https://schema.jupytergis.org/events/` message that has a `verb` and an `object` naming `document_path`. It resolves the map and records the message. Trail records a schema it has no class for as a plain `JupyterEvent` ([consumer.py:131](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/consumer.py:131)). A class in Trail's [event.py](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/event.py) gives a record its fields and its console display. Trail's tests keep copies of the eventfeed schemas and compare them with the installed checkout ([test_events.py:268](/home/arstneio/PyCharmProjects/trail/tests/jupyter/test_events.py:268)).

## What each behavior needs

| Behavior | What exists | What this plan adds |
|---|---|---|
| Initialize a Trail project | Trail knows when it creates its metadata, but records nothing. | Trail records the project's creation once. |
| Create or open a map | JupyterGIS sends `document/opened` in both cases. Jupyter Server reports the new file as an ordinary save. | JupyterGIS flags the `opened` of a map that its own code just created. Trail shows that map as created. |
| Register a resource | Trail's `AddAssetEvent` records a tracked file, shown as `tracked` or `discovered`. | No new event. Maps join the geospatial preset. |
| Load a resource as a layer | `layer/added` names only the ID of the layer's source. | JupyterGIS reports the source's file or URL with each layer message, and source edits in a new `source/v1` message. Trail links the layer to the tracked asset. |
| Apply or remove a filter | A filter is a predicate in the layer's symbology. One filter edit sends one `layer/updated` with the old and new layer. | Trail describes the predicates added and removed. |
| Select a map tool | Not reported. JupyterGIS keeps one exclusive interaction mode per map and signals its changes. | JupyterGIS sends a new `mode/v1` message. Trail records the tool and orders it with focus events. |

One row of the focus plan's table is out of date. Its filter row, and the paragraph below that table, describe a filter panel that JupyterGIS no longer shows. The filter section explains what replaced it. The creation row is accurate. Jupyter Server's contents events do not separate creating a map from opening one either, which is why JupyterGIS has to flag creation itself.

Layers, sources and filters also need transaction locality, which the first step of the [upstream plan](/home/arstneio/PyCharmProjects/trail/notes/10-29-2-eventfeed-upstream-plan.md) proposes. Locality says whether a change was made in the reporting copy of a map or replicated from another copy. The layer section covers it.

## Initialize a Trail project

A project begins when Trail first writes its `.trail` metadata. [Trail.__init__](/home/arstneio/PyCharmProjects/trail/src/trail/trail.py:286) already computes `created`, which is true when `project.json` does not exist yet. It uses the flag only to write a preset `.ontrail`. The console decides separately whether to say "Welcome back" ([cli/__init__.py:272](/home/arstneio/PyCharmProjects/trail/src/trail/cli/__init__.py:272)). A new project's log therefore begins with the `discovered` assets of its first scan, and nothing marks where the project began.

When `created` is true, Trail appends one project-creation record after it writes `project.json` and before it refreshes its Dirs and scans for assets ([trail.py:293](/home/arstneio/PyCharmProjects/trail/src/trail/trail.py:293)). In a new project the record is then first in the log, and the first scan's registrations follow it. It holds the Trail's id, the project directory, and the `.ontrail` rules and `.offtrail` lines the project starts with. The console's `init` writes the chosen `.ontrail` before it opens the Trail ([cli/init.py:522](/home/arstneio/PyCharmProjects/trail/src/trail/cli/init.py:522)), so the rules are known by then. The record has no entry. Trail already supports records without one. Replaying it changes nothing. The console shows it with the verb `initialized`.

The record is written once per project:

- Reopening a project writes none, because `project.json` exists.
- Running `trail init` on an existing project rewrites `.ontrail` ([cli/init.py:443](/home/arstneio/PyCharmProjects/trail/src/trail/cli/init.py:443)) but does not create the project, so it writes none. Whether replaced rules deserve their own record is a decision below.
- A Trail opened without a directory persists nothing and records nothing.
- A project whose `project.json` was deleted counts as new, even if its old log survives. Trail replays that log first ([trail.py:292](/home/arstneio/PyCharmProjects/trail/src/trail/trail.py:292)), so the record follows the surviving records. Trail is early in development and handles no older state.

The checks: a new project's first record is the creation record, followed by its first scan's `discovered` assets. Reopening and re-initializing add no record. The record survives reopening.

## Create or open a map

JupyterGIS's emitter sends `document/opened` when the first widget of a map's model joins its tracker ([plugin.ts:23](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts:23), [104](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts:104)), whether the map existed or was just made. Further views of the same model send nothing. JupyterGIS's own command for a new map runs from the launcher, the command palette, and "New JupyterGIS Project" in the file browser's context menu ([plugins.ts:252](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/jgisplugin/plugins.ts:252), [302](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/jgisplugin/plugins.ts:302), [377](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/jgisplugin/plugins.ts:377)). The command creates an untitled file with `contents.newUntitled`, saves the default map into it with `contents.save`, and then opens it.

Jupyter Server cannot tell Trail the difference either. Its `contents_service/v1` schema lists a `create` action ([v1.yaml:35](/home/arstneio/micromamba/envs/eventfeed_dev/lib/python3.13/site-packages/jupyter_server/event_schemas/contents_service/v1.yaml:35)), but Jupyter Server 2.21.1 never emits it. A new file goes through `new()`, which calls `save()` ([manager.py:1037](/home/arstneio/micromamba/envs/eventfeed_dev/lib/python3.13/site-packages/jupyter_server/services/contents/manager.py:1037)), so it is reported as a `save` ([filemanager.py:1046](/home/arstneio/micromamba/envs/eventfeed_dev/lib/python3.13/site-packages/jupyter_server/services/contents/filemanager.py:1046)). A new map therefore reaches Trail as two saves and an `opened`. Trail's watchdog sees the file appear, but records it only if a rule tracks it ([session.py:896](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:896)).

The code that creates a map knows that it did. Before the new-map command opens its file, it records the new path in a small service provided through `@jupytergis/schema`, the package that already gives the event-feed plugin its map tracker. The command, the event-feed plugin and the notebook renderer are separate plugins in two packages, so state kept inside one plugin would not reach the others. When the emitter sends `opened` for a recorded path, it adds `object.created: true` and forgets the path. A recorded path expires after a short time, so an open that fails cannot flag a later, unrelated opening. The notebook renderer creates a missing map file when Python names one ([notebookrenderer.ts:227](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_lab/src/notebookrenderer.ts:227)), and it records the path the same way. The upstream plan proposes replacing that implicit creation with an explicit option, and the recording moves with it.

`document_v1.yaml` gains the optional boolean `object.created`, meaningful only with `opened`. Existing messages remain valid. Trail's document records show the verb `created` when the flag is set, and the flag can be selected in `trail feed`. The stored payload keeps `verb: opened`.

Other designs were weaker:

- A separate `created` verb would send two messages for one action. Their POSTs are independent and can arrive in either order.
- The emitter could watch the page's contents manager, whose `fileChanged` signal reports `new` for an untitled file ([index.ts:1342](/home/arstneio/PyCharmProjects/eventfeed/node_modules/@jupyterlab/services/src/contents/index.ts:1342)). This needs no change to the command. But a copy also reports `new` ([index.ts:1484](/home/arstneio/PyCharmProjects/eventfeed/node_modules/@jupyterlab/services/src/contents/index.ts:1484)), so a duplicated map opened soon after would be flagged as created. The notebook renderer creates its file with a plain save, which this route misses. The choice is a decision below.
- Lumino's `commandExecuted` signal fires as soon as a command starts ([index.ts:449](/home/arstneio/PyCharmProjects/eventfeed/node_modules/@lumino/commands/src/index.ts:449)), before the file exists. The widget is known only after `opened` was sent.
- Trail could guess from a watchdog creation shortly before an `opened`. That guess also labels a map that a script wrote and the user opened later.

Maps written by scripts, QGIS export, Save As and file-browser duplicates still arrive as `opened`. A duplicate produces a `save` for the new path and then a `copy` naming its source ([manager.py:1082](/home/arstneio/micromamba/envs/eventfeed_dev/lib/python3.13/site-packages/jupyter_server/services/contents/manager.py:1082)). Trail's consumer skips the `copy` today and could record it for maps later.

The checks: creating a map from the launcher, the palette and the file browser gives one `opened` with `created: true`. Opening an existing map gives none, and so does showing an existing map in a notebook. An open that fails leaves no flag for a later opening of the same path.

## Register a resource

Registration already has its event. [AddAssetEvent](/home/arstneio/PyCharmProjects/trail/src/trail/event.py:192) records a tracked file with its size and modification time. Its `rule` holds the `.ontrail` line that took the file, or nothing when the user tracked it explicitly, for example with the console's `ontrail PATH` ([commands.py:156](/home/arstneio/PyCharmProjects/trail/src/trail/cli/commands.py:156)). The console shows the first as `discovered` and the second as `tracked` ([render.py:257](/home/arstneio/PyCharmProjects/trail/src/trail/cli/render.py:257)). The demonstration needs nothing more.

One change helps the layer link below. Trail records each JupyterGIS message against its map, which becomes the record's entry when the map is tracked ([consumer.py:139](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/consumer.py:139)). No preset tracks maps, so a map's records usually have no entry. The last step of the [integration plan](/home/arstneio/PyCharmProjects/trail/notes/10-28-2-jupyter-submodule-plan.md:2294) adds `*.jgis`, `*.qgz` and `*.qgs` to the geospatial preset. Trail lowercases paths and patterns before matching them ([ontrail.py:136](/home/arstneio/PyCharmProjects/trail/src/trail/ontrail.py:136)), so `*.jgis` also takes `world.jGIS`.

## Load a resource as a layer

A JupyterGIS map keeps its data references apart from its layers. A source holds the reference to the data: a path, a URL or inline data. A layer names its source's ID in `parameters.source` and holds the styling. Every way of adding a layer from data adds the source first and the layer second: the layer creation form ([creationform.tsx:269](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/shared/formbuilder/creationform.tsx:269)), the layer browser ([layer-browser/index.tsx:100](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/features/layer-browser/index.tsx:100)), dragging a GeoJSON file from the desktop onto the map ([mainView.tsx:588](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx:588)), the OpenEO dialog ([commands/index.ts:795](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/commands/index.ts:795)), the processing tools ([processing/index.ts:515](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/features/processing/index.ts:515)), the empty draw layer of the Edit Features tool ([commands/index.ts:2126](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/commands/index.ts:2126)), and each click in marker mode ([mainView.tsx:3686](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx:3686)). Each `addSource` and each `addLayer` is its own Yjs transaction ([doc.ts:337](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/doc.ts:337), [doc.ts:420](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/doc.ts:420)).

Today, `layer/added` carries the layer's ID and its whole snapshot ([plugin.ts:61](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts:61)), and the snapshot names the source only by ID. The shared document reports source changes with their new values only ([doc.ts:623](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/doc.ts:623)), and the emitter does not subscribe to them. Trail therefore cannot say which file a new layer shows. Trail's layer records mark this as unfinished ([event.py:178](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/event.py:178)).

JupyterGIS loads a local path relative to the map's own folder. It joins the directory of the map's path with the source's path and reads the result through the Contents API ([tools.ts:798](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/tools.ts:798)). Its rule resolves `..`, treats a leading `/` as the server root, and never climbs above that root ([path.ts:101](/home/arstneio/PyCharmProjects/eventfeed/node_modules/@jupyterlab/coreutils/src/path.ts:101)). A path that starts with `http://` or `https://` is a URL ([tools.ts:672](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/tools.ts:672)). For example, `examples/earthquakes.jGIS` names `data/eq.geojson`, which is `examples/data/eq.geojson`.

The [source schemas](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/schema/project/sources) keep references in different fields:

| Reference | Source types and fields |
|---|---|
| A path in `path`, which may also be a URL | GeoJSON when not inline, Shapefile with its `additionalFiles`, GeoPackage raster and vector with `tables`, GeoParquet, Image |
| A URL in `url`, `urls` or `serverUrl` | Raster tiles, vector tiles, raster DEM, WMS and GeoZarr use `url`. GeoTIFF uses `urls`. OpenEO uses `serverUrl`. |
| Inline data | GeoJSON `data`, a marker's `feature`, OpenEO's `processGraph` |
| Secrets | `httpHeaders` on most types, `urlParameters` on tile sources, OpenEO's persisted `authBearer` token, and credentials inside URLs |

The Python API keeps the path or URL for most layer types. Its `add_geojson_layer` reads a local file and embeds the file's contents instead of its path ([gis_document.py:523](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_lab/jupytergis_lab/notebook/gis_document.py:523)).

### Source references in layer messages

JupyterGIS's emitter adds the referenced source to each layer message: `layer/added`, `layer/removed`, and any `layer/updated` that changes `parameters.source`. It reads the source from the shared document when it observes the layer change. For an added layer, the source is present by then, because it was added in an earlier transaction. A layer that uses an existing source, such as one saved in the map before it was opened, resolves the same way. A removed layer's source can already be gone. Removing a layer also removes its orphaned source ([model.ts:623](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/model.ts:623)), and a copy that receives both deletions in one update observes them together. The emitter therefore remembers each source's last reference, from the sources present at initial synchronization and from later source changes, and uses it when the source is missing. Each layer message then stands on its own, and Trail does not depend on the order in which messages arrive.

The reference goes in an optional `object.source`:

| Field | Content |
|---|---|
| `id` | The source's ID. |
| `type` | The source's type, such as `GeoJSONSource`. |
| `path` | The path as written in the source, for types that use `path`. `null` when that path is a URL. |
| `resolved_path` | The Contents path that JupyterGIS would load, computed with the loader's rule. `null` for URLs and inline data. |
| `tables` | A GeoPackage source's tables. |
| `urls` | Each URL from `url`, `urls`, `serverUrl`, or a `path` that is a URL, without user information, query string or fragment. |
| `inline` | For embedded data, the field that holds it and its serialized size in bytes. Never the data. |

The emitter never copies `httpHeaders`, `urlParameters`, `authBearer` or a process graph. Removing query strings also removes harmless parameters. Keeping some of them is a decision below.

### Source events

Some edits change a source without any layer message. Editing a source's path replaces the source ([doc.ts:426](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/doc.ts:426)) and leaves the layer's `parameters.source` as it was. To record such edits, JupyterGIS adds `source/v1`, with the verbs `added`, `updated` and `removed`. Like a layer message, each carries `subject`, `object.document_path` and the source's ID, so Trail's consumer routes it unchanged ([consumer.py:107](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/consumer.py:107)). It also carries the reference above, before and after the change. The shared document's source observer must first keep each change's old value, as its layer observer does ([doc.ts:592](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/doc.ts:592)). Source events are wired after the map's initial synchronization, like layer events, so a map's existing sources are not reported as added when it opens.

`source/updated` is sent only when the reference changes. Other writes replace sources often. The draw tool writes its inline features back after every drawn feature ([mainView.tsx:4125](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx:4125)). Loading a GeoZarr source stores its detected bands, in every view that loads it ([mainView.tsx:1381](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx:1381)). Neither write changes the reference, so neither sends a message. Recording drawn features is not part of this plan.

### Transaction locality

Each layer and source message also says whether the change was made in the reporting copy. The upstream plan's first step adds `local`, taken from the Yjs transaction, to layer messages. Source events carry it from the start. Without it, a change made by a collaborator or from Python reaches Trail once from every open view, and Trail cannot tell which copy made it. `local: true` means the change began in that copy. It does not prove that a person made the change by hand.

### Linking layers to assets in Trail

Trail resolves the reference to the file that JupyterGIS reads. It maps `resolved_path` to a local path with Trail's `Server.locate` ([server.py:205](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/server.py:205)). The loader's rule leaves no `..` in that path, which `Server.locate` would refuse. Trail follows the renames it has seen and looks up the asset. A message without `resolved_path`, from an older emitter, is recorded without a link.

A layer record keeps the map as its entry and stores the resource's asset ID beside it, so `trail feed` can select a resource's layers. The console line names the resource:

| Reference | The console shows |
|---|---|
| Tracked file | Its path and asset ID. |
| Untracked file inside the project | Its path, marked untracked. |
| File outside the project or the server root | The path as written. |
| URL | The URL as reported. |
| Inline data | `inline`, with its size. |

A GeoPackage source links the `.gpkg` asset and shows its tables. Trail records `source/v1` messages as their own records, linked the same way.

Trail could instead read the saved map to find a source's path, without any JupyterGIS change. The layer message arrives before the room's autosave writes the file, so Trail would have to wait for the save and match it. Trail would also have to read `.qgs` and `.qgz` maps. JupyterGIS's emitter already holds the source in memory.

Four cases stay unlinked or uncertain:

- A GeoJSON layer added from Python with a local file arrives as inline data and cannot be linked. JupyterGIS would have to keep the original path as metadata that its loader ignores. That is a decision for upstream.
- Dragging a file from the desktop stores only the file's name as the path ([mainView.tsx:594](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx:594)). JupyterGIS then loads that name beside the map, so Trail links whatever file has that name there, which may not be the file the user dragged.
- A shapefile's sidecar files are not linked. JupyterGIS's loader does not read a source's `additionalFiles`, so only the named `.shp` or `.zip` is linked.
- The processing tools create sources derived from existing layers. Recording that lineage needs metadata from each tool and is not part of this plan.

The checks: adding a GeoJSON layer from a tracked file through the creation form gives one `layer/added` whose reference Trail links to that asset. A second layer on an existing source also links. Removing a layer from Python still reports its source. Editing the source's path gives a `source/updated` with both paths, and drawing a feature gives none. A relative path such as `../data/x.geojson` in a map under `maps/` resolves to `data/x.geojson`. A tile URL, or a GeoJSON path that is a URL, arrives without its query. An OpenEO layer arrives without its token. A GeoJSON layer embedded from Python reports inline data and its size. A change made from Python arrives with `local: false`.

## Apply or remove a filter

In current JupyterGIS, a filter is a predicate in a layer's symbology. A vector layer's `parameters.symbologyState` holds grammar layers, and each grammar layer holds encoding rules. A grammar layer, and each of its rules, can carry `when` predicates, combined by `whenOp` as `all` or `any` ([symbology.json:128](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/schema/project/symbology.json:128), [435](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/schema/project/symbology.json:435), [465](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/schema/project/symbology.json:465)). A predicate tests a feature's geometry type, the presence of a field, a field's value, a comparison or a range. When JupyterGIS renders the layer, it combines a grammar layer's predicates with each rule's ([grammarToOLStyle.ts:128](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/features/layers/symbology/grammarToOLStyle.ts:128)). A feature that fails them is not drawn by that grammar layer. For example, `examples/earthquakes.jGIS` keeps only earthquakes with `mag > 5` this way. Other examples use `geometryType` predicates to style polygons and lines separately.

Users edit predicates in the grammar panel of the symbology dialog ([Grammar.tsx:379](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/features/layers/symbology/Grammar.tsx:379)). Edits stay in the dialog until OK. OK writes the whole layer once through `sharedModel.updateLayer` ([Grammar.tsx:637](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/features/layers/symbology/Grammar.tsx:637), [symbologyUtils.ts:184](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/features/layers/symbology/symbologyUtils.ts:184)). One OK therefore produces one `layer/updated` with the old and new layer. Python's symbology builders set the same predicates ([symbology.py:577](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_lab/jupytergis_lab/notebook/symbology.py:577)), and browsers observe those changes as remote updates. When the dialog edits a story segment's override, it updates the segment's `layerOverride`, not the target layer.

The focus plan's filter row describes an older design. The filter panel that plan cites ([Filter.tsx:198](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/features/filter/Filter.tsx:198)) wrote `layer.filters`, but nothing has mounted it since upstream PR #1390 replaced the symbology panels (commit `303841a1`, 2026-05-18). The 0.5-to-0.6 migration moves old `filters` into grammar predicates ([v0_5_to_v0_6.ts:74](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/migrations/v0_5_to_v0_6.ts:74)). The temporal slider still sets `layer.filters`, but through `triggerLayerUpdate`, a local signal that never reaches the shared document ([TemporalSlider.tsx:297](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/TemporalSlider.tsx:297), [model.ts:1436](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/model.ts:1436)). Playing the temporal slider therefore sends no messages. Trail's captured fixture holds a synthetic `filters` update, not a message from the current interface ([focus.jsonl:4](/home/arstneio/PyCharmProjects/trail/tests/jupyter/fixtures/focus.jsonl:4)).

JupyterGIS needs no change to report filter edits. Trail interprets them. For each `layer/updated`, Trail compares the predicates of the old and new symbology:

- It matches grammar layers and rules by their IDs, which the format keeps stable.
- It lists the predicates added and removed, by value. A changed predicate appears as one removed and one added. A change of `whenOp` is listed separately.
- Predicates on field values read as filters, for example `filter added: mag > 5`. Geometry-type predicates are listed as geometry conditions, since they select a kind of geometry.
- Predicates on a rule are listed as conditions of that rule. Predicates on a grammar layer filter the whole grammar layer.
- If the update also changes other symbology, the record says so. An update with no predicate change has no filter description.

The description is derived from the stored payload, as the layer's name is today, and its parts can be selected in `trail feed`. One OK is one message, so filter edits need no merging and are recorded at once. Until `local` arrives, Trail records the same edit once from each open view. Story-segment overrides are left for later.

A dedicated filter message from JupyterGIS would repeat what the layer message already holds, and it would tie the feed to Trail's presentation. The upstream plan's `layer/v2` would send only the changed paths of a layer. Trail would then read the predicates from those paths.

The checks: adding a `mag > 5` predicate and pressing OK gives one `layer/updated`, shown as a filter added. Removing it shows a filter removed. Switching `all` to `any` shows the combinator change. Changing only a color, or pressing OK without changes, shows no filter. Playing the temporal slider sends nothing.

## Select a map tool

JupyterGIS keeps one exclusive interaction mode per map model: `panning`, `identifying`, `marking` or `drawing` ([types.ts:62](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/types.ts:62)). A model starts in `panning` ([model.ts:1473](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/model.ts:1473)). Setting a different mode emits `modeChanged` with the new mode, and setting the current one emits nothing ([model.ts:1252](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/model.ts:1252)). Selecting the active mode's tool again returns to `panning` ([model.ts:1244](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/model.ts:1244)).

Three toolbar buttons select the modes ([toolbar/widget.tsx:169](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/workspace/toolbar/widget.tsx:169)):

| Button | Command | Mode | Effect |
|---|---|---|---|
| Identify | `jupytergis:identify`, also Accel I | `identifying` | A click shows the features under it. |
| Add Marker | `jupytergis:addMarker` | `marking` | A click adds a marker source and layer. |
| Edit Features | `jupytergis:toggleDrawFeatures`, also Accel Shift D | `drawing` | Draws into a vector layer. Without a drawable selection, it first creates an empty draw layer. |

Escape returns to `panning` through the identify command ([commands/index.ts:468](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/commands/index.ts:468), [keybindings.json:23](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/keybindings.json:23)). Some mode changes are automatic. Identify returns to `panning` when the toolbar refreshes while the selected layer cannot be identified ([commands/index.ts:426](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/commands/index.ts:426)). Drawing returns to `panning` when a layer that cannot be drawn into is selected, or when the draw layer is removed ([mainView.tsx:2609](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx:2609), [mainView.tsx:2981](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx:2981)).

The mode is a plain field of the model. It is not stored in the shared document or in awareness. Views of one model share it, collaborators never see it, and a reopened map starts in `panning`. The temporal controller is a separate toggle kept in awareness ([model.ts:1274](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/model.ts:1274)). The draw tool's choice of point, line or polygon is the view's own state ([mainView.tsx:4026](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx:4026)). Neither changes the mode, and this plan does not report them.

JupyterGIS adds `mode/v1`, named after its own term. Its verb is `changed`. Like a layer message, it carries `subject` and `object.document_path`, and its `object` also holds the new `mode` and the `previous` mode. The emitter keeps the previous mode per model, since the signal carries only the new one. It connects once per model, so two views of one model give one message per change. It sends nothing for the initial `panning`, and nothing when the map closes.

Mode messages also carry the page's `frontend_id` and a `focus_seq`, at the top level of the message as on focus messages. The focus controller numbers them from its own counter. It schedules focus transitions in a microtask ([focus.ts:83](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/focus.ts:83)), so before it numbers a mode change, it publishes any transition still pending. An activation is then numbered before the mode change it caused. Trail can order a page's activation, tool changes and deactivation, whatever order their POSTs arrive in. The counter's schema description widens from focus transitions to focus and tool transitions.

The first version reports every change, the automatic ones included, without naming a cause. A later optional `reason`, like the one on focus messages, would need each place that sets the mode to pass its cause.

Trail adds a record class for `mode/v1`. Its `mode` field shows the tool by its button label, or `none` for `panning`, beside the `previous` one. The name avoids the `tool` field that focus records already use for the application ([event.py:103](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/event.py:103)). Trail declares mode records not shared, like focus records, since a mode exists only in one page's model and collaborators never see it. Nothing in Trail reads that declaration yet. It is meant for the duplicate handling that the integration plan defers. [trail.jupyter.focus(...)](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/jupyter.py:83) includes mode records in its ordered timeline. Before the class exists, Trail already records these messages as plain `JupyterEvent`s with their payload.

A map shown in a notebook has its own model. Its modes are reported, but it has no focus events to order them against.

The checks: Identify on and off gives two changes, and Accel I does what the button does. Escape while identifying gives one change to `panning`, and Escape while panning gives none. Add Marker followed by a click also sends a layer message, which carries a source reference once step 4 below is done. Edit Features without a drawable layer also creates the draw layer. Selecting a layer that cannot be identified while identifying gives `panning`, and so does removing the draw layer. Two views of one model give one message per change. Reopening a map sends no initial message. In one page, mode and focus messages follow one counter, and an activation comes before a mode change that follows it in the same task.

## Order of work

Work continues on the existing local branches, `focus` in eventfeed and `triage` in Trail, and keeps their uncommitted work. Steps 1 and 2 change only Trail, although step 2 needs a running JupyterGIS to capture a message. Step 4 builds on step 3. Steps 5 and 6 are otherwise independent, but step 6's marker check expects step 4's source references.

1. Trail records project creation and passes the checks of its section.
2. Trail describes filter edits. A real grammar-panel OK, captured from a browser, first replaces the synthetic filter message in the fixture.
3. JupyterGIS adds `local` to layer messages, as the upstream plan's first step specifies.
4. JupyterGIS adds source references to layer messages and then `source/v1`. Trail links layers and sources to assets, and maps join the geospatial preset.
5. JupyterGIS flags created maps, and Trail shows them as created.
6. JupyterGIS sends `mode/v1` in page order, and Trail records it in the focus timeline.
7. The demonstration below runs in a browser. Its messages are checked on the WebSocket and in Trail's log after reopening, and they are kept as a fixture.

Each JupyterGIS step updates [events.md](/home/arstneio/PyCharmProjects/eventfeed/docs/contributor_guide/explanation/events.md) and the YAML schemas, adds emitter tests beside the focus and viewport tests, and extends `test_events.py`. Each new or changed schema is copied into Trail's `tests/jupyter/schemas`. The checks are `pnpm run test` and `pnpm run build` in eventfeed, `python -m pytest python/jupytergis_core/jupytergis_core/tests/test_events.py` in its development environment, the `ui-tests` Playwright cases, and `.venv/bin/python -m pytest` in Trail. New schemas register when the server starts, so the installed extension is rebuilt and the server restarted before the browser check.

These steps do not need the upstream plan's later foundations: document references, per-document ownership, sequence numbers and the delivery queue. The new fields are optional and are captured when the change is observed, so they combine with those foundations when they land.

## The demonstration

The supervisor's sequence lists each action with the record Trail should show for it. These are expected records, not observed ones.

| Action | Trail record |
|---|---|
| Start Trail in a new project | The project-creation record, then its `discovered` assets |
| Run `ontrail data/crossings.geojson` | `AddAssetEvent`, tracked |
| Create a map from the launcher | The new map's `AddAssetEvent`, discovered, then document `created` |
| The new map is shown | Document `activated` |
| Add a GeoJSON layer from `data/crossings.geojson` | Layer `added`, linked to the tracked asset |
| Add a `when` predicate and press OK, then remove it | Layer `updated` with a filter added, then with the filter removed |
| Pan or zoom | Viewport `moved` |
| Click Identify | Mode `changed` to Identify |
| Switch to a notebook and back | Document `deactivated`, then `activated` |

The new map is discovered once maps join the geospatial preset, either by the watchdog or by the consumer when the map's first message arrives ([consumer.py:197](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/consumer.py:197)). Either way, the discovery comes before the document record. Jupyter's two saves of the new file arrive before the open. Trail records them as `saved` only if the map is already tracked when they arrive.

## Decisions

1. Mark a created map with a flag on `opened`, as recommended, or with a separate `created` verb.
2. Let the code that creates a map record its path through a service in `@jupytergis/schema`, as recommended, or let the emitter watch the page's contents manager for new files. The second needs no change to the command, but it also flags duplicates and misses the notebook renderer.
3. Order mode messages with the page's focus counter, as recommended, or wait for the upstream plan's per-document sequence numbers.
4. Report mode changes without a cause at first, as recommended, or make every place that sets the mode name its cause now.
5. Remove URL query strings entirely, as recommended, or keep parameters known to be harmless.
6. Send `source/updated` only when a source's reference changes, as recommended, or also report edits inside inline data, such as drawn features.
7. Leave GeoJSON embedded from Python unlinked for now, as recommended, or ask upstream to keep its original path as metadata.
8. Record nothing when `trail init` replaces an existing project's rules, as recommended, or record the replacement.
9. Interpret filters in Trail, as recommended, or have JupyterGIS's emitter describe them.

## What remains unsupported

- Messages still lack stream and sequence numbers, so Trail records them in arrival order. Only one page's focus and mode messages can be put in order.
- Only JupyterGIS's own creation paths flag a created map.
- GeoJSON embedded from Python, desktop drag-and-drop, shapefile sidecars and processing lineage remain as the layer section describes.
- Drawn features are not recorded.
- Filter descriptions follow the current symbology format. A later format change needs a matching change in Trail.
- Automatic mode resets appear as tool changes.
- Story-segment overrides of symbology are not described.
