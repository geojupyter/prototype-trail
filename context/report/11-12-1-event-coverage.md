## 1. Notebook execution

| Event | Markdown sources |
|---|---|
| Cell executed or rerun. Capture code and execution number from IOPub. | [ipython-history-report.md:24](11-3-1-ipython-history.md#L24) |
| Cell started, completed, or errored. Collect timing, output, and errors by request ID. | [ipython-history-report.md:36](11-3-1-ipython-history.md#L36) |
| Notebook and cell attribution for a run. Associate notebooks with kernels and capture cell identity where available. | [ipython-history-report.md:30](11-3-1-ipython-history.md#L30) · [ipython-history-report.md:49](11-3-1-ipython-history.md#L49) |

## 2. File activity and saves

| Event | Markdown sources |
|---|---|
| File created or overwritten. Fold notifications into one observed write session per path. | [event-folding-report.md:214](11-4-3-event-folding.md#L214) · [event-folding-report.md:247](11-4-3-event-folding.md#L247) |
| File opened, moved, deleted, or replaced. Standalone read-only records are open. Treat temporary-file replacement as modification. | [event-folding-report.md:284](11-4-3-event-folding.md#L284) |
| Notebook or other Jupyter file saved or autosaved. Group consecutive saves and associated watcher activity. | [jupyter-submodule-plan.md:1856](../plan/10-28-2-jupyter-submodule.md#L1856) · [jupyter-submodule-plan.md:1930](../plan/10-28-2-jupyter-submodule.md#L1930) |
| Directory dataset accessed or modified. Access grouping exists. Grouping separate shapefile parts remains open. | [event-folding-report.md:199](11-4-3-event-folding.md#L199) · [event-folding-report.md:269](11-4-3-event-folding.md#L269) |

## 3. File changes detected from metadata

| Event | Markdown sources |
|---|---|
| Saved map project changed. Compare QGIS (`.qgs`, `.qgz`) and JupyterGIS (`.jgis`) summaries for layer, source, order, or projection changes. | [asset-metadata-plan.md:375](../plan/11-10-1-asset-metadata.md#L375) · [file-events-plan.md:402](../plan/10-29-3-file-events.md#L402) · [file-events-plan.md:425](../plan/10-29-3-file-events.md#L425) |
| Vector dataset or table changed. Compare counts, fields, geometry, bounds, or CRS. For example, check a tracked GeoPackage after a notebook export or a watcher notification. | [asset-metadata-plan.md:306](../plan/11-10-1-asset-metadata.md#L306) · [asset-metadata-plan.md:2052](../plan/11-10-1-asset-metadata.md#L2052) |
| Raster or array changed. Compare bands, nodata, dimensions, variables, shape, or dtype. | [asset-metadata-plan.md:360](../plan/11-10-1-asset-metadata.md#L360) · [asset-metadata-plan.md:2052](../plan/11-10-1-asset-metadata.md#L2052) |

## 4. Live JupyterGIS editor events

| Event | Markdown sources |
|---|---|
| Map opened, closed, or created. Open and close records exist. Distinguish newly created maps. | [remaining-baseline-events-plan.md:157](../plan/11-4-2-remaining-baseline-events.md#L157) |
| Map activated, deactivated, panned, or zoomed. These events exist in the local prototype. | [jupytergis-focus-events-plan.md:237](../plan/10-31-1-jupytergis-focus-events.md#L237) · [viewport-moveend-plan.md:63](../plan/11-1-1-viewport-moveend.md#L63) |
| Layer added, updated, or removed. Add origin information to identify replicated reports across views. | [jupytergis-focus-events-plan.md:247](../plan/10-31-1-jupytergis-focus-events.md#L247) · [eventfeed-upstream-plan.md:65](../plan/10-29-2-eventfeed-upstream.md#L65) |
| Layer source linked, added, changed, or removed. Report data references and link tracked assets. | [remaining-baseline-events-plan.md:221](../plan/11-4-2-remaining-baseline-events.md#L221) · [remaining-baseline-events-plan.md:241](../plan/11-4-2-remaining-baseline-events.md#L241) |
| Map filter added, changed, or removed. Describe the predicates that changed in the layer settings. | [remaining-baseline-events-plan.md:289](../plan/11-4-2-remaining-baseline-events.md#L289) |
| Map tool selected or reset. Order tool switches with map focus changes. | [remaining-baseline-events-plan.md:315](../plan/11-4-2-remaining-baseline-events.md#L315) |

## 5. Project and tracking lifecycle

| Event | Markdown sources |
|---|---|
| Project initialized. Record the initial tracking rules before discovery. | [remaining-baseline-events-plan.md:138](../plan/11-4-2-remaining-baseline-events.md#L138) |
| Asset discovered, tracked, or untracked. Records exist. Extend the geospatial preset to discover saved maps. | [remaining-baseline-events-plan.md:184](../plan/11-4-2-remaining-baseline-events.md#L184) |
| Trail started or shut down gracefully. Optional session records. | [remaining-baseline-events-plan.md:153](../plan/11-4-2-remaining-baseline-events.md#L153) |

## 6. Follow-ups and research

| Event | Markdown sources |
|---|---|
| Missed execution recovered from IPython history. | [ipython-history-report.md:51](11-3-1-ipython-history.md#L51) |
| Notebook tab switched. Extend focus coverage beyond map tabs. | [jupytergis-focus-events-plan.md:123](../plan/10-31-1-jupytergis-focus-events.md#L123) |
| Saved notebook contents edited. An `.ipynb` comparison reader is a candidate. | [jupyter-submodule-plan.md:413](../plan/10-28-2-jupyter-submodule.md#L413) · [asset-metadata-plan.md:2093](../plan/11-10-1-asset-metadata.md#L2093) |
| File changed between Trail runs. Detection needs a persistence design. | [asset-metadata-plan.md:2029](../plan/11-10-1-asset-metadata.md#L2029) |
| Write attributed to a process or operation. Evaluate native collectors and explicit writer signals. | [native-file-events-report.md:218](11-5-1-native-file-events.md#L218) · [event-folding-report.md:304](11-4-3-event-folding.md#L304) |
| Map copied with Save As, or live layer tree, options, annotations, stories, or presets changed. | [eventfeed-upstream-plan.md:325](../plan/10-29-2-eventfeed-upstream.md#L325) |
