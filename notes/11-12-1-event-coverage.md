## 1. Notebook execution

| Event | Markdown sources |
|---|---|
| Cell executed or rerun. Capture code and execution number from IOPub. | [ipython-history-report.md:24](11-3-1-ipython-history-report.md#L24) |
| Cell started, completed, or errored. Collect timing, output, and errors by request ID. | [ipython-history-report.md:36](11-3-1-ipython-history-report.md#L36) |
| Notebook and cell attribution for a run. Associate notebooks with kernels and capture cell identity where available. | [ipython-history-report.md:30](11-3-1-ipython-history-report.md#L30) · [ipython-history-report.md:49](11-3-1-ipython-history-report.md#L49) |

## 2. File activity and saves

| Event | Markdown sources |
|---|---|
| File created or overwritten. Fold notifications into one observed write session per path. | [event-folding-report.md:214](11-4-3-event-folding-report.md#L214) · [event-folding-report.md:247](11-4-3-event-folding-report.md#L247) |
| File opened, moved, deleted, or replaced. Standalone read-only records are open. Treat temporary-file replacement as modification. | [event-folding-report.md:284](11-4-3-event-folding-report.md#L284) |
| Notebook or other Jupyter file saved or autosaved. Group consecutive saves and associated watcher activity. | [jupyter-submodule-plan.md:1856](10-28-2-jupyter-submodule-plan.md#L1856) · [jupyter-submodule-plan.md:1930](10-28-2-jupyter-submodule-plan.md#L1930) |
| Directory dataset accessed or modified. Access grouping exists. Grouping separate shapefile parts remains open. | [event-folding-report.md:199](11-4-3-event-folding-report.md#L199) · [event-folding-report.md:269](11-4-3-event-folding-report.md#L269) |

## 3. File changes detected from metadata

| Event | Markdown sources |
|---|---|
| Saved map project changed. Compare QGIS (`.qgs`, `.qgz`) and JupyterGIS (`.jgis`) summaries for layer, source, order, or projection changes. | [asset-metadata-plan.md:375](11-10-1-asset-metadata-plan.md#L375) · [file-events-plan.md:402](10-29-3-file-events-plan.md#L402) · [file-events-plan.md:425](10-29-3-file-events-plan.md#L425) |
| Vector dataset or table changed. Compare counts, fields, geometry, bounds, or CRS. For example, check a tracked GeoPackage after a notebook export or a watcher notification. | [asset-metadata-plan.md:306](11-10-1-asset-metadata-plan.md#L306) · [asset-metadata-plan.md:2052](11-10-1-asset-metadata-plan.md#L2052) |
| Raster or array changed. Compare bands, nodata, dimensions, variables, shape, or dtype. | [asset-metadata-plan.md:360](11-10-1-asset-metadata-plan.md#L360) · [asset-metadata-plan.md:2052](11-10-1-asset-metadata-plan.md#L2052) |

## 4. Live JupyterGIS editor events

| Event | Markdown sources |
|---|---|
| Map opened, closed, or created. Open and close records exist. Distinguish newly created maps. | [remaining-baseline-events-plan.md:157](11-4-2-remaining-baseline-events-plan.md#L157) |
| Map activated, deactivated, panned, or zoomed. These events exist in the local prototype. | [jupytergis-focus-events-plan.md:237](10-31-1-jupytergis-focus-events-plan.md#L237) · [viewport-moveend-plan.md:63](11-1-1-viewport-moveend-plan.md#L63) |
| Layer added, updated, or removed. Add origin information to identify replicated reports across views. | [jupytergis-focus-events-plan.md:247](10-31-1-jupytergis-focus-events-plan.md#L247) · [eventfeed-upstream-plan.md:65](10-29-2-eventfeed-upstream-plan.md#L65) |
| Layer source linked, added, changed, or removed. Report data references and link tracked assets. | [remaining-baseline-events-plan.md:221](11-4-2-remaining-baseline-events-plan.md#L221) · [remaining-baseline-events-plan.md:241](11-4-2-remaining-baseline-events-plan.md#L241) |
| Map filter added, changed, or removed. Describe the predicates that changed in the layer settings. | [remaining-baseline-events-plan.md:289](11-4-2-remaining-baseline-events-plan.md#L289) |
| Map tool selected or reset. Order tool switches with map focus changes. | [remaining-baseline-events-plan.md:315](11-4-2-remaining-baseline-events-plan.md#L315) |

## 5. Project and tracking lifecycle

| Event | Markdown sources |
|---|---|
| Project initialized. Record the initial tracking rules before discovery. | [remaining-baseline-events-plan.md:138](11-4-2-remaining-baseline-events-plan.md#L138) |
| Asset discovered, tracked, or untracked. Records exist. Extend the geospatial preset to discover saved maps. | [remaining-baseline-events-plan.md:184](11-4-2-remaining-baseline-events-plan.md#L184) |
| Trail started or shut down gracefully. Optional session records. | [remaining-baseline-events-plan.md:153](11-4-2-remaining-baseline-events-plan.md#L153) |

## 6. Follow-ups and research

| Event | Markdown sources |
|---|---|
| Missed execution recovered from IPython history. | [ipython-history-report.md:51](11-3-1-ipython-history-report.md#L51) |
| Notebook tab switched. Extend focus coverage beyond map tabs. | [jupytergis-focus-events-plan.md:123](10-31-1-jupytergis-focus-events-plan.md#L123) |
| Saved notebook contents edited. An `.ipynb` comparison reader is a candidate. | [jupyter-submodule-plan.md:413](10-28-2-jupyter-submodule-plan.md#L413) · [asset-metadata-plan.md:2093](11-10-1-asset-metadata-plan.md#L2093) |
| File changed between Trail runs. Detection needs a persistence design. | [asset-metadata-plan.md:2029](11-10-1-asset-metadata-plan.md#L2029) |
| Write attributed to a process or operation. Evaluate native collectors and explicit writer signals. | [native-file-events-report.md:218](11-5-1-native-file-events-report.md#L218) · [event-folding-report.md:304](11-4-3-event-folding-report.md#L304) |
| Map copied with Save As, or live layer tree, options, annotations, stories, or presets changed. | [eventfeed-upstream-plan.md:325](10-29-2-eventfeed-upstream-plan.md#L325) |
