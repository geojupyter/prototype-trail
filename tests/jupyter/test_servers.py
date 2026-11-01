from __future__ import annotations

import asyncio
import json
import os
import pickle
from pathlib import Path

import pytest

from trail import Trail
from trail.jupyter.server import JSON, JSONs, Secret, normalize_url


def runtime(
        directory: Path,
        root: Path,
        *,
        token: str = 'runtime-secret',
        url: str = 'http://localhost:8888/base/',
        pid: int | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    if pid is None:
        pid = os.getpid()
    path = directory / f'jpserver-{pid}.json'
    data = {
        'url': url,
        'root_dir': str(root),
        'token': token,
        'pid': pid,
    }
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


def test_runtime_refresh_replaces_changed_details_and_skips_partial_files(
        trail: Trail,
        tmp_path: Path,
) -> None:
    files = JSONs(tmp_path / 'runtime')
    path = runtime(files.directory, tmp_path)
    (files.directory / 'jpserver-0.json').write_text('{', encoding='utf-8')
    files.refresh()
    assert len(files) == 1
    assert files[path].is_running
    assert files[path].token.value == 'runtime-secret'
    assert 'runtime-secret' not in repr(files)
    runtime(files.directory, tmp_path, token='new-secret')
    files.refresh()
    assert files[path].token.value == 'new-secret'
    path.unlink()
    files.refresh()
    assert not files


def test_stale_process_and_deleted_file_stop_a_server(
        trail: Trail,
        tmp_path: Path,
) -> None:
    files = trail.jupyter.jsons
    path = runtime(files.directory, tmp_path, pid=999999999)
    servers = trail.jupyter.session.servers
    servers.refresh()
    server = next(iter(servers.values()))
    assert server.state == 'stopped'
    path.unlink()
    servers.refresh()
    assert server.is_stopped


def test_runtime_restart_on_same_url_replaces_credentials(
        trail: Trail,
        tmp_path: Path,
) -> None:
    files = trail.jupyter.jsons
    runtime(files.directory, tmp_path)
    servers = trail.jupyter.session.servers
    servers.refresh()
    first = next(iter(servers.values()))
    runtime(files.directory, tmp_path, token='replacement-secret')
    servers.refresh()
    second = next(iter(servers.values()))
    assert second is not first
    assert first.state == 'stopped'
    assert second.headers() == {'Authorization': 'token replacement-secret'}
    assert 'replacement-secret' not in repr(servers)
    assert 'replacement-secret' not in second.endpoint


def test_stale_runtime_file_cannot_hide_a_live_server(
        trail: Trail,
        tmp_path: Path,
) -> None:
    files = trail.jupyter.jsons
    runtime(files.directory, tmp_path)
    runtime(files.directory, tmp_path, token='stale', pid=999999999)
    servers = trail.jupyter.session.servers
    servers.refresh()
    server = next(iter(servers.values()))
    assert server.is_running
    assert server.json.pid == os.getpid()
    assert server.json.token.value == 'runtime-secret'


def test_project_overlap_and_map_scope(
        trail: Trail,
        tmp_path: Path,
) -> None:
    jupyter = trail.jupyter
    assert jupyter.overlaps(tmp_path)
    assert jupyter.overlaps(tmp_path.parent)
    assert jupyter.overlaps(tmp_path / 'nested')
    assert not jupyter.overlaps(tmp_path.parent / 'unrelated')
    assert jupyter.is_inside(tmp_path)
    assert jupyter.is_inside(tmp_path / 'nested')
    assert not jupyter.is_inside(tmp_path.parent)


def test_secrets_and_base_urls() -> None:
    secret = Secret('sensitive-value')
    assert repr(secret) == '<secret>'
    assert str(secret) == '<secret>'
    with pytest.raises(TypeError, match='cannot be pickled'):
        pickle.dumps(secret)
    assert normalize_url('https://localhost:8888/user/a') == 'https://localhost:8888/user/a/'
    for url in ('http://x/?token=secret', 'http://user:secret@x/', 'ftp://x/', 'http://x/#secret'):
        with pytest.raises(ValueError):
            normalize_url(url)
    info = JSON(url='http://localhost/', root=Path('/tmp'), token=secret)
    assert 'sensitive-value' not in repr(info)


def test_session_is_explicit_and_deletion_makes_a_fresh_run(trail: Trail) -> None:
    jupyter = trail.jupyter
    session = jupyter.session
    assert not session.is_active
    assert not session.start()

    async def run() -> None:
        assert session.start()
        assert session.start()
        assert session.is_active
        task = session.task
        del jupyter.session
        assert jupyter.session is not session
        await asyncio.gather(task, return_exceptions=True)
        assert not session.is_active
        async with jupyter.session:
            assert jupyter.session.is_active
        assert not jupyter.session.is_active
        assert trail.lock.fd is None

    asyncio.run(run())
