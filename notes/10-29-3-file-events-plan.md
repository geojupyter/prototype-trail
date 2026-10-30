# File-specific events: deferred proposal

## Decision: defer extension events

As of 2026-10-29, defer extension-based file events entirely. Ignore this proposal when implementing the current Trail and Jupyter integration. This includes `ExtEvent`, format-specific comparisons, in-memory baselines, persistent snapshots, and their supporting registration, serialization, scheduling, and recovery work. Continue with filesystem event recording and consumption of the Jupyter event feed. Revisit extension events only when a concrete need makes them worth reconsidering; no follow-up implementation is scheduled.

The design and skeletons below remain as a record of the discussion. Their implementation instructions describe the deferred proposal and are not active work.

> Are snapshots extending Trail into version control, contrary to the project scope and recent meeting priorities? Would storing an initial version and subsequent changes avoid duplicating relevant files?

> **Codex:** The scope review favors deferral. The October 15 meeting names consuming Matt's feed as the first priority, and October 20 describes cross-tool provenance starting with that feed. The architecture likewise favors consuming application events; the Jupyter plan leaves file comparisons to later work. See [October 15](/home/arstneio/PyCharmProjects/trail/compass/meeting-notes/20261015-collaboration-cafe/index.md:211), [October 20](/home/arstneio/PyCharmProjects/trail/compass/meeting-notes/20261020-jupytergis-sync/index.md:48), [architecture](/home/arstneio/PyCharmProjects/trail/explanation/architecture.md:18), and [the Jupyter plan](/home/arstneio/PyCharmProjects/trail/notes/10-28-2-jupyter-submodule-plan.md:2251).
>
> A comparison needs previous information, but not necessarily a full file: projection changes need the old projection; layer additions/removals need old layer IDs. The proposed storage keeps two sanitized representations rather than an unlimited file history, but comprehensive comparisons can still retain most project content. Persistent storage also introduces manifests, compatibility rules, pruning, and crash reconciliation. Those responsibilities were added before a demonstrated need. The [project constraints](/home/arstneio/PyCharmProjects/trail/PROJECT_PLAN.md:190) favor a smaller scope; keeping a disposable baseline would not itself violate the [Git-command constraint](/home/arstneio/PyCharmProjects/trail/PROJECT_PLAN.md:118).
>
> An initial version plus deltas requires complete, ordered, replayable changes. Our summarized and redacted events cannot reconstruct arbitrary prior content. Implementing that guarantee would add version-history responsibilities. Git itself models snapshots and can delta-compress their storage; see [Git's model](https://git-scm.com/book/en/v2/Getting-Started-What-is-Git%3F) and [packfiles](https://git-scm.com/book/en/v2/Git-Internals-Packfiles). If comparisons become useful later, a selected-field in-memory baseline would be a smaller experiment; persistence would need a separate justification.

> Defer ext-events altogether for now. They might come up later, but ignore them in the current work.

> **Codex:** Agreed. The decision above defers the whole extension-event proposal, including the smaller in-memory experiment. The implementation outline below is retained only for reference.

## Deferred design

The proposal would compare successive observed versions of tracked `.jgis`, `.qgs`, and `.qgz` files and record what changed: layers, sources, ordering, projection, and other project content. It would work without Jupyter running. Jupyter's feed would continue to describe activity in the editor; these additional events would describe changes actually observed on disk.

This develops [File-specific changes in the Jupyter plan](/home/arstneio/PyCharmProjects/trail/notes/10-28-2-jupyter-submodule-plan.md:2011) and the missing events and QGIS support discussed in [the meeting outline](/home/arstneio/PyCharmProjects/trail/notes/10-29-1-matt-meeting-outline.md:164). The proposed upstream feed changes are not prerequisites.

The implementation has four parts:

- `ExtEvent`, in `src/trail/extevent.py`, is the base for records describing file content changes. Its subclasses register themselves and supply the format reader and comparison methods.
- `JGISEvent` and `QGISEvent`, both initially in `src/trail/jupyter/extevent.py`, implement the formats supported by the Jupyter integration. The first reads JupyterGIS JSON; the second reads plain and compressed QGIS projects. Each record contains a list of changes. Open/close activity belongs to the separate Jupyter feed event classes; these extension events need no `Change` qualifier.

> Do we really need `Change` in the name? Are we ever going to have something else like `QGISOpenEvent` or `QGISCloseEvent`? Should we keep the name shorter for now?
- `Snapshot`, in `src/trail/snapshot.py`, holds one parsed, sanitized version and its observation metadata. `Snapshots`, in that module, stores the previous versions under `.trail/files/`.
- `Files`, in `src/trail/files.py`, is reached as `trail.files`. It schedules reads, compares snapshots, and appends events. The watchdog and Jupyter save handling notify it.

The base, snapshots, and coordinator stay in Trail's core. Each submodule owns its format-specific `ExtEvent` subclasses. Start with both GIS implementations in `trail.jupyter.extevent`; split them into `trail.jupyter.jgis` and `trail.jupyter.qgis` only when their size warrants it. Their location does not make them dependent on a running Jupyter server.

The guarantee is a comparison between versions Trail successfully observed. Several writes can occur before Trail reads the file; intermediate edits, their authors, and their exact times cannot be reconstructed from the final bytes.

> I think it should be organized differently. We should have an `ExtEvent`, under the base `src/trail/extevent.py` which implements the base class. Then, as our submodules grow, they implement their own `ExtEvent` subclasses. For jupyter, we'll have `src/trail/jupyter/extevent.py`, where they implement their own extension-based events. If the ext events implementations are big and need more organization, then we can divide into jupyter/jgis.py, jupyter/qgis.py, etc.

## Existing behavior and scope

The current [Event registry](/home/arstneio/PyCharmProjects/trail/src/trail/event.py:60) already registers subclasses by name in `Event.classes`. Rename that mapping to `Event.name2cls` as part of this plan. [Log replay](/home/arstneio/PyCharmProjects/trail/src/trail/event.py:111) uses that name to reconstruct each event. The filesystem currently produces `WatchdogEvent`; there is no `ExtEvent` or file-content cache yet.

> Let's rename `classes` to `name2cls` so we can use `ext2cls` for the ExtEvents.

The earlier plan's statement that comparing `.qgz` requires QGIS is too strong. `.qgs` is XML; `.qgz` contains a `.qgs` document and may contain auxiliary data. Python can read both directly. [QGIS format documentation](https://docs.qgis.org/3.44/en/docs/user_manual/appendices/qgis_file_formats.html#qgs-qgz-the-qgis-project-file-format).

Reading the original XML also preserves information that conversion to JupyterGIS would discard. The prototype's [QGIS importer](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_qgis/jupytergis_qgis/qgis_loader.py:524) returns selected project fields, and its [source mapping](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_qgis/jupytergis_qgis/qgis_loader.py:385) can generate source IDs. Trail should not use that importer as its comparison format.

This plan uses the proposed `WatchdogSession` and checklist names. Those refactors and the Jupyter integration remain separate work. The readers and snapshots can be implemented independently; the final integration uses whichever hooks have landed by then.

## Event registration

`ExtEvent` is a subclass of `Event`, not of `WatchdogEvent`. It describes a content comparison and does not move, create, or delete an asset. Its concrete subclasses participate in two mappings:

| Mapping | Key | Purpose |
|---|---|---|
| `Event.name2cls` | Serialized class name | Replay every kind of Trail event. |
| `ExtEvent.ext2cls` | Lowercase suffix, including the dot | Choose the reader for a tracked file. |

Keep one inherited `Event.name2cls` mapping for all events. Defining a separate `ExtEvent.name2cls` would divert registration away from the mapping that `Event.from_record` reads. Add `ExtEvent.ext2cls` with the same `ClassVar` and `__init_subclass__` pattern.

The `classes` → `name2cls` rename covers the declaration, `Event.__init_subclass__`, `Event.from_record`, and the registry lookup and reference documentation in `Events.types`, which supplies completion fields. Update corresponding tests and the planned class-name guard. The serialized `cls` field and existing concrete event names stay unchanged, so existing logs need no migration. `JGISEvent` and `QGISEvent` are new planned classes; shortening their names now changes no existing records. `Command.classes` is a separate command registry and stays unchanged.

The registration-only sketch below belongs in `src/trail/extevent.py`. Record fields and parsing methods are specified in the following sections.

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from trail.event import Event
```

```python
@dataclass(kw_only=True, slots=True, repr=False)
class ExtEvent(Event):
    suffixes: ClassVar[tuple[str, ...]] = ()
    ext2cls: ClassVar[dict[str, type[ExtEvent]]] = dict()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        suffixes = cls.__dict__.get("suffixes", ())
        for suffix in suffixes:
            if not suffix.startswith(".") or suffix != suffix.casefold():
                raise TypeError(f"Invalid file suffix: {suffix!r}")
            known = ExtEvent.ext2cls.get(suffix)
            if known is not None:
                if not (
                    known.__module__ == cls.__module__
                    and known.__qualname__ == cls.__qualname__
                ):
                    raise TypeError(f"{suffix!r} already belongs to {known.__name__}")
        super(ExtEvent, cls).__init_subclass__(**kwargs)
        for suffix in suffixes:
            ExtEvent.ext2cls[suffix] = cls
```

The two concrete classes explicitly declare their suffixes in `src/trail/jupyter/extevent.py`. That module imports `ExtEvent` from `trail.extevent`, plus `dataclass` and `ClassVar`. They use the same dataclass options as `ExtEvent`:

```python
@dataclass(kw_only=True, slots=True, repr=False)
class JGISEvent(ExtEvent):
    suffixes: ClassVar[tuple[str, ...]] = (".jgis",)


@dataclass(kw_only=True, slots=True, repr=False)
class QGISEvent(ExtEvent):
    suffixes: ClassVar[tuple[str, ...]] = (".qgs", ".qgz")
```

Selection is `ExtEvent.ext2cls.get(path.suffix.casefold())`, so `.jGIS` and `.QGZ` work too. Suffixes choose candidates; the reader still validates their contents. Registration does not track files or override checklist rules. Unknown suffixes retain ordinary watchdog behavior.

`slots=True` creates a replacement class and calls the registration hook again. Both mappings must end up pointing to that replacement. The existing plan's module-and-qualified-name exception handles this; a different class claiming the same class name or suffix raises. Validate all suffixes before changing either mapping. Use class attributes rather than required class-definition keyword arguments. [Python dataclass behavior](https://docs.python.org/3.12/library/dataclasses.html#dataclasses.dataclass).

Only suffixes declared in a subclass's own namespace register. A subclass that inherits `.suffixes` without declaring it must not silently take over its parent's formats. The same-definition exception follows the earlier plan; it is not a general plugin override mechanism.

Import `trail.jupyter.extevent` from `trail.py` before `events.jsonl.read()`, including when the Jupyter session or recording is disabled. Core `trail.extevent` imports only the base event and generic dependencies; it does not import its implementations. Keep `trail.jupyter.__init__` and the registration module lightweight: importing the event classes must not start discovery or subscriptions, or require Jupyter/QGIS runtimes. Load parser-only dependencies when parsing, so replay does not require them. If the implementations are split later, `trail.jupyter.extevent` re-exports them and remains the registration import. Third-party subclasses must likewise be imported before opening a log that uses them; discovery and entry points are outside this first implementation.

## What a file event contains

Each concrete `ExtEvent` records one nonempty comparison. Its `apply` method resolves the tracked asset, sets `entry`, and appends the event. On replay it returns the resolved entry without reading the project file or modifying it.

Each list entry is a `Change`, a fixed named-tuple record in `src/trail/extevent.py` that holds one difference. Alongside the inherited `id`, `entry`, and observation `timestamp`, the base adds:

| Field | Meaning |
|---|---|
| `src_path: str` | The asset's path when the new version was observed. |
| `version: int` | Version of this record's change representation, initially 1. |
| `before: str`, `after: str` | IDs of the compared snapshots. |
| `basis: str` | `watchdog`, `save`, or `startup`: what requested the comparison, not who wrote the file. |
| `changes: list[Change]` | The differences, with values already sanitized and bounded. |

Events contain enough information to display their changes after snapshots are pruned. They do not contain a Jupyter `subject`, claim an editor action, or acquire the feed's `saved` field. A content observation is not proof of an application's successful save or of durable storage.

### Typed changes

`Change`, in `src/trail/extevent.py`, is the fixed record returned by every format comparator and stored in `ExtEvent.changes`. A `NamedTuple` fits: fields are fixed, entries do not change after comparison, and each entry avoids a per-instance attribute dictionary. Nested payloads still dominate memory for large changes. Annotations support static checking; decoding must validate runtime input separately.

`JSONValue` describes permitted sanitized values. `Verb` restricts operation names, and `Basis` restricts the observation triggers used by the coordinator. `Missing` distinguishes an absent old/new value from JSON null (`None`). `path` is a tuple of property names or list indices, relative to the entity named by `key`; `layers` optionally names layers affected by a source update. The serialized schema is fixed too, but absent `old` and `new` fields are omitted.

```python
from enum import Enum
from typing import Any, Literal, NamedTuple, Self
```

```python
type JSONValue = (
    None | bool | int | float | str
    | list[JSONValue] | dict[str, JSONValue]
)
type Verb = Literal["added", "removed", "updated", "reordered"]
type Basis = Literal["watchdog", "save", "startup"]


class Missing(Enum):
    VALUE = "missing"


MISSING = Missing.VALUE


class Change(NamedTuple):
    part: str
    verb: Verb
    path: tuple[str | int, ...] = ()
    key: str | None = None
    old: JSONValue | Missing = MISSING
    new: JSONValue | Missing = MISSING
    layers: tuple[str, ...] = ()

    def to_record(self) -> dict[str, Any]:
        out = dict(
            part=self.part,
            verb=self.verb,
            path=list(self.path),
            key=self.key,
            layers=list(self.layers),
        )
        if self.old is not MISSING:
            out["old"] = self.old
        if self.new is not MISSING:
            out["new"] = self.new
        return out

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Self:
        ...
```

`Change.from_record` restores tuple fields, maps absent values to `MISSING`, and checks the verb and required sides: added needs `new`, removed needs `old`, and updated/reordered need both. Existing JSON null remains `None`. A planned source addition and opacity edit become:

```python
source = dict(
    type="GeoJSONSource",
    parameters=dict(path="data/parks.geojson"),
)
changes = [
    Change(part="sources", verb="added", key="S2", new=source),
    Change(
        part="layers",
        verb="updated",
        key="L1",
        path=("parameters", "opacity"),
        old=1,
        new=0.35,
    ),
]
```

`ExtEvent.to_record` explicitly encodes each entry with `Change.to_record`; passing named tuples directly to `json.dumps` would produce positional arrays. For replay, add a class-level `decode_record` hook to `Event`, defaulting to returning the record unchanged. `Event.from_record` calls it on the selected `event_cls` before construction. `ExtEvent.decode_record` restores `changes` with `Change.from_record`. Overriding only `ExtEvent.from_record` would not work: today's base method selects the class and calls its constructor directly. The JSON log keeps named fields, while the runtime uses typed entries.

> Are the changes entries going to be variable keys or always the same keys? Should we used a NamedTuple for better memory usage and type-hinting/checking for the entries to changes?

Start with `added`, `removed`, `updated`, and `reordered`. An entity addition or removal includes its bounded value once, rather than a row for every child field. Existing entities are compared recursively. Ordering of emitted rows is deterministic. Lists remain ordered; only documented maps or sets are normalized without order.

## Reader contract and snapshots

The event subclasses provide two class methods: `parse(content: bytes) -> dict[str, JSONValue]` creates the format's sanitized comparison representation, and `compare(old, new) -> list[Change]` describes differences between two such representations. `parse` takes the bytes already read by `Files`, so it cannot accidentally reopen a different version. Both methods are independent of `Trail`, the watcher, and Jupyter. Parser errors propagate to `Files` for fallback handling.

Each subclass also declares `snapshot_version: ClassVar[int] = 1`, the version of its comparison representation. This is separate from the event record's `version`. Its parsed representation preserves recognized fields and retains evidence of unrecognized content. Parsing must not run JupyterGIS migrations or load QGIS data providers: both could change IDs, depend on installed software, or discard project content.

### Why keep snapshots?

A watchdog notification says that a path changed; it does not contain the previous bytes. After a script changes opacity from 1 to 0.35, reading the file gives only 0.35. `Snapshot` preserves the earlier parsed value so the reader can report both sides. Neither the asset's size/mtime nor a digest can recover that value. Reconstructing it from events would also fail when the initial file was tracked without an earlier content event.

`Snapshot` is one captured value. `Snapshots` is its storage manager: it finds each asset's previous value, writes new values, and recovers the baseline after a restart. Keeping these responsibilities separate lets a worker produce a candidate snapshot without making it the accepted baseline. No copy of the original JSON, XML, or ZIP is retained; the stored value is the sanitized comparison representation.

The skeletons below specify state, method boundaries, and the main control flow. An ellipsis marks a method whose implementation is described in the accompanying text. They are planned code, not changes to the library.

### `Snapshot`: one captured version

`Snapshot`, in `src/trail/snapshot.py`, is a slotted dataclass passed from the reader worker to `Files`. `id` identifies this observation, `digest` identifies the raw bytes, and `data` holds the values needed for comparison. These are distinct: two observations can have different IDs and identical content. `handler` and `version` prevent comparisons between incompatible reader representations.

```python
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Self

from trail.extevent import ExtEvent, JSONValue
```

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Snapshot:
    read_gap: ClassVar[float] = 0.05

    id: str
    asset: str
    src_path: str
    handler: str
    version: int
    captured: datetime
    digest: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int
    data: dict[str, JSONValue]

    def compatible(self, other: Snapshot) -> bool:
        out = (
            self.asset == other.asset
            and self.handler == other.handler
            and self.version == other.version
        )
        return out

    @classmethod
    def capture(
        cls,
        asset: str,
        path: Path,
        handler: type[ExtEvent],
        previous: Snapshot | None,
    ) -> Self:
        ...

    def to_record(self) -> dict[str, Any]:
        ...

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Self:
        ...
```

`capture` opens the path once per read attempt, enforces the byte limit, reads it, and compares `fstat` before/after with the path's current stat. It checks device, inode, size, mtime, and ctime. It repeats the read after `read_gap` seconds and requires matching metadata and byte digests. The wait happens in the worker, not on the event loop. An unstable read raises a retryable error; it never returns a partly parsed snapshot. A save notification can bypass debounce but not these checks. The SHA-256 digest is computed from the bytes read. If it matches a compatible `previous`, reuse its sanitized `data`; otherwise call `handler.parse`. The returned snapshot gets a new UUID, the handler's `__name__`, its `snapshot_version`, and the UTC capture time.

`to_record` encodes `captured` as ISO text; `from_record` validates the envelope and restores it. `frozen=True` protects the attributes, not nested dictionaries: readers and comparators must treat `data` as read-only. A comparator must never modify either snapshot. The raw bytes are discarded after parsing.

### `Snapshots`: accepted versions and storage

`Snapshots`, also in `src/trail/snapshot.py`, is reached as `trail.files.snapshots`. It owns the accepted baseline for each asset. Without it, every restart would discard the comparison history, and every reader would need its own persistence and recovery code. It uses Trail's existing `Node` parent pattern; `Node` follows its parent to the owning `Trail`.

The disk layout is:

```text
.trail/files/<asset-id>/
    <snapshot-id>.json
    <previous-snapshot-id>.json
    current.json
```

`current.json` contains `version` for the cache envelope, `current` and `previous` snapshot IDs, and `event`, the last committed extension-event ID or null. It contains no project content. Snapshot files are immutable. IDs are validated before becoming path components. Temporary files live in the same directory and are atomically replaced into place; `.trail/` remains excluded from watching.

The manager keeps `current` snapshots and their last `event_ids` in memory. A separate `stored` mapping supports an in-memory Trail and candidates awaiting acceptance. Disk-backed operation need not keep every stored snapshot resident. Additional imports for this class are:

```python
from collections.abc import Iterable
from functools import cached_property
from typing import TYPE_CHECKING

from trail.event import Event
from trail.node import Node

if TYPE_CHECKING:
    from trail.files import Files
```

```python
class Snapshots(Node):
    _parent: Files

    def __init__(self, parent: Files) -> None:
        super().__init__(parent)
        self.current: dict[str, Snapshot] = dict()
        self.event_ids: dict[str, str | None] = dict()
        self.stored: dict[str, Snapshot] = dict()

    @cached_property
    def path(self) -> Path | None:
        directory = self._trail.dir
        if directory is None:
            return None
        out = directory / "files"
        return out

    def get(self, asset: str) -> Snapshot | None:
        out = self.current.get(asset)
        return out

    def recover(self, events: Iterable[Event]) -> None:
        ...

    def write(self, snapshot: Snapshot) -> None:
        ...

    def advance(
        self,
        snapshot: Snapshot,
        event_id: str | None,
    ) -> None:
        ...

    def forget(self, asset: str) -> None:
        ...

    def clear(self) -> None:
        ...
```

| Method | Implementation responsibility |
|---|---|
| `recover(events)` | Load and validate `current.json` and its snapshot for each tracked asset, then reconcile later extension events in log order. Populate `current` and `event_ids` before sampling starts. Only a recorder holding the lock runs recovery; ordinary log replay does not touch the cache. |
| `write(snapshot)` | Serialize and persist the immutable candidate, or retain it in `stored` for an in-memory Trail. Do not change the accepted baseline. A failed write prevents event creation. |
| `advance(snapshot, event_id)` | Atomically replace `current.json`, setting `previous` to the former current ID, then update the in-memory mappings. Prune unreferenced versions only after that succeeds. With no disk directory, update the mappings directly. |
| `forget(asset)` | Remove that asset's mappings and cached files after `Files` invalidates its pending work. |
| `clear()` | Reset all baselines after the main event log is cleared; a selected view of the log does not call it. |

`write`, event append, and `advance` form one synchronous commit on the event-loop thread, following Trail's current synchronous JSONL writer. There is no `await` between them, so a lifecycle callback cannot interleave an offtrail or move with acceptance. Parsing and reading the potentially large project run in a worker; bounded cache serialization remains in this commit. If measured commit latency becomes a problem, preparation can move off-loop while keeping acceptance serialized.

On a persistence failure, stop comparisons for that asset until recovery succeeds. In particular, if the event append succeeded but pointer replacement failed, do not retry the comparison against the old baseline and append it again. Recovery advances from the recorded event's `after` snapshot. A cache-format change without a supported migration follows the baseline-reset behavior below.

The cache is keyed by asset ID, not filename. A move carries its baseline with it. A copy gets the new asset's own baseline. An extension change keeps the comparison only when it still selects the same handler, such as `.qgs` to `.qgz`; a different handler establishes a fresh baseline.

### First observation, restart, and failed reads

The first valid read establishes a baseline without pretending that every existing layer was just added. A new asset still gets its ordinary creation/tracking events. Restart compares the last valid stored version with the current file and emits at most one `basis="startup"` event for the net difference. Its time is the observation time; the cache retains the previous observation time.

Invalid JSON/XML, an incomplete ZIP, a vanished file, permissions, and configured size limits leave the last valid baseline intact. Reads retry after further changes or a bounded retry delay. The coordinator exposes a concise notice, and ordinary filesystem records remain available. The next valid read compares against the last valid snapshot, even if an invalid version occurred between them.

A missing or corrupt cache, or an incompatible comparison-format version, establishes a new baseline with a notice that comparison continuity was lost. Do not compare representations produced by incompatible readers. Replaying historical events still works without the files or cache.

### Persistence and payload size

Write the new immutable snapshot first, append the event second, and replace the baseline pointer last. The pointer includes the last committed file-event ID. On restart, reconcile a lagging pointer with later file events and their snapshots before comparing the live file. An unreferenced snapshot from a crash is harmless; an event whose snapshot was lost causes a baseline reset, not a duplicate reconstructed event. Test failures between these writes explicitly.

When the bytes change but the comparison is empty, update the baseline without appending an event. The last committed event ID remains unchanged. Hash bytes on each requested read; matching size and modification time alone do not prove that content is unchanged.

After recovery, retain the current and previous snapshots and any snapshot needed by an unfinished write; prune older unreferenced snapshots. Clearing the main event log cancels pending comparisons and resets these baselines. Clearing an event selection does neither. Offtrailing an asset cancels its work and removes its cached content.

Sanitize before writing snapshots, events, notices, or reprs. Cover JupyterGIS `authBearer`, credential-bearing URLs, and QGIS datasource connection strings. Unknown connection syntax is represented by a digest and size rather than stored verbatim. Embedded images and other large values become descriptors containing type, size, and digest. A changed digest for an omitted value produces a bounded update row with details omitted. Equal parsed content caused solely by formatting or ZIP metadata produces no semantic event.

Persist digests for omitted values, never their raw values. Limits on file size, ZIP members, expanded bytes, and change payload size apply before allocation; summaries of omitted details must say that they are summaries.

## Determining JupyterGIS changes

`JGISEvent` parses the saved JSON directly. The starting coverage comes from the prototype's [project schema](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/schema/project/jgis.json:1) and [serializer](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/jupytergis_core/jgis_ydoc.py:30), checked at commit `abecc972293f4b92148173c17984513570c85476`.

| Saved content | Detection and resulting change |
|---|---|
| `layers` | Compare stable layer IDs for additions/removals, then names, type, visibility, filters, and nested parameters for updates. A style edit stays a layer update with a precise property path. |
| `sources` | Compare source IDs, then type, path/URL, inline data, and other parameters. A changed source can affect several layers; include their IDs as context without fabricating separate layer edits. |
| `layerTree` | Compare ordered layer references and nested groups. Report `reordered` only when the same occurrences remain under the same parent and only their order changes. Additions, removals, grouping, or ambiguous identity produce a structural `updated` row. |
| `options.projection` | Report the old and new project projection. This is a settings change, not a claim that data was reprojected. |
| Other `options` | Report changed extent, center, zoom, bearing, pitch, and extent mode as view-setting updates. |
| `viewState` | Report persisted per-view state separately, including newly appearing view IDs. Do not infer a person from those IDs. |
| `annotations` | Compare annotation IDs, then position, label, contents, parent, and other fields. |
| `stories` | Compare story settings and `storySegments` order. Story-segment content lives in `StorySegmentLayer` entries and is handled under `layers`. |
| `presets`, `metadata` | Compare keyed values recursively. |
| `schemaVersion` and unfamiliar fields | Record format-version changes and retain generic changes for content the reader does not yet understand. |

The groups in `layerTree` have names and child lists but no schema-defined stable group ID. Do not match renamed groups by name alone or pretend that every list-index shift is a move. Use a bounded tree update when matching is ambiguous. Ordered lists with duplicate entries require occurrence-aware comparison.

Object-key order and JSON indentation have no meaning. Preserve missing-versus-null and value types; Python's `True == 1` must not erase a type change. Reject duplicate JSON keys and non-finite numbers rather than silently choosing a value. Validate known structures without discarding new fields; an unsupported shape falls back to a generic section difference.

An opening rewrite can contain both view changes and a format migration. Record the observed fields under those categories; proximity to `opened` does not make every change harmless. The old-format random style IDs remain visible unless a specifically tested normalization proves equivalence. Changes to a referenced dataset are not changes to the project file itself.

## Determining QGIS changes

`QGISEvent` converts XML into a comparison representation that keeps QGIS concepts. Both suffixes use the same extractor and comparator; the parser recognizes a ZIP container from the bytes. The uncompressed project is parsed directly; for a ZIP, locate its `.qgs` member without assuming it has the archive's basename. Require one unambiguous project member and reject duplicate member names.

The committed fixtures already exercise this distinction: `examples/geotiff.qgz` contains `tmps5syr5q_.qgs`, and the inspected archives also contain `OuSPpA_styles.db`. These were inspected from the local prototype's committed bytes, not its modified working examples.

| XML content | Detection and resulting change |
|---|---|
| `projectlayers/maplayer`, keyed by child `id` | Layer additions/removals and updates to name, type, visibility-related properties, opacity, filters, labels, and other properties. |
| Each layer's `provider` and `datasource` | A source update keyed by the owning layer ID. QGIS does not provide the JGIS source dictionary; do not generate source UUIDs. |
| `layer-tree-group`, `layer-tree-layer`, `custom-order`, `layerorder` | Group/tree changes, checked visibility, and explicit rendering order. Distinguish tree order from rendering order when custom ordering is enabled. |
| `projectCrs/spatialrefsys` and layer CRS | Project- or layer-projection updates. Preserve custom CRS definitions; an authority code alone is insufficient. |
| `ProjectViewSettings`, and `mapcanvas` where present | Saved view extent, rotation, and related settings, using the forms present in that QGIS version. |
| `renderer-v2`, `rasterrenderer`, labeling and style subtrees | Style updates. Extract common properties for readable rows; retain a bounded subtree change for unfamiliar renderers. |
| `Annotations`, `main-annotation-layer`, `Layouts`, `relations`, `projectMetadata` | Add/remove/update by a stable ID when one exists; otherwise compare the section structurally. |
| Other root attributes, elements, and layer properties | Generic project or layer-property updates, so unsupported QGIS features do not disappear from the comparison. |
| Non-project ZIP members | `attachments` added/removed/updated, using member name, uncompressed size, and digest. |

The XML paths above come from the local [QGIS fixture](/home/arstneio/PyCharmProjects/eventfeed/ui-tests/tests/gis-files/buildings.qgs:1) and extracted committed archives. They are the first tested forms, not a claim that every QGIS version serializes identically. Missing/duplicate layer IDs use generic section comparison instead of silently overwriting entries in an ID mapping.

Normalize XML attribute order, irrelevant indentation between elements, and the ordering of explicitly ID-keyed collections. Preserve text, meaningful whitespace, namespaces, unknown attributes, and ordered children. Ignore only documented serialization metadata such as root `saveDateTime` and the saving-user attributes for semantic comparison; preserve the QGIS format version. Normalize numeric/boolean values only for recognized fields.

Map mirrored XML properties to one logical field only when they agree. If, for example, the layer tree and layer definition disagree on a duplicated value, retain the discrepancy as a generic change. Do not normalize all datasource strings as filesystem paths: providers also use database connection strings, URLs, and compound URIs. Keep relative paths as written; resolve them against the project directory only for optional display context, without accessing the data.

Use `zipfile` and a hardened XML parser, proposed as `defusedxml.ElementTree`, with entity expansion and external resource access disabled. Accept the normal QGIS DOCTYPE without fetching it; the parser exposes separate controls for these cases. [Parser options](https://github.com/tiran/defusedxml#defusedxml). Read ZIP entries in memory without extracting paths or executing embedded project code. Validate member counts, expanded-size limits, and CRCs.

ZIP timestamps, compression, and member ordering are not project edits. Auxiliary databases and other members receive content-level attachment changes first, not invented row-level meanings. Renamed members remain additions/removals unless a later rule can identify them reliably. A `.qgs` file's external `.qgd` sidecar is outside that file's bytes: this first implementation does not claim to observe it unless it is independently tracked. Semantic SQLite comparisons and automatic sidecar association are later extensions.

## `Files`: scheduling and recording comparisons

`Files`, in `src/trail/files.py`, joins the format readers to `Snapshots`. It is necessary because neither an event record nor a snapshot should own timers, worker tasks, or a watcher subscription. Keeping that mutable state here also gives watchdog arrivals and Jupyter saves one queue: the same write cannot start two independent comparisons of the same asset.

`Trail.files` constructs it with the same cached-property pattern as `Trail.assets`. Construction is passive; the recorder starts and stops it alongside the watchdog. The dependency direction is `Trail` → `Files` → `Snapshots`; format handlers depend on `ExtEvent`, not on this coordinator.

```python
from functools import cached_property

from trail.files import Files
```

The property below is added to `Trail`:

```python
@cached_property
def files(self) -> Files:
    out = Files(self)
    return out
```

### Pending requests and worker results

Two small records in `src/trail/files.py` make the thread boundary explicit. `Request` freezes an asset ID, path, handler, and scheduling information; the worker never receives a live `Asset`. Its `generation` changes whenever a later request or lifecycle change makes an older result stale. `first` and `due` are monotonic times used to cap debounce delays. `Sample` returns the candidate snapshot and its typed changes. Neither record grants permission to append an event.

```python
import asyncio
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from trail.asset import Asset
from trail.extevent import Basis, Change, ExtEvent
from trail.node import Node
from trail.snapshot import Snapshot, Snapshots

if TYPE_CHECKING:
    from trail.trail import Trail
```

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Request:
    asset: str
    path: Path
    handler: type[ExtEvent]
    basis: Basis
    generation: int
    first: float
    due: float


class Sample(NamedTuple):
    snapshot: Snapshot
    changes: list[Change]
```

### Coordinator state and request handling

`Files.pending` keeps only the latest request per asset. `running` holds at most one task per asset, with a small global concurrency limit. `generations` lets an in-flight worker finish harmlessly after its asset moves or is offtrailed. These are instance mappings; `ExtEvent.ext2cls` remains the shared class registry.

```python
class Files(Node):
    _parent: Trail
    debounce = 0.25
    max_wait = 2.0
    concurrency = 2

    def __init__(self, parent: Trail) -> None:
        super().__init__(parent)
        self.snapshots = Snapshots(self)
        self.pending: dict[str, Request] = dict()
        self.running: dict[str, asyncio.Task[None]] = dict()
        self.generations: dict[str, int] = dict()
        self.woken = asyncio.Event()
        self.runner: asyncio.Task[None] | None = None
        self.is_active = False

    def request(
        self,
        asset: Asset,
        *,
        basis: Basis = "watchdog",
        immediate: bool = False,
    ) -> None:
        if not self.is_active or asset.is_directory:
            return
        handler = ExtEvent.ext2cls.get(asset.path.suffix.casefold())
        if handler is None:
            return
        now = time.monotonic()
        first = now
        previous = self.pending.get(asset.id)
        if previous is not None:
            first = previous.first
        generation = self.generations.get(asset.id, 0) + 1
        self.generations[asset.id] = generation
        due = min(now + self.debounce, first + self.max_wait)
        if immediate:
            due = now
        self.pending[asset.id] = Request(
            asset=asset.id,
            path=asset.path,
            handler=handler,
            basis=basis,
            generation=generation,
            first=first,
            due=due,
        )
        self.woken.set()

    async def start(self) -> None:
        ...

    async def run(self) -> None:
        ...

    async def stop(self) -> None:
        ...

    def invalidate(self, asset: str) -> None:
        self.generations[asset] = self.generations.get(asset, 0) + 1
        self.pending.pop(asset, None)
        self.woken.set()
```

`start` requires recorder ownership, invalidates generations from any previous run, recovers the snapshots after log replay, creates fresh loop-bound scheduling state, and requests startup comparisons. Arrivals retained while startup is recovering are then drained. `run` waits on `woken` or the earliest deadline, removes ready requests from `pending`, and launches `_sample` tasks up to `concurrency`. It leaves requests for an already-running asset pending. Task completion removes the running entry and wakes the runner; exceptions are retrieved and handled per asset. This avoids one sleeping task per notification and prevents errors from killing the whole coordinator.

`stop` accepts the final watchdog/save notifications during the recorder's drain phase, finishes eligible work within the shutdown budget, then marks the coordinator inactive and invalidates unfinished results. Cancelling `asyncio.to_thread` does not stop its thread, so generation/active checks remain necessary. `invalidate` is called before a move, offtrail, or reset takes effect; it does not delete a baseline. After a move, request a comparison at the new path; after an offtrail, call `snapshots.forget`. A whole-log clear invalidates every job before `snapshots.clear`.

The most recent coalesced request supplies `basis`; it describes the trigger, not provenance. A maximum delay starts a read attempt even during a stream of changes. It does not override the stable-read checks or allow an obsolete result to commit.

### Reading, comparison, and acceptance

The following methods belong to `Files`. `_read` runs in a worker and returns `Sample`; `_sample` runs on the event loop and decides whether that result still applies. `_is_current` checks both the asset identity and its generation before the result is accepted. The coordinator passes the selected `ExtEvent` subclass to `Snapshot.capture`; it never branches on JGIS or QGIS itself.

```python
@staticmethod
def _read(
    request: Request,
    previous: Snapshot | None,
) -> Sample:
    snapshot = Snapshot.capture(
        request.asset,
        request.path,
        request.handler,
        previous,
    )
    changes: list[Change] = []
    if (
        previous is not None
        and previous.compatible(snapshot)
        and previous.digest != snapshot.digest
    ):
        changes = request.handler.compare(previous.data, snapshot.data)
    out = Sample(snapshot=snapshot, changes=changes)
    return out

def _is_current(self, request: Request) -> bool:
    asset = self._trail.assets.id2entry.get(request.asset)
    out = (
        self.is_active
        and asset is not None
        and not asset.is_directory
        and asset.path == request.path
        and self.generations.get(request.asset) == request.generation
        and ExtEvent.ext2cls.get(request.path.suffix.casefold()) is request.handler
    )
    return out

async def _sample(self, request: Request) -> None:
    previous = self.snapshots.get(request.asset)
    sample = await asyncio.to_thread(self._read, request, previous)
    if not self._is_current(request):
        return
    snapshot = sample.snapshot
    if (
        previous is not None
        and previous.compatible(snapshot)
        and previous.digest == snapshot.digest
    ):
        return
    self.snapshots.write(snapshot)
    event_id = self.snapshots.event_ids.get(request.asset)
    if sample.changes:
        assert previous is not None
        event = request.handler(
            src_path=snapshot.src_path,
            timestamp=snapshot.captured,
            before=previous.id,
            after=snapshot.id,
            basis=request.basis,
            changes=sample.changes,
        )
        event.apply(self._trail)
        event_id = event.id
    self.snapshots.advance(snapshot, event_id)
```

This shows the three outcomes explicitly: no prior compatible snapshot establishes a baseline; identical bytes do nothing; changed bytes are compared and only a nonempty comparison creates an event. Changed bytes with equal parsed content still advance the baseline. After successfully accepting a replacement for an incompatible old snapshot, `_sample` also emits a continuity notice; that notice branch is omitted here. Exceptions propagate to `run`'s per-asset completion handling.

`_sample` relies on the current-asset check and a synchronous commit: `ExtEvent.apply` must append to that still-tracked asset or raise. It must not silently return without recording and let `advance` run. A file can change again after capture; this event still describes the captured bytes, and the next notification schedules another read. No claim is made that capture and the writer's save are one transaction.

Read/parse failures retain the old baseline and schedule bounded retries. Persistence failures block acceptance for that asset until recovery, as specified for `Snapshots`; they do not enter the ordinary read-retry path. Workers never append events, change asset paths, or update accepted baselines.

Only the active recorder may sample files or write baselines. Reuse the project lock from the Jupyter plan before enabling this lifecycle; passive Trail instances only replay the log. An explicit refresh follows the same ownership rule.

The sequence is:

1. At recorder startup, load existing baselines, establish missing ones, and compare tracked supported files. Place watches before sampling and retain arrivals during startup so changes during initialization schedule another read.
2. On a tracked file's `modified`, `created`, or replacement/move arrival, mark it for sampling. A newly tracked file becomes eligible after its `AddAssetEvent` has been applied. A plain `opened` never schedules a comparison.
3. Debounce by asset, initially 250 ms. A write-close signal, where available, or a Jupyter contents-save message can request a sample sooner; neither substitutes for a successful read. A plain browser layer message does not request one.
4. Read bytes from one descriptor and check metadata before/after the read and against the current path, including device/inode, size, and nanosecond modification time. Resample after instability or replacement. Require an unchanged repeat sample before accepting the candidate, even when a save notification requested it. This reduces partial-read errors but cannot turn arbitrary writes into transactions.
5. Compare the byte digest, then the parsed representation when needed. An identical version emits nothing. A valid nonempty difference becomes the format's event, and its baseline advances using the persistence order above.
6. Serialize comparisons per asset. Each queued job carries the asset's current generation; a move, offtrail, reset, or newer read invalidates stale results. Changes arriving during a read remain pending for another sample.

Use a maximum settling interval, initially 2 s, to attempt reads during continuous changes; retries and work queues stay bounded. Do not record an invalid or changing candidate merely because the interval elapsed. On graceful shutdown, drain completed work and attempt pending reads within the shutdown budget; preserve unresolved baselines for the next startup.

Ordinary creation, move, and deletion events keep their existing behavior and ordering. A confirmed move carries the baseline and invalidates work using the old path. A transient delete during atomic replacement does not discard it. A permanently absent tracked file retains its last valid baseline until offtrailed; no synthetic content removal is invented. Where an intermediate version is no longer readable before a move or deletion, retain the structural event and report no fabricated diff.

Trail's own reads may produce filesystem `opened` notifications. They must not trigger recursive sampling. Do not suppress other programs' opens using a timing heuristic.

## Integration with Jupyter and the console

The earlier plan's `Writes`, reached as `trail.jupyter.session.writes`, holds filesystem events and folds recognized Jupyter-save counts into feed records. Its boolean observer chain stops at the first observer that claims an event. Registering `Files` after it would miss those writes.

Add a non-claiming notification to `Files` at watchdog ingress, before that observer chain. It only schedules content reads; it neither consumes nor reapplies the original event. After structural processing and automatic tracking, notify it of the resolved asset/path change as needed. `Writes.saved` also requests a sample for the resolved asset, including when no useful watchdog arrival was received. Repeated requests coalesce through the same per-asset state and digest checks.

Keep file comparisons and feed records separate in this first version. They share the asset ID. `Writes` continues to own folded counts, and the original raw events remain wherever the existing plan does not fold them. `ExtEvent` neither duplicates those counts nor deletes a comparison because a layer message looks similar. The feed can describe unsaved activity; a disk comparison can include unrelated script changes and several editor operations.

A save notification is a read trigger, not proof that the bytes read later belong exclusively to that save. The planned `last_modified` and `size` fields would improve correlation but still do not establish authorship. Exact cross-record linkage should wait for enough evidence; timestamps alone do not justify it.

The console shows the concrete event type, file, observation basis, and concise change rows, with a bounded summary for large changes. Stored records retain the full bounded structured payload. Existing class filters select `JGISEvent` or `QGISEvent`. Since `changes` is a list, the existing dotted-field lookup does not automatically provide an “any change matches” query; adding that query language is separate work.

## Deferred implementation outline

All steps below are deferred. The unchecked boxes record proposed work, not an active backlog or an instruction to create a branch. If this proposal is explicitly revived, reassess its scope before following the outline. The original implementation workflow was one step at a time on `codex/file-events`, branched from the then-current base, with passing tests and each step left unstaged and uncommitted for review. No branch, push, or PR is requested by this note.

- [ ] **1. Register and replay extension-event classes.** Add `ExtEvent` in `src/trail/extevent.py` and the concrete classes in `src/trail/jupyter/extevent.py`. Rename `Event.classes` to `Event.name2cls` throughout its consumers, add `ExtEvent.ext2cls`, arrange lightweight built-in imports, and add the class-name guard if it has not already landed. Add `Change` and its explicit record codecs, including the `Event.decode_record` dispatch hook. Verify missing-versus-null round trips and object-shaped JSON entries, then both registries retain the final slotted class; case-insensitive selection; suffix and name collisions; inherited suffixes; and replay in a fresh process with no project files. Keep reading/recording inactive until later steps.
- [ ] **2. Implement JGIS parsing and comparison.** Add fixtures for every table row, nested groups, duplicate list entries, missing versus null, type changes, unknown fields, migration changes, credentials, and embedded images. Check that formatting produces no event and one version changing several parts produces one record with several rows. Include unsupported versions/shapes and invalid JSON.
- [ ] **3. Implement QGS/QGZ parsing and comparison.** Use small committed fixtures plus representative copies from the prototype. Verify equal XML yields equal project changes through either container; archive metadata-only rewrites yield none; attachment-only updates remain visible; arbitrary inner filenames work; unknown XML is retained; duplicate IDs and malformed archives fail safely. Test real QGIS DOCTYPEs, custom CRS, provider URIs, old/new view forms, and resource limits.
- [ ] **4. Add snapshots and the comparison coordinator.** Add `Snapshot` and `Snapshots` in `src/trail/snapshot.py`, and `Files` in `src/trail/files.py`, reached as `trail.files`. Implement explicit refresh, sanitization, bounded workers, cache versioning, and crash recovery. Reuse the recorder lock, or land that prerequisite first. Test first baseline, restart differences, missing/corrupt caches, each persistence failure point, passive instances, parse recovery, clear/offtrail, and stale worker results. A saved event must remain readable without its snapshots. Inspect cache and log outputs for omitted secrets and bounded payloads.
- [ ] **5. Connect watchdog and Jupyter saves.** Add startup/stop lifecycle, non-claiming notifications, structural transitions, and save-triggered sampling. Test partial writes, repeated notifications, same-size writes, atomic replacement, rename/delete during reads, startup races, and two files changing concurrently. Verify a claimed Jupyter write still gets compared, an unrelated external write stays visible, and own reads do not loop. Preserve existing folding/count tests.
- [ ] **6. Finish console and end-to-end coverage.** Add rendering, concise notices, and the geospatial preset suffixes if the Jupyter plan has not already added them. Test existing projects without silently rewriting their checklists. Exercise JGIS edits with the feed disconnected and with feed-only unsaved changes; exercise QGS/QGZ edits from QGIS and JupyterGIS when those runtimes are available. Keep mandatory reader tests independent of either application. Run the full suite and record the actual result in this note.

The first real-application runs should specifically check opening-without-editing, Save As, style changes, layer order, source replacement, custom CRS, layouts, and auxiliary archive members. They determine which serialization changes can be safely normalized. Until verified, unfamiliar changes remain visible as generic updates.

## Checks performed for this plan

Read both input notes, the current registry/replay/watchdog code, and the local event-feed prototype at `abecc972293f4b92148173c17984513570c85476`. Inspected committed `.qgs` XML and two `.qgz` archives with Python without loading QGIS. The prototype's working examples contain local modifications, so archive observations above use `git show HEAD:<path>` bytes. These checks establish file structure and integration points; the implementation steps and application tests remain pending.

Before the naming revision, executed the original registration sketch against the current `Event` class in a separate Python process. Both mappings retained the final slotted classes; mixed-case selection, inherited suffix handling, and rejection of a conflicting suffix all passed. Checked local link targets and Markdown code fences. No implementation or application test result is claimed by those checks.

For the skeleton expansion, compiled all 16 Python blocks and executed focused checks of `Change.to_record` (absent values versus JSON null) and `Snapshot.compatible` (asset and reader version). Those checks passed. The methods marked with ellipses, scheduling lifecycle, and persistence remain planned work; the library test suite was not run for this note-only change.
