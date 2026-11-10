# Asset metadata and delayed filesystem events

> I was earlier investigating how we could track changes for specific items, such as .jGIS. I backed off because it seemed that we were just delving into version control data, however she pushed back saying git isn't really designed for version control.
>
> Yes, git is not designed for version control of data like parquets, but it could be useful for tracking versions of files like .git. So git-like functionality might be helpful for something like .jGIS, but I think eventually we'd want to be able to track changes like of other dataset files? What do you suggest? How will we be able to determine change events after they have occurred without being too costly on the system?

**Codex:**

I’d give Trail **one event per observed change to a tracked asset**, with optional detail about what changed. Use successful saves or dataset commits when the writer exposes them. For other writes, fold filesystem notifications and inspect the file only after the activity settles.

### Detect the change cheaply

- **Keep one pending record per dirty asset.** The 205 GeoJSON notifications update that record in memory. When it settles, read the file’s identity, size, and modification time once. Treat a temp file renamed over a tracked path as a replacement of the same Trail asset. Matching size and mtime are a useful shortcut for reconciliation, but they can miss a rewrite. [Event folding report](/home/arstneio/PyCharmProjects/trail/notes/11-4-3-event-folding-report.md:164)
- **Use the strongest boundary available.** A successful save from an instrumented writer can carry an operation ID, session, and output revision. On Linux, `FileClosedEvent` can shorten the wait for a dirty file. It means a writable handle closed, and other writers may still be active. Fall back to a short per-asset quiet period based on write activity. Leave read-only opens and closes out of that timer. On Windows, USN is a separate change and close source from the ETW tracing exposed by `pywintrace`. [Linux inotify manual](https://www.man7.org/linux/man-pages/man7/inotify.7.html) · [Native events report](/home/arstneio/PyCharmProjects/trail/notes/11-5-1-native-file-events-report.md:172)
- **Use `post_run_cell` to request a check.** Record when Trail observed the change and how it detected it. Attach a session or cell as the writer when there’s an explicit link. If another process was writing at the same time, timing alone leaves attribution unresolved. If Trail only reads the two `A` states in `A → B → A`, it can report observed write activity, but the intermediate contents are gone. Capturing both transitions requires observing both versions or getting events from the writer.

### Spend more only when the detail matters

| Asset | Comparison worth doing |
|---|---|
| `.jGIS` | Keep the last observed projection, layer IDs, and selected source fields. Compare those on save to report “projection changed” or “layer added.” This is the smallest useful piece of the deferred design. [File events plan](/home/arstneio/PyCharmProjects/trail/notes/10-29-3-file-events-plan.md:402) |
| Standalone GeoJSON or Parquet | Default to a file-level observation. A Parquet footer can cheaply show schema or row-count changes. An equal footer leaves row equality unresolved. Offer full streaming hashes for selected assets when byte-level confirmation matters. [Parquet format](https://parquet.apache.org/docs/file-format/) |
| Versioned dataset | Consume the dataset’s own commit ID and change metadata. For example, Iceberg manifests let Trail identify added or removed data files without scanning their rows. [Iceberg specification](https://iceberg.apache.org/spec/#manifests) |

When Trail does hash or parse, run one bounded job per asset off the notebook’s execution path. Check file identity and metadata before and after reading, and retry if a newer notification makes the result stale. A full hash still reads every byte. Hashing during a write Trail controls avoids that second pass, and a digest alone can only tell you whether the bytes differ.

**I’d ship folding first, then pilot one `.jGIS` comparison** using a small baseline. Git remains useful for restoring committed `.jGIS` versions. Trail can record when it observed a change, what evidence it has about the session or source, and which project fields changed. For large datasets, use existing dataset revisions wherever the writer provides them.

> So how about a `meta` attribute for our Assets or something?  If applicable, for example a .qGIS, then we store basically a dictionary of file metadata, but don't actually save previous versions of the file. So then at each event, we compare the metadata before and after. A problem with that, approach however, is `trail` reads events at a delayed time wrt the actual events. So an outdated FileClosedEvent could trigger Trail to compare the metadata of the file that could have actually changed more recently and the event simply hasn't been processed yet. So what could we do?

**Codex:**

Yes. I’d make `Asset.meta` the **last accepted summary** of the file. For a QGIS project (`.qgs` or `.qgz`), start with the project CRS, layer IDs and names, and layer order. Trail can report “layer added” or “CRS changed” by keeping one small dictionary per asset. Initialize it on the first successful read, then replace it after each accepted comparison. [Deferred file-events plan](/home/arstneio/PyCharmProjects/trail/notes/10-29-3-file-events-plan.md:425)

**Use `FileClosedEvent` to schedule a read. Timestamp the diff at capture.** Trail’s `Arrival.at` already means “when the watchdog thread received this notification.” If QGIS saves `A → B → C` before Trail reads the file, an old close for `B` might trigger a read of `C`. Record one observed diff from `A` to `C`. If the later close triggers another read and the summary is still `C`, there’s no second diff. [Trail’s `Arrival`](/home/arstneio/PyCharmProjects/trail/src/trail/session.py:83) · [inotify limitations](https://man7.org/linux/man-pages/man7/inotify.7.html#LIMITATIONS_AND_CAVEATS)

To handle a write that overlaps the read:

1. **Mark the path dirty when the watchdog handler receives the event.** Increment a counter there, before the consumer queue. Coalesce notifications into one pending read per asset. A writable close can shorten the wait.
2. **Read from one open file handle in a worker.** Check file identity, size, and timestamps before and after reading, then verify that the path still points to that file. Retry if those checks fail or the counter advances before the result is accepted. This catches both writes during the read and notifications Trail has received but hasn’t processed yet.
3. **Compare with `Asset.meta` and advance it after recording the diff.** A failed parse leaves the prior summary available for the next attempt.

There’s still a window where `C` is already on disk and its notification hasn’t reached the handler. The checks can accept a stable observation of `C`, but they can’t recover `B`. If you need **one semantic diff per QGIS save**, a QGIS plugin can capture the small summary inside `QgsProject.projectSaved` and send the summary with the save event. That preserves `B` even if Trail receives it after `C` has been written. [QGIS `projectSaved` signal](https://api.qgis.org/api/classQgsProject.html)

> How large is a qGIS file typically? How large would the metadata be for to store in memory as a python dict?

**Codex:**

**Small QGIS projects can be tens of KiB on disk. The proposed `Asset.meta` is about 2 KiB for 10 layers or 20 KiB for 100 layers.**

In the local JupyterGIS fixtures I measured, `.qgz` files were **7–30 KiB** on disk and contained **34–332 KiB** of uncompressed project XML. The two standalone `.qgs` files were **74–75 KiB**. These are small examples, so they give a starting point. A QGIS user reported a **7.5 MB** difference in `.qgz` size after removing 90 print layouts. [QGIS file formats](https://docs.qgis.org/3.44/en/docs/user_manual/appendices/qgis_file_formats.html#qgs-qgz-the-qgis-project-file-format) · [QGIS issue #51233](https://github.com/qgis/QGIS/issues/51233)

For `Asset.meta`, I measured a dictionary holding the project CRS, a layer-ID-to-name mapping, and an ordered tuple that reuses the same ID strings. On 64-bit CPython 3.12, counting the containers and strings:

| Layers | RAM per project |
|---:|---:|
| 1–7, measured fixtures | 0.7–1.6 KiB |
| 10, synthetic | 2.3 KiB |
| 100, synthetic | 19.7 KiB |
| 1,000, synthetic | 186 KiB |

Keeping one baseline for **100 projects with 100 layers each** would use about **2 MiB** for the summaries. I’d keep those in memory and bound the number of concurrent parses and the amount of uncompressed XML each can read. The table measures retained summaries. Decompression and XML parsing use additional temporary memory.

## Decision and startup behavior

> My conclusion si that I will keep an `Asset.meta` attribute, which will have custom definitions for supported assets, e.g. qGIS, Parquet, etc. For qGIS we get the metadata attributes, for parquet we read the footer, for zarr there surely is some sort of metadata, etc. We'll keep the metadata in memory (probably loaded with threads on startup to minimize startup lag? What if we have many assets?) and reconcile with the m_time and m_size when there's a modification.

**Codex:**

Keep one in-memory summary per supported asset. Each format reader selects the fields Trail can compare. For QGIS, use project fields such as CRS and layer IDs. For Parquet, read the footer for fields such as schema and row count. For Zarr, read the relevant array and group metadata. Zarr v2 uses `.zarray`, `.zgroup`, and optional `.zattrs`. V3 uses `zarr.json` per array or group. A Parquet row rewrite or Zarr chunk write can leave these selected fields equal, so keep the ordinary file or directory change event in that case. [Parquet file format](https://parquet.apache.org/docs/file-format/) · [Zarr v2 storage specification](https://zarr-specs.readthedocs.io/en/latest/v2/v2.0.html) · [Zarr v3 stored representation](https://zarr-specs.readthedocs.io/en/latest/v3/core/index.html#stored-representation)

Start the watcher first, then populate summaries in the background with a small, bounded pool of threads. Queue only tracked assets with a supported reader, submit work as workers become free, and prioritize assets with pending changes. This keeps startup responsive and caps concurrent I/O and parsing memory even with thousands of assets. If an asset changes before its initial read, record the filesystem change and use the first stable summary as its baseline. The same limit applies to changes that happened between runs when the baseline exists only in memory.

Use `st_mtime_ns`, `st_size`, and file identity as cheap reconciliation signals. A write or replacement notification can still warrant a metadata read when size and mtime match. For directory-backed Zarr, use the affected child paths and the metadata files they identify. The root directory’s size and mtime can stay the same when an existing child is rewritten.
