from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from trail import Trail
from trail import ipython as extension
from trail.event import MetadataEvent


def test_a_cell_that_changes_a_project_logs_its_rows(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell_module = pytest.importorskip('IPython.core.interactiveshell')
    root = tmp_path.resolve()
    (root / '.ontrail').write_text('./\n*.jgis\n', encoding='utf-8')
    project = {'layers': {}, 'sources': {}, 'layerTree': [], 'options': {'projection': 'EPSG:3857'}}
    (root / 'map.jGIS').write_text(json.dumps(project), encoding='utf-8')
    Trail(root)
    monkeypatch.chdir(root)
    shell = shell_module.InteractiveShell.instance()
    try:
        extension.load_ipython_extension(shell)
        # the first cell reads the baselines
        shell.run_cell('x = 1')
        changed = {**project, 'options': {'projection': 'EPSG:4326'}}
        code = (
            'import json, time\n'
            'time.sleep(0.02)\n'
            f"open('map.jGIS', 'w').write(json.dumps({changed!r}))\n"
        )
        shell.run_cell(code)
    finally:
        extension.unload_ipython_extension(shell)
        shell_module.InteractiveShell.clear_instance()
    reopened = Trail(root)
    [event] = [
        event
        for event in reopened.events
        if isinstance(event, MetadataEvent)
    ]
    assert event.basis == 'cell'
    assert event.format == 'jgis'
    assert [change.path for change in event.changes] == [('projection',)]
    assert not extension.hooks


def test_outside_a_project_nothing_is_registered(
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    shell_module = pytest.importorskip('IPython.core.interactiveshell')
    monkeypatch.setenv('TRAIL_CEILING_DIRECTORIES', str(tmp_path))
    monkeypatch.chdir(tmp_path)
    shell = shell_module.InteractiveShell.instance()
    try:
        extension.load_ipython_extension(shell)
        assert id(shell) not in extension.hooks
        assert not os.path.exists(tmp_path / '.trail')
    finally:
        shell_module.InteractiveShell.clear_instance()
