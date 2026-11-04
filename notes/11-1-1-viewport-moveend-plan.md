# Plan: prompt viewport events

Replace JupyterGIS's 10-second inactivity debounce with one event at the end of each local pan or zoom. This is feasible with Trail's current event format and append-only console. The change belongs mainly in `../eventfeed`; Trail needs regression coverage, not a new event lifecycle.

This reports the **first completed movement**, not the first wheel tick. OpenLayers still finishes the gesture and its animation before `moveend`. Continuous movement can therefore postpone the event, but there is no additional wait after completion. If the requirement becomes “show something while movement is still happening,” this plan does not satisfy it; that needs a separate progress display or event contract.

## Evidence and scope

Checked on 2026-11-01 against the working trees of eventfeed (`focus`, HEAD `fb9b71fd`) and Trail (`triage`, HEAD `e2b2118`). Both contain existing uncommitted changes. This document proposes changes; it does not implement them.

- The [viewport emitter](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts:82) restarts `VIEWPORT_SETTLE_MS = 10_000` on every local awareness update. That is the deliberate delay.
- The [map component](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx:613) updates viewport awareness through a 200 ms throttle attached only to `change:center`. A zoom that leaves the center fixed can be missed. Its existing `moveend` handler already reads the final view and saves map options.
- The installed OpenLayers 10.9.0 [implementation](/home/arstneio/PyCharmProjects/eventfeed/node_modules/ol/Map.js:1676) dispatches `moveend` once the view is neither interacting nor animating. It does not wait for tile loading. The public [Map API](https://openlayers.org/en/latest/apidoc/module-ol_Map-Map.html) distinguishes movement completion from rendering completion.
- Trail's [subscription](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/subscription.py:37) passes each WebSocket message directly to its [consumer](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/consumer.py:98), which appends the event. The two-second session interval discovers servers; it does not batch subscribed messages.
- Trail already has [ViewportV1Event](/home/arstneio/PyCharmProjects/trail/src/trail/jupyter/event.py:232), with center, zoom and extent. Its [console feed](/home/arstneio/PyCharmProjects/trail/src/trail/cli/feed.py:21) prints rows once. An event containing the completed movement needs no subsequent update.

The initial scope is direct map navigation: drag, wheel/trackpad, touch zoom, keyboard navigation and zoom controls. Automatic initialization, collaborator following, restoring the local view after following, resizing and programmatic camera changes must not become local navigation events. GUI commands such as “zoom to layer” can be added separately with explicit origin information; they must not be guessed from any camera change.

## How the pieces fit

The map component knows when movement finishes and whether local navigation caused it. The document model carries that completed snapshot through a new local signal. The event-feed plugin converts the signal into the existing `viewport/v1` message. Trail receives and displays it through its existing path.

Keep the existing awareness stream for collaboration. It has different timing and includes remote updates, so it should not also serve as the event-feed completion signal.

## Capture completed local navigation

Add a small `ViewportMoves` helper beside [MainView](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx:252), in `packages/base/src/mainview/viewportMoves.ts`. `MainView` owns one helper per rendered map. The helper observes navigation and `moveend`, and calls the model only for an eligible completed movement.

Its behavior should be:

- Mark movement caused by an accepted local navigation interaction. Include the zoom controls, whose target can be outside the map viewport. A generic click, pointer-down or document keypress is not sufficient evidence of navigation.
- Associate that evidence with the current movement, including kinetic panning and zoom animation. Clear it after completion or cancellation. A no-op at a zoom limit must not leave permission for a later programmatic movement to emit.
- Suppress initialization and automatic camera operations. Clear pending local navigation when entering follow mode; keep follow restoration suppressed through its completion even though `remoteUser` has already cleared. The current [follow handler](/home/arstneio/PyCharmProjects/eventfeed/packages/base/src/mainview/mainView.tsx:2670) contains both paths.
- Read center, zoom and extent directly from the current OpenLayers view at `moveend`, using the current map size. Copy the values into a snapshot. Do not read the last throttled awareness value: it can be stale or absent for fixed-center zoom.
- Accept zoom `0`; test for missing or non-finite values instead of truthiness. Reject missing size and incomplete snapshots. Compare the completed state with the state at navigation start to suppress no-op movements.
- Establish the initial baseline without emitting. Do not unconditionally discard the first `moveend`, because it may be the user's first real movement. Reset the baseline on view replacement and automatic movement.
- Remove map, input and control listeners on unmount. Pending movement must not publish after closure.

`moveend` itself contains no reliable user-origin flag. Checking only `localState.remoteUser` would therefore be insufficient. The first implementation step must prove the interaction tracking with real browser input before changing the feed. Keep it local to navigation; if it requires a general camera-origin framework across unrelated features, stop and reassess the scope rather than expanding this demo fix.

## Carry the snapshot through the model

The document model, `JupyterGISModel` in `packages/schema/src/model.ts`, is already how the plugin observes map activity. Extend its public contract, [IJupyterGISModel](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/interfaces.ts:307), with a `viewportMoved` signal and a `notifyViewportMoved` method used by `ViewportMoves`.

Use a small typed payload containing `viewport: IViewPortState` and `emitter: string`, the originating main-view ID. The existing [IViewPortState](/home/arstneio/PyCharmProjects/eventfeed/packages/schema/src/interfaces.ts:77) already defines coordinates, zoom and extent. The view ID keeps ownership explicit when multiple views share a document model; it need not be added to the wire format.

This signal is local to the browser model. Emitting it must not update awareness or shared document options. Preserve the map's existing option-saving and collaboration behavior. Model disposal clears its signal connections; each map helper separately disposes its own listeners.

## Publish immediately through the existing feed

Replace `wireViewportEvents`' awareness listener with a listener to `viewportMoved`. Remove its pending snapshot, timeout and 10-second constant. For each signal, call the existing event emitter immediately with:

- Schema `https://schema.jupytergis.org/events/viewport/v1`, version `'1'`.
- The model's client ID as `subject`, and `moved` as `verb`.
- The model's current file path as `object.document_path` and the completed snapshot as `object.new`.

Keep one subscription per model, using the plugin's existing model-deduplication approach. Tie the subscription to the model rather than the first widget: closing one of two views must not disable the remaining view. Guard deferred initial-sync callbacks against disposed models. Map readiness and input-origin filtering prevent initialization events; subscription timing alone does not.

The [viewport schema](/home/arstneio/PyCharmProjects/eventfeed/python/jupytergis_core/jupytergis_core/event_schemas/viewport_v1.yaml) already describes a viewport after movement, so no schema version or Trail runtime change is needed. Preserve the existing emission error reporting. Update the [feed explanation](/home/arstneio/PyCharmProjects/eventfeed/docs/contributor_guide/explanation/events.md) to remove both the 10-second instruction and the stale claim that viewport events are unimplemented.

Several separate movements can produce several rows. Keep them as separate immutable events. Folding or rewriting console history is outside this change and must not add another wait.

## Implementation order and status

Implement one step at a time, run its checks, tick its checkbox and leave it uncommitted for review. Start the next step only after the preceding commit or an instruction to continue. Use the existing local branches: `focus` in eventfeed and, when needed, `triage` in Trail. Preserve existing work; do not stage it, rewrite it, push or create a PR.

- [x] **Step 1 — completed-movement signal.** Add the model contract and per-map helper, with unit tests and a browser test exercising real interactions. Leave the old feed connected for this step. Verify the new signal handles fixed-center zoom and filters initialization, resize, follow/restore and programmatic movement. Run the eventfeed JavaScript suite and build. Stop here if origin filtering cannot remain bounded.
- [x] **Step 2 — immediate feed emission.** Switch the plugin to the signal, remove the debounce, add emitter and lifecycle tests, and revise the feed documentation. Run the JavaScript suite, build, Python event-schema tests and browser regression. Rebuild the installed extension and reload JupyterLab before evaluating the demo.
- [ ] **Step 3 — Trail verification.** Add a regression for two consecutive viewport messages with different final snapshots: each is recorded immediately, keeps its own payload, renders correctly and survives reopening the Trail. Run Trail's full test suite and the live demo check below. Change runtime code only if this exposes an actual compatibility defect.

Use `pnpm run test` and `pnpm run build` from eventfeed, and `python -m pytest python/jupytergis_core/jupytergis_core/tests/test_events.py` in its development environment. Run the new browser case through the existing `ui-tests` Playwright setup. In Trail, use `.venv/bin/python -m pytest`.

## Acceptance checks

Unit tests establish the signal and payload contract; browser checks establish that real navigation reaches it. Use an already connected Trail watching a saved map inside its project, with the normal feed visible rather than an active triage screen.

| Action | Required result |
|---|---|
| Open a map, then perform the first zoom | No initialization event; first completed zoom produces one event. |
| Zoom buttons with unchanged center; zoom to level zero | Final zoom and extent arrive correctly. |
| Wheel/trackpad zoom, drag with inertia, touch or keyboard navigation | Event follows movement completion, without the extra inactivity delay. |
| Two movements separated by a pause | Both completion snapshots appear; the second does not replace the first. |
| Resize, click without moving, or attempt zoom beyond its limit | No navigation event or stale pending intent. |
| Follow, receive remote movement, then unfollow | No falsely attributed local movement; the next real local navigation still emits. |
| Two views share a model; close one and navigate the other | One event per originating movement; surviving view remains connected. |
| Close during animation or before initial sync finishes | No delayed publication or leaked subscription. |

Measure completion-to-emission in the browser and completion-to-display in the live demo separately. The plugin must call `emit` in the signal callback, without a scheduled settling delay. Target under one second from `moveend` to the Trail row on the local demo setup; report the observed result rather than promising a network-independent bound. Do not use tile-load completion as the timing reference.

Validation performed while writing this plan: `.venv/bin/python -m pytest tests/jupyter/test_events.py -q` — **14 passed in 0.73s**. This verifies the current Trail event tests; the proposed browser behavior remains to be implemented and tested.
