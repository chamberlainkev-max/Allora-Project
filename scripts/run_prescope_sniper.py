"""
scripts/run_prescope_sniper.py
==============================
Runs the Pre-Scope & Terminal Sniper engine standalone, 24/7, from its own wallet
(SNIPER_WALLET_PRIVATE_KEY). Independent of main.py (Elora meme marketing).
Strategy values (pre-scope %, buy zone, trade size, TP/SL) come from .env (SNIPER_*).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio

from logging_config.logger import configure_logging
configure_logging()

from execution.sniper_runner import SniperRunner
from shield.fail_safe import ServiceWatchdog


async def main() -> None:
    runner = SniperRunner()
    watchdog = ServiceWatchdog("sniper_runner", runner.start, restart_delay=5.0)
    await watchdog.start()
    try:
        await asyncio.Event().wait()
    finally:
        await runner.stop()
        await watchdog.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
