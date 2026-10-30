# Plan: `trail.jupyter`, second draft

This plan replaces notes/10-21-2-jupyter-submodule-plan.md. It adds `src/trail/jupyter/`, so that
Trail listens to the event feed of each Jupyter server that can serve the project. Trail records
each JupyterGIS GUI action as an event in `.trail/events.jsonl` and prints it live in the console.
The console also becomes a debugger for the feed: it shows which servers Trail follows, what each
one sent, and why a message was skipped.

Three things changed since 10-21-2:

- The plan applies your comments on 10-21-2. "Appendix: your comments on 10-21-2" gives one line
  for each.
- Matt Fisher, who wrote the event feed, leaves the project in November. "What to settle with
  Matt before November" lists the quirks that only he can answer or fix cheaply, with ways to
  resolve each.
- Experiments on 2026-10-28 changed parts of Trail's own design. "Design questions on Trail's
  side" covers them: what Trail does when Jupyter writes a tracked file, Jupyter's backup files,
  which servers Trail connects to, and two Trails recording one project.

The experiments ran against the eventfeed clone at abecc972, which still matches upstream, and the
eventfeed_dev environment: jupyter_server 2.21.1, jupyter_events 0.12.1, jupyter-collaboration
4.4.2, jupyter_server_fileid 0.10.0 and JupyterLab 4.6.4.

- A real JupyterLab with JupyterGIS was driven in headless Chromium. The runs opened maps, changed
  layers, renamed files and folders, used Save As, showed maps in notebooks and changed them from
  Python, while a subscriber captured the feed.
- Jupyter servers were run with pycrdt clients, a watchdog observer and a feed subscriber, to time
  saves, reloads, restarts and races.
- The hidden-file rule in "Core changes" was prototyped on a copy of Trail, and the suite passed.

A second agent then re-checked every finding against the code and corrected several. A statement
that rests on reading code alone, or on a guess, says so.

The plan assumes that the two refactors in the appendix land first, so it uses their names:
`trail.entries` in place of `Trail._entry`, and `.checklist` in place of `.markers`. The work goes
on a branch `jupyter` off `hodczak`, one step at a time (see "Steps").

## How it fits together

Trail connects to each live Jupyter server whose root overlaps one of its Dirs, and turns what the
server's event feed says into events in the log. Six parts do the work:

- `trail.jupyter.jsons` reads the `jpserver-*.json` files that running Jupyter servers write.
  `Servers` keeps a `Server`, Trail's handle on one Jupyter server, for each server that connects.
- Each `Server` runs one subscription task. It holds the server's event feed open and hands every
  frame to the `Pipeline`, which turns messages into events.
- `Pipeline.receive` stamps each message on arrival. Jupyter's own contents and collaboration
  messages tell the pipeline when Jupyter saved, renamed or reloaded a file. JupyterGIS messages
  are kept in `Messages`, the debugger's memory. Each one passes the duplicate check, the lookup of
  the map it is about, which gives a `Map`, and the reload check.
- A JupyterGIS message then joins a `Burst`: the run of messages about one layer that ends with one
  write of the map. A drag of the opacity slider is one burst of `updated` messages.
- While a connected server has a tracked file open, the Jupyter session also sees that file's
  watchdog events. The `opened` and `modified` events of Jupyter's own write of the file are
  folded into the event the write belongs to, as counts. Every other file event is logged as
  before.
- A burst closes 0.25 s after Jupyter's save of the map, or 2 s after its last message if no save
  comes, and its event is appended to the log. Saves with no layer event are recorded as `saved`
  events. Trail's console prints what is appended, and a `jupyter` command shows the feed.

For example, take one opacity drag on a map that is open in a tab and in a notebook. The tab sends
14 `updated` messages, and the notebook view sends 14 copies, which are dropped as duplicates. The
14 messages merge into one burst. The autosave comes 1.0 s after the last step. Jupyter copies the
map to `.~city.jGIS`, writes the map in place and deletes the copy, and the map's three `opened`
and two `modified` events of that write are folded. The contents `save` message marks the burst
saved, and 0.25 s later it closes as one `LayerV1Event` with `messages: 14`,
`count: opened 3, modified 2` and `change: parameters.opacity 1 → 0.35`.

## What to settle with Matt before November

Matt leaves the project in November. The issues below are the ones that only he can answer, or
that are cheapest to fix while he still owns the emitter. They are ordered by how much they could
change Trail's design. Each says what happens and what it does to Trail's record, and its
subsection "How to resolve it" gives the options and the question to ask. The JupyterGIS code
cited is the prototype at abecc972: the emitter python/jupytergis_core/src/eventFeed/plugin.ts and
the document model packages/schema/src/doc.ts.


### Layer events report every change, whatever made it

JupyterGIS's model reports every change to a map's layers, whatever made it. Yjs passes each
observer the transaction, which says whether the change was made locally, but
`_layersObserver` (doc.ts:592-615) drops it, and plugin.ts sends every entry. The viewport handler
keeps only local changes, through `isLocalClient`. The layer handler keeps everything. In the
browser runs:

- One opacity change in one JupyterLab page sent two `updated` messages 10 ms apart, one from each
  open page. Copies arrived within 40 ms of each other, identical except for the subject.
- A Python change through a notebook `GISDocument` was sent 0 times while its output was not
  displayed, once when it was, and once more for each other view of the map. events.md says that
  Python API changes are deliberately not emitted.
- After a script rewrote an open map, the room reloaded it, and every open view sent `updated` for
  every layer, changed or not. A room rebuilt from its store after a server restart, or 60 s after
  its last client left, does the same with no `overwrite` message.
- Maps last saved in schema 0.5.0 get new random symbology ids each time they load. So after such a
  reload even unchanged layers arrive with `old` different from `new`.
- An edit that the server lost at shutdown came back after the restart as an ordinary `updated`
  that reverts it, from the same subject.

Trail would record these copies, notebook cells, scripts and reverts as GUI actions by the person
at the browser. 10-21-2 has three checks to undo this: a 1.0 s window for duplicates, a 2 s window
after an `overwrite` message, and dropping an update whose `old` equals its `new`. Each check
leaks. A rebuilt room sends no `overwrite`, old-format maps defeat the equality check, and a real
GUI edit within 2 s of a reload is dropped.

#### How to resolve it

The fix belongs in JupyterGIS, and it is small. `_layersObserver` passes `transaction.local` into
the change it emits, and plugin.ts skips changes that are not local, as the viewport handler does.
A change then arrives once, from the view where it was made. The burst of `added` messages that a
notebook view can send right after it opens disappears too, since that sync is remote.

Trail keeps its three checks as a fallback for JupyterGIS builds without the filter.

The team also has to decide whether the feed carries Python API changes. If it does, they need an
origin tag and a single emitter. If it does not, the local filter drops them, as events.md
promises.

Ask Matt: can layer events be filtered on `transaction.local` like viewport events, before
November? Should Python API changes be dropped, or tagged and sent once?

> What does "local" mean here? So basically, the first update has `transacation.local`, and the remainder do not, because they are coming from different pages? What if transaction.local is included in the schema, and instead Trail determines the filtering? Or am I misinterpreting?

### A layer event does not mean the file changed

The emitter reports a change when the shared document changes. jupyter-collaboration writes the
file about a second later, and several things stop that write:

- Moving an open map outside Jupyter, with `mv` or `git mv`, leaves its room saving to the old path.
  Renaming a folder above an open map in JupyterLab does the same, since jupyter_server_fileid does
  not move the map's record. Every later save fails with a session/v1 message at level `ERROR`,
  "Error saving file", and the edits never reach the file. After a folder named `maps (v1)` is
  renamed, the browser also keeps sending the old path.
- With a map open both as a tab and as a notebook output, closing the view that opened first stops
  the other view's connection to the room. Its later edits are still sent to the feed but never
  saved, and nothing reports an error.
- An edit made less than a second before the server stops is never saved.
- An outside write within about a second of a GUI edit discards the GUI edit when the room reloads
  the file.
- For a file Jupyter cannot write, the room skips the write but still sends session/v1 `save`
  "Content saved.". This was read from the code, not run.

So Trail's log could hold layer changes that the file never had. 10-21-2 records a burst that gets
no save within 2 s as an ordinary event.

> I have no idea what you are talking about here. Make less assumptions about my knowledge of jupyter. I have no idea what a "room" is.

#### How to resolve it

Trail treats a layer event as a claim about the shared document. It confirms the claim by the
contents `save` message for that path or by Jupyter's write of the file, never by the session
`save` message. A burst that closes with no confirmed save is recorded as not saved. A session/v1
`ERROR` for the map marks its open bursts unsaved, with the message. "Bursts" describes this.

The failures themselves are jupyter-collaboration, jupyter_server_fileid and JupyterLab bugs. "Bugs
to report upstream" lists them, with short repros.

Ask Matt: should events.md say that a layer event is not a save? Does JupyterGIS want to show the
user that a save failed?

### Opening a map rewrites it

About 1.2 s after a map opens in a browser, the room saves it, with no user action. OpenLayers fires
`moveend` after the map's first render, and JupyterGIS's handler writes the whole options object:
center, zoom, extent and projection. That write schedules the autosave. Every additional view of
the map is also followed by a save about 1 s after it opens, and so are a rename of an open map and
a reconnect after a server restart. In
the browser run, opening the committed examples/world.jGIS gave a save 1.4 s after `opened`, and
the file grew from 2,539 to 4,338 bytes:

- `schemaVersion` went from 0.5.0 to 0.6.0, and the old symbology was migrated with new random ids;
- the json was written again with sorted keys and an indent of 2;
- the zoom was clamped to the window's width, from 1.8361 to 2.0634.

Your ../eventfeed checkout shows the same. examples/world.jGIS and examples/shapefile.jGIS were
rewritten when they were opened on 10-21 and 10-22. Both now have `schemaVersion` 0.6.0 and the
same zoom, 2.5343, which fits a map about 1,480 px wide.

Each time someone opens a map, Trail, and git, see a new version of it, and no event says why.
10-21-2 records each such save as a bare `saved` event.

#### How to resolve it

JupyterGIS could write the options only after the user moves the map, and make the migration's ids
deterministic, so a reload does not change layers nobody touched. Until then, Trail can tell an
open-induced save by its timing: it comes about 1 s after a view joins the map's room, which the
feed shows as a session/v1 "New client connected", an awareness `join` or a document `opened`.

Ask Matt: is the write on open intended? Could options be written only after user input, and could
the migration use deterministic ids?

### Events cannot name a map reliably

An event names its map by `document_path`, the contents API path relative to the server's root,
read when the event is sent. That name can be wrong or ambiguous:

- A rename by another client, such as a second page, another user or a REST call, leaves an open
  view sending the old path, which no longer exists. Meanwhile the server's own messages carry the
  new one. A notebook view never updates its path, even after a rename in the same page.
- Save As closes the old view and opens a new one, with a new subject and a new file id. Nothing
  links the copy to its source. The file browser's Duplicate does send a contents `copy` with
  `source_path`.
- A map opened from an additional drive, such as jupyter-fs or jupyter-drives, loses its drive
  name. `S3:maps/a.jGIS` and a local `maps/a.jGIS` both report `maps/a.jGIS`. This comes from
  reading the code; no extra drive was installed.
- In a notebook, `GISDocument('x.jGIS')` resolves `x.jGIS` against the folder of the active tab,
  not the kernel's working directory. If no file is there, the browser silently creates an empty
  map. `GISDocument()` with no path reports `<folder>/unsaved_project`, not the `null` that
  events.md documents.

jupyter-collaboration already has a stable id for each file. A map's room is named
`text:jgis:<file id>`. The file id comes from jupyter_server_fileid and survives renames made
through Jupyter. The browser writes the room's name into the shared document's state as
`document_id`. The feed's session/v1 messages carry the room's name and the server's current path.

Trail resolves a message by the view's subject and then by its path, as "Resolving the map"
describes. A stale path can name a new file created at the old path, and links to subjects are
lost when Trail restarts.

#### How to resolve it

JupyterGIS could add `document_id` to every event's object, and read the path from the shared
state (`sharedModel.getState('path')`), so that stale views and notebook views report the current
path. It could send one event that links a Save As target to its source. In the Python API, it
could resolve paths against the kernel's working directory, raise for a missing file, and send
null for an unsaved map.

On Trail's side, `document_id` would let Trail link a map to its file id and follow it across
renames. File ids belong to one Jupyter data directory and survive server restarts, so Trail would
key them by data directory, not by server url.

Ask Matt: can every event carry `document_id`, and the path from the shared state? Could Save As
send a linking event? Are maps meant to open from additional drives?

### Events carry no time, no order and no sequence number

The emitter sends each event as its own POST and does not wait for the reply. A message has no
time, no id and no sequence number, and jupyter_server forwards only the payload to subscribers.
When 200 POSTs were sent at once over six connections, as a browser does, they arrived with 8 to
15 neighbors swapped. A failed POST, for example during a server restart, is logged in the
browser's console and dropped, and nothing retries it.

The POST API has an optional `timestamp`, but it accepts only whole seconds followed by an offset
and a literal Z. JavaScript's `toISOString()` is rejected with a 400, and subscribers never see the
value anyway. #1710 would pass the server's emit time to subscribers. On a local server that
differs from Trail's arrival time by milliseconds.

Trail stamps each message when it arrives and cannot tell that an event was lost. It merges a drag
by order of arrival, so a swapped tail would make the merged `new` an intermediate value.

#### How to resolve it

JupyterGIS could add two optional properties to the payload of each v1 schema: `seq`, counting
per subject, and `client_time`, in epoch milliseconds. This is an additive change under events.md's
rules. Extra properties already pass validation and reach subscribers unchanged, which a test POST
confirmed. The emitter could also send each view's events in order, through a chain of promises,
with a small queue that retries.

Trail then sorts a burst by `seq` and reports gaps. Until then it can merge a burst by chaining
each message's `old` to the previous message's `new`, instead of trusting arrival order.

Ask Matt: can `seq` and `client_time` land in v1 before November? Who carries #1710 after November?

### Edits that send no event

The emitter listens only to layers and the viewport. Autosave writes the whole document, so these
edits reach the file with no event:

- reordering layers, and making or removing groups, in the layer tree;
- adding, editing and removing sources, including GeoJSON drawn with the draw tool;
- options such as the projection, and pans and zooms;
- annotations, story maps and metadata.

Removing a layer also removes its source, and only the layer is reported. A layer event names the
layer's data only by a source id.

For each of these edits Trail sees a save with no cause. It also cannot link a layer to its data
file, which the Trail initiative asks for.

#### How to resolve it

JupyterGIS could send one local-only event that names the top-level keys that changed, such as
`layers`, `layerTree`, `sources` and `options`, so that every save has a cause. Source events would
then carry a source's path or url, which links layers to data files. Until then, Trail records such
saves without a cause, and it could read the source paths from the saved map (see the decisions).

Ask Matt: which comes first, source events or one generic event for the other keys?

### `opened`, `closed` and the subject

`opened` is sent once per map model per JupyterLab page. `closed` is tied to the first view only.
Closing that view sends `closed` while a second view stays open and keeps sending. Closing the
second view sends nothing, and reloading or closing the page sends nothing. In one browser session,
6 of 12 subjects never sent `closed`.

The subject is the Yjs client id of one view's model. Each map, page, reload and notebook output
gets a new one. Nothing in an event names the user, although the browser already puts the
Jupyter identity into the room's awareness.

So Trail cannot use `opened` and `closed` to know which views are live, or to bound a working
session. On a shared server or JupyterHub, it cannot attribute an action to a person.

#### How to resolve it

JupyterGIS could send `closed` when the model is disposed, and on `pagehide` with `keepalive`. It
could add the awareness user, a username and a display name, to every event. Trail keeps a
subject's links after `closed`, as 10-21-2 does, and treats the subject as the id of a view, not of
a person.

Ask Matt: can `closed` follow the model's disposal? Should events name the user?

### Large payloads

Every layer `updated` carries the whole old and new layer: 1 to 3.5 KB for the example maps. A
story segment keeps a pasted image as base64 inside the layer, so every edit to that segment sends
the image twice. The opacity slider sends one message per 1% step, up to 100 per drag and view.
tornado closes a socket on a message over 10 MiB, which a segment with an image of about 4 MB would
exceed. That limit was read from tornado's code, not tested.

#### How to resolve it

A `layer/v2` could send the changed paths without attachments. Trail raises the socket's message
limit either way.

Ask Matt: would a changed-paths payload suit a v2 of the layer schema?

### Smaller points for Matt

- No endpoint lists the schemas a server has registered, so Trail's probe sends a POST that fails on
  purpose. Each probe prints three warning lines per schema in the user's Jupyter terminal. A GET
  route in JupyterGIS's server extension, listing its schema ids and versions, would replace the
  probe.
- A payload must never hold a top-level `schema_id` key. jupyter_server's subscribe handler then
  raises in every listener, after the POST has already returned 204. This deserves a line in
  events.md.
- QGIS projects, .qgz and .qgs, go through the same emitter, and each save exports the project
  through QGIS again. Is that round trip lossless, and does opening a .qgz rewrite it? QGIS was not
  installed in eventfeed_dev, so this was not tested.
- events.md's "High-frequency events need throttling" still says viewport events are not
  implemented, and layer_v1.yaml still says "Absent for 'deleted' verb" where it means `removed`.

### Bugs to report upstream

These are not JupyterGIS bugs, but each loses a user's edits, and each has a short repro from the
2026-10-28 runs:

- jupyter_server_fileid: `ArbitraryFileIdManager._move_recursive` moves a folder's children only if
  the folder itself has a record, which opening a map does not create. It also selects children
  with a SQLite GLOB, so brackets in the folder's path, or in the root's, break it. Renaming a
  folder above an open map then breaks the map's room.
- JupyterLab: the document registry updates open paths with an unescaped `RegExp`, so renaming a
  folder such as `maps (v1)` leaves its open views on the old path.
- JupyterLab's collaborative provider keeps one connection per path. A notebook view and a tab of
  the same map share that slot, so closing the earlier one stops the other from saving.
- jupyter-collaboration:
  - a room does not follow a map moved outside Jupyter under the default file id manager, and every
    later save fails;
  - shutdown does not wait for pending saves, as a TODO in its code notes, so the last second's
    edit is lost;
  - the file watcher ignores a write that keeps or lowers the file's mtime, such as `cp -p` or a
    checkpoint restore, and the next autosave overwrites that write;
  - a pending autosave can write its snapshot from before a reload over the outside write that
    caused the reload;
  - "Content saved." is sent when nothing was written;
  - the store `.jupyter_ystore.db` defaults to the server's working directory, which is often the
    project.

Ask Matt: will he file these, or should we? Should the notebook view reuse the tab's model, which
would avoid the shared connection slot in JupyterGIS itself?

## Design questions on Trail's side

The runs also showed where Trail's own design has to change. These are Trail's decisions, whatever
Matt answers, though some get simpler if his changes land. Each subsection says what happens and
what 10-21-2 would do with it, and its "How to resolve it" gives the choice this plan makes.

### When Jupyter writes a file Trail tracks

You asked whether Trail should suppress the file events of a write that a Jupyter event announces,
and how. Four facts decide it.

First, a Jupyter save rewrites a file in place. When jupyter_server saves a file that exists, it
copies the file to `.~<name>` beside it, writes the new content into the file itself, syncs it,
and deletes the copy. For an autosave of `city.jGIS`, Trail's watcher reports:

1. `opened city.jGIS`, as the copy reads it;
2. `created`, `opened` and `modified` on `.~city.jGIS`;
3. `opened city.jGIS` and one or two `modified city.jGIS`, as the new content is written;
4. `deleted .~city.jGIS`;
5. one more `opened city.jGIS`, as the room reads the file back to hash it.

On the map itself that is three `opened` and one or two `modified`. `created` and `deleted` happen
only to the copy. The other things Jupyter does to files, such as creating, copying, renaming,
Save As and deleting, create, move or delete them.

Second, the contents_service/v1 `save` message names only the action and the path. jupyter_server
sends it after the write, but it can reach Trail before or after the write's file events. In the
browser run it came 0.3 ms before the map's `modified`.

Third, on replay only `created` and `moved` change what Trail tracks (event.py:377-387). Folding
`opened` and `modified` into another event therefore loses nothing on replay. Folding `created`,
`moved` or `deleted` would.

Fourth, 10-21-2's fold catches writes that are not Jupyter's. It holds every `opened` and `modified`
of a map while any burst of that map is open, about 1.3 s or longer, and folds them all once a save
arrives. In the browser, a script wrote the map 0.3 s after a GUI change. The room reloaded the
script's content and the GUI change was lost. Under 10-21-2's rule, the log would show only the GUI
change, marked saved, with the script's write hidden in its counts.

Trail has four ways to treat the file events of a Jupyter write:

- Suppress them: drop the matched events. The log is smallest, but a wrong match erases another
  program's write without a trace.
- Fold them: count them on the Jupyter event, as 10-21-2 does. The counts leave a trace, but only
  if the match is right.
- Link them: log every file event with the id of the Jupyter event that caused it. Nothing is lost,
  but every autosave adds four or five records, which is what you wanted to avoid.
- Annotate them: log every file event with a tag such as `writer: jupyter`. This is like linking,
  without the link.

Trail can tell Jupyter's write from another program's in three ways:

- The backup bracket. The file's events from the `opened` just before `created .~<name>` to
  `deleted .~<name>`, plus the one `opened` that follows, are Jupyter's write. The kernel reports
  them in order on one inotify queue. The bracket needs no feed, so it marks Jupyter's writes even
  when Trail is not connected. It is missing when `use_atomic_writing` is off, for a new file, for
  upload chunks after the first, and for a checkpoint restore.
- The save message. Events within 0.25 s of the contents `save` message for the same path, before or
  after it. This is the fallback for writes without a bracket. An atomic save of a 50 MB file took
  144 ms on tmpfs, so the window fits maps and most files.
- A content hash from `GET /api/contents/<path>?hash=1`, which jupyter_server 2.21 supports, does
  not help. The server computes it when Trail asks, from whatever bytes are on disk then, so it says
  nothing about who wrote them. A hash or an mtime inside the save message itself would help.

#### How to resolve it

Trail never suppresses. It folds only the `opened` and `modified` events of the written file, and
only those inside the file's backup bracket or, with no bracket, within 0.25 s of the save message
for that path. The event that takes the fold keeps the counts and the times of the first and last
folded events, so a fold can be checked later. Every other file event is logged as today.
`created`, `moved` and `deleted` are never folded.

This applies to every tracked file that a connected Jupyter server saves, not only maps. A notebook
or a csv open in JupyterLab is autosaved the same way, about a second after each pause in typing.

The backup bracket is Jupyter's convention, so the code that reads it lives in `trail.jupyter`, not
in the base modules. The base gains one generic hook: the watchdog session hands each file event to
the observers registered on it before it applies the event, and an observer may hold the event and
apply it later. The Jupyter session registers itself as an observer while it runs. "Folding
Jupyter's writes" describes the mechanism.

Upstream, jupyter_server could put the file's `last_modified` and `size`, and ideally its hash, in
the contents `save` message. jupyter-collaboration already computes the hash after each save. Trail
could then match a write exactly. This would be a jupyter_server issue next to #1710, which is a
question for Matt, but Trail does not wait for it.

### Jupyter's backups and other files in the project folder

The backup copy `.~<name>` is made for every file Jupyter saves, whatever its extension: notebooks,
csv files, maps, and the first chunk of an upload over an existing file. It usually lives for a
millisecond or two, and Trail's watcher waits 0.1 s before it scans a created file, so Trail
usually never sees it. A slow save is another matter. Re-uploading a 600 MB tif kept its backup
alive for 178 ms, and Trail tracked the backup as an asset. Once tracked, the backup stays an asset
with a missing file, and every later save of the real file logs its backup's events onto it: in one
run, 52 events on the phantom against 36 on the file. If the save fails, Jupyter moves the backup
back over the file, and the file's path passes to the backup's asset, so the file's asset id
changes.

Your comment on 10-21-2's `.~` check asked whether the checklist could exclude backups instead. The
checks of each idea:

- There is no list of extensions, so `!.~*.jgis` misses `.~a.csv` and `.~notes.ipynb`.
- A root line `!.~*` loses to any dir scope, since a dir scope decides first: `data/*.csv` takes
  `data/.~a.csv`.
- A bracket in the pattern works in pathspec: `[!.]*.jgis` matches `map.jGIS` and not `.~map.jGIS`,
  at any depth. But every line, at the root and in each Dir, must carry it, since
  `data/[!.]*.csv` alone still loses to a root `*.csv`. `check jgis` appends a plain `*.jgis`,
  which lets the backups back in, and `uncheck jgis` cannot remove the bracket line. The extension
  helpers and completion skip such a line, and a preset reaches only new projects.
- `[!.]` drops every hidden file, not only `.~` ones. So the bracket is a general rule, written by
  hand on every line.

Jupyter leaves other files in a project folder too. The store `.jupyter_ystore.db` lands in the
folder the server was started from, which is often the project. Each change to a shared document is
one SQLite write, and each write makes about five watchdog events on its journal file. In one short
session the project saw 130 journal events against 11 on the map, each costing Trail's consumer
about 0.1 ms. A `./*` line tracks this store today, along with `.env` and Trail's own `.markers`.
`.jupyter/collaboration_sessions.json` and `.ipynb_checkpoints/` lie in hidden folders, which
Trail's walk and watcher already skip. A delete from JupyterLab on a separate mount moves the file
into `<mount point>/.Trash-<uid>/files`, which lies inside the project when the project is the
mount point, as with a Docker bind mount. Trail then logs a move into the trash and keeps tracking
the file.

#### How to resolve it

Trail adopts a general rule: a wildcard does not match the leading dot of a file name, as in the
shell and Python's glob. A hidden file is decided only by lines that name its dot, such as `.env`,
`./.env` or `.*.csv`, and by exclusions. This is not Jupyter code. It extends the rule Trail
already applies to hidden folders, which the walk enters and the watcher reports only if a Dir sits
at or below them. It covers every extension, every dir scope and every existing project, with no
edit to the checklist. It also stops `./*` from tracking `.checklist`, `.env` and the ystore, and
`*.shp` from tracking the `._roads.shp` files macOS writes on shared drives. A dirasset's own
hidden children, such as `.zattrs`, are untouched, since they are matched by the dirasset's pattern,
not by the checklist. "Hidden files" under "Core changes" gives the code.

The rule has two side effects to accept. `./*` and `*.env` no longer reach hidden files, so a user
who wants `.env` writes `./.env`. And `check .env` currently writes `*.env`, since the checklist
reads `.env` as an extension, so it should write `./.env` when a file `.env` exists. That is in the
decisions.

The other files need no Jupyter code either. With the rule, the ystore is never tracked. Its events
still cost the consumer, and the watcher could drop events on hidden files no line names, if
profiling ever shows a need. Trail's notes for Jupyter users can mention
`--SQLiteYStore.db_path` to keep the store out of the project. The trash case is in the decisions:
a move into a hidden folder that no Dir covers could count as a deletion.

### Which servers Trail listens to

You asked why Trail should not watch every server and keep what touches checked files. A server can
only name files under its own root, since the contents manager refuses any path that leaves it. So
a server whose root neither contains a Dir nor sits inside one can never name a file Trail tracks.
The exception would be a symlink inside its root that points into the project, which the code
allows but no run tested.

Connecting to every server therefore finds nothing more than connecting to the overlapping ones,
and it costs three things. Each socket carries every event of every schema from that server,
including other projects' maps and files. Each probe prints three warning lines per schema in that
server's terminal. And Trail would hold tokens it can never use.

10-21-2 connected by itself only to a server whose root is a Dir's folder or inside one. A
JupyterLab started from the home folder, or from a folder above the project, never connected. The
user saw one row in the feed and had to type `jupyter connect` again in every new Trail. How common
that setup is, as against starting JupyterLab in the project or from a launcher, is a question for
Matt and the JupyterGIS users.

#### How to resolve it

A server connects by itself when its resolved root overlaps a Dir: the root is a Dir's folder, a
folder inside a Dir that tracks subfolders, or a folder above a Dir, such as the home folder.
Messages that resolve outside the project are counted, and their payloads are not kept. Servers
rooted elsewhere stay available for `jupyter connect`, which covers the symlink case and servers in
containers.

The probe runs only for servers rooted at or inside a Dir, so it never prints warnings in the
terminal of a JupyterLab rooted at the home folder. Trail reads only the `jpserver-*.json` files,
never the `jpserver-*-open.html` files beside them, which also hold the token. Every one of the 15
runtime files on this machine is stale today, so the check that a server's process is alive stays.

### Two Trails recording one project

Trail has no lock between processes. A Trail opened in a notebook starts recording on its own,
since `Trail(root)` calls `reconcile`, which starts the watchdog session under the kernel's running
loop. So the console in a terminal and a notebook on the same project are two recorders. In a run
with two processes, every file event was logged twice, a new file got two AddAssetEvents with
different asset ids, and after a reload three events pointed at an asset that is no longer
tracked. With the feed, every Jupyter event would be recorded twice as well.

A recorder inside a kernel also stalls while a cell runs, since the cell runs on the same loop. In
a run with a 2 s blocking call in place of a cell, a csv that lived for 0.8 s during the call was
never tracked.

#### How to resolve it

Trail takes an advisory lock, `.trail/lock`, with `fcntl.flock` in exclusive, non-blocking mode,
when the first recording session of a process starts. It holds the lock until the process stops
recording. The file holds the pid, for the message. A Trail that cannot take the lock does not
start its watchdog or Jupyter session and says once which process records. The operating system
releases the lock when its holder dies, even on `kill -9`, so no stale lock is left behind. This
lands before the Jupyter session, as part of "Core changes".

Whether a Trail in a kernel should start recording by itself is in the decisions. With the lock, a
notebook that opens a Trail first would make the console read-only, while the notebook stalls on
every long cell.

### Checking events against the saved map

The issues for Matt show that a layer event is a claim about the shared document. The change may
come from a script, a reload or a notebook cell, and it may never reach the file. Trail can check
the claim in two ways.

Confirming is cheap. A layer burst counts as saved only when Jupyter's write of the map is seen,
through the backup bracket or the contents `save` message. A session/v1 `ERROR` for the map, or no
save at all, records the event as not saved.

Diffing is thorough. Trail would keep the last saved layers of each tracked map and compare each
new save with them. The log would then hold the layer changes the file really took, whoever made
them, and the feed would only say who made them. This catches Python changes with no view open,
reloads, lost edits and edits made while Trail was not running. It needs JupyterGIS's file format,
including its migrations, and a copy of each map's last state in `.trail/`. It would also cover the
old open question of reading data paths from the saved map.

#### How to resolve it

This plan confirms saves and does not diff. Diffing is worth deciding once Matt has answered about
the local filter. If the filter lands, the feed reports only GUI changes and confirming is enough.
If it does not, diffing is the only way to keep the log honest.

## Core changes

Trail's own modules need five changes before any Jupyter code can land. None of them is about
Jupyter, and together they are the first step. They are written against the names of the two
refactors in the appendix, which land before this plan: `trail.entries` in place of
`Trail._entry`, and `.checklist` in place of `.markers`.

### Events with no entry

You decided that Trail records events about maps it does not track. Today every record in the log
names an asset or a Dir, and an event with no entry crashes Trail in four places:
`Event.to_record`, `Event.from_record`, `JSONL.read` and `JSONL.record2entry`. Such an event is
written to the log as `"entry": null`.

The two lookups use `trail.entries` as your comment on 10-21-2 asked. `Event.from_record` uses
the strict `trail.entries[entry_id]`, which raises KeyError for an id that is neither an asset nor
a Dir, as the code does by hand today. `JSONL.read` uses `trail.entries.get(entry_id)`, since it
makes the entry from its first record when the Trail does not hold it yet. The local cache in
`JSONL.read` is renamed from `entries` to `seen`, so it reads apart from `trail.entries`.

```python
# Event.to_record (event.py:158-160)
        out["cls"] = type(self).__name__
        # an event about no asset, such as one about a map Trail does not track
        if self.entry is None:
            out["entry"] = None
        else:
            out["entry"] = self.entry.id
        return out

# Event.from_record (event.py:123, 134-142): the id is taken before the event is built
        identifier = record.pop("entry")
        ...
        if identifier is None:
            return event
        entry_id = normalize_id(identifier)
        if resolved_entry is None:
            resolved_entry = trail.entries[entry_id]
        elif resolved_entry.id != entry_id:
            raise ValueError(f"Entry ID does not match event record: {entry_id}")
        event.entry = resolved_entry
        return event

# JSONL.read (event.py:549-557)
                record = json.loads(line)
                identifier = record["entry"]
                entry = None
                if identifier is not None:
                    entry_id = normalize_id(identifier)
                    entry = seen.get(entry_id)
                    if entry is None:
                        entry = trail.entries.get(entry_id)
                        if entry is None:
                            entry = self.record2entry(record, entry_id)
                        seen[entry_id] = entry
                event = Event.from_record(trail=trail, resolved_entry=entry, **record)
```

`record2entry` makes an entry from the first record about its id, and it needs that record's
`src_path`. After `events clear`, a Jupyter event can be the first record about a map. So a
Jupyter event with an entry always carries the map's absolute `src_path`.

### Dotted fields in selections

A selection term such as `verb=updated` compares one field of each record. A Jupyter event keeps
its payload nested, as the server sent it, so a term needs a dotted field such as
`object.layer_id` to read into it. `Select.held` is the method that reads a record's field for a
comparison. It gains dotted fields, and the docstring you asked for.

```python
# select.py, which also imports Mapping from collections.abc
COMPARISON: Final = re.compile(
    r'(?P<field>[A-Za-z_]\w*(?:\.[\w-]+)*)(?P<operator>==|!=|<=|>=|=|<|>)(?P<value>.*)',
    re.DOTALL,
)

    @staticmethod
    def held(
            record: object,
            field: str,
    ) -> object | None:
        """
        Returns the value of a record's field, to compare with a term's value. Returns None if the
        record has no such field.

        - `cls` gives the record's class.
        - A dotted field reads the record's attribute named by its first part. Each later part is a
          key of the dict read so far, so `object.layer_id` reads `event.object["layer_id"]`. A
          part that meets something other than a dict is read as an attribute.
        - An empty string counts as None, since an event leaves an unset field empty.
        - A record held by reference, such as an event's asset, gives its id.
        """
        if field == 'cls':
            out = type(record)
            return out
        parts = field.split('.')
        out = getattr(record, parts[0], None)
        for key in parts[1:]:
            if isinstance(out, Mapping):
                out = out.get(key)
            else:
                out = getattr(out, key, None)
        if (
                out is None
                or out == ''
        ):
            return None
        out = getattr(out, 'id', out)
        return out

# cli/command.py, Query.comparison
        # a path inside a message is relative to its Jupyter server, not to the project
        if (
            not field.endswith("path")
            or field.startswith("object.")
            or not value
        ):
            return super().comparison(item)
```

With this, the following terms work:

- `events verb=updated object.layer_id=L1`;
- `object.new.visible=false` and `object.new.parameters.opacity<0.5`, with the existing bool and
  float coercion;
- `count.modified>3` on dirasset accesses;
- `entry.name=map.jGIS`, and `entry.path=maps/map.jGIS`, which is still read against the project
  root.

Parts after the first may contain hyphens. The first part is an attribute name, which cannot hold
one. A layer in JupyterGIS's older style format keeps its colors under hyphenated keys, so this
term finds the layers whose circles were filled pink:

```
events object.new.parameters.color.circle-fill-color=#dc8add
```

The format JupyterGIS migrates a map to when the map opens keeps style in `symbologyState`
instead. Its hyphenated names, such as `circle-fill-color`, are values in lists, which a dotted
field cannot reach, and the two example maps have no hyphenated key after the migration. So the
hyphen only helps with maps that JupyterGIS has not rewritten yet. It costs one character class in
the pattern, so the plan keeps it.

test_listing.py:147-148 expects `events is_s` and `events (cl` to offer one completion each, so no
new field may start with `is_s` or `cl`.

### The class-name guard

The log stores each event's class by name only, and replay looks the name up in `Event.classes`.
A second class with the same name would silently take the first one's place in replay.
`Event.__init_subclass__` refuses it.

```python
# Event.__init_subclass__ (event.py:105-108)
    def __init_subclass__(cls, **kwargs) -> None:
        # slots=True replaces the class captured by zero-argument super()
        super(Event, cls).__init_subclass__(**kwargs)
        known = cls.classes.get(cls.__name__)
        if known is not None:
            # slots=True builds a second class from the same definition, which may take the
            # first one's name. Any other class with a registered name is refused
            if not (
                known.__module__ == cls.__module__
                and known.__qualname__ == cls.__qualname__
            ):
                raise TypeError(f"{cls.__name__} is already an event class in {known.__module__}")
        cls.classes[cls.__name__] = cls
```

A name, once logged, must stay registered for good.

### Hidden files

This replaces 10-21-2's `.~` check in `_is_markable`, as "Jupyter's backups and other files in the
project folder" explains: a wildcard does not match the leading dot of a file name. `Scopes.decide`
is where both root scopes and dir scopes decide a path, so the rule goes there, in checklist.py
after the rename. A hidden file is decided only by exclusions and by the lines whose last part
starts with a dot.

```python
# checklist.py
def names_dot(pattern: str) -> bool:
    """Whether a pattern's last part starts with a dot, as in `.env` or `.*.csv`."""
    last = (
        pattern
        .removeprefix('!')
        .rstrip('/')
        .rsplit('/', 1)[-1]
    )
    out = last.startswith('.')
    return out


# Scopes
    def decide(self, relative: str) -> str | None:
        """
        Returns the line that decides a relative path, or None if none matches. Case is ignored.
        A wildcard does not match the leading dot of a file name, as in the shell. So a hidden file
        is decided only by an exclusion or by a line that names its dot.
        """
        name = relative.rsplit('/', 1)[-1]
        if not name.startswith('.'):
            index = self.spec.check_file(relative.lower()).index
            if index is None:
                return None
            out = self.lines[index]
            return out
        index = self.hidden_spec.check_file(relative.lower()).index
        if index is None:
            return None
        out = self.lines[self.hidden[index]]
        return out

    @cached_property
    def hidden(self) -> tuple[int, ...]:
        """The positions of the lines that can decide a hidden file."""
        out = tuple(
            index
            for index, pattern in enumerate(self.patterns)
            if (
                pattern.startswith('!')
                or names_dot(pattern)
            )
        )
        return out

    @cached_property
    def hidden_spec(self) -> PathSpec:
        """The lines that can decide a hidden file, compiled for matching."""
        lowered = (
            self.patterns[index].lower()
            for index in self.hidden
        )
        out = PathSpec.from_lines('gitignore', lowered)
        return out
```

> The naming seems rather vague; would `hidden_positions` and `deciding_path` be better? names_dot seem terrible, shouldn't we use `is_dotfile` or equivalent? We should have our checklist ignore dotfiles by default unless explicitly included, right?

On a copy of Trail with this change, the full suite passed after two tests changed:
test_scopes.py:91 and :118 expected `./*` to track `.env`, and now expect that it does not. Four
tests are new:

- a wildcard does not match `.~a.csv` at the root, in a Dir and deeper, nor `.~map.jGIS`;
- a line that names the dot takes hidden files: `./.env`, `.env` in a subfolder and `.*.csv`, while
  `*.env` does not take `.env` and `!data/**` still excludes `data/.a.csv`;
- a slow save never tracks its backup;
- a failed save keeps the asset id of the file.

The last two fail on today's code, with a phantom `.~a.csv` and a changed id.

### One recorder per project

Two Trail processes on one project both record, as "Two Trails recording one project" describes.
The lock comes before the Jupyter session, since the feed would double every Jupyter event as well.
It is the file `.trail/lock`, reached as `trail.lock` and defined in src/trail/lock.py. The
watchdog session and the Jupyter session take it when they start and let go when they close. A
process takes the file once, however many of its sessions record.

```python
# src/trail/lock.py
class Lock(Node):
    """
    The lock that lets one process at a time record a project. It is the file `.trail/lock`, held
    with `fcntl.flock`, and it holds the pid of the process that records.
    """

    _parent: Trail

    def acquire(self, holder: object) -> bool:
        """
        Take the lock for a session that starts recording. Returns False if another process holds
        it. A forked child does not inherit its parent's holders.
        """

    def release(self, holder: object) -> None:
        """
        Let go for a session that stops. The file is unlocked when the last holder lets go.
        """

    @property
    def owner(self) -> int | None:
        """The pid in the lock file, for the message that says which process records."""
```

- `Session.start` and `JupyterSession.start` return False, with one warning naming the owner's pid,
  when `acquire` fails. That Trail still reads the log, and it is not updated live.
- The lock file sits in `.trail/`, which the watcher already ignores, so it never becomes an asset
  or an event.
- flock is advisory and local. It does not protect a folder that two machines sync, such as with
  Syncthing, and it is weaker over NFS. Windows would use `msvcrt.locking`.
- Tests: a subprocess holds the lock, and a second Trail's session does not start, so the log holds
  each event once. After the holder exits, the second Trail can record.

We have JupyterSession to disambiguate from Session; should we rename Session to WatchdogSession?

## The event model

This part decides what a Jupyter event looks like in Trail's log and in Python. A message from a
server's feed is the schema's payload with one key added. jupyter_events validates the payload
against its schema and calls it `data`, both in `emit(schema_id=..., data=...)` and in its
listeners. jupyter_server forwards it to the feed as `dict(schema_id=schema_id, **data)`
(jupyter_server/services/events/handlers.py:69). The value of `schema_id` is the schema's `$id`.
`$id` itself never appears in a message, since it is a JSON Schema keyword of the schema file.

### What a record stores

A record holds the message as received, split back into `schema_id` and `data`, with Trail's own
fields next to them. That pair is what jupyter_server sent and what jupyter_events validates. Pari
can validate each `data` against the schema that `schema_id` names without knowing Trail's class
names, and an additive change to a schema loses nothing. An illustrative record of a merged burst:

```json
{"id": "3f2a…", "timestamp": "2026-10-21T18:02:11.204000+00:00",
 "schema_id": "https://schema.jupytergis.org/events/layer/v1",
 "data": {"subject": "3271840155", "verb": "updated",
          "object": {"document_path": "maps/city.jGIS", "layer_id": "5b1e…",
                     "old": {"type": "VectorLayer", "name": "streets", "visible": true,
                             "parameters": {"source": "S1", "opacity": 1}},
                     "new": {"type": "VectorLayer", "name": "streets", "visible": true,
                             "parameters": {"source": "S1", "opacity": 0.35}}}},
 "server": "http://localhost:8888/", "src_path": "/home/arstneio/project/maps/city.jGIS",
 "saved": "2026-10-21T18:02:12.215000+00:00", "count": {"opened": 3, "modified": 2},
 "started": "2026-10-21T18:02:09.880000+00:00", "messages": 14, "cls": "LayerV1Event",
 "entry": "4e26…"}
```

- `timestamp` is the arrival of the last message, since a message carries no time. If
  jupyter_server#1710 ever passes the emitter's time on, the record gets a field for it.
- A merged burst stores the `data` of its last message, with the first message's `object.old`.
  Fields that differ between the messages of a burst are kept from the last message only, except
  `old`. The merged payload still validates against layer/v1.
- `saved`, `save_error` and `count` describe the save that wrote the change: when Trail saw it,
  the server's error if it failed, and the file events of the write that were folded into the
  event. "Bursts" and "Folding Jupyter's writes" say how they are filled.

The keys of `data` are interned when an event is made. Interning makes equal keys one string
object, which every event then shares. `json.loads` makes new key strings for every message, so
without interning each event would carry its own copy of each key. On 2026-10-27, 2000 layer
`updated` messages took 6,797 bytes per event as parsed and 5,380 with interned keys (CPython 3.12,
shared objects counted once). Only keys are interned, never values such as layer ids, paths or
subjects, since an interned string lives as long as the process.

Three other ways to store the payload were rejected:

- A flat `data` keyed by dotted path, such as `data["object.layer_id"]`. It saves about 1.5 KB per
  layer update. In exchange it needs code to flatten each payload and nest it again for the log, a
  test that no schema key holds a dot, and a special case in `Select`.
- One dataclass field per schema key. It drops a property it does not know, and `from_record`
  raises TypeError on one.
- Nested TypedDicts for `data`, which 10-21-2 had. Trail's own code reads the payload through about
  six paths, and the typed properties below give mypy and PyCharm the types callers use. A
  TypedDict per schema version would add type parameters, a `cast` and a drift test, and only
  direct `event.data[...]` reads in tests or notebooks would use them.

### The event classes

Every event from a Jupyter server is a `JupyterEvent`, defined in src/trail/jupyter/event.py. The
pipeline makes one when a burst of messages closes, and replay makes one from each record whose
`cls` names a subclass. Below it is one base class per kind of JupyterGIS object, and below each
kind one leaf class per `$id`:

```
Event                           trail/event.py
└── JupyterEvent                any message from a server's feed: schema_id, data, server,
    │                           src_path, saved, count; the registry by $id; the class of a
    │                           JupyterGIS $id that has no class of its own
    ├── ContentsServiceV1Event  jupyter_server's contents_service/v1: a run of Jupyter's saves
    │                           of one tracked file, with no layer event
    ├── DocumentEvent           opened, closed
    │   └── DocumentV1Event     .../events/document/v1
    ├── LayerEvent              added, updated, removed; merges bursts; the layer's name and
    │   │                       the change summary
    │   └── LayerV1Event        .../events/layer/v1
    └── ViewportEvent           moved: the center and the zoom
        └── ViewportV1Event     .../events/viewport/v1
```

On the names:

- `JupyterEvent` is the only base class of events from Jupyter, as your comment on 10-21-2
  suggested. 10-21-2 put `JupyterGISEvent` between them, to hold the registry, the payload
  properties and the burst fields. `ContentsServiceV1Event` was the only class outside it, and it
  can carry the same fields, so the two classes become one. `JupyterEvent` takes the name of the
  stub that event.py removes (lines 504-513). No log on this machine contains that stub.
- There is one leaf class per `$id`, named after it, such as `LayerV1Event`. A leaf is three lines:
  the decorator, the class and `schema`. A leaf stays registered after a `LayerV2Event` arrives,
  since old records name it.
- `cls=LayerEvent` selects every version, because `Select` compares `cls` through the MRO.
- The other design is a single `LayerEvent`, with the version only in `schema_id`. It never keeps
  an old class, but a breaking v2 pushes version branches into its properties.

`Envelope` is a mixin that `JupyterEvent` shares with `Message`, the class that keeps a received
message in memory (see "Messages"). Both have the fields `schema_id` and `data`. Its properties
`subject`, `verb` and `object` read `data` with `.get`, and `kind` reads the object's name out of
`schema_id`. `object` comes last in the class body, since that name shadows the builtin for
whatever follows it. It declares `__slots__ = ()`. Without that, every event and every message
would get a `__dict__` again, since one class without `__slots__` among the bases gives instances
a `__dict__`.

> What benefit do we get from the Envelope mixin? Do we need subject, verb, and object properties? Isn't that just remnant from a previous idea about having properties--but now we're just going to have a `data` field for the data passed from jupyter ?

```python
# src/trail/jupyter/event.py
# copied, since Trail does not depend on JupyterGIS
PREFIX: Final = "https://schema.jupytergis.org/"
# events.md: an $id always reads https://schema.jupytergis.org/events/<object>/v<version>. The
# plan calls <object> the kind
SCHEMA_ID: Final = re.compile(
    r"https://schema\.jupytergis\.org/events/(?P<kind>[\w-]+)/v(?P<version>\d+)",
)


@dataclass(kw_only=True, slots=True, repr=False)
class JupyterEvent(Envelope, Event):
    """
    A message from a Jupyter server's event feed, as Trail records it. The payload is kept as the
    server sent it. JupyterEvent itself records a JupyterGIS `$id` that has no class of its own.
    """

    # the class of each $id Trail knows
    registry: ClassVar[dict[str, type[JupyterEvent]]] = dict()
    # the $id a leaf class records, or "" for a base class
    schema: ClassVar[str] = ""
    # whether a run of updated messages becomes one event
    is_merged: ClassVar[bool] = False
    # whether every view of a map sends the message, so a copy from another view is a duplicate
    is_shared: ClassVar[bool] = True
    # the dotted fields `events` offers as completions, besides the event's own fields
    selectable: ClassVar[tuple[str, ...]] = (
        "schema_id",
        "subject",
        "verb",
        "object.document_path",
    )

    entry: Asset | None = field(default=None, init=False, repr=False)
    # the $id of the schema the payload follows
    schema_id: str
    # the payload as the server sent it, with interned keys
    data: dict[str, Any] = field(repr=False)
    # the server's url, never its token
    server: str = ""
    # the map's absolute local path, or "" for a map Trail could not place
    src_path: str = ""
    # the arrival of the confirmed save that wrote this change, or None if none was seen
    saved: datetime | None = None
    # the server's message if that save failed
    save_error: str = ""
    # the file events of Jupyter's write that were folded into this event, by event type
    count: dict[str, int] = field(default_factory=dict)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        # slots=True replaces the class captured by zero-argument super()
        super(JupyterEvent, cls).__init_subclass__(**kwargs)
        if cls.schema:
            JupyterEvent.registry[cls.schema] = cls

    def __post_init__(self) -> None:
        Event.__post_init__(self)
        # the pipeline and a replay both pass the payload as json.loads made it
        self.data = interned(self.data)
        # a replay passes the datetime as the log holds it
        if isinstance(self.saved, str):
            self.saved = datetime.fromisoformat(self.saved)

    def apply(
            self,
            trail: Trail,
            *,
            replay: bool = False,
    ) -> Asset | None:
        """
        Log the event and return its map's asset. A Jupyter event changes nothing in the Trail, so
        a replay only returns the asset.
        """
        if not replay:
            trail.events.append(self)
        out = self.entry
        return out

    def summary(self) -> Iterator[tuple[str, object]]:
        """
        Yields the rows the console shows for the event. This class shows the object cut short.
        """
        yield "schema_id", self.schema_id
        yield "object", shown(self.object)


@dataclass(kw_only=True, slots=True, repr=False)
class LayerEvent(JupyterEvent):
    """
    A layer added, updated or removed in a map. A run of updates to one layer becomes one event,
    which keeps the first message's `old` and the last message's `new`.
    """

    is_merged: ClassVar[bool] = True
    selectable: ClassVar[tuple[str, ...]] = (
        *JupyterEvent.selectable,
        "layer",
        "layer_id",
        "object.old.name",
        "object.new.name",
        "object.new.type",
        "object.new.visible",
    )

    # the arrival of the first message of a merged run
    started: datetime | None = None
    # how many messages the event merges
    messages: int = 1

    def __post_init__(self) -> None:
        JupyterEvent.__post_init__(self)
        # a replay passes the datetime as the log holds it
        if isinstance(self.started, str):
            self.started = datetime.fromisoformat(self.started)

    @property
    def layer_id(self) -> str | None:
        """The layer's id, which keys it in the map's `layers`."""
        out = self.object.get("layer_id")
        return out

    @property
    def old(self) -> dict[str, Any] | None:
        """The whole layer before the change, or None for `added`."""
        out = self.object.get("old")
        return out

    @property
    def new(self) -> dict[str, Any] | None:
        """The whole layer after the change, or None for `removed`."""
        out = self.object.get("new")
        return out

    @property
    def layer(self) -> str | None:
        """The layer's name after the change, or before it for `removed`."""
        found = (
            self.new
            or self.old
            or dict()
        )
        out = found.get("name")
        return out

    def changes(self) -> list[tuple[str, object, object]]:
        """
        Returns each leaf that differs between `old` and `new`, by dotted path, with both values.
        """
        before = leaves(self.old or dict())
        after = leaves(self.new or dict())
        paths = dict.fromkeys([*before, *after])
        out = [
            (path, before.get(path), after.get(path))
            for path in paths
            if before.get(path) != after.get(path)
        ]
        return out

    def summary(self) -> Iterator[tuple[str, object]]:
        yield "layer", self.layer
        yield "layer_id", self.layer_id
        if self.verb == "updated":
            yield "change", self.describe()


@dataclass(kw_only=True, slots=True, repr=False)
class LayerV1Event(LayerEvent):
    schema: ClassVar[str] = "https://schema.jupytergis.org/events/layer/v1"
```

The typed properties are what mypy checks in place of TypedDicts. `event.layer_id` is
`str | None`, and a v2 class overrides only the properties whose paths moved. Any other dotted term,
such as `object.new.parameters.opacity`, still works when typed. `selectable` only decides which
terms the console offers as completions.

The rest of the module:

- `DocumentEvent` sets `is_shared = False`. Each view sends its own `opened` and `closed`, so they
  are never duplicates. Its `summary` is empty.
- `ViewportEvent` sets `is_shared = False`, since only the view that moved reports it. It is not
  merged, since the plugin already sends one event per pause. Its typed properties are `center`, a
  pair of floats, `zoom` and `extent`, all in the map's projection, which the event does not name.
  Its `summary` shows the center and the zoom. Its `timestamp` is its arrival, about 10 s after the
  movement ended.
- `ContentsServiceV1Event` is named after jupyter_server's schema `contents_service/v1`. It
  records a run of Jupyter's saves of one tracked file that no layer event accounts for, as "Saves
  with no layer event" describes. It keeps the payload of the last contents `save`, and adds
  `started` and `saves`, the time of the first save and the number of saves. Its `verb` is the
  contents action, and the console renders it as `saved`.
- Each leaf, such as `DocumentV1Event` and `ViewportV1Event`, only sets `schema`.
- `interned(value)` returns the payload with the keys of every dict in it interned. `leaves(value)`
  returns every leaf below a dict by dotted path, such as `parameters.opacity`, to compare two
  layers. Both live in event.py, which replaces 10-21-2's schema.py.
- `describe()` lists at most three of `changes()` as `path old → new`, then `+N more`. It gives
  `none` for a burst that ends where it began. `shown(value)` writes a value as json and cuts it at
  24 characters.
- `_repr_items` yields the rows of the example below, in that order, and leaves out None and "",
  as `Event._repr_items` does. With no `src_path`, the raw path shows as `object.document_path`,
  which is also its selection field.

The console shows a merged event like this (illustrative values):

```
7. LayerV1Event
    id: 3f2a9c0e5b7d4e1f8a6b2c4d0e9f1a3b
    timestamp: 18:02:11.204
    verb: updated
    src_path: maps/city.jGIS
    layer: streets
    layer_id: 5b1e0c4e-9b7f-4f43-a1d2-6f0c8e2b7a91
    change: parameters.opacity 1 → 0.35
    started: 18:02:09.880
    messages: 14
    saved: 18:02:12.215
    count: opened 3, modified 2
    subject: 3271840155
    server: http://localhost:8888/
    entry: 4e26a1b0c39d4f6e8b7a2c5d1e0f9a8b
```

### Evolving a schema

events.md sets three rules. A breaking change gets a new file and a new `$id`. Both versions can be
published side by side. Additive changes stay in the same `$id`. For Trail that means:

- Dispatch is on the full `$id`, through `JupyterEvent.registry`. A breaking `layer/v2` adds
  `LayerV2Event(LayerEvent)`, which overrides the properties whose paths moved. Nothing else
  changes.
- An `$id` Trail does not know, such as a future `source/v1`, is recorded by `JupyterEvent` itself.
  It keeps `data` whole, shows `schema_id` and the object cut short, and never merges.
- `viewport/v1`, added on 2026-10-21, took this path upstream: a new file and a new `$id`. Trail
  adds `ViewportV1Event`. A Trail without it records viewport events as plain `JupyterEvent`s.
- An additive property stays in `data`. It can be selected as `object.<key>`, and `jupyter show`
  prints it.
- An additive verb is recorded but not merged, and it is rendered with the default style.
- Side-by-side versions are not built yet, since no v2 schema exists. When one does, a copy of an
  action in a second version is dropped like a duplicate, since both copies have the same server,
  subject, verb, map and layer and arrive within the duplicate window. The copy of a class Trail
  knows wins over a plain `JupyterEvent`, then the higher version wins.
- Three drift tests guard the copies of the YAML schemas in `tests/jupyter/schemas/`:
  - Each leaf's `schema` is the `$id` of a copy, and every upstream `$id` has a leaf. Each
    `object.` term in a class's `selectable` exists as a path in its schema, or for a layer's own
    keys in `definitions.jGISLayer` of JupyterGIS's jgis.json. A key renamed upstream then fails a
    test.
  - Each verb Trail treats on its own, such as the `updated` that merges, is in the schema's enum.
  - When `jupytergis_core` imports, the copies are compared with its `EVENT_SCHEMAS`. Otherwise the
    test skips. This .venv has it only from the 10-20 experiments, and `uv sync` removes it.

## The runtime

This part covers what runs while Trail listens: finding servers, subscribing to their feeds, and
the pipeline that turns messages into events. It keeps the split you agreed to on 10-21-2.
`trail.jupyter` holds what is stable, and `trail.jupyter.session` holds one run of the
connections, the way `trail.watchdog` and `trail.watchdog.session` split the file watching.

### `trail.jupyter` and `trail.jupyter.session`

`trail.jupyter` is a `Jupyter`, defined in src/trail/jupyter/jupyter.py. It decides what Trail
connects to, and it holds no connection itself. `Node._jupyter` follows the pattern of
`_watchdog`, and `Trail.jupyter` is a plain cached_property.

- `jsons` holds the runtime files of the Jupyter servers on this machine, by path. "Finding
  servers" describes it.
- `connect(*, url, token, root)` adds a server Trail cannot discover, such as one in a container,
  and `disconnect(url)` stops listening to one. Both are remembered by url for the rest of the
  process, so a restarted server and a new session keep them.
- `overlaps(root)` decides which servers connect by themselves, and `is_inside(folder)` decides
  whether a map is part of the project. "Finding servers" and "Resolving the map" give the rules.
- `on_notice` and `on_message` are callbacks the console sets. Without the console, notices go to
  the `trail.jupyter` logger.
- `__repr__` lists the session's servers while a run is active. Otherwise it lists the servers a
  run would connect to, as `Watchdog.__repr__` lists the watches a run would place. It never shows
  a token.

`trail.jupyter.session` is a `JupyterSession`, defined in src/trail/jupyter/session.py. It is one
run of the connections. It starts the tasks, holds what they find, and records what is still open
when it stops.

- `servers`, `pipeline`, `writes`, `messages` and `tasks` are cached_properties. `servers` holds a
  `Server` for each Jupyter server of the run, as the next section describes. `pipeline` turns
  messages into events, and `writes` folds Jupyter's writes of tracked files, as "The receive
  pipeline" and "Folding Jupyter's writes" describe.
- `bool(session)` says whether a run has started in this process and has not closed, and
  `is_active` whether its tasks are alive, as on the watchdog's `Session`.
- The descriptor that makes `watchdog.session` on first read, closes and drops it on `del`, and
  forgets it in a forked child moves into a base class that both sessions use.
  `del trail.jupyter.session` gives the next read a clean run.
- `async with trail.jupyter.session` calls `start()` and `stop()`.
- The closer task's `run` records what is still open in a `finally` guarded by
  `asyncio.current_task()`, as `Session.run` does. A Ctrl-C during `stop` still records the bursts,
  and an atexit hook covers an interpreter that exits without `stop`.
- `drop()` forgets the open bursts. `Events.clear` calls it next to `session.accesses.drop()`.
  `Trail.offtrail` calls `flush(asset)` before the RemoveAssetEvent, as `DirAsset.offtrail`
  flushes a dirasset's accesses.
- `summary` is the console header's segment, read from memory only.

Nothing starts when a Trail opens, since connecting to servers is a side effect. A library user
calls `trail.jupyter.session.start()` or `async with trail.jupyter.session`, and the console does
this itself.

```python
# src/trail/jupyter/session.py
class JupyterSession(Node):
    """
    One run of Trail's connections to Jupyter servers. It is made on first read of
    `trail.jupyter.session` and kept until it is deleted.
    """

    _parent: Jupyter
    # seconds stop() waits for the autosave of a burst that changed a map
    settle = 1.5
    # whether a run has started and not closed since
    is_started = False

    def start(self) -> bool:
        """
        Start the discovery and closer tasks on the running loop. Returns False if no loop runs,
        and True if the run is active afterward.
        """
        if any(
            not task.done()
            for task in self.tasks
        ):
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        trail = self._trail
        # one process at a time records a project. See "One recorder per project"
        if not trail.lock.acquire(self):
            return False
        # Jupyter's writes are folded as the watchdog reports them. See "Folding Jupyter's writes"
        trail.watchdog.session.observers.append(self.writes.observe)
        discovering = self.servers.run()
        closing = self.pipeline.run()
        discovery = loop.create_task(discovering, name="jupyter-discovery")
        closer = loop.create_task(closing, name="jupyter-closer")
        self.tasks[:] = [discovery, closer]
        self.is_started = True
        self.pid = os.getpid()
        return True

    async def stop(self) -> None:
        """
        Wait up to `settle` seconds for the autosave of open bursts, end the subscriptions, then
        record the bursts. Stopping never raises for a task that failed, since that was reported.
        """
        trail = self._trail
        try:
            await self.pipeline.settle(self.settle)
        finally:
            # the subscriptions end first, so no message arrives while the bursts are recorded
            tasks = [
                *self.servers.tasks(),
                *self.tasks,
            ]
            for task in tasks:
                task.cancel()
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                self.tasks.clear()
                # the held file events are decided before the bursts they may fold into close
                trail.watchdog.session.observers.remove(self.writes.observe)
                self.writes.settle(math.inf)
                self.pipeline.close()
                trail.lock.release(self)
                self.is_started = False
```

### Servers

Trail talks to each Jupyter server through a `Server`, defined in src/trail/jupyter/server.py. A
`Server` holds the server's runtime file, the state of the subscription to its event feed, and
what the feed has sent so far. The pipeline asks it for the feed's address, the headers that carry
the token, and the local path of a map. The session keeps its servers in a `Servers`, reached as
`trail.jupyter.session.servers` and keyed by url. They belong to one run, and the next run finds
the servers again. A stopped server keeps its entry for the rest of the run, so the console still
shows its counts.

Each attribute below is documented by its own comment, directly above it, as your comment on
10-21-2 asked. The class docstrings only say what each class is for. The plan uses this form for
every class it adds.

```python
# src/trail/jupyter/server.py
# the life of a Server, from the moment Trail finds it
State = Literal[
    # found, and not connected, since its root cannot serve a file Trail tracks
    "available",
    "connecting",
    # its event feed is open
    "subscribed",
    # the socket dropped or could not open, and Trail tries again after the backoff
    "retrying",
    # the server refused the token or the certificate
    "refused",
    # the server has no event feed: no /api/events/subscribe, or no WebSocket there
    "unsupported",
    # the subscription task crashed
    "error",
    # `jupyter disconnect` stopped listening to it
    "disconnected",
    # its file or its process is gone
    "stopped",
]


@dataclass(eq=False, kw_only=True)
class Counts:
    """
    What one server's feed has sent during a session, for the console header and `jupyter servers`.
    """

    # frames received
    received: int = 0
    # messages that became an event of their own
    recorded: int = 0
    # messages merged into another message's event
    merged: int = 0
    # skipped messages by reason, such as "duplicate"
    skipped: Counter[str] = field(default_factory=Counter)
    # messages of schemas the pipeline does not use, by schema_id
    other: Counter[str] = field(default_factory=Counter)


@dataclass(eq=False, kw_only=True)
class Server:
    """
    Trail's handle on one Jupyter server during a session. The pipeline asks it for the feed's
    address, the headers that carry the token, and the local path of a map.
    """

    # the session's servers, which hold this one
    servers: Servers = field(repr=False)
    # its `#N` in the console. Numbers are not reused within a session
    number: int
    # the runtime file it was found with, or the details given to `connect`
    json: JSON
    # how the subscription is doing
    state: State = "available"
    # why the subscription last failed, for `jupyter servers`
    last_error: str = ""
    # the numbers the console header shows for this server
    counts: Counts = field(default_factory=Counts)
    # the probe's answer for each $id Trail knows: the version the server has, or None
    schemas: dict[str, int | None] = field(default_factory=dict)
    # the new path of each path renamed through the server's contents API
    renames: dict[str, str] = field(default_factory=dict)
    # the monotonic time the server last reloaded each path from disk
    reloading: dict[str, float] = field(default_factory=dict)
    # the subscription task, while it runs
    task: asyncio.Task[None] | None = None
    # seconds before the first retry
    backoff: float = 0.5
    # the most seconds any retry waits
    ceiling: float = 30.0

    @property
    def is_running(self) -> bool:
        """Whether the server's process is alive and is the one that wrote its file."""
        pid = self.json.pid
        if pid is None:
            return False
        try:
            process = psutil.Process(pid)
            started = process.create_time()
        except psutil.Error:
            return False
        # a process that started over a minute after the file reused the pid. The minute allows
        # for a clock step after boot
        out = started <= self.json.written + 60
        return out

    @property
    def is_stopped(self) -> bool:
        """Whether the server's file or process is gone."""

    @property
    def events_url(self) -> str:
        """The url of the server's event feed. See "Subscription"."""

    def headers(self) -> dict[str, str]:
        """Returns the headers of a request to the server, with the token."""

    def locate(self, document_path: str) -> Path | None:
        """
        Returns the local path of a map the server names by its path under `json.root`.
        """

    def is_reloading(
            self,
            found: Map,
            seen: float,
    ) -> bool:
        """
        Whether the server reloaded the map from disk within `reloaded` seconds before `seen`.
        """

    async def listen(self, socket: WebSocketClientConnection) -> None:
        """Hand each frame of an open feed to the pipeline until the socket drops."""

    def fail(
            self,
            state: State,
            reason: str,
    ) -> None:
        """Set the state and `last_error`, and tell the console with a notice."""


class Servers(Node):
    """
    The session's servers, by url. A server found in a runtime file and one given to `connect`
    share an entry if their urls match.
    """

    _parent: JupyterSession
    # seconds between two refreshes
    interval = 2.0

    def __init__(self, parent: JupyterSession | None = None) -> None:
        Node.__init__(self, parent)
        # each server of the run, by url
        self.data: dict[str, Server] = dict()
        # the number the next server gets
        self.next = 1

    async def run(self) -> None:
        """Refresh every `interval` seconds until the task is cancelled."""

    def refresh(self) -> None:
        """
        Bring the servers in line with `trail.jupyter.jsons` and the servers given to `connect`. A
        new live server connects if its root overlaps a Dir or it was given to `connect`. A new
        runtime file with a known url gives that server its new `json`, as after a restart on the
        same port. A server whose file or process is gone is stopped.
        """

    def tasks(self) -> list[asyncio.Task[None]]:
        """Returns the subscription tasks that are running."""
```

- `number` is what the console shows as `#2`, and what `jupyter connect 2` takes.
- A server whose file or process is gone is `stopped`. Its task is cancelled, and its bursts are
  recorded.
- A server with a unix socket, or with a password and no token, stays `available`. This plan does
  not connect it.
- `fail` puts the reason in the header and the feed, so a server that stops listening says why. The
  done-callback of a subscription task calls `fail("error", ...)` when the task crashes.

### Finding servers

Every Jupyter server writes a file `jpserver-<pid>.json` into Jupyter's runtime directory when it
starts, and deletes it when it stops cleanly. A server that crashes leaves its file behind.
`jupyter server list` reads these files, and so does Trail. Each file holds the server's `url`,
`token`, `root_dir`, `pid`, `sock` and a few other keys (jupyter_server's `ServerApp.server_info`).
A `JSON`, defined in server.py, is one file read into the fields Trail uses:

- `url` and `token` open the server's event feed;
- `root` is the folder the server serves. The rule below compares it with the Dirs, and
  `Server.locate` joins it with a message's `document_path` to find the map on disk;
- `pid` and `written` tell a live server from a file a crash left behind.

`trail.jupyter.jsons` holds these files, as your comment on 10-21-2 suggested, so the name
matches `server.json`. It is a `JSONs`, a mapping from each runtime file's path to its `JSON`, kept
on `trail.jupyter` because the files outlive a run. Its `refresh()` lists the directory, reads a
file again only when its mtime changed, and drops the files that are gone. Each `Server` holds the
`JSON` it was found with, as `server.json`, the way the Trail holds its `project.json` as
`trail.json`. A server given to `connect` gets a `JSON` built by hand, with no path. Since `jsons`
is keyed by path, `trail.jupyter` holds those servers by url in `connected`.

```python
# src/trail/jupyter/server.py
@dataclass(frozen=True, slots=True, kw_only=True)
class JSON:
    """
    One `jpserver-<pid>.json` file from Jupyter's runtime directory, or the same details given to
    `connect` for a server that writes no file.
    """

    # the file, or None for a server given to `connect`
    path: Path | None = None
    # where the server listens, such as http://localhost:8888/
    url: str
    # the folder the server serves, resolved
    root: Path
    # the token that authenticates Trail. It is never shown
    token: Secret = field(repr=False, compare=False)
    # the server's process
    pid: int | None = None
    # the file's mtime, to tell the server from a later process that reused its pid
    written: float = 0.0
    # the unix socket the server listens on in place of a port, or ""
    sock: str = ""

    @classmethod
    def read(cls, path: Path) -> JSON | None:
        """Returns the file's details, or None for a file that cannot be parsed yet."""


class JSONs(Mapping[Path, JSON]):
    """
    The `jpserver-*.json` files in Jupyter's runtime directory, each read into a `JSON`, by path.
    """

    def __init__(self, directory: Path) -> None:
        # Jupyter's runtime directory, which tests replace
        self.directory = directory
        # each file's JSON, by path
        self.data: dict[Path, JSON] = dict()

    def refresh(self) -> None:
        """
        List the directory again. A new file, or one whose mtime changed, is read. A file that is
        gone is dropped. A file that cannot be parsed yet is left out until a later refresh.
        """
```

- `JSONs` implements `__getitem__`, `__iter__` and `__len__` over `data`.
- `Secret` holds the token. Its repr is `<secret>`, and pickling it raises TypeError.
- `JSON.read` returns None for a file it cannot parse yet, including a `root_dir` that is not a
  string or a `pid` that is not an int. The next refresh reads it again.
- `Servers.refresh` runs every 2 s, and again after a socket drops. It counts stale files and
  never deletes them. `jupyter server list` deletes them, and Trail should not write to Jupyter's
  directory.
- psutil is used for liveness, not `os.kill(pid, 0)`, since that call terminates the process on
  Windows.

A server connects by itself when its resolved root overlaps a Dir: the root is a Dir's folder, a
folder above a Dir, or a folder inside a Dir that tracks subfolders. "Which servers Trail listens
to" gives the reasons. 10-21-2 connected only to a root inside a Dir, which left out a JupyterLab
started from the home folder.

```python
# src/trail/jupyter/jupyter.py, Jupyter
    def overlaps(self, root: Path) -> bool:
        """
        Whether a server rooted at `root` can serve a file a Dir tracks. Its root is a Dir's
        folder, a folder above one, or a folder inside a Dir that tracks subfolders.
        """
        out = any(
            root == entry.path
            or entry.path.is_relative_to(root)
            or (
                entry.is_recursive
                and root.is_relative_to(entry.path)
            )
            for entry in self._trail.dirs
        )
        return out
```

| The server's resolved root, with Dirs `./` and `data/` | Connects by itself |
|---|---|
| the project, or `project/data` | yes |
| `project/notebooks`, inside the recursive root Dir | yes |
| the project's parent, or the home folder | yes; its messages about other projects are counted and dropped |
| an unrelated folder | no; it cannot name a file Trail tracks |

A server that does not connect gets one row in the feed:
`jupyter: #2 serves ~/other, outside this project; 'jupyter connect 2' listens to it`. A manual
connect or disconnect is remembered by url for the rest of the process, so a restarted server
keeps it.

### Subscription

Once a server connects, Trail holds its event feed open. Trail needs two network clients for that:
a WebSocket client for the feed, and an HTTP client for the probe in the next section.

You asked what tornado is and why it is needed. tornado is the asynchronous networking library
that Jupyter Server is built on. Jupyter Server's HTTP server, its WebSocket handlers and the event
feed itself all run on it. Python's standard library has neither a WebSocket client nor an
asynchronous HTTP client. tornado's `websocket_connect` and `AsyncHTTPClient` provide both. They
run on asyncio, so the subscriptions share the console's event loop with the watchdog session.
Trail already installs tornado: it depends on `ipykernel>=7.3.0`, which requires `tornado>=6.4.1`,
and Trail's .venv has tornado 6.5.10. Declaring it in pyproject.toml only states a dependency that
Trail now imports itself. The other choice is the `websockets` library, which the decisions at the
end compare. `httpx` and `requests` have no WebSocket client, and `aiohttp` would be a new
dependency.

There is one task and one socket per server. The token goes only in the `Authorization` header,
never in the url, so no url in an error message carries it.

```python
# src/trail/jupyter/consumer.py
SUBSCRIBE: Final = "api/events/subscribe"
# 599 is tornado's code for a timeout
RETRIED: Final = frozenset({500, 502, 503, 504, 599})


async def subscribe(server: Server) -> None:
    """
    Hold the server's event feed open, and open it again after a drop, until the server stops,
    refuses Trail, or has no feed.
    """
    from tornado.httpclient import HTTPClientError, HTTPRequest
    from tornado.websocket import WebSocketError, websocket_connect

    delay = server.backoff
    while True:
        server.state = "connecting"
        request = HTTPRequest(
            server.events_url,
            headers=server.headers(),
            connect_timeout=10,
            request_timeout=10,
        )
        try:
            # Jupyter sends no pings, and Trail's find a socket left half open by a sleeping laptop
            socket = await websocket_connect(request, ping_interval=20, ping_timeout=20)
        except HTTPClientError as error:
            if error.code == 404:
                server.fail("unsupported", "no /api/events/subscribe; it needs jupyter_server 2")
                return
            if error.code not in RETRIED:
                # a 403, or a redirect to the login page. A server restarted on the same port
                # refuses the old token, so its file is read again and the new token is tried
                used = server.json
                server.servers.refresh()
                if server.is_stopped:
                    return
                if server.json is used:
                    server.fail("refused", "the token was refused")
                    return
                continue
            server.last_error = f"http {error.code}"
        except WebSocketError as error:
            # the url answered, but not as a WebSocket
            server.fail("unsupported", str(error))
            return
        except ssl.SSLCertVerificationError as error:
            # a certificate that fails verification fails again on every retry
            server.fail("refused", type(error).__name__)
            return
        except OSError as error:
            server.last_error = type(error).__name__
        else:
            delay = server.backoff
            await server.listen(socket)
        server.state = "retrying"
        # a socket that dropped may belong to a server that stopped
        server.servers.refresh()
        if server.is_stopped:
            return
        await asyncio.sleep(delay)
        delay = min(delay * 2, server.ceiling)
```

- `events_url` is the runtime file's `url` with `http` → `ws` and `https` → `wss`, plus
  `api/events/subscribe`. `url` already holds `base_url`, so JupyterHub's `/user/<name>/` works.
- The backoff starts at 0.5 s, doubles up to 30 s, and resets on each subscribe.
- `Server.listen(socket)` sets the state `subscribed` and reads frames until `read_message()`
  returns None. tornado returns None when the socket drops, since Jupyter sends no close frame.
- `listen` hands each frame to the pipeline inside `try/except Exception`. A message that fails is
  counted, and the subscription goes on.
- A done-callback on the subscription task calls `server.fail("error", ...)`, so a crash shows in
  the header and the feed.
- tornado ignores proxy variables, follows no redirects, and closes without waiting for the other
  side. Its message limit is 10 MiB.
- 10-21-2 gave up on a server after any 403. A restarted server writes a new runtime file with a
  new token, so the loop now reads the file again and tries once more when the file changed.

### Probe

Once per server, Trail checks which JupyterGIS schemas the server has. For each `$id` Trail knows,
it POSTs `{"schema_id": <id>, "version": "0", "data": {}}` to `/api/events` with tornado's
`AsyncHTTPClient`. The request uses the same header, a 5 s timeout and `raise_error=False`.

- "was not found in the schema registry" means the server does not have that schema.
- "Unregistered version: '0'≠'1'" means it does, and gives its version.
- No version is "0", so the server rejects the POST before any listener sees it, JupyterLab
  included. A POST with the right version and empty data would be checked only while someone
  listens, and it would be broadcast if a future schema required nothing.
- The answers only feed the `jupyter servers` rows and the notices. If the server has no JupyterGIS
  schema, one row says its JupyterGIS has no event feed, as in every release so far.
- The probe does not ask about versions Trail does not know. Each probe prints three warning lines
  in the Jupyter terminal, and a warning about a missing `layer/v2` reads like a broken emitter.
- The probe runs only for a server rooted at or inside a Dir, so it never prints warnings in the
  terminal of a JupyterLab rooted at the home folder. It runs once per server process, not on each
  reconnect.
- The two strings come from different packages. "Unregistered version" comes from jupyter_server's
  handler, and "was not found in the schema registry" from jupyter_events. Both are error messages,
  not an API, so a test pins them, and an answer that matches neither counts as unknown, not as
  absent.

### Messages

The pipeline keeps every JupyterGIS message it receives in memory, recorded or not, so the console
can show what came in and why a message was skipped. A `Message`, defined in
src/trail/jupyter/message.py, is one received message. `Messages` holds the last 1000 of them,
reached as `trail.jupyter.session.messages`.

```python
# src/trail/jupyter/message.py
Outcome = Literal["pending", "recorded", "merged", "used", "skipped"]


@dataclass(kw_only=True, slots=True, repr=False)
class Message(Envelope):
    """One message from a server's feed, as received, with what became of it."""

    id: str = field(default_factory=lambda: uuid4().hex)
    # its number in this process, kept when older messages are dropped
    seq: int
    # its arrival, since a message carries no time
    timestamp: datetime
    # the url of the server that sent it
    server: str
    # the $id of the schema its payload follows
    schema_id: str
    # the payload as received
    data: dict[str, Any] = field(repr=False)
    # the map's absolute local path, once resolved
    src_path: str = ""
    # what became of it
    outcome: Outcome = "pending"
    # why it was skipped
    reason: str = ""
    # the id of the event that recorded it
    event: str = ""
    selectable: ClassVar[tuple[str, ...]] = (
        "schema_id",
        "subject",
        "verb",
        "object.document_path",
        "object.layer_id",
    )
```

- `Message.make` takes `schema_id` out of the received message and keeps the rest as `data`, so
  the pipeline, the duplicate check and `Select` read a message as they read an event.
  `jupyter show` prints `dict(schema_id=..., **data)`, which is the message as received.
- `_repr_items` yields `seq`, the clock time, `server`, `schema_id`, `verb`, the contents `action`,
  `subject`, `src_path`, `outcome`, `reason` and `event`, leaving out None and "". `__repr__` is
  built from it, as `Event.__repr__` is.
- `Messages` is a `Collection` capped at 1000. It drops the oldest inside `__setitem__`, and its
  `ids` stays a list, since `Select` slices it. Its parent is the Trail, like the other
  collections, and `trail.jupyter.session` holds it.
- `seq` keeps a message's number after eviction, while positions in a listing shift as the oldest
  go.
- `jupyter messages outcome=skipped reason=duplicate -10:` goes through the same `Select` as
  `events`.
- `Watch` cannot follow a buffer that evicts, since its cursor is a position. The live tail
  therefore uses `jupyter.on_message`.

### The receive pipeline

The `Pipeline`, defined in src/trail/jupyter/pipeline.py and reached as
`trail.jupyter.session.pipeline`, turns the messages of every server's feed into events. Each
`Server` hands it the frames it reads. The pipeline needs three things beyond the messages
themselves. `resolve` finds the map a message is about and returns it as a `Map`, which the next
section introduces. A `Burst` collects the messages about one layer until the map's save, as
"Bursts" describes. And Jupyter's own messages tell the pipeline when a file was saved, renamed or
reloaded.

> How about `Consumer`,reached as `trail.jupyter.session.messages.consumer`; the attribute access makes it more clear that it's consuming messages; and it's more intuitive that the messages are being removed and being processed into something?

You asked for docstrings that say what these methods are and why they are needed. They follow.

```python
# src/trail/jupyter/pipeline.py
CONTENTS: Final = "https://events.jupyter.org/jupyter_server/contents_service/v1"
COLLABORATION: Final = "https://schema.jupyter.org/jupyter_collaboration/session/v1"


class Pipeline(Node):
    """
    Turns the messages of every server's feed into events. Each Server hands it the frames it
    reads, and the closer task records the bursts whose time has come.
    """

    _parent: JupyterSession
    # seconds after its last message that a burst closes, if no save of its map is confirmed
    pause = 2.0
    # seconds a burst stays open after the save that wrote it, for the watchdog to deliver the write
    grace = 0.25
    # seconds within which an equal message from another view is a duplicate
    window = 1.0
    # seconds after the last reload message during which a map's layer messages are not GUI actions
    reloaded = 2.0

    def receive(
            self,
            server: Server,
            text: str,
            *,
            at: float | None = None,
            seen: float | None = None,
    ) -> Message | None:
        """
        Handle one frame from a server's feed. Returns the message kept for it, or None.

        A frame is counted on its server and stamped on arrival, since a message carries no time.
        `at` and `seen` let tests set the wall-clock and monotonic times. Jupyter's own contents
        and collaboration messages go to `observe`, which follows saves, renames and reloads with
        them. A message of another schema is only counted. A JupyterGIS message is kept in
        `Messages` and passed to `route`. An error in `route` is recorded on the message, so that
        one bad message does not end the subscription.
        """
        if at is None:
            at = time.time()
        if seen is None:
            seen = time.monotonic()
        counts = server.counts
        counts.received += 1
        try:
            message = json.loads(text)
            schema_id = message["schema_id"]
        except (ValueError, KeyError, TypeError):
            counts.skipped["malformed"] += 1
            return None
        if schema_id in (CONTENTS, COLLABORATION):
            out = self.observe(server, message, at, seen)
            return out
        if not schema_id.startswith(PREFIX):
            counts.other[schema_id] += 1
            return None
        record = Message.make(server, message, at)
        self._parent.messages.append(record)
        try:
            self.route(server, record, seen)
        except Exception as error:  # noqa: BLE001
            self.skip(server, record, f"error: {error!r}")
        out = record
        return out

    def route(
            self,
            server: Server,
            record: Message,
            seen: float,
    ) -> None:
        """
        Decide what becomes of one JupyterGIS message. It is skipped, with the reason recorded on
        the message, if:

        - another view of the map sent an equal message within `window` seconds;
        - it names no map Trail can place, as `resolve` reports;
        - it is a layer message for a map the server is reloading from disk;
        - it is an update whose old and new layers are equal.

        Otherwise it joins a burst. The first, third and fourth checks exist because the emitter
        reports every change to a map, whatever made it. They become safety nets once JupyterGIS
        filters on local changes.
        """
        event_cls = JupyterEvent.registry.get(record.schema_id, JupyterEvent)
        if (
            event_cls.is_shared
            and self.duplicates.is_duplicate(record, seen)
        ):
            self.skip(server, record, "duplicate")
            return
        found = self.resolve(server, record)
        if found.reason is not None:
            self.skip(server, record, found.reason)
            return
        if (
            record.kind == "layer"
            and server.is_reloading(found, seen)
        ):
            self.skip(server, record, "reloaded")
            return
        if record.is_unchanged:
            self.skip(server, record, "unchanged")
            return
        self.add(record, event_cls, found, seen)

    def observe(
            self,
            server: Server,
            message: dict[str, Any],
            at: float,
            seen: float,
    ) -> Message | None:
        """
        Use one of Jupyter's own contents or collaboration messages, and return it if it was kept.
        These messages are how the pipeline learns that Jupyter saved, renamed or reloaded a file,
        which the JupyterGIS messages never say.
        """
```

What `observe` does with each message:

- A contents `get` is only counted, apart from noting that its path is open in Jupyter. Each open
  document's room polls its file once a second, so `get` is 98% of the feed: 13,478 of 13,708
  messages in a 7.8 h browser session.
- A contents `save` is handed to `Writes`, which matches it with the file's watchdog events (see
  "Folding Jupyter's writes"). A save of a `copy` path starts with a slash, which is stripped.
- A contents `rename`, with `path` and `source_path`, is kept on the server, so a stale path can
  still be resolved.
- A session/v1 `overwrite` marks its path as reloading, and so does the pair that a rebuilt room
  sends: `initialize` "The file is out-of-sync with the ystore." followed by `load` "Content loaded
  from disk.". An `overwrite` also closes the map's open bursts at once. Their change was lost in
  the reload, unless a save already confirmed it.
- A session/v1 message at level `ERROR`, such as "Error saving file: maps/m.jGIS HTTPError()", marks
  the open bursts of that map unsaved, with the message.
- A session/v1 `save` is counted and kept, but it never confirms a save, since the room also sends
  it when it wrote nothing.
- The messages the pipeline uses stay in `Messages` with the outcome `used`. The others are only
  counted.

### Resolving the map

Every JupyterGIS message names a map, and the pipeline needs that map's asset. `Pipeline.resolve`
finds it and returns a `Map`. You asked why 10-21-2 called this class `Target` and not `Map`.
`Target` was meant to cover messages about files other than maps, but every message `resolve` sees
names a map, so `Map` is the better name. Code names a `Map` instance `found`, since `map` would
shadow the builtin.

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Map:
    """
    The map a message is about, as `Pipeline.resolve` found it: its asset, or only its local path
    for a map Trail does not track, or the reason it could not be placed.
    """

    # the map's asset, or None for a map Trail does not track
    asset: Asset | None
    # the map's local path, or None if the message names no file
    path: Path | None
    # why the map could not be placed, such as "file missing", or None if it was placed
    reason: str | None = None

    @property
    def src_path(self) -> str:
        """
        The map's absolute path, from its asset if it has one, since the asset follows moves.
        """
        if self.asset is not None:
            out = str(self.asset.path)
            return out
        if self.path is None:
            return ""
        out = str(self.path)
        return out
```

`Pipeline.resolve` looks the map up in this order:

1. `server.locate(document_path)` gives the local path. It is None for a null path, or for a name
   that ends in `unsaved_project`. A path outside the server's root is refused.
2. If the message's subject is linked to an asset that Trail still tracks, that asset.
3. The asset at the path, or the one `trail.assets.scan(path)` tracks now if the checklist matches.
   The subject is then linked to it.
4. If nothing exists at the path, a contents rename of the path, or of a folder above it.

10-21-2 tried the path first. It assumed that after Save As the subject stays linked to the old
file. The browser runs showed that Save As closes the view and opens a new one with a new subject,
so a subject names one map for its whole life. Trying the subject first then covers the views
whose path goes stale: views renamed by another client, notebook views, and maps moved outside
Jupyter. It also keeps a stale view's messages off a new file created at its old path. Subject links
live for the run and are lost when Trail restarts. If JupyterGIS adds `document_id`, step 2 uses
the file id instead, and the links could outlive a restart.

| The message names | Recorded as |
|---|---|
| a tracked map, or a subject linked to one | an event on that asset |
| an untracked map that the checklist matches | an AddAssetEvent, then an event on the new asset |
| an untracked map that exists inside the project | an event with no entry, with `src_path` |
| a map with no file | skipped: `unsaved` |
| an offtrailed map | skipped: `offtrailed`, since `offtrail` promises to ignore later events |
| a path that does not exist | skipped: `file missing` |
| a map outside the project, or outside the server's root | skipped: `outside project`, `outside root` |

"Inside the project" means `trail.jupyter.is_inside(path.parent)`: the map's folder is a Dir's
folder, or a folder inside a Dir that tracks subfolders. Unlike `overlaps`, it does not accept a
folder above a Dir, since a map in the home folder is not part of the project. A message skipped as
`outside project` is counted, and its payload is not kept in `Messages`, since a server rooted at
the home folder carries every project's maps.

### Bursts

A `Burst` is the open run of messages about one layer, or about one document, before it becomes an
event. The pipeline keeps its open bursts by key, and its closer task records each one when its
time comes.

```python
@dataclass(slots=True, eq=False, kw_only=True)
class Burst:
    """
    The open run of messages about one layer or one document, before it becomes an event.
    """

    # the server that sent the messages
    server: Server
    # the class of the event the burst becomes
    event_cls: type[JupyterEvent]
    # the map the messages are about
    found: Map
    # the first message of the run
    first: Message
    # the last message of the run
    last: Message
    # monotonic seconds of the last message, which the pause is measured from
    seen: float
    # monotonic seconds of the confirmed save of the map, once Writes reports it
    saved: float | None = None
    # the session/v1 error that says the save of the map failed
    save_error: str = ""
    # how many messages the burst merged
    messages: int = 1
    # the folded file events of the save, by event type
    count: Counter[str] = field(default_factory=Counter)

    def takes(self, record: Message) -> bool:
        """
        Whether a message joins this burst. A drag sends one update per step, and a save ends the
        run.
        """
        out = (
            self.event_cls.is_merged
            and record.verb == "updated"
            and self.last.verb == "updated"
            and self.saved is None
        )
        return out

    def deadline(
            self,
            pause: float,
            grace: float,
    ) -> float:
        """
        The monotonic time the burst closes: `grace` after its save, or `pause` after its last
        message.
        """
        out = self.seen + pause
        if self.saved is not None:
            out = min(out, self.saved + grace)
        return out
```

`Pipeline.add` puts each message that gets this far into a burst:

- A burst's key is the server, the map, the `schema_id` and the layer id, without the subject,
  since duplicates are already gone. A document message has no layer id.
- An `updated` message joins the open burst for its key while `takes` allows it. Any other message
  opens a new burst, and the open burst for its key closes now, unless it has a save.
- `added`, `removed` and `opened` are held like `updated`, so the save that follows them can still
  be folded into them. `closed` is recorded right after the map's other bursts close. Closing a view
  writes nothing.
- A viewport `moved` is recorded at once. Its save came about 9 s earlier, so holding it would fold
  nothing.

A burst closes 0.25 s after `Writes` confirms the save of its map, or 2 s after its last message if
no save is confirmed. Its event then carries:

- `schema_id` and `data` of the last message, with the first message's `object.old`;
- `timestamp`, the last arrival, and `started`, the first, when the burst took several messages;
- `saved`, the arrival of the confirmed save, or None. The console shows `saved: no` for None;
- `save_error`, the session/v1 error, if the save failed;
- `count`, the folded file events of the save;
- `src_path` from `found.src_path`, and `entry` from `found.asset`.

`Pipeline.close(*, due=None, key=None, asset=None)` pops the selected bursts and orders them by
their first message. It appends their events with one `trail.events.extend`, which wakes the
console's stream once. It then marks each burst's messages `recorded` or `merged`, with the event's
id. With no arguments it closes everything, which `stop()` uses. One closer task waits for the
earliest deadline, of a burst or of a write in `Writes`, as `Session.run` does for accesses. All
this state lives on the loop thread, so it needs no lock. `Pipeline.settle(timeout)` waits until
every open burst that changed a layer has its save, or until `timeout` passes.

On the pause, which your notes left open:

- A burst normally closes 0.25 s after the save of its map, about 1.26 s after the last change. One
  event then matches one write of the map.
- Without a save, a burst closes 2 s after its last message, recorded as not saved. Autosave may be
  off, the write may be slow, or the save may have failed.
- Any pause under about 1.1 s would close a burst before its save.
- The cost is latency: a layer event reaches the console 1.3 to 2 s after its last message.
  `jupyter tail` prints each message as it arrives.
- Events can reach the log out of order. A burst appends its event when it closes, after file events
  of other files that came later. Each event keeps its own `timestamp`. A dirasset's access works
  the same way today.

### Folding Jupyter's writes

This is the mechanism behind "When Jupyter writes a file Trail tracks". `Writes`, defined in
src/trail/jupyter/writes.py and reached as `trail.jupyter.session.writes`, sees every file event of
the watchdog session before it is applied. It holds the events that may belong to a Jupyter write,
folds those that do into the event the write belongs to, and lets the rest through.

The base gains a generic hook for it. The watchdog session keeps a list of observers, callables
that see each file event before it is applied. One that returns True holds the event and applies
it later itself. The Jupyter session adds `writes.observe` when it starts and removes it when it
stops. Nothing in the base names Jupyter.

```python
# session.py, Session
    @cached_property
    def observers(self) -> list[Callable[[WatchdogEvent], bool]]:
        """
        Callables that see each file event before it is applied. One that returns True holds the
        event, and applies it later itself.
        """
        out: list[Callable[[WatchdogEvent], bool]] = []
        return out

# Session.consume, after the branch for directory events
            if any(
                observer(event)
                for observer in self.observers
            ):
                continue
            if event.apply(trail) is not None:
                ...
```

```python
# src/trail/jupyter/writes.py
class Writes(Node):
    """
    Jupyter's writes of tracked files, as the watchdog reports them. It holds the `opened` and
    `modified` events of each file a connected server has open, and folds those that belong to a
    Jupyter write into the event the write belongs to.
    """

    _parent: JupyterSession
    # seconds a held event waits for the save message of its write, before or after it
    grace = 0.25
    # seconds a file counts as open in Jupyter after a message about it, such as a room's poll
    recent = 2.0

    def observe(self, event: WatchdogEvent) -> bool:
        """
        See one file event before it is applied. Returns True if the event is held.

        - An event on a backup `.~<name>` opens or closes the bracket of its file, and passes on.
        - An `opened` or `modified` of a tracked file that Jupyter has open, or that sits inside an
          open bracket, is held.
        - A `moved` or `deleted` of a tracked file first records the file's open bursts.
        - Every other event passes on.
        """

    def saved(
            self,
            path: Path,
            seen: float,
    ) -> None:
        """
        Note a contents `save` message for a path. Its held events are matched against it.
        """

    def settle(self, now: float) -> None:
        """
        Decide the held events whose wait is over. An event inside a closed bracket, or within
        `grace` of a save message for its path, is folded. Any other event is applied, in the
        order it arrived.
        """
```

How a held event is decided:

- A file counts as open in Jupyter while its room polls it, and for 2 s after any contents message
  about it. That covers autosaves, explicit saves and the chunks of an upload, and it keeps every
  other file's events from waiting at all.
- The bracket of a file runs from the `opened` just before `created .~<name>` to `deleted .~<name>`,
  plus one `opened` within 0.25 s after it, which is the room reading the file back. Its events
  belong to the write. Without a bracket, the events within 0.25 s of a save message for the path
  belong to it.
- The folded events go to the write's save, which `Writes` reports to the pipeline. If the map has
  an open burst, the save confirms it, and the counts go on its event. Otherwise the counts go to
  the file's run of saves, which "Saves with no layer event" describes.
- An event that matches no write is applied once 0.25 s have passed and no bracket of its file is
  open. It is logged as today, at most 0.25 s late.
- A bracket with no save message, for example from a server Trail is not connected to, folds
  nothing. Its events are applied as usual.
- The backup's own events are never tracked, under the hidden-file rule, and never logged.
- Only `opened` and `modified` are folded, so replay is unchanged. `created`, `moved` and `deleted`
  are never held.
- A `moved` or `deleted` of a file first records its open bursts and its run of saves, so their
  events come before the move, as `Session.flush` does for a dirasset's accesses. `Trail.offtrail`
  calls `trail.jupyter.session.flush(asset)` for the same reason, and `Events.clear` calls
  `trail.jupyter.session.drop()`.

The folded `opened` events include the room's hash read, and the bracket includes the copy's read
of the file. Neither is an access by a person, so neither belongs in the log as one.

### Saves with no layer event

Many saves have no layer event: opening a map, every additional view, a rename, a reconnect, a pan,
and every edit the emitter does not report. Notebooks and text files open in JupyterLab are saved
about a second after each pause in typing. 10-21-2 recorded one `saved` event per save, so a
person typing in a tracked notebook would add one event a second.

This plan records a run of saves of one file as one `ContentsServiceV1Event`. The run starts with
a confirmed save that no burst takes. It closes when the file has had no save for 30 s, as a
dirasset's access closes, and when a burst of the same map opens, so the order of events stays
right. The event keeps the last save message, `started` and `timestamp` for the first and last
save, `saves` for their number, and `count` for the folded file events. The console renders it as
`saved`. One event per save, as in 10-21-2, is the other choice in the decisions.

A pan or zoom then gives a run of saves, and about 10 s after the movement ends, the viewport
event.

## The console as a live tracker and debugger

Trail's console prints Jupyter events as they are recorded, the way it prints file events, and it
gains a `jupyter` command to look into the feed. It starts the Jupyter session inside the watchdog
session:

```python
# cli/console.py, Run.__call__
        watching = trail.events.watch()
        jupyter = trail.jupyter
        jupyter.on_notice = self.notice
        async with trail.watchdog.session:
            # leaving this context waits up to 1.5 s for the autosave of an open burst, cancels the
            # sockets, and records the bursts while the watchdog session still runs
            async with jupyter.session:
                ...
        self.drain()
```

On the order:

- While the Jupyter session waits for an autosave, the watchdog session is still active. Its
  debounce applies, the save's file events are held and folded, and a late scan places its watches
  normally.
- Once `trail.jupyter.session.stop` returns, no message is handled, so no late scan can start a
  watchdog run after the watchdog session stops.
- Quitting takes up to 1.5 s longer, and only while a burst waits for its save.
- The final `drain` prints what both stops appended.

Other console changes:

- The header gets `  jupyter 1/2`: the servers subscribed, out of the live servers whose root
  overlaps a Dir. It shows `!` while a server refuses its token. It gets `  pending N` while bursts
  are open, as `accesses N` shows. It reads memory only.
- `Renderer.verb` returns a JupyterGIS event's own verb, and `saved` for a
  `ContentsServiceV1Event`. theme.py adds `event.added` (green), `event.updated` (yellow),
  `event.removed` (red), `event.closed` (grey) and `event.saved`. A map's `opened` shares the style
  of a file's `opened`, and a viewport's `moved` that of a file's `moved`.
- tornado logs some failures on its `tornado.general` logger, such as a certificate that fails
  verification. The console routes that logger into notices, since prompt_toolkit's `patch_stdout`
  would print it unstyled above the command bar.
- `Renderer.record` catches an error from `_repr_items` and prints an error row for that record.
  Today one record that fails to render stops `Run.stream` without a word until exit, and a
  debugger has to survive a bad message.
- `Run.monitor` also watches the discovery, closer and subscription tasks. It reports each failure
  once, with a set of reported tasks in place of the single `failed` flag.
- `Run.notice(text, is_error)` prints `jupyter: ...` rows, only when a server changes state.
- `Run.accept` replaces `token=<value>` in a submitted line with `token=<redacted>`, before the echo
  and the history see it. That catches a pasted server url.

The `jupyter` command, in src/trail/cli/jupyter.py, registers through an import in console.py. The
command lives under `trail.cli`, since `trail.py` imports `trail.jupyter` at module level, and an
import of `trail.cli` from `trail.jupyter` would fail on a partly initialized `trail.trail`. For the
same reason `trail.jupyter` imports `Trail` only under `TYPE_CHECKING`.

| Command | What it does |
|---|---|
| `jupyter`, `jupyter servers` | one row per server: #N, state, url, root, whether it overlaps, counts, skips by reason, the probe's schemas, the busiest other schemas, stale files |
| `jupyter connect N` | listen to a server that does not overlap a Dir, for this process |
| `jupyter disconnect N` | stop listening to a server, for this process |
| `jupyter messages [EXPRESSION]` | list Messages, with the same expressions as `events` |
| `jupyter show SEQ` | print one message's json on one row |
| `jupyter tail [on\|off]` | print one row per message as it arrives, with its outcome |

- `submit` takes the first word as the subcommand and hands `messages` the rest unsplit, as
  `events` does. `complete` offers the subcommands and hands `Listing.complete` the text after
  `jupyter `.
- `Listing` gains a `title` that defaults to `name`, so `MessagesListing` prints
  `jupyter messages (3 of 41)`. A class-level `name` would register it as a command, and mypy
  rejects setting a ClassVar through an instance.
- The help notes gain three items: which servers connect by themselves, that a token is never typed
  in the command bar, and that `jupyter messages` and `jupyter tail` show what came in and why it
  was skipped.

## Steps

Each step leaves the suite passing and is one small change, the size of a PR in prototype-trail. The
two refactors in the appendix come first, on their own branch.

1. The core changes land first: records with no entry, dotted fields, the class-name guard, the
   hidden-file rule and the recorder lock. The hidden-file rule also fixes a bug every preset has
   today: a slow save in JupyterLab tracks its backup. The pre-commit change goes here too, if the
   decision keeps `dict()`.
   - Tests: a reload with `"entry": null` on a disk-backed Trail (a Trail with no directory never
     calls `to_record`); `count.modified>3`; a hyphenated key; `entry.path=` read against the root;
     the hidden-file tests of "Hidden files"; a second process does not record while the lock is
     held.
2. The event model follows: event.py with the document, layer, viewport and contents classes, the
   package `__init__` and the import from trail.py. The stub is removed. `Renderer.verb`, the theme,
   and `Renderer.record`'s error row. The YAML copies, the drift tests and the dev dependency
   `pyyaml`.
   - Tests: each of the three `EVENT_SCHEMA_CASES` payloads becomes its class; records survive a
     reload; `data` has shared key strings after a reload; the feed rows match the illustration.
3. The pipeline records each message at once: `Jupyter`, `JupyterSession` and the shared session
   descriptor, `Node._jupyter`, `Trail.jupyter`, `Server`, `Servers`, `JSON`, `JSONs` and
   `Messages`, and `receive` → `resolve` → record.
   - Tests: other schemas are counted; each row of the resolve table; a subject linked to a map
     still resolves after another client renames it; a stale path does not land on a new file at
     the old path; a message with no subject links nothing; `del trail.jupyter.session` gives a
     clean run.
4. Trail finds servers by itself: `JSONs.refresh`, psutil, the overlap rule, the notices, and the
   declared `jupyter_core` and `psutil`.
   - Tests: a live file is found and a stale one counted; the overlap table; a removed file stops
     its server; a restart on the same port gives the server its new token; one url gives one
     `Server`; the token never appears in `repr(trail.jupyter)`, feed rows, Messages or notices.
5. Trail subscribes and probes, and the console gets a first view: `consumer.py` with the declared
   tornado, the probe, `async with trail.jupyter.session` in `Run`, the header, the notices and the
   monitor. Then the
   end-to-end run below, with its feed captured as a fixture.
   - Tests, on a fake tornado server: messages reach the pipeline; a wrong token is refused once and
     not retried; a drop reconnects; a drop with the file gone means `stopped`; the probe's two
     error strings, and an answer that matches neither.
6. The pipeline handles duplicates, reloads, unchanged layers and failed saves: the duplicate
   check, `observe` for `overwrite`, for a rebuilt room's reload and for session errors, and
   `is_unchanged`.
   - Tests: a copy from another subject is dropped; the same change twice from one subject is kept;
     `opened` from two views is recorded twice; an `overwrite`, and a rebuilt room's `initialize`
     and `load`, each make later layer messages `reloaded`; a session error marks the map's open
     burst unsaved.
7. Bursts merge messages and Jupyter's writes are folded, tuned on the capture from step 5:
   `Burst`, `close`, the closer task, `stop`'s wait, `Writes` with the observer hook in the
   watchdog session, the backup bracket, runs of saves and `ContentsServiceV1Event`, `Events.clear`
   and `Trail.offtrail`.
   - Tests: 10 updates become one event, with the first old, the last new, `messages: 10` and
     `started`; a saved burst is not closed early; `closed` comes after the map's bursts; the
     events inside a bracket are folded, and a script's write 0.3 s before the bracket is logged;
     a write with no bracket is folded by its save message; an `overwrite` closes a burst as not
     saved; a save while quitting folds; a Ctrl-C during `stop` records; a move during a burst logs
     the event before the move; the file-event fixtures follow the measured save sequence.
8. The `jupyter` command arrives: servers, connect, disconnect, messages, show and tail,
   `Listing.title`, the token redaction and the help notes.
   - Tests: the messages listing shows skips with their reasons; `show` prints the json; a pasted
     `?token=` is redacted from the rows and from the history.
9. The geospatial preset gains `*.jgis`, `*.qgz` and `*.qgs`. A preset only affects new projects,
   so existing ones run `check jgis`.

The end-to-end run in step 5:

- Make a scratch folder with the geospatial preset, a few of `../eventfeed/examples/*.jGIS` at their
  committed versions (`git show HEAD:examples/world.jGIS`), and `../eventfeed/examples/data/`.
- Start `jupyter lab --ServerApp.root_dir=<that folder>` from the eventfeed_dev environment, from a
  folder outside it, so that `.jupyter_ystore.db` lands elsewhere. Its root equals the root Dir, so
  it connects by itself.
- Run `trail` in that folder.
- Go through the checklist in events.md's "Using this prototype": open a map, add and remove a
  layer, change a layer's symbology, move the viewport and wait 10 s, then close the map. Add the
  cases of 2026-10-28: rename a map in the file browser, use Save As, show the map in a notebook and
  change it from Python, and write the map from a script while it is open. The headless browser
  scripts of those runs can drive the same steps.
- Running Trail in ../eventfeed itself would write `.trail/` and `.checklist` into Matt's checkout.
- A short log from this run is also the sample trailfile for Pari.

An optional integration test against a real jupyter_server with jupytergis_core can run under
`pytest.importorskip`. It skips in CI and after any `uv sync`.

## Decisions for you

1. When does a burst close?
   - (A, recommended) Close a burst 0.25 s after the confirmed save of its map, or 2 s after its
     last message without one: about 1.26 s, with `count` and one event per write.
   - (B) Close a burst after a short pause such as 0.5 s, and record single messages at once.
     Events show sooner, but the record loses `count` and one event per write.
2. What happens to the file events of Jupyter's own writes? This is your question.
   - (A, recommended) Fold the `opened` and `modified` events of Jupyter's write, found by its
     backup bracket or within 0.25 s of its save message, into the event the write belongs to.
   - (B) Log every file event, linked by id to the Jupyter event that caused it.
   - (C) Log every file event with a tag such as `writer: jupyter`.
   - Suppressing the events is not offered, since a wrong match would erase another program's
     write without a trace.
3. How are saves with no layer event recorded?
   - (A, recommended) One `ContentsServiceV1Event` per run of saves of one file, closed after 30 s
     with no save.
   - (B) One event per save, as in 10-21-2.
   - Should every tracked file that Jupyter saves get this, as the plan recommends, or only maps?
4. Which untracked maps are recorded? The plan records them only inside the project. Skip offtrailed
   maps, maps with no file, and paths that do not exist. Is that what you meant by recording events
   about maps Trail does not track?
5. Which servers does Trail connect to by itself?
   - (A, recommended) The overlap rule, with the probe only for servers rooted at or inside a Dir.
   - (B) The rule of 10-21-2: connect by itself only to a root inside a Dir.
   - (C) The overlap rule with no probe at all. Trail learns which schemas a server has from its
     first JupyterGIS messages, and cannot tell a server with no feed from a quiet one.
6. Does a Trail in a kernel start recording by itself? With the lock, whichever Trail starts
   recording first holds it. A notebook that opens a Trail first would leave the console read-only,
   while the notebook stalls on every long cell.
   - (A, recommended) A Trail starts recording only when asked: the console asks, and a notebook
     calls `start()`.
   - (B) Any Trail starts recording when an asset is tracked under a running loop, as today.
7. What becomes of an `$id` Trail does not know? Record it as a plain `JupyterEvent`
   (recommended), or skip it with a notice.
8. How are the event classes cut? A leaf class per `$id` under a base class per kind
   (recommended), or one `LayerEvent` for every version.
9. Which WebSocket client does Trail use?
   - (A, recommended) tornado. Trail already installs it through ipykernel. It ignores proxy
     variables, follows no redirects and logs no headers. Its `AsyncHTTPClient` makes the probe
     cancellable.
   - (B) websockets. It has typed errors, keepalive by default and `unix_connect`, and it is what
     the reference consumer uses. It needs `proxy=None`, a logger pinned above DEBUG, since it logs
     the Authorization header, and `>=16.1`, so that a redirect cannot carry the token to another
     origin.
   - Both need the same hand-written reconnect loop.
10. Does Trail check events against the saved map?
    - (A, recommended) Confirm saves now, and decide on diffing once Matt has answered about the
      local filter.
    - (B) Diff each save of a map against its last saved layers now. This would also settle the
      open question of reading each layer's data path from the saved map. If yes, OpenEO's
      `authBearer` and `httpHeaders` must be redacted, since the file can hold credentials.
11. How does a library user start the Jupyter session? Explicitly (recommended for now), or by
    itself under a running loop, the way the watchdog session starts. This follows the answer to 6.
12. How are empty dicts written? Configure `check-builtin-literals` with `--ignore=dict`, which
    follows CLAUDE.md, or write `{}` for empty dicts in this repo.
13. How does a user check one hidden file by name? `check .env` writes `*.env` today, since the
    checklist reads `.env` as an extension, and the hidden-file rule makes that line miss `.env`
    itself.
    - (A, recommended) `canonical` writes `./.env` when a file `.env` exists.
    - (B) Users type `./.env`.
14. Is a move into a hidden folder a deletion? A delete from JupyterLab on a separate mount moves
    the file into the project's `.Trash-<uid>`, and Trail logs a move.
    - (A, recommended) A move into a hidden folder that no Dir covers counts as a deletion.
    - (B) Keep logging a move, as today.

Not in this plan:

- servers on unix sockets;
- adding a server by hand from the console;
- the side-by-side version rule, until a v2 exists;
- remembering connections and subject links across runs;
- diffing saved maps;
- catching up after downtime from an event log file, which jupyter_server writes only when it is
  configured in Python;
- notebook-link/metrics events and kernel events.

## Risks that remain

- The reload checks are time-based and leak until JupyterGIS filters on local changes. A GUI edit
  within 2 s of a reload is dropped, and maps in an old format defeat the equality check.
- The duplicate window of 1.0 s fit every measured case, but all ran on one machine. Two users who
  make the same change within a second are merged.
- A burst merges messages in arrival order, which concurrent POSTs can swap, mostly on remote
  servers.
- A pending autosave can write the room's old content over an outside write. Trail records it as an
  ordinary save, and only diffing would show it.
- Subject links are lost when Trail restarts. A stale view's messages are then skipped as
  `file missing`, unless Trail saw the contents rename.
- The probe depends on two error strings.
- psutil's process start time follows clock steps. The 60 s margin covers a step after boot, not a
  larger one.

## Appendix: `trail.entries` and the checklist rename

This appendix carries over the two refactors that 10-21-2 planned from your notes of 2026-10-27.
Neither is about Jupyter, and both land before this plan, on a branch `checklist` off `hodczak`.
The plan above is already written against their names. The second refactor is what your `# no:`
comment at asset.py:491 refers to.

### `trail.entries`

The class named `Entries` today, in entry.py, is the base of `Assets` and `Dirs`. It becomes
`EntryCollection`. Only entry.py, asset.py and dir.py name it.

`Trail.entries` existed until 5234685 ("refactor", 2026-10-05). It was a third collection of its
own, and `Entries._synced` copied every change to `trail.assets` or `trail.dirs` into it. 5234685
deleted entry.py, and `Trail.entries` with it. aab2b1d brought `Entry` back as the base of assets
and Dirs, but not `Trail.entries`. `Trail._entry` (trail.py:605) is what is left of it.

The new `Entries` lives in entries.py and is reached as `trail.entries`. It holds no entries. It
reads `trail.assets` and `trail.dirs` on each call, so nothing has to be kept in sync, which is
what your comment asked for when it said it should not be an instance of the old class. It looks
up both kinds at once, the way `trail.assets` looks up assets:

```python
# a Dir, by id
trail.entries['04e15251538241309989553fb4672653']
# an asset, by path
trail.entries[project / 'new.csv']
# None, not KeyError
trail.entries.get('0' * 32)
# whether data/ is an asset or a Dir
(project / 'data') in trail.entries
```

- `trail.entries[key]` returns the asset or the Dir with an id or at a path. It raises KeyError
  for a key that is neither. Several keys return a tuple, as `trail.assets[...]` does.
- `trail.entries.get(key)` returns None, or a default, instead of raising.
- `key in trail.entries` holds for an id, a path or an entry.
- Iterating yields the Dirs, then the assets. A new Trail logs them in this order, and
  `Trail.__repr__` lists them in it. `len` counts both. The repr lists them as a collection's repr
  does.
- A lookup tries the assets first. Ids never collide, since both kinds take a uuid4. A path is held
  by both kinds only between tracking a directory whole and the `dirs.refresh` that follows it
  (trail.py:358-362). The asset is the one that stays.
- `trail.entries` has no `select`. A selection is a copy of one collection, and `trail.entries`
  holds none. `trail.assets.select` and `trail.dirs.select` stay. src/dev.py:64 calls
  `trail.entries.select('st_size>=60')`, which fails today. It becomes `trail.assets.select(...)`.

`Trail._entry` goes. Its two callers, `Event.from_record` and `JSONL.read`, use
`trail.entries[...]` and `trail.entries.get(...)`, as "Events with no entry" shows.

The checks and the cache that the scan, the walk and the watchdog apply to a path move into
`trail.entries` as well. They read the same state as the lookups: the assets, the Dirs, the
offtrailed paths and `.trail`.

| Today | After | Used by |
|---|---|---|
| `trail._is_ignored(path)` | `trail.entries.is_ignored(path)` | 10 calls in entry.py, trail.py, asset.py, event.py, session.py and watchdog.py |
| `trail._offtrailed_paths` | `trail.entries.offtrailed` | written by `AddAssetEvent.apply` and `RemoveAssetEvent.apply`; read by the scan, the walk and `WatchdogEvent.apply` |
| `Assets._is_markable(path)` | `trail.entries.is_checked(path)` | `Assets.scan` |
| `Assets._descends(path)` | `trail.entries.is_descending(path)` | `walk`, through `Assets.scan` |

`is_checked` and `is_descending` take the names that your `# no:` comments at asset.py:491 and
asset.py:526 ask for. The line in `Assets.scan` that passes `is_descending` to `walk` gets its
intermediate variable, as the `# no:` comment at asset.py:406 asks. This step addresses those three
comments. `is_checked` becomes a public name. Its docstring says that it is False for a path the
checklist decides but that Trail already tracks or has offtrailed. `Dirs.tracks` still answers
whether the checklist alone decides a path. The hidden-file rule from "Core changes" lives in
`Scopes.decide`, below both, so neither changes for it.

Some helpers stay where they are:

- `Assets.holder` and `Assets.is_child` read only the assets and the dirasset suffixes.
  `is_checked` calls them.
- `Assets._reach` and `Dirs.match` belong to the walk and to the checklist.
- `Trail.track`, `offtrail`, `_track`, `_absorb` and `_moved` change entries and write the log.
  `trail.entries` only looks things up and answers questions, so everything that writes the log
  stays on `Trail`.

`trail.entries` reaches the collections through `self._trail`, as `Dirs` does. Only trail.py
imports the new module. The other modules reach `trail.entries` at run time through the Trail, so
no import cycle appears.

### Renaming markers to checklist

The `.markers` file becomes `.checklist`. Each of its lines is checked. Adding a line is checking,
and removing one is unchecking. The names change as follows.

| Today | After |
|---|---|
| the file `.markers` | `.checklist` |
| `trail/markers.py` | `trail/checklist.py` |
| the presets in `trail/markers/`, `MARKERS`, `DEFAULT_MARKERS` | `trail/checklists/`, `CHECKLISTS`, `DEFAULT_CHECKLIST` |
| `Markers`, `trail.markers` | `Checklist`, `trail.checklist` |
| `Trail(dir, markers='default')` | `Trail(dir, checklist='default')` |
| `Trail.mark`, `Trail.unmark` | `Trail.check`, `Trail.uncheck` |
| the console's `mark`, `unmark` and `markers` | `check`, `uncheck` and `checklist` |
| `MarkCommand`, `UnmarkCommand`, `MarkersCommand` | `CheckCommand`, `UncheckCommand`, `ChecklistCommand` |
| `TXT.markers`, `TXT.line2marker` | `TXT.checked`, `TXT.line2checked` |
| `cli/marker_completion.py`, `MarkerCompleter`, `MarkerPaths` | `cli/checklist_completion.py`, `ChecklistCompleter`, `ChecklistPaths` |
| `tests/test_markers.py`, `TestMarkers`, `TestMarking` | `tests/test_checklist.py`, `TestChecklist`, `TestChecking` |
| `marked`, `already marked`, `not marked` and `unmarked` in the feed | `checked`, `already checked`, `not checked` and `unchecked` |
| `Not a marker: '!'` | `Not a checklist line: '!'` |
| "marker set" in the init walkthrough | "checklist" |

The completer classes take `Checklist`, not `Checked`, because they complete a line of the
checklist. Where "marker" names one line as a noun, the plan writes "line", as most docstrings
already do. "Not checked: '!'" would say that the line is unchecked, not that it is invalid.

Some names stay. Dirs, root scopes, dir scopes and the kinds that the `checklist` command prints
keep their names. So do `Dirs.tracks` and `Dirs.match`. The log does not change, since no field in
it names a marker. `AddAssetEvent.rule` and `AddDirEvent.rule` still hold the line that picked the
entry up. The notes in `notes/` keep the old words, since they record what was true when they were
written.

"Mark" also appears where it means something else. `pytest.mark` in test_accesses.py and
test_trail.py, the `marker` separator in session.py:477, "mark it for a restart" in console.py:198
and the tutorial's `marker` variable all stay. So the rename goes identifier by identifier, not by
find and replace. It covers about 570 occurrences in 25 files, plus the preset directory and the
package data in pyproject.toml.

The rename also edits the console's help docstring under the `# no:` comment at console.py:130,
which asks for bullet points for track, offtrail, mark and listings. This step addresses that
comment.

There is no migration. A project made before the rename has a `.markers` file and no
`.checklist`. If Trail opens it, Trail reads an empty checklist, logs a RemoveDirEvent for every
Dir and watches nothing. Rename the file first, with `mv .markers .checklist`.

### Order

`trail.entries` goes first, so `_is_markable` moves once and the rename does not touch it.

1. `trail.entries`. The base class becomes `EntryCollection`. The new `Entries` takes the lookups
   and the four helpers above, and `Trail._entry` goes. Nothing a user types changes.
   - Tests: a lookup by id and by path for each kind; KeyError and `get` for a key that is neither;
     `in` with an entry; Dirs before assets when iterating; a path offtrailed before a reload is
     still in `offtrailed` after it; `is_ignored` for `.trail` and for a file inside it. The
     existing scan and walk tests cover `is_checked` and `is_descending`.
2. The checklist rename, across the library, the CLI, the presets and the tests. No behaviour
   changes, so the existing suite, renamed, is its test.

### Decisions for these two refactors

1. What is the new class called? Your comment suggested `EntriesMapping`, keeping `Entries` for the
   base of `Assets` and `Dirs`. This appendix renames the base to `EntryCollection` and gives the
   new class the plain name `Entries`, since it does not iterate keys as a `Mapping` does (see 2).
   Keep your name, or take this one?
2. What does iteration yield? Your comment asks for `trail.entries` to behave like a mapping. A
   `collections.abc.Mapping` iterates its keys, but `trail.assets` and `trail.dirs` iterate their
   entries.
   - (A, recommended) `for entry in trail.entries` yields entries, as the collections do, with
     `[]`, `get` and `in` as above.
   - (B) Subclass `Mapping[str, Entry]`, keyed by id. `keys`, `values` and `items` come for free,
     but iterating `trail.entries` yields ids while iterating `trail.assets` yields assets.
3. Are the stale docstring examples updated? About 14 examples go stale with the rename: the
   `Markers (3)` reprs in trail.py, markers.py and fmt.py, `self.mark('zarr')`,
   `self.unmark('.zarr')` with its warning, `trail.unmark('raw/')` in event.py, the `.markers` path
   in the `TXT` repr, and the console transcripts in commands.py and marker_completion.py. Only
   names change in them. Your rule is to report stale examples, not to fix them. Should the rename
   step update them? I recommend yes.
4. What is one line of the checklist called? The plan writes "line" where "marker" named one line.
   The other choice is "item", as in a checklist item, and `TXT.checked` would then be `TXT.items`.

## Appendix: your comments on 10-21-2

One line per comment, by its line in 10-21-2, with what this plan did.

- 102, rename `folded` to `count`: every event uses `count`.
- 203, `trail.entries` in place of `_entry`: `Event.from_record` uses `trail.entries[...]`, and
  `JSONL.read` uses `trail.entries.get(...)`. The first appendix plans the class, and its name is
  the first decision there.
- 258, a docstring for the dotted fields: `Select.held` has one.
- 273, an example of hyphens: `object.new.parameters.color.circle-fill-color=#dc8add`, with the
  caveat that maps in JupyterGIS's current format have no hyphenated keys.
- 294, `is_same`: the condition sits inside `if not (...)`.
- 318, the `.~` check as a code smell: replaced by the general hidden-file rule in `Scopes.decide`.
  Your bracket idea works for one line but has to be on every line, so the rule does it once.
- 403-409, the `parameters` hints and flattening: settled on 10-27. `data` stays nested.
- 536, `JupyterEvent` as the only base class: `JupyterGISEvent` is merged into it.
- 768, `count` and `nest`: settled on 10-27.
- 807, nested TypedDicts: dropped, as you agreed. Typed properties remain.
- 901, the session split: kept, as you agreed.
- 1134, the `Server` docstring: each attribute has its own comment above it, in every class the
  plan adds, and the class docstrings only say what the class is for.
- 1135, `backoff` and `ceiling` sharing one comment: one comment each.
- 1138, which servers to watch: the overlap rule. Watching every server finds nothing more.
- 1151, `Server` used before it was introduced: "Servers" comes before "Finding servers", as you
  agreed, and "Messages" now comes before the pipeline that uses it.
- 1203, `jupyter.jsons`: `trail.jupyter.jsons`, a mapping from each runtime file's path to its
  `JSON`, read again only when a file's mtime changes.
- 1236 and 1257, `Info` and `JSON`: settled. `JSON` stays.
- 1362, what tornado is: explained under "Subscription".
- 1477, docstrings for `receive` and `route`: added, and `observe` has one too.
- 1520, `Target`: renamed `Map`.
