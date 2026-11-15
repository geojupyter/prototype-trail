# Plan: `trail.jupyter`, recording the JupyterGIS event feed

This plan adds `src/trail/jupyter/`. Trail will listen to the event feed of each Jupyter server that
serves the project. It will record each JupyterGIS GUI action as an event in `.trail/events.jsonl`
and print it live in the console. The console also becomes a debugger for the feed. It shows which
servers Trail follows, what each one sent, and why a message was skipped.

The plan builds on notes/10-20-1, 10-20-2 and 10-20-3, on the 2026-10-15 café
(compass/meeting-notes/20261015-collaboration-cafe/index.md), and on the "Evolving a schema" section
of events.md. It also covers the appendix of notes/10-20-2: the 13 commits up to abecc972, which
your ../eventfeed checkout now has. It was checked on 2026-10-21 with experiments, two review
rounds and prototypes:

- Jupyter Server, its events API and jupyter-collaboration were run, with jupyter_server 2.21.1,
  jupyter_events 0.12.1 and jupyter-collaboration 4.4.2. The last runs used the three schemas of
  abecc972.
- The core changes, the event classes and the fold were run against a copy of Trail. With them in
  place, the 211 tests pass. One existing test,
  `test_a_session_left_running_logs_its_accesses_when_replaced`, failed once in three runs before
  any change.
- `subscribe` and the probe were run against a real Jupyter Server.
- No browser was run. All browser behaviour comes from reading plugin.ts and JupyterGIS's code.

## New facts that shape the design

Measured:

- jupyter-collaboration writes a map 1.0 s after its last change, as a trailing debounce. The feed
  announces each write. A `contents_service/v1` message `{"action": "save", "path": "map.jGIS"}`
  arrives about 3 ms after the write, then a `jupyter_collaboration/session/v1` save.
- One autosave makes Trail log three `opened` and one or two `modified` file events on the map. The
  last `opened`, a read to hash the file, comes after the save message.
- When a .jGIS changes on disk from outside Jupyter (git checkout, a script), the server reloads the
  room. It first sends a `jupyter_collaboration/session/v1` message with `"action": "overwrite"`.
  A pycrdt client then saw every layer reported as changed. For the browser this is inferred: every
  open view would report every layer as `updated`.
- `*.jgis` matches Jupyter's `.~map.jGIS` backup, because a gitignore `*` matches a leading dot. The
  same holds for `*.csv` and `.~a.csv`. During a slow save, Trail really tracks the backup.
- An event with no entry crashes Trail in four places: `Event.to_record`, `Event.from_record`,
  `JSONL.read` and `JSONL.record2entry`.
- `Select` rejects dotted fields. `Query` resolves every field that ends in `path` against the
  project root.
- A POST with a wrong version (`"version": "0"`) tells whether a server has a schema. It needs no
  subscription and can never reach a listener. The reply is either "was not found in the schema
  registry" or "Unregistered version: '0'≠'1'". Each probe leaves a 400 warning in the server's
  terminal.
- Jupyter Server sends no close frame when it stops (code 1006). It removes `jpserver-<pid>.json`
  about 45 ms before the socket drops. All 13 jpserver files in ~/.local/share/jupyter/runtime are
  stale.
- The console echoes every submitted line into the feed and keeps it in the command history.
- The repo's pre-commit hook `check-builtin-literals` rejects an empty `dict()`.

Read from code:

- Opening a map, panning, zooming, reordering layers and editing a source all save the map, with no
  layer event.
- Jupyter Server sends no WebSocket pings.

New at abecc972, read from code (the appendix of notes/10-20-2):

- A third schema, `viewport/v1`, has the verb `moved`. Its object holds `document_path` and `new`:
  the center as `coordinates.x` and `coordinates.y`, `zoom`, and `extent`, all in the map's
  projection. The projection itself is not in the event.
- The plugin sends one viewport event once the view has been still for 10 s. Only the view that
  moved reports it.
- A pan still writes the map's options. Its autosave comes about 1 s after the movement, about 9 s
  before its viewport event.
- Every event reads the map's current path when it is sent. After a rename or "Save as", events
  carry the new path wherever the model follows it. events.md now says a path is not a stable
  document identifier.
- The layer handler still has no local-client filter, so layer duplicates and changes from Python
  still arrive as GUI events.

## How it fits together

A burst is the run of messages about one layer that ends with one write of the map. A drag of the
opacity slider is one burst of `updated` messages.

1. `Servers` reads the `jpserver-*.json` files every 2 s. It keeps the live servers and connects the
   approved ones.
2. Each `Server` runs one subscription task, which hands every text frame to `Pipeline.receive`.
3. `receive` stamps the frame on arrival. JupyterGIS messages and the server messages the pipeline
   uses are kept in `Messages`. The others are only counted. Each JupyterGIS message then passes
   the duplicate check, the map lookup and the reload check, and joins a burst. A viewport event is
   recorded at once.
4. A burst closes 0.25 s after the server announces a save of the map, or 2 s after its last
   message if no save comes. Its event is appended with one `trail.events.extend`.
5. While a burst is open, the map's `opened` and `modified` file events wait in it. If the server
   saved the map, they are folded into the event as counts by event type. Otherwise they are
   logged with it.
6. The console prints the appended events the way it prints file events.

For example, take one opacity drag on a map that is open in a tab and in a notebook. The tab sends
14 `updated` messages, and the notebook sends 14 copies, which are dropped as duplicates. The 14
messages merge into one burst. The autosave comes 1.0 s after the last step, and its four file
events wait in the burst. The save message marks the burst saved, and 0.25 s later it closes as one
`LayerV1Event` with `messages: 14`, `count: opened 3, modified 1` and
`change: parameters.opacity 1 → 0.35`.

> rename folded to conut
> >

## What else we need

Your three modules stay, with the names you gave them: `jupyter.py`, `consumer.py` and `event.py`.
`consumer.py` holds the subscription and the probe. Six more files are needed, five in the package
and the command in the CLI:

```
src/trail/jupyter/
    __init__.py   imports event and jupyter, so replay finds the event classes
    jupyter.py    Jupyter(Node), reachable as trail.jupyter: the servers found, approval,
                  connect, the callbacks
    session.py    JupyterSession, reachable as trail.jupyter.session: one run, with start,
                  stop, fold, flush and drop
    consumer.py   subscribe and probe, with tornado
    event.py      JupyterEvent, ContentsServiceV1Event, JupyterGISEvent, the document, layer and
                  viewport events, Envelope
    schema.py     PREFIX, SCHEMA_ID, the TypedDicts DocumentV1, LayerV1, ViewportV1 and their
                  parts, interned, leaves, selectable
    server.py     State, Counts, Server, Servers, JSON (one jpserver file), Secret
    pipeline.py   Pipeline: receive, duplicates, reloads, resolve, Burst, the closer, fold
    message.py    Message, Messages: the last 1000 messages and what became of them
src/trail/cli/jupyter.py   the `jupyter` command
```

The command lives in `src/trail/cli/`. `trail.py` imports `trail.jupyter` at module level, so if
`trail.jupyter` imported anything under `trail.cli`, startup would fail on a partly initialized
`trail.trail` (verified). For the same reason `trail.jupyter` imports `Trail` only under
`TYPE_CHECKING`.

Outside the package:

- `event.py`:
  - records with no entry;
  - a class-name guard in `Event.__init_subclass__`;
  - the fold and flush calls in `WatchdogEvent.apply`;
  - the `JupyterEvent` stub (lines 504-513) is removed;
  - the docstrings of `Events` and `Event.apply` stop saying every event is about an asset or a Dir.
- `select.py`: dotted fields. `cli/command.py`: the path rule in `Query`, and `Listing.title`.
- `trail.py`: `Trail.jupyter`, and `offtrail` flushes a map's bursts. `node.py`: `Node._jupyter`.
- `session.py`: the descriptor that makes `watchdog.session` moves into a base that
  `JupyterSession` shares.
- `asset.py`: `_is_markable` refuses names that start with `.~`.
- `cli/console.py`, `cli/render.py` and `cli/theme.py`: the live view. `cli/commands.py`: the help
  notes.
- `markers/geospatial.txt`: `*.jgis`, `*.qgz`, `*.qgs`.
- `pyproject.toml` and `uv.lock`, through `uv add`:
  - `tornado>=6.4.1`, `jupyter_core>=5.1,!=6.0.*` and `psutil>=5.7`. ipykernel already installs all
    three at these floors, but Trail imports them, so it declares them;
  - dev: `pyyaml`, for the drift tests.
- `.pre-commit-config.yaml`, if the empty `dict()` stays (decision 11).
- Tests:
  - copies of the three YAML schemas at abecc972 in `tests/jupyter/schemas/`;
  - a captured feed from step 5 as a fixture;
  - an autouse fixture that lowers `Pipeline.pause`, as `settled` does for `Session.settle`.

## Core changes

These are step 1. None of them is about Jupyter yet.

### Events with no entry

This follows your decision to record events about maps Trail does not track. In the log such an
event is `"entry": null`.

```python
# Event.to_record (event.py:158-160)
        out["cls"] = type(self).__name__
        # an event about no asset, such as one about a map Trail does not track
        if self.entry is None:
            out["entry"] = None
        else:
            out["entry"] = self.entry.id
        return out

# Event.from_record (event.py:123, 134-140): the id is taken before the event is built
        identifier = record.pop("entry")
        ...
        if identifier is None:
            return event
        entry_id = normalize_id(identifier)
        if resolved_entry is None:
            ...

# JSONL.read (event.py:549-557)
                record = json.loads(line)
                identifier = record["entry"]
                entry = None
                if identifier is not None:
                    entry_id = normalize_id(identifier)
                    entry = entries.get(entry_id)
                    if entry is None:
                        entry = trail._entry(entry_id)
                        if entry is None:
                            entry = self.record2entry(record, entry_id)
                        entries[entry_id] = entry
                event = Event.from_record(trail=trail, resolved_entry=entry, **record)
```

> I'm not a fan of our use of the private method `_entry`; its function is not immediately clear from the name and might be useful for the user. Let's reintroduce `trail.entries`  which behaves like a mapping; you use `trail.entries[id]` for a strict lookup or `trail.entries.get(id)` for a lookup that returns None if the id is not found. I believe it was previously implemented in this repo? It shouldn't be an instance of the Entries class, maybe EntriesMapping? just to provide the flexible access of both `trail.assets` and `trail.dirs`
>
>


`record2entry` needs `src_path` in the first record about an id. After `events clear`, a Jupyter
event can be that first record, so a Jupyter event with an entry always carries the map's absolute
`src_path`. An older Trail cannot replay a log that has `"entry": null`.

### Dotted fields in selections

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
        # `cls` is the record's class, the name its stored record gives it
        if field == 'cls':
            out = type(record)
            return out
        # a dotted field reads the record's attribute, then the keys of what that holds
        parts = field.split('.')
        out = getattr(record, parts[0], None)
        for key in parts[1:]:
            if isinstance(out, Mapping):
                out = out.get(key)
            else:
                out = getattr(out, key, None)
        # an empty string is how an event leaves a field unset, and its repr omits it likewise
        if (
                out is None
                or out == ''
        ):
            return None
        # a record held by reference, such as an event's asset, is compared by its id
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

>  ok, but include a docstring

With this, the following terms work:

- `events verb=updated object.layer_id=L1`;
- `object.new.visible=false` and `object.new.parameters.opacity<0.5`, using the existing bool and
  float coercion;
- `count.modified>3` on dirasset accesses;
- `entry.name=map.jGIS`, and `entry.path=maps/map.jGIS`, which is still read against the project
  root.

Segments after the first may contain hyphens, since layer parameter keys look like
`circle-fill-color`. test_listing.py:147-148 expects `events is_s` and `events (cl` to offer one
completion each, so no new field may start with `is_s` or `cl`.

> Give an example of hyphens after the first segments

### The class-name guard

```python
# Event.__init_subclass__ (event.py:105-108)
    def __init_subclass__(cls, **kwargs) -> None:
        # slots=True replaces the class captured by zero-argument super()
        super(Event, cls).__init_subclass__(**kwargs)
        known = cls.classes.get(cls.__name__)
        if known is not None:
            is_same = (
                known.__module__ == cls.__module__
                and known.__qualname__ == cls.__qualname__
            )
            # slots=True builds a second class from the same definition. Any other class with a
            # registered name would silently replace it in replay
            if not is_same:
                raise TypeError(f"{cls.__name__} is already an event class in {known.__module__}")
        cls.classes[cls.__name__] = cls
```
> Good, but no need to assign is_same here, just use `if not ():...`

The log stores only class names, so a name, once logged, must stay registered for good.

### Jupyter's backups

```python
# Assets._is_markable (asset.py:478-485)
        if (
            path in self.path2entry
            or path in trail._offtrailed_paths
            or trail._is_ignored(path)
            # Jupyter's copy of a file it is saving, gone a few milliseconds later
            or path.name.startswith(".~")
            or not trail.dirs.tracks(path)
            or self.is_child(path)
        ):
            return False
```

This one gate covers the initial scan, `mark`, and the session's scans of created and moved files.
A `!.~*` line in `.markers` would not help, because a dir scope such as `data/*.csv` wins over a
root scope.

> Still seems like a code-smell to me, because we are bringing jupyter-specific functionality into the base-module's code. What else can we do? Do we know the specific extensions that jupyter does this for? E.g., can we somehow just do !.~*.jgis? But then that doesn't apply if we have  /data/*.csv? The best solution is to include substring exclusion in our jupyter-specific file markers, right? so we have markers which mix the wildcard, the jupyter extensions, and "filename doesn't start with .~" in the glob?

## The event model

### Typed payloads

Each message from a server's feed is the schema's payload with one key added. jupyter_events
validates the payload against the schema and calls it `data`, in `emit(schema_id=..., data=...)` and
in its listeners. jupyter_server forwards it to the feed as `dict(schema_id=schema_id, **data)`
(jupyter_server/services/events/handlers.py:69). The value of `schema_id` is the schema's `$id`.
`$id` itself never appears in an event, since it is a JSON Schema keyword of the schema file.

A Jupyter event keeps the two parts as two fields:

- `schema_id` is the `$id`, named as jupyter_server names it. For the three classes Trail knows, it
  repeats what the class says. The generic class, which records an `$id` Trail does not know, has
  nothing else that says which schema an event follows. Every event of a class shares one string
  object, so the field costs one pointer per event.
- `data` is the payload as the server sent it, nested, which is what the YAML schema describes. Each
  leaf class types `data` with a TypedDict for its `$id`:

```python
event: LayerV1Event
# str
event.data["object"]["layer_id"]
# str | None, since old is absent on added
event.data["object"].get("old", {}).get("name")
# error: no such key
event.data["object"]["layr_id"]
```

On 2026-10-27, `mypy --strict` and pyright gave these types for the nested TypedDicts below, and
both rejected the misspelled key. Typing `data` through the class's type parameter, as in
`LayerV1Event(LayerEvent[LayerV1])`, was checked the same day.

Each TypedDict is `total=False`, so every key may be absent and no key carries a marker. Trail does
not need to know which keys are required. jupyter_events validates `data` against the schema of its
`$id` before any listener gets it, and the feed's websocket is such a listener
(jupyter_events/logger.py, `emit`). Every message Trail receives already has the keys its schema
requires. Under `mypy --strict`, which scratch/9-30.md asks for, `total=False` works like this
(mypy 2.4.0, checked on 2026-10-27):

- `data["object"]["layer_id"]` is `str`, with no error. It raises KeyError if a key is absent, so
  code reads a part that an event may leave out, such as `old` on `added`, with `.get`.
- `data["object"].get("old", {}).get("name")` is `str | None`, so mypy makes code handle the
  missing key.
- A misspelled key is an error.

pyright also flags direct reads of keys that may be absent. The team plans to use mypy.

The keys of `data` are interned when an event is made. Interning makes equal keys one string
object, which every event then shares. `json.loads` makes new key strings for every message, so
without interning each event carries its own copy of each key. On 2026-10-27, 2000 layer `updated`
messages took 6,797 bytes per event as parsed and 5,380 with interned keys (CPython 3.12, shared
objects counted once). The event without its payload takes 385. Only keys are interned, never
values such as layer ids, paths or subjects, since interned strings stay for the life of the
process.

Two other ways to save memory do not work:

- Events are already slotted. `Event` is a `slots=True` dataclass with no base class, so an event
  has no `__dict__`. A slot per payload key, such as `object__layer_id`, is the dataclass field per
  key that "What a record stores" rejects.
- Keeping the payload in an instance `__dict__` would add one dict per event, which is the same
  dict under another name. It would also mix the payload's keys with the event's attributes, such
  as the `verb` key and the `verb` property.

Flat TypedDicts with dotted keys, such as `data["object.layer_id"]`, were the other way. They read
an optional deep value with one `.get`, and with interned keys a layer update takes 3,829 bytes,
about 1.5 KB less. In exchange they need code to flatten each payload and to nest it again for the
log, a drift test that no schema key holds a dot, and a special case in `Select`.

The TypedDict also works at runtime:

- `selectable(shape)` walks it into dotted terms, so `events object.la<TAB>` completes
  `object.layer_id=`. The term reads `Envelope.object`, then the keys below it, as any dotted field
  does.
- `get_type_hints(LayerV1)["verb"]` gives the Literal of the verbs, which the drift test reads.

layer_v1.yaml leaves the layer itself (`old`, `new`) untyped. Its shape comes from JupyterGIS's
jgis.json: `name`, `type` and `visible` are required, and `parameters` and `filters` are optional.
`parameters.source` names the source in 8 of the 10 layer types. The event schema promises none of
these keys. `parameters` and `filters` may be null, since layers written through the Python API
likely carry `filters: null`.

> Does `object.old.parameters` need to be a key when a layer has no parameters? The `dict | None` hints seem useless.

> **Claude:** The hints were useless, so `LayerV1` drops the four `parameters` and `filters` keys. `data` still keeps a null or empty `parameters` as the server sent it. `flatten` cannot tell that null from a value's null, such as `object.document_path: None`, which the schema requires. It costs one dict entry per event, or 64 bytes for an empty `{}`.

> Was flattening and type-hinting the events worth the effort? What do we gain: speed, memory, an easier UI, or mypy hints that nested dicts would not give?

> **Claude:** The type hints are worth keeping, but flattening gained little. It adds work on every message and every log write, and saves about 1.5 KB per layer update over interned nested keys. Selection terms are dotted either way, and nested TypedDicts type as well under `mypy --strict`. The plan now stores `data` nested, as received, with interned keys. It drops `flatten`, `nest`, the dotted-key drift test and the special case in `select.py`. `LayerState` declares `parameters` and `filters` again, since each now holds the layer's whole object.

```python
# src/trail/jupyter/schema.py
# copied, since Trail does not depend on JupyterGIS
PREFIX: Final = "https://schema.jupytergis.org/"
# events.md: an $id always reads https://schema.jupytergis.org/events/<object>/v<version>; the
# plan calls <object> the kind
SCHEMA_ID: Final = re.compile(
    r"https://schema\.jupytergis\.org/events/(?P<kind>[\w-]+)/v(?P<version>\d+)",
)


class DocumentObject(TypedDict, total=False):
    document_path: str | None


class DocumentV1(TypedDict, total=False):
    subject: str
    verb: Literal["opened", "closed"]
    object: DocumentObject


class LayerParameters(TypedDict, total=False):
    # the other keys depend on the layer type
    source: str


# a layer as jgis.json defines it; layer_v1.yaml leaves it untyped
class LayerState(TypedDict, total=False):
    name: str
    type: str
    visible: bool
    parameters: LayerParameters | None
    filters: dict[str, Any] | None


class LayerObject(TypedDict, total=False):
    document_path: str | None
    layer_id: str
    old: LayerState
    new: LayerState


class LayerV1(TypedDict, total=False):
    subject: str
    verb: Literal["added", "updated", "removed"]
    object: LayerObject


class Coordinates(TypedDict, total=False):
    x: float
    y: float


# the viewport after a move, in the map's projection, which the event does not name
class Viewport(TypedDict, total=False):
    coordinates: Coordinates
    zoom: float
    extent: list[float]


class ViewportObject(TypedDict, total=False):
    document_path: str | None
    new: Viewport


class ViewportV1(TypedDict, total=False):
    subject: str
    verb: Literal["moved"]
    object: ViewportObject


def interned(value: Any) -> Any:
    # the value with the keys of every dict in it interned, so all events share one string per key
    out: Any = value
    if isinstance(value, dict):
        out = {
            sys.intern(key): interned(item)
            for key, item in value.items()
        }
    elif isinstance(value, list):
        out = [
            interned(item)
            for item in value
        ]
    return out


def leaves(
        value: Mapping[str, Any],
        prefix: str = "",
) -> dict[str, Any]:
    # every leaf below a dict by dotted path, such as "parameters.opacity", to compare two layers
    out: dict[str, Any] = dict()
    for key, item in value.items():
        path = f"{prefix}{key}"
        if (
            isinstance(item, Mapping)
            and item
        ):
            nested = leaves(item, f"{path}.")
            out.update(nested)
        else:
            out[path] = item
    return out
```

Each `$id` is written once, in its class's `schema`, since the TypedDicts do not hold `schema_id`.
The drift test ties it to the YAML copy. `selectable(shape)` walks a TypedDict into dotted terms
such as `object.old.name`, and leaves out terms typed as dicts.

### The event classes

```
Event                               trail/event.py
└── JupyterEvent[D]                 a message from a server's feed: schema_id, data, server, src_path
    ├── ContentsServiceV1Event      Jupyter saved a tracked map, with no layer event
    └── JupyterGISEvent[D]          an $id under PREFIX; the registry; the generic fallback
        ├── DocumentEvent[D]
        │   └── DocumentV1Event     .../events/document/v1, binds DocumentV1
        ├── LayerEvent[D]           merges bursts; the layer name and the change summary
        │   └── LayerV1Event        .../events/layer/v1, binds LayerV1
        └── ViewportEvent[D]        the center and the zoom
            └── ViewportV1Event     .../events/viewport/v1, binds ViewportV1
```

> Do we really gain from having JupyterEvent and then FeedEvent? I believe we are only going to be collecting events from the feed, so JupyterEvent can be the sole base class?

On the names:

- `LayerV1Event` is a good name. There is one leaf class per `$id`, named after it. A leaf is four
  lines: the decorator, the class, `schema` and `shape`. `__init_subclass__` derives its
  `selectable` from `shape`.
- A leaf stays registered after a `LayerV2Event` arrives, since old records name it.
- `cls=LayerEvent` selects every version, because `Select` compares `cls` through the MRO.
- `JupyterEvent` is the base of every event from Jupyter, and each of them comes from a server's
  feed. It takes the name of the stub that event.py removes. No log on this machine contains that
  stub, so no record names the old class.
- The other design is a single `LayerEvent`, with the version only in `schema_id`. It never keeps an
  old class. But its typed view becomes `LayerV1 | LayerV2`, and a breaking v2 pushes version
  branches into the class.

`Envelope` is a mixin that `JupyterGISEvent` shares with `Message`. Both classes have the fields
`schema_id` and `data`. Its properties `subject`, `verb` and `object` read `data` with `.get`, and
`kind` reads `schema_id` with `SCHEMA_ID`. `object` comes last in the class body, since that name
shadows the builtin for whatever follows it. It declares `__slots__ = ()`. Without that, every
event and every message would get a `__dict__` again, since one class without `__slots__`
anywhere in the bases gives instances a `__dict__`.

```python
# src/trail/jupyter/event.py
@dataclass(kw_only=True, slots=True, repr=False)
class JupyterEvent[D: Mapping[str, Any]](Event):
    # whether a run of updated messages becomes one event
    is_merged: ClassVar[bool] = False

    entry: Asset | None = field(default=None, init=False, repr=False)
    # the $id of the schema the payload follows
    schema_id: str
    # the payload as the server sent it, with interned keys
    data: D = field(repr=False)
    # the server's url, never its token
    server: str = ""
    # the map's absolute local path, or "" for a map Trail could not place
    src_path: str = ""
    # the file events of the map's save folded into this event, by event type, as
    # DirectoryAccessEvent.count holds them and renders them
    count: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        Event.__post_init__(self)
        # the pipeline and a replay both pass the payload as json.loads made it
        shared = interned(self.data)
        self.data = cast(D, shared)

    def apply(
            self,
            trail: Trail,
            *,
            replay: bool = False,
    ) -> Asset | None:
        # an event from Jupyter changes nothing in the Trail, so a replay only returns its asset
        if not replay:
            trail.events.append(self)
        out = self.entry
        return out


@dataclass(kw_only=True, slots=True, repr=False)
class JupyterGISEvent[D: Mapping[str, Any]](Envelope, JupyterEvent[D]):
    # the class of each $id Trail knows. JupyterGISEvent itself records any other $id under PREFIX
    registry: ClassVar[dict[str, type[JupyterGISEvent[Any]]]] = dict()
    schema: ClassVar[str] = ""
    shape: ClassVar[Any] = None
    # whether every view of the map reports it, so a copy from another view is a duplicate
    is_shared: ClassVar[bool] = True
    selectable: ClassVar[tuple[str, ...]] = (
        "schema_id",
        "subject",
        "verb",
        "object.document_path",
    )

    # a merged burst: the arrival of its first message, and how many messages it took
    started: datetime | None = None
    messages: int = 1
    # when the server announced the save that wrote the change
    saved: datetime | None = None

    def __init_subclass__(cls, **kwargs: Any) -> None:
        # slots=True replaces the class captured by zero-argument super()
        super(JupyterGISEvent, cls).__init_subclass__(**kwargs)
        if not cls.schema:
            return
        JupyterGISEvent.registry[cls.schema] = cls
        keys = selectable(cls.shape)
        # slots=True runs this twice for one class, so the names are deduplicated
        names = dict.fromkeys([*cls.selectable, *keys])
        cls.selectable = tuple(names)

    def __post_init__(self) -> None:
        JupyterEvent.__post_init__(self)
        # a replay passes the datetimes as the log holds them
        if isinstance(self.started, str):
            self.started = datetime.fromisoformat(self.started)
        if isinstance(self.saved, str):
            self.saved = datetime.fromisoformat(self.saved)

    def summary(self) -> Iterator[tuple[str, object]]:
        # the generic class cannot read the object, so it shows it cut short
        yield "schema_id", self.schema_id
        yield "object", shown(self.object)


@dataclass(kw_only=True, slots=True, repr=False)
class LayerEvent[D: Mapping[str, Any]](JupyterGISEvent[D]):
    is_merged: ClassVar[bool] = True
    selectable: ClassVar[tuple[str, ...]] = (
        *JupyterGISEvent.selectable,
        "layer",
        "layer_id",
    )

    @property
    def layer(self) -> str | None:
        found = (
            self.object.get("new")
            or self.object.get("old")
            or dict()
        )
        out = found.get("name")
        return out

    @property
    def layer_id(self) -> str | None:
        out = self.object.get("layer_id")
        return out

    def changes(self) -> list[tuple[str, object, object]]:
        old = self.object.get("old") or dict()
        new = self.object.get("new") or dict()
        before = leaves(old)
        after = leaves(new)
        paths = dict.fromkeys([*before, *after])
        out: list[tuple[str, object, object]] = [
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
class LayerV1Event(LayerEvent[LayerV1]):
    schema: ClassVar[str] = "https://schema.jupytergis.org/events/layer/v1"
    shape: ClassVar[Any] = LayerV1
```

The rest of the module:

- `DocumentEvent` sets `is_shared = False`. Each view sends its own `opened` and `closed`, so they
  are never duplicates. Its `summary` is empty. `DocumentV1Event` binds `DocumentV1` like the layer
  leaf.
- `ViewportEvent` sets `is_shared = False`, since only the view that moved reports it. It is not
  merged, since the plugin already sends one event per pause. Its `summary` shows the center and
  the zoom. Its `timestamp` is its arrival, about 10 s after the movement ended. `ViewportV1Event`
  binds `ViewportV1`.
- `ContentsServiceV1Event` is named after jupyter_server's schema `contents_service/v1`. It is a
  `JupyterEvent[dict[str, Any]]`, with no TypedDict. It keeps the payload of the contents `save`,
  shows its `path`, and renders as `saved`.
- `rows()` yields the rows of the example below, in that order. `_repr_items` leaves out None and
  "", as `Event._repr_items` does. With no `src_path`, the raw path shows as `object.document_path`,
  which is also its selection field.
- `describe()` lists at most three of `changes()` as `path old → new`, then `+N more`. It gives
  `none` for a burst that ends where it began. `shown(value)` writes each value as json and cuts
  it at 24 characters.

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
    count: opened 3, modified 1
    subject: 3271840155
    server: http://localhost:8888/
    entry: 4e26a1b0c39d4f6e8b7a2c5d1e0f9a8b
```

### What a record stores

A record holds the message as received, split into `schema_id` and `data`. Trail's own fields sit
next to them. The two keys are the message jupyter_server sent, and the pair jupyter_events
validates. Pari can validate each `data` against the PR #4 schema that `schema_id` names, without
knowing Trail's class names. An additive schema change loses nothing for a single message.

A merged burst stores the `data` of its last message, with the first message's `object.old`.
Fields that differ between the messages of a burst are kept from the last message only,
except `old`. The merged payload still validates against layer/v1 (tested). Illustrative record:

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
 "count": {"opened": 3, "modified": 1}, "started": "2026-10-21T18:02:09.880000+00:00",
 "messages": 14, "saved": "2026-10-21T18:02:12.215000+00:00", "cls": "LayerV1Event",
 "entry": "4e26…"}
```

Two other ways to store events were rejected:

- A flat `data`, keyed by dotted path. "Typed payloads" says why.
- One dataclass field per schema key drops an unknown property. `from_record` would also raise
  TypeError on it.

`timestamp` is the arrival time of the last message, since a message carries no time. If
jupyter_server#1710 ever adds metadata to the message, the record gets a field for it.

> I think we agreed on `count` instead of `folded`?  Not sure what `nest` is, don't see it in the block?

> **Claude:** Yes, your comment under "How it fits together" asked for it. `folded` is now `count`, a dict by event type such as `count: opened 3, modified 1`, as `DirectoryAccessEvent.count` already holds the file events it takes in. `nest` rebuilt the nested payload from flat `data` for the log. It is gone, since we are doing away with the flat TypedDict and `data` stays as the server sent it.

### Evolving a schema

From events.md:

- a breaking change gets a new file and a new `$id`;
- both versions can be published side by side;
- additive changes stay in the same `$id`.

For Trail that means:

- Dispatch is on the full `$id`, through `JupyterGISEvent.registry`. A breaking `layer/v2` adds
  `LayerV2` and `LayerV2Event(LayerEvent[LayerV2])`, and nothing else changes.
- An `$id` Trail does not know, such as a future `source/v1`, is recorded by `JupyterGISEvent`. That
  class keeps `data` whole, shows `schema_id` and the object cut short, and never merges.
- `viewport/v1`, added on 2026-10-21, took this path: a new file and a new `$id` upstream. Trail
  adds `ViewportV1` and `ViewportV1Event`. A Trail without them records viewport events with the
  generic class.
- An additive property stays in `data`. It can be selected as `object.<key>`, and `jupyter show`
  prints it.
- An additive verb is recorded but not merged, and it is rendered with the default style.
- Side-by-side versions are not built yet, since no v2 schema exists. When one does, a copy of an
  action in a second version is dropped like a duplicate. Both copies have the same server,
  subject, verb, map and layer, and they arrive within the duplicate window.
  - The copy of a class Trail knows wins over the generic one, then the higher version wins.
  - A version that arrives without a copy is recorded.
  - Since a burst holds its event at least 1.3 s, the better copy can replace the earlier one
    inside its burst.
- Three drift tests:
  - The copies in `tests/jupyter/schemas/` are checked against each TypedDict: the keys, the
    types and the verbs. The layer's own keys are checked against `definitions.jGISLayer` in
    jgis.json.
  - When `jupytergis_core` imports, the copies are compared with its `EVENT_SCHEMAS`. Otherwise the
    test skips. This .venv has it only from the 10-20 experiments, and `uv sync` removes it.
  - Every upstream `$id` has a class, mirroring eventfeed's own every-schema-has-a-case test.

> For evolving a schema, are nested TypedDicts still worthwhile? Do they really help mypy at this project's scope?

> **Claude:** No. Trail's own code reads the payload through `Envelope.object`, which returns `dict[str, Any]`, so mypy never checks those reads against the TypedDicts. Only direct `event.data[...]` reads in tests or notebooks get the types. Trail reads about six paths, and the `EVENT_SCHEMA_CASES` tests already cover them. Each breaking version would need its own TypedDict hierarchy, on top of the `[D]` type parameters, the `cast` and a drift test. Typed properties such as `event.layer_id` give mypy and PyCharm the types callers use, and a v2 class overrides only the properties whose paths moved.

>ok


## The runtime

### `trail.jupyter` and `trail.jupyter.session`

`trail.jupyter` decides what Trail connects to, and `trail.jupyter.session` is one run of the
connections. This is how `trail.watchdog` and `trail.watchdog.session` split the file watching.

```python
# src/trail/jupyter/jupyter.py
class Jupyter(Node):
    _parent: Trail
    session = JupyterSession.descriptor()

    def is_inside(self, directory: Path) -> bool:
        # a Dir's own folder, or a folder inside a Dir that tracks its subfolders
        out = any(
            directory == entry.path
            or (
                entry.is_recursive
                and directory.is_relative_to(entry.path)
            )
            for entry in self._trail.dirs
        )
        return out


# src/trail/jupyter/session.py
class JupyterSession(Node):
    _parent: Jupyter
    # seconds stop() waits for the autosave of a burst that changed the map
    settle = 1.5
    # whether a run has started and not closed since
    is_started = False

    def start(self) -> bool:
        # like Session.start, a notebook or a REPL with a running loop can start it
        if any(
            not task.done()
            for task in self.tasks
        ):
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        discovering = self.servers.run()
        closing = self.pipeline.run()
        discovery = loop.create_task(discovering, name="jupyter-discovery")
        closer = loop.create_task(closing, name="jupyter-closer")
        self.tasks[:] = [discovery, closer]
        self.is_started = True
        self.pid = os.getpid()
        return True

    async def stop(self) -> None:
        try:
            # an autosave comes 1.0 s after the last change, and waiting for it lets it fold
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
                # a task that failed was already reported, and stopping must not raise
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                self.tasks.clear()
                self.pipeline.close()
                self.is_started = False

    def fold(self, event: WatchdogEvent) -> bool:
        # asked on every file event, so a session that has not started creates nothing
        if not self:
            return False
        out = self.pipeline.fold(event)
        return out

    def flush(self, entry: Entry) -> None:
        # a map's open events are logged before its move, deletion or offtrail
        if self:
            self.pipeline.close(asset=entry)
```

> We have start and stop in jupyter, is this really appropriate here or should we just mirror the same as trail.watchdog.session, where trail.jupyter contains stable jupyter info while trail.jupyter.session contains the live objects which actually watch the jupyter servers?

**Claude:** Yes, mirror it. `Jupyter` as planned mixes two lifetimes: what Trail should connect to, and the state of one run. `Watchdog` and `Session` already keep those apart.

- **`trail.watchdog`** decides what to watch from the Trail's Dirs and assets (`wanted`, `desired`). It holds no runtime state. Its repr shows the watches the next run would place.
- **`trail.watchdog.session`** is one run: the observer, the queue, the consumer task, the open accesses and the atexit hook. `prepare` creates that state when a run starts, so a stop and restart begins fresh. `del trail.watchdog.session` closes the run and drops it.

The planned `Jupyter` puts `start`/`stop`, the tasks and the pipeline's open bursts next to `runtime`, `is_inside`, `connect` and the callbacks. So `stop` has to clear the tasks and close the pipeline by hand. `fold` also has to inspect `vars(self)` to avoid creating a pipeline on every file event.

Split the same way:

- **`trail.jupyter`** would keep what's stable:
  - the runtime dir, the servers found in the `jpserver-*.json` files as plain data, and the approval rule (`is_inside`);
  - manually added servers (`connect`/`disconnect`);
  - the `on_notice`/`on_message` callbacks.

  When nothing is running, its repr would show the servers a run would connect to, as `Watchdog.__repr__` shows the watches a run would place.
- **`trail.jupyter.session`** would hold one run:
  - the discovery task, and one subscription task per server with its state and counts;
  - the pipeline with its bursts and closer task;
  - `Messages`;
  - `start`, `stop` and `async with`;
  - `fold`, `flush` and `drop`;
  - the atexit hook.

  `bool(session)` and `is_active` would mean what they mean on `Session`.

What this gains:

- **One pattern for both watchers.** The console would run `async with trail.watchdog.session, trail.jupyter.session:`.
- **Hooks into the core read the same way.** The file-event hook in `WatchdogEvent.apply`, the reset in `Events.clear` and the flush in `Trail.offtrail` would call `trail.jupyter.session.fold`, `.drop` and `.flush`. That matches the existing `trail.watchdog.session.flush(...)` in `DirAsset.offtrail` and `session.accesses.drop()` in `Events.clear`. The `vars(self)` check becomes `if not session: return False`.
- **A clean reset.** `del trail.jupyter.session` gives tests and `restart` a fresh run, as it does for the watchdog.
- **The library-use question settles itself.** Starting under a running loop can simply follow what the watchdog session does.

What it costs:

- **One more class.** The planned `start`/`stop` code moves into it almost unchanged.
- **The descriptor.** The custom descriptor in `Session.descriptor` (`__get__`, `__delete__`, fork handling) would need to be shared or copied. A plain `cached_property` also supports `del`, but it skips the fork check.
- **A naming clash.** In Jupyter, "session" already means two other things: jupyter_server's kernel sessions (`/api/sessions`), and the `jupyter_collaboration/session/v1` messages the pipeline handles under a constant named `SESSION`. I'd keep `session` to match the watchdog and rename that constant, for example to `COLLABORATION`, so the pipeline code doesn't use one word for both.

I made the split in this section and in the places that use it. The descriptor moves into a base that both sessions share, and the constant is now `COLLABORATION`.

>ok

`trail.jupyter` keeps what is stable:

- `runtime` is `jupyter_runtime_dir()`, imported lazily, and tests replace it. `files()` reads the
  `jpserver-*.json` files there as `JSON`s, which are plain data. See "Finding servers".
- `is_inside` is the approval rule.
- `connect(*, url, token, root)` adds a server Trail cannot discover, such as one in a container,
  and `disconnect(url)` stops listening to one. Both are remembered by url for the rest of the
  process, so a restarted server and a new session keep them.
- `on_notice` and `on_message` are callbacks the console sets. Without the console, notices go to
  the `trail.jupyter` logger.
- `__repr__` lists the session's servers by number, state, url, root and counts while a run is
  active. Otherwise it lists the servers a run would connect to, as `Watchdog.__repr__` lists the
  watches a run would place. It never shows the token.

`trail.jupyter.session` holds one run:

- `servers`, `pipeline`, `messages` and `tasks` are cached_properties. `servers` holds the live
  `Server`s, each with its subscription task, state and counts. See "Servers".
- `bool(session)` says whether a run has started in this process and has not closed, and
  `is_active` whether its tasks are alive, as on `Session`. The descriptor that makes
  `watchdog.session` on first read, closes and drops it on `del`, and forgets it in a forked child
  moves into a base that both sessions use. `del trail.jupyter.session` gives the next read a clean
  run.
- `async with trail.jupyter.session` calls `start()` and `stop()`.
- The closer task's `run` also records what is open, in a `finally` guarded by
  `asyncio.current_task()`, as `Session.run` does. A Ctrl-C during `stop` still records the bursts,
  and an atexit hook covers an interpreter that exits without `stop`.
- `drop()` forgets the open bursts. `Events.clear` calls it next to `session.accesses.drop()`.
  `Trail.offtrail` calls `flush(asset)` before the RemoveAssetEvent, as `DirAsset.offtrail` flushes
  accesses.
- `summary` is the header segment, read from memory only.

`Node._jupyter` follows the pattern of `_watchdog`, and `Trail.jupyter` is a plain cached_property.
Nothing starts when a Trail opens, since connecting to servers is a side effect. A library user
calls `trail.jupyter.session.start()` or `async with trail.jupyter.session`, and the console does
this itself (decision 10).

### Servers

Trail talks to each Jupyter server through a `Server`. A `Server` holds the server's runtime file,
the state of the subscription to its event feed, and what the feed has sent so far. The pipeline
asks it for the feed's address, the headers that carry the token, and the local path of a map.

The session keeps its servers in `Servers`, reached as `trail.jupyter.session.servers`. They belong
to one run, and the next run finds the servers again. A stopped server keeps its entry for the rest
of the run, so the console still shows its counts.

```python
# src/trail/jupyter/server.py
# the life of a Server, from the moment Trail finds it
State = Literal[
    # found, and waiting for approval or for `jupyter connect`
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
    received: int = 0
    recorded: int = 0
    merged: int = 0
    # skipped messages by reason, such as "duplicate"
    skipped: Counter[str] = field(default_factory=Counter)
    # messages of schemas the pipeline does not use, by schema_id
    other: Counter[str] = field(default_factory=Counter)


@dataclass(eq=False, kw_only=True)
class Server:
    """
    Trail's handle on one Jupyter server during a session. It holds the server's runtime file, the
    state of the subscription to its event feed, and what the feed has sent so far.

    - `number` is its `#N` in the console. Numbers are not reused within a session.
    - `json` is the runtime file it was found with, or the details given to `connect`.
    - `state` and `last_error` say how the subscription is doing.
    - `counts` are the console header's numbers for this server.
    - `schemas` holds the probe's answers: the version of each `$id` Trail knows, or None.
    - `renames` and `reloading` come from the server's contents and collaboration messages.
    """

    servers: Servers = field(repr=False)
    number: int
    json: JSON
    state: State = "available"
    last_error: str = ""
    counts: Counts = field(default_factory=Counts)
    schemas: dict[str, int | None] = field(default_factory=dict)
    # the new path of each renamed path, from contents renames
    renames: dict[str, str] = field(default_factory=dict)
    # the paths the server reloads from disk, with the monotonic time of each overwrite
    reloading: dict[str, float] = field(default_factory=dict)
    task: asyncio.Task[None] | None = None
    # seconds before the first retry, and the most any retry waits
    backoff: float = 0.5
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
        """Returns the local path of a map the server names by its path under `json.root`."""

    def is_reloading(
            self,
            target: Target,
            seen: float,
    ) -> bool:
        """Whether the server reloaded the target's map from disk shortly before `seen`."""

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
    The session's servers, by url. `refresh` keeps them in line with the runtime files. A server
    found in a file and one given to `connect` share an entry if their urls match.
    """

    _parent: JupyterSession
    # seconds between two reads of the runtime directory
    interval = 2.0

    def __init__(self, parent: JupyterSession | None = None) -> None:
        Node.__init__(self, parent)
        self.data: dict[str, Server] = dict()
        # the number the next server gets
        self.next = 1

    async def run(self) -> None:
        """Refresh every `interval` seconds until the task is cancelled."""

    def refresh(self) -> None:
        """
        Read the runtime files again. A new live file adds a server, which connects if it is
        approved or was connected by hand. A server whose file or process is gone is stopped.
        """

    def tasks(self) -> list[asyncio.Task[None]]:
        """Returns the subscription tasks that are running."""
```

> Your Server documentation is odd; instead of using `` in the dostring, they sholud just be comments directly above the respective attribtue.
> You have the "ceiling" comment in the same comment as the "backoff" comment? documentation should be systematic and consistent


> Not sure how exactly we decide which jupyter servers to watch, if not all? Should we just watch all of them and see if any interact with checked assets?

- `Servers` is keyed by url, so a manual connect and a discovered file share one `Server`.
- `number` is what the console shows as `#2`, and what `jupyter connect 2` takes.
- A server whose file or process is gone is `stopped`. Its task is cancelled, and its bursts are
  recorded.
- A server with a unix socket, or with a password only, stays `available`. This plan does not
  connect it.
- `fail` puts the reason in the header and the feed, so a server that stops listening says why. The
  done-callback of a subscription task calls `fail("error", ...)` when the task crashes.
- "Finding servers" says how `refresh` reads the files and which servers connect by themselves.
  "Subscription" and "Probe" say what a `Server` does once it connects.

  > Don't you think this is ordered wrong? We're already talking about "Info" and "Subscription," when I don't even what the "Server" does; I assume you ahve a Server class because you mentioned "Server.locate" but there's nothing here about `class Server` which seems very important? Are you implementing `Server`? `trail.servers`? I have no idea, because you already dove into what I assume are attributes of Server.

**Claude:** Yes, the order is wrong. `Server` is one of the central classes in the plan, but it's never introduced. The plan uses it everywhere before saying what it is:

- **Where it shows up:**
  - "How it fits together" mentions `Servers` and `Server` in two lines;
  - the session section says `servers` holds the live `Server`s;
  - "Discovery and approval" then starts with `Info` and `State`, and calls `Server.locate`;
  - only after the code and the bullets does "A `Server` holds:" list its attributes. There's no class, no docstring and no sentence saying what it's for.
- **`Servers`** appears only as `Servers.refresh`.
- **`State`** lists eight states, but the plan explains only two of them, `stopped` (line 1098) and `subscribed` (line 1195).

To your questions: yes, the plan implements both classes, `Server` and `Servers`, in `src/trail/jupyter/server.py`. After the session split you reach them as `trail.jupyter.session.servers`, not `trail.servers`, because they belong to one run.

- **`Server`** is Trail's handle on one Jupyter server during a run. It holds:
  - the server's file (`Info`, soon `JSON`);
  - its state and last error;
  - the subscription task with its retry backoff;
  - the counts the console header shows;
  - the probe's answers;
  - the contents renames and the paths being reloaded.

  It gives the pipeline `events_url`, `headers()` (which carry the token) and `locate(document_path)`.
- **`Servers`** holds the session's servers, keyed by url and numbered `#1`, `#2` for the console. Every 2 s, `refresh` reads the runtime files. It adds new servers, connects the approved ones, and stops any whose file or process is gone.

I'd reorder the runtime section like this:

1. `trail.jupyter` and `trail.jupyter.session`, as now.
2. A new "Servers" section: what a `Server` is, its class with a docstring, `Servers`, and what each `State` means.
3. "Finding servers": the runtime files, `Info` renamed to `JSON` with `is_running` moved onto `Server`, liveness, and the approval table.
4. Subscription, Probe and the receive pipeline, as now.

I made this change. "Servers" now comes first, with the `Server` and `Servers` classes and a comment on each state. I added an `error` state, which `fail("error", ...)` already used. "Discovery and approval" is now "Finding servers", with `Info` renamed to `JSON`, a `path` field, `is_running` on `Server`, and `found()` renamed to `files()`. The line numbers above are from before the change.

### Finding servers

Every Jupyter server writes a file `jpserver-<pid>.json` into Jupyter's runtime directory when it
starts, and deletes it when it stops cleanly. A server that crashes leaves its file behind.
`jupyter server list` reads these files, and so does Trail, to find the servers on this machine.
Each file holds the server's `url`, `token`, `root_dir`, `pid`, `sock` and a few other keys
(jupyter_server's `ServerApp.server_info`). `JSON` is one file, read into the fields Trail uses:

- `url` and `token` open the server's event feed.
- `root` is the directory the server serves. The approval rule compares it with the Dirs, and
  `Server.locate` joins it with a message's `document_path` to find the map on disk.
- `pid` and `written` tell a live server from a file a crash left behind.

`trail.jupyter.files()` returns one `JSON` per file. Each `Server` holds the `JSON` it was found
with, as `server.json`, the way the Trail holds its `project.json` as `trail.json`.
`connect(*, url, token, root)` builds a `JSON` by hand for a server that writes no file, such as one
in a container. Its `path` is None, as `trail.json.path` is None for a Trail with no directory.

> how about `jupyter.jsons` to match the `server.json`? and should it be a cached dict / mapping? path to instance

```python
# src/trail/jupyter/server.py
@dataclass(frozen=True, slots=True, kw_only=True)
class JSON:
    """
    One `jpserver-<pid>.json` file from Jupyter's runtime directory, or the same details given to
    `connect` for a server that writes no file.

    - `path` is the file, or None for a server given to `connect`.
    - `url` is where the server listens, such as `http://localhost:8888/`.
    - `root` is the directory the server serves, resolved.
    - `token` authenticates Trail with the server. It is never shown.
    - `pid` is the server's process, and `written` is the file's mtime. `Server.is_running` uses
      them to tell a live server from a stale file.
    - `sock` is the unix socket the server listens on in place of a port, or "".
    """

    path: Path | None = None
    url: str
    root: Path
    token: Secret = field(repr=False, compare=False)
    pid: int | None = None
    # the file's mtime, to tell the server from a later process that reused its pid
    written: float = 0.0
    sock: str = ""

    @classmethod
    def read(cls, path: Path) -> JSON | None:
        """Returns the file's details, or None for a file that cannot be parsed yet."""
```

> Not sure what Info is for or where it's used? You just kind of dive into info? Provide more context and a docstring for it?

**Claude:** `Info` holds one Jupyter server's connection details, read from the file the server writes when it starts. I added an intro and docstrings for it at the start of "Discovery and approval" in `notes/10-21-2-jupyter-submodule-plan.md`.

**What it's for.** Every Jupyter server writes `jpserver-<pid>.json` into Jupyter's runtime directory when it starts, and deletes it when it stops cleanly. That's how `jupyter server list` finds servers, and how Trail does too. I checked the keys in jupyter_server's `ServerApp.server_info`. `Info` is one of these files, cut down to the fields Trail uses:
- `url` and `token` open the server's event feed;
- `root` is the directory the server serves;
- `pid` and `written` (the file's mtime) tell a live server from a file a crash left behind;
- `sock` is the unix socket the server listens on instead of a port, if it has one.

**Where it's used:**
- **Finding servers:** `trail.jupyter.found()` returns one `Info` per file.
- **The session:** each live `Server` holds the `Info` it was found with.
- **Approval:** the rule compares `info.root` with the Dirs.
- **Locating maps:** `Server.locate` joins `root` with a message's `document_path` to find the map on disk.
- **Servers without a file:** `connect(*, url, token, root)` builds an `Info` by hand for a server that writes no file, such as one in a container.

**Added to the plan:**
- **Intro:** a paragraph before the code that covers the points above.
- **Docstrings:** a class docstring on `Info` with one bullet per field, and a one-line docstring on `is_running`.

> Because it's always a json, maybe just name it JSON? we have other JSOn classes in the repo .server.json seems appropriate, what d you think?

**Claude:** Yes, `JSON` read as `server.json` fits. The repo already names each file wrapper after its format, and it reads as "the server's json file":

- `trail.json`: the Trail's `project.json` (`trail.py:25`);
- `trail.markers.txt`: the `.markers` file;
- `trail.events.jsonl`: `events.jsonl`.

What would change:

- **Add a `path` field.** `server.json.path` would show which `jpserver-*.json` a server came from, which helps in the debugger. A server added by hand with `connect` has no file, so its `path` is None. That matches `trail.json.path`, which is None for a Trail without a directory.
- **Move `is_running` to `Server`.** `server.json.is_running` reads as if the file were running. It belongs on `Server`, reading `self.json.pid` and `self.json.written`, which keep their meaning as fields of the file.
- **Keep it a frozen dataclass, not a `Node`.** The Trail's `JSON` is a `Node` because it needs the Trail to find its path and to write fields back into it. The server's file only gets read, and it carries its own path, so it needs no parent.
- **Rename `found()`.** "Returns the JSONs" reads oddly; `trail.jupyter.files()`, returning one `JSON` per runtime file, reads better.

There's no clash with the Trail's `JSON` in practice. Both are reached as `trail.json` and `server.json`, and no module needs to import both classes, just as `TXT` and `JSONL` already share generic names per module.

- `Secret` holds the token. Its repr is `<secret>`, and pickling it raises TypeError.
- `JSON.read(path)` returns None for a file it cannot parse yet, including a `root_dir` that is not
  a string or a `pid` that is not an int. The next scan reads it again.
- `Servers.refresh` runs every 2 s and again after a socket drops. It reads each file once per
  mtime. It counts stale files and never deletes them: `jupyter server list` deletes them, and
  Trail should not write to Jupyter's directory.
- psutil is used for liveness, not `os.kill(pid, 0)`, since that call terminates the process on
  Windows.

A server connects by itself when `trail.jupyter.is_inside(server.json.root)` holds. This is how the plan
reads your rule "if the folder matches a watched directory path, connect automatically":

| The server's resolved root_dir, with Dirs `./` and `data/` | Connects by itself |
|---|---|
| the project, or `project/data` | yes |
| `project/notebooks`, inside the recursive root Dir | yes |
| the project's parent, such as ~/PyCharmProjects | no; it serves other projects too |
| an unrelated folder | no |

`Watchdog.covers` is not used for this. It also covers the project's parent, which Trail watches
alone to see the project renamed.

Trail asks about any other server with one feed row:
`jupyter: #2 serves ~/other; 'jupyter connect 2' listens to it`. A manual connect or disconnect is
remembered by url for the rest of the process, so a restarted server keeps it.

### Subscription

There is one task and one socket per server. The token goes only in the `Authorization` header,
never in the URL, so no URL in an error message carries it.

```python
# src/trail/jupyter/consumer.py
SUBSCRIBE: Final = "api/events/subscribe"
# 599 is tornado's code for a timeout
RETRIED: Final = frozenset({500, 502, 503, 504, 599})


async def subscribe(server: Server) -> None:
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
                # refuses the old token, so its file is read again first
                server.servers.refresh()
                if not server.is_stopped:
                    server.fail("refused", "the token was refused")
                return
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

> What is tornado and why is it necessary here?

- `events_url` is the runtime file's `url` with `http` → `ws` and `https` → `wss`, plus
  `api/events/subscribe`. `url` already holds `base_url`, so JupyterHub's `/user/<name>/` works.
- The backoff starts at 0.5 s, doubles up to 30 s, and resets on each subscribe.
- `Server.listen(socket)` sets the state `subscribed` and reads frames until `read_message()`
  returns None. tornado returns None when the socket drops, since Jupyter sends no close frame.
- `listen` hands each frame to `receive` inside `try/except Exception`. A message that fails is
  counted, and the subscription goes on.
- A done-callback on the subscription task calls `server.fail("error", ...)`, so a crash shows in
  the header and the feed.
- tornado ignores proxy variables, follows no redirects, and closes without waiting for the other
  side. Its message limit is 10 MiB.

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
- The probe does not ask about versions Trail does not know. Each probe leaves a 400 warning in the
  Jupyter terminal, and a warning about a missing `layer/v2` reads like a broken emitter.
- The classification reads two jupyter_events error strings, and a test pins them.

### The receive pipeline

```python
# src/trail/jupyter/pipeline.py
CONTENTS: Final = "https://events.jupyter.org/jupyter_server/contents_service/v1"
COLLABORATION: Final = "https://schema.jupyter.org/jupyter_collaboration/session/v1"


class Pipeline(Node):
    _parent: JupyterSession
    # seconds after its last message that a burst closes, if the server announces no save
    pause = 2.0
    # seconds a burst stays open after the save that wrote it, for the watchdog to deliver the write
    grace = 0.25
    # seconds within which an equal message from another view is a duplicate
    window = 1.0
    # seconds after a reload from disk during which a map's layer messages are not GUI actions
    reloaded = 2.0

    def receive(
            self,
            server: Server,
            text: str,
            *,
            at: float | None = None,
            seen: float | None = None,
    ) -> Message | None:
        # stamped on arrival, since a message carries no time
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
            # the message stays in Messages with the error, and the subscription goes on
            self.skip(server, record, f"error: {error!r}")
        return record

    def route(
            self,
            server: Server,
            record: Message,
            seen: float,
    ) -> None:
        event_cls = JupyterGISEvent.registry.get(record.schema_id, JupyterGISEvent)
        if (
            event_cls.is_shared
            and self.duplicates.is_duplicate(record, seen)
        ):
            self.skip(server, record, "duplicate")
            return
        target = self.resolve(server, record)
        if target.reason is not None:
            self.skip(server, record, target.reason)
            return
        if (
            record.kind == "layer"
            and server.is_reloading(target, seen)
        ):
            # the watchdog already logged the write that made the server reload the map
            self.skip(server, record, "reloaded")
            return
        if record.is_unchanged:
            self.skip(server, record, "unchanged")
            return
        self.add(record, event_cls, target, seen)
```
> include docstrings explaining what are these methods adn why they are necessary

- Duplicates: a message is a duplicate if the same server sent an equal message from another
  subject in the last 1.0 s. The comparison covers `schema_id` and every key of `data` except
  `subject`, so it includes the full `old` and `new`.
  - The check covers kinds that every view reports: layers, and kinds Trail does not know. A
    document's `opened` and `closed` and a viewport's `moved` come from one view each, so they are
    never duplicates.
  - The same change twice from one subject is kept. Hide, show, hide within a second sends the
    first and the third as equal messages.
  - The window is a guess, since no browser was run.
- `is_unchanged` holds for an `updated` whose `object` has both `old` and `new`, and they are equal.
- `observe` handles the server's own messages:
  - A contents `save` sets `saved` on the open bursts of that map and wakes the closer. The path
    follows renames, so it is compared with the asset's current path. A save of a tracked map with
    no open burst opens a save burst (see "Saves with no layer event").
  - A contents `rename` `{path, source_path}` is kept on the server.
  - A session `overwrite` marks its path as reloading.
  - Those messages stay in `Messages` with the outcome `used`. Every other contents and session
    message, such as the `get` an open document sends every second, is only counted.

### Resolving the map

```python
@dataclass(frozen=True, slots=True, kw_only=True)
class Target:
    # the map a message is about: its asset, or only its path for a map Trail does not track
    asset: Asset | None
    path: Path | None
    reason: str | None = None

    @property
    def src_path(self) -> str:
        # the asset's current path, since the watchdog follows a rename during a burst
        if self.asset is not None:
            out = str(self.asset.path)
            return out
        if self.path is None:
            return ""
        out = str(self.path)
        return out
```

> Why "Target" and not just "Map"?


`Pipeline.resolve` looks the map up in this order:

1. `server.locate(document_path)`:
   - None for a null path, or for a name that ends in `unsaved_project`;
   - a path outside `root_dir` is refused.
2. The asset at that path, or the one `trail.assets.scan(path)` tracks now if `.markers` matches.
   `Pipeline.subjects` then links the message's subject, per server, to that asset's id.
3. If nothing exists at the path, the asset the subject is linked to, if it is still tracked. Then a
   contents rename of the path, or of a folder above it.

The path comes first because each event carries the map's current path since abecc972. After a
rename, the path already names the moved file. After "Save as", it names the new file, which is a
new asset, while the subject is still linked to the old one. The subject link covers a view whose
path is stale: builds before abecc972, and views whose model does not follow the rename. The link
survives `closed`, since another view can keep sending under the same subject. It is lost when
Trail restarts.

| The message names | Recorded as |
|---|---|
| a tracked map, or a stale path whose subject is linked to one | an event on that asset |
| an untracked map that `.markers` matches | an AddAssetEvent, then an event on the new asset |
| an untracked map that exists inside the project | an event with no entry, with `src_path` |
| a map with no file | skipped: `unsaved` |
| an offtrailed map | skipped: `offtrailed`, since `offtrail` promises to ignore later events |
| a path that does not exist | skipped: `file missing` |
| a map outside the project, or outside `root_dir` | skipped: `outside project`, `outside root` |

"Inside the project" means `trail.jupyter.is_inside(path.parent)`, the approval rule. It stops a
manually connected server rooted at `~` from filling this project's log with other projects' maps.
Skipped messages stay in `Messages` with their reason.

### Bursts

```python
@dataclass(slots=True, eq=False, kw_only=True)
class Burst:
    server: Server
    event_cls: type[JupyterEvent[Any]]
    target: Target
    first: Message
    last: Message
    # monotonic seconds of the last message, which the pause is measured from
    seen: float
    # monotonic seconds of the save that wrote the map, once the server announces it
    saved: float | None = None
    messages: int = 1
    # the map's file events since the burst opened, folded in if the server saved the map
    held: list[WatchdogEvent] = field(default_factory=list)

    def takes(self, record: Message) -> bool:
        # a drag sends one update per step; after a save, the next change starts a new event
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
        out = self.seen + pause
        if self.saved is not None:
            out = min(out, self.saved + grace)
        return out

    def to_events(self) -> list[Event]:
        # held file events are logged before the event, unless a save of the map accounts for them
        event = self.to_event()
        if self.saved is not None:
            types = (
                held.event_type
                for held in self.held
            )
            event.count = dict(Counter(types))
            out = [event]
            return out
        out = [
            *self.held,
            event,
        ]
        return out
```


`to_event` builds the `event_cls` event:

- `schema_id` and `data` are the last message's, with the first message's `object.old`.
- `timestamp` is the last arrival, and `started` the first, when the burst took several messages.
- `saved` is the save's arrival time.
- `src_path` is `target.src_path`, and `entry` is `target.asset`.

`Pipeline.add` puts each message that gets this far into a burst:

- A burst's key is (server, map, `schema_id`, layer id), without the subject, since duplicates
  are already gone. A document message has no layer id.
- An `updated` message joins the open burst for its key while `takes` allows it.
- Any other message opens a new burst. The open burst for its key closes now, unless it has a save:
  a saved burst is never closed before `saved + grace`, so its file events can still arrive.
- `added`, `removed` and `opened` are held like `updated`, so the save that follows them can still
  be folded into them. Opening a map writes its options, so an autosave follows that too.
- `closed` is recorded right after the map's other bursts close, never before them. Closing a view
  writes nothing, so nothing folds into it.
- A viewport `moved` is recorded at once. Its save came about 9 s before it, so holding it would
  fold nothing.

`Pipeline.close(*, due=None, key=None, asset=None)` pops the selected bursts and orders them by
their first message. It appends their events with one `trail.events.extend`, which wakes the
console's stream once. It then marks each burst's messages `recorded` or `merged`, with the event's
id. With no arguments it closes everything, which `stop()` uses. One closer task waits for the
earliest deadline, as `Session.run` does for accesses. All this state lives on the loop thread, so
it needs no lock. `Pipeline.settle(timeout)` waits until every open burst that changed a layer has
its save, or until `timeout` passes.

The pause, which your notes left open (decision 1):

- A burst normally closes 0.25 s after the server's save of the map, about 1.26 s after the last
  change. One event then matches one write of the .jGIS.
- Without a save, it closes 2 s after its last message, for example when autosave is off or the
  write is slow. Its held file events are then logged with it. Nothing is lost; only the fold is
  missed.
- Any pause under about 1.1 s would close a burst before its save.
- The cost is latency: a layer event reaches the feed 1.3 to 2 s after its last message. `jupyter
  tail` prints each message as it arrives.
- Events can reach the log out of order. A burst appends its event, and the file events it held,
  when it closes, after file events of other maps that came later. Each event keeps its own
  `timestamp`. A dirasset's access works the same way today.

### Folding the autosave

```python
# event.py, WatchdogEvent.apply, after _find returns the entry (line 402)
        # a map's open Jupyter events are logged before it moves or goes
        if self.event_type in ("moved", "deleted"):
            trail.jupyter.session.flush(entry)
        ...
        self.entry = entry
        # a map's file events wait in its open Jupyter burst, which folds a save into its event
        if trail.jupyter.session.fold(self):
            return entry
        trail.events.append(self)
        return entry

# pipeline.py
# a save writes the map in place, which the watchdog reports as opens and modifications
FOLDED: Final = ("opened", "modified")

    def fold(self, event: WatchdogEvent) -> bool:
        if (
            event.is_directory
            or event.event_type not in FOLDED
        ):
            return False
        bursts = [
            burst
            for burst in self.bursts.values()
            if burst.target.asset is event.entry
        ]
        if not bursts:
            return False
        # a burst whose save is being written first, then the latest layer burst
        chosen = max(bursts, key=attrgetter("rank"))
        chosen.held.append(event)
        return True
```

- A held file event waits for its burst to close, and nothing depends on message order. The save
  message may come before or after the file events of the write. Measured, a slow save handled its
  save message up to 80 ms after the session had applied the write.
- Only `opened` and `modified` are held. `created`, `moved` and `deleted` change what is tracked, so
  they are always applied and logged. A move or deletion of the map first records its open bursts,
  so their events come before the move, as `Session.flush` does for a dirasset's accesses.
- An editor that writes the map during a burst with no Jupyter save is logged with the burst, not
  folded.
- `Burst.rank` is (has a save, is a layer burst, `seen`).
- Returning the entry keeps `Session.consume` from rescanning. The hook runs on the loop thread,
  and tests that call `consume()` without a loop still work.

### Saves with no layer event

A contents `save` of a tracked map with no open burst opens a save burst. The same 0.25 s grace
applies, and its file events are held the same way. It records one `ContentsServiceV1Event`, which
keeps the contents message, renders as `saved` and carries `count`. Opening, panning and zooming a
map then log one event per save in place of four or five file events (decision 3).

A pan or zoom then gives two events about 9 s apart: the save, then the viewport event once the view
has been still for 10 s.

### Messages: the debugger's memory

```python
# src/trail/jupyter/message.py
Outcome = Literal["pending", "recorded", "merged", "used", "skipped"]


@dataclass(kw_only=True, slots=True, repr=False)
class Message(Envelope):
    id: str = field(default_factory=lambda: uuid4().hex)
    # its number in this process, kept when older messages are dropped
    seq: int
    timestamp: datetime
    server: str
    schema_id: str
    data: dict[str, Any] = field(repr=False)
    src_path: str = ""
    outcome: Outcome = "pending"
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
  built from it, as `Event.__repr__` is. Without them, nothing could print a message.
- `Messages` is a `Collection` capped at 1000. It drops the oldest inside `__setitem__`, and its
  `ids` stays a list, since `Select` slices it. Its parent is the Trail, like the other collections,
  and it is held by `trail.jupyter.session`.
- `seq` keeps a message's number after eviction. Positions in a listing shift as the oldest go.
- `jupyter messages outcome=skipped reason=duplicate -10:` goes through the same `Select` as
  `events`.
- `Watch` cannot follow a buffer that evicts, since its cursor is a position. The live tail
  therefore uses `jupyter.on_message`.

## The console as a live tracker and debugger

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

- While Jupyter waits for the autosave, the session is active. Its debounce applies, the save's file
  events are held and folded, and a late scan places its watches normally.
- Once `trail.jupyter.session.stop` returns, no message is handled. No late scan can then start a watchdog run
  after the session stops.
- Quitting takes up to 1.5 s longer, only while a burst waits for its save.
- The final `drain` prints what both stops appended.

Other console changes:

- The header gets `  jupyter 1/2`, subscribed out of live servers, with `!` while a server refuses
  its token. It gets `  pending N` while bursts are open, as `accesses N` shows. It reads memory
  only.
- `Renderer.verb` returns a JupyterGIS event's own verb, and `saved` for a `ContentsServiceV1Event`.
  theme.py adds `event.added` (green), `event.updated` (yellow), `event.removed` (red),
  `event.closed` (grey) and `event.saved`. A map's `opened` shares the style of a file's `opened`,
  and a viewport's `moved` that of a file's `moved`.
- tornado logs some failures on its `tornado.general` logger, such as a certificate that fails
  verification. The console routes that logger into notices, since patch_stdout would print it
  unstyled above the command bar.
- `Renderer.record` catches an error from `_repr_items` and prints an error row for that record.
  Today one record that fails to render stops `Run.stream` without a word until exit, and a
  debugger has to survive a bad message.
- `Run.monitor` also watches the discovery, closer and subscription tasks. It reports each failure
  once, with a set of reported tasks in place of the single `failed` flag.
- `Run.notice(text, is_error)` prints `jupyter: ...` rows, only when a server changes state.
- `Run.accept` replaces `token=<value>` in a submitted line with `token=<redacted>`, before the echo
  and the history see it. That catches a pasted server URL.

The `jupyter` command, in `src/trail/cli/jupyter.py`, registers through an import in console.py:

| Command | What it does |
|---|---|
| `jupyter`, `jupyter servers` | one row per server: #N, state, url, root, approval, counts, skips by reason, the probe's schemas, the busiest other schemas, stale files |
| `jupyter connect N` | listen to a discovered server for this process |
| `jupyter disconnect N` | stop listening to it for this process |
| `jupyter messages [EXPRESSION]` | list Messages, with the same expressions as `events` |
| `jupyter show SEQ` | print one message's json on one row |
| `jupyter tail [on\|off]` | print one row per message as it arrives, with its outcome |

- `submit` takes the first word as the subcommand and hands `messages` the rest unsplit, as
  `events` does. `complete` offers the subcommands and hands `Listing.complete` the text after
  `jupyter `.
- `Listing` gains a `title` that defaults to `name`, so `MessagesListing` prints `jupyter messages
  (3 of 41)`. A class-level `name` would register it as a command, and mypy rejects setting a
  ClassVar through an instance.
- The help notes gain three items:
  - which servers connect by themselves;
  - that a token is never typed in the command bar;
  - that `jupyter messages` and `jupyter tail` show what came in and why it was skipped.

## Steps

Each step leaves the suite passing and is one small change, the size of a PR in prototype-trail.

1. **Core.** Records with no entry, dotted fields, the class-name guard, and the `.~` rule. The rule
   fixes a bug every preset has today, with `.~a.csv`. Also the pre-commit change, if decision 11
   keeps `dict()`.
   - Tests: a reload with `"entry": null` on a disk-backed Trail (a Trail with no directory never
     calls `to_record`); `count.modified>3`; a hyphenated key; `entry.path=` read against the root;
     a slow save that never tracks `.~a.csv`.
2. **Event model.** `schema.py`, `event.py` with the document, layer and viewport classes, the
   package `__init__` and the import from trail.py. The stub is removed. `Renderer.verb`, the theme,
   and `Renderer.record`'s error row. The YAML copies, the drift tests and the dev dependency
   `pyyaml`.
   - Tests: each of the three `EVENT_SCHEMA_CASES` payloads becomes its class; records survive a
     reload; `data` has shared key strings after a reload; the feed rows match the illustration.
3. **Pipeline, recording at once.** `Jupyter`, `JupyterSession` and the shared session
   descriptor, `Node._jupyter`, `Trail.jupyter`, `Server`, `Servers` and `JSON`, `Messages`,
   and `receive` → `resolve` → record each message at once.
   - Tests: other schemas are counted; each row of the resolve table; a renamed map resolves by its
     new path, and a stale path through its subject; after "Save as", the new path wins over the
     subject; a message with no subject links nothing; `del trail.jupyter.session` gives a clean
     run; a session that has not started folds nothing.
4. **Finding servers and approval.** `Servers.refresh`, psutil, `is_inside`, the notices, and the declared
   `jupyter_core` and `psutil`.
   - Tests: a live file is found and a stale one counted; the approval table; a removed file stops
     its server; one url gives one `Server`; the token never appears in `repr(trail.jupyter)`, feed
     rows, Messages or notices.
5. **Subscription, probe, and a first console.** `consumer.py` with the declared tornado, the probe,
   `async with trail.jupyter.session` in `Run`, the header, the notices and the monitor. Then the
   end-to-end run below, and its feed captured as a fixture.
   - Tests, on a fake tornado server: messages reach the pipeline; a wrong token is refused once and
     not retried; a drop reconnects; a drop with the file gone means `stopped`; the probe's two
     error strings.
6. **Duplicates, reloads, unchanged layers.** The duplicate check, `observe` for `overwrite`, and
   `is_unchanged`.
   - Tests: a copy from another subject is dropped; the same change twice from one subject is kept;
     `opened` from two views is recorded twice; an `overwrite` then an `updated` with old != new is
     skipped as `reloaded`.
7. **Bursts and folding**, tuned on the capture from step 5. `Burst`, `close`, the closer task,
   `stop`'s wait, contents `save` and `rename`, save bursts, `ContentsServiceV1Event`, the fold and
   flush calls, `Events.clear` and `Trail.offtrail`.
   - Tests: 10 updates become one event, with the first old, the last new, `messages: 10` and
     `started`; a saved burst is not closed early; `closed` comes after the map's bursts; held file
     events are folded with a save and logged without one; a save while quitting folds; a Ctrl-C
     during `stop` records; a move during a burst logs the event before the move; file event
     fixtures follow the real save sequence, with at least three folded.
8. **The `jupyter` command.** servers, connect, disconnect, messages, show and tail,
   `Listing.title`, the token redaction and the help notes.
   - Tests: the messages listing shows skips with their reasons; `show` prints the json; a pasted
     `?token=` is redacted from the rows and from the history.
9. **Preset.** `*.jgis`, `*.qgz` and `*.qgs` in geospatial.txt. A preset only affects new
   projects, so existing ones run `mark jgis`.

The end-to-end run in step 5:

- Make a scratch folder with the geospatial preset, a few copies of `../eventfeed/examples/*.jGIS`,
  and `../eventfeed/examples/data/`.
- Rebuild the `eventfeed_dev` environment that notes/10-20-2 describes at abecc972 or later, with
  `python scripts/dev-install.py`. The branch now pins Python below 3.15.
- Start `jupyter lab --ServerApp.root_dir=<that folder>` from that environment. Its root then equals
  the root Dir, so it connects by itself.
- Run `trail` in that folder.
- Go through the checklist in events.md's new "Using this prototype" section: open a map, add and
  remove a layer, change a layer's symbology, move the viewport and wait 10 s, then close the map.
  The reference consumer can run beside Trail as a cross-check.
- Running Trail in ../eventfeed itself would write `.trail/` and `.markers` into Matt's checkout.
  The general preset's `*.json` would also track every package.json under node_modules.
- A short log from this run is also the sample trailfile for Pari.

An optional integration test against a real jupyter_server with jupytergis_core can run under
`pytest.importorskip`. It skips in CI and after any `uv sync`.

## How the sources are covered

| Source | Point | Where |
|---|---|---|
| 10-15 café | first priority: consume Matt's events | the whole plan |
| 10-15 café | dependencies in pyproject.toml | tornado, jupyter_core, psutil and pyyaml (dev), with `uv add` |
| 10-15 café | kwargs, boolean names, early returns | `connect(*, url, token, root)`, `receive(..., *, at, seen)`, `is_running`, `is_stopped`, `is_shared` |
| 10-15 café | type annotations, mypy | the TypedDicts under "Typed payloads" |
| 10-15 café | small PRs | the nine steps |
| 10-15 café | Google-style docstrings, Sphinx | not changed here; a repo-wide decision (10-20-1, task 6) |
| events.md | a breaking change gets a new `$id` | a leaf class per `$id`, the registry by `$id` |
| events.md | versions published side by side | the copy rule, built with the first v2 |
| events.md | an additive property or verb | `data` keeps every key, `object.<key>` terms, no merge for an unknown verb |
| events.md | the feed carries everything | the prefix filter; other schemas counted; contents and session messages used |
| 10-20-1 | the `JupyterEvent` stub | removed; the name becomes the base of every feed event |
| 10-20-1 | the log is append-only | a burst is appended once, when it closes |
| 10-20-1 | project-level events (initiative Q4) | events with no entry |
| 10-20-1 | a mock trailfile for Pari | `schema_id` and `data` as jupyter_server sent them; a sample log from step 5 |
| 10-20-1 | record trailfile and event-model decisions (task 7) | `"entry": null` is a log format change to record in geojupyter/trail |
| 10-20-2 | find servers, follow restarts, add manual servers, never show the token | discovery, `Secret`, the header-only token, the redaction |
| 10-20-2 | tie events to tracked maps; scan; ignore `.~` | resolve, `_is_markable` |
| 10-20-2 | link a layer to its data file | decision 9 |
| 10-20-2 | stamp on arrival, keep the whole message, a short summary | `timestamp`, `schema_id` and `data`, `change` |
| 10-20-2 | relate the save to the layer event | the fold (your decision "fold into it"; decision 2) |
| 10-20-2 | quirks: rename, `closed` with other views, duplicates, slider bursts, unsaved maps | the path first, then subjects and contents renames; links kept after `closed`; duplicates; bursts; the resolve table |
| 10-20-2 | a live view with connection state, counts, messages with reasons, and the probe | the console section |
| 10-20-2 | your decisions: untracked maps yes, auto-connect or ask, fold, drop duplicates, only while running | resolve, approval, fold, duplicates, no catch-up |
| 10-20-2 appendix | `viewport/v1`, sent once the view is still for 10 s | `ViewportV1` and `ViewportV1Event` |
| 10-20-2 appendix | the path is read when each event is sent; "Save as" can change it | resolve tries the path before the subject |
| 10-20-2 appendix | the local-client filter covers only viewport events | layer duplicates are still dropped |
| 10-20-2 appendix | "Using this prototype" and its manual checklist | the end-to-end run in step 5 |
| 10-20-2 appendix | the stale paragraph on high-frequency events | a point for PR #4 |
| 10-20-3 | the reference consumer's gaps | runtime files, reconnect, the header token, base_url and wss, records in place of printed json |
| 10-20-3 | do not import the prefix from jupytergis_core | `PREFIX` is copied |
| 10-20-3 | subject optional, document_path null, old and new opaque | no subject link, `unsaved`, `total=False` TypedDicts |
| 10-20-3 | reuse `EVENT_SCHEMA_CASES`; a fake WebSocket server | steps 2 and 5 |

## Decisions for you

1. **The pause.**
   - (A, recommended) Hold each layer and document event until 0.25 s after the server's save of
     the map, or 2 s without one: about 1.26 s, with `count` and one event per write.
   - (B) Close a burst after a short pause such as 0.5 s, and record single messages at once.
     Separately, drop the map's file events until the next save plus the grace. Events show sooner,
     but the record loses `count` and one event per write.
2. **Where to fold.**
   - (A, recommended) In the log: the file events of the save are not written, and the event keeps
     `count` and `saved`.
   - (B) In the console only: the file events are logged with the event's id, and the console
     shows them under it. Your answer to the 10-20-2 question was about the console, so this is
     yours to choose.
3. **Saves with no layer event.**
   - Record one `ContentsServiceV1Event` per save of a tracked map (recommended), or keep them as
     four or five file events.
   - Should every tracked file saved through Jupyter (notebooks, a csv edited in JupyterLab) get the
     same treatment?
   - Should a pan's save fold into its viewport event? The save would wait about 10 s in its
     burst. I recommend two events.
4. **Untracked maps.** Record them only inside the project. Skip offtrailed maps, maps with no file,
   and paths that do not exist. Is that what you meant by recording events about maps Trail does
   not track?
5. **Approval.**
   - Is the approval table what "matches a watched directory path" means?
   - Is a feed row enough for "ask first", or should the command bar ask `connect #2? [y/N]`?
   - Should a manual connect be remembered across runs, for example in `.trail/project.json`? I
     recommend not yet.
6. **Unknown `$id`s.** Record them with the generic class (recommended), or skip them with a
   notice.
7. **Class names.** A leaf per `$id` under a base class per kind (recommended), or one `LayerEvent`
   for every version.
8. **WebSocket client.**
   - (A, recommended) tornado. ipykernel already installs it, and Trail would declare it. It
     ignores proxy variables, follows no redirects and logs no headers. Its `AsyncHTTPClient` makes
     the probe cancellable.
   - (B) websockets. It has typed errors, keepalive by default, `unix_connect`, and it is what the
     reference consumer uses. It needs `proxy=None`, a logger pinned above DEBUG (it logs the
     Authorization header) and `>=16.1`, so that a redirect cannot carry the token to another
     origin.
   - Both need the same hand-written reconnect loop.
9. **Data paths and starting layers from the saved .jGIS** (still open from 10-20-2). Should Trail
   read each layer's data path or URL from the map file, to link a layer to its data until
   JupyterGIS sends source events? Should it also record the layers a map held when it opened,
   which the feed never reports? If yes, OpenEO's `authBearer` and `httpHeaders` must be redacted,
   since the file can hold credentials.
10. **Library use.** Start `trail.jupyter.session` explicitly (recommended for now), or start it
    under a running loop the way the watchdog session does. In a notebook, the kernel's own server is usually rooted at the
    project.
11. **Empty `dict()`.** Configure `check-builtin-literals` with `--ignore=dict`, which follows
    CLAUDE.md, or write `{}` for empty dicts in this repo.

Not in this plan:

- unix-socket servers;
- adding a manual server from the console;
- the side-by-side version rule, until a v2 exists;
- remembering approvals across runs;
- notebook-link/metrics events;
- kernel execution events.

## Risks

- Folding is a heuristic. A script that writes the map during a burst that Jupyter also saves would
  be folded with it.
- The duplicate window of 1.0 s is a guess. An identical change by another user within it is
  dropped.
- Messages carry no sequence number, and rapid POSTs could arrive out of order (speculation).
- Subject links are lost when Trail restarts. A view whose path is stale, from a build before
  abecc972 or a model that does not follow a rename, then names a missing file. Its events are
  skipped as `file missing`, unless Trail saw the contents rename.
- JupyterGIS reports every change to the shared layers, whatever its origin. A notebook's
  `doc.add_layer()` likely arrives as a GUI `added`, and Trail cannot tell. The plugin has
  `transaction.local` in hand but drops it.
- Notebook-output maps likely send `closed` right after `opened`, and no `closed` at the end (read
  from the code, not observed).
- The probe depends on two jupyter_events error strings.
- Two Trail processes on one project, such as the console and a notebook, would both record the
  feed into one log. The watchdog has the same risk today. An advisory lock in `.trail/` could come
  later.
- psutil's process start time follows clock steps. The 60 s margin covers a step after boot, not
  a larger one.

Points to raise on PR #4:

- a local-client filter for layer events, as viewport events now have;
- a stable document identity across "Save as", which events.md leaves open;
- the map's projection in viewport events;
- a timestamp or a sequence number (jupyter_server#1710);
- source events;
- the `closed` semantics with several views;
- "Absent for 'deleted' verb" in layer_v1.yaml should say `removed`, still at abecc972;
- "High-frequency events need throttling" still says viewport events are not implemented.

## Appendix: the checklist rename and `trail.entries`

This appendix plans the two refactors from your notes of 2026-10-27. Neither is about Jupyter. Both
touch code that step 1 of the Jupyter plan edits, `Assets._is_markable` and `Trail._entry`. The
second is the `trail.entries` that your comment under "Events with no entry" asks for.

### `trail.entries`

The class named `Entries` today, in entry.py, is the base of `Assets` and `Dirs`. It becomes
`EntryCollection`. Only entry.py, asset.py and dir.py name it.

`Trail.entries` existed until 5234685 ("refactor", 2026-10-05). It was a third collection, an
`Entries[Entry]` of its own, and `Entries._synced` copied every change to `trail.assets` or
`trail.dirs` into it. 5234685 deleted entry.py, and `Trail.entries` with it. aab2b1d brought `Entry`
back as the base of assets and Dirs, but not `Trail.entries`. `Trail._entry` (trail.py:605) is what
is left of it.

The new `Entries` lives in entries.py and is reached as `trail.entries`. It holds no entries. It
reads `trail.assets` and `trail.dirs` on each call, so nothing has to be kept in sync. The old one
needed `_synced` for that.

It looks up both kinds at once, the way `trail.assets` looks up assets:

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

- `trail.entries[key]` returns the asset or the Dir with an id or at a path. It raises KeyError for
  a key that is neither. Several keys return a tuple, as `trail.assets[...]` does.
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

`Trail._entry` goes. Its two callers, `Event.from_record` (event.py:135) and `JSONL.read`
(event.py:553), call `trail.entries.get(entry_id)`.

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
comments.

`is_checked` becomes a public name. Its docstring says that it is False for a path that the
checklist decides but that Trail already tracks or has offtrailed. `Dirs.tracks` still answers
whether the checklist alone decides a path.

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

There is no migration. A project made before the rename has a `.markers` file and no `.checklist`.
If Trail opens it, Trail reads an empty checklist, logs a RemoveDirEvent for every Dir and watches
nothing. Rename the file first, with `mv .markers .checklist`.

### Order and steps

I recommend that both refactors land before the Jupyter plan, on a branch `checklist` off
`hodczak`. The Jupyter plan's first step edits `_is_markable` and calls `trail._entry`. If these
land first, the Jupyter steps are written once, against the new names. `trail.entries` goes first,
so `_is_markable` moves once and the rename does not touch it.

1. `trail.entries`. The base class becomes `EntryCollection`. The new `Entries` takes the lookups
   and the four helpers above, and `Trail._entry` goes. Nothing a user types changes.
   - Tests: a lookup by id and by path for each kind; KeyError and `get` for a key that is neither;
     `in` with an entry; Dirs before assets when iterating; a path offtrailed before a reload is
     still in `offtrailed` after it; `is_ignored` for `.trail` and for a file inside it. The
     existing scan and walk tests cover `is_checked` and `is_descending`.
2. The checklist rename, across the library, the CLI, the presets and the tests. No behaviour
   changes, so the existing suite, renamed, is its test.

After both land, these places in the Jupyter plan change. I would update them when you say so.

- "What else we need": `_is_markable` becomes `trail.entries.is_checked`, and
  `markers/geospatial.txt` becomes `checklists/geospatial.txt`.
- "Events with no entry": the `JSONL.read` sketch calls `trail.entries.get(entry_id)`.
- "Jupyter's backups": the gate is `is_checked` in entries.py, and the `!.~*` remark says
  `.checklist`.
- Step 9: existing projects run `check jgis`.
- The end-to-end run: `.checklist` in Matt's checkout.
- The sources table: `_is_markable` becomes `is_checked`.

### Decisions for these two refactors

1. Iteration. Your comment asks for `trail.entries` to behave like a mapping. A
   `collections.abc.Mapping` iterates its keys, but `trail.assets` and `trail.dirs` iterate their
   entries.
   - (A, recommended) `for entry in trail.entries` yields entries, as the collections do, with
     `[]`, `get` and `in` as above.
   - (B) Subclass `Mapping[str, Entry]`, keyed by id. `keys`, `values` and `items` come for free,
     but iterating `trail.entries` yields ids while iterating `trail.assets` yields assets.
2. Docstring examples. About 14 examples go stale with the rename. They are the `Markers (3)` reprs
   in trail.py, markers.py and fmt.py, `self.mark('zarr')`, `self.unmark('.zarr')` with its
   warning, `trail.unmark('raw/')` in event.py, the `.markers` path in the `TXT` repr, and the
   console transcripts in commands.py and marker_completion.py. Only names change in them. Your
   rule is to report stale examples, not to fix them. Should the rename step update them? I
   recommend yes.
3. The noun. The plan writes "line" where "marker" named one line. The other choice is "item", as in
   a checklist item. `TXT.checked` would then be `TXT.items`.
4. Order. These land before the Jupyter plan (recommended), or they are folded into its first step.
