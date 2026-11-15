# Format metadata in Trail: libraries, conventions and scope

Trail's `metadata` package read a short summary of each tracked file in a supported format, such as the columns of a parquet file or the CRS of a geotiff, and logged what differed after the file changed. This report examines whether a third-party library could do that work, which conventions apply to it, how the package behaved when a format's library was missing, whether the feature fits Trail's scope, and what earlier plans and meetings said about it.

The package was taken out of `stable` on 2026-11-14. Its last version is on the remote branch [`metadata`](https://github.com/geojupyter/prototype-trail/tree/metadata), at commit `231d653`.

## TLDR

### The package and the question

The package gave every tracked file an `asset.metadata` object. For a file in one of 14 supported formats, that object read a summary after the file changed: the layers of a geopackage and their CRS, the row count and columns of a parquet file, the arrays of a zarr store. It compared the summary with the one kept from the previous read and logged the differences as change rows. A change to a geopackage, for example, was logged as `column 'checked' added in layer 'crossings'`. A file in any other format only had its size and modification time compared.

The package also held two checks that did not depend on any format. An IPython extension compared every tracked file after each notebook cell. At startup, Trail compared each file with the summary kept in `.trail/metadata.json`, so it could report changes made while it was not running.

At the 12 November meeting, Matt raised two concerns: the responsibility Trail takes on by understanding file structures, and dependency bloat. The meeting asked whether a third-party library already understands these formats, and limited the work to parquet. The committed package had 14 readers at the time. This report answers five questions: whether a library could do the work, which conventions apply, whether to read a format only when its library is installed, whether the feature belongs in Trail, and whether earlier plans discussed it. [Details](#what-the-metadata-package-was)

### Third-party libraries

No library does the whole job. Reading facts out of a file is a solved problem: GDAL, the C library behind most geospatial file reading, already reads nearly every format the package parsed by hand, and geopandas, rasterio and QGIS all bring it with them. In a test on the package's own pairs of test files, about 20 lines around GDAL's JSON summaries reported the same changes as Trail's readers for parquet, flatgeobuf, geojson and geotiff files. Only a flatgeobuf dataset-name change was missing.

No library decides which fields matter or what to call a change. The tools that report what changed in geospatial data keep every version of the data, which makes them version control. Kart is a git-like version control system for geospatial data, geodiff computes row changes between two geopackages, and Icechunk is a versioned storage engine for zarr data.

Handing the reading to GDAL has its own failure modes, and the tutorial environment showed two of them. The environment holds two copies of GDAL. The copy inside the pip-installed pyogrio, which geopandas uses to read files, cannot open a geoparquet file at all. When Python started from the environment without activating it, GDAL could not find PROJ, its coordinate-system database, and silently reported the wrong CRS for a geojson file.

The hand-written parsers fail differently: they fall behind changes to a format without raising any error. Trail's parquet reader looked for the CRS only in the `geo` metadata key. Newer parquet files can carry the CRS in the native geometry type of the parquet format instead. For such a file, a change from EPSG:4326 to EPSG:3857 produced an event with no change rows. [Details](#what-existing-libraries-cover)

### Conventions

Conventions exist for naming the facts in a summary and for recording a difference, but none covers the wording of a change.

- STAC, a JSON vocabulary for describing geospatial datasets, has fields for projections, rasters, tables and data cubes that cover nearly every key the readers used. GDAL already emits some of them.
- OpenLineage, an open standard for recording the data a job reads and writes, records a dataset's new schema and the kind of change, and leaves the comparison to whoever reads the record.
- The trail initiative already names RO-Crate, a way to package research data with its metadata, and PROV-O, the W3C vocabulary for provenance, for export. Both describe each revision of a file as a state of its own, not as a difference from the previous one.
- RFC 6902, JSON Patch, is the standard format for the operations that turn one JSON document into another.

Phrases such as `column 'country' added` were Trail's own invention. They are the part Matt described as taking responsibility for the meaning of changes. [Details](#conventions-for-the-facts-and-the-differences)

### Formats whose library is missing

The package already read a format only when its library was installed. The libraries were in an optional group of dependencies, the `formats` extra installed with `trail[formats]`. Each reader imported its library only when it ran, and a missing library meant one warning and a comparison of size and modification time.

Two gaps remained. First, a process without the library erased the summary that another process had saved, so the next change could not be described either. In a test, a parquet file changed twice across two environments, and neither change was described. Second, saved summaries carried no version of the reader that wrote them. After an upgrade of pyarrow or of a reader, the next real write could log changes that did not happen. [Details](#how-the-package-handled-a-missing-library)

### Fit with Trail's scope

Recording facts about a file's new state fits the trail initiative, which has Trail record "the new durable state" of a registered resource after it changes. Interpreting those facts across many formats does not fit the constraints the project set for itself. PROJECT_PLAN asks for the standard library unless a dependency is clearly justified. The initiative asks Trail to record only what can be established reliably, rather than inferring operations from internal data structures.

The reader for `.jgis` files partly duplicated JupyterGIS's event feed. The feed already reports layers and sources that a user adds, changes or removes in the map editor. Most of the package's code did not depend on any format: about 1,040 of its 2,260 lines. A parquet-only version would still need all of that code. [Details](#the-case-for-and-against-the-feature)

### Earlier plans and meetings

No team plan proposed reading file contents. PROJECT_PLAN, the original brief and the trail initiative all compare a file by its size and modification time. The 12 November meeting is the only team discussion. Trail's own notes deferred the idea on 29 October, revived it on 9 November, and designed the readers on 10 November. [Details](#what-the-plans-and-meetings-said)

### Where the code is now

On 2026-11-14 the package was removed from `stable`, together with the readers for QGIS and JupyterGIS project files, the IPython extension and their tests. Their last version is on the remote branch `metadata`. The two format-independent checks went with the package, since both lived in it. Trail no longer compares files after a notebook cell, and no longer reports at startup the changes made while it was not running.

The folding of a file's filesystem events into one record per write session stayed. So did the grouping of shapefile parts under their `.shp`. A Trail log written while the package was present will not open if it holds an access or metadata record. The tutorial project in `../trail-tutorial` holds none and still opens. [Details](#what-the-removal-changed)

### If the work resumes

None of these steps has been taken. A trial would follow the 12 November decision and keep only the core and the parquet reader. It would name the parquet summary's fields after STAC and drop the per-format wording. It would also close the two gaps around missing libraries and reader versions, and read the CRS from parquet's native geometry type.

More formats would come through GDAL, used only when its Python bindings are installed. That means one GDAL-backed reader for vector files and one for rasters, not more hand-written parsers. [Details](#steps-for-a-parquet-only-trial)

## Deep Dive

### What the metadata package was

This section describes the package as it was on the `metadata` branch, so that the later sections can refer to its parts. The package lived in `src/trail/metadata/`. The readers for QGIS and JupyterGIS project files were in `src/trail/jupyter/metadata.py`, and the notebook hook in `src/trail/ipython.py`.

| Part | Module | Role |
|---|---|---|
| `Metadata` | `metadata/__init__.py` | Reached as `asset.metadata`. A subclass for each format reads the summary in `read()`. The base class compares only the size, modification time and inode. |
| `Stat`, `Change`, `diff` | `metadata/__init__.py` | The recorded size, mtime and inode of a file. One difference row. The comparison of two summaries. |
| `MetadataEvent` | `event.py` | The log record of a difference, with its change rows. |
| `Reads` | `metadata/reads.py` | With the watcher running, reads a file in a worker thread after its write session ends, and keeps Trail's own reads out of the access log. |
| `Baselines` | `metadata/baselines.py` | Keeps each asset's last stat and summary in `.trail/metadata.json` for the next run. |
| The readers | `metadata/geospatial.py`, `metadata/general.py`, `jupyter/metadata.py` | 14 classes for 26 suffixes. |
| The IPython extension | `ipython.py` | Checks every asset after each notebook cell. |

The readers covered parquet, feather, geotiff, geopackage, zarr, shapefile, geojson, flatgeobuf, netcdf, csv and tsv, npy and npz, hdf5, and QGIS and JupyterGIS project files. Four of them needed a library: pyarrow for parquet and feather, h5py for hdf5, netCDF4 for netcdf, and defusedxml for QGIS projects. The others parsed the file with the standard library: `struct` for binary headers, `sqlite3` for geopackages, and `json`, `csv` and `zipfile` for the rest.

A logged change looked like this, from the event coverage runs of 12 November ([summary](/home/arstneio/PyCharmProjects/trail/context/report/11-12-3-event-coverage-summary.md:64)):

```text
MetadataEvent(basis='startup', src_path='data/crossings.gpkg', format='geopackage', change=["column 'checked' added in layer 'crossings'"])
```

The package's size, counted at commit `231d653`:

| Part | Lines |
|---|---:|
| Core: `metadata/__init__.py`, `reads.py`, `baselines.py` | 1,040 |
| `ParquetMetadata` | 54 |
| The other 13 readers and their helpers | 1,165 |
| `tests/test_metadata.py`, 28 tests, with 29 fixture files | 681 |

The package and its readers were 2,259 of the 15,157 lines in `src/trail`, about 15%, not counting the hooks in `session.py`, `event.py` and `asset.py`.

The 12 November meeting discussed the package under "Dependency policy?". The meeting notes ([DSE notes](</home/arstneio/PyCharmProjects/trail/scratch/DSE GeoJupyter Notes [shared].md:70>)) say:

```text
Daniel: Dependency policy?
File modification events: messages include e.g. column added, datatype added for parquet.
Matt: Concerned about how much responsibility we take on for understanding file structure.
Potentially huge complexity burden! Let's explore this with parquet only just so we can
visualize what this looks like to implement but not go further with other file types.
```

The raw notes of the same meeting ([DSE notes](</home/arstneio/PyCharmProjects/trail/scratch/DSE GeoJupyter Notes [shared].md:118>)) add: dependency bloat; "column was added" is probably not needed; taking responsibility for the meaning of changes leads to snowballing complexity; go ahead with parquet and keep it as a dependency; look at how much code there is; and "Is this something a third-party library already understands?"

### What existing libraries cover

The job has three parts, and libraries serve only the first well.

| Part | Existing tools | Fit for Trail |
|---|---|---|
| Read facts from a file | GDAL, through `gdal.Info` and `gdal.VectorInfo` with `format='json'`, pyogrio's `read_info` or rasterio. pyarrow, xarray and h5py for the formats GDAL does not cover well. | Good. One vector reader and one raster reader could replace the hand-written parsers. |
| Compare two summaries | DeepDiff, jsonpatch | Little gain. The package's `diff` was 33 lines. |
| Say what changed in geodata | `kart diff`, geodiff (behind Mergin Maps), `gdal raster compare` (GDAL 3.12 and later), Icechunk's `repo.diff`, the logs of Iceberg and Delta tables | Poor. Each needs both versions of the data or owns its history, which is version control. Trail keeps no copies of files. |

The tools in the last row show where a library can take over the meaning of a change: when the data already lives in a versioned store. A table in Iceberg or a store in Icechunk records its own changes, and OpenLineage's `version` facet exists to point at such a version. Trail could record that pointer, and leave the question of what changed to the store.

GDAL reproduced the readers' findings. Each fixture pair holds a file before and after an edit that adds a column or row, changes the CRS, or changes the bands. A summary built from GDAL's JSON was compared with the package's `diff`. GDAL ran from the activated tutorial environment, version 3.13.3.

| Fixture pair | Trail's readers reported | GDAL's summary reported |
|---|---|---|
| `cities.parquet` | rows 5 → 6, column `country` added, CRS EPSG:4326 → EPSG:3857, bbox | the same four |
| `cities.fgb` | dataset name, features 5 → 6, column `country` added, CRS, envelope | the same, without the dataset name |
| `cities.geojson` | features 5 → 6, property `country` added, CRS | the same, plus the extent |
| `scene.tif` | bands 3 → 4, overviews 2 → 0, CRS EPSG:32633 → EPSG:32634, nodata 0 → 255 | the same four |

The same environment showed what delegating costs:

- The environment holds two copies of GDAL. QGIS from conda-forge brings GDAL 3.13.3 behind `osgeo`. The pip wheel of pyogrio 0.13.0 carries its own GDAL 3.12.4, which has no parquet driver. pyogrio could not open the geoparquet fixture: GDAL's ADBC driver claimed the file and failed on a missing `libduckdb.so`. With that driver skipped, GDAL reported the file as not recognized.
- When Python was started from the environment without activating it, `PROJ_DATA` was unset and GDAL could not open its PROJ database. GDAL then returned no CRS for the flatgeobuf file and EPSG:4326 for a geojson file that declares EPSG:3857, without raising. Identifying the geotiff's CRS raised an error. Any process that starts Python from the environment without activating it gets the same wrong facts.
- The Linux wheels on PyPI on 2026-11-14 were 52.5 MiB for pyarrow 26.0.1, 37.3 MiB for rasterio 1.5.2, about 31 MiB for pyogrio 0.13.0, 10.1 MiB for netCDF4 1.7.5 and 5.2 MiB for h5py 3.16.0. pyogrio and rasterio each bundle their own GDAL.

The hand-written parsers fail without any error when a format changes. The geoparquet 2.0 specification makes the CRS on parquet's native geometry type the source of truth, and allows a writer to emit only the native type without the `geo` key. Such a file is not conformant geoparquet, but readers are expected to read it. GDAL 3.13 writes such files with the layer option `USE_PARQUET_GEO_TYPES=ONLY`.

Two such files were written, one in EPSG:4326 and one in EPSG:3857. pyarrow 26 shows their geometry column as plain `binary`, and keeps the CRS in the parquet schema's logical type, as `Geometry(crs=…)`. Trail's parquet reader looked only at the `geo` key ([geospatial.py:112](https://github.com/geojupyter/prototype-trail/blob/231d653f5a319d5dfb7118a979c3248e9712133c/src/trail/metadata/geospatial.py#L112)). It gave both files the same summary:

```json
{"num_rows": 1, "num_row_groups": 1, "columns": {"name": "string", "geometry": "binary"}, "geo": null}
```

Replacing the first file with the second therefore logged an event with no change rows. GDAL's `VectorInfo` reported EPSG:4326 and EPSG:3857 for the two files.

The same exposure applies to every parser that decodes a format's bytes itself. The geotiff reader decoded the tiff tags and geokeys in 163 lines. The zarr reader handled both store versions and consolidated metadata in 164 lines. The shapefile reader decoded the `.shp`, `.shx`, `.dbf`, `.prj` and `.cpg` parts in 149 lines. The flatgeobuf reader decoded the FlatBuffers header in 115 lines, with the position of each field written into the code. The readers that called a library were small: parquet 54 lines, feather 30, netcdf 34 and hdf5 28.

### Conventions for the facts and the differences

STAC names nearly every fact the readers kept:

| Reader key | STAC field |
|---|---|
| parquet `num_rows` | `table:row_count` |
| parquet `columns` | `table:columns` |
| geotiff `crs`, and the CRS of a parquet geometry column | `proj:code`, such as `EPSG:4326` |
| geotiff `width` and `height` | `proj:shape` |
| geotiff `transform` | `proj:transform` |
| geotiff `bands`, `dtype` and `nodata` | `data_type` and `nodata` of each band, in `raster:bands` or the common `bands` field of STAC 1.1 |
| the dimensions, arrays and variables of zarr and netcdf | `cube:dimensions` and `cube:variables` |

GDAL already emits these names for rasters. The JSON of `gdal.Info(path, format='json')` for `scene_a.tif` holds a `stac` block with `proj:shape`, `proj:epsg`, `proj:transform`, `proj:wkt2`, `proj:projjson`, `raster:bands` (with `data_type` and `nodata`) and `eo:bands`. GDAL 3.13 still writes `proj:epsg`, which version 2 of STAC's projection extension replaced with `proj:code`, so conventions change as well. The team already treats STAC as the metadata of a JupyterGIS layer: the 13 January community meeting agreed to move a layer's STAC metadata into a provenance field ([meeting notes](/home/arstneio/PyCharmProjects/trail/compass/meeting-notes/20260113-community/index.md:62)).

OpenLineage describes datasets with facets, which are small named JSON objects:

- The `schema` facet lists a dataset's fields with their names and types.
- The `lifecycleStateChange` facet says what happened to the dataset: `ALTER`, `CREATE`, `DROP`, `OVERWRITE`, `RENAME` or `TRUNCATE`, with the previous name for a rename.
- The `version` facet holds a version that the storage defines, such as an Iceberg snapshot ID.

A producer states the new schema and the kind of change. The consumer of the record compares versions. That division is close to where Matt drew the line.

The trail initiative already plans an export to RO-Crate and PROV-O ([initiative](/home/arstneio/PyCharmProjects/trail/scratch/trail-initiative.md:63)). PROV describes each revision of a file as an entity with its own attributes, linked to the one before by `prov:wasRevisionOf`. It has no vocabulary for a difference. The package logged only change rows, and kept the full summary only in `.trail/metadata.json`, which each read overwrote. An exporter could therefore not describe any earlier state of a file. Logging the summary with each event would have fixed that. The summaries of the 29 fixtures measured on 11 November were 21 B to 1.4 KiB as JSON ([plan](/home/arstneio/PyCharmProjects/trail/context/plan/11-10-1-asset-metadata.md:1844)).

RFC 6902, JSON Patch, records a difference with operations on paths written in RFC 6901, JSON Pointer:

| Trail verb | JSON Patch operation | Difference |
|---|---|---|
| `added` | `add` | none |
| `removed` | `remove` | A patch holds no old value. A `test` operation placed before it can state the old value. |
| `updated` | `replace` | As for `remove`. |
| `reordered` | none | A patch replaces the whole list. |

The package kept a path as a tuple of keys. A JSON Pointer must escape `/` as `~1`, and zarr array paths such as `group/temperature` contain it. Keeping tuples internally and converting them on export avoids that.

The libraries themselves follow a common packaging pattern. A pip extra with the import inside the reader is the usual way to make a library optional, and the package did that. When other packages supply readers, Python entry points are the usual mechanism; xarray finds its backends this way. Conda has no extras. The 30 June JupyterGIS sync noted that optional dependency groups for conda, CEP 44, were coming ([meeting notes](/home/arstneio/PyCharmProjects/trail/compass/meeting-notes/20260630-jupytergis-sync/index.md:57)). Whether CEP 44 has landed was not checked.

### How the package handled a missing library

The `formats` extra held pyarrow, h5py and netCDF4 ([pyproject.toml](https://github.com/geojupyter/prototype-trail/blob/231d653f5a319d5dfb7118a979c3248e9712133c/pyproject.toml#L25-L31)). Each reader imported its library inside `read()`. On an `ImportError`, `Metadata.capture` marked the format unavailable for the rest of the process and warned once ([`__init__.py`](https://github.com/geojupyter/prototype-trail/blob/231d653f5a319d5dfb7118a979c3248e9712133c/src/trail/metadata/__init__.py#L409)). The tutorial environment already had pyarrow 26.0.1 and GDAL 3.13.3, so the extra cost its users nothing there.

A capture without a summary then reached `Metadata.accept`, which cleared the saved summary and wrote the empty one to `.trail/metadata.json` ([`__init__.py`](https://github.com/geojupyter/prototype-trail/blob/231d653f5a319d5dfb7118a979c3248e9712133c/src/trail/metadata/__init__.py#L467-L472)):

```python
if new is not None:
    self._summary = intern(new)
elif not capture.error:
    # too large, or no reader: nothing to compare the next read with
    self._summary = None
trail.baselines.mark(self)
```

A process without the library therefore erased the summary that a process with the library had saved. The test used two environments:

1. The tutorial environment, which has pyarrow, tracked a parquet file and saved its summary.
2. The project's `.venv`, which has no pyarrow, saw the file replaced by an edited version. It warned `parquet metadata is not read: No module named 'pyarrow'`, logged an event with no change rows, and saved `summary: null`.
3. The tutorial environment saw the file changed back. It also logged no change rows, since it had nothing to compare with.

Two changes to the schema and the CRS were logged, and neither was described. Keeping the saved summary when the reader is unavailable would let the next process that has the library describe both changes as one difference. The tutorial runs the console and the kernels in one environment, so it never shows this. A console installed with `pipx` or `uv tool` would.

Saved summaries also carried no version. `Metadata.restore` compared only the format's name ([`__init__.py`](https://github.com/geojupyter/prototype-trail/blob/231d653f5a319d5dfb7118a979c3248e9712133c/src/trail/metadata/__init__.py#L275-L280)), and the summaries in `.trail/metadata.json` outlived upgrades of pyarrow and of Trail. A type that pyarrow names differently, or a key added to `read()`, would have appeared as changes on the next real write. The deferred design of 29 October gave each reader a `snapshot_version` for this purpose ([file events plan](/home/arstneio/PyCharmProjects/trail/context/plan/10-29-3-file-events.md:223)). The idea did not reach the package. A version for each reader, saved with the library's version, would let Trail take a new baseline without logging anything when either changes.

### The case for and against the feature

The trail initiative supports recording facts about a file's state. After a cell runs, Trail compares registered resources with their last recorded state and records "the new durable state" of a changed file ([initiative](/home/arstneio/PyCharmProjects/trail/scratch/trail-initiative.md:71)). It also lists a resource's format and version among its fields ([initiative](/home/arstneio/PyCharmProjects/trail/scratch/trail-initiative.md:35)). For geospatial data, the CRS, the schema and the row count decide whether a later step still works, and reading them costs one header read.

The constraints the project set for itself point the other way. PROJECT_PLAN asks for the standard library unless a dependency is clearly justified, and for minimal dependencies and small modules ([PROJECT_PLAN](/home/arstneio/PyCharmProjects/trail/PROJECT_PLAN.md:29), [constraints](/home/arstneio/PyCharmProjects/trail/PROJECT_PLAN.md:197)). The initiative says that where an application does not expose enough semantic state, Trail records only what can be established reliably rather than inferring an operation from internal data structures ([initiative](/home/arstneio/PyCharmProjects/trail/scratch/trail-initiative.md:122)). That rule is written about GUI applications, and it applies equally to the inside of files.

On 3 June, Fernando warned that a project that looks bespoke for one workflow is likely to fail. Fernando also asked to prove a single case before generalizing ([DSE notes](</home/arstneio/PyCharmProjects/trail/scratch/DSE GeoJupyter Notes [shared].md:656>), [DSE notes](</home/arstneio/PyCharmProjects/trail/scratch/DSE GeoJupyter Notes [shared].md:673>)). Limiting the work to parquet is that single case.

The reader for `.jgis` files partly duplicated JupyterGIS's event feed. The feed already reports layers and sources that a user adds, changes or removes in the map editor, and Trail records those events ([DSE notes](</home/arstneio/PyCharmProjects/trail/scratch/DSE GeoJupyter Notes [shared].md:50>)). The [JupyterGIS reader](https://github.com/geojupyter/prototype-trail/blob/231d653f5a319d5dfb7118a979c3248e9712133c/src/trail/jupyter/metadata.py#L108) re-derived the same facts from the saved file. It only added something for edits made outside JupyterGIS.

Most of the cost was in the core, which a parquet-only version would still carry. That core scheduled reads after write sessions, ran them in worker threads with retries, and opened a window around each read. The window kept the read's own open and close notifications out of the access log ([session.py](https://github.com/geojupyter/prototype-trail/blob/231d653f5a319d5dfb7118a979c3248e9712133c/src/trail/session.py#L462-L467)). The core also saved summaries across restarts and ordered competing reads of the same file. Measuring how much code the feature needs, as the 12 November meeting asked, therefore starts at about 1,040 lines before the first reader.

### What the plans and meetings said

- PROJECT_PLAN detects changes by path, size and modification time ([PROJECT_PLAN](/home/arstneio/PyCharmProjects/trail/PROJECT_PLAN.md:25)). It considers content hashing only for detecting renames ([PROJECT_PLAN](/home/arstneio/PyCharmProjects/trail/PROJECT_PLAN.md:79)).
- The original brief says "Think git", records size and mtime, and asks for the standard library where possible ([notes.txt](/home/arstneio/PyCharmProjects/trail/scratch/notes.txt:8), [notes.txt](/home/arstneio/PyCharmProjects/trail/scratch/notes.txt:17)).
- The trail initiative compares the filesystem metadata of registered resources after a cell, which is their size and time, not their contents ([initiative](/home/arstneio/PyCharmProjects/trail/scratch/trail-initiative.md:71)).
- The DSE notes hold one discussion of the feature, on 12 November, quoted under [What the metadata package was](#what-the-metadata-package-was). The schema question that Maryam raised on 15 October is about the schemas of events that tools send to Trail, not about file contents ([DSE notes](</home/arstneio/PyCharmProjects/trail/scratch/DSE GeoJupyter Notes [shared].md:154>)).
- The compass meeting notes, current through 20 October, say nothing about Trail and file contents. Nearby are the STAC provenance field from 13 January and the JupyterGIS metadata viewer from 25 August ([meeting notes](/home/arstneio/PyCharmProjects/trail/compass/meeting-notes/20260825-jupytergis-sync/index.md:85)).
- Trail's own notes deferred format-specific comparisons and in-memory baselines on 29 October ([file events plan](/home/arstneio/PyCharmProjects/trail/context/plan/10-29-3-file-events.md:5), [decision](/home/arstneio/PyCharmProjects/trail/context/plan/10-29-3-file-events.md:17)). The 9 November discussion reversed that ([discussion](/home/arstneio/PyCharmProjects/trail/context/explain/11-9-1-asset-metadata-discussion.md:66)). The 10 November plan designed the readers, put their libraries in an extra ([plan](/home/arstneio/PyCharmProjects/trail/context/plan/11-10-1-asset-metadata.md:2019)), and left export to another vocabulary out of scope ([plan](/home/arstneio/PyCharmProjects/trail/context/plan/11-10-1-asset-metadata.md:4019)).
- The notes of Daniel's meetings with Matt, a Google Doc linked from the 1 October DSE notes, were not searched.

### What the removal changed

On 2026-11-14 the branch `metadata` was created at commit `231d653` and pushed to `origin`, so it holds the package exactly as it was. The following were then removed from `stable`:

- the package `src/trail/metadata/`, the QGIS and JupyterGIS readers in `src/trail/jupyter/metadata.py`, and the IPython extension `src/trail/ipython.py`
- `MetadataEvent`, and the `format`, `changes` and `more` fields of `FileAccessEvent` and `DirectoryAccessEvent`
- `asset.metadata`, `Assets.observe` and `Trail.baselines`
- the parts of the watcher session that read files after a write, waited to log a write until its read finished, kept Trail's own reads out of the access log, and checked every asset at startup
- the `changed` verb and its colour in the console
- the dependency on defusedxml, in `pyproject.toml` and `requirements.txt`, and the `formats` extra
- `tests/test_metadata.py`, `tests/test_ipython.py` and the fixtures in `tests/fixtures/metadata/`

With the package went the two checks that did not depend on a format. Trail no longer compares tracked files after a notebook cell. It no longer reports at startup the changes made while it was not running. Without a watcher, nothing compares a tracked file with its recorded size and time any more, as before the package was added.

The folding of a file's filesystem events into one record per write session came in the same commit as the package, and it stayed. So did the grouping of untracked shapefile parts under their `.shp`. Its helper `sibling_shp` moved from `metadata/geospatial.py` into `session.py`.

Three test files changed:

- `tests/test_writers.py` keeps its checks that real writers produce one record per write: geopandas for a geopackage and a shapefile, rasterio for a geotiff. It drops the assertions about change rows and the zarr test, which only checked change rows.
- `tests/test_accesses.py` returns to the strict order of two accesses that it had before the package.
- `tests/test_folds.py` drops a test of reading a `MetadataEvent`'s nanosecond mtime.

A Trail log written while the package was present will not open if it holds a `MetadataEvent` or an access record, since replay rejects the class or the removed fields. Such a `.trail` directory has to be deleted and the project tracked again. The tutorial project in `../trail-tutorial`, the only Trail project beside this repository, holds no such record and still opens. Its `.trail/metadata.json` is now unused.

Bringing the feature back means restoring these hooks along with the package. The removal touched `session.py`, `event.py`, `asset.py`, `trail.py` and the console as well.

### Steps for a parquet-only trial

These are recommendations, not decisions.

1. Follow the 12 November decision: restore the core and `ParquetMetadata` from the `metadata` branch, without the other 13 readers. The raw meeting notes say "keep it as a dependency", while pyarrow sat in the optional extra. Keeping the extra, and confirming that this is what Matt meant, avoids a 52.5 MiB wheel for users who do not have pyarrow.
2. Name the parquet summary's fields after STAC's table and projection fields, and drop the per-format wording in `phrases`. Trail then invents neither the vocabulary nor the meaning, and the trial shows whether the generic wording is enough.
3. Keep a saved summary when a process cannot read the format, and save a version for each reader together with the library's version. Take a new baseline without logging anything when either changes.
4. Read the CRS of a geometry column from parquet's native type as well as from the `geo` key.
5. If the trial earns more formats, add them through GDAL rather than more hand-written parsers. That means one reader for vector files and one for rasters, used only when `osgeo` can be imported.

### How the checks were run

All checks ran on 2026-11-14 on Linux 6.12, against the package at commit `231d653`. Two environments were used:

- The tutorial environment, made with micromamba as described in `tutorials/setup.md`, has Python 3.13.18, GDAL 3.13.3 behind `osgeo`, pyarrow 26.0.1, pyogrio 0.13.0 with its own GDAL 3.12.4, rasterio 1.5.2, geopandas 1.2.0 and xarray 2026.9.0. It has neither h5py nor netCDF4. Unless stated otherwise it was activated with `micromamba run -n trail`.
- The project's `.venv` has Python 3.12.16 and no pyarrow.

The fixture pairs were those in the package's `tests/fixtures/metadata/`. Each pair was compared in a temporary project: the first file was tracked, a baseline was read, the second file was copied over it, and the asset was observed again. The GDAL summaries were built as follows and compared with the package's `diff`:

```python
from osgeo import gdal

gdal.UseExceptions()


def vector_summary(path: str) -> dict:
    info = gdal.VectorInfo(path, format='json')
    layer = info['layers'][0]
    geometry = layer['geometryFields'][0]
    identifier = (
        geometry['coordinateSystem']
        .get('projjson', {})
        .get('id', {})
    )
    columns = {
        field['name']: field['type']
        for field in layer['fields']
    }
    out = {
        'crs': f"{identifier.get('authority')}:{identifier.get('code')}",
        'geometry_type': geometry['type'],
        'features': layer['featureCount'],
        'columns': columns,
        'extent': geometry['extent'],
    }
    return out


def raster_summary(path: str) -> dict:
    dataset = gdal.Open(path)
    srs = dataset.GetSpatialRef()
    srs.AutoIdentifyEPSG()
    band = dataset.GetRasterBand(1)
    out = {
        'width': dataset.RasterXSize,
        'height': dataset.RasterYSize,
        'bands': dataset.RasterCount,
        'dtype': gdal.GetDataTypeName(band.DataType),
        'crs': f'{srs.GetAuthorityName(None)}:{srs.GetAuthorityCode(None)}',
        'transform': list(dataset.GetGeoTransform()),
        'nodata': band.GetNoDataValue(),
        'overviews': band.GetOverviewCount(),
        'compression': dataset.GetMetadataItem('COMPRESSION', 'IMAGE_STRUCTURE'),
    }
    return out
```

The two parquet files with native geometry were written with GDAL's parquet driver, one point each, using `layer = source.CreateLayer('cities', srs, ogr.wkbPoint, options=['USE_PARQUET_GEO_TYPES=ONLY'])` with `srs` imported from EPSG:4326 and EPSG:3857. The wheel sizes come from PyPI's JSON API for the Linux x86_64 wheels of each package's latest release.

The removal was checked with `ruff check` and the test suite. In `.venv`, the suite gave 412 passed, 9 skipped and 1 failed before the removal, and 379 passed, 4 skipped and 1 failed after it. The failure is the same in both runs: `test_init.py::TestInit::test_the_message_opens_with_the_welcome_and_ends_with_the_paths`, which expects a line for a `data/` folder in the welcome message and does not involve the package. The four tests skipped after the removal include the three writer tests, which need geopandas and rasterio. In the tutorial environment, the writer tests and the fold tests passed in three runs out of three.

### Sources

- [pyogrio API reference](https://pyogrio.readthedocs.io/en/latest/api.html)
- [Kart patch format](https://docs.kartproject.org/en/latest/pages/development/patch_format.html)
- [geodiff](https://github.com/merginmaps/geodiff)
- [`gdal raster compare`](https://gdal.org/en/stable/programs/gdal_raster_compare.html)
- [Icechunk snapshots and diffs](https://icechunk.io/en/stable/reference/snapshots/)
- [OpenLineage lifecycle state change facet](https://openlineage.io/spec/facets/1-0-0/LifecycleStateChangeDatasetFacet.json)
- [OpenLineage facets guide](https://openlineage.io/docs/guides/facets)
- [STAC table extension](https://github.com/stac-extensions/table)
- [pystac projection extension](https://pystac.readthedocs.io/en/stable/_modules/pystac/extensions/projection.html)
- [geoparquet 2.0.0-rc.1](https://geoparquet.org/releases/v2.0.0-rc.1/)
- [Native geospatial types in Apache Parquet](https://parquet.apache.org/blog/2026/02/13/native-geospatial-types-in-apache-parquet/)
- [RFC 6902, JSON Patch](https://www.rfc-editor.org/rfc/rfc6902)
