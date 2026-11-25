"""
scripts/run_prescope_sniper.py
==============================
Runs the Pre-Scope & Terminal Sniper engine standalone, 24/7, from its own wallet
(SNIPER_WALLET_PRIVATE_KEY). Independent of main.py (Elora meme marketing).
Strategy values (pre-scope %, buy zone, trade size, TP/SL) come from .env (SNIPER_*).

F04 — acquires a process lock so a restart cannot leave an old sniper worker running.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import signal

from logging_config.logger import configure_logging
configure_logging()

from config import settings
from execution.sniper_runner import SniperRunner
from shield.fail_safe import ServiceWatchdog
from shield.process_lock import ProcessLock, ProcessLockError
from logging_config.logger import get_logger

log = get_logger(__name__)


async def main() -> None:
    lock = ProcessLock(settings.SNIPER_LOCK_FILE)
    try:
        lock.acquire()
    except ProcessLockError as exc:
        log.error("sniper_already_running", reason=str(exc))
        raise SystemExit(1) from exc

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:
            pass  # Windows: Ctrl+C arrives as KeyboardInterrupt

    runner = SniperRunner()
    watchdog = ServiceWatchdog("sniper_runner", runner.start, restart_delay=5.0)
    try:
        await watchdog.start()
        try:
            await shutdown_event.wait()
        finally:
            await runner.stop()
            await watchdog.stop()
    finally:
        lock.release()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
