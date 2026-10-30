# The JupyterGIS event feed, from Trail's side

Matt Fisher has built a prototype event feed for JupyterGIS. When someone opens a map or changes a
layer in JupyterGIS's interface, JupyterGIS publishes a small json message. Any program that holds
the Jupyter server's token can listen to these messages. This report follows one action from the
click to the point where Trail should record it. It then describes what Trail needs to do to use
these events.

Jupyter Server is the Python process that `jupyter lab` starts. It serves files and kernels to the
browser, and it owns the events API that the event feed uses. I tested that API on 2026-10-20
against a real Jupyter Server with JupyterGIS's two event schemas registered. I did not run
JupyterLab or JupyterGIS. Everything about the browser side comes from reading JupyterGIS's code at
commit 6c3d5727, the commit your clone in ../eventfeed has checked out. Matt pushed two more
commits on 2026-10-20, ending at 413d069b. They change one of the quirks below.

## Where the event feed comes from

Matt's code is on the `prototyping` branch of geojupyter/prototype-jupytergis-eventfeed. That
repository is a copy of JupyterGIS's main branch from late August 2026, version 0.16.2, with the
event feed added on top. The branch is open as draft PR #4, "Emit events for GUI operations", and
you are a requested reviewer. Matt describes the PR as a reference for a future implementation. No
JupyterGIS release has the event feed yet. The latest release is 0.16.8, from 2026-10-15. For now,
Trail will only see events from someone running the prototype.

JupyterGIS sends these events so that other tools can track and repeat work done in its GUI. The
page you linked, docs/contributor_guide/explanation/events.md, says Trail's design is the reason for
the feature. The Trail initiative had planned a Trail-side JupyterLab extension that would watch
JupyterGIS's internal signals. It also asked whether GeoJupyter should define a small, stable event
interface instead (initiatives/001-trail/index.md in geojupyter/initiatives). The event feed is a
prototype of that interface. With it, Trail does not need its own JupyterLab extension. Trail
listens to Jupyter Server instead.

JupyterGIS means to send events only for GUI actions, not for changes made through its Python API.
The section on quirks explains why some Python changes may still arrive as GUI events.

The 2026-10-15 collaboration café made consuming these events your first priority
(compass/meeting-notes/20261015-collaboration-cafe/index.md). At the 2026-10-20 JupyterGIS sync,
Martin Renou pointed to notebook-link/metrics, a JupyterLab extension that sends UI events through
the same events API (compass/meeting-notes/20261020-jupytergis-syc/index.md). Its events record
which command ran and which tab became active. That could later help with tracking tool switching,
an item on Trail's roadmap.

## Following one layer from the click to Trail

Say a user has map.jGIS open in JupyterLab and adds a layer named streets. A .jGIS file is
JupyterGIS's json document for one map. It holds the map's layers and the sources they draw from.
JupyterGIS can also open QGIS project files, .qgz and .qgs, as maps, and those send the same events.

In the browser, JupyterGIS keeps each open map in a document model, the object that holds the map's
layers and sources. Adding the layer makes the model fire a signal called sharedLayersChanged.
JupyterGIS's event-feed plugin, a piece of TypeScript that runs in the browser, listens for that
signal. It sends an HTTP POST to /api/events on Jupyter Server.

Jupyter Server has an event bus from the jupyter_events library. An event bus takes events from many
senders and hands each one to every listener. Each kind of event must first be registered with a
JSON Schema. JupyterGIS registers its two schemas when Jupyter Server starts. Jupyter Server checks
the event against its schema, then passes the event to every program connected to the WebSocket
/api/events/subscribe. JupyterLab in the browser is one listener. Trail would be another. Trail
talks to Jupyter Server, never to JupyterGIS.

If Jupyter Server rejects an event, the event-feed plugin only prints a warning in the browser's
developer console. Trail never learns of it.

Trail would receive this message. The values are made up.

```json
{
  "schema_id": "https://schema.jupytergis.org/events/layer/v1",
  "subject": "1234",
  "verb": "added",
  "object": {
    "document_path": "map.jGIS",
    "layer_id": "L1",
    "new": {
      "type": "VectorLayer",
      "name": "streets",
      "visible": true,
      "parameters": {"source": "S1", "opacity": 1}
    }
  }
}
```

JupyterGIS has two event types today.

| schema_id | verb | object |
|---|---|---|
| `https://schema.jupytergis.org/events/document/v1` | opened, closed | document_path |
| `https://schema.jupytergis.org/events/layer/v1` | added, updated, removed | document_path, layer_id, old, new |

The `subject` is a client ID from Yjs, the library that keeps the browser's copy of a map in sync
with Jupyter Server. A browser gets a new client ID each time it loads a map. So the subject
identifies one open copy of a map, not a person. `document_path` is relative to the folder the
Jupyter server serves. The schema allows null for a map that has no file. In practice JupyterGIS
sends a made-up path for such a map, as the last quirk below explains. `old` and `new` hold the
whole layer before and after the change. A breaking change would get a new `schema_id`, such as
`.../layer/v2`. JupyterGIS could send both versions for a while, and a listener that filters on the
`https://schema.jupytergis.org/` prefix receives both.

JupyterGIS sends `opened` when JupyterLab first shows a map, in its own tab or as a notebook output.
It sends `closed` when that view closes. Maps already open when the event-feed plugin starts are
reported as `opened`. Layers already in a map when it opens are not reported as `added`, because
layer events start only once the map has loaded. So the event feed never tells Trail what a map held
when it opened. The message has no timestamp and no event id. Matt asked Jupyter Server for a way to
pass the timestamp on, in jupyter_server issue #1710, which is still open.

A bigger gap for Trail is that a layer event does not say which data the layer shows. The layer
names its data only by a source id, S1 in the example. The source itself is not in the event, and
sources have no events of their own. Most sources hold a file path or a URL. Some hold their data
inline. JupyterGIS stores drawn features this way, and by default it embeds the result of a buffer
the same way. So Trail learns that a layer called streets was added, but not which file the layer
shows.

jupyter-collaboration is the Jupyter Server extension that keeps the browser's copy of a document in
sync with the file on disk. It writes map.jGIS to disk once edits pause for about a second. Jupyter
Server first copies the old file to a backup named .~map.jGIS, then writes map.jGIS and deletes the
backup. Trail's file watcher sees the save only if map.jGIS is tracked, and no bundled markers
preset matches .jGIS files. Today no Trail process listens on the WebSocket, so the layer event
never reaches Trail.

## Listening to the event feed

A listener such as Trail connects to `ws://HOST:PORT/BASE_URL/api/events/subscribe`, where BASE_URL
is usually `/`. The listener must send the Jupyter server's token, either as `?token=<TOKEN>` or in
an `Authorization: token <TOKEN>` header. The token is the random secret in the URL that
`jupyter lab` prints. Without the token, Jupyter Server refuses the connection with 403.

Each event arrives as one json text message. The connection carries every event Jupyter Server
emits, so a listener keeps only the messages whose `schema_id` starts with
`https://schema.jupytergis.org/`. The other messages are Jupyter Server's own events, such as files
saved, renamed or deleted through JupyterLab and kernels started or restarted, plus
jupyter-collaboration's events. Some of them are frequent. From reading jupyter-collaboration's
code, Jupyter Server sends a file `get` event about once a second for every open shared document.
Later, the file events could tell Trail that a file change came from JupyterLab.

Jupyter Server keeps no past events. Each event goes only to the listeners connected at that
moment, so Trail misses every event sent while Trail is not connected.

## Quirks that affect Trail

These come from reading JupyterGIS's code at 6c3d5727. None has been seen in a browser yet.

- Changes made from Python or in another browser may arrive as GUI events. The event-feed plugin
  reacts to every change of the shared document, and a map shown in a notebook shares its document
  with the Python kernel. So `doc.add_layer()` in a notebook would likely arrive as a layer
  `added`, and Trail would log a notebook action as a GUI action. JupyterGIS could fix this by
  sending only changes made in that browser.
- One change can arrive twice. A map open both in a notebook output and in its own tab has two
  document models, and so do two browser tabs or two users. Each model reports the change under its
  own subject.
- After a map is renamed in JupyterLab, its events keep the old path, because the event-feed plugin
  reads the path only once.
- The `closed` event fires when the first view of a map closes, even if a second view of the same
  map is still open. The second view keeps sending events under the same subject, so `closed` does
  not mean that the map's events have ended.
- Dragging the opacity slider sends one `updated` per slider step. One drag can send dozens of
  events, each with two full layers.
- At 6c3d5727, a map shown in a notebook may report the layers it already has as `added` when it
  appears. Matt's newest commits make the event-feed plugin wait until every view has finished
  loading its map, which should fix this.
- A map created in Python with `GISDocument()` and no file name reports a path ending in
  `unsaved_project`, which does not exist. events.md says the path is null in this case.

## Seeing real events today

The reference consumer in ../eventfeed/examples/event_feed_consumer prints JupyterGIS events as they
arrive. Your micromamba environment jupytergis_dev cannot show events yet. Its JupyterGIS packages
are installed from ~/PyCharmProjects/jupytergis, which is upstream main at 0.16.8 and has no event
feed. Do not reuse jupytergis_dev for ../eventfeed. JupyterGIS's troubleshooting page says that
reusing one environment for two checkouts can break the install, and that the fix is to recreate
the environment.

Instead, pull the `prototyping` branch in ../eventfeed to get Matt's two newest commits. Create a
second environment with the micromamba command in docs/contributor_guide/development_setup.md,
changing only its name, for example to eventfeed_dev. Activate it and run
`python scripts/dev-install.py` in ../eventfeed. Then start `jupyter lab` in ../eventfeed, open one
of the .jGIS files in examples/, and run the consumer in a second terminal with the token from
`jupyter server list`.

```bash
cd ~/PyCharmProjects/eventfeed/examples/event_feed_consumer
JUPYTER_BASE_URL=localhost:8888 JUPYTER_TOKEN=<token> uv run consumer.py
```

Opening, closing and editing the map should print document and layer events.

## What Trail needs to do

The goal is that adding the streets layer becomes a record about the map.jGIS asset in
.trail/events.jsonl, which Trail's console prints as it arrives. Trail needs eight functions for
that. Each paragraph below describes one.

Trail has to find the user's Jupyter servers on its own, because a Jupyter server usually gets a
new token each time it starts. Each running Jupyter server writes a small json file,
jpserver-<pid>.json, in Jupyter's runtime directory, usually ~/.local/share/jupyter/runtime. The
file holds the server's url, its token, and `root_dir`, the folder the server serves.
`jupyter server list` reads the same files. Trail can follow every Jupyter server whose `root_dir`
contains the project folder, sits inside it, or contains a file Trail tracks elsewhere. Trail should
notice Jupyter servers that start or stop, and reconnect after a restart. A manual URL and token can
cover a server Trail cannot discover, such as one in a container. Trail then also needs the local
folder that matches that server's `root_dir`. A Jupyter server on another machine serves files
Trail cannot track, so its events would all be skipped. The token grants full control of its
Jupyter server, so Trail's log and Trail's console must never show it.

Trail has to tie each event to a tracked file. Every record in .trail/events.jsonl stores the id of
an asset or a Dir, so an event about something untracked has nowhere to go. Trail can resolve
`document_path` against the server's `root_dir` and record the event only if that map file is
tracked, the same rule Trail applies to file events. If the map is not tracked yet but matches
.markers, Trail should scan for it first, the way Trail's file watcher picks up a new file that
matches .markers. A user can mark maps with `mark jgis`, and the geospatial preset could mark them
by default. A preset only affects projects created after it changes. QGIS project files need the
same treatment. Trail should also ignore Jupyter's `.~` backup copies, since a .jGIS marker could
briefly track one during a slow save. Keeping events about untracked maps would need log records
that belong to no file. The initiative's fourth open question calls these project-level events, and
its own example leans toward keeping them.


Trail has to link each layer to the data file it shows. The initiative's fourth question asks for
the same link. Until JupyterGIS sends source events, Trail could read the source's path or URL from
the saved .jGIS file, where local paths are relative to the file's folder. The saved file lags the
event by about a second, so Trail would read it after the file watcher sees the write. The saved
file is also the only place Trail can learn which layers a map held when it opened. Layers whose
data sits inside the .jGIS file have no file to link.

Trail has to store the events in its log, stamped on arrival because the message carries no time.
Each record should keep everything in the message, so nothing is lost. The `events` command should
find these records by verb or layer id, like any other field. Trail's console should print a short
summary of what changed, such as `opacity 1 → 0.35`, not whole layers.

Trail has to relate a layer event to the save that follows it. About a second after each change,
jupyter-collaboration saves map.jGIS, and Trail's file watcher records that save as file events on
the same asset. Trail's console could show those file events beside the layer event, link them to
it, or fold them into it.

Trail has to cope with the quirks. When a map opens, Trail should remember which tracked file that
subject is showing on that Jupyter server. Trail should keep this link after a `closed`, because
another view of the map can keep sending events. After a rename, JupyterGIS keeps sending the old
path. Trail can still file those events under the right asset through the subject, because Trail's
file watcher has already followed the rename. Trail should accept an `updated` or `removed` for a
layer it never saw added. Layers already in a map are never reported, and the map may also have
opened before Trail connected. Duplicates can stay until JupyterGIS fixes their cause, or Trail can
drop identical events from different subjects that arrive close together.

Trail has to merge bursts. Rapid updates to one layer should become one log entry with the first
`old`, the last `new`, a count and the start time. The entry closes after a short pause, such as
1.5 s, or when the layer gets another kind of event, the map closes or Trail stops.
jupyter-collaboration also waits for a pause before it saves, so one merged entry roughly matches
one write of the .jGIS file. Trail already merges a zarr store's file events this way, and the
initiative asks for GUI activity to be "summarized as provenance, not telemetry".

Trail's console has to work as a live view and a debugger for the event feed. Trail's console
should print JupyterGIS events as they are recorded, the same way it prints file events. Its header
should show whether Trail is connected to a Jupyter server. A command in the trail CLI should list
each Jupyter server Trail follows, with its `root_dir`, its connection state, and counts of the
messages received, recorded and skipped. Trail should also keep the recent JupyterGIS messages in
memory, recorded or not. Each skipped message should carry its reason, such as map not tracked or
file missing. The trail CLI should let you search these messages like events, and print them as they
arrive. Messages of other schemas are better counted than kept, since the once-a-second file `get`
events would push everything else out.

Trail's console should also show whether each Jupyter server knows JupyterGIS's event schemas at
all. Once Trail's own connection is open, Trail can send a deliberately incomplete test event for
each schema. Jupyter Server rejects the test with one message if the schema is registered, and with
another if the installed JupyterGIS has no event feed, as in every release so far. Jupyter Server
never passes the test event on. It checks events only while someone is listening, which is why
Trail must be connected first.

These tools show whether Trail received an event and why Trail skipped it. If nothing arrived,
JupyterGIS did not send it, Jupyter Server rejected it, or Trail was not connected at that moment. A
rejection shows only in the browser's console.

## What has to wait

Trail records events only while it runs, which is acceptable for now. Later, Trail could catch up
when it starts. Jupyter Server can write every event to a log file even when nobody listens, but
only if the user turns this on in jupyter_server_config.py. Another option is to run part of Trail
inside Jupyter Server. The 2026-07-08 café raised that idea for a different reason, to avoid several
processes writing one shared file.

Other needs wait on JupyterGIS, starting with a source event that carries the source's path or URL.
In the 2026-07-08 café's target story, a user loads data, zooms to an area and lingers, draws a
polygon and buffers it (compass/meeting-notes/20260708-collaboration-cafe/index.md). Today only
loading data has an event, a layer `added` that does not name the file. Zooming and lingering need
viewport events, which are not built yet. Drawing changes a source, and sources have no events. A
buffer shows up only as one more layer `added`, with nothing saying it came from the drawn polygon.
Timestamps depend on jupyter_server issue #1710. You can raise these gaps and the quirks on PR #4.

## Decisions to make

- Trail should record events about maps it does not track. This requires records that can exist
  without a tracked map asset.
> Yes
- Trail should connect automatically when the server folder matches a watched directory path.
  Otherwise, it should ask first.
> If the folder matches a watched directory path, then we should connect automatically. Otherwise, ask first.
- The pause that ends a burst of layer updates remains undecided; 1.5 s above is a proposal.
> Unsure
- Trail should fold each autosave's file events into the corresponding layer event.
> Fold into it
- Trail should drop duplicate events.
> Drop
- Should Trail read data paths and starting layers from the saved .jGIS file until source events
  exist?
- Trail should record only while it runs. Catch-up from a Jupyter Server event log is outside
  the current scope.
> Only while running

## Appendix: changes since October 20, checked October 21, 2026

This appendix compares yesterday's documentation at `413d069b` with `abecc972`, the remote
`prototyping` head checked today. All 13 intervening commits are dated October 21. It updates the
report's earlier snapshot; the browser behavior below was checked in source, not tested in a
running JupyterGIS session. [Full comparison](https://github.com/geojupyter/prototype-jupytergis-eventfeed/compare/413d069b...abecc972).

### What changed on the linked page

The [event-feed page](https://prototype-jupytergis-eventfeed--4.org.readthedocs.build/contributor_guide/explanation/events.html)
now calls the prototype ready for testing and links to PR #4. A new **Using this prototype**
section covers cloning, installing from source in a Mamba environment, starting `jupyter lab`,
copying its token, and running the consumer in a second activated terminal. From the repository
root, the consumer command is:

```bash
export JUPYTER_TOKEN=<token>
uv run examples/event_feed_consumer/consumer.py
```

Its manual checklist covers opening a document, adding and removing a layer, changing symbology,
moving the viewport and waiting 10 seconds, and closing the document. The separate environment
recommendation in this report still applies to our two local checkouts.

The **Object** section now warns that a document path is not a unique document identifier:
“Save as” can change it during an event stream. The prototype explicitly leaves stable document
identity unresolved. The example JSON also loses an invalid trailing comma; other page edits are
formatting changes.

### Implementation changes that affect the report

[Viewport support was added](https://github.com/geojupyter/prototype-jupytergis-eventfeed/commit/f53af252).
There are now three schemas, adding `https://schema.jupytergis.org/events/viewport/v1` with verb
`moved`. Its object contains `document_path` and `new`, the latest viewport state: center
coordinates, zoom and extent, expressed in the map's projection. Every local movement restarts a
10-second timer; one event is emitted after movement stops for that interval. Collaborators'
viewport changes are ignored. This is a debounce, not an event every 10 seconds during movement.
Trail can now observe zooming and lingering, previously listed under **What has to wait**.

The page is internally inconsistent: its opening warning that some example events are unimplemented
was removed, and its checklist tests viewport movement, but **High-frequency events need
throttling** still says viewport pan/zoom is unimplemented. That paragraph is stale.

The [event-wiring refactor](https://github.com/geojupyter/prototype-jupytergis-eventfeed/commit/d91970a7)
also reads `widget.model.filePath` when each event is emitted, instead of capturing it once when
the map opens. The old-path quirk and Trail workaround above are therefore outdated wherever the
model reflects the changed path. This does not give Trail a stable document identifier across
renames or “Save as”. The local-client filter applies to viewport events; the layer handler still
has no equivalent filter, so the earlier concerns about layer attribution and duplicates remain.

The branch also caps Python below 3.15 in the root and layer-gallery project configuration.
At this check, the local checkout remains at `413d069b`, 13 commits behind the fetched remote;
these features require updating it and rebuilding the prototype installation.
