"""
tests/test_terminal_sniper.py
==============================
Pre-Scope & Terminal Sniper strategy rules and bonding-curve maths.
"""

import pytest

import base64
import struct

from execution.sniper_runner import (
    SniperRunner, _bonding_curve_key, _curve_pct, _decode_curve, _empty_sell_error, _fill_report, _pct_diff, _sell_value_sol,
)
from execution.terminal_sniper import TerminalSniper, TerminalSniperConfig


@pytest.fixture
def sniper():
    return TerminalSniper(TerminalSniperConfig(
        enabled=True,
        pre_scope_min_curve=68.0,
        final_stretch_min_curve=70.0,
        final_stretch_max_curve=90.0,
        sell_curve_pct=0.0,
        require_pre_scope=True,
        min_token_age_secs=60.0,
        require_rising=True,
        max_rise_pct=8.0,
        snipe_amount_sol=0.05,
        balance_pct=100.0,
        reserve_sol=0.03,
        buy_slippage_pct=10.0,
        take_profit_pct=85.0,
        stop_loss_pct=15.0,
        exit_on_migration=True,
        max_hold_seconds=900,
    ))


class TestCurveProgress:

    def test_fresh_token_is_zero(self):
        assert _curve_pct(1_073_000_000) == pytest.approx(0.0)

    def test_completed_curve_is_hundred(self):
        assert _curve_pct(279_900_000) == pytest.approx(100.0)

    def test_midpoint(self):
        assert _curve_pct(1_073_000_000 - 793_100_000 * 0.7) == pytest.approx(70.0)


class TestBondingCurveAccount:

    def _account(self, v_tokens_ui: float, v_sol: float, complete: bool) -> str:
        raw = bytes(8) + struct.pack("<QQQQQ", int(v_tokens_ui * 1e6), int(v_sol * 1e9), 0, 0, 0)
        return base64.b64encode(raw + bytes([1 if complete else 0]) + bytes(32)).decode()

    def test_decodes_reserves(self):
        data, complete = _decode_curve(self._account(502_000_000, 64.0, False))
        assert _curve_pct(data["vTokensInBondingCurve"]) == pytest.approx((1_073_000_000 - 502_000_000) / 793_100_000 * 100)
        assert data["vSolInBondingCurve"] == pytest.approx(64.0)
        assert complete is False

    def test_non_standard_curve_is_ignored(self):
        # Mainnet curve with custom reserves (PMUSE): read as 69% by the standard formula, market cap ~0
        assert _decode_curve(self._account(522_700_000, 0.0058, False)) is None
        assert _decode_curve(self._account(2_072_999_985, 0.001472161, False)) is None

    def test_complete_flag(self):
        assert _decode_curve(self._account(279_900_000, 115.0, True))[1] is True

    def test_curve_key_matches_on_chain(self):
        # Bonding curve that held this mint's tokens on mainnet
        assert _bonding_curve_key("B3YfBzxXdSKmm7ZmmxBHv45ARYEt3PmoPX2e87UZpump") == \
            "6UtPqpAbiJLUVScZ7Q9yyFtCGkhDWDgAyAjktpcSHhW7"


class TestPositionValue:

    def test_selling_own_buy_back_returns_same_sol(self):
        # Constant product: buying 1.6 SOL of tokens and selling them straight back
        # returns 1.6 SOL (before fees) — the buy's own price impact is not profit.
        v_sol, v_tokens = 64.0, 502_000_000.0
        k = v_sol * v_tokens
        tokens = v_tokens - k / (v_sol + 1.6)
        after = {"vSolInBondingCurve": v_sol + 1.6, "vTokensInBondingCurve": v_tokens - tokens}
        assert _sell_value_sol(tokens, after) == pytest.approx(1.6)

    def test_falls_back_to_market_cap(self):
        assert _sell_value_sol(1_000_000, {"marketCapSol": 100.0}) == pytest.approx(0.1)


class TestFillParsing:
    """Confirmed tx → network fee, token account rent, SOL that went into the pool."""

    WALLET, MINT = "Wa11et", "B3YfBzxXdSKmm7ZmmxBHv45ARYEt3PmoPX2e87UZpump"
    CURVE = "6UtPqpAbiJLUVScZ7Q9yyFtCGkhDWDgAyAjktpcSHhW7"

    def _runner(self, tx: dict) -> SniperRunner:
        class _RPC:
            async def get_transaction(self, signature):
                return tx

        runner = object.__new__(SniperRunner)
        runner._wallet_pubkey = self.WALLET
        runner._rpc = _RPC()
        return runner

    def _tx(self, wallet, ata, curve, tokens_pre, tokens_post, fee):
        """wallet / ata / curve = (pre, post) lamports; curve=None → curve not in the tx (migrated pool)."""
        def balances(amount):
            return [{"accountIndex": 1, "owner": self.WALLET, "mint": self.MINT,
                     "uiTokenAmount": {"uiAmount": amount}}] if amount is not None else []
        accounts = [(self.WALLET, wallet), ("Ata", ata)] + ([(self.CURVE, curve)] if curve else [])
        return {
            "transaction": {"message": {"accountKeys": [{"pubkey": key} for key, _ in accounts]}},
            "meta": {
                "fee": fee,
                "preBalances": [pre for _, (pre, _) in accounts],
                "postBalances": [post for _, (_, post) in accounts],
                "preTokenBalances": balances(tokens_pre),
                "postTokenBalances": balances(tokens_post),
            },
        }

    @pytest.mark.asyncio
    async def test_buy_opening_token_account(self):
        # 0.1 SOL into the pool (0.098 to the curve + 0.002 protocol fees) + rent + network fee
        tx = self._tx((1_000_000_000, 1_000_000_000 - 100_000_000 - 2_039_280 - 105_000),
                      (0, 2_039_280), (50_000_000_000, 50_098_000_000), None, 3_000_000.0, 105_000)
        fill = await self._runner(tx)._read_fill("sig", self.MINT)
        assert fill["sol_change"] == pytest.approx(-0.10214428)
        assert fill["network_fee"] == pytest.approx(0.000105)
        assert fill["rent_change"] == pytest.approx(0.00203928)
        assert fill["trade_sol"] == pytest.approx(0.1)
        assert fill["curve_sol"] == pytest.approx(0.098)
        assert fill["protocol_fees"] == pytest.approx(0.002)
        assert fill["token_change"] == pytest.approx(3_000_000.0)

        report = _fill_report(fill, 3_000_000.0, signal_price=0.098 / 3_000_000.0 / 1.01)
        assert report["execution_price"] == pytest.approx(0.098 / 3_000_000.0)
        assert report["slippage_vs_signal_pct"] == pytest.approx(1.0)
        assert report["all_in_price"] == pytest.approx(0.1 / 3_000_000.0)

    @pytest.mark.asyncio
    async def test_sell_closing_token_account(self):
        # 0.1225 SOL out of the curve, 0.0025 protocol fees, 0.12 SOL to the wallet + rent refund - fee
        tx = self._tx((1_000_000_000, 1_000_000_000 + 120_000_000 + 2_039_280 - 105_000),
                      (2_039_280, 0), (50_000_000_000, 49_877_500_000), 3_000_000.0, None, 105_000)
        fill = await self._runner(tx)._read_fill("sig", self.MINT)
        assert fill["rent_change"] == pytest.approx(-0.00203928)
        assert fill["trade_sol"] == pytest.approx(-0.12)
        assert fill["curve_sol"] == pytest.approx(-0.1225)
        assert fill["protocol_fees"] == pytest.approx(0.0025)
        assert fill["token_change"] == pytest.approx(-3_000_000.0)

    @pytest.mark.asyncio
    async def test_migrated_sell_has_no_curve_breakdown(self):
        tx = self._tx((1_000_000_000, 1_120_000_000 - 105_000), (2_039_280, 2_039_280), None,
                      3_000_000.0, 0.0, 105_000)
        fill = await self._runner(tx)._read_fill("sig", self.MINT)
        assert fill["curve_sol"] is None and fill["protocol_fees"] is None
        report = _fill_report(fill, 3_000_000.0, signal_price=0.04 / 1e6)
        assert report["execution_price"] is None
        assert report["all_in_vs_signal_pct"] == pytest.approx(0.0)

    def test_slippage_vs_signal(self):
        assert _pct_diff(1.02, 1.0) == pytest.approx(2.0)
        assert _pct_diff(0.0, 1.0) is None
        assert _pct_diff(None, 1.0) is None


class TestEntryRules:

    def test_below_prescope_ignored(self, sniper):
        assert sniper.evaluate_entry(60.0, pre_scoped=False) is None

    def test_prescope_at_threshold(self, sniper):
        assert sniper.evaluate_entry(68.0, pre_scoped=False) == "PRE_SCOPE"

    def test_unscoped_token_in_buy_zone_signals_buy(self, sniper):
        assert sniper.evaluate_entry(75.0, pre_scoped=False) == "BUY"

    def test_scoped_token_in_buy_zone_buys(self, sniper):
        assert sniper.evaluate_entry(70.0, pre_scoped=True) == "BUY"
        assert sniper.evaluate_entry(90.0, pre_scoped=True) == "BUY"

    def test_scoped_token_below_buy_zone_waits(self, sniper):
        assert sniper.evaluate_entry(69.0, pre_scoped=True) is None

    def test_above_buy_zone_never_buys(self, sniper):
        assert sniper.evaluate_entry(91.0, pre_scoped=True) is None
        assert sniper.evaluate_entry(91.0, pre_scoped=False) is None

    def test_disabled_never_signals(self):
        off = TerminalSniper(TerminalSniperConfig(enabled=False))
        assert off.evaluate_entry(75.0, pre_scoped=True) is None


class TestBuyFilters:

    def test_bundled_launch_straight_into_buy_zone_is_blocked(self, sniper):
        assert sniper.buy_blocker(pre_scoped=False, token_age_secs=3.4, rising=True, rise_pct=0.0) == "NOT_PRE_SCOPED"

    def test_pump_and_dump_jump_is_blocked(self, sniper):
        # KUDOS: 68.7% → 78.0% in 5 s, rugged 10 s after the buy
        assert sniper.buy_blocker(pre_scoped=True, token_age_secs=90.0, rising=True, rise_pct=9.3) == "TOO_FAST"

    def test_fast_but_normal_climb_buys(self, sniper):
        # RIP: 69.0% → 73.6% in 2 s, closed at +101%
        assert sniper.buy_blocker(pre_scoped=True, token_age_secs=90.0, rising=True, rise_pct=4.6) is None

    def test_young_token_waits(self, sniper):
        assert sniper.buy_blocker(pre_scoped=True, token_age_secs=30.0, rising=True, rise_pct=2.0) == "TOO_YOUNG"

    def test_flat_or_falling_curve_waits(self, sniper):
        assert sniper.buy_blocker(pre_scoped=True, token_age_secs=90.0, rising=False, rise_pct=0.0) == "NOT_RISING"

    def test_scoped_old_rising_token_buys(self, sniper):
        assert sniper.buy_blocker(pre_scoped=True, token_age_secs=90.0, rising=True, rise_pct=2.0) is None

    def test_filters_can_be_turned_off(self):
        off = TerminalSniper(TerminalSniperConfig(require_pre_scope=False, min_token_age_secs=0.0,
                                                  require_rising=False, max_rise_pct=0.0))
        assert off.buy_blocker(pre_scoped=False, token_age_secs=0.0, rising=False, rise_pct=50.0) is None


class TestTradeSize:

    def test_full_balance_leaves_room_for_slippage_and_fees(self, sniper):
        size = sniper.trade_size_sol(1.68)
        assert size == pytest.approx(1.65 / 1.12, abs=1e-4)
        assert size * 1.12 <= 1.65

    def test_partial_balance(self):
        half = TerminalSniper(TerminalSniperConfig(
            balance_pct=50.0, reserve_sol=0.03, buy_slippage_pct=10.0, snipe_amount_sol=0.05,
        ))
        assert half.trade_size_sol(1.15) == pytest.approx(0.5)

    def test_below_minimum_skips(self, sniper):
        assert sniper.trade_size_sol(0.08) is None

    def test_ten_percent_just_under_minimum_still_buys_minimum(self):
        # Wallet was 0.5848 SOL: 10% sized to 0.0495, under the 0.05 minimum, so every buy was skipped.
        ten = TerminalSniper(TerminalSniperConfig(
            balance_pct=10.0, reserve_sol=0.03, buy_slippage_pct=10.0, snipe_amount_sol=0.05,
        ))
        assert ten.trade_size_sol(0.584821113) == pytest.approx(0.05)
        assert ten.trade_size_sol(0.08) is None

    def test_empty_sell_error_is_the_zero_amount_code(self):
        assert _empty_sell_error("custom program error: 0x1786")
        assert not _empty_sell_error("custom program error: 0x1772")

    def test_fixed_size_mode(self):
        fixed = TerminalSniper(TerminalSniperConfig(
            balance_pct=0.0, reserve_sol=0.03, buy_slippage_pct=10.0, snipe_amount_sol=0.25,
        ))
        assert fixed.trade_size_sol(1.0) == pytest.approx(0.25)
        assert fixed.trade_size_sol(0.3) is None


class TestExitRules:

    def test_take_profit(self, sniper):
        assert sniper.evaluate_exit(85.0, migrated=False, held_secs=10).startswith("TAKE_PROFIT")

    def test_stop_loss(self, sniper):
        assert sniper.evaluate_exit(-15.0, migrated=False, held_secs=10).startswith("STOP_LOSS")

    def test_migration_exit(self, sniper):
        assert sniper.evaluate_exit(20.0, migrated=True, held_secs=10).startswith("MIGRATION_EXIT")

    def test_max_hold_timeout(self, sniper):
        assert sniper.evaluate_exit(5.0, migrated=False, held_secs=900).startswith("MAX_HOLD_TIMEOUT")

    def test_hold_inside_band(self, sniper):
        assert sniper.evaluate_exit(40.0, migrated=False, held_secs=60) is None
        assert sniper.evaluate_exit(-10.0, migrated=False, held_secs=60) is None

    def test_sells_at_86_curve_and_does_not_buy_there(self):
        zone = TerminalSniper(TerminalSniperConfig(
            enabled=True, pre_scope_min_curve=68.0, final_stretch_min_curve=76.0,
            final_stretch_max_curve=86.0, sell_curve_pct=86.0,
        ))
        assert zone.evaluate_entry(76.0, pre_scoped=True) == "BUY"
        assert zone.evaluate_entry(85.9, pre_scoped=True) == "BUY"
        assert zone.evaluate_entry(86.0, pre_scoped=True) is None
        assert zone.evaluate_exit(10.0, migrated=False, held_secs=30, curve_pct=86.0).startswith("SELL_CURVE")
        assert zone.evaluate_exit(10.0, migrated=False, held_secs=30, curve_pct=80.0) is None
