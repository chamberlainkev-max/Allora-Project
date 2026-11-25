"""
main.py
========
Elora — meme marketing process (no trading, no token creation).

Meme launches are manual only (scripts/launch_meme.py, Kevin-provided memes).
The Pre-Scope Sniper is a separate process (scripts/run_prescope_sniper.py) with its
own wallet, so the two systems never share config, wallets or runtime.

Startup sequence:
  1. Structured logging
  2. F04 process lock (single Elora instance)
  3. Telegram (previews + 1-click approvals)
  4. Marketing bot under a ServiceWatchdog — only if MARKETING_ENABLED
  5. Run until SIGINT / SIGTERM, then shut down cleanly
"""

import asyncio
import signal
import sys

from logging_config.logger import configure_logging, get_logger

# Configure logging first — before any other imports that log
configure_logging()
log = get_logger(__name__)

from config import settings
from integrations.telegram_client import TelegramClient
from marketing.publisher import MarketingBot
from shield.fail_safe import ServiceWatchdog
from shield.process_lock import ProcessLock, ProcessLockError


async def main() -> None:
    lock = ProcessLock(settings.ELORA_LOCK_FILE)
    try:
        lock.acquire()
    except ProcessLockError as exc:
        log.error("elora_already_running", reason=str(exc))
        raise SystemExit(1) from exc

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:
            pass  # Windows: Ctrl+C arrives as KeyboardInterrupt / CancelledError instead

    try:
        log.info("system_starting", env=settings.ENV)
        telegram = TelegramClient()
        await telegram.connect()

        marketing_bot = None
        watchdog = None
        if settings.MARKETING_ENABLED:
            marketing_bot = MarketingBot(telegram=telegram)
            watchdog = ServiceWatchdog("marketing_bot", marketing_bot.start, restart_delay=60.0)
            await watchdog.start()
        else:
            log.info("marketing_bot_skipped", reason="MARKETING_ENABLED != true")

        try:
            log.info("system_running", hint="Press Ctrl+C to stop")
            await shutdown_event.wait()
        finally:
            log.info("system_shutting_down")
            if marketing_bot:
                await marketing_bot.stop()
            if watchdog:
                await watchdog.stop()
            await telegram.disconnect()
            log.info("system_stopped")
    finally:
        lock.release()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    sys.exit(0)
