from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from trail.trail import Trail

if TYPE_CHECKING:
    from IPython.core.interactiveshell import ExecutionResult, InteractiveShell

# the hook registered for each shell, so it can be unregistered
hooks: dict[int, object] = {}


def observe(trail: Trail) -> None:
    """
    Check the Trail's assets after a cell. With the watcher running in this process, each changed
    asset is read once the event loop resumes. Without it, each asset is checked now, while the
    project lock is held. Nothing is checked if another process records the project.
    """
    session = trail.watchdog.session
    if session.is_active:
        assets = list(trail.assets)
        # a stat check can read a format's files, as zarr's does
        with session.reads.own(assets):
            stale = [
                asset
                for asset in assets
                if asset.metadata.is_stale()
            ]
        for asset in stale:
            session.reads.add(asset, 'cell', is_immediate=True)
        return
    if not trail.lock.acquire(observe):
        return
    try:
        trail.assets.observe('cell')
    finally:
        trail.lock.release(observe)


def load_ipython_extension(shell: InteractiveShell) -> None:
    """Check the project's assets after each cell. Outside a Trail project nothing is registered."""
    if Trail.locate(Path.cwd()) is None:
        return
    trail = Trail(Path.cwd())

    def post_run_cell(result: ExecutionResult) -> None:
        observe(trail)

    shell.events.register('post_run_cell', post_run_cell)
    hooks[id(shell)] = post_run_cell


def unload_ipython_extension(shell: InteractiveShell) -> None:
    hook = hooks.pop(id(shell), None)
    if hook is not None:
        shell.events.unregister('post_run_cell', hook)
