# JupyterGIS event-feed changes

| AI-generated patch against `abecc972` | Unique files | Added lines | Removed lines | Net lines |
|---|---:|---:|---:|---:|
| **Runtime TypeScript** | **16** | **1,133** | **67** | **1,066** |
| Event schemas | 5 | 273 | 2 | 271 |

Feature tables follow the [Trail event coverage summary](11-12-3-event-coverage-summary.md). Runtime totals exclude tests, event schemas, documentation, examples, and configuration. Counts are Git diff lines, including comments and blank lines. Shared runtime lines are counted once.

## Cell events

| File | Change | Added | Removed |
|---|---|---:|---:|
| [executions.ts](../../../eventfeed/python/jupytergis_core/src/eventFeed/executions.ts) | Observe notebook and console execution requests | 113 | 0 |
| [notebook_v1.yaml](../../../eventfeed/python/jupytergis_core/jupytergis_core/event_schemas/notebook_v1.yaml) | Define the request payload | 44 | 0 |
| [plugin.ts](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L167) | Connect the request observer | 2 | 0 |
| **Runtime code subtotal** | | **115** | **0** |
| **Event schema subtotal** | | **44** | **0** |

## Map events

| File | Change | Added | Removed |
|---|---|---:|---:|
| [newDocuments.ts](../../../eventfeed/packages/schema/src/newDocuments.ts) | Track newly created maps until their first view | 56 | 0 |
| [index.ts](../../../eventfeed/packages/schema/src/index.ts) | Export the new-map registry | 1 | 0 |
| [plugins.ts](../../../eventfeed/python/jupytergis_core/src/jgisplugin/plugins.ts#L290) | Register maps created by the new-map command | 13 | 4 |
| [notebookrenderer.ts](../../../eventfeed/python/jupytergis_lab/src/notebookrenderer.ts#L230) | Register maps created in notebooks | 3 | 0 |
| [focus.ts](../../../eventfeed/python/jupytergis_core/src/eventFeed/focus.ts) | Track map tab activation and deactivation | 206 | 0 |
| [document_v1.yaml](../../../eventfeed/python/jupytergis_core/jupytergis_core/event_schemas/document_v1.yaml) | Add creation and focus fields to the document schema | 33 | 2 |
| [plugin.ts](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L30) | Emit creation and focus events alongside existing open and close events | 11 | 3 |
| **Runtime code subtotal** | | **290** | **7** |
| **Event schema subtotal** | | **33** | **2** |

## Viewport events

| File | Change | Added | Removed |
|---|---|---:|---:|
| [viewportMoves.ts](../../../eventfeed/packages/base/src/mainview/viewportMoves.ts) | Detect completed local pan and zoom gestures | 245 | 0 |
| [mainView.tsx](../../../eventfeed/packages/base/src/mainview/mainView.tsx#L575) | Connect tracking to the map view and camera operations | 22 | 0 |
| [mainviewmodel.ts](../../../eventfeed/packages/base/src/mainview/mainviewmodel.ts) | Initialize the view ID | 1 | 1 |
| [interfaces.ts](../../../eventfeed/packages/schema/src/interfaces.ts#L83) | Define the viewport movement signal | 7 | 0 |
| [model.ts](../../../eventfeed/packages/schema/src/model.ts#L665) | Expose completed movements from the map model | 9 | 0 |
| [viewport.ts](../../../eventfeed/python/jupytergis_core/src/eventFeed/viewport.ts) | Emit the final center, zoom, and extent | 35 | 0 |
| [plugin.ts](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L205) | Connect the move signal and remove the 10-second timer | 2 | 48 |
| **Runtime code subtotal** | | **321** | **49** |

## Tool events

| File | Change | Added | Removed |
|---|---|---:|---:|
| [mode.ts](../../../eventfeed/python/jupytergis_core/src/eventFeed/mode.ts) | Report old and new tools in page event order | 56 | 0 |
| [mode_v1.yaml](../../../eventfeed/python/jupytergis_core/jupytergis_core/event_schemas/mode_v1.yaml) | Define the interaction-mode payload | 55 | 0 |
| [plugin.ts](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L188) | Connect mode tracking | 2 | 0 |
| **Runtime code subtotal** | | **58** | **0** |
| **Event schema subtotal** | | **55** | **0** |

## Layer events

| File | Change | Added | Removed |
|---|---|---:|---:|
| [plugin.ts](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L63) | Add source references, mark changes from another document copy as `local: false`, and suppress unchanged writes | 30 | 9 |
| [doc.ts](../../../eventfeed/packages/schema/src/doc.ts#L597) | Capture the layer transaction origin | 2 | 0 |
| [interfaces.ts](../../../eventfeed/packages/schema/src/interfaces.ts#L100) | Expose origin on layer changes | 2 | 0 |
| [layer_v1.yaml](../../../eventfeed/python/jupytergis_core/jupytergis_core/event_schemas/layer_v1.yaml) | Allow source references and transaction origin. Trail derives Filter Added from the existing old and new layer states. | 50 | 0 |
| **Runtime code subtotal** | | **34** | **9** |
| **Event schema subtotal** | | **50** | **0** |

## Source events

| File | Change | Added | Removed |
|---|---|---:|---:|
| [plugin.ts](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L112) | Emit added, updated, and removed references | 36 | 0 |
| [doc.ts](../../../eventfeed/packages/schema/src/doc.ts#L628) | Capture the old source and transaction origin | 4 | 0 |
| [interfaces.ts](../../../eventfeed/packages/schema/src/interfaces.ts#L119) | Expose source history and origin | 3 | 0 |
| [source_v1.yaml](../../../eventfeed/python/jupytergis_core/jupytergis_core/event_schemas/source_v1.yaml) | Define source event payloads | 91 | 0 |
| **Runtime code subtotal** | | **43** | **0** |
| **Event schema subtotal** | | **91** | **0** |

## Shared by layer and source events

| File | Change | Added | Removed |
|---|---|---:|---:|
| [sources.ts](../../../eventfeed/python/jupytergis_core/src/eventFeed/sources.ts) | Resolve paths, sanitize URLs, and cache source references | 258 | 0 |
| [plugin.ts](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L201) | Connect the shared reference cache | 5 | 1 |
| **Runtime code subtotal** | | **263** | **1** |

## Shared event-feed wiring

| File | Change | Added | Removed |
|---|---|---:|---:|
| [plugin.ts](../../../eventfeed/python/jupytergis_core/src/eventFeed/plugin.ts#L152) | Set up shared event wiring | 9 | 1 |
| **Runtime code subtotal** | | **9** | **1** |

## Supporting changes

| Area | Files | Added | Removed |
|---|---:|---:|---:|
| Documentation | 1 | 112 | 9 |
| Example maps and notebooks | 4 | 273 | 89 |
| Build, packaging, and dependencies | 10 | 31 | 32 |
| **Total** | **15** | **416** | **130** |

## Trail coverage without a JupyterGIS patch

| Coverage entries | Where the record comes from | Added | Removed |
|---|---|---:|---:|
| Cell Recovered | Trail reads kernel history. Cell Started, Completed, and Errored share the request integration in the Cell events table. | 0 | 0 |
| Kernel Attached, Restarted, Detached | Trail tracks Jupyter Server sessions and kernel messages. | 0 | 0 |
| Notebook Saved | Trail records Jupyter Server contents-service events. | 0 | 0 |
| File Created, Modified, Opened, Moved, Deleted | Trail observes filesystem activity. | 0 | 0 |
| GeoPackage Changed, Raster Changed, Zarr Store Written, Zarr Store Changed, Map File Changed, File Changed While Off | Trail uses filesystem events and asset metadata. Example map edits are counted under Supporting changes. | 0 | 0 |
| Trail Initialized | Trail records its project initialization. | 0 | 0 |
