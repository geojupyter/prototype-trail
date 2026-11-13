from __future__ import annotations

from pathlib import Path

import pytest

from trail.session import WatchdogSession
from trail.trail import Trail


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line('markers', 'init_record: keep the initialized record of a new project')


@pytest.fixture(autouse=True)
def init_record(
        request: pytest.FixtureRequest,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A new project logs an initialized record first. Tests that count positions in a new project's
    log predate it, so it is left out unless a test is marked `init_record`.
    """
    if request.node.get_closest_marker('init_record') is None:
        monkeypatch.setattr(Trail, '_log_init', lambda self: None)


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
