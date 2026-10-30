# Add interactive tracking, marker rules, and directory dataset access logging

<!--
Local draft for hodczak -> main, following https://github.com/geojupyter/prototype-trail/pull/1.
Compared main 881423f3dc564dd06f7a1c8369e326d3ef50e610 with hodczak c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e.
The draft also includes the uncommitted CLI completion rename: cli/scopes.py -> cli/marker_completion.py,
Scope -> MarkerCompleter, ScopePaths -> MarkerPaths, and the corresponding imports and test references.
Other uncommitted source/test edits are excluded from the described changes.
Reprs were generated from an exported copy of c4fb319.
GitHub source URLs were checked against that commit in geojupyter/prototype-trail.
MarkerCompleter and MarkerPaths currently link to the local file because their renamed definitions are not committed.
After squashing or adding fixes, regenerate the reprs, rerun validation, and update source hashes/anchors.

Before publication:
- Replace the local completion links with commit-pinned GitHub links after committing the rename.
- Decide whether the two internal notes files (3,380 added lines) belong in the merge.
- Remove the stray "rst" added under "Definition of done" in PROJECT_PLAN.md.
- Update the toy-project tutorial before using it as the end-to-end example: it still uses
  trail.entries, .by_pos, AddEntryEvent, and watchdog.stop(), and its closing prose says no CLI exists.
This comment is drafting context, not part of the proposed public description.
-->

This PR adds an interactive terminal interface, persistent rules for discovering assets, and access logging for datasets stored as directories.

The tracking model now distinguishes directories watched for matching files from directories tracked as one dataset. Marker rules select files and datasets; directory datasets retain one resource ID, and activity inside them is summarized into access events. Shared collections expose the same selection expressions through Python and the terminal.

**Examples:** The [directory dataset tests](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/tests/test_dirassets.py#L20) and [access logging tests](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/tests/test_accesses.py#L53) demonstrate the new behavior. The reprs below come from a temporary project containing a CSV file, a scoped PNG file, and a two-file Zarr directory.

## Core Classes

### [Trail](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/trail.py#L71)

```text
Trail
    id: 83ab54b8426f496e939d02f686d664c0
    dir: /tmp/tmpio4gg9r_/.trail
    dirs: [
        /tmp/tmpio4gg9r_,
        /tmp/tmpio4gg9r_/data,
    ]
    assets: [
        /tmp/tmpio4gg9r_/cube.zarr,
        /tmp/tmpio4gg9r_/new.csv,
        /tmp/tmpio4gg9r_/data/map.png,
    ]
```

Project entry point for marker rules, watched directories, tracked assets, and their event history.

- [`Trail(path)`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/trail.py#L218) searches the supplied directory and its ancestors for the nearest `.trail`. [`Trail.locate()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/trail.py#L271) stops at `TRAIL_CEILING_DIRECTORIES`.
- [`Trail.track(*paths)`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/trail.py#L303) tracks files explicitly and treats a directory as one `DirAsset`. A single path returns its asset; multiple paths return a list.
- [`Trail.mark(*rules)`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/trail.py#L423) writes canonical rules to `.markers` and immediately discovers existing matches. For example, `trail.mark('csv')` adds `*.csv`.
- [`Trail.unmark(*rules)`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/trail.py#L458) removes rules without removing assets already tracked by them. It warns when matching assets remain.
- [`Trail.offtrail(*paths)`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/trail.py#L396) removes assets from active tracking and records their paths as excluded from later automatic discovery, including after reopening the project.
- [`Trail.clear()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/trail.py#L593) offtrails assets and discards the log. The project ID and `.markers` remain; live Dirs are logged again with their existing IDs.

### [Markers](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/markers.py#L600) and [Scopes](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/markers.py#L356)

```text
Markers (4)
    ./
    *.csv
    data/*.png
    *.zarr

Scopes (2)
    *.csv
    *.zarr
```

`Markers` holds the editable configuration. `Scopes` holds the compiled matching rules for one directory or the roots.

- [`trail.markers`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/trail.py#L179) reads `.markers` from the project directory. Root lines such as `./` name directories to watch recursively.
- [`trail.scopes`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/trail.py#L157) contains root patterns such as `*.csv`. These apply at any depth beneath a root. A directory rule such as `data/*.png` applies relative to that directory.
- [`Scopes.decide()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/markers.py#L433) supports inclusion and `!` exclusion rules. Matching ignores case; the last matching rule within a scope wins. [`Dirs.match()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/dir.py#L300) consults the deepest matching Dir before its ancestors and root scopes.
- Directory patterns address direct children unless they include a pattern reaching subfolders, such as `**`. A root line alone watches a directory without selecting all its files.
- Bundled presets include [`default`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/markers/default.txt), [`general`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/markers/general.txt), [`geospatial`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/markers/geospatial.txt), and [`ml`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/markers/ml.txt). The default contains only `./`; the other presets also select file extensions.
- [`Markers.add()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/markers.py#L663), [`discard()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/markers.py#L681), and [`replace()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/markers.py#L702) update the file and refresh Dirs. Incremental edits preserve comments and blank lines.
- [`Markers.warnings`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/markers.py#L734) reports invalid rules, ambiguous directory syntax, and attempts to use directory datasets as scan roots.

### [Dir](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/dir.py#L19) and [Dirs](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/dir.py#L185)

```text
Dirs (2)
    0. Dir
        id: 3450ecfbbb954733a538644ca5a535cf
        path: /tmp/tmpio4gg9r_
        root: True
        scopes: []
    1. Dir
        id: 93a042dd23354370a5cd40f781bf97ea
        path: /tmp/tmpio4gg9r_/data
        root: False
        scopes: [
            ./*.png,
        ]
```

A `Dir` now represents a directory named by `.markers`, carrying its root status and local scopes.

- [`Dirs.refresh()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/dir.py#L234) creates and removes Dirs as rules change, recording `AddDirEvent` and `RemoveDirEvent`. Surviving Dirs retain their IDs across refreshes and reloads.
- [`Dir.decide()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/dir.py#L80) evaluates local patterns. Ordinary subfolders do not acquire separate Dir records merely because a recursive watch encounters them.
- Directory moves repath tracked descendant assets. The Dir and its marker remain anchored at the configured path; a warning identifies a marker whose directory moved.
- A directory tracked whole as a `DirAsset` is excluded from `Dirs`. Its dataset children are not enrolled as separate assets by discovery.

### [Asset](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/asset.py#L21) and [DirAsset](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/asset.py#L185)

```text
Asset
    id: fa323492d18c4f4bbd484b59b1e3fd39
    path: /tmp/tmpio4gg9r_/new.csv
    st_size: 18 B
    st_mtime: 2026-10-21 00:55:01

DirAsset
    id: 7ec4e5a551374f6393f81fcbb68b50c2
    path: /tmp/tmpio4gg9r_/cube.zarr
    files: 2
    st_size: 5 B
    st_mtime: 2026-10-21 00:55:01
```

Files and directory datasets share the tracked-asset collection. A directory dataset has one stable ID.

- [`Asset.from_path()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/asset.py#L66) chooses `Asset` for a file or `DirAsset` for a directory. Explicitly tracking a directory does not recursively track its contents as separate resources.
- [`DirAsset.is_match()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/asset.py#L197) recognizes `.zarr`, `.n5`, `.gdb`, and `.safe` directories during discovery. [`Assets.scan()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/asset.py#L353) can select the dataset itself and skips descending into its chunks.
- [`DirAsset.children`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/asset.py#L229) uses format-specific filename patterns to distinguish dataset activity from temporary files, locks, and unrelated files. Other explicitly tracked directories include every child.
- [`Assets.holder()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/asset.py#L426) prevents separately tracking dataset children. Unrelated files inside a recognized dataset can still be tracked independently. Tracking a directory whole offtrails any already-tracked children it absorbs and warns about them.
- [`Asset.fingerprint`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/asset.py#L116) computes file count, total bytes, and newest modification time on access. For directories it inspects all nonsymlink descendants, independent of the activity filename filter. Reprs display live size and modification time, plus file count for directory assets; missing resources display `<missing>`.

### [Collection](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/collection.py#L46) and [Select](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/select.py#L42)

```text
>>> trail.events.select('cls=AddAssetEvent rule=*.csv')
Events (1)
    0. AddAssetEvent
        id: 5b6a242e6ff5493e9ecca1a54640412e
        timestamp: 07:55:01.427
        src_path: /tmp/tmpio4gg9r_/new.csv
        rule: *.csv
        st_size: 18 B
        st_mtime: 2026-10-21 00:55:01
```

Shared ordered storage and selection for events, assets, and Dirs.

- [`Collection`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/collection.py#L46) holds records by ID and insertion order. Iteration yields records; membership accepts a record or its lookup key, and `get()` supports a default.
- [`collection.select[index]`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/select.py#L87) supports positive and negative positions. Slices return a selection of the same collection type.
- [`collection.select(expression)`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/select.py#L78) supports named field comparisons, quoted values, parentheses, `not`, `and`, and `or`. Adjacent terms imply `and`.
- Slices are ordered expression terms: `event_type=modified -5:` selects the last five matching events; `-5: event_type=modified` filters only the last five events.
- [`Select.compare()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/select.py#L220) converts comparison values to the field's type. `cls` includes subclasses, record references compare by ID, and timestamps accept ISO datetimes or a UTC clock time for today.
- [`Select.subset()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/select.py#L199) copies collection membership while sharing records and the project parent. Clearing an event selection leaves the persisted log unchanged; clearing an asset selection offtrails its selected assets from the project.

### [DirectoryAccessEvent](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/event.py#L444)

```text
Events (1)
    0. DirectoryAccessEvent
        id: 2c324c4f4e864879aea25025a7de855e
        timestamp: 07:55:01.669
        src_path: /tmp/tmpio4gg9r_/cube.zarr
        event_type: modified
        started: 07:55:01.427
        count: opened 1, modified 2
```

One synthetic event summarizes activity inside a directory dataset.

- [`Access`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/session.py#L91) accumulates delivered `opened`, `created`, `modified`, `moved`, and `deleted` events for one dataset. Concurrent readers and writers share the access.
- `started` records the first event's arrival time; `timestamp` records the last. `count` measures filesystem events delivered by the observer, not distinct files or application-level operations.
- An access containing only opens is recorded as `opened`. Any other counted activity is recorded as `modified`. Temporary-file renames into dataset files count as writes; ignored lock files do not turn a read into a write.
- [`DirAsset.access`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/asset.py#L240) exposes the current access. [`Session.idle_gap`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/session.py#L516) defaults to 30 seconds; an idle access is closed and logged once. Empty accesses are discarded.
- Accesses close before dataset moves, deletion, or offtrailing, preserving the activity's resource and path. Rewrites and moves retain the dataset's ID.
- [`Events.clear()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/event.py#L826) drops pending accesses when discarding the live log. Access events serialize their start time and operation counts for replay.

### [Watchdog](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/watchdog.py#L14) and [Session](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/session.py#L500)

```text
Watchdog
    running: False
    watches: [
        /tmp,
        /tmp/tmpio4gg9r_,
    ]
```

Watch placement is separated from the observer run. Outside an asyncio loop, the repr shows the watches a later run would place.

- [`Watchdog.desired()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/watchdog.py#L81) requests recursive watches for Dirs and directory assets, plus parent watches for moves and deletion. Watches already covered by a recursive ancestor are omitted.
- [`Session.start()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/session.py#L647) starts observation on a running asyncio loop. Tracking and marker changes reconcile watches automatically; synchronous use remains dormant until a loop starts a session.
- [`async with trail.watchdog.session`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/session.py#L735) manages a run explicitly. Each run owns its observer, watches, queue, handler, and consumer task.
- [`Handler`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/session.py#L318) receives filesystem events on the observer thread. It timestamps arrivals, counts directory accesses, and queues other events; the consumer applies project changes on the asyncio loop.
- Hidden directories are excluded unless explicitly covered, and missing-watch failures warn without stopping unrelated watches. [`Watch.notify()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/event.py#L639) wakes event consumers safely across threads and ignores closed loops.
- [`Session.stop()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/session.py#L754) stops the observer, drains delivered events, and closes accesses. A later run replaces the observer and its per-run state.

## Command-Line Interface

### [Initialization](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/cli/init.py#L446)

```text
trail [PATH]
trail init [PATH]
python -m trail [PATH]
```

An interactive setup and console now implement the `trail` command.

- [`main()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/cli/__init__.py#L42) opens a project or runs setup for a new project. `trail init` runs setup separately, and `--nodir` starts an in-memory console.
- [`Picker`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/cli/init.py#L95) previews bundled marker sets and user-supplied files. Arrow keys choose a preset; a typed path selects a custom file. Escape selects no preset; interrupting setup writes nothing.
- Reinitialization replaces `.markers` while retaining the project ID, tracked assets, and event history.
- The package now includes marker preset files and depends on `pathspec` for pattern matching, with `prompt_toolkit>=3.0.52` for terminal input.

### [Console](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/cli/console.py#L36), [Commands](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/cli/commands.py#L470), and [Feed](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/cli/feed.py#L19)

```text
>>> console.commands
Commands (track, offtrail, mark, unmark, markers, assets, dirs, events, clear, help, restart, quit, exit)
>>> console.commands.find('ev')
EventsCommand('events')
```

The console combines an editable command bar with a stream of new events.

- [`Run`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/cli/console.py#L289) prints events recorded during the current run. Earlier history remains available through `events`. Its shutdown path also drains access events closed by the session.
- [`Feed`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/cli/feed.py#L19) prints ordinary terminal output, retaining scrollback and text selection. Its in-memory row history is bounded to 2,000 rows.
- [`Renderer`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/cli/render.py#L27) shows project-relative paths, event positions, resource IDs, and counts. The header reports active tracking and open dataset accesses.
- [`track`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/cli/commands.py#L59) and [`offtrail`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/cli/commands.py#L109) handle explicit resources. `mark` and `unmark` edit persistent discovery rules; `markers` lists their kinds and directories. Offtrail globs match tracked paths, including deleted resources.
- [`Listing`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/cli/command.py#L224) exposes selection expressions through `assets`, `dirs`, and `events`. Paths resolve against the project root; displayed positions remain those of the original collection. Without an explicit slice, listings show the last 20 matches.
- [`clear`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/cli/commands.py#L362) and collection-clear commands report their effect and require `-f` to proceed. Dirs must be removed through their marker rules.
- [`Commands.find()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/cli/commands.py#L520) accepts unambiguous prefixes. Tab completion covers commands, paths, tracked resources, marker extensions, and selection fields. `help` lists usage; [`restart`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/cli/commands.py#L427) reopens the console in a new process.
- [`MarkerCompleter`](/home/arstneio/PyCharmProjects/trail/src/trail/cli/marker_completion.py:50) provides shared cursor handling and rule classification for completion in `cli/marker_completion.py`. [`MarkerPaths`](/home/arstneio/PyCharmProjects/trail/src/trail/cli/marker_completion.py:83) completes paths while preserving a leading `!`; sibling completers suggest bundled extensions and existing marker lines.

## Compatibility

- `register()` and `unregister()` become `track()` and `offtrail()`.
- `trail.entries` is removed; assets and marker-defined Dirs have separate collections.
- `.by_pos[...]` becomes `.select[...]`, and collection iteration yields records instead of IDs.
- Explicit directory tracking returns one `DirAsset`; `.markers` controls recursive file discovery.
- Observer lifecycle calls move to `trail.watchdog.session`; `watchdog.context()` becomes `async with watchdog.session`.
- `AddEntryEvent` and `RemoveEntryEvent` become `AddAssetEvent` and `RemoveAssetEvent`, with separate events for marker-defined Dirs. Serialized fields `size` and `mtime` become `st_size` and `st_mtime`. No migration is provided for the previous log format: [`Event.from_record()`](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/src/trail/event.py#L109) rejects the former class names.

## Validation

The expanded test suite covers:

- [marker parsing, presets, persistence, and comments](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/tests/test_markers.py), [scope precedence and exclusions](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/tests/test_scopes.py), and [Dir identity and history](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/tests/test_dirs.py)
- [directory dataset formats, child matching, and reload](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/tests/test_dirassets.py), plus [access counts, timing, concurrency, moves, and shutdown](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/tests/test_accesses.py)
- [selection parsing, comparison types, slices, and collection copies](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/tests/test_select.py)
- [setup and preset previews](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/tests/test_init.py), [command listings and clearing](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/tests/test_listing.py), and [terminal input](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/tests/test_input.py)
- [observer session lifecycle and cross-thread delivery](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/tests/test_session.py), [recursive and parent watch placement](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/tests/test_watches.py), and [project persistence and stable identities](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/tests/test_trail.py)

The new [GitHub Actions workflow](https://github.com/geojupyter/prototype-trail/blob/c4fb319ae699afa5056aefc82e4b2e9a6ec2b56e/.github/workflows/test.yml#L1) runs pytest with Python 3.12 on Ubuntu, macOS, and Windows. Open-event tests account for the observer backend's platform support.

Local validation used the full working tree after the marker completion rename, including its pre-existing uncommitted edits, with Python 3.12.14 and pytest 9.1.1. Ruff checks passed for `marker_completion.py`, `commands.py`, `init.py`, and `test_scopes.py`.

```text
211 passed in 11.08s
```
