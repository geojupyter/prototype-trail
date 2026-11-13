from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from collections.abc import Sequence
from glob import iglob
from pathlib import Path
from typing import NoReturn

from pathspec import PathSpec

from trail.cli.console import Console
from trail.cli.init import init
from trail.lock import Lock
from trail.rule import Rule, Rules
from trail.trail import Trail

__all__ = ["Console", "main", "parse", "relaunch"]

INIT = "init"
ONTRAIL = "ontrail"
OFFTRAIL = "offtrail"
TRIAGE = "triage"
MAGIC = re.compile(r"[*?\[]")


def print_rules(
        rules: Rules,
        project: Path,
) -> None:
    rows = []
    for rule in rules:
        try:
            base, _ = Rule(rule).parse(project)
        except ValueError:
            owner = ""
        else:
            if base is None:
                owner = "every root"
            elif base == project:
                owner = "./"
            else:
                try:
                    owner = str(base.relative_to(project)) + "/"
                except ValueError:
                    owner = str(base) + "/"
        rows.append((rule, Rule(rule).kind(project), owner))
    if not rows:
        print(f"{rules.filename}: none; edit {rules.filename} to add rules")
    else:
        width = max(
            (
                len(rule)
                for rule, _, _ in rows
            ),
            default=0,
        )
        kinds = max(
            (
                len(described)
                for _, described, _ in rows
            ),
            default=0,
        )
        for rule, described, owner in rows:
            print(f"  {rule:<{width}}  {described:<{kinds}}  {owner}")
    for warning in rules.warnings:
        print(f"warning: {warning}", file=sys.stderr)


def run_subcommand(
        command: str,
        patterns: Sequence[str],
) -> int:
    located = Trail.locate(Path.cwd())
    if located is None:
        print("trail: not in a Trail project (or any parent directory)", file=sys.stderr)
        return 2
    root = located.parent
    trail = Trail(root)
    if not patterns:
        if command == ONTRAIL:
            print_rules(trail.ontrail, root)
        else:
            print_rules(trail.offtrail, root)
        return 0

    if command == ONTRAIL:
        for pattern in patterns:
            expanded = Path(pattern).expanduser()
            if not MAGIC.search(pattern):
                matches = [(root / expanded).resolve()]
            else:
                found = iglob(
                    str(expanded),
                    root_dir=root,
                    recursive=True,
                )
                matches = sorted(
                    (root / Path(match)).resolve()
                    for match in found
                )
            if not matches:
                print(f"no matches: {pattern}", file=sys.stderr)
            for path in matches:
                if path in trail.assets:
                    if path.is_relative_to(root):
                        rel_path = path.relative_to(root)
                    else:
                        rel_path = path
                    print(f"already tracked: {rel_path}")
                    continue
                try:
                    trail.ontrail(path)
                    if path.is_relative_to(root):
                        display = path.relative_to(root)
                    else:
                        display = path
                    print(f"tracked: {display}")
                except (OSError, ValueError) as error:
                    if path.is_relative_to(root):
                        display = path.relative_to(root)
                    else:
                        display = path
                    print(f"error: {display}: {error}", file=sys.stderr)
    else:
        for pattern in patterns:
            expanded = Path(pattern).expanduser()
            if not MAGIC.search(pattern):
                target = (root / expanded).resolve()
                matches = [target]
            else:
                if not expanded.is_absolute():
                    abs_pat = (root / expanded).resolve()
                else:
                    abs_pat = expanded
                spec = PathSpec.from_lines("gitignore", [abs_pat.as_posix()])
                matches = sorted(
                    asset.path
                    for asset in trail.assets
                    if spec.match_file(asset.path.as_posix())
                )
            if not matches:
                print(f"no matches: {pattern}", file=sys.stderr)
            for path in matches:
                if path not in trail.assets:
                    if path.is_relative_to(root):
                        display = path.relative_to(root)
                    else:
                        display = path
                    print(f"not tracked: {display}", file=sys.stderr)
                    continue
                trail.offtrail(path)
                if path.is_relative_to(root):
                    display = path.relative_to(root)
                else:
                    display = path
                print(f"untracked: {display}")
    return 0


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line. `main` removes a leading `init` before calling this."""
    parser = argparse.ArgumentParser(
        prog="trail",
        description="Watch a project directory and record what happens to its resources.",
        epilog=(
            "'trail init [PATH]' walks through (re)initializing a project. "
            "'trail triage [PATH]' reviews pending events."
        ),
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="project directory to open; the working directory by default",
    )
    parser.add_argument(
        "--nodir",
        action="store_true",
        help="keep the log in memory instead of serializing it to PATH/.trail",
    )
    return parser.parse_args(argv)


def run_triage(
    root: Path,
    nodir: bool = False,
) -> int:
    lock = None
    holder = object()
    if not nodir:
        directory = Trail.locate(root)
        if directory is None:
            print('trail: no Trail project; run trail init first', file=sys.stderr)
            return 2
        lock = Lock(path=directory / 'lock')
        if not lock.acquire(holder):
            return 2
    try:
        if nodir:
            trail = Trail()
        else:
            trail = Trail(directory)
            trail.lock = lock
        console = Console(trail, root)
        asyncio.run(console.run(triage=True))
    except KeyboardInterrupt:
        return 130
    finally:
        if lock is not None:
            lock.release(holder)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """
    The `trail` entry point. Returns the exit code.

    `trail init` runs the setup walkthrough and exits. Opening a directory that has no Trail
    runs the walkthrough first, then opens the console. `--nodir` skips the walkthrough and
    keeps events in memory only.
    """
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)
    if bool(argv) and argv[0] in (ONTRAIL, OFFTRAIL):
        return run_subcommand(argv[0], argv[1:])
    initializing = (
        bool(argv)
        and argv[0] == INIT
    )
    triaging = bool(argv) and argv[0] == TRIAGE
    if initializing or triaging:
        argv = argv[1:]
    arguments = parse(argv)
    if (
        initializing
        and arguments.nodir
    ):
        print("trail: --nodir leaves nothing to initialize", file=sys.stderr)
        return 2
    if arguments.path is None:
        root = Path.cwd()
    else:
        root = Path(arguments.path).expanduser().resolve()
    # `trail ./.trail` names a project's metadata, not a project of its own
    if root.name == ".trail":
        root = root.parent
    if not root.is_dir():
        print(f"trail: not a directory: {root}", file=sys.stderr)
        return 2
    if not sys.stdin.isatty():
        print("trail: the console needs an interactive terminal", file=sys.stderr)
        return 2
    if triaging:
        out = run_triage(root, arguments.nodir)
        return out
    returning = False
    if arguments.nodir:
        trail = Trail()
    elif (
        initializing
        or Trail.locate(root) is None
    ):
        try:
            trail = init(root)
        except (KeyboardInterrupt, EOFError):
            print("trail: init aborted; nothing was written", file=sys.stderr)
            return 130
        if initializing:
            return 0
    else:
        trail = Trail(root)
        returning = True
    console = Console(trail, root, returning)
    try:
        asyncio.run(console.run())
    except KeyboardInterrupt:
        return 130
    if console.restarting:
        relaunch()
    if trail.dir is None:
        print(f"trail #{trail.id}: {len(trail.events)} events, not recorded")
    else:
        print(f"trail #{trail.id}: {len(trail.events)} events in {trail.dir}")
    return 0


def relaunch() -> NoReturn:
    """
    Replace this process with a new interpreter running the same command line. The console then
    reloads all of its code.

    `sys.orig_argv` holds the full original command line, including the interpreter and its flags.
    It restarts `python -m trail` and the `trail` script the same way. The first token is looked up
    on PATH, as the shell did.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os.execvp(sys.orig_argv[0], sys.orig_argv)
