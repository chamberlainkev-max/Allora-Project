"""
integrations/discord_client.py
================================
Discord alert integration.

Sends structured event notifications to the company's designated
Discord channel.

NOTE: Bot token, server/channel ID, and required configuration
must be provided by Kevin at the final integration stage.
Only functionality required by the agreement is implemented here.
"""

import asyncio

import discord

from config import settings
from logging_config.logger import get_logger

log = get_logger(__name__)


class DiscordClient:
    """
    Sends notifications to the configured Discord channel.
    Uses discord.py with a minimal bot implementation.
    """

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = False  # Only need to send, not read
        self._client = discord.Client(intents=intents)
        self._channel: discord.TextChannel | None = None
        self._ready = asyncio.Event()

        @self._client.event
        async def on_ready() -> None:
            channel_id_int = int(settings.DISCORD_CHANNEL_ID or 0)
            if channel_id_int:
                self._channel = self._client.get_channel(channel_id_int)
            self._ready.set()
            log.info(
                "discord_connected",
                bot_user=str(self._client.user),
                channel_id=settings.DISCORD_CHANNEL_ID,
            )

    async def connect(self) -> None:
        """Start the Discord bot in the background."""
        if not settings.DISCORD_BOT_TOKEN:
            log.warning("discord_disabled", reason="DISCORD_BOT_TOKEN not set")
            return

        # Start client in a background task
        asyncio.create_task(
            self._client.start(settings.DISCORD_BOT_TOKEN),
            name="discord_bot",
        )
        # Wait until on_ready fires (max 15s)
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=15)
        except asyncio.TimeoutError:
            log.error("api_error", platform="Discord", reason="Bot did not become ready in 15s")

    async def disconnect(self) -> None:
        await self._client.close()
        log.info("discord_disconnected")

    async def send_launch_alert(self, name: str, symbol: str, mint: str, signature: str) -> None:
        """Send a meme launch notification to the Discord channel."""
        if not self._channel:
            return

        embed = discord.Embed(
            title=f"✅ Token Launch: ${symbol}",
            url=f"https://pump.fun/coin/{mint}",
            color=discord.Color.green(),
        )
        embed.add_field(name="Name", value=name, inline=True)
        embed.add_field(name="Ticker", value=f"${symbol}", inline=True)
        embed.add_field(name="Network", value=settings.SOLANA_NETWORK, inline=True)
        embed.add_field(name="Mint", value=f"`{mint}`", inline=False)
        embed.add_field(name="TX", value=f"https://solscan.io/tx/{signature}", inline=False)
        embed.set_footer(text="Elora")

        for attempt in range(1, 4):
            try:
                await self._channel.send(embed=embed)
                log.info("discord_sent", token_ticker=symbol)
                return
            except discord.DiscordException as exc:
                log.warning(
                    "api_error",
                    platform="Discord",
                    attempt=attempt,
                    reason=str(exc),
                )
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)

        log.error("api_error", platform="Discord", reason="All retry attempts failed")
