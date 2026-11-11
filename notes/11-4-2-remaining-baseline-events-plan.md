# Plan for the remaining events in the example timeline

## TLDR

Trail should give someone one readable history of their project, from setting it up to working on a map. **Trail** keeps the project log. **JupyterLab** is the browser workspace where the user opens notebooks and maps. **JupyterGIS** is the map editor inside that workspace. It reports actions to **Jupyter Server**, which passes them to Trail.

### What we know so far ([Deep Dive](#what-we-know-so-far-tldr))

The local prototype already reports switches to and from a map tab and completed map movements, including zooms. The rest of the [example timeline](/home/arstneio/PyCharmProjects/trail/notes/10-31-1-jupytergis-focus-events-plan.md) builds on that prototype. The current behavior comes from reading the code. The proposed changes and full browser demonstration are pending.

| Action | What Trail shows today | What the plan would change |
|---|---|---|
| [Start a new Trail project](#starting-a-new-trail-project-deep-dive) | The files found in the first scan. | Add one initialization entry before the file entries. |
| [Create or open a map](#telling-map-creation-from-opening-a-map-deep-dive) | Both appear as opening a map. | Show `created` when JupyterGIS makes the file. |
| [Track a file](#tracking-files-used-in-the-project-deep-dive) | Files appear as `discovered` or `tracked`. | Include map files in the default rules for new geospatial projects. |
| [Add data to a map](#showing-where-a-map-layer-gets-its-data-deep-dive) | The addition is logged without the file or web address supplying the data. | Show where the data comes from, link tracked files, and report changes to the reference. |
| [Change which features are shown](#explaining-what-a-map-filter-changed-deep-dive) | A general update for that set of features. | Describe the filter that was added or removed, such as “magnitude greater than 5.” |
| [Choose a map tool](#recording-which-map-tool-the-user-selected-deep-dive) | No entry for the switch. | Show the tool change in order with switches to and from the map tab. |

### How a map action reaches the project log ([Deep Dive](#how-a-map-action-reaches-the-project-log-tldr))

**Current behavior.** When a user opens a map, JupyterGIS sends a message about that action to Jupyter Server. The server checks the message format and forwards it to Trail. Trail uses the map's path to associate the message with the project and saves an eligible message as a log entry. Trail's console can then display that entry.

**Proposed change.** Use the same route for the additional actions. Some changes need new messages from the editor. Others, such as explaining a filter edit, need only a clearer description of data Trail already receives.

### Choosing what appears in the console ([Deep Dive](#choosing-what-appears-in-the-console-tldr))

**Current behavior.** Trail's `feed` command narrows which new entries appear in the live console. Its `events` command can search the saved entries. For example, a user could view only map events and still query the rest of the project history later.

**Recommendation.** Use the display filter when the console is busy. Controls over which entries get saved are a separate decision because they change the history available to later queries and replay.

### Starting a new Trail project ([Deep Dive](#starting-a-new-trail-project-tldr))

**Current behavior.** Trail can tell when it is setting up a new project. Its log starts with the files it discovers, with no entry explaining when the project began or what tracking rules it started with.

**Proposed change.** Write one `initialized` entry before the first file discovery. Include the project folder and the rules used to track or ignore files. Reopening the project or rerunning setup would leave that single entry in place. Verify its position in the log and that it survives reopening.

**Follow-up.** If we need to see when Trail was running, record startup and graceful shutdown as separate events. They would happen per run and need a clear definition of when a run begins.

### Telling map creation from opening a map ([Deep Dive](#telling-map-creation-from-opening-a-map-tldr))

**Current behavior.** Creating a map from JupyterLab's launcher and opening one from the file browser both produce an `opened` message. Jupyter Server reports the new file through ordinary saves, which are also used for existing files.

**Proposed change.** Have JupyterGIS mark the first opening of a map it just created. Trail would display that entry as `created`. This gives the log one entry for the action and uses the part of the editor that knows it created the file. A map exported by another tool or duplicated in the file browser would still appear as `opened`. Check new and existing maps, including a failed open followed by a later attempt.

### Tracking files used in the project ([Deep Dive](#tracking-files-used-in-the-project-tldr))

**Current behavior.** Trail shows a file as `discovered` when a rule picks it up and as `tracked` when the user adds it explicitly. The default geospatial rules include data files but leave out the project files that store maps.

**Proposed change.** Add map files from JupyterGIS and the desktop map editor QGIS to those rules. New Trail projects that select the geospatial preset would then discover these maps automatically, and existing projects could add the same file patterns. Confirm that a map opening is attached to the discovered map file and that explicit tracking still works. An opt-in preset that tracks all files remains a separate decision.

### Showing where a map layer gets its data ([Deep Dive](#showing-where-a-map-layer-gets-its-data-tldr))

A **layer** is a set of things drawn on the map, such as earthquake locations. Its **data source** can be a file, a web address, or data stored directly inside the map.

**Current behavior.** Trail can log that a layer was added, but the message identifies its source only by an internal ID. The data location can also change without a layer edit. If two browser views report the same edit, Trail cannot tell which copy originated it.

**Proposed layer and source messages.** Include the data reference when a layer is added or removed, or when it switches sources. Keep the last known reference for removals because the editor can delete the source with the layer. Send separate source entries when the reference itself changes, for example when a layer starts reading another file. Edits to individual points or shapes would stay out of those entries. Web addresses would omit credentials and query parameters, and embedded data would be described by size without copying its contents.

**Proposed origin and file linking.** Include whether the change originated in the map copy reporting it. Trail would keep the map as the record's main file and link the data file too if Trail tracks it. For example, adding crossings to a city map would let someone find that event through either the map or the crossings file. The editor knows the source when the change happens, so Trail would not need to wait for the map to save.

**Acceptance checks.** Add two layers using the same tracked file and confirm both links. Change the data path and check that the old and new paths appear. Test a path relative to the map, an edit made from Python, and an embedded source.

### Explaining what a map filter changed ([Deep Dive](#explaining-what-a-map-filter-changed-tldr))

**Current behavior.** A map filter decides which features get drawn. If the user adds a rule to show only earthquakes above magnitude 5 and clicks OK, JupyterGIS sends the layer's settings before and after the edit. Trail shows a layer update without explaining the rule that changed.

**Proposed change.** Compare those settings in Trail and show the conditions added or removed. A change that only adjusts a color should still appear as a layer update without a filter description. This step can use the existing editor message. Before implementing it, capture an actual edit from the current interface to replace the synthetic example used by the tests.

### Recording which map tool the user selected ([Deep Dive](#recording-which-map-tool-the-user-selected-tldr))

**Current behavior.** JupyterGIS lets the user pan, inspect a feature with Identify, add a marker, or draw features. It knows when the active tool changes. Trail receives no entry for the switch.

**Proposed change.** Report the previous and new tools. For example, after the user opens a map tab and chooses Identify, Trail should show the tab becoming active before the switch to Identify. Reuse the browser page's existing event counter to preserve that order even if the messages arrive in reverse. Test selection, Escape to return to panning, and two views of the same map sharing one tool state.

### What to build first ([Deep Dive](#what-to-build-first-tldr))

1. In Trail, add the initialization entry and verify its place at the beginning of the project log.
2. Capture a real filter edit in the browser and teach Trail to describe the edit. The first two steps change only Trail.
3. In JupyterGIS, report whether a layer edit originated in the copy that sent the message.
4. Build on step 3 to report source references and source changes. In Trail, link the data files and update the geospatial preset.
5. Mark the maps JupyterGIS creates and have Trail display them as `created`.
6. Add tool changes to the map-tab timeline. The tool work can proceed independently, but checking the data reference of a new marker requires step 4.
7. Run the browser demonstration, inspect the messages sent to Trail, and verify the saved entries after reopening the project. Keep the captured messages for regression tests.

### What the finished history should show ([Deep Dive](#what-the-finished-history-should-show-tldr))

**Expected result.** Start with a fresh Trail project using the updated geospatial preset. Use a file of street crossings as the map data.

| Action | Expected Trail history after implementation |
|---|---|
| Start Trail in the new project | Project initialized, then the files found in the first scan. |
| Explicitly track the crossings file | Crossings file tracked. |
| Create a map and let its tab become active | Map file discovered, map created, then map tab activated. |
| Add the crossings file to the map | Layer added, with a link to the tracked crossings file. |
| Add a rule for which crossings to draw, then remove it | Filter added, then filter removed. |
| Move or zoom the map | Completed map movement. |
| Choose Identify | Tool switched to Identify. |
| Switch to a notebook, then back | Map tab deactivated, then activated. |

Reopen the project and check that the history persists.

### Decisions still open for review ([Deep Dive](#decisions-still-open-for-review-tldr))

- **Map creation.** Prefer a flag on the existing open message, supplied by the code that creates the map. A separate message could arrive out of order. Watching for a new file would also pick up duplicates.
- **Tool changes.** Use the browser page's existing counter and report automatic resets. Recording the reason for each reset would require changes wherever the editor sets a tool.
- **Data sources.** Omit all URL query parameters and report changes to the data reference. Retaining the original filename when Python embeds data would require a separate change to JupyterGIS.
- **Trail records.** Use Trail to interpret map-filter edits. Leave changes to an existing project's setup rules outside the initialization record.

### What this plan would still leave out ([Deep Dive](#what-this-plan-would-still-leave-out-tldr))

Only map-tab and tool changes within one browser page would gain explicit ordering. Map creation would cover the editor's own creation actions. Sources embedded by Python would lack an original file link, and dragging a file from the desktop can leave only its filename. The proposal also leaves shapefile sidecars, the history of how derived layers were made, individual drawing edits, and filter overrides in map presentations outside the log. Automatic resets would appear as ordinary tool changes.

## Deep Dive

### What we know so far ([TLDR](#what-we-know-so-far-deep-dive))

The current behavior below is based on source inspected on 2026-11-04: `../eventfeed` on `focus` at `fb9b71fd`, including its uncommitted focus and viewport work, and Trail on `triage` at `782a857`. The [viewport plan](/home/arstneio/PyCharmProjects/trail/notes/11-1-1-viewport-moveend-plan.md) describes the change to one message per completed movement. The document revision cross-checked the central distinctions against the local source. The browser, server, and proposed acceptance checks have yet to be run.

### How a map action reaches the project log ([TLDR](#how-a-map-action-reaches-the-project-log-deep-dive))

**Current behavior.** A map's model holds its state and signals changes. The browser's event-feed plugin listens to those signals and posts messages to Jupyter Server's `/api/events`. Jupyter Server validates each message against a registered schema that defines its fields. It forwards the message over a WebSocket, an open connection over which Trail receives events. Trail's consumer appends eligible messages to the project log. The schema identifies the message type, and the verb identifies the action. For example, `document/opened` means an `opened` message under the `document/v1` schema. [Event-feed plugin](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L82)

- JupyterGIS's schema registrar registers each YAML file under `event_schemas/` at server startup. [events.py](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/jupytergis_core/events.py#L14)
- The focus controller tracks which map is active. It gives each browser page a `frontend_id` and numbers that page's focus transitions with `focus_seq`. [focus.ts](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/focus.ts#L145)
- Trail's consumer accepts messages under `https://schema.jupytergis.org/events/` with a `verb` and an `object` naming `document_path`. It resolves the map and records eligible messages. A schema without a specialized class falls back to a plain `JupyterEvent`. A specialized class supplies fields and a console display. The tests compare copies of the eventfeed schemas with the installed checkout. [Consumer](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/consumer.py#L101), [record classes](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/event.py), [schema check](/home/arstneio/PyCharmProjects/trail/tests/jupyter/test_events.py#L268)

**Proposed change.** Use this transport for the new messages. Register each new schema through the existing registrar and restart the server before testing it. Reuse the page's focus counter to order tool changes with focus transitions.

### Choosing what appears in the console ([TLDR](#choosing-what-appears-in-the-console-deep-dive))

**Current behavior.** The Trail console already has `feed [EXPRESSION]` for filtering which new records it displays and `events [EXPRESSION]` for querying the saved log. [Console commands](/home/arstneio/PyCharmProjects/trail/src/trail/cli/commands.py#L282)

> My concern is that now we are tracking too many events, maybe we want a way to toggle which events are tracked? Would that be a good feature?  Maybe we'll have a `trail filter` which filters events?

**Recommendation.** Use the existing display filter for a busy console. Add controls over what gets saved only if storage, privacy, or another recording requirement calls for them. That choice would affect which events are available for later queries and replay, so it can be evaluated separately from this timeline.

### Starting a new Trail project ([TLDR](#starting-a-new-trail-project-deep-dive))

**Current behavior.**

A project begins when Trail first writes its `.trail` metadata. [Trail.__init__](/home/arstneio/PyCharmProjects/trail/src/trail/trail.py#L286) already computes `created`, which is true when `project.json` does not exist yet. It uses the flag only to write a preset `.ontrail`. The console decides separately whether to say "Welcome back" ([cli/__init__.py:272](/home/arstneio/PyCharmProjects/trail/src/trail/cli/__init__.py#L272)). A new project's log therefore begins with the `discovered` assets of its first scan, and nothing marks where the project began.

**Proposed change.** When `created` is true, append one project-creation record after writing `project.json` and before refreshing the directory index and scanning for assets. Store the Trail ID, project directory, initial `.ontrail` rules, and initial `.offtrail` lines. The console's `init` already writes the selected `.ontrail` before opening Trail, so the rules are available at this point. Use Trail's existing support for records without an asset or directory entry. The record should have no effect on replay and display as `initialized`. [Insertion point](/home/arstneio/PyCharmProjects/trail/src/trail/trail.py#L293), [console initialization](/home/arstneio/PyCharmProjects/trail/src/trail/cli/init.py#L522)

- Reopening a project should leave the log without another initialization record because `project.json` exists.
- Running `trail init` on an existing project currently rewrites `.ontrail`. The recommendation is to omit a record for that replacement. [Existing-project behavior](/home/arstneio/PyCharmProjects/trail/src/trail/cli/init.py#L443)
- Opening Trail without a directory should still persist nothing.
- If `project.json` is deleted and the old log survives, the existing creation test treats the project as new. Trail currently replays the surviving log first, so the proposed initialization record would follow those records. [Replay order](/home/arstneio/PyCharmProjects/trail/src/trail/trail.py#L292)

**Acceptance checks.** Verify that a new project's first record is `initialized`, followed by the first scan's `discovered` records. Verify that reopening preserves the record and that neither reopening nor re-initializing adds another one.

> INitializing a Trail event is a worthwhile addition, but I was thinking, should we also have an event for Trail startup? Whenever the program starts again, we have an emit--might be useful for events? And when we have a controlled stop signal, should we have a TrailShutdown signal or something?

**Follow-up proposal.** Startup and graceful shutdown could be useful as separate session records if the log needs to show when Trail was actively running. First define what starts a session, for example a console run versus a Python object opening the project. A shutdown record would confirm a controlled exit. A crash or forced kill could leave only the startup record. Keep these events outside the initialization step.

### Telling map creation from opening a map ([TLDR](#telling-map-creation-from-opening-a-map-deep-dive))

**Current behavior.**

The map tracker keeps track of open map views, called widgets. When it registers the first view of a map model, the emitter sends `document/opened` for either a new or existing file. Further views of that same model do not send another `opened`. [Open message](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L23), [one subscription per model](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L104)

The new-map command is available from the launcher, the command palette, and the file browser's “New JupyterGIS Project” context menu item. It creates an untitled file with `contents.newUntitled`, writes the default map with `contents.save`, then opens it. [Command](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/jgisplugin/plugins.ts#L252), [launcher](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/jgisplugin/plugins.ts#L302), [file browser](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/jgisplugin/plugins.ts#L377)

Jupyter Server cannot tell Trail the difference either. Its `contents_service/v1` schema lists a `create` action ([v1.yaml:35](/home/arstneio/micromamba/envs/eventfeed_dev/lib/python3.13/site-packages/jupyter_server/event_schemas/contents_service/v1.yaml#L35)), but Jupyter Server 2.21.1 never emits it. A new file goes through `new()`, which calls `save()` ([manager.py:1037](/home/arstneio/micromamba/envs/eventfeed_dev/lib/python3.13/site-packages/jupyter_server/services/contents/manager.py#L1037)), so it is reported as a `save` ([filemanager.py:1046](/home/arstneio/micromamba/envs/eventfeed_dev/lib/python3.13/site-packages/jupyter_server/services/contents/filemanager.py#L1046)). A new map therefore reaches Trail as two saves and an `opened`. Trail's watchdog sees the file appear, but records it only if a rule tracks it ([session.py:896](/home/arstneio/PyCharmProjects/trail/src/trail/session.py#L896)).

The notebook renderer displays a map inside a Python notebook. It creates a missing map file when Python names one. Separately, a file-browser duplicate produces a `save` for the new path and then a `copy` naming its source. Trail's consumer skips the `copy`. [Notebook renderer](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_lab/src/notebookrenderer.ts#L227), [Jupyter Server copy](/home/arstneio/micromamba/envs/eventfeed_dev/lib/python3.13/site-packages/jupyter_server/services/contents/manager.py#L1082)

**Proposed change.** Before opening a newly created map, have the new-map command and notebook renderer register its path with a small service in `@jupytergis/schema`. This package already provides the map tracker and is available to the command, emitter, and renderer, which are separate plugins in two packages. When the emitter sends `opened` for a registered path, add `object.created: true` and consume the registration. Clear it on a known open failure and expire abandoned registrations after a short time. If the upstream plan makes notebook creation explicit, move the registration to that creation path.

Extend `document_v1.yaml` with an optional Boolean `object.created` for `opened`. Keep the payload verb `opened`. Have Trail display `created` when the flag is true and make the flag selectable in `feed`. Older messages should remain valid.

**Alternatives considered.**

- A separate `created` verb would send two messages for one action. Their POSTs are independent and can arrive in either order.
- The emitter could watch the page's contents manager, whose `fileChanged` signal reports `new` for an untitled file ([index.ts:1342](/home/arstneio/PyCharmProjects/eventfeed/node_modules/@jupyterlab/services/src/contents/index.ts#L1342)). This needs no change to the command. But a copy also reports `new` ([index.ts:1484](/home/arstneio/PyCharmProjects/eventfeed/node_modules/@jupyterlab/services/src/contents/index.ts#L1484)), so a duplicated map opened soon after would be flagged as created. The notebook renderer creates its file with a plain save, which this route misses.
- Lumino's `commandExecuted` signal fires as soon as a command starts ([index.ts:449](/home/arstneio/PyCharmProjects/eventfeed/node_modules/@lumino/commands/src/index.ts#L449)), before the file exists. The widget is known only after `opened` was sent.
- Trail could guess from a watchdog creation shortly before an `opened`. That guess also labels a map that a script wrote and the user opened later.

**Limits after implementation.** Files made by scripts, QGIS export, Save As, or file-browser duplication would still arrive as ordinary `opened` events. A future change could record `copy` separately.

**Acceptance checks.** Create a map from the launcher, palette, and file browser and verify one `opened` with `created: true` for each. Open an existing map in a tab and in a notebook and verify no creation flag. Verify that a failed open cannot flag a later unrelated open of the same path.

### Tracking files used in the project ([TLDR](#tracking-files-used-in-the-project-deep-dive))

**Current behavior.**

Registration already has its event. [AddAssetEvent](/home/arstneio/PyCharmProjects/trail/src/trail/event.py#L192) records a tracked file with its size and modification time. Its `rule` holds the `.ontrail` line that took the file, or nothing when the user tracked it explicitly, for example with the console's `ontrail PATH` ([commands.py:156](/home/arstneio/PyCharmProjects/trail/src/trail/cli/commands.py#L156)). The console shows the first as `discovered` and the second as `tracked` ([render.py:257](/home/arstneio/PyCharmProjects/trail/src/trail/cli/render.py#L257)).

When a map is tracked, Trail associates each JupyterGIS record with the map as its entry. The bundled geospatial preset has rules for data files such as GeoJSON and GeoPackage but omits map project files. Maps can already be tracked explicitly or with a custom rule. Trail lowercases paths and patterns before matching. [Record entry](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/consumer.py#L139), [geospatial preset](/home/arstneio/PyCharmProjects/trail/src/trail/ontrail/geospatial.txt), [pattern matching](/home/arstneio/PyCharmProjects/trail/src/trail/ontrail.py#L136)

**Proposed change.** Add `*.jgis`, `*.qgz`, and `*.qgs` to the geospatial preset, as specified in the last step of the integration plan. `*.jgis` will also match `world.jGIS`. The preset applies to projects that select it at initialization. Existing projects can add the patterns to their `.ontrail` rules. [Integration plan](/home/arstneio/PyCharmProjects/trail/notes/10-28-2-jupyter-submodule-plan.md#steps)

> Yes, make the presets more realistic: make them actually cover the extensions one might be interested. Also maybe add an `all` preset, that covers all assets?

**Follow-up proposal.** A broader review of preset coverage could check which common files each preset misses. An opt-in `all` preset is a separate choice. Define its scope and how it interacts with `.offtrail` before adding it.

**Acceptance checks.** Verify that `ontrail PATH` still records an explicit registration as `tracked`. In a new project with the updated geospatial preset, verify that opening a matching map discovers the file and associates the map message with that asset.

### Showing where a map layer gets its data ([TLDR](#showing-where-a-map-layer-gets-its-data-deep-dive))

**Current behavior.**

A JupyterGIS map keeps its data references apart from its layers. A source holds the reference to the data: a path, a URL or inline data. A layer names its source's ID in `parameters.source` and holds the styling. Every way of adding a layer from data adds the source first and the layer second: the layer creation form ([creationform.tsx:269](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/shared/formbuilder/creationform.tsx#L269)), the layer browser ([layer-browser/index.tsx:100](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/features/layer-browser/index.tsx#L100)), dragging a GeoJSON file from the desktop onto the map ([mainView.tsx:588](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx#L588)), the OpenEO dialog ([commands/index.ts:795](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/commands/index.ts#L795)), the processing tools ([processing/index.ts:515](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/features/processing/index.ts#L515)), the empty draw layer of the Edit Features tool ([commands/index.ts:2126](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/commands/index.ts#L2126)), and each click in marker mode ([mainView.tsx:3686](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx#L3686)). Each `addSource` and each `addLayer` is its own Yjs transaction ([doc.ts:337](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/doc.ts#L337), [doc.ts:420](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/doc.ts#L420)).

Today, `layer/added` carries the layer's ID and its whole snapshot ([plugin.ts:61](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L61)), and the snapshot names the source only by ID. The shared document reports source changes with their new values only ([doc.ts:623](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/doc.ts#L623)), and the emitter does not subscribe to them. Trail therefore cannot say which file a new layer shows. Trail's layer records mark this as unfinished ([event.py:178](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/event.py#L178)).

JupyterGIS loads a local path relative to the map's own folder. It joins the directory of the map's path with the source's path and reads the result through the Contents API ([tools.ts:798](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/tools.ts#L798)). Its rule resolves `..`, treats a leading `/` as the server root, and never climbs above that root ([path.ts:101](/home/arstneio/PyCharmProjects/eventfeed/node_modules/@jupyterlab/coreutils/src/path.ts#L101)). A path that starts with `http://` or `https://` is a URL ([tools.ts:672](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/tools.ts#L672)). For example, `examples/earthquakes.jGIS` names `data/eq.geojson`, which is `examples/data/eq.geojson`.

The [source schemas](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/schema/project/sources) keep references in different fields:

| Reference | Source types and fields |
|---|---|
| A path in `path`, which may also be a URL | GeoJSON when not inline, Shapefile with its `additionalFiles`, GeoPackage raster and vector with `tables`, GeoParquet, Image |
| A URL in `url`, `urls` or `serverUrl` | Raster tiles, vector tiles, raster DEM, WMS and GeoZarr use `url`. GeoTIFF uses `urls`. OpenEO uses `serverUrl`. |
| Inline data | GeoJSON `data`, a marker's `feature`, OpenEO's `processGraph` |
| Secrets | `httpHeaders` on most types, `urlParameters` on tile sources, OpenEO's persisted `authBearer` token, and credentials inside URLs |

The Python API keeps the path or URL for most layer types. Its `add_geojson_layer` reads a local file and embeds the file's contents instead of its path ([gis_document.py:523](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_lab/jupytergis_lab/notebook/gis_document.py#L523)).

#### Source references in layer messages

**Proposed change.** Attach a reference to `layer/added` and `layer/removed`. Include it in `layer/updated` when `parameters.source` changes. Read the source from the shared map document when observing the layer change. A newly added source is normally available from the earlier transaction, and an existing source can be read the same way.

JupyterGIS removes an orphaned source with its layer, and another copy can observe both deletions together. Seed a cache of each source's last reference after initial synchronization, update it on source changes, and use it when the live source is missing. That gives Trail the resource reference without requiring a separate source message to arrive first. [Layer removal](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/model.ts#L623)

Add the reference as an optional `object.source`.

| Field | Content |
|---|---|
| `id` | The source's ID. |
| `type` | The source's type, such as `GeoJSONSource`. |
| `path` | The path as written in the source, for types that use `path`. `null` when that path is a URL. |
| `resolved_path` | The Contents path that JupyterGIS would load, computed with the loader's rule. `null` for URLs and inline data. |
| `tables` | A GeoPackage source's tables. |
| `urls` | Each URL from `url`, `urls`, `serverUrl`, or a `path` that is a URL, without user information, query string or fragment. |
| `inline` | For embedded data, the field that holds it and its serialized size in bytes. Never the data. |

Exclude `httpHeaders`, `urlParameters`, `authBearer`, and the contents of a process graph. The proposed URL fields omit user information, fragments, and all query parameters, including harmless ones. The [design decisions](#decisions-still-open-for-review-tldr) record the alternative of keeping known harmless parameters.

#### Source events

**Current behavior.** Editing a source's path replaces the source and leaves the layer's `parameters.source` unchanged. The draw tool writes inline features back after every drawn feature. Loading a GeoZarr source writes its detected bands in every view that loads it. [Source update](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/doc.ts#L426), [draw tool](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx#L4125), [GeoZarr loading](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx#L1381)

**Proposed change.** Add `source/v1` with the verbs `added`, `updated`, and `removed`. Include `subject`, `object.document_path`, the source ID, and the before and after references where applicable. The existing Trail consumer can route this shape. Extend the source observer to keep the old value, as the layer observer already does. Wire the source observer after initial synchronization to avoid reporting existing sources as newly added. [Trail routing](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/consumer.py#L107), [layer observer](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/doc.ts#L592)

Emit `source/updated` only for a change to the data reference. Treat serialized inline size as display metadata when deciding whether the reference changed. This keeps feature edits from generating source events even when the byte count changes. GeoZarr band metadata also stays outside the comparison.

#### Transaction locality

**Current behavior.** JupyterGIS uses Yjs to synchronize copies of a map. A transaction is a set of changes that Yjs applies together. One edit can reach Trail from multiple open copies of a map, and existing layer messages omit which copy originated it.

**Proposed change.** Add `local` from the Yjs transaction to layer messages in step 3, as specified in the first step of the upstream plan. Include it in `source/v1` from its first version. `local: true` means the change originated in the copy that reported it. A local change can come from either a user action or local code. [Upstream plan](/home/arstneio/PyCharmProjects/trail/notes/10-29-2-eventfeed-upstream-plan.md)

#### Linking layers to assets in Trail

**Current behavior.** Trail's `Server.locate` maps a server-relative path to a local path and rejects paths containing `..`. Trail also remembers renames reported by Jupyter Server. [Path resolution](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/server.py#L205)

**Proposed change.** Resolve `resolved_path` with `Server.locate`, follow known renames, and look up the resource asset. The JupyterGIS loader's normalized path should contain no `..`. A message from an older emitter without `resolved_path` should still be recorded without a resource link.

Keep the map as the layer record's entry and store the resource's asset ID separately, so `feed` can select layers that use a resource. For GeoPackage, link the `.gpkg` asset and show its tables. Store `source/v1` messages as their own records and link them by the same rule.

| Reference | Proposed console display |
|---|---|
| Tracked file | Its path and asset ID. |
| Untracked file inside the project | Its path, marked untracked. |
| File outside the project or the server root | The path as written. |
| URL | The URL as reported. |
| Inline data | `inline`, with its size. |

**Alternative considered.**

Trail could instead read the saved map to find a source's path, without any JupyterGIS change. The layer message arrives before the room's autosave writes the file, so Trail would have to wait for the save and match it. Trail would also have to read `.qgs` and `.qgz` maps. The browser already has the source in memory when it observes the layer change.

**Limits after implementation.**

- The Python helper embeds local GeoJSON as inline data. Linking it would require upstream to retain its original path as metadata that the loader ignores.
- Desktop drag-and-drop currently stores only the filename. JupyterGIS looks for that name beside the map. The proposed link would point to the file at that path, which could differ from the file dragged. [Drop handler](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx#L594)
- The shapefile loader does not read `additionalFiles`. The proposed link covers the named `.shp` or `.zip` only.
- The processing tools create sources from existing layers. Tracking that lineage would require metadata from each tool and remains outside this plan.

**Acceptance checks.**

- Add a GeoJSON layer from a tracked file through the creation form, and then add another layer that uses the same source. Verify that each `layer/added` links to the file. Remove a layer from Python and check that its source is still included.
- Change a source's path and verify a `source/updated` with both paths. Draw a feature and verify no source update. For a map under `maps/`, check that `../data/x.geojson` resolves to `data/x.geojson`.
- Verify that tile and GeoJSON URLs arrive without queries, that an OpenEO layer omits its token, and that GeoJSON embedded from Python reports its inline field and size.
- Make a change from Python and verify `local: false` in the browser's message.

### Explaining what a map filter changed ([TLDR](#explaining-what-a-map-filter-changed-deep-dive))

**Current behavior.**

Symbology controls what a map layer draws and how it looks. For layers containing features such as points or polygons, `parameters.symbologyState` stores groups of styling rules called grammar layers. A grammar layer and each of its rules can have `when` predicates, which test a feature's geometry type, the presence of a field, a value, a comparison, or a range. `whenOp` says whether `all` or `any` of the predicates must match. [Symbology schema](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/schema/project/symbology.json#L128), [rule conditions](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/schema/project/symbology.json#L435), [grammar layer conditions](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/schema/project/symbology.json#L465)

JupyterGIS combines the predicates from a grammar layer and its rule when drawing a feature. For example, `examples/earthquakes.jGIS` uses `mag > 5` to draw only earthquakes above magnitude 5. Other examples use `geometryType` predicates to style polygons and lines separately. [Renderer](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/features/layers/symbology/grammarToOLStyle.ts#L128)

Users edit predicates in the grammar panel of the symbology dialog ([Grammar.tsx:379](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/features/layers/symbology/Grammar.tsx#L379)). Edits stay in the dialog until OK. OK writes the whole layer once through `sharedModel.updateLayer` ([Grammar.tsx:637](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/features/layers/symbology/Grammar.tsx#L637), [symbologyUtils.ts:184](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/features/layers/symbology/symbologyUtils.ts#L184)). One OK therefore produces one `layer/updated` with the old and new layer. Python's symbology builders set the same predicates ([symbology.py:577](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_lab/jupytergis_lab/notebook/symbology.py#L577)), and browsers observe those changes as remote updates. When the dialog edits a story segment's override, it updates the segment's `layerOverride`, not the target layer.

The focus plan's filter row describes an older design. The filter panel that plan cites ([Filter.tsx:198](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/features/filter/Filter.tsx#L198)) wrote `layer.filters`, but nothing has mounted it since upstream PR #1390 replaced the symbology panels (commit `303841a1`, 2026-05-18). The 0.5-to-0.6 migration moves old `filters` into grammar predicates ([v0_5_to_v0_6.ts:74](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/migrations/v0_5_to_v0_6.ts#L74)). The temporal slider still sets `layer.filters`, but through `triggerLayerUpdate`, a local signal that never reaches the shared document ([TemporalSlider.tsx:297](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/TemporalSlider.tsx#L297), [model.ts:1436](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/model.ts#L1436)). Playing the temporal slider therefore sends no messages. Trail's captured fixture holds a synthetic `filters` update, not a message from the current interface ([focus.jsonl:4](/home/arstneio/PyCharmProjects/trail/tests/jupyter/fixtures/focus.jsonl#L4)).

**Proposed change.** Derive the filter description in Trail from the old and new symbology in `layer/updated`. The existing JupyterGIS message already carries the data needed for this step.

- Match grammar layers and rules by their IDs, which the format keeps stable.
- List the predicates added and removed, by value. A changed predicate appears as one removed and one added. A change of `whenOp` is listed separately.
- Describe field-value predicates as filters, for example `filter added: mag > 5`. Describe geometry-type predicates as geometry conditions.
- List predicates on a rule as conditions of that rule. Predicates on a grammar layer filter the whole grammar layer.
- Mention any other symbology changes in the same record. Omit the filter description when no predicate changes.

Derive the description from the stored payload as Trail already does for the layer's name. Make its parts selectable in `feed`. Record the update when it arrives. Each OK already writes the whole layer once, so this step needs no merging. Before the `local` field lands, multiple views can still report the same edit. Story-segment overrides remain out of scope.

**Alternative considered.** A dedicated filter message would duplicate the layer payload and couple the emitter to Trail's presentation. If the upstream plan adds `layer/v2` with only changed paths, Trail should read predicate changes from those paths.

**Acceptance checks.** Capture a real grammar-panel update before replacing the synthetic fixture. Add and remove `mag > 5` and verify that Trail describes each operation. Switch `all` to `any` and verify the combinator change. Verify that a color-only change or OK without changes yields no filter description, and that playing the temporal slider sends no layer message.

### Recording which map tool the user selected ([TLDR](#recording-which-map-tool-the-user-selected-deep-dive))

**Current behavior.**

JupyterGIS keeps one exclusive interaction mode per map model: `panning`, `identifying`, `marking` or `drawing` ([types.ts:62](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/types.ts#L62)). A model starts in `panning` ([model.ts:1473](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/model.ts#L1473)). Setting a different mode emits `modeChanged` with the new mode, and setting the current one emits nothing ([model.ts:1252](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/model.ts#L1252)). Selecting the active mode's tool again returns to `panning` ([model.ts:1244](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/model.ts#L1244)).

Three toolbar buttons select the modes ([toolbar/widget.tsx:169](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/workspace/toolbar/widget.tsx#L169)):

| Button | Command | Mode | Effect |
|---|---|---|---|
| Identify | `jupytergis:identify`, also Accel I | `identifying` | A click shows the features under it. |
| Add Marker | `jupytergis:addMarker` | `marking` | A click adds a marker source and layer. |
| Edit Features | `jupytergis:toggleDrawFeatures`, also Accel Shift D | `drawing` | Draws into a vector layer. Without a drawable selection, it first creates an empty draw layer. |

Escape returns to `panning` through the identify command ([commands/index.ts:468](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/commands/index.ts#L468), [keybindings.json:23](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/keybindings.json#L23)). Some mode changes are automatic. Identify returns to `panning` when the toolbar refreshes while the selected layer cannot be identified ([commands/index.ts:426](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/commands/index.ts#L426)). Drawing returns to `panning` when a layer that cannot be drawn into is selected, or when the draw layer is removed ([mainView.tsx:2609](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx#L2609), [mainView.tsx:2981](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx#L2981)).

The mode is a plain field of the model. It is not stored in the shared document or in awareness. Views of one model share it, collaborators never see it, and a reopened map starts in `panning`. The temporal controller is a separate toggle kept in awareness ([model.ts:1274](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/model.ts#L1274)). The draw tool's choice of point, line or polygon is the view's own state ([mainView.tsx:4026](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx#L4026)). Neither changes the mode. The browser event feed currently emits no message for a mode change.

**Proposed change.** Emit `mode/v1` with verb `changed`, `subject`, `object.document_path`, and the current and previous modes in `object.mode` and `object.previous`. Keep the previous mode per model because `modeChanged` supplies only the new value. Connect once per model so two views do not report a change twice. Skip initial `panning` and closing the map. Report automatic resets as changes. An optional `reason` would require each caller that sets the mode to supply its cause.

Include `frontend_id` and `focus_seq` at the top level, as on focus messages. Use the focus controller's existing counter for both event types and broaden the schema description accordingly. The controller currently schedules focus transitions in a microtask. Before numbering a mode change, have it emit any pending transition so the activation that led to the change gets the earlier number. Trail can then sort events within one page regardless of POST arrival order. [Focus scheduling](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/focus.ts#L83)

Add a Trail record class and include it in `trail.jupyter.focus(...)`. Display both modes by their toolbar labels, using `none` for `panning`. Use the field name `mode` because the existing focus record's `tool` names the application. Mark mode records as unshared for the future duplicate handling deferred by the integration plan. The generic consumer could already store a valid mode message as a plain `JupyterEvent` if the emitter sent one. [Existing focus field](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/event.py#L103), [existing focus timeline](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/jupyter.py#L83)

**Limits after implementation.** A notebook map has its own model. Its mode changes should be reported, with no focus transitions for that model to order them against. Leave the temporal controller toggle and point, line, or polygon selection outside the mode feed.

**Acceptance checks.**

- Toggle Identify twice and verify two changes. Verify that Accel I matches the button. Press Escape while identifying and verify one change to `panning`. Press it again and verify no event.
- Click while in Add Marker and verify the layer event. After step 4, verify its source reference. Select Edit Features without a drawable layer and verify that it also creates an empty draw layer.
- Verify automatic return to `panning` when selecting a layer that cannot be identified or when removing the draw layer.
- Verify one event per change across two views of one model and no initial event when reopening. Check that a page's focus and mode events share a counter and that an activation precedes a mode change it causes in the same task.

### What to build first ([TLDR](#what-to-build-first-deep-dive))

**Proposed sequence.** Continue on the existing local branches, `focus` in eventfeed and `triage` in Trail, preserving their working changes. The first two steps change only Trail. Step 2 needs a running JupyterGIS to capture a real message. Step 4 depends on step 3. Step 6 can be implemented independently of step 4, but its marker check needs step 4's source references. Step 5 is independent.

1. Record project initialization in Trail and run its acceptance checks.
2. Capture a grammar-panel OK in a browser, replace the synthetic filter fixture, and implement Trail's filter descriptions.
3. Add transaction locality (`local`) to JupyterGIS layer messages.
4. Add source references to layer messages and then `source/v1`. Link both to assets in Trail and add map extensions to the geospatial preset.
5. Flag newly created maps in JupyterGIS and display them as `created` in Trail.
6. Send `mode/v1` in page order and include it in Trail's focus timeline.
7. Run the browser demonstration, check the WebSocket messages and the reopened Trail log, and save the captured messages as a fixture.

The proposed sequence can use the current message transport. It does not depend on the upstream plan's later work on document references, per-document ownership, sequence numbers, or the delivery queue. The new fields are optional and captured when the change is observed.

**Validation to run.** For each JupyterGIS step, update the event documentation and YAML schemas, add emitter tests beside the focus and viewport tests, and extend `test_events.py`. Copy each new or changed schema to Trail's `tests/jupyter/schemas`. Rebuild the installed extension and restart the server before checking browser behavior. [Event documentation](/home/arstneio/PyCharmProjects/eventfeed/docs/contributor_guide/explanation/events.md)

- In eventfeed, run `pnpm run test` and `pnpm run build`, run `python -m pytest python/jupytergis_core/jupytergis_core/tests/test_events.py` in the development environment, and run the relevant `ui-tests` Playwright cases.
- In Trail, run `.venv/bin/python -m pytest`.

### What the finished history should show ([TLDR](#what-the-finished-history-should-show-deep-dive))

These are the expected log records after implementation. The browser demonstration is pending.

| Action | Expected Trail record |
|---|---|
| Start Trail in a new project | The project `initialized` record, then its `discovered` assets |
| Run `ontrail data/crossings.geojson` | `AddAssetEvent`, tracked |
| Create a map from the launcher | The new map's `AddAssetEvent`, discovered, then document `created` |
| The new map is shown | Document `activated` |
| Add a GeoJSON layer from `data/crossings.geojson` | Layer `added`, linked to the tracked asset |
| Add a `when` predicate and press OK, then remove it | Layer `updated` with a filter added, then with the filter removed |
| Pan or zoom | Viewport `moved` |
| Click Identify | Mode `changed` to Identify |
| Switch to a notebook and back | Document `deactivated`, then `activated` |

With the updated preset, either the watchdog or the consumer should discover the map before its document record. The consumer already scans an unregistered map path when it resolves a message. Jupyter Server performs both saves before the map opens. Trail records a save only if it is already tracking the map when that save arrives. [Consumer scan](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/consumer.py#L197)

### Decisions still open for review ([TLDR](#decisions-still-open-for-review-deep-dive))

The detailed proposal uses the recommendations below. The alternatives remain open for review.

1. Mark a created map with a flag on `opened`, as recommended, or with a separate `created` verb.
2. Let the code that creates a map record its path through a service in `@jupytergis/schema`, as recommended, or let the emitter watch the page's contents manager for new files. The second needs no change to the command, but it also flags duplicates and misses the notebook renderer.
3. Order mode messages with the page's focus counter, as recommended, or wait for the upstream plan's per-document sequence numbers.
4. Report mode changes without a cause at first, as recommended, or make every place that sets the mode name its cause now.
5. Remove URL query strings entirely, as recommended, or keep parameters known to be harmless.
6. Send `source/updated` only when a source's reference changes, as recommended, or also report edits inside inline data, such as drawn features.
7. Leave GeoJSON embedded from Python unlinked for now, as recommended, or ask upstream to keep its original path as metadata.
8. Record nothing when `trail init` replaces an existing project's rules, as recommended, or record the replacement.
9. Interpret filters in Trail, as recommended, or have JupyterGIS's emitter describe them.

### What this plan would still leave out ([TLDR](#what-this-plan-would-still-leave-out-deep-dive))

These are limits of the proposed result.

- General event delivery still uses arrival order. The proposed focus counter orders focus and mode messages within one browser page.
- The creation flag covers JupyterGIS's own creation paths.
- GeoJSON embedded from Python, desktop drag-and-drop, shapefile sidecars and processing lineage remain as the layer section describes.
- Drawn features are not recorded.
- Filter descriptions follow the current symbology format. A later format change needs a matching change in Trail.
- Automatic mode resets appear as tool changes.
- Story-segment overrides of symbology are not described.
