# Directory asset sessions: plan

This plan covers scalable and reliable support for directory assets, mainly zarr stores, file
geodatabases (`.gdb`) and Sentinel-2 products (`.safe`). It describes the code at
`9fe304d Flatten nested expressions into named intermediates` and builds on
`notes/10-7-directory-asset-events.md` and `notes/directory-assets.md`. Where this plan and the 10-7
note disagree, §2 lists each difference and why. The 10-7 note stays the record of what was decided
before the measurements.

**This is a series of one-feature PRs, not one change.** §15 gives the order. Each PR leaves the
suite passing and can be reviewed on its own, as `notes/9-30.md` asks.

Most of the evidence was measured on 2026-10-08 on this machine: Linux 6.12, Python 3.12.14,
watchdog 6.0.0, tmpfs with a warm cache. The format traces used zarr-python 2.18.7, 3.1.2 and
3.4.0 with xarray and dask, and GDAL 3.9 to 3.13 through pyogrio, fiona, rasterio and the GDAL
command-line tools. The measurement scripts lived in a temporary directory that a reboot later
erased, but their numbers are kept here. A claim marked **measured** was run. A claim marked
**assumed** was reasoned from code or documentation. §21 lists what still has to be measured.

## Status

The PRs of §15. A PR is ticked once it is merged and the suite passes with it.

- [ ] PR 0: hygiene (prerequisite, outside this plan's code)
- [ ] PR 1: `*.safe` in the geospatial preset
- [ ] PR 2: gap 1, marking one store
- [ ] PR 3: gap 4, nothing tracked or named inside a directory asset
- [ ] PR 4: gap 2, a store tracked mid-run is watched
- [ ] PR 5: the watchdog shift and the hand-off from observer threads
- [ ] PR 6: `Event.moments` and `DirectoryAccessEvent`
- [ ] PR 7: sessions on the existing top-level watch
- [ ] PR 8: the Linux interior reader
- [ ] PR 9: overflow detection and recovery
- [ ] PR 10: interiors on macOS and Windows, and the Linux fallback
- [ ] PR 11: a fingerprint in one walk
- [ ] PR 12: console
- [ ] PR 13 (optional): `select` on counts and members
- [ ] PR 14 (needs a decision): opens below the top level

## 1. Vocabulary

| Term | Meaning | Lives at |
|---|---|---|
| directory asset | a directory tracked as one dataset: a container (`.zarr`, `.gdb`, `.safe`, `.n5`) or any directory tracked with `track DIR` | `trail.assets`, `asset.is_directory` |
| interior | everything below a directory asset's root | watched by `Interiors` |
| session | one access to a directory asset, from its first event until it goes idle. Concurrent readers and writers share it | open as a `Tally`, logged as a `DirectoryAccessEvent` |
| tally | the counts of a session that is still open | `Inbox.tallies` |
| seal | close a tally, so the loop logs it once | `Inbox.seal` |
| member | a named part of a directory asset: an array of a zarr group, a table of a gdb, a file of a safe product. Later also a sibling file of a shapefile (the deferred `Asset.members`) | `Profile.member` |
| transient name | a temporary, lock or sidecar file whose events are not counted | `Profile.transient` |
| lossy | Trail knows that some events of the session were not observed | `DirectoryAccessEvent.is_lossy` |
| shift | one stretch of watching, from `Watchdog.start` to `Watchdog.stop`. It owns every thread, queue and handle of that stretch. The code calls this a session today | `Watchdog.shift` |
| reader | the Linux thread that watches every interior through one inotify instance | `InotifyInteriors.reader` |
| lifecycle record | a directory asset's root created, deleted or moved, as the reader saw it | `Lifecycle` |

The word session now means an access, as the 10-7 note uses it. `Watchdog._start_session` and the
run-level wording in `watchdog.py` become `_start_shift` and "shift", so the two meanings never sit
side by side.

> How about use `Access` for an access? That way, we can use `session` for `watchdog.session`. Simple as that!

## 2. Decisions that change 10-7

Each decision needs your agreement before the PR that implements it. Comments go under the
decision they are about.

### 2.1. Watching interiors

- **10-7 says:** Watch each subfolder of a directory asset with its own watchdog watch, through
  `Watchdog.desired` and a filter argument (§3.2).
- **This plan:** On Linux, one Trail-owned inotify instance and one thread watch every interior and
  the parent of every root. watchdog keeps watching Dirs and file assets.
- **Why:** Each watchdog watch is its own inotify instance with 2 threads and 3 fds. Per-subfolder
  watches failed with EMFILE after about 867, and saw only 0-18% of writes into new chunk folders.
  Separate instances also reorder events, and watchdog drops overflow silently (§3, §8.1).

> So what you're esaying is that we can't use watchdog for directoryassets because it doesn't scale: it'll fail when thre are at at least 867 subfolders? Does that mean that watchdog itself will fail when trail is made to watch more than 867 dirs and we should just use inotify in general?

> **Claude:** Not quite. 867 is not a watchdog limit. Linux caps how many inotify instances one
> user may hold across all programs (`fs.inotify.max_user_instances`: 1,024 here, 128 by default on
> Debian and Ubuntu), and watchdog spends one instance and two threads on every `schedule()` call.
> Here 149 instances were already held by other programs, which left about 867. On a stock Ubuntu
> machine the wall comes after roughly 100.
>
> So watchdog itself can watch a store. One recursive watch costs one instance however many
> subfolders it has. What fails is the layout of one watch per subfolder. The other problems in
> §8.1 hold for any watchdog layout: silent overflow, reordering between instances, the late
> re-watch after `mode='w'`, and the move bookkeeping that is never freed.
>
> And yes, Trail as a whole hits the same wall today. `desired()` gives every folder of a recursive
> Dir its own watch (`watchdog.py:186-200` and `_subtree` at `:218-230`), so a project whose
> watched tree has more folders than the free instances fails with
> `OSError: inotify instance limit reached` out of `Watchdog.watch`. That bug exists now,
> independent of directory assets.
>
> I recommend using inotify in general on Linux, but not in the same step. Once the reader of PR 8
> works for interiors, Dir watches move onto the same instance, where a folder costs one watch
> (524,288 allowed here) instead of one instance. I suggest a PR 15 for it, so the riskier change
> lands on its own. macOS and Windows have no inotify and no such instance limit, so they keep
> watchdog.

> Okay, we'll continue using watchdog or now and think about inotify later. But self.observer.schedule() really needs recursive=True, right? There doesn't seem to be a case where we don't want recursive. Also, for each watch, to check if any parents are already recursively watching?

### 2.2. Where open sessions live

- **10-7 says:** The open session is `asset.session`.
- **This plan:** Open sessions live in the shift's `Inbox`. `Asset.session` is a read-only
  snapshot.
- **Why:** Counts change off the loop, an `Asset` object is replaced on retrack, and an open
  session belongs to one shift.

> Why use asset.inbox instead of asset.sessions? Seems like an unnecessary new term?

> Why have `Asset.session` as a read-only snapshot? Shouldn't it be updated while it's live? So why do we need an Inbox  the first place? We only have one session--every sub-asset is counted in that session. Why have a `tallies` dict instead of `Session.opened: int`, `Session.modified: int`, etc?

> **Claude:** The plan has no `asset.inbox`. The inbox belongs to the running shift
> (`trail.watchdog.shift.inbox`). Your point still stands: it is a new term for two things that
> already have names. I suggest dropping it.
>
> - `trail.watchdog.sessions` holds the open sessions, one per directory asset being accessed,
>   keyed by asset id.
> - `trail.watchdog.queue` keeps its current name for what waits for the loop: events from
>   watchdog, sealed sessions and lifecycle records, in order.
>
> Both belong to the running shift and are empty while the watchdog is dormant.
>
> **One session per asset, several assets.** Every member of an asset is counted in its one
> session, as you say. The dict is not per member. It holds one entry for each directory asset
> being accessed at the same moment, such as `cube.zarr` and `roads.gdb` together. The thread that
> counts uses it to find the session for an event without touching Trail's `Asset` objects, which
> belong to the loop.
>
> **Fixed counts instead of a Counter.** Agreed. The event types are a closed set, so a session
> gets five int fields, `opened`, `created`, `modified`, `moved` and `deleted`, and
> `DirectoryAccessEvent` gets the same fields in place of `ops`. That also makes
> `events modified>0` work with today's `select`, so PR 13 shrinks to `members` alone. `members`
> stays a mapping from member name to those five counts, since member names are open-ended. The
> class `Tally` becomes `Session`, a name that is free once the watchdog run is called a shift.
>
> **Why a snapshot.** `asset.session` is a property, so every read is current, the way
> `asset.st_size` stats the file on every read. It returns a copy because the live session is
> changed by the counting thread while you hold it. Iterating the live `members` while that thread
> adds a member can raise `RuntimeError: dictionary changed size during iteration`. A copy taken
> under the lock is consistent, and the next read is a fresh copy.

> ok
### 2.3. Counting under a lock

- **10-7 says:** No lock is needed, since `consume` runs only on the loop.
- **This plan:** Events inside a directory asset are counted on the thread that receives them,
  under a `threading.Lock`. Your comment at 10-7:54-57 already suggested a lock.
- **Why:** While a notebook cell blocks the loop, today's handler keeps 612 B per raw event
  (58 MiB per 10⁵ events). Counters on the receiving thread keep 0 B per event.

> I am not sure about the threading lock, do yuo think it is necessary? I just suggested it for he directoryasset incrementor problem, but I believe it was indicated that they don't need a lock? then don't do it

> **Claude:** It is necessary for this design, and I would keep it. The 10-7 reply said no lock
> was needed because there every raw event went to the loop and was counted there, so only one
> thread touched the counts. The measurements ruled that out for notebooks. While a cell runs a
> write, the loop is blocked, so every raw event waits in memory until the cell ends: 612 B each
> with today's handler, about 200 B with the slimmest hand-off. A zarr write produces 3 to 4 events
> per chunk, so a 10⁶-chunk write in one cell would hold roughly 0.7 to 2 GB. That is extrapolated
> from the measured per-event numbers. Counting where the events arrive keeps it at zero, and then
> two threads touch a session: the counting thread, and the loop, which reads `asset.session` and
> takes a session out before an offtrail. That is the case a lock is for. Your instinct at
> 10-7:54-57 was right.
>
> The lock is cheap and narrow. An uncontended `threading.Lock` costs a fraction of a microsecond,
> and the measured count-under-a-lock path was 1.3 to 2.1 µs per event, against about 20 µs per
> event today. It guards only the session table and the queue, never I/O.
>
> The lock-free alternative is for the counting thread to own its sessions alone and for the loop
> to ask it for anything it needs. An offtrail would send "seal this asset" to the reader and wait
> for the answer before logging `RemoveAssetEvent`, and `asset.session` would be a round trip too.
> On macOS and Windows the counting thread is watchdog's dispatcher, which has no timer to seal
> idle sessions, so the loop would have to send those requests as well. That is more machinery
> than one lock, with the same synchronization hidden in a queue. It is workable if you still
> prefer it, and §7 would change to that model.

ok
### 2.4. Interval fields

- **10-7 says:** `timestamp` is when the session opened and `ended` when it closed.
- **This plan:** `timestamp` is the session's last event and `started` its first.
- **Why:** It matches `ExecutionEvent` in `notes/JUPYTER_INTEGRATION.md` (timestamp is the finish,
  with `started`). Sessions are logged when they seal, so the log then stays within `idle_gap` of
  timestamp order instead of within the 60 s session limit.

> Is it really the best decision to have a DurationLimit? What if the asset truly is large with massive chunks?  Is your intent for durationlimit to be a timeout between sub-events, or just a broad time limit for the overall event? What if it's 20 minutes of reading, 5 seconds between chunk?

> **Claude:** There are two limits, and only one of them is a timeout.
>
> - `idle_gap` (1.5 s) is the timeout between events. A gap longer than it ends the session.
> - `longest_session` (60 s) caps the overall session. It never drops anything. An access longer
>   than the cap is logged as consecutive sessions, each at most that long, so a 20-minute write
>   becomes 20 back-to-back records of about 0.5-2 KB each.
>
> The cap exists for three reasons. Open sessions live in memory, so a killed process loses at
> most one cap's worth of record. A notebook kernel killed by OOM in the middle of a large write is
> the common case. The feed shows a long access while it runs instead of only at its end. And
> Jupyter attribution gets bounded intervals.
>
> Your example exposes a real problem, but it is `idle_gap`, not the cap. With 5 s between chunks
> and a 1.5 s gap, every chunk is its own session, so 20 minutes gives about 240 sessions. 1.5 s was
> chosen to exceed watchdog's 0.5 s hold, not from how people work, and compute-bound dask reads
> and writes pause for seconds between chunks. I suggest these defaults instead, both still class
> attributes:
>
> - `idle_gap` of 30 s, so pauses inside one piece of work do not split it. Two accesses less than
>   30 s apart then merge into one session, which still says truthfully that the asset was
>   accessed from `started` to `timestamp`. A session reaches the log 30 s after its last event,
>   and the console header's `sessions N` (PR 12) shows it while it is open.
> - `longest_session` of 10 minutes, so a crash loses at most 10 minutes of record and a 20-minute
>   access is 2 records.
>
> One caveat for reads: until PR 14, chunk reads below the top level of a zarr store are not
> watched at all. A 20-minute read is seen as the opens of the root metadata at its start, so it is
> one short session however long the reading lasts. Validation task V7 settles the defaults on
> real dask workloads.

> I still don't understand why longest_session is necessary in the OOM crash example. If an OOM exists, you would expect the events to stop, so idle_gap would be triggered and the session would be closed.

### 2.5. Closing before a move or deletion

- **10-7 says:** A move or deletion of the asset closes its session first.
- **This plan:** Unchanged on Linux, where kernel order inside one instance makes it exact.
  Elsewhere a per-asset generation drops events that arrive late from a dead watch.
- **Why:** The parent's `DirDeletedEvent(root)` arrived before 511 of 654 inner deletions when they
  came from different watchdog watches.
- **Status:** agreed.

### 2.6. Member keys

- **10-7 says:** `members` counts per top-level entry of the store.
- **This plan:** Each kind of directory asset has a member function: top-level entry for zarr,
  table for gdb, file name for safe. Temporary files are folded away and members are capped.
- **Why:** zarr writes every key through a `.<32 hex>.partial` temp file, which gave 14-25 member
  keys per write session. All band activity of a safe product falls under one top-level entry,
  `GRANULE`.

> So we are going to need to implement custom member functiosn for each type of directory asset? Does that scale? Seems like it will require maintenance, especially when the standard changes e.g. zarr v2 versus v3?  What do we need members for ?

> **Claude:** Members say which parts of a dataset an access touched: which arrays of a zarr group
> were written, which tables of a gdb were read, which bands of a safe product. Without them a
> session says only that `cube.zarr` was modified. That is already the core fact of 10-7, so
> members are useful detail, not a requirement.
>
> The maintenance worry is fair, but it falls mostly on another part. The member rules do not
> depend on format versions. zarr v2 and v3 both keep each array in its own top-level directory
> (`temp/0.0` in v2, `temp/c/0/0` in v3), so the top-level entry works for both, and the traces
> showed no difference. What does change between versions is the temporary file names. zarr 3.0 to
> 3.1.2 write in place, while zarr 2 and zarr 3.1.3 or later write `.partial` files and rename them.
> Those patterns are needed even without members. Without them, a failed exclusive write shows up
> as `deleted`, the two kinds of zarr writer give different counts for the same write, and an
> ArcGIS read of a gdb becomes `modified` because of its lock files.
>
> To keep the maintenance down, I suggest one member rule for everything, the top-level entry,
> with one exception for safe, whose top-level entry is always `GRANULE`, so there it is the file
> name. A gdb then shows `a00000009.gdbtable` and `a00000009.gdbtablx` as separate members instead
> of one table. That is a little noisier but needs no gdb-specific code. A directory type nobody
> wrote a rule for gets the top-level entry and the zarr temp pattern, so a new format needs no
> code unless its temporary files differ. If you would rather drop members entirely, the five
> counts alone still answer the 10-7 question, and `Profile.member` and the member cap go away.

> okay, but what exactly is Profile? Is there a better name for members? Would subasset be better than member? Seems more obvious. What is a Profile? Doesn't seem like
### 2.7. Opens

- **10-7 says:** Opens are watched at the top level only.
- **This plan:** Kept for zarr, gdb and directories tracked with `track DIR`. A safe product
  watches opens at every depth. Opens below the top level for the rest wait for a decision
  (PR 14).
- **Why:** Top-level opens catch every root-based zarr open and every gdb read. They miss deferred
  dask compute, `zarr.open_array(store / 'temp')` and rasterio band reads of a safe product, which
  open only files at depth 5.

> Yes, so we will use inotify threads like previously discussed?

> **Claude:** Yes, with one thread in total, not one per asset. On Linux, one thread reads one
> inotify instance that watches every directory asset (§8), and watchdog is not used inside
> directory assets. With this decision the reader arms opens on the root of a zarr, gdb or
> `track DIR` asset, and on every folder of a safe product. macOS and Windows have no inotify, so
> there one recursive watchdog watch per asset counts writes, and reads are not seen (§10).

### 2.8. Closing sessions at stop

- **10-7 says:** Stopping the watchdog closes every open session, found by iterating
  `trail.assets`.
- **This plan:** `stop()` seals every tally in the `Inbox`.
- **Why:** The open tallies are already indexed there.

> yes, but why have an inbox?  Where is the inbox stored? `Trail.watchdog.inbox`? Can we do `Trail.watchdog.sessions` instead?

> **Claude:** It lives on the shift, at `trail.watchdog.shift.inbox`. `start()` makes it and
> `stop()` drops it. Yes to `trail.watchdog.sessions`. The reply under §2.2 splits the inbox into
> `trail.watchdog.sessions` and `trail.watchdog.queue` and drops the term.

### 2.9. Region writes today

- **10-7 says:** (§1) A region write records nothing.
- **Measured at 9fe304d:** A region write is recorded as an `opened` event, a write logged as a
  read. Its only top-level event is the open of the root metadata (zarr 3.4).
- **This plan:** A region write is `modified`, once PR 8 watches below the top level. §5.1 shows
  the record it should produce.

> Your plan is for a regino write to be recorded as an opened event? Shouldn't it be a modified event?

> **Claude:** No, it is `modified` under the plan. 2.9 describes today's code, and its wording made
> it read like the plan, so I reworded it above. Today only the top level of a store is watched. A
> region write's only top-level event is the open of `zarr.json`, and the per-batch fold logs a
> batch of only opens as `opened`, so today the write is logged as a read. Under the plan the
> reader counts each chunk written below the top level. §5.1 shows that record, with 72 chunks
> `modified`.

### 2.10. Unchanged

Unchanged from 10-7: one `DirectoryAccessEvent` per session, no child events, `event_type` is
_`opened` or `modified`, chunk events are never logged, and the caller of an access is out of scope_.

## 3. Evidence

### 3.1. Measured

| Finding | Number |
|---|---|
| Cost of one watchdog watch, recursive or not | 1 inotify instance, 2 threads, 3 fds, about 41 KiB and 1.5 ms of caller time |
| Per-user inotify instances | 1,024 here, 149 already used by other programs. Debian and Ubuntu default to 128 |
| Per-subfolder layout, writes into new chunk folders | 0-18% seen. One recursive watch per asset saw 94-100% |
| A recursive `schedule()` | walks the tree on the calling thread at 7-20 µs per directory, holding the observer lock, which stalls dispatch for every other watch (206 ms at 10⁴ dirs) |
| watchdog drops `IN_Q_OVERFLOW` | silently, at `inotify_c.py:335-336` (`if wd == -1: continue`) |
| Loss under heavy writes, with no signal | 18-47% from one external writer, 74-89% from four writer threads in the same process |
| After an overflow during directory creation | 21.7% of new chunk directories stayed unwatched |
| watchdog's move bookkeeping | about 0.4 KB kept forever per temp-plus-rename write (`Inotify._moved_from_events` is never cleared) |
| An unmatched `IN_MOVED_FROM` | holds every later event of that watch 0.5 s (`InotifyBuffer.delay`, head-of-line) |
| watchdog's own pipeline | 7-8 µs CPU per delivered event |
| Today's `Handler.on_any_event` | 3.8 µs to build a `WatchdogEvent` (uuid4, datetime, `normalize_id`), and 2.2-7.6 µs per `call_soon_threadsafe` |
| A deque with one wakeup per burst | 0.07-0.87 µs per event, no context switches |
| `Watchdog._enclosing` | 13.3 µs per chunk event five levels deep |
| A 40,000-chunk zarr write watched in its own process | 29.2 s unwatched, 40.0-42.7 s watched by watchdog |
| A full read of the same store | +20% with opens watched below the top level, +0% without |
| `to_zarr(mode='w')` | deletes and recreates the store root, in every zarr version tested |
| zarr 2 and zarr ≥ 3.1.3 writes | every key goes to `<name>.<32 hex>.partial` and is renamed into place. zarr 3 replaces the suffix, so `zarr.json` is written as `zarr.<hex>.partial`. zarr 3.0 to 3.1.2 write in place |
| gdb | flat, 24-27 files. A layer read opens 6-8 files at the top level. REPACK writes `<table>._compress.gdbtable` and renames it over the table |
| safe | rasterio band reads open only `GRANULE/<g>/IMG_DATA/R10m/*.jp2` at depth 5. `gdalinfo -stats` writes `*.aux.xml` sidecars |
| Separate watches reorder events | the parent's `DirDeletedEvent(root)` came before 511 of 654 inner deletions |
| One inotify instance that also watches the root's parent | all 2,053 inner deletions came before the parent's `DELETE(root)`. A deleted and recreated root was watched again 0.06-0.29 ms after `mkdir`, and 505 of 505 rewritten files were counted. Root renames within a parent and between two watched parents paired by cookie, with no re-walk |
| Re-adding a watched directory | returns the same wd and replaces its mask. A directory open arrives as `IN_OPEN \| IN_ISDIR` |
| A 4,000-dir, 40,000-file overwrite through one instance | overflowed in 2 of 2 runs and lost the parent's `DELETE(root)` and `CREATE(root)` |
| A directory walk over an interior with opens armed on every wd | queues 2N-1 `IN_OPEN \| IN_ISDIR` events and overflows by itself at 10,102 dirs |
| A by-path `inotify_add_watch` after an ancestor rename | fails with ENOENT. Through `/proc/self/fd/<O_PATH root fd>/<relative>` it returns the same wd |
| Rename pairs split across reads | the second half was always in the very next read (17 of 20,000 pairs with one writer, 143 of 80,000 with four) |
| Today's code across two `asyncio.run` calls without `stop()` | one append logged twice, with 10 watchdog threads alive |
| A `consume` after the consumer finished | started a new watchdog run that outlived `clear()` |
| The inbox lock held across `observer.schedule()` | deadlock with the dispatcher thread |
| `Watch.notify` from `atexit` after the loop closed | raised after the first append, so the rest were never logged |
| A non-thread-reading seam | one non-blocking read right after the syscall returned the full event sequence in 30,000 of 30,000 rounds |
| A `DirectoryAccessEvent` record | about 500 B with 2 members, about 2.2 KB with 64 |
| `Asset.fingerprint` | 132 ms per 10⁵ files, and `st_size` plus `st_mtime` walk the store twice |

### 3.2. Assumed

- ArcGIS creates `*.sr.lock`, `*.rd.lock`, `*.wr.lock` and `*.ed.lock` files in a gdb, even for a
  read. This is from Esri's documentation. GDAL created none in any run.
- The cost per event of a Python reader thread on one inotify instance is below watchdog's 7-8 µs,
  because it skips watchdog's three threads and its per-event objects. The spike that measured it
  was lost before its report was written (V1 in §21).
- `/proc/self/fd/N/<relative>` resolving through an `O_PATH` fd works on every Linux kernel Trail
  supports. It was run on 6.12 only.
- macOS FSEvents and Windows `ReadDirectoryChangesW` deliver a store's subtree cheaply to one
  recursive watchdog watch. That is from reading watchdog's backends, not from running them.

## 4. Overview

```
 observer thread (watchdog)          reader thread (Linux)                 asyncio loop
 Dirs, file assets, parents          every interior and root parent
 ─────────────────────────           ─────────────────────────────         ──────────────────────
 Handler.on_any_event                pump(): read inotify records
   Arrival(at, event) ──put──┐         count into Tally ──count──┐
                             │         Lifecycle (after seal) ─put┤
                             ▼                                    ▼
                        ┌──────────────── Inbox (one per shift, one lock) ────────────────┐
                        │ items: Arrival | Lifecycle | Tally | Notice | STOP, in order     │
                        │ tallies: open sessions by asset id                               │
                        │ generations, root inodes                                         │
                        └──────────────────────────────┬──────────────────────────────────┘
                                                       │ one wakeup per burst
                                                       ▼
                                            Watchdog.apply → consume(inbox)
                                              Tally → DirectoryAccessEvent → log
                                              Lifecycle → WatchdogEvent moved/deleted/created
                                              Arrival → WatchdogEvent, as today
```

- Every event inside a directory asset is counted where it arrives. Nothing per event is queued,
  built or logged for it.
- The producer splits sessions at event time. An event more than `idle_gap` after the tally's last
  event, or more than `longest_session` after its first, seals the old tally and starts a new one.
  The last tally of a burst is sealed by a deadline, in the reader's poll timeout on Linux and in
  the loop's wait elsewhere.
- The loop turns each sealed tally into one `DirectoryAccessEvent` and logs it. A lifecycle record
  for the same asset always comes after its tally in the `Inbox`, so the session is logged before
  the move or deletion.

## 5. Event model

### 5.1. `DirectoryAccessEvent`

A subclass of `WatchdogEvent` in `event.py`, so `Event.classes` holds it before `JSONL.read` runs in
`Trail.__init__` (`trail.py:262`).

| Field | Type | Meaning |
|---|---|---|
| `timestamp` | `datetime` | the session's last observed event, in UTC |
| `started` | `datetime`, required | its first observed event |
| `src_path` | `str` | the asset's path while the session was open |
| `event_type` | `str` | `opened` when every counted event was an open, `modified` otherwise |
| `is_directory` | `bool`, always True, not an init argument | as for any event about a directory asset |
| `is_synthetic` | `bool`, always True, not an init argument | inferred from many filesystem events |
| `is_lossy` | `bool` | some events were not observed: an overflow, a budget, or a watch placed late |
| `ops` | `Counter[str]` | observed events by type: `opened`, `created`, `modified`, `moved`, `deleted` |
| `members` | `dict[str, Counter[str]]` | the same counts per member, for at most 64 members |

> Why not `count` instead of `ops`? Seems more obvious. Something like Event.count\["modified"\] instead of
> Event.ops\[ "modified"\] reads better

> What for keep Event.members

A zarr 3.4 region write of 72 chunks should log this, once temp files are folded (V4 in §21):

```json
{"id": "6b1f…", "timestamp": "2026-10-08T17:02:11.204000+00:00", "src_path": "/home/user/project/cube.zarr",
 "dest_path": "", "event_type": "modified", "is_directory": true, "is_synthetic": true,
 "started": "2026-10-08T17:02:10.962000+00:00", "is_lossy": false,
 "ops": {"opened": 1, "modified": 72},
 "members": {"temp": {"modified": 72}, "zarr.json": {"opened": 1}},
 "cls": "DirectoryAccessEvent", "entry": "4e26…"}
```

- **Counts are observations, not operations.** The kernel merges identical unread events, and a
  read below the top level is not watched. 1,000 `write()` calls on one fd gave 206 `modified`
  events. Counts are for magnitude and for which members were touched. They are never chunk
  counts, and tests never assert exact values from real inotify.
- **Writes count on close.** Inside an interior, `IN_CLOSE_WRITE` is counted as `modified`, not
  `IN_MODIFY`. That is one event per file written instead of one per `write()` call (50 instead
  of 465 measured). Writes through `mmap` and a `truncate` without a close are not seen.
- **`members` loses nothing from `ops`.** Past 64 members a new member is not added, and its events
  still count in `ops`. No `<others>` key is stored, since the remainder is `ops` minus the members.
> I have no idea what your'e talking about. Past 64 members a new member is not added?  What? Are we keeping instances
> for the members inside each dir asset? Let's not. No point if it's not going to scale, and minimal use for seeing the obvious "oh yes dir.zarr/file is a member of dir.zarr"

- **Old records stay as they are.** The collapsed `WatchdogEvent(is_directory=True,
  is_synthetic=True)` records already in logs replay as no-ops (`event.py:378-387`), so no
  migration is needed, in line with the decision to skip back-compat while Trail is early.

### 5.2. Record, replay and repr

- `Event.moments: ClassVar[tuple[str, ...]] = ("timestamp",)` names the fields held as datetimes.
  `DirectoryAccessEvent` sets `("timestamp", "started")`. One ClassVar drives `to_record` (ISO
  format), `from_record` (parsed back) and `_repr_items` (the clock format). Today only
  `timestamp` is converted (`event.py:127`, `:158`), so a second datetime field makes `json.dumps`
  raise inside `Events.__setitem__`, which would kill the consumer.
> WHat does this achieve? Why do we need moments?

- `DirectoryAccessEvent.__post_init__` turns `ops` and `members` back into `Counter`s after a
  replay, and calls `WatchdogEvent.__post_init__(self)` explicitly. Zero-argument `super()` fails
  in a `slots=True` dataclass on Python 3.12.

> Seems pointless? I think to said use count: dict ? Is this necessary because of the thread lock requirement? Where
> could we put the thread lock?

- The repr shows `started` in the timestamp's clock format, `ops: opened 1, modified 72` in a
  fixed type order, `members: temp 72, zarr.json 1` (the five busiest, then `<n others>`), and
  `is_lossy` only when it is True.
- `DirectoryAccessEvent.apply` logs the event when it is live and only returns its asset on
  replay. A session changes nothing about the asset.
- The class docstring gets its example repr pasted from a real test run, never written by hand.

### 5.3. Parents, children and containment

- **No child events.** As settled in 10-7, a `DirectoryAccessEvent` is the only record of what
  happened inside an asset. Raw events are counted and dropped. The deferred design in 10-7 §2.1
  stays deferred. If a trace of raw events is ever needed for debugging, it goes to a separate,
  opt-in file outside `events.jsonl`, never into the log (§23).
- **A session's parent is its asset.** `entry` is the directory asset, by id. A session never
  points at another event. Ordering carries the relations that matter: a session is logged
  before the `moved` or `deleted` event that ended it, and after the `AddAssetEvent` that started
  tracking.
- **Members are names, not entries.** A member has no id and no events of its own. If
  `Asset.members` later means member assets with ids, the field is renamed then.
> Seems like a pointless new term, how about just `Asset.children: set[str]` for the glob patterns. I don't think we
> need to have a container for the possibly thousands of chunks for a zarr
- **A directory asset contains no entries.** Nothing inside it is tracked (gap 4, §11), and no Dir
  is named inside it. Its interior is watched only by `Interiors`, so one event is never counted
  twice.
- **Jupyter attribution** becomes an overlap between `[started, timestamp]` and a cell's
  execution window. One session can span several cells. Sealing at cell boundaries is deferred
  (§23).

### 5.4. Listings and `select`

- `cls=DirectoryAccessEvent`, `cls=WatchdogEvent`, `event_type=opened` and `started>=…` already
  work through `Select` (`select.py:238-299`).
- `EventsCommand.fields` (`cli/commands.py:356-366`) gains `started`, `ops`, `members` and
  `is_lossy` for completion.
- PR 13 (optional) adds dotted fields that step through mappings (`ops.modified>0`), and
  membership for mapping values (`members=temp`). Today `ops=modified` matches nothing and
  `ops>1` raises TypeError. The change touches the grammar of every listing, so it is its own PR.

## 6. Sessions

### 6.1. Lifecycle of a session

1. The first counted event of an asset opens a `Tally` with `first` and `last` set to its time.
2. Each later event updates `last` and the counts.
3. An event more than `idle_gap` after `last`, or more than `longest_session` after `first`,
   seals the tally and opens a new one. A deadline (`last + idle_gap`) seals the last tally of a
   burst.
4. A sealed tally is queued in the `Inbox`. The loop pops it, builds the `DirectoryAccessEvent` and
   logs it.
5. Sealing happens in kernel order on Linux, so a lifecycle record of the same asset is always
   queued after the tally it ended.

`idle_gap` is 1.5 s and `longest_session` 60 s, both class attributes of `Watchdog`, in seconds,
like `debounce`. `idle_gap` must exceed watchdog's 0.5 s head-of-line hold plus the 0.1 s debounce,
since an asset's events can wait behind it on the fallback path. Inside one operation the largest
measured gap between events was 22 ms.

### 6.2. When a session closes

| Trigger | Where | Order in the log |
|---|---|---|
| idle gap or longest session | the producer, or a deadline | session |
| root deleted | reader, parent wd `DELETE` | session, then `deleted` |
| root recreated (zarr `mode='w'`) | reader, parent wd `CREATE` | session, `deleted`, `created`, then the rewrite's session |
| root moved | reader, paired move or a moved root fd | session, then `moved` |
| an ancestor Dir renamed | `Trail._moved`, before repathing | session at the old path, then `moved` |
| offtrail, and an occupant offtrailed by `Entry.track` or `Asset.move` | `Asset.offtrail`, which calls `Watchdog.release` | session, then `RemoveAssetEvent` |
| `Trail.clear` | through offtrail | sessions, then the removals, then the cleared log |
| `events clear` | `Events.clear` discards tallies | none |
| `stop()` | seals every tally | sessions, logged by the consumer before it returns |
| the consumer cancelled by `asyncio.run` teardown | `apply`'s `finally` | sessions |
| interpreter exit without `stop()` | the exit hook | sessions, through one `Events.extend` |

### 6.3. Clocks

Producers capture both clocks once per batch of events. Wall-clock time (`time.time()`) becomes
`started` and `timestamp`. Monotonic time (`time.monotonic()`) drives `idle_gap`, `longest_session`
and every deadline, so a clock step cannot split or merge sessions. A session that spans a laptop
suspend may merge two accesses, since the monotonic clock stops during suspend on Linux. That is
accepted.

### 6.4. Profiles

`util.py` holds one `Profile` per container suffix, and `CONTAINERS` becomes `frozenset(PROFILES)`.

| Suffix | Member | Transient names | Opens watched |
|---|---|---|---|
| `.zarr`, `.n5` | top-level entry: an array, or a group in a DataTree store | `\.[0-9a-f]{32}\.partial$` | top level |
| `.gdb` | table, the file name up to its first `.` | `\._compress\.`, `\.(sr\|rd\|wr\|ed)\.lock$` | top level, which is everything |
| `.safe` | file name, such as a band | `\.aux\.xml$` | every depth |
| `track DIR` | top-level entry | the zarr pattern | top level. Hidden directories are not watched |

- Events on a transient name are dropped. A rename from a transient name to a real one counts as
  `modified` on the destination's member, so both zarr writers give `modified` per chunk. Failed
  exclusive writes, which create and delete a temp file at the top level, then leave no trace.
- Lock files and `.aux.xml` sidecars are written by reads. Counting them would turn a read
  session into `modified`.
- A safe product has about 14 directories and 50 files, so watching opens at every depth costs a
  few events per read and catches band reads.
- Any other directory asset uses the `track DIR` profile. It skips hidden directories, as
  `Assets._descends` does (`asset.py:323-330`), so `.git` and `.venv` never use the budget.

> This seems like alot of new terms just to support directory assets.  Can we get more reuse of previous names?
> Instead of Profile, maybe Container?  Instead of putting it in util, maybe create a container.py, and then we have:
> container.Container, and container.Member?  contain all the information in the definitiosn in this module
> Is it really
> necessary to keep transient names for each extension or can we just treat all subassets the same? Probably not,
> because we don't want tempfiles to count towards the open/delete/modified? We don't need to determine the depth of
> opens watched for each "profile", do we? just do a recursive=True watch and filter based on transient names

> necessary to track "transients"? Why not just select on detected member names, rather than filter on transient names?

> Even more simply: instead of this staying in util, we have the `DirAsset` itself access `DirAsset.children: set[str | None]`
> which returns from some sort of global mapping. Use `None` to support extensionless chunks.
> No aditional classes needed, just add a @property to DirAsset to determine which pattern matches are supported. (
> returns indexed dict elsewhere )


## 7. Threads and the hand-off

### 7.1. The shift

`Watchdog._start_shift` builds one `Shift`. It holds the loop, the `Inbox`, the watchdog
`Observer` and its `Handler`, the folder watches, `Interiors` and the consumer task. Every producer
holds its shift, never the `Watchdog`.

- A shift left running by an `asyncio.run` that ended without `stop()` is halted before a new one
  starts. Today the next run double-logs every event (measured). `stop()` on such a dead shift
  halts its threads instead of only clearing references.
- `Shift.is_closing` makes `ensure`, `watch`, `cover` and `reconcile` no-ops. The final consume
  runs inside the consumer task before it returns, so nothing after `stop()` can start a new
  shift.
- The consumer receives its shift as an argument when the task is created, not by reading
  `self.queue` on its first step (`watchdog.py:372`).

>  How about Session instead of Shift, and Access instead of dir asset's "Session"? ANd instead of Inbox, we'll do `watchdog.sessions`.
> How about using a descriptor with a custom getter for Shift, which initializes these attributes,
> and a custom deleter to perform cleanup? performed via `del watchdog.shift`?
>

### 7.2. The inbox

- **One lock, O(1) under it.** The lock guards the item deque, the open tallies, the generations
  and the root inodes. No I/O, logging, walks, `inotify_add_watch`, watchdog `schedule()`,
  `unschedule()` or `stop()`, and no thread joins run while it is held. The lock order is
  watchdog's observer lock, then the inbox lock. Holding the inbox lock across `schedule()`
  deadlocked against the dispatcher (measured).
- **One wakeup per burst.** A producer appends and schedules a wakeup only when none is pending.
  The loop clears its `asyncio.Event`, then pops items one at a time. `is_wake_pending` is cleared
  only in the same critical section that finds the deque empty. Clearing it separately stranded
  the last item under a forced interleaving (measured).
- **Generations.** `Inbox.cover(asset)` gives the asset a new generation. Producers pass the
  generation they were given with every count. `Inbox.retire(asset)` seals the asset's tally,
  takes its queued tallies out and retires the generation in one critical section. Events of a
  retired generation are dropped. This is what stops a session being logged after
  `RemoveAssetEvent`, and late events from a dead watch opening a new one.
- **Root inodes.** `(st_dev, st_ino)` of each covered root, written by the reader at cover time.
  The loop reads it to drop watchdog's own reports about covered roots (§9.2).
- **Built eagerly.** The `Inbox` is made in `_start_shift` and handed to the handler and the
  reader. A lazily built one can be built twice by two threads, since `cached_property` takes no
  lock on 3.12 (measured).

> Why not `watchdog.session.accesses`
>
> Instead of cover, retire, etc. can we just implement a UserDict\[asset_id, Access\] ?  Contains the lock, mimicks dict
> for adding/removing accesses
> asset.access will return watchdog.session.accesses\[asset.id\] and creates a new Access if it doesn't exist.

>
> Session.count:dict[str, int]

### 7.3. Events from watchdog

`Handler.on_any_event` appends `Arrival(at=time.time(), event=event)` and returns. It catches every
exception, since one exception kills watchdog's only dispatcher thread, which serves every watch.

This answers the open comment at 10-7:72-73. Neither tuples, dicts nor `__slots__` objects:
watchdog has already built an immutable `FileSystemEvent` whose paths are `str`, because Trail
schedules `str` paths, so passing it costs 0.02 µs. A tuple costs 0.16 µs, a `__slots__` object
0.27 µs, and a dict 0.23 µs with 2.3 times a tuple's memory. `WatchdogEvent` is already
`slots=True` (`event.py:343`), so `__slots__` gains nothing. What costs is building a
`WatchdogEvent` per raw event (3.8 µs) and one `call_soon_threadsafe` per event (2.2-7.6 µs). The
fix is the wakeup per burst, the counting inside directory assets, and building `WatchdogEvent`
only on the loop, stamped with `Arrival.at`. `consume` also skips an open, modification or deletion
of a path no entry holds before building anything.

> ok

### 7.4. Stop, cancellation, exit and fork

`stop()` does this, in order:

1. Set `is_closing`.
2. Wait until 0.55 s after the last `Arrival`, then poll `observer.event_queue.unfinished_tasks`
   while the dispatcher is alive, for at most 1 s. `observer.stop()` discards whatever is still
   queued (measured), and an unmatched move-out waits 0.5 s in watchdog's `DelayedQueue` while
   `unfinished_tasks` is already 0.
3. Run `observer.stop()`, its join, and the reader's halt through `asyncio.to_thread`. The reader
   drains its fd, resolves held move halves, seals its tallies and exits.
4. Seal every tally, queue `STOP`, and await the consumer, which logs everything and returns.
5. Clear the shift and unregister the exit hook.

- `apply` has a `finally` that logs sealed tallies when `asyncio.run` cancels it, as happens to a
  script that relied on `ensure()` and never stopped.
- The exit hook is registered once per shift through a weakref. It seals the tallies and logs them
  with one `Events.extend`, guarding each step, and never consumes anything else. It checks
  `os.getpid()` against the process that made the shift, and `os.register_at_fork` gives a forked
  child an empty inbox with a new lock.
- `Watch.notify` becomes thread-aware. It keeps each waiter's loop, calls
  `loop.call_soon_threadsafe(waiter.set)` off that loop, drops waiters of closed loops and never
  raises. Today a notify from `atexit` after the loop closed stopped the logging after one session.
- The reader is a daemon thread. Python joins non-daemon threads before `atexit` runs, so a
  non-daemon reader hangs the exit (measured).
- The reader posts warnings and failures to the inbox as a `Notice`, and the loop raises them with
  `warnings.warn`. The console's warning capture is process-wide, so a warning raised on the
  reader thread would land on whatever command happens to be running (`cli/console.py:279-280`).

## 8. Interiors on Linux

> Just ignore inotify stuff for now

### 8.1. Why a reader of Trail's own

| | watchdog, one watch per subfolder (10-7 §3.2) | watchdog, one recursive watch per asset, plus a top-level opens watch | Trail-owned reader, one instance |
|---|---|---|---|
| inotify instances and threads | 1 and 2 per subfolder | 2 and 4 per asset | 1 and 1 in total |
| writes into new chunk folders | 0-18% | 94-100% | 100% in the overwrite case (505 of 505) |
| root deleted and recreated (`mode='w'`) | the watch dies with the root | re-placed by the loop after the batch, 53-63% of the rewrite seen, and nothing while a notebook cell blocks the loop | re-covered by the reader in 0.06-0.29 ms, also while the loop is blocked |
| order of the root's deletion against inner events | not kept across instances | not kept (511 of 654 inner deletions late) | kernel order |
| overflow | silent | silent | detected, and every covered root reconciled by inode |
| renames of the root | a stale path on every later event | the same | paired by cookie, or followed through the root's fd |
| per-mask control | one mask per instance | opens only through a second instance | a mask per wd |
| move bookkeeping | 0.4 KB kept per temp-plus-rename write | the same | released when the pair resolves |
| private API | none | none, or patched private hooks for overflow and the leak | none, but Linux-only code Trail must test |

The middle column is the simplest design that still sees writes. It remains the fallback (§10),
and on Linux it is a valid stopping point if PR 8 proves too costly to maintain. Its limits are
the ones in the table.

> Ignore for now: supporting windows/mac is more important than optimizing linux

### 8.2. Layout and masks

- One `inotify_init1(IN_NONBLOCK | IN_CLOEXEC)` instance per shift, read by one daemon thread,
  `trail-interiors`. A `socket.socketpair` wakes it, with a closed flag checked under a lock. A
  raw pipe fd written after it was closed once put a NUL byte into `events.jsonl` (measured).
  Commands (`Cover`, `Uncover`, `Halt`) arrive through a `queue.SimpleQueue`.
- Each covered asset holds an `O_PATH | O_DIRECTORY` fd on its root and one on its parent. Every
  `inotify_add_watch` and every walk goes through `/proc/self/fd/<fd>/<relative>`, with the root
  itself as `/proc/self/fd/<fd>/.`. An ancestor rename then never breaks an add (measured).
  Paths in records come from the loop's `asset.path`, or from `readlink` of the root fd for a move.
- `IN_MASK_CREATE` on the first add of a root makes a second cover of the same inode fail with
  EEXIST.

| wd | Mask |
|---|---|
| root | `IN_CREATE \| IN_DELETE \| IN_MOVED_FROM \| IN_MOVED_TO \| IN_CLOSE_WRITE \| IN_OPEN \| IN_DELETE_SELF \| IN_MOVE_SELF \| IN_ONLYDIR \| IN_DONT_FOLLOW \| IN_EXCL_UNLINK` |
| below the root | the same without `IN_OPEN`, or with it when the profile watches opens at every depth |
| root's parent | `IN_CREATE \| IN_DELETE \| IN_MOVED_FROM \| IN_MOVED_TO \| IN_ONLYDIR \| IN_DONT_FOLLOW`, filtered to the covered root names |

### 8.3. Records and actions

| Record | Action |
|---|---|
| `IN_Q_OVERFLOW` (wd -1) | §8.5 |
| `IN_IGNORED` | forget the wd. wd state otherwise lives until `IN_IGNORED`, and events for unknown or removing wds are dropped. wds are not reused within an instance (measured) |
| parent `DELETE \| ISDIR` of a covered name | seal the tally, put `Lifecycle(deleted)`, close the root fd, keep watching the parent for the name |
| parent `CREATE \| ISDIR` of a covered name | open a new root fd, record the inode, walk and watch the new root, put `Lifecycle(created)`. Files found by the walk count as `created` |
| parent `MOVED_FROM` and `MOVED_TO` with one cookie | seal, then put `Lifecycle(moved)` with the destination from `readlink`. Re-key the name under the destination's parent |
| parent `MOVED_FROM` unpaired after the next read or 50 ms | `readlink` the root fd. A path means the root moved out of view: seal, put `Lifecycle(moved)`, and watch the new parent. `(deleted)` means it is gone: seal and put `Lifecycle(deleted)` |
| root `DELETE_SELF`, `MOVE_SELF` | release wd state only. Lifecycle comes from the parent alone, since an fd on the old root (a cwd, a scandir iterator, or Trail's own `O_PATH` fd) delays these past the recreation (measured) |
| `CREATE \| ISDIR` inside | add a watch for the new directory and walk it, adding nested directories and counting files found as `created`. An add that fails with ENOENT marks the parent for a rescan at the end of the batch |
| `MOVED_FROM`/`MOVED_TO \| ISDIR` paired inside | rewrite the relative paths of that subtree's wds and count one `moved`. watchdog's synthetic event per descendant does not exist here |
| `MOVED_TO \| ISDIR` unpaired | a directory moved in from outside: walk it, watch it, count its files as `created`. watchdog never watches these (measured) |
| `MOVED_FROM \| ISDIR` unpaired after the hold | moved out: `rm_watch` its subtree, mark those wds removing, count one `deleted` |
| file `OPEN` | `opened`. Opens of directories (`ISDIR`) are ignored. They come from listings, including Trail's own walks |
| file `CLOSE_WRITE` | `modified` |
| file `CREATE`, `DELETE` | `created`, `deleted` |
| file `MOVED_FROM`/`MOVED_TO` | paired: classify through the profile. Unpaired: `deleted` or `created` |

Pairs match by cookie, never by adjacency. 3,722 of 80,000 pairs had other events between their
halves (measured).

### 8.4. Walks and budgets

- A walk is a breadth-first queue per asset, advanced in steps of at most 500 directories between
  reads of the fd, so the reader keeps draining the kernel queue while it walks. With opens armed
  on the root wd only, a walk queues at most one `IN_OPEN | IN_ISDIR` per top-level entry.
- Each asset may hold `interior_limit` watched directories (10,000) and the reader `watch_budget`
  in total (50,000), both class attributes. Breadth-first order watches the shallow levels first.
  An asset past its budget, or an `ENOSPC` from the kernel, marks the asset partial, so every
  session of it is lossy, and posts one `Notice`. `max_user_watches` is shared with IDEs. 60,776
  of 524,288 were already in use on this machine.
- `Asset.fingerprint` and Trail's scans only list directories and stat files, so they open no
  files and start no session. They still queue `IN_OPEN | IN_ISDIR` on wds with opens armed, which
  is why opens below the top level stay off (PR 14).

### 8.5. Overflow

1. Mark every open tally lossy.
2. Reconcile every covered root. `os.stat(name, dir_fd=parent_fd, follow_symlinks=False)` against
   the inode recorded at cover. Missing means `Lifecycle(deleted)`. A changed inode means
   `Lifecycle(deleted)`, `Lifecycle(created)` and a new cover. This is the case where a large
   `mode='w'` overwrite lost the parent's records to the overflow (measured).
3. Once the burst quiets, which is when the asset's tally seals, walk each remaining interior once
   to add the watches the overflow cost. One rewalk per quiet period, never one per overflow, so a
   rewalk cannot feed the next overflow.

### 8.6. The pump seam

The thread's loop body is `Reader.pump(timeout)`: poll, run commands, read and handle records,
advance walks, seal tallies past their deadline, resolve held move halves. Tests call `pump(0)`
right after a filesystem action with no thread running. One non-blocking read right after the
syscall returned the full event sequence in 30,000 of 30,000 rounds (measured).

## 9. Lifecycle and moves

### 9.1. A store's own lifecycle

| What happens | What is logged |
|---|---|
| `rm -rf cube.zarr` | the session with the inner deletions, then `deleted`. The asset stays tracked and missing, as today |
| `to_zarr(mode='w')` | the session with the deletions, `deleted`, `created` (the same id, since the asset is still tracked at its path), then a session with the rewrite |
| the store created while Trail runs | the `AddAssetEvent` from the scan, then sessions once the reader covers it. Writes between the scan's baseline walk and the cover are not counted, so that first session is lossy |
| a store copied in, or moved in from an unwatched place | the general watch's `DirCreatedEvent` leads to a scan, an `AddAssetEvent` with a new id, and a cover |

> Why are we using `Store`? Let's just continue with the cls name  `DirAsset` for now on: dir-asset, or dirasset as a word.

### 9.2. A store moved

| Move | Handling | Identity |
|---|---|---|
| within its parent | the reader pairs the parent's `MOVED_FROM` and `MOVED_TO` | kept |
| between two parents the reader watches | paired by cookie inside the one instance | kept |
| to any other directory on the same filesystem | unpaired `MOVED_FROM`, then `readlink` of the root fd gives the new path | kept |
| to another filesystem (a copy and a delete) | `readlink` says deleted. The copy is a new directory | new id |
| an ancestor Dir renamed | watchdog's `DirMovedEvent` reaches `Trail._moved`, which seals and logs the store's session, then repaths it. The reader's fds follow the inodes, so nothing breaks | kept |

watchdog's general watches see the same moves, so `consume` drops a watchdog directory event when
its path is a covered asset's current path, or when its destination stats to a covered root inode
in `Inbox.inodes`. Without that, a watchdog `DirCreatedEvent(dest)` that wins the race tracks the
store a second time under a new id, and `Asset.move` later offtrails that copy without logging it
(measured on today's `consume`).

> How do you plan to do this with just watchdog ?

### 9.3. Moves inside a store

- A temp file renamed into place is a write (§6.4).
- A directory renamed inside the store keeps its wds and counts one `moved`.
- A directory moved in is walked and watched. A directory moved out is unwatched.
- A file asset moved into a store is reported by watchdog's watch of its old directory as
  `deleted`, after the 0.5 s hold, and by the reader as a `created` inside the store. That is the
  same identity fork any file asset gets when it moves between directories watched by separate
  watchdog watches (§20).



### 9.4. Dirs and file assets

Unchanged. Their watches stay with watchdog, one non-recursive watch per directory, which keeps
`.git` and `.venv` out. §20 records what that costs.

## 10. Other platforms and the Linux fallback

> ignore for now, we're only using watchdog

- On macOS and Windows, `WatchdogInteriors` schedules one recursive watchdog watch per directory
  asset with a structural filter: created, modified, deleted and moved, for files and directories.
  FSEvents and `ReadDirectoryChangesW` are recursive natively, so scheduling walks nothing
  (assumed). The watch's handler is bound to one asset and one generation, drops watchdog's
  synthetic events and events about the root itself, and counts into the same `Inbox` through the
  same `Profile`.
- No backend other than inotify ever reports opens, so on these platforms reads leave no session.
  Writes, creations, deletions and moves do.
- The root's lifecycle comes from the general watch of its parent. On a deletion the loop retires
  the generation after logging the session, so inner events arriving late from the dying watch
  are dropped. On a recreation it covers the asset again under a new generation.
- On Linux, if the reader cannot start (`inotify_init1` fails with EMFILE), the same adapter runs
  on watchdog's inotify backend, with a `Notice` saying so. It then has the limits in the middle
  column of §8.1.

## 11. Gaps and containment

> for now on, things like .zarr will be called "dirasset", not store, not container

- **Gap 1, marking one store.** `markers.canonical_path` (`markers.py:253-317`) adds no `/` to a
  container: `directory = (trailing or bare) and not container(Path(body.rstrip('/')))`. That also
  gives `!data/cube.zarr` and `data/*.zarr`. `markers.slashless` (`:164-193`) returns None for a
  container, so `data/cube.zarr` no longer draws "did you mean `data/cube.zarr/`". The scopes
  completer (`cli/scopes.py`) stops appending `/` to containers. The refusal in the init editor
  that `directory-assets.md` mentions was removed in `67f81ab`, so nothing is left to fix there.

> Yes, only append / if the dir has no extension. So dir/path becomes dir/path/, but dir/cube.zarr stays dir/cube.zarr.

- **Gap 2, a store tracked mid-run.** `Trail._track` calls `self.watchdog.cover(asset)` for every
  new directory asset. That covers scans from `consume`, from `Trail.mark`, and from
  `Trail.__init__`.

> What is the point of  watchdog.cover?  Let's avoid unnecessary methods. We're going to have a Watchdog.session: could Watchdog.session.watches be a custom UserDict, which does the observer.schedule() in `__setitem__`? Similarly, we coudl have delitem to unschedule an individual watch.

- **Gap 4, tracking inside a store.** `Trail._track` refuses a path inside a tracked directory
  asset or inside a directory requested in the same call, and a directory that holds `trail.dir`.
  The last one would put `.trail` inside a recursive watch, so every log append would count as a
  write. A new directory asset first offtrails the assets inside it, with a warning such as
  "tracking table whole offtrails 1 asset inside it", then logs its `AddAssetEvent`. The
  offtrailed paths stay offtrailed if the directory asset is later offtrailed (§20).
> I think unecessary to worry about if we just have recursive watch for all directories?  If a file is inside the dirasset that doesn't match any `dirasset.children` pattern, then we can check if it matches any asset patterns, and have it as an asset independent to the dirasset, as an edge case.
> Also, I mentioned `dirasset.children: set` to support different extensiosn, but maybe `dirasset.children: str` instead, because re.match can match multiple extensions? Or will we encounter problems wtih just a singular string to determine whether a file is a dirasset child?

- **Leaks found next to gap 4.** `Assets.scan` skips explicit paths inside a container or a
  directory asset (today `mark data/cube.zarr/band/` tracks chunk files). `dir.build` leaves out
  Dirs anywhere below a directory asset, not only directly at one (`dir.py:121-130`).
  `Markers.warnings` checks the whole lineage (`markers.py:721-731`). A shared
  `Assets.enclosing(path)` replaces `Watchdog._enclosing` for these checks.
- **`*.safe`** goes into `markers/geospatial.txt`, as your comment at 10-7:166 asks. Matching
  ignores case, so it matches `.SAFE` products. `*.n5` stays out unless you want it.



## 12. Fingerprint

`Asset.fingerprint` keeps its exact walk, which is the on-demand foundation ("think git, not a
daemon", `notes/notes.txt:8-11`). It changes in three ways: `st_size` and `st_mtime` come from one
walk instead of two, entries that vanish mid-walk are skipped instead of failing the whole walk,
and `AddAssetEvent` also records `files` for a directory asset, so a later `status` or `seek` can
compare all three totals. There is no cache that depends on watch coverage. The walk still costs
about 1.3 µs per file on the loop, so the console's `assets` listing of a 10⁶-file store is slow.
That stays a known limit (§20).

> Probably not necssary for now, we are just using watchdog, no detective work for tracking st_size and st_mtime

## 13. Console

- After the watchdog context exits, `Run` drains the feed once more, so the sessions sealed by
  `stop()` are printed (`cli/console.py:318-343`).
- `Run.diagnose` also reports a dead reader or adapter, not only a dead consumer.
- The header shows `sessions N` while N is not zero. A long write is otherwise invisible until it
  seals.
- `NOTES` and `MarkCommand` say that a container is marked without `/`, and that `track DIR`
  offtrails what is tracked inside it.
- The dead `discovered` branch of `Renderer.verb` (`cli/render.py:252-254`) and the unused
  `Renderer.directory` (`:258-266`) are removed.

## 14. Data model

The sketches follow the repo's style and passed ruff 0.16.8 where they could be checked in
isolation. Imports come first, per module.

### `util.py`

The new names need these imports. `Path` is already imported there.

```python
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal
```

```python
# the name zarr writes a key under before renaming it into place. zarr 2 appends it to the key,
# and zarr 3.1.3 or later replaces the key's last suffix with it
PARTIAL: Final = re.compile(r'\.[0-9a-f]{32}\.partial$')
# GDAL's REPACK writes a table aside before renaming it over the original, and ArcGIS leaves
# lock files beside the tables, even for a read
GDB_TRANSIENT: Final = re.compile(r'\._compress\.|\.(?:sr|rd|wr|ed)\.lock$', re.IGNORECASE)
# GDAL writes statistics beside a raster it reads
SIDECAR: Final = re.compile(r'\.aux\.xml$', re.IGNORECASE)


def top(relative: str) -> str:
    """
    The top-level entry a path inside a directory asset is under, such as an array of a zarr
    group.

    >>> top('temp/c/0/0/0')
    'temp'
    """
    return relative.partition('/')[0]


def table(relative: str) -> str:
    """
    The table a file of a geodatabase belongs to. It is the file name up to its first dot.

    >>> table('a00000009.gdbtablx')
    'a00000009'
    """
    name = relative.rpartition('/')[2]
    return name.partition('.')[0]


def leaf(relative: str) -> str:
    """
    The file name alone, such as a band of a Sentinel product.

    >>> leaf('GRANULE/L2A_T34VFJ/IMG_DATA/R10m/T34VFJ_B04_10m.jp2')
    'T34VFJ_B04_10m.jp2'
    """
    return relative.rpartition('/')[2]


@dataclass(frozen=True, slots=True)
class Profile:
    """
    How the inside of one kind of directory asset is counted. `member` names the member a path
    belongs to. Events on a `transient` name are not counted, except that a temporary file renamed
    into place counts as a write of its destination. `opens` says whether opens are watched at the
    top level only or at every depth.
    """

    member: Callable[[str], str]
    transient: re.Pattern[str] | None = None
    opens: Literal['top', 'all'] = 'top'
    skips_hidden: bool = False

    def classify(
            self,
            op: str,
            relative: str,
            destination: str = '',
    ) -> tuple[str, str] | None:
        """
        Returns the event type and the member an event inside the asset counts as, or None for an
        event that is not counted. Paths are relative to the asset's root.
        """
        name = relative.rpartition('/')[2]
        is_transient = self.is_transient(name)
        if op != 'moved':
            if is_transient:
                return None
            return op, self.member(relative)
        target = destination.rpartition('/')[2]
        if self.is_transient(target):
            return None
        if is_transient:
            return 'modified', self.member(destination)
        return 'moved', self.member(relative)

    def is_transient(self, name: str) -> bool:
        """Whether a file name is one of the asset's temporary, lock or sidecar files."""
        return (
            self.transient is not None
            and self.transient.search(name) is not None
        )


PROFILES: Final[dict[str, Profile]] = {
    '.zarr': Profile(member=top, transient=PARTIAL),
    '.n5': Profile(member=top, transient=PARTIAL),
    '.gdb': Profile(member=table, transient=GDB_TRANSIENT),
    '.safe': Profile(member=leaf, transient=SIDECAR, opens='all'),
}
# a directory tracked with `track DIR`, such as a hive-partitioned parquet table
DIRECTORY_PROFILE: Final = Profile(member=top, transient=PARTIAL, skips_hidden=True)
# directories that hold one dataset, so they are tracked whole and never walked into
CONTAINERS: Final = frozenset(PROFILES)


def profile(path: Path) -> Profile:
    """Returns the profile of a directory asset, by its suffix."""
    return PROFILES.get(path.suffix.lower(), DIRECTORY_PROFILE)
```

`clock_repr(moment: datetime) -> str` also moves here from `Event._repr_items` (`event.py:68-77`),
unchanged, so `timestamp` and `started` share it.

### `inbox.py` (new)

```python
from __future__ import annotations

import threading
from collections import Counter, deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Final, NamedTuple

if TYPE_CHECKING:
    from asyncio import AbstractEventLoop

    from watchdog.events import FileSystemEvent
```

```python
# what the producers queue after the last item of a shift
STOP: Final = object()


class Arrival(NamedTuple):
    """A watchdog event, with the time its observer thread received it, in epoch seconds."""

    at: float
    event: FileSystemEvent


class Lifecycle(NamedTuple):
    """
    A directory asset's root created, deleted or moved, as the interior reader saw it. The reader
    queues it after sealing the asset's tally, so the session is logged before the change.
    """

    at: float
    asset: str
    generation: int
    event_type: str
    dest_path: str = ''


class Notice(NamedTuple):
    """A warning from a producer thread, raised on the loop."""

    text: str


@dataclass(slots=True, eq=False)
class Tally:
    """
    The counts of one session that is still open. `first` and `last` are wall-clock times of its
    first and last events, and `opened` and `seen` the monotonic times that drive its deadlines.
    """

    # members a tally keeps counts for. Past it, events still count in `ops`
    member_limit: ClassVar[int] = 64

    asset: str
    generation: int
    first: float
    last: float
    opened: float
    seen: float
    ops: Counter[str] = field(default_factory=Counter)
    members: dict[str, Counter[str]] = field(default_factory=dict)
    is_lossy: bool = False

    def count(
            self,
            op: str,
            member: str,
    ) -> None:
        """Count one event of a member."""
        self.ops[op] += 1
        counts = self.members.get(member)
        if counts is None:
            if len(self.members) >= self.member_limit:
                return
            counts = Counter()
            self.members[member] = counts
        counts[op] += 1


class Inbox:
    """
    Carries what the observer threads and the interior reader receive to the asyncio loop.
    Events inside a directory asset are counted into its open tally where they arrive, so memory
    stays flat however long the loop is busy. Everything else is queued in order, and the loop is
    woken once per burst.

    The lock guards only the plain data below. No I/O, logging, watchdog call, walk or join runs
    while it is held.
    """

    def __init__(
            self,
            loop: AbstractEventLoop | None,
            wake: Callable[[], None],
            idle_gap: float,
            longest_session: float,
    ) -> None:
        self.loop = loop
        self.wake = wake
        # seconds
        self.idle_gap = idle_gap
        # seconds
        self.longest_session = longest_session
        self.lock = threading.Lock()
        self.items: deque[object] = deque()
        self.tallies: dict[str, Tally] = {}
        self.generations: dict[str, int] = {}
        self.inodes: dict[tuple[int, int], str] = {}
        self.is_wake_pending = False

    def cover(self, asset: str) -> int:
        """Start counting an asset, and return the generation its producer counts under."""
        with self.lock:
            generation = self.generations.get(asset, 0) + 1
            self.generations[asset] = generation
        return generation

    def count(
            self,
            asset: str,
            generation: int,
            op: str,
            member: str,
            at: float,
            seen: float,
    ) -> None:
        """
        Count one event inside a directory asset. An event of a retired generation is dropped.
        One more than `idle_gap` after the tally's last event, or more than `longest_session`
        after its first, seals the tally and starts a new one.
        """
        with self.lock:
            if self.generations.get(asset) != generation:
                return
            tally = self.tallies.get(asset)
            if (
                tally is not None
                and (
                    seen - tally.seen > self.idle_gap
                    or seen - tally.opened > self.longest_session
                )
            ):
                self._seal(asset)
                tally = None
            if tally is None:
                tally = Tally(
                    asset=asset,
                    generation=generation,
                    first=at,
                    last=at,
                    opened=seen,
                    seen=seen,
                )
                self.tallies[asset] = tally
                self._wake()
            tally.last = at
            tally.seen = seen
            tally.count(op, member)

    def put(self, item: object) -> None:
        """Queue an arrival, a lifecycle record, a notice or STOP."""
        with self.lock:
            self.items.append(item)
            self._wake()

    def pop(self) -> object | None:
        """
        Returns the oldest queued item, or None once the queue is empty. The pending wakeup is
        cleared only here, in the same critical section that finds the queue empty, so no item is
        left without one.
        """
        with self.lock:
            if self.items:
                return self.items.popleft()
            self.is_wake_pending = False
            return None

    def seal(self, before: float | None = None) -> None:
        """Seal the tallies last seen before a monotonic time, or every tally."""
        with self.lock:
            identifiers = [
                identifier
                for identifier, tally in self.tallies.items()
                if before is None
                or tally.seen <= before
            ]
            for identifier in identifiers:
                self._seal(identifier)

    def retire(self, asset: str) -> list[Tally]:
        """
        Returns the asset's open and queued tallies, oldest first, removes them, and retires its
        generation, so nothing counted later is logged for it.
        """
        with self.lock:
            self._seal(asset)
            self.generations.pop(asset, None)
            out = [
                item
                for item in self.items
                if isinstance(item, Tally)
                and item.asset == asset
            ]
            kept = [
                item
                for item in self.items
                if not (
                    isinstance(item, Tally)
                    and item.asset == asset
                )
            ]
            self.items = deque(kept)
        return out

    def deadline(self) -> float | None:
        """The monotonic time the quietest open tally goes idle, or None when none is open."""
        with self.lock:
            seen = [
                tally.seen
                for tally in self.tallies.values()
            ]
        if not seen:
            return None
        return min(seen) + self.idle_gap

    def _seal(self, asset: str) -> None:
        tally = self.tallies.pop(asset, None)
        if tally is None:
            return
        self.items.append(tally)
        self._wake()

    def _wake(self) -> None:
        if (
            self.is_wake_pending
            or self.loop is None
        ):
            return
        self.is_wake_pending = True
        # the loop may close while a producer thread still delivers
        with suppress(RuntimeError):
            self.loop.call_soon_threadsafe(self.wake)
```

`Inbox(loop=None, …)` is the unit-test seam: counts with explicit times, no thread, no wakeup.
`peek(asset) -> Tally | None` (a copy under the lock, for `Asset.session`), `mark_lossy()` (every
open tally) and `discard()` (for `Events.clear`) are three more short methods.

### `event.py`

```python
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field, fields
from datetime import datetime
from typing import TYPE_CHECKING, ClassVar

from trail.util import clock_repr, counts_repr, members_repr, mtime_repr, st_size_repr
```

```python
@dataclass(kw_only=True, slots=True, repr=False)
class Event:
    classes: ClassVar[dict[str, type[Event]]] = {}
    # the fields held as datetimes. The log holds them in ISO format
    moments: ClassVar[tuple[str, ...]] = ("timestamp",)
    ...

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        for event_field in fields(self):
            if not event_field.repr:
                continue
            name = event_field.name
            value = getattr(self, name)
            if (
                value is None
                or value == ""
            ):
                continue
            if name in self.moments:
                value = clock_repr(value)
            # the baseline is stored as the stat reported it. The units are put back here
            elif name == "st_size":
                value = st_size_repr(value)
            elif name == "st_mtime":
                value = mtime_repr(value)
            yield name, value

    def to_record(self) -> dict:
        out = {}
        for event_field in fields(self):
            name = event_field.name
            if name == "entry":
                continue
            value = getattr(self, name)
            if name in self.moments:
                value = value.isoformat()
            out[name] = value
        out["cls"] = type(self).__name__
        out["entry"] = self.entry.id
        return out
```

`from_record` replaces its single `timestamp` line (`event.py:127`) with a loop over
`event_cls.moments`, parsing each name present in the record.

```python
@dataclass(kw_only=True, slots=True, repr=False)
class DirectoryAccessEvent(WatchdogEvent):
    """
    One session of a directory asset: what happened inside it from `started` to its last event,
    which is its `timestamp`. It is inferred from many filesystem events, so it is always
    synthetic. `ops` counts the events of each type and `members` the events of each member, such
    as an array of a zarr group or a table of a gdb. They count what Trail observed, not how many
    files were read or written. `event_type` is `opened` when every event was an open, and
    `modified` otherwise. `is_lossy` says that some events were not observed.
    """

    moments: ClassVar[tuple[str, ...]] = ("timestamp", "started")

    started: datetime
    is_directory: bool = field(default=True, init=False, repr=False)
    is_synthetic: bool = field(default=True, init=False, repr=False)
    is_lossy: bool = field(default=False, repr=False)
    ops: Counter[str] = field(default_factory=Counter)
    members: dict[str, Counter[str]] = field(default_factory=dict)
    entry: Asset | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        WatchdogEvent.__post_init__(self)
        # a replay reads the counts back as plain dicts
        self.ops = Counter(self.ops)
        self.members = {
            member: Counter(counts)
            for member, counts in self.members.items()
        }

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        """Yields the fields a repr shows, with the counts worded on one line each."""
        for name, value in WatchdogEvent._repr_items(self):
            if name == "ops":
                value = counts_repr(value)
            elif name == "members":
                value = members_repr(value)
            yield name, value
        if self.is_lossy:
            yield "is_lossy", True

    def apply(
            self,
            trail: Trail,
            *,
            replay: bool = False,
    ) -> Asset | None:
        """
        Log the session and return its asset. A session changes nothing about the asset, so a
        replay only returns it.
        """
        if not replay:
            trail.events.append(self)
        return self.entry
```

`Watch.notify` (`event.py:578-581`) keeps a `dict[asyncio.Event, AbstractEventLoop]` of waiters,
sets a waiter directly when called on its loop, uses `call_soon_threadsafe` otherwise, drops
waiters whose loop is closed, and never raises.

### `watchdog.py`

```python
class Reach(StrEnum):
    """How `desired` says a directory is watched."""

    # a non-recursive watchdog watch with EVENT_FILTER
    FOLDER = "folder"
    # the root of a directory asset, covered by Interiors
    INTERIOR = "interior"
```

- `desired()` returns `dict[Path, Reach]`. Dir paths, the subtrees of recursive Dirs and the
  parents of assets map to `FOLDER`, as today. A directory asset's root maps to `INTERIOR`. Its
  subfolders are never listed. `cube in trail.watchdog.desired()` and
  `cube / 'band' not in trail.watchdog.desired()` (`tests/test_trail.py:917-918`) keep passing.
- `reconcile()` diffs `FOLDER` paths against `shift.watches`, as today, and covers or uncovers
  interiors.
- `cover(asset)` is the incremental form, called from `Trail._track`. `release(asset)` retires the
  asset's tallies, logs them, and uncovers it. `Asset.offtrail` calls it, which also catches the
  occupants that `Entry.track` and `Asset.move` offtrail without going through `Trail.offtrail`.
- `Watchdog.queue`, `loop`, `observer`, `handler` and `consumer` move onto `Shift`.
  `Watchdog.watches` stays as a property that returns `shift.watches`, or None while dormant.
  `test_trail.py:165` changes from `queue is None` to `shift is None`.

```python
    async def apply(self, shift: Shift) -> None:
        """
        Consume what the shift's producers queue until STOP, sealing idle tallies on their
        deadlines. Tallies still open when the task is cancelled are logged on the way out.
        """
        inbox = shift.inbox
        try:
            while True:
                timeout = None
                deadline = inbox.deadline()
                if deadline is not None:
                    timeout = max(0.0, deadline - time.monotonic())
                with suppress(TimeoutError):
                    await asyncio.wait_for(shift.woken.wait(), timeout)
                shift.woken.clear()
                if self.debounce:
                    await asyncio.sleep(self.debounce)
                inbox.seal(before=time.monotonic() - self.idle_gap)
                if self.consume(inbox):
                    return
        finally:
            self.flush(inbox)

    def consume(self, inbox: Inbox) -> bool:
        """
        Apply what the inbox holds, one item at a time, and return True once STOP is reached. A
        sealed tally is logged as a session, a lifecycle record as the move, deletion or creation
        of its asset, and an arrival as today.
        """
        seen: set[tuple[str, str, str]] = set()
        is_moved = False
        while True:
            item = inbox.pop()
            if item is None:
                break
            if item is STOP:
                return True
            if isinstance(item, Tally):
                self.record(item)
            elif isinstance(item, Lifecycle):
                self._lifecycle(item)
            elif isinstance(item, Notice):
                warnings.warn(item.text, stacklevel=2)
            else:
                is_moved |= self._arrival(item, seen)
        # a file moved into a directory not yet watched needs that directory watched
        if is_moved:
            self.reconcile()
        return False
```

`record(tally)` drops a tally whose asset is no longer tracked or whose generation was retired,
builds the `DirectoryAccessEvent` with `src_path=str(asset.path)`, `started` and `timestamp` from
`datetime.fromtimestamp(…, UTC)`, `event_type` from `ops`, and calls `event.apply(trail)`.
`_lifecycle` builds the matching `WatchdogEvent` and applies it the way `_directory` does today.
`_arrival` is today's per-event body of `consume` (`watchdog.py:408-426`), with the covered-root
drop of §9.2, the stamp from `Arrival.at`, and the early skip of §7.3. The per-batch fold
(`watchdog.py:427-438`) and `_enclosing` go away.

### `interior.py` (new)

`Interiors` is a `Node` under the `Shift` with `cover(asset)`, `uncover(asset)` and `halt()`.
`InotifyInteriors` runs the reader on Linux, and `WatchdogInteriors` is the adapter of §10. The
bindings are three `ctypes` functions, so Trail depends on no private watchdog module.

```python
import ctypes
import os
import select
import socket
import struct
import threading
from collections import deque
from dataclasses import dataclass, field
from queue import SimpleQueue
```

```python
# struct inotify_event: wd, mask, cookie, len, then len bytes of name
RECORD: Final = struct.Struct('iIII')
# how many directories a walk adds before the reader drains the fd again
WALK_STEP: Final = 500


@dataclass(slots=True, eq=False)
class Root:
    """One covered directory asset, as the reader sees it."""

    asset: str
    generation: int
    profile: Profile
    fd: int
    parent_fd: int
    name: str
    inode: tuple[int, int]
    # relative directory paths still to be watched, breadth first
    pending: deque[str] = field(default_factory=deque)
    watched: int = 0
    is_partial: bool = False


@dataclass(slots=True, eq=False)
class Folder:
    """What one wd watches: a directory inside a root, or a root's parent."""

    root: Root | None
    relative: str
    # covered root names, for the wd of a root's parent
    names: dict[str, Root] = field(default_factory=dict)
    is_removing: bool = False


class Reader(threading.Thread):
    """
    Watches every interior of one shift through one inotify instance, and counts what happens
    inside each directory asset into the shift's inbox.
    """

    def __init__(self, inbox: Inbox) -> None:
        super().__init__(name='trail-interiors', daemon=True)
        self.inbox = inbox
        self.fd = _init1(IN_NONBLOCK | IN_CLOEXEC)
        self.wake_r, self.wake_w = socket.socketpair()
        self.commands: SimpleQueue[object] = SimpleQueue()
        self.folders: dict[int, Folder] = {}
        self.roots: dict[str, Root] = {}
        # unpaired MOVED_FROM halves by cookie, with the monotonic time they expire
        self.held: dict[int, tuple[float, int, int, str]] = {}

    def run(self) -> None:
        try:
            while self.pump(timeout=self.timeout()):
                pass
        except Exception as error:
            # the loop falls back to the watchdog adapter on this notice
            self.inbox.put(Notice(f'interior reader stopped: {error!r}'))

    def pump(self, timeout: float | None) -> bool:
        """
        Run one round: wait for records or commands, apply the commands, handle every record read,
        advance walks, then seal the tallies and resolve the move halves past their deadlines.
        Returns False once halted. Tests call it directly with no thread running.
        """
        ...
```

The rest of `Reader` follows §8.3 to §8.5 record by record. Its parts are `_cover(root)`,
`_walk(root)` (one `WALK_STEP` at a time), `_handle(wd, mask, cookie, name)`, `_parent(…)`,
`_inside(…)`, `_overflowed()` and `_resolve_held(now)`. Each count calls
`inbox.count(root.asset, root.generation, op, member, at=at, seen=seen)` with the two clocks read
once per batch of records.

### `trail.py`, `asset.py`, `dir.py`, `markers.py`

```python
    def _refuse(
            self,
            path: Path,
            requested: Mapping[Path, str | None],
    ) -> None:
        """
        Raises ValueError for a path that cannot be tracked: Trail metadata, a directory that
        holds the trail, or a path inside a directory asset or inside another requested directory.
        """
        if self._ignored(path):
            raise ValueError(f"Cannot track Trail metadata: {path}")
        if (
            self.dir is not None
            and self.dir.is_relative_to(path)
        ):
            raise ValueError(f"Cannot track a directory that holds the trail: {path}")
        holder = self.assets.enclosing(path)
        if holder is not None:
            raise ValueError(f"{path} is inside {holder.path}, which is tracked whole")
        outer = [
            other
            for other in requested
            if other != path
            and path.is_relative_to(other)
        ]
        if outer:
            raise ValueError(f"{path} is inside {outer[0]}, which is tracked whole")
```

`Trail._track` calls `_refuse` for every requested path, absorbs the assets inside a new
directory asset before its `AddAssetEvent` (so the `RemoveDirEvent` from `dirs.refresh` stays the
last event, as `tests/test_dirs.py:120-125` expects), and ends with `self.watchdog.cover(asset)`.
`Trail._moved` (`trail.py:521-559`) calls `self.watchdog.flush(asset)` for each directory asset
under the source before applying its moved event. `Asset.offtrail` calls
`self._trail.watchdog.release(self)` before removing itself. `Asset.session` returns a snapshot
built from `inbox.peek(self.id)`, or None.

## 15. Implementation, in order

Each PR lists its modules, API changes, migration and acceptance tests. "Acceptance" means the
named tests exist and pass, and the whole suite passes. No PR migrates existing logs, assets or
`project.json`, per the decision to skip back-compat while Trail is early. Every log written today
stays readable after every PR.

### PR 0: hygiene (prerequisite)

Not part of this plan's code, but every later PR depends on it.

- Commit `src/trail/entry.py` and `tests/test_dirs.py`. HEAD imports `trail.entry`
  (`asset.py:10`, `dir.py:8`, `trail.py:15`) but does not contain it, so a clean checkout fails
  with ModuleNotFoundError. Decide on `src/trail/markers/general.txt`, which HEAD's tests use, and
  leave `src/a/` out.
- Land or split the 47-commit branch.
- Move the pre-commit ruff rev from v0.15.20 to v0.16.8, which the dev group uses, and fix the one
  violation it reports (SIM114 at `util.py:236`). New code is written against 0.16.8's defaults,
  including DTZ, RUF012 and BLE001.
- Add pytest to the dev group, `testpaths = ["tests"]`, and a Linux CI job on Python 3.12 and the
  newest Python. Today nothing runs the tests on a push.
- Stop the three Trails that tests leave watching (`test_trail.py:940`, `test_dirs.py:168`,
  `test_input.py:51`). One of them keeps an emitter on `/tmp` alive for the rest of the run.
- Fix `playground.Asset.open`, which returns None (`playground.py:172-178`) and breaks
  `GeoJSON.append`.

### PR 1: `*.safe`

- **Modules:** `markers/geospatial.txt`.
- **Acceptance:** `test_the_geospatial_preset_tracks_safe_products` (an `S2A_….SAFE` directory is
  tracked as a container).

### PR 2: gap 1

- **Modules:** `markers.py` (`canonical_path`, `slashless`, `Markers.warnings`), `cli/scopes.py`.
- **API:** `canonical('data/cube.zarr', …)` returns `'data/cube.zarr'`.
- **Acceptance:** `test_a_container_gets_no_slash`, `test_a_container_line_raises_no_slash_warning`,
  `test_marking_one_store_tracks_it`, `test_a_negated_store_is_excluded`.

### PR 3: gap 4 and containment

- **Modules:** `trail.py` (`_refuse`, absorb), `asset.py` (`Assets.enclosing`, the scan guard),
  `dir.py` (`build`), `markers.py` (`warnings` lineage).
- **API:** `Trail.track` raises ValueError for a path inside a directory asset and for a directory
  holding `.trail`. `track DIR` warns and offtrails what it absorbs.
- **Changed tests:** `test_trail.py:959-960` inverts. `test_dirs.py:116-129` expects the warning.
- **Acceptance:** `test_tracking_a_path_inside_a_store_is_refused`,
  `test_tracking_a_directory_offtrails_the_assets_inside_it`,
  `test_the_project_directory_cannot_be_tracked_whole`,
  `test_marking_inside_a_store_tracks_nothing`,
  `test_a_dir_inside_a_tracked_directory_is_left_out`.

### PR 4: gap 2

- **Modules:** `trail.py` (`_track` calls `watchdog.cover`), `watchdog.py` (`cover`, placing
  today's top-level watch for now).
- **Acceptance:** `test_a_store_created_while_watching_is_watched`,
  `test_marking_stores_while_watching_watches_them`.

### PR 5: the shift and the hand-off

- **Modules:** `watchdog.py` (`Shift`, `Handler` posting `Arrival`, `apply(shift)`, `consume`
  popping one item at a time, `stop` as in §7.4), `inbox.py` (`Inbox` without tallies yet,
  `Arrival`, `Notice`, `STOP`), `event.py` (`Watch.notify`).
- **API:** `Watchdog.shift` replaces `queue`, `loop`, `observer`, `handler` and `consumer`.
  `_start_session` becomes `_start_shift`.
- **Behavior:** events keep their observed time, and today's double logging across
  `asyncio.run` calls ends. Events are still logged as they are today.
- **Acceptance:** `test_an_event_is_stamped_when_it_arrives`,
  `test_a_shift_left_running_is_stopped_before_the_next`,
  `test_nothing_after_stop_starts_a_shift`, `test_one_wakeup_carries_a_burst`,
  `test_stop_keeps_the_events_watchdog_already_read`,
  `test_a_handler_exception_does_not_stop_dispatch`,
  `test_notifying_from_another_thread_wakes_a_watcher`.

### PR 6: the event model

- **Modules:** `event.py` (`Event.moments`, `to_record`, `from_record`, `_repr_items`,
  `DirectoryAccessEvent`), `util.py` (`clock_repr`, `counts_repr`, `members_repr`, `OPS`),
  `cli/commands.py` (`EventsCommand.fields`).
- **Acceptance:** `test_a_session_survives_a_reload` (tz-aware `started`, `Counter` counts),
  `test_a_session_is_one_line_of_the_log`, `test_the_feed_renders_a_session`,
  `test_cls_finds_sessions_and_their_base`, `test_event_type_finds_opened_and_modified_sessions`.
  Each uses its own fixture, since the shared ones in `test_select.py` and `test_listing.py` pin
  event positions.

### PR 7: sessions on the existing top-level watch

- **Modules:** `inbox.py` (`Tally`, generations, `retire`, `peek`, `discard`), `util.py`
  (`Profile`, `PROFILES`), `watchdog.py` (counting arrivals inside directory assets through a
  string-prefix map of roots, `record`, `flush`, `release`, deadlines, the exit hook),
  `trail.py` (`_moved` flushes), `asset.py` (`Asset.offtrail`, `Asset.session`), `event.py`
  (`Events.clear` discards).
- **Behavior:** the per-batch fold goes away, and so does today's bug that logs a write after a
  later deletion or rename of the store. Only top-level events exist until PR 8.
- **Changed tests:** `test_a_container_is_tracked_whole` (`test_trail.py:900-944`) expects one
  `DirectoryAccessEvent`, sets `idle_gap = 0.1` and `debounce = 0`, and checks key membership
  rather than exact counts.
- **Acceptance (unit):** `test_an_access_is_recorded_as_one_session`,
  `test_a_session_closes_after_the_idle_gap`, `test_a_session_longer_than_the_limit_is_split`,
  `test_a_session_starts_at_its_first_event_and_ends_at_its_last`,
  `test_a_session_is_recorded_before_the_store_moves`,
  `test_a_session_is_recorded_before_the_store_is_deleted`,
  `test_offtrailing_a_store_records_its_session_first`,
  `test_clearing_the_events_drops_an_open_session`,
  `test_events_of_a_retired_generation_are_dropped`, `test_a_session_counts_each_member`,
  `test_members_beyond_the_cap_still_count_in_ops`, `test_a_session_of_only_opens_is_opened`,
  `test_a_temp_file_renamed_into_place_is_a_write`, `test_lock_files_leave_a_read_a_read`,
  `test_stopping_the_watchdog_records_every_open_session`,
  `test_a_cancelled_consumer_records_its_sessions`.
- **Acceptance (integration):** `test_reading_a_store_records_one_opened_session`,
  `test_two_reads_apart_record_two_sessions`.

### PR 8: the Linux interior reader

- **Modules:** `interior.py` (`Interiors`, `InotifyInteriors`, `Reader`), `watchdog.py`
  (`Reach`, `desired`, `reconcile`, `cover` and `uncover` through `Interiors`, the covered-root
  drop in `_arrival`, `_lifecycle`), `cli/console.py` (`Run.diagnose`).
- **Behavior:** writes below the top level are recorded (gap 3). A directory asset's root has no
  watchdog watch any more. Its parent still has one, for Dirs and for scans.
- **Acceptance (pump seam, Linux only):** `test_a_write_below_the_top_level_is_counted`,
  `test_a_new_chunk_directory_is_watched_at_once`,
  `test_a_directory_moved_into_a_store_is_watched`,
  `test_the_inner_deletions_come_before_the_root_deletion`,
  `test_a_recreated_root_is_covered_again_in_the_reader`,
  `test_a_root_renamed_in_its_parent_is_paired`,
  `test_a_root_moved_out_of_view_is_followed_by_its_fd`,
  `test_an_ancestor_rename_does_not_break_new_watches`,
  `test_a_walk_drains_the_queue_between_steps`, `test_a_store_past_its_budget_is_lossy`,
  `test_hidden_directories_of_a_tracked_directory_are_not_watched`.
- **Acceptance (integration):** `test_rewriting_a_store_in_place_keeps_its_id`
  (order: session, deleted, created, session), `test_a_moved_store_keeps_its_id`,
  `test_concurrent_reads_merge_into_one_session`,
  `test_a_store_with_many_folders_uses_one_inotify_instance` (counts `anon_inode:inotify` in
  `/proc/self/fd`), `test_editing_a_gdb_counts_each_table`,
  `test_reading_a_band_of_a_safe_product_is_recorded`.

### PR 9: overflow

- **Modules:** `interior.py` (`_overflowed`, inode reconciliation, the coalesced rewalk).
- **Acceptance:** `test_an_overflow_marks_open_sessions_lossy`,
  `test_a_store_replaced_during_an_overflow_is_found_by_its_inode`,
  `test_watches_lost_to_an_overflow_are_restored_once`. The pump seam produces an overflow
  deterministically by writing more than `max_queued_events` (read from
  `/proc/sys/fs/inotify/max_queued_events`) events before calling `pump`.

### PR 10: other platforms and the Linux fallback

- **Modules:** `interior.py` (`WatchdogInteriors`), `watchdog.py` (choosing an implementation).
- **Acceptance:** `test_the_adapter_counts_writes_below_the_top_level`, on Linux by forcing the
  adapter, and `test_the_adapter_drops_events_from_a_dead_watch`. CI on macOS runs the adapter
  tests, if a macOS runner is added.

### PR 11: fingerprint

- **Modules:** `asset.py` (`fingerprint`, `st_size`, `st_mtime`), `event.py`
  (`AddAssetEvent._stat` records `files`).
- **Acceptance:** `test_size_and_mtime_take_one_walk`, `test_a_file_vanishing_mid_walk_is_skipped`.

### PR 12: console

- **Modules:** `cli/console.py`, `cli/render.py`, `cli/commands.py`.
- **Acceptance:** `test_the_feed_prints_the_sessions_stop_sealed`,
  `test_the_header_counts_open_sessions`, `test_a_dead_reader_is_reported`.

### PR 13 (optional): `select` on counts and members

- **Modules:** `select.py` (`COMPARISON`, `held`, `equals`).
- **Acceptance:** `test_a_dotted_field_reads_a_count`, `test_members_is_a_membership_test`,
  `test_a_missing_count_is_zero`.

### PR 14 (needs a decision): opens below the top level

Watching opens at every depth catches deferred dask compute, `zarr.open_array(store / 'temp')`
and `dask.array.from_zarr(component=…)`. The measured design is "disarm after the first file
open in a directory, re-arm when the session seals": re-adding the wd without `IN_OPEN` keeps
the same wd and replaces its mask. It has known costs that §21 has to settle first. Every listing
of a directory then queues `IN_OPEN | IN_ISDIR` events (2N-1 for a walk). The arm state has to be
owned per wd by the reader, which re-arms any disarmed wd whose asset has no open tally after every
read. A full read of a zarr v2 store with flat chunks reports one open per chunk directory.

## 16. Tests

- **Unit, no thread and no inotify.** A dormant `Trail(root, markers=None)` over a playground
  store, an `Inbox(loop=None, …)` driven with explicit `at` and `seen` times, then
  `trail.watchdog.consume(inbox)`. The log order is checked directly. Counts are exact here.
- **Reader, Linux only.** `Reader.pump(0)` right after each filesystem action, with no thread
  running. Skipped elsewhere with `pytest.mark.skipif(sys.platform != 'linux', …)`.
- **Integration, Linux only.** The repo's `asyncio.run(run())` pattern, `asyncio.timeout(5)`
  around every wait, `idle_gap = 0.1` and `debounce = 0` set right after `Trail(root)`, and a
  helper that always stops every Trail it opens. Waits use
  `contextlib.aclosing(trail.events.watch(timeout=5))` or poll `asset.session`. Real counts are
  nondeterministic (one `write_text` gave one or two `modified` across runs), so assertions check
  presence, keys, `event_type` and order, never exact values.
- **Playground.** `Zarr` (v3: `zarr.json`, `arr/zarr.json`, `arr/c/0/0`), `ZarrV2` (`.zgroup`,
  `.zmetadata`, `arr/.zarray`, `arr/0.0`), `GDB` (flat `a0000000N.gdbtable` and `.gdbtablx`) and
  `Safe` (`manifest.safe`, `GRANULE/<g>/IMG_DATA/R10m/*.jp2`) nodes, built one `subdir()` level at
  a time. `Store.rewrite()` removes and recreates the root as `mode='w'` does. The repr collapses a
  container to one line with its file count. New test modules use the `dir(suite)` runner of
  `test_dirs.py:223-228`.
- **Performance smoke.** `test_a_write_of_many_chunks_costs_one_record` drives 10⁵ counts through
  an `Inbox` and checks one record, members within the cap, one line added to the log, and a
  tracemalloc peak bound.

## 17. Invariants

1. Every `DirectoryAccessEvent` has `started <= timestamp` and is logged after the
   `AddAssetEvent` of its asset.
2. A session is logged before the `moved`, `deleted` or `RemoveAssetEvent` that ended it, and no
   session of an asset is logged after its `RemoveAssetEvent` until it is tracked again.
3. Each sealed tally is logged at most once.
4. The sum of the counts in `members` never exceeds `ops`, type by type.
5. No raw event inside a directory asset reaches `events.jsonl`, and none is retained per event
   while the loop is busy.
6. A path inside a directory asset is never an asset, and never a Dir.
7. Each interior event is counted by exactly one producer: the reader on Linux, the adapter
   elsewhere. A directory asset's root never also has a watchdog watch.
8. Every producer belongs to one shift. After `stop()` returns, no thread of that shift runs and no
   consume can start a new shift.
9. The inbox lock is never held across I/O, a watchdog call, a walk or a join.
10. A known loss of events (overflow, budget, late cover) always sets `is_lossy` on the sessions it
    touches.
11. A lifecycle record never comes from a root's own `DELETE_SELF` or `MOVE_SELF`.
12. A store keeps its id across `mode='w'`, a rename, a move on the same filesystem, and an ancestor
    rename.

## 18. Failure modes

| Failure | Detection | Effect | Handling |
|---|---|---|---|
| kernel queue overflow | `IN_Q_OVERFLOW` on the reader's fd | lost counts, possibly a lost lifecycle | sessions lossy, roots reconciled by inode, one rewalk (§8.5) |
| overflow on a watchdog instance | none, since watchdog drops it | lost Dir and file events | unchanged from today. §20 |
| `ENOSPC` (`max_user_watches`) or the budget | `inotify_add_watch` errno, the counters | part of an interior unwatched | asset partial, sessions lossy, one `Notice` |
| `EMFILE` at `inotify_init1` | the exception in `_start_shift` | no reader | the watchdog adapter, with a `Notice` |
| the reader thread dies | `Notice` from `run`, `Run.diagnose` | no interior events | fall back to the adapter for the rest of the shift |
| one handler exception | caught in `Handler.on_any_event` | none | logged as a `Notice` |
| a root deleted while an fd holds it | parent `DELETE`, a delayed `DELETE_SELF` | none | lifecycle from the parent only |
| an add after `CREATE \| ISDIR` fails with ENOENT | the errno | a new subdirectory unwatched | the parent rescanned at the end of the batch |
| the process is killed | none | open tallies lost | at most `longest_session` of activity per asset, accepted (§20) |
| the interpreter exits without `stop()` | `atexit` | none | the exit hook logs the tallies |
| a fork | `os.register_at_fork` | none | the child starts with an empty inbox and no hook |
| the loop closes with a pending watcher | `loop.is_closed()` in `notify` | none | the waiter is dropped |
| a move-out held 0.5 s by watchdog at `stop()` | the settle wait | none | `stop()` waits 0.55 s after the last arrival |
| a cross-filesystem move of a store | `readlink` says deleted | a new id for the copy | accepted |

## 19. Performance targets

Targets, checked by the validation tasks in §21 where they are not measured yet.

| | Target | Today or watchdog |
|---|---|---|
| loop work per event inside a directory asset | none. Loop work is per session | about 20 µs (build, hand-off, `_enclosing`) |
| memory per event while the loop is blocked | 0 B. Each open tally is bounded by 64 members | 612 B |
| reader CPU per kernel event | at most 3 µs | 7-8 µs in watchdog's threads |
| a 40,000-chunk zarr write in the same process | at most +10% wall time | +37-46% |
| a full read with top-level opens | +0% | +0% without opens, +20% with deep opens |
| loss without overflow | none | none |
| loss with overflow | always detected and flagged | silent |
| loop blocked by a cover | under 1 ms per command, with the walk in the reader | 7-20 µs per directory on the loop |
| resources | 1 thread, 1 inotify instance, 2 fds for the wake socket, 2 `O_PATH` fds per directory asset | 2 threads, 1 instance and 3 fds per watch |
| log growth | one record of 0.5-2.2 KB per session, at most one per `longest_session` per asset | one record per 100 ms batch |
| `stop()` | at most 1.6 s in the worst case | — |
| each new integration test | at most 0.4 s with `idle_gap = 0.1` | — |

## 20. Limitations

- **Reads are Linux-only.** No watchdog backend other than inotify reports opens.
- **Top-level opens miss reads that start below the root.** Deferred dask compute and direct
  sub-array opens leave no session for zarr until PR 14. A safe product watches every depth.
- **Counts are approximate.** They are lower bounds under coalescing and overcount where a walk
  races a writer.
- **Network filesystems.** inotify sees only writers on the local host, so a store on NFS or SMB
  written elsewhere looks untouched (assumed).
- **Two Trails on one project double-record.** A console and a notebook kernel each watching the
  same project both log every session. The single-writer design in `notes/notes` is out of scope.
- **Who acted is unknown.** As 10-7 §3.3 says, the filesystem never says.
- **A killed process loses its open sessions**, at most `longest_session` of activity per asset.
  A journal of open tallies is deferred.
- **File assets still fork their id when moved between directories**, and a file asset moved
  into a store is logged as deleted. Both come from watchdog's separate instances.
- **Dir watches still cost one watchdog instance and two threads per folder.** A project with a
  few hundred watched folders can exhaust a 128-instance default. Moving Dir watches onto the
  reader's instance is deferred.
- **Overflow on watchdog's own instances stays silent.**
- **Absorbed assets stay offtrailed** after the directory asset that absorbed them is offtrailed.
- **Listing a 10⁶-file store walks it**, at about 1.3 µs per file.
- **Writes through `mmap`, and truncation without a close, are not counted.**
- **Activity while Trail is not running is not recorded**, as decided on 9-30.

## 21. Validation tasks

Each one is a short measurement to run on the PR it belongs to. None blocks this plan.

| # | Task | PR | Pass when |
|---|---|---|---|
| V1 | reader CPU per event and the maximum event rate, against watchdog, for one and four external writers and four in-process writer threads | 8 | at most 3 µs per event, and fewer overflows than watchdog's 18-89% loss on the same load |
| V2 | in-process 40,000-chunk zarr write and full read, unwatched and with the reader | 8 | write at most +10%, read +0% |
| V3 | a 4,000-dir, 40,000-file `mode='w'` overwrite by another process | 9 | overflow detected, `deleted` and `created` logged, the new store covered, sessions lossy |
| V4 | real zarr 2 and zarr 3 scenarios: create, `open_zarr().load()`, region write, `append_dim`, `mode='w'`, and gdb create, read, append and rewrite through pyogrio, and safe band reads through rasterio | 8 | sessions match §5.1 and §6.4, with no temp names among the members |
| V5 | the adapter on macOS FSEvents and on Windows | 10 | writes below the top level counted, and no walk on schedule |
| V6 | default budgets on a CI runner and on a typical laptop, where `max_user_watches` may be as low as 8,192 on older kernels | 8 | a 10,000-dir store covered, or flagged partial with one notice |
| V7 | compute-bound dask writes, which may leave more than 1.5 s between chunks | 7 | a sensible `idle_gap` default, or a longer gap once a session has writes |
| V8 | `/proc/self/fd/N/<relative>` adds on the oldest supported kernel | 8 | same wd, and no ENOENT after an ancestor rename |
| V9 | the cost of watching opens at every depth with disarm and re-arm, including listings | 14 | data to decide PR 14 |

## 22. Things that are easy to get wrong

- **Lock order.** Observer lock, then inbox lock, never the reverse. Nothing slow runs under the
  inbox lock.
- **The wakeup flag.** It is cleared only where the queue is found empty.
- **Root self events.** `DELETE_SELF` and `MOVE_SELF` of a root never make a lifecycle record.
- **By-path adds.** Every add goes through the root's `O_PATH` fd. A by-path add after
  `mv a a_old; mkdir a` watches a different inode under a new wd.
- **wd state.** It is kept until `IN_IGNORED`, since queued events for a removed wd still arrive.
- **Pairing.** By cookie, never by adjacency, with the 50 ms hold inside the poll timeout.
- **Walk floods.** A walk over wds with `IN_OPEN` queues two events per directory, and Trail's own
  fingerprint walk is one.
- **Never read files inside a store.** Opening `zarr.json` from Trail would start an `opened`
  session for Trail itself.
- **Slots and `super()`.** Methods of `DirectoryAccessEvent` call `WatchdogEvent` explicitly.
- **Datetimes in records.** Every new datetime field goes into `moments`, or `json.dumps` raises
  inside the consumer.
- **Two meanings of session.** The run-level one is renamed to shift in the same PR that adds
  sessions.
- **Stale paths.** The reader never reports a path of its own except through `readlink`. The loop
  uses `asset.path`.

## 23. Deferred

- Opens below the top level for zarr and `track DIR` (PR 14).
- Sealing sessions at Jupyter cell boundaries, for attribution.
- A journal of open tallies, so a killed kernel loses nothing.
- Moving Dir watches onto the reader's instance, which would fix the instance limit and silent
  overflow for Dirs too, and pair file moves across directories by cookie.
- Pairing a file asset's cross-directory move by inode.
- A debug trace of raw events, in its own file and opt-in.
- `Asset.members` for datasets spread over sibling files, such as shapefiles, using the same
  member vocabulary.
- More container suffixes (`.tiledb`, `.lance`) and a configurable profile table.
- Mapping gdb table stems to layer names, which needs `GDB_Items` (`a00000004`).
- Relogging live assets in `Events.clear`, as it relogs Dirs. Today an asset whose first record
  after an `events clear` is a session is not tracked again on restart.
- Ruff `ALL` and mypy strict, as `notes/9-30.md` asks. Ruff `ALL` would need SIM108 ignored, since
  it contradicts the explicit if/else rule.

## 24. Resolved

| Question | Answer |
|---|---|
| child events (10-7 §2.1) | none. One `DirectoryAccessEvent` per session |
| `is_child`, `parent_id` | moot |
| tuples, dicts or `__slots__` through the queue (10-7:72-73) | none of them. Pass watchdog's own event with its arrival time, wake the loop once per burst, and count inside directory assets where the events arrive (§7.3) |
| a lock for the counters (10-7:54-57) | yes, once counting moves to the receiving thread (§2.3) |
| `.safe` in the geospatial preset (10-7:166) | yes, PR 1 |
| where an open session lives | the shift's `Inbox`. `Asset.session` is a read-only snapshot |
| interval fields | `timestamp` is the last event, `started` the first |
| watching interiors on Linux | one Trail-owned inotify instance (§8) |
| watching interiors elsewhere | one recursive watchdog watch per directory asset (§10) |
| opens | top level for zarr, gdb and `track DIR`, every depth for safe. The rest waits for PR 14 |
| counting writes | on `IN_CLOSE_WRITE` |
| member keys | per profile, temp names folded, at most 64 |
| temporary, lock and sidecar files | not counted, and a temp renamed into place is a write |
| `mode='w'` overwrite | session, `deleted`, `created`, session, with the same id |
| defaults | `idle_gap` 1.5 s, `longest_session` 60 s, 64 members, 10,000 directories per asset, 50,000 in total |
| fingerprint cache | none. One walk for size and mtime |
| tracking inside a directory asset | refused. `track DIR` offtrails what it absorbs |
| migrations | none |
