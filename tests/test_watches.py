from __future__ import annotations

import asyncio
import warnings
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from watchdog.events import DirCreatedEvent, FileCreatedEvent, FileModifiedEvent

from trail import Trail


class StubLoop:
    """Stands in for the loop a session wakes."""

    def call_soon_threadsafe(
            self,
            callback: Callable[..., object],
            *args: object,
    ) -> None:
        pass


class TestWatches:
    """Which directories a session watches, and which events its handler passes on."""

    @staticmethod
    @contextmanager
    def workspace(*markers: str) -> Iterator[Path]:
        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            text = ''.join(
                f'{marker}\n'
                for marker in markers
            )
            (root / '.markers').write_text(text, encoding='utf-8')
            (root / 'data' / 'raw').mkdir(parents=True)
            yield root

    @staticmethod
    @asynccontextmanager
    async def watching() -> AsyncIterator[Callable[..., Trail]]:
        """Opens Trails that react at once, and stops every one of them on the way out."""
        trails: list[Trail] = []

        def open(*args: object, **kwargs: object) -> Trail:
            trail = Trail(*args, **kwargs)
            trail.watchdog.session.debounce = 0
            trails.append(trail)
            return trail

        try:
            yield open
        finally:
            for trail in trails:
                await trail.watchdog.session.stop()

    @staticmethod
    async def until(predicate: Callable[[], bool]) -> None:
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(0.01)

    def test_a_dir_is_watched_recursively(self) -> None:
        with self.workspace('./', '*.csv') as root:
            trail = Trail(root)
            assert trail.watchdog.desired()[root] is True

            async def run() -> None:
                async with self.watching() as open:
                    trail = open(root)
                    assert trail.watchdog.session.watches[root] is True
                    deep = root / 'data' / 'raw' / 'deep.csv'
                    deep.write_text('a\n', encoding='utf-8')
                    await self.until(lambda: deep in trail.assets)

            asyncio.run(run())

    def test_a_folder_inside_a_recursive_watch_gets_no_watch(self) -> None:
        with self.workspace('./', 'data/', 'data/raw/*.tif') as root:
            trail = Trail(root)
            assert list(trail.dirs.path2entry) == [root, root / 'data', root / 'data' / 'raw']
            assert trail.watchdog.desired() == {root: True, root.parent: False}
            assert trail.watchdog.covers(root / 'data' / 'raw')

            async def run() -> None:
                async with self.watching() as open:
                    trail = open(root)
                    assert dict(trail.watchdog.session.watches) == {root: True, root.parent: False}

            asyncio.run(run())

    def test_a_parent_is_watched_alone(self) -> None:
        with self.workspace('data/') as root, TemporaryDirectory() as elsewhere:
            outside = Path(elsewhere).resolve()
            (outside / 'far.csv').write_text('a\n', encoding='utf-8')
            trail = Trail(root)
            trail.track(outside / 'far.csv')
            # a Dir's parent is watched to see the Dir renamed, and an asset's to see the asset
            assert trail.watchdog.desired() == {
                root / 'data': True,
                root: False,
                outside: False,
            }
            assert not trail.watchdog.covers(root / 'other')

    def test_events_below_a_hidden_directory_are_dropped(self) -> None:
        with self.workspace('./', '*.json') as root:
            venv = root / '.venv' / 'lib'
            venv.mkdir(parents=True)
            trail = Trail(root)
            session = trail.watchdog.session
            session.prepare(StubLoop())
            session.publish()
            handler = session.handler
            handler.handle(FileCreatedEvent(str(venv / 'a.json')))
            handler.handle(DirCreatedEvent(str(root / '.git')))
            handler.handle(FileModifiedEvent(str(root / '.trail' / 'events.jsonl')))
            assert not session.queue
            # a hidden file is not a hidden directory
            handler.handle(FileCreatedEvent(str(root / '.env')))
            assert len(session.queue) == 1

            async def run() -> None:
                async with self.watching() as open:
                    trail = open(root)
                    (venv / 'b.json').write_text('{}', encoding='utf-8')
                    (root / 'c.json').write_text('{}', encoding='utf-8')
                    await self.until(lambda: root / 'c.json' in trail.assets)
                    await asyncio.sleep(0.1)
                    assert venv / 'b.json' not in trail.assets

            asyncio.run(run())

    def test_a_dir_below_a_hidden_directory_is_still_seen(self) -> None:
        with self.workspace('./', '.config/app/', '*.json') as root:
            app = root / '.config' / 'app'
            app.mkdir(parents=True)

            async def run() -> None:
                async with self.watching() as open:
                    trail = open(root)
                    assert app in trail.dirs
                    settings = app / 'settings.json'
                    settings.write_text('{}', encoding='utf-8')
                    await self.until(lambda: settings in trail.assets)

            asyncio.run(run())

    def test_a_dirasset_outside_every_dir_gets_its_own_watch(self) -> None:
        with self.workspace('./') as root, TemporaryDirectory() as elsewhere:
            outside = Path(elsewhere).resolve()
            cube = outside / 'cube.zarr'
            cube.mkdir()
            trail = Trail(root)
            trail.track(cube)
            assert trail.watchdog.desired() == {
                root: True,
                root.parent: False,
                cube: True,
                outside: False,
            }

    def test_a_dirasset_created_while_watching_is_watched(self) -> None:
        with self.workspace('./', '*.zarr') as root:
            cube = root / 'data' / 'cube.zarr'

            async def run() -> None:
                async with self.watching() as open:
                    trail = open(root)
                    cube.mkdir()
                    (cube / 'zarr.json').write_text('{}', encoding='utf-8')
                    await self.until(lambda: cube in trail.assets)
                    assert trail.watchdog.covers(cube)
                    # the project's recursive watch sees inside it, so it needs none of its own
                    assert cube not in trail.watchdog.session.watches

            asyncio.run(run())

    def test_a_vanished_directory_fails_only_its_own_watch(self) -> None:
        with self.workspace() as root, TemporaryDirectory() as elsewhere:
            outside = Path(elsewhere).resolve()
            kept = outside / 'kept.zarr'
            kept.mkdir()
            vanished = outside / 'vanished.zarr'
            trail = Trail(root, markers=None)
            asset = trail.track(kept)
            trail.watchdog.session.debounce = 0
            desired = trail.watchdog.desired()
            # the directory is named by desired() and gone by the time its watch is placed
            trail.watchdog.desired = lambda: desired | {vanished: True}

            async def run() -> None:
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter('always')
                    assert trail.watchdog.session.start()
                assert any(
                    f'{vanished} is not watched' in str(warning.message)
                    for warning in caught
                )
                watches = trail.watchdog.session.watches
                assert watches[kept] is True
                assert vanished not in watches
                assert trail.watchdog.session.observer.is_alive()
                (kept / 'zarr.json').write_text('{}', encoding='utf-8')
                await self.until(lambda: asset.access.seen is not None)
                await trail.watchdog.session.stop()

            asyncio.run(run())


if __name__ == "__main__":
    suite = TestWatches()
    for name in dir(suite):
        if name.startswith('test_'):
            getattr(suite, name)()
            print(f"  ✓ {name.removeprefix('test_').replace('_', ' ')}")
