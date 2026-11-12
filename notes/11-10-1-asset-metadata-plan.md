# Plan: `Asset.metadata`, a summary of each tracked file that lets Trail say what changed

This is a proposal. Nothing in it is in `src/` yet. It follows the [asset metadata discussion](/home/arstneio/PyCharmProjects/trail/notes/11-9-1-asset-metadata-discussion.md) of 11-9 and replaces the snapshot design of the deferred [file events plan](/home/arstneio/PyCharmProjects/trail/notes/10-29-3-file-events-plan.md).

Code blocks marked **Prototype** ran against the real `trail` package on 2026-11-11, at commit `0de8c21` plus the uncommitted comments on the `triage` branch. Their results are under [Observed results](#observed-results). Code blocks marked **Proposed** were not run in that form. The prototype lived in a temporary directory. The code in this note is that code, with its imports and one class name changed to the target modules.

## TLDR

### The goal

Trail can say today that `parks.qgs` was modified. It cannot say that a layer was added to it, or that its CRS changed. The same holds for a parquet file that gained a column, or a geotiff that was reprojected.

The 10-29 plan answered this by storing a parsed copy of each project file under `.trail/files/`. That was deferred, since it moved Trail toward version control. The 11-9 discussion settled on something smaller: keep one small summary of each file in memory, read the summary again after the file changes, and compare the two. No earlier version of the file is stored.

This plan turns that decision into a design. It covers where the summary lives, how a format defines its summary, when Trail compares, what it logs, how it words the result, and how a notebook cell triggers a check.

After the plan is implemented, saving a geopackage with one more column, more rows and a new CRS logs one event with these lines. They are real output from the prototype:

```text
layer 'cities' CRS EPSG:4326 → EPSG:3857
layers.cities.bounds: [-59.54149490410117, -59.97719980711878, 59.9401622708432... → [-6628128.893794836, -8394663.426546443, 6672508.34205645...
layer 'cities' features 500 → 525
column 'country' added in layer 'cities'
```

Details: [Where things stand](#where-things-stand).

### What exists today

**Current behavior.**

- Trail logs one `WatchdogEvent` for each filesystem notification about a tracked asset. One save gives several `modified` events. None says what changed inside the file.
- `AddAssetEvent` records `st_size` and `st_mtime` once, when an asset is tracked. Nothing compares against them later.
- A directory asset, such as a zarr store, already folds its notifications into one `DirectoryAccessEvent`.
- The watcher runs only in a process that has a running asyncio event loop and holds the project lock. A notebook kernel has such a loop. A plain script or terminal IPython does not.
- Trail has no IPython hook. Nothing in `src/` mentions `post_run_cell`.
- The log is replayed by class name. A record whose class is not imported makes the whole log unreadable.

### How the implementation is laid out

**Proposed change.** Three existing modules gain a few lines each, and six modules are new.

| Module | Status | What it holds |
|---|---|---|
| `src/trail/metadata/__init__.py` | new | `Metadata`, the namespace on every asset, with `Stat`, `Change`, `Capture` and the comparison function `diff` |
| `src/trail/metadata/reads.py` | new | `Reads`, which reads files off the event loop while the watcher runs |
| `src/trail/metadata/geospatial.py` | new | the readers for the geospatial preset |
| `src/trail/metadata/general.py` | new | the readers for the general and ml presets |
| `src/trail/jupyter/metadata.py` | new | the readers for qgis and jupytergis projects |
| `src/trail/ipython.py` | new | the IPython extension |
| `src/trail/asset.py` | changed | one line that puts `metadata` on `Asset`, and `Assets.observe` |
| `src/trail/event.py` | changed | `MetadataEvent` |
| `src/trail/session.py` | changed | `WatchdogSession` creates a `Reads` and feeds it |

How they connect: something asks Trail to look at a file. That is a notebook cell through the extension, the watcher through `Reads`, or a direct call. Each ends in the same two calls on the asset's `Metadata`. `capture` reads the file's summary. `accept` compares it with the last one and logs a `MetadataEvent`.

```text
src/trail/ipython.py     post_run_cell ──> Assets.observe ──┐
                                                            ├─> Metadata.capture ─> Metadata.accept ─> MetadataEvent
src/trail/session.py     consume ──> Reads ─────────────────┘      reads the file     compares, logs      in the log
```

Details: [How it fits together](#how-it-fits-together).

### The namespace and its names

**Recommendation.** `asset.metadata` is a namespace, and `asset.metadata.dict` is the summary.

**Proposed.**

```python
# new: src/trail/metadata/__init__.py
class Metadata(Node):
    stat: Stat | None

    def read(self) -> Summary: ...

    def observe(
            self,
            basis: str = 'manual',
            *,
            is_forced: bool = False,
    ) -> MetadataEvent | None: ...

    @property
    def dict(self) -> Summary: ...


# changed: src/trail/asset.py
class Asset(Entry):
    metadata = Metadata.descriptor()
```

`Metadata.descriptor()` follows `Session.descriptor()`, which the code already uses for `watchdog.session`. Each asset gets its own `Metadata` object on first access.

| Your option | Verdict | Reason |
|---|---|---|
| `asset.metadata -> dict` | No | It leaves no place for the stat, the reader, or the comparison. Every caller would hold the stored object itself, so the storage could never change. |
| `asset.metadata.dict -> dict` | Yes | The name promises the type that is returned, not the type that is stored. Storage stays private and can change later. |
| `asset.meta.data` | No | `data` already means two other things in the code: the id-to-record mapping of a `Collection`, and the payload of a `JupyterEvent`. |
| `m_time`, `m_size` | Use `st_mtime_ns`, `st_size` | `Asset.st_size`, `Asset.st_mtime` and `AddAssetEvent.st_size` already follow `os.stat`. They live together as `asset.metadata.stat`. |

Three things to know about these names:

- A property named `dict` shadows the builtin inside the body of `Metadata`. An annotation there that spells `dict[...]` would resolve to the property. The class body therefore uses type aliases. This is contained in one class, and a format's own class is not affected. If you would rather not carry the trap, `asset.metadata.summary` is the alternative.
- `Entries.is_metadata(path)` already uses the word for Trail's own files under `.trail/`. The two do not collide in code. They do collide for a reader.
- Three methods in `asset.py` and one in `session.py` name a local variable `metadata` for an `os.stat` result. Rename those locals to `stat` when the namespace lands. Your pending comment at [asset.py:153](/home/arstneio/PyCharmProjects/trail/src/trail/asset.py:153) already asks for `fingerprint` to become `stat()`.

`asset.metadata.stat` is the stat **recorded** when the summary was read. `asset.st_size` stays the **live** value.

Details: [Metadata: the namespace and the registry](#metadata-the-namespace-and-the-registry).

### A format is a Metadata subclass, not an ExtEvent subclass

**Recommendation.** Your sketch put `def metadata(self) -> dict` on an `ExtEvent` subclass. Keep the shape, and move it from the event to the namespace.

**Prototype.** The whole definition of a format:

```python
class ToyMetadata(Metadata):
    suffixes: ClassVar[tuple[str, ...]] = ('.toy',)

    def read(self) -> Summary:
        text = self._asset.path.read_text(encoding='utf-8')
        out = json.loads(text)
        return out
```

`asset.metadata` for a `.toy` file is then a `ToyMetadata`, and `self._asset` is the asset. The class registered itself for its suffix when it was defined.

Three findings from the prototype decide against the event class:

1. **Replay breaks.** A log that holds an event of a class the reading process never imported cannot be opened at all: `ValueError: Unknown event class: 'ToyEvent'`. A format defined in a notebook would make the log unreadable to the CLI. With one `MetadataEvent` class for every format, the log replays without any format class.
2. **The reader has to run before any event exists.** An event is the record of a comparison. The summary must be read first, so `self` cannot be an event.
3. **An event subclass needs boilerplate that fails quietly.** An `Event` subclass written without `@dataclass(kw_only=True, slots=True, repr=False)` registers itself, then rejects its own fields: `TypeError: Event.__init__() got an unexpected keyword argument 'src_path'`.

What this gives up: a class per format in the console's `cls=` filter. `format=qgis` filters the same events.

Details: [Defining a format](#defining-a-format), [The designs that were compared](#the-designs-that-were-compared).

### How a comparison runs

A filesystem notification is treated only as a request to look. Trail never assumes that the file it reads is the version the notification was about.

**Proposed.** The method every check ends in, with its error handling left out:

```python
# new: src/trail/metadata/__init__.py
class Metadata(Node):
    def observe(
            self,
            basis: str = 'manual',
            *,
            is_forced: bool = False,
    ) -> MetadataEvent | None:
        # the stat is unchanged, so the file is not read
        if (
            not is_forced
            and not self.is_stale()
        ):
            return None
        # stat, read(), stat again: raises Unstable if the two stats differ
        capture = self.capture()
        # compares with the last summary, logs a MetadataEvent, keeps the new summary
        out = self.accept(capture, basis)
        return out
```

`basis` is a short word for what asked for the check, such as `cell` or `watchdog`. It is stored in the event.

This answers your question from 11-9 about the stale `FileClosedEvent`. Suppose QGIS saves `A`, then `B`, then `C` before Trail reads. The late notification for `B` makes Trail read `C`, and it logs one difference from `A` to `C`. The late notification for `C` makes Trail read again, find the same summary, and log nothing. The prototype shows exactly that: one event, then none. The intermediate `B` is not recoverable, and the event's timestamp is when Trail read the file.

The first read of a file is its baseline and logs nothing. A file that cannot be parsed keeps the last good summary, so the next good save is compared with it. A result that is older than the last one accepted is refused.

One limit to know: the baseline is what the file held when Trail first read it. A file that is tracked and written in the same notebook cell gets its state after the write as its baseline, so that write has no change rows.

Details: [Observing a file](#observing-a-file).

### What is logged

One `MetadataEvent` per observed difference, of one class for every format.

**Proposed.**

```python
# changed: src/trail/event.py
@dataclass(kw_only=True, slots=True, repr=False)
class MetadataEvent(Event):
    src_path: str
    format: str = ''
    basis: str = ''
    st_size: int | None = None
    st_mtime_ns: int | None = None
    changes: list[Change] = field(default_factory=list)
    more: int = 0


# new: src/trail/metadata/__init__.py
class Change(NamedTuple):
    path: tuple[str, ...]
    verb: str
    old: JSONValue = None
    new: JSONValue = None
    label: str = ''
```

A row in the log:

```json
{"path": ["crs"], "verb": "updated", "old": "EPSG:4326", "new": "EPSG:3857"}
```

The rows are produced by one generic comparison of two dicts. It reports `added`, `removed`, `updated` and `reordered` at a path of keys. For that to read well, a format keeps collections as dicts keyed by a stable id or name: layers by id, columns by name. Then an added layer is one `added` row and not a rewritten list.

An event is logged when the summary changed. When no watcher runs in the process, it is also logged with no rows for a file whose size or mtime changed. That covers files with no reader, such as a `.txt`. With a watcher running, the filesystem events already say that the file changed.

Details: [MetadataEvent: the log record](#metadataevent-the-log-record), [Change and the comparison](#change-and-the-comparison).

### How a change is worded

**Recommendation.** Do both, in layers. Store structure, and word it only when it is shown.

| Layer | What the format writes | Example |
|---|---|---|
| Generic, always available | nothing | `layers.L2.provider (Roads): 'ogr' → 'gdal'` |
| A table of phrases | one line per key that deserves better wording | `layer 'Roads' opacity 1 → 0.5` |
| A method | `describe`, for anything a phrase cannot say | free |

**Proposed.** The second layer, for qgis:

```python
# new: src/trail/jupyter/metadata.py
class QGISMetadata(Metadata):
    named = ('layers',)
    phrases = {
        ('crs',): 'project CRS {old} → {new}',
        ('layers', '*', 'opacity'): "layer '{label}' opacity {old} → {new}",
    }


# what the console calls for each row of an event
line = QGISMetadata.describe(change)
```

The log never stores a sentence. It stores the rows, and a row carries the entity's display name as `label`, so the wording needs neither the file nor the old summary. Wording can then improve without rewriting logs. A process that never imported the format still shows the generic line.

Details: [Messages](#messages).

### The check with no watcher

`Assets.observe` is the reconciliation the Trail initiative describes: compare each registered file with its last recorded state. It needs no watcher, no event loop and no thread.

**Proposed.**

```python
# changed: src/trail/asset.py
class Assets(EntryCollection[Asset]):
    def observe(self, basis: str = 'manual') -> list[MetadataEvent]:
        events = []
        for asset in self:
            event = asset.metadata.observe(basis)
            if event is not None:
                events.append(event)
        return events
```

It takes one stat per asset and reads only the files whose size, mtime or inode changed. Measured on this machine: 1,000 stats take 4 ms and 100,000 take 97 ms. A check of 29 assets, 5 of them changed, took 18 ms including the reads.

It follows the choice `PROJECT_PLAN.md` recommends for its status check: a check logs what it finds and moves the baseline forward. It does not yet record deleted or moved files.

Details: [Assets.observe: the check with no watcher](#assetsobserve-the-check-with-no-watcher).

### When the watcher runs

With the watcher running, one save produces a burst of notifications. `Reads` waits for a file to go quiet, reads its summary in a worker thread, and compares it back on the event loop.

**Proposed.** The one call that feeds it, and the method at its centre, simplified:

```python
# changed: WatchdogSession.consume in src/trail/session.py
entry = event.apply(trail)
if (
    isinstance(entry, Asset)
    and event.event_type in READ_AFTER
):
    self.reads.add(entry)


# new: src/trail/metadata/reads.py
class Reads(Node):
    async def read(self, identifier: str) -> None:
        basis = self.bases.pop(identifier, 'watchdog')
        metadata = self._trail.assets.id2entry[identifier].metadata
        # the file is read in a worker thread
        capture = await asyncio.to_thread(metadata.capture)
        # the comparison and the log write run on the loop
        metadata.accept(capture, basis)
```

Observed with real filesystem events: a file saved as `B` and then as `C`, 50 ms apart, gave one read and one event with the rows from `A` to `C`. Forty rewrites of the same content gave 85 notifications, one read and no event.

At startup the watcher is placed first. The baselines of the assets that have a reader are then read in the background, two at a time, behind any file that has just changed. This is the order the 11-9 note decided.

Details: [Reads: the watcher path](#reads-the-watcher-path).

### Checking after a notebook cell

A notebook's cells run in a kernel, a Python process that stays alive between cells. IPython is the interactive Python inside it. An IPython extension can register a function that IPython calls each time a cell finishes. That hook is named `post_run_cell`. The Trail initiative uses it as the moment to check, without reading the cell's code.

**Proposed.** The lock handling is left out here:

```python
# new: src/trail/ipython.py
def load_ipython_extension(shell: InteractiveShell) -> None:
    trail = Trail(Path.cwd())

    def post_run_cell(result: ExecutionResult) -> None:
        observe(trail)

    shell.events.register('post_run_cell', post_run_cell)


def observe(trail: Trail) -> None:
    session = trail.watchdog.session
    if session.is_active:
        for asset in trail.assets:
            if asset.metadata.is_stale():
                session.reads.add(asset, 'cell', is_immediate=True)
        return
    trail.assets.observe('cell')
```

`%load_ext trail.ipython` loads it. Outside a Trail project it registers nothing.

- With no watcher, the hook is the check: `trail.assets.observe('cell')`.
- With the watcher, the hook still stats every asset. A cell blocks the kernel's event loop while it runs, so the watcher has not yet processed what the cell wrote. The hook hands each changed asset to `Reads`, which reads it as soon as the loop resumes. In the prototype that read began 2 ms after the cell returned.

In a real kernel the hook runs on the main thread, and a slow hook delays the cell by its full duration. That is why the reads are handed off.

The hook says when Trail looked. It is not evidence that the cell wrote the file, and the event says only `basis: cell`.

One thing is not settled: the extension opens its own `Trail`. A second `Trail` on the same project in the same kernel does not share its assets. See [Open questions](#open-questions), question 3.

Details: [The IPython extension](#the-ipython-extension).

### Dicts now, something else later

A dict is the right boundary type. A reader returns one, and the log is JSON. What should not be fixed is how the summary is **stored** between reads.

Three things stay stable, so the storage can change without touching a reader, an event or a user's format:

1. `read()` returns a JSON-shaped dict.
2. `asset.metadata.dict` returns a dict. It is a copy, so a caller cannot change what Trail holds.
3. `compare(old, new)` returns `Change` rows.

Measured for a summary of 100 entries: 36.9 KiB as a plain dict, 22.3 KiB with interned strings, 6.4 KiB as canonical JSON text. The plan stores interned dicts now. If memory matters later, store the JSON text and parse it only when a file changes. That is a change inside `Metadata` alone.

Details: [Representation and memory](#representation-and-memory).

### Formats

Nineteen reader classes, covering 32 suffixes, were written and run on 29 files made with real writer libraries.

| Tier | Formats | Dependency |
|---|---|---|
| First | qgis (`.qgs`, `.qgz`), jupytergis (`.jgis`), parquet and geoparquet, geotiff, geopackage, zarr | stdlib and defusedxml, except parquet (pyarrow, imported inside the reader) |
| Next | feather, the shapefile parts, geojson, flatgeobuf, kml and kmz, gpx, netcdf, hdf5, file geodatabase, csv and tsv, npy and npz, xlsx, safetensors | stdlib, except feather, netcdf, hdf5 and file geodatabase |
| Described, not written | safe, n5, pmtiles, mbtiles | stdlib |
| Stat only | pickles (`.pt`, `.pkl`, `.joblib`), onnx, txt, json, yaml | none |

Most readers read only a header, a footer or a metadata member. A 153 MiB parquet file was summarized in 0.3 ms and a 232 MiB geotiff in 0.1 ms. Geojson, kml, gpx and the two project formats have no header and are parsed whole, up to a size limit.

Details: [Format readers](#format-readers).

### Order of work

Each step works on its own and is checked before the next.

1. The core: `Metadata`, `Stat`, `Change`, the comparison, `MetadataEvent`, `Assets.observe`. No watcher. **Done when** the 46 core checks of the prototype pass as tests.
2. The first two formats, qgis and jupytergis. **Done when** the 7 checks that need a real project file pass.
3. The watcher path, `Reads`.
4. The IPython extension.
5. The remaining readers.
6. Display in the console.
7. Changes made while Trail was not running. This step is not designed here.

Details: [Implementation steps](#implementation-steps).

### Decisions needed

The five that most need your decision:

1. `metadata` or `meta` as the attribute name, and `dict` or `summary` for the summary.
2. Whether a file with no reader should get a stat-only event from a check with no watcher.
3. Which `Trail` the IPython extension uses, so that a kernel has one.
4. Whether the folded write event of the [folding report](/home/arstneio/PyCharmProjects/trail/notes/11-4-3-event-folding-report.md) and `MetadataEvent` should become one record.
5. How a change made while Trail was not running is noticed.

Details: [Open questions](#open-questions).

### What was verified, and what was not

**Observed results.** The core design, the watcher path, the cell hook and all nineteen readers ran against the real package. 53 checks pass.

**Not done.** The two multi-agent workflows launched on 11-10 produced nothing. Every agent was stopped by the usage limit within minutes, on each of three attempts, before writing a file. The three competing designs were therefore not each prototyped and judged by a panel. One design was prototyped, and the specific claims that separate it from the other two were tested directly.

**Reviewed.** An independent reviewer audited the first draft against the code and the earlier notes. It found twelve design defects, among them a cell hook that did nothing while the watcher ran. All were fixed and re-run. A second reviewer then built Steps 1 and 2 from this note alone, in a copy of the package. 50 of its 51 checks passed, the existing tests gave the same result with the steps applied, and its findings were folded in. See [Review](#review) and [Evidence and its limits](#evidence-and-its-limits).

## Deep Dive

### Where things stand

This section records the current code and the earlier decisions the plan builds on. Everything here is **current behavior**.

**The namespace pattern.** Trail already has a descriptor that gives each owner its own lazily made object: [Session.descriptor](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:500) returns an instance made with `cls.__new__`, [Node.\_\_set_name\_\_](/home/arstneio/PyCharmProjects/trail/src/trail/node.py:101) records the attribute name, and [Session.\_\_get\_\_](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:514) keeps the owner's object in the owner's `__dict__`. It is used twice, as [Watchdog.session](/home/arstneio/PyCharmProjects/trail/src/trail/watchdog.py:33) and as [Jupyter.session](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/jupyter.py:20). `Metadata` follows the same pattern.

**Assets.** [Asset](/home/arstneio/PyCharmProjects/trail/src/trail/asset.py:25) is a dataclass with `kw_only=True, eq=False, repr=False` and no slots, so each asset has a `__dict__`. [Asset.fingerprint](/home/arstneio/PyCharmProjects/trail/src/trail/asset.py:123) stats the file on every access. [DirAsset](/home/arstneio/PyCharmProjects/trail/src/trail/asset.py:194) is a directory tracked as one dataset. Its `suffix2children` names the files that are part of it.

**Events and replay.** [Event](/home/arstneio/PyCharmProjects/trail/src/trail/event.py:59) keeps every subclass in `Event.classes` by class name. [Event.from_record](/home/arstneio/PyCharmProjects/trail/src/trail/event.py:128) looks the class up and raises `ValueError` at [line 145](/home/arstneio/PyCharmProjects/trail/src/trail/event.py:145) when it is missing. [JSONL.read](/home/arstneio/PyCharmProjects/trail/src/trail/event.py:551) does not catch it, so `Trail(...)` fails. [DirectoryAccessEvent.\_\_post_init\_\_](/home/arstneio/PyCharmProjects/trail/src/trail/event.py:501) shows the existing way to decode a field on replay: it turns `started` back into a datetime.

**The filesystem path.** watchdog calls [Handler.handle](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:335) on its observer thread. The handler never touches the Trail. It queues an [Arrival](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:83), and [WatchdogSession.consume](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:862) applies it on the asyncio loop, at [line 896](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:896). [EVENT_FILTER](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:55) subscribes to created, modified, deleted, moved and opened events. It does not subscribe to `FileClosedEvent`.

**When the watcher runs.** [WatchdogSession.start](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:652) starts a run only if an asyncio loop is running and the session gets the project [Lock](/home/arstneio/PyCharmProjects/trail/src/trail/lock.py). The lock is a file lock on `.trail/lock`. A second process, or a second `Trail` object in the same process, cannot take it. `session.is_active` says whether this process is watching.

**The baseline that exists.** [AddAssetEvent.\_stat](/home/arstneio/PyCharmProjects/trail/src/trail/event.py:243) records `st_size` and `st_mtime` when an asset is tracked. `st_mtime` is a float of seconds.

**Earlier decisions this plan keeps.**

| Decision | Source |
|---|---|
| One in-memory summary per supported asset. No earlier version of the file is stored. | 11-9 note, "Decision and startup behavior" |
| Reconcile with size, mtime and file identity. | same |
| Start the watcher first. Fill the summaries in the background with a small bounded pool. Queue only assets with a reader, and read assets with pending changes first. | same |
| If an asset changes before its first read, record the filesystem change and use the first stable summary as its baseline. | same |
| The base class is in Trail's core. Each submodule holds its own formats, starting with one file under `src/trail/jupyter/`. | your comment in the 10-29 plan |
| The entries of a change list have fixed keys and are a `NamedTuple`. | your comment in the 10-29 plan |
| The execution hook tells Trail when to check. It is not evidence that the cell caused the change. | the initiative |
| Dependencies stay minimal. | `PROJECT_PLAN.md`, section 8 |

**One earlier decision this plan only half applies.** Your comment in the 10-29 plan renamed `Event.classes` to `Event.name2cls`, so that the extension registry could be `ext2cls`. This plan uses both names on `Metadata`. It does not need the rename on `Event`. Step 1 lists the rename as an optional part, so the two classes read alike.

**What the 10-29 design had that this plan drops.** `Snapshot`, `Snapshots`, the `.trail/files/` layout, byte digests, the pointer file, crash recovery of the cache, and the `Missing` sentinel. The `Files` coordinator shrinks to `Reads`. The per-format `ExtEvent` classes become per-format `Metadata` classes and one event class.

### How it fits together

This section names every piece once, in the order data flows through them. Each has its own section after this one.

A tracked file has an `Asset`. The asset has a `Metadata`, reached as `asset.metadata`. The `Metadata` holds two things about the file as Trail last saw it: a `Stat` and a summary dict. Which `Metadata` subclass an asset gets is decided by its suffix, through the registry `Metadata.ext2cls`.

Something asks Trail to look at a file, and gives a `basis`: a short word for what asked. There are four askers:

| Asker | Basis | When |
|---|---|---|
| `Assets.observe`, a loop over every asset | `cell`, or `manual` | a notebook cell finished and no watcher runs, or a direct call |
| `Reads`, fed by the watcher's consumer | `watchdog` | a filesystem event was applied to an asset |
| `Reads`, fed at startup or by the cell hook | `startup`, `cell` | the watcher started, or a cell finished while the watcher runs |
| reading `asset.metadata.dict` | `access` | the first time only |

All four end in the same two calls on the asset's `Metadata`. `capture()` takes the stat, reads the summary and takes the stat again. `accept()` compares the new summary with the old one, logs a `MetadataEvent` if they differ, and keeps the new one. `capture` touches only the filesystem and may run in a worker thread. `accept` touches the Trail and runs on the thread that owns it.

The comparison is a function, `diff`, that walks two dicts and yields `Change` rows. A `MetadataEvent` stores those rows. When the event is shown, `Metadata.describe` turns each row into a line of text.

The modules import each other in one direction. `trail.metadata` imports only `trail.node`. `trail.asset` imports `trail.metadata` for the descriptor. `trail.event` imports both. `Metadata.accept` imports `MetadataEvent` inside the method, as `Assets.add` already does for `AddAssetEvent`.

**Prototype.** The imports of `src/trail/metadata/__init__.py`:

```python
from __future__ import annotations

import copy
import json
import sys
import time
import warnings
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, Self

from trail.node import Node

if TYPE_CHECKING:
    from trail.asset import Asset
    from trail.event import MetadataEvent
```

Four type aliases follow them:

```python
type JSONValue = None | bool | int | float | str | list[JSONValue] | dict[str, JSONValue]
type Summary = dict[str, JSONValue]
type Phrases = dict[tuple[str, ...], str]
type Registry = dict[str, type[Metadata]]
```

`Summary` is what a reader returns. `Phrases` and `Registry` exist for one reason. `Metadata` has a property named `dict`, and inside a class body a name defined in that body wins over a builtin. An annotation in the body of `Metadata` that spelled `dict[...]` would resolve to the property when a tool evaluates it. The reviewer showed this on the first draft: `typing.get_type_hints(Metadata)` raised `TypeError: 'property' object is not subscriptable`. With the aliases, no annotation in that body spells `dict`. The body of a format's own class does not define `dict`, so a format may spell it. `typing.get_type_hints(Metadata)` still fails afterward, with `NameError` for `Asset`. Every existing class that annotates `_parent` with an import made only for type checking fails the same way.

**Style.** The repository's lint, `ruff check` with its `pyproject.toml`, rejects `dict(key=value)` calls, so the code in this note uses dict literals. Every code block marked Prototype passes that lint.

### Stat: the recorded signature of a file

`Stat` is what Trail remembers about a file's filesystem state at the moment it read the summary. It lives in `src/trail/metadata/__init__.py` and is reached as `asset.metadata.stat`. `Metadata` uses it to decide whether a file needs reading again, and to detect a write that lands during a read.

**Prototype.**

```python
class Stat(NamedTuple):
    st_size: int
    st_mtime_ns: int
    st_ino: int
    st_dev: int

    @classmethod
    def take(cls, paths: Iterable[Path]) -> Self | None:
        size = 0
        newest = 0
        inode = None
        device = None
        for path in paths:
            stat = path.stat()
            size += stat.st_size
            newest = max(newest, stat.st_mtime_ns)
            if inode is None:
                inode = stat.st_ino
                device = stat.st_dev
        # an asset with no source has nothing to stat
        if inode is None:
            return None
        out = cls(
            st_size=size,
            st_mtime_ns=newest,
            st_ino=inode,
            st_dev=device,
        )
        return out
```

- `take` accepts several paths because a directory asset is described by several files. Sizes are summed and the newest mtime is kept. For a plain file there is one path.
- `take` returns `None` when it is given no path. An asset with no file to stat has no `Stat`. This is the case for a directory asset with no reader.
- `st_mtime_ns` is an integer of nanoseconds. A float of seconds cannot hold it exactly.
- `st_ino` and `st_dev` catch a file replaced by renaming a temporary file over it, even when size and mtime are equal.
- Two stats are compared with `==`.

**Limits.** A rewrite that keeps the size and lands in the same timestamp tick is invisible to a stat. On the tmpfs used for the prototype, a tick is a few milliseconds, and the tests wait 20 ms between writes for that reason. On filesystems with coarse timestamps the window is one or two seconds.

### Change and the comparison

`Change` is one difference between two summaries. `diff` is the function that produces them. Both live in `src/trail/metadata/__init__.py`. `Metadata.compare` calls `diff`, and `MetadataEvent` stores the result.

**Prototype.**

```python
class Change(NamedTuple):
    path: tuple[str, ...]
    verb: str
    old: JSONValue = None
    new: JSONValue = None
    label: str = ''

    def to_record(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            'path': list(self.path),
            'verb': self.verb,
        }
        if self.verb != 'added':
            out['old'] = self.old
        if self.verb != 'removed':
            out['new'] = self.new
        if self.label:
            out['label'] = self.label
        return out

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Self:
        out = cls(
            path=tuple(record['path']),
            verb=record['verb'],
            old=record.get('old'),
            new=record.get('new'),
            label=record.get('label', ''),
        )
        return out
```

| Field | Meaning |
|---|---|
| `path` | The keys from the top of the summary down to what changed, such as `('layers', 'L2', 'opacity')`. |
| `verb` | `added`, `removed`, `updated` or `reordered`. |
| `old`, `new` | The values. `added` has no `old`, and `removed` has no `new`. The verb says which side exists, so no sentinel for "missing" is needed. |
| `label` | The display name of the entity the path points into, such as a layer's name. It is filled at comparison time and stored, so a message never needs the file. |

`to_record` writes named fields, since `json.dumps` would write a `NamedTuple` as a bare list.

```python
def canonical(value: JSONValue) -> str:
    out = json.dumps(value, sort_keys=True)
    return out


def diff(
        old: JSONValue,
        new: JSONValue,
        path: tuple[str, ...] = (),
) -> Iterator[Change]:
    if (
        isinstance(old, dict)
        and isinstance(new, dict)
    ):
        for key, value in old.items():
            if key in new:
                yield from diff(value, new[key], (*path, key))
            else:
                yield Change(path=(*path, key), verb='removed', old=value)
        for key, value in new.items():
            if key not in old:
                yield Change(path=(*path, key), verb='added', new=value)
        return
    # json text tells True from 1 and 1 from 1.0
    if canonical(old) == canonical(new):
        return
    verb = 'updated'
    if (
        isinstance(old, list)
        and isinstance(new, list)
        and sorted(map(canonical, old)) == sorted(map(canonical, new))
    ):
        verb = 'reordered'
    yield Change(path=path, verb=verb, old=old, new=new)


def brief(value: JSONValue) -> str:
    out = repr(value)
    if len(out) > 60:
        out = f'{out[:57]}...'
    return out
```

The rules of `diff`:

- Two dicts are compared key by key. A key on one side only is one `added` or `removed` row that carries the whole value. It is not one row per field inside it.
- Anything else is compared as JSON text. That tells `True` from `1` and `1` from `1.0`, which `==` does not.
- Two lists with the same members in another order give `reordered`. Any other list difference is `updated` with both lists.
- Nothing inside a list is compared. This is why a format keeps collections as dicts keyed by id or name, and keeps a meaningful order as a separate list of ids.
- `diff` leaves `label` empty. `Metadata.compare` fills it, as the section on defining a format shows.

`brief` shortens a long value when a message is worded. The stored row keeps the full value.

### Metadata: the namespace and the registry

`Metadata` is the object behind `asset.metadata`. It lives in `src/trail/metadata/__init__.py`. Every asset has one, made on first access. Assets of a supported format get a subclass, and every other asset gets `Metadata` itself, which has a stat and an empty summary. The watcher, `Assets.observe` and the user all reach a file's summary through it.

The class is shown in three parts. This first part is the registry and the descriptor.

**Prototype.**

```python
class Metadata(Node):
    name: ClassVar[str] = ''
    suffixes: ClassVar[tuple[str, ...]] = ()
    phrases: ClassVar[Phrases] = {}
    # the top-level collections whose entries are keyed by id and carry a `name`
    named: ClassVar[tuple[str, ...]] = ()
    max_bytes: ClassVar[int | None] = None
    max_changes: ClassVar[int] = 50
    is_available: ClassVar[bool] = True
    ext2cls: ClassVar[Registry] = {}
    name2cls: ClassVar[Registry] = {}

    _parent: Asset
    stat: Stat | None = None
    error: str = ''
    is_observed: bool = False
    # counts the captures accepted, so a capture that began before the last one is refused
    generation: int = 0
    _summary: Summary | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        suffixes = cls.__dict__.get('suffixes', ())
        for suffix in suffixes:
            if (
                not suffix.startswith('.')
                or suffix != suffix.lower()
            ):
                raise TypeError(f'Invalid file suffix: {suffix!r}')
        if 'name' not in cls.__dict__:
            cls.name = (
                cls.__name__
                .removesuffix('Metadata')
                .lower()
            )
        # the last definition wins, so a notebook cell can be run again
        for suffix in suffixes:
            Metadata.ext2cls[suffix] = cls
        Metadata.name2cls[cls.name] = cls

    @classmethod
    def descriptor(cls) -> Self:
        out = cls.__new__(cls)
        return out

    def __get__(
            self,
            instance: Asset | None,
            owner: type,
    ) -> Self:
        if instance is None:
            return self
        cache = instance.__dict__
        suffix = instance.path.suffix.lower()
        cls = Metadata.ext2cls.get(suffix, Metadata)
        metadata = cache.get(self.__name__)
        # a move to another format, or a format defined since, starts over
        if type(metadata) is not cls:
            metadata = cls(instance)
            cache[self.__name__] = metadata
        return metadata

    def __delete__(self, instance: Asset) -> None:
        instance.__dict__.pop(self.__name__, None)
```

**Proposed change** in [asset.py](/home/arstneio/PyCharmProjects/trail/src/trail/asset.py:43). One line in the body of `Asset`, with no annotation, so the dataclass does not take it for a field:

```python
from trail.metadata import Metadata
```

```python
@dataclass(kw_only=True, eq=False, repr=False)
class Asset(Entry):
    ...
    is_directory: bool = field(default=False, repr=False)
    metadata = Metadata.descriptor()
```

The prototype attached the same descriptor from outside the package, with `Asset.metadata = Metadata.descriptor()` followed by `Asset.metadata.__set_name__(Asset, 'metadata')`. Inside the class body, Python calls `__set_name__` itself.

How it behaves:

- `ext2cls` maps a lowercase suffix to the class that reads it. `name2cls` maps a format's name to its class, for wording an event that names its format.
- A subclass registers the suffixes written in its own body. A subclass that inherits `suffixes` registers nothing.
- A format's `name` defaults to its class name without `Metadata`, in lowercase.
- **The last definition wins.** Running a notebook cell twice must not raise. A user's class may also take over a built-in suffix. This is deliberately looser than `Event.__init_subclass__`, which raises when another module defines the same class name.
- `__get__` picks the class from the asset's current suffix on every access. A move from `a.qgs` to `a.qgz` keeps the object and its baseline, since both suffixes map to one class. A move to another format gets a new object with no baseline.
- A format defined, or defined again, after an asset was first read takes effect on the next access. The asset then starts over with no baseline, since a summary read by the old class cannot be compared with one read by the new.
- `__delete__` drops an asset's object. `del asset.metadata` forgets its baseline.

The instance attributes default at class level:

| Attribute | Meaning |
|---|---|
| `stat` | The `Stat` at the last accepted read. `None` before it, and for an asset with nothing to stat. |
| `_summary` | The summary last accepted. `None` before it, and for a file with no summary. |
| `is_observed` | Whether a read was ever accepted. The first one is the baseline. |
| `generation` | How many reads were accepted. A read that began before the last accepted one is refused. |
| `error` | The last parse failure, as text. Empty after a good read. |

### Defining a format

This section covers the second part of `Metadata`: what a format can override. A format is a subclass with `suffixes` and `read`. Everything else has a default.

**Prototype.**

```python
class Metadata(Node):
    ...

    def read(self) -> Summary:
        return {}

    def sources(self) -> list[Path]:
        asset = self._asset
        # a directory's own stat does not change when a file inside it is rewritten
        if asset.is_directory:
            return []
        out = [asset.path]
        return out

    def label(
            self,
            path: tuple[str, ...],
            old: Summary,
            new: Summary,
    ) -> str:
        if (
            len(path) < 2
            or path[0] not in self.named
        ):
            return ''
        for summary in (new, old):
            entry = (
                summary
                .get(path[0], {})
                .get(path[1])
            )
            if isinstance(entry, dict):
                name = entry.get('name')
                if isinstance(name, str):
                    return name
        return ''

    def compare(
            self,
            old: Summary,
            new: Summary,
    ) -> list[Change]:
        out = [
            change._replace(label=self.label(change.path, old, new))
            for change in diff(old, new)
        ]
        return out

    @classmethod
    def describe(cls, change: Change) -> str:
        for pattern, phrase in cls.phrases.items():
            is_match = (
                len(pattern) == len(change.path)
                and all(
                    part in ('*', key)
                    for part, key in zip(pattern, change.path, strict=True)
                )
            )
            if is_match:
                out = phrase.format(
                    *change.path,
                    verb=change.verb,
                    old=change.old,
                    new=change.new,
                    label=change.label,
                )
                return out
        where = '.'.join(change.path)
        if change.label:
            where = f'{where} ({change.label})'
        if change.verb == 'updated':
            out = f'{where}: {brief(change.old)} → {brief(change.new)}'
        else:
            out = f'{where} {change.verb}'
        return out
```

| Member | A format overrides it to | Default |
|---|---|---|
| `suffixes` | name the suffixes it reads, lowercase, with the dot | none |
| `read()` | return the summary of the file at `self._asset.path` | `{}` |
| `sources()` | name the files whose stat decides whether the summary may have changed | the asset's own path for a file, and nothing for a directory |
| `named` | name the top-level collections whose entries have a `name` | none |
| `phrases` | give better wording for some paths | none |
| `max_bytes` | refuse files above a size, for a reader that parses the whole file | no limit |
| `compare()`, `label()`, `describe()` | replace the comparison, the labels or the wording | generic |

**Why a directory has no default source.** A directory's own size and mtime do not change when a file inside it is rewritten. The 11-9 note says so for zarr. A directory format therefore names its metadata files in `sources()`. A directory asset whose format has no reader has nothing to stat. Its changes are seen only by the watcher.

**What `read` must do.** These rules are the reader contract.

1. Read only headers, footers or metadata members. Never scan rows, pixels or chunks. A reader that has to parse the whole file sets `max_bytes`.
2. Return a small dict. Bound whatever can grow without limit, such as thousands of columns, with a count and a digest.
3. Return JSON-shaped values only, and the same dict for the same bytes. Leave out fields that change on every save without meaning, such as a save time or the writer's build string.
4. Key collections by a stable id or name. Keep a meaningful order as a separate list of ids.
5. Import a third-party package inside `read`, never at the top of the module.
6. Never write to the file or lock it.
7. Raise on a file that cannot be parsed. Do not return half a summary.
8. Keep credentials out. Strip passwords and tokens from connection strings and URLs.

Rule 7 has a limit that the truncation test made plain. A reader that reads only a header cannot tell that the data after the header is cut off. See [Observed results](#observed-results).

**Helpers for readers.** Readers share five small functions. They live at the top of `src/trail/metadata/geospatial.py`. `digest` gives a short hash of a value. `capped` replaces a mapping with more than `cap` entries by its count and a digest, which is how rule 2 is met. `crs_code` turns a PROJJSON CRS into a code such as `EPSG:4326`, or into a digest when it has none. `exact` reads a fixed number of bytes or raises. `local` strips the namespace from an XML tag. `canonical` comes from the core module.

**Prototype.**

```python
from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import os
import re
import struct
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO, ClassVar

from trail.metadata import JSONValue, Metadata, Summary, canonical
```

```python
def digest(value: JSONValue) -> str:
    text = canonical(value)
    out = (
        hashlib
        .blake2b(text.encode('utf-8'), digest_size=8)
        .hexdigest()
    )
    return out


def capped(
        mapping: dict[str, JSONValue],
        cap: int,
) -> dict[str, JSONValue]:
    if len(mapping) <= cap:
        return mapping
    out = {
        'count': len(mapping),
        'digest': digest(mapping),
    }
    return out


def crs_code(crs: JSONValue) -> str | None:
    if crs is None:
        return None
    if isinstance(crs, dict):
        identifier = crs.get('id')
        if isinstance(identifier, dict):
            out = f"{identifier.get('authority')}:{identifier.get('code')}"
            return out
    out = f'digest:{digest(crs)}'
    return out


def exact(
        file: BinaryIO,
        size: int,
) -> bytes:
    out = file.read(size)
    if len(out) != size:
        raise ValueError(f'Truncated: wanted {size} bytes, got {len(out)}')
    return out


def local(tag: str) -> str:
    # an XML tag without its namespace
    out = tag.rpartition('}')[2]
    return out
```

**The complete code for parquet**, as a user or Trail would write it:

```python
class ParquetMetadata(Metadata):
    name: ClassVar[str] = 'parquet'
    suffixes: ClassVar[tuple[str, ...]] = ('.parquet', '.geoparquet')
    max_columns: ClassVar[int] = 200
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('num_rows',): 'rows {old} → {new}',
        ('columns', '*'): "column '{1}' {verb}",
        ('geo', 'columns', '*', 'crs'): "geometry column '{2}' CRS {old} → {new}",
    }

    @staticmethod
    def geo(raw: bytes | None) -> JSONValue:
        if raw is None:
            return None
        declared = json.loads(raw)
        columns = {}
        declared_columns = declared.get('columns', {})
        for name, column in declared_columns.items():
            # an absent crs means OGC:CRS84; a null crs means unknown
            crs = 'OGC:CRS84'
            if 'crs' in column:
                crs = crs_code(column['crs'])
            columns[name] = {
                'encoding': column.get('encoding'),
                'geometry_types': column.get('geometry_types'),
                'crs': crs,
                'bbox': column.get('bbox'),
            }
        out = {
            'primary_column': declared.get('primary_column'),
            'columns': columns,
        }
        return out

    def read(self) -> Summary:
        import pyarrow.parquet as pq

        with pq.ParquetFile(self._asset.path) as file:
            metadata = file.metadata
            schema = file.schema_arrow
        columns = {
            field.name: str(field.type)
            for field in schema
        }
        declared = None
        if schema.metadata is not None:
            declared = schema.metadata.get(b'geo')
        out = {
            'num_rows': metadata.num_rows,
            'num_row_groups': metadata.num_row_groups,
            'columns': capped(columns, self.max_columns),
            'geo': self.geo(declared),
        }
        return out
```

The footer is read through pyarrow, which is imported inside `read`. No row group is read. `geo` decodes the geoparquet key of the footer.

**A missing package.** If `read` raises `ImportError`, Trail warns once for that format and treats its assets as having no summary. Their stat is still tracked. This path was **not run**.

**Labels.** `named` tells the comparison where display names are. For qgis, `named = ('layers',)` means that a change at `('layers', <id>, 'opacity')` gets the `name` of that layer as its label. The first version of the prototype inferred labels from any dict with a `name` key. That broke on a geopackage with a column called `name`, so labels are now declared.

**Limits.**

- A comparison that finds more than `max_changes` rows keeps the first 50 and counts the rest in the event's `more`. The baseline still moves forward, so the rows left out are not logged later.
- Only the last suffix of a name is used. `a.copc.laz` is looked up as `.laz`.

### Observing a file

This section covers the third part of `Metadata`: reading a file safely and deciding what to log. It uses `Stat`, `Change` and the format members above.

`capture` and `accept` are separate so that the slow part can run in a thread. `Capture` is what passes between them, and `Unstable` is raised when a file changed during the read.

**Prototype.**

```python
class Unstable(Exception):
    pass
```

```python
class Capture(NamedTuple):
    stat: Stat | None
    summary: Summary | None
    at: float
    generation: int
    error: str = ''
```

```python
def intern(value: Any) -> Any:
    if isinstance(value, dict):
        out = {
            sys.intern(key): intern(item)
            for key, item in value.items()
        }
    elif isinstance(value, list):
        out = [
            intern(item)
            for item in value
        ]
    elif isinstance(value, str):
        out = sys.intern(value)
    else:
        out = value
    return out
```

```python
class Metadata(Node):
    ...

    def is_stale(self) -> bool:
        if not self.is_observed:
            return True
        try:
            stat = Stat.take(self.sources())
        except OSError:
            return False
        is_changed = (
            stat is not None
            and stat != self.stat
        )
        return is_changed

    def capture(self) -> Capture:
        generation = self.generation
        before = Stat.take(self.sources())
        summary = None
        error = ''
        is_readable = (
            type(self).is_available
            and (
                self.max_bytes is None
                or before is None
                or before.st_size <= self.max_bytes
            )
        )
        if is_readable:
            try:
                summary = self.read()
            except ImportError as missing:
                type(self).is_available = False
                warnings.warn(f'{self.name} metadata is not read: {missing}', stacklevel=2)
            # a reader fails in its own ways on a damaged file
            except Exception as failure:  # noqa: BLE001
                error = repr(failure)
        # raises OSError if the file is gone
        after = Stat.take(self.sources())
        if before != after:
            raise Unstable(str(self._asset.path))
        out = Capture(
            stat=after,
            summary=summary,
            at=time.time(),
            generation=generation,
            error=error,
        )
        return out

    def accept(
            self,
            capture: Capture,
            basis: str,
    ) -> MetadataEvent | None:
        from trail.event import MetadataEvent

        asset = self._asset
        trail = self._trail
        # since the capture began, the asset was untracked, moved to another format, or observed
        if (
            asset.metadata is not self
            or trail.assets.id2entry.get(asset.id) is not asset
            or capture.generation != self.generation
        ):
            return None
        was_observed = self.is_observed
        previous = self.stat
        old = self._summary
        new = capture.summary
        changes: list[Change] = []
        if (
            old is not None
            and new is not None
        ):
            changes = self.compare(old, new)
        self.generation += 1
        self.is_observed = True
        self.stat = capture.stat
        self.error = capture.error
        if new is not None:
            self._summary = intern(new)
        elif not capture.error:
            # too large, or no reader: nothing to compare the next read with
            self._summary = None
        if not was_observed:
            return None
        # with a watcher running, the filesystem events already say the file changed
        if (
            not changes
            and (
                capture.stat is None
                or capture.stat == previous
                or trail.watchdog.session.is_active
            )
        ):
            return None
        kept = changes[:self.max_changes]
        size = None
        mtime = None
        if capture.stat is not None:
            size = capture.stat.st_size
            mtime = capture.stat.st_mtime_ns
        event = MetadataEvent(
            src_path=str(asset.path),
            format=self.name,
            basis=basis,
            st_size=size,
            st_mtime_ns=mtime,
            changes=kept,
            more=len(changes) - len(kept),
            timestamp=datetime.fromtimestamp(capture.at, UTC),
        )
        event.entry = asset
        event.apply(trail)
        return event

    def observe(
            self,
            basis: str = 'manual',
            *,
            is_forced: bool = False,
    ) -> MetadataEvent | None:
        if (
            not is_forced
            and not self.is_stale()
        ):
            return None
        try:
            capture = self.capture()
        except (OSError, Unstable):
            return None
        out = self.accept(capture, basis)
        return out

    @property
    def dict(self) -> Summary:
        # only a first observation happens here, and a first observation logs nothing
        if not self.is_observed:
            self.observe('access')
        out = copy.deepcopy(self._summary)
        if out is None:
            out = {}
        return out
```

**`is_stale`** answers whether the file needs reading, with one stat and no read. It is true before the first observation, and afterward when the stat differs from the recorded one. A missing file is not stale: its deletion is recorded elsewhere. An asset with nothing to stat is never stale after its baseline.

**`capture`** touches only the filesystem. It may run in a worker thread.

- It notes the `generation` it started at.
- It takes the stat before and after the read, by path. A format's library opens the file itself, so Trail cannot hold one descriptor across the read. The two stats still catch a write in place and a replacement by rename.
- A file larger than `max_bytes`, or a format whose package is missing, gives a capture with no summary.
- Any exception from the reader is a parse failure. It is kept as text in `error` and the capture has no summary. That includes an `OSError`, which h5py and netCDF4 raise for a damaged file.
- If the file is gone, the stat after the read raises `OSError`. That passes through, and `observe` returns `None`. The deletion is recorded elsewhere.

**`accept`** touches the Trail. It runs on the thread that owns the Trail, which is the loop when one runs.

- It refuses a result for an asset that was untracked, or moved to another format, while it was read.
- It refuses a result whose `generation` is not the current one. Another caller accepted a read since this one began, so this one may be older. Without this, a slow background read could overwrite a newer explicit check and log a change backward.
- With an old and a new summary it compares them. Otherwise there are no rows.
- It always moves `stat` forward. It replaces the summary when it has a new one. After a parse failure it keeps the old summary, so the next good read is compared with the last good one.
- The first observation of a file logs nothing.
- **When an event is logged.** If there are rows, always. If there are none, only when the stat changed and no watcher is running in this process. With a watcher, the filesystem events already say that the file was modified.

**`observe`** is the synchronous form: stop if the file is not stale, else capture and accept. `is_forced` skips the stat shortcut. A filesystem notification is a reason to read even when size and mtime are equal.

**`dict`** returns a copy of the summary. If the file was never observed, it observes it first. That first observation is the baseline, which logs nothing, so reading `dict` never writes to the log.

**The stale event, step by step.** The file holds `A` and Trail's baseline is `A`.

1. A program writes `B`, then `C`. Two notifications are on their way.
2. The notification for `B` arrives. Trail reads the file and gets `C`. It logs one event with the rows from `A` to `C`. The baseline is `C`.
3. The notification for `C` arrives. Trail reads the file and gets `C`. There are no rows and the stat is equal, so nothing is logged.

`B` is never seen. To record it, the writer would have to report its own save, as a QGIS plugin could. That is outside this plan.

The 11-9 discussion suggested a counter of notifications, raised on the watcher's thread, to catch a write that overlaps a read. This plan does not use one. Every notification leads to a read, the two stats bracket each read, and `generation` orders the results. That covers the same cases without state shared between threads.

**Who may write to the log.** Automatic observation needs the project lock: `Reads` runs only inside the watcher's session, and the cell hook takes the lock for the length of its check. A direct call to `observe()` logs like any other direct call, such as `trail.ontrail(path)`, which does not check the lock either.

**Lifecycle.**

| What happens | Effect on `asset.metadata` |
|---|---|
| The asset moves, same format | Nothing. The object and its baseline follow the asset. |
| The asset moves to another format | A new object, with no baseline. |
| The asset is untracked | `accept` refuses any result still in flight. `Reads` forgets the asset. |
| The log is cleared | Nothing. A baseline is about the file, not about the log. |
| The file cannot be parsed | The last good summary stays. `error` says why. The file is not parsed again until it changes. |
| The file is too large for its reader | No summary. The stat is still tracked. |
| The file is deleted | `observe` returns `None`. The watcher logs the deletion. |
| The process ends | Every baseline is gone. The next process reads its own. |

**Limits.**

- The baseline is the first read. A file tracked and written in the same notebook cell is read only after the cell, so its baseline already holds the write. The 11-9 decision accepts this: the filesystem change is recorded, and the first stable summary is the baseline.
- Several saves between two reads give one event.

### MetadataEvent: the log record

`MetadataEvent` is the record of one observed difference. It lives in `src/trail/event.py` with the other core events. `Metadata.accept` creates it, and the log replays it. There is one class for every format.

**Prototype.**

```python
from typing import Any

from trail.metadata import Change, Metadata
```

`event.py` already imports from `typing`, so `Any` joins that line. The class goes after `DirectoryAccessEvent`.

```python
@dataclass(kw_only=True, slots=True, repr=False)
class MetadataEvent(Event):
    src_path: str
    format: str = ''
    basis: str = ''
    st_size: int | None = None
    st_mtime_ns: int | None = None
    changes: list[Change] = field(default_factory=list)
    more: int = 0
    entry: Asset | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        Event.__post_init__(self)
        # a replay passes each change as the log holds it
        changes = []
        for change in self.changes:
            if isinstance(change, dict):
                change = Change.from_record(change)
            changes.append(change)
        self.changes = changes

    def _repr_items(self) -> Iterator[tuple[str, object]]:
        handler = Metadata.name2cls.get(self.format, Metadata)
        for name, value in Event._repr_items(self):
            if name == 'changes':
                for change in value:
                    yield 'change', handler.describe(change)
            elif name == 'st_mtime_ns':
                yield 'st_mtime', fmt.mtime(value / 1e9)
            elif name == 'more':
                if value:
                    yield 'more', f'{value} changes not shown'
            else:
                yield name, value

    def to_record(self) -> dict[str, Any]:
        out = Event.to_record(self)
        out['changes'] = [
            change.to_record()
            for change in self.changes
        ]
        return out

    def apply(
            self,
            trail: Trail,
            *,
            replay: bool = False,
    ) -> Asset | None:
        if not replay:
            trail.events.append(self)
        out = self.entry
        return out
```

| Field | Meaning |
|---|---|
| `src_path` | The asset's path when it was read. |
| `format` | The name of the `Metadata` subclass that read it, such as `qgis`. Empty for a file with no reader. |
| `basis` | What asked for the check: `watchdog`, `cell`, `startup`, `access` or `manual`. It is not who wrote the file. |
| `st_size`, `st_mtime_ns` | The file's stat when it was read. `None` for an asset with nothing to stat. |
| `changes` | The `Change` rows, at most `Metadata.max_changes`. |
| `more` | How many rows were left out. |
| `timestamp` | When Trail read the file. Inherited from `Event`. |

How it fits the existing event code:

- `__post_init__` turns each change from the log back into a `Change`. This is the same move as `DirectoryAccessEvent.__post_init__`, so `Event.from_record` needs no new hook.
- `to_record` writes each change with named fields.
- `_repr_items` yields one `change` line per row. The console's [Renderer.parameters](/home/arstneio/PyCharmProjects/trail/src/trail/cli/render.py:110) prints whatever `_repr_items` yields, so the lines appear with no change to the renderer.
- `apply` on replay does nothing. It reads no file and restores no baseline. A baseline lives in memory only.
- `format` and `basis` are dataclass fields, so the console's `field=` filter offers them.

A real record from the prototype's log, written after a notebook cell changed a file. It is shown without its `id`, `timestamp`, `src_path`, `entry` and the other inherited fields:

```json
{"cls": "MetadataEvent", "format": "toy", "basis": "cell", "st_size": 20, "changes": [{"path": ["crs"], "verb": "updated", "old": "EPSG:4326", "new": "EPSG:3857"}]}
```

### Messages

This section explains how a `Change` becomes a line of text. It builds on `Change.label` and on the `phrases` and `named` members of a format.

`Metadata.describe` is a classmethod, so it needs no asset and no file. `MetadataEvent._repr_items` calls it on the class that `Metadata.name2cls` gives for the event's `format`. If that format was never imported, it falls back to `Metadata`, and the line is generic.

A phrase is a format string. Its key is a path pattern in which `*` stands for any one key. The string may use:

| Placeholder | Value |
|---|---|
| `{old}`, `{new}` | the two values |
| `{verb}` | `added`, `removed`, `updated` or `reordered` |
| `{label}` | the entity's display name |
| `{0}`, `{1}`, … | the parts of the path, by position |

The phrases of the qgis reader:

```python
phrases: ClassVar[dict[tuple[str, ...], str]] = {
    ('crs',): 'project CRS {old} → {new}',
    ('layers', '*'): "layer '{label}' {verb}",
    ('layers', '*', 'name'): "layer renamed '{old}' → '{new}'",
    ('layers', '*', 'source'): "layer '{label}' source changed",
    ('layers', '*', 'crs'): "layer '{label}' CRS {old} → {new}",
    ('layers', '*', 'is_visible'): "layer '{label}' visible {old} → {new}",
    ('layers', '*', 'opacity'): "layer '{label}' opacity {old} → {new}",
    ('order',): 'layer order changed',
}
```

A qgis layer is keyed by an opaque id, so its phrases use `{label}`. A geopackage layer is keyed by its table name, so its phrases use the path: `"column '{3}' {verb} in layer '{1}'"`.

**Observed results.** Edits made with geopandas, rasterio, xarray and h5py, then one check. Lines with a dotted path had no phrase and are generic:

`cities.parquet`:

```text
rows 500 → 525
column 'country' added
column '__index_level_0__' added
geometry column 'geometry' CRS EPSG:4326 → EPSG:3857
geo.columns.geometry.bbox: [-59.54149490410117, -59.97719980711878, 59.9401622708432... → [-6628128.893794836, -8394663.426546443, 6672508.34205645...
```

`scene.tif`:

```text
bands 3 → 4
overviews: 2 → 0
CRS EPSG:32633 → EPSG:32634
nodata 0 → 255
```

`cube.zarr`:

```text
array 'temperature' shape [4, 64, 64] → [3, 64, 64]
array 'temperature' chunks [4, 64, 64] → [3, 64, 64]
array 'time' shape [4] → [3]
array 'time' chunks [4] → [3]
array 'pressure' added
```

`weights.h5`:

```text
datasets.layer2/kernel added
```

The `__index_level_0__` column is real: the edit left a non-default pandas index, and pandas wrote it.

**Why the sentence is not stored.** A stored sentence cannot be filtered, cannot be reworded, and would differ between Trail versions. The rows can be exported to another vocabulary later, such as PROV-O, without parsing text.

### Assets.observe: the check with no watcher

`Assets.observe` is the reconciliation the Trail initiative describes: compare each registered resource with its last recorded state. It is a method of [Assets](/home/arstneio/PyCharmProjects/trail/src/trail/asset.py:342), reached as `trail.assets.observe()`. The cell hook calls it when no watcher runs. The CLI and tests can call it too. It needs no watcher, no event loop and no thread.

**Proposed.** The prototype attached this same function to `Assets` from outside the package.

```python
def observe(self, basis: str = 'manual') -> list[MetadataEvent]:
    events = []
    for asset in self:
        event = asset.metadata.observe(basis)
        if event is not None:
            events.append(event)
    return events
```

`asset.py` imports `MetadataEvent` under `TYPE_CHECKING` for the annotation. For each asset, `Metadata.observe` takes one stat. If the stat matches the recorded one, nothing else happens. If it differs, the file is read and compared.

**What it logs**, when no watcher runs in the process:

| The file | Result |
|---|---|
| has a reader, and its summary changed | one event with rows |
| has a reader, and only its stat changed | one event with no rows |
| has no reader, and its stat changed | one event with no rows and an empty `format` |
| could not be parsed, and its stat changed | one event with no rows. The last good summary is kept. |
| is unchanged | nothing |
| is seen for the first time | nothing. The read is the baseline. |
| is a directory asset with no reader | nothing. It has nothing to stat. |

An event with no rows says that the file changed and gives its new size and mtime. Without a watcher, nothing else in the log would say so.

**How it relates to the status check of `PROJECT_PLAN.md`.** Phase 3 of that plan is an on-demand check that classifies each resource as unchanged, modified, missing or renamed. `Assets.observe` is the modified part of it. It follows the choice that plan recommends in its section 6: the check logs what it finds and moves the baseline forward, so a second call logs nothing. There is one baseline per asset, `asset.metadata.stat`. Missing and renamed files remain that plan's work.

**Observed results.**

| Assets | `os.stat` on each, warm cache |
|---:|---:|
| 100 | 0.39 ms |
| 1,000 | 4.1 ms |
| 10,000 | 9.2 ms |
| 100,000 | 97 ms |

These were measured on tmpfs. The larger runs are cheaper per file because the first runs include the first lookup of each path. A check over 29 fixture assets, 5 of which had changed, took 18 ms including the five reads.

**Limits.**

- A stat cannot see a rewrite that keeps the size and the timestamp. Tools that preserve mtime, such as `cp -p`, `rsync -t` and Syncthing, produce this when the size is also equal. The inode catches the case where the file was replaced by a rename.
- This check does not record a deleted or moved file. A missing file is skipped.
- A zarr store's `sources()` lists its group directories on every check, to find metadata files that were added. The cost grows with the number of groups, not with the number of chunks.

### Reads: the watcher path

`Reads` is what turns filesystem events into observations while the watcher runs. It lives in `src/trail/metadata/reads.py` and is reached as `trail.watchdog.session.reads`. The session's consumer adds an asset to it each time it applies a filesystem event to that asset. The cell hook adds assets too. `Reads` waits for an asset to go quiet, runs `Metadata.capture` in a worker thread, and calls `Metadata.accept` back on the loop.

It exists for three reasons. One save produces a burst of notifications, and Trail should read once. A read must not block the event loop. With thousands of assets, only a few reads may run at once.

**Prototype.** The class was named `Requests` there.

```python
from __future__ import annotations

import asyncio
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING

from trail.metadata import Unstable
from trail.node import Node

if TYPE_CHECKING:
    from trail.asset import Asset
    from trail.session import WatchdogSession
```

```python
class Reads(Node):
    _parent: WatchdogSession
    # seconds without a new event before an asset is read
    quiet = 0.25
    # the longest a steady stream of events can postpone a read
    limit = 2.0
    concurrency = 2
    retries = 3

    def __init__(self, parent: WatchdogSession) -> None:
        super().__init__(parent)
        self.timers: dict[str, asyncio.TimerHandle] = {}
        self.first: dict[str, float] = {}
        self.bases: dict[str, str] = {}
        self.waiting: dict[str, None] = {}
        self.backlog: dict[str, None] = {}
        self.reading: dict[str, asyncio.Task[None]] = {}
        self.again: set[str] = set()
        self.is_closed = False

    def add(
            self,
            asset: Asset,
            basis: str = 'watchdog',
            *,
            is_immediate: bool = False,
    ) -> None:
        if self.is_closed:
            return
        self.bases[asset.id] = basis
        now = time.monotonic()
        first = self.first.setdefault(asset.id, now)
        delay = min(self.quiet, max(0.0, first + self.limit - now))
        if is_immediate:
            delay = 0.0
        timer = self.timers.pop(asset.id, None)
        if timer is not None:
            timer.cancel()
        self.timers[asset.id] = self._parent.loop.call_later(delay, self.due, asset.id)

    def load(self, assets: Iterable[Asset]) -> None:
        if self.is_closed:
            return
        for asset in assets:
            self.bases.setdefault(asset.id, 'startup')
            self.backlog[asset.id] = None
        self.fill()

    def due(self, identifier: str) -> None:
        self.timers.pop(identifier, None)
        self.first.pop(identifier, None)
        if identifier in self.reading:
            # events arrived during the read, so the asset is read once more after it
            self.again.add(identifier)
            return
        self.backlog.pop(identifier, None)
        self.waiting[identifier] = None
        self.fill()

    def fill(self) -> None:
        if self.is_closed:
            return
        loop = self._parent.loop
        while len(self.reading) < self.concurrency:
            # an asset that changed is read before one that only needs its baseline
            if self.waiting:
                queue = self.waiting
            elif self.backlog:
                queue = self.backlog
            else:
                break
            identifier = next(iter(queue))
            del queue[identifier]
            coroutine = self.read(identifier)
            self.reading[identifier] = loop.create_task(coroutine, name='metadata-read')

    async def read(self, identifier: str) -> None:
        trail = self._trail
        basis = self.bases.pop(identifier, 'watchdog')
        try:
            for attempt in range(self.retries):
                asset = trail.assets.id2entry.get(identifier)
                if asset is None:
                    return
                metadata = asset.metadata
                try:
                    capture = await asyncio.to_thread(metadata.capture)
                except Unstable:
                    await asyncio.sleep(self.quiet * (attempt + 1))
                    continue
                except OSError:
                    return
                # another caller observed the asset during the read
                if capture.generation != metadata.generation:
                    continue
                # back on the loop: nothing else runs between the check and the append
                metadata.accept(capture, basis)
                return
            metadata.error = 'The file kept changing while it was read'
        finally:
            self.reading.pop(identifier, None)
            if identifier in self.again:
                self.again.discard(identifier)
                self.waiting[identifier] = None
            self.fill()

    def drop(self, identifier: str) -> None:
        timer = self.timers.pop(identifier, None)
        if timer is not None:
            timer.cancel()
        self.first.pop(identifier, None)
        self.bases.pop(identifier, None)
        self.waiting.pop(identifier, None)
        self.backlog.pop(identifier, None)
        self.again.discard(identifier)

    def hurry(self) -> None:
        for identifier, timer in tuple(self.timers.items()):
            timer.cancel()
            self.due(identifier)

    async def drain(self) -> None:
        self.hurry()
        while self.reading:
            await asyncio.gather(*self.reading.values(), return_exceptions=True)

    def close(self) -> None:
        self.is_closed = True
        for timer in self.timers.values():
            timer.cancel()
        for task in self.reading.values():
            task.cancel()
        self.timers.clear()
        self.first.clear()
        self.bases.clear()
        self.waiting.clear()
        self.backlog.clear()
        self.again.clear()
```

| Attribute | Meaning |
|---|---|
| `timers` | One timer per asset that is waiting for quiet, by asset id. |
| `first` | When each waiting asset's first event came, so a steady stream cannot postpone its read past `limit`. |
| `bases` | The basis of each asset's next read. |
| `waiting` | Assets that changed and are due, in the order they became due. |
| `backlog` | Assets that only need their baseline. They are read when nothing is waiting. |
| `reading` | The read in flight for each asset. At most `concurrency` at once, and never two for one asset. |
| `again` | Assets that got an event during their read. Each is read once more after it. |
| `is_closed` | Set by `close`. Nothing is added or started afterward. |

| Method | What it does |
|---|---|
| `add(asset, basis, is_immediate=False)` | Start or restart the asset's timer. `is_immediate` skips the wait. |
| `load(assets)` | Put assets in the backlog, for their baselines. |
| `due(identifier)` | The timer fired. Queue the asset, or mark it for another read if one is in flight. |
| `fill()` | Start reads until `concurrency` are in flight. Changed assets go first. |
| `read(identifier)` | Capture in a thread, accept on the loop. A capture that was `Unstable`, or that another caller overtook, is tried again, up to `retries` times. After that the asset's `error` says the file kept changing. |
| `drop(identifier)` | Forget an asset that was untracked. |
| `hurry()` | End every wait now. |
| `drain()` | `hurry`, then wait for the reads in flight. |
| `close()` | Cancel every timer and every read, and refuse new work. |

`accept` runs on the loop, right after the `await` returns. Nothing else runs between its checks and its append to the log.

**Proposed changes in `session.py`, `asset.py` and `event.py`.** These were not run in this form. The prototype made the same `add` call by wrapping `WatchdogEvent.apply`, and set `session.reads` by hand.

1. [prepare](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:618) creates the queue with the rest of the run's state: `self.reads = Reads(self)`. Each run gets a new one. `session.py` imports `Reads` from `trail.metadata.reads` and `Metadata` from `trail.metadata`.

2. [consume](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:896) keeps the entry that `apply` returns and adds it. `session.py` imports `Asset` only for type checking today, so it needs the import at runtime.

```python
# the event types after which an asset's metadata is read
READ_AFTER: Final = frozenset({'created', 'modified', 'moved'})
```

```python
            entry = event.apply(trail)
            if entry is not None:
                moved |= event.event_type == "moved"
                if (
                    isinstance(entry, Asset)
                    and event.event_type in READ_AFTER
                ):
                    self.reads.add(entry)
                continue
```

3. [record](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:909) adds a directory asset when one of its accesses closes with writes: `self.reads.add(asset, is_immediate=True)` when `event.event_type == 'modified'`. The access already waited `idle_gap` seconds, so no further wait is needed.

4. [start](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:652) queues the baselines after the watches are placed. Only assets with a reader are queued, as the 11-9 note decided. An asset with no reader needs no baseline while the watcher runs, since a change without rows is not logged then.

```python
            self.place_watches()
            readable = [
                asset
                for asset in self._trail.assets
                if type(asset.metadata) is not Metadata
            ]
            self.reads.load(readable)
```

[Assets.add](/home/arstneio/PyCharmProjects/trail/src/trail/asset.py:363) does the same for the assets it newly tracks while a session is active, after its call to `session.reconcile()`.

5. [stop](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:759) drains after the consumer has finished: `await self.reads.drain()` goes after `await consumer` and before `self.close()`. [close](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:804) calls `self.reads.close()` before it releases the lock.

6. `RemoveAssetEvent.apply` calls `session.reads.drop(asset.id)` when a session is started.

**`opened` must never be in `READ_AFTER`.** Trail's own read of a file produces an `opened` notification. If that asked for another read, Trail would read the file forever.

**Close events.** `EVENT_FILTER` does not subscribe to `FileClosedEvent`, and watchdog reports closes only on Linux. `Reads` therefore relies on the quiet period, which works on every platform. If the [folding work](/home/arstneio/PyCharmProjects/trail/notes/11-4-3-event-folding-report.md) adds close events, a close after writing calls `add(entry, is_immediate=True)` and nothing else changes.

**Observed results**, with the real watchdog session and real filesystem events. Seven assets had a reader and one did not.

| Scenario | Reads | Metadata events |
|---|---:|---:|
| Startup: seven baselines through `load`, and one `add` for a file among them | 8 | 0. The asset with no reader was not read. |
| `A`, then `B`, then `C`, 50 ms apart | 1 | 1, with the rows from `A` to `C` and basis `watchdog` |
| 40 rewrites of the same content over 0.4 s, 85 filesystem events | 1 | 0 |
| The cell hook right after a write, before the loop resumed | began 2 ms after the loop resumed | 1, with basis `cell` |
| One second later, after the watcher caught up with the same write | 1 more | 0. No event for the changed `.txt` either. |
| A direct `observe` while a background read of the same file was in flight | | 1. The summary ended at the newer state. |
| `close()` with six assets due | 0 started afterward | 0 |
| A save right before `stop()`, drained after the consumer finished | 1 | 1 |

The first prototype drained before the consumer had delivered the last event, and missed that save. The order in step 5 is the fix.

**Limits.**

- A run that ends without `stop`, such as a kernel that is killed, loses the reads that were still waiting. Nothing is logged for a save in the last quarter second.
- A directory asset is read when its access closes, 30 s after its last event. The cell hook reads it sooner if it has a reader, since `is_stale` checks its metadata files.
- The existing session tests use a [StubLoop](/home/arstneio/PyCharmProjects/trail/tests/test_session.py:21) that has only `call_soon_threadsafe`. Tests that call `consume` on it will reach `Reads.add`, which needs `call_later`. The stub has to grow that method, or those tests have to give the session a `Reads` that does nothing. `Reads` reads the clock itself, so its own tests need a real loop and a short `quiet`.

### The IPython extension

The extension is the piece that makes a notebook cell a moment to check. It lives in `src/trail/ipython.py` and is loaded with `%load_ext trail.ipython`, or for every session with `c.InteractiveShellApp.extensions = ['trail.ipython']` in the IPython configuration. It uses `Assets.observe` and `Reads.add`.

**Prototype.**

```python
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from trail.trail import Trail

if TYPE_CHECKING:
    from IPython.core.interactiveshell import ExecutionResult, InteractiveShell
```

```python
hooks: dict[int, object] = {}


def observe(trail: Trail) -> None:
    session = trail.watchdog.session
    if session.is_active:
        # the watcher runs in this process: what changed is read once the loop resumes
        for asset in trail.assets:
            if asset.metadata.is_stale():
                session.reads.add(asset, 'cell', is_immediate=True)
        return
    # another process records this project
    if not trail.lock.acquire(observe):
        return
    try:
        trail.assets.observe('cell')
    finally:
        trail.lock.release(observe)


def load_ipython_extension(shell: InteractiveShell) -> None:
    # outside a Trail project the extension registers nothing
    if Trail.locate(Path.cwd()) is None:
        return
    trail = Trail(Path.cwd())

    def post_run_cell(result: ExecutionResult) -> None:
        observe(trail)

    shell.events.register('post_run_cell', post_run_cell)
    hooks[id(shell)] = post_run_cell


def unload_ipython_extension(shell: InteractiveShell) -> None:
    hook = hooks.pop(id(shell), None)
    if hook is not None:
        shell.events.unregister('post_run_cell', hook)
```

`load_ipython_extension` registers the hook. `observe` is the check itself, as a module function, so it can be called and tested without a shell.

- **Outside a project** the extension registers nothing. [Trail.locate](/home/arstneio/PyCharmProjects/trail/src/trail/trail.py:300) finds no `.trail` above the working directory.
- **With the watcher.** In a kernel an asyncio loop is running, so a `Trail` starts its watcher as soon as it has something to watch. The hook then stats every asset and hands the changed ones to `Reads` with basis `cell`. It cannot rely on the watcher having queued them. A cell runs on the loop's thread and blocks the loop, so the consumer has not run since the cell began. The reviewer found this on the first draft, whose hook only called `hurry` and so did nothing.
- **Without the watcher**, as in terminal IPython, the hook is the check. It takes the project lock for the length of the check and releases it. [Lock.acquire](/home/arstneio/PyCharmProjects/trail/src/trail/lock.py) fails when another process is recording, such as the console in a terminal, and the hook then returns. Two processes that both observed would each log the same change, since each has its own baselines.

**The reconciliation points of the initiative**, and what this plan does at each:

| Point | What happens |
|---|---|
| A session begins | With the watcher, baselines are read. A change made before that is not noticed. See Step 7. |
| After a user-run cell | The hook checks. |
| A session ends | `Reads.drain` reads what was still waiting. |
| Before Trail presents or exports the record | Nothing. The console could call `trail.assets.observe()` before it lists events when no watcher runs. This is not designed here. |

**Observed results** for IPython 9.17.1 and ipykernel 7.3.0 on Python 3.12.16:

| Question | Answer | How it was checked |
|---|---|---|
| What does the hook receive? | An `ExecutionResult`. It has `success`, `error_in_exec`, `execution_count` and `info.raw_cell`. | a real `InteractiveShell` |
| Does it fire for a cell that raised? | Yes, with `success=False` and the exception in `error_in_exec`. | same |
| For a syntax error? | Yes, with `success=False`. | same |
| For an `await` cell? | Yes. | same |
| For a silent execution? | No. `post_execute` fires, `post_run_cell` does not. | same |
| In what order? | `post_execute` first, then `post_run_cell`. | same |
| Which thread, in a kernel? | The main thread. | a real kernel through `jupyter_client` |
| Is an asyncio loop running there? | Yes. `asyncio.get_running_loop()` succeeds. | same |
| Does a slow hook delay the cell? | Yes. A hook that sleeps 500 ms raised the round trip from 7 ms to 510 ms. | same |
| Can the hook hand work to the loop? | Yes. A task made with `loop.create_task` finished after the cell had returned, and the round trip stayed at 10 ms. | same |
| Does the extension stay dormant outside a project? | Yes. 0 hooks registered outside, 1 inside. | `%load_ext` in both |
| With no watcher, does a cell that writes two files log two events? | Yes: one with a `crs` row, one with no rows for a csv. | the log file |
| With the watcher, does the hook get the cell's write read? | Yes. Called right after a write, before the loop resumed, it queued the file, and the event had basis `cell`. | `observe(trail)` called under a running session |

The last row ran `observe` directly, inside a running loop. The whole extension was not run in a kernel with the watcher active.

**What the hook does not do.** It does not read the cell's source. It does not record which cell ran. The event says `basis: cell`, which means a cell had just finished when Trail looked. Another program may have written the file.

**Limits.**

- **The extension opens its own `Trail`.** If a cell also runs `Trail('.')`, the kernel has two. They do not share assets in memory, and only the first holds the lock, so the second is passive. An asset tracked through the second is not checked by the hook. This is not solved here. See [Open questions](#open-questions), question 3.
- Loading the extension through the IPython configuration may run before the kernel's loop starts. The watcher would then not start at load. The hook's branch for no watcher covers that case. This was **not tested**.
- With the watcher, the hook's cost is one stat per tracked asset. Without it, the cost also includes a read of each file that changed, on the thread that runs the cell. Readers that parse a whole file are bounded by `max_bytes`.

### Representation and memory

This section answers whether to move away from dicts. It builds on the three stable points named in the TLDR: `read()`, `.dict` and `compare()`.

**Observed results.** A synthetic summary with `n` column entries, each a small dict, as a parquet file with nested types might give. 200 summaries were held at once and measured with `tracemalloc`:

| Entries | JSON size | Plain dict | Interned dict | Canonical JSON text | `diff`, no change | `==` on the dicts |
|---:|---:|---:|---:|---:|---:|---:|
| 10 | 0.7 KiB | 4.2 KiB | 2.4 KiB | 0.7 KiB | 53 µs | 0.6 µs |
| 100 | 6.3 KiB | 36.9 KiB | 22.3 KiB | 6.4 KiB | 0.46 ms | 5.0 µs |
| 1,000 | 64.3 KiB | 359.6 KiB | 214.2 KiB | 64.7 KiB | 4.5 ms | 53 µs |

The summaries of the 29 real fixtures are 21 B to 1.4 KiB as JSON. At that size, 1,000 assets cost a few MiB in any form.

**What to do now.** Store the dict with its strings interned. A reader builds new strings for every file, and thousands of assets repeat the same keys and type names. Interning cut memory by about 40 % in the measurement. `intern` is shown under [Observing a file](#observing-a-file).

**What to do if memory matters later.** Store the canonical JSON text and parse it only when a file changes. It is about 3.4 times smaller than the interned dict and about 5.7 times smaller than the plain one. Only `Metadata._summary`, `Metadata.accept` and `Metadata.dict` change. No reader, event or user format is touched.

**What not to do.** A slotted record class per format would be smaller still, but it would need one class per format and a second way to compare. A digest alone can say that two summaries differ. It cannot say how.

**`dict` returns a copy.** A caller that edits the returned dict does not change Trail's baseline. The copy costs time on every access. If that matters, return a read-only view.

**A cheap speedup that is not in the prototype.** `diff` walks both summaries and compares every leaf as JSON text. Comparing the canonical JSON text of the two whole summaries first would settle the common case where nothing changed with two calls. Plain `==` on the dicts is about 90 times faster than the walk, but it cannot be used alone, since `True == 1`.

### The designs that were compared

Three designs were on the table. This section says what each is, what was tested, and why the plan picks the second.

| | A. Event-centric | B. Metadata-centric | C. Function registry |
|---|---|---|---|
| A format is | an `ExtEvent` subclass with class methods to read and compare | a `Metadata` subclass with `read` | a function registered for a suffix |
| The namespace holds | state, and delegates to the event class | state and behavior | state |
| The log record is | one event class per format | one `MetadataEvent` | one `MetadataEvent` |
| Format-specific wording | a method on the event class | `phrases`, or `describe` | a table beside the function |

A is your sketch from the request. B is this plan. C is the smallest surface.

**Observed results** on the points that separate them:

| Claim | Test | Result |
|---|---|---|
| A: a log with a per-format event replays anywhere | Log an event of a class defined in one process. Open the Trail in a process that never imported it. | Fails. `ValueError: Unknown event class: 'ToyEvent'`, and the Trail cannot be opened. |
| A: an event subclass is easy to write | Subclass `Event` without the `@dataclass(...)` line. | It registers, then `TypeError: Event.__init__() got an unexpected keyword argument 'src_path'`. |
| A: a cell that defines an event class can be run again | Define the class twice in one module, then in another module. | The same module is accepted. Another module raises `TypeError: CellEvent is already an event class in __main__`. |
| B: the log replays without the format class | Open the Trail in a process that imported no format. | Works. The event shows with generic wording. |
| B: a format can be defined twice | Define a second class for `.toy`. | The asset's next `metadata` is the new class. Nothing raises. |
| B: the descriptor works on a dataclass | Attach it to `Asset`. | It is not a dataclass field, there is one object per asset, and it works on `DirAsset`. |

A could be repaired. `Event.from_record` could fall back to a base class for an unknown name, and the base could accept undecorated subclasses. Both are new machinery in the replay path, added only to keep one class per format.

C was not prototyped. Its difference from B is small: a decorated function instead of a class with one method. B was preferred because a format needs more than a function as soon as it is a directory (`sources`), has opaque ids (`named`), parses whole files (`max_bytes`) or deserves wording (`phrases`). A class holds those in one place, and `asset.metadata` is then the same object the user subclassed.

This comparison rests on one prototype and three targeted tests. A and C were not built.

### Implementation steps

The steps are in the order each depends on the one before. Each lists what it adds and the checks that say it is done. A check listed under **Acceptance checks** passed in the prototype unless it is marked **not run**.

#### Step 1. The core

**Adds.**

- `src/trail/metadata/__init__.py`, new: the four type aliases, `Unstable`, `Stat`, `Change`, `Capture`, `intern`, `canonical`, `diff`, `brief` and `Metadata`, as shown above.
- `src/trail/asset.py`: the line `metadata = Metadata.descriptor()` in `Asset`, and `Assets.observe`, with `MetadataEvent` imported under `TYPE_CHECKING`.
- `src/trail/event.py`: `MetadataEvent`, after `DirectoryAccessEvent`, with `Any` added to the `typing` import.
- `tests/test_metadata.py`, with a toy format that reads a JSON file, shown under [A format is a Metadata subclass](#a-format-is-a-metadata-subclass-not-an-extevent-subclass).
- Optional: rename `Event.classes` to `Event.name2cls`, as your 10-29 comment decided. It touches the declaration, `Event.__init_subclass__`, `Event.from_record` and `Events.types`.
- Optional: rename the locals named `metadata` that hold stat results, in `asset.py` and at `session.py:1020`. In `Asset.fingerprint` the name `stat` is already taken by a loop variable, so that one needs another name. This fits best with your pending rename of `fingerprint`.

**Acceptance checks.** Each uses a Trail on a temporary directory, opened with `Trail(root, ontrail=None)`, with no event loop, so no watcher runs. Files are tracked with `trail.ontrail(path)`. `A`, `B` and `C` are three versions of a toy JSON file:

```python
A = {'crs': 'EPSG:4326', 'layers': {'L1': {'name': 'Parks', 'opacity': 1}}, 'order': ['L1']}
B = {'crs': 'EPSG:4326', 'layers': {'L1': {'name': 'Parks', 'opacity': 0.5}}, 'order': ['L1']}
C = {
    'crs': 'EPSG:3857',
    'layers': {
        'L1': {'name': 'Parks', 'opacity': 0.35},
        'L2': {'name': 'Roads', 'opacity': 1},
    },
    'order': ['L2', 'L1'],
}
```

| Check | It passes when |
|---|---|
| a1 | `metadata` is not among `dataclasses.fields(Asset)`. |
| a2 | `asset.metadata is asset.metadata`, and two assets have different objects. |
| a3 | A `.toy` asset gets the toy class, and a `.zarr` directory with no zarr reader defined gets `Metadata`. |
| a4 | The namespace works on a `DirAsset`, and its `_asset` is that asset. |
| a5 | `Asset.metadata`, on the class, returns the descriptor. |
| a6 | `repr(asset)` does not mention `metadata`. |
| a7 | After `asset.move` to another name with the same suffix, `asset.metadata` is the same object and its `dict` is equal. |
| a9 | None of the above logged a `MetadataEvent`. |
| b1 | `UPPER.TOY` gets the toy class. |
| b2 | A `.txt` asset gets `Metadata`, an empty `dict` and a `stat`. |
| b3 | `suffixes = ('TOY',)` raises `TypeError`. |
| b4 | A second class defined for `.toy` replaces the first, and an existing asset's next `metadata` is the new class. |
| c1 | The first `observe` returns `None`, sets `dict` to `A` and logs nothing. |
| c2 | After the file becomes `C`, `observe` logs one event. |
| c3 | Its rows are exactly: `crs` updated, `layers.L1.opacity` updated, `layers.L2` added, `order` updated. |
| c4 | `observe(is_forced=True)` on the unchanged file logs nothing. |
| c5 | Writing `C` with `order` as `['L1', 'L2']` gives one `reordered` row. |
| c6 | Adding a key `flag` with value `1`, observing, then writing it as `True` gives one row at `flag`. |
| d1 | With baseline `A`, the file is written as `B` and then `C`. The first forced `observe('watchdog')` logs one event. |
| d2 | The second forced `observe('watchdog')` logs nothing. |
| d3 | The event's rows hold `A`'s values as `old` and `C`'s as `new`. |
| e1 | A subclass of the toy class, with `suffixes = ()` so that it does not take over the suffix, rewrites the file inside its `read`. An instance made directly on the asset, as `Racing(asset)`, raises `Unstable` from `capture`. |
| e2 | Nothing was logged and `dict` is still `A`. |
| e3 | The next forced `observe` logs the rows from `A` to `C`. |
| f1 | A file cut in the middle of its JSON keeps the summary `A`, sets `error`, and logs one event with no rows. |
| f1b | A further `observe` on that unchanged file does not call the reader. |
| f2 | The next valid save is compared with `A`, and `error` is cleared. |
| g1 | `to_record()` holds each change as a dict with a `path` key. |
| g2 | `Event.from_record` on the JSON round trip gives equal `Change` rows and the same entry. |
| g3 | A fresh process that imports no format opens the Trail and finds the events with their rows. The asset has no `stat` and no summary. Reading its `dict` logs nothing. |
| h1 | With five toy files and a csv tracked, the first `trail.assets.observe('cell')` reads five files and logs nothing. Reads are counted by wrapping the toy class's `read`. |
| h2 | A second call reads nothing. |
| h3 | After one toy file and the csv change, only that toy file is read. |
| h4 | Two events are returned, one per changed asset. |
| h5 | The csv's event has no rows, the new `st_size`, and `basis == 'cell'`. |
| h6 | With `WatchdogSession.is_active` patched to return `True`, a further change to the csv logs nothing. |
| i1 | The generic line for an opacity change with label `Roads` is `layers.L2.opacity (Roads): 1 → 0.5`. |
| i1b | `Metadata.describe(Change(path=('bbox',), verb='updated', old=list(range(40)), new=[1]))` is under 90 characters. Each side is cut to 60. |
| l1 | A capture accepted after its asset was untracked returns `None`. |
| m1 | With baseline `A`, a capture is taken of `B`. The file becomes `C` and is observed. Accepting the older capture then returns `None`, `dict` is `C`, and one event exists. |
| m2 | Editing the dict that `asset.metadata.dict` returned does not change the next `dict`. |
| m3 | Each annotation string of `Metadata` and of a format class, evaluated with `eval` in the module's globals plus `Asset`, with the class's own namespace as locals, raises no `TypeError`. |
| m4 | A `.n5` directory asset with no reader has `sources() == []`, a `stat` of `None`, is not stale after its first observation, and logs nothing when a file inside it changes. |
| m5 | Reading `dict` on a newly tracked asset returns its summary and logs nothing. |
| n1 | A reader that raises `OSError` on a damaged file gives one event with no rows, sets `error`, keeps the summary, and is not called again while the file is unchanged. |
| n2 | After the file is deleted, a forced `observe` returns `None` and the asset is not stale. |

The tmpfs timestamps moved in ticks of a few milliseconds, so the prototype slept 20 ms after each write. Tests should do the same, or write files of different sizes.

One more check was **not run**: a toy format with one entry in `phrases` gives that wording. The prototype tested phrases only with the qgis class, in Step 2.

#### Step 2. The first two formats

**Adds.**

- `src/trail/jupyter/metadata.py` with `QGISMetadata`, `JGISMetadata` and the `sanitize` helper. `src/trail/jupyter/__init__.py` gains `from trail.jupyter.metadata import JGISMetadata, QGISMetadata` and lists both in `__all__`, so the formats are registered whenever Trail is imported. The code is under [Format readers](#format-readers).
- `defusedxml` in `pyproject.toml`. It is installed in the project's environment today only because another package needs it.
- `*.jgis`, `*.qgs` and `*.qgz` in `src/trail/ontrail/geospatial.txt`. No preset names them today.

**Acceptance checks.**

| Check | It passes when |
|---|---|
| a8 | After `asset.move` from a `.toy` path to a `.qgs` path, `asset.metadata` is a new `QGISMetadata`. |
| i2 | `QGISMetadata.describe` on an opacity change with label `Roads` gives `layer 'Roads' opacity 1 → 0.5`. |
| i3 | An added layer gives `layer 'Roads' added`. |
| i4 | A path with no phrase, `layers.L2.provider`, falls back to the generic line. |
| i5 | Renaming a layer in a real `.qgs` gives exactly `layer renamed 'OpenStreetMap' → 'Basemap'`. |
| i6 | Changing `layerOpacity` in that file gives `layer 'test' opacity 1.0 → 0.35`. The label is the layer's name, not its id. |
| i7 | The stored record of that change has `label: 'test'`. |

The real file is `ui-tests/tests/gis-files/buildings.qgs` in the event feed clone. The two edits were made as text replacements in its XML: `<layername>OpenStreetMap</layername>` and `<layerOpacity>1</layerOpacity>`. The second string occurs twice in the file. Only the one inside `projectlayers` is read, and replacing both is fine.

Further checks:

- `ui-tests/tests/gis-files/buildings.qgs`, the `buildings.qgz` beside it, and `examples/geotiff.qgz` each give a summary. The inner `.qgs` of `geotiff.qgz` is named `tmps5syr5q_.qgs`, so the reader must not assume the archive's name. Passed.
- The `.qgs` and the `.qgz` of `buildings` give the same `crs` and the same layer ids. Passed.
- A `.qgs` cut to 60 % raises. Passed, with `ParseError`.
- `sanitize` removes the secret from each of these: `password='it''s'`, `password="dq secret"`, a percent-encoded `%3Ftoken%3Dabc`, an Azure `sig=` value, `user:pass@host` and `API_KEY=`. Passed. The summary holds the datasource percent-decoded, since a secret can hide behind the encoding.
- A file named `map.jGIS` is taken by the `*.jgis` rule of the preset. Passed in the second reviewer's build: `Scopes` lowercases both the rule and the path.

#### Step 3. The watcher path

**Adds.** `src/trail/metadata/reads.py` and the six changes listed under [Reads: the watcher path](#reads-the-watcher-path). The existing `StubLoop` in the tests needs the change named there.

**Acceptance checks.** The table of observed results in that section, as tests. Two more that were **not run**:

- A directory asset with a reader is read once after its access closes with writes.
- An asset untracked while its read is in flight logs nothing and leaves no timer.

#### Step 4. The IPython extension

**Adds.** `src/trail/ipython.py`.

**Acceptance checks.** The last three rows of the table under [The IPython extension](#the-ipython-extension). Two more that were **not run**:

- In a real kernel with the watcher active, a cell that writes a tracked file logs its event with basis `cell`.
- With another process holding the lock and no watcher here, the hook logs nothing.

#### Step 5. The remaining readers

**Adds.** `src/trail/metadata/geospatial.py` and `src/trail/metadata/general.py`, imported from `src/trail/trail.py`. Importing them is cheap, since each third-party package is imported inside `read`. The optional packages go into an extra in `pyproject.toml`, such as `trail[formats]`, not into the core dependencies.

**Acceptance checks.** For each reader: its summary agrees with the writer library on the points listed under [Observed results](#observed-results), an edit made with the writer library gives the expected rows, and a missing package gives one warning and stat-only behavior. The first two passed. The third was **not run**.

#### Step 6. Display

**Adds.** A branch in [Renderer.verb](/home/arstneio/PyCharmProjects/trail/src/trail/cli/render.py:257) that gives a `MetadataEvent` the verb `modified`, so its heading has the colour of a modification. Today it would fall through to `metadata`, which has no style.

**Acceptance checks.** The feed shows one `change:` line per row. `format=qgis` and `basis=cell` select events. **Not run**: the prototype checked `_repr_items` and the repr, not the console.

#### Step 7. Changes made while Trail was not running

This step is **not designed here**. It needs its own plan. This section records what that plan has to handle.

**The need.** The initiative says a change made while no Trail process ran can be found at the next reconciliation point.

**The constraint.** The summary is in memory only, by the 11-9 decision. After a restart there is nothing to compare a summary with. Such a change can be noticed, as "this file changed while Trail was not running", but not described field by field.

**What has to be persisted.** The file's last known stat. `AddAssetEvent` has `st_size` and a float `st_mtime` from the moment of tracking. `MetadataEvent` has `st_size` and `st_mtime_ns`. Neither is enough as it stands.

**What a design must get right.** The first draft of this plan restored the stat from the log on replay, and the reviewer showed how that goes wrong:

- With the watcher running, a write that does not change the summary logs no `MetadataEvent`. The stat in the log is then stale. At the next start, a restored stat would differ from the file, and Trail would log a change that it had in fact watched happen.
- A directory asset's `AddAssetEvent` holds the totals of every file inside it. Its `Metadata.stat` covers only its metadata files. The two cannot be compared.
- A run that ends without `stop` writes nothing at its end.
- An inode means nothing after a copy to another machine, and this project is synced between two machines. A persisted stat can hold only size and mtime.
- The prototype therefore restores nothing on replay.

**Two ways it could go.** Both are sketches.

1. Log the stat. At the end of a run, write one event with no rows for each asset whose stat changed during the run without a `MetadataEvent`. Replay then knows the last stat of every asset. This keeps one source of truth and adds events.
2. Keep a small cache file under `.trail/` with one stat per asset, rewritten when a run ends. It adds no events. It is a second place that can disagree with the log.

### Format readers

This section lists the formats and shows how each summary is extracted. It follows the reader contract and uses the helpers under [Defining a format](#defining-a-format). Every reader shown here is **Prototype** code that ran on real files. The samples are real output.

#### Which formats

The first table covers every suffix in the presets `ontrail/geospatial.txt`, `general.txt` and `ml.txt`, and the directory asset suffixes of `DirAsset.suffix2children`.

| Suffix | Preset | Reader | Summary | Status |
|---|---|---|---|---|
| `.parquet`, `.geoparquet` | geospatial, general, ml | `ParquetMetadata` | rows, row groups, columns and types, geo columns with CRS and bbox | ran |
| `.feather` | geospatial | `FeatherMetadata` | record batches, columns and types, geo columns | ran |
| `.tif`, `.tiff` | geospatial | `GeoTIFFMetadata` | size, bands, dtype, compression, tiling, overviews, CRS, transform, nodata | ran |
| `.gpkg` | geospatial | `GeoPackageMetadata` | layers with type, geometry, CRS, bounds, feature count, columns | ran |
| `.zarr` | geospatial, directory | `ZarrMetadata` | format version, groups, arrays with shape, chunks, dtype, dimensions, codecs | ran, v2 and v3 |
| `.shp`, `.shx`, `.dbf`, `.prj`, `.cpg` | geospatial | `ShapefileMetadata` | per part: shape type and bbox, record count, fields, CRS name, encoding | ran |
| `.geojson` | geospatial | `GeoJSONMetadata` | feature count, geometry types, property names, CRS | ran |
| `.fgb` | geospatial | `FlatGeobufMetadata` | name, geometry, feature count, columns, CRS, envelope | ran |
| `.kml`, `.kmz` | geospatial | `KMLMetadata` | first name, counts of folders, placemarks and geometry types | ran |
| `.gpx` | geospatial | `GPXMetadata` | version, counts of waypoints, routes, tracks and their points | ran |
| `.gdb` | geospatial, directory | `FileGDBMetadata` | layers with geometry, CRS, feature count, columns | ran |
| `.safe` | geospatial, directory | not written | product type, platform, sensing time and level, from `manifest.safe` | described. No real product was at hand to test against. |
| `.csv`, `.tsv` | general | `DelimitedMetadata` | delimiter and column names | ran |
| `.xlsx` | general | `XLSXMetadata` | sheet names in order | ran |
| `.h5` | ml | `HDF5Metadata` | datasets with shape and dtype | ran |
| `.npy`, `.npz` | ml | `NumpyMetadata` | shape and dtype, per array | ran |
| `.safetensors` | ml | `SafetensorsMetadata` | tensor names with dtype and shape | ran |
| `.n5` | directory | not written | per dataset: dimensions, block size, data type, from `attributes.json` | described. No writer was at hand. |
| `.pt`, `.pth`, `.ckpt`, `.pkl`, `.joblib` | ml | none | stat only. A pickle must never be loaded to summarize it. | by design |
| `.onnx`, `.xls`, `.txt`, `.json`, `.jsonl`, `.yaml` | general, ml | none | stat only. No cheap summary that means something. | by design |

The second table covers formats that are in no preset and that the team will likely want.

| Format | Why | Reader |
|---|---|---|
| `.jgis`, `.qgs`, `.qgz` | They are the map projects JupyterGIS and QGIS save. The 10-29 plan was about them. | `JGISMetadata`, `QGISMetadata`, ran |
| `.nc`, `.nc4`, `.hdf5` | xarray workflows write them. | `NetCDFMetadata`, `HDF5Metadata`, ran |
| `.arrow` | The same container as feather. | `FeatherMetadata` |
| `.pmtiles` | Two JupyterGIS example projects load one. | not written. A fixed 127-byte header gives tile type, zoom range and bounds. |
| a zipped shapefile, `.zip` | The JupyterGIS examples load `data/nyc/nyc.zip`. | not written. A suffix alone cannot identify it. |
| `.mbtiles` | A SQLite file like a geopackage. | not written. Its `metadata` table gives name, format, bounds and zoom range. |
| `.vrt`, `.jp2`, `.las`, `.laz`, `.ipynb`, STAC `.json` | Common beside the formats above. | not written |

The JupyterGIS source types found in the example projects of the event feed clone are `GeoJSONSource`, `GeoTiffSource`, `GeoParquetSource`, `GeoPackageVectorSource`, `GeoPackageRasterSource`, `GeoZarrSource`, `ShapefileSource`, `ImageSource`, `VectorTileSource`, `RasterSource`, `RasterDemSource`, `WmsTileSource` and `MarkerSource`. The readers that ran cover the first six.

**Observed results.** Size and time for each fixture:

| File | Reader | File size | Summary as JSON | Read time |
|---|---|---:|---:|---:|
| `arrays.npz` | `NumpyMetadata` | 658 B | 142 B | 0.4 ms |
| `big.parquet` | `ParquetMetadata` | 153 MiB | 133 B | 0.3 ms |
| `big.tif` | `GeoTIFFMetadata` | 232 MiB | 209 B | 0.1 ms |
| `book.xlsx` | `XLSXMetadata` | 15.5 KiB | 34 B | 0.2 ms |
| `buildings.qgs` | `QGISMetadata` | 75.3 KiB | 746 B | 1.4 ms |
| `cities.cpg` | `ShapefileMetadata` | 5 B | 21 B | 0.2 ms |
| `cities.csv` | `DelimitedMetadata` | 9.5 KiB | 59 B | 0.6 ms |
| `cities.dbf` | `ShapefileMetadata` | 57.3 KiB | 93 B | 0.0 ms |
| `cities.feather` | `FeatherMetadata` | 27.9 KiB | 344 B | 0.4 ms |
| `cities.fgb` | `FlatGeobufMetadata` | 72.8 KiB | 239 B | 0.1 ms |
| `cities.gdb` | `FileGDBMetadata` | directory | 159 B | 0.6 ms |
| `cities.geojson` | `GeoJSONMetadata` | 90.9 KiB | 195 B | 0.9 ms |
| `cities.gpkg` | `GeoPackageMetadata` | 176.0 KiB | 630 B | 1.0 ms |
| `cities.parquet` | `ParquetMetadata` | 26.0 KiB | 357 B | 0.3 ms |
| `cities.prj` | `ShapefileMetadata` | 145 B | 73 B | 0.2 ms |
| `cities.shp` | `ShapefileMetadata` | 13.8 KiB | 111 B | 0.0 ms |
| `cities.shx` | `ShapefileMetadata` | 4.0 KiB | 131 B | 0.0 ms |
| `cube.nc` | `NetCDFMetadata` | 105.0 KiB | 348 B | 0.7 ms |
| `cube.zarr` | `ZarrMetadata` | directory | 613 B | 1.3 ms |
| `cube_v2.zarr` | `ZarrMetadata` | directory | 559 B | 0.1 ms |
| `geotiff.qgz` | `QGISMetadata` | 11.8 KiB | 1.4 KiB | 2.1 ms |
| `grid.npy` | `NumpyMetadata` | 4.8 KiB | 56 B | 0.1 ms |
| `map.jgis` | `JGISMetadata` | 2.2 KiB | 900 B | 0.1 ms |
| `model.safetensors` | `SafetensorsMetadata` | 312 B | 114 B | 0.0 ms |
| `places.kml` | `KMLMetadata` | 3.1 KiB | 162 B | 0.1 ms |
| `places.kmz` | `KMLMetadata` | 866 B | 162 B | 0.2 ms |
| `scene.tif` | `GeoTIFFMetadata` | 3 MiB | 217 B | 0.1 ms |
| `waypoints.gpx` | `GPXMetadata` | 2.1 KiB | 98 B | 0.1 ms |
| `weights.h5` | `HDF5Metadata` | 4.6 KiB | 121 B | 0.5 ms |

The first read of a format includes importing its package. The fixtures were written with pyarrow 26.0.1, geopandas 1.2.0, pyogrio 0.13.0 with GDAL 3.12.4, rasterio 1.5.2, zarr 3.4.2, xarray 2026.9.0, netCDF4 1.7.5, h5py 3.16.0, numpy 2.5.4, openpyxl 3.1.5 and safetensors 0.8.0.

#### qgis projects: `.qgs` and `.qgz`

A `.qgs` is XML. A `.qgz` is a zip archive that holds one `.qgs` and auxiliary files. One reader serves both by checking whether the bytes are an archive. It uses the stdlib and `defusedxml`, which refuses entity expansion. The whole document is parsed, so the reader sets `max_bytes`.

The summary has the project CRS, the layers by id, and the layer order of the layer tree. Each layer has its name, type, provider, sanitized source, CRS, visibility and, where QGIS writes one, opacity.

`sanitize` strips credentials from a datasource string. It lives in `src/trail/jupyter/metadata.py` with the two project formats. That module also imports `io`, `json`, `re`, `zipfile`, `unquote` from `urllib.parse`, `ClassVar`, and `Metadata` and `Summary` from `trail.metadata`.

```python
SECRET = re.compile(
    r"(?i)\b(password|passwd|pwd|token|access_token|apikey|api_key|key|secret|authcfg"
    r"|sig|signature|user|username)=('(?:[^']|'')*'|\"[^\"]*\"|[^&\s]*)"
)
USERINFO = re.compile(r'(?<=://)[^/@\s]+@')


def sanitize(source: str) -> str:
    # a secret may hide behind percent-encoding
    out = unquote(source)
    out = SECRET.sub(r'\1=***', out)
    out = USERINFO.sub('***@', out)
    return out
```

```python
class QGISMetadata(Metadata):
    name: ClassVar[str] = 'qgis'
    suffixes: ClassVar[tuple[str, ...]] = ('.qgs', '.qgz')
    max_bytes: ClassVar[int | None] = 64 * 2**20
    # the most the project XML inside a .qgz may expand to
    max_xml: ClassVar[int] = 256 * 2**20
    named: ClassVar[tuple[str, ...]] = ('layers',)
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('crs',): 'project CRS {old} → {new}',
        ('layers', '*'): "layer '{label}' {verb}",
        ('layers', '*', 'name'): "layer renamed '{old}' → '{new}'",
        ('layers', '*', 'source'): "layer '{label}' source changed",
        ('layers', '*', 'crs'): "layer '{label}' CRS {old} → {new}",
        ('layers', '*', 'is_visible'): "layer '{label}' visible {old} → {new}",
        ('layers', '*', 'opacity'): "layer '{label}' opacity {old} → {new}",
        ('order',): 'layer order changed',
    }

    def xml(self) -> bytes:
        content = self._asset.path.read_bytes()
        if not zipfile.is_zipfile(io.BytesIO(content)):
            return content
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = [
                info
                for info in archive.infolist()
                if (
                    info.filename
                    .lower()
                    .endswith('.qgs')
                )
            ]
            if len(members) != 1:
                raise ValueError(f'Expected one .qgs member, found {len(members)}')
            member = members[0]
            if member.file_size > self.max_xml:
                raise ValueError(f'{member.filename} expands to {member.file_size} bytes')
            out = archive.read(member)
        return out

    def read(self) -> Summary:
        from defusedxml import ElementTree

        root = ElementTree.fromstring(self.xml())
        if root.tag != 'qgis':
            raise ValueError(f'Not a QGIS project: <{root.tag}>')
        tree = {
            node.get('id'): node
            for node in root.iter('layer-tree-layer')
        }
        layers = {}
        for layer in root.iterfind('projectlayers/maplayer'):
            identifier = layer.findtext('id')
            if identifier is None:
                raise ValueError('A layer has no id')
            entry = {
                "name": layer.findtext('layername'),
                "type": layer.get('type'),
                "provider": layer.findtext('provider'),
                "source": sanitize(layer.findtext('datasource', '')),
                "crs": layer.findtext('srs/spatialrefsys/authid'),
            }
            node = tree.get(identifier)
            if node is not None:
                entry['is_visible'] = node.get('checked') == 'Qt::Checked'
            opacity = layer.findtext('layerOpacity')
            if opacity is not None:
                entry['opacity'] = float(opacity)
            layers[identifier] = entry
        order = [
            node.get('id')
            for node in root.iter('layer-tree-layer')
        ]
        out = {
            "crs": root.findtext('projectCrs/spatialrefsys/authid'),
            "layers": layers,
            "order": order,
        }
        return out
```

`buildings.qgs`:

```json
{
  "crs": "EPSG:3857",
  "layers": {
    "_39982b05_dd95_4bc4_a191_6f700f51f700": {
      "name": "OpenStreetMap",
      "type": "raster",
      "provider": "wms",
      "source": "crs=EPSG:3857&format&type=xyz&url=https://tile.openstreetmap.org/{z}/{x}/{y}.png&zmax=19&zmin=0",
      "crs": "EPSG:3857",
      "is_visible": true
    },
    "test_e94a6e1f_b777_4c83_ad2b_615943ed8744": {
      "name": "test",
      "type": "vector-tile",
      "provider": "xyzvectortiles",
      "source": "type=xyz&url=https://planetarycomputer.microsoft.com/api/data/v1/vector/collections/ms-buildings/tilesets/global-footprints/tiles/{z}/{x}/{y}&zmax=13&zmin=0&http-header:referer=",
      "crs": "EPSG:3857",
      "is_visible": true,
      "opacity": 1.0
    }
  },
  "order": ["test_e94a6e1f_b777_4c83_ad2b_615943ed8744", "_39982b05_dd95_4bc4_a191_6f700f51f700"]
}
```

The OpenStreetMap layer has no `opacity`: QGIS writes `layerOpacity` for vector and vector-tile layers, and stores a raster layer's opacity elsewhere.

**It cannot see** styles, labels, layouts, filters, the view extent, or anything else outside these fields. The 10-29 plan's table of QGIS fields lists what could be added.

#### jupytergis projects: `.jgis`

A `.jgis` is JSON. The summary has the schema version, the map projection, the layers and the sources by id, and the flattened layer tree. The whole document is parsed.

```python
class JGISMetadata(Metadata):
    name: ClassVar[str] = 'jgis'
    suffixes: ClassVar[tuple[str, ...]] = ('.jgis',)
    max_bytes: ClassVar[int | None] = 64 * 2**20
    named: ClassVar[tuple[str, ...]] = ('layers', 'sources')
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('projection',): 'map projection {old} → {new}',
        ('layers', '*'): "layer '{label}' {verb}",
        ('layers', '*', 'name'): "layer renamed '{old}' → '{new}'",
        ('layers', '*', 'is_visible'): "layer '{label}' visible {old} → {new}",
        ('layers', '*', 'opacity'): "layer '{label}' opacity {old} → {new}",
        ('sources', '*'): "source '{label}' {verb}",
        ('sources', '*', 'path'): "source '{label}' path changed",
        ('order',): 'layer order changed',
    }

    @staticmethod
    def flatten(tree: list) -> list[str]:
        out = []
        for item in tree:
            if isinstance(item, dict):
                out.extend(JGISMetadata.flatten(item.get('layers', [])))
            else:
                out.append(item)
        return out

    def read(self) -> Summary:
        text = self._asset.path.read_text(encoding='utf-8')
        project = json.loads(text)
        layers = {}
        saved = project.get('layers', {})
        for identifier, layer in saved.items():
            parameters = layer.get('parameters', {})
            layers[identifier] = {
                'name': layer.get('name'),
                'type': layer.get('type'),
                'is_visible': layer.get('visible'),
                'source': parameters.get('source'),
                'opacity': parameters.get('opacity'),
            }
        sources = {}
        saved = project.get('sources', {})
        for identifier, source in saved.items():
            parameters = source.get('parameters', {})
            location = (
                parameters.get('path')
                or parameters.get('url')
                or ''
            )
            sources[identifier] = {
                'name': source.get('name'),
                'type': source.get('type'),
                'path': sanitize(str(location)),
            }
        options = project.get('options', {})
        out = {
            'schema': project.get('schemaVersion'),
            'projection': options.get('projection'),
            'layers': layers,
            'sources': sources,
            'order': self.flatten(project.get('layerTree', [])),
        }
        return out
```

`map.jgis`, a copy of `examples/geoparquet.jGIS`:

```json
{
  "schema": "0.5.0",
  "projection": "EPSG:3857",
  "layers": {
    "9556ca29-a5ec-41af-bf14-4b543c52aafe": {
      "name": "OpenStreetMap.Mapnik Layer",
      "type": "RasterLayer",
      "is_visible": true,
      "source": "2a52082b-7992-40dc-92d6-75e309a1ed27",
      "opacity": 1.0
    },
    "d7a2ad84-0750-4e9b-82c0-542fcc6b3265": {
      "name": "GeoParquet Vector Layer",
      "type": "VectorLayer",
      "is_visible": true,
      "source": "c1da95b9-8a71-4fee-b4e3-6f0b5f53d2d4",
      "opacity": 1.0
    }
  },
  "sources": {
    "2a52082b-7992-40dc-92d6-75e309a1ed27": {
      "name": "OpenStreetMap.Mapnik",
      "type": "RasterSource",
      "path": "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
    },
    "c1da95b9-8a71-4fee-b4e3-6f0b5f53d2d4": {
      "name": "Custom GeoParquet Source",
      "type": "GeoParquetSource",
      "path": "https://raw.githubusercontent.com/opengeospatial/geoparquet/main/examples/example.parquet"
    }
  },
  "order": ["9556ca29-a5ec-41af-bf14-4b543c52aafe", "d7a2ad84-0750-4e9b-82c0-542fcc6b3265"]
}
```

**It cannot see** symbology, filters, the saved view, annotations or stories.

#### parquet and geoparquet

The reader is shown under [Defining a format](#defining-a-format). It reads the footer through pyarrow and no row group. The `geo` key of the footer's key-value metadata gives the geometry columns.

`cities.parquet`, written by geopandas:

```json
{
  "num_rows": 500,
  "num_row_groups": 1,
  "columns": {
    "id": "int64",
    "name": "large_string",
    "population": "int64",
    "geometry": "binary"
  },
  "geo": {
    "primary_column": "geometry",
    "columns": {
      "geometry": {
        "encoding": "WKB",
        "geometry_types": ["Point"],
        "crs": "EPSG:4326",
        "bbox": [-59.54149490410117, -59.97719980711878, 59.94016227084322, 59.43597640654333]
      }
    }
  }
}
```

`big.parquet`:

```json
{
  "num_rows": 4000000,
  "num_row_groups": 40,
  "columns": {
    "c0": "double",
    "c1": "double",
    "c2": "double",
    "c3": "double"
  },
  "geo": null
}
```

Left out as noise: `created_by`, and the `pandas` key of the key-value metadata, which holds library versions.

**It cannot see** a rewrite of the rows that keeps the schema and the row count. Footer statistics per row group could signal that. They were not added, since they grow with the number of row groups and columns.

A stdlib reader for the footer would need a Thrift decoder. It was not attempted.

#### feather

A feather file is an Arrow IPC file. The reader maps the file and reads its schema.

```python
class FeatherMetadata(Metadata):
    name: ClassVar[str] = 'feather'
    suffixes: ClassVar[tuple[str, ...]] = ('.feather', '.arrow')
    max_columns: ClassVar[int] = 200

    def read(self) -> Summary:
        import pyarrow as pa
        from pyarrow import ipc

        with pa.memory_map(str(self._asset.path)) as source:
            reader = ipc.open_file(source)
            schema = reader.schema
            batches = reader.num_record_batches
        columns = {
            field.name: str(field.type)
            for field in schema
        }
        declared = None
        if schema.metadata is not None:
            declared = schema.metadata.get(b'geo')
        out = {
            'num_record_batches': batches,
            'columns': capped(columns, self.max_columns),
            'geo': ParquetMetadata.geo(declared),
        }
        return out
```

```json
{
  "num_record_batches": 1,
  "columns": {
    "id": "int64",
    "name": "large_string",
    "population": "int64",
    "geometry": "binary"
  },
  "geo": {
    "primary_column": "geometry",
    "columns": {
      "geometry": {
        "encoding": "WKB",
        "geometry_types": ["Point"],
        "crs": "EPSG:4326",
        "bbox": [-59.54149490410117, -59.97719980711878, 59.94016227084322, 59.43597640654333]
      }
    }
  }
}
```

**It cannot see** the row count. pyarrow gives it only by loading each record batch, which decompresses the data of a compressed file.

#### geotiff

The reader walks the image file directories with `struct`, in the stdlib. It handles both byte orders, classic tiff and bigtiff. It loads the values of fourteen tags and skips the rest, so the strip and tile offset tables are never read.

```python
class GeoTIFFMetadata(Metadata):
    name: ClassVar[str] = 'geotiff'
    suffixes: ClassVar[tuple[str, ...]] = ('.tif', '.tiff')
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('crs',): 'CRS {old} → {new}',
        ('width',): 'width {old} → {new} px',
        ('height',): 'height {old} → {new} px',
        ('bands',): 'bands {old} → {new}',
        ('nodata',): 'nodata {old} → {new}',
    }
    # struct codes of the TIFF field types
    types: ClassVar[dict[int, str]] = {
        1: 'B', 2: 'c', 3: 'H', 4: 'I', 5: 'II', 6: 'b', 7: 'B', 8: 'h', 9: 'i',
        10: 'ii', 11: 'f', 12: 'd', 13: 'I', 16: 'Q', 17: 'q', 18: 'Q',
    }
    wanted: ClassVar[frozenset[int]] = frozenset({
        254, 256, 257, 258, 259, 277, 322, 323, 339, 33550, 33922, 34264, 34735, 42113,
    })
    compressions: ClassVar[dict[int, str]] = {
        1: 'none', 5: 'lzw', 7: 'jpeg', 8: 'deflate', 32773: 'packbits', 32946: 'deflate',
        34712: 'jp2k', 34887: 'lerc', 50000: 'zstd', 50001: 'webp',
    }

    def ifds(self, file: BinaryIO) -> Iterator[dict[int, tuple]]:
        head = exact(file, 16)
        orders = {b'II': '<', b'MM': '>'}
        order = orders.get(head[:2])
        if order is None:
            raise ValueError('Not a TIFF')
        magic = struct.unpack_from(order + 'H', head, 2)[0]
        if magic == 42:
            count_code = 'H'
            entry_code = 'HHI4s'
            pointer = 'I'
            offset = struct.unpack_from(order + 'I', head, 4)[0]
        elif magic == 43:
            count_code = 'Q'
            entry_code = 'HHQ8s'
            pointer = 'Q'
            offset = struct.unpack_from(order + 'Q', head, 8)[0]
        else:
            raise ValueError('Not a TIFF')
        entry_size = struct.calcsize(order + entry_code)
        seen = set()
        while offset:
            if offset in seen:
                raise ValueError('The IFDs form a loop')
            seen.add(offset)
            file.seek(offset)
            raw = exact(file, struct.calcsize(order + count_code))
            count = struct.unpack(order + count_code, raw)[0]
            block = exact(file, count * entry_size + struct.calcsize(order + pointer))
            tags = {}
            for index in range(count):
                tag, kind, length, value = struct.unpack_from(
                    order + entry_code,
                    block,
                    index * entry_size,
                )
                if (
                    tag not in self.wanted
                    or kind not in self.types
                ):
                    continue
                code = self.types[kind]
                size = struct.calcsize('<' + code) * length
                # a value too long for the entry is stored elsewhere in the file
                if size > len(value):
                    file.seek(struct.unpack(order + pointer, value)[0])
                    value = exact(file, size)
                if code == 'c':
                    tags[tag] = (value[:length],)
                else:
                    tags[tag] = struct.unpack_from(order + code * length, value)
            offset = struct.unpack_from(order + pointer, block, count * entry_size)[0]
            yield tags

    @staticmethod
    def crs(keys: tuple | None) -> str | None:
        if keys is None:
            return None
        values = {
            keys[index]: keys[index + 3]
            for index in range(4, len(keys) - 3, 4)
            if keys[index + 1] == 0
        }
        # the projected code, else the geographic one
        code = (
            values.get(3072)
            or values.get(2048)
        )
        if code is None:
            return None
        if code == 32767:
            return 'user-defined'
        out = f'EPSG:{code}'
        return out

    @staticmethod
    def transform(tags: dict[int, tuple]) -> list[float] | None:
        matrix = tags.get(34264)
        if matrix is not None:
            out = [matrix[0], matrix[1], matrix[3], matrix[4], matrix[5], matrix[7]]
            return out
        scale = tags.get(33550)
        tie = tags.get(33922)
        if (
            scale is None
            or tie is None
        ):
            return None
        out = [
            scale[0],
            0.0,
            tie[3] - tie[0] * scale[0],
            0.0,
            -scale[1],
            tie[4] + tie[1] * scale[1],
        ]
        return out

    def read(self) -> Summary:
        with self._asset.path.open('rb') as file:
            pages = list(self.ifds(file))
        if not pages:
            raise ValueError('The TIFF has no image')
        first = pages[0]
        kinds = {1: 'uint', 2: 'int', 3: 'float'}
        kind = kinds.get(first.get(339, (1,))[0], 'unknown')
        bits = first.get(258, (1,))[0]
        tile = None
        if 322 in first:
            tile = [first[322][0], first[323][0]]
        nodata = None
        if 42113 in first:
            nodata = (
                first[42113][0]
                .rstrip(b'\x00')
                .decode('ascii', 'replace')
            )
        overviews = 0
        for page in pages[1:]:
            subfile = page.get(254, (0,))[0]
            # a reduced-resolution image that is not a mask
            if (
                subfile & 1
                and not subfile & 4
            ):
                overviews += 1
        compression = first.get(259, (1,))[0]
        out = {
            'width': first[256][0],
            'height': first[257][0],
            'bands': first.get(277, (1,))[0],
            'dtype': f'{kind}{bits}',
            'compression': self.compressions.get(compression, str(compression)),
            'tile': tile,
            'overviews': overviews,
            'crs': self.crs(first.get(34735)),
            'transform': self.transform(first),
            'nodata': nodata,
        }
        return out
```

`scene.tif`:

```json
{
  "width": 1024,
  "height": 768,
  "bands": 3,
  "dtype": "uint8",
  "compression": "deflate",
  "tile": [256, 256],
  "overviews": 2,
  "crs": "EPSG:32633",
  "transform": [10.0, 0.0, 500000.0, 0.0, -10.0, 4649776.0],
  "nodata": "0"
}
```

Every field agreed with rasterio on both fixtures: width, height, band count, dtype, CRS, transform, nodata, overview count and tile size.

- The CRS comes from the GeoKey directory: the projected code if there is one, else the geographic code. A CRS with no code gives `user-defined`.
- `nodata` is the text GDAL stores, not a number.
- Left out as noise: the `DateTime` and `Software` tags.

**It cannot see** pixel values. A file cut inside its pixel data still has a whole header, so the reader returns a summary for it.

#### geopackage

A geopackage is a SQLite database. The reader opens it read-only with the stdlib's `sqlite3` and reads the catalog tables.

```python
class GeoPackageMetadata(Metadata):
    name: ClassVar[str] = 'geopackage'
    suffixes: ClassVar[tuple[str, ...]] = ('.gpkg',)
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('layers', '*'): "layer '{1}' {verb}",
        ('layers', '*', 'num_features'): "layer '{1}' features {old} → {new}",
        ('layers', '*', 'crs'): "layer '{1}' CRS {old} → {new}",
        ('layers', '*', 'columns', '*'): "column '{3}' {verb} in layer '{1}'",
    }

    def read(self) -> Summary:
        import sqlite3
        from urllib.parse import quote

        uri = f'file:{quote(str(self._asset.path))}?mode=ro'
        connection = sqlite3.connect(uri, uri=True)
        try:
            rows = connection.execute(
                'SELECT srs_id, organization, organization_coordsys_id FROM gpkg_spatial_ref_sys'
            )
            srs = {
                identifier: f'{organization}:{code}'
                for identifier, organization, code in rows
            }
            rows = connection.execute(
                'SELECT table_name, geometry_type_name FROM gpkg_geometry_columns'
            )
            geometry = dict(rows)
            counts = {}
            has_counts = (
                connection
                .execute("SELECT 1 FROM sqlite_master WHERE name = 'gpkg_ogr_contents'")
                .fetchone()
            )
            if has_counts:
                rows = connection.execute(
                    'SELECT table_name, feature_count FROM gpkg_ogr_contents'
                )
                counts = dict(rows)
            query = (
                'SELECT table_name, data_type, identifier, srs_id, min_x, min_y, max_x, max_y '
                'FROM gpkg_contents ORDER BY table_name'
            )
            contents = (
                connection
                .execute(query)
                .fetchall()
            )
            layers = {}
            for table, kind, identifier, srs_id, *bounds in contents:
                quoted = table.replace('"', '""')
                columns = {
                    row[1]: row[2]
                    for row in connection.execute(f'PRAGMA table_info("{quoted}")')
                }
                layers[table] = {
                    'name': identifier,
                    'type': kind,
                    'geometry': geometry.get(table),
                    'crs': srs.get(srs_id),
                    'bounds': bounds,
                    'num_features': counts.get(table),
                    'columns': columns,
                }
        finally:
            connection.close()
        out = {'layers': layers}
        return out
```

`cities.gpkg`:

```json
{
  "layers": {
    "capitals": {
      "name": "capitals",
      "type": "features",
      "geometry": "POINT",
      "crs": "EPSG:4326",
      "bounds": [-58.54241686636649, -59.96391718716925, 59.64123516869901, 59.0427874297734],
      "num_features": 50,
      "columns": {
        "fid": "INTEGER",
        "geom": "POINT",
        "id": "INTEGER",
        "name": "TEXT",
        "population": "INTEGER"
      }
    },
    "cities": {
      "name": "cities",
      "type": "features",
      "geometry": "POINT",
      "crs": "EPSG:4326",
      "bounds": [-59.54149490410117, -59.97719980711878, 59.94016227084322, 59.43597640654333],
      "num_features": 500,
      "columns": {
        "fid": "INTEGER",
        "geom": "POINT",
        "id": "INTEGER",
        "name": "TEXT",
        "population": "INTEGER"
      }
    }
  }
}
```

- The feature count comes from `gpkg_ogr_contents`, which GDAL maintains. The reader never counts rows. A geopackage written without that table has `num_features: null`.
- Left out as noise: `last_change` in `gpkg_contents`.
- After a read, no `-journal`, `-wal` or `-shm` file was left beside the database.

**It cannot see** edits that keep the counts, the bounds and the columns. A database in write-ahead-log mode keeps recent writes in a `-wal` file until a checkpoint. The reader was not tested against one.

#### zarr

A zarr store is a directory. Version 2 describes each node with `.zgroup`, `.zarray` and `.zattrs`. Version 3 uses one `zarr.json` per node. Either can carry consolidated metadata, one file that describes the whole store. The reader uses the stdlib.

```python
class ZarrMetadata(Metadata):
    name: ClassVar[str] = 'zarr'
    suffixes: ClassVar[tuple[str, ...]] = ('.zarr',)
    max_nodes: ClassVar[int] = 500
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('arrays', '*'): "array '{1}' {verb}",
        ('arrays', '*', 'shape'): "array '{1}' shape {old} → {new}",
        ('arrays', '*', 'chunks'): "array '{1}' chunks {old} → {new}",
        ('arrays', '*', 'dtype'): "array '{1}' dtype {old} → {new}",
    }

    def files(self) -> list[Path]:
        root = self._asset.path
        for name in ('.zmetadata', 'zarr.json'):
            path = root / name
            if not path.is_file():
                continue
            if name == 'zarr.json':
                node = json.loads(path.read_text(encoding='utf-8'))
                if 'consolidated_metadata' not in node:
                    continue
            # consolidated metadata describes the whole store in one file
            out = [path]
            return out
        found = []
        pending = [root]
        while (
            pending
            and len(found) < self.max_nodes
        ):
            directory = pending.pop()
            names = ('zarr.json', '.zarray', '.zgroup', '.zattrs')
            here = [
                directory / name
                for name in names
                if (directory / name).is_file()
            ]
            found.extend(here)
            is_array = (
                (directory / '.zarray').is_file()
                or (
                    (directory / 'zarr.json').is_file()
                    and (directory / 'c').is_dir()
                )
            )
            # an array holds chunks, not nodes
            if is_array:
                continue
            with os.scandir(directory) as entries:
                children = [
                    Path(entry.path)
                    for entry in entries
                    if entry.is_dir(follow_symlinks=False)
                ]
            pending.extend(sorted(children))
        return found

    def sources(self) -> list[Path]:
        out = self.files()
        return out

    @staticmethod
    def array(
            node: dict,
            attrs: dict,
    ) -> dict[str, JSONValue]:
        if node.get('zarr_format') == 3:
            chunks = (
                node.get('chunk_grid', {})
                .get('configuration', {})
                .get('chunk_shape')
            )
            codecs = [
                codec.get('name')
                for codec in node.get('codecs', [])
            ]
            out = {
                'shape': node.get('shape'),
                'chunks': chunks,
                'dtype': node.get('data_type'),
                'dims': node.get('dimension_names'),
                'codecs': codecs,
            }
            return out
        compressor = node.get('compressor')
        codecs = []
        if compressor is not None:
            codecs = [compressor.get('id')]
        out = {
            'shape': node.get('shape'),
            'chunks': node.get('chunks'),
            'dtype': node.get('dtype'),
            'dims': attrs.get('_ARRAY_DIMENSIONS'),
            'codecs': codecs,
        }
        return out

    def nodes(self) -> dict[str, dict]:
        root = self._asset.path
        files = self.files()
        documents = {}
        if (
            len(files) == 1
            and files[0].name == '.zmetadata'
        ):
            consolidated = json.loads(files[0].read_text(encoding='utf-8'))
            documents = consolidated['metadata']
        elif (
            len(files) == 1
            and files[0].parent == root
            and files[0].name == 'zarr.json'
        ):
            node = json.loads(files[0].read_text(encoding='utf-8'))
            documents['zarr.json'] = node
            inner = (
                node.get('consolidated_metadata', {})
                .get('metadata', {})
            )
            for key, value in inner.items():
                documents[f'{key}/zarr.json'] = value
        if not documents:
            for path in files:
                key = str(path.relative_to(root))
                documents[key] = json.loads(path.read_text(encoding='utf-8'))
        return documents

    def read(self) -> Summary:
        documents = self.nodes()
        if not documents:
            raise ValueError('The store has no metadata')
        arrays = {}
        groups = []
        version = None
        for key, node in sorted(documents.items()):
            directory, _, name = key.rpartition('/')
            location = directory or '/'
            if name == '.zarray':
                attrs = documents.get(f'{directory}/.zattrs'.lstrip('/'), {})
                arrays[location] = self.array(node, attrs)
                version = 2
            elif name == '.zgroup':
                groups.append(location)
                version = 2
            elif name == 'zarr.json':
                version = 3
                if node.get('node_type') == 'array':
                    arrays[location] = self.array(node, {})
                else:
                    groups.append(location)
        out = {
            'zarr_format': version,
            'groups': groups,
            'arrays': capped(arrays, self.max_nodes),
        }
        return out
```

`cube.zarr`, version 3, written by xarray:

```json
{
  "zarr_format": 3,
  "groups": ["/"],
  "arrays": {
    "elevation": {
      "shape": [64, 64],
      "chunks": [64, 64],
      "dtype": "float64",
      "dims": ["y", "x"],
      "codecs": ["bytes", "zstd"]
    },
    "temperature": {
      "shape": [4, 64, 64],
      "chunks": [4, 64, 64],
      "dtype": "float32",
      "dims": ["time", "y", "x"],
      "codecs": ["bytes", "zstd"]
    },
    "time": {
      "shape": [4],
      "chunks": [4],
      "dtype": "int64",
      "dims": ["time"],
      "codecs": ["bytes", "zstd"]
    },
    "x": {
      "shape": [64],
      "chunks": [64],
      "dtype": "int64",
      "dims": ["x"],
      "codecs": ["bytes", "zstd"]
    },
    "y": {
      "shape": [64],
      "chunks": [64],
      "dtype": "int64",
      "dims": ["y"],
      "codecs": ["bytes", "zstd"]
    }
  }
}
```

- `sources` returns the metadata files. These files are what a check stats.
- With consolidated metadata, one file is read and one is statted. Without it, the reader walks the groups, on a read and on every check. It never lists an array's directory, so the number of chunks does not matter.
- Stale consolidated metadata gives a stale summary. Nothing detects that.

**It cannot see** a write of chunks alone. The summary is equal, and the `DirectoryAccessEvent` still records the write. Attributes are not summarized.

#### The shapefile parts

The geospatial preset tracks each part of a shapefile as its own asset, so each part has its own summary. One class reads all five and chooses by suffix.

```python
class ShapefileMetadata(Metadata):
    name: ClassVar[str] = 'shapefile'
    suffixes: ClassVar[tuple[str, ...]] = ('.shp', '.shx', '.dbf', '.prj', '.cpg')
    shapes: ClassVar[dict[int, str]] = {
        0: 'Null', 1: 'Point', 3: 'PolyLine', 5: 'Polygon', 8: 'MultiPoint',
        11: 'PointZ', 13: 'PolyLineZ', 15: 'PolygonZ', 18: 'MultiPointZ',
        21: 'PointM', 23: 'PolyLineM', 25: 'PolygonM', 28: 'MultiPointM', 31: 'MultiPatch',
    }

    def header(self) -> Summary:
        path = self._asset.path
        with path.open('rb') as file:
            head = exact(file, 100)
        code, words = struct.unpack_from('>i20xi', head, 0)
        if code != 9994:
            raise ValueError('Not a shapefile')
        size = path.stat().st_size
        if words * 2 != size:
            raise ValueError(f'The header says {words * 2} bytes, the file has {size}')
        shape = struct.unpack_from('<i', head, 32)[0]
        bbox = list(struct.unpack_from('<4d', head, 36))
        out = {
            'shape_type': self.shapes.get(shape, str(shape)),
            'bbox': bbox,
        }
        if path.suffix.lower() == '.shx':
            out['num_records'] = (size - 100) // 8
        return out

    def table(self) -> Summary:
        path = self._asset.path
        with path.open('rb') as file:
            head = exact(file, 32)
            records, header_size, record_size = struct.unpack_from('<IHH', head, 4)
            body = exact(file, header_size - 32)
        if path.stat().st_size < header_size + records * record_size:
            raise ValueError('The table is shorter than its header says')
        fields = {}
        for start in range(0, len(body) - 1, 32):
            descriptor = body[start:start + 32]
            if descriptor[:1] == b'\r':
                break
            name = (
                descriptor[:11]
                .split(b'\x00')[0]
                .decode('ascii', 'replace')
            )
            kind = chr(descriptor[11])
            fields[name] = f'{kind}({descriptor[16]},{descriptor[17]})'
        out = {
            'num_records': records,
            'fields': fields,
        }
        return out

    def projection(self) -> Summary:
        text = (
            self._asset.path
            .read_text(encoding='utf-8')
            .strip()
        )
        name = re.search(r'^\w+\["([^"]*)"', text)
        authority = re.search(r'(?:AUTHORITY|ID)\["(\w+)",\s*"?(\d+)"?\]\]$', text)
        out = {
            'name': None,
            'authority': None,
            'digest': digest(text),
        }
        if name:
            out['name'] = name.group(1)
        if authority:
            out['authority'] = f'{authority.group(1)}:{authority.group(2)}'
        return out

    def read(self) -> Summary:
        suffix = self._asset.path.suffix.lower()
        if suffix in ('.shp', '.shx'):
            out = self.header()
        elif suffix == '.dbf':
            out = self.table()
        elif suffix == '.prj':
            out = self.projection()
        else:
            text = self._asset.path.read_text(encoding='ascii')
            out = {'encoding': text.strip()}
        return out
```

`cities.shp`, `cities.shx`, `cities.dbf`, `cities.prj` and `cities.cpg`:

```json
{
  "shape_type": "Point",
  "bbox": [-59.54149490410117, -59.97719980711878, 59.94016227084322, 59.43597640654333]
}
```

```json
{
  "shape_type": "Point",
  "bbox": [-59.54149490410117, -59.97719980711878, 59.94016227084322, 59.43597640654333],
  "num_records": 500
}
```

```json
{
  "num_records": 500,
  "fields": {
    "id": "N(18,0)",
    "name": "C(80,0)",
    "population": "N(18,0)"
  }
}
```

```json
{
  "name": "GCS_WGS_1984",
  "authority": null,
  "digest": "9345f6d4ca11dcf2"
}
```

```json
{
  "encoding": "UTF-8"
}
```

- `.shp` and `.shx` state their own length in the header, and `.dbf` states its record count and record size. The reader checks each against the file size, so these three raise on a cut file.
- Left out as noise: the date of last update in the `.dbf` header.
- One save changes up to five assets and logs up to five events.

**It cannot see** geometry edits that keep the bounding box and the record count.

#### geojson

A geojson file has no header. The reader parses the whole file, so its cost grows with the data. `max_bytes` keeps it to files of 32 MiB or less. A larger file has no summary and is tracked by its stat.

```python
class GeoJSONMetadata(Metadata):
    name: ClassVar[str] = 'geojson'
    suffixes: ClassVar[tuple[str, ...]] = ('.geojson',)
    # the whole file is parsed, so large files keep only their stat
    max_bytes: ClassVar[int | None] = 32 * 2**20
    max_properties: ClassVar[int] = 200
    phrases: ClassVar[dict[tuple[str, ...], str]] = {
        ('num_features',): 'features {old} → {new}',
        ('properties', '*'): "property '{1}' {verb}",
    }

    def read(self) -> Summary:
        text = self._asset.path.read_text(encoding='utf-8')
        document = json.loads(text)
        kind = document.get('type')
        if kind == 'FeatureCollection':
            features = document.get('features', [])
        elif kind == 'Feature':
            features = [document]
        else:
            features = []
        geometries = set()
        properties = {}
        for feature in features:
            geometry = feature.get('geometry')
            if geometry is not None:
                geometries.add(geometry.get('type'))
            for key in feature.get('properties') or ():
                properties[key] = True
        crs = None
        declared = document.get('crs')
        if declared is not None:
            crs = (
                declared
                .get('properties', {})
                .get('name')
            )
        out = {
            'type': kind,
            'num_features': len(features),
            'geometry_types': sorted(geometries),
            'properties': capped(properties, self.max_properties),
            'crs': crs,
            'bbox': document.get('bbox'),
        }
        return out
```

```json
{
  "type": "FeatureCollection",
  "num_features": 500,
  "geometry_types": ["Point"],
  "properties": {
    "id": true,
    "name": true,
    "population": true
  },
  "crs": "urn:ogc:def:crs:OGC:1.3:CRS84",
  "bbox": null
}
```

**It cannot see** coordinates or property values. The 32 MiB limit is a default that was not tuned.

#### flatgeobuf

A flatgeobuf file starts with eight magic bytes and a header in the FlatBuffers encoding. The reader decodes only the fields it needs, by hand, with `struct`.

```python
class FlatGeobufMetadata(Metadata):
    name: ClassVar[str] = 'flatgeobuf'
    suffixes: ClassVar[tuple[str, ...]] = ('.fgb',)
    geometries: ClassVar[tuple[str, ...]] = (
        'Unknown', 'Point', 'LineString', 'Polygon', 'MultiPoint', 'MultiLineString',
        'MultiPolygon', 'GeometryCollection', 'CircularString', 'CompoundCurve', 'CurvePolygon',
        'MultiCurve', 'MultiSurface', 'Curve', 'Surface', 'PolyhedralSurface', 'TIN', 'Triangle',
    )
    kinds: ClassVar[tuple[str, ...]] = (
        'Byte', 'UByte', 'Bool', 'Short', 'UShort', 'Int', 'UInt', 'Long', 'ULong', 'Float',
        'Double', 'String', 'Json', 'DateTime', 'Binary',
    )

    @staticmethod
    def field(
            buffer: bytes,
            table: int,
            index: int,
    ) -> int | None:
        vtable = table - struct.unpack_from('<i', buffer, table)[0]
        size = struct.unpack_from('<H', buffer, vtable)[0]
        slot = 4 + 2 * index
        if slot >= size:
            return None
        offset = struct.unpack_from('<H', buffer, vtable + slot)[0]
        if not offset:
            return None
        out = table + offset
        return out

    @staticmethod
    def follow(
            buffer: bytes,
            position: int,
    ) -> int:
        out = position + struct.unpack_from('<I', buffer, position)[0]
        return out

    def string(
            self,
            buffer: bytes,
            table: int,
            index: int,
    ) -> str | None:
        position = self.field(buffer, table, index)
        if position is None:
            return None
        start = self.follow(buffer, position)
        length = struct.unpack_from('<I', buffer, start)[0]
        out = (
            buffer[start + 4:start + 4 + length]
            .decode('utf-8')
        )
        return out

    def scalar(
            self,
            buffer: bytes,
            table: int,
            index: int,
            code: str,
            default: int,
    ) -> int:
        position = self.field(buffer, table, index)
        if position is None:
            return default
        out = struct.unpack_from('<' + code, buffer, position)[0]
        return out

    def read(self) -> Summary:
        with self._asset.path.open('rb') as file:
            magic = exact(file, 8)
            if (
                magic[:3] != b'fgb'
                or magic[4:7] != b'fgb'
            ):
                raise ValueError('Not a FlatGeobuf file')
            size = struct.unpack('<I', exact(file, 4))[0]
            buffer = exact(file, size)
        header = self.follow(buffer, 0)
        columns = {}
        position = self.field(buffer, header, 7)
        if position is not None:
            start = self.follow(buffer, position)
            count = struct.unpack_from('<I', buffer, start)[0]
            for index in range(count):
                column = self.follow(buffer, start + 4 + 4 * index)
                kind = self.scalar(buffer, column, 1, 'B', 0)
                columns[self.string(buffer, column, 0)] = self.kinds[kind]
        envelope = None
        position = self.field(buffer, header, 1)
        if position is not None:
            start = self.follow(buffer, position)
            count = struct.unpack_from('<I', buffer, start)[0]
            envelope = list(struct.unpack_from(f'<{count}d', buffer, start + 4))
        crs = None
        position = self.field(buffer, header, 10)
        if position is not None:
            table = self.follow(buffer, position)
            crs = f"{self.string(buffer, table, 0)}:{self.scalar(buffer, table, 1, 'i', 0)}"
        geometry = self.scalar(buffer, header, 2, 'B', 0)
        out = {
            'name': self.string(buffer, header, 0),
            'geometry': self.geometries[geometry],
            'num_features': self.scalar(buffer, header, 8, 'Q', 0),
            'columns': capped(columns, 200),
            'crs': crs,
            'envelope': envelope,
        }
        return out
```

```json
{
  "name": "cities",
  "geometry": "Point",
  "num_features": 500,
  "columns": {
    "id": "Long",
    "name": "String",
    "population": "Long"
  },
  "crs": "EPSG:4326",
  "envelope": [-59.54149490410117, -59.97719980711878, 59.94016227084322, 59.43597640654333]
}
```

The feature count, geometry type, column names and CRS agreed with pyogrio.

**It cannot see** edits that keep the count and the envelope. A file cut after its header still gives a summary.

#### kml and kmz

A kml file is XML. A kmz file is a zip archive whose main document is its first `.kml` member. The reader parses the whole document with `defusedxml` and counts elements, so it sets `max_bytes`.

```python
class KMLMetadata(Metadata):
    name: ClassVar[str] = 'kml'
    suffixes: ClassVar[tuple[str, ...]] = ('.kml', '.kmz')
    # the whole document is parsed
    max_bytes: ClassVar[int | None] = 32 * 2**20
    counted: ClassVar[tuple[str, ...]] = (
        'Folder', 'Placemark', 'Point', 'LineString', 'Polygon', 'MultiGeometry',
        'NetworkLink', 'GroundOverlay',
    )

    def xml(self) -> bytes:
        path = self._asset.path
        if path.suffix.lower() != '.kmz':
            out = path.read_bytes()
            return out
        with zipfile.ZipFile(path) as archive:
            members = [
                info
                for info in archive.infolist()
                if (
                    info.filename
                    .lower()
                    .endswith('.kml')
                )
            ]
            if not members:
                raise ValueError('The archive holds no .kml')
            # the main document is the first .kml in the archive
            member = members[0]
            if member.file_size > self.max_bytes:
                raise ValueError(f'{member.filename} expands to {member.file_size} bytes')
            out = archive.read(member)
        return out

    def read(self) -> Summary:
        from defusedxml import ElementTree

        root = ElementTree.fromstring(self.xml())
        if local(root.tag) != 'kml':
            raise ValueError(f'Not a kml document: <{root.tag}>')
        counts = dict.fromkeys(self.counted, 0)
        name = None
        for node in root.iter():
            tag = local(node.tag)
            if tag in counts:
                counts[tag] += 1
            elif (
                tag == 'name'
                and name is None
            ):
                name = node.text
        out = {
            'name': name,
            'counts': counts,
        }
        return out
```

`places.kml`, written by GDAL:

```json
{
  "name": "places",
  "counts": {
    "Folder": 1,
    "Placemark": 20,
    "Point": 20,
    "LineString": 0,
    "Polygon": 0,
    "MultiGeometry": 0,
    "NetworkLink": 0,
    "GroundOverlay": 0
  }
}
```

The summary of the `.kmz` made from that file was equal.

**It cannot see** coordinates, names other than the first, or styles.

#### gpx

A gpx file is XML. The reader counts waypoints, routes, tracks and their points.

```python
class GPXMetadata(Metadata):
    name: ClassVar[str] = 'gpx'
    suffixes: ClassVar[tuple[str, ...]] = ('.gpx',)
    # the whole document is parsed
    max_bytes: ClassVar[int | None] = 32 * 2**20
    counted: ClassVar[tuple[str, ...]] = ('wpt', 'rte', 'rtept', 'trk', 'trkseg', 'trkpt')

    def read(self) -> Summary:
        from defusedxml import ElementTree

        content = self._asset.path.read_bytes()
        root = ElementTree.fromstring(content)
        if local(root.tag) != 'gpx':
            raise ValueError(f'Not a gpx document: <{root.tag}>')
        counts = dict.fromkeys(self.counted, 0)
        for node in root.iter():
            tag = local(node.tag)
            if tag in counts:
                counts[tag] += 1
        out = {
            'version': root.get('version'),
            'counts': counts,
        }
        return out
```

`waypoints.gpx`, written by GDAL:

```json
{
  "version": "1.1",
  "counts": {
    "wpt": 20,
    "rte": 0,
    "rtept": 0,
    "trk": 0,
    "trkseg": 0,
    "trkpt": 0
  }
}
```

Left out as noise: the `creator` attribute, which names the writing program and its version.

**It cannot see** coordinates or times.

#### netcdf and hdf5

Both need a package. `netCDF4` reads classic netcdf files and those based on hdf5. `h5py` lists the datasets of any hdf5 file.

```python
class NetCDFMetadata(Metadata):
    name: ClassVar[str] = 'netcdf'
    suffixes: ClassVar[tuple[str, ...]] = ('.nc', '.nc4')
    max_variables: ClassVar[int] = 500

    def read(self) -> Summary:
        import netCDF4

        with netCDF4.Dataset(self._asset.path, mode='r') as dataset:
            dimensions = {
                name: len(dimension)
                for name, dimension in dataset.dimensions.items()
            }
            variables = {
                name: {
                    'dtype': str(variable.dtype),
                    'dims': list(variable.dimensions),
                }
                for name, variable in dataset.variables.items()
            }
            conventions = getattr(dataset, 'Conventions', None)
            model = dataset.data_model
        out = {
            'format': model,
            'conventions': conventions,
            'dimensions': dimensions,
            'variables': capped(variables, self.max_variables),
        }
        return out
```

```json
{
  "format": "NETCDF4",
  "conventions": null,
  "dimensions": {
    "time": 4,
    "y": 64,
    "x": 64
  },
  "variables": {
    "temperature": {
      "dtype": "float32",
      "dims": ["time", "y", "x"]
    },
    "elevation": {
      "dtype": "float64",
      "dims": ["y", "x"]
    },
    "time": {
      "dtype": "int64",
      "dims": ["time"]
    },
    "y": {
      "dtype": "int64",
      "dims": ["y"]
    },
    "x": {
      "dtype": "int64",
      "dims": ["x"]
    }
  }
}
```

```python
class HDF5Metadata(Metadata):
    name: ClassVar[str] = 'hdf5'
    suffixes: ClassVar[tuple[str, ...]] = ('.h5', '.hdf5')
    max_datasets: ClassVar[int] = 500

    def read(self) -> Summary:
        import h5py

        datasets = {}

        def visit(
                name: str,
                node: object,
        ) -> None:
            if isinstance(node, h5py.Dataset):
                datasets[name] = {
                    'shape': list(node.shape),
                    'dtype': str(node.dtype),
                }

        with h5py.File(self._asset.path, 'r') as file:
            file.visititems(visit)
        out = {'datasets': capped(datasets, self.max_datasets)}
        return out
```

```json
{
  "datasets": {
    "layer1/bias": {
      "shape": [8],
      "dtype": "float64"
    },
    "layer1/kernel": {
      "shape": [8, 8],
      "dtype": "float64"
    }
  }
}
```

**They cannot see** values or attributes, apart from `Conventions`.

#### File geodatabase

An Esri file geodatabase is a directory. The reader uses pyogrio, which reads it through GDAL.

```python
class FileGDBMetadata(Metadata):
    name: ClassVar[str] = 'filegdb'
    suffixes: ClassVar[tuple[str, ...]] = ('.gdb',)

    def sources(self) -> list[Path]:
        # the catalog and the item table change when layers or their definitions change
        root = self._asset.path
        out = [
            path
            for path in (root / 'a00000001.gdbtable', root / 'a00000004.gdbtable')
            if path.is_file()
        ]
        return out

    def read(self) -> Summary:
        import pyogrio

        layers = {}
        for name, geometry in pyogrio.list_layers(self._asset.path):
            info = pyogrio.read_info(self._asset.path, layer=name)
            names = info['fields'].tolist()
            kinds = info['dtypes'].tolist()
            columns = dict(zip(names, kinds, strict=True))
            layers[str(name)] = {
                'geometry': geometry,
                'crs': info['crs'],
                'num_features': info['features'],
                'columns': columns,
            }
        out = {'layers': layers}
        return out
```

```json
{
  "layers": {
    "cities": {
      "geometry": "Point",
      "crs": "EPSG:4326",
      "num_features": 500,
      "columns": {
        "id": "float64",
        "name": "object",
        "population": "float64"
      }
    }
  }
}
```

`sources` names the two system tables as the files to stat. That choice is a guess. It was **not tested** that every change of a layer's definition rewrites one of them. If neither file exists, the asset has nothing to stat, and only the watcher leads to a read.

#### csv and tsv

The reader reads the first 64 KiB and returns the delimiter and the column names. It does not count rows, since that needs the whole file.

```python
class DelimitedMetadata(Metadata):
    name: ClassVar[str] = 'delimited'
    suffixes: ClassVar[tuple[str, ...]] = ('.csv', '.tsv')
    # only the start of the file is read
    head: ClassVar[int] = 64 * 2**10

    def read(self) -> Summary:
        with self._asset.path.open('rb') as file:
            raw = file.read(self.head)
        text = raw.decode('utf-8-sig', 'replace')
        first = text.split('\n', 1)[0]
        delimiter = '\t'
        if self._asset.path.suffix.lower() == '.csv':
            delimiter = (
                csv.Sniffer()
                .sniff(first, delimiters=',;|\t')
                .delimiter
            )
        columns = next(csv.reader(io.StringIO(first), delimiter=delimiter))
        out = {
            'delimiter': delimiter,
            'columns': columns,
        }
        return out
```

```json
{
  "delimiter": ",",
  "columns": ["id", "name", "population"]
}
```

#### npy and npz

A npy file starts with a short header that holds the shape and the dtype as a Python literal. A npz file is a zip archive of npy files. The reader reads only the header of each member.

```python
class NumpyMetadata(Metadata):
    name: ClassVar[str] = 'numpy'
    suffixes: ClassVar[tuple[str, ...]] = ('.npy', '.npz')
    max_arrays: ClassVar[int] = 500

    @staticmethod
    def header(file: BinaryIO) -> dict[str, JSONValue]:
        magic = exact(file, 8)
        if magic[:6] != b'\x93NUMPY':
            raise ValueError('Not a npy file')
        # version 1 holds the header length in two bytes, later versions in four
        if magic[6] == 1:
            size = struct.unpack('<H', exact(file, 2))[0]
        else:
            size = struct.unpack('<I', exact(file, 4))[0]
        text = (
            exact(file, size)
            .decode('latin1')
        )
        header = ast.literal_eval(text)
        out = {
            'shape': list(header['shape']),
            'dtype': str(header['descr']),
            'is_fortran': header['fortran_order'],
        }
        return out

    def read(self) -> Summary:
        path = self._asset.path
        if path.suffix.lower() == '.npy':
            with path.open('rb') as file:
                out = self.header(file)
            return out
        arrays = {}
        with zipfile.ZipFile(path) as archive:
            for info in archive.infolist():
                name = info.filename.removesuffix('.npy')
                with archive.open(info) as member:
                    arrays[name] = self.header(member)
        out = {'arrays': capped(arrays, self.max_arrays)}
        return out
```

`grid.npy` and `arrays.npz`:

```json
{
  "shape": [30, 40],
  "dtype": "<f4",
  "is_fortran": false
}
```

```json
{
  "arrays": {
    "weights": {
      "shape": [10, 3],
      "dtype": "<f8",
      "is_fortran": false
    },
    "index": {
      "shape": [5],
      "dtype": "<i8",
      "is_fortran": false
    }
  }
}
```

**It cannot see** values. A npy file cut after its header still gives a summary.

#### xlsx

An xlsx file is a zip archive. The reader reads the sheet names from `xl/workbook.xml` and no cell.

```python
class XLSXMetadata(Metadata):
    name: ClassVar[str] = 'xlsx'
    suffixes: ClassVar[tuple[str, ...]] = ('.xlsx',)
    # the most the workbook part may expand to
    max_xml: ClassVar[int] = 16 * 2**20

    def read(self) -> Summary:
        from defusedxml import ElementTree

        with zipfile.ZipFile(self._asset.path) as archive:
            member = archive.getinfo('xl/workbook.xml')
            if member.file_size > self.max_xml:
                raise ValueError(f'{member.filename} expands to {member.file_size} bytes')
            content = archive.read(member)
        root = ElementTree.fromstring(content)
        sheets = [
            node.get('name')
            for node in root.iter()
            if local(node.tag) == 'sheet'
        ]
        out = {'sheets': sheets}
        return out
```

```json
{
  "sheets": ["cities", "capitals"]
}
```

#### safetensors

A safetensors file starts with the length of a JSON header, then the header. The header names each tensor with its dtype and shape.

```python
class SafetensorsMetadata(Metadata):
    name: ClassVar[str] = 'safetensors'
    suffixes: ClassVar[tuple[str, ...]] = ('.safetensors',)
    # the most the header may claim
    max_header: ClassVar[int] = 64 * 2**20
    max_tensors: ClassVar[int] = 500

    def read(self) -> Summary:
        with self._asset.path.open('rb') as file:
            size = struct.unpack('<Q', exact(file, 8))[0]
            if size > self.max_header:
                raise ValueError(f'The header claims {size} bytes')
            header = json.loads(exact(file, size))
        tensors = {
            name: {
                'dtype': entry.get('dtype'),
                'shape': entry.get('shape'),
            }
            for name, entry in header.items()
            if name != '__metadata__'
        }
        out = {'tensors': capped(tensors, self.max_tensors)}
        return out
```

```json
{
  "tensors": {
    "encoder.bias": {
      "dtype": "F32",
      "shape": [8]
    },
    "encoder.weight": {
      "dtype": "F32",
      "shape": [8, 4]
    }
  }
}
```

**It cannot see** values. A file cut after its header still gives a summary.

### Observed results

This section gathers what was run and what came out. Everything here was run on 2026-11-11 on Linux 6.12, with Python 3.12.16, IPython 9.17.1, ipykernel 7.3.0 and jupyter_client 8.10.0, against the `trail` package at commit `0de8c21`. Temporary projects were on tmpfs.

Two environments were used. The core checks, the watcher path and the IPython checks ran in the project's own `.venv`. The readers that need a third-party package ran in a separate environment made with `uv`, which held the writer libraries and Trail's dependencies, with the repository's `src` directory on the import path. Nothing was installed into the project's environment.

| What | Result | Where the detail is |
|---|---|---|
| The core: namespace, registry, comparison, event, replay, the check with no watcher | 53 of 53 checks pass | [Step 1](#step-1-the-core), [Step 2](#step-2-the-first-two-formats) |
| The three claims against per-format event classes | all three confirmed | [The designs that were compared](#the-designs-that-were-compared) |
| The watcher path with real filesystem events | one read and one event per save | [Reads: the watcher path](#reads-the-watcher-path) |
| `post_run_cell` in a shell and in a real kernel | thirteen questions answered | [The IPython extension](#the-ipython-extension) |
| Memory and comparison time | measured for 10, 100 and 1,000 entries | [Representation and memory](#representation-and-memory) |
| Nineteen reader classes on 29 fixtures | all return a summary | [Format readers](#format-readers) |

**Readers against their writer libraries.**

| Reader | Compared with | Agreed on |
|---|---|---|
| parquet | `pyarrow.parquet.read_metadata` | row count. The geo CRS read `EPSG:4326`. |
| geotiff, tiled | rasterio | width, height, bands, dtype, CRS, transform, nodata, overview count, tile size |
| geotiff, striped bigtiff | rasterio | width, CRS, transform. `tile` is `null`, as it should be. |
| flatgeobuf | `pyogrio.read_info` | feature count, geometry type, field names, CRS |
| geopackage | `pyogrio.read_info` | feature count. No side file was left behind. |
| shapefile `.dbf`, `.shx` | the 500 rows written | record count, field names |
| kml, kmz | the 20 points written | placemark and point counts. The two summaries were equal. |
| gpx | the 20 points written | waypoint count |
| npy, npz | numpy | shape, dtype, member names |
| xlsx | the two sheets written | sheet names and order |
| safetensors | the two tensors written | names, dtype, shape |

**Edits.** After a baseline, five fixtures were rewritten with their writer libraries: 25 more rows, a new column and a new CRS for the parquet file and the geopackage, a fourth band, a new CRS and a new nodata value for the geotiff, one variable more and one time step fewer for the zarr store, and one more dataset for the hdf5 file. One `trail.assets.observe('cell')` over the 29 assets took 18 ms and logged five events. Their lines are under [Messages](#messages) and at the top of this note.

**Truncation.** Each file reader was run on its fixture cut to 60 % of its length.

| Outcome | Readers |
|---|---|
| Raises | qgis in both containers, kml and gpx (`ParseError`). jupytergis and geojson (`JSONDecodeError`). parquet and feather (`ArrowInvalid`). geopackage (`DatabaseError`). netcdf and hdf5 (`OSError`). shapefile `.shp`, `.shx`, `.dbf` (`ValueError`). kmz, npz and xlsx (`BadZipFile`). |
| Returns a summary | geotiff, flatgeobuf, csv, npy, safetensors, shapefile `.prj` and `.cpg` |

The second row is the limit of a header-only reader. The header is whole, and nothing in it says how long the file should be. Such a file is still being written or is damaged. In the first case the stat check and the next notification lead to another read. In the second, the summary describes what the header claims.

The two fixtures above 16 MiB and the directory formats were not part of this test.

### Evidence and its limits

This section says where the plan's evidence comes from, so its weight can be judged.

**The two workflows produced nothing.** On 11-10 two multi-agent workflows were launched. They planned 24 agents: three to map the code, the notes and IPython, seven to write readers, seven to verify them, one to survey formats, three to build competing designs and three to judge them. Seventeen started. Each was stopped by the usage limit within 1 to 13 minutes, on each of three attempts, before it wrote any file. The seven verifiers never started. The three judges started once, with no design to judge. The temporary directory they worked in was emptied by a restart.

**What their transcripts still held.** Version numbers, excerpts of the IPython and ipykernel sources, a listing of the source types in the JupyterGIS example projects, and the structure of the QGIS fixture. This note uses the listing, under [Which formats](#which-formats). Everything else in the note was produced afterward by one author in one session.

**What that means.**

- Designs A and C were not built. The comparison rests on one prototype and three targeted tests.
- No reader was attacked by an independent verifier. Each fixture came from one writer library at one version.
- The qgis edits were text replacements in the XML, not saves from QGIS. No file was saved from JupyterGIS.
- Nothing was run on macOS or Windows, or on a kernel other than ipykernel 7.3.0.
- The changes to `session.py` were not made. The prototype injected the calls from outside.
- These were not tested: a geopackage in write-ahead-log mode, a zarr store with thousands of nodes, a missing optional package, the console's rendering of the event, a directory asset on the watcher path, and the whole extension in a kernel with the watcher active.
- Your note says a colleague pushed back that git is not designed for version control of data. The meeting notes in `compass/` were not searched for that discussion.

### Review

Two reviewers were started on the first draft of this note on 11-11. Neither had access to the conversation or to the prototype.

**The audit.** One reviewer read the draft against the code, the two earlier notes, the folding report, `PROJECT_PLAN.md` and the initiative. It checked every file link and found them correct. It found twelve design defects and several wrong claims. Each was checked, and the design defects were fixed in the prototype and run again before this version was written.

| Finding | What was done |
|---|---|
| With the watcher running, the cell hook only called `hurry`. A cell blocks the event loop, so nothing was queued yet and the hook did nothing. | The hook now stats every asset and adds the changed ones to `Reads`. Run: the read began 2 ms after the loop resumed, with basis `cell`. |
| Restoring the stat from the log on replay made the first check after a restart log a change that had been watched. | Replay restores nothing. Step 7 is no longer a recipe. |
| Reading `asset.metadata.dict` could write to the log. | `dict` observes only a file that was never observed, which logs nothing. Check m5. |
| An older background read could be accepted over a newer direct check. | `generation` orders reads, and `accept` refuses a late one. Check m1, and a run with a direct `observe` during a background read. |
| `Reads` kept starting reads after the session closed. | `Reads.close` cancels timers and reads and refuses new work. Run: none started after `close`. |
| A file tracked and written in one cell has no rows for that write. | Stated as a limit. The 11-9 decision accepts it. Open question 14 keeps the alternative. |
| The extension opens a second `Trail`, and held the lock until it was unloaded. | The lock is now held only during a check. The second `Trail` is open question 3. |
| A directory asset with no reader was checked against its root directory's stat, which does not change. | Such an asset has no `Stat` and is never stale. Check m4. |
| Step 7 contradicted itself. | Rewritten as what a later plan must handle. |
| The session changes break tests that use `StubLoop`. | Stated under `Reads`. |
| Startup queued every asset, ahead of changed ones. This went against the 11-9 decision. | `Reads.load` queues only assets with a reader, behind changed ones. Run. |
| A file that failed to parse was parsed again on every check. A caller could edit the stored summary. The basis was overwritten on a retry. | Fixed: `is_stale`, a copy from `dict`, and `bases`. Checks f1b and m2. |
| The class body of `Metadata` spelled `dict[...]` in three annotations, which the `dict` property shadows. | Type aliases. Check m3. |
| "Sixteen readers", "used once", "all read only headers", a link to a missing section, and Step 1 checks that needed the qgis class. | Corrected. Seven checks moved to Step 2. |
| No extraction code for kml, kmz, gpx, npy, npz, xlsx and safetensors, which are in the presets. | Five reader classes were added and run. safe and n5 remain described. |
| No TLDR section for the watcher path. Format names in capitals. Helpers used before they were introduced. | A TLDR section was added, format names are lowercase, and the helpers moved before their first use. |

Two of its findings were not acted on. It asked that `accept` log only in a process that holds the lock. A direct call to `observe` still logs, as other direct calls do. That is open question 12. It also asked for a reconciliation before the record is displayed. That is noted and not designed.

**The build.** A second reviewer built Steps 1 and 2 from the revised note alone, in a copy of the package, without the prototype. It could. 50 of the 51 checks then listed passed, and the one failure was a check whose wording could not be met. Its summaries for `buildings.qgs` and for the jupytergis example equalled the samples in this note exactly. Its findings, and what was done:

| Finding | What was done |
|---|---|
| A reader that raises `OSError` on a damaged file, as h5py and netCDF4 do, was treated as a vanished file. Nothing was recorded, and the file was parsed again on every check. | Any exception from a reader is now a parse failure. Checks n1 and n2. |
| The code used `dict(key=value)`, which the repository's lint rejects. | Dict literals throughout. Every Prototype block passes `ruff check` with the repository's configuration. |
| `sanitize` missed doubled quotes, double quotes, percent-encoding and Azure signatures. | Fixed, and run on those cases. |
| Check i1b could not pass as worded. Checks e1, h1 and m3 needed a mechanism the note did not give. | The table in Step 1 now says exactly what each does. |
| Unstated: `Any` in `event.py`, `MetadataEvent` in `asset.py`, the import line in `jupyter/__init__.py`, and where the event class goes. | Stated in Steps 1 and 2. |
| `typing.get_type_hints(Metadata)` still fails after the aliases, with `NameError` for `Asset`. | The claim is corrected. |
| Renaming the `metadata` locals collides with a loop variable in `Asset.fingerprint`. | The rename is optional and says so. |

**The existing tests.** The second reviewer ran the test suite against an unchanged copy of the package and against the copy with Steps 1 and 2 applied. The results were equal: adding the descriptor and the event class broke no test. On the working tree of 11-11 nine test modules could not be collected in either copy, because `src/trail/cli/console.py` imports `trail.cli.check` and that file is not there. This is unrelated to the plan. With a stub for that module, 345 tests passed in both copies.

Steps 3 to 7 were not built by a reviewer.

### Open questions

Each question has a recommendation. The first five are the ones the TLDR lists.

1. **The names.** `metadata` or `meta` for the attribute: `Entries.is_metadata` already uses the word for Trail's own files. `dict` or `summary` for the summary: `dict` needs the type aliases in one class body. **Recommended:** `asset.metadata.dict`, as you wrote it. If either wrinkle bothers you, `asset.metadata.summary` avoids the second, and renaming `is_metadata` to something like `is_internal` avoids the first.

2. **Stat-only events.** With no watcher, a check logs an event with no rows for a changed file that has no reader, and for a supported file whose summary did not change. **Recommended:** keep them. Without a watcher they are the only record that the file changed, and the initiative asks for exactly that. With a watcher they are not logged.

3. **Which `Trail` the extension uses.** The extension opens its own. A `Trail('.')` in a cell is a second one, which shares nothing in memory with the first and cannot take the lock. **Recommended:** one `Trail` per project in a process. The smallest way is for `Trail` to keep a weak set of its open instances, and for the hook to check each open instance of the project, opening one only when none exists. This needs your decision, since it touches `Trail.__init__`.

4. **One record or two per write.** The folding report plans one event per write session, with its start, its end and the final size and mtime. A `MetadataEvent` also has the final size and mtime. **Recommended:** when folding is built, let it reuse `MetadataEvent` and add `started`, so a save is one record with optional rows. Until then both are logged. This plan does not depend on folding.

5. **Changes made while Trail was not running.** See [Step 7](#step-7-changes-made-while-trail-was-not-running). **Recommended:** decide between the log and a cache file before designing it.

6. **The shapefile parts.** Five assets give five events per save. **Recommended:** leave it until the folding work decides whether the parts become one asset, as that report already raises.

7. **Linking an event to a cell.** The hook could add the execution count and a kernel id to the event. The 11-9 discussion said to attach a writer only when there is an explicit link. **Recommended:** record only `basis: cell` now.

8. **Formats defined in a notebook.** A class defined in a notebook exists only in that kernel. If the console is the recorder, it does not have the class and treats the file as stat-only. **Recommended:** decide later whether a project can name a module of its own formats that every Trail process imports.

9. **The default fields for qgis and jupytergis.** The readers here take a small set. The 10-29 tables list many more. **Recommended:** start small and add a field when someone misses it. Each added field can add noise.

10. **Untuned defaults.** These values were chosen without measurement beyond what this note reports: `Reads.quiet` 0.25 s, `Reads.limit` 2 s, `Reads.concurrency` 2, `Reads.retries` 3, `Metadata.max_changes` 50, `max_bytes` of 32 MiB for geojson, kml and gpx and 64 MiB for the project formats, the caps of 200 columns and 500 nodes.

11. **Write-ahead-log geopackages.** QGIS can turn the mode on. Recent writes then sit in the `-wal` file. **Recommended:** test it before the geopackage reader ships, and add the `-wal` file to `sources()` if the summary lags.

12. **A direct `observe()` without the lock.** It logs, as `trail.ontrail(path)` does. If another process is recording, both may log the same change. **Recommended:** leave it, and document that the console and a notebook should not both check by hand.

13. **Deleted and moved files, and a check before display.** `Assets.observe` skips a missing file. The initiative also names "before Trail presents or exports the record" as a point to reconcile. **Recommended:** do both with the status check of `PROJECT_PLAN.md`, phase 3.

14. **A baseline at tracking time.** Reading the summary inside `Assets.add` would give a file tracked and written in one cell its rows. It would also make tracking thousands of files slow. **Recommended:** keep the background read. Offer the immediate read only for files tracked one by one, if someone needs it.

15. **The `Event.classes` rename.** Your 10-29 comment decided it. This plan does not need it. **Recommended:** do it with Step 1.

### What this plan leaves out

- A baseline that survives a restart.
- A record of every save. Several saves between two reads give one event. A writer that reports its own saves, such as a QGIS plugin, would be needed for more.
- Deletions and moves in the check with no watcher.
- Folding filesystem events into one event per write.
- Who wrote the file.
- Comparison inside lists.
- Sources that are not local files.
- Export of the rows to another vocabulary.
