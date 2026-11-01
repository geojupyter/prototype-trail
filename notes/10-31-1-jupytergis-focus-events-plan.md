# Plan: JupyterGIS focus events for Trail

Add events for entering and leaving JupyterGIS, so Trail can show tool switches alongside
resource registration and map edits. **Matt's current feed does not report focus switches.**
It already reports layer edits, including filters, and some viewport movement. The supervisor's
example therefore needs both existing events and a small upstream extension.

This extends the [Jupyter integration plan](/home/arstneio/PyCharmProjects/trail/notes/10-28-2-jupyter-submodule-plan.md) and its later
[upstream feed plan](/home/arstneio/PyCharmProjects/trail/notes/10-29-2-eventfeed-upstream-plan.md). The browser detects focus; JupyterGIS
publishes it through the existing server event feed; the planned `trail.jupyter` consumer records
it. File comparisons remain [deferred](/home/arstneio/PyCharmProjects/trail/notes/10-29-3-file-events-plan.md:3).

Checked on 2026-10-31 against `../eventfeed` at `fb9b71fd` on `hodczak`. Its emitter, schemas and
event-feed explanation are unchanged from local `prototyping` at `abecc972`. These findings are
from source inspection. The first implementation step below starts from this baseline.

## Feature comments and implementation order

Implement the necessary JupyterGIS changes in `../eventfeed` before implementing Trail's Jupyter
module. Use a local `codex/jupytergis-focus-events` branch off `hodczak`, the branch checked out
when this work starts. Implement one reviewable step at a time, test it, update this checklist,
and leave it uncommitted for review before starting the next step.

Mark non-obvious feature/event integration points with `// trail: <feature> - <purpose>` in
TypeScript and `# trail: <feature> - <purpose>` in Python/YAML. These comments explain implemented
behavior. Use `trail-todo:` separately for unfinished work. For example:

```typescript
// trail: focus - detect returning to the same map after another document was selected.
```

Use stable tags: `focus`, `viewport`, `resources`, `origin`, `document-identity`, and `delivery`.
The [upstream plan](/home/arstneio/PyCharmProjects/trail/notes/10-29-2-eventfeed-upstream-plan.md)
and this plan hold the rationale, supported behavior, limitations, implementation references,
and test coverage. Put comments at meaningful boundaries, not on every changed line. Keep their
explanations useful to JupyterGIS maintainers as well as Trail developers.

## Baseline before implementation

The [browser emitter](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts:21)
wires three kinds of events. Opening a document and selecting an already open document are
different actions; only the first currently produces an event.

| Requested behavior | Current support |
|---|---|
| Initialize a Trail project | Trail knows when it creates a project, but has no dedicated initialization event. This belongs in Trail. |
| Open/create a JupyterGIS map | `document/opened`; opening and creation are not distinguished. `closed` also exists. |
| Register a resource in Trail | `AddAssetEvent` already records a file becoming tracked. |
| Load a resource as a map layer | `layer/added` identifies the layer and its source ID. It does not provide the source's file path. |
| Apply/remove a filter | `layer/updated` includes the changed `filters` object. Trail can describe the difference. |
| Pan/zoom | `viewport/moved` after 10 seconds without another viewport signal. Fixed-center zoom can be missed. |
| Switch into/out of JupyterGIS | Missing, including returning to the same open map. |
| Select a map interaction tool | Missing; this is separate from switching applications. |

The [filter implementation](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/features/filter/Filter.tsx:198)
writes the layer, which the emitter observes. The viewport's limitation comes from observing
[center changes](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx:613).
The explanation's claim that viewport events are unimplemented is stale; the
[emitter implements them](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts:80).

## Emit focus transitions in JupyterGIS

Extend `document/v1` with `activated` and `deactivated`. These describe the active map workflow;
`opened` and `closed` keep their existing meaning. Adding verbs and optional properties follows
the feed's schema-evolution rules and lets Trail reuse its planned document event class.

Add a page-level focus controller in `python/jupytergis_core/src/eventFeed/focus.ts`, started by
the existing emitter plugin. It keeps the last active JupyterGIS widget and emits only changes:

- Observe `app.shell.currentChanged` and check `tracker.has(app.shell.currentWidget)` to identify
  the selected map. The generic shell makes this signal optional; enable this feature where it
  exists without making the core plugin depend on JupyterLab's shell service.
- Do not rely on the JupyterGIS tracker's `currentChanged`: its current widget is the last
  focused or added map, so leaving JupyterGIS and returning to the same map can be invisible.
  The relevant contracts are in the installed
  [shell interface](/home/arstneio/PyCharmProjects/eventfeed/node_modules/@jupyterlab/application/src/frontend.ts:278)
  and [widget tracker](/home/arstneio/PyCharmProjects/eventfeed/node_modules/@jupyterlab/apputils/src/widgettracker.ts:24).
- Also observe window `focus`/`blur` and document `visibilitychange`. A map is active when it is
  the current main document and the page is visible and has focus. Recompute once per signal
  turn and suppress repeated states. Browser visibility and focus are distinct observations:
  [Page Visibility API](https://developer.mozilla.org/en-US/docs/Web/API/Page_Visibility_API),
  [document.hasFocus](https://developer.mozilla.org/en-US/docs/Web/API/Document/hasFocus).
- Notebook → map emits `activated`; map → notebook emits `deactivated` for the map being left.
  Map A → map B emits A's deactivation followed by B's activation. Moving into a map sidebar or
  dialog keeps the current map context. Mark the initial observed state as `initial_state`;
  do not present it as a known switch from another tool.
- Track widgets separately from shared documents. Two views can share a model; focus wiring must
  sit outside the emitter's existing model-deduplication guard. Dispose listeners with their
  owners and reconcile focus when a widget is registered, renamed or closed. Queue the active
  view's `deactivated` before its document feed queues `closed` and detaches.

Extend `event_schemas/document_v1.yaml` and its tests. Each transition carries the existing
`subject` and current document reference, plus `frontend_id` (one UUID per browser page),
`focus_seq` (one increasing counter across that page's focus transitions), `object.widget_id`,
and `object.reason`. These properties are optional for older lifecycle messages; the new emitter
supplies them for both focus verbs. Capture values when the transition occurs.

Use `DocumentFeed`, the per-shared-document subscription owner proposed in the upstream plan,
for its `stream`, `seq`, `time`, document identity and delivery queue. `focus_seq` orders switches
across different document streams; `subject` still identifies a Yjs copy, not a person or page.
An illustrative message received by Trail would be:

```json
{
  "schema_id": "https://schema.jupytergis.org/events/document/v1",
  "subject": "3271840155",
  "stream": "<document-stream-uuid>",
  "seq": 8,
  "time": "2026-10-31T18:00:00.000Z",
  "frontend_id": "<page-uuid>",
  "focus_seq": 3,
  "verb": "activated",
  "object": {
    "document_id": null,
    "document_path": "maps/crossings.jGIS",
    "widget_id": "<widget-id>",
    "reason": "document_switch"
  }
}
```

Browser focus loss establishes that the page lost focus; it cannot identify the destination
application. A timeline claiming “switched to DataConnect” needs instrumentation in DataConnect
too. Browser chrome can also cause blur. Record reasons such as `window_blurred` or `page_hidden`
without inventing a destination. Page shutdown delivery remains best effort; a missing exit
leaves the interval unknown.

Start with standalone map tabs. A map embedded in a notebook needs focus/pointer handling on its
own widget: selecting its containing notebook alone does not establish interaction with the map.

## Receive the events in Trail

The transport already exists upstream:

```text
browser serviceManager.events.emit()
    → authenticated POST /api/events
    → Jupyter Server event logger
    → WebSocket /api/events/subscribe
    → planned trail.jupyter.session.consumer
    → Trail's events.jsonl
```

The [schema registrar](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/jupytergis_core/events.py:9)
loads YAML schemas at server startup. Restart the server after updating schemas. Trail needs no
new endpoint or second connection. Its integration is still planned: `src/trail/jupyter/` does
not exist, and the current [JupyterEvent](/home/arstneio/PyCharmProjects/trail/src/trail/event.py:503)
is only a stub.

In the existing plan, `Consumer` turns received messages into records, and `DocumentV1Event`
holds `document/v1` messages. Extend that handling as follows:

- Preserve `schema_id` and all payload fields, as already planned. Keep document events
  `is_shared = False`; another view's focus is independent. Suppress delivery retries by
  `(server, stream, seq)`, not by matching the payload across views.
- Record `activated`/`deactivated` immediately. Explicitly exclude these verbs from bursts,
  autosave waiting and folding of filesystem events. They require no save confirmation.
- Resolve both verbs using the map they name, including the map being left. Apply the existing
  project/asset rules. Do not attribute an unsaved or unrelated map to every Trail connected to
  a home-root server; an unsaved map needs explicit project association before recording.
- Show `tool: JupyterGIS` for activation, with its map and reason. Use page identity for grouping;
  order that page's focus timeline by `focus_seq`, since independent document queues can deliver
  transitions out of order. Do not infer one global active tool across collaborative clients.

## Review order and demonstration

Focus support can be demonstrated by step 5 of the original integration plan, before its later
burst/save-folding work. The appendix's entries/ontrail/offtrail/session refactors have already
landed; the remaining core changes, including records without an entry, still apply.

- [x] 1. Add standalone focus transitions to the existing JupyterGIS emitter and document schema,
  with feature comments and tests. Preserve existing lifecycle messages. This first slice uses
  current document paths; room identity and reliable ordered delivery remain step 2.
- [ ] 2. Complete the necessary upstream-plan foundations before Trail implementation: document
  references, `DocumentFeed`, locality, stream identity and ordered delivery. Coordinate focus
  disposal with the new document owner and verify actual WebSocket delivery.
- [ ] 3. Demonstrate existing layer/filter events and apply the upstream plan's viewport repair:
  observe completed movement including fixed-center zoom, retain its settling policy, and flush
  or cancel pending movement on close. Preserve the planned locality metadata for layer edits;
  current layer observations can include replicated or programmatic changes.
- [ ] 4. Complete resource linkage if required for the illustrated timeline. Add the upstream
  plan's `source/v1` events from `sharedSourcesChanged`, with source ID, locality and a usable
  file/URL reference; include the referenced source in `layer/added` so an existing source also
  resolves. Resolve relative paths from the map's resource base and link to matching Trail assets.
  Keep URLs/inline data distinct from local files and omit credentials and embedded data bytes.
- [ ] 5. Implement the original Trail integration plan's remaining core/event-model/consumer/
  connection steps 1–5, with the immediate focus routing above. Verify the complete sequence on
  the WebSocket and in Trail's persisted log, including returning to the same map, browser focus,
  rename, close, and independent views. Then continue its later burst/save-folding steps.
- [ ] 6. Add a literal project-initialization record only if needed for the demo. Trail already
  knows whether persistent metadata was newly created in
  [Trail.__init__](/home/arstneio/PyCharmProjects/trail/src/trail/trail.py:274); emit once there,
  before initial resource registration, and never on reopening. If map-tool selection is also
  required, emit a separate mode-change event from the existing
  [modeChanged signal](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/model.ts:1235).

### Step 1 implementation and validation

`DocumentFocus`, the per-page controller in
[focus.ts](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/focus.ts),
now observes shell selection and browser focus/visibility. The emitter registers each view
before its model-deduplication guard. Active-view disposal emits `deactivated` before the existing
`closed` callback; shell disposal removes browser listeners. Focus messages include page identity,
page-wide order, observation time, widget ID and reason. Existing lifecycle payloads remain valid.

Changed files in `../eventfeed`, all uncommitted on `codex/jupytergis-focus-events`:

- [focus.ts](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/focus.ts)
  and [plugin.ts](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts): controller and emitter wiring, with `trail: focus` comments.
- [document_v1.yaml](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/jupytergis_core/event_schemas/document_v1.yaml): additive verbs and optional metadata.
- [focus.spec.ts](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/__tests__/focus.spec.ts)
  and [test_events.py](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/jupytergis_core/tests/test_events.py): behavior and schema/logger coverage.
- [jest.config.js](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/jest.config.js),
  [tsconfig.test.json](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/tsconfig.test.json),
  [tsconfig.json](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/tsconfig.json),
  and [package.json](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/package.json): core-package test setup, following the other packages' existing Jest configuration.
- [events.md](/home/arstneio/PyCharmProjects/eventfeed/docs/contributor_guide/explanation/events.md): focus contract, manual checks and limitations.

Validation: 13 focus tests and 16 event-schema/logger tests pass. Production and test TypeScript
checks, ESLint, Prettier and whitespace checks pass. The full core Python suite reports 23 passed
and one existing migration-fixture failure: the story-segment migration omits the expected
`content.panelWidth = "25%"`. The same failure was reproduced from archived `fb9b71fd` sources and
fixtures. Python lint passes with the pre-existing `CPY001` missing-copyright notice excluded;
that notice also occurs on the unchanged base file.

No Trail module implementation or browser/WebSocket demonstration is included in this step.
Embedded map focus, existing document-lifecycle ownership bugs, room identity and delivery
guarantees remain for later steps. Existing modified examples and the layer-gallery lockfile
were preserved. Stop here for review; step 2 starts after this step is committed or continuation
is requested.

The demo sequence is: initialize Trail → register a file → activate JupyterGIS → add its layer
→ apply/remove a filter → move the map → leave and return. Capture the real application beside
Trail's arriving records, with annotations naming each observed action. An annotated GIF is a
later presentation artifact; it does not require additional event infrastructure.

## Trail foundation implementation

This slice implements Trail's connection and event-recording foundation against the current
`../eventfeed` schemas, including the uncommitted focus extension. It is on the local
`codex/jupyter-base-events` branch, based on `ontrail`, and remains uncommitted. Upstream steps
2–4 are still separate work; the consumer accepts their future metadata without requiring it.

- [x] Add records with optional entries, schema/class registration, dotted selections and replay.
- [x] Add server discovery, explicit connections, authenticated subscriptions, reconnects and
  shared recorder locking for watchdog and Jupyter sessions.
- [x] Record document, layer and viewport messages immediately, preserving their payloads.
  Record contents-service saves separately and use contents renames to resolve stale paths.
- [x] Record focus immediately, display its tool and reason, and provide
  `trail.jupyter.focus(server=..., frontend_id=...)` to select one page's records in `focus_seq`
  order. Keep the persisted log in arrival order. Do not merge independent views.
- [x] Verify real Jupyter Server HTTP emission → WebSocket subscription → Trail persistence,
  and retain the received messages as a regression fixture.

The console starts `trail.jupyter.session` with its watchdog session. Library use starts it
explicitly, through `start()` or an asynchronous context. `trail.jupyter.connect(url=..., token=...,
root=...)` supplies a server and its local root when runtime discovery cannot find it.
Untracked maps inside the project produce records with no entry. Matching `.ontrail` files are
registered first. Unsaved, missing, offtrailed and unrelated maps are excluded. Focus is resolved
against the map named by each event, including the map being left.

The current emitter has no `stream` or `seq`. Trail preserves repeated focus messages from that
emitter. When those fields are supplied, a bounded cache suppresses recent retries by server URL,
stream and sequence. `frontend_id` and `focus_seq` group and order focus; they do not identify one
global active tool. Burst merging, save folding, origin filtering, resource linkage, document IDs,
feature-specific layer interpretation and the full `jupyter` console command remain deferred.
Feature boundaries and deferred work use the agreed `trail:` and `trail-todo:` comments.

Changed files:

- `src/trail/jupyter/__init__.py`, `event.py`, `message.py`, `consumer.py`, `server.py`,
  `jupyter.py`, `session.py`, `subscription.py`: event classes, payload retention, map lookup,
  diagnostic messages, server discovery, session ownership, subscriptions and focus selection.
- `src/trail/lock.py`, `src/trail/session.py`, `src/trail/trail.py`: recorder lock shared by both
  sessions, their common descriptor, and `trail.jupyter` access.
- `src/trail/event.py`, `src/trail/select.py`: optional entries, replay identity, class-name guard
  and dotted fields.
- `src/trail/cli/command.py`, `console.py`, `feed.py`, `render.py`, `theme.py`: payload-path
  selections, connection startup/status, resilient rendering and event colors.
- `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`: direct transport/discovery
  dependencies and schema-validation test dependencies. The ignored local `uv.lock` was refreshed.
- `tests/conftest.py`: isolate runtime discovery from the user's running Jupyter servers.
- `tests/jupyter/__init__.py`, `conftest.py`, `test_events.py`, `test_consumer.py`, `test_servers.py`,
  `test_subscription.py`, `test_lock.py`: event, resolution, focus, transport, discovery and lock tests.
- `tests/jupyter/schemas/{document,layer,viewport,contents_service}_v1.yaml`: schema snapshots.
- `tests/jupyter/fixtures/focus.jsonl`: ten messages captured from the actual server subscription.
- This plan: implementation status and validation.

Validation: **287 tests passed**. The suite reports one recorder-lock warning when an existing
reload test opens a second Trail while its first recorder is active. The selected Ruff checks
(`E4`, `E7`, `E9`, `F`, `I`, `UP`) and whitespace checks pass. Schema snapshots match the installed
eventfeed checkout. The actual-server check used its registered schemas and authenticated
`POST /api/events`; all ten received records, including five focus transitions, survived reopening
Trail. It did not drive browser focus: the browser demonstration in step 5 remains outstanding.
