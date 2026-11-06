# Folding file events into one event per write

A notebook cell that saves a GeoDataFrame to a gpkg leaves about fifteen events in Trail's log. There is one `created` or one registration, one `opened`, about thirteen `modified`, and often a late second `opened`. This report explains where each of those events comes from and how Trail could fold them into one event per write. It follows up the 2026-11-01 discussion with Maryam about recording newly saved data.

Everything below was reproduced on 2026-11-04 on this machine, with Trail on `triage` at `782a857`. The writer ran the cell from `scratch/demo/Untitled.ipynb` in the `jupytergis_dev` environment, which has geopandas 1.2.0, pyogrio 0.13.0, GDAL 3.12.4 and jupyter_server 2.21.1. The cell reads `nyc.zip`, reprojects it to EPSG:3857 and calls `to_file("streets_projected.gpkg", driver="GPKG")`. The writer ran as a plain Python process, not in a kernel. A kernel runs the same library code and touches no other file in the directory. Three observers watched each write at the same time:

- A small `LD_PRELOAD` library logged each open, read, write, close, unlink and rename that the writer made on files in the directory.
- Raw inotify readers logged every kernel notification. One asked for all notification types, and one asked for the types Trail asks for.
- watchdog 6.0.0 observers reported events, once with Trail's filter and once without it.

The writes ran in a directory under `/tmp`, which neither KDE's file indexer nor Syncthing watches, and in directories under `$HOME`, which the indexer watches. Separate runs of a Trail session recorded what Trail logs for the same writes. The demo's own log starts at 17:44 that day, after the Slack trace, so the comparison uses the trace as quoted in Slack.

## Summary

- GDAL writes a gpkg as an SQLite database. One `to_file` made 1 open, 153 page writes and 1 close on `streets_projected.gpkg`, spread over 12 transactions. Each transaction also created, wrote and deleted a `streets_projected.gpkg-journal` file. inotify reports each of these system calls, and watchdog turns each notification into an event.
- The number of `modified` events does not count the writes. The kernel merges a notification into the previous one if they are identical and the previous one has not been read yet. The same write gave 20, 13 or 12 `modified` events depending on how quickly the reader drained its queue.
- Trail's watchdog filter leaves out close events, so the kernel never tells Trail when the writer finished. Trail cannot tell when a write ends, and it cannot tell a read from a write by `opened` alone.
- The `opened` at 22:30:27.920 in the Slack trace, 0.6 s after the write, was Baloo, KDE's file indexer, reading the new file. It disappears when the file is written where Baloo does not index.
- Jupyter does not create `.~streets_projected.gpkg` when a cell writes the gpkg. jupyter_server writes a `.~` backup only when it saves a file through its contents API, as in a notebook autosave or a collaborative save of `map.jGIS`.
- Trail already folds events for directory assets. It collects their events and logs one `DirectoryAccessEvent` after 30 s of quiet. The recommendation is to fold file events in a similar way, but to end each fold shortly after the writer closes the file, with a quiet period as the fallback.

## Where the events come from

This section follows one `to_file` call from the system calls it makes to the events that reach Trail. Each subsection explains one reason the result is noisy.

### inotify reports system calls

inotify is the Linux kernel's interface for file notifications. A program asks for notifications on a directory and names the types it wants. The kernel then queues a notification for each matching system call on a file in that directory. watchdog, the library Trail uses to watch directories, reads the queue and turns each notification into an event:

| inotify notification | System call that causes it | watchdog event |
|---|---|---|
| `IN_CREATE` | creating a file | `created` |
| `IN_OPEN` | opening a file, to read or to write | `opened` |
| `IN_MODIFY` | each `write`, `pwrite` or `truncate` | `modified` |
| `IN_ATTRIB` | changing permissions or timestamps | `modified` |
| `IN_CLOSE_WRITE` | closing a file that was opened for writing | `closed` |
| `IN_CLOSE_NOWRITE` | closing a file that was opened read-only | `closed_no_write` |
| `IN_MOVED_FROM` and `IN_MOVED_TO` | renaming a file within or between watched directories | `moved` |
| `IN_DELETE` | deleting a file | `deleted` |

`to_file` with `driver="GPKG"` hands the frame to pyogrio, which calls GDAL's `GPKG` driver. A gpkg is an SQLite database, and GDAL writes it through SQLite. SQLite writes the database in 4096-byte pages. It protects each transaction with a rollback journal, a temporary file named `<database>-journal` that holds the old pages until the transaction commits. The writer's trace begins and ends like this:

```text
   0.00 ms  open   streets_projected.gpkg          flags=RW|CREAT
   9.25 ms  open   streets_projected.gpkg-journal  flags=RW|CREAT
   9.28 ms  pwrite streets_projected.gpkg-journal  n=512 off=0
   9.75 ms  pwrite streets_projected.gpkg          n=4096 off=0
   9.76 ms  pwrite streets_projected.gpkg          n=4096 off=4096
   ...
   9.98 ms  close  streets_projected.gpkg-journal
   9.99 ms  unlink streets_projected.gpkg-journal
  10.00 ms  open   streets_projected.gpkg-journal  flags=RW|CREAT
   ...
  17.91 ms  unlink streets_projected.gpkg-journal
  17.95 ms  close  streets_projected.gpkg
```

In 18 ms the writer made these calls:

| File | Calls |
|---|---|
| `streets_projected.gpkg` | 1 open, 23 reads, 153 page writes, 1 close |
| `streets_projected.gpkg-journal` | 12 times: created, written, closed and deleted |

The database stays open for the whole write. The one open and the one close bracket the entire operation, which is what makes folding by the close possible.

### The number of `modified` events is arbitrary

The kernel does not keep every notification. The inotify(7) manual says that if a new notification has the same watch, type, cookie and name as the last one in the queue, and that one has not been read yet, the two are merged. A run of 153 page writes therefore becomes as many `IN_MODIFY` notifications as the reader happened to drain in between. The same write gave:

| Reader | `modified` events for the gpkg |
|---|---|
| A raw reader with Trail's types, reading as fast as it can | 20 |
| A raw reader with Trail's types, pausing 20 ms before each read | 12 |
| watchdog with Trail's filter, in `/tmp` and in `$HOME` | 13 |
| Trail's log in the Slack trace | 13 |

The journal's notifications interrupt the runs of gpkg writes. That is why even the slow reader keeps about one `modified` per transaction. The count depends on scheduling, file size and how the writer splits its transactions. Trail should not record it as if it measured something.

### Trail does not ask for the end of a write

Trail tells watchdog which events it wants with `EVENT_FILTER` in [session.py](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:55):

```python
EVENT_FILTER: Final = [
    FileCreatedEvent,
    FileModifiedEvent,
    FileDeletedEvent,
    FileMovedEvent,
    FileOpenedEvent,
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
]
```

watchdog's inotify backend turns this filter into the mask it gives the kernel, so a type the filter leaves out is never requested. This happens in [get_event_mask_from_filter](/home/arstneio/PyCharmProjects/trail/.venv/lib/python3.12/site-packages/watchdog/observers/inotify.py:201):

```python
def get_event_mask_from_filter(self) -> int | None:
    """Optimization: Only include events we are filtering in inotify call."""
    if self._event_filter is None:
        return None

    # Always listen to delete self
    event_mask = InotifyConstants.IN_DELETE_SELF

    for cls in self._event_filter:
        ...
        elif cls is FileClosedEvent:
            event_mask |= InotifyConstants.IN_CLOSE_WRITE
        elif cls is FileClosedNoWriteEvent:
            event_mask |= InotifyConstants.IN_CLOSE_NOWRITE
        ...
    return event_mask
```

`EVENT_FILTER` has neither `FileClosedEvent` nor `FileClosedNoWriteEvent`, so the kernel never sends Trail `IN_CLOSE_WRITE` or `IN_CLOSE_NOWRITE`. A watchdog observer without the filter saw the gpkg write end with one `closed` event. Trail sees an `opened` followed by `modified` events that simply stop, and it cannot know that the write is over. It also cannot tell whether an `opened` began a read or a write, since only the kind of close says which.

Leaving out the closes also hides opens. When the gpkg already exists, GDAL opens it once to inspect it, closes it, and opens it again to write. With no close requested in between, the two `IN_OPEN` notifications are identical and adjacent, and the kernel merges them. Trail logged one `opened` for the two opens.

### Other programs read the new file

The writer opened `streets_projected.gpkg` once. In the Slack trace a second `opened` arrived at 22:30:27.920, 0.6 s after the last write, so another process opened the file. Writing the same file in three directories showed which process it was:

| Directory | Open and read-only close after the write |
|---|---|
| A directory under `/tmp` | none |
| `~/trail-probe-home3` | 0.73 s after the writer's close. A run in another such directory gave 0.63 s. |
| `~/trail-probe-home2/node_modules` | none |

Baloo indexes `$HOME` on this machine, and `~/.config/baloofilerc` excludes every folder named `node_modules`. The late read follows that rule, so it is Baloo reading the new file to index it. The demo directory is under `$HOME`, so Baloo reads every new file written there.

The demo directory is also inside the Syncthing folder that mirrors this repository. By default Syncthing scans a changed file 10 s after the change and reads it to compute its hash. That should add another `opened` about 10 s after each write, but it was not measured here.

The opens around the rename in the Slack trace come from the same kind of reader. A rename is one `rename` call. A traced `os.rename` and a traced rename through jupyter_server's contents manager, which JupyterLab's file browser uses, each gave exactly one `moved` and no opens. The opens just before and after the rename in the Slack trace came from another process. Baloo and Dolphin, KDE's file manager, are the likely ones, but they were not isolated.

inotify does not say which process caused a notification. Trail therefore cannot tell these reads apart from a read by the kernel.

### The `.~` files come from Jupyter's own saves

Of the packages involved, which are GDAL, pyogrio, geopandas, jupyter_server, jupyter-collaboration and JupyterGIS, only jupyter_server writes files whose names start with `.~`. Its contents manager names the backup file with [path_to_intermediate](/home/arstneio/micromamba/envs/jupytergis_dev/lib/python3.13/site-packages/jupyter_server/services/contents/fileio.py:88):

```python
def path_to_intermediate(path):
    """Name of the intermediate file used in atomic writes.

    The .~ prefix will make Dropbox ignore the temporary file."""
    dirname, basename = os.path.split(path)
    return os.path.join(dirname, ".~" + basename)
```

`atomic_writing`, in the same file, uses this name each time the server saves a file through its contents API. It copies the existing file to the `.~` path, rewrites the original in place, flushes it to disk, and deletes the copy. A traced save of `map.jGIS` through `FileContentsManager.save` gave these events with Trail's filter:

| File | Events |
|---|---|
| `map.jGIS` | `opened` to copy it, `opened` to rewrite it, `modified` |
| `.~map.jGIS` | `created`, `opened`, `modified`, `deleted` |

The server saves this way when a notebook autosaves. It also saves this way when jupyter-collaboration, the server extension behind real-time collaboration, writes a shared document back to disk, which it does by calling the contents manager's `save` ([loaders.py](/home/arstneio/micromamba/envs/jupytergis_dev/lib/python3.13/site-packages/jupyter_server_ydoc/loaders.py:221)). A `to_file` call in a cell writes the file directly and never reaches the contents API. None of the traced gpkg writes created a `.~` file. The `.~` activity in the demo directory was most likely `.~Untitled.ipynb` from autosaves of the notebook and `.~map.jGIS` from saves of the map. If `.~streets_projected.gpkg` itself appeared, the server saved the gpkg, for example from an editor tab opened on it in JupyterLab.

The demo's `.offtrail` ignores `.*`, so Trail tracks none of these backups and drops their events. The same goes for `.jupyter_ystore.db`, the SQLite store that jupyter-collaboration keeps in the same directory and writes on every document change.

### Other formats

Each format writes in its own pattern, but each produced a burst of `modified` events and none produced a single event. The table lists what Trail's filter lets through for writes of the same frame:

| Write | Events for the output file | Other files the write touches |
|---|---|---|
| gpkg, new file | `created`, `opened`, 13 `modified` | `-journal`, created and deleted 12 times |
| gpkg, over an existing file | `opened`, 8 `modified` | `-journal`, created and deleted 8 times |
| geojson | `created`, `opened`, 205 `modified` | none |
| shapefile | `created`, `opened` and 1 to 63 `modified` on each part | five separate files: `.shp`, `.shx`, `.dbf`, `.prj` and `.cpg` |
| fgb | `created`, `opened`, 4 `modified` | `_temp.fgb`, created, written and deleted |
| parquet | `created`, `opened`, 10 `modified` | none |
| csv from pandas | `created`, `opened`, 9 `modified` | none |
| rename, with `os.rename` or JupyterLab | `moved` | none |

Writing a gpkg over an existing one does not delete and recreate the file. The write opens the existing database and rewrites it in place, so the file keeps its inode and Trail keeps its asset. The geojson is the noisiest case. GDAL writes its text through a buffered stream in at least 209 chunks. Most likely the time spent formatting text between chunks lets the reader drain the queue, so few notifications merge.

## What Trail logs today

Trail logs at most one `WatchdogEvent`, its record of a filesystem event, for each watchdog event it receives. An `AddAssetEvent` records that Trail started tracking a file. Trail sessions in two scratch projects, one whose `.ontrail` takes `*.gpkg` and one that takes `*.csv`, logged these entries:

| Write | Trail's log |
|---|---|
| gpkg at a new path | `AddAssetEvent`, `opened`, 12 `modified` |
| gpkg written over the same path | `opened`, 8 `modified` |
| gpkg renamed | `moved` |
| csv deleted, then written again at the same path | `deleted`, `created`, `opened`, `modified`, all on the same asset |
| csv replaced by renaming `.a.csv.tmp` onto it | `moved` from `.a.csv.tmp` to `a.csv` |

For a new path, Trail does not log the `created` event. Trail's consumer waits 0.1 s for a burst of events to arrive, then applies them in order. When it applies `created`, no asset exists for the path, so [WatchdogSession.consume](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:862) scans the path instead, and `.ontrail` takes it as a new asset. The `AddAssetEvent` records the size and mtime at the moment of that scan. The gpkg write took 18 ms, well inside the 0.1 s wait, so the recorded size was final. A write that takes longer than the wait would leave a partial size as the asset's baseline. The scan also gives the `AddAssetEvent` a timestamp 0.1 s later than the `opened` logged after it.

The Slack trace shows `created` because its path was already a tracked asset. The trash shows that `streets_projected.gpkg` had been written and moved to the trash several times that afternoon. A deleted asset stays in Trail, so the next `created` at its path found it and was logged, as in the csv row above.

The last row of the table is a separate problem. Many programs save a file by writing a temporary file and renaming it onto the original. Syncthing also delivers changes from another device this way, through a hidden `.syncthing.<name>.tmp` file. Trail logs such a save as a move from the temporary name, but the asset did not move. Its content was replaced.

### Trail already folds events for directory assets

A directory asset, or dirasset, is a folder that Trail tracks as one asset, such as a `.zarr` store. Writing one can touch thousands of files, so Trail never logs their events one by one. Trail keeps an [Access](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:91) for each dirasset in use. An access counts the events of each type inside the dirasset and remembers when the first and last arrived. [Accesses](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:143) holds the open access of every dirasset. The watchdog thread counts each event into its access. The consumer closes an access after `idle_gap`, 30 s without an event, and logs it as one [DirectoryAccessEvent](/home/arstneio/PyCharmProjects/trail/src/trail/event.py:475). The example in its docstring shows a write to a zarr store:

```text
DirectoryAccessEvent
    timestamp: 21:24:58.104
    src_path: /tmp/tmp69jvvrsu/cube.zarr
    event_type: modified
    started: 21:24:57.862
    count: opened 1, modified 72
```

Before a dirasset is moved, deleted or untracked, [WatchdogSession.flush](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:922) logs its open access, so the log stays in order. Events on a file asset bypass all of this. [Handler.count](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:378) returns early for any path outside a dirasset, and the event is queued to be logged on its own.

## What one event per write means

The goal is one log event for each write session. A write session starts with the first event on a path. It ends when the writer has closed the file and the path has stayed quiet for a short time. The folded event keeps what is reliable about the session: when it started, when its last write or write close arrived, and the file's size and mtime at the end. A read that falls inside the session does not move its end. It leaves out event counts, since inotify merges them arbitrarily. Its type follows from what the session contained:

| The session contained | Folded event |
|---|---|
| a creation | `created` |
| writes, or a close after writing, and the size or mtime changed | `modified` |
| a close after writing, and the size and mtime did not change | none, or `opened` |
| only opens and read-only closes | `opened` |

The size and mtime check is needed because `IN_CLOSE_WRITE` fires whenever a file opened for writing is closed, even if nothing was written. The overwrite trace has an example. GDAL opened the existing gpkg for writing only to inspect it, and the close still raised `IN_CLOSE_WRITE`.

Moves and deletions stay single events. They change the asset's path or end the asset, and inotify already reports each as one notification. A session that is open on the path is logged first, as `flush` does for dirassets.

## Ways to fold

Three rules can decide where a session ends. They differ in how long the log waits, in what they need from the kernel, and in which platforms they work on.

### A quiet period

The simplest rule ends a session once its path has been quiet for a fixed time. It needs no new event types and works on every platform watchdog supports. Dirassets already use it with their 30 s `idle_gap`. Its weakness is the length of the period. A short period splits a write that pauses, such as a raster written tile by tile with computation between tiles. A long period delays every log entry by the full period and merges unrelated activity into one session.

### The writer's close

`IN_CLOSE_WRITE` marks the end of a write exactly. In every write traced here, the writer's last close of the output file came within 18 ms of its first open, and nothing wrote to the file after that close. Ending a session at the close keeps the delay short and needs no tuned period. It has three limits:

- It needs the close events in `EVENT_FILTER`.
- watchdog reports opens and closes only on Linux. Its macOS and Windows backends report neither.
- A writer can hold a file open for a long time, for example a notebook that keeps a GDAL or h5py dataset open across cells. The close then comes long after the writes.

Counting opens against closes, to find when the last handle closes, does not work. inotify merges identical adjacent notifications, so two opens with nothing between them arrive as one, as the overwrite showed. A close should be read as a sign that the writer may be done, not as the end of a matched pair.

### Recommended: the close, with a quiet period as fallback

The recommended rule combines the two. A session on a path ends at the first of these:

1. A short settle period passes with no new event after an `IN_CLOSE_WRITE` or `IN_CLOSE_NOWRITE`.
2. A longer quiet period passes with no event at all.

The settle period absorbs trailing activity, such as the journal's last deletion and quick reads by indexers. Baloo read the new file 0.63 to 0.73 s after the writer closed it. A settle period of about 1 s folds that read into the write session instead of logging it as a separate `opened`. The longer period covers writers that keep the file open and platforms with no close events. If a write resumes after the longer period has ended its session, the new writes start another session. One long write then gives two `modified` events, which is acceptable.

For the traced gpkg in `/tmp`, this rule logs the asset's registration and one `created` event about 1 s after the write. In the demo directory, Baloo's read restarts the settle period, so the event is logged about 1.7 s after the write. Either way the event starts at 0 ms, ends at 18 ms with the writer's close, and records the final 487424 bytes. Trail logs about fifteen events for the same write today.

Some cases need a closer look:

| Situation | What the rule does |
|---|---|
| The writer keeps the file open | The longer quiet period ends the session. Later writes start another. |
| The writer is killed | The kernel closes its files, so `IN_CLOSE_WRITE` still arrives. |
| The file is written through `mmap` | inotify reports no `IN_MODIFY` for writes through a memory map. The writer's close still ends the session. |
| The file is opened for writing but not changed | The size and mtime check finds no change. |
| Two programs use the file at once | Their events fold into one session. inotify cannot separate them. |
| macOS or Windows | Only the longer quiet period applies. |

## Folding across files

The rule above folds the events on one path. Several of the traced writes spread over more than one file:

| Pattern | Extra files | Effect in Trail today |
|---|---|---|
| SQLite rollback journal, used by gpkg, sqlite and mbtiles writers | `<name>-journal` | Untracked, so its events are dropped. Nothing is lost. |
| SQLite write-ahead log | `<name>-wal` and `<name>-shm` | Writes land in `-wal` and reach the database only at a checkpoint, so Trail sees them late. |
| shapefile | `.shp`, `.shx`, `.dbf`, `.prj` and `.cpg` | Five assets, each with its own events. |
| fgb with a spatial index | `<name>_temp.fgb` | Untracked, so its events are dropped. |
| jupyter_server save | `.~<name>` | Ignored by `.offtrail`. The original's own events fold correctly. |
| replace by rename | a temporary file renamed onto the original | Logged as a `moved` of the original. |

Two of these need work. A rename from an untracked path onto a tracked asset should be logged as `modified` of that asset, since the asset's path did not change. The shapefile parts could be grouped into one asset, much as a dirasset groups its children by name. Write-ahead logs appear only when a program turns them on. GDAL does not by default. QGIS turns them on for gpkg files it edits through its `qgis/walForSqlite3` setting, but that was not tested here.

## How this fits into Trail

The following functionalities would give Trail one event per write. They are listed in the order each depends on the one before.

1. Trail requests close events. `EVENT_FILTER` gains `FileClosedEvent` and `FileClosedNoWriteEvent`. Dirasset counting then has to handle the new types too. An `Access` counts only the five event types in [OPS](/home/arstneio/PyCharmProjects/trail/src/trail/event.py:30) and would fail on `closed`.
2. Trail folds file events by path on the consumer's side. The consumer already knows which paths are tracked and runs the `.ontrail` scan. The watchdog thread knows only the dirasset roots. A new file is not an asset until its session ends, so a session is keyed by its path, not by an asset id. The consumer already sleeps until the next dirasset access is due to close, and the session deadlines can join that wait.
3. Trail logs an open session before it logs a move, a deletion, an untrack or the end of a run. Trail already does this for dirasset accesses.
4. Trail registers a new file when its session ends, not on its first event. The `AddAssetEvent` baseline is then the finished file, and the folded `created` follows it in the log.
5. Trail logs one event per session with its start, its end, and the final size and mtime. One choice is to widen `DirectoryAccessEvent` to cover files. The other is a sibling event for files. Either way the counts stay out of file events.
6. Trail logs a rename from an untracked path onto a tracked asset as `modified`.

The traces from this report can drive tests directly. `tests/test_session.py` already feeds arrivals to a session through a stub loop. A recorded sequence, such as the gpkg write followed by Baloo's read, can be replayed with fixed timestamps and checked for exactly one folded event.

Four choices remain open:

- The settle period. About 1 s absorbs Baloo's read. A shorter period logs sooner but logs indexer reads as separate `opened` events.
- The longer quiet period. Dirassets use 30 s. A file session could use less, since a close normally ends it first.
- Whether read-only sessions are logged. They record reads by the kernel, which matter for provenance. They also record reads by Baloo, Syncthing and file managers, which Trail cannot tell apart from the kernel's.
- Whether to group shapefile parts now or later.

## What folding does not solve

Folding gives one event per write, but it does not say who wrote. inotify carries no process id. A read by the kernel looks the same as a read by Baloo, and a write by the kernel looks the same as a write by QGIS or Syncthing. Two ways to attribute a session remain:

- Trail can match a session's start and end against the kernel's cell executions, as Maryam suggested in the discussion. IPython's history database has no time for each input. The [IPython history report](/home/arstneio/PyCharmProjects/trail/notes/11-3-1-ipython-history-report.md) proposes live execution events with start and end times from `pre_run_cell` and `post_run_cell`. A folded session gives the interval to match against them. A match shows correlation, not cause.
- fanotify, the kernel's other notification interface, reports the process that caused each event. Without `CAP_SYS_ADMIN`, fanotify_init(2) says a listener does not receive that process id unless the listener caused the event itself. It does not help an unprivileged Trail.
