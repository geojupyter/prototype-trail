from __future__ import annotations

from pathlib import Path

import pytest

from trail.session import WatchdogSession


@pytest.fixture(autouse=True)
def settled(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Tests stop the watchdog right after the events they waited for. Waiting for a move that watchdog
    holds back would only slow them. A test that needs the wait sets `settle` itself.
    """
    monkeypatch.setattr(WatchdogSession, 'settle', 0)


@pytest.fixture(autouse=True)
def jupyter_runtime(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('JUPYTER_RUNTIME_DIR', str(tmp_path / 'runtime'))
