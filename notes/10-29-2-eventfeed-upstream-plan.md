# Plan: upstream changes to the JupyterGIS event feed

Make the event feed identify the document and the reporting client, distinguish local changes
from replicated changes, preserve emission order, and end each client's stream correctly. These
changes let Trail replace its timing guesses with explicit facts from JupyterGIS. Smaller messages
and fixes to notebook paths and migration IDs follow separately.

This plan builds on the [Trail integration plan](/home/arstneio/PyCharmProjects/trail/notes/10-28-2-jupyter-submodule-plan.md)
and [meeting outline](/home/arstneio/PyCharmProjects/trail/notes/10-29-1-matt-meeting-outline.md).
Source inspection on 2026-10-29 used the [eventfeed checkout](/home/arstneio/PyCharmProjects/eventfeed)
on `prototyping`, commit `abecc972293f4b92148173c17984513570c85476`. The behavioral runs cited
in those notes are earlier evidence; they were not repeated for this plan. The proposals below
are implementation decisions, not descriptions of changes already made.

## How the pieces fit together

The feed starts in a browser's copy of a map. A **shared document** is the Yjs object holding that
copy. A **model** wraps it for JupyterGIS, and a **widget** displays it. Multiple widgets can use
one model; separate models can refer to the same server document. The server keeps participating
copies synchronized through a **room**.

The existing implementation has four relevant parts:

| Part | Responsibility and source |
|---|---|
| Shared-document signals | `JupyterGISDoc` observes Yjs transactions and reports changed layers. Its signal types live in [interfaces.ts](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/interfaces.ts:90), and its observers in [doc.ts](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/doc.ts:592). |
| Browser emitter | [eventFeed/plugin.ts](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts) subscribes to model signals, builds document/layer/viewport messages, and posts each independently. |
| Document integration | The [widget factory](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/factory.ts:59) and [notebook renderer](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_lab/src/notebookrenderer.ts:196) supply models and paths to the emitter. |
| Server contract | [YAML schemas](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/jupytergis_core/event_schemas), registered by [events.py](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/jupytergis_core/events.py), describe accepted messages. Jupyter Server supplies the POST and WebSocket endpoints. |

Most work belongs in these parts. Migration fixes belong in JupyterGIS's file-format code.
Failures to save or follow renamed files belong in the dependencies listed near the end.

## Common message contract

Establish the field meanings before changing the emitter. Preserve the existing `subject`,
`verb`, `object`, and full layer snapshots in v1. Add the following optional schema properties;
the updated emitter supplies them whenever their values are available.

| Payload field | Meaning |
|---|---|
| `subject` | Existing Yjs client ID, represented as a string. Identifies the reporting copy, not a person. |
| `stream` | New random UUID for one attachment lifetime of the emitter to a shared document. Distinguishes sequence resets and reopening an existing model. |
| `seq` | Integer starting at 1, increasing across all event types in that stream. |
| `time` | UTC ISO 8601 time when the browser observed the change. For a settled viewport, the time of its last included movement. |
| `local` | For layer changes, the Yjs transaction's locality in the reporting copy. Absent where that distinction does not apply. |
| `object.document_id` | The collaboration room identifier, read from shared state, or `null` when unavailable. Treat it as opaque. |
| `object.document_path` | Current Jupyter Contents path, including a drive prefix when present, or `null` for a document without a backing file. |

`stream`, `seq`, `time`, and `local` go alongside `subject`; document identity stays inside
`object`. These are payload fields inside the POST's `data`. The POST's `schema_id` and `version`
remain outside `data`; the server adds `schema_id` when forwarding the payload to subscribers.

The extra `stream` field is a small addition to the meeting proposal. A counter keyed only by
`subject` is ambiguous if handlers are detached and reattached to a surviving model. Consumers
identify a delivery by `(server, stream, seq)`. Copies reported by different browser clients still
have different stream IDs; this key handles retries, not replication between views.

Additions stay optional in the v1 schemas so older emitters remain valid. Keep existing required
fields and their meanings. Test both old and enriched messages. Do not treat a new required
property, removal of a property, or a change to the meaning of `old` and `new` as additive.
An absent `local` means unknown, not `false`. Trail should use the new fields when present and
retain its older-feed handling when they are absent.

## 1. Preserve transaction locality

The shared document already receives enough information to distinguish local changes from
replication. `_layersObserver` currently discards it before the emitter sees the change.

- Accept the Yjs transaction argument in `_layersObserver`; propagate `transaction.local` through
  `IJGISLayerDocChange` and the existing `sharedLayersChanged` signal.
- Put that boolean in each emitted layer message as `data.local`. All layer changes from one
  transaction receive the same value.
- Keep remote messages. Trail decides whether to record them, as requested in the earlier note.
- Keep initialization separate: the first synchronization must not report every existing layer
  as a newly added layer. A later remote transaction is still observable with `local: false`.

`local: true` means the transaction originated in this Yjs copy. Browser code, undo/redo, and
initialization can also make local transactions; it is not proof of a mouse or keyboard action.
`local: false` does not distinguish a collaborator, Python, and a disk reload. The flag removes
the need to guess whether a message is a remote observation, but does not identify the original
actor or give Python a complete event stream when no browser is open.

**Acceptance:** apply one change to document A and transfer its Yjs update to document B. Observe
one local signal and one remote signal with the same layer values. Cover add, update, remove,
undo/redo, and multiple layer changes in one transaction. Preserve remote-only observations from
Python and reloads in browser integration tests.

## 2. Resolve document identity at observation time

Locality identifies the reporting copy's relationship to a change. Document identity determines
which file the change belongs to, including after another client renames it.

Add a small `DocumentReference` resolver in
`python/jupytergis_core/src/eventFeed/document.ts`. The emitter uses it to build
`object.document_id` and `object.document_path` for every event type. It reads the shared
document's `document_id` and `path` state and retains the document context's drive information.

- Prefer the provider-maintained shared path when available, and the current document context
  for a file-backed model without that state. Listen to shared-state path changes as well as
  context path changes; a notebook view needs the former too.
- Retain the full Contents path from `context.path`. The factory currently copies
  `context.localPath`, which drops the drive. Recombine provider-local paths using the Contents
  API and known drive identity. Do not change every use of `model.filePath` without checking its
  resource-resolution callers; the event reference can be separate.
- Snapshot identity with the change, before queueing it. Retrying an event after a rename must
  not change the path it originally reported.
- Represent an ephemeral map explicitly. Do not identify it by checking whether a filename ends
  in `unsaved_project`: a real file could have that name.
- Return `null` for an unavailable identity instead of fabricating a file ID. In particular, a
  provider-local path without enough information to recover its drive must not masquerade as a
  path on the default drive.

The room ID is scoped to the server and its file-ID provider. It is not a permanent identity
across servers, file-ID database replacement, or every move outside Jupyter. A rename that the
provider handles should preserve it; Save As to a new file should produce a different one.

**Acceptance:** two browser pages report the same room ID and distinct subjects; rename from
either page and from a parent directory; reuse the old filename for a different map; verify a
notebook view follows the rename. Cover a named extra drive, an ephemeral document, and `.jGIS`,
`.qgs`, and `.qgz` references. A dependency's failure to update the room is reported as such,
not hidden with path heuristics in the emitter.

## 3. Own subscriptions for the lifetime of the shared document's views

The emitter currently remembers models in a `WeakSet` but connects `closed` to the first widget.
It also leaves viewport timers running and can finish attaching handlers after that widget has
already closed. Replace this wiring with explicit ownership.

Introduce `DocumentFeed` in `python/jupytergis_core/src/eventFeed/document.ts`, beside the
reference resolver. It manages one shared document's event subscriptions, attached widgets,
stream ID, sequence counter, pending viewport, and cleanup. The plugin holds an iterable registry
keyed by shared-document object identity and registers every widget with its corresponding feed.
It does not key this registry by file path or room ID: separate client copies of one room remain
separate reporters.

- The first widget starts the feed and produces one `opened`. Further widgets sharing the same
  document do not add duplicate layer observers.
- Attach viewport listeners once per distinct model where necessary; observe shared layers once
  per shared document. Removing one widget must preserve handlers needed by surviving widgets.
- The last widget flushes its pending viewport, queues one `closed`, disconnects signals,
  cancels timers, and removes the feed from the registry. Previously queued messages can finish
  delivery within the queue's limits.
- A later attachment starts a new stream. Disposed models force cleanup even if a widget's
  disposal notification is missing. Repeated disposal cannot emit a second `closed`.
- Check liveness after every asynchronous initialization wait. Handle initialization failure.
  `initialSyncReady` currently resolves on the first options observation, not an explicit
  provider-ready notification; verify already loaded, empty, ephemeral, and JupyterLite models.
  Use a provider readiness signal where available and an explicit local-load completion path
  elsewhere, rather than a timer or an assumption that any options change means synchronization.

This makes `closed` mean the last local widget for this reporting copy has closed. It does not
mean the last browser anywhere has left the server room. The latter requires server-side
connection accounting. A tab and notebook with separate shared documents have separate feeds,
even when their `document_id` values match.

Page shutdown is best effort. On a non-persisted `pagehide`, form one `closed` for each live
stream and attempt small authenticated keepalive POSTs, retaining existing sequence IDs on
any retried messages. Test the Jupyter
request headers, XSRF handling, base URL, and browser size limits. Do not emit `closed` merely
because a tab becomes hidden; do not close a page retained in the back/forward cache. Browser
termination can skip lifecycle callbacks, so a missing `closed` must remain possible.
[MDN documents this limitation](https://developer.mozilla.org/en-US/docs/Web/API/Window/pagehide_event#usage_notes);
[its beacon guidance](https://developer.mozilla.org/en-US/docs/Web/API/Navigator/sendBeacon) also
explains why custom request properties require `fetch` rather than a blind `sendBeacon` substitution.

**Acceptance:** close two shared-model widgets in both orders; continue editing in the survivor;
close before initial synchronization; reopen a surviving model; dispose twice. Verify the final
viewport precedes `closed`, timers produce nothing afterward, and a second browser remains live.
Treat page-close delivery as a tested attempt, not a delivery guarantee.

## 4. Sequence, timestamp, and queue emissions

With each feed's lifetime explicit, it can assign stable delivery identities and serialize its
requests. Add a queue in `python/jupytergis_core/src/eventFeed/transport.ts`, used by
`DocumentFeed`, around the existing `app.serviceManager.events.emit()` call.

- Capture payload values, subject, document reference, and `time` at observation. Assign `seq`
  when the logical message is enqueued, before asynchronous serialization or hashing. Retrying
  preserves the complete message. A viewport receives its sequence number when its settled
  message is formed, but retains the final sample's captured reference and time.
- Allow one request in flight per stream. Keep streams independent so one unavailable document
  does not block all maps. Retry transient network/server failures with capped exponential
  backoff and jitter; honor a supplied retry delay.
- Do not endlessly retry invalid schemas, oversized requests, or rejected authentication.
  Record the failure and allow later messages to proceed, leaving a sequence gap. Bound queued
  event count, total serialized bytes, and retention time with named configuration constants.
  A rejected or expired message consumes its sequence number and produces a visible diagnostic.
- Include schema, stream, sequence, and failure category in diagnostics. Do not log complete
  layer payloads or authenticated request URLs.
- Reuse the same transport settings for a page-close attempt. It may overtake a request already
  in flight; subscribers must still use sequence numbers rather than arrival order.

These changes reduce loss and request reordering. They do not provide exactly-once delivery:
the server might accept a POST whose response is lost, and retrying then publishes a duplicate.
Serial POST completion also does not establish a server-wide order across subscribers or clients.
The current endpoint has no replay for a disconnected subscriber.

`time` is a browser observation time, not a save time or a trusted cross-machine clock. Consumers
use `seq` for ordering within a stream. A gap detected after receiving a later message reveals
missing emissions; it cannot detect a lost final message or prove a file contains the edit.

**Acceptance:** delay and fail selected requests; verify one in-flight request per stream,
unchanged retry payloads, duplicate detection by sequence, queue limits, and progress after a
permanent failure. Check sequence continuity across document, layer, and viewport event types.
An HTTP/WebSocket integration test must inspect what subscribers actually receive, beyond the
existing Python logger test.

## 5. Repair viewport coverage

Lifecycle cleanup fixes delayed emissions after close. A separate viewport fix covers movements
that never reach the emitter: [mainView.tsx](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx:613)
currently updates awareness from `change:center`, and its `!zoom` check excludes zoom level zero.

- Observe completed local viewport changes, including resolution changes with a fixed center.
  Keep the existing 10-second settling policy and local-client filter.
- Accept zoom zero. Compare center, zoom, extent, and projection rather than center alone.
- Add projection as an optional field in the viewport state and `object.new`, so coordinates
  can be interpreted without opening the map file.
- Keep the final sample's observation time through the debounce. Cancel or flush timers under
  the ownership rules above.

Initial fitting and programmatic moves can still be local. Do not suppress the first viewport
message indiscriminately; that could discard a real early interaction. Distinguishing an initial
fit from a user's movement needs an explicit signal at the operation that causes it. Document
the remaining behavior until that signal exists. This change does not prevent autosave on open.

**Acceptance:** pan, zoom buttons with an unchanged center, zoom zero, projection changes,
remote awareness updates, and disposal during the settling interval.

## 6. Make migration IDs deterministic in Python and TypeScript

Stable document identity does not prevent false layer differences when loading an older map.
The 0.5-to-0.6 migration generates new IDs for style rules and their containing style layers.
Both implementations need the same correction:

- [Python migration](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/jupytergis_core/migrations/v0_5_to_v0_6.py).
- [TypeScript migration](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/migrations/v0_5_to_v0_6.ts),
  which calls the shared [grammar converters](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/grammar/grammarConversions.ts).

Derive each generated ID from a fixed migration namespace, the enclosing map-layer ID, and its
structural role/index, for example the first grammar layer versus its first rule. Specify one
deterministic algorithm and identical UTF-8 input encoding in both languages; UUIDv5 is suitable.
The migration supplies deterministic ID generation to the converters, while normal creation of
new styles retains fresh IDs. Preserve existing IDs in already migrated documents.

The current [Python tests](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/jupytergis_core/tests/test_migrations.py:39)
and [TypeScript tests](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/__tests__/migrations.spec.ts:13)
replace UUID generation with `test-uuid`. Remove those mocks for migration tests and update the
shared fixtures with real deterministic values. Include multiple layers and roles to detect
collisions instead of accepting one repeated test ID.

**Acceptance:** independently load the same legacy input twice and obtain identical IDs; Python
and TypeScript agree; running the versioned migration again preserves IDs; changing the document
path does not change them. New user-created styles must still get distinct IDs. This fixes ID
churn without trying to redesign autosave or viewport options.

## 7. Correct notebook file references

The feed can report accurate references only if notebook initialization supplies them. The
notebook renderer currently resolves a relative map path from `app.shell.currentWidget`, catches
every read error by attempting to create a file, and assigns `unsaved_project` to unnamed maps.

- Resolve from the notebook or console session that owns the widget communication, not whichever
  tab is active when initialization runs. Carry the owning session's base path through widget
  initialization. Define explicit behavior for shared kernels and multiple frontends; if the
  owner is unavailable, require a server-relative reference instead of guessing from focus.
- Make opening and creating explicit operations. Recommend that a supplied path opens an existing
  map and a missing map produces an error, with a separate creation option/API for new files.
  This changes Python API behavior and deserves its own compatibility decision and release note.
- Independently fix the catch-all: permission errors, server errors, and network errors must
  never trigger file creation, even if implicit creation is temporarily retained for 404s.
- Keep unnamed maps explicitly ephemeral, with a unique in-memory document and null event path.
  Preserve their resource-resolution base separately. Two unnamed maps must not accidentally
  share a fake backing-file identity.
- Follow shared path changes after initialization and feed them to `DocumentReference`.

**Acceptance:** execute in notebook A while B is active; test missing versus forbidden paths;
display two unnamed maps; rename a displayed map from another page; cover a non-default drive.
Test the tab/notebook connection-lifetime bug separately in jupyter-collaboration: changing the
event emitter does not repair the connection that persists their edits.

## 8. Introduce bounded layer changes as `layer/v2`

The v1 changes preserve full snapshots. A separate schema version should remove the repeated
embedded images and unchanged layer fields that make ordinary edits expensive.

Add `layer_v2.yaml` beside the existing schemas and a serializer in
`python/jupytergis_core/src/eventFeed/changes.ts`. Retain the common message fields and layer ID.
Replace whole-layer `old` and `new` with `object.changes`, a list of operations on JSON Pointer
paths relative to the layer. Each operation has `op`, `path`, and the relevant `old`/`new` values.
For example, an opacity edit is:

```json
{"op": "replace", "path": "/parameters/opacity", "old": 1, "new": 0.35}
```

- Define add, remove, and replace explicitly. Missing and JSON `null` are different. Escape `/`
  and `~` in pointer tokens. Initially replace a changed array as a whole, avoiding ambiguous
  sequences of shifting indexes.
- For adding or removing a layer, use a root operation containing its sanitized snapshot. For
  updates, include only changed paths and values. Treat an equal old/new snapshot as a no-op.
- Replace attachments with typed metadata containing filename, MIME type, decoded byte size,
  and a SHA-256 digest. Handle each representation in an attachment's MIME bundle. The format
  must distinguish metadata from ordinary JSON objects. Do not change the actual document.
- Cover attachments inside added/removed subtrees as well as individual image changes. Large
  text or inline data also needs an explicit size policy: emit an identified omitted-value
  summary when necessary, not truncated JSON. Consequently v2 is an audit description, not a
  promise that every event can reconstruct the original layer.
- Cache attachment digests by content and bound that cache. Snapshot changes before asynchronous
  hashing; a later edit must not change an earlier message or overtake it in the queue.

Register both versions, but choose one emitted layer version through configuration. Start with
v2 opt-in. Registering both schemas does not require broadcasting both. Sending full v1 snapshots
alongside v2 would preserve the oversized-message problem for every WebSocket subscriber.

Keep slider updates unmerged initially. A later coalescing policy would need its own first/last
time and count semantics and could hide intermediate operations. Smaller payloads are independent
of Trail's decision to combine a drag into one record.

**Acceptance:** an unrelated edit to a slide containing a 4 MiB image transmits no image bytes;
attachment replacement sends correct metadata; add/remove never bypass sanitization. Test null,
deletion, escaped keys, arrays, no-ops, payload limits, retry stability, and v1/v2 selection.

## Follow-up feed features

These improve attribution or coverage but are not prerequisites for the initial corrections.

| Feature | Proposed change and condition |
|---|---|
| Save As lineage | Add a dedicated `document-copy/v1` event with source and destination references, emitted after a confirmed successful copy. Capture the source before invoking Save As and obtain the actual destination afterward. Do not infer a copy from nearby `closed`/`opened` events. A cancelled or failed save produces no copy event; saving over an existing destination need not create a new file ID. |
| Save As integration | The emitter's tracker cannot establish lineage alone. Instrument the operation that performs Save As, or add a narrow completion signal upstream if the destination is unavailable through public APIs. In the installed JupyterLab [context implementation](/home/arstneio/PyCharmProjects/eventfeed/node_modules/@jupyterlab/docregistry/src/context.ts:299), `saveAs()` returning `true` does not itself establish success. |
| Source events | Add `source/v1` using `sharedSourcesChanged`, extending the shared signal with old values and transaction locality. Use the v2 serializer for potentially large data. Report source IDs and useful resource references so a layer can be connected to its inputs. Exclude known credentials such as OpenEO `authBearer` before emitting values. |
| Other map edits | If upstream wants action attribution beyond sources, add a document-change event listing the changed sections: layer tree, options, annotations, stories, presets, or metadata. Preserve locality. Trail's saved-file comparisons still provide the actual stored result. |
| User identity | Keep `subject` unchanged. Add optional reporter identity from local awareness, clearly identified as client-supplied. It names the reporter, not the author of a remote transaction. Authenticated attribution requires server-side enrichment; copying the local user's name onto remote changes would be misleading. |
| Schema discovery | Prefer a generic authenticated Jupyter Server schema-listing endpoint. A JupyterGIS-specific capability endpoint is optional if that is not available. Trail can continue discovering schemas from received events. |

## Documentation and integration tests

Update [events.md](/home/arstneio/PyCharmProjects/eventfeed/docs/contributor_guide/explanation/events.md)
with each corresponding implementation step. Its contract should state that layer events report
observed in-memory changes, not completed saves; Python changes may be observed remotely when a
browser is present; subjects identify clients; optional metadata has the scope defined above;
and missing closure or sequence gaps remain possible.

Correct the stale viewport-throttling paragraph and `deleted` wording in `layer_v1.yaml`.
Document the outer POST envelope versus the forwarded payload, including the prohibition on
`data.schema_id`. Include `.qgs` and `.qgz` in path descriptions and examples when QGIS-backed
tests verify them. Explain v2's omitted values and version-selection setting.

Add focused browser-emitter unit tests under `python/jupytergis_core/src/eventFeed/__tests__/`.
The core package currently lacks a `test` script and Jest configuration; add these using the
repository's existing Jest setup, with a separate test tsconfig. Shared-document tests belong in
`packages/schema/src/__tests__/`. Extend `test_events.py` with old/new valid payloads and invalid
metadata cases; do not rely solely on checking whether a schema ID appears in a log string.

Add a functional browser test in `ui-tests/tests/event-feed.spec.ts` that subscribes to the real
WebSocket before acting. Assert event content and lifecycle across two pages and a notebook view.
Use temporary copies of maps, a temporary server root, and an isolated collaboration store;
opening a committed example can modify it. Include a separate HTTP/WebSocket transport test
covering validation, injected failures, and subscribers' received sequence fields.

For implementation, use the repository's existing `pnpm run test` and `pytest -v python` suites,
the affected TypeScript builds and lint checks, and the new functional UI test through the
`ui-tests` package. Run QGIS-backed cases in the QGIS environment and JupyterLite cases where
shared-document initialization or migration changes apply. Record baseline failures separately.
No implementation test result is claimed by this planning note.

## Changes that belong in other upstream packages

The earlier notes report the following dependency failures. Carry their reproduction scripts and
exact dependency versions into separate issues or fixes; recheck current upstream behavior before
submission. They must not be presented as bugs repaired by adding event fields.

| Owner | Change to pursue | Regression case |
|---|---|---|
| jupyter_server | Expose event metadata to subscribers; add optional `last_modified` and `size` to contents-save events from the completed save model. | Fields reach a subscriber and correspond to the completed write. |
| jupyter_server_fileid | Update descendant file IDs when a parent is renamed even if the parent has no ID; avoid treating literal path characters as pattern syntax. | Rename unopened parents and names containing brackets, `?`, or `*`; neighboring paths are unaffected. |
| JupyterLab | Escape paths used to update open document contexts after directory rename. | Rename a folder containing `(` and verify all open contexts follow it. |
| jupyter-collaboration | Correct ownership of a provider shared by tab and notebook models. | Close either view first; edits in the survivor still save. |
| jupyter-collaboration / file-ID provider | Follow externally moved files where the provider supports it, or surface a persistent save failure. | Move an open map outside Jupyter, then edit it. |
| jupyter-collaboration | Await or explicitly resolve pending saves at shutdown. | Stop the server inside the autosave delay and verify the final accepted edit. |
| jupyter-collaboration | Detect external replacement beyond modification time alone and reconcile it with an in-flight autosave. | Restore a checkpoint preserving mtime; reproduce the documented delayed-read race. |
| jupyter-collaboration | Emit successful-save status only after a write actually completes. | A skipped/no-op save does not claim it wrote content. |
| jupyter-collaboration | Make store location explicit and document a suitable application-state location. | Starting the server in a project need not create `.jupyter_ystore.db` there. This is a storage-policy issue, not lost edits. |

The notes' successful `LocalFileIdManager` workaround is useful configuration guidance, not a
guarantee for every filesystem or file replacement. Likewise, `last_modified` plus `size`
improves save matching but cannot make it exact: two writes can have the same size and timestamp.
Neither those fields nor a save notification prove that every preceding GUI edit survived a
concurrent change. Exact attribution would require a saved revision or content identifier.

## Status and review order

Implement on a local `codex/eventfeed-fixes` branch from the eventfeed branch checked out when
implementation starts, currently `prototyping`. Keep one step uncommitted for review at a time,
run its checks, and tick its item as part of that step. Upstream submission is a later action;
the plan branch is reviewed locally.

- [ ] 1. Propagate and emit `local`; extend schema and transaction tests.
- [ ] 2. Add the document-reference resolver, room IDs, and current drive-aware paths.
- [ ] 3. Introduce `DocumentFeed`, correct widget ownership, initialization, and cleanup.
- [ ] 4. Add stream IDs, sequence numbers, timestamps, the bounded transport queue, and delivery tests.
- [ ] 5. Complete viewport coverage and test final emission before close. Add the best-effort page-close transport after step 4 supplies stable sequence IDs.
- [ ] 6. Make Python and TypeScript migration IDs deterministic; replace UUID-mocked fixtures.
- [ ] 7. Correct notebook origin paths and ephemeral references; settle explicit file creation behavior.
- [ ] 8. Add opt-in `layer/v2`, bounded serialization, attachment metadata, and migration guidance.

Steps 1–4 are the first useful upstream feed change set. Step 5 completes viewport handling.
The migration fix can land independently; notebook API changes deserve a separate review; v2
deserves a separate schema review. Save As lineage, source events, user identity, and dependency
fixes follow as separately scoped work. Each delivered step includes its documentation and tests.
