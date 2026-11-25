"""
shield/process_lock.py
======================
F04 — single-instance process lock.

Prevents a restart / second launch from leaving two sniper or marketing
workers running at once. The OS releases the lock when the process exits
(including SIGKILL), so a dead worker cannot permanently block restart.
"""

from __future__ import annotations

import os
import sys

from logging_config.logger import get_logger

log = get_logger(__name__)


class ProcessLockError(RuntimeError):
    """Raised when another live process already holds the lock."""


class ProcessLock:
    """Exclusive file lock for one long-running worker process."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._fh = None

    def acquire(self) -> None:
        directory = os.path.dirname(self._path) or "."
        os.makedirs(directory, exist_ok=True)
        # Binary mode so Windows byte-range locking is well-defined.
        self._fh = open(self._path, "a+b")
        try:
            self._lock_file()
        except OSError as exc:
            self._fh.close()
            self._fh = None
            raise ProcessLockError(
                f"another instance already holds {self._path}"
            ) from exc

        self._fh.seek(0)
        self._fh.truncate()
        self._fh.write(f"{os.getpid()}\n".encode("ascii"))
        self._fh.flush()
        log.info("process_lock_acquired", path=self._path, pid=os.getpid())

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            self._unlock_file()
        except OSError:
            pass
        try:
            self._fh.close()
        finally:
            self._fh = None
        log.info("process_lock_released", path=self._path)

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def _lock_file(self) -> None:
        if sys.platform == "win32":
            import msvcrt

            self._fh.seek(0)
            if self._fh.read(1) == b"":
                self._fh.write(b"\0")
                self._fh.flush()
            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(self) -> None:
        if sys.platform == "win32":
            import msvcrt

            self._fh.seek(0)
            msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
