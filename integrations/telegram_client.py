"""
integrations/telegram_client.py
================================
Telegram integration (@allorameme).

  - Meme launch alerts, sniper signals and trade alerts
  - Marketing draft previews and 1-click approval buttons.
    Only administrators of TELEGRAM_CHANNEL_ID can approve or reject.
"""

import asyncio
import uuid

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

from config import settings
from logging_config.logger import get_logger

log = get_logger(__name__)

_ADMIN_STATUSES = ("creator", "administrator")


class TelegramClient:
    """
    Sends notifications to the configured Telegram channel.
    Handles connection errors with automatic retry.
    """

    def __init__(self, channel_id: str | None = None) -> None:
        self._bot: Bot | None = None
        self._channel_id: str = channel_id or settings.TELEGRAM_CHANNEL_ID
        self._app: Application | None = None
        self._pending_approvals: dict[str, asyncio.Future] = {}

    async def connect(self) -> None:
        """Initialise the Telegram bot client."""
        if not settings.TELEGRAM_BOT_TOKEN:
            log.warning("telegram_disabled", reason="TELEGRAM_BOT_TOKEN not set")
            return
        self._bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        me = await self._bot.get_me()
        log.info("telegram_connected", bot_username=me.username, channel=self._channel_id)

    async def disconnect(self) -> None:
        """Stop the approval listener if it was started."""
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            self._app = None

    async def _send(self, text: str, **kwargs) -> None:
        """Send a message with retry. Plain text — no Markdown parsing failures."""
        if not self._bot:
            return
        for attempt in range(1, 4):
            try:
                await self._bot.send_message(
                    chat_id=self._channel_id,
                    text=text,
                    disable_web_page_preview=True,
                    **kwargs,
                )
                return
            except TelegramError as exc:
                log.warning("api_error", platform="Telegram", attempt=attempt, reason=str(exc))
                if attempt < 3:
                    await asyncio.sleep(2 ** attempt)
        log.error("api_error", platform="Telegram", reason="All retry attempts failed")

    # -----------------------------------------------------------
    # Alerts
    # -----------------------------------------------------------
    async def send(self, text: str) -> None:
        """Send a plain text message to the channel."""
        await self._send(text)

    async def send_launch_alert(self, name: str, symbol: str, mint: str, signature: str) -> None:
        """Send a meme launch notification."""
        await self._send(
            f"✅ Launched\n"
            f"Token: {name} (${symbol})\n"
            f"Pump.fun: https://pump.fun/coin/{mint}\n"
            f"TX: https://solscan.io/tx/{signature}\n"
            f"Network: {settings.SOLANA_NETWORK}"
        )
        log.info("telegram_sent", token_ticker=symbol)

    async def send_sniper_signal(
        self,
        symbol: str,
        name: str,
        mint: str,
        curve_pct: float,
        market_cap_sol: float,
        stage: str,
    ) -> None:
        """Send a pre-scope / final-stretch signal."""
        if stage == "PRE_SCOPE":
            title = f"🎯 [PRE-SCOPE ALPHA {settings.SNIPER_PRESCOPE_PCT:g}%]"
            status_text = f"⚡ Token hit {curve_pct:.1f}% bonding curve — warming up for buy!"
        else:
            title = "🚀 [RUNNER SNIPER SIGNAL]"
            status_text = f"🔥 Active in {stage} zone ({curve_pct:.1f}%)"

        await self._send(
            f"{title}\n\n"
            f"🪙 Token: ${symbol} ({name})\n"
            f"📈 Curve Progress: {curve_pct:.1f}%\n"
            f"💰 Est. MC: ~{market_cap_sol:.1f} SOL\n\n"
            f"{status_text}\n\n"
            f"🔗 Links:\n"
            f"• Pump.fun: https://pump.fun/coin/{mint}\n"
            f"• DexScreener: https://dexscreener.com/solana/{mint}"
        )
        log.info("telegram_signal_sent", symbol=symbol, mint=mint[:12], curve=curve_pct, stage=stage)

    async def send_trade_alert(
        self,
        action: str,
        symbol: str,
        mint: str,
        signature: str,
        wallet_pubkey: str,
        details: list[str],
    ) -> None:
        """Send a sniper BUY / SELL execution alert."""
        emoji = "🟢" if action == "BUY" else "🔴"
        detail_text = "\n".join(details)
        await self._send(
            f"{emoji} SNIPER {action}\n\n"
            f"Token: ${symbol}\n"
            f"Mint: {mint}\n"
            f"{detail_text}\n"
            f"TX: https://solscan.io/tx/{signature}\n"
            f"Wallet: https://solscan.io/account/{wallet_pubkey}"
        )

    # -----------------------------------------------------------
    # 1-click approvals
    # -----------------------------------------------------------
    async def _ensure_approval_listener(self) -> None:
        if self._app:
            return
        self._app = Application.builder().token(settings.TELEGRAM_BOT_TOKEN).build()
        self._app.add_handler(CallbackQueryHandler(self._on_approval_click))
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling(allowed_updates=["callback_query"])
        log.info("telegram_approval_listener_started")

    async def request_approval(self, text: str, timeout_secs: float) -> bool:
        """
        Post `text` with ✅ / ❌ buttons and wait for a channel admin to click.
        Returns False on reject, timeout, or if Telegram is not configured.
        """
        if not self._bot:
            log.warning("approval_unavailable", reason="TELEGRAM_BOT_TOKEN not set")
            return False

        await self._ensure_approval_listener()
        request_id = uuid.uuid4().hex[:16]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_approvals[request_id] = future

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Approve", callback_data=f"approve:{request_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject:{request_id}"),
        ]])
        try:
            message = await self._bot.send_message(
                chat_id=self._channel_id,
                text=text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except TelegramError as exc:
            self._pending_approvals.pop(request_id, None)
            log.error("approval_send_failed", reason=str(exc))
            return False

        try:
            approved = await asyncio.wait_for(future, timeout=timeout_secs)
        except asyncio.TimeoutError:
            approved = False
            try:
                await message.edit_text(f"{text}\n\n⌛ Expired — not approved")
            except TelegramError:
                pass
        finally:
            self._pending_approvals.pop(request_id, None)

        log.info("approval_result", request_id=request_id, approved=approved)
        return approved

    async def _on_approval_click(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        if not query or not query.data:
            return

        action, _, request_id = query.data.partition(":")
        future = self._pending_approvals.get(request_id)
        if future is None or future.done():
            await query.answer("Expired or already processed.")
            return

        member = await self._bot.get_chat_member(self._channel_id, query.from_user.id)
        if member.status not in _ADMIN_STATUSES:
            await query.answer("Only channel admins can approve.", show_alert=True)
            return

        approved = action == "approve"
        future.set_result(approved)
        await query.answer("Approved" if approved else "Rejected")
        verdict = "✅ Approved" if approved else "❌ Rejected"
        try:
            await query.edit_message_text(f"{query.message.text}\n\n{verdict} by {query.from_user.full_name}")
        except TelegramError:
            pass
