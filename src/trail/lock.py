from __future__ import annotations

import os
import warnings
from pathlib import Path

from trail.node import Node


class Lock(Node):
    def __init__(
        self,
        parent: Node | None = None,
        *,
        path: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = path
        self.pid = os.getpid()
        self.fd: int | None = None
        self.holders: set[object] = set()
        self.warned = False

    @property
    def path(self) -> Path | None:
        if self._path is not None:
            return self._path
        directory = self._trail.dir
        if directory is None:
            return None
        out = directory / 'lock'
        return out

    def acquire(self, holder: object) -> bool:
        if self.pid != os.getpid():
            if self.fd is not None:
                os.close(self.fd)
            self.fd = None
            self.holders.clear()
            self.pid = os.getpid()
            self.warned = False
        if self.holders:
            self.holders.add(holder)
            return True
        path = self.path
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
            try:
                if os.name == 'nt':
                    import msvcrt

                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                if not self.warned:
                    message = f'Trail is already recording in process {self.owner}'
                    warnings.warn(message, stacklevel=2)
                    self.warned = True
                return False
            try:
                os.ftruncate(fd, 0)
                owner = str(self.pid).encode('ascii')
                os.write(fd, owner)
            except BaseException:
                os.close(fd)
                raise
            self.fd = fd
        self.holders.add(holder)
        return True

    def release(self, holder: object) -> None:
        if self.pid != os.getpid():
            return
        self.holders.discard(holder)
        if not self.holders and self.fd is not None:
            os.close(self.fd)
            self.fd = None

    @property
    def owner(self) -> int | None:
        path = self.path
        if path is None:
            return None
        try:
            out = int(path.read_text(encoding='ascii'))
        except (OSError, ValueError):
            return None
        return out
