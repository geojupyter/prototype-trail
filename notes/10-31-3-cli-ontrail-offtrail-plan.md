# Plan: Refactor CLI `ontrail` and `offtrail` to Text-Only Rule Configuration

## Context & Motivation

The `.ontrail` and `.offtrail` files should not be modified within the CLI: they can only be text edits.
Remove `add` and `remove` (`rm`) actions for `ontrail` and `offtrail`. The user only uses `trail ontrail ...` to explicitly ontrail a pattern, and `trail offtrail ...` to explicitly offtrail a pattern. Neither command updates `.ontrail` or `.offtrail` text files.

---

## Current State & Backups

All working tree modifications before this change have been archived:
- Archive: `backup-codex-jupyter-base-events.tar.gz`
- Patch diff: `backup-codex-jupyter-base-events.patch`

---

## Architectural Changes

```text
Shell CLI:
  trail [PATH]                    -> Run interactive console (or init walkthrough if no Trail)
  trail init [PATH]               -> Setup walkthrough only
  trail ontrail [PATTERN...]      -> Explicitly track paths matching pattern(s); if none, list .ontrail rules
  trail offtrail [PATTERN...]     -> Explicitly untrack assets matching pattern(s); if none, list .offtrail rules

Interactive Console:
  ontrail                         -> List rules of .ontrail (read-only view)
  ontrail PATTERN...              -> Explicitly track paths now via trail.ontrail(*paths) (no file edits)
  offtrail                        -> List rules of .offtrail (read-only view)
  offtrail PATTERN...             -> Explicitly untrack paths now via trail.offtrail(*paths) (no file edits)

File Configuration:
  .ontrail                        -> User edits text file directly; file changes are picked up by watchdog/reload
  .offtrail                       -> User edits text file directly; file changes are picked up by watchdog/reload
```

---

## Detailed Implementation Steps

### 1. Refactor Console Commands (`src/trail/cli/commands.py`)

- **`RulesCommand`**:
  - Remove `actions = ("add", "remove")`.
  - Remove `def add(self, texts)` and `def remove(self, texts)`.
  - Remove `from trail.cli.rule_completion import completer` and the `completer` cached property.
  - Simplify `__call__`:
    ```python
    def __call__(self, arguments: Sequence[str]) -> None:
        if not arguments:
            self.show()
            return
        self.call(arguments)
    ```
  - In `complete`: delegate directly to `self.complete_paths(document, complete_event)` (no `add`/`remove` keyword completion).
  - In `show()`: update empty notice from `none; '{self.name} add' adds a rule` to `none; edit .{self.name} to add rules`.

- **`OnTrailCommand`**:
  - Remove `def add(self, texts)`.
  - Update `usage = "ontrail [PATH...]"`
  - Update `summary = "track paths now; list .ontrail rules"`
  - Update docstring.

- **`OffTrailCommand`**:
  - Update `usage = "offtrail [PATH...]"`
  - Update `summary = "stop tracking paths; list .offtrail rules"`
  - Update docstring.

- **`DirsCommand`**:
  - In `discard`: update rejection message from:
    `"dirs: a Dir comes from .ontrail, so 'ontrail remove' its rules to remove it"`
    to:
    `"dirs: a Dir comes from .ontrail, so edit .ontrail to remove its rule"`

- **`NOTES`**:
  - Remove references suggesting `ontrail add` / `remove` or `offtrail add`. Emphasize that rule files are edited as text files.

### 2. Add Shell Subcommands (`src/trail/cli/__init__.py`)

- Support `trail ontrail [PATTERNS...]` and `trail offtrail [PATTERNS...]` from bash / command-line.
- Resolve the project directory via `Trail.locate(Path.cwd())`.
- If no Trail project is found, print error to `stderr` and exit with code 2.
- Execute the command against `Trail(root)`:
  - If no patterns provided: print the rules table (same as `RulesCommand.show()`) and return 0.
  - If patterns provided:
    - `ontrail`: expand globs, track each path with `trail.ontrail(path)`, print confirmation, return 0.
    - `offtrail`: match patterns against tracked assets, untrack each with `trail.offtrail(path)`, print confirmation, return 0.

### 3. Update Tests (`tests/test_listing.py`)

- In `test_assets_and_dirs_list_their_own_collections`:
  Write rules directly into `.ontrail` text file:
  ```python
  (root / ".ontrail").write_text("./\ndata/*.csv\nfolder/*.csv\n", encoding="utf-8")
  console._trail.ontrail.txt.read()
  ```
  And update the error string check for `dirs clear -f`.
- Replace `TestOnTrail.test_add_tracks_now_and_writes_the_rule` and `test_remove_leaves_what_it_tracked` with:
  - Verifying `ontrail` with no arguments lists `.ontrail` rules.
  - Verifying `ontrail <pattern...>` tracks paths immediately without altering `.ontrail`.
- Replace `TestOffTrail.test_offtrail_lists_adds_and_removes_its_rules` with:
  - Verifying `offtrail` with no arguments lists `.offtrail` rules.
  - Verifying `offtrail <pattern...>` untracks paths immediately without altering `.offtrail`.
- Add tests for `trail ontrail` and `trail offtrail` command-line invocation via `main()`.

### 4. Verification

- All changes implemented and validated.
- `ruff check --select E4,E7,E9,F,I,UP` passes cleanly across `src/` and `tests/`.
- Full pytest suite passes: **287 passed, 1 warning in 13.92s**.
