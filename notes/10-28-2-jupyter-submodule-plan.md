# Plan: `trail.jupyter`, third draft

This plan adds `trail.jupyter`, the part of Trail that listens to the Jupyter servers on this
machine. When someone works in JupyterGIS's map editor, Trail records what they did, such as
"made the streets layer 35% opaque", next to the file events it already records. The console shows
these records as they arrive, and it also works as a debugger for the connection.

It keeps the design of notes/10-28-1-jupyter-submodule-plan.md, which replaced 10-21-2, with your
comments on 10-28-1 applied. The text is simpler, and each term gets a short explanation or
example before the plan uses it. The plan has four parts:

- "What to settle with Matt" goes through the parts of Jupyter and JupyterGIS that matter to Trail,
  and the questions and upstream changes to discuss with him. Each topic starts with the
  background it needs, so this part can be read first.
- "How the plan works" follows one change, from a drag of a slider in JupyterGIS to a record in
  Trail's log.
- The sections from "Changes to Trail's core" to "The console" give the design, piece by piece.
  Your question, whether Trail should suppress a file event that a Jupyter message explains, is
  answered in "When Jupyter writes a file Trail tracks".
- "Steps" and "Decisions for you" close the plan. "What to settle with Matt (now that you're all
  caught up)" then covers the same topics as the first part again, briefly, for the meeting.

The behavior described was checked on 2026-10-28. The checks drove a real JupyterLab with
JupyterGIS in a headless browser, ran Jupyter servers with scripted clients and a file watcher, and
prototyped a dotfile rule on a copy of Trail. A second pass re-checked each finding against
the code. Where something was only read from code, or is a guess, the text says so.

The plan assumes that the three refactors in the first appendix land first. `trail.entries`
replaces `Trail._entry`. The `.markers` file becomes `.trailtrack`, read by `ToTrack` at
`trail.to_track`, and the appendix's decision 5 picks the commands that replace `mark` and
`unmark`. And the watchdog's `Session` class becomes `WatchdogSession`, as your comment on 10-28-1
suggested.

## What to settle with Matt

Matt Fisher wrote the event feed, the way JupyterGIS tells other programs what happens in its map
editor. Trail will be the first program to use it. The topics below are what to discuss with
him, most important first.

### 1. What the event feed is

A JupyterGIS map is a json file whose name ends in `.jGIS`. It lists the map's layers, the sources
the layers draw from, the order of the layers, and the view. Here is a trimmed example:

```json
{
  "layers": {
    "5b1e…": {"name": "streets", "type": "VectorLayer", "visible": true,
              "parameters": {"source": "S1", "opacity": 1}}
  },
  "sources": {
    "S1": {"name": "streets", "type": "GeoJSONSource",
           "parameters": {"path": "data/streets.geojson"}}
  },
  "layerTree": ["5b1e…"],
  "options": {"latitude": 37.8, "longitude": -122.3, "zoom": 12, "projection": "EPSG:3857"},
  "schemaVersion": "0.6.0"
}
```

A layer names its data only through a source id, `S1` here. The source holds the file's path or
url.

The event feed carries what happens to such a map:

1. You open `maps/city.jGIS` in JupyterLab, Jupyter's editor. JupyterLab runs in a browser page
   and shows each open file or notebook in a tab inside that page. JupyterGIS is the extension that
   lets JupyterLab edit maps. You drag the streets layer's opacity slider.
2. A small piece of JupyterGIS code in the browser, the emitter, notices the change. It sends a json
   message to Jupyter Server, the program that `jupyter lab` starts, with an HTTP POST to
   `/api/events`.
3. Jupyter Server checks the message against a schema, a description of the allowed fields, that
   JupyterGIS registered when the server started. It then passes the message to every program
   connected to the WebSocket `/api/events/subscribe`. Trail connects there with the server's
   token, the secret in the url that `jupyter lab` prints.
4. Trail receives this:

```json
{
  "schema_id": "https://schema.jupytergis.org/events/layer/v1",
  "subject": "3271840155",
  "verb": "updated",
  "object": {
    "document_path": "maps/city.jGIS",
    "layer_id": "5b1e…",
    "old": {"name": "streets", "parameters": {"source": "S1", "opacity": 1}},
    "new": {"name": "streets", "parameters": {"source": "S1", "opacity": 0.35}}
  }
}
```

`schema_id` names the kind of message and its version. It is the url the schema gives itself in its
`$id` field. `subject` says who acted, `verb` what they did, and `object` what they did it to.
These three make up the message's payload. JupyterGIS sends three kinds today:

- a document `opened` or `closed`;
- a layer `added`, `updated` or `removed`;
- the viewport `moved`, sent once the map has been still for 10 s.

Jupyter Server's own messages travel on the same WebSocket, such as one each time a file is read or
saved.

The feed exists only in Matt's prototype, prototype-jupytergis-eventfeed, a separate copy of
JupyterGIS's repository. It sits on that copy's `prototyping` branch, open as draft PR #4 against
the copy's `main`, and no JupyterGIS release has it yet. Matt explains the feed on that branch in
docs/contributor_guide/explanation/events.md, which this plan calls events.md. Matt also filed
jupyter_server#1710 on 2026-10-17, so that listeners such as Trail could see when an event
happened. The jupyter_server maintainers have not replied.

Matt is a JupyterGIS core team member and can make the feed changes in JupyterGIS. He is away
only for November. We can also implement the changes ourselves and open upstream PRs, including
while he is away. The first priorities are a `local` field in each layer message (topic 2), a
document id in each message (topic 5), and a sequence number and a time (topic 6).

> Yes of course it will go into JupyterGIS, he's a core member of the team. He's only gone for November. If he's gone, and we need changes on the feed, we can create the changes ourselves and make a PR.

> **Claude:** Understood. The text above now says that Matt or we can make each feed change, including while he is away. One catch: PR #4 is open against the prototype repository's `main`, not JupyterGIS's, so the feed still needs its own PR to JupyterGIS.

### 2. Every view reports every change

A map can be open in several places at once: in two browser pages, in a colleague's browser, or in a
notebook next to the map's tab in the same page. Each of these is a view. Two tabs of the same map
in one page count as one view, since they share one copy. JupyterLab does not let views edit the
file directly. Each view holds its own copy of the map in memory, a shared document built with the
Yjs library. jupyter-collaboration, an extension of Jupyter Server, keeps all the copies of one file
in step through a room: an object inside Jupyter Server that every view of the file connects to. A
file has one room for each kind of editor it is open in, so a map usually has one. When one view
changes its copy, the room passes the change on to the other views.

Yjs marks each change in a view's copy as local or remote. A local change was made in that view,
for example by your mouse. A remote change arrived from the room. Take one opacity change, with the
map open in two browser pages:

| | Page 1, where you drag | Page 2 |
|---|---|---|
| the change in its copy | local | remote, passed on by the room |
| a message from its emitter today | yes | yes |

A remote change can also come from the room reloading the file, or from Python. A notebook shows a
map, and changes it from Python, like this:

```python
from jupytergis import GISDocument

doc = GISDocument("maps/city.jGIS")
# displaying the document shows the map below the cell, as one more view
doc
# changes the map from Python. Every view receives this as a remote change
doc.add_raster_layer(...)
```

The `subject` of a message is the Yjs id of the view's copy, a random number such as 3271840155. It
names a view, not a person.

The emitter sends a message for every change any view sees, local or remote. Its viewport messages
already keep only local changes, but its layer messages do not. In the runs:

- One opacity change, with the map open in two browser pages, sent two `updated` messages 10 ms
  apart, one from each page, as the table shows.
- A change from Python was sent 0 times while the notebook did not display the map, once when it
  did, and once more for each other open view. events.md says Python changes are not sent at all.
- When a script rewrote the open map, the room loaded the file again and announced it on the feed
  with an `overwrite` message. Every view then sent `updated` for every layer, changed or not.
- The same can happen after a server restart. A room saves every change it receives to its store,
  a database file on disk, and a new room starts from that store. If the file changed while the
  server was down, for example by a git checkout, the room loads the file instead. Every view that
  stayed open then sends `updated` for every layer, and this time the room sends no `overwrite`
  message. A scripted client stood in for the browser here.
- Maps saved in an older format get new random ids in their styles each time a room loads them, so
  even unchanged layers look changed after a reload. This comes from the code and two scripted
  loads.

Trail would record all of these as things the person at the browser did.

Your comment on 10-28-1 asked what if the message carried the local flag, and Trail did the
filtering. That works, and it is better for Trail than having the emitter drop remote changes.
JupyterGIS's code reads each change in one place, `_layersObserver` in packages/schema/src/doc.ts,
where Yjs offers the flag and the code drops it. If each layer message carried `"local": true` or
`false`, Trail could:

- keep the local message, and drop the remote copies from other views;
- tell a person's change in a view from a change that reached every view from elsewhere, such as
  Python or a reload, and record that kind of change separately or skip it;
- stop guessing from timing which messages are copies or reloads, as this plan must without the
  flag.

Adding a field is allowed in the same schema version under events.md's rules. The cost is that the
feed keeps one message per open view, as today.

Ask Matt: can layer messages carry `local`? And should Python changes be in the feed at all?

### 3. A layer message does not mean the file changed

The room writes its shared document to the file about a second after the last change. This is
autosave. The room also checks the file every second. If another program changed it, the room
loads the file again and sends the new content to every view. The room reports what it does on
the feed with session messages, such as:

```json
{"schema_id": "https://schema.jupyter.org/jupyter_collaboration/session/v1",
 "level": "INFO", "room": "text:jgis:4029a877-…", "path": "maps/city.jGIS",
 "action": "save", "msg": "Content saved."}
```

Jupyter Server itself reports each file it reads or writes with a contents message, such as
`{"action": "save", "path": "maps/city.jGIS"}`.

The emitter sends its message when the shared document changes. The write to the file comes later,
and in the runs these things stopped it:

- The map was moved outside Jupyter, with `mv` or `git mv`, or a folder above it was renamed in
  JupyterLab. The room kept saving to the old path. Every save failed with an `ERROR` session
  message, and no later edit reached the file.
- The map was open in a tab and in a notebook, and the view that opened first was closed. The other
  view's edits were still sent to the feed, but never saved, and nothing reported an error.
- The server stopped less than a second after an edit. The edit was never saved.
- Another program wrote the map within about a second of an edit. The room loaded that program's
  version, and the edit was lost.

So Trail's log could hold changes the file never had.

Ask Matt: should events.md say that a layer message is not a save? Does JupyterGIS want to tell the
user when a save fails? The failures themselves are bugs in other packages, which topic 11 lists.

### 4. Opening a map rewrites it

`options` in a map file hold its center, zoom and projection. `schemaVersion` holds the version of
JupyterGIS's file format. When JupyterGIS opens a map in an older format, it migrates the map to
the current one.

About a second after a map opens in a browser, the room saves it, with no action by the user. Two
writes to the shared document come right after a view opens, and either one would start the
autosave. The runs did not tell which one does:

- JupyterGIS writes the options once the map is first drawn, since the map library then announces
  that the map moved.
- jupyter-collaboration's code in the browser writes the room's name into the shared document, as
  `document_id`.

In the browser run, opening the committed examples/world.jGIS gave a save 1.4 s later, and the
file grew from 2,539 to 4,338 bytes. Its format went from 0.5.0 to 0.6.0, with new random ids in
the migrated styles. Its zoom went from 1.84 to 2.06, the zoom at which the world fills a map
1,070 px wide, so the zoom was probably fitted to the window. A second view of a map, a rename and
a reconnect after a restart are each followed by a save too.

The random ids are a JupyterGIS bug, and a small PR to JupyterGIS can fix them. Its migration from
format 0.5 to 0.6 draws new ids each time it runs, and it runs each time an old map is loaded,
until the map is saved in the new format. Since opening a map saves it within a second, each old
map gets new ids about once. Ids derived from each layer's own id would stay the same.

> The 0.5 to 0.6 and random IDs seem like real issues on the Jupyter side that should be worked out with a PR to them?

> **Claude:** Yes. It is a JupyterGIS bug, and a small PR fixes it by deriving each id from the layer's own id. The 0.5 to 0.6 migration draws new random ids each time it loads an old map, until the map is saved in the new format. Since opening a map saves it within a second, each old map is hit about once. The paragraph above, the ask below and topic 11 now say so.

Your ../eventfeed checkout shows the same. examples/world.jGIS and examples/shapefile.jGIS were
rewritten when they were opened on 10-21 and 10-22.

So each time a map is opened, Trail and git see a new version of it, and no message says why. The
run you pasted under topic 5 also shows a viewport `moved` right after `opened`, with the zoom of
2.53 that fitting the window gives. So opening seems to send a viewport message too, 10 s later,
with no movement by the user.

Asking JupyterGIS and jupyter-collaboration to stop writing when a map opens would change how they
handle options and state for Trail's sake alone, so this plan works around the save instead. Trail
folds the save that follows a map's `opened` into the `opened` record, and the comparison of saved
versions in "File-specific changes" would show that such a save changed only the view and the
format.

Ask Matt: would he take a PR that makes the migration's ids stable?

> Yes, this is a problem, but asking Jupyter to not edit the file when the map is opened is probably too big of an ask. We should probably lean towards working around it rather than expecting them to change how options are handled for us?

> **Claude:** Agreed. The plan no longer asks JupyterGIS or jupyter-collaboration to change how they write options or state. Trail folds the save that follows a map's `opened` into the `opened` record. Comparing saved versions, as "File-specific changes" sketches, would show that such a save changed only the view and the format. The text above now says so.

### 5. Messages cannot name a map reliably

`document_path` is the map's path relative to the folder the server serves, its root. A server
started in `~/project` names `~/project/maps/city.jGIS` as `maps/city.jGIS`.

jupyter_server_fileid, another extension of Jupyter Server, gives each file an id that stays the
same when the file is renamed through Jupyter. A map's room is named after that id, as in
`text:jgis:4029a877-…`, and the browser already stores the room's name in the shared document, as
`document_id`.

The path in a message can be wrong or ambiguous:

- When the map is renamed from somewhere else, such as a second page or a colleague's browser, the
  open view keeps sending the old path, which no longer exists. A notebook view never updates its
  path at all.
- Save As opens the copy in a new view, with a new subject and a new file id. Nothing links the
  copy to the original.
- A map opened from an extra drive, such as an S3 bucket through jupyter-drives, loses the drive's
  name, so it looks like a local file at the same path. This was read from code, not tried.
- In a notebook, `GISDocument("x.jGIS")` looks for `x.jGIS` next to the tab that is active in the
  browser, not next to the notebook. If no file is there, the browser silently creates an empty
  map. A map made with `GISDocument()` and no path reports `<folder>/unsaved_project`, not the
  `null` that events.md describes.

`object.document_path`, which the run you pasted below shows, is the field this plan uses to find a
map. It is enough as long as the path is right, and the cases above are where it is not. The
subject is not related to `document_id`. The subject names one view's copy and changes with every
view and every reload, which is why your `opened` and `moved` share the subject 1905429982: they
came from the same view. `document_id` names the room, which belongs to the file, so it stays the
same across views, reloads and renames made through Jupyter.

Ask Matt: can every message carry `document_id`, and the current path from the shared document?
Could Save As send a message that links the copy to its source?

>[arstneio@desktop ~/PyCharmProjects/eventfeed]$ JUPYTER_BASE_URL=localhost:8889 \
JUPYTER_TOKEN='4931d7f62734d3d9dac889f9a9998db049e892ee217ce357' \
uv run examples/event_feed_consumer/consumer.py
Subscribed to <ws://localhost:8889/api/events/subscribe?token=4931d7f62734d3d9dac889f9a9998db049e892ee217ce357>
{
  "schema_id": "<https://schema.jupytergis.org/events/document/v1>",
  "subject": "1905429982",
  "verb": "opened",
  "object": {
    "document_path": "examples/world.jGIS"
  }
}
> {
  "schema_id": "<https://schema.jupytergis.org/events/viewport/v1>",
  "subject": "1905429982",
  "verb": "moved",
  "object": {
    "document_path": "examples/world.jGIS",
    "new": {
      "coordinates": {
        "x": -66704.81155383075,
        "y": 0
      },
      "zoom": 2.5343028824546305,
      "extent": [
        -10200306.468392625,
        -20037508.342789244,
        10066896.845284965,
        20037508.342789244
      ]
    }
  }
}

> Here the document path is stored under object, is that of help to you?

> **Claude:** Yes. `object.document_path` is what the plan uses to find a map. It fails only in the cases this topic lists: a rename from another page, notebook views, Save As and extra drives. The paragraph above your run now says so.

> Is subject_id related to the document_id you are looking for?

> **Claude:** No, they are unrelated. `subject` names one view's in-memory copy of the map, and it changes with every view and every reload. Your `opened` and `moved` share 1905429982 because they came from the same view. `document_id` names the file's room on the server, such as `text:jgis:<file id>`, so it is the same for every view and survives renames made in Jupyter. The paragraph above your run now explains this.

### 6. Messages carry no time, no order and no sequence number

The emitter sends each message as its own POST and does not wait for the answer. Jupyter Server
passes on only the message itself, so Trail stamps each message with the time it arrives.

A message that fails, for example while the server restarts, is lost, and nothing tries again.
Messages sent at the same moment can also arrive out of order. A test sent 200 messages at once
over six connections, as a browser does. In each try, 8 to 15 pairs of messages sent one after the
other arrived in the opposite order. Messages sent 2 ms or 16 ms apart arrived in order, and no
browser run showed a swap. All of this ran on one machine, so a swap within a real drag is likely
only over a slow network. Trail cannot tell that a message is missing, and a swap changes what it
records. For example:

- A drag ends with `updated 0.4 → 0.37` and then `updated 0.37 → 0.35`, and the two arrive the
  other way round. Trail keeps the last message's `new`, so it records the drag as ending at 0.37,
  while the file holds 0.35.
- A layer is added and renamed within a moment, and the rename arrives first. Trail records a
  rename of a layer it has not seen, and then the layer's addition.

Ask Matt: can each message carry a `seq`, which counts per view, and a `time`, the browser's clock
when the change was made? Extra fields already pass Jupyter Server's checks unchanged, which a test
confirmed. Could the emitter send each view's messages in order, and try again when one fails?

`time` collides with nothing. `timestamp` would not collide inside the payload either, but
jupyter_server's POST already has a field of that name, which listeners never see, and every Trail
record has its own `timestamp`, the arrival time. A third `timestamp` would invite mix-ups, so the
plan asks for `time`.

> How about just `time` or `timestamp` instead of client_time or does that collide with something? Can you give an example of a consequence that might actually arise from swapped messages?

> **Claude:** `time` collides with nothing, so the plan now asks for `time`. `timestamp` would work inside the payload, but jupyter_server's POST already has a field of that name, and every Trail record has its own `timestamp`. A third one would confuse. The text above now gives two consequences of a swap. If a drag's last two messages arrive swapped, Trail records the opacity as 0.37 while the file holds 0.35. If an add and a quick rename swap, Trail logs a rename of a layer it has not seen, then the add.

### 7. Edits that send no message

The emitter watches only the layers and the viewport. Autosave writes the whole file, so these
edits reach the file with no message:

- reordering layers, or making groups, in the layer list;
- adding, editing or removing sources, including shapes drawn with the draw tool;
- changing the projection, and zooming with the zoom buttons, which keeps the center;
- annotations, the notes a user pins to a point on the map;
- stories, slide shows built on the map: the order of the slides and the story's settings. Each
  slide is itself a layer, so editing a slide does send a layer message.

For each of these, Trail sees a save with no cause. Trail also cannot link a layer to its data
file, which the Trail initiative asks for, since a layer message names only the source id.

Trail still sees these saves, as your comment on 10-28-2 says. A tracked map's writes reach Trail's
file watcher, and this plan records them as runs of saves, without what changed. Since a map is
json, Trail can compare each saved version with the previous one and tell what changed: a source
added, the layers reordered, the projection changed. One record class per kind of file, holding a
list of changes, scales better than one class per kind of change. "File-specific changes"
sketches this, with a hook that other kinds of files can use later. A message from the emitter
would still add who made a change, which a comparison cannot tell.

Ask Matt: are source messages, which would say who added or changed a source, on his list? A
general message that names which part of the map changed matters less once Trail compares saved
versions.

> The emitter only watches layers and viewport, not the file. But the file would be in a tracked directory, and we would have it in our tracked assets if it is appropriate to track updates, right?  So we still get the file modified events through watchdog, just not information on what was changed? Since .jGIS files are json, could we write our own events to parse the diff, and determine what kind of event happened? So maybe something like JGISEvent base class, and then JGISReorder, JGISAddSource, JGISRemoveSource, JGISChangeProjection, etc. or maybe there's so many things that it's not scalable to have a bunch of custom events: just one JGISEvent to rule them all?
> That being said, we should start planning for file-specific events? This one is specifically for JGIS, but we write our code in anticipation of other files with custom behavior, with the appropriate hooks.

> **Claude:** Yes to both. A tracked map's saves reach Trail's file watcher, and since a map is json, Trail can compare each saved version with the previous one to say what changed. One record class per kind of file, `JGISChangeEvent`, holding a list of changes, scales better than about 15 classes, one per type of change. A small hook, which says which files a kind covers, how to read a version and how to compare two, lets csv files and notebooks follow later. The new section "File-specific changes" sketches this as the plan to follow this one, and the text above points to it.

### 8. `opened`, `closed`, and who did it


Yjs also keeps an awareness list for each room: who is in it, and where. JupyterLab puts the user's
name there.

`opened` comes once per map per browser page. `closed` comes when the first view of the map
closes, even if another view stays open. Reloading the page sent no `closed`, and by the code,
closing the page sends none either. In one session, 6 of 12 views never sent `closed`. And no
message says which person acted, since the subject changes with every view and every reload.

This belongs in the emitter, as your comment on 10-28-2 says: `closed` should come when the last
view of a map closes, not the first. The emitter can count the open views of each map's copy and
send `closed` when the count reaches zero, and send it when the page closes as well. It is a small
change to plugin.ts, which Matt or we can make.

Ask Matt: does sending `closed` from the last view suit him? Should messages name the user, as the
awareness list does?

> It seems that should be resolved in the event feed? Instead of first registered widget triggering "closed," let's have the last remaining widget trigger "closed."

> **Claude:** Agreed, it belongs in the emitter. It can count each map's open views and send `closed` when the count reaches zero, and also when the page closes. That is a small change to plugin.ts, which Matt or we can make. The text above now says so.

### 9. Large messages

Every `updated` carries the whole layer before and after the change: 1 to 3.5 KB for most layers in
the example maps, and about 11 KB for the longest story slide in story-blog.jGIS, which holds only
text. An image pasted into a slide is kept inside the slide's layer as an attachment, in base64
text, which is a third larger than the image. So every edit to that slide sends the image twice,
and an image of about 4 MB makes a message of about 10.7 MB. tornado, the library Trail uses to
listen, closes a connection on a message over 10 MiB, about 10.5 MB, unless the listener raises
that limit. Trail raises it. That limit was read from code, not tried. The opacity slider sends a
message each time its value changes during a drag, up to 100 per drag.

Both of your suggestions would fix this in the emitter. A message could carry only the paths that
changed, and an embedded image could be replaced by its metadata, such as its name, type, size and
a hash. Both change what `old` and `new` hold, so they belong in a new version of the layer schema,
`layer/v2`. Raising the limit keeps Trail connected, but it does not make a transfer faster.

Ask Matt: would a `layer/v2` with changed paths and image metadata suit him?

> Tornado's websocket has a 10MB limit. The issue is that a story slide can have a pasted image which exceeds this limit or slows transfers. Can we only include the parts that changed? Can we just use metadata in place of embedded images?

> **Claude:** Yes to both, as a `layer/v2`, since both change what `old` and `new` hold. A message would carry only the paths that changed, and an image's name, type, size and hash in place of its bytes. Trail raises tornado's limit in the meantime, which keeps Trail connected but does not make large messages faster. The text above now says so.

### 10. Smaller points

These change little in Trail's design, and each is quick to answer.

- No part of Jupyter lists the schemas a server knows. 10-28-1 had Trail check whether a server's
  JupyterGIS sends events by posting a message meant to fail, the probe, which printed three
  warning lines per schema in the user's Jupyter terminal. Trail needed the answer only for the
  console, to say that a server's JupyterGIS has no feed, as in every release so far. As your
  comment on 10-28-2 suggests, this plan drops the probe and learns a server's schemas from the
  messages it sends. The console then cannot tell a server with no feed from a quiet one. A route
  in JupyterGIS's server code that lists its schemas would close that gap. It is a small PR, and
  not urgent.
> Okay, so currently we have a "probe" intended to fail; which is a bad code smell. Instead, the jupyter server should support a request that returns its schemas? Why do we need to know? The messages return a schema_id, so why not just wait until an event happens?

> **Claude:** Agreed, the probe is dropped. Trail learns which schemas a server sends from its messages. The only loss is that the console cannot tell a server with no feed from a quiet one, and a route in JupyterGIS that lists its schemas would close that gap later. The bullet above and the section "Which schemas a server sends" now say so.

- The contents `save` message names only the file. If it also carried the file's `last_modified`
  and `size`, which jupyter_server's contents model already holds when the message is sent, Trail
  could match Jupyter's writes to its file events exactly. It is a small change to jupyter_server,
  not JupyterGIS, and it could go with #1710 or be our own PR.
> Yes, it would probably help with our detective work to have m_time and m_size

> **Claude:** Agreed. jupyter_server's contents model already names them `last_modified` and `size`, and the server has them in hand when it sends the save message, so the change is cheap. It is a jupyter_server change, not a JupyterGIS one, and it could go with #1710 or be our own PR. The bullet above, "When Jupyter writes a file Trail tracks" and the closing list now use these names.

- Trail supports the three usual ways of starting JupyterLab: in the project folder, from the home
  folder, and from a launcher. Trail connects by itself to any server whose folder contains the
  project or sits inside it, which covers the first two. A launcher works the same way if its
  server writes its runtime file, the small file every Jupyter server writes to announce itself,
  to the default folder. This was not tested with JupyterLab Desktop or PyCharm's servers. A
  server in a container needs `jupyter connect`.
> Presumably we must support all three

> **Claude:** Yes, with one exception. Trail connects by itself to any server whose folder contains the project or sits inside it, which covers JupyterLab started in the project or from the home folder. A launcher works the same way if its server writes its runtime file to the default folder, which was not tested with JupyterLab Desktop or PyCharm's servers. A server in a container needs `jupyter connect`. The bullet above now states this, and the question is gone from the closing list.

- The emitter posts `schema_id` next to the payload. Jupyter Server adds `schema_id` to the payload
  when it passes the message on, with `dict(schema_id=schema_id, **data)`, which gives the message
  shown in topic 1. So a payload must never hold its own `schema_id` field, since that call then
  raises a `TypeError`. Jupyter Server then passes the message on to no listener, after telling
  the browser it succeeded. events.md could say so.
> Why? I see this in the event feed: {
  "schema_id": "<https://schema.jupytergis.org/events/document/v1>",
  "subject": "1905429982",
  "verb": "opened",
  "object": {
    "document_path": "examples/world.jGIS"
  }
}

> **Claude:** The `schema_id` you see was added by the server. The emitter posts `{"schema_id": ..., "version": "1", "data": {...}}`, and jupyter_server passes listeners `dict(schema_id=schema_id, **data)` (jupyter_server/services/events/handlers.py). If `data` held its own `schema_id` key, that call would raise `TypeError: dict() got multiple values for keyword argument 'schema_id'`. No listener would get the message, though the browser already got a success reply. Your pasted message is fine, since its `schema_id` came from the server. The bullet above now names the call.

- JupyterGIS also opens QGIS projects, `.qgz` and `.qgs`. They send the same messages as maps, so
  Trail supports them once the geospatial preset adds `*.qgz` and `*.qgs`, as the last step of this
  plan does. Each save exports the project through QGIS again. Does that lose anything, and does
  opening a `.qgz` rewrite it? QGIS was not installed for the runs, so this was not tried.
> We will need to also support qgs events

> **Claude:** Agreed. JupyterGIS sends the same messages for `.qgz` and `.qgs` projects, so Trail supports them once the geospatial preset adds `*.qgz` and `*.qgs`, as step 9 does. Without QGIS installed, the runs could not test whether opening a project rewrites it, loses content or changes its source ids. Comparing saved versions of a `.qgz` would also need QGIS to read it. The bullet above, "File-specific changes" and the closing list now say so.

- Two lines are out of date: events.md's "High-frequency events need throttling" says viewport
  events do not exist yet, and layer_v1.yaml says "deleted" where it means `removed`.

### 11. Bugs to report upstream

Most of the failures above come from other packages. jupyter_server_fileid gives files their ids,
JupyterLab is the editor itself, and jupyter-collaboration runs the rooms. One comes from
JupyterGIS. Most of the bugs below lose a user's edits and have a short repro from the runs. The
exceptions are noted.

- JupyterGIS: its migration from format 0.5 to 0.6 draws new random ids each time it loads an old
  map, as topic 4 describes. Ids derived from each layer's own id would stay the same.
- jupyter_server_fileid: renaming a folder updates the ids of the files inside it only if the folder
  itself has an id, which opening a map does not give it. It finds those files by a pattern on the
  folder's full path, so brackets in a folder's or the root's path break it, and a `?` or `*` in a
  renamed folder's name also catches neighboring folders. An open map's room then saves to a path
  that no longer exists.
- JupyterLab: after a folder with characters such as `(` in its name is renamed, open views keep the
  old path.
- jupyter-collaboration:
  - its browser code keeps one connection per file, so a tab and a notebook view of the same map
    share it, and closing the view that opened first stops the other from saving;
  - a room does not notice a map moved outside Jupyter, and every later save fails;
  - shutting down does not wait for a pending save, so the last second's edit is lost;
  - a write that keeps the file's modification time goes unnoticed, and the next autosave
    overwrites it. Restoring a checkpoint, the copy JupyterLab keeps of a file's last manual save,
    is such a write, and so is `cp -p`. A restore was tried; `cp -p` was read from code;
  - a pending autosave can overwrite the outside write that the room just loaded. This was
    reproduced only with the room's read slowed down on purpose;
  - the session message says "Content saved." even when nothing was written. This was read from
    code;
  - the room's store, a database file `.jupyter_ystore.db` that keeps every change made in a room,
    is created in the folder the server was started from, which is often the project. This loses
    nothing, but it puts Jupyter's state in the project.

One workaround exists. jupyter_server_fileid's `LocalFileIdManager` follows files by their inode,
the number the filesystem gives a file and keeps when the file moves. With it, a room followed an
`mv` of an open map and a rename of its folder, and later edits were saved. Trail's notes for
Jupyter users could recommend it.

Ask Matt: will he file these, or should we? Should the notebook view reuse the tab's copy of the
map, which would avoid the shared connection inside JupyterGIS?

## How the plan works

Trail's side of the feed has two inputs and one output. The inputs are the file events Trail's
watcher already sees, and the messages on each Jupyter server's feed. The output is records in
`.trail/events.jsonl`, which the console prints.

```
Jupyter server ── feed ──► Server ── messages ──► Consumer ── records ──► events.jsonl ──► console
                                                     ▲
Trail's file watcher ── file events ──► Writes ──────┘   (Jupyter's own saves are folded in)
```

The parts, each described in its own section later:

- A `Server` is Trail's connection to one Jupyter server: its address, its token and the open
  WebSocket.
- The `Consumer` decides what each message becomes. It drops copies sent by other views, finds the
  file a message is about, and merges the messages of one change.
- A burst is the run of messages about one layer that ends with one save of the map. Dragging the
  opacity slider sends one message per step, and Trail merges the run into one record.
- `Writes` looks at Trail's file events for the files Jupyter has open, and recognizes the ones
  that come from Jupyter's own saves. Those are folded into the record of the change they belong
  to: counted on it, not logged one by one.
- `Messages` keeps the last 1000 messages in memory, so the console can show what came in and what
  became of it.

Here is one change from start to end. You drag the streets layer's opacity in `maps/city.jGIS`
from 1 to 0.35, with the map open in a tab and in a notebook.

1. Each step of the drag is a change. The tab sends 14 `updated` messages, and the notebook view
   sends 14 copies, which Trail drops.
2. Trail merges the 14 messages into one burst.
3. About a second after the last step, the room saves the map. Trail's watcher reports five events
   on the map file, three `opened` and two `modified`. Jupyter Server sends a contents `save`
   message for `maps/city.jGIS`.
4. Trail recognizes the five file events as Jupyter's save and folds them into the burst. The save
   message marks the burst as saved.
5. A quarter of a second later the burst closes, and Trail appends one record. Its class is
   `LayerV1Event`, Trail's class for a layer message, which "The event model" describes. The
   console prints it (illustrative values):

> This is based on a `Burst` abstraction with a different timing configurations; could JupyterGIS send some sort of mouse press vs mouse release messages to better inform us? Or are mouse presses too much friction for the jupyter server and to much of an ask?



```
7. LayerV1Event
    timestamp: 18:02:11.204
    verb: updated
    src_path: maps/city.jGIS
    layer: streets
    change: parameters.opacity 1 → 0.35
    messages: 14
    saved: 18:02:12.215
    count: opened 3, modified 2
```

Without the feed, the same drag leaves five file events and nothing about what changed.

## Changes to Trail's core

Five changes to Trail's existing modules come first. None of them mentions Jupyter.

### Events with no entry

Every record in the log today names the asset or the Dir it is about, by id, in its `entry` field.
You decided that Trail also records messages about maps it does not track, so such a record has
`"entry": null`:

```json
{"cls": "LayerV1Event", "entry": null, "src_path": "/home/arstneio/project/maps/draft.jGIS", ...}
```

Today a record with no entry crashes Trail in `Event.to_record`, `Event.from_record`, `JSONL.read`
and `JSONL.record2entry`. The fix writes and reads `null`, and uses `trail.entries` for the
lookups, as your comment on 10-21-2 asked. `from_record` uses the strict `trail.entries[entry_id]`,
which raises KeyError for an unknown id. `JSONL.read` uses `trail.entries.get(entry_id)`, since it
makes the entry from its first record when the Trail does not hold it yet.

```python
# Event.to_record
        out["cls"] = type(self).__name__
        # an event about no asset, such as one about a map Trail does not track
        if self.entry is None:
            out["entry"] = None
        else:
            out["entry"] = self.entry.id
        return out

# Event.from_record: the id is taken before the event is built
        identifier = record.pop("entry")
        ...
        if identifier is None:
            return event
        entry_id = normalize_id(identifier)
        if resolved_entry is None:
            resolved_entry = trail.entries[entry_id]
        ...

# JSONL.read. The local cache is renamed from `entries` to `seen`, so it reads apart from
# `trail.entries`
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
```

`record2entry` makes an asset from the first record about its id, and it needs that record's
`src_path`. After `events clear`, a Jupyter record can be that first record. So a Jupyter record
with an entry always carries the map's absolute `src_path`.

### Dotted fields in selections

A term such as `events event_type=modified` compares one field of each record. A Jupyter record
keeps the message's payload as it arrived, in a field named `data`, so a term needs a dotted field
to reach inside it. `data.object.layer_id=5b1e…` reads `record.data["object"]["layer_id"]`. These
terms then work:

- `events data.verb=updated data.object.layer_id=5b1e…`;
- `data.object.new.visible=false` and `data.object.new.parameters.opacity<0.5`, with the existing
  conversion of `false` and numbers;
- `count.modified>3` on a dirasset's accesses;
- `entry.name=city.jGIS`, and `entry.path=maps/city.jGIS`, which is still read from the project
  root.

`Select.held` is the method that reads a record's field for a comparison. It gains dotted fields
and the docstring you asked for:

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
          key of the dict read so far. For example, `data.verb` reads `event.data["verb"]`. A part
          that meets something other than a dict is read as an attribute.
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
```

The parts after the first may contain hyphens. You asked for an example. A layer in JupyterGIS's
older style format keeps its colors under hyphenated keys, so this term finds the layers whose
circles are filled pink:

```
events data.object.new.parameters.color.circle-fill-color=#dc8add
```

The current format, which JupyterGIS migrates a map to when the map opens, keeps styles under
`symbologyState`. There the hyphenated names are values in lists, which a dotted field cannot
reach, and the two example maps have no hyphenated key after the migration. So the hyphen only
helps with maps JupyterGIS has not rewritten yet. It costs one character class, so the plan keeps
it.

`Query.comparison` in cli/command.py reads a field ending in `path` against the project root. Its
docstring says so, and it gains the exception for fields under `data.`, since a path inside a
message is relative to its Jupyter server:

```python
        """
        Returns the records one term selects. A path value is resolved against the project root,
        except in a field under `data.`, which holds a payload as it was received.
        """
```

### The class-name guard

The log stores each record's class by name, in `cls`, and replay looks the name up in
`Event.classes`. A second class with the same name would silently take the first one's place.
`Event.__init_subclass__` refuses it, with the condition inline as you asked:

```python
# Event.__init_subclass__
    def __init_subclass__(cls, **kwargs) -> None:
        # slots=True replaces the class captured by zero-argument super()
        super(Event, cls).__init_subclass__(**kwargs)
        known = cls.classes.get(cls.__name__)
        if known is not None:
            # slots=True builds a second class from the same definition, with the same module and
            # qualname. Any other class with a registered name is refused
            if not (
                known.__module__ == cls.__module__
                and known.__qualname__ == cls.__qualname__
            ):
                raise TypeError(f"{cls.__name__} is already an event class in {known.__module__}")
        cls.classes[cls.__name__] = cls
```

A class name, once in a log, must stay registered for good.

### Dotfiles

A dotfile is a file whose name starts with a dot, such as `.env`. When Jupyter saves a file that
already exists, it copies the file to a dotfile `.~<name>` in the same folder, writes the new
content into the file itself, and deletes the copy. It does this for every file it saves, whatever
the extension: maps, notebooks, csv files. The copy usually lives for a millisecond or two, and
Trail waits 0.1 s before it looks at a new file, so Trail usually never sees it.

A slow save is another matter. In the patterns of `.trailtrack`, which follow gitignore, `*`
matches a leading dot, so `*.csv` matches `.~a.csv`. Re-uploading a 600 MB tif kept its copy alive for 178
ms, and Trail tracked the copy as an asset. That asset then stays, with its file gone, and every
later save of the real file logs the copy's events onto it: in one run, 52 events on the copy
against 36 on the real file. If the save fails, Jupyter moves the copy back over the file, and the
file's asset id changes.

10-21-2 skipped names starting with `.~` in `_is_markable`. Your comment called that a code smell,
Jupyter knowledge in the base module, and asked whether `.trailtrack` itself could exclude the
copies. Each way of writing that in `.trailtrack` falls short:

- Jupyter has no list of extensions it does this for, so `!.~*.jgis` misses `.~a.csv` and
  `.~notes.ipynb`.
- A root line `!.~*` loses to any dir scope, wherever it stands in the file, since a dir scope
  decides first. With `data/*.csv` in `.trailtrack`, `data/*.csv` takes `data/.~a.csv`.
  `!./**/.~*` loses the same way, to the deeper Dir. An exclusion in each Dir works, such as
  `!data/.~*` after `data/*.csv`. But each Dir needs its own, and it must stay below every line it
  overrides. A new line is appended, so adding `*.csv` after `!.~*` puts it below and lets the
  copies back in.

> Do dotfiles really have no way to be explicitly excluded in the checklist without the risk of other patterns being prioritized over the exclusion?

> **Claude:** They do have one. An exclusion in each scope set, placed after the lines it overrides, wins. Run against `dir.match`, `!data/.~*` after `data/*.csv` excludes `data/.~a.csv`, and `!data/**/.~*` after `data/**/*.csv` also excludes `data/sub/.~a.csv`. A root `!.~*` and an anchored `!./**/.~*` both lose to the deeper `data` Dir. So the problem is upkeep, not precedence. Each Dir needs its own exclusion, and nothing warns when a new Dir lacks one. The exclusion must also stay below every line it overrides, and `check` appends, so a later `*.csv` lets the copies back in. If Trail kept these lines itself, Jupyter knowledge would be back in the base module. Git has the same model: a deeper `.gitignore` wins, and `core.excludesFile` ranks lowest. The conclusion holds. The bullet above now covers the per-Dir exclusion.

> Yes, well that solution is obvious, but it's clearly not a good solution; we'll have to have implicit dotfile exclusion.
> I was thinking, we are going to have a `checklist.txt` after renaming from `markers.txt`. `checklist.txt` is still not so straightforward; should we rename "checklist"  to "to_track", where the class is renamed to `ToTrack` and the file is renamed to `to_track.txt`? Then, we support an additional file; `to_ignore.txt`, which takes precedence over `to_track.txt`;
> in to_ignore you can patterns to *always* ignore; typically it just contains the dotfile glob pattern. Usually, the user doesn't even edit it. That way, dotfiles are ignored by default, but it's not hard-coded; the user can disable it, or add other patterns to ignore, such as files that contain sensitive information, or some nested directories.

> Can we just always keep the dotfile exclusion glob at the end of the markers file? Would that reliably exclude Jupyter's `.~` files and other dotfiles?

> **Claude:** No. A line's position decides only within one scope set. `match` checks the dir scopes first, deepest Dir first, and reaches the root scopes only if no Dir decided. Run against `dir.match`: a trailing `!.*` is a root scope, so `data/*.csv` still takes `data/.~a.csv`. A trailing `!./**/.*` is a scope of the project's Dir, so `data/**/*.csv` still takes `data/.~a.csv` and `data/sub/.~a.csv`. Neither reaches a Dir outside the project. The line would be reliable only if the matcher checked it before every dir scope, which is a separate tier kept in the same file. `.trailignore` is that tier in a file of its own. The bullet above now says the root line loses wherever it stands. The text below now plans `.trailtrack` and `.trailignore` as the best choice for now, with `ToTrack` and `ToIgnore` reached as `trail.to_track` and `trail.to_ignore`.

- A character class in the pattern works: `[!.]*.jgis` matches `map.jGIS` and not `.~map.jGIS`, at
  any depth. But every line needs it, at the root and in each Dir, since `data/[!.]*.csv` alone
  still loses to a root `*.csv`. Adding `jgis` later appends a plain `*.jgis`, which lets the
  copies back in. Removing `jgis` removes only a plain `*.jgis`, not `[!.]*.jgis`. And a preset
  reaches only new projects.
- `[!.]` excludes every dotfile, not only Jupyter's copies. So it is a general rule, written by
  hand on every line.

So the plan adds a second file, as your comment above proposes. `.trailtrack` says what to track.
`.trailignore`, beside it, says what never to track, and it decides first. A new project's
`.trailignore` holds one line, `.*`, which ignores every dotfile and every hidden folder. Users
rarely edit it. They can empty it, or add lines for files that hold secrets or for folders such as
`node_modules/`. Dotfiles are then ignored unless a line includes them, as your comment on 10-28-1
put it. The rule is a line the user can see and change, not code in the matcher. An earlier draft
built it into `Scopes.decide` instead.

Each file has a class:

- `ToTrack`, in to_track.py, is today's `Markers` under a new name, reached as `trail.to_track`.
  The first appendix renames it.
- `ToIgnore`, in to_ignore.py, is new, and is reached as `trail.to_ignore`. It is the same kind of
  list: the lines of `.trailignore` in file order, with `add`, `discard` and `replace`, and every
  change written through to the file.

Both files use the same line syntax. Decision 15 asks where the code they share lives.

A path is decided in two stages:

1. `trail.to_ignore` decides first. The last line of `.trailignore` that matches the path decides
   it. If that line is not an exclusion, the path is ignored, whatever `.trailtrack` says. A `!`
   line takes a path back, such as `!.env`.
2. A path that `.trailignore` does not ignore goes on to the dir scopes and root scopes of
   `.trailtrack`, as today.

`.trailignore` has no scopes. Its lines are matched against the full path, so the last match in the
file wins, wherever each line points. A line without a slash, such as `.*`, matches at any depth,
inside the project or outside it. An anchored line, such as `data/secrets/`, is resolved from the
project, as in `.trailtrack`. So the depth rule that makes `!.~*` lose in `.trailtrack` does not
apply here.

```python
# to_ignore.py
class ToIgnore(
    Node,
    Sequence[str],
):
    """
    The lines of `.trailignore`, in file order. A path it ignores is never tracked, whatever
    `.trailtrack` says. Every change is written through to the file.
    """

    def decide(self, path: Path) -> str | None:
        """
        Returns the last line that matches the path, or None. Lines are matched against the full
        path. Case is ignored.
        """

    def ignores(self, path: Path) -> bool:
        """Whether the line that decides the path is not an exclusion."""
        line = self.decide(path)
        out = (
            line is not None
            and not line.startswith('!')
        )
        return out
```

The table shows both stages. "Ignored" means `.trailignore` decided the path. "Not taken" means no
line of `.trailtrack` takes it. The `.trailignore` column was checked with pathspec alone, since
Trail does not have the file yet.

| `.trailignore` | `.trailtrack` | `a.csv` | `data/.~a.csv` | `.env` |
|---|---|---|---|---|
| `.*` | `./*`, `data/*.csv` | taken | ignored | ignored |
| `.*`, `!.env` | `./*`, `data/*.csv` | taken | ignored | taken |
| `.*`, `!.env` | `*.csv`, `data/*.csv` | taken | ignored | not taken |
| empty | `./*`, `data/*.csv` | taken | taken | taken |

Two checks consult `trail.to_ignore`. The first appendix moves both into `trail.entries`.

- `trail.entries.is_trackable`, today `Assets._is_markable`, returns False for a path that
  `.trailignore` ignores. It already skips a path inside a dirasset before any pattern. So
  `.trailignore` judges a dirasset by its root. A change to `.zattrs` in a zarr store still counts
  as a change to the store, though `.*` matches `.zattrs`.
- `trail.entries.is_descending`, today `Assets._descends`, does not enter a folder that
  `.trailignore` ignores. This replaces its rule for hidden folders, since `.*` covers them.

`Dirs.tracks` and `Dirs.match` do not change, so `AddAssetEvent.rule` still names the line of
`.trailtrack` that picked an entry up.

Some cases follow from `.trailignore` deciding first:

- A Dir inside an ignored folder, such as `.hidden/data/`, is tracked today. It now needs a line
  such as `!.hidden/data/` in `.trailignore`. Unlike git, pathspec lets a `!` line take back a path
  below an ignored folder. This was checked. The walk need not enter `.hidden/`, since each Dir is
  walked from its own path.
- `.trailtrack` and `.trailignore` are never tracked, even with an empty `.trailignore`.
  `trail.entries.is_metadata`, today `trail._is_ignored`, covers only `.trail`. It covers both files
  too.
- The Jupyter side still sees the copies. It needs their events to tell Jupyter's saves apart. It
  sees each watcher event before Trail applies it, so `.trailignore` does not hide them.
- A project without `.trailignore`, such as one made before this change, gets the default file
  when Trail opens it. To ignore nothing, empty the file, since a deleted file comes back. An empty
  `.trailignore` lets walks into `.git` and `.venv`, and lets Jupyter's copies be tracked again.

Like the built-in rule it replaces, `.*` covers every extension, every dir scope and every project.
It also stops `./*` from tracking `.env` and Jupyter's `.jupyter_ystore.db`, and `*.shp` from
tracking the `._roads.shp` files macOS writes on shared drives.

The earlier draft prototyped its built-in rule on a copy of Trail. Its four new tests carry over,
with the dotfile test now about `.trailignore`: a `.~` copy is not tracked, at the root, in a Dir or
deeper; a `!` line in `.trailignore` takes a dotfile back; a slow save never tracks its copy; and a
failed save keeps the file's asset id. All four failed on today's code. New tests: a missing
`.trailignore` is written on open; an empty one tracks dotfiles; a line of `.trailignore` beats a
deeper Dir scope; a walk skips an ignored folder.

One side effect needs a decision. A line of `.trailtrack` no longer takes a dotfile by itself:
`./.env` takes `.env` only once `.trailignore` has `!.env`. Decision 13 asks what the command that
adds a line does with a path that `.trailignore` ignores.

### One recorder per project

Opening a Trail starts its file watcher by itself whenever an asyncio event loop is running, which
is always the case in a notebook. So the console in a terminal and a notebook on the same project
both record:

```python
# in a notebook, while `trail` runs in a terminal on the same project
trail = Trail("~/project")
# True: this process records too
trail.watchdog.session.is_active
```

With two recorders, every file event was logged twice, and a new file got two AddAssetEvents with
two different asset ids. After a reload, three events pointed at an asset that was no longer
tracked. Listening to Jupyter would double every message too.

So one process at a time records a project. It holds a lock, the file `.trail/lock`, reached as
`trail.lock`. Both kinds of recording take it when they start and let it go when they stop: the
watchdog session, which watches files, and the Jupyter session, which "Connecting to servers"
introduces.

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
        it, and warns once per process, naming that process's pid. A forked child does not
        inherit its parent's holders.
        """

    def release(self, holder: object) -> None:
        """
        Let go for a session that stops. The file is unlocked when the last holder lets go.
        """

    @property
    def owner(self) -> int | None:
        """The pid in the lock file, for the warning that says which process records."""
```

- A session that cannot take the lock does not start. That Trail can still read the log, but it
  does not update live.
- The operating system releases the lock when its holder dies, even on `kill -9`, so no stale lock
  is left behind.
- `.trail/` is already ignored by the watcher, so the lock never becomes an asset or a record.
- flock is local and advisory. It does not protect a folder that two machines sync, and it is weak
  over NFS. Windows would use `msvcrt.locking`.

A recorder inside a notebook has a second problem: it stalls while a cell runs, since the cell runs
on the same loop. In a run with a 2 s blocking call in place of a cell, a csv that lived 0.8 s
during the call was never tracked. Whether a notebook's Trail should record by itself is one of
the decisions.

## The event model

This part decides what a Jupyter record looks like, in the log and in Python. It builds on the
message format from "What to settle with Matt": a `schema_id`, plus the payload with `subject`,
`verb` and `object`.

### What a record stores

A Jupyter record keeps the message as Jupyter Server sent it, split into `schema_id` and `data`,
the payload. Trail's own fields sit next to them. Here is the record of the drag from "How the plan
works" (illustrative values):

```json
{"id": "3f2a…", "cls": "LayerV1Event", "entry": "4e26…",
 "timestamp": "2026-10-21T18:02:11.204+00:00",
 "schema_id": "https://schema.jupytergis.org/events/layer/v1",
 "data": {"subject": "3271840155", "verb": "updated",
          "object": {"document_path": "maps/city.jGIS", "layer_id": "5b1e…",
                     "old": {"name": "streets", "parameters": {"source": "S1", "opacity": 1}},
                     "new": {"name": "streets", "parameters": {"source": "S1", "opacity": 0.35}}}},
 "server": "http://localhost:8888/", "src_path": "/home/arstneio/project/maps/city.jGIS",
 "started": "2026-10-21T18:02:09.880+00:00", "messages": 14,
 "saved": "2026-10-21T18:02:12.215+00:00", "save_error": "", "count": {"opened": 3, "modified": 2}}
```

What each of Trail's fields means:

- `timestamp` is when the last message arrived, since a message carries no time.
- `server` is the Jupyter server's url, never its token.
- `src_path` is the map's absolute path, or "" for a map Trail could not place.
- `started` and `messages` say when the first message of a merged run arrived, and how many
  messages the record merges. A merged record keeps the last message's `data`, with the first
  message's `object.old`, so it still matches the schema.
- `saved` is when Trail saw Jupyter save the change to the file, or null if it never did.
  `save_error` holds the server's error if the save failed.
- `count` holds the file events of that save, by type, which were folded into this record.

Keeping `schema_id` and `data` as received has two benefits. Anyone can check `data` against the
schema `schema_id` names, without knowing Trail's classes, as Pari would for the sample trailfile.
And a field that JupyterGIS adds later is kept, not lost. A selection term reaches into `data` with
a dotted field, such as `data.verb=updated`, as "Dotted fields in selections" shows.

The keys inside `data` are interned when a record is made: equal keys become one shared string,
which saves about 20% of a layer record's memory. The values are not interned.

Three other ways to hold the payload were considered and dropped:

- One dataclass field per payload key would lose the keys Trail does not know.
- Nested TypedDicts for `data`, as in 10-21-2, would type-check only the few direct reads of
  `data`, at the cost of one TypedDict family per schema version. You agreed on 10-27 to drop them.
- An `Envelope` mixin, as in 10-28-1, gave every record and message `subject`, `verb` and `object`
  properties that read `data`. Your comment on 10-28-1 asked whether it was a remnant of the
  earlier design, and it was. Terms such as `data.verb=updated` reach the same values, and the code
  reads `data["verb"]` directly.

### The event classes

Each record's class says what kind of message it holds. Replay rebuilds a record into its class by
the name in `cls`, and the console uses the class to decide what to show. The classes, in
src/trail/jupyter/event.py:

```
Event                           trail/event.py, the base of every record
└── JupyterEvent                any message from a Jupyter server's feed
    ├── ContentsServiceV1Event  a run of Jupyter's saves of one tracked file, with no layer message
    ├── DocumentEvent           a map opened or closed
    │   └── DocumentV1Event     the document/v1 schema
    ├── LayerEvent              a layer added, updated or removed; merges runs of updates
    │   └── LayerV1Event        the layer/v1 schema
    └── ViewportEvent           the map moved: its center and zoom
        └── ViewportV1Event     the viewport/v1 schema
```

- `JupyterEvent` is the one base that every event from Jupyter shares, with no class between it and
  the kind classes, as your comment on 10-21-2 suggested. A JupyterGIS message of a schema Trail
  does not know is recorded as a plain `JupyterEvent`.
- A kind class, such as `LayerEvent`, holds what all versions of that kind share. A leaf, one per
  schema version, only names its schema. So `events cls=LayerEvent` selects every version.
- A leaf stays in the code after a newer version arrives, since old records name it.

```python
# src/trail/jupyter/event.py
@dataclass(kw_only=True, slots=True, repr=False)
class JupyterEvent(Event):
    """
    A message from a Jupyter server's event feed, as Trail records it. The payload is kept as the
    server sent it. JupyterEvent itself records a JupyterGIS schema that has no class of its own.
    """

    # the class of each schema Trail knows, by its $id
    registry: ClassVar[dict[str, type[JupyterEvent]]] = dict()
    # the $id a leaf class records, or "" for a base class
    schema: ClassVar[str] = ""
    # whether a run of updated messages becomes one record
    is_merged: ClassVar[bool] = False
    # whether every view sends the message, so that a copy from another view is a duplicate
    is_shared: ClassVar[bool] = True

    # the map's asset, or None for a map Trail does not track
    entry: Asset | None = field(default=None, init=False, repr=False)
    # the $id of the schema the payload follows
    schema_id: str
    # the payload as the server sent it, with interned keys
    data: dict[str, Any] = field(repr=False)
    # the server's url, never its token
    server: str = ""
    # the map's absolute path, or "" for a map Trail could not place
    src_path: str = ""
    # when Trail saw Jupyter save this change, or None if it never did
    saved: datetime | None = None
    # the server's error if that save failed
    save_error: str = ""
    # the file events of the save that were folded into this record, by type
    count: dict[str, int] = field(default_factory=dict)

    def apply(
            self,
            trail: Trail,
            *,
            replay: bool = False,
    ) -> Asset | None:
        """
        Log the record and return its map's asset. A Jupyter record changes nothing in the Trail,
        so a replay only returns the asset.
        """
```

`LayerEvent` adds the fields of a merged run, and typed properties for the parts of the payload
that the console and the consumer read. They are also selection fields, so `events layer=streets`
works:

```python
@dataclass(kw_only=True, slots=True, repr=False)
class LayerEvent(JupyterEvent):
    """
    A layer added, updated or removed. A run of updates to one layer becomes one record, which
    keeps the first message's `old` and the last message's `new`.
    """

    is_merged: ClassVar[bool] = True

    # when the first message of a merged run arrived
    started: datetime | None = None
    # how many messages the record merges
    messages: int = 1

    @property
    def layer_id(self) -> str | None:
        """The layer's id, its key in the map's `layers`."""

    @property
    def layer(self) -> str | None:
        """The layer's name after the change, or before it for `removed`."""

    def changes(self) -> list[tuple[str, object, object]]:
        """
        Returns each value that differs between `old` and `new`, by dotted path, with both values.
        """


@dataclass(kw_only=True, slots=True, repr=False)
class LayerV1Event(LayerEvent):
    schema: ClassVar[str] = "https://schema.jupytergis.org/events/layer/v1"
```

The other classes work as follows:

- `DocumentEvent` sets `is_shared = False`, since an `opened` or `closed` is never a copy of another
  view's message.
- `ViewportEvent` sets `is_shared = False`, since only the view that moved reports it. It is not
  merged, since the emitter already waits for the map to be still. Its properties give the center,
  the zoom and the extent, in the map's projection.
- `ContentsServiceV1Event` is named after Jupyter Server's contents schema. It records a run of
  saves of one tracked file that no layer message explains, as "Saves with no layer message"
  describes. It adds `started` and `saves`, the time of the first save and the number of saves.
  The console shows its verb as `saved`.
- The console prints a layer record's `change` as at most three `path old → new` pairs from
  `changes()`, then `+N more`. It shows `saved: no` for a layer record that never got a save. Other
  records with no save leave `saved` out, since they never wait for one.

### Evolving a schema

events.md sets three rules for changing a schema. A breaking change gets a new schema with a new
`$id`, such as `layer/v2`. Both versions may be sent side by side for a while. A change that only
adds a field or a verb stays in the same `$id`. For Trail that means:

- Trail picks a record's class by the full `$id`. A `layer/v2` adds a `LayerV2Event(LayerEvent)`
  that overrides the properties whose paths moved, and nothing else changes.
- An `$id` Trail does not know, such as a future `source/v1`, is recorded as a plain
  `JupyterEvent`. It keeps all of `data`, and the console shows it cut short.
- An added field stays in `data`. It can be selected as `data.object.<key>`, and `jupyter show`
  prints it. The proposed `local` field would be such a field.
- An added verb is recorded, but not merged.
- When two versions are sent side by side, the copy in the older version is dropped like a
  duplicate. This is built only once a v2 exists.
- Tests keep copies of the three schema files in `tests/jupyter/schemas/`. They check that every
  schema has a class, that the paths Trail reads exist in the schema, and, when `jupytergis_core`
  is installed, that the copies match JupyterGIS's own.

## Connecting to servers

This part covers how Trail finds Jupyter servers and holds their feeds open. The next part, "From
messages to events", covers what Trail does with the messages.

### `trail.jupyter` and `trail.jupyter.session`

Trail's file watching is split in two: `trail.watchdog` decides what to watch, and
`trail.watchdog.session` is one run of the watching. The Jupyter side is split the same way, as you
agreed on 10-21-2:

```python
# what to connect to: the servers found on this machine, those added by hand, and the rule
trail.jupyter
# one run of the connections: the open sockets, the messages, the records being built
trail.jupyter.session

async with trail.jupyter.session:
    ...
```

`trail.jupyter` is a `Jupyter`, in src/trail/jupyter/jupyter.py. It holds no connection itself:

- `jsons` holds the runtime files of the Jupyter servers on this machine (see "Finding servers").
- `connect(*, url, token, root)` adds a server Trail cannot find by itself, such as one in a
  container, and `disconnect(url)` stops listening to one. Both last for the rest of the process.
- `overlaps(root)` decides which servers connect by themselves, and `is_inside(folder)` whether a
  map is part of the project. "Finding servers" and "Resolving the map" give the two rules.
- `on_notice` and `on_message` are callbacks the console sets.
- Its repr lists the servers a run would connect to, or those it is connected to, never a token.

`trail.jupyter.session` is a `JupyterSession`, in src/trail/jupyter/session.py. It is one run:

- `servers`, `consumer`, `writes` and `messages` hold the run's state. Each has its own section
  below.
- `start()` and `stop()` begin and end a run. `async with trail.jupyter.session` calls both.
- `del trail.jupyter.session` drops the run, so the next read gives a fresh one. The descriptor that
  does this for the watchdog session moves into a base that both sessions share.
- `flush(asset)` records a map's open records before the map moves, is deleted or is offtrailed.
  `drop()` forgets them, which `events clear` uses.

Nothing starts when a Trail opens, since connecting to servers is a side effect. The console starts
the session, and a library user calls `start()` or uses `async with`.

```python
# src/trail/jupyter/session.py
class JupyterSession(Node):
    """
    One run of Trail's connections to Jupyter servers. It is made on first read of
    `trail.jupyter.session` and kept until it is deleted.
    """

    _parent: Jupyter
    # seconds stop() waits for the autosave of a change that is not saved yet
    settle = 1.5

    def start(self) -> bool:
        """
        Start the run on the running loop. Returns True if the run is active afterward. Returns
        False if no loop is running, or if another process records the project and holds its lock.
        The run takes the lock, lets `writes` see Trail's file events, and starts two tasks: one
        that looks for servers every 2 s, and one that closes records when their time comes. If any
        of this fails, what was done is undone.
        """

    async def stop(self) -> None:
        """
        End the run. Does nothing if no run started, so it is safe to call twice. Otherwise it
        waits up to `settle` seconds for open records to close after their save, closes the
        sockets, decides the file events `writes` still holds, records what is open, and lets go
        of the lock.
        """
```

### Servers

A `Server`, in src/trail/jupyter/server.py, is Trail's handle on one Jupyter server during a run. It
holds the server's runtime file, the state of the connection, and what the feed has sent so far.
The run keeps its servers in a `Servers`, by url, as `trail.jupyter.session.servers`. The console's
`jupyter servers` command shows one row per server (illustrative):

```
#1  subscribed  http://localhost:8888/  ~/project   received 1204, recorded 9, merged 41, skipped 37
#2  available   http://localhost:8889/  ~/other     outside this project
```

A server's state is one of these:

```python
# the life of a Server, from the moment Trail finds it
State = Literal[
    # found, and not connected: its root does not overlap a Dir, or it listens on a unix socket,
    # or it takes a password and no token
    "available",
    # Trail is opening its feed
    "connecting",
    # its feed is open
    "subscribed",
    # the socket dropped or could not open, and Trail tries again after a pause
    "retrying",
    # the server refused the token or the certificate
    "refused",
    # the server has no feed
    "unsupported",
    # the task that reads the feed crashed
    "error",
    # `jupyter disconnect` stopped listening to it
    "disconnected",
    # its runtime file or its process is gone
    "stopped",
]
```

Each attribute of `Server` has its own comment above it, as you asked on 10-21-2. The class
docstring only says what the class is for:

```python
@dataclass(eq=False, kw_only=True)
class Server:
    """
    Trail's handle on one Jupyter server during a run. The consumer asks it for the feed's
    address, the headers that carry the token, and the local path of a map.
    """

    # the run's servers, which hold this one
    servers: Servers = field(repr=False)
    # its `#N` in the console. Numbers are not reused within a run
    number: int
    # the runtime file it was found with, or the details given to `connect`
    json: JSON
    # how the connection is doing
    state: State = "available"
    # why the connection last failed, for `jupyter servers`
    last_error: str = ""
    # the numbers `jupyter servers` shows
    counts: Counts = field(default_factory=Counts)
    # the version of each JupyterGIS schema seen in this server's messages so far, by $id
    schemas: dict[str, int] = field(default_factory=dict)
    # the new path of each path renamed through the server
    renames: dict[str, str] = field(default_factory=dict)
    # the monotonic time the server last reloaded each path from disk
    reloading: dict[str, float] = field(default_factory=dict)
    # the task that reads the feed, while it runs
    task: asyncio.Task[None] | None = None
    # seconds before the first retry
    backoff: float = 0.5
    # the most seconds any retry waits
    ceiling: float = 30.0

    @property
    def is_running(self) -> bool:
        """
        Whether the server's process is alive and is the one that wrote its runtime file.
        """

    @property
    def is_stopped(self) -> bool:
        """
        Whether the server's runtime file or its process is gone. A server given to `connect` has
        neither, so only `disconnect` stops it.
        """

    def headers(self) -> dict[str, str]:
        """Returns the headers of a request to the server, with the token."""

    def locate(self, document_path: str) -> Path | None:
        """
        Returns the local path of a file the server names by its path under its root.
        """

    def is_reloading(
            self,
            found: Map,
            seen: float,
    ) -> bool:
        """Whether the server reloaded the map from disk shortly before `seen`."""

    def fail(
            self,
            state: State,
            reason: str,
    ) -> None:
        """Set the state and `last_error`, and tell the console."""
```

`Counts` holds the numbers in the console's row: messages received, recorded and merged, the
skipped ones by reason, and the messages of other schemas by schema. `Map` is what the
consumer's lookup of a message's file returns, as "Resolving the map" describes.

### Finding servers

Every Jupyter server writes a runtime file when it starts, `jpserver-<pid>.json` in
`~/.local/share/jupyter/runtime`, and deletes it when it stops cleanly. A server that crashes
leaves its file behind. `jupyter server list` reads these files, and so does Trail. One looks like
this, with the token left out:

```json
{"url": "http://localhost:8888/", "token": "…", "root_dir": "/home/arstneio/project",
 "pid": 48213, "port": 8888, "base_url": "/", "sock": "", "version": "2.21.1"}
```

A `JSON` is one such file, read into the fields Trail uses. `trail.jupyter.jsons`, the name you
suggested to match `server.json`, holds them all, by path:

```python
# illustrative
trail.jupyter.jsons
# JSONs (2)
#     ~/.local/share/jupyter/runtime/jpserver-48213.json: http://localhost:8888/ ~/project
#     ~/.local/share/jupyter/runtime/jpserver-51190.json: http://localhost:8889/ ~/other
```

```python
# src/trail/jupyter/server.py
@dataclass(frozen=True, slots=True, kw_only=True)
class JSON:
    """
    One `jpserver-<pid>.json` file from Jupyter's runtime folder, or the same details given to
    `connect` for a server that writes no file.
    """

    # the file, or None for a server given to `connect`
    path: Path | None = None
    # where the server listens, such as http://localhost:8888/
    url: str
    # the folder the server serves, resolved
    root: Path
    # the token that lets Trail in. It is never shown
    token: Secret = field(repr=False, compare=False)
    # the server's process
    pid: int | None = None
    # the file's modification time, to tell the server from a later process with the same pid
    written: float = 0.0
    # the unix socket the server listens on in place of a port, or ""
    sock: str = ""


class JSONs(Mapping[Path, JSON]):
    """
    The runtime files in Jupyter's runtime folder, each read into a `JSON`, by path.
    """

    def refresh(self) -> None:
        """
        List the folder again. A new file, or one whose modification time changed, is read. A file
        that is gone is dropped.
        """
```

- `Secret` holds the token. Its repr is `<secret>`, and it cannot be pickled.
- `Servers.refresh` runs every 2 s. It reads `jsons` and the servers given to `connect`, starts a
  `Server` for each new live server that should connect, and stops a server whose file or process
  is gone. A server restarted on the same port writes a new file, and its `Server` takes the new
  token from it.
- Trail checks that a server's process is alive with psutil, since a crashed server leaves its file.
  All 15 runtime files on this machine are stale today.
- Trail reads only `jpserver-*.json`. The `jpserver-*-open.html` file next to each also holds the
  token, and Trail never reads it.

Which servers connect by themselves? You asked why not every server. A server can only name files
under its own root, so a server whose root neither contains one of Trail's Dirs nor sits inside one
can never name a file Trail tracks. Connecting to it finds nothing, and it costs a socket that
carries that server's every message, and a token Trail cannot use. So a server connects by itself
when its root overlaps a Dir:

| The server's root, with Dirs `./` and `data/` | Connects by itself |
|---|---|
| the project, or `project/data` | yes |
| `project/notebooks`, inside the root Dir | yes |
| the project's parent, or the home folder | yes; its messages about other projects are counted and dropped |
| an unrelated folder | no; it cannot name a file Trail tracks |

10-21-2 connected only to a root inside a Dir, which left out a JupyterLab started from the home
folder. A server that does not connect gets one row in the console, and `jupyter connect 2` still
connects it by hand, for example when its root is a path inside a container.

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

### Subscription

Once a server connects, Trail keeps its feed open, which needs a WebSocket client. You asked what
tornado is and why it is needed. tornado is the networking library Jupyter Server itself is built
on. Python's standard library has no WebSocket client, and tornado provides one, on asyncio, so the
connections share the console's event loop with the file watcher. Trail already installs tornado,
since it depends on ipykernel, which requires it. The other choice is the `websockets` library,
compared in the decisions.

There is one task, and one socket, per server. 10-21-2 put this code in consumer.py, the name you
gave the module. That name now goes to the `Consumer` of "From messages to events", so the
subscription moves to subscription.py:

```python
# src/trail/jupyter/subscription.py
async def subscribe(server: Server) -> None:
    """
    Keep the server's feed open, and open it again after a drop, until the server stops, refuses
    Trail, or turns out to have no feed.
    """
```

- The token goes only in the `Authorization` header, never in the url, so no error message shows
  it.
- After a drop, Trail waits 0.5 s, then twice as long each time up to 30 s, and tries again.
- Jupyter Server sends no close message when it stops, and no keepalive pings. Trail sends its own
  pings every 20 s, so a socket left half open, for example by a sleeping laptop, is noticed.
- A server restarted on the same port refuses the old token. Trail then reads the runtime files
  again and, if the server wrote a new one, tries once more with the new token. 10-21-2 gave up at
  that point.
- A message that fails in the consumer is counted, and the feed goes on.
- tornado ignores proxy settings, follows no redirects, and closes a socket on a message over
  10 MiB. Trail raises that limit for the feed.

### Which schemas a server sends

10-28-1 asked each server which JupyterGIS schemas it knows, with a probe: a POST meant to fail,
whose error message gave the answer. As your comment on 10-28-2 pointed out, that is a code smell,
and Trail can do without it. Trail learns a server's schemas from the messages it sends, and keeps
each one's version in `Server.schemas`. `jupyter servers` shows the schemas seen so far, or
`no JupyterGIS messages yet`. That cannot tell a server whose JupyterGIS has no feed, as in every
release so far, from one where nothing has happened yet. A route in JupyterGIS's server code that
lists its schemas would close that gap, if one is added later.

## From messages to events

This part follows a message from the moment it arrives until it becomes a record, or is skipped.
It uses the background from "What to settle with Matt": views and their subjects, rooms, autosave,
and the session and contents messages.

### Messages

Trail keeps the JupyterGIS messages it receives in memory, recorded or not, so that the console can
show what came in and what became of it. A `Message`, in src/trail/jupyter/message.py, is one
received message, and `Messages` keeps the last 1000, as `trail.jupyter.session.messages`. The
console's `jupyter messages` lists them, with the same terms as `events` (illustrative):

```
jupyter messages (3 of 41)
  39  18:02:09.880  #1  layer/v1 updated  3271840155  maps/city.jGIS  merged → 3f2a…
  40  18:02:09.891  #1  layer/v1 updated  1120334671  maps/city.jGIS  skipped: duplicate
  41  18:02:11.204  #1  layer/v1 updated  3271840155  maps/city.jGIS  merged → 3f2a…
```

```python
# src/trail/jupyter/message.py
Outcome = Literal["pending", "recorded", "merged", "used", "skipped"]


@dataclass(kw_only=True, slots=True, repr=False)
class Message:
    """One message from a server's feed, as received, with what became of it."""

    # the message's key in Messages
    id: str = field(default_factory=lambda: uuid4().hex)
    # its number in this process, kept when older messages are dropped
    number: int
    # when it arrived, since a message carries no time
    timestamp: datetime
    # the url of the server that sent it
    server: str
    # the $id of the schema its payload follows
    schema_id: str
    # the payload as received
    data: dict[str, Any] = field(repr=False)
    # the map's absolute path, once Trail found it
    src_path: str = ""
    # what became of it
    outcome: Outcome = "pending"
    # why it was skipped
    reason: str = ""
    # the id of the record that holds it
    event: str = ""
```

- `jupyter show 40` prints message 40 as Jupyter Server sent it.
- `used` is the outcome of one of Jupyter's own contents or session messages that the consumer
  acted on, such as a save.
- A message's number stays the same when older messages are dropped, while its position in a
  listing shifts.
- The live tail, `jupyter tail on`, prints each message as it arrives, with its outcome.
- A message about a map outside the project is counted but not kept, since a server rooted at the
  home folder carries every project's maps.

### The consumer

The `Consumer`, in src/trail/jupyter/consumer.py, turns messages into records. It is
`trail.jupyter.session.consumer`, and each `Server` hands it what its feed sends. 10-28-1 called it
`Pipeline`, and your comment on 10-28-1 suggested `Consumer`, reached as
`trail.jupyter.session.messages.consumer`. The name fits: the watchdog session's task that applies
file events is its consumer too. It hangs off the session, not off `messages`, because `Messages`
is not a queue the consumer empties. The consumer takes each message straight from a socket, and
`Messages` keeps a copy afterward, with its outcome, for the console.

A message goes one of three ways:

- Jupyter's own contents and session messages go to `observe`. They tell the consumer when Jupyter
  saved, renamed or reloaded a file, which JupyterGIS's messages never say.
- A message of any other schema that is not JupyterGIS's is only counted.
- A JupyterGIS message goes to `route`, which skips it or adds it to a record being built, and is
  then kept in `Messages`.

You asked for docstrings that say what these methods do and why they are needed:

```python
# src/trail/jupyter/consumer.py
class Consumer(Node):
    """
    Turns the messages of every server's feed into records. Each Server hands it the messages it
    reads, and a task closes the records whose time has come.
    """

    _parent: JupyterSession
    # seconds after its last message that a burst closes, if Trail sees no save of its map
    pause = 2.0
    # seconds a burst stays open after the save of its map, for the file events of that save
    grace = 0.25
    # seconds within which an equal message from another view is a duplicate
    window = 1.0
    # seconds after a reload of a map during which its layer messages are not a person's edits
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
        Handle one message from a server's feed. Returns the message kept for it, or None.

        The message is counted on its server and stamped with its arrival, since it carries no
        time. `at` and `seen` let tests set the clock and the monotonic time. Jupyter's own contents
        and session messages go to `observe`. A message of another schema is only counted. A
        JupyterGIS message goes to `route` and is then kept in `Messages`, unless it is about a map
        outside the project. An error in `route` is written on the message, so that one bad
        message does not end the feed.
        """

    def route(
            self,
            server: Server,
            record: Message,
            seen: float,
    ) -> None:
        """
        Decide what becomes of one JupyterGIS message. The message is skipped in four cases, and
        the reason is written on it.

        - Another view of the map sent an equal message within `window` seconds.
        - It names no map Trail can place, as `resolve` reports.
        - It is a layer message for a map the server is reloading from disk.
        - It is an update whose old and new layers are equal.

        Otherwise it joins a burst. The first, third and fourth checks are needed because every
        view reports every change. Once messages carry `local`, the consumer keeps the local
        message and drops the others, and these checks become a fallback.
        """

    def observe(
            self,
            server: Server,
            message: dict[str, Any],
            at: float,
            seen: float,
    ) -> Message | None:
        """
        Use one of Jupyter's own contents or session messages, and return it if it was kept. These
        messages are how the consumer learns that Jupyter saved, renamed or reloaded a file.
        """
```

The duplicate check keeps, for `window` seconds, each shared message's server, schema, verb and
object, with the subject that sent it. A message is a duplicate if another subject sent an equal
one within that window. An update is unchanged if its `old` and `new` layers are equal.

`observe` handles each kind of message as follows:

- A contents `get` is counted, and `Writes.noted` marks its file as open in Jupyter. A room reads
  its file once a second, so `get` is nearly the whole feed: 98% of 13,708 messages in a 7.8 h
  browser session.
- A contents `save` goes to `Writes.saved`, which matches it with the file events of the save. "When
  Jupyter writes a file Trail tracks" describes this.
- A contents `rename` is kept on the server, so a stale path can still be found.
- A session `overwrite`, or a rebuilt room's pair of `initialize` "The file is out-of-sync with the
  ystore." and `load` "Content loaded from disk.", marks the map as reloading. An `overwrite` also
  closes the map's open bursts at once, since their change was lost in the reload.
- A session message at level `ERROR`, such as "Error saving file: maps/m.jGIS HTTPError()", marks
  the map's open bursts as not saved, with the message.
- A session `save` is counted but never taken as proof of a save, since the room also sends it when
  it wrote nothing.

### Resolving the map

Each JupyterGIS message names a map, and Trail needs the map's asset. `Consumer.resolve` finds it,
and returns a `Map`. For example, a message about `maps/city.jGIS` from a server rooted at
`~/project` gives `Map(asset=<the city.jGIS asset>, path=~/project/maps/city.jGIS)`, and a message
about a file that no longer exists gives `Map(asset=None, path=..., reason="file missing")`.

You asked why 10-21-2 called this class `Target` and not `Map`. `Target` was meant to cover
messages about files other than maps, but every message `resolve` sees names a map, so the plan
now calls it `Map`. Code names an instance `found`, since `map` would shadow the builtin.

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Map:
    """
    The map a message is about, as `Consumer.resolve` found it. It holds the map's asset, or only
    its path for a map Trail does not track. For a map that could not be placed, it holds the
    reason.
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
```

`resolve` tries these in order:

1. `server.locate(document_path)` turns the message's path into a local path, under the server's
   root. A null path, or one ending in `unsaved_project`, gives none. A path that leaves the root,
   such as one with `..`, is refused. Symlinks are resolved only after that check, so a link inside
   the root that points into the project still reaches the project's file.
2. If the message's subject, its view, is linked to an asset Trail still tracks, that asset.
3. The asset at the path, or the one `trail.assets.scan(path)` tracks now if `.trailtrack` takes the
   file. The subject is then linked to it.
4. If nothing is at the path, a rename the server reported, of the path or of a folder above it.

10-21-2 tried the path first, on the belief that after Save As a subject stays linked to the old
file. The browser runs showed that Save As opens a new view with a new subject, so a subject names
one map for its whole life. Trying the subject first covers the views whose path goes stale, after
another client's rename, in a notebook view, or after a move outside Jupyter. It also keeps a stale
view's messages off a new file made at its old path. Links last until Trail stops. If JupyterGIS
adds `document_id`, step 2 uses it instead, and links could outlast a restart of Trail.

| The message names | What Trail records |
|---|---|
| a tracked map, or a subject linked to one | a record on that asset |
| an untracked map that `.trailtrack` takes | an AddAssetEvent, then a record on the new asset |
| an untracked map inside the project | a record with no entry, with `src_path` |
| a map with no file | nothing: skipped as `unsaved` |
| an offtrailed map | nothing: skipped as `offtrailed` |
| a path that does not exist | nothing: skipped as `file missing` |
| a map outside the project, or outside the server's root | nothing: skipped as `outside project` or `outside root` |

A map is inside the project when its folder is a Dir's folder, or inside a Dir that tracks
subfolders. That is `trail.jupyter.is_inside`. Unlike `overlaps`, it does not accept a folder above
a Dir, since a map in the home folder is not part of the project.

### Bursts

A burst is the run of messages about one layer, or one document, that will become one record. It
stays open until Jupyter saves the map, so that one record matches one save. For the drag in "How
the plan works":

```
18:02:09.880  updated  opacity 1 → 0.95       a burst opens
              ... 12 more updated ...         each joins the burst
18:02:11.204  updated  opacity 0.4 → 0.35     the last step
18:02:12.215  contents save of the map        Jupyter saved it: the burst is saved
18:02:12.465                                  0.25 s later the burst closes as one record
```

```python
@dataclass(slots=True, eq=False, kw_only=True)
class Burst:
    """
    The open run of messages about one layer or one document, before it becomes a record.
    """

    # the server that sent the messages
    server: Server
    # the class of the record the burst becomes
    event_cls: type[JupyterEvent]
    # the map the messages are about
    found: Map
    # the messages of the run, in the order they arrived
    merged: list[Message] = field(default_factory=list)
    # monotonic seconds of the last message, which the pause is measured from
    seen: float
    # the contents save message that confirmed the burst. Its arrival becomes the record's `saved`
    save: Message | None = None
    # monotonic seconds of that save, which the grace is measured from
    saved: float | None = None
    # the session error that says the save of the map failed
    save_error: str = ""
    # the file events of the save that were folded into the burst, by type
    count: Counter[str] = field(default_factory=Counter)
```

The rules:

- A burst is keyed by the server, the map, the schema and the layer. Documents and viewports add the
  subject to the key, since their messages are never copies of another view's.
- An `updated` message joins the open burst of its key, unless that burst is already saved. Any
  other message under the key, and an `updated` whose burst is saved, starts a new burst. The burst
  it replaces takes no more messages but stays open until its own deadline, so the coming save
  still reaches it.
- `added`, `removed` and `opened` are held like `updated`, since a save follows them too. The save
  that follows a map's `opened`, about 1.2 s later, is folded into the `opened` record. `closed` is
  recorded once the map's other bursts close. A viewport `moved` is recorded at once, since its
  save came about 9 s earlier.
- One save confirms every open burst of its map. Its file events are counted once, on the burst
  whose last message came last.
- A burst closes 0.25 s after Jupyter saves its map. If no save comes, it closes 2 s after its last
  message and is recorded with `saved: null`.
- When a burst closes, its messages are marked `merged` in `Messages`, with the record's id.
- A record can reach the log a little after file events of other files that came later. Each record
  keeps its own `timestamp`. A dirasset's access works the same way today.
- When Trail stops, it waits up to 1.5 s for open bursts to close after their save, then records
  everything still open.

> Still, having these preset time configurations seems like a code smell to me. Look into eventfeed; could it be modified to emit "start" and "stop" messages for burst of events? E.g. SliderClicked; SliderUnclicked, etc.

> **Claude:** It could, but start and stop messages are the weaker form. A stop can go missing or arrive before the drag's last `updated`, and arrow keys, typing, Python and reloads have no drag, so Trail would keep a timeout anyway. JupyterGIS already knows when a drag ends, since Radix calls the slider's `onValueCommit` once on release. Asking Matt for one `updated` per drag is better, ideally by having the slider write the shared document only on release. That removes the timer that decides where a burst ends, which today is the room's 1 s autosave. `local`, and `last_modified` and `size` in the save message, remove most of the rest. `Consumer.pause` and `JupyterSession.settle` stay as long as a record waits for its save, as in decision 1 (A).

> Will Burst still be necessary with our upstream changes to jupyter server and event feed such that modified time and hash are propagated in the messages?

> **Claude:** Yes, but smaller. A burst has two jobs: merging the messages of one change into one record, and confirming that the change was saved. The new fields help only with confirming. Merging stays while a drag sends an `updated` per slider step (topic 9) and other views send copies (topic 2). Only one `updated` per drag, through `onValueCommit`, and `local` would remove it.
>
> With the hash, Trail knows that the map it reads is the version the room saved. So it confirms a burst only if the saved map holds the burst's last `new`, not because any save of the map came, and it closes the burst once that save is matched, not 0.25 s later. The 2 s deadline stays for losses no message reports, such as the server stopping within a second of the edit (topic 3). With the emitter changes too, a burst is one record waiting for its save, and a variant of decision 1 (B) could drop even that wait: log each record at once, and have the save record list the records it confirmed.

### Saves with no layer message

Many saves come with no layer message to fold into: a second view of an open map, a rename, a
reconnect, a pan, and every edit the emitter does not report. Notebooks and text files open in
JupyterLab are saved too, about a second after each pause in typing. 10-21-2 recorded one `saved`
record per save, so someone typing in a tracked notebook would add a record every few seconds.

This plan records a run of saves of one file as one `ContentsServiceV1Event`. A run starts with a
save that no burst takes. It closes after 30 s with no save, as a dirasset's access closes, or when
a burst of the same map opens, so that the records stay in order. The consumer keeps each file's
open run in `Consumer.runs`, by path, and closes, flushes and drops runs as it does bursts. The
record keeps the time of the first and last save, the number of saves, and the folded file events.
For example, typing in a tracked notebook for five minutes, in short bursts, gives one record
(illustrative):

```
12. ContentsServiceV1Event
    timestamp: 14:35:02.118
    verb: saved
    src_path: analysis.ipynb
    started: 14:30:11.402
    saves: 61
    count: opened 183, modified 122
```

One record per save, as in 10-21-2, is the other choice in the decisions.

### When Jupyter writes a file Trail tracks

This answers your question: when a Jupyter message explains a file event, should Trail suppress
the file event, and how?

First, what a Jupyter save looks like to Trail's watcher. Jupyter saves a file in place: it copies
the file to `.~<name>`, writes the new content into the file, and deletes the copy. One measured
autosave of a map, as Trail's watcher reported it:

```
 0.0 ms  opened    city.jGIS     the copy is made: Jupyter reads the map
 0.3 ms  created   .~city.jGIS
 0.4 ms  opened    .~city.jGIS
 0.5 ms  modified  .~city.jGIS
 0.6 ms  modified  .~city.jGIS
 0.7 ms  opened    city.jGIS     the new content is written
 0.7 ms  modified  city.jGIS
 1.2 ms  modified  city.jGIS
 1.3 ms  deleted   .~city.jGIS   the copy is removed
 3.1 ms  opened    city.jGIS     the room reads the map back
 3.8 ms  contents save message for maps/city.jGIS arrives
```

So on the map itself, a save is three `opened` and two `modified`. None of them is a person's
access, and none changes what Trail tracks: on replay, only `created` and `moved` change Trail's
state. Jupyter's other file operations, such as new files, copies, renames, Save As and deletes,
do create, move or delete files, so they change what Trail tracks. A `deleted` changes nothing on
replay either, but it is the log's only record that the file is gone.

The save message arrived 3.8 ms after the save began here, but it can also arrive before the file
events. And 10-21-2 held every `opened` and `modified` of a map while any burst of the map was
open, and folded them all into the burst once a save arrived. That catches other programs' writes
too. In a browser run, a script wrote the map 0.3 s after a person's change. The room loaded the
script's version, and the person's change was lost. Under 10-21-2's rule, Trail would have logged
only the person's change, marked as saved, with the script's write hidden in its count.

Trail has four ways to handle the file events of a Jupyter save:

- Suppress them: drop them. The log is smallest, but a wrong match erases another program's write
  without a trace.
- Fold them: count them on the record of the change, as `count: opened 3, modified 2`. The count
  leaves a trace, as long as the match is right.
- Link them: log each one, with the id of the record that explains it. Nothing is lost, but every
  autosave adds five lines to the log.
- Tag them: log each one with a tag such as `writer: jupyter`. This is like linking, without the
  link.

This plan never suppresses. It folds, and only what clearly belongs to a save:

- A file event is folded only into a save Trail knows of from a contents `save` message for the
  same path.
- Which events belong to that save is decided by the save's bracket: the file's events from the
  `opened` just before `created .~<name>` to `deleted .~<name>`, plus the one `opened` right after.
  The operating system reports these in order. A save that a crash left half done leaves its copy
  behind, so the next save's bracket starts with `modified .~<name>` instead of `created`.
- Without a bracket, the events within 0.25 s of the save message, before or after it, belong to
  the save. Jupyter writes no copy when `use_atomic_writing` is off, for a new file, for a file in a
  folder Jupyter cannot write to, for the later chunks of an upload, and when it restores a
  checkpoint.

Everything else is logged as today. `created`, `moved` and `deleted` are never folded. They are
also not linked to the contents message that announces them, so the log does not say that
JupyterLab, not another program, moved or deleted a file. Asking Jupyter for a hash of the file
would not help, since it is computed when Trail asks, from whatever is on disk then. The file's
`last_modified` and `size` inside the save message would, as topic 10 of "What to settle with
Matt" asks.

The same goes for every tracked file that Jupyter saves, not only maps. A notebook or a csv open in
JupyterLab is autosaved the same way, about a second after each pause in typing. This was read from
the code, and tried with a csv, not with a notebook.

`Writes`, in src/trail/jupyter/writes.py and reached as `trail.jupyter.session.writes`, does the
matching. It has to see file events before Trail applies them, including those of the `.~` copies,
which Trail never tracks. So the base gets one general hook, which names nothing about Jupyter: the
watchdog session keeps a list of observers, and passes each file event to them before applying it.
An observer that returns True keeps the event, and applies it later itself. The Jupyter session
adds `writes.observe` while it runs.

```python
# session.py, WatchdogSession
    @cached_property
    def observers(self) -> list[Callable[[WatchdogEvent], bool]]:
        """
        Callables that see each file event before it is applied. One that returns True keeps the
        event, and applies it later itself.
        """
        out: list[Callable[[WatchdogEvent], bool]] = []
        return out

# WatchdogSession.consume, after the branch for folders
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
    Jupyter's writes of tracked files, as Trail's watcher reports them. It keeps the `opened` and
    `modified` events of each file Jupyter has open, and folds those that belong to a Jupyter
    save into the record that save belongs to.
    """

    _parent: JupyterSession
    # seconds a kept event waits for the save message of its write, before or after it
    grace = 0.25
    # seconds a file counts as open in Jupyter after a message about it, such as a room's read
    recent = 2.0

    def observe(self, event: WatchdogEvent) -> bool:
        """
        See one file event before it is applied. Returns True if the event is kept.

        - An event on a `.~<name>` copy opens or closes its file's bracket, and passes on.
        - An `opened` or `modified` of a tracked file that Jupyter has open, or that lies inside an
          open bracket, is kept.
        - A `moved` or `deleted` of a tracked file first decides that file's kept events. It then
          records the file's open bursts and its run of saves, and passes on.
        - Every other event passes on.
        """

    def noted(
            self,
            path: Path,
            seen: float,
    ) -> None:
        """
        Note a contents message about a path. The file then counts as open in Jupyter for `recent`
        seconds.
        """

    def saved(
            self,
            path: Path,
            at: float,
            seen: float,
    ) -> None:
        """
        Note a contents `save` message for a path, which arrived at the clock time `at`. The kept
        events of that path are matched against it.
        """

    def settle(self, now: float) -> None:
        """
        Decide the kept events whose wait is over. An event that belongs to a save is folded. Any
        other event is applied, in the order it arrived.
        """
```

`Writes` decides a kept event as follows:

- A file counts as open in Jupyter while its room reads it each second, and for 2 s after any
  contents message about it. Other files' events pass through at once, so they are never late. A
  save of a file that no room reads and that had no recent contents message, such as a one-off
  upload, is logged as today.
- Folded events go to the save they belong to. If the map has open bursts, the save confirms them,
  and the counts go on one of them, as "Bursts" says. Otherwise they go to the file's run of saves.
- An event that belongs to no save is applied once 0.25 s have passed and no bracket of its file is
  open. It is logged as today, at most 0.25 s late.
- A failed save ends its bracket with `moved .~<name>` back onto the file, and folds nothing. A
  bracket that sees no event on its copy for 2 s, as after a crash in the middle of a save, is
  dropped, and its events are applied.
- A bracket with no save message, for example from a server Trail is not connected to, folds
  nothing, and its events are logged as usual.
- Replay is unchanged, since only `opened` and `modified` are ever folded.
- For the script example above: the script's write lies outside any bracket and away from any save
  message, so it is logged as its own `modified`. The person's change gets no save, since the reload
  lost it, and is recorded with `saved: null`.

> Is there anything that the jupyter eventfeed can do for a better solution?

**Claude:** **Who writes the file.** Three programs send messages to the feed that Trail listens to:

- **Jupyter Server** is the program `jupyter lab` starts. JupyterLab reads and writes every file through its *contents manager*. The contents manager sends a *contents message* for each read and each write, such as `{"action": "save", "path": "maps/city.jGIS"}`.
- **jupyter-collaboration** is an extension of Jupyter Server. For each open file it keeps a *room*, the server's copy of the document, which keeps all open views of the file in step. About 1 s after each change, the room saves its copy by asking the contents manager to write the file. It reports what it does with *session messages*. Examples are `initialize` when the room starts, `save`, `overwrite` when it reloads a file that another program wrote, and `clean` when the room is deleted.
- **JupyterGIS's emitter** is Matt's code. It runs in the browser and sends the layer, document and viewport messages. It never writes the file.

So when you drag a slider, the emitter reports the change. About a second later, the room writes the file through Jupyter Server.

**What one autosave sends today.** The contents manager writes the file and sends a contents message. The room then reads the file back to compute a hash, and sends a session message:

```json
{"schema_id": "https://events.jupyter.org/jupyter_server/contents_service/v1",
 "action": "save", "path": "maps/city.jGIS"}

{"schema_id": "https://schema.jupyter.org/jupyter_collaboration/session/v1",
 "level": "INFO", "room": "text:jgis:4029a877-…", "path": "maps/city.jGIS",
 "action": "save", "msg": "Content saved."}
```

Neither message says which version of the file was written. Trail's own watcher reports the file events of the write separately, and Trail has to work out which events go with which message. That's why `Writes` relies on the `.~` copy and on time limits, such as "events within 0.25 s of the save message belong to the save".

Yet when the room sends its message, it already knows the file's modification time, size and hash, because its save returned them. The hash is a sha256 of the file's bytes. The room stores the hash in the shared document, where every open view can see it, but puts none of the three in the message.

**What exists and what I propose**

| Message | Sent by | Today | Proposed |
|---|---|---|---|
| session `save` | the room | Path and room id only. Also sent when the room wrote nothing. | Add `last_modified`, `size`, `hash`. Send only after a write. PR to jupyter-collaboration. |
| contents `save` | Jupyter Server | Path only. | Add `last_modified`, `size` (topic 10). PR to jupyter_server. |
| session `initialize`, `clean` | the room | Sent when a room starts, and 60 s after its last view closes. | Nothing to change. Trail starts using them. |

With the proposed fields, the session `save` would look like this (made-up values):

```json
{"schema_id": "https://schema.jupyter.org/jupyter_collaboration/session/v1",
 "level": "INFO", "room": "text:jgis:4029a877-…", "path": "maps/city.jGIS",
 "action": "save", "msg": "Content saved.",
 "last_modified": "2026-10-28T18:02:12.214Z", "size": 4338, "hash": "9f2c…"}
```

**What each gives Trail**

- **Session `save`:** this covers every file open in JupyterLab, including maps, notebooks and csv files. Trail gets the file's modification time and size with `os.stat` and compares them with the message. If they match, the file holds what the room wrote. A comparison then replaces the 0.25 s time limit. The hash is for when Trail reads the file anyway, as "File-specific changes" plans to: it tells Trail that the version it read is the one the room wrote.
- **Contents `save`:** this covers writes through Jupyter Server that have no room, such as uploads. A related request is already open upstream: jupyter_server#1453 asks Jupyter Server to return the hash when it saves. If that lands, the room could stop reading the file back, and each save would cause one fewer `opened`. The room's code has a note waiting for exactly this.
- **Room `initialize` and `clean`:** these tell Trail which files have a room, and so which files Jupyter might write at any moment. Today's plan guesses this: a file counts as open in Jupyter for 2 s after any message about it.

If jupyter-collaboration turns down the first PR, JupyterGIS's emitter could send the hash itself, since every view sees it in the shared document. That would also be a proposal, a change to Matt's code.

> What could we achieve with this? Probably do away with the Writer, and have some data structure that allows for O(1) inserts, so that we always have sorted by `last_modified`, and from that we can determine which events should be folded?

## File-specific changes

Your comment on 10-28-2 asked whether Trail could tell what changed in a map by reading the file
itself, and whether to plan for events specific to a kind of file. Yes to both. This section
sketches the shape. It is the start of a plan of its own, which would follow this one.

Today Trail logs that a tracked file was modified, not what changed in it. For a map, the feed
tells part of the story: layers and the viewport, but not sources, layer order, projection,
annotations or stories, as topic 7 of "What to settle with Matt" describes. A map is json, though,
so Trail can compare each saved version of a tracked map with the previous one and say what the
save changed (illustrative):

```
14. JGISChangeEvent
    timestamp: 14:02:11.204
    src_path: maps/city.jGIS
    changes:
        sources added S2 (data/parks.geojson)
        layerTree reordered
        projection EPSG:3857 → EPSG:4326
```

Should each kind of change get its own class, such as `JGISReorder` or `JGISAddSource`? That would
be one class per part of the map and per verb, about 15 today, and more each time JupyterGIS's
format grows. So the sketch has one record class per kind of file, `JGISChangeEvent`, whose
`changes` lists what one save changed:

```python
# illustrative: what one save of a map changed
changes = [
    dict(
        part="sources",
        verb="added",
        key="S2",
        new=dict(type="GeoJSONSource", parameters=dict(path="data/parks.geojson")),
    ),
    dict(
        part="layerTree",
        verb="reordered",
    ),
    dict(
        part="options",
        verb="updated",
        key="projection",
        old="EPSG:3857",
        new="EPSG:4326",
    ),
]
```

Other kinds of files plug in through one hook in the base. A kind of file says which files it
covers, how to read a saved version, and how to compare two versions. Trail calls it when a file it
covers is saved, and keeps the last version of each such file under `.trail/`, since a comparison
needs the previous one. Maps come first. Later, a csv could report added rows or changed columns,
and a notebook its changed cells. A QGIS project, `.qgz`, is a zip that QGIS writes, so comparing
its versions would need QGIS to read it.

```python
# a sketch of the hook. The names are placeholders for the plan that follows
class FileKind:
    """A kind of file whose saves Trail can describe, such as a JupyterGIS map."""

    # the `.trailtrack` lines of the files the kind covers, such as "*.jgis"
    patterns: ClassVar[tuple[str, ...]] = ()

    def read(self, path: Path) -> object:
        """Returns what Trail keeps of one saved version of the file."""

    def compare(
            self,
            old: object,
            new: object,
    ) -> list[dict[str, Any]]:
        """Returns the changes from one saved version to the next."""
```

How it fits with this plan:

- The save of a map, which this plan folds into a layer record or a run of saves, would also be
  compared with the previous version. The feed says who made a change and when, and the comparison
  says what the file really took.
- A save on opening would show only a new view and, for an old map, a new format. Trail can then
  record it as such, which is the workaround topic 4 asks for.
- Changes made with no message at all would still be described: from Python with no view open,
  by a script, or while Trail was not connected to the server.
- Trail has to keep a copy of each covered file's last version in `.trail/`. A map is a few KB.
- A map can hold credentials, such as OpenEO's `authBearer`, which the copy and the records must
  hide.

> See @notes/10-29-3-file-events-plan.md. File-specific events seem to be out of the scope for now, because they require us tracking a snapshot of each participating file, and determining the difference for the file, which steers us into git file versioning territory.

## The console

Trail's console already prints file events as they are logged. It prints Jupyter records the same
way, and it gains a `jupyter` command for looking into the feed.

The console starts the Jupyter session inside the watchdog session, so that while the Jupyter
session waits for a last save, the file watcher still runs and the save's file events can still be
folded:

```python
# cli/console.py, Run.__call__
        jupyter = trail.jupyter
        jupyter.on_notice = self.notice
        async with trail.watchdog.session:
            async with jupyter.session:
                ...
        self.drain()
```

The header gains `jupyter 1/2`: one server subscribed, out of the two that should be, which are the
live servers whose root overlaps a Dir plus any given to `connect`. It shows `!` while a server
refuses its token, and `pending 3` while three records are being built. A row such as
`jupyter: #2 serves ~/other, outside this project` appears only when a server changes state.

The `jupyter` command:

| Command | What it does |
|---|---|
| `jupyter` or `jupyter servers` | one row per server: number, state, url, root, counts, skips by reason, the JupyterGIS schemas seen |
| `jupyter connect 2` | listen to server #2, for this process |
| `jupyter disconnect 2` | stop listening to server #2, for this process |
| `jupyter messages [terms]` | list the kept messages, with the same terms as `events` |
| `jupyter show 40` | print message 40 as received |
| `jupyter tail on` or `off` | print each message as it arrives, with its outcome |

> Good, and servers are going to have the same repr style as our events/assets, too?

The console also changes in smaller ways:

- New verbs get colors: `added` green, `updated` yellow, `removed` red, `closed` grey, and
  `saved`. A map's `opened` looks like a file's `opened`, and a viewport's `moved` like a file's
  `moved`.
- A record that fails to print shows an error row. Today one such record silently stops the
  console's stream, and a debugger has to survive a bad message.
- If a pasted line holds `token=…`, the console replaces the value with `<redacted>` before the line
  is echoed or kept in the history.
- tornado's own log lines become notices, so they do not print unstyled over the command bar.
- The `jupyter` command lives in src/trail/cli/jupyter.py, since `trail.jupyter` may not import
  anything from `trail.cli`.

## Steps

The three refactors in the first appendix come first.

1. The changes to Trail's core: records with no entry, dotted fields, the class-name guard,
   `.trailignore` and the lock. `.trailignore` also fixes a bug every preset has today: a slow save
   in JupyterLab tracks its `.~` copy. The options you choose in decisions 6, 13, 14 and 15 go here
   too.
   - Tests: a reload with `"entry": null`; `count.modified>3`; a hyphenated key; `entry.path=` read
     against the root; the `.trailignore` tests from "Dotfiles"; a second process does not record
     while the lock is held.
2. The event model: the record classes, the import from trail.py, the console's colors and error
   row, and the copies of the schemas with their tests.
   - Tests: each example payload from JupyterGIS's tests becomes its class; records survive a
     reload; the console rows match the illustration.
3. The consumer records each message at once, with no bursts yet: `trail.jupyter`, its session,
   `Server`, `Servers`, `JSON`, `JSONs`, `Messages`, `receive`, `route`, `resolve`, and `observe`
   for contents renames.
   - Tests: messages of other schemas are counted; each row of the table in "Resolving the map"; a
     subject linked to a map still finds it after another client renames it; a stale path does not
     land on a new file at the old path; a stale path is found through a rename of a folder above
     it; `del trail.jupyter.session` gives a fresh run.
4. Trail finds servers by itself: the runtime files, the liveness check, the overlap rule and the
   notices.
   - Tests: a live file is found and a stale one counted; the overlap table; a removed file stops
     its server; a restart on the same port gives the server its new token; the token appears
     nowhere in reprs, rows, messages or notices.
5. Trail subscribes, and the console gets a first view. The step ends with the end-to-end run
   below, and its feed is saved as a test fixture.
   - Tests, on a fake server: messages reach the consumer; a wrong token is refused once; a drop
     reconnects; a drop with the runtime file gone means `stopped`; a server's schemas are
     learned from its messages.
6. The consumer skips duplicates, reloads and unchanged layers, and notices failed saves.
   - Tests: a copy from another view is dropped; the same change twice from one view is kept; an
     `overwrite`, and a rebuilt room's reload, make later layer messages `reloaded`; a session
     error marks the map's open burst as not saved.
7. Bursts merge messages, `Writes` folds Jupyter's saves, and runs of saves are recorded, tuned on
   the fixture from step 5.
   - Tests: 10 updates become one record with the first `old`, the last `new` and `messages: 10`;
     the events inside a bracket are folded; a script's write just before a bracket is logged; a
     save without a bracket is folded by its save message; one save confirms two open bursts and
     counts once; an `overwrite` closes a burst as not saved; a move during a burst records the
     burst first; a save while quitting is folded; a run of saves closes after 30 s.
8. The `jupyter` command, the token redaction and the help notes.
9. The geospatial preset gains `*.jgis`, `*.qgz` and `*.qgs`. A preset reaches only new projects,
   so existing projects add `*.jgis` to `.trailtrack` themselves.

The end-to-end run in step 5 goes like this:

- Make a scratch folder with the geospatial preset, a few maps from `../eventfeed/examples/` at
  their committed versions, and `../eventfeed/examples/data/`.
- Start `jupyter lab --ServerApp.root_dir=<that folder>` from the eventfeed_dev environment, from
  another folder, so that `.jupyter_ystore.db` lands elsewhere.
- Run `trail` in the scratch folder.
- Follow the manual test steps in events.md's "Using this prototype": open a map, add and remove a
  layer, change a layer's style, move the map and wait 10 s, close the map. Then add the cases from
  2026-10-28: rename a map, Save As, show the map in a notebook and change it from Python, and write
  the map from a script while it is open.
- A short log from this run is also the sample trailfile for Pari.

## Decisions for you

These are the choices the plan leaves to you. Where it recommends an option, the option says so.

1. When does a burst close?
   - (A, recommended) 0.25 s after Jupyter saves its map, or 2 s after its last message if no save
     comes. One record then matches one save, about 1.3 s after the last change.
   - (B) After a short pause, such as 0.5 s, with single messages recorded at once. Records show
     sooner, but lose `count` and the match with saves.
2. What happens to the file events of Jupyter's own saves? This is your question.
   - (A, recommended) Fold the `opened` and `modified` events of a save that a contents `save`
     message announces, chosen by the save's `.~` bracket or within 0.25 s of the message, into the
     record the save belongs to.
   - (B) Log every file event, linked by id to the record that explains it.
   - (C) Log every file event with a tag such as `writer: jupyter`.
   - Suppressing them is not offered, since a wrong match would erase another program's write.
3. How are saves with no layer message recorded?
   - (A, recommended) One record per run of saves of one file, closed after 30 s with no save.
   - (B) One record per save, as in 10-21-2.
   - Should this cover every tracked file Jupyter saves, as the plan recommends, or only maps?
4. Which untracked maps are recorded? The plan records them only inside the project, and skips
   offtrailed maps, maps with no file and paths that do not exist. Is that what you meant by
   recording messages about maps Trail does not track?
5. Which servers does Trail connect to by itself?
   - (A, recommended) Servers whose root overlaps a Dir.
   - (B) Only servers rooted inside a Dir, as in 10-21-2.
6. Does a Trail in a notebook start recording by itself? With the lock, whichever process starts
   first records. A notebook that opens a Trail first would then leave the console unable to
   record, while the notebook stalls on every long cell.
   - (A, recommended) A Trail records only when asked: the console asks, and a notebook calls
     `start()`.
   - (B) Any Trail records as soon as an event loop runs, as today.
7. What becomes of a message of a JupyterGIS schema Trail does not know? Record it as a plain
   `JupyterEvent` (recommended), or skip it with a notice.
8. How are the record classes cut? A leaf class per schema under a class per kind (recommended), or
   one `LayerEvent` for every version.
9. Which WebSocket client does Trail use?
   - (A, recommended) tornado. Trail already installs it through ipykernel. It ignores proxy
     settings, follows no redirects and logs no headers.
   - (B) websockets. It is what JupyterGIS's example listener uses. It needs proxies turned off, its
     logger kept above DEBUG, since it logs the token's header, and version 16.1 or later, so that
     a redirect cannot carry the token elsewhere.
10. When does Trail compare saved versions of a map? This plan only checks that a save followed a
    message.
    - (A, recommended) In the plan that follows this one, as "File-specific changes" sketches.
    - (B) In this plan, as a step after the bursts.
11. How does a library user start the Jupyter session? Explicitly (recommended for now), or by
    itself when an event loop runs, as the watchdog session does. This follows your answer on
    whether a notebook's Trail records by itself.
12. How are empty dicts written? Configure the pre-commit check `check-builtin-literals` with
    `--ignore=dict`, which follows CLAUDE.md, or write `{}` for empty dicts in this repo.
13. What does adding a line do for a path that `.trailignore` ignores? Adding `.env` writes `*.env`
    today, since `.env` reads as an extension. That line matches `.env`, but `.*` in `.trailignore`
    ignores it first.
    - (A, recommended) The command refuses, names the line of `.trailignore` that ignores the path,
      and takes a flag that also writes the `!` line. `git add` refuses an ignored file the same
      way, unless given `-f`.
    - (B) The command writes the `!` line to `.trailignore` itself, and says so.
14. Is a move into a hidden folder a deletion? JupyterLab deletes a file by moving it to the trash.
    On a separate drive, the trash is a hidden folder at the drive's top, which lies inside the
    project when the project is that drive, as with a Docker bind mount. If that trash folder
    already exists, Trail logs a move into it and keeps tracking the file. This was simulated with
    a move, not run on a real drive.
    - (A, recommended) A move to a path that `.trailignore` ignores counts as a deletion. Its `.*`
      line covers the trash folder.
    - (B) Keep logging a move, as today.
15. Where does the code that `.trailtrack` and `.trailignore` share live? Both use `parse`,
    `canonical` and the `TXT` file wrapper, which live in markers.py today.
    - (A, recommended) A shared module, such as lines.py. to_track.py keeps `ToTrack` and `Scopes`,
      and to_ignore.py holds `ToIgnore`.
    - (B) to_ignore.py imports them from to_track.py.

## Risks and what this plan leaves out

These risks remain once the plan lands, most of them until the upstream changes described above
land:

- Until messages carry `local`, Trail's reload check works by time. A person's edit within 2 s of a
  reload is dropped, and maps in an old format, whose ids change at each load, look changed.
- After a server restart, a view can reconnect, and its room can reload, before Trail has found the
  new runtime file and reconnected. The reload's messages then arrive without the session messages
  that mark them, and are recorded as a person's edits. This timing was not measured.
- Copies from other views arrived within 40 ms in every run, all on one machine. Two people who
  make the same change within a second are merged into one.
- A burst merges messages in the order they arrive, which can differ from the order they were sent.
- A pending autosave can write the room's old content over another program's write. Trail logs it
  as an ordinary save.
- Links from views to maps are lost when Trail stops. A stale view's messages are then skipped as
  `file missing`, unless Trail saw the rename.
- `Server.locate` joins `document_path` with the root in the runtime file. That is the wrong folder
  for a map on an extra drive, for a server set to serve files from another folder than the root its
  runtime file names, and on a case-insensitive macOS drive when a path's case differs. Trail then
  records the message on another file, or skips it.
- One file can have two rooms, for example when a map is also opened in the text editor. Each saves
  the file, and the editor's saves have no layer messages.
- A large upload reaches Trail before it is complete, so its AddAssetEvent records a partial size.

This plan leaves out the following:

- servers on unix sockets;
- adding a server by hand from the console;
- file-specific changes, which "File-specific changes" sketches as the plan to follow;
- linking the `created`, `moved` and `deleted` events of Jupyter's own file operations to the
  contents messages that announce them;
- marking a new map as a probable Save As of another, from the timing of `closed` and `opened`;
- remembering connections and links across Trail processes;
- catching up on messages sent while Trail was not running. Jupyter Server can write every event
  to a log file, but only when it is configured to;
- messages about kernels, and the notebook-link/metrics events.

## What to settle with Matt (now that you're all caught up)

The same topics as "What to settle with Matt", in the same order, without the background. Each says
what Trail does in the meantime, what would fix it upstream, and what to ask.

1. The event feed. It lives in draft PR #4. Matt can make changes in JupyterGIS, and we can
   implement them and open upstream PRs ourselves, including during his November absence.
   Prioritize `local`, `document_id`, and `seq` with `time`.

2. Every view reports every change. Until messages carry `local`, Trail drops copies within 1 s,
   skips layer messages while a map reloads, and skips updates whose old and new layers are equal.
   These checks leak. A view that reconnects after a restart can send its reload before Trail has
   reconnected to see the room's messages, old-format maps change their ids at each load, and a real
   edit right after a reload is dropped. The fix, your idea: `_layersObserver` in doc.ts passes on
   Yjs's `transaction.local`, each layer message carries it as `local`, and Trail keeps the local
   message and decides what to do with the rest. Ask: should Python changes be in the feed at all?

3. A layer message does not mean the file changed. Trail marks a burst as saved only when a contents
   `save` message for the map arrives, never on the session `save`. A session `ERROR` marks the
   map's open bursts as not saved, and a burst with no save is recorded with `saved: null`. Ask:
   should events.md say that a layer message is not a save, and does JupyterGIS want to show failed
   saves to the user?

4. Opening a map rewrites it. Trail works around it. It folds the save that follows an `opened`
   into the `opened` record, and a save after a second view, a rename or a reconnect becomes a run
   of saves. Comparing saved versions would show that such a save changed only the view and the
   format. The random ids of the 0.5 to 0.6 migration are a JupyterGIS bug that a small PR fixes.
   Ask: would he take that PR?

5. Messages cannot name a map reliably. Trail finds a map by its view first, then by its path, then
   by renames the server reported. Links are lost when Trail stops. Upstream, each message could
   carry `document_id`, the room's name, and the map's current path from the shared document. Save
   As could send a message that links the copy to its source. The Python API could resolve paths
   from the notebook, refuse a missing file, and send null for an unsaved map. Ask: which of these
   can land?

6. No time, order or sequence number. Trail stamps messages on arrival and merges a burst in arrival
   order. Upstream, `seq` per view and `time` in each payload are an additive change that already
   passes Jupyter Server's checks, and the emitter could send each view's messages in order and
   retry. Ask: can `seq` and `time` land in v1?

7. Edits that send no message. Trail records their saves as runs of saves, and comparing saved
   versions would say what changed. Upstream, source messages would add who changed a source.
   Ask: are source messages on his list?

8. `opened`, `closed`, and who did it. Trail keeps a view's link after `closed`, and treats the
   subject as a view, not a person. The fix is in the emitter: send `closed` when the last view of
   a map closes, and when the page closes, which Matt or we can do in plugin.ts. Ask: does that
   suit him, and is naming the user in scope?

9. Large messages. Trail raises tornado's 10 MiB message limit for the feed. Upstream, a `layer/v2`
   could send only the changed paths, and metadata in place of embedded images. Ask: would that
   suit a v2?

10. Smaller points. Trail no longer probes, so a route that lists a server's JupyterGIS schemas is
    a nice-to-have. Ask for `last_modified` and `size` in the contents `save` message, a small
    jupyter_server change that could go with #1710. Trail supports QGIS projects too, so ask
    whether the round trip through QGIS loses anything. Ask for a line in events.md that says a
    payload may not hold `schema_id`, and for fixes to the two stale lines in events.md and
    layer_v1.yaml.

11. Bugs to report upstream. JupyterGIS's migration draws new random ids at each load of an old
    map. jupyter_server_fileid loses track of files when their folder is
    renamed. JupyterLab leaves views on old paths after renaming a folder whose name has `(`.
    jupyter-collaboration lets a tab and a notebook view share one connection, does not follow moves
    made outside Jupyter, drops the last edit at shutdown, misses writes that keep the modification
    time, can save an old snapshot over a reload, says "Content saved." when nothing was written,
    and puts its store in the server's starting folder. `LocalFileIdManager` works around the
    rename and move bugs. Ask: will Matt file these, or should we?

## Appendix: three refactors that come first

This appendix plans three refactors. The first two carry over from 10-21-2, which planned them
from your notes of 2026-10-27. The third comes from your comment on 10-28-1. None is about Jupyter,
and all three land before this plan, on a branch `ontrail` off `hodczak`. The plan above still uses
the earlier names `.trailtrack`, `ToTrack` and `.trailignore`. The names in this appendix, settled
in its decisions, replace them. The second refactor is what your `# no:` comment at asset.py:491
refers to.

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
untracked paths and `.trail`. The untracked paths are those removed with `offtrail`, which the
second refactor renames `untrack`. The scan does not pick them up again.

| Today | After | Used by |
|---|---|---|
| `trail._is_ignored(path)` | `trail.entries.is_metadata(path)` | 10 calls in entry.py, trail.py, asset.py, event.py, session.py and watchdog.py |
| `trail._offtrailed_paths` | `trail.entries.untracked` | written by `AddAssetEvent.apply` and `RemoveAssetEvent.apply`; read by the scan, the walk and `WatchdogEvent.apply` |
| `Assets._is_markable(path)` | `trail.entries.is_trackable(path)` | `Assets.scan` |
| `Assets._descends(path)` | `trail.entries.is_descending(path)` | `walk`, through `Assets.scan` |

`is_trackable` and `is_descending` follow your `# no:` comments at asset.py:491 and asset.py:526.
The comment at asset.py:491 asked for `_is_checked`, after the checklist. With `.ontrail`, the name
becomes `is_trackable`. The line in `Assets.scan` that passes `is_descending` to `walk` gets its
intermediate variable, as the `# no:` comment at asset.py:406 asks. This step addresses those three
comments. `is_trackable` becomes a public name. Its docstring says that it is False for a path
`.ontrail` takes but that Trail already tracks or that is in `untracked`. `Dirs.tracks` still
answers whether `.ontrail` alone decides a path.

`is_ignored` becomes `is_metadata`, so it does not read as the check of `.offtrail`. `.offtrail`,
planned as `.trailignore` in "Changes to Trail's core", comes later. Its rules beat those of
`.ontrail`. It is consulted by `is_trackable` and `is_descending`, not by `Dirs.tracks`.

Some helpers stay where they are:

- `Assets.holder` and `Assets.is_child` read only the assets and the dirasset suffixes.
  `is_trackable` calls them.
- `Assets._reach` and `Dirs.match` belong to the walk and to `.ontrail`.
- `Trail.track`, `offtrail` (renamed `untrack` in the second refactor), `_track`, `_absorb` and
  `_moved` change entries and write the log. `trail.entries` only looks things up and answers
  questions, so everything that writes the log stays on `Trail`.

`trail.entries` reaches the collections through `self._trail`, as `Dirs` does. Only trail.py
imports the new module. The other modules reach `trail.entries` at run time through the Trail, so
no import cycle appears.

### Renaming markers to `ontrail`

The `.markers` file becomes `.ontrail`, and `Markers` becomes `OnTrail`, reached as
`trail.ontrail`. `Trail.offtrail` becomes `Trail.untrack`, with the same behaviour. That frees
"offtrail" for `.offtrail` and `OffTrail`, at `trail.offtrail`, which hold the rules that are always
ignored. They come later, since they change behaviour. The names change as follows.

| Today | After |
|---|---|
| the file `.markers` | `.ontrail` |
| `trail/markers.py` | `trail/ontrail.py` |
| the presets in `trail/markers/`, `MARKERS`, `DEFAULT_MARKERS` | `trail/presets/`, `PRESETS`, `DEFAULT_PRESET` |
| `Markers`, `trail.markers` | `OnTrail`, `trail.ontrail` |
| `Trail(dir, markers='default')` | `Trail(dir, ontrail='default')` |
| `Trail.mark`, `Trail.unmark` | `trail.ontrail.add`, `trail.ontrail.remove` |
| `Trail.offtrail`, and `Entry.offtrail`, which removes an asset or a Dir from its collection | `Trail.untrack`, `Entry.untrack` |
| the console's `mark` and `unmark` | `ontrail add` and `ontrail remove` |
| the console's `markers` | `ontrail` |
| the console's `offtrail` | `untrack` |
| `MarkCommand`, `UnmarkCommand`, `MarkersCommand` | `OnTrailCommand` |
| `OfftrailCommand` | `UntrackCommand` |
| `offtrailed`, the console's word for a RemoveAssetEvent, and its theme class `event.offtrailed` | `untracked`, `event.untracked` |
| `TXT.markers`, `TXT.line2marker` | `TXT.rules`, `TXT.line2rule` |
| `cli/marker_completion.py`, `MarkerCompleter`, `MarkerPaths` | `cli/rule_completion.py`, `RuleCompleter`, `RulePaths` |
| `tests/test_markers.py`, `TestMarkers`, and `TestMarking` in test_listing.py | `tests/test_ontrail.py`, `TestOnTrail`, `TestOnTrailCommand` |
| `marked`, `already marked`, `not marked` and `unmarked` in the feed | `added to .ontrail`, `already in .ontrail`, `not in .ontrail` and `removed from .ontrail` |
| `Not a marker: '!'` | `Not a rule: '!'` |
| "marker set" in the init walkthrough | "preset" |

`trail.ontrail.add` and `trail.ontrail.remove` do what `Trail.mark` and `Trail.unmark` do today,
with the same `cwd` argument. `add` makes each text canonical, writes its line and tracks what the
line picks up now. `remove` removes the lines and warns about the assets that stay tracked, which
`untrack` removes. They absorb `Markers.add` and `Markers.discard`, which edit lines as written and
which only `mark`, `unmark` and two tests call. `replace` stays. `trail.offtrail.add` and
`trail.offtrail.remove` will work the same way. In the console, `ontrail` alone lists the rules.

The feed names the file, so `.offtrail` can use the same words. Decision 4 explains "rule". The
completer classes are named after one line, since the same completion can serve both files. Where
"marker" names one line in prose, the plan writes "line", as most docstrings already do.

Some names stay. Dirs, root scopes, dir scopes and the kinds that the `ontrail` command prints keep
their names. So do `Dirs.tracks` and `Dirs.match`. The log does not change, since no field in it
names a marker or offtrailing. `AddAssetEvent.rule` and `AddDirEvent.rule` still hold the line that
picked the entry up. The notes in `notes/` keep the old words, since they record what was true when
they were written.

"Mark" also appears where it means something else. `pytest.mark` in test_accesses.py and
test_trail.py, the `marker` separator in session.py:477, "mark it for a restart" in console.py:198
and the tutorial's `marker` variable all stay. So the rename goes identifier by identifier, not by
find and replace. It covers about 570 occurrences in 25 files, plus the preset directory and the
package data in pyproject.toml. `offtrail` adds about 110 occurrences in 17 files, test names
included.

The rename also edits the console's help docstring under the `# no:` comment at console.py:130,
which asks for bullet points for track, offtrail, mark and listings. The bullets name `track`,
`untrack`, `ontrail` and the listings. This step addresses that comment.

There is no migration. A project made before the rename has a `.markers` file and no `.ontrail`. If
Trail opens it, Trail reads an empty `.ontrail`, logs a RemoveDirEvent for every Dir and watches
nothing. Rename the file first, with `mv .markers .ontrail`.

### Renaming `Session` to `WatchdogSession`

Your comment on 10-28-1 asked whether the watchdog's `Session` should become `WatchdogSession`,
now that `JupyterSession` exists. It should: the two classes then name what they run, and the plan
can say "the watchdog session" and "the Jupyter session" without ambiguity. The class keeps its
module, session.py, and its place as `trail.watchdog.session`. The rename touches 15 uses in 4
files: session.py, watchdog.py, tests/conftest.py and tests/test_accesses.py.

### Order

`trail.entries` goes first, so `_is_markable` moves once and the rename does not touch it. The
`WatchdogSession` rename is independent and can go anywhere.

1. `trail.entries`. The base class becomes `EntryCollection`. The new `Entries` takes the lookups
   and the four helpers above, and `Trail._entry` goes. Nothing a user types changes.
   - Tests: a lookup by id and by path for each kind; KeyError and `get` for a key that is neither;
     `in` with an entry; Dirs before assets when iterating; a path removed with `offtrail` before
     a reload is still in `untracked` after it; `is_metadata` for `.trail` and for a file inside
     it. The existing scan and walk tests cover `is_trackable` and `is_descending`.
2. The `ontrail` rename, with `offtrail` becoming `untrack`, across the library, the CLI, the
   presets and the tests. No behaviour changes, so the existing suite, renamed, is its test.
3. The `WatchdogSession` rename. The existing suite is its test.

### Decisions for these refactors

These are the choices the refactors leave to you.

1. What is the new class called? Your comment suggested `EntriesMapping`, keeping `Entries` for the
   base of `Assets` and `Dirs`. This appendix renames the base to `EntryCollection` and gives the
   new class the plain name `Entries`, since it does not iterate keys as a `Mapping` does, as the
   next decision explains. Keep your name, or take this one?
> Yes, entrycollection
2. What does iteration yield? Your comment asks for `trail.entries` to behave like a mapping. A
   `collections.abc.Mapping` iterates its keys, but `trail.assets` and `trail.dirs` iterate their
   entries.
   - (A, recommended) `for entry in trail.entries` yields entries, as the collections do, with
     `[]`, `get` and `in` as above.
   - (B) Subclass `Mapping[str, Entry]`, keyed by id. `keys`, `values` and `items` come for free,
     but iterating `trail.entries` yields ids while iterating `trail.assets` yields assets.
 > a
3. Are the stale docstring examples updated? About 14 examples go stale with the rename: the
   `Markers (3)` reprs in trail.py, markers.py and fmt.py, `self.mark('zarr')`,
   `self.unmark('.zarr')` with its warning, `trail.unmark('raw/')` in event.py, the `.markers` path
   in the `TXT` repr, and the console transcripts in commands.py and marker_completion.py. Only
   names change in them. Your rule is to report stale examples, not to fix them. Should the rename
   step update them? I recommend yes.
> Yes. Also, we want `self.to_track()`, `self.to_untrack()`, `self.to_ignore()`, and `self.to_unignore()`

4. What is one line called in names? Prose says "line". The table above uses "rule", since
   `AddAssetEvent.rule` already holds the line that picked an entry up: `TXT.rules`,
   `TXT.line2rule`, `RuleCompleter` and `Not a rule: '!'`. "Pattern" would clash with
   `Scopes.patterns`, which holds the patterns compiled from the lines.
> It should just be "rule", right? What is "line2rule" for; mapping a line number to the string of text? Maybe "num2line"? Or maybe keep "line" as meaning "line number" and then "string" for the raw glob pattern text?

5. Which commands edit `.trailtrack` and `.trailignore`? `track` is taken: the console's `track`
   and `Trail.track` track paths now, as assets. They cannot merge with `mark`. A folder given to
   `track` becomes one asset, while a folder given to `mark` gets a line that watches what is in
   it.
   - (A, recommended) `track` and `Trail.track` become `add` and `Trail.add`. `mark` and `unmark`
     become `track` and `untrack`, so `.trailtrack` and its commands share a name. `.trailignore`
     gets `ignore` and `unignore`.
   - (B) `check` and `uncheck` for `.trailtrack`, as this plan had before `.trailtrack`, with
     `ignore` and `unignore` for `.trailignore`. `track` keeps its meaning.
> check and uncheck are just more terminology to learn. Let's use the to_ prefix for implicit tracking/ignoring through the .trailtrack and .trailignore files. track() and ignore() are then explicit specific-asset commands. Oh and we still have to respect the `offtrail` decision, so use `offtrail` where appropriat
> Actually, I have another idea, as the to_track() versus track() might be confusing. Instead of "markers" or "checklist"  or "trailtrack," let's use "ontrail" for the glob patterns that will be tracked, and "offtrail" for patterns that will be always ignored. So offtrail is prioritized over ontrail. Our current .track() stays, but .offtrail() becomes .untrack().

> Is anything here still unclear before the first refactor? Ignore the `to_` names. Use `.ontrail` and `.offtrail`, each edited with `add` and `remove`, and `untrack` does exactly what `offtrail` does today.

> **Claude:** Nothing is unclear now, and the appendix above now uses these names:
>
> - Decision 1: the base of `Assets` and `Dirs` becomes `EntryCollection`, and the new class is `Entries`, at `trail.entries`.
> - Decision 2: (A), iterating `trail.entries` yields entries.
> - Decision 3: the rename updates the stale docstring examples. There are no `to_` names.
> - Decision 4: a line is a line of the file as written, a rule is what a line that is not blank or a comment says, and a number is a line number. `line2rule` turns a line into its rule, or None, and `numbered()` yields `(number, rule)`, so `TXT.rules`, `TXT.line2rule` and `RuleCompleter` stay.
> - Decision 5: `.markers` becomes `.ontrail`, with `OnTrail` at `trail.ontrail`, in `trail/ontrail.py`. The planned `.trailignore` becomes `.offtrail`, with `OffTrail` at `trail.offtrail`, and an offtrail rule beats an ontrail rule. `trail.ontrail.add` and `trail.ontrail.remove` replace `mark` and `unmark`, and `trail.offtrail.add` and `trail.offtrail.remove` come with `.offtrail`. The console gets `ontrail add` and `ontrail remove`, and `ontrail` alone lists the rules. `track` stays, and `offtrail` becomes `untrack`, with the same behaviour.
> - `trail._offtrailed_paths` becomes `trail.entries.untracked`, not `trail.entries.offtrailed`, since "offtrail" now names the ignore file.
> - The refactors go on a branch `ontrail` off `hodczak`, after the pending source changes are committed.
> - Still open, for the `.offtrail` step: whether an explicit `track(path)` beats an offtrail rule. Today, `track` on a path that `offtrail` removed takes it off the remembered paths.

## Appendix: your comments

### On 10-28-1

One line per comment, by its line in 10-28-1, with what this plan does.

- 122, what "local" means, and whether the message could carry the flag so that Trail filters:
  topic 2 of "What to settle with Matt" explains it with two pages. Your reading is right, and your
  idea is now the proposed feed change: a `local` field in each layer message, with Trail deciding.
- 146, not knowing what a room is: "What to settle with Matt" now gives the background of each topic
  first, including rooms, views, autosave and file ids.
- 835, the names in the dotfile rule: `.trailignore` replaces the built-in rule, as "Dotfiles"
  explains, so `is_dotfile_pattern`, `hidden_positions` and `dotfile_spec` are not needed.
  `Scopes.decide` keeps its name, since it returns a line, not a path. And yes, dotfiles are
  ignored unless a line includes them, through the `.*` line of `.trailignore`.
- 892, renaming `Session` to `WatchdogSession`: yes, as the third refactor in the first appendix,
  15 uses in 4 files.
- 998, whether the `Envelope` mixin is a remnant: it was, and it is gone. Terms reach the payload
  as `data.verb`, and only `LayerEvent` and `ViewportEvent` keep a few typed properties.
- 1857, `Consumer` at `trail.jupyter.session.messages.consumer`: the class is now `Consumer`, at
  `trail.jupyter.session.consumer`. `Messages` is not a queue the consumer empties: it keeps every
  message afterward, for the console.

### On 10-21-2

One line per comment, by its line in 10-21-2, with what this plan does.

- 102, rename `folded` to `count`: every record uses `count`.
- 203, `trail.entries` in place of `_entry`: `Event.from_record` uses `trail.entries[...]`, and
  `JSONL.read` uses `trail.entries.get(...)`. The first appendix plans the class, and its name is
  the first decision there.
- 258, a docstring for the dotted fields: `Select.held` has one, in "Dotted fields in selections".
- 273, an example of hyphens: `data.object.new.parameters.color.circle-fill-color=#dc8add`, with the
  caveat that maps in JupyterGIS's current format have no hyphenated keys.
- 294, `is_same`: the condition sits inside `if not (...)`.
- 318, the `.~` check as a code smell: replaced by `.trailignore`, whose `.*` line ignores the
  copies. Your `[!.]` idea works for one line, but has to be on every line, so `.trailignore` does
  it once.
- 403-409, the `parameters` hints and flattening: settled on 10-27. `data` stays nested.
- 536, `JupyterEvent` as the only base class: `JupyterGISEvent` is gone.
- 768, `count` and `nest`: settled on 10-27.
- 807, nested TypedDicts: dropped, as you agreed. A few typed properties remain.
- 901, the session split: kept, as you agreed.
- 1134, the `Server` docstring: each attribute has its own comment above it, in every class the
  plan adds, and class docstrings only say what the class is for.
- 1135, `backoff` and `ceiling` sharing one comment: one comment each.
- 1138, which servers to watch: servers whose root overlaps a Dir. Watching every server finds
  nothing more.
- 1151, `Server` used before it was introduced: every class now gets a short description or example
  before the plan uses it.
- 1203, `jupyter.jsons`: `trail.jupyter.jsons`, a mapping from each runtime file's path to its
  `JSON`, read again only when a file's modification time changes.
- 1236 and 1257, `Info` and `JSON`: settled. `JSON` stays.
- 1362, what tornado is: explained in "Subscription".
- 1477, docstrings for `receive` and `route`: added, and `observe` has one too.
- 1520, `Target`: renamed `Map`.
