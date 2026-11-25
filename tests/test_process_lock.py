"""
tests/test_process_lock.py
==========================
F04 — single-instance process lock.
"""

import pytest

from shield.process_lock import ProcessLock, ProcessLockError


class TestProcessLockF04:
    def test_acquire_and_release(self, tmp_path):
        path = str(tmp_path / "sniper.lock")
        lock = ProcessLock(path)
        lock.acquire()
        lock.release()
        # After release, another acquire must succeed.
        lock2 = ProcessLock(path)
        lock2.acquire()
        lock2.release()

    def test_second_acquire_fails_while_held(self, tmp_path):
        path = str(tmp_path / "sniper.lock")
        first = ProcessLock(path)
        first.acquire()
        second = ProcessLock(path)
        with pytest.raises(ProcessLockError):
            second.acquire()
        first.release()

    def test_context_manager(self, tmp_path):
        path = str(tmp_path / "elora.lock")
        with ProcessLock(path):
            with pytest.raises(ProcessLockError):
                ProcessLock(path).acquire()
        # Unlocked after context exit.
        again = ProcessLock(path)
        again.acquire()
        again.release()
