from __future__ import annotations

import asyncio
import os
import subprocess
import sys

import pytest

from trail import Trail


def test_watchdog_and_jupyter_share_the_lock(trail: Trail) -> None:
    other = Trail(trail.dir)

    async def run() -> None:
        assert trail.watchdog.session.start()
        assert trail.jupyter.session.start()
        assert trail.lock.owner == os.getpid()
        with pytest.warns(UserWarning, match='already recording'):
            assert not other.jupyter.session.start()
        assert not other.watchdog.session.start()
        await trail.jupyter.session.stop()
        assert trail.lock.fd is not None
        await trail.watchdog.session.stop()
        assert trail.lock.fd is None
        assert other.jupyter.session.start()
        await other.jupyter.session.stop()

    asyncio.run(run())


def test_another_process_cannot_take_the_lock(trail: Trail) -> None:
    holder = object()
    assert trail.lock.acquire(holder)
    script = '''
import sys
import warnings
from trail import Trail
trail = Trail(sys.argv[1])
with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    acquired = trail.lock.acquire('child')
trail.lock.release('child')
sys.exit(0 if acquired else 3)
'''
    try:
        result = subprocess.run(
            [sys.executable, '-c', script, str(trail.dir)],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 3, result.stderr
    finally:
        trail.lock.release(holder)
    result = subprocess.run(
        [sys.executable, '-c', script, str(trail.dir)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
