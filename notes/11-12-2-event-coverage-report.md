# Event coverage

Trail keeps one readable history of a project. This round added the events that show what a researcher does in their notebooks, maps and data files. The first part shows the new events, most important first. The second part covers each area's goal, what was done, what remains and the challenges.

A few terms are used throughout:

- **JupyterLab** is the browser workspace where the user opens notebooks and maps.
- **JupyterGIS** is the map editor inside JupyterLab.
- A **kernel** is the process that runs a notebook's code.
- **Jupyter Server** connects the browser and the kernels, and passes their reports to Trail.

## New events, most important first

Each section says what was added and shows records from the runs of 12 November 2026.

### 1. Notebook cells

Each cell run: its code, notebook, cell and outcome.

```
229. ExecutionEvent
    id: a12bb7d2766a4ea88c03e30715a45a60
    timestamp: 15:38:45.853
    schema_id: trail/notebook-execution/v1
    server: http://localhost:8899/
    src_path: Untitled6.ipynb
    verb: started
    execution_count: 3
    code: 1 / 0
    cell_id: ba3c039d-0daa-4c5f-8531-2e0cd70eb5fb
    kernel_id: b5bdcc4a-4932-4773-835e-8e7a1ab232ba
    entry: fecc6c352c874c8e8948726260ba764e
230. ExecutionEvent
    id: f37cb70887214fd889e15c2593772109
    timestamp: 15:38:45.946
    schema_id: trail/notebook-execution/v1
    server: http://localhost:8899/
    src_path: Untitled6.ipynb
    verb: errored
    execution_count: 3
    duration: 0.094 s
    error: ZeroDivisionError: division by zero
    cell_id: ba3c039d-0daa-4c5f-8531-2e0cd70eb5fb
    kernel_id: b5bdcc4a-4932-4773-835e-8e7a1ab232ba
    entry: fecc6c352c874c8e8948726260ba764e
```

### 2. Changes inside saved files

What changed inside a saved file, for 14 formats.

```
20. FileAccessEvent
    id: 1ea29be9c98f45a79603c4d7418b06d8
    timestamp: 16:29:32.916
    src_path: data/crossings.gpkg
    event_type: modified
    started: 16:29:32.782
    st_size: 96.0 KiB
    st_mtime: 2026-11-12 08:29:32
    change: layer 'crossings' CRS EPSG:4326 → EPSG:3857
    change: layers.crossings.bounds: [-122.27, 37.87, -122.26, 37.87] → [-13611034.13929356, 4561077.415263828, -13609920.9443856...
    change: column 'lanes' added in layer 'crossings'
    entry: 17e63571805c43d598d134fc00ac3bd5
```

### 3. Map layers

Layers added, changed or removed, linked to their data file.

```
22. LayerV1Event
    id: 95a95ee5dbc44c4fa9525e56ca25d88f
    timestamp: 15:33:57.518
    schema_id: https://schema.jupytergis.org/events/layer/v1
    server: http://localhost:8899/
    src_path: maps/city.jGIS
    verb: added
    local: True
    layer_id: layer-crossings
    layer: crossings
    source: GeoJSONSource ../data/crossings.geojson
    resource_path: data/crossings.geojson
    resource_id: 07e0bdcd86ae4552b29f019c362beab9
    entry: 921af778c7f74cd69aaf9be1d53607ae
264. LayerV1Event
    id: ada9341db59d4271a0556ec12cf8e626
    timestamp: 15:53:06.813
    schema_id: https://schema.jupytergis.org/events/layer/v1
    server: http://localhost:8899/
    src_path: maps/city.jGIS
    verb: updated
    local: True
    layer_id: layer-streets
    layer: streets
    change: visible False → True
    change: opacity 0.7 → 0.3
    entry: 921af778c7f74cd69aaf9be1d53607ae
```

### 4. A layer's data file replaced

The old and new file, both linked.

```
25. SourceV1Event
    id: 1d2e2b8e50f7489c95273c871eec90c9
    timestamp: 15:33:58.963
    schema_id: https://schema.jupytergis.org/events/source/v1
    server: http://localhost:8899/
    src_path: maps/city.jGIS
    verb: updated
    local: True
    source_id: src-crossings
    old: GeoJSONSource ../data/crossings.geojson
    new: GeoJSONSource ../data/crossings_2026.geojson
    resource_path: data/crossings_2026.geojson
    resource_id: 2b39bcce5a2a424f839fcf3752abb612
    previous_resource_path: data/crossings.geojson
    previous_resource_id: 07e0bdcd86ae4552b29f019c362beab9
    entry: 921af778c7f74cd69aaf9be1d53607ae
```

### 5. File reads, writes, renames and deletions

One record per use of a file.

```
11. FileAccessEvent
    id: 1e072c7f7d684c8cb439c9be03ad6ef5
    timestamp: 16:29:18.769
    src_path: data/counts.csv
    event_type: opened
    started: 16:29:18.769
    st_size: 19 B
    st_mtime: 2026-11-12 08:29:15
    entry: 84a415bb1f1d4c2c842add1d6d4fd9a4
12. WatchdogEvent
    id: 77564b9ab7324ede9206e1615aa7a6b3
    timestamp: 16:29:22.375
    src_path: data/counts.csv
    dest_path: data/totals.csv
    event_type: moved
    entry: 84a415bb1f1d4c2c842add1d6d4fd9a4
14. WatchdogEvent
    id: 5907dc1d8a23468eace1a4e4efa9cbf5
    timestamp: 16:29:25.854
    src_path: data/totals.csv
    event_type: deleted
    entry: 84a415bb1f1d4c2c842add1d6d4fd9a4
```

### 6. Map filters

Filter conditions added or removed.

```
123. LayerV1Event
    id: 48e0416b5f894c648fa66ddcaac44585
    timestamp: 15:35:24.430
    schema_id: https://schema.jupytergis.org/events/layer/v1
    server: http://localhost:8899/
    src_path: maps/earthquakes.jGIS
    verb: updated
    local: True
    layer_id: ccf98385-bbe3-425b-9dde-fa64d31c196a
    layer: Earthquakes
    filter added: mag > 5
    entry: 7e9b303347af46a183fedb0a0aba436c
```

### 7. Kernels attached to notebooks

Kernel attached, restarted and detached.

```
224. KernelSessionEvent
    id: ee1f1b47455c415fbf1c57057def58d8
    timestamp: 15:38:41.943
    schema_id: trail/kernel-session/v1
    server: http://localhost:8899/
    src_path: Untitled6.ipynb
    verb: attached
    kernel_name: python3
    kernel_id: b5bdcc4a-4932-4773-835e-8e7a1ab232ba
    entry: fecc6c352c874c8e8948726260ba764e
233. KernelSessionEvent
    id: ef4ff016f194498a86cfa9722e758fbb
    timestamp: 15:38:51.033
    schema_id: trail/kernel-session/v1
    server: http://localhost:8899/
    src_path: Untitled6.ipynb
    verb: restarted
    kernel_name: python3
    kernel_id: b5bdcc4a-4932-4773-835e-8e7a1ab232ba
    entry: fecc6c352c874c8e8948726260ba764e
236. KernelSessionEvent
    id: 9254062d941548e884fc63f00fcd9a0c
    timestamp: 15:38:56.036
    schema_id: trail/kernel-session/v1
    server: http://localhost:8899/
    src_path: Untitled6.ipynb
    verb: detached
    kernel_name: python3
    kernel_id: b5bdcc4a-4932-4773-835e-8e7a1ab232ba
    entry: fecc6c352c874c8e8948726260ba764e
```

### 8. Saves from JupyterLab

Consecutive saves grouped into one record.

```
237. ContentsServiceV1Event
    id: 10a1789140c74686900b9ce76e6b41f1
    timestamp: 15:38:53.853
    schema_id: https://events.jupyter.org/jupyter_server/contents_service/v1
    server: http://localhost:8899/
    src_path: Untitled6.ipynb
    started: 15:38:41.508
    saves: 7
    writes: 7
    verb: saved
    entry: fecc6c352c874c8e8948726260ba764e
```

### 9. Maps created, opened and closed

`created` told apart from `opened`.

```
7. DocumentV1Event
    id: 84bb4836fd2b4964aa1ab5c74b1dfdba
    timestamp: 15:33:48.955
    schema_id: https://schema.jupytergis.org/events/document/v1
    server: http://localhost:8899/
    src_path: untitled.jGIS
    verb: created
    entry: efd3dfb6de064a0e9676197c25e1dc96
```

### 10. Map edits from Python

The same layer records, marked `local: False`.

```
99. LayerV1Event
    id: c234a69dfb4e461da3dcffbac0aaa3b7
    timestamp: 15:34:52.573
    schema_id: https://schema.jupytergis.org/events/layer/v1
    server: http://localhost:8899/
    src_path: maps/city.jGIS
    verb: added
    local: False
    layer_id: 1c8ac47f-17c8-4cba-91e2-936819f14a8a
    layer: from python
    source: GeoJSONSource inline data 374 B
    entry: 921af778c7f74cd69aaf9be1d53607ae
```

### 11. Changes made while Trail was off

Found at the next start, with `basis: startup`.

```
42. MetadataEvent
    id: dd056d740bef4eada2f6bd5dc4ce011d
    timestamp: 16:30:09.809
    src_path: data/crossings.gpkg
    format: geopackage
    basis: startup
    st_size: 96.0 KiB
    st_mtime: 2026-11-12 08:30:09
    change: column 'checked' added in layer 'crossings'
    entry: 17e63571805c43d598d134fc00ac3bd5
```

### 12. Cells run before Trail connected

Recovered from the kernel's history.

```
81. ExecutionEvent
    id: a2759c10d2a443bf9215b5bb86a43eee
    timestamp: 15:34:34.435
    schema_id: trail/notebook-execution/v1
    server: http://localhost:8899/
    verb: recovered
    execution_count: 1
    code: from jupytergis import GISDocument (+2 lines)
    paths: ['Untitled1.ipynb']
    kernel_id: 1e6d64f4-bbaa-473d-a832-5cd3c3aa340c
```

### 13. Map focus, pans and zooms

Tab focus, and the view after each move.

```
18. DocumentV1Event
    id: e85df9ef8bb7429ba4b0c0413700f1f3
    timestamp: 15:33:55.163
    schema_id: https://schema.jupytergis.org/events/document/v1
    server: http://localhost:8899/
    src_path: maps/city.jGIS
    verb: activated
    tool: JupyterGIS
    frontend_id: 533e89a9-245b-4e6e-b987-f8a658e47bd1
    focus_seq: 5
    widget_id: id-a26e19a3-d838-4a3c-a6f4-f43eccab84ad
    reason: document_switch
    entry: 921af778c7f74cd69aaf9be1d53607ae
30. ViewportV1Event
    id: 0f7523a0fff44400b36546939ba84c7c
    timestamp: 15:34:01.672
    schema_id: https://schema.jupytergis.org/events/viewport/v1
    server: http://localhost:8899/
    src_path: maps/city.jGIS
    verb: moved
    coordinates: {'x': -13611034.139293559, 'y': 4561086.969892361}
    zoom: 13
    extent: [-13621257.591826702, 4553796.788319662, -13600810.686760416, 4568377.15146506]
    entry: 921af778c7f74cd69aaf9be1d53607ae
```

### 14. Map tools

The tool picked and the one before it.

```
33. ModeV1Event
    id: d4a741417d4e453b9af39a0fa673575c
    timestamp: 15:34:07.394
    schema_id: https://schema.jupytergis.org/events/mode/v1
    server: http://localhost:8899/
    src_path: maps/city.jGIS
    verb: changed
    mode: Add Marker
    previous: none
    frontend_id: 533e89a9-245b-4e6e-b987-f8a658e47bd1
    focus_seq: 6
    entry: 921af778c7f74cd69aaf9be1d53607ae
```

### 15. Project start

The rules the project started with.

```
0. InitTrailEvent
    id: fc0a68cbf8c54764a33007909288aa16
    timestamp: 15:33:19.883
    trail_id: 29a5e6a386ce41658111f379a75c813a
    root: /home/arstneio/.local/share/claude-tmp/claude-1000/-home-arstneio-PyCharmProjects-trail/cc20a7f1-eeac-4526-8e1d-211c983c3d6a/scratchpad/live/final
    ontrail: ['./', '*.jgis', '*.qgz', '*.qgs', '*.feather', '*.geojson', '*.gpkg', '*.shp', '*.tif', '*.tiff', '*.kml', '*.kmz', '*.gpx', '*.parquet', '*.geoparquet', '*.fgb', '*.zarr', '*.gdb', '*.safe', '*.ipynb', '*.csv']
    offtrail: ['.*']
```

The tutorials walk through each of these with more records:

- [notebook.md](../scratch/notebook.md): sections 1, 7, 8 and 12.
- [files.md](../scratch/files.md): sections 2, 5 and 11.
- [gis.md](../scratch/gis.md): sections 3, 4, 6, 9, 10, 13 and 14.

## Goals, progress and challenges

### 1. The goal

Trail should record what someone does in Jupyter and on disk at a level a person can read: which code ran, which files changed and how, and what was done to each map. It should do this without flooding the log. The work had four parts, each with its own plan. All four are done for Linux and were run end to end on 12 November 2026.

### 2. What was done, area by area

#### Notebook execution ([plan](11-3-1-ipython-history-report.md))

**Goal.** Record the code submitted to a kernel, when each cell started and how it ended, and which notebook and cell sent it.

**Done.**

- Each cell gives a `started` and a `completed` or `errored` record. They hold the code, its execution count, the cell id, the duration, output counts and the error name and message.
- The notebook and cell come from JupyterLab's own report of each run, so attribution stays right when several notebooks share one kernel.
- Kernels are recorded as attached to, restarted in and detached from each notebook.
- Cells that ran before Trail connected are recovered from the kernel's history, and a later run of Trail does not record them twice.
- Python and bash kernels were tested. Nothing is installed in the kernel.

**Remaining.**

- Link a cell to the files it wrote. Today only the overlapping times show it.
- Recovered cells have no notebook, because a kernel's history keeps none.
- A kernel that stops while Trail is off is never recorded as detached.
- A notebook's own content edits are not compared yet.

**Challenges.**

- A kernel does not record who sent its code. Trail has to join the kernel's messages with JupyterLab's report, and the two arrive in either order. A `started` record waits up to half a second for the report.
- JupyterLab briefly starts a new notebook's kernel under a made-up name. The live run caught this.

#### Changes inside saved files ([plan](11-10-1-asset-metadata-plan.md))

**Goal.** Say what changed when a QGIS or JupyterGIS project, a vector dataset or a raster is saved.

**Done.**

- 14 formats are compared, from GeoPackage, GeoTIFF, shapefile and Zarr to QGIS and JupyterGIS projects. The change rows sit in the write's own record.
- Trail keeps the last version it read in `.trail/metadata.json`, so changes made while it was off are found at the next start.
- A shapefile's five files are one asset with one record per write.
- Credentials in QGIS connection strings are masked.

**Remaining.**

- Data values are not compared, only structure.
- A rewritten Zarr store gives several records, and GDAL, QGIS and Zarr rewrites show a deletion before the new file.
- QGIS layers are matched by their internal id, so a layer removed and added again counts as two changes.

**Challenges.**

- A file can change while Trail reads it, so Trail checks it before and after reading and retries.
- Trail's own reads must not be logged as user activity.
- Some formats need optional libraries: pyarrow, netCDF4 and h5py.

#### Live JupyterGIS events ([plan](11-4-2-remaining-baseline-events-plan.md))

**Goal.** Record maps opened, closed and created; focus changes, pans and zooms; layers and their data sources; tools; and filter edits.

**Done.**

- All of the above.
- Layer and source records link the data file to the tracked asset.
- Layer updates say what changed.
- Filter edits are described in words.
- Edits from Python are marked `local: False`.
- Closing a dialog with OK and no edits no longer sends an update.

**Remaining.**

- Propose the eventfeed changes upstream to JupyterGIS: the new message types for sources, tools and notebook runs, the `created` verb, and the locality flag.
- With the same map open in two browser windows, each window reports a layer change, so Trail records it twice.
- Focus changes are reported for map tabs only, not notebook tabs.
- Save As, the layer tree, annotations and stories are not reported.

**Challenges.**

- Every open window sees every change to a shared map. Only the window that made it knows it did.
- A Python edit reaches the browser as a change from elsewhere. Dropping all `local: False` copies would therefore drop Python edits too.
- Live records need a browser with the map open.

#### File activity on Linux ([report](11-4-3-event-folding-report.md))

**Goal.** Reduce the spam: one record per use of a file instead of dozens of notifications.

**Done.**

- Writes, reads, renames and deletions are one record each.
- A temporary file renamed over the original counts as a write to the original.
- SQLite journal files and Trail's own reads are hidden.
- A file moved in from another folder, as QGIS saves `.qgz`, ends its record within a second.

**Remaining.**

- macOS and Windows.
- Which program wrote a file.
- A deletion followed by a new file could become one "replaced" record.

**Challenges.**

- Linux merges identical notifications, so counting opens against closes does not work. Trail ends a record one second after the last close, or after ten seconds of quiet.
- The other systems do not report closes at all.

### 3. What else was done

- **Testing.** The live run of everything found five bugs that the tests had missed. All five are fixed, with tests:
  - The watcher stopped silently after a restart.
  - A QGIS save lost its changes.
  - Made-up kernel names were recorded as notebooks.
  - Kernel attachments were recorded twice after a restart.
  - Empty layer updates were recorded.
- **Preset.** The geospatial preset now tracks QGIS and JupyterGIS maps. A new project's first record keeps its starting rules.
- **Cleanup.** `trail check`, whose source file was missing from the branch, was removed. The optional format packages are declared in `pyproject.toml`.
- **Tutorials.** [notebook.md](../scratch/notebook.md), [files.md](../scratch/files.md) and [gis.md](../scratch/gis.md). Their example records are copied from the live logs and were checked line by line.
- **Observed results.**

  | Check | Result |
  |---|---|
  | Trail tests, main environment | 444 passed, 9 skipped |
  | Trail tests, environment with all format packages | 452 passed, 1 skipped |
  | eventfeed unit tests, core package | 49 passed |
  | eventfeed browser tests | 11 passed |
  | eventfeed schema-package unit tests and Python tests | 22 and 35 passed in the previous round, unchanged since |

  All Trail and eventfeed changes are on `event-coverage` branches, uncommitted.

### 4. What remains, and the main challenges

1. **macOS and Windows file activity.** Records rely on Linux's close notifications. The other systems need a different signal for "the writer is done".
2. **Who changed what.** Matching a write to the cell or program that made it. Today the times are the only link. The [native file events report](11-5-1-native-file-events-report.md) has the options.
3. **Upstreaming the eventfeed changes.** The new message types need review by the JupyterGIS maintainers before Trail can rely on them outside this branch.
4. **Duplicate reports from several windows.** The fix needs a reliable way to know which window made a change.
5. **Rewrites that delete and recreate.** GDAL, QGIS and Zarr rewrites could each become one record.
6. **Display.** Event times print in UTC while file times print in local time. This was already the case before this work.
7. **Trail started and stopped.** Only `trail init` writes a record. The log does not show when Trail was not running, so the gap behind a startup change or a recovered cell is not visible. A crash writes no stop record, so the next start would have to note it.

### 5. Open questions

1. Should a recovered cell be attributed to the kernel's only notebook, or stay unattributed because the history cannot prove it?
2. When two windows report the same layer change, should Trail keep both records or only the one from the window that made it?
3. Which eventfeed changes should be proposed to JupyterGIS first, and who reviews the message formats?
4. What comes first: macOS and Windows, or linking writes to the cell that made them?
5. Should pans, zooms and focus changes stay in the default feed, or be hidden like file noise?
6. Should the emojis in Trail's welcome lines be removed? They are still in the console and setup screens.

### 6. Things tried along the way

- **Counting opens against closes to find the end of a write.** It does not work, because Linux merges repeated notifications. Ending a record shortly after the last close does work.
- **Hiding Trail's own reads by folder.** For Zarr stores this also hid other programs' reads. Trail now hides reads by exact file and time window.
- **Finding a cell's notebook from the kernel's sessions alone.** This is wrong when notebooks share a kernel. JupyterLab's own report of each run is used instead.
- **Getting the notebook from kernel history.** History keeps only the code and its number.
- **Testing QGIS for real.** QGIS ran headless through PyQGIS to save projects, which is how the `.qgz` timing bug was found.

### 7. Demo

A five-minute demo that follows the tutorials:

1. **Notebook** ([notebook.md](../scratch/notebook.md)). Run a cell, a failing cell and a kernel restart, and show the records.
2. **Data file** ([files.md](../scratch/files.md)). Reproject a GeoPackage and add a column from a cell, and show the change rows.
3. **Map** ([gis.md](../scratch/gis.md)). Add a layer from `data/crossings.geojson`, add the filter `mag > 5` and add a layer from Python. Then use `events resource_path=data/crossings.geojson` to show every map that used the file.

[triage.md](../scratch/triage.md) still says the geospatial preset skips `.jGIS` files. It now tracks them, so its `ontrail map.jGIS` step is no longer needed.
