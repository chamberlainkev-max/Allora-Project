"""
execution/sniper_runner.py
============================
Pre-Scope & Terminal Sniper Runner — live Pump.fun bonding curve tracker.

Pipeline (cost-aware):
  1. PumpPortal WebSocket: subscribeNewToken + subscribeMigration (free) → track new mints
  2. Every SNIPER_SCAN_INTERVAL_SECONDS all tracked bonding curves are read via RPC
     getMultipleAccounts (batches of 100) → curve %
  3. Optional (SNIPER_WATCH_PCT > 0): tokens at or above it get subscribeTokenTrade
     (metered, 0.01 SOL per 10k events) for real-time updates between scans
  4. Strategy rules (terminal_sniper.py): pre-scope → final-stretch BUY, unless a buy filter
     blocks it (not pre-scoped, climbed too fast, too young, curve not rising)
  5. Open positions: P&L = what the held tokens would sell for on the live curve
     vs. the SOL actually spent (read from the confirmed buy) → TP / SL / migration / timeout SELL
  6. Telegram alerts for every signal and execution

Every signal, missed opportunity and fill (execution price, slippage vs. signal,
protocol fees, network fee, token account rent, latency, net P&L) is appended to the
SNIPER_TRADE_LOG journal.

Tokens whose curve stops moving for SNIPER_TRACK_IDLE_SECONDS, or that move past the
buy zone, are dropped. Open positions are persisted to SNIPER_STATE_FILE.
"""

import asyncio
import base64
import json
import os
import struct
import time
from datetime import datetime, timezone

import websockets
from solders.keypair import Keypair
from solders.pubkey import Pubkey

from config import settings
from execution.priority_fee import calculate_priority_fee_sol
from execution.rpc_client import SolanaRPCClient
from execution.terminal_sniper import TerminalSniper
from integrations.pumpfun_client import PumpFunClient, sign_transaction
from integrations.telegram_client import TelegramClient
from logging_config.logger import get_logger

log = get_logger(__name__)


def _empty_sell_error(reason: str) -> bool:
    """Pump.fun 6022: the sell amount was zero because the wallet no longer holds the token."""
    text = reason.lower()
    return "0x1786" in text or "sellzeroamount" in text

_PUMPPORTAL_WS_URL = "wss://pumpportal.fun/api/data"
_PUMPFUN_PROGRAM_ID = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")

# Pump.fun bonding curve: virtual reserves start at 1,073,000,000 tokens / 30 SOL and the
# curve completes once 793,100,000 tokens have been bought out of it.
_INITIAL_VIRTUAL_TOKENS = 1_073_000_000
_INITIAL_VIRTUAL_SOL = 30
_CURVE_TOKENS_FOR_SALE = 793_100_000
# Constant product of a standard curve; curves that deviate use other reserves, so curve % is meaningless
_CURVE_K = _INITIAL_VIRTUAL_TOKENS * _INITIAL_VIRTUAL_SOL
_CURVE_K_TOLERANCE = 0.01
_TOKEN_SUPPLY = 1_000_000_000
_TOKEN_DECIMALS = 1e6

# Bonding curve account: 8-byte discriminator, then u64 virtual_token_reserves,
# u64 virtual_sol_reserves, 3 more u64 fields, then bool complete at byte 48.
_CURVE_RESERVES_OFFSET = 8
_CURVE_COMPLETE_OFFSET = 48

_SCAN_BATCH = 100
_HOUSEKEEPING_INTERVAL_SECS = 10.0
_TRADE_ATTEMPTS = 3
_FILL_LOOKUP_ATTEMPTS = 10


def _curve_pct(v_tokens: float) -> float:
    return max(0.0, min(100.0, (_INITIAL_VIRTUAL_TOKENS - v_tokens) / _CURVE_TOKENS_FOR_SALE * 100.0))


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _bonding_curve_key(mint: str) -> str:
    return str(Pubkey.find_program_address(
        [b"bonding-curve", bytes(Pubkey.from_string(mint))], _PUMPFUN_PROGRAM_ID
    )[0])


def _decode_curve(data_b64: str) -> tuple[dict, bool] | None:
    """Bonding curve account → (market data in PumpPortal event shape, complete flag).

    None for unreadable accounts and for non-standard curves that are not complete.
    """
    raw = base64.b64decode(data_b64)
    if len(raw) <= _CURVE_COMPLETE_OFFSET:
        return None
    v_tokens_raw, v_sol_lamports = struct.unpack_from("<QQ", raw, _CURVE_RESERVES_OFFSET)
    if v_tokens_raw == 0:
        return None
    v_tokens = v_tokens_raw / _TOKEN_DECIMALS
    v_sol = v_sol_lamports / 1e9
    complete = raw[_CURVE_COMPLETE_OFFSET] != 0
    if not complete and abs(v_tokens * v_sol / _CURVE_K - 1.0) > _CURVE_K_TOLERANCE:
        return None
    data = {
        "vTokensInBondingCurve": v_tokens,
        "vSolInBondingCurve": v_sol,
        "marketCapSol": v_sol / v_tokens * _TOKEN_SUPPLY,
        "pool": "pump",
    }
    return data, complete


def _spot_price(data: dict) -> float:
    """SOL per token in this event."""
    v_sol = _to_float(data.get("vSolInBondingCurve"))
    v_tokens = _to_float(data.get("vTokensInBondingCurve"))
    if v_sol > 0 and v_tokens > 0:
        return v_sol / v_tokens
    return _to_float(data.get("marketCapSol")) / _TOKEN_SUPPLY


def _sell_value_sol(tokens: float, data: dict) -> float:
    """SOL the tokens would return if sold into the pool in this event (before fees)."""
    v_sol = _to_float(data.get("vSolInBondingCurve"))
    v_tokens = _to_float(data.get("vTokensInBondingCurve"))
    if v_sol > 0 and v_tokens > 0:
        return v_sol * tokens / (v_tokens + tokens)
    return tokens * _to_float(data.get("marketCapSol")) / _TOKEN_SUPPLY


def _pct_diff(actual: float | None, reference: float) -> float | None:
    return round((actual / reference - 1.0) * 100.0, 3) if actual and actual > 0 and reference > 0 else None


def _fill_report(fill: dict | None, tokens: float, signal_price: float) -> dict:
    """
    Prices + cost breakdown of a fill (tokens = amount bought / sold, positive).
      execution_price      — curve price actually paid / received (no fees)
      slippage_vs_signal   — execution price vs. spot price at the signal (buy: + is worse, sell: - is worse)
      all_in_price         — price including protocol fees
    """
    if not fill or tokens <= 0:
        return {
            "execution_price": None, "slippage_vs_signal_pct": None, "all_in_price": None,
            "all_in_vs_signal_pct": None, "protocol_fees_sol": None,
            "network_fee_sol": fill["network_fee"] if fill else None,
            "token_account_rent_sol": fill["rent_change"] if fill else None,
        }
    execution_price = abs(fill["curve_sol"]) / tokens if fill["curve_sol"] is not None else None
    all_in_price = abs(fill["trade_sol"]) / tokens
    return {
        "execution_price": execution_price,
        "slippage_vs_signal_pct": _pct_diff(execution_price, signal_price),
        "all_in_price": all_in_price,
        "all_in_vs_signal_pct": _pct_diff(all_in_price, signal_price),
        "protocol_fees_sol": fill["protocol_fees"],
        "network_fee_sol": fill["network_fee"],
        "token_account_rent_sol": fill["rent_change"],
    }


def _fees_line(report: dict) -> str:
    protocol = report["protocol_fees_sol"]
    protocol_text = f"{protocol:.6f}" if protocol is not None else "n/a"
    return (f"Fees: protocol {protocol_text} | network {report['network_fee_sol']:.6f} | "
            f"rent {report['token_account_rent_sol']:+.6f} SOL")


class SniperRunner:
    """Connects the TerminalSniper rules to live Pump.fun data and executes trades."""

    def __init__(self, telegram: TelegramClient | None = None) -> None:
        if not settings.SNIPER_WALLET_PRIVATE_KEY:
            raise ValueError("SNIPER_WALLET_PRIVATE_KEY is not set")
        if settings.SNIPER_WALLET_PRIVATE_KEY == settings.PUMPPORTAL_WALLET_PRIVATE_KEY:
            raise ValueError("SNIPER_WALLET_PRIVATE_KEY must not be the meme wallet (PUMPPORTAL_WALLET_PRIVATE_KEY)")
        self._sniper = TerminalSniper()
        self._pumpfun = PumpFunClient()
        self._wallet = Keypair.from_base58_string(settings.SNIPER_WALLET_PRIVATE_KEY)
        self._wallet_pubkey = str(self._wallet.pubkey())
        self._owns_telegram = telegram is None
        self._telegram = telegram or TelegramClient(
            channel_id=settings.SNIPER_TELEGRAM_CHANNEL_ID or settings.TELEGRAM_CHANNEL_ID
        )
        self._rpc = SolanaRPCClient()
        self._ws = None
        self._running = False

        self._tracked: dict[str, dict] = {}            # mint → {curve_key, v_tokens, changed, created}
        self._watched: set[str] = set()                # mints on the real-time trade stream
        self._metadata: dict[str, tuple[str, str]] = {}  # mint → (symbol, name)
        self._scoped: set[str] = set()                 # pre-scoped mints
        self._filtered: dict[str, set[str]] = {}       # mint → buy filter reasons already journaled
        self._attempted: set[str] = set()              # mints already bought once
        self._undersized: set[str] = set()             # mints already journaled as too small to buy
        self._empty_sell = False
        self._busy: set[str] = set()                   # mints with an in-flight trade
        self._notices: set[str] = set()                # PumpPortal server notices already logged
        self._positions: dict[str, dict] = {}          # mint → open position
        self._buy_lock = asyncio.Lock()                # one buy at a time: size is read from the live balance
        self._priority_fee_sol = 0.0                   # refreshed off the hot path by housekeeping
        self._loss_limit_alerted = False
        self._load_positions()

    async def start(self) -> None:
        if not self._sniper.config.enabled:
            log.info("sniper_runner_skipped", reason="SNIPER_ENABLED=false")
            return
        if settings.SNIPER_WATCH_PCT > 0 and not settings.PUMPPORTAL_API_KEY:
            log.warning("sniper_no_api_key", reason="SNIPER_WATCH_PCT needs PUMPPORTAL_API_KEY; relying on RPC scans only")

        self._running = True
        await self._rpc.connect()
        await self._refresh_priority_fee()
        if self._owns_telegram:
            try:
                await self._telegram.connect()
            except Exception as exc:
                log.warning("telegram_connect_failed", reason=str(exc))

        log.info("sniper_runner_started", wallet=self._wallet_pubkey, open_positions=len(self._positions),
                 **self._sniper.get_status())
        try:
            await asyncio.gather(self._stream(), self._scan(), self._housekeeping())
        finally:
            await self._rpc.close()

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
        log.info("sniper_runner_stopped")

    # -----------------------------------------------------------
    # Position persistence + trade journal
    # -----------------------------------------------------------
    def _load_positions(self) -> None:
        if not os.path.exists(settings.SNIPER_STATE_FILE):
            return
        with open(settings.SNIPER_STATE_FILE, encoding="utf-8") as fh:
            self._positions = json.load(fh)
        now = time.time()
        for mint, position in self._positions.items():
            self._tracked[mint] = {"curve_key": _bonding_curve_key(mint), "v_tokens": 0.0, "changed": now, "created": now}
            self._metadata[mint] = (position["symbol"], "")
            self._attempted.add(mint)
        if self._positions:
            log.info("sniper_positions_restored", mints=list(self._positions))

    def _save_positions(self) -> None:
        os.makedirs(os.path.dirname(settings.SNIPER_STATE_FILE) or ".", exist_ok=True)
        tmp = f"{settings.SNIPER_STATE_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._positions, fh)
        os.replace(tmp, settings.SNIPER_STATE_FILE)

    @staticmethod
    def _journal(event: str, **fields) -> None:
        record = {"time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "event": event, **fields}
        try:
            os.makedirs(os.path.dirname(settings.SNIPER_TRADE_LOG) or ".", exist_ok=True)
            with open(settings.SNIPER_TRADE_LOG, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            log.warning("sniper_journal_error", reason=str(exc))

    # -----------------------------------------------------------
    # PumpPortal WebSocket stream
    # -----------------------------------------------------------
    async def _send(self, method: str, keys: list[str] | None = None) -> None:
        if not self._ws:
            return
        msg: dict = {"method": method}
        if keys is not None:
            msg["keys"] = keys
        await self._ws.send(json.dumps(msg))

    async def _stream(self) -> None:
        url = _PUMPPORTAL_WS_URL
        if settings.SNIPER_WATCH_PCT > 0 and settings.PUMPPORTAL_API_KEY:
            url = f"{url}?api-key={settings.PUMPPORTAL_API_KEY}"

        while self._running:
            try:
                async with websockets.connect(url) as ws:
                    self._ws = ws
                    await self._send("subscribeNewToken")
                    await self._send("subscribeMigration")
                    if self._watched:
                        await self._send("subscribeTokenTrade", list(self._watched))
                    log.info("pumpportal_ws_connected", resubscribed=len(self._watched))

                    async for message in ws:
                        try:
                            data = json.loads(message)
                        except json.JSONDecodeError:
                            continue
                        try:
                            await self._handle_event(data)
                        except Exception as exc:
                            log.warning("ws_event_error", reason=str(exc))

            except websockets.ConnectionClosed:
                log.warning("pumpportal_ws_disconnected", action="reconnecting")
            except Exception as exc:
                log.error("pumpportal_ws_error", reason=str(exc))
            finally:
                self._ws = None

            if self._running:
                await asyncio.sleep(5.0)

    async def _untrack(self, mints: list[str]) -> None:
        unwatch = [mint for mint in mints if mint in self._watched]
        for mint in mints:
            self._tracked.pop(mint, None)
            self._watched.discard(mint)
            self._scoped.discard(mint)
            self._filtered.pop(mint, None)
            self._attempted.discard(mint)
            self._metadata.pop(mint, None)
        if unwatch:
            await self._send("unsubscribeTokenTrade", unwatch)

    async def _handle_event(self, data: dict) -> None:
        mint = data.get("mint")
        if not mint:
            notice = data.get("errors") or data.get("message")
            if notice and notice not in self._notices:
                self._notices.add(notice)
                log.warning("pumpportal_notice", message=notice)
            return

        tx_type = data.get("txType")
        if tx_type == "create":
            if mint not in self._tracked:
                now = time.time()
                self._metadata[mint] = (data.get("symbol") or mint[:6], data.get("name") or "")
                self._tracked[mint] = {
                    "curve_key": data.get("bondingCurveKey") or _bonding_curve_key(mint),
                    "v_tokens": _to_float(data.get("vTokensInBondingCurve")),
                    "changed": now,
                    "created": now,
                }
        elif tx_type in ("migrate", "migration"):
            if mint in self._positions:
                self._positions[mint]["migrated"] = True
                self._check_exit(mint)
            elif mint in self._tracked:
                await self._untrack([mint])
        else:
            await self._on_market(mint, data, source="stream")

    # -----------------------------------------------------------
    # RPC bonding curve scan
    # -----------------------------------------------------------
    async def _scan(self) -> None:
        while self._running:
            await asyncio.sleep(settings.SNIPER_SCAN_INTERVAL_SECONDS)
            mints = list(self._tracked)
            for i in range(0, len(mints), _SCAN_BATCH):
                chunk = [m for m in mints[i:i + _SCAN_BATCH] if m in self._tracked]
                try:
                    accounts = await self._rpc.get_multiple_accounts([self._tracked[m]["curve_key"] for m in chunk])
                except Exception as exc:
                    log.warning("sniper_scan_error", reason=str(exc))
                    continue
                for mint, account in zip(chunk, accounts):
                    decoded = _decode_curve(account) if account else None
                    if not decoded:
                        continue
                    data, complete = decoded
                    if complete:
                        await self._handle_event({"mint": mint, "txType": "migrate"})
                    else:
                        await self._on_market(mint, data, source="scan")

    # -----------------------------------------------------------
    # Market update (trade event or scan)
    # -----------------------------------------------------------
    async def _on_market(self, mint: str, data: dict, source: str) -> None:
        position = self._positions.get(mint)
        if position:
            if position["tokens"] > 0:
                gross_sol = _sell_value_sol(position["tokens"], data)
            else:
                mcap = _to_float(data.get("marketCapSol"))
                gross_sol = position["spent_sol"] * mcap / position["entry_mcap"] if position["entry_mcap"] > 0 else 0.0
            if gross_sol > 0:
                position["value_sol"] = gross_sol
                position["last_price"] = _spot_price(data)
            if data.get("pool") not in (None, "", "pump"):
                position["migrated"] = True
            v_tokens = _to_float(data.get("vTokensInBondingCurve"))
            if v_tokens > 0:
                position["curve_pct"] = _curve_pct(v_tokens)
            self._check_exit(mint)
            return

        entry = self._tracked.get(mint)
        if not entry or mint in self._busy:
            return
        v_tokens = _to_float(data.get("vTokensInBondingCurve"))
        if v_tokens <= 0:
            return
        now = time.time()
        prev_pct = _curve_pct(entry["v_tokens"]) if entry["v_tokens"] > 0 else 0.0
        if v_tokens != entry["v_tokens"]:
            entry["v_tokens"], entry["changed"] = v_tokens, now
        curve_pct = _curve_pct(v_tokens)
        history = entry.setdefault("history", [])
        history.append((now, curve_pct))
        while history[0][0] < now - settings.SNIPER_RISE_WINDOW_SECONDS:
            history.pop(0)
        rise_pct = curve_pct - min(pct for _, pct in history)
        c = self._sniper.config

        if curve_pct > c.final_stretch_max_curve or mint in self._attempted:
            if mint not in self._attempted and prev_pct < c.final_stretch_min_curve:
                self._journal("missed", mint=mint, symbol=self._metadata.get(mint, (mint[:6], ""))[0],
                              last_seen_pct=round(prev_pct, 2), now_pct=round(curve_pct, 2), source=source)
            await self._untrack([mint])
            return

        if 0 < settings.SNIPER_WATCH_PCT <= curve_pct and mint not in self._watched:
            self._watched.add(mint)
            await self._send("subscribeTokenTrade", [mint])

        action = self._sniper.evaluate_entry(curve_pct, pre_scoped=mint in self._scoped)
        if action is None:
            return

        symbol, name = self._metadata.get(mint, (mint[:6], ""))
        token_age = round(now - entry["created"], 1)
        if action == "BUY":
            blocker = self._sniper.buy_blocker(mint in self._scoped, token_age, rising=curve_pct > prev_pct,
                                               rise_pct=rise_pct)
            if blocker:
                if blocker not in self._filtered.setdefault(mint, set()):
                    self._filtered[mint].add(blocker)
                    self._journal("buy_filtered", mint=mint, symbol=symbol, reason=blocker,
                                  curve_pct=round(curve_pct, 2), rise_pct=round(rise_pct, 2),
                                  token_age_secs=token_age, source=source)
                if blocker in ("NOT_PRE_SCOPED", "TOO_FAST"):
                    await self._untrack([mint])
                return

        mcap = _to_float(data.get("marketCapSol"))
        signal = {
            "mint": mint,
            "symbol": symbol,
            "curve_pct": round(curve_pct, 2),
            "mcap_sol": round(mcap, 3),
            "price": _spot_price(data),
            "token_age_secs": token_age,
            "rise_pct": round(rise_pct, 2),
            "source": source,
        }
        self._journal("signal", stage=action, **signal)

        if action == "PRE_SCOPE":
            self._scoped.add(mint)
            log.info("prescope_token_scoped", mint=mint, symbol=symbol, curve_pct=round(curve_pct, 2))
            asyncio.create_task(self._telegram.send_sniper_signal(
                symbol=symbol, name=name, mint=mint,
                curve_pct=curve_pct, market_cap_sol=mcap, stage="PRE_SCOPE",
            ))
        elif action == "BUY":
            self._attempted.add(mint)
            self._busy.add(mint)
            asyncio.create_task(self._buy(name, signal, time.time()))

    # -----------------------------------------------------------
    # Trade execution
    # -----------------------------------------------------------
    async def _execute_trade(
        self,
        mint: str,
        action: str,
        amount: float | str,
        slippage_pct: int,
        denominated_in_sol: bool,
        pool: str,
    ) -> str | None:
        """Build (PumpPortal) → sign locally → submit → confirm. Returns signature if confirmed."""
        self._empty_sell = False
        for attempt in range(1, _TRADE_ATTEMPTS + 1):
            try:
                unsigned = await self._pumpfun.get_unsigned_trade_tx(
                    wallet_pubkey=self._wallet_pubkey,
                    token_mint=mint,
                    action=action,
                    amount=amount,
                    slippage_pct=slippage_pct,
                    priority_fee_sol=self._priority_fee_sol,
                    denominated_in_sol=denominated_in_sol,
                    pool=pool,
                )
            except Exception as exc:
                log.warning("snipe_tx_build_error", action=action, mint=mint, attempt=attempt, reason=str(exc))
                await asyncio.sleep(0.5)
                continue

            try:
                signature = await self._rpc.send_raw_transaction(sign_transaction(unsigned, [self._wallet]))
            except Exception as exc:
                log.warning("snipe_tx_send_error", action=action, mint=mint, attempt=attempt, reason=str(exc))
                # A buy that errored on send may still land — never risk buying twice.
                if action == "buy" or (action == "sell" and _empty_sell_error(str(exc))):
                    self._empty_sell = action == "sell"
                    return None
                await asyncio.sleep(0.5)
                continue

            if await self._rpc.confirm_transaction(signature):
                return str(signature)
            log.error("snipe_tx_not_confirmed", action=action, mint=mint, signature=str(signature))
            return None
        return None

    async def _read_fill(self, signature: str, mint: str) -> dict | None:
        """
        Sniper wallet result of a confirmed trade:
          sol_change   — total wallet SOL change (trade + network fee + token account rent)
          token_change — tokens received (+) / sold (-)
          network_fee  — transaction fee incl. priority fee
          rent_change  — token account rent paid (+, account opened) / refunded (-, closed)
          trade_sol    — SOL that went into (+) / came out of (-) the pool incl. protocol fees
          curve_sol    — SOL change of the bonding curve itself (pure price, no fees);
                         None when the trade did not touch the curve (migrated pool)
          protocol_fees — pump.fun + creator + PumpPortal fees (trade_sol - curve_sol), or None
        """
        curve_key = _bonding_curve_key(mint)
        for _ in range(_FILL_LOOKUP_ATTEMPTS):
            try:
                tx = await self._rpc.get_transaction(signature)
            except Exception as exc:
                log.warning("snipe_fill_lookup_error", signature=signature, reason=str(exc))
                tx = None
            if tx and tx.get("meta"):
                meta = tx["meta"]
                pre, post = meta["preBalances"], meta["postBalances"]
                keys = [k["pubkey"] for k in tx["transaction"]["message"]["accountKeys"]]
                idx = keys.index(self._wallet_pubkey)

                tokens = {"pre": 0.0, "post": 0.0}
                token_accounts: set[int] = set()
                for side in ("pre", "post"):
                    for b in meta.get(f"{side}TokenBalances") or []:
                        if b.get("owner") == self._wallet_pubkey and b.get("mint") == mint:
                            tokens[side] += _to_float(b["uiTokenAmount"]["uiAmount"])
                            token_accounts.add(b["accountIndex"])

                sol_change = (post[idx] - pre[idx]) / 1e9
                network_fee = meta["fee"] / 1e9
                rent_change = sum(post[i] - pre[i] for i in token_accounts) / 1e9
                trade_sol = -(sol_change + network_fee + rent_change)
                curve_sol = None
                if curve_key in keys:
                    curve_idx = keys.index(curve_key)
                    curve_sol = (post[curve_idx] - pre[curve_idx]) / 1e9
                return {
                    "sol_change": sol_change,
                    "token_change": tokens["post"] - tokens["pre"],
                    "network_fee": network_fee,
                    "rent_change": rent_change,
                    "trade_sol": trade_sol,
                    "curve_sol": curve_sol,
                    "protocol_fees": trade_sol - curve_sol if curve_sol is not None else None,
                }
            await asyncio.sleep(0.5)
        return None

    async def _buy(self, name: str, signal: dict, detected_at: float) -> None:
        mint, symbol = signal["mint"], signal["symbol"]
        try:
            async with self._buy_lock:
                balance_sol = await self._rpc.get_balance(self._wallet.pubkey()) / 1e9
                equity_sol = balance_sol + sum(p["value_sol"] for p in self._positions.values())
                if equity_sol < settings.SNIPER_MIN_BALANCE_SOL:
                    log.warning("snipe_buy_skipped", mint=mint, symbol=symbol, equity_sol=equity_sol,
                                reason="loss limit (SNIPER_MIN_BALANCE_SOL)")
                    self._journal("buy_skipped", mint=mint, symbol=symbol, balance_sol=balance_sol,
                                  equity_sol=equity_sol, reason="LOSS_LIMIT")
                    if not self._loss_limit_alerted:
                        self._loss_limit_alerted = True
                        asyncio.create_task(self._telegram.send(
                            f"⛔ Sniper loss limit hit: wallet + open positions {equity_sol:.4f} SOL < "
                            f"{settings.SNIPER_MIN_BALANCE_SOL} SOL. No new buys; open positions are still managed."
                        ))
                    await self._untrack([mint])
                    return
                amount_sol = self._sniper.trade_size_sol(balance_sol)
                if amount_sol is None:
                    if mint not in self._undersized:
                        self._undersized.add(mint)
                        log.info("snipe_buy_skipped", mint=mint, symbol=symbol, balance_sol=balance_sol,
                                 reason="free balance below minimum trade size")
                        self._journal("buy_skipped", mint=mint, symbol=symbol, balance_sol=balance_sol)
                    return

                asyncio.create_task(self._telegram.send_sniper_signal(
                    symbol=symbol, name=name, mint=mint,
                    curve_pct=signal["curve_pct"], market_cap_sol=signal["mcap_sol"], stage="FINAL_STRETCH_BUY",
                ))
                log.info("snipe_buy_executing", mint=mint, symbol=symbol, amount_sol=amount_sol,
                         curve_pct=signal["curve_pct"])
                signature = await self._execute_trade(
                    mint, "buy", amount_sol, settings.SNIPER_BUY_SLIPPAGE_PCT,
                    denominated_in_sol=True, pool="pump",
                )
            confirmed_at = time.time()
            if not signature:
                log.error("snipe_buy_failed", mint=mint, symbol=symbol)
                self._journal("buy_failed", mint=mint, symbol=symbol, amount_sol=amount_sol)
                await self._untrack([mint])
                return

            fill = await self._read_fill(signature, mint)
            spent_sol = -fill["sol_change"] if fill else amount_sol
            tokens = fill["token_change"] if fill else 0.0
            report = _fill_report(fill, tokens, signal["price"])
            buy_fees = (report["protocol_fees_sol"] + report["network_fee_sol"]
                        if report["protocol_fees_sol"] is not None else None)
            latency = round(confirmed_at - detected_at, 2)

            self._positions[mint] = {
                "symbol": symbol,
                "spent_sol": spent_sol,
                "tokens": tokens,
                "entry_mcap": signal["mcap_sol"],
                "entry_price": report["execution_price"],
                "buy_fees_sol": buy_fees,
                "value_sol": spent_sol,
                "last_price": signal["price"],
                "entry_time": confirmed_at,
                "migrated": False,
            }
            self._save_positions()
            self._journal(
                "buy", **signal, signature=signature, amount_sol=amount_sol, spent_sol=spent_sol, tokens=tokens,
                **report, latency_secs=latency,
            )
            log.info("snipe_buy_success", mint=mint, symbol=symbol, spent_sol=spent_sol, tokens=tokens,
                     slippage_vs_signal_pct=report["slippage_vs_signal_pct"], latency_secs=latency,
                     signature=signature)

            details = [
                f"Signal: {signal['curve_pct']:.1f}% curve ({signal['source']})",
                f"Spent: {spent_sol:.4f} SOL (incl. all fees + token account rent)",
            ]
            if report["slippage_vs_signal_pct"] is not None:
                details.append(f"Execution vs signal price: {report['slippage_vs_signal_pct']:+.2f}%")
            if fill:
                details.append(_fees_line(report))
            details.append(f"Signal → confirmed: {latency:.1f}s")
            await self._telegram.send_trade_alert(
                action="BUY", symbol=symbol, mint=mint, signature=signature,
                wallet_pubkey=self._wallet_pubkey, details=details,
            )
        finally:
            self._busy.discard(mint)

    def _check_exit(self, mint: str) -> None:
        position = self._positions.get(mint)
        if not position or mint in self._busy:
            return
        pnl_pct = (position["value_sol"] / position["spent_sol"] - 1.0) * 100.0
        held_secs = time.time() - position["entry_time"]
        reason = self._sniper.evaluate_exit(pnl_pct, position["migrated"], held_secs, position.get("curve_pct"))
        if reason:
            self._busy.add(mint)
            asyncio.create_task(self._sell(mint, reason, round(pnl_pct, 3), time.time()))

    async def _token_ui_amount(self, mint: str) -> float | None:
        """Tokens this wallet still holds, or None if the balance read failed."""
        try:
            result = await self._rpc._raw_call(
                "getTokenAccountsByOwner",
                [self._wallet_pubkey, {"mint": mint}, {"encoding": "jsonParsed"}],
            )
        except Exception as exc:
            log.warning("snipe_token_balance_error", mint=mint, reason=str(exc))
            return None
        total = 0.0
        for acc in (result or {}).get("value") or []:
            try:
                total += float(acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"] or 0)
            except (KeyError, TypeError, ValueError):
                continue
        return total

    async def _sell(self, mint: str, reason: str, trigger_pnl_pct: float, detected_at: float) -> None:
        position = self._positions[mint]
        symbol = position["symbol"]
        signal_price = position.get("last_price", 0.0)
        log.info("snipe_sell_executing", mint=mint, symbol=symbol, reason=reason)
        try:
            signature = await self._execute_trade(
                mint, "sell", "100%", settings.SNIPER_SELL_SLIPPAGE_PCT,
                denominated_in_sol=False, pool="auto",
            )
            confirmed_at = time.time()
            if not signature:
                if self._empty_sell and await self._token_ui_amount(mint) == 0.0:
                    log.warning("snipe_sell_already_gone", mint=mint, symbol=symbol, reason=reason)
                    self._journal("sell_already_gone", mint=mint, symbol=symbol, reason=reason)
                    del self._positions[mint]
                    self._save_positions()
                    await self._untrack([mint])
                    await self._telegram.send(
                        f"⚪ {symbol} was already sold from the wallet. Position closed."
                    )
                    return
                log.error("snipe_sell_failed", mint=mint, symbol=symbol, reason=reason)
                self._journal("sell_failed", mint=mint, symbol=symbol, reason=reason)
                return

            del self._positions[mint]
            self._save_positions()
            await self._untrack([mint])
            fill = await self._read_fill(signature, mint)
            received_sol = fill["sol_change"] if fill else position["value_sol"]
            sold = -fill["token_change"] if fill else 0.0
            report = _fill_report(fill, sold, signal_price)
            buy_fees = position.get("buy_fees_sol")
            round_trip_fees = (buy_fees + report["protocol_fees_sol"] + report["network_fee_sol"]
                               if buy_fees is not None and report["protocol_fees_sol"] is not None else None)
            pnl_sol = received_sol - position["spent_sol"]
            pnl_pct = pnl_sol / position["spent_sol"] * 100.0
            latency = round(confirmed_at - detected_at, 2)
            held_secs = round(confirmed_at - position["entry_time"], 1)

            self._journal(
                "sell", mint=mint, symbol=symbol, reason=reason, signature=signature,
                trigger_pnl_pct=trigger_pnl_pct, spent_sol=position["spent_sol"], received_sol=received_sol,
                tokens=sold, entry_price=position.get("entry_price"), exit_signal_price=signal_price,
                **report, round_trip_fees_sol=round_trip_fees, latency_secs=latency, held_secs=held_secs,
                net_pnl_sol=round(pnl_sol, 9), net_pnl_pct=round(pnl_pct, 3),
            )
            log.info("snipe_sell_success", mint=mint, symbol=symbol, reason=reason, received_sol=received_sol,
                     pnl_sol=round(pnl_sol, 6), pnl_pct=round(pnl_pct, 2), signature=signature)

            details = [
                f"Reason: {reason} (held {held_secs:.0f}s)",
                f"Spent: {position['spent_sol']:.4f} SOL → Received: {received_sol:.4f} SOL",
            ]
            if report["slippage_vs_signal_pct"] is not None:
                details.append(f"Execution vs signal price: {report['slippage_vs_signal_pct']:+.2f}%")
            if fill:
                details.append(_fees_line(report))
            if round_trip_fees is not None:
                details.append(f"Round-trip fees: {round_trip_fees:.6f} SOL")
            details.append(f"Net P&L: {pnl_sol:+.4f} SOL ({pnl_pct:+.2f}%)")
            await self._telegram.send_trade_alert(
                action="SELL", symbol=symbol, mint=mint, signature=signature,
                wallet_pubkey=self._wallet_pubkey, details=details,
            )
        finally:
            self._busy.discard(mint)

    async def _refresh_priority_fee(self) -> None:
        fee_sol = await calculate_priority_fee_sol(self._rpc)
        if fee_sol > 0:
            self._priority_fee_sol = fee_sol

    # -----------------------------------------------------------
    # Housekeeping: priority fee refresh, max-hold exits, idle cleanup
    # -----------------------------------------------------------
    async def _housekeeping(self) -> None:
        while self._running:
            await asyncio.sleep(_HOUSEKEEPING_INTERVAL_SECS)
            try:
                await self._refresh_priority_fee()
                for mint in list(self._positions):
                    self._check_exit(mint)

                cutoff = time.time() - settings.SNIPER_TRACK_IDLE_SECONDS
                idle = [
                    mint for mint, entry in self._tracked.items()
                    if entry["changed"] < cutoff and mint not in self._positions and mint not in self._busy
                ]
                await self._untrack(idle)
            except Exception as exc:
                log.warning("sniper_housekeeping_error", reason=str(exc))
