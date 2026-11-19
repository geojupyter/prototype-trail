# Tool events

## TLDR

Unlabeled statements describe the current code, read at the checkouts in [How this was checked](#how-this-was-checked-tldr). **Observed** marks output from a run. **Deduction** marks my inference. **Planned** marks work that a plan or a `trail-todo` comment describes and the code does not do. A reason is credited to the plan, schema, docstring or comment that states it. Links to Trail code point at the working tree. Links to JupyterGIS code are pinned to a commit.

### What happens when you pick a tool ([Deep Dive](#what-happens-when-you-pick-a-tool-tldr))

You pick a tool in the map editor: Identify, Add Marker or Edit Features, or you press Escape to go back to panning. Trail records each change as one [`ModeV1Event`](../../src/trail/jupyter/event.py#L521): the tool now active and the tool before it. A tool is called a mode in the code, because the editor's own name for it is the interaction mode of the map. **Observed:** a captured session replayed through Trail's consumer gave 10 tool records, such as `mode: Add Marker` with `previous: none`, where `none` is how Trail prints panning ([what each record looks like](#what-each-record-looks-like)).

These terms are needed:

- **JupyterLab** is the browser workspace that holds notebooks and maps. One loaded browser page is one **page**, with its own random [`frontend_id`](../../src/trail/jupyter/event.py#L134).
- **JupyterGIS** is the map editor inside JupyterLab. A map is a `.jGIS` file, which Trail tracks as an asset.
- A **tool**, or **mode**, is how the map reacts to the mouse. The editor has four: `panning`, which is the default, `identifying`, `marking` and `drawing`. Only one is active in a map at a time.
- **Jupyter Server** is the process behind JupyterLab. It relays JupyterGIS's messages to anything that subscribes.

Three steps stay distinct:

1. **You act.** You pick a tool, press Escape, or the editor resets the tool itself.
2. **JupyterGIS reports.** The page sends a `mode/v1` message with the new mode, the old mode, the page's `frontend_id` and a number from the page's counter ([what the browser reports](#what-the-browser-reports-tldr)). Jupyter Server checks it and relays it.
3. **Trail records.** Trail subscribes to the server. It finds the map's asset ([finding the map](#finding-the-map-tldr)), builds a [`ModeV1Event`](../../src/trail/jupyter/event.py#L521) ([the record class](#the-record-class-tldr)) and appends it to its event log ([storage and replay](#storage-and-replay-tldr)).

| Piece | Where | Role |
|---|---|---|
| emitter, [`wireModeEvents`][ef-wire-mode] | JupyterGIS, in the browser | Sends one message for each change of a map's mode. |
| [`DocumentFocus.order`][ef-focus-order] | JupyterGIS, in the browser | Gives the message its place in the page's counter ([ordering with focus](#ordering-with-focus-tldr)). |
| [`Consumer.route`](../../src/trail/jupyter/consumer.py#L166) | `trail.jupyter.consumer`, reached as [`trail.jupyter.session.consumer`](../../src/trail/jupyter/session.py#L37) | Finds the map's asset, builds the record and appends it. |
| [`ModeEvent`](../../src/trail/jupyter/event.py#L474) | `trail.jupyter.event` | The record ([the record class](#the-record-class-tldr)). |
| [`Jupyter.focus`](../../src/trail/jupyter/jupyter.py#L101) | `trail.jupyter` | Puts tool and focus records of one page back in order. |

Skeleton of the Trail side, condensed from the source and not runnable as written:

```python
# trail/jupyter/consumer.py
class Consumer(Node):
    def route(self, server, record) -> None:
        event_cls = JupyterEvent.registry.get(record.schema_id, JupyterEvent)   # ModeV1Event
        event = event_cls(schema_id=..., data=data, ...)
        event.entry = found.asset
        event.apply(self._trail)

# trail/jupyter/event.py
class ModeEvent(PageEvent):
    labels: ClassVar[dict[str, str]] = {'panning': 'none', 'identifying': 'Identify', ...}
```

### What the browser reports ([Deep Dive](#what-the-browser-reports-tldr))

The map's model holds its mode and tells its listeners when the mode changes. The emitter keeps the last mode it saw, and for each change sends one message with the new and the old mode. Two views of the same map share one model, so a change is sent once. The first `panning` is not a change, and setting the mode that is already active sends nothing. Automatic resets, such as the editor going back to panning when the selected layer cannot be identified, are sent like any other change.

```ts
// python/jupytergis_core/src/eventFeed/mode.ts
const publish = (_: IJupyterGISModel, mode: Modes): void => {
  if (model.isDisposed || mode === previous) return;
  const { frontend_id, focus_seq } = page.order();
  emit({ schema_id: MODE_SCHEMA, data: { subject, verb: 'changed', frontend_id, focus_seq,
                                         object: { document_path, mode, previous } } });
  previous = mode;
};
```

### Ordering with focus ([Deep Dive](#ordering-with-focus-tldr))

A tool change belongs on the timeline of its page, next to the maps the page activated and deactivated. The page numbers both kinds of message with one counter, [`focus_seq`](../../src/trail/jupyter/event.py#L139). Before the emitter numbers a tool change it sends any pending focus change, so the activation that led to the tool change gets the smaller number. Trail then rebuilds the page's order from the numbers, whatever order the messages arrived in. [`Jupyter.focus`](../../src/trail/jupyter/jupyter.py#L101) does this.

```ts
// python/jupytergis_core/src/eventFeed/focus.ts
order(): { frontend_id: string; focus_seq: number } {
  this.flush();
  return { frontend_id: this._frontendId, focus_seq: ++this._sequence };
}
```

### Finding the map ([Deep Dive](#finding-the-map-tldr))

A tool message goes through the same checks as every JupyterGIS message. [`Consumer.resolve`](../../src/trail/jupyter/consumer.py#L238) finds the map's asset from the message's `document_path`. A map with no saved path, a map outside the server's root and a map outside the project give no record. The record's `entry` is the map. The tool is not linked to a layer or a data file.

```python
# trail/jupyter/consumer.py
def resolve(self, server, data) -> Map: ...
# Map(reason=...) for unsaved, outside root, offtrailed, outside project, file missing
```

### The record class ([Deep Dive](#the-record-class-tldr))

[`Event`](../../src/trail/event.py#L59) → [`JupyterEvent`](../../src/trail/jupyter/event.py#L36) → [`PageEvent`](../../src/trail/jupyter/event.py#L127) → `ModeEvent` → `ModeV1Event`. `PageEvent` adds the page's `frontend_id` and `focus_seq`. `ModeEvent` reads the new and the old mode and prints both by their toolbar names: `Identify`, `Add Marker`, `Edit Features`, and `none` for panning. A mode that Trail has no name for prints as sent. A query uses the raw value, such as `mode=identifying`, not the printed name.

```python
# trail/jupyter/event.py
class ModeEvent(PageEvent):
    @property
    def mode(self) -> str | None: ...        # object.mode
    @property
    def previous(self) -> str | None: ...    # object.previous
```

### Storage and replay ([Deep Dive](#storage-and-replay-tldr))

Each record is one JSON line in the log, with the map's asset id as its entry. The rows are computed from `data` when the record is read back, and the toolbar names are not stored. **Observed:** after reopening the project, all 10 tool records of a test run rebuilt to equal records, and the page timeline rebuilt in the same order.

```python
# trail/event.py
class Event:
    def to_record(self) -> dict: ...                  # one JSON line of the log
    @classmethod
    def from_record(cls, /, trail, resolved_entry=None, **record) -> Event: ...
```

### Limits today ([Deep Dive](#limits-today-tldr))

**Current behavior:**

- **Panning prints as `none`.** The first mode and every return to the default read as the absence of a tool ([the record class](#the-record-class-tldr)).
- **A reset looks like a choice.** The message does not say whether you or the editor changed the tool ([what the browser reports](#what-the-browser-reports-tldr)).
- **Closing a map ends no mode.** A tool that is active when the map closes leaves no change back to panning ([what the browser reports](#what-the-browser-reports-tldr)).
- **A tool record is tied to one page and one map.** Collaborators never see it, and a reopened map starts in panning ([what the browser reports](#what-the-browser-reports-tldr)).

### How this was checked ([Deep Dive](#how-this-was-checked-tldr))

The consumer and event tests pass: 76 passed and 1 skipped, together with the kernel and subscription tests. The emitter's event feed tests pass: 49. **Observed:** a scenario run replayed a captured session through the real consumer and sent tool messages with unknown, missing and future values. No browser was run. Everything about the emitter comes from reading its source, its tests and its docs.

## Deep Dive

### What happens when you pick a tool ([TLDR](#what-happens-when-you-pick-a-tool-deep-dive))

This section lists every stage between picking a tool and a line in the log.

| # | Where | What happens | What can stop it |
|---|---|---|---|
| 1 | JupyterGIS model, [`currentMode`][ef-set-mode] | Setting a different mode emits `modeChanged`. | Setting the mode that is already active. |
| 2 | emitter, [`wireModeEvents`][ef-wire-mode] | Numbers the change with [`DocumentFocus.order`][ef-focus-order] and sends `mode/v1` with `events.emit`. | A change that equals the last mode the emitter saw. A disposed model. |
| 3 | Jupyter Server, `POST /api/events` | Validates the message against the schema registered under its `schema_id` and puts it on the event bus. | A rejected message gives a `400` that only the browser console shows. |
| 4 | Trail, [`subscribe`](../../src/trail/jupyter/subscription.py#L14) | Reads the server's WebSocket feed at [`Server.endpoint`](../../src/trail/jupyter/server.py#L288) and passes each text message to the consumer. | A refused, unsupported or dropped connection. |
| 5 | [`Consumer.receive`](../../src/trail/jupyter/consumer.py#L96) | Parses the message and keeps JupyterGIS messages. | Invalid JSON, or another schema. |
| 6 | [`Consumer.route`](../../src/trail/jupyter/consumer.py#L166) and [`resolve`](../../src/trail/jupyter/consumer.py#L238) | Finds the map's asset and builds the record. | Unsaved, outside root, outside project, offtrailed, file missing. |
| 7 | [`JupyterEvent.apply`](../../src/trail/jupyter/event.py#L114) | Appends the record to `trail.events`, which writes one line to the log. | |

The Trail side runs in a session: [`JupyterSession`](../../src/trail/jupyter/session.py#L21) creates the consumer as [`session.consumer`](../../src/trail/jupyter/session.py#L37). The classes [`Event`](../../src/trail/event.py#L59) and [`JupyterEvent`](../../src/trail/jupyter/event.py#L36), the consumer's checks and the log are shared with the other event families. [Layer events](11-15-1-layer-event.md#making-the-record-tldr) explains the consumer, and [Map events](11-15-4-map-event.md) explains the page, its counter and [`PageEvent`](../../src/trail/jupyter/event.py#L127). This note covers what is specific to tools.

### What the browser reports ([TLDR](#what-the-browser-reports-deep-dive))

This section covers what the emitter sends and when. The emitter lives in JupyterGIS, not in Trail. Its code is in the clone `../eventfeed`, a prototype that adds the event feed to JupyterGIS. The tool message is one of the prototype's additions, and the event coverage report lists the new message types for tools among the changes still to propose upstream ([event coverage report](../../scratch/11-12-2-event-coverage.md)).

#### The mode of a map

A map model has one interaction mode, of the type [`Modes`][ef-modes-type]: `panning`, `identifying`, `marking` or `drawing`. The model starts in `panning` ([`_currentMode`][ef-initial-mode]). The setter of [`currentMode`][ef-set-mode] emits a signal only when the value changes, and [`toggleMode`][ef-toggle-mode] switches a mode off to `panning` if it was already the active one:

```ts
  toggleMode(mode: Modes) {
    this.currentMode = this._currentMode === mode ? 'panning' : mode;
  }

  get currentMode(): Modes {
    return this._currentMode;
  }

  set currentMode(value: Modes) {
    if (this._currentMode === value) {
      return;
    }

    this._currentMode = value;
    this.modeChanged.emit(value);
  }
```

The plan for tool records ([Recording which map tool the user selected](../plan/11-4-2-remaining-baseline-events.md)) lists how the mode changes: the toolbar buttons Identify, Add Marker and Edit Features select `identifying`, `marking` and `drawing`, and Escape returns to `panning`. Some changes are automatic: Identify returns to `panning` when the selected layer cannot be identified, and Drawing returns when a layer that cannot be drawn into is selected or the draw layer is removed. The mode is a plain field of the model. It is not stored in the shared document or in awareness. Views of one model share it, collaborators never see it, and a reopened map starts in `panning`.

#### `wireModeEvents`

[`wireModeEvents`][ef-wire-mode] is wired once for each model, directly after `wireDocumentEvents`, in the plugin's `wireJupyterGISEvents`. It does not wait for the model's first synchronization, since the mode is not part of the shared document:

```ts
export function wireModeEvents(
  model: IJupyterGISModel,
  page: IPageOrder,
  emit: (request: Event.Request) => void,
): void {
  if (model.isDisposed) {
    return;
  }
  let previous: Modes = model.currentMode;
  const publish = (_: IJupyterGISModel, mode: Modes): void => {
    if (model.isDisposed || mode === previous) {
      return;
    }
    const { frontend_id, focus_seq } = page.order();
    emit({
      schema_id: MODE_SCHEMA,
      version: '1',
      data: {
        subject: String(model.getClientId()),
        verb: 'changed',
        time: new Date().toISOString(),
        frontend_id,
        focus_seq,
        object: {
          document_path: model.filePath || null,
          mode,
          previous,
        },
      },
    });
    previous = mode;
  };
```

The emitter keeps `previous` itself, because the signal supplies only the new mode. It starts from the model's current mode, so the initial `panning` is not a change. The docstring says the same: the initial `panning` is not a change, and automatic resets are reported like any other change. [`IPageOrder`][ef-page-order] is the small interface that the focus controller implements, and the source comment gives the reason for the one counter: focus and tool changes share one page counter. The message has no `reason`, so a reset cannot be told from a choice. The plan says an optional reason would require each caller that sets the mode to supply its cause, and the schema has none.

The function also disconnects its handler when the model is disposed. It sends nothing when a map closes, so a tool that is active at that moment ends without a change back to `panning`. **Deduction** from the source.

The emitter's tests cover the message and its order: [`reports each change with the previous mode`][ef-test-mode-change], [`reports nothing for the initial mode or setting the current mode`][ef-test-mode-initial], [`stops after the model is disposed`][ef-test-mode-disposed] and [`numbers a mode change after the activation that caused it`][ef-test-mode-order].

#### The message

The schema is [`mode_v1.yaml`][ef-schema-mode]. Trail keeps a copy for its tests as [`tests/jupyter/schemas/mode_v1.yaml`](../../tests/jupyter/schemas/mode_v1.yaml). **Observed:** a manual `diff` found no difference. The schema requires `verb` and `object`, and the object requires `document_path`, `mode` and `previous`. The `mode` and `previous` fields allow the four mode names.

| Field | Content |
|---|---|
| `verb` | Always `changed`. |
| `subject` | The client id of the map copy that sent the message. |
| `time` | The page's clock when it observed the change. Trail stores it and does not use it as the record's time. |
| [`frontend_id`](../../src/trail/jupyter/event.py#L134), [`focus_seq`](../../src/trail/jupyter/event.py#L139) | The page and its counter ([ordering with focus](#ordering-with-focus-tldr)). |
| `object.document_path` | The `.jGIS` path relative to the server root, or `null`. |
| `object.mode`, `object.previous` | The mode after the change and the mode before it. |

This message is hand-written to match the schema:

```json
{
  "schema_id": "https://schema.jupytergis.org/events/mode/v1",
  "subject": "1928399274",
  "verb": "changed",
  "time": "2026-11-12T09:50:00.549Z",
  "frontend_id": "34d02c8a-db83-4f58-b6d4-dff696c77bcb",
  "focus_seq": 6,
  "object": {
    "document_path": "maps/city.jGIS",
    "mode": "marking",
    "previous": "panning"
  }
}
```

#### Delivery to Trail

The browser sends the message with `events.emit`, which posts it to Jupyter Server. The server validates it against the schema registered under its `schema_id` by [`register_event_schemas`][ef-register]. It rejects an unregistered id with a `400` that reaches only the browser console ([Silent failures][ef-events-md-silent]).

### Ordering with focus ([TLDR](#ordering-with-focus-deep-dive))

This section covers the page's counter, which the focus controller and the mode emitter share. [Map events](11-15-4-map-event.md#which-map-has-focus-tldr) covers the focus messages. This section covers what a tool change needs from it.

A browser page runs one [`DocumentFocus`][ef-focus]. It counts every focus change with `++this._sequence`. A tool change needs a number from the same counter, so that the page's records form one sequence. [`order`][ef-focus-order] gives it one:

```ts
  order(): { frontend_id: string; focus_seq: number } {
    this.flush();
    return { frontend_id: this._frontendId, focus_seq: ++this._sequence };
  }
```

The controller does not publish a focus change at once. [`_schedule`][ef-focus-schedule] waits one microtask, so that several browser signals describe one change. A tool change can happen in that gap. [`flush`][ef-focus-flush] publishes the pending change first, so the activation that led to the tool change gets the smaller number. The emitter's docs state it: before numbering a mode change, the focus controller publishes any pending focus transition, so a tab activation precedes a tool change it caused ([Mode changes][ef-events-md-mode]). The test [`numbers a mode change after the activation that caused it`][ef-test-mode-order] covers it.

Both message types carry the page's `frontend_id` and the number, so Trail can sort them. [`Jupyter.focus`](../../src/trail/jupyter/jupyter.py#L101) takes a server and a `frontend_id` and returns the page's tool records and focus changes in the order of `focus_seq`. A record without a counter comes last, ordered by time. [Map events](11-15-4-map-event.md#which-map-has-focus-tldr) shows the method in full. This is the filter that picks the tool records:

```python
        events = [
            event
            for event in self._trail.events
            if (
                (
                    isinstance(event, ModeEvent)
                    or (
                        isinstance(event, DocumentEvent)
                        and event.is_focus
                    )
                )
                and event.server == url
                and event.frontend_id == frontend_id
            )
        ]
```

The plan's reason for including tool records is in the method's comment: tool changes share the focus counter of the page that sent them. The plan adds that Trail can then sort the records of one page regardless of the order the POST requests arrived in.

**Observed results.** A scenario run, at the checkouts in [How this was checked](#how-this-was-checked-tldr), replayed the captured session [`baseline.json`](../../tests/jupyter/fixtures/baseline.json) and asked for the page `34d02c8a`. The timeline had 10 records, numbered 1 to 10, with an activation and two tool changes in the middle:

| `focus_seq` | Record |
|---|---|
| 5 | [`DocumentV1Event`](../../src/trail/jupyter/event.py#L195) `activated`, `maps/city.jGIS` |
| 6 | `ModeV1Event` `changed`, `marking` |
| 7 | `ModeV1Event` `changed`, `panning` |
| 8 | `DocumentV1Event` `deactivated`, `maps/city.jGIS` |

[`test_mode_and_focus_share_page_order`](../../tests/jupyter/test_consumer.py#L440) sends a tool and a focus message of one page in the order 4, 1, 2, 3 and a tool message of another page, and checks that the first page's timeline comes back as 1, 2, 3, 4, also after reopening the project.

### Finding the map ([TLDR](#finding-the-map-deep-dive))

This section covers what `Consumer.route` and `Consumer.resolve` do with a tool message. They are the same for every JupyterGIS message. [Layer events](11-15-1-layer-event.md#making-the-record-tldr) describes them in full, and [Map events](11-15-4-map-event.md#finding-the-map-tldr) covers what matters for a map. A tool message needs the same fields as a map message: a string `verb`, an `object` mapping and an `object.document_path` that is a string or `null`.

A tool message with no saved path is dropped as `unsaved`. A path outside the server's root is dropped as `outside root`, and one outside the project as `outside project`. An untracked map or a missing file is kept as a skipped [`Message`](../../src/trail/jupyter/message.py#L25). The record's `entry` is the map's asset. Trail does not check `object.mode` or `object.previous`. **Observed:** a message with a mode Trail has no name for, one without `previous` and one without `mode` were all recorded ([the record class](#the-record-class-tldr)).

The tool is not tied to a layer or a data file. The plan notes that the Add Marker tool also creates a source and a layer on each click, and those arrive as their own records ([Source events](11-15-6-source-event.md), [Layer events](11-15-1-layer-event.md)).

### The record class ([TLDR](#the-record-class-deep-dive))

This section introduces the class that holds a tool record. [`ModeEvent`](../../src/trail/jupyter/event.py#L474) lives in `trail.jupyter.event`. The consumer builds it, and the console and [`trail.events.select`](../../src/trail/collection.py#L75) read it. It extends [`PageEvent`](../../src/trail/jupyter/event.py#L127), which adds `frontend_id` and `focus_seq` ([Map events](11-15-4-map-event.md#the-record-classes-tldr)), and [`JupyterEvent`](../../src/trail/jupyter/event.py#L36) before it.

```python
@dataclass(kw_only=True, slots=True, repr=False)
class ModeEvent(PageEvent):
    """A tool of the map editor changed, such as to Identify or Add Marker."""

    # trail: mode - a mode belongs to one map model in one page and is never synchronized.
    is_shared: ClassVar[bool] = False
    selectable: ClassVar[tuple[str, ...]] = (
        'verb', 'mode', 'previous', 'frontend_id', 'focus_seq',
    )
    # display name of each mode
    labels: ClassVar[dict[str, str]] = {
        'panning': 'none',
        'identifying': 'Identify',
        'marking': 'Add Marker',
        'drawing': 'Edit Features',
    }

    @property
    def mode(self) -> str | None:
        out = (
            self.data
            .get('object', {})
            .get('mode')
        )
        return out
```

[`ModeV1Event`](../../src/trail/jupyter/event.py#L521) extends it and only names the schema `https://schema.jupytergis.org/events/mode/v1`, so a `mode/v2` would get a sibling class. **Observed:** a `mode/v2` message was recorded as a plain `JupyterEvent` with only the common rows and `verb`.

| Name | Reads | Meaning |
|---|---|---|
| [`ModeEvent.is_shared`](../../src/trail/jupyter/event.py#L478) | class attribute | `False`: a mode belongs to one map model in one page and is never synchronized, as the comment says. Nothing in `src` reads it. |
| [`ModeEvent.selectable`](../../src/trail/jupyter/event.py#L479) | class attribute | The names the console offers as completions. |
| [`labels`](../../src/trail/jupyter/event.py#L483) | class attribute | The name shown for each mode. |
| [`mode`](../../src/trail/jupyter/event.py#L491) | `object.mode` | The tool now active, as sent. |
| [`ModeEvent.previous`](../../src/trail/jupyter/event.py#L500) | `object.previous` | The tool before the change, as sent. |

The plan chose the field name `mode` because the document record's `tool` names the application, which is always `JupyterGIS`. It chose the labels to match the toolbar, using `none` for `panning`, and it marked mode records unshared for the duplicate handling that the integration plan defers.

#### Printing

[`ModeEvent._repr_items`](../../src/trail/jupyter/event.py#L508) yields the rows the console prints:

```python
def _repr_items(self) -> Iterator[tuple[str, object]]:
    yield from JupyterEvent._repr_items(self)
    for name in ('mode', 'previous'):
        value = getattr(self, name)
        if value is not None:
            yield name, self.labels.get(value, value)
    for name in ('frontend_id', 'focus_seq'):
        value = getattr(self, name)
        if value is not None:
            yield name, value
```

The rows follow the [`JupyterEvent` rows](../../src/trail/jupyter/event.py#L107): `id`, `timestamp`, `schema_id`, `server`, [`src_path`](../../src/trail/jupyter/event.py#L58) and `verb`. A mode with no name in `labels` is printed as sent. A missing `mode`, `previous`, `frontend_id` or `focus_seq` leaves its row out. The console adds an `entry` row last, through [`Renderer.parameters`](../../src/trail/cli/render.py#L111). [`Renderer.verb`](../../src/trail/cli/render.py#L254) returns the verb for the heading, and [`VERB_STYLES`](../../src/trail/cli/theme.py#L46) has no entry for `changed`, so the heading uses the default `event` style. [`test_mode_labels_and_selection`](../../tests/jupyter/test_events.py#L391) checks the labels, the `Renderer.verb` and the selections.

#### What each record looks like

**Observed results.** A scenario run replayed [`baseline.json`](../../tests/jupyter/fixtures/baseline.json) through the real consumer. Every message was recorded, and 10 of the records were `ModeV1Event`. The listings are `print(trail.events.select('cls=ModeEvent'))`, with paths shortened to the project root as the console does, and without the `id`, `timestamp`, `schema_id` and `server` rows. The console would add an `entry` row to each record.

A tool picked, and the return to panning:

```text
0. ModeV1Event
    src_path: maps/city.jGIS
    verb: changed
    mode: Add Marker
    previous: none
    frontend_id: 34d02c8a-db83-4f58-b6d4-dff696c77bcb
    focus_seq: 6
1. ModeV1Event
    src_path: maps/city.jGIS
    verb: changed
    mode: none
    previous: Add Marker
    frontend_id: 34d02c8a-db83-4f58-b6d4-dff696c77bcb
    focus_seq: 7
```

The other tools gave `mode: Identify` three times, with `previous: none` each time, and `mode: Edit Features` once. A scenario run also sent messages that Trail has no name for:

```text
0. ModeV1Event
    src_path: map.jGIS
    verb: changed
    mode: measuring
    previous: none
1. ModeV1Event
    src_path: map.jGIS
    verb: changed
    mode: Identify
2. ModeV1Event
    src_path: map.jGIS
    verb: changed
3. JupyterEvent
    schema_id: https://schema.jupytergis.org/events/mode/v2
    src_path: map.jGIS
    verb: changed
```

The first message had the mode `measuring` and the previous mode `panning`. It printed the unknown mode as sent and `panning` as `none`. The second had no `previous`, and the third had neither mode, so their rows are missing. The fourth was a `mode/v2` message, recorded as a plain `JupyterEvent`. Records of earlier recorded sessions, as the console printed them, are in the event coverage report's [map tools](../../scratch/11-12-2-event-coverage.md#14-map-tools).

#### Selecting

A query can read the names in `selectable`, and any other attribute of the record, through [`Select.held`](../../src/trail/select.py#L264). `cls=ModeEvent` also selects records of later schema versions, because [`Select.equals`](../../src/trail/select.py#L241) matches a class by the names of its bases.

| Query | Selects | Observed on the captured session |
|---|---|---|
| `events cls=ModeEvent` | every tool record | 10 records |
| `events cls=ModeEvent mode=identifying` | changes to Identify | 3 records |
| `events cls=ModeEvent mode=Identify` | the printed name | 0 records |
| `events cls=ModeEvent previous=panning` | changes made from panning | 5 records |
| `events cls=ModeEvent focus_seq>10` | later changes of any page | 2 records |

A query uses the raw value, so `mode=identifying` selects the records that print `Identify`, and `mode=Identify` selects none. [`test_mode_labels_and_selection`](../../tests/jupyter/test_events.py#L391) checks that `mode=identifying` and `previous=identifying` select the right records.

### Storage and replay ([TLDR](#storage-and-replay-deep-dive))

This section covers what the log keeps for a tool record and how it is rebuilt. The writing and reading paths are shared with the other families ([Layer events](11-15-1-layer-event.md#storage-and-replay-tldr)).

[`Event.to_record`](../../src/trail/event.py#L169) writes every dataclass field except the entry. `ModeEvent` has none of its own. **Observed:** a stored record has these keys, in this order: `id`, `timestamp`, `is_accepted`, `schema_id`, `data`, `server`, `src_path`, `cls`, `entry`, `entry_type`. `data` holds the message without its `schema_id`: `subject`, `verb`, `time`, `frontend_id`, `focus_seq` and `object`. The toolbar names are computed when the record prints. The record's time is the moment Trail received the message, and the page's `time` stays in `data`.

[`JSONL.read`](../../src/trail/event.py#L625) replays the log, and [`Event.from_record`](../../src/trail/event.py#L128) finds the class by its stored name. **Observed results.** Reopening the project in the scenario run rebuilt all 10 tool records to equal records, and `Jupyter.focus` gave the same timeline. [`test_baseline_history_replays`](../../tests/jupyter/test_events.py#L411) does this for the captured session and also checks that the timeline of the page has the records in order. [`test_schema_registry_and_roundtrip`](../../tests/jupyter/test_events.py#L37) does it for a `mode/v1` message.

### Limits today ([TLDR](#limits-today-deep-dive))

These are the limits of today's tool records. Each is labeled by how it was established. The last part lists where the code differs from the [event coverage report](../../scratch/11-12-2-event-coverage.md) and the plan.

**Current behavior: panning prints as `none`.** The label comes from the plan, which chose `none` for `panning`. A change to a tool shows `previous: none`, and a return to panning shows `mode: none`. **Observed.** The report reads `previous: none` as "the one before it", although panning is the default mode and not the absence of a tool.

**Current behavior: a reset looks like a choice.** The message has no reason. A tool that the editor resets, and one you leave with Escape, give the same record. The schema description says automatic resets are reported the same way.

**Current behavior: closing a map ends no mode.** The emitter disconnects when the model is disposed and sends nothing. **Deduction** from [`wireModeEvents`][ef-wire-mode]. It was not run.

**Current behavior: a tool record belongs to one page and one map.** The mode is not synchronized, so collaborators never see it. A map shown in a notebook has its own model. The plan expected its tool changes to be reported, with no focus changes to order them against. **Not checked:** whether the emitter wires a map in a notebook.

**Current behavior: the printed name is not the value to query.** **Observed**, as shown under [selecting](#selecting).

**Current behavior: unknown, missing and future values are recorded as they are.** **Observed**, as shown under [what each record looks like](#what-each-record-looks-like). Trail does not check `mode` or `previous`. A mode that the editor adds later prints as sent.

**Current behavior: a failed emit leaves no trace.** The browser's `events.emit` call can be rejected. The rejection reaches only the browser console ([Silent failures][ef-events-md-silent]).

**Current behavior: a record without a counter comes last in a page's timeline.** [`Jupyter.focus`](../../src/trail/jupyter/jupyter.py#L101) sorts it by time after all numbered records.

#### Differences from the event coverage report and the plan

The [event coverage report](../../scratch/11-12-2-event-coverage.md) says in its section on map tools that each record shows the tool picked and the one before it. The code agrees with the report's record, field by field. These details are not in the report:

- `none` stands for panning, which is a mode of the map like the others.
- A tool that is active when a map closes ends without a record.
- The `time` field is stored and not used.
- The message cannot tell a reset from a choice.

The plan for the remaining events ([11-4-2](../plan/11-4-2-remaining-baseline-events.md)) says which design this code follows. The code matches it on the message fields, the shared page counter, the pending focus change that is published first, the toolbar labels with `none`, the field name `mode`, the unshared flag, and the inclusion in `Jupyter.focus`. The plan's limit about maps in notebooks is the one point that this note did not check.

### How this was checked ([TLDR](#how-this-was-checked-deep-dive))

**Checkouts.**

- **Trail:** commit `25d97ba` on `stable`. `stable` is two commits ahead of `origin/stable`, so Trail links are relative to the working tree. The working tree held uncommitted docstrings and comments in `src/trail/jupyter`, which another session added while these notes were written. No code changed. Links and code excerpts were checked against those files at 15:33 on 15 November 2026, and line anchors move if the files change.
- **JupyterGIS emitter:** commit [`932c09ae`](https://github.com/hodczak/prototype-jupytergis-eventfeed/commit/932c09aef44cfce398c3c4db41af8e56d552d9fe) of `hodczak/prototype-jupytergis-eventfeed`, branch `stable`, equal to `origin/stable`. Its links are pinned to that commit.

**Observed results.**

- **Trail tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/jupyter/test_kernels.py tests/jupyter/test_consumer.py tests/jupyter/test_events.py tests/jupyter/test_subscription.py` gave 76 passed and 1 skipped. The skipped test is [`test_schema_copies_match_installed_eventfeed`](../../tests/jupyter/test_events.py#L289), because `jupytergis_core` is not installed in that environment.
- **Emitter tests.** `npx jest src/eventFeed --ci --coverage=false` in `python/jupytergis_core` of the clone gave 6 suites and 49 tests passed. The clone stayed clean.
- **Schema copy.** A manual `diff` of `tests/jupyter/schemas/mode_v1.yaml` against the emitter's [`mode_v1.yaml`][ef-schema-mode] showed no difference.
- **Scenario runs.** Throwaway scripts built a project in a scratch folder with `.ontrail` holding `./` and `*.jgis`. They built a [`Server`](../../src/trail/jupyter/server.py#L233) as the [`server` fixture](../../tests/jupyter/conftest.py#L130) does and sent messages through `Consumer.receive`: the captured session [`baseline.json`](../../tests/jupyter/fixtures/baseline.json), and tool messages with an unknown mode, a missing `previous`, no mode and a `mode/v2` schema id. They then reopened the project and ran the selections above. The scripts were not kept in the repo.

**Not checked.** No browser was run. The emitter's behavior comes from its source, its unit tests and its docs. The tool changes of a map shown in a notebook, the closing of a map with a tool active, and the automatic resets were not run.

[ef-events-md-mode]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/docs/contributor_guide/explanation/events.md#mode-changes
[ef-events-md-silent]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/docs/contributor_guide/explanation/events.md#silent-failures
[ef-wire-mode]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/mode.ts#L18
[ef-page-order]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/mode.ts#L9
[ef-focus]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/focus.ts#L22
[ef-focus-order]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/focus.ts#L68
[ef-focus-flush]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/focus.ts#L76
[ef-focus-schedule]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/focus.ts#L103
[ef-modes-type]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/schema/src/types.ts#L62
[ef-initial-mode]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/schema/src/model.ts#L1473
[ef-toggle-mode]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/schema/src/model.ts#L1244
[ef-set-mode]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/schema/src/model.ts#L1252
[ef-schema-mode]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/jupytergis_core/event_schemas/mode_v1.yaml
[ef-register]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/jupytergis_core/events.py#L14
[ef-test-mode-change]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/mode.spec.ts#L62
[ef-test-mode-initial]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/mode.spec.ts#L92
[ef-test-mode-disposed]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/mode.spec.ts#L99
[ef-test-mode-order]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/mode.spec.ts#L107
