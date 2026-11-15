# Trail basics

Follow [setup](setup.md), then use the Trail terminal for the commands below. Trail records activity in the copied `trail-tutorial` project.

Event examples are from earlier runs. Ids, times and notebook or map names will differ.

## Read the header

The header shows how many assets, directories and events Trail has recorded. An asset is a tracked file or a folder treated as one file, such as a Zarr store.

`watching` means file monitoring is running. `jupyter 1` means one Jupyter event subscription is active.

New events appear above the command bar. Type `help` to list the commands.

## List tracked files

```text
assets
```

Show one file:

```text
assets src_path=data/crossings.geojson
```

List the directories named by the tracking rules:

```text
dirs
```

Tracking a matching map file produces an event like this (use `events cls=AddAssetEvent` to list tracking events):

```text
2. AddAssetEvent
    id: a5050b14f4b04953af6cccc28818fc84
    timestamp: 12 21:53:03.709
    src_path: maps/earthquakes.jGIS
    rule: *.jgis
    st_size: 6.5 KiB
    st_mtime: 2026-11-12 13:53:00
    entry: e2b06a2a13ee46f2a807190ca6cac9ea
```

## Track a file

The project's `.ontrail` file contains the rules for automatic tracking. For example, `*.geojson` tracks GeoJSON files.

Stop tracking one of the sample files:

```text
offtrail data/crossings_2026.geojson
```

Track it again:

```text
ontrail data/crossings_2026.geojson
```

Check the result with `assets`.

## Read the log

Show the last 20 events:

```text
events
```

Show one file's history:

```text
events src_path=data/crossings.geojson
```

Show the last five events:

```text
events -5:
```

Filters can be combined. Terms next to each other mean `and`; use `or` for alternatives and `not` to exclude:

```text
events cls=WatchdogEvent event_type=moved
events src_path=data/crossings.geojson or src_path=data/crossings_2026.geojson
events cls=WatchdogEvent not event_type=deleted
```

Order matters: `events src_path=data/crossings.geojson -5:` shows that file's last five events. `events -5: src_path=data/crossings.geojson` searches only the log's last five events.

## Stop Trail

Enter `quit`, or press Ctrl+C. The log stays in `.trail` and loads next time you run `trail`.

Tab completes commands and paths. Up and Down recall commands. Ctrl+L clears the terminal without deleting records.

Continue with [file activity](files.ipynb), [notebook execution](notebook.ipynb), or [JupyterGIS](gis.ipynb).
