"""
execution/terminal_sniper.py
============================
Algorithmic strategy rules for the Pump.fun Pre-Scope & Terminal Sniper.

  Pre-Scope   : token crosses SNIPER_PRESCOPE_PCT (72%) → flagged + alerted
  Final Stretch: token inside SNIPER_BUY_MIN_PCT–SNIPER_BUY_MAX_PCT (70–90%) → BUY
                (SNIPER_BALANCE_PCT of the wallet, or fixed SNIPER_BUY_SOL)
  Buy filters : must have been pre-scoped, not have climbed more than SNIPER_MAX_RISE_PCT
                within SNIPER_RISE_WINDOW_SECONDS, be SNIPER_MIN_TOKEN_AGE_SECONDS old and
                still rising — skips bundled launches and pump-and-dumps
  Exit        : +SNIPER_TAKE_PROFIT_PCT (85%) take-profit, -SNIPER_STOP_LOSS_PCT (15%)
                stop-loss, migration exit, or SNIPER_MAX_HOLD_SECONDS timeout.
                TP / SL are position P&L % (live sell value of the tokens vs. SOL spent
                on the buy), not bonding curve levels.

All values come from config/settings.py (.env).
"""

import math
from dataclasses import dataclass
from typing import Optional

from config import settings

# Pump.fun curve fee (~1.25%) + PumpPortal Local API fee (0.5%), rounded up
_TRADE_FEES_PCT = 2.0


@dataclass(frozen=True)
class TerminalSniperConfig:
    enabled: bool = settings.SNIPER_ENABLED
    pre_scope_min_curve: float = settings.SNIPER_PRESCOPE_PCT
    final_stretch_min_curve: float = settings.SNIPER_BUY_MIN_PCT
    final_stretch_max_curve: float = settings.SNIPER_BUY_MAX_PCT
    sell_curve_pct: float = settings.SNIPER_SELL_CURVE_PCT
    require_pre_scope: bool = settings.SNIPER_REQUIRE_PRESCOPE
    min_token_age_secs: float = settings.SNIPER_MIN_TOKEN_AGE_SECONDS
    require_rising: bool = settings.SNIPER_REQUIRE_RISING
    max_rise_pct: float = settings.SNIPER_MAX_RISE_PCT
    snipe_amount_sol: float = settings.SNIPER_BUY_SOL
    balance_pct: float = settings.SNIPER_BALANCE_PCT
    reserve_sol: float = settings.SNIPER_RESERVE_SOL
    buy_slippage_pct: float = settings.SNIPER_BUY_SLIPPAGE_PCT
    take_profit_pct: float = settings.SNIPER_TAKE_PROFIT_PCT
    stop_loss_pct: float = settings.SNIPER_STOP_LOSS_PCT
    exit_on_migration: bool = settings.SNIPER_EXIT_ON_MIGRATION
    max_hold_seconds: int = settings.SNIPER_MAX_HOLD_SECONDS


class TerminalSniper:
    """Pure decision logic — no I/O. SniperRunner feeds it live market data."""

    def __init__(self, config: Optional[TerminalSniperConfig] = None) -> None:
        self.config = config or TerminalSniperConfig()

    def get_status(self) -> dict:
        c = self.config
        size = f"{c.balance_pct:g}% of balance" if c.balance_pct > 0 else f"{c.snipe_amount_sol} SOL"
        return {
            "status": "ARMED_AND_READY" if c.enabled else "STANDBY_OFF",
            "pre_scope_threshold": f"{c.pre_scope_min_curve}%",
            "final_stretch_range": f"{c.final_stretch_min_curve}% - {c.final_stretch_max_curve}%",
            "trade_size": size,
            "buy_filters": {
                "require_pre_scope": c.require_pre_scope,
                "min_token_age_secs": c.min_token_age_secs,
                "require_rising": c.require_rising,
                "max_rise_pct": c.max_rise_pct,
            },
            "sell_curve_pct": c.sell_curve_pct,
            "take_profit_pct": c.take_profit_pct,
            "stop_loss_pct": c.stop_loss_pct,
        }

    def evaluate_entry(self, curve_pct: float, pre_scoped: bool) -> Optional[str]:
        """Return 'PRE_SCOPE', 'BUY' or None for a token that is not held."""
        c = self.config
        if not c.enabled or curve_pct > c.final_stretch_max_curve:
            return None
        if c.sell_curve_pct > 0 and curve_pct >= c.sell_curve_pct:
            return None
        if curve_pct >= c.final_stretch_min_curve:
            return "BUY"
        if not pre_scoped and curve_pct >= c.pre_scope_min_curve:
            return "PRE_SCOPE"
        return None

    def buy_blocker(self, pre_scoped: bool, token_age_secs: float, rising: bool, rise_pct: float) -> Optional[str]:
        """Why a BUY signal must not be executed yet, or None to buy.

        rise_pct = curve points gained within the rise window.
        'NOT_PRE_SCOPED' and 'TOO_FAST' are final; the others may clear on a later read.
        """
        c = self.config
        if c.require_pre_scope and not pre_scoped:
            return "NOT_PRE_SCOPED"
        if c.max_rise_pct > 0 and rise_pct > c.max_rise_pct:
            return "TOO_FAST"
        if token_age_secs < c.min_token_age_secs:
            return "TOO_YOUNG"
        if c.require_rising and not rising:
            return "NOT_RISING"
        return None

    def trade_size_sol(self, balance_sol: float) -> Optional[float]:
        """SOL to spend on a buy given the wallet balance, or None if too small / unaffordable.

        The worst-case cost (size + slippage + fees) must fit in balance - reserve.
        """
        c = self.config
        free_sol = balance_sol - c.reserve_sol
        worst_case = 1.0 + (c.buy_slippage_pct + _TRADE_FEES_PCT) / 100.0
        if c.balance_pct <= 0:
            size = c.snipe_amount_sol
        else:
            size = math.floor(round(free_sol * c.balance_pct / 100.0 / worst_case * 10_000, 6)) / 10_000
            # 10% can land just under the minimum while the wallet can still pay that minimum.
            if size < c.snipe_amount_sol and c.snipe_amount_sol * worst_case <= free_sol:
                size = c.snipe_amount_sol
        if size < c.snipe_amount_sol or size * worst_case > free_sol:
            return None
        return size

    def evaluate_exit(self, pnl_pct: float, migrated: bool, held_secs: float, curve_pct: float | None = None) -> Optional[str]:
        """Return the sell reason for an open position, or None to keep holding."""
        c = self.config
        if c.sell_curve_pct > 0 and curve_pct is not None and curve_pct >= c.sell_curve_pct:
            return f"SELL_CURVE ({curve_pct:.1f}%)"
        if pnl_pct >= c.take_profit_pct:
            return f"TAKE_PROFIT (+{pnl_pct:.1f}%)"
        if pnl_pct <= -c.stop_loss_pct:
            return f"STOP_LOSS ({pnl_pct:.1f}%)"
        if migrated and c.exit_on_migration:
            return f"MIGRATION_EXIT ({pnl_pct:+.1f}%)"
        if held_secs >= c.max_hold_seconds:
            return f"MAX_HOLD_TIMEOUT ({c.max_hold_seconds}s, {pnl_pct:+.1f}%)"
        return None
