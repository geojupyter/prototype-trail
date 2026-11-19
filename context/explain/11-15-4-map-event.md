# Map events

## TLDR

Unlabeled statements describe the current code, read at the checkouts in [How this was checked](#how-this-was-checked-tldr). **Observed** marks output from a run. **Deduction** marks my inference. **Planned** marks work that a plan or a `trail-todo` comment describes and the code does not do. A reason is credited to the plan, schema, docstring or comment that states it. Links to Trail code point at the working tree. Links to JupyterGIS code are pinned to a commit.

### What happens when you use a map ([Deep Dive](#what-happens-when-you-use-a-map-tldr))

You create a map, open one, switch to another tab, come back to the browser, pan, zoom, or close the map. Trail records each as one record. A [`DocumentV1Event`](../../src/trail/jupyter/event.py#L195) has the verb `created`, `opened`, `closed`, `activated` or `deactivated`. A [`ViewportV1Event`](../../src/trail/jupyter/event.py#L707) has the verb `moved`. Each record names the map file it is about. **Observed:** a captured session of 61 messages, replayed through Trail's consumer, gave 27 document records and 2 viewport records ([what each record looks like](#what-each-record-looks-like)).

These terms are needed:

- **JupyterLab** is the browser workspace that holds notebooks and maps. One loaded browser page is one **page**. Each page has its own random [`frontend_id`](../../src/trail/jupyter/event.py#L134).
- **JupyterGIS** is the map editor inside JupyterLab. A map is a `.jGIS` file, which Trail tracks as an asset.
- A **view** is one tab that shows a map. A map can be open in several views. The views of one map in one page share one in-memory copy of the map, which JupyterGIS calls its model.
- **Jupyter Server** is the process behind JupyterLab. It serves the files and relays JupyterGIS's messages to anything that subscribes.

Three steps stay distinct:

1. **You act.** You use a map in the editor.
2. **JupyterGIS reports.** The page sends a `document/v1` message when a map opens, is created or closes ([opening, creating and closing a map](#opening-creating-and-closing-a-map-tldr)), and when the page's focus moves to or from a map ([which map has focus](#which-map-has-focus-tldr)). It sends a `viewport/v1` message when a pan or zoom ends ([pans and zooms](#pans-and-zooms-tldr)). Jupyter Server checks each message and relays it.
3. **Trail records.** Trail subscribes to the server. For each message it finds the map's asset ([finding the map](#finding-the-map-tldr)), builds the record ([the record classes](#the-record-classes-tldr)) and appends it to its event log ([storage and replay](#storage-and-replay-tldr)).

| Piece | Where | Role |
|---|---|---|
| emitters, [`wireDocumentEvents`][ef-wire-document], [`DocumentFocus`][ef-focus], [`wireViewportEvents`][ef-wire-viewport] | JupyterGIS, in the browser | Send the messages. |
| [`Consumer`](../../src/trail/jupyter/consumer.py#L74) | `trail.jupyter.consumer`, reached as [`trail.jupyter.session.consumer`](../../src/trail/jupyter/session.py#L37) | Finds the map's asset, builds the record and appends it ([finding the map](#finding-the-map-tldr)). |
| [`DocumentEvent`](../../src/trail/jupyter/event.py#L145), [`ViewportEvent`](../../src/trail/jupyter/event.py#L661) | `trail.jupyter.event` | The records ([the record classes](#the-record-classes-tldr)). |
| [`Jupyter.focus`](../../src/trail/jupyter/jupyter.py#L101) | `trail.jupyter` | Puts one page's focus and tool records back in the order the page sent them ([which map has focus](#which-map-has-focus-tldr)). |

Skeleton of the Trail side, condensed from the source and not runnable as written:

```python
# trail/jupyter/consumer.py
class Consumer(Node):
    def route(self, server, record) -> None:
        found = self.resolve(server, data)                    # the map's asset, or a reason to skip
        event_cls = JupyterEvent.registry.get(record.schema_id, JupyterEvent)
        event = event_cls(schema_id=..., data=data, ...)      # DocumentV1Event or ViewportV1Event
        event.entry = found.asset
        event.apply(self._trail)

# trail/jupyter/jupyter.py
class Jupyter(Node):
    def focus(self, *, server: str, frontend_id: str) -> Events: ...
```

### Opening, creating and closing a map ([Deep Dive](#opening-creating-and-closing-a-map-tldr))

The first time a page shows a map, the emitter sends `document/v1` with the verb `opened`. If JupyterGIS had just created that map file, it sends `created` instead. JupyterGIS registers a new map's path before it opens the map, and the emitter consumes the registration. When the view closes, it sends `closed`. A map written by a script, exported from QGIS or duplicated in the file browser is not registered, so it is reported as `opened`. A map without a saved path sends a message with a null path, which Trail drops.

```ts
// python/jupytergis_core/src/eventFeed/plugin.ts
const verb = path && newDocuments.consume(path) ? 'created' : 'opened';
emit({ schema_id: DOCUMENT_SCHEMA, version: '1', data: { subject: clientId, verb, object: { document_path: path } } });
widget.disposed.connect(() => emit({ ..., data: { subject: clientId, verb: 'closed', ... } }));
```

### Which map has focus ([Deep Dive](#which-map-has-focus-tldr))

The emitter reports which map the page is showing. A map is active when it is the current tab of the main area in a page that is visible and has focus. When that changes, the emitter sends `deactivated` for the old map and `activated` for the new one. The same message says why, such as `document_switch` or `window_blurred`. Each page numbers its focus messages with a counter, [`focus_seq`](../../src/trail/jupyter/event.py#L139), and sends its own `frontend_id`, so Trail can put them back in order. [`Jupyter.focus`](../../src/trail/jupyter/jupyter.py#L101) does that.

```ts
// python/jupytergis_core/src/eventFeed/focus.ts
private _publish(widget, verb: 'activated' | 'deactivated', reason: FocusReason): void {
  this._options.emit({ schema_id: '.../document/v1', data: { subject, verb, time, frontend_id, focus_seq: ++this._sequence,
                       object: { document_path, widget_id: widget.id, reason } } });
}
```

### Pans and zooms ([Deep Dive](#pans-and-zooms-tldr))

When a pan or zoom made by the user in this page ends, the emitter sends one `viewport/v1` message with the view's center, zoom and extent after the move. It sends nothing while the move is going on, and nothing for a move that the map made itself, such as its first view or following a collaborator. The message does not carry a page id, a counter or a time.

```ts
// python/jupytergis_core/src/eventFeed/viewport.ts
model.viewportMoved.connect((_, { viewport }) => {
  emit({ schema_id: '.../viewport/v1', data: { subject, verb: 'moved', object: { document_path, new: viewport } } });
});
```

### Finding the map ([Deep Dive](#finding-the-map-tldr))

[`Consumer.route`](../../src/trail/jupyter/consumer.py#L166) asks [`Consumer.resolve`](../../src/trail/jupyter/consumer.py#L238) for the map's asset. A message with a null path, a path outside the server's root, or a map outside the project is dropped. A message about an untracked map or a missing file is kept as skipped. A map that exists, lies in the project and matches a rule of `.ontrail` is tracked at that moment, so its asset comes before its first record. **Observed:** the first `created` message for a new `.jGIS` file gave an [`AddAssetEvent`](../../src/trail/event.py#L193) with the rule `*.jgis` and then the `DocumentV1Event`.

```python
# trail/jupyter/consumer.py
def resolve(self, server, data) -> Map:
    if not document_path or PurePosixPath(document_path).name == 'unsaved_project':
        return Map(reason='unsaved')
    ...
    if asset is None:
        trail.assets.scan(path)          # a new map that .ontrail takes is tracked here
        asset = trail.assets.get(path)
```

### The record classes ([Deep Dive](#the-record-classes-tldr))

[`Event`](../../src/trail/event.py#L59) → [`JupyterEvent`](../../src/trail/jupyter/event.py#L36) → [`PageEvent`](../../src/trail/jupyter/event.py#L127) → `DocumentEvent` → `DocumentV1Event` holds the document records. `PageEvent` adds the page's `frontend_id` and `focus_seq`. `DocumentEvent` adds the focus flag, the widget and the reason. A viewport record is a `JupyterEvent` → `ViewportEvent` → `ViewportV1Event` with the center, zoom and extent. A record prints only the rows it has: an `opened` record prints the verb, and an `activated` record also prints the page, the counter, the widget and the reason.

```python
# trail/jupyter/event.py
class PageEvent(JupyterEvent):
    @property
    def frontend_id(self) -> str | None: ...
    @property
    def focus_seq(self) -> int | None: ...

class DocumentEvent(PageEvent):
    @property
    def is_focus(self) -> bool: ...        # activated or deactivated

class ViewportEvent(JupyterEvent):
    @property
    def zoom(self) -> float | None: ...
```

### Storage and replay ([Deep Dive](#storage-and-replay-tldr))

Each record is one JSON line in the log. The line holds the message `data`, `schema_id`, `server`, [`src_path`](../../src/trail/jupyter/event.py#L58), the record's `id` and `timestamp`, and the map's asset id. The rows are computed from `data` when the record is read back. The record's time is the moment Trail received the message. The page's own `time`, which focus messages carry, is kept in `data` and is not used. **Observed:** after reopening the project, all 29 map records of a test run rebuilt to equal records.

```python
# trail/event.py
class Event:
    def to_record(self) -> dict: ...                  # one JSON line of the log
    @classmethod
    def from_record(cls, /, trail, resolved_entry=None, **record) -> Event: ...
```

### Limits today ([Deep Dive](#limits-today-tldr))

**Current behavior:**

- **`closed` follows the first view of a map.** **Deduction:** if the map is open in two views and the first one closes, the message is sent then, and closing the second sends nothing ([opening, creating and closing a map](#opening-creating-and-closing-a-map-tldr)).
- **The time of a record is the arrival time.** The page's `time` is stored and not used ([storage and replay](#storage-and-replay-tldr)).
- **A viewport record has no page and no order.** It is not part of a page's timeline ([pans and zooms](#pans-and-zooms-tldr)).
- **A map without a saved path leaves no record** ([finding the map](#finding-the-map-tldr)).
- **Focus covers map tabs only**, not a map shown inside a notebook ([which map has focus](#which-map-has-focus-tldr)).

### How this was checked ([Deep Dive](#how-this-was-checked-tldr))

The consumer and event tests pass: 76 passed and 1 skipped, together with the kernel and subscription tests. The emitter's tests pass: 49 in the event feed, 7 in the schema package and 13 for the viewport tracker. **Observed:** a scenario run replayed a captured session of 61 messages through the real consumer, and further runs sent the messages that Trail drops or skips. No browser was run. Everything about the emitter comes from reading its source, its tests and its docs.

## Deep Dive

### What happens when you use a map ([TLDR](#what-happens-when-you-use-a-map-deep-dive))

This section lists every stage between an action in the map editor and a line in the log. Later sections cover each stage in depth.

| # | Where | What happens | What can stop it |
|---|---|---|---|
| 1 | JupyterLab, [`eventFeedPlugin`][ef-plugin] | Wires each map view that the tracker holds, now or later. | A shell that has no signal for its current widget gets no focus events. |
| 2 | Emitters in the page | Build a `document/v1` or `viewport/v1` message and send it with `events.emit`. | A move that the map made itself sends no viewport message. |
| 3 | Jupyter Server, `POST /api/events` | Validates the message against the schema registered under its `schema_id` and puts it on the event bus. | A rejected message gives a `400` that only the browser console shows. |
| 4 | Trail, [`subscribe`](../../src/trail/jupyter/subscription.py#L14) | Reads the server's WebSocket feed at [`Server.endpoint`](../../src/trail/jupyter/server.py#L288) and passes each text message to the consumer. | A refused, unsupported or dropped connection. |
| 5 | [`Consumer.receive`](../../src/trail/jupyter/consumer.py#L96) | Parses the message and keeps JupyterGIS messages. | Invalid JSON, or another schema. |
| 6 | [`Consumer.route`](../../src/trail/jupyter/consumer.py#L166) and [`resolve`](../../src/trail/jupyter/consumer.py#L238) | Finds the map's asset and builds the record. | Unsaved, outside root, outside project, offtrailed, file missing. |
| 7 | [`JupyterEvent.apply`](../../src/trail/jupyter/event.py#L114) | Appends the record to `trail.events`, which writes one line to the log. | |

The Trail side runs in a session: [`JupyterSession`](../../src/trail/jupyter/session.py#L21) creates the consumer as [`session.consumer`](../../src/trail/jupyter/session.py#L37). The [`Consumer`](../../src/trail/jupyter/consumer.py#L74) also appends a [`Message`](../../src/trail/jupyter/message.py#L25) wrapper for every message it keeps to [`session.messages`](../../src/trail/jupyter/session.py#L49). The wrapper records the outcome, the reason a message was skipped and the id of the record it became.

The classes [`Event`](../../src/trail/event.py#L59) and [`JupyterEvent`](../../src/trail/jupyter/event.py#L36), the consumer's checks and the log are shared with the other event families. [Layer events](11-15-1-layer-event.md#making-the-record-tldr) explains the consumer and [its record classes](11-15-1-layer-event.md#the-record-classes-tldr). This note covers what is specific to maps.

### Opening, creating and closing a map ([TLDR](#opening-creating-and-closing-a-map-deep-dive))

This section covers the three lifecycle verbs. The emitter lives in JupyterGIS, not in Trail. Its code is in the clone `../eventfeed`, a prototype that adds the event feed to JupyterGIS.

#### One wiring for each model

JupyterGIS keeps a map's content in a model. Views of the same map in the same page share that model. [`eventFeedPlugin`][ef-plugin] calls a function for each map widget that the tracker holds, and for each one added later. The function wires focus for every view and everything else once for each model:

```ts
    const wireJupyterGISEvents = (widget: IJupyterGISWidget): void => {
      // trail: focus - each view needs focus tracking even when views share a model.
      focus.add(widget);

      // Two widgets can share the same model. We only want to connect these
      // handlers once per document.
      if (wiredModels.has(widget.model)) {
        return;
      }

      wiredModels.add(widget.model);

      const eventWiringProps: IEventWiringProps = {
        widget: widget,
        clientId: String(widget.model.getClientId()),
        emit: emit,
      };

      wireDocumentEvents(eventWiringProps);
      wireModeEvents(widget.model, focus, emit);
```

[`wiredModels`][ef-wired-models] is a `WeakSet` of models. The lifecycle messages use the first widget of a model. The `clientId` becomes the message's `subject`: the client id of this copy of the map in the page. [`wireViewportEvents`][ef-wire-viewport] is wired later, after the model's first synchronization ([pans and zooms](#pans-and-zooms-tldr)).

#### `wireDocumentEvents`

[`wireDocumentEvents`][ef-wire-document] sends the messages for opening, creating and closing:

```ts
export const wireDocumentEvents = ({
  widget,
  clientId,
  emit,
}: IEventWiringProps): void => {
  const DOCUMENT_SCHEMA = 'https://schema.jupytergis.org/events/document/v1';
  const path = widget.model.filePath || null;
  // trail: created - the first view of a map JupyterGIS just made reports created, not opened.
  const verb = path && newDocuments.consume(path) ? 'created' : 'opened';

  emit({
    schema_id: DOCUMENT_SCHEMA,
    version: '1',
    data: {
      subject: clientId,
      verb,
      object: { document_path: path },
    },
  });

  widget.disposed.connect(() => {
    emit({
      schema_id: DOCUMENT_SCHEMA,
      version: '1',
      data: {
        subject: clientId,
        verb: 'closed',
        object: { document_path: widget.model.filePath || null },
      },
    });
  });
};
```

The path is the model's file path, or `null` for a map that is not saved. `closed` is sent when the widget that `wireDocumentEvents` received is disposed, with the model's path at that moment. **Deduction:** the call happens once for each model, with the first widget. If a second view of the same map is open, closing the first sends `closed` while the map stays open in the second view, and closing the second sends nothing. This was not run, and the emitter's tests do not cover it.

#### `created`

The `created` verb tells a new map from an existing one. The emitter cannot tell by itself. It sees a view open, and the code that made the file is elsewhere. The source comment says so: the creating code knows a map is new, and the event feed only sees it open. [`NewDocuments`][ef-new-documents-class] is a small registry in the shared package `@jupytergis/schema`, and `newDocuments` is the page's one instance:

```ts
  register(path: string): void {
    this._paths.set(normalize(path), this._now() + this._ttl);
  }
  consume(path: string): boolean {
    const key = normalize(path);
    const expiry = this._paths.get(key);
    this._paths.delete(key);
    return expiry !== undefined && expiry >= this._now();
  }
```

The code that creates a map calls [`register`][ef-new-documents-register] before it opens the map. Two places do: the new-map command of the launcher, the palette and the file browser ([`plugins.ts`][ef-create-register]), and the notebook renderer when it creates a map file that Python named ([`notebookrenderer.ts`][ef-renderer-register]). The command calls [`discard`][ef-new-documents-discard] if the open fails. [`consume`][ef-new-documents-consume] removes the registration and answers true only if it has not expired. A registration expires after 30 seconds, so an abandoned one cannot mark a later open of the same path. A path is compared without a drive prefix such as `RTC:` and without leading slashes.

The emitter's docs say what is left out: a map written by a script, exported by QGIS, or duplicated in the file browser is reported as `opened` ([Created maps][ef-events-md-created]). The tests are [`consumes a registration once`][ef-test-consume] and its neighbors for the registry, and [`reports the first view of a mockRegistered new map as created`][ef-test-created] for the emitter.

**Documented rationale, and a difference from the plan.** The plan for the remaining events ([Telling map creation from opening a map](../plan/11-4-2-remaining-baseline-events.md)) found that Jupyter Server cannot tell Trail either: its contents service lists a `create` action but version 2.21.1 never sends it, so a new map reaches Trail as two saves and an `opened`. The plan proposed to keep the verb `opened` and add a Boolean `object.created`. It rejected a separate `created` verb, because that would send two messages for one action. The code does neither. It sends `created` in place of `opened`, one message for one action, and [`document_v1.yaml`][ef-schema-document] lists `created` among the verbs with the comment that it is sent in place of `opened`. The objection in the plan does not apply to that design. The event coverage report lists the `created` verb among the changes to propose upstream.

#### The messages

The schema is [`document_v1.yaml`][ef-schema-document]. Trail keeps a copy for its tests as [`tests/jupyter/schemas/document_v1.yaml`](../../tests/jupyter/schemas/document_v1.yaml). **Observed:** a manual `diff` found no difference. The schema requires [`JupyterEvent.verb`](../../src/trail/jupyter/event.py#L92) and `object`. It lists five verbs. `time`, [`frontend_id`](../../src/trail/jupyter/event.py#L134) and [`focus_seq`](../../src/trail/jupyter/event.py#L139) are optional and are sent only with focus messages.

| Field | Content |
|---|---|
| `verb` | `created`, `opened`, `closed`, `activated` or `deactivated`. |
| `subject` | The client id of the map copy that sent the message. |
| `object.document_path` | The `.jGIS` path relative to the server root, or `null`. |
| `object.widget_id`, `object.reason`, `frontend_id`, `focus_seq`, `time` | Focus messages only ([which map has focus](#which-map-has-focus-tldr)). |

#### Delivery to Trail

The browser sends the message with `events.emit`, which posts it to Jupyter Server. The server validates it against the schema registered under its `schema_id` by [`register_event_schemas`][ef-register]. It rejects an unregistered id with a `400` that reaches only the browser console ([Silent failures][ef-events-md-silent]).

### Which map has focus ([TLDR](#which-map-has-focus-deep-dive))

This section covers the `activated` and `deactivated` messages and how Trail puts them in order. The emitter's docs say that a map is active when it is the current main-area widget in a visible page that has focus, and that moving keyboard focus to a sidebar or dialog keeps the selected map context ([Document focus][ef-events-md-focus]).

#### `DocumentFocus`

[`DocumentFocus`][ef-focus] is one object for the whole page. The plugin creates it once, and every map view is added to it with [`add`][ef-focus-add]. It listens to the shell's current-widget signal, to the browser window's `focus` and `blur`, and to the page's visibility. Several of these signals can fire for one change, so [`_schedule`][ef-focus-schedule] records the reason and waits for one microtask. One state change then gives one comparison.

[`_reconcile`][ef-focus-reconcile] decides which map is active:

```ts
  private _reconcile(): void {
    const { shell, tracker } = this._options;
    const current = shell.currentWidget as IJupyterGISWidget | null;
    let next: IJupyterGISWidget | null = null;
    if (
      document.visibilityState === 'visible' &&
      document.hasFocus() &&
      current &&
      !current.isDisposed &&
      tracker.has(current) &&
      this._widgets.has(current)
    ) {
      next = current;
    }

    let reason = this._reason;
    if (this._initial) {
      reason = 'initial_state';
      this._initial = false;
    }
    if (next === this._active) {
      return;
    }
    if (this._active) {
      this._publish(this._active, 'deactivated', reason);
    }
    this._active = next;
    if (next) {
      this._publish(next, 'activated', reason);
    }
  }
```

The page must be visible and have focus, and the current widget must be a standalone map that was added. If the map that is active differs from the last one, the emitter publishes `deactivated` for the old one and `activated` for the new one. Nothing is sent when nothing changed. The first comparison always has the reason `initial_state`. A map shown inside a notebook is not treated as a map of the tracker, and the emitter's docs say its focus is not reported. The tests cover [a notebook containing a map](https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/focus.spec.ts#L235) and [coalesced signals](https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/focus.spec.ts#L143).

| `reason` | Cause |
|---|---|
| `initial_state` | The first comparison after the page loaded. |
| `document_switch` | The current widget changed, or a view was added or removed. |
| `window_focused`, `window_blurred` | The browser window gained or lost focus. |
| `page_visible`, `page_hidden` | The page became visible or hidden. |
| `widget_closed` | The active map's view was disposed. |

The reasons are the type [`FocusReason`][ef-focus-reason]. The schema says a reason is an observed cause and does not name another application. The docs add that the events identify a page losing focus and not the application that received it, and that browser chrome can also cause a loss of focus.

When the active view is disposed, the listener that [`add`][ef-focus-add] connected publishes `deactivated` with the reason `widget_closed` before the lifecycle listener of `wireDocumentEvents` sends `closed`. The source comment names the order: deactivate before the lifecycle listener emits closed. **Observed:** in the captured session the last two document records of the map are `deactivated` with `widget_closed` and then `closed`.

#### `_publish`, the page and the counter

[`_publish`][ef-focus-publish] sends the message:

```ts
  private _publish(
    widget: IJupyterGISWidget,
    verb: 'activated' | 'deactivated',
    reason: FocusReason,
  ): void {
    this._options.emit({
      schema_id: 'https://schema.jupytergis.org/events/document/v1',
      version: '1',
      data: {
        subject: String(widget.model.getClientId()),
        verb,
        time: new Date().toISOString(),
        frontend_id: this._frontendId,
        // trail: focus - one page counter orders transitions between different maps.
        focus_seq: ++this._sequence,
        object: {
          document_path: widget.model.filePath || null,
          widget_id: widget.id,
          reason,
        },
      },
    });
  }
```

`frontend_id` is a random UUID made when the page loaded. It names a page, not a person. `focus_seq` rises by one for each focus message of the page, across all its maps. The two exist because POST requests can arrive in any order, and the page's own counter gives the true order. The schema describes the counter as increasing across all maps in the page. [`order`][ef-focus-order] numbers other messages of the page with the same counter. The tool messages of [Tool events](11-15-5-tool-event.md#what-the-browser-reports-tldr) use it, and it first publishes any pending focus change, so an activation gets a lower number than a tool change that it caused.

#### `Jupyter.focus`

[`Jupyter`](../../src/trail/jupyter/jupyter.py#L19) is `trail.jupyter`, the object that holds what Trail connects to. [`Jupyter.focus`](../../src/trail/jupyter/jupyter.py#L101) returns one page's records in the order the page sent them:

```python
def focus(
        self,
        *,
        server: str,
        frontend_id: str,
) -> Events:
    """
    The focus and tool events of one browser page, in the order the page sent them.
    """
    url = normalize_url(server)
    # trail: mode - tool changes share the focus counter of the page that sent them.
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
    ...
    # trail: focus - page order can differ from arrival order across document streams.
    events.sort(key=order)
    keys = [
        event.id
        for event in events
    ]
    out = self._trail.events.select.subset(keys)
    return out
```

The elided function `order` gives each record the key `(False, focus_seq, timestamp)`, or `(True, 0, timestamp)` if it has no integer counter, so a record without a counter comes last. The method returns an [`Events`](../../src/trail/event.py#L865) collection, so a caller can select further. The plan's reason is in the comment: the order on a page can differ from the order the messages arrived in.

**Observed results.** A scenario run replayed the captured session, at the checkouts in [How this was checked](#how-this-was-checked-tldr), and asked for the page `34d02c8a`. The listing shows the counter, the class and verb, the reason or mode, and the map:

| `focus_seq` | Record | Detail | Map |
|---|---|---|---|
| 1 | `DocumentV1Event` `activated` | `document_switch` | `untitled1.jGIS` |
| 2 | `DocumentV1Event` `deactivated` | `document_switch` | `untitled1.jGIS` |
| 3 | `DocumentV1Event` `activated` | `document_switch` | `maps/untitled1.jGIS` |
| 4 | `DocumentV1Event` `deactivated` | `document_switch` | `maps/untitled1.jGIS` |
| 5 | `DocumentV1Event` `activated` | `document_switch` | `maps/city.jGIS` |
| 6 | [`ModeV1Event`](../../src/trail/jupyter/event.py#L521) `changed` | `marking` | `maps/city.jGIS` |
| 7 | `ModeV1Event` `changed` | `panning` | `maps/city.jGIS` |
| 8 | `DocumentV1Event` `deactivated` | `document_switch` | `maps/city.jGIS` |
| 9 | `DocumentV1Event` `activated` | `document_switch` | `maps/city.jGIS` |
| 10 | `DocumentV1Event` `deactivated` | `widget_closed` | `maps/city.jGIS` |

No viewport record is in the timeline, because a viewport message carries no counter ([pans and zooms](#pans-and-zooms-tldr)). [`test_focus_records_immediately_and_orders_each_page`](../../tests/jupyter/test_consumer.py#L25) sends the counters 3, 1 and 2 in that order and checks the result is 1, 2, 3, also after reopening the project. [`test_mode_and_focus_share_page_order`](../../tests/jupyter/test_consumer.py#L440) mixes tool and focus messages.

### Pans and zooms ([TLDR](#pans-and-zooms-deep-dive))

This section covers when the emitter sends `viewport/v1`, and what it sends. The plan for prompt viewport events ([Plan: prompt viewport events](../plan/11-1-1-viewport-moveend.md)) replaced an earlier design that waited ten seconds after the last awareness update. The code now sends one message at the end of each local movement, with no extra delay.

#### Where a move comes from

[`ViewportMoves`][ef-viewport-moves] belongs to each rendered map. It wraps the `handleEvent` of the OpenLayers interactions for drag, wheel, pinch, double click and keyboard navigation, and listens for clicks on the zoom buttons. When one of them starts a movement, it remembers the view as it was. When OpenLayers reports `moveend`, [`_complete`][ef-viewport-complete] publishes the view as it is now, if it differs from the one remembered:

```ts
  private _complete = (): void => {
    const before = this._start;
    this._start = undefined;
    if (!before || this._suppressed || this.following() || this._disposed) {
      return;
    }
    const viewport = this._snapshot();
    if (viewport && !this._equal(before, viewport)) {
      this.publish(viewport);
    }
  };
```

A move that no user interaction started, so `before` is missing, publishes nothing. [`suppress`][ef-viewport-suppress] clears a pending start when the map replaces its view, changes its size, loses a pointer, or is moved by the application. The map follows a collaborator when `localState.remoteUser` is set, and it publishes nothing then ([`mainView.tsx`][ef-mainview-moves]). The tracker also keeps its suppression until the movement has settled, so a restore after following does not publish. [`_snapshot`][ef-viewport-snapshot] reads the center, zoom and extent from the view and refuses an incomplete or non-finite one. The plan gives the reason: the center alone is not enough, since a zoom keeps the center, and `moveend` has no reliable flag for user origin, so the tracker has to observe the interactions.

The map component calls `notifyViewportMoved` on the model, which emits a local `viewportMoved` signal. [`wireViewportEvents`][ef-wire-viewport] listens to it:

```ts
export function wireViewportEvents(
  model: IJupyterGISModel,
  emit: (request: Event.Request) => void,
): void {
  if (model.isDisposed) {
    return;
  }
  const publish = (_: IJupyterGISModel, { viewport }: IViewportMove): void => {
    if (model.isDisposed) {
      return;
    }
    emit({
      schema_id: 'https://schema.jupytergis.org/events/viewport/v1',
      version: '1',
      data: {
        subject: String(model.getClientId()),
        verb: 'moved',
        object: {
          document_path: model.filePath || null,
          new: viewport as unknown as JSONObject,
        },
      },
    });
  };
```

The function is wired once for each model, after the model's first synchronization, as the other layer and source emitters are. The signal also carries the view's id as `emitter`, and the message does not send it. The message has the center, zoom and extent after the move under `object.new`, and nothing about the move before it.

The emitter's docs say that drag, wheel and trackpad, touch, keyboard navigation and zoom controls are tracked, and that initialization, resizing, follow and unfollow restoration and application camera operations are not ([Viewport events report completed navigation][ef-events-md-viewport]). The tests are [`viewportMoves.spec.ts`][ef-test-moves] for the tracker, [`viewportMoved.spec.ts`][ef-test-moved] for the model, and [`viewport.spec.ts`][ef-test-viewport] for the emitter. **Observed:** all three suites pass ([How this was checked](#how-this-was-checked-tldr)).

#### The message

The schema is [`viewport_v1.yaml`][ef-schema-viewport], and Trail's copy [`tests/jupyter/schemas/viewport_v1.yaml`](../../tests/jupyter/schemas/viewport_v1.yaml) is the same. **Observed:** a manual `diff` found no difference. The fields are `verb`, which is always `moved`, `subject`, `object.document_path` and `object.new`. The schema describes `new` as the viewport after the move, in the map's projection: [`coordinates`](../../src/trail/jupyter/event.py#L668) with `x` and `y` for the center, `zoom` and `extent`, the bounding box of the visible area. It has no `time`, no `frontend_id` and no `focus_seq`.

The first captured viewport message is in [`baseline.json`](../../tests/jupyter/fixtures/baseline.json). Its center is `x: -13611034.139293559, y: 4561086.969892362` and its zoom is `13`. The coordinates are in the map's projection units.

### Finding the map ([TLDR](#finding-the-map-deep-dive))

This section covers what `Consumer.route` and `Consumer.resolve` do with a map message. They are the same for every JupyterGIS message, and [Layer events](11-15-1-layer-event.md#making-the-record-tldr) describes them in full. This section covers what matters for a map.

[`route`](../../src/trail/jupyter/consumer.py#L166) requires a string `verb`, an `object` mapping, and an `object.document_path` that is a string or `null`. It then calls [`resolve`](../../src/trail/jupyter/consumer.py#L238). The first test of `resolve` is the one that matters most for a new map:

```python
        # trail: focus - an unsaved map has no project association, even on a home-root server.
        if not document_path or PurePosixPath(document_path).name == 'unsaved_project':
            out = Map(reason='unsaved')
            return out
```

A map that has no path yet, or the placeholder `unsaved_project` that the notebook renderer uses for a map without a path, is dropped. [`Map`](../../src/trail/jupyter/consumer.py#L28) is the dataclass that `resolve` returns, with an `asset`, a `path` and a `reason`. The comment says why: an unsaved map has no project association, even on a server whose root is the home folder.

The reasons are in the table. `unsaved`, `outside root` and `outside project` are dropped without a `Message`. The others are kept with their reason:

| Reason | Cause | Kept as a `Message` |
|---|---|---|
| `unsaved` | `document_path` is `null` or empty, or the map is `unsaved_project`. | No |
| `outside root` | [`Server.locate`](../../src/trail/jupyter/server.py#L298) finds no path. | No |
| `outside project` | There is no asset and the map's folder is not in the project. | No |
| `offtrailed` | The path is untracked or ignored by `.offtrail`, or the asset was untracked. | Yes |
| `file missing` | The path is not a file. | Yes |

**Observed:** with one subject for each message, a `null` path and `maps/unsaved_project` gave `None` from `receive`. A path `../outside.jGIS` gave `None`. A missing file gave a skipped `Message` with the reason `file missing`, and a hidden `.hidden.jGIS` gave `offtrailed`. The counters were `unsaved: 2`, `outside root: 1`, `file missing: 1` and `offtrailed: 1`. [`test_unsaved_focus_is_not_attributed`](../../tests/jupyter/test_consumer.py#L68), [`test_paths_outside_server_root_are_rejected`](../../tests/jupyter/test_consumer.py#L80) and [`test_missing_and_offtrailed_maps_are_skipped`](../../tests/jupyter/test_consumer.py#L106) cover them.

#### A new map is tracked before its record

At the end of `resolve`, a map that is a file in the project but not yet an asset is scanned in, so that `.ontrail` can take it:

```python
        if asset is None:
            # a new map taken by ontrail is tracked before its event
            trail.assets.scan(path)
            asset = trail.assets.get(path)
```

The geospatial preset tracks `*.jgis`, so a created map becomes an asset when its first message arrives, and the record's `entry` is that asset. **Observed:** the log of a scenario run began with [`InitTrailEvent`](../../src/trail/event.py#L258), [`AddDirEvent`](../../src/trail/event.py#L325) and [`AddAssetEvent`](../../src/trail/event.py#L193) for the first map, and each later map's `AddAssetEvent` came before its first `DocumentV1Event`. [`test_new_matching_map_is_registered_before_its_event`](../../tests/jupyter/test_consumer.py#L123) and [`test_created_map_is_discovered_by_the_preset`](../../tests/jupyter/test_consumer.py#L425) cover it.

#### The subject link

`Consumer.resolve` remembers the asset that a `subject` resolved to, in [`Consumer.subjects`](../../src/trail/jupyter/consumer.py#L90). The comment there says a subject is one map view and that the link is kept across renames. **Observed:** a second message from subject `7` that named `other.jGIS` was recorded against `keep.jGIS`, the asset subject `7` had resolved to first. A real subject belongs to one map. A script that reuses a subject for several maps shows the effect, and the earlier [Layer events](11-15-1-layer-event.md#resolve-finding-the-map) note has the same observation. The source marks the design `trail-todo: document-identity`, to be replaced when room ids are available.

#### Duplicate deliveries

[`route`](../../src/trail/jupyter/consumer.py#L166) skips a message as a `duplicate delivery` if a recorded message has the same server, `stream` and `seq`. The map messages carry neither field: the schemas list none, and the emitter sends none. **Observed:** the captured session of 61 messages had no `stream` or `seq`, and all 61 were recorded. The check can fire only for a message that has both. [`test_only_stream_retries_are_deduplicated`](../../tests/jupyter/test_consumer.py#L49) builds such messages by hand.

### The record classes ([TLDR](#the-record-classes-deep-dive))

This section introduces the classes that hold a map record, from the page to the viewport. [`Event`](../../src/trail/event.py#L59) is the base of every record in `trail.events`. [`JupyterEvent`](../../src/trail/jupyter/event.py#L36) is the base of the records made from JupyterGIS messages. [Layer events](11-15-1-layer-event.md#the-record-classes-tldr) describes both.

#### `PageEvent`

[`PageEvent`](../../src/trail/jupyter/event.py#L127) lives in `trail.jupyter.event`. It is the base of the records that a page numbers, which are the document records and the tool records of [Tool events](11-15-5-tool-event.md).

```python
@dataclass(kw_only=True, slots=True, repr=False)
class PageEvent(JupyterEvent):
    """
    An event that a browser page numbers. frontend_id names the page and focus_seq is its counter.
    """

    # trail: mode - one browser page numbers its focus and mode records with one counter.
    @property
    def frontend_id(self) -> str | None:
        out = self.data.get('frontend_id')
        return out

    @property
    def focus_seq(self) -> int | None:
        out = self.data.get('focus_seq')
        return out
```

Both properties read the top level of `data`, not the `object`.

#### `DocumentEvent` and `DocumentV1Event`

[`DocumentEvent`](../../src/trail/jupyter/event.py#L145) is the document record. [`DocumentV1Event`](../../src/trail/jupyter/event.py#L195) extends it and only names the schema `https://schema.jupytergis.org/events/document/v1`, so a `document/v2` would get a sibling class. The first excerpt is the class with its first property:

```python
@dataclass(kw_only=True, slots=True, repr=False)
class DocumentEvent(PageEvent):
    """A map opened, created, closed, activated or deactivated in a page."""

    # trail: focus - each view's lifecycle and focus transitions are independent.
    is_shared: ClassVar[bool] = False
    selectable: ClassVar[tuple[str, ...]] = (
        'verb', 'is_focus', 'tool', 'frontend_id', 'focus_seq', 'widget_id', 'reason',
    )

    @property
    def is_focus(self) -> bool:
        """Whether this is a focus change, activated or deactivated."""
        out = self.verb in ('activated', 'deactivated')
        return out
```

| Name | Reads | Meaning |
|---|---|---|
| [`DocumentEvent.is_shared`](../../src/trail/jupyter/event.py#L149) | class attribute | `False`: no other view sends a copy of this message. Nothing in `src` reads it. |
| [`is_focus`](../../src/trail/jupyter/event.py#L155) | the verb | `True` for `activated` and `deactivated`. |
| [`tool`](../../src/trail/jupyter/event.py#L161) | nothing | Always `JupyterGIS`. It names the application the message comes from. |
| [`widget_id`](../../src/trail/jupyter/event.py#L166), [`reason`](../../src/trail/jupyter/event.py#L175) | `object.widget_id`, `object.reason` | The view and the cause of a focus change. |

The comment says why `is_shared` is `False`: each view's lifecycle and focus transitions are independent. The mode records carry the same flag, and the plan for them names its purpose: they are marked unshared "for the future duplicate handling deferred by the integration plan". **Deduction:** that handling is the `trail-todo: origin` of [Layer events](11-15-1-layer-event.md#limits-today-tldr), and no code uses the flag yet. The name `tool` is the application; the plan notes that [Tool events](11-15-5-tool-event.md) therefore call the active tool `mode`.

#### `ViewportEvent` and `ViewportV1Event`

[`ViewportEvent`](../../src/trail/jupyter/event.py#L661) is the viewport record and [`ViewportV1Event`](../../src/trail/jupyter/event.py#L707) names its schema. It extends `JupyterEvent` directly, not [`PageEvent`](../../src/trail/jupyter/event.py#L127), because a viewport message has no page or counter:

```python
@dataclass(kw_only=True, slots=True, repr=False)
class ViewportEvent(JupyterEvent):
    """The map view moved. The properties describe the view after the move."""

    is_shared: ClassVar[bool] = False
    selectable: ClassVar[tuple[str, ...]] = ('verb', 'coordinates', 'zoom', 'extent')

    @property
    def coordinates(self) -> dict[str, float] | None:
        out = (
            self.data
            .get('object', {})
            .get('new', {})
            .get('coordinates')
        )
        return out
```

[`zoom`](../../src/trail/jupyter/event.py#L678) and [`extent`](../../src/trail/jupyter/event.py#L688) read `object.new.zoom` and `object.new.extent` in the same way. Each returns `None` for a message that lacks it.

#### Printing

[`DocumentEvent._repr_items`](../../src/trail/jupyter/event.py#L184) yields the rows the console prints. A focus record adds its page, counter, widget and reason:

```python
def _repr_items(self) -> Iterator[tuple[str, object]]:
    yield from JupyterEvent._repr_items(self)
    if self.is_focus:
        yield 'tool', self.tool
        for name in ('frontend_id', 'focus_seq', 'widget_id', 'reason'):
            value = getattr(self, name)
            if value is not None:
                yield name, value
```

The rows after the [`JupyterEvent` rows](../../src/trail/jupyter/event.py#L107) are the page and the reason, only for a focus change. The `tool` row appears only on a focus change, although the property is true for every document record ([selecting](#selecting)). [`ViewportEvent._repr_items`](../../src/trail/jupyter/event.py#L697) yields `coordinates`, `zoom` and `extent`, for each that the message has. The console adds an `entry` row last, through [`Renderer.parameters`](../../src/trail/cli/render.py#L111). [`VERB_STYLES`](../../src/trail/cli/theme.py#L46) has a style for all six map verbs: `created`, `opened`, `closed`, `activated`, `deactivated` and `moved`.

#### What each record looks like

**Observed results.** A scenario run replayed [`baseline.json`](../../tests/jupyter/fixtures/baseline.json) through the real consumer. The fixture is a captured session of 61 messages. The project held the five maps it names and the two data files, with `.ontrail` holding `./` and `*.jgis`. Every message was recorded. The log held 27 `DocumentV1Event`, 2 `ViewportV1Event`, 10 `ModeV1Event`, 10 [`SourceV1Event`](../../src/trail/jupyter/event.py#L469) and 12 [`LayerV1Event`](../../src/trail/jupyter/event.py#L383) records. Of the 27 document records, 4 were `created`, 3 `opened`, 1 `closed`, 10 `activated` and 9 `deactivated`. The listings are `print(trail.events.select(...))`, with paths shortened to the project root as the console does, and without the `id`, `timestamp`, `schema_id` and `server` rows. The console would add an `entry` row to each record.

A created map and an opened map have only the verb:

```text
0. DocumentV1Event
    src_path: untitled.jGIS
    verb: created
```

```text
0. DocumentV1Event
    src_path: maps/city.jGIS
    verb: opened
```

A focus change has the page, the counter, the widget and the reason:

```text
0. DocumentV1Event
    src_path: untitled.jGIS
    verb: activated
    tool: JupyterGIS
    frontend_id: f22d6613-fe4f-4dc3-af05-d410f3b0359a
    focus_seq: 1
    widget_id: id-67f175fb-4d74-473a-aed4-96ec7034939f
    reason: document_switch
```

The last view of the map closing deactivates it first, and then reports `closed`:

```text
0. DocumentV1Event
    src_path: maps/city.jGIS
    verb: deactivated
    tool: JupyterGIS
    frontend_id: 34d02c8a-db83-4f58-b6d4-dff696c77bcb
    focus_seq: 10
    widget_id: id-eb44744b-3ced-4bf8-a99c-819074f09c3e
    reason: widget_closed
```

```text
0. DocumentV1Event
    src_path: maps/city.jGIS
    verb: closed
```

A move prints the view after it:

```text
0. ViewportV1Event
    src_path: maps/city.jGIS
    verb: moved
    coordinates: {'x': -13611034.139293559, 'y': 4561086.969892362}
    zoom: 13
    extent: [-13621257.591826702, 4553796.788319663, -13600810.686760416, 4568377.151465061]
```

Records of earlier recorded sessions, as the console printed them, are in the event coverage report's [maps created, opened and closed](../../scratch/11-12-2-event-coverage.md#9-maps-created-opened-and-closed) and [map focus, pans and zooms](../../scratch/11-12-2-event-coverage.md#13-map-focus-pans-and-zooms).

#### Selecting

A query can read the names in `selectable`, which the console offers as completions, and any other attribute of the record, through [`Select.held`](../../src/trail/select.py#L264). `cls=DocumentEvent` also selects records of later schema versions, because [`Select.equals`](../../src/trail/select.py#L241) matches a class by the names of its bases.

| Query | Selects | Observed on the captured session |
|---|---|---|
| `events cls=DocumentEvent` | every document record | 27 records |
| `events cls=DocumentEvent verb=created` | created maps | 4 records |
| `events cls=DocumentEvent is_focus=true` | focus changes | 19 records |
| `events cls=DocumentEvent is_focus=false` | opened, created and closed | 8 records |
| `events tool=JupyterGIS` | every document record, not only focus changes | 27 records |
| `events cls=DocumentEvent reason=widget_closed` | focus lost because a view closed | 1 record |
| `events cls=DocumentEvent verb=activated focus_seq>5` | later activations | 3 records |
| `events cls=ViewportEvent` | every move | 2 records |
| `events cls=ViewportEvent zoom=13` | moves that ended at zoom 13 | 2 records |

`tool=JupyterGIS` selects all 27 document records, since the property is constant, while only the 19 focus records print a `tool` row. A `focus_seq` comparison selects within one page's counter, and counters of different pages overlap, so a query for one page also needs `frontend_id`. [`test_focus_schema_metadata_and_repr`](../../tests/jupyter/test_events.py#L246) covers the focus rows, and [`test_captured_server_feed_replays_in_trail`](../../tests/jupyter/test_events.py#L318) checks 10 records and 5 focus changes from a smaller capture.

### Storage and replay ([TLDR](#storage-and-replay-deep-dive))

This section covers what the log keeps for a map record and how it is rebuilt. The writing and reading paths are shared with the other families ([Layer events](11-15-1-layer-event.md#storage-and-replay-tldr)).

[`Event.to_record`](../../src/trail/event.py#L169) writes every dataclass field except the entry, then the class name, then the entry's id and type. The map classes have no field of their own, so a record is the common one. **Observed:** a stored record has these keys, in this order: `id`, `timestamp`, `is_accepted`, `schema_id`, `data`, `server`, [`src_path`](../../src/trail/jupyter/event.py#L58), `cls`, `entry`, `entry_type`. `data` holds the message without its `schema_id`, so a focus record keeps `time`, `frontend_id` and `focus_seq` there. The paths are absolute. The console shortens them when it prints.

The record's `timestamp` is the moment `Consumer.receive` ran. **Observed:** a focus message with `time: 2020-01-02T03:04:05.000Z` was recorded with a timestamp of the day it arrived, and the stored `data['time']` was the page's value. The `time` is not among the printed rows either. `receive` has an `at` argument, in epoch seconds, that tests use to set the arrival time, and nothing else sets it. **Deduction:** the page's time is kept for later use, and the page's counter, not the time, is what orders a page's records.

[`JSONL.read`](../../src/trail/event.py#L625) replays the log when a project opens, and [`Event.from_record`](../../src/trail/event.py#L128) finds the class by its stored name. **Observed results.** Reopening the project in the scenario run rebuilt all 29 document and viewport records, and every one equalled its original `to_record()`. [`test_baseline_history_replays`](../../tests/jupyter/test_events.py#L411) does this for every message of the captured session, and [`test_consecutive_viewports_record_render_and_reopen`](../../tests/jupyter/test_events.py#L117) checks that two moves give two records, which print once and rebuild. [`test_schema_registry_and_roundtrip`](../../tests/jupyter/test_events.py#L37) does it for a message of each schema.

### Limits today ([TLDR](#limits-today-deep-dive))

These are the limits of today's map records. Each is labeled by how it was established. The last part lists where the code differs from the [event coverage report](../../scratch/11-12-2-event-coverage.md) and from the plans.

**Current behavior: `closed` follows the first view of a map.** [`wireDocumentEvents`][ef-wire-document] connects to the disposal of the widget it is given, and the plugin gives it the first widget of each model. **Deduction** from the source. The emitter's tests do not cover it, and it was not run.

**Current behavior: a record is dated when Trail received the message.** The page's `time` is stored in `data`. **Observed**, as shown under [storage and replay](#storage-and-replay-tldr).

**Current behavior: a viewport record has no page and no order.** The message has no `frontend_id`, no counter and no time. [`Jupyter.focus`](../../src/trail/jupyter/jupyter.py#L101) leaves viewport records out, and nothing else places one between focus changes except its arrival time. **Observed:** the page timeline of the captured session has 10 records and no viewport record.

**Current behavior: a map without a saved path leaves no record.** A map created from Python without a path, or with the placeholder `unsaved_project`, is dropped as `unsaved`. **Observed**, as shown under [finding the map](#finding-the-map-tldr). The first view of the map that the notebook renderer shows gets a record only when a path names a file.

**Current behavior: a map outside the project leaves no record.** `outside project` and `outside root` are dropped without a `Message`.

**Current behavior: `created` covers maps JupyterGIS made itself.** A map written by a script, exported from QGIS or duplicated in the file browser is `opened`. A registration expires after 30 seconds, so a map that is opened more than 30 seconds after its creation is `opened` as well.

**Current behavior: focus covers standalone map tabs.** A map shown in a notebook is not in the tracker. The emitter's docs say that embedded notebook-map focus is not reported and that a shell without a current-widget signal publishes no focus events. The event coverage report lists "Focus changes are reported for map tabs only, not notebook tabs" as remaining.

**Current behavior: a page that ends can lose its last messages.** The docs say that page termination can lose the final event, so a `deactivated` or `closed` may be missing.

**Current behavior: a failed emit leaves no trace.** The browser's `events.emit` call can be rejected. The rejection reaches only the browser console ([Silent failures][ef-events-md-silent]).

**Current behavior: the duplicate check never applies to map messages.** See [finding the map](#finding-the-map-tldr).

**Current behavior: a subject keeps its first asset.** See [the subject link](#the-subject-link).

**Current behavior: a new schema version is recorded without map rows.** A `document/v2` message is stored as a plain `JupyterEvent` until a class is registered for it.

#### Differences from the event coverage report and the plans

The [event coverage report](../../scratch/11-12-2-event-coverage.md) describes these records in its sections on maps created, opened and closed, and on map focus, pans and zooms. The code agrees with the report's records, field by field. These details are not in the report:

- `closed` is tied to the first view of a map, and `activated` is not reported for a map that is shown inside a notebook ([limits](#limits-today-tldr)).
- The `time` field that focus messages carry is not the record's time.
- Viewport records are not part of a page's timeline.
- A map with no saved path leaves no record. The report's `created` example is a saved map.

The report lists "Propose the eventfeed changes upstream to JupyterGIS: the new message types for sources, tools and notebook runs, the `created` verb, and the locality flag" as remaining. So the `created` verb exists only in the prototype until the maintainers review it.

Two plans differ from the code in how they describe the design:

| The plan | The code |
|---|---|
| [11-4-2](../plan/11-4-2-remaining-baseline-events.md): a Boolean `object.created` on `opened`, and no separate verb | The verb `created` replaces `opened`. |
| [11-1-1](../plan/11-1-1-viewport-moveend.md): replace a ten-second inactivity delay with a message at the end of each movement | Implemented as planned: `ViewportMoves`, a local `viewportMoved` signal, and no delay. |

### How this was checked ([TLDR](#how-this-was-checked-deep-dive))

**Checkouts.**

- **Trail:** commit `25d97ba` on `stable`. `stable` is two commits ahead of `origin/stable`, so Trail links are relative to the working tree. The working tree held uncommitted docstrings and comments in `src/trail/jupyter`, which another session added while these notes were written. No code changed. Links and code excerpts were checked against those files at 15:33 on 15 November 2026, and line anchors move if the files change.
- **JupyterGIS emitter:** commit [`932c09ae`](https://github.com/hodczak/prototype-jupytergis-eventfeed/commit/932c09aef44cfce398c3c4db41af8e56d552d9fe) of `hodczak/prototype-jupytergis-eventfeed`, branch `stable`, equal to `origin/stable`. Its links are pinned to that commit.

**Observed results.**

- **Trail tests.** `PYTHONPATH=src .venv/bin/python -m pytest tests/jupyter/test_kernels.py tests/jupyter/test_consumer.py tests/jupyter/test_events.py tests/jupyter/test_subscription.py` gave 76 passed and 1 skipped. The skipped test is [`test_schema_copies_match_installed_eventfeed`](../../tests/jupyter/test_events.py#L289), because `jupytergis_core` is not installed in that environment.
- **Emitter tests.** In the clone, `npx jest src/eventFeed --ci --coverage=false` in `python/jupytergis_core` gave 6 suites and 49 tests passed. The same command in `packages/schema` for `viewportMoved.spec.ts` and `newDocuments.spec.ts` gave 2 suites and 7 tests passed, and in `packages/base` for `viewportMoves.spec.ts` gave 1 suite and 13 tests passed. The clone stayed clean.
- **Schema copies.** A manual `diff` of `tests/jupyter/schemas/document_v1.yaml` and `viewport_v1.yaml` against the emitter's [`document_v1.yaml`][ef-schema-document] and [`viewport_v1.yaml`][ef-schema-viewport] showed no difference.
- **Scenario runs.** Throwaway scripts built a project in a scratch folder with `.ontrail` holding `./` and `*.jgis`. They built a [`Server`](../../src/trail/jupyter/server.py#L233) as the [`server` fixture](../../tests/jupyter/conftest.py#L130) does and sent messages through `Consumer.receive`: the captured session [`baseline.json`](../../tests/jupyter/fixtures/baseline.json), a map created after Trail started, and messages with a null path, `unsaved_project`, a path outside the root, a missing file and a hidden file. They then reopened the project and ran the selections above. The scripts were not kept in the repo.

**Not checked.** No browser was run. The emitter's behavior comes from its source, its unit tests and its docs. The `closed` behavior for several views of one map, and a map shown inside a notebook, were not run.

[ef-events-md-created]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/docs/contributor_guide/explanation/events.md#created-maps
[ef-events-md-focus]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/docs/contributor_guide/explanation/events.md#document-focus
[ef-events-md-viewport]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/docs/contributor_guide/explanation/events.md#viewport-events-report-completed-navigation
[ef-events-md-silent]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/docs/contributor_guide/explanation/events.md#silent-failures
[ef-wire-document]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/plugin.ts#L30
[ef-plugin]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/plugin.ts#L152
[ef-wired-models]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/plugin.ts#L158
[ef-new-documents-class]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/schema/src/newDocuments.ts#L10
[ef-new-documents-register]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/schema/src/newDocuments.ts#L19
[ef-new-documents-discard]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/schema/src/newDocuments.ts#L26
[ef-new-documents-consume]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/schema/src/newDocuments.ts#L33
[ef-create-register]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/jgisplugin/plugins.ts#L294
[ef-renderer-register]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_lab/src/notebookrenderer.ts#L236
[ef-focus]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/focus.ts#L22
[ef-focus-add]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/focus.ts#L37
[ef-focus-order]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/focus.ts#L68
[ef-focus-schedule]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/focus.ts#L103
[ef-focus-reconcile]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/focus.ts#L122
[ef-focus-publish]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/focus.ts#L154
[ef-focus-reason]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/focus.ts#L12
[ef-wire-viewport]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/viewport.ts#L5
[ef-viewport-moves]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/base/src/mainview/viewportMoves.ts#L35
[ef-viewport-suppress]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/base/src/mainview/viewportMoves.ts#L58
[ef-viewport-complete]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/base/src/mainview/viewportMoves.ts#L174
[ef-viewport-snapshot]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/base/src/mainview/viewportMoves.ts#L203
[ef-mainview-moves]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/base/src/mainview/mainView.tsx#L575
[ef-schema-document]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/jupytergis_core/event_schemas/document_v1.yaml
[ef-schema-viewport]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/jupytergis_core/event_schemas/viewport_v1.yaml
[ef-register]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/jupytergis_core/events.py#L14
[ef-test-created]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/plugin.spec.ts#L65
[ef-test-consume]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/schema/src/__tests__/newDocuments.spec.ts#L12
[ef-test-moves]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/base/src/mainview/__tests__/viewportMoves.spec.ts#L11
[ef-test-moved]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/packages/schema/src/__tests__/viewportMoved.spec.ts#L4
[ef-test-viewport]: https://github.com/hodczak/prototype-jupytergis-eventfeed/blob/932c09aef44cfce398c3c4db41af8e56d552d9fe/python/jupytergis_core/src/eventFeed/__tests__/viewport.spec.ts#L51
