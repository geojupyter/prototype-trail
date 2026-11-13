from __future__ import annotations

import asyncio
import os
import time
from functools import cached_property
from typing import TYPE_CHECKING, Self

from trail.jupyter.consumer import Consumer
from trail.jupyter.kernels import Kernels
from trail.jupyter.message import Messages
from trail.jupyter.server import Servers
from trail.session import Session

if TYPE_CHECKING:
    from types import TracebackType

    from trail.jupyter.jupyter import Jupyter


class JupyterSession(Session):
    _parent: Jupyter
    interval = 2.0
    task: asyncio.Task[None] | None = None

    @cached_property
    def servers(self) -> Servers:
        out = Servers(self)
        return out

    @cached_property
    def consumer(self) -> Consumer:
        out = Consumer(self)
        return out

    @cached_property
    def kernels(self) -> Kernels:
        out = Kernels(self)
        return out

    @cached_property
    def messages(self) -> Messages:
        out = Messages(self)
        return out

    @property
    def is_active(self) -> bool:
        out = bool(self) and self.task is not None and not self.task.done()
        return out

    def start(self) -> bool:
        if self.is_active:
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        if not self._trail.lock.acquire(self):
            return False
        self.pid = os.getpid()
        self.is_started = True
        # trail: saves - the file sessions of Jupyter's saves are offered to their save runs
        self._trail.watchdog.observers.append(self.consumer.claim)
        try:
            self.task = loop.create_task(self.run(), name='jupyter-discovery')
            self.servers.refresh()
        except BaseException:
            self.close()
            raise
        return True

    async def run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.interval)
                self.servers.refresh()
                self.kernels.refresh()
                self.consumer.close_runs(before=time.time() - self.consumer.run_gap)
        finally:
            if self.task is asyncio.current_task():
                self.close()

    def close(self) -> None:
        if not self:
            return
        self.is_started = False
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if (
            self.task is not None
            and self.task is not current
            and not self.task.done()
            and not self.task.get_loop().is_closed()
        ):
            self.task.cancel()
        try:
            for server in self.servers.values():
                server.close('stopped')
            self.kernels.close()
            self.close_runs()
        finally:
            observers = self._trail.watchdog.observers
            if self.consumer.claim in observers:
                observers.remove(self.consumer.claim)
            self._trail.lock.release(self)

    def close_runs(self) -> None:
        # the file sessions of the last saves are offered to their runs before the runs are logged
        watchdog = self._trail.watchdog.session
        if watchdog:
            for path in list(self.consumer.runs):
                watchdog.end_fold(str(path))
        self.consumer.close_runs()

    async def stop(self) -> None:
        if not self:
            return
        tasks = [
            server.task
            for server in self.servers.values()
            if server.task is not None
            and server.task.get_loop() is asyncio.get_running_loop()
        ]
        if self.task is not None and self.task.get_loop() is asyncio.get_running_loop():
            tasks.append(self.task)
        self.close()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def __aenter__(self) -> Self:
        self.start()
        return self

    async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
    ) -> None:
        await self.stop()
