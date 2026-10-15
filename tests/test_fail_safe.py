"""
tests/test_fail_safe.py
========================
ServiceWatchdog self-healing tests: auto-restart after a crash, clean shutdown.
"""

import asyncio

import pytest

from shield.fail_safe import ServiceWatchdog


class TestServiceWatchdog:

    @pytest.mark.asyncio
    async def test_watchdog_restarts_on_crash(self):
        """Watchdog must restart the service after a crash."""
        crash_count = 0
        success_event = asyncio.Event()

        async def service_factory():
            nonlocal crash_count
            if crash_count < 2:
                crash_count += 1
                raise RuntimeError("service crashed")
            success_event.set()
            # Hold open until cancelled
            await asyncio.sleep(10)

        watchdog = ServiceWatchdog(
            name="test_service",
            factory=service_factory,
            restart_delay=0.05,
        )
        await watchdog.start()

        # Wait for the service to succeed on 3rd attempt
        try:
            await asyncio.wait_for(success_event.wait(), timeout=5.0)
        finally:
            await watchdog.stop()

        assert crash_count == 2, "Service must have crashed twice before succeeding"

    @pytest.mark.asyncio
    async def test_watchdog_stops_cleanly(self):
        """Watchdog must stop without raising on clean shutdown."""
        async def stable_service():
            await asyncio.sleep(100)

        watchdog = ServiceWatchdog(
            name="stable_service",
            factory=stable_service,
            restart_delay=0.1,
        )
        await watchdog.start()
        await asyncio.sleep(0.05)  # Let it start
        await watchdog.stop()  # Must not raise
