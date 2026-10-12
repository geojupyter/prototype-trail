# Dirasset accesses: plan

## Summary

A dirasset is a directory that is one dataset, such as a `.zarr` store, a `.gdb` geodatabase or a
`.safe` Sentinel product. Trail should track it as one asset and never track the files inside it.
This plan says how Trail watches a dirasset and logs who used it and when, without logging its
thousands of chunk files.

- **What gets logged.** One `DirectoryAccessEvent` per access. An access is a stretch of activity
  on a dirasset that ends after 30 s with no events. It records `started`, `timestamp` (the last
  event), a count per event type, and whether it was a read (`opened`) or a write (`modified`).
  Single chunks are never named.
- **How it is watched.** watchdog on every platform, with one recursive watch per Dir and per
  dirasset. Today's one watch per folder runs out of inotify instances after 867 folders, and it
  missed up to 100% of writes into new chunk folders. A recursive watch saw 94-100% of them.
- **What belongs to a dirasset.** Each format gets a regex, `DirAsset.children`, for the file names
  that are part of it. Temp files, lock files and GDAL sidecars do not match it, so a read that
  writes a lock file is still logged as a read.
- **Classes.** `DirAsset` subclasses `Asset` only, not `Dir`. Open accesses live in
  `trail.watchdog.session.accesses`, and `dirasset.access` returns the live one.
- **Fixes along the way.** The watchdog session ends cleanly, so a second `asyncio.run` no longer
  logs events twice. A scan no longer tracks chunk files when a folder inside a store is marked.
- **Known limits.** Reads are only seen on Linux. Counts are approximate, because inotify drops
  events under heavy writes without telling anyone. A kernel killed by an OOM loses its open
  access. A store moved somewhere unwatched is logged as deleted.
- **Deferred.** A Trail-owned inotify reader, a journal of open accesses, and which arrays or
  tables an access touched.
- **Delivery.** Twelve commits on one branch (§9), each leaving the tests passing. The status
  list below tracks them, and the whole series is reviewed at once at the tip.

The rest of the document is the detailed record: answers to review comments (§2), changes from the
earlier 10-7 design (§3), measurements (§4), the design (§5-§8) and tests, invariants and limits
(§10-§16).

## Background

This plan replaces `notes/10-8-directory-asset-sessions-plan.md` and takes in every comment left on
it. It describes the code at `9fe304d Flatten nested expressions into named intermediates` and
builds on `notes/10-7-directory-asset-events.md` and `notes/directory-assets.md`. §2 answers each
comment, §3 lists the decisions that change 10-7, and the rest is the plan itself.

Three choices from the comments shape all of it:

- **watchdog only.** A Trail-owned inotify reader is deferred, andsupport for macOS and Windows
  comes before optimizing Linux. One design runs on every platform.
- **Recursive watches.** Every Dir and every dirasset gets one recursive watchdog watch, and a
  directory already inside a recursive watch gets none of its own.
- **Few new names.** An access is an `Access`, the watchdog's run is its `session`, and a dirasset
  is a `DirAsset` whose `children` pattern names the files that belong to it.

**This is a series of one-feature commits.** §9 gives the order. Each one leaves the suite passing,
as `notes/9-30.md` asks.

The measurements cited here were run on 2026-10-08 on this machine: Linux 6.12, Python 3.12.14,
watchdog 6.0.0, tmpfs with a warm cache, zarr-python 2.18.7, 3.1.2 and 3.4.0, and GDAL 3.9 to 3.13.
They are marked **measured**. A claim reasoned from code or documentation is marked **assumed**,
and §14 lists what still has to be checked.

## Status

- [ ] Commit 0: hygiene (prerequisite)
- [ ] Commit 1: `*.safe` in the geospatial preset
- [ ] Commit 2: gap 1, marking one dirasset
- [ ] Commit 3: `DirAsset` and `children`
- [ ] Commit 4: gap 4, what belongs to a dirasset
- [ ] Commit 5: `Watchdog.session` and the hand-off
- [ ] Commit 6: recursive watches, and gap 2
- [ ] Commit 7: `DirectoryAccessEvent`
- [ ] Commit 8: accesses
- [ ] Commit 9: dirasset lifecycle and moves
- [ ] Commit 10: console
- [ ] Commit 11 (optional): `select` on counts

## 1. Vocabulary

| Term | Meaning | Lives at |
|---|---|---|
| dirasset | a directory tracked as one dataset: a zarr store, a file geodatabase, a Sentinel-2 product, or any directory tracked with `track DIR` | `DirAsset`, in `trail.assets` |
| children | the pattern of file names that are part of a dirasset. A file inside a dirasset whose name does not match it, such as a temporary or lock file, is not part of it | `DirAsset.children` |
| access | one stretch of activity on a dirasset, from its first event until it goes quiet for `idle_gap`. Concurrent readers and writers share it | `Access`, open in `trail.watchdog.session.accesses`, logged as a `DirectoryAccessEvent` |
| session | one stretch of watching, from `Watchdog.start` to `Watchdog.stop`. It owns the observer, its watches, the queue, the open accesses and the consumer | `trail.watchdog.session` |
| seal | close an open access and queue it, so the loop logs it once | `Accesses.seal` |

## 2. Answers to the comments on the 10-8 plan

### 2.1. Does watchdog fail past 867 directories, and should Trail use inotify?

The limit is the kernel's cap on inotify instances per user (`fs.inotify.max_user_instances`,
1,024 here and 128 on Debian and Ubuntu), shared with every other program. watchdog spends one
instance and two threads on each `schedule()` call. 867 is what was left here after other programs
took 149. So watchdog itself can watch a large tree. What runs out is one watch per folder, which
is what Trail does for the folders of a recursive Dir today (`watchdog.py:186-200`, `:218-230`).
inotify is deferred, as you decided (§16). Recursive watches (§5.1) remove the per-folder cost.

### 2.2. Should every watch be recursive, skipping one whose parent is already watched recursively?

Yes for every Dir and every dirasset, and yes to the check. A directory inside a recursive watch
gets no watch of its own, since that watch already sees it, and two watches on one directory would
report each event twice.

One kind of watch has to stay non-recursive: the parent of a Dir or of an asset. It is watched only
to see the Dir or the asset itself renamed or deleted. The parent of the project's root Dir is a
folder such as `~/projects` or `/tmp`, and a recursive watch there would watch every sibling
project. A parent that already sits inside a recursive watch gets no watch at all.

On Linux a recursive watch has two costs that non-recursive watches avoided:

- **It cannot prune.** watchdog adds one kernel watch per directory, inside `.git`, `.venv` and
  `node_modules` too, and its events arrive at Trail. The handler drops them, by the same rule
  `Assets._descends` uses for walks (§5.2). They still cost watch descriptors and CPU time in
  watchdog's threads.
- **It walks the tree when it is placed,** on the calling thread at 7-20 µs per directory
  (measured). A project whose `.venv` holds 10⁴ directories blocks the loop for 0.1-0.2 s when the
  session starts (assumed size, V1).

macOS FSEvents and Windows `ReadDirectoryChangesW` are recursive natively, so neither cost applies
there (assumed, V2).

### 2.3. Why `Inbox`, `Shift`, `Tally` and `Profile`?

They are gone. An access is an `Access` and the watchdog's run is `trail.watchdog.session`, as you
proposed. Open accesses live in `trail.watchdog.session.accesses`, a `UserDict[str, Access]` that
holds the lock. The per-format data shrinks to one pattern per suffix, `DirAsset.children` (§5.3),
so no `Profile` remains.

### 2.4. Should `asset.access` be live instead of a snapshot?

Yes. The snapshot existed because the live object had a `members` dict that the counting thread
could grow while you iterated it. Members are gone (§2.6), and `Access.count` starts with all five
event types at 0, so its keys never change and reading it while it counts is safe.
`dirasset.access` returns `session.accesses[dirasset.id]`, and makes an empty access if none is
open, as you proposed. An empty access is never logged. A sealed access stops changing, so read
`dirasset.access` again for the current one.

### 2.5. Is the thread lock necessary?

You agreed to keep it. It is the session's lock, a `threading.RLock`, held by `Accesses` and by the
queue for a few dict and deque operations only. It is reentrant because `Accesses.__missing__`
takes it, and `count` already holds it when it reads a missing key.

### 2.6. What are members for, and what about thousands of chunks?

Members are dropped. An access keeps only its counts by event type, so no record ever names a chunk
or an array. The question "which parts of the dataset changed" can come back later, for example
for shapefiles, under its own design (§16).

### 2.7. Why `ops`, and why make counts `Counter`s again after a replay?

`ops` becomes `count: dict[str, int]`, read as `event.count["modified"]`. It is a plain dict, so a
replay reads it back unchanged and no conversion is needed. The conversion was never about the lock.

### 2.8. Why `moments`?

`to_record` converts only `timestamp` to text today (`event.py:158`), so a second datetime field,
`started`, makes `json.dumps` raise inside the consumer. `moments` named those fields. It goes away:
`to_record` converts any `datetime` value, and `DirectoryAccessEvent.__post_init__` parses `started`
back when a replay passes it as text.

### 2.9. Is `longest_session` necessary? An OOM stops the events, so the idle gap closes the access.

Only if Trail lives in the process that dies. In a notebook Trail runs inside the kernel, so an
OOM kill takes Trail with it. No timer fires, and the open access, which exists only in memory, is
lost with everything else. When Trail runs in another process, such as the console, the events stop
and the idle gap closes the access with nothing lost, as you say.

The cap only bounded that loss. It is dropped. A killed kernel loses the access it had open, which
§15 lists. If that matters later, a journal of open accesses is the real fix (§16).

### 2.10. Is `idle_gap` a timeout between events, and what of 20 minutes of reads 5 s apart?

`idle_gap` is the timeout between events. It becomes 30 s instead of 1.5 s, so pauses between
chunks of compute-bound work do not split an access. 20 minutes of activity with 5 s gaps is one
access. Two separate accesses less than 30 s apart become one, which still says truthfully that the
dirasset was in use from `started` to `timestamp`. V5 settles the default.

### 2.11. Could the session be a descriptor that builds itself on get and cleans up on delete?

The deleter, yes: `del watchdog.session` stops the session's threads and drops it, synchronously,
whether or not its loop still runs. The getter does not build a session. A session needs a running
asyncio loop and starts threads, and reading an attribute should not start threads as a side
effect. `Watchdog.__repr__`, `running` and the tests read it while the watchdog is dormant.
`ensure()` and `start()` build it. `stop()` stays async, because draining what watchdog has already
read needs awaiting, and it ends with `del self.session`.

### 2.12. Can `session.watches` schedule in `__setitem__` and unschedule in `__delitem__`, instead of `cover`?

Yes. `watches` is a `UserDict[Path, bool]` that maps each directory to whether its watch is
recursive. `watches[path] = True` schedules a recursive watch, and `del watches[path]` unschedules
it. `cover` goes away, and so does `uncover`. `reconcile()` sets and deletes entries to match
`desired()`, and `Trail._track` calls `reconcile()` for a new dirasset, which is gap 2.

### 2.13. Transient names, or a pattern of the names that belong?

The pattern of the names that belong, as you proposed: `DirAsset.children`. A file that matches it
is part of the dirasset and is counted. Anything else inside is not, and §5.4 says what happens to
it.

One cost to know about: when a format adds a kind of file the pattern does not know, writes to it
go uncounted. Chunk writes, which are the bulk of any write, are still seen, so the access is still
logged as `modified`.

### 2.14. `children: str` or `set[str | None]`?

One `str`, a regular expression matched against the whole file name with case ignored. Suffixes are
not enough, which is where a set would fail:

- zarr v3 chunks have no suffix (`temp/c/0/0/0`).
- zarr v2 chunks are named `0.0.0`, and `Path('0.0.0').suffix` is `.0`.
- gdb index files have two dots (`a0000000a.FDO_OBJECTID.atx`).
- GDAL's sidecar `B04.jp2.aux.xml` ends in `.xml`, like the product's real metadata.

Each of these was checked against the patterns in §5.3.

### 2.15. Is gap 4 unnecessary once every directory is watched recursively?

Mostly. A file inside a dirasset whose name matches `children` belongs to the dirasset and is never
an asset of its own. A file whose name does not match is an ordinary file, checked against
`.markers` like any other, as you proposed. Three refusals remain:

- **Tracking a child path.** `track cube.zarr/zarr.json` is refused, since its events count toward
  the dirasset.
- **Tracking a directory that holds `.trail`.** Every log append would count toward the dirasset,
  and each logged access is itself an append, so it would feed itself.
- **Assets already tracked inside a new dirasset whose names match its children** are offtrailed,
  with a warning, before the dirasset's `AddAssetEvent`.

### 2.16. "Store", "container"?

Gone from the prose. Things like `.zarr` are dirassets, and the class is `DirAsset`. In `util.py`,
`CONTAINERS` and `container()` become `CHILDREN` and `is_dirasset()`.

### 2.17. Gap 1: append `/` only when the directory has no extension?

Close to that. A directory gets its `/` unless its suffix is a dirasset's (`.zarr`, `.gdb`, `.safe`,
`.n5`). "No extension" would also take the `/` away from ordinary folders whose names hold a dot,
such as `results.v2`, `python3.12` or `2026.10`. That would turn their lines into scopes that track
nothing. If you prefer the plain rule anyway, it is a one-line change.

### 2.18. Moves with watchdog only?

§6.2 covers each case. A move watchdog sees in one instance arrives as one `DirMovedEvent`, which
keeps the dirasset's id. That includes any move inside one recursive Dir watch. A move between two
watches arrives as a creation at the destination and, 0.5 s later, a deletion at the source.
Trail matches the creation to the dirasset by the root's inode, so the id is kept there too. A move
to a directory nothing watches is a deletion, until inotify is revisited.

### 2.19. Fingerprint?

Dropped, as you said. `Asset.fingerprint` stays as it is.

### 2.20. Should `DirAsset` inherit from both `Asset` and `Dir`?

No. `DirAsset` inherits from `Asset` alone. The two classes answer opposite questions, and
`notes/directory-assets.md` §1 already records that a path is never both. A `Dir` is a place to
look for assets: `.markers` names it, and each matching file inside becomes an asset of its own. A
dirasset is one dataset: nothing inside it is ever walked or tracked as an asset. A class that is
both would say that its contents are and are not assets.

A trial in memory with the real classes on 2026-10-08 (`class DirAsset(Asset, Dir)`, measured)
shows what would break:

- **It prints as a Dir.** `Asset` defines no `__repr__`, so the method resolution order
  (`DirAsset, Asset, Dir, Entry`) picks `Dir.__repr__`. A zarr store showed `root: False` and
  `scopes: []`, with no file count, size or mtime.
- **It fits in both collections.** `Entries.__setitem__` admits any instance of `entry_type`
  (`entry.py:162-164`), so both `trail.assets` and `trail.dirs` would accept it. `Trail._entry`
  and the replay's `JSONL._made` then depend on which collection is checked first.
- **It inherits Dir behavior that is wrong for it.** It gets `root`, `scopes`, `recursive` and
  `decide()`, so any code that takes a `Dir`, such as `match()` or a scan's walk roots, would treat
  the store as a place to look for assets inside it. That is the per-chunk asset that §1 of
  `directory-assets.md` was written to rule out.
- **The two disagree on moves.** `WatchdogEvent.apply` moves anything that is an `Asset`
  (`event.py:409-414`), while a Dir keeps its path because its line in `.markers` still names it.
- **The two declare `is_directory` differently.** `Dir` has it as a ClassVar (`dir.py:36`), and
  `Asset` as a field.

Nothing would be gained. What `Dir` and `DirAsset` really share is already in `Entry`: an id in the
log, a path, `track`, `offtrail` and the base repr. Being a directory is the one boolean
`is_directory`, which both already have. The one place this plan treats them alike is
`Watchdog.desired()`, which gives both a recursive watch (§5.1) and can do so with no common class.
If a name for "a Dir or a dirasset" helps the type hints there, a type alias
`Watched = Dir | DirAsset` is enough. A `Directory` mixin would hold only `is_directory`, which
clashes with `Asset`'s field.

## 3. Decisions that change 10-7

### 3.1. Watch layout

- **10-7 says:** watch each subfolder of a dirasset with its own watch, opens at the top level only.
- **This plan:** one recursive watchdog watch per Dir and per dirasset, deduplicated by ancestry,
  with `EVENT_FILTER` unchanged, so opens are seen at every depth.
- **Why:** per-folder watches cost one inotify instance and two threads each (EMFILE after 867 here)
  and saw 0-18% of writes into new chunk folders. A recursive watch saw 94-100% (measured). Opens at
  every depth catch what the top level misses: deferred dask compute, `zarr.open_array(store /
  'temp')` and band reads of a safe product. The cost was +20% on a read of 40,000 tiny chunks in
  the same process (measured).

### 3.2. Where an open access lives

- **10-7 says:** `asset.session`.
- **This plan:** `trail.watchdog.session.accesses[asset.id]`, reached as `dirasset.access`.

### 3.3. Counting

- **10-7 says:** no lock, since `consume` runs only on the loop.
- **This plan:** events of a dirasset's children are counted on watchdog's observer thread, under
  the session's lock.
- **Why:** while a notebook cell blocks the loop, today's handler keeps 612 B per raw event
  (measured). A 10⁶-chunk write produces several million events.

### 3.4. Interval fields

- **10-7 says:** `timestamp` is when the session opened, `ended` when it closed.
- **This plan:** `timestamp` is the access's last event and `started` its first, as `ExecutionEvent`
  has it in `notes/JUPYTER_INTEGRATION.md`. An access is logged when it seals, so the log stays
  within `idle_gap` of timestamp order.

### 3.5. Closing

- **10-7 says:** close after 1-2 s idle, or after a maximum length.
- **This plan:** close after `idle_gap` (30 s), with no maximum length (§2.9, §2.10).

### 3.6. What is counted

- **10-7 says:** `ops` per event type and `members` per top-level entry.
- **This plan:** `count` per event type only, for the files that match `children`.

### 3.7. A region write today

10-7 §1 says a region write records nothing. At 9fe304d it is recorded as `opened`, because the
open of the root `zarr.json` is its only top-level event (measured with zarr 3.4). This plan
records it as `modified`.

Unchanged from 10-7: one `DirectoryAccessEvent` per access, no child events, `event_type` is
`opened` or `modified`, chunk events are never logged, and who acted is out of scope.

## 4. Evidence used

| Finding | Number |
|---|---|
| one watchdog watch | 1 inotify instance, 2 threads, 3 fds |
| a recursive `schedule()` | walks the tree on the calling thread at 7-20 µs per directory, holding the observer lock |
| writes into new chunk folders | 0-18% seen with per-folder watches, 94-100% with one recursive watch |
| watchdog's pipeline | 7-8 µs of CPU per delivered event |
| today's handler | 3.8 µs to build a `WatchdogEvent`, and 2.2-7.6 µs per `call_soon_threadsafe` |
| a deque with one wakeup per burst | 0.07-0.87 µs per event |
| a counter update under a lock | 1.3-2.1 µs per event |
| a string walk up a path's parents | 1.1 µs, against 13.3 µs for `Watchdog._enclosing` |
| a 40,000-chunk zarr write watched in its own process | 29.2 s unwatched, 40.0-42.7 s watched |
| a full read of it | +20% with opens watched at every depth |
| `to_zarr(mode='w')` | deletes and recreates the root, in every zarr version tested |
| zarr 2 and zarr ≥ 3.1.3 writes | every key goes to `<name>.<32 hex>.partial`, then is renamed into place |
| a gdb | flat. A layer read opens 6-8 files |
| a safe product | band reads open only `GRANULE/<g>/IMG_DATA/R10m/*.jp2`. `gdalinfo -stats` writes `.aux.xml` sidecars |
| separate watches | the parent's `DirDeletedEvent(root)` came before 511 of 654 inner deletions |
| watchdog and overflow | `IN_Q_OVERFLOW` is dropped silently. Heavy writes lost 18-89% of events |
| a directory moved into a recursive watch on Linux | never watched (watchdog's own TODO, `inotify_c.py:368-375`) |
| today's code across two `asyncio.run` calls without `stop()` | one append logged twice, with 10 watchdog threads alive |
| `observer.stop()` | discards what is still queued. An unmatched move-out waits 0.5 s in watchdog's buffer first |

## 5. Design

### 5.1. Watches

`Watchdog.desired()` returns `dict[Path, bool]`, a directory mapped to whether its watch is
recursive.

- Every Dir's path, and every dirasset's path, maps to True.
- The parent of every Dir and of every asset maps to False, unless it is already True.
- A directory strictly inside a recursive one is left out.
- Trail metadata and missing directories are left out, as today.

`reconcile()` deletes the entries of `session.watches` that `desired()` no longer names or names
with a different recursion, then sets the missing ones. An `OSError` from `schedule()` (a vanished
directory, `ENOSPC`, `EMFILE`) becomes a warning instead of stopping the watchdog. `_subtree`,
`_reaches`, `extend` and `desired(within=…)` go away, and `Watchdog.covers(path)` answers whether a
directory is watched, directly or through a recursive ancestor.

`Dir.recursive` keeps deciding how a scan walks. A Dir whose scopes do not reach subfolders is
still watched recursively, and its subfolders' events reach `consume`, where `.markers` declines
them as it does today.

The observer starts before the first watch is placed, so a directory that vanishes fails only its
own watch. Today `observer.start()` aborts the whole session on the first vanished directory
(measured).

### 5.2. The handler

`Handler.on_any_event` runs on watchdog's observer thread and never touches the Trail. It reads two
values the loop publishes on the session, each replaced whole and never changed in place:

- `roots`, the path of each dirasset mapped to its id and compiled `children` pattern.
- `hidden`, the Dirs that sit at or below a hidden directory.

For each event, in order:

1. A watchdog synthetic `moved` event, one per descendant of a renamed folder, is dropped.
2. An event below a hidden directory (`.git`, `.venv`, `.trail`), or about a hidden directory
   itself, is dropped unless a Dir in `hidden` covers it. A recursive watch sees inside these, and
   Trail never walks them. Without this a `pip install` into `.venv` would track every `*.json` it
   writes.
3. The nearest dirasset root above the path is found by walking up its parents as strings,
   `os.path.dirname` at a time.
4. Inside a dirasset, a file event whose name matches `children` is counted into the dirasset's
   access (§5.5). A folder renamed inside counts once as `moved`. Other folder events inside are
   dropped, since the file events say the same. A file that does not match is queued like any
   other event.
5. Anything else is queued as `Arrival(at=time.time(), event=event)`.

It catches every exception, since one exception kills watchdog's only dispatcher thread, which
serves every watch. The exception is queued for the loop, which raises it as a warning.

This answers the open comment at 10-7:72-73. Neither tuples, dicts nor `__slots__` objects: watchdog
has already built an immutable `FileSystemEvent` with `str` paths, so passing it costs 0.02 µs. What
costs is building a `WatchdogEvent` per raw event (3.8 µs) and waking the loop per event (2.2-7.6
µs). The queue wakes the loop once per burst, events inside dirassets never reach it, and `consume`
builds a `WatchdogEvent` only for events about tracked entries, stamped with `Arrival.at`.

### 5.3. Children

| Suffix | `children` | Leaves out |
|---|---|---|
| `.zarr` | `\d+(?:\.\d+)*\|zarr\.json\|\.z(?:array\|attrs\|group\|metadata)` | `*.<32 hex>.partial` temp files, and failed exclusive writes, which create and delete one |
| `.n5` | `\d+\|attributes\.json` | the same temp files |
| `.gdb` | `a[0-9a-f]{8}(?:\.(?!_compress\.)[^.]+)?\.(?:gdbtable\|gdbtablx\|gdbindexes\|spx\|atx\|freelist\|horizon)\|gdb\|timestamps` | REPACK's `._compress.` tables, ArcGIS `*.sr.lock`, `*.rd.lock`, `*.wr.lock`, `*.ed.lock` |
| `.safe` | `.+(?<!\.aux)\.(?:jp2\|xml\|safe\|tiff?\|gml\|html?\|xsd\|png\|jpe?g\|kml\|json)` | GDAL's `*.aux.xml` sidecars |
| `track DIR` | `.*` | nothing |

Checked on 2026-10-08 against chunk, metadata, temp, lock and sidecar names from the zarr, gdb and
safe traces: every child matched and every leftover did not.

- A rename onto a child name from a name that is not one counts as `modified`. That is how zarr's
  temp files and REPACK's tables land, so both zarr writers give one `modified` per chunk.
- A rename between two child names counts as `moved`. A rename from a child name to one that is not
  a child, or out of the dirasset, counts as `deleted`.
- Lock files and sidecars are written by reads. Counting them would turn a read into `modified`.
- ArcGIS lock files come from Esri's documentation (assumed). GDAL created none.

### 5.4. Files inside a dirasset that are not children

They are ordinary files. Their events are queued, and `consume` handles them as today: a creation
or a move is scanned, and `.markers` may track the file as an asset of its own. A `notes.md` inside
a zarr store becomes an asset when it is created or moved while Trail runs and `*.md` is marked.
Scans still never walk into a dirasset, so a file like that which already exists is not found until
it changes.

### 5.5. Accesses

- `Access` holds `asset`, `count` (all five event types, starting at 0), `started` and `ended` (wall
  clock, epoch seconds) and `seen` (monotonic).
- `Accesses.count(asset, op, at, seen)` takes the lock, seals the open access if this event is more
  than `idle_gap` after its last one, and counts. The first event of an access also wakes the
  consumer, whose next deadline depends on it.
- The consumer waits for a wakeup or for the earliest `seen + idle_gap`, and then seals what has
  gone quiet. Split points are decided by event times, so an access is split correctly even when the
  loop was blocked across the gap. Only its final seal waits for the loop.
- `idle_gap` is 30 s, a class attribute of `Watchdog` in seconds, like `debounce`. Tests set it to
  0.1.
- A sealed access is queued. The loop builds a `DirectoryAccessEvent` from it and logs it. An empty
  access is dropped, and so is one whose dirasset is no longer tracked.
- `Watchdog.flush(dirasset)` seals the dirasset's open access and logs it, together with any of its
  accesses still in the queue, before something ends it: the root deleted or moved, an ancestor
  renamed, an offtrail.

| What ends an access | Where | Order in the log |
|---|---|---|
| `idle_gap` with no event | the consumer's deadline, or the next event | access |
| the root deleted | `_directory`, before the deletion is applied | access, then `deleted` |
| the root moved, or an ancestor renamed | `_directory`, before the move is applied | access at the old path, then `moved` |
| offtrail, including an occupant offtrailed by `Entry.track` or `Asset.move` | `DirAsset.offtrail` | access, then `RemoveAssetEvent` |
| `events clear` | `Events.clear` drops the accesses | none |
| `stop()` | every access sealed, logged by the consumer before it returns | accesses |
| `asyncio.run` cancels the consumer | `apply`'s `finally` | accesses |
| the interpreter exits without `stop()` | the exit hook | accesses, through one `Events.extend` |

### 5.6. The session

- `Watchdog.session` is a property backed by `_session`, with a deleter that halts the session's
  threads and drops it. A session left by an `asyncio.run` that ended without `stop()` is deleted
  before a new one starts, which ends today's double logging (measured).
- `Session.is_closing` makes `ensure()` and `reconcile()` no-ops, so a `consume` running during or
  after `stop()` cannot start a new session. Today one does (measured).
- The consumer task receives its session as an argument, instead of reading `self.queue` on its
  first step (`watchdog.py:372`).
- `stop()`: set `is_closing`, wait until `settle` seconds (0.55) after the last arrival, then
  poll `observer.event_queue.unfinished_tasks` while the dispatcher is alive, for at most 1 s. Then
  run `observer.stop()` and its join through `asyncio.to_thread`, seal every access, queue `STOP`,
  await the consumer, and `del self.session`.
- The exit hook is registered per session through a weakref. It seals the accesses and logs them
  with one `Events.extend`, and never consumes anything else. It checks `os.getpid()`, and a forked
  child starts with no session.
- `Watch.notify` sets each waiter on its own loop: directly from that loop, through
  `call_soon_threadsafe` from any other thread, never for a closed loop, and never raising. Today a
  notify from the exit hook after the loop closed stops the logging after the first access
  (measured).

### 5.7. `DirectoryAccessEvent`

| Field | Type | Meaning |
|---|---|---|
| `timestamp` | `datetime` | the access's last event, in UTC |
| `started` | `datetime`, required | its first event |
| `src_path` | `str` | the dirasset's path during the access |
| `event_type` | `str` | `opened` when only opens were counted, `modified` otherwise |
| `is_directory`, `is_synthetic` | `bool`, always True, not init arguments | |
| `count` | `dict[str, int]` | events counted by type: `opened`, `created`, `modified`, `moved`, `deleted` |

A zarr 3.4 region write of 72 chunks should log this (V3):

```json
{"id": "6b1f…", "timestamp": "2026-10-08T17:02:11.204000+00:00",
 "src_path": "/home/user/project/cube.zarr", "dest_path": "", "event_type": "modified",
 "is_directory": true, "is_synthetic": true,
 "started": "2026-10-08T17:02:10.962000+00:00",
 "count": {"opened": 1, "created": 0, "modified": 72, "moved": 0, "deleted": 0},
 "cls": "DirectoryAccessEvent", "entry": "4e26…"}
```

- Counts are what watchdog delivered, not operations. The kernel merges identical unread events,
  and `IN_MODIFY` fires per `write()` call, coalesced unpredictably. Tests never assert exact counts
  from real watchdog.
- The repr shows `started` in the timestamp's clock format and `count: opened 1, modified 72`, with
  zeros left out.
- `apply` logs it when live and only returns its dirasset on replay.
- Old collapsed `WatchdogEvent(is_directory=True, is_synthetic=True)` records replay as no-ops
  (`event.py:378-387`), so logs need no migration, in line with skipping back-compat while Trail is
  early.

## 6. Lifecycle and moves

### 6.1. A dirasset's own lifecycle

| What happens | What is logged |
|---|---|
| `rm -rf cube.zarr` | the access with the deletions, then `deleted`. The dirasset stays tracked and missing, as today |
| `to_zarr(mode='w')` | the access with the deletions, `deleted`, `created` with the same id, then an access with the rewrite |
| a dirasset created while Trail runs | the `AddAssetEvent` from the scan, then accesses |
| a dirasset copied in, or moved in from somewhere unwatched | a scan, an `AddAssetEvent` with a new id, then accesses |

When the dirasset sits inside a recursive Dir watch, which is the usual case, one inotify instance
reports its inside and its root. The kernel then orders every inner deletion before the root's own
deletion, and watchdog adds a watch for a recreated root in its own thread at once. That holds even
while a notebook cell blocks the loop (assumed from the same mechanism measured for new chunk
folders, V3). A dirasset outside every Dir has a watch of its own and a watch on its parent, which
are two instances, so its deletion can arrive before some of the deletions inside it (§15).

### 6.2. A dirasset moved

| Move | How watchdog reports it | Identity |
|---|---|---|
| within its parent, or anywhere inside one recursive Dir watch | one `DirMovedEvent`, since both halves come from one instance | kept |
| between two separate watches | `DirCreatedEvent(dest)` at once and `DirDeletedEvent(src)` 0.5 s later. `consume` stats the destination, matches its `(st_dev, st_ino)` with `DirAsset.inode`, and applies a move. The late deletion then finds nothing at the source | kept |
| to a directory nothing watches | `DirDeletedEvent(src)` only | lost. The dirasset stays tracked and missing |
| to another filesystem, which is a copy and a delete | a deletion, and a new directory | a new id for the copy |
| an ancestor Dir renamed | `DirMovedEvent` of the Dir. `Trail._moved` repaths the dirasset after `_directory` flushed its access | kept |

`DirAsset.inode` is taken when the dirasset is tracked or replayed, and again after a recreation or
a move. A dirasset with its own watch that moves needs it placed again. `reconcile()` after the
move does that, since `desired()` now names the new path.

### 6.3. Inside a dirasset

- A temp file renamed into place is a write (§5.3).
- A folder renamed inside counts one `moved`. watchdog's synthetic event for each file below it is
  dropped.
- A folder moved in from outside on Linux is never watched by watchdog. Its synthetic `created`
  events are counted, but later writes inside it are not seen until the watch is placed again
  (§15).

## 7. Gaps

- **Gap 1.** `markers.canonical_path` (`markers.py:253-317`) adds `/` to a directory unless
  `is_dirasset` holds for it. `markers.slashless` (`:164-193`) returns None for a dirasset, which
  ends the warning "did you mean `data/cube.zarr/`". The scopes completer (`cli/scopes.py`) adds no
  `/` to a dirasset. If `mark` and `unmark` go away (`notes/prompt.txt`), only the `slashless` and
  completer parts remain.
- **Gap 2.** `Trail._track` calls `self.watchdog.reconcile()` after tracking a dirasset, which
  publishes the new root to the handler and places a watch if no recursive watch covers it.
- **Gap 4.** §2.15. `Assets._markable` refuses a path that is a child of a dirasset, so a scan
  never tracks a chunk. That fixes `mark data/cube.zarr/band/`, which today tracks chunk files.
  `dir.build` leaves out a Dir anywhere below a dirasset, not only directly at one
  (`dir.py:121-130`), and `Markers.warnings` checks the whole lineage (`markers.py:721-731`).
- **`*.safe`** goes into `markers/geospatial.txt`. Matching ignores case, so it matches `.SAFE`.

## 8. Data model

### 8.1. `util.py`

`Path` and `Final` are already imported there.

```python
# the names of the files that are part of a dirasset, by the dirasset's suffix. A name is matched
# whole, ignoring case. Anything else inside, such as a temporary or lock file, is not part of it
CHILDREN: Final[dict[str, str]] = {
    '.zarr': r'\d+(?:\.\d+)*|zarr\.json|\.z(?:array|attrs|group|metadata)',
    '.n5': r'\d+|attributes\.json',
    '.gdb': (
        r'a[0-9a-f]{8}(?:\.(?!_compress\.)[^.]+)?'
        r'\.(?:gdbtable|gdbtablx|gdbindexes|spx|atx|freelist|horizon)'
        r'|gdb|timestamps'
    ),
    '.safe': r'.+(?<!\.aux)\.(?:jp2|xml|safe|tiff?|gml|html?|xsd|png|jpe?g|kml|json)',
}
# a directory tracked with `track DIR` holds everything in it
EVERYTHING: Final = r'.*'
# the event types an access counts, in the order a repr lists them
OPS: Final = ('opened', 'created', 'modified', 'moved', 'deleted')


def is_dirasset(path: Path) -> bool:
    """
    Whether a directory holds one dataset, judged by its suffix.

    >>> is_dirasset(Path('cube.zarr'))
    True
    """
    return path.suffix.lower() in CHILDREN
```

### 8.2. `asset.py`

```python
from trail.util import CHILDREN, EVERYTHING
```

```python
@dataclass(kw_only=True, eq=False, repr=False)
class DirAsset(Asset):
    """
    A directory tracked as one dataset, such as a zarr store, a file geodatabase or a Sentinel
    product. Its inside is never walked. The files in it whose names match `children` are part of
    it, and their events are counted into its access.
    """

    is_directory: bool = field(default=True, repr=False)
    # the root's (st_dev, st_ino), to follow a move the watchdog reports as a creation
    inode: tuple[int, int] | None = field(default=None, init=False, repr=False)

    @property
    def children(self) -> str:
        """
        The pattern of the names of the files that are part of the dirasset. A directory tracked
        with `track DIR` holds everything in it.
        """
        return CHILDREN.get(self.path.suffix.lower(), EVERYTHING)

    @property
    def access(self) -> Access | None:
        """
        The dirasset's open access. An empty one is made when none is open, and an empty access is
        never logged. None while the watchdog is dormant.
        """
        session = self._trail.watchdog.session
        if session is None:
            return None
        return session.accesses[self.id]

    def offtrail(self) -> None:
        """Log the dirasset's open access, then remove it from the collection."""
        self._trail.watchdog.flush(self)
        Asset.offtrail(self)
```

- `DirAsset` inherits from `Asset` only, never from `Dir` (§2.20).
- `Asset.from_path` returns a `DirAsset` for a directory.
- `JSONL._made` (`event.py:509-532`) makes a `DirAsset` when the record has `is_directory`.
- Every `asset.is_directory` check keeps working.

### 8.3. `session.py` (new)

`Session`, `Watches`, `Accesses`, `Access`, `Arrival` and `Handler` live here, since each belongs to
one session. `watchdog.py` imports `Session`.

```python
from __future__ import annotations

import asyncio
import os
import re
import threading
import time
from collections import UserDict, deque
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from trail.node import Node
from trail.util import OPS

if TYPE_CHECKING:
    from watchdog.observers.api import ObservedWatch

    from trail.watchdog import Watchdog
```

```python
# queued by stop() after everything else
STOP: Final = object()


class Arrival(NamedTuple):
    """A watchdog event, with the time the observer thread received it, in epoch seconds."""

    at: float
    event: FileSystemEvent


@dataclass(slots=True, eq=False)
class Access:
    """
    The open access of one dirasset: how many events of each type were counted, and when its first
    and last events came. Concurrent readers and writers share it.
    """

    asset: str
    count: dict[str, int] = field(default_factory=lambda: dict.fromkeys(OPS, 0))
    # epoch seconds of the first and the last event, which become `started` and `timestamp`
    started: float | None = None
    ended: float | None = None
    # monotonic seconds of the last event, which the idle gap is measured from
    seen: float | None = None

    def add(
            self,
            op: str,
            at: float,
            seen: float,
    ) -> None:
        """Count one event at its wall-clock and monotonic times."""
        if self.started is None:
            self.started = at
        self.ended = at
        self.seen = seen
        self.count[op] += 1


class Accesses(UserDict[str, Access]):
    """
    The open access of each dirasset, by asset id. The observer thread counts into it and the loop
    seals it, so every change holds the session's lock. Reading a dirasset that has no open access
    makes an empty one, which is never logged.
    """

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session

    def __missing__(self, asset: str) -> Access:
        with self.session.lock:
            access = Access(asset=asset)
            return self.data.setdefault(asset, access)

    def count(
            self,
            asset: str,
            op: str,
            at: float,
            seen: float,
    ) -> None:
        """
        Count one event of a dirasset. One more than `idle_gap` after the access's last event seals
        the access and opens the next.
        """
        session = self.session
        with session.lock:
            access = self[asset]
            if (
                access.seen is not None
                and seen - access.seen > session.idle_gap
            ):
                self.seal(asset)
                access = self[asset]
            if access.seen is None:
                # the consumer's next deadline depends on this access
                session.wake()
            access.add(op, at, seen)

    def seal(
            self,
            asset: str | None = None,
            before: float | None = None,
    ) -> None:
        """
        Queue the open access of `asset`, the ones last seen before a monotonic time, or every one.
        An empty access is dropped.
        """
        session = self.session
        with session.lock:
            if asset is not None:
                identifiers = [asset]
            else:
                identifiers = [
                    identifier
                    for identifier, access in self.data.items()
                    if before is None
                    or (
                        access.seen is not None
                        and access.seen <= before
                    )
                ]
            for identifier in identifiers:
                access = self.data.pop(identifier, None)
                if (
                    access is not None
                    and access.seen is not None
                ):
                    session.put(access)

    def deadline(self) -> float | None:
        """The monotonic time the quietest open access goes idle, or None when none is open."""
        session = self.session
        with session.lock:
            seen = [
                access.seen
                for access in self.data.values()
                if access.seen is not None
            ]
        if not seen:
            return None
        return min(seen) + session.idle_gap


class Watches(UserDict[Path, bool]):
    """
    The directories the session watches, each mapped to whether its watch is recursive. Setting a
    directory schedules its watch, and deleting it unschedules the watch.
    """

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        self.handles: dict[Path, ObservedWatch] = {}

    def __setitem__(
            self,
            directory: Path,
            recursive: bool,
    ) -> None:
        if self.data.get(directory) == recursive:
            return
        if directory in self.data:
            del self[directory]
        session = self.session
        handle = session.observer.schedule(
            session.handler,
            str(directory),
            recursive=recursive,
            event_filter=EVENT_FILTER,
        )
        self.handles[directory] = handle
        self.data[directory] = recursive

    def __delitem__(self, directory: Path) -> None:
        handle = self.handles.pop(directory)
        del self.data[directory]
        # the emitter stopped itself if its directory was deleted
        with suppress(KeyError):
            self.session.observer.unschedule(handle)
```

`EVENT_FILTER` moves here from `watchdog.py` unchanged. `Session` holds, in its `__init__`: `loop`,
`lock` (a `threading.RLock` guarding the queue, the accesses and `is_wake_pending`, and nothing
slow), `queue` (a deque), `is_wake_pending`, `is_closing`, `woken` (an `asyncio.Event`),
`idle_gap`, `handler`, `observer`, `watches`, `accesses`, `roots`, `hidden`, `last` and `consumer`.
Its methods are short:

```python
    def put(self, item: object) -> None:
        """Queue an arrival, a sealed access, an exception or STOP, and wake the consumer."""
        with self.lock:
            self.queue.append(item)
            self.wake()

    def pop(self) -> object | None:
        """
        Returns the oldest queued item, or None once the queue is empty. The pending wakeup is
        cleared only where the queue is found empty, so no item waits without one.
        """
        with self.lock:
            if self.queue:
                return self.queue.popleft()
            self.is_wake_pending = False
            return None

    def wake(self) -> None:
        """Wake the consumer once per burst. The caller holds the lock."""
        if self.is_wake_pending:
            return
        self.is_wake_pending = True
        # the loop may close while the observer thread still delivers
        with suppress(RuntimeError):
            self.loop.call_soon_threadsafe(self.woken.set)

    def enclosing(self, path: str) -> str | None:
        """Returns the root of the dirasset a path is strictly inside of, or None."""
        roots = self.roots
        parent = os.path.dirname(path)
        while True:
            if parent in roots:
                return parent
            above = os.path.dirname(parent)
            # the filesystem root is its own parent
            if above == parent:
                return None
            parent = above
```

`Session.halt()` sets `is_closing`, stops the observer and joins it. It is safe to call twice, and
from a loop that has ended. `Handler.on_any_event` follows §5.2: it reads `session.roots` and
`session.hidden` once per event, counts with `session.accesses.count(…)`, queues everything else
with `session.put(Arrival(at=…, event=event))`, and wraps its body in a `try` that queues any
exception.

### 8.4. `watchdog.py`

```python
import time
from datetime import UTC, datetime

from trail.asset import Asset, DirAsset
from trail.event import DirectoryAccessEvent
from trail.session import Access, Session
```

```python
    @property
    def session(self) -> Session | None:
        """The running session, or None while the watchdog is dormant."""
        return self._session

    @session.deleter
    def session(self) -> None:
        session = self._session
        self._session = None
        if session is not None:
            session.halt()

    def record(self, access: Access) -> DirectoryAccessEvent | None:
        """
        Log a sealed access as one DirectoryAccessEvent. The access of a dirasset no longer tracked
        is dropped.
        """
        trail = self._trail
        asset = trail.assets.id2entry.get(access.asset)
        if not isinstance(asset, DirAsset):
            return None
        is_read = all(
            count == 0
            for op, count in access.count.items()
            if op != "opened"
        )
        if is_read:
            event_type = "opened"
        else:
            event_type = "modified"
        started = datetime.fromtimestamp(access.started, UTC)
        ended = datetime.fromtimestamp(access.ended, UTC)
        event = DirectoryAccessEvent(
            src_path=str(asset.path),
            event_type=event_type,
            timestamp=ended,
            started=started,
            count=dict(access.count),
        )
        event.entry = asset
        event.apply(trail)
        return event

    def flush(self, asset: Asset) -> None:
        """
        Log a dirasset's accesses that are open or still queued, so they come before the deletion,
        move or offtrail about to be logged.
        """
        session = self.session
        if session is None:
            return
        with session.lock:
            session.accesses.seal(asset.id)
            pending = [
                item
                for item in session.queue
                if isinstance(item, Access)
                and item.asset == asset.id
            ]
            kept = [
                item
                for item in session.queue
                if item not in pending
            ]
            session.queue.clear()
            session.queue.extend(kept)
        for access in pending:
            self.record(access)

    async def apply(self, session: Session) -> None:
        """
        Consume what the session queues until STOP, sealing accesses that have gone quiet. The
        accesses still open when the task is cancelled are logged on the way out.
        """
        accesses = session.accesses
        try:
            while True:
                timeout = None
                deadline = accesses.deadline()
                if deadline is not None:
                    timeout = max(0.0, deadline - time.monotonic())
                with suppress(TimeoutError):
                    await asyncio.wait_for(session.woken.wait(), timeout)
                session.woken.clear()
                if self.debounce:
                    await asyncio.sleep(self.debounce)
                accesses.seal(before=time.monotonic() - self.idle_gap)
                if self.consume(session):
                    return
        finally:
            # cancelled by asyncio.run's teardown, as a script that never stops is
            self._record_open(session)
```

`consume(session)` pops one item at a time, so a `flush` reached from inside it still sees every
queued access. A sealed `Access` goes to `record`, an exception to `warnings.warn`, and an
`Arrival` to today's per-event body (`watchdog.py:408-426`), built into a `WatchdogEvent` stamped
with `Arrival.at`. It returns True at `STOP`. The per-batch fold (`watchdog.py:427-438`) and
`_enclosing` go away. `_directory` calls `flush` for every dirasset at or below the source before it
applies a deletion or a move, and matches a created directory's inode against the tracked
dirassets (§6.2).

### 8.5. `event.py`

`DirAsset` comes from `trail.asset`, which `event.py` already imports at runtime.

```python
from trail.asset import Asset, DirAsset
from trail.util import OPS, asset_repr, clock_repr, mtime_repr, normalize_id, st_size_repr
```

```python
    def to_record(self) -> dict:
        """
        Returns the event as one record of the log. The entry is held by its id, and every
        datetime in ISO format.
        """
        out = {}
        for event_field in fields(self):
            name = event_field.name
            if name == "entry":
                continue
            value = getattr(self, name)
            if isinstance(value, datetime):
                value = value.isoformat()
            out[name] = value
        out["cls"] = type(self).__name__
        out["entry"] = self.entry.id
        return out
```

`Event._repr_items` formats any `datetime` the way it formats `timestamp` today, through a
`clock_repr` helper moved into `util.py` from `event.py:68-77`.

```python
@dataclass(kw_only=True, slots=True, repr=False)
class DirectoryAccessEvent(WatchdogEvent):
    """
    One access to a dirasset: what happened inside it from `started` to its last event, which is
    its `timestamp`. It is inferred from many filesystem events, so it is always synthetic. `count`
    holds the events of each type that were counted for the dirasset's children. It counts what the
    watchdog delivered, not how many files were read or written. `event_type` is `opened` when only
    opens were counted, and `modified` otherwise.
    """

    started: datetime
    is_directory: bool = field(default=True, init=False, repr=False)
    is_synthetic: bool = field(default=True, init=False, repr=False)
    count: dict[str, int] = field(default_factory=dict)
    entry: DirAsset | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        WatchdogEvent.__post_init__(self)
        # a replay passes the start as the log holds it
        if isinstance(self.started, str):
            self.started = datetime.fromisoformat(self.started)

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        """Yields the fields a repr shows, with the counts on one line and zeros left out."""
        for name, value in WatchdogEvent._repr_items(self):
            if name == "count":
                value = ", ".join(
                    f"{op} {value[op]}"
                    for op in OPS
                    if value.get(op)
                )
            yield name, value

    def apply(
            self,
            trail: Trail,
            *,
            replay: bool = False,
    ) -> DirAsset | None:
        """
        Log the access and return its dirasset. An access changes nothing about the dirasset, so a
        replay only returns it.
        """
        if not replay:
            trail.events.append(self)
        return self.entry
```

Methods of a `slots=True` dataclass call their base explicitly, since zero-argument `super()` fails
there on Python 3.12 (measured). The docstring's example repr is pasted from a real test run, never
written by hand.

### 8.6. `trail.py`

`Trail._track` refuses Trail metadata as today, a directory that holds `trail.dir`, and a path whose
name matches the children of a dirasset it is inside. A new dirasset first offtrails the tracked
assets inside it that match its children, with a warning such as "tracking table whole offtrails 1
asset inside it", then logs its `AddAssetEvent`, records its inode, and ends with
`self.watchdog.reconcile()`. The `RemoveDirEvent` from `dirs.refresh` stays the last event, as
`tests/test_dirs.py:120-125` expects.

## 9. Commits, in order

The commits go on one local branch off `dhodczak`, in this order, and are not pushed or opened as
PRs. They are written in one run and reviewed together at the tip. Fixes from review go on top as
new commits.

No commit migrates existing logs, assets or `project.json`. Every log written today stays readable
after every commit.

### Commit 0: hygiene

- Commit `src/trail/entry.py` and `tests/test_dirs.py`. HEAD imports `trail.entry` (`asset.py:10`,
  `dir.py:8`, `trail.py:15`) but does not contain it, so a clean checkout cannot import `trail`.
  Decide on `src/trail/markers/general.txt`, which HEAD's tests use, and leave `src/a/` out.
- Move the pre-commit ruff rev from v0.15.20 to v0.16.8, which the dev group uses, and fix SIM114
  at `util.py:236`.
- Add pytest to the dev group and a CI test job on Linux, macOS and Windows. Support for macOS and
  Windows comes first, so they need CI from the start.
- Stop the Trails that tests leave watching (`test_trail.py:940`, `test_dirs.py:168`,
  `test_input.py:51`).
- Fix `playground.Asset.open`, which returns None (`playground.py:172-178`).

### Commit 1: `*.safe`

- **Modules:** `markers/geospatial.txt`.
- **Acceptance:** `test_the_geospatial_preset_tracks_safe_products`.

### Commit 2: gap 1

- **Modules:** `markers.py`, `cli/scopes.py`.
- **Acceptance:** `test_a_dirasset_gets_no_slash`, `test_a_dotted_folder_keeps_its_slash`,
  `test_a_dirasset_line_draws_no_slash_warning`, `test_marking_one_dirasset_tracks_it`.

### Commit 3: `DirAsset` and `children`

- **Modules:** `util.py` (`CHILDREN`, `EVERYTHING`, `is_dirasset` in place of `CONTAINERS` and
  `container`), `asset.py` (`DirAsset`, `Asset.from_path`), `event.py` (`JSONL._made`), and every
  caller of `container()`.
- **API:** a directory asset is a `DirAsset`. `is_directory` keeps working.
- **Acceptance:** `test_a_tracked_directory_is_a_dirasset`, `test_a_dirasset_survives_a_reload`,
  `test_children_match_the_files_of_each_format` (the names of §5.3).

### Commit 4: gap 4

- **Modules:** `trail.py`, `asset.py` (`_markable`), `dir.py`, `markers.py`.
- **Changed tests:** `test_trail.py:959-960` inverts. `test_dirs.py:116-129` expects the warning.
- **Acceptance:** `test_tracking_a_child_of_a_dirasset_is_refused`,
  `test_a_file_that_is_not_a_child_can_be_tracked`,
  `test_tracking_a_directory_offtrails_its_children`,
  `test_the_project_directory_cannot_be_tracked_whole`, `test_marking_inside_a_dirasset_tracks_no_chunk`.

### Commit 5: the session and the hand-off

- **Modules:** `session.py` (new: `Session`, `Watches`, `Arrival`, `Handler`, `STOP`),
  `watchdog.py` (`session`, `apply(session)`, `consume`, `stop`), `event.py` (`Watch.notify`).
- **API:** `Watchdog.session` replaces `queue`, `loop`, `observer`, `handler` and `consumer`.
  `Watchdog.watches` stays as a property returning `session.watches`, or None while dormant.
  `test_trail.py:165` checks `session is None` instead of `queue is None`.
- **Acceptance:** `test_an_event_is_stamped_when_it_arrives`,
  `test_a_session_left_running_is_stopped_before_the_next`,
  `test_nothing_after_stop_starts_a_session`, `test_one_wakeup_carries_a_burst`,
  `test_stop_keeps_the_events_watchdog_already_read`,
  `test_a_handler_exception_does_not_stop_dispatch`,
  `test_notifying_from_another_thread_wakes_a_watcher`.

### Commit 6: recursive watches, and gap 2

- **Modules:** `watchdog.py` (`desired`, `reconcile`, `covers`, removal of `_subtree`, `_reaches`,
  `extend`), `session.py` (the hidden rule and `hidden`), `trail.py` (`_track` reconciles).
- **Changed tests:** the `desired()` checks at `test_trail.py:691`, `:828`, `:890-891` become
  `covers()` checks.
- **Acceptance:** `test_a_dir_is_watched_recursively`,
  `test_a_folder_inside_a_recursive_watch_gets_no_watch`, `test_a_parent_is_watched_alone`,
  `test_events_below_a_hidden_directory_are_dropped`,
  `test_a_dir_below_a_hidden_directory_is_still_seen`,
  `test_a_dirasset_outside_every_dir_gets_its_own_watch`,
  `test_a_dirasset_created_while_watching_is_watched`,
  `test_a_vanished_directory_fails_only_its_own_watch`.

### Commit 7: `DirectoryAccessEvent`

- **Modules:** `event.py`, `util.py` (`clock_repr`, `OPS`), `cli/commands.py`
  (`EventsCommand.fields` gains `started` and `count`).
- **Acceptance:** `test_an_access_survives_a_reload`, `test_an_access_is_one_line_of_the_log`,
  `test_the_feed_renders_an_access`, `test_cls_finds_accesses_and_their_base`. Each uses its own
  fixture, since the shared ones in `test_select.py` and `test_listing.py` pin event positions.

### Commit 8: accesses

- **Modules:** `session.py` (`Access`, `Accesses`, counting in `Handler`), `watchdog.py`
  (`record`, `flush`, deadlines, the exit hook, `idle_gap`), `asset.py` (`DirAsset.access`,
  `DirAsset.offtrail`), `event.py` (`Events.clear` drops accesses).
- **Behavior:** the per-batch fold goes away, and so does today's bug that logs a write after a
  later deletion or rename of the dirasset.
- **Changed tests:** `test_a_container_is_tracked_whole` (`test_trail.py:900-944`) expects one
  `DirectoryAccessEvent`, sets `idle_gap = 0.1` and `debounce = 0`, writes below the top level too,
  and checks that counts are positive rather than exact.
- **Acceptance (unit):** `test_an_access_is_recorded_once`, `test_an_access_closes_after_the_idle_gap`,
  `test_a_gap_splits_an_access_even_when_the_loop_was_blocked`,
  `test_an_access_starts_at_its_first_event_and_ends_at_its_last`,
  `test_a_temp_file_renamed_into_place_is_a_write`, `test_lock_files_leave_a_read_a_read`,
  `test_an_access_of_only_opens_is_opened`, `test_an_empty_access_is_never_logged`,
  `test_offtrailing_a_dirasset_records_its_access_first`,
  `test_clearing_the_events_drops_an_open_access`, `test_stopping_records_every_open_access`,
  `test_a_cancelled_consumer_records_its_accesses`.
- **Acceptance (integration):** `test_reading_a_dirasset_records_one_opened_access`,
  `test_writing_below_the_top_level_is_counted`, `test_concurrent_reads_merge_into_one_access`,
  `test_reading_a_band_of_a_safe_product_is_recorded`.

### Commit 9: lifecycle and moves

- **Modules:** `watchdog.py` (`_directory` flushes first, the inode match), `asset.py`
  (`DirAsset.inode`).
- **Acceptance:** `test_an_access_is_recorded_before_the_dirasset_is_deleted`,
  `test_rewriting_a_dirasset_in_place_keeps_its_id` (order: access, deleted, created, access),
  `test_a_dirasset_renamed_in_its_parent_keeps_its_id`,
  `test_a_dirasset_moved_between_watches_keeps_its_id`,
  `test_an_ancestor_rename_records_the_access_at_the_old_path`.

### Commit 10: console

- **Modules:** `cli/console.py`, `cli/render.py`, `cli/commands.py`.
- **Changes:** drain the feed after the watchdog context exits, so the accesses `stop()` sealed are
  printed (`cli/console.py:318-343`). Show `accesses N` in the header while N is not zero. Say in
  `NOTES` and `MarkCommand` that a dirasset is marked without `/`. Remove the dead `discovered`
  branch of `Renderer.verb` (`cli/render.py:252-254`) and the unused `Renderer.directory`
  (`:258-266`).
- **Acceptance:** `test_the_feed_prints_the_accesses_stop_sealed`, `test_the_header_counts_open_accesses`.

### Commit 11 (optional): `select` on counts

Dotted fields that step through mappings, so `events count.modified>0` works. Today a field name
cannot hold a dot (`select.py:19-22`).

## 10. Tests

- **Unit, no thread.** A dormant `Trail(root, markers=None)`, a `Session` built with a stub loop,
  `session.accesses.count(…)` and `session.put(Arrival(…))` driven with explicit times, then
  `trail.watchdog.consume(session)`. Counts are exact here, and the log order is checked directly.
- **Integration.** The repo's `asyncio.run(run())` pattern, `asyncio.timeout(5)` around every wait,
  `idle_gap = 0.1`, `debounce = 0` and `settle = 0` set right after `Trail(root)`, and a helper that
  always stops every Trail it opens. Real counts vary between runs (one `write_text` gave one or
  two `modified`), so assertions check presence, `event_type` and order, never exact values. Tests
  that need opens skip off Linux, since only watchdog's inotify backend reports them.
- **Playground.** Nodes for a zarr v3 store (`zarr.json`, `arr/zarr.json`, `arr/c/0/0`), a zarr v2
  store (`.zgroup`, `.zmetadata`, `arr/.zarray`, `arr/0.0`), a gdb (flat `a0000000N.gdbtable` and
  `.gdbtablx`) and a safe product (`manifest.safe`, `GRANULE/<g>/IMG_DATA/R10m/*.jp2`).
  `rewrite()` removes and recreates the root as `mode='w'` does. A dirasset shows as one line with
  its file count in the playground's repr. New modules use the `dir(suite)` runner of
  `test_dirs.py:223-228`.

## 11. Invariants

1. An access is logged at most once, after its dirasset's `AddAssetEvent`, with
   `started <= timestamp`.
2. An access is logged before the deletion, move or `RemoveAssetEvent` that ended it. The
   exception is the cross-watch deletion of §15.
3. No raw event of a dirasset's child reaches `events.jsonl`, and none is kept per event while the
   loop is busy.
4. A child of a dirasset is never an asset, and no Dir is named inside a dirasset.
5. No directory is watched twice. A directory inside a recursive watch has no watch of its own.
6. Events below a hidden directory that no Dir covers never reach `consume`.
7. After `stop()` returns, no thread of that session runs, and nothing can start a new session from
   it.
8. The session's lock is never held across I/O, a watchdog call or a join.

## 12. Failure modes

| Failure | Effect | Handling |
|---|---|---|
| overflow on any watch | events lost silently, and on Linux some new folders left unwatched | none with watchdog. §15 |
| `ENOSPC` when a watch is placed | that directory unwatched | a warning naming `fs.inotify.max_user_watches` |
| `ENOSPC` for a folder created later | watchdog's buffer thread dies, and that watch goes silent | none with watchdog's public API. §15 |
| a directory vanishes before its watch is placed | that watch fails | a warning. The other watches still run |
| an exception in the handler | none | caught and raised on the loop as a warning |
| the Trail's own process is killed | its open accesses are lost | accepted (§2.9) |
| the interpreter exits without `stop()` | none | the exit hook logs the accesses |
| a fork | none | the child starts with no session |
| a move-out held 0.5 s by watchdog at `stop()` | none | `stop()` waits `settle` after the last arrival |

## 13. Performance targets

| | Target | Today |
|---|---|---|
| loop work per event of a dirasset's child | none. The loop works once per access | about 20 µs |
| memory per raw event while the loop is blocked | 0 B. One `Access` per open dirasset | 612 B |
| handler work per event, on top of watchdog's 7-8 µs | at most 3 µs: the parent walk (1.1 µs) and a count under the lock (1.3-2.1 µs) | 3.8 µs plus 2.2-7.6 µs per wakeup |
| a full read with opens at every depth | at most +20% on tiny chunks, less on real ones | +0% (opens below the top level not watched) |
| log growth | one record of about 0.4 KB per access | one record per 100 ms batch |
| each new integration test | at most 0.4 s | — |

## 14. Validation tasks

| # | Task | Commit |
|---|---|---|
| V1 | directories under typical `.venv`, `node_modules` and `.git`, the time to place a recursive project watch on Linux, and the headroom in `max_user_watches` on a stock Ubuntu machine | 6 |
| V2 | recursive watches on macOS FSEvents and Windows: cost to place, a write below the top level, a root deleted and recreated | 6, 9 |
| V3 | real zarr 2 and zarr 3 writes and `mode='w'` under a recursive Dir watch, gdb through pyogrio and safe band reads through rasterio, against §5.3 and §5.7 | 8, 9 |
| V4 | the overhead of opens at every depth on real chunk sizes | 8 |
| V5 | compute-bound dask writes and reads, to settle `idle_gap` | 8 |

## 15. Limitations

- **Overflow is silent.** watchdog drops `IN_Q_OVERFLOW`, so heavy writes can lose counts, and on
  Linux a folder created during an overflow stays unwatched until the next session.
- **A folder moved into a recursive watch on Linux is not watched,** a gap in watchdog itself. Its
  files are counted as created when it arrives. Later writes inside it are not seen until the next
  session.
- **A dirasset outside every Dir** has a watch of its own and one on its parent. Its deletion can
  arrive before some of the deletions inside it, which then form a short access logged after
  `deleted`.
- **A dirasset moved somewhere nothing watches** is logged as deleted, and its id is lost when it
  comes back.
- **Reads are seen only on Linux.** No watchdog backend other than inotify reports opens.
- **Recursive watches see inside `.git`, `.venv` and `node_modules` on Linux.** The handler drops
  those events, but they cost watch descriptors and watchdog CPU time.
- **A killed Trail loses its open accesses,** for as long as they were open.
- **Counts are approximate.**
- **A tracked file moved into a dirasset under a child name** stays tracked at its new path, and the
  dirasset also counts it.
- **Two Trails on one project log every access twice.**
- **The writer of an access is unknown,** as 10-7 §3.3 says.

## 16. Deferred

- **A Trail-owned inotify reader on Linux.** One instance for every watch, a mask per directory,
  overflow detection, cookie pairing across directories, and moved-in folders watched. The
  measurements for it are in `notes/10-8-directory-asset-sessions-plan.md` §8.
- **watchdog's private overflow hook.** Replacing `Inotify._parse_event_buffer` on the class does
  catch overflows (measured), but it means pinning watchdog.
- **A journal of open accesses,** so a killed kernel loses nothing.
- **Which parts of a dataset an access touched,** such as arrays or tables. It may return with
  datasets spread over sibling files, such as shapefiles.
- **Fingerprint changes:** one walk for size and mtime, and the file count in the baseline.
- **Sealing accesses at Jupyter cell boundaries,** for attribution.

## 17. Resolved

| Question | Answer |
|---|---|
| backend | watchdog on every platform. inotify deferred |
| watches | recursive for every Dir and dirasset, alone for parents, none inside a recursive watch |
| names | `DirAsset`, `children`, `Access`, `trail.watchdog.session`, `session.accesses`, `session.watches` |
| `DirAsset` bases | `Asset` only. Not `Dir`: a path is never both, and a class that is both prints as a Dir and fits in both collections (§2.20) |
| what an access counts | `count: dict[str, int]` per event type. No members |
| what belongs to a dirasset | file names matching `DirAsset.children`, one regex per suffix |
| other files inside a dirasset | ordinary files, checked against `.markers` |
| opens | every depth, as watchdog's recursive watch delivers them |
| `idle_gap` | 30 s. No maximum length |
| interval fields | `timestamp` is the last event, `started` the first |
| lock | the session's `RLock`, held only for queue and access bookkeeping |
| `asset.access` | live: `session.accesses[asset.id]` |
| hand-off (10-7:72-73) | watchdog's own event with its arrival time, one wakeup per burst |
| `.safe` (10-7:166) | `*.safe` in the geospatial preset |
| gap 1 | `/` for every directory except a dirasset |
| `moments` | removed. Any datetime is written in ISO format |
| fingerprint | unchanged |
| migrations | none |
