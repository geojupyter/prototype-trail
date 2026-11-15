# Add interactive tracking, `.ontrail` and `.offtrail` rules, and directory dataset access logging

<!--
Local draft for hodczak -> main, following https://github.com/geojupyter/prototype-trail/pull/1.
It replaces notes/10-21-1-pr-2-draft.md.
Compared main 881423f3dc564dd06f7a1c8369e326d3ef50e610 with the current working tree of ontrail,
including the shared Rules base, .offtrail, Entries checks, and WatchdogSession lifecycle.
These changes are not committed yet.
Reprs and console transcripts were generated from that working tree on temporary projects.
Every link uses {commit} in place of a hash, since these changes have no commit yet. Line anchors
were checked against the working tree. Once the changes are committed and hodczak is pushed, replace
{commit} with that hash, and check the anchors again if the commit differs from the working tree.

Before publication:
- Decide whether the local notes belong in the merge.
- Update the toy-project tutorial before using it as the end-to-end example. It still uses .by_pos,
  AddEntryEvent, trail.register in its prose and watchdog.stop(), and its closing prose says no CLI
  exists. Its trail.entries cells run again, but now show Dirs before assets.
- prototype-trail #2 is pre-commit-ci's autoupdate PR, so this PR opens as #3 or later.
This comment is drafting context, not part of the proposed public description.
-->

This PR adds an interactive terminal interface, persistent rules for discovering and excluding assets, and access logging for datasets stored as directories.

The tracking model now distinguishes directories watched for matching files from directories tracked as one dataset. The rules in `.ontrail` select files and datasets, and `.offtrail` excludes paths from discovery; directory datasets retain one resource ID, and activity inside them is summarized into access events. Shared collections expose the same selection expressions through Python and the terminal. `trail.entries` looks up assets and Dirs together, and holds the checks that decide which paths a scan tracks and a walk enters.

The [directory dataset tests](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_dirassets.py#L19) and [access logging tests](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_accesses.py#L52) demonstrate the new behavior. The reprs below come from a temporary project with a csv file, a png file that a Dir scope picks up, and a zarr store of two files that is written twice during a watchdog session. The console transcripts reopen the same project, which also holds an untracked `notes.md`.

## Core Classes

### [Trail](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/trail.py#L69)

```text
Trail
    id: e7d77d814d7a4b6b88b316b8308b475e
    dir: /tmp/tmpn0alq_gb/.trail
    dirs: [
        /tmp/tmpn0alq_gb,
        /tmp/tmpn0alq_gb/data,
    ]
    assets: [
        /tmp/tmpn0alq_gb/cube.zarr,
        /tmp/tmpn0alq_gb/new.csv,
        /tmp/tmpn0alq_gb/data/map.png,
    ]
```

Project entry point for `.ontrail` and `.offtrail` rules, watched directories, tracked assets, and their event history.

- [`Trail(path)`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/trail.py#L232) searches the supplied directory and its ancestors for the nearest `.trail`. [`Trail.locate()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/trail.py#L288) stops at `TRAIL_CEILING_DIRECTORIES`. For a new project, `Trail(path, ontrail='geospatial')` starts `.ontrail` from a preset.
- [`Trail.clear()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/trail.py#L358) untracks assets and discards the log. The project ID, `.ontrail`, and `.offtrail` remain; live Dirs are logged again with their existing IDs.
- [`trail.ontrail`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/trail.py#L183) and [`trail.offtrail`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/trail.py#L199) hold the `OnTrail` discovery rules and `OffTrail` exclusion rules described next.
- [`trail.entries`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/trail.py#L168) is an `Entries`, a lookup over the assets and the Dirs together. The section on `Entries` below describes it.

### [Rule](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/rule.py#L26), [Rules](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/rule.py#L493), and [TXT](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/rule.py#L332)

`Rule` is an immutable string with the syntax operations shared by inclusion and exclusion rules. `Rules` holds an ordered collection of these values and is the base of `OnTrail` and `OffTrail`. Its `TXT` object reads and edits the corresponding file, preserving comments and blank lines.

- [`Rule.parse()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/rule.py#L29) separates a rule into its directory anchor and matching pattern. `validate()`, `kind()`, and `ambiguous_directory()` check or describe the same text.
- [`Rule.canonicalize()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/rule.py#L198) turns typed shortcuts such as `csv` into another `Rule`, here `*.csv`. The project directory and working directory are explicit arguments.
- [`Rules.available()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/rule.py#L518) lists the presets of the owning subclass. [`TXT.create()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/rule.py#L412) copies from that subclass's preset directory, so `.offtrail` never uses an inclusion preset.

### [OnTrail](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/ontrail.py#L142) and [Scopes](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/ontrail.py#L43)

```text
OnTrail (4)
    ./
    *.csv
    data/*.png
    *.zarr

Scopes (2)
    *.csv
    *.zarr
```

`.ontrail` is a text file beside `.trail` that says which directories to watch and which files to track in them, one rule per line. `OnTrail` holds its rules, and `Scopes` holds the compiled matching rules for one directory or the roots.

- [`trail.ontrail(*paths)`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/ontrail.py#L158) tracks files explicitly and treats a directory as one `DirAsset`. A single path returns its asset; multiple paths return a list. Calling it leaves both rule files unchanged.
- [`trail.ontrail`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/trail.py#L183) reads `.ontrail` from the project directory. Root rules such as `./` name directories to watch recursively.
- [`trail.scopes`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/trail.py#L156) contains root patterns such as `*.csv`. These apply at any depth beneath a root. A directory rule such as `data/*.png` applies relative to that directory.
- [`Scopes.decide()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/ontrail.py#L127) supports inclusion and `!` exclusion rules. Matching ignores case; the last matching rule within a scope wins. [`Dirs.match()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/dir.py#L301) consults the deepest matching Dir before its ancestors and root scopes.
- Directory patterns address direct children unless they include a pattern reaching subfolders, such as `**`. A root rule alone watches a directory without selecting all its files.
- Bundled presets include [`default`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/ontrail/default.txt), [`general`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/ontrail/general.txt), [`geospatial`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/ontrail/geospatial.txt), and [`ml`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/ontrail/ml.txt). The default contains only `./`; the other presets also select file extensions.
- [`OnTrail.add()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/ontrail.py#L183) writes canonical rules to `.ontrail` and immediately discovers existing matches. For example, `trail.ontrail.add('csv')` adds `*.csv`.
- [`OnTrail.remove()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/ontrail.py#L225) removes rules without untracking the assets they took. It warns when such assets remain.
- [`OnTrail.replace()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/rule.py#L573) makes the file hold exactly the given rules. Every change refreshes the Dirs, and edits preserve comments and blank lines.
- [`OnTrail.warnings`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/ontrail.py#L294) reports invalid rules, ambiguous directory syntax, and attempts to use directory datasets as scan roots.

### [OffTrail](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/offtrail.py#L90)

```text
OffTrail (1)
    .*
```

`.offtrail` is the companion text file that excludes paths from automatic tracking before `.ontrail` is considered. `OnTrail` and `OffTrail` share the [`Rules`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/rule.py#L493) base for ordered rules, file edits, and comment preservation.

- [`trail.offtrail(*paths)`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/offtrail.py#L102) removes assets from active tracking and adds their paths to `trail.entries.untracked`, which excludes them from later automatic discovery, including after reopening the project. Calling it leaves both rule files unchanged.
- [`trail.offtrail`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/trail.py#L199) starts from the [`default` preset](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/offtrail/default.txt), containing only `.*` to exclude dotfiles and hidden folders. A missing file is recreated with this default; an empty file ignores nothing.
- [`OffTrail.decide()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/offtrail.py#L205) uses the same rule syntax as `.ontrail`, ignoring case. The last matching rule wins; adding `!.env` hands `.env` back to `.ontrail` and immediately discovers it if selected.
- [`OffTrail.add()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/offtrail.py#L146) leaves existing assets tracked and warns when the new rule excludes them. `trail.offtrail(path)` removes them.
- [`OffTrail.remove()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/offtrail.py#L185) scans again, tracking newly eligible paths that `.ontrail` selects and that were not explicitly untracked.
- [`trail.ontrail()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/ontrail.py#L158) explicitly tracks a path even when `.offtrail` excludes it. Trail metadata remains protected.

### [Dir](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/dir.py#L20) and [Dirs](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/dir.py#L186)

```text
Dirs (2)
    0. Dir
        id: db2a5e359a264bd59ad134a1fa752567
        path: /tmp/tmpn0alq_gb
        root: True
        scopes: []
    1. Dir
        id: 37e9e69cd84445b49074b8434380c788
        path: /tmp/tmpn0alq_gb/data
        root: False
        scopes: [
            ./*.png,
        ]
```

A `Dir` now represents a directory named by `.ontrail`, carrying its root status and local scopes.

- [`Dirs.refresh()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/dir.py#L235) creates and removes Dirs as rules change, recording `AddDirEvent` and `RemoveDirEvent`. Surviving Dirs retain their IDs across refreshes and reloads.
- [`Dir.decide()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/dir.py#L81) evaluates local patterns. Ordinary subfolders do not acquire separate Dir records merely because a recursive watch encounters them.
- Directory moves repath tracked descendant assets. The Dir and its rule remain anchored at the configured path; a warning identifies a rule whose directory moved.
- A directory tracked whole as a `DirAsset` is excluded from `Dirs`. Its dataset children are not enrolled as separate assets by discovery.

### [Asset](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/asset.py#L25) and [DirAsset](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/asset.py#L192)

```text
Asset
    id: a09b507f20c74514a99ebdd3f6a1ad14
    path: /tmp/tmpn0alq_gb/new.csv
    st_size: 18 B
    st_mtime: 2026-10-30 19:57:50

DirAsset
    id: 03c3213e4ee442a9bfddeac83cb621d8
    path: /tmp/tmpn0alq_gb/cube.zarr
    files: 2
    st_size: 5 B
    st_mtime: 2026-10-30 19:57:50
```

Files and directory datasets share the `Assets` collection at `trail.assets`. A directory dataset has one stable ID.

- [`Assets.add()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/asset.py#L361) accepts a mapping of resolved paths to the rule that selected each one, or `None` for explicit additions. `OnTrail.__call__` and `Assets.scan` share this operation. It returns assets in request order, reuses existing assets without logging them again, and logs additions, refreshes Dirs, and reconciles watches.
- [`Asset.from_path()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/asset.py#L70) chooses `Asset` for a file or `DirAsset` for a directory. Explicitly tracking a directory does not recursively track its contents as separate resources.
- [`DirAsset.is_match()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/asset.py#L217) recognizes `.zarr`, `.n5`, `.gdb`, and `.safe` directories during discovery. [`Assets.scan()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/asset.py#L443) can select the dataset itself and skips descending into its chunks.
- [`DirAsset.suffix2children`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/asset.py#L201) holds format-specific filename patterns, and [`DirAsset.children`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/asset.py#L249) picks the one for a dataset. They distinguish dataset activity from temporary files, locks, and unrelated files. Other explicitly tracked directories include every child.
- [`Assets.holder()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/asset.py#L519) prevents separately tracking dataset children. Unrelated files inside a recognized dataset can still be tracked independently. Tracking a directory whole untracks any already-tracked children it absorbs and warns about them.
- [`Asset.fingerprint`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/asset.py#L123) computes file count, total bytes, and newest modification time on access. For directories it inspects all nonsymlink descendants, independent of the activity filename filter. Reprs display live size and modification time, plus file count for directory assets; missing resources display `<missing>`.

### [Collection](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/collection.py#L46) and [Select](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/select.py#L42)

```text
>>> trail.events.select('cls=AddAssetEvent rule=*.csv')
Events (1)
    0. AddAssetEvent
        id: f96b3b6643514f54a66165b1c89166b4
        timestamp: 02:57:50.384
        src_path: /tmp/tmpn0alq_gb/new.csv
        rule: *.csv
        st_size: 18 B
        st_mtime: 2026-10-30 19:57:50
```

Shared ordered storage and selection for events, assets, and Dirs.

- [`Collection`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/collection.py#L46) holds records by ID and insertion order. Iteration yields records; membership accepts a record or its lookup key, and `get()` supports a default.
- [`EntryCollection`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/entry.py#L113) is the base of `Assets` and `Dirs`. It adds lookup by path to lookup by ID, and several keys return a tuple.
- [`collection.select[index]`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/select.py#L87) supports positive and negative positions. Slices return a selection of the same collection type.
- [`collection.select(expression)`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/select.py#L78) supports named field comparisons, quoted values, parentheses, `not`, `and`, and `or`. Adjacent terms imply `and`.
- Slices are ordered expression terms: `event_type=modified -5:` selects the last five matching events; `-5: event_type=modified` filters only the last five events.
- [`Select.compare()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/select.py#L220) converts comparison values to the field's type. `cls` includes subclasses, record references compare by ID, and timestamps accept ISO datetimes or a UTC clock time for today.
- [`Select.subset()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/select.py#L199) copies collection membership while sharing records and the project parent. Clearing an event selection leaves the persisted log unchanged; clearing an asset selection untracks its selected assets from the project.

### [Entries](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/entries.py#L17)

```text
>>> trail.entries
Entries (5)
    0. Dir
        id: db2a5e359a264bd59ad134a1fa752567
        path: /tmp/tmpn0alq_gb
        root: True
    1. Dir
        id: 37e9e69cd84445b49074b8434380c788
        path: /tmp/tmpn0alq_gb/data
        root: False
        scopes: ./*.png
    2. DirAsset
        id: 03c3213e4ee442a9bfddeac83cb621d8
        path: /tmp/tmpn0alq_gb/cube.zarr
        files: 2
        st_size: 5 B
        st_mtime: 2026-10-30 19:57:50
    3. Asset
        id: a09b507f20c74514a99ebdd3f6a1ad14
        path: /tmp/tmpn0alq_gb/new.csv
        st_size: 18 B
        st_mtime: 2026-10-30 19:57:50
    4. Asset
        id: b3f7f2d49cd84d918ea047bee0a84875
        path: /tmp/tmpn0alq_gb/data/map.png
        st_size: 8 B
        st_mtime: 2026-10-30 19:57:50
```

`trail.entries` looks up the assets and the Dirs together. It replaces main's `EntryLookup` and holds no entries of its own: each call reads `trail.assets` and `trail.dirs`, so nothing has to be kept in sync with them.

- [`trail.entries[key]`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/entries.py#L37) returns the asset or the Dir with an ID or at a path, trying the assets first. Several keys return a tuple, and a key that is neither raises `KeyError`. [`get()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/entries.py#L70) returns `None` or a default instead.
- `key in trail.entries` holds for an entry, an ID, or a path. Iteration yields the Dirs, then the assets, each in the order it was added; `len` counts both.
- It has no `select`, since a selection is a copy of one collection. `trail.assets.select` and `trail.dirs.select` remain.
- [`untracked`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/entries.py#L29) holds the paths removed with `offtrail()`. Scans and walks skip them, replay of the log restores them, and tracking a path again removes it.
- [`is_metadata()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/entries.py#L112) is true for `.trail` and anything inside it, plus `.ontrail` and `.offtrail`. Tracking, moves, and the watchdog refuse such paths.
- [`is_trackable()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/entries.py#L131) decides whether a scan tracks a path: a file or a directory dataset that `.ontrail` takes, and not already tracked, untracked, Trail metadata, excluded by `.offtrail`, or part of a dataset.
- [`is_walkable()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/entries.py#L163) decides whether a walk enters a directory. Symlinks, Trail metadata, untracked paths, and directory datasets are never entered. A directory excluded by `.offtrail`, such as `.git` by default, is entered only if a Dir sits at or below it.

### [DirectoryAccessEvent](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/event.py#L444)

```text
Events (1)
    0. DirectoryAccessEvent
        id: 4bcec4c4415d488e9fd61e7f207ce67f
        timestamp: 02:57:50.691
        src_path: /tmp/tmpn0alq_gb/cube.zarr
        event_type: modified
        started: 02:57:50.589
        count: opened 2, modified 2
```

One synthetic event summarizes activity inside a directory dataset.

- [`Access`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/session.py#L91) accumulates delivered `opened`, `created`, `modified`, `moved`, and `deleted` events for one dataset. Concurrent readers and writers share the access.
- `started` records the first event's arrival time; `timestamp` records the last. `count` measures filesystem events delivered by the observer, not distinct files or application-level operations.
- An access containing only opens is recorded as `opened`. Any other counted activity is recorded as `modified`. Temporary-file renames into dataset files count as writes; ignored lock files do not turn a read into a write.
- [`DirAsset.access`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/asset.py#L261) exposes the current access. [`WatchdogSession.idle_gap`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/session.py#L511) defaults to 30 seconds; an idle access is closed and logged once. Empty accesses are discarded.
- Accesses close before dataset moves, deletion, or untracking, preserving the activity's resource and path. Rewrites and moves retain the dataset's ID.
- [`Events.clear()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/event.py#L826) drops pending accesses when discarding the live log. Access events serialize their start time and operation counts for replay.

### [Watchdog](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/watchdog.py#L14) and [WatchdogSession](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/session.py#L495)

```text
Watchdog
    running: False
    watches: [
        /tmp,
        /tmp/tmpn0alq_gb,
    ]
```

Watch placement is separated from the observer run. Outside an asyncio loop, the repr shows the watches a later run would place.

- [`Watchdog.desired()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/watchdog.py#L81) requests recursive watches for Dirs and directory assets, plus parent watches for moves and deletion. Watches already covered by a recursive ancestor are omitted.
- [`WatchdogSession.start()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/session.py#L644) starts observation on a running asyncio loop. Tracking and rule changes reconcile watches automatically; synchronous use remains dormant until a loop starts a session.
- [`async with trail.watchdog.session`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/session.py#L729) manages a run explicitly. Each run owns its observer, watches, queue, handler, and consumer task.
- [`Handler`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/session.py#L318) receives filesystem events on the observer thread. It timestamps arrivals, counts directory accesses, and queues other events; the consumer applies project changes on the asyncio loop. Its `.offtrail` checks read rule and watch snapshots published by the loop, without reading live Trail collections.
- Directories excluded by `.offtrail` are skipped unless explicitly covered, and missing-watch failures warn without stopping unrelated watches. [`Watch.notify()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/event.py#L639) wakes event consumers safely across threads and ignores closed loops.
- [`WatchdogSession.stop()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/session.py#L748) stops the observer, drains delivered events, and closes accesses. A later run replaces the observer and its per-run state.

## Command-Line Interface

The `trail` command opens a project in an interactive console. A project without a `.trail` gets a setup walkthrough first. The console prints each event as it is recorded, and its commands list and edit the rules, the Dirs, the assets, and the log.

### [Initialization](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/init.py#L443)

```text
trail [PATH]
trail init [PATH]
python -m trail [PATH]
```

[`main()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/__init__.py#L42) runs setup for a directory without a Trail, then opens the console. `trail init` runs setup alone and exits, and `--nodir` skips setup and keeps events in memory. This is setup on a new project that holds `streets.geojson`, `elevation.tif`, and `sidewalks.csv`, with `geospatial` chosen by pressing <DOWN> twice, then <ENTER>:

```text
$ trail init
🌲 Welcome to Trail! 🌳

Trail leaves breadcrumbs along your data journey: the interactions git never sees,
such as opening a dataset, viewing it on a map, or carrying it between tools, so you
can retrace your project's steps.
Read more at https://geojupyter.github.io/trail

🎒 Let's start a Trail! 🥾

.ontrail says which folders to check and which files to automatically track in them. 🪧
  - A line ending in `/` is a folder to track.
  - A line with an extension like `*.csv` tracks those files in the tracked folders.
  - A combination e.g. `data/*.png` tracks extensions under specific folders.
  - A line starting with `!` e.g. `!requirements.txt` excludes that pattern from tracking.
  - A line with `./` tracks the project directory itself.

.offtrail excludes paths from automatic tracking before .ontrail is checked. 🚧
  - It starts with `.*`, which skips dotfiles and hidden folders such as `.git`.
  - Its lines are written as in .ontrail, and win over it.
  - A line starting with `!` e.g. `!.env` hands a path back to .ontrail.

Let's pick a preset for .ontrail to start from 🪨:
  - <UP> and <DOWN> to browse presets.
  - <ENTER> to choose one.
  - Enter a path to start from a file of your own.
  - <ESCAPE> to start with no rules.

  rules from geospatial

Trail started in /tmp/tmpqka3wy54/project 🌲
  ontrail: ./ *.feather *.geojson *.gpkg *.shp *.shx *.dbf *.prj *.cpg *.tif *.tiff *.kml *.kmz *.gpx *.parquet *.geoparquet *.fgb *.zarr *.gdb *.safe
  offtrail: .*
  tracked: 2 assets in 1 dir
Edit .ontrail and .offtrail in /tmp/tmpqka3wy54/project to change the rules.
```

Between the hints and `rules from geospatial`, the preset picker runs below the hints. It lists the presets on the left and previews the selected one on the right, and it is erased once a preset is chosen. This is the picker on an 80-column terminal, just before <ENTER> chooses `geospatial`:

```text
    default    │ ./
    general    │ *.feather
  > geospatial │ *.geojson
    ml         │ *.gpkg
               │ *.shp
               │ *.shx
               │ *.dbf
               │ *.prj
               │ *.cpg
               │ *.tif
               │ *.tiff
               │ *.kml
               │ *.kmz
               │ *.gpx
               │ *.parquet
               │ *.geoparquet
               │ *.fgb
               │ *.zarr
               │ *.gdb
               │ *.safe
  path:
```

- [`Picker`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/init.py#L92) previews the bundled presets and files of the user's own. Arrow keys choose a preset, and a path typed at `path:` previews that file, or the `.ontrail` inside a typed directory. Escape starts with no rules, and interrupting setup writes nothing.
- Setup writes `.ontrail`, then opens the Trail, creating the default `.offtrail` if it is missing and tracking what the rules take. Here that is `streets.geojson` and `elevation.tif`, but not `sidewalks.csv`, since `geospatial` has no `*.csv` rule.
- Reinitialization replaces `.ontrail` while retaining `.offtrail`, the project ID, tracked assets, and event history.
- The package now includes the inclusion presets in `trail/ontrail/` and exclusion presets in `trail/offtrail/` and depends on `pathspec` for pattern matching, with `prompt_toolkit>=3.0.52` for terminal input.

### [Console](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/console.py#L36) and [Feed](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/feed.py#L19)

The console is an editable command bar below a feed. The feed starts with a banner, then prints each event as it is recorded, along with the output of each command. Reopening the project from the reprs above:

```text
$ trail
🌱🪵🌳 Welcome back to Trail! 🌳🪵🌱

trail #e7d77d814d7a4b6b88b316b8308b475e on /tmp/tmpn0alq_gb
assets 3  dirs 2  events 6
type 'help' for help
```

The header row above the command bar keeps the counts current while the watchdog runs:

```text
 trail #e7d77d814d7a4b6b88b316b8308b475e  /tmp/tmpn0alq_gb  assets 3  dirs 2  events 6  watching
```

- [`Run`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/console.py#L292) prints the [banner](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/console.py#L426), then the events recorded during the run. Earlier history remains available through `events`. Its shutdown path also drains access events closed by the session.
- [`Feed`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/feed.py#L19) prints ordinary terminal output, retaining scrollback and text selection. Its in-memory row history is bounded to 2,000 rows. [`Feed.write()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/feed.py#L37) prints a date row before the first event of each day.
- [`Renderer`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/render.py#L27) shows project-relative paths, positions in the collection, resource IDs, and counts. [`Renderer.header()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/render.py#L183) also reports open dataset accesses, and `watching` or `idle`. [`Renderer.verb()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/render.py#L244) colours each heading: `tracked` or `discovered`, `untracked`, `watched`, `unwatched`, or the watchdog's event type.

### Assets and rules: [`ontrail`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/commands.py#L230) and [`offtrail`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/commands.py#L322)

`ontrail PATH...` tracks paths immediately, and `offtrail PATH...` removes assets from tracking. Without arguments, each command shows its rule file; `add` and `remove` edit that file. Here a `*.md` rule picks up `notes.md`, is removed again, and `notes.md` is untracked:

```text
> ontrail
  ./          root                        ./
  *.csv       root scope                  every root
  data/*.png  dir scope · not subfolders  data/
  *.zarr      root scope                  every root
> ontrail add md
added to .ontrail: *.md (root scope); tracked 1 now
Friday 30 October 2026
6. AddAssetEvent
    id: 982fa916800d4601b1e951aecc0950a2
    timestamp: 02:57:51.404
    src_path: notes.md
    rule: *.md
    st_size: 8 B
    st_mtime: 2026-10-30 19:57:50
    entry: da339e0b021b4462bbc7a35fd27d7d4e
> ontrail remove md
removed from .ontrail: *.md
removing '*.md' leaves 1 asset tracked; offtrail removes it
> offtrail notes.md
7. RemoveAssetEvent
    id: 247a41c450d5464792c91a6c0f2dea3f
    timestamp: 02:57:51.411
    src_path: notes.md
    entry: da339e0b021b4462bbc7a35fd27d7d4e
```

- [`ontrail`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/commands.py#L230) alone prints each rule with its kind and the Dir it belongs to, then any `OnTrail.warnings`.
- [`ontrail add`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/commands.py#L256) adds one rule per pattern and tracks what it takes now; the feed then prints the events. `md`, `.md`, and `**/*.md` all add `*.md`, and an existing directory gets a trailing `/`.
- [`ontrail remove`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/commands.py#L164) only edits `.ontrail`. Assets that a removed rule took stay tracked, and the warning says how many.
- [`offtrail PATH...`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/commands.py#L343) records a `RemoveAssetEvent` for each path and keeps the path out of later scans. Its globs match tracked paths the way `.ontrail` reads them, so a deleted file can still be removed. [`ontrail PATH...`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/commands.py#L286) tracks paths now, expanding globs against the filesystem.
- [`offtrail`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/commands.py#L322) lists and edits `.offtrail` through the same command interface. Adding a rule warns about assets left tracked; removing a rule discovers eligible matches again.
- <TAB> after `ontrail` completes filesystem paths; after `offtrail` it completes tracked paths. Both also offer `add` and `remove`, which complete paths, preset extensions as `*.ext`, and existing rules with their kinds. Files named `add` or `remove` can be addressed as `./add` or `./remove`. [`RuleCompleter`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/rule_completion.py#L51) is the base of these completers, and [`RulePaths`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/rule_completion.py#L84) completes paths while preserving a leading `!`.

### Listings: [`dirs`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/commands.py#L375), [`assets`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/commands.py#L362), and [`events`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/commands.py#L408)

Each listing prints the records of one collection, numbered by their position in it, as the feed prints events:

```text
> dirs
dirs (2 of 2)
0. Dir
    id: db2a5e359a264bd59ad134a1fa752567
    path: .
    root: True
1. Dir
    id: 37e9e69cd84445b49074b8434380c788
    path: data
    root: False
    scopes: ./*.png
> assets
assets (3 of 3)
0. DirAsset
    id: 03c3213e4ee442a9bfddeac83cb621d8
    path: cube.zarr
    files: 2
    st_size: 5 B
    st_mtime: 2026-10-30 19:57:50
1. Asset
    id: a09b507f20c74514a99ebdd3f6a1ad14
    path: new.csv
    st_size: 18 B
    st_mtime: 2026-10-30 19:57:50
2. Asset
    id: b3f7f2d49cd84d918ea047bee0a84875
    path: data/map.png
    st_size: 8 B
    st_mtime: 2026-10-30 19:57:50
```

What follows a listing's name is a selection, the same expression `collection.select()` takes in Python. A term compares one field, and a slice cuts what the terms before it left:

```text
> assets st_size>5
assets (2 of 3)
1. Asset
    id: a09b507f20c74514a99ebdd3f6a1ad14
    path: new.csv
    st_size: 18 B
    st_mtime: 2026-10-30 19:57:50
2. Asset
    id: b3f7f2d49cd84d918ea047bee0a84875
    path: data/map.png
    st_size: 8 B
    st_mtime: 2026-10-30 19:57:50
> events src_path=new.csv
events (1 of 6)
3. AddAssetEvent
    id: f96b3b6643514f54a66165b1c89166b4
    timestamp: 02:57:50.384
    src_path: new.csv
    rule: *.csv
    st_size: 18 B
    st_mtime: 2026-10-30 19:57:50
    entry: a09b507f20c74514a99ebdd3f6a1ad14
> events -3:
events (3 of 6)
3. AddAssetEvent
    id: f96b3b6643514f54a66165b1c89166b4
    timestamp: 02:57:50.384
    src_path: new.csv
    rule: *.csv
    st_size: 18 B
    st_mtime: 2026-10-30 19:57:50
    entry: a09b507f20c74514a99ebdd3f6a1ad14
4. AddAssetEvent
    id: 3cd8c922db5948c08afaf07e5044a6b5
    timestamp: 02:57:50.384
    src_path: data/map.png
    rule: data/*.png
    st_size: 8 B
    st_mtime: 2026-10-30 19:57:50
    entry: b3f7f2d49cd84d918ea047bee0a84875
5. DirectoryAccessEvent
    id: 4bcec4c4415d488e9fd61e7f207ce67f
    timestamp: 02:57:50.691
    src_path: cube.zarr
    event_type: modified
    started: 02:57:50.589
    count: opened 2, modified 2
    entry: 03c3213e4ee442a9bfddeac83cb621d8
```

- [`Listing`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/command.py#L224) is the base of the three. Without a slice, a listing shows the last 20 matches. The header counts the matches against the whole collection, and each record keeps its position in the collection.
- [`Query`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/command.py#L188) reads path values against the project root, so `src_path=new.csv` matches the absolute path the log holds. `st_size>5` compares sizes as numbers, so the 5-byte `cube.zarr` is left out.
- Terms side by side must all hold. `or`, `not`, and parentheses combine them, and `cls=DirectoryAccessEvent` selects by class, including subclasses. For example, `assets path=new.csv or name=map.png` lists the same two assets as `assets st_size>5`.
- Every listing takes `clear`. `assets clear -f` untracks what it lists, and `events clear -f` discards the log. [`clear -f`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/commands.py#L424) on its own does both. Without `-f`, each reports what it would discard. `dirs clear` is refused, since a Dir goes when its rules leave `.ontrail`.
- <TAB> completes field names as `field=` terms, read from the record classes of the collection.

### [Commands](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/commands.py#L532)

```text
>>> console.commands
Commands (ontrail, offtrail, assets, dirs, events, clear, help, restart, quit, exit)
>>> console.commands.find('ev')
EventsCommand('events')
```

Every subclass of `Command` with a name registers itself, so dispatch, `help`, and completion read the same table. `help` lists them:

```text
> help
commands
  ontrail [PATH...|add RULE...|remove RULE...]   track paths now; list or edit .ontrail rules
  offtrail [PATH...|add RULE...|remove RULE...]  stop tracking paths; list or edit .offtrail rules
  assets [EXPRESSION]                            the tracked files, sliced or filtered
  dirs [EXPRESSION]                              the directories .ontrail names, sliced or filtered
  events [EXPRESSION]                            the recorded events, sliced or filtered
  clear [-f]                                     discard the project record: untrack everything, then empty the log
  help                                           this list
  restart [-f]                                   reopen this project in a new process, picking up edits to the console
  quit                                           leave the session
```

- [`Commands.find()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/commands.py#L582) accepts unambiguous prefixes, so `ev` runs `events`. Tab completion covers command names, then each command's arguments.
- [`help`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/commands.py#L450) also lists the key bindings and notes on how `ontrail PATH` differs from `ontrail add RULE`, on `.offtrail`, on selections, and on `clear`.
- [`restart`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/cli/commands.py#L489) reopens the console in a new process. With `--nodir` the log would be lost, so it needs `-f`.

## Compatibility

- `register()` and `unregister()` become `trail.ontrail()` and `trail.offtrail()`.
- `trail.entries` still looks up assets and Dirs by ID or path, but it is no longer a `Mapping`. Iteration yields entries, Dirs first, instead of IDs, and `ids`, `items()`, `by_pos`, and `clear()` are gone.
- `.by_pos[...]` becomes `.select[...]`, and collection iteration yields records instead of IDs.
- Explicit directory tracking returns one `DirAsset`; `.ontrail` controls recursive file discovery, and Dirs come only from `.ontrail`.
- Observer lifecycle calls move to `trail.watchdog.session`, a `WatchdogSession`; `watchdog.context()` becomes `async with watchdog.session`.
- `AddEntryEvent` and `RemoveEntryEvent` become `AddAssetEvent` and `RemoveAssetEvent`, with separate events for the Dirs that `.ontrail` names. Serialized fields `size` and `mtime` become `st_size` and `st_mtime`. No migration is provided for the previous log format: [`Event.from_record()`](https://github.com/geojupyter/prototype-trail/blob/{commit}/src/trail/event.py#L111) rejects the former class names.

## Validation

The expanded test suite covers:

- [rule parsing, canonicalization, string values, and preset ownership](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_rule.py)

- [`.ontrail` presets, persistence, and comments](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_ontrail.py), [scope precedence and exclusions](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_scopes.py), and [Dir identity and history](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_dirs.py)
- [`.offtrail` defaults, precedence, exclusions, edits, and explicit tracking](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_offtrail.py)
- [directory dataset formats, child matching, and reload](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_dirassets.py), plus [access counts, timing, concurrency, moves, and shutdown](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_accesses.py)
- [lookups across assets and Dirs, untracked paths across reloads, and Trail metadata](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_entries.py)
- [selection parsing, comparison types, slices, and collection copies](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_select.py)
- [setup and preset previews](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_init.py), [listings, the rule commands, and clearing](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_listing.py), [terminal input](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_input.py), and [the console's welcome banner](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_welcome.py)
- [observer session lifecycle, rule snapshots, and cross-thread delivery](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_session.py), [recursive and parent watch placement](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_watches.py), and [project persistence and stable identities](https://github.com/geojupyter/prototype-trail/blob/{commit}/tests/test_trail.py)

The new [GitHub Actions workflow](https://github.com/geojupyter/prototype-trail/blob/{commit}/.github/workflows/test.yml#L1) runs pytest with Python 3.12 on Ubuntu, macOS, and Windows. Open-event tests account for the observer backend's platform support.

Local validation ran on the current working tree with Python 3.12.14 and pytest 9.1.1. `ruff check src tests` and `git diff --check` passed. An offline wheel build included all five preset files; importing directly from the wheel verified both modules, default rules, and preset-based discovery.

```text
243 passed in 10.61s
```
