from __future__ import annotations

import pytest

from trail.session import Session


@pytest.fixture(autouse=True)
def settled(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Tests stop the watchdog right after the events they waited for. Waiting for a move that watchdog
    holds back would only slow them. A test that needs the wait sets `settle` itself.
    """
    monkeypatch.setattr(Session, 'settle', 0)
