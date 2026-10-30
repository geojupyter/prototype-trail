# Meeting outline: Trail and the JupyterGIS event feed, with Matt

This is a one-hour agenda drawn from notes/10-28-2-jupyter-submodule-plan.md. Each item says what
happens today, what we propose, and what to ask Matt. Matt is away for November, so the meeting
should also settle who makes each change: Matt before he leaves, or us, with PRs, while he is away.

## Goals

- Agree on the four small feed changes Trail needs most, and who makes each one.
- Get the answers that only Matt has.
- Decide who reports the bugs we found in other packages.

## Agenda

| Time | Topic | Main ask |
|---|---|---|
| 0:00 | Where Trail stands | none |
| 0:05 | Every view reports every change | a `local` field |
| 0:20 | Naming a map | a `document_id` field |
| 0:30 | Order and lost messages | `seq` and `time` fields |
| 0:37 | `closed`, and who acted | `closed` from the last view |
| 0:44 | Large layer messages | a `layer/v2` |
| 0:50 | Saves and upstream bugs | who files what |
| 0:55 | Smaller points and wrap-up | owners for November |

## 1. Where Trail stands (5 min)

- Trail will listen to each Jupyter server's event feed, and record JupyterGIS actions next to the
  file events it already records.
- On 2026-10-28 we ran Matt's prototype, at abecc972, in a real JupyterLab, with scripted clients
  and a file watcher.
- Trail can work around most of what we found. Four small changes to the emitter would remove most
  of the guesswork: `local`, `document_id`, `seq` with `time`, and `closed` from the last view.

## 2. Every view reports every change (15 min)

Every open view of a map sends a message for every change, whatever made the change. So Trail
receives copies from other pages, changes made from Python, and reloads from disk, and they all
look like a person's edits in the browser.

- Python changes arrive 0, 1 or several times, depending on which views are open. events.md says
  they are not sent at all.
- Yjs already knows whether a change was made in that view, a "local" change.
  `_layersObserver` in packages/schema/src/doc.ts drops that flag.

We propose:

- Each layer message carries `"local": true` or `false`.
- Trail keeps the local message, and decides what to do with the copies.
- It only adds a field, so it stays in layer/v1.

We should ask Matt:

- Is a `local` field fine, and who adds it?
- Should Python changes be in the feed at all?

## 3. Naming a map (10 min)

A message names its map only by `document_path`, and that path can be wrong.

- When another page or a colleague renames the map, the open view keeps sending the old path.
- A map shown in a notebook never updates its path.
- Save As opens a new view, with a new subject and a new file id. Nothing links the copy to the
  original.
- `subject` names one view's copy of the map, not the map itself.

We propose:

- Add `document_id`, the name of the map's room on the server. The browser already stores it in
  the shared document.
- Send the current path from the shared document, not the view's own copy of it.
- Send one message that links a Save As copy to its source.

We should ask Matt:

- Can these land, and who makes them?
- In the Python API, should `GISDocument("x.jGIS")` look in the notebook's folder, not the active
  tab's, fail when the file is missing, and report `null` for a map with no file?

## 4. Order and lost messages (7 min)

Each message is its own POST. Messages carry no time and no order, and a message that fails is
lost. If the last two messages of a drag arrive swapped, Trail records the opacity as 0.37 while the
file holds 0.35.

We propose:

- Add `seq`, which counts per view, and `time`, the browser's clock, to each message.
- Send each view's messages in order, and retry one that fails.

We should ask Matt:

- Can both fields go into v1?
- Is there news on jupyter_server#1710, his issue about passing event times to listeners?
- Could jupyter_server's contents `save` message also carry the file's `last_modified` and `size`?
  That would let Trail match each save to its file events exactly.

## 5. `closed`, and who acted (7 min)

`closed` comes when the first view of a map closes, even if other views stay open. Reloading or
closing the page sends nothing. No message names the user.

We propose:

- The emitter counts the open views of each map, and sends `closed` when the last one closes and
  when the page closes. This is a small change to plugin.ts.
- Messages name the user, as the room's awareness list already does.

We should ask Matt:

- Is this fine, and who changes plugin.ts?
- Is naming the user in scope?

## 6. Large layer messages (6 min)

Every `updated` carries the whole layer, before and after the change.

- An image pasted into a story slide is stored inside the layer as base64 text. Each edit to the
  slide sends it twice, so a 4 MB image makes a message of about 10.7 MB.
- That is over the 10 MiB message limit of tornado, the library Trail listens with. Trail raises
  its limit, but large messages still slow every listener down.
- The opacity slider sends up to 100 messages per drag.

We propose a `layer/v2` that sends only the paths that changed, with metadata in place of embedded
images: the image's name, type, size and a hash.

We should ask Matt whether a v2 like this fits his plans.

## 7. Saves and upstream bugs (5 min)

A layer message is not a save. The file is written about a second later, and in our runs these
stopped the write:

- the map was moved outside Jupyter, or a folder above it was renamed;
- the map was open in a tab and in a notebook, and the view that opened first was closed;
- the server stopped less than a second after an edit.

Opening a map also saves it, with no action by the user, and maps in the old 0.5 format get new
random style ids each time they load. Trail works around the save on opening.

We propose:

- Trail counts a change as saved only when Jupyter's save message arrives.
- A small JupyterGIS PR makes the migration's ids stable.
- Trail's notes tell users about `LocalFileIdManager`, which keeps a map's room working after a
  move or a folder rename.

We should ask Matt:

- Should events.md say that a layer message is not a save?
- Will he take the migration-id PR?
- Who reports each of these bugs?
  - jupyter_server_fileid: renaming a folder loses the ids of the files inside it.
  - JupyterLab: renaming a folder with `(` in its name leaves open views on the old path.
  - jupyter-collaboration: a tab and a notebook view of one map share one connection.
  - jupyter-collaboration: a map moved outside Jupyter breaks every later save.
  - jupyter-collaboration: shutting down drops the last second's edit.
  - jupyter-collaboration: a write that keeps the file's modification time is missed, and the next
    autosave overwrites it.
  - jupyter-collaboration: an autosave can overwrite a reload from disk.
  - jupyter-collaboration: "Content saved." is reported when nothing was written.
  - jupyter-collaboration: its store, `.jupyter_ystore.db`, lands in the project folder.

## 8. Smaller points and wrap-up (5 min)

These need a short answer each:

- Some edits send no message: layer order, sources, projection, annotations and stories. Trail
  will compare saved versions of a map itself. Are source messages, which would add who made a
  change, on his list?
- Trail no longer probes a server with a message meant to fail. It learns a server's schemas from
  its messages. A route that lists the schemas would be nice, not needed.
- A payload must not hold its own `schema_id`, since Jupyter Server adds one when it passes the
  message on. Can events.md say so?
- QGIS projects, `.qgz` and `.qgs`, send the same messages, and Trail will support them. Does the
  round trip through QGIS lose anything, and does opening a `.qgz` rewrite it?
- Two lines are stale: events.md's paragraph on high-frequency events, and "deleted" in
  layer_v1.yaml where it means `removed`.

To close, agree on an owner and a rough date for each change, and on what we do in November.

## Leave the meeting with

- An owner for each of the four feed changes: `local`, `document_id`, `seq` with `time`, and
  `closed` from the last view.
- A yes or no on Python changes in the feed.
- The scope of a `layer/v2`.
- A name next to each upstream bug.

## What we can do ourselves in November

- Open PRs to JupyterGIS for any of the four feed changes, the migration ids, and the events.md
  fixes.
- File the upstream bugs in item 7, with the repro steps from our runs.
