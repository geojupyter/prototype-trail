from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from trail.cli.console import Console
from trail.cli.init import init
from trail.trail import Trail

__all__ = ["Console", "main", "parse", "relaunch"]

# the one subcommand; anything else in its place is the project directory
INIT = "init"


def parse(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the command line. `main` removes a leading `init` before calling this."""
    parser = argparse.ArgumentParser(
        prog="trail",
        description="Watch a project directory and record what happens to its resources.",
        epilog="'trail init [PATH]' walks through (re)initializing a project.",
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
    initializing = (
        bool(argv)
        and argv[0] == INIT
    )
    if initializing:
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
    console = Console(trail, root)
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
    Replace this process with a new interpreter running the same command line, so the console
    reloads all of its code.

    `sys.orig_argv` holds the full original command line, including the interpreter and its
    flags. This lets `python -m trail` and the `trail` script restart the same way. The first
    token is looked up on PATH, as the shell did.
    """
    sys.stdout.flush()
    sys.stderr.flush()
    os.execvp(sys.orig_argv[0], sys.orig_argv)
