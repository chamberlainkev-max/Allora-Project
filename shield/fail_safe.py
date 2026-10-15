"""
shield/fail_safe.py
====================
Self-healing service supervisor.

A crashed long-running service (marketing bot, sniper) is logged and restarted
automatically — no manual restart required for recoverable failures.
"""

import asyncio
from typing import Callable, Awaitable

from logging_config.logger import get_logger

log = get_logger(__name__)


class ServiceWatchdog:
    """
    Monitors a named service (represented by an async coroutine)
    and restarts it automatically on failure.
    """

    def __init__(
        self,
        name: str,
        factory: Callable[[], Awaitable[None]],
        restart_delay: float = 5.0,
    ) -> None:
        self._name = name
        self._factory = factory
        self._restart_delay = restart_delay
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the service and the watchdog loop."""
        self._running = True
        self._task = asyncio.create_task(self._watchdog_loop(), name=f"watchdog:{self._name}")
        log.info("watchdog_started", service=self._name)

    async def stop(self) -> None:
        """Gracefully stop the watchdog and the service."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("watchdog_stopped", service=self._name)

    async def _watchdog_loop(self) -> None:
        while self._running:
            try:
                log.info("service_starting", service=self._name)
                await self._factory()
                log.info("service_exited", service=self._name)
                if self._running:
                    await asyncio.sleep(self._restart_delay)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error(
                    "service_crashed",
                    service=self._name,
                    reason=str(exc),
                    restarting_in=self._restart_delay,
                )
                if self._running:
                    await asyncio.sleep(self._restart_delay)
