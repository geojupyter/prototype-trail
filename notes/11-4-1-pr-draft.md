# Add interactive tracking, text-file rules, and directory dataset access logging

<!--
Local revision of 10-30-2-pr-draft.md, following the class-oriented format of
https://github.com/geojupyter/prototype-trail/pull/1 and all eight review comments.
The reviewed draft and its comments remain unchanged.

This describes the original tracking/CLI scope relative to main. The current triage branch
also contains later Jupyter, triage, check, and feed-filter work. Select the actual PR source
before publication and reconcile this description with its complete diff against the target.

Requested code changes remain pending: expose root scopes as trail.ontrail.scopes and remove
OnTrail/OffTrail.add/remove, Rules.replace, and editing-only machinery. Current root scopes
are still exposed as trail.scopes. This Markdown revision does not implement those requests.
The body documents the retained text-file workflow and marks the scopes proposal explicitly.
The CLI already has no add/remove rule-editing actions.

Reprs and console output were regenerated from the current working tree with Python 3.12.14.
The directory-access event is constructed from a representative fixture: one opened and 72
modified notifications over 242 ms. It is not a measurement of a real application run.
The console examples use dummy terminal input and synchronous command submission; their
tracking/untracking operations were checked to leave both rule files byte-for-byte unchanged.
Filesystem modification times and console day headings use America/Los_Angeles; event timestamps
are UTC. The console header is idle because these examples do not run the observer.

Before publication, implement or resolve the two API proposals, regenerate affected examples,
and rerun checks. The toy-project tutorial still needs API/prose updates before it can be linked
as a current end-to-end example. Repository links are relative and refer to current definitions;
convert them to verified commit-pinned URLs only for a requested PR-ready version.

Feedback coverage: 29 -> broad bullets and trail.select; 59 -> separate locate bullet;
65 -> one class per section; 90 -> scopes proposal under OnTrail; 99-100 and 117 -> text-file
configuration with Python editing removal recorded as pending; 166 -> separate Asset/DirAsset/
Assets sections and its own Assets.scan bullet; 171 -> no fingerprint implementation discussion.
-->

This PR extends Trail with automatic asset discovery, directory dataset tracking, and an interactive terminal.

- **Interactive terminal:** inspect tracked resources and their event history while filesystem observation runs.
- **Text-file rules:** configure automatic tracking in `.ontrail` and exclusions in `.offtrail`.
- **Directory datasets:** track stores such as Zarr as resources and summarize activity inside them into access events.
- **Shared selections:** use the [`trail.select`](../src/trail/select.py) expression language through Python collections and terminal listings.

The examples use a temporary project containing `new.csv`, `data/map.png`, `cube.zarr`, and an initially untracked `notes.md`. The directory-access example uses a constructed event with representative notification counts. The [directory dataset tests](../tests/test_dirassets.py) and [access tests](../tests/test_accesses.py) cover discovery and observation behavior.

## Core classes

### [Trail](../src/trail/trail.py#L71)

`Trail`, in `trail.trail`, is the project entry point. It exposes inclusion and exclusion rules, configured directories, tracked assets, and the event log.

```text
Trail
    id: 38ca41930e0445368c84ee5516a40a9b
    dir: /tmp/trail-pr-example-1dbbg0k7/.trail
    dirs: [
        /tmp/trail-pr-example-1dbbg0k7,
        /tmp/trail-pr-example-1dbbg0k7/data,
    ]
    assets: [
        /tmp/trail-pr-example-1dbbg0k7/cube.zarr,
        /tmp/trail-pr-example-1dbbg0k7/new.csv,
        /tmp/trail-pr-example-1dbbg0k7/data/map.png,
    ]
```

- [`Trail(path)`](../src/trail/trail.py#L244) opens the nearest project found from the supplied directory. A new project can start from an inclusion preset, such as `Trail(path, ontrail='geospatial')`.
- [`Trail.locate()`](../src/trail/trail.py#L300) searches the directory and its ancestors for `.trail`, stopping before directories listed in `TRAIL_CEILING_DIRECTORIES`.
- [`Trail.clear()`](../src/trail/trail.py#L370) untracks assets and discards the log. The project ID and rule files remain; configured directories are logged again with their existing IDs.

### [Collection](../src/trail/collection.py#L46)

`Collection`, in `trail.collection`, supplies ordered storage shared by event, asset, and directory collections.

- Iteration yields records. Membership accepts a record or its lookup key.
- [`Collection.select`](../src/trail/collection.py#L75) exposes positional and expression-based selection, described under `Select` below.
- [`Collection.get()`](../src/trail/collection.py#L110) supports a default for missing keys.

### [EntryCollection](../src/trail/entry.py#L113)

`EntryCollection`, in `trail.entry`, is the base used by the asset and configured-directory collections. It adds path lookup to ID lookup. Passing several keys returns a tuple of entries.

### [Select](../src/trail/select.py#L42)

`Select`, in `trail.select`, implements the selections reached through each collection's `.select`. The terminal uses the same parser and comparison rules.

```text
>>> trail.events.select('cls=AddAssetEvent rule=*.csv')
Events (1)
    0. AddAssetEvent
        id: 1f201596c7cd4529abd976dbe54ebf15
        timestamp: 06:12:54.110
        src_path: /tmp/trail-pr-example-1dbbg0k7/new.csv
        rule: *.csv
        st_size: 19 B
        st_mtime: 2026-11-04 22:12:54
```

- [`select[index]`](../src/trail/select.py#L87) supports positive and negative positions. A slice returns a selection of the same collection type.
- [`select(expression)`](../src/trail/select.py#L78) supports field comparisons, quoted values, parentheses, `not`, `and`, and `or`. Adjacent terms imply `and`.
- Slices act in expression order: `event_type=modified -5:` takes the last five matching events; `-5: event_type=modified` filters only the last five events.
- [`Select.compare()`](../src/trail/select.py#L220) converts comparison values to the field's type. Class comparisons include subclasses, record references compare by ID, and timestamps accept ISO datetimes or a UTC clock time for today.
- [`Select.subset()`](../src/trail/select.py#L199) copies membership while sharing records and their project parent. Clearing an event selection leaves the persisted log unchanged; clearing an asset selection untracks the selected assets from the project.

### [Rule](../src/trail/rule.py#L26)

`Rule`, in `trail.rule`, is an immutable string with the syntax operations used by both rule files.

- [`Rule.parse()`](../src/trail/rule.py#L29) separates a rule into its directory anchor and matching pattern.
- [`Rule.kind()`](../src/trail/rule.py#L130) describes it for rule listings, for example `root scope` or `dir scope`.
- [`Rule.validate()`](../src/trail/rule.py#L100) rejects invalid syntax.
- [`Rule.ambiguous_directory()`](../src/trail/rule.py#L164) identifies a directory written without its trailing `/` so diagnostics can explain how the rule will be read.

### [Rules](../src/trail/rule.py#L493)

`Rules`, in `trail.rule`, holds the ordered rules of one file. Its inclusion and exclusion subclasses share iteration, indexing, diagnostics, and preset lookup.

- [`Rules.available()`](../src/trail/rule.py#L518) lists the presets belonging to the subclass. Inclusion and exclusion presets stay separate.
- Rule files are edited directly as text, for example with Vim. Restart the console or reopen the project to apply external edits; automatic rule-file reload is not implemented.
- Changing discovery rules leaves already tracked assets in place. Explicit untracking removes them from active tracking.

### [TXT](../src/trail/rule.py#L332)

`TXT`, in `trail.rule`, is the file wrapper reached through a rule collection's `.txt`.

```text
TXT
    path: /tmp/trail-pr-example-1dbbg0k7/.ontrail
    tail:
        ./
        *.csv
        data/*.png
        *.zarr
```

- [`TXT.read()`](../src/trail/rule.py#L408) loads rules in file order, skipping comments and blank lines. It does not rewrite the file or itself rebuild project state.
- [`TXT.numbered()`](../src/trail/rule.py#L401) retains source line numbers for diagnostics.
- [`TXT.create()`](../src/trail/rule.py#L412) initializes a missing file from its owner's preset directory or a supplied file. This setup operation remains necessary with direct text editing.

### [Scopes](../src/trail/ontrail.py#L43)

`Scopes`, in `trail.ontrail`, holds inclusion rules beside their compiled patterns. Root scopes apply beneath configured roots; local scopes apply relative to one configured directory.

```text
Scopes (2)
    *.csv
    *.zarr
```

- [`Scopes.decide()`](../src/trail/ontrail.py#L128) returns the last matching rule. Matching ignores case; a leading `!` excludes the match.
- Local directory patterns address direct children unless they reach subfolders, such as with `**`. A root rule alone watches a directory without selecting all its files.

### [OnTrail](../src/trail/ontrail.py#L143)

`OnTrail`, in `trail.ontrail`, is reached as `trail.ontrail`. It holds `.ontrail`, the text file beside `.trail` that names directories to watch and files or datasets to discover.

```text
OnTrail (4)
    ./
    *.csv
    data/*.png
    *.zarr
```

- [`trail.ontrail(*paths)`](../src/trail/ontrail.py#L159) tracks paths immediately. A directory becomes one directory dataset. A single path returns its asset; multiple paths return a list. Neither rule file changes.
- Root rules such as `./` name directories to watch recursively. Root patterns such as `*.csv` apply at any depth beneath those roots; `data/*.png` applies relative to `data`.
- **Proposed API change:** expose root scopes as `trail.ontrail.scopes`, replacing `trail.scopes`. This move is requested by the review and remains unimplemented.
- [`OnTrail.warnings`](../src/trail/ontrail.py#L295) reports invalid rules, ambiguous directory syntax, and attempts to use directory datasets as scan roots.
- Bundled presets are [`default`](../src/trail/ontrail/default.txt), [`general`](../src/trail/ontrail/general.txt), [`geospatial`](../src/trail/ontrail/geospatial.txt), and [`ml`](../src/trail/ontrail/ml.txt). The default contains only `./`; the others also select extensions.

### [OffTrail](../src/trail/offtrail.py#L90)

`OffTrail`, in `trail.offtrail`, is reached as `trail.offtrail`. It holds `.offtrail`, whose rules exclude paths from automatic discovery before inclusion rules are considered.

```text
OffTrail (1)
    .*
```

- [`trail.offtrail(*paths)`](../src/trail/offtrail.py#L102) untracks assets and remembers their paths so later discovery, including after reopening, does not take them again. Neither rule file changes.
- [`OffTrail.default()`](../src/trail/offtrail.py#L135) initializes a missing `.offtrail` with [`.*`](../src/trail/offtrail/default.txt), excluding dotfiles and hidden folders. An empty file ignores nothing; a missing file receives the default again.
- [`OffTrail.decide()`](../src/trail/offtrail.py#L205) uses case-insensitive matching and the last matching rule. A negation such as `!.env` makes the path eligible for inclusion rules again.
- Explicit `trail.ontrail(path)` can track a path excluded by `.offtrail`. Trail metadata remains protected.

The review also proposes removing the Python rule-file editing methods from both rule collections and their shared base. Those removals are pending; the workflows described here use text edits and explicit path calls.

### [Dir](../src/trail/dir.py#L20)

`Dir`, in `trail.dir`, now represents a directory named by inclusion rules, with its root status and local scopes. Ordinary subfolders encountered by recursive observation do not acquire separate directory records.

```text
Dir
    id: 76af82f6291f4f389397ae14b69a74df
    path: /tmp/trail-pr-example-1dbbg0k7/data
    root: False
    scopes: [
        ./*.png,
    ]
```

- [`Dir.decide()`](../src/trail/dir.py#L81) evaluates its local scope patterns.
- Moving a configured directory repaths tracked descendant assets. The rule remains anchored at its configured path, and a warning identifies the moved directory.
- A directory tracked as one dataset is excluded from the configured-directory collection.

### [Dirs](../src/trail/dir.py#L186)

`Dirs`, in `trail.dir`, is reached as `trail.dirs`. It holds the configured directory records and the root scopes used during matching.

- [`Dirs.refresh()`](../src/trail/dir.py#L235) reconciles records with inclusion rules and records directory additions or removals. Surviving records retain their IDs across refreshes and reopening.
- [`Dirs.match()`](../src/trail/dir.py#L301) consults the deepest matching directory before its ancestors and the root scopes.
- [`Dirs.clear()`](../src/trail/dir.py#L230) refuses direct clearing: a configured directory goes when its rules leave `.ontrail`.

### [Asset](../src/trail/asset.py#L25)

`Asset`, in `trail.asset`, represents a tracked file. Its repr now includes its current size and modification time; a missing resource displays `<missing>`.

```text
Asset
    id: 2a0737e73af0421d8792abbcef140708
    path: /tmp/trail-pr-example-1dbbg0k7/new.csv
    st_size: 19 B
    st_mtime: 2026-11-04 22:12:54
```

- [`Asset.from_path()`](../src/trail/asset.py#L70) chooses a file asset or a directory dataset from the supplied path.

### [DirAsset](../src/trail/asset.py#L194)

`DirAsset`, in `trail.asset`, represents a directory tracked as one dataset. It shares the asset collection with files and retains one resource ID across dataset rewrites and moves.

```text
DirAsset
    id: 3678d4736f734c17b653762632dac855
    path: /tmp/trail-pr-example-1dbbg0k7/cube.zarr
    files: 2
    st_size: 5 B
    st_mtime: 2026-11-04 22:12:54
```

- [`DirAsset.is_match()`](../src/trail/asset.py#L219) recognizes `.zarr`, `.n5`, `.gdb`, and `.safe` directories during automatic discovery. Explicit tracking also accepts other directories as datasets.
- [`DirAsset.children`](../src/trail/asset.py#L251) chooses the format-specific filename patterns that distinguish dataset activity from locks, temporary files, and unrelated files. Other explicitly tracked directories include every child.
- [`DirAsset.access`](../src/trail/asset.py#L263) exposes the current `Access`, the object accumulating filesystem notifications for that dataset.

### [Assets](../src/trail/asset.py#L342)

`Assets`, in `trail.asset`, is reached as `trail.assets`. It holds tracked files and directory datasets, using the shared entry-collection lookup and selection behavior.

- [`Assets.add()`](../src/trail/asset.py#L363) accepts resolved paths mapped to their selecting rule, or `None` for explicit additions. It returns assets in request order, reuses existing assets without duplicate addition events, and reconciles directory records and watches.
- [`Assets.scan()`](../src/trail/asset.py#L445) discovers eligible paths. It can select a directory dataset itself and skips descending into its dataset contents.
- [`Assets.holder()`](../src/trail/asset.py#L521) identifies the dataset owning a path and prevents separate tracking of dataset children. Unrelated files within recognized dataset directories can still be tracked independently. Tracking a directory whole untracks already tracked children it absorbs and warns about them.

### [Entries](../src/trail/entries.py#L17)

`Entries`, in `trail.entries`, is reached as `trail.entries`. It replaces the earlier `EntryLookup` with a combined view over assets and configured directories, without maintaining a second collection of records.

- [`entries[key]`](../src/trail/entries.py#L37) looks up an ID or path, trying assets first. Several keys return a tuple; missing keys raise `KeyError`.
- [`Entries.get()`](../src/trail/entries.py#L70) returns a default for missing keys.
- Iteration yields configured directories followed by assets. Selections remain on the individual collections.
- [`Entries.untracked`](../src/trail/entries.py#L29) remembers explicitly untracked paths across log replay. Tracking a path again removes it from this set.
- [`Entries.is_metadata()`](../src/trail/entries.py#L112) protects `.trail` and its contents, `.ontrail`, and `.offtrail` from tracking and filesystem-event handling.
- [`Entries.is_trackable()`](../src/trail/entries.py#L131) applies discovery eligibility, exclusions, remembered untracking, and dataset ownership.
- [`Entries.is_walkable()`](../src/trail/entries.py#L163) prevents descending into symlinks, metadata, remembered untracked paths, and directory datasets. An excluded directory is traversed only when a configured directory at or below it requires access.

### [Access](../src/trail/session.py#L91)

`Access`, in `trail.session`, accumulates notifications for one directory dataset. Concurrent readers and writers share the same access.

- [`Access.add()`](../src/trail/session.py#L105) counts delivered `opened`, `created`, `modified`, `moved`, and `deleted` notifications and updates the first and last arrival times.
- [`Access.to_event()`](../src/trail/session.py#L118) produces an `opened` access when only opens occurred, or `modified` when other counted activity occurred. Empty accesses are discarded.
- The counts measure delivered filesystem notifications, not distinct files or application operations. Temporary-file renames into dataset children count as writes; ignored lock files do not turn a read into a write.

### [DirectoryAccessEvent](../src/trail/event.py#L475)

`DirectoryAccessEvent`, in `trail.event`, records a completed dataset access as one synthetic event. The representative fixture below contains one open and 72 modification notifications over 242 ms.

```text
>>> trail.events.select('cls=DirectoryAccessEvent')
Events (1)
    0. DirectoryAccessEvent
        id: 1a145093167f4b13b04277fe5bd8fd01
        timestamp: 06:12:54.111
        src_path: /tmp/trail-pr-example-1dbbg0k7/cube.zarr
        event_type: modified
        started: 06:12:53.869
        count: opened 1, modified 72
```

- `started` is the first notification's arrival time; `timestamp` is the last.
- Start time and operation counts are serialized for replay.
- Accesses close before moves, deletion, or untracking so their resource and path remain associated with the preceding activity.

### [Watchdog](../src/trail/watchdog.py#L14)

`Watchdog`, in `trail.watchdog`, is reached as `trail.watchdog`. Watch placement is now separate from the observer run; synchronous inspection shows the watches a later run would place.

```text
Watchdog
    running: False
    watches: [
        /tmp,
        /tmp/trail-pr-example-1dbbg0k7,
    ]
```

- [`Watchdog.desired()`](../src/trail/watchdog.py#L81) requests recursive watches for configured directories and directory datasets, plus parent watches for moves and deletion. Watches already covered by recursive ancestors are omitted.

### [WatchdogSession](../src/trail/session.py#L557)

`WatchdogSession`, in `trail.session`, is reached as `trail.watchdog.session`. It owns each run's observer, watches, queue, handler, and consumer task.

- [`WatchdogSession.start()`](../src/trail/session.py#L652) starts observation on a running asyncio loop. Synchronous use remains dormant until a loop starts a session.
- [`async with trail.watchdog.session`](../src/trail/session.py#L740) manages a run explicitly.
- [`WatchdogSession.reconcile()`](../src/trail/session.py#L693) updates watch placement and publishes rule snapshots for the observer thread.
- Idle dataset accesses close after the configured gap, 30 seconds by default.
- Missing-watch failures warn without stopping unrelated watches.
- [`WatchdogSession.stop()`](../src/trail/session.py#L759) stops observation, drains delivered events, and closes remaining accesses. A later run creates a fresh observer and per-run state.
- Clearing the live event log drops pending accesses so they cannot repopulate the discarded history.

### [Handler](../src/trail/session.py#L318)

`Handler`, in `trail.session`, receives observer notifications on the watchdog thread. It timestamps arrivals, counts dataset accesses, and queues other events for the asyncio consumer.

- [`Handler.handle()`](../src/trail/session.py#L335) uses the session's published rule and watch snapshots instead of reading live Trail collections on the observer thread.
- Excluded directories are skipped unless an explicitly covered path requires them.

## Command-line interface

The terminal combines setup, a live event feed, and commands for inspecting or changing active tracking. Rule configuration stays in the two text files.

### [Initialization](../src/trail/cli/init.py#L443)

[`main()`](../src/trail/cli/__init__.py) opens the console, running setup first when the directory has no project. Setup can also run alone:

```text
trail [PATH]
trail init [PATH]
python -m trail [PATH]
```

- `--nodir` skips setup and holds events in memory.
- Setup writes the selected `.ontrail` and initializes a missing `.offtrail`, then discovers eligible assets.
- Reinitialization replaces `.ontrail` while retaining `.offtrail`, the project ID, tracked assets, and event history.
- Package data includes both preset directories. Pattern matching uses `pathspec`, and terminal input uses `prompt_toolkit>=3.0.52`.

### [Picker](../src/trail/cli/init.py#L92)

`Picker`, in `trail.cli.init`, previews bundled presets and supplied rule files during initialization.

- Arrow keys browse presets and Enter chooses one. A typed path previews a file or the `.ontrail` inside a directory.
- Escape starts with no inclusion rules. Interrupting setup writes nothing.

### [Console](../src/trail/cli/console.py#L38)

`Console`, in `trail.cli.console`, holds the editable command bar, command dispatch, and rendering objects.

```text
🪵🏔️🍁 Welcome back to Trail! 🍁🏔️🪵

trail #38ca41930e0445368c84ee5516a40a9b on /tmp/trail-pr-example-1dbbg0k7
assets 3  dirs 2  events 6
type 'help' for help
```

The header keeps project, asset, directory, event, and open-access counts current while a session runs. These synchronous examples show the console idle.

### [Run](../src/trail/cli/console.py#L315)

`Run`, in `trail.cli.console`, is reached as `console.run`. It prints the banner and events recorded during the run. Earlier history remains available through `events`, and shutdown drains access events closed by the session.

### [Feed](../src/trail/cli/feed.py#L20)

`Feed`, in `trail.cli.feed`, is reached as `console.feed`. It prints ordinary terminal output, retaining native scrollback and text selection, with an in-memory history bounded to 2,000 rows.

- [`Feed.write()`](../src/trail/cli/feed.py#L39) prints events with their collection positions and a date row when the day changes.
- [`Feed.clear()`](../src/trail/cli/feed.py#L123) discards retained rows and the current date. Clearing the terminal does not clear tracked resources or event history.

### [Renderer](../src/trail/cli/render.py#L28)

`Renderer`, in `trail.cli.render`, is reached as `console.renderer`. It turns resources and events into feed rows with project-relative paths, collection positions, resource IDs, and counts.

- [`Renderer.header()`](../src/trail/cli/render.py#L184) reports counts, open dataset accesses, and whether observation is active.
- [`Renderer.verb()`](../src/trail/cli/render.py#L257) chooses the event verb used to color each heading: tracking, untracking, directory watching, or a filesystem operation.

### [RulesCommand](../src/trail/cli/commands.py#L66)

`RulesCommand`, in `trail.cli.commands`, is the base of the inclusion and exclusion commands. With no arguments, each lists its rule file; paths change active tracking.

- [`RulesCommand.show()`](../src/trail/cli/commands.py#L101) prints each rule's kind and owning directory, then diagnostics.
- Rule-file edits use the user's text editor. These commands have no `add` or `remove` actions.

### [OnTrailCommand](../src/trail/cli/commands.py#L144)

`OnTrailCommand`, in `trail.cli.commands`, implements `ontrail`: list inclusion rules or track supplied paths immediately.

```text
> ontrail
  ./          root                        ./
  *.csv       root scope                  every root
  data/*.png  dir scope · not subfolders  data/
  *.zarr      root scope                  every root
> ontrail notes.md
Wednesday 04 November 2026
6. AddAssetEvent
    id: 79354092aca24796a021283fc9b071b4
    timestamp: 06:12:54.131
    src_path: notes.md
    st_size: 6 B
    st_mtime: 2026-11-04 22:12:54
    entry: 9a25d4f95a844164821f8d26dcf536ca
```

- Path patterns expand against the filesystem. Tracking a directory adds one dataset.
- Tab completes filesystem paths. Explicit path operations leave `.ontrail` and `.offtrail` unchanged.
- The shell form, `trail ontrail [PATH...]`, performs the same kind of operation without opening the console.

### [OffTrailCommand](../src/trail/cli/commands.py#L201)

`OffTrailCommand`, in `trail.cli.commands`, implements `offtrail`: list exclusion rules or untrack supplied paths.

```text
> offtrail
  .*  root scope  every root
> offtrail notes.md
7. RemoveAssetEvent
    id: ea1f6350eb7448f6bcc8b4172b552124
    timestamp: 06:12:54.133
    src_path: notes.md
    entry: 9a25d4f95a844164821f8d26dcf536ca
```

- Globs match tracked paths, including resources already deleted from disk. Tab completes tracked paths.
- Each removal records an event and keeps the path out of later automatic discovery.
- The shell form is `trail offtrail [PATH...]`.

### [Query](../src/trail/cli/command.py#L188)

`Query`, in `trail.cli.command`, adapts `Select` for terminal commands. Relative path comparisons resolve against the project root, so `src_path=new.csv` matches an absolute stored path. Numeric fields compare numerically.

### [Listing](../src/trail/cli/command.py#L225)

`Listing`, in `trail.cli.command`, is the base of resource and event listings. It applies `Query` to the named collection.

- Without a slice, a listing shows the last 20 matches. Its header reports the displayed count against the whole collection, and records keep their positions in the original collection.
- Tab completes field names as `field=` terms.
- `clear` previews clearing the whole named collection; `-f` confirms it. It does not reuse a previous listing's filter.

### [AssetsCommand](../src/trail/cli/commands.py#L235)

`AssetsCommand`, in `trail.cli.commands`, lists tracked resources. Here the 5-byte dataset is excluded by a numeric size comparison:

```text
> assets st_size>5
assets (2 of 3)
1. Asset
    id: 2a0737e73af0421d8792abbcef140708
    path: new.csv
    st_size: 19 B
    st_mtime: 2026-11-04 22:12:54
2. Asset
    id: 0e6996dc54324106886287e0f2cda49c
    path: data/map.png
    st_size: 8 B
    st_mtime: 2026-11-04 22:12:54
```

`assets clear -f` untracks all assets and records their removals.

### [DirsCommand](../src/trail/cli/commands.py#L248)

`DirsCommand`, in `trail.cli.commands`, lists configured directories, including their root status and local scopes. It refuses `dirs clear`: remove their rules from `.ontrail` and reopen the project instead.

### [EventsCommand](../src/trail/cli/commands.py#L281)

`EventsCommand`, in `trail.cli.commands`, lists recorded history:

```text
> events src_path=new.csv
events (1 of 6)
3. AddAssetEvent
    id: 1f201596c7cd4529abd976dbe54ebf15
    timestamp: 06:12:54.110
    src_path: new.csv
    rule: *.csv
    st_size: 19 B
    st_mtime: 2026-11-04 22:12:54
    entry: 2a0737e73af0421d8792abbcef140708
```

`events clear -f` discards the log and records the configured directories again. Tracked assets remain active during the session; reopening reconstructs assets from the remaining log and current discovery rules.

### [Commands](../src/trail/cli/commands.py#L463)

`Commands`, in `trail.cli.commands`, is reached as `console.commands`. Named command subclasses register themselves so dispatch, help, and completion use the same table.

```text
>>> console.commands.find('ev')
EventsCommand('events')
```

- [`Commands.find()`](../src/trail/cli/commands.py#L513) accepts exact names and unambiguous prefixes.
- `help` lists commands, key bindings, path-versus-rule behavior, selections, and clearing.
- `clear -f` untracks assets and empties the project log. Ctrl-L clears terminal output.
- `restart` reopens the console in a new process, picking up source and rule-file edits. With `--nodir`, it requires `-f` because the in-memory log would be lost.

## Compatibility

- `register()` and `unregister()` become `trail.ontrail()` and `trail.offtrail()`.
- Combined entry lookup remains available by ID or path, but `trail.entries` is no longer a `Mapping`. Iteration yields directory records followed by assets; `ids`, `items()`, `by_pos`, and `clear()` are removed from this combined view.
- `.by_pos[...]` becomes `.select[...]`; collection iteration yields records instead of IDs.
- Explicit directory tracking returns one directory dataset. Recursive discovery follows `.ontrail`; configured directory records come only from those rules.
- Observer lifecycle operations move to `trail.watchdog.session`; `watchdog.context()` becomes `async with trail.watchdog.session`.
- `AddEntryEvent` and `RemoveEntryEvent` become `AddAssetEvent` and `RemoveAssetEvent`, with separate events for configured directories. Serialized `size` and `mtime` fields become `st_size` and `st_mtime`. No migration is supplied for the previous event class names.
- Pending review changes are the move from `trail.scopes` to `trail.ontrail.scopes` and removal of the Python rule-file editing APIs. The explicit path-call APIs remain.

## Validation

Coverage relevant to this PR includes:

- [rule syntax and preset ownership](../tests/test_rule.py), [inclusion files and presets](../tests/test_ontrail.py), [scope precedence](../tests/test_scopes.py), and [exclusion rules](../tests/test_offtrail.py)
- [configured-directory identity and history](../tests/test_dirs.py), [directory datasets](../tests/test_dirassets.py), and [access counts, timing, concurrency, moves, and shutdown](../tests/test_accesses.py)
- [combined lookups, metadata protection, and remembered untracking](../tests/test_entries.py), [selection parsing and comparison types](../tests/test_select.py), and [persistence](../tests/test_trail.py)
- [setup and preset previews](../tests/test_init.py), [listings and path commands](../tests/test_listing.py), [terminal input](../tests/test_input.py), and [welcome rendering](../tests/test_welcome.py)
- [observer lifecycle and cross-thread snapshots](../tests/test_session.py), and [recursive and parent watches](../tests/test_watches.py)

The [test workflow](../.github/workflows/test.yml) runs pytest with Python 3.12 on Ubuntu, macOS, and Windows. Open-event coverage accounts for backend support.

Local validation used Python 3.12.14 and pytest 9.1.1. `ruff check src tests` passed. The new draft's relative links, definition anchors, example output, and whitespace were checked. The result below covers the current working tree, including later features outside this description; the proposed API cleanup remains unimplemented.

```text
364 passed, 1 warning in 31.06s
```

The warning came from the existing recording-lock check in `test_discovered_assets_survive_reload`.
