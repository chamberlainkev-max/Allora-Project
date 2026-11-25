"""
Run a self-buy pass for one trade-pool tier against Kevin's mint.

Hard rules:
  - Tier must be armed (state + env)
  - Mint must match TRADE_POOL_ALLOWED_MINT (unless --dry-run)
  - Buy only (no wash loops)

Usage:
  python scripts/run_trade_pool.py --tier 10 --mint <MINT> --dry-run
  python scripts/run_trade_pool.py --tier 10 --mint <MINT> --live
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from execution.priority_fee import calculate_priority_fee_sol
from execution.rpc_client import SolanaRPCClient
from execution.trade_pool import (
    TIERS,
    is_tier_enabled,
    load_state,
    save_state,
    wallets_for_tier,
)
from execution.tx_verify import TxExpectation
from integrations.pumpfun_client import PumpFunClient, sign_transaction_async
from logging_config.logger import get_logger

log = get_logger(__name__)


async def run_tier(tier: int, mint: str, live: bool) -> int:
    if tier not in TIERS:
        print(f"Bad tier {tier}")
        return 1
    if not is_tier_enabled(tier):
        print(f"Tier {tier} not armed. Enable via trade_pool_ctl + env, then retry.")
        return 1

    allowed = (settings.TRADE_POOL_ALLOWED_MINT or "").strip()
    if live:
        if not allowed:
            print("LIVE blocked: set TRADE_POOL_ALLOWED_MINT to Kevin's mint first.")
            return 1
        if mint != allowed:
            print(f"LIVE blocked: mint {mint} != allowed {allowed}")
            return 1

    wallets = wallets_for_tier(tier)
    buy_sol = float(settings.TRADE_POOL_BUY_SOL)
    stagger = float(settings.TRADE_POOL_STAGGER_SECONDS)
    print(f"Tier {tier} | wallets={len(wallets)} | buy={buy_sol} SOL | mint={mint}")
    print(f"Mode: {'LIVE' if live else 'DRY-RUN'}")

    fills = 0
    fails = 0

    if not live:
        for w in wallets:
            print(f"[{w.index}] {w.pubkey[:10]}… dry-run: would buy (fund before --live)")
            fills += 1
        state = load_state()
        state["last_mint"] = mint
        state["last_run"] = {
            "tier": tier,
            "mint": mint,
            "live": False,
            "fills": fills,
            "fails": 0,
            "ts": int(time.time()),
        }
        save_state(state)
        print(f"Done tier {tier}: fills={fills} fails=0 (dry-run, no RPC)")
        return 0

    pf = PumpFunClient()
    async with SolanaRPCClient() as rpc:
        for w in wallets:
            kp = w.keypair()
            try:
                bal = await rpc.get_balance(kp.pubkey()) / 1e9
            except Exception as exc:
                print(f"[{w.index}] balance read failed: {exc}")
                fails += 1
                continue
            print(f"[{w.index}] {w.pubkey[:10]}… bal={bal:.4f}")
            if bal < buy_sol + 0.01:
                print("       skip: insufficient SOL")
                fails += 1
                continue

            try:
                priority = await calculate_priority_fee_sol(rpc)
                unsigned = await pf.get_unsigned_trade_tx(
                    wallet_pubkey=w.pubkey,
                    token_mint=mint,
                    action="buy",
                    amount=buy_sol,
                    slippage_pct=settings.TRADE_POOL_BUY_SLIPPAGE_PCT,
                    priority_fee_sol=priority,
                )
                signed = await sign_transaction_async(
                    unsigned,
                    [kp],
                    TxExpectation(wallet_pubkey=w.pubkey, action="buy", mint=mint),
                    rpc,
                )
                sig = await rpc.send_raw_transaction(signed)
                ok = await rpc.confirm_transaction(sig)
                log.info(
                    "trade_pool_buy",
                    tier=tier,
                    index=w.index,
                    mint=mint[:12],
                    sig=str(sig),
                    confirmed=ok,
                )
                print(f"       buy sig={sig} confirmed={ok}")
                if ok:
                    fills += 1
                else:
                    fails += 1
            except Exception as exc:
                fails += 1
                log.error("trade_pool_buy_failed", index=w.index, reason=str(exc))
                print(f"       FAIL: {exc}")

            if stagger > 0:
                await asyncio.sleep(stagger)

    state = load_state()
    state["last_mint"] = mint
    state["last_run"] = {
        "tier": tier,
        "mint": mint,
        "live": True,
        "fills": fills,
        "fails": fails,
        "ts": int(time.time()),
    }
    save_state(state)
    print(f"Done tier {tier}: fills={fills} fails={fails}")
    return 0 if fails == 0 else 2


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tier", type=int, required=True, choices=TIERS)
    p.add_argument("--mint", default="", help="defaults to TRADE_POOL_ALLOWED_MINT")
    p.add_argument("--live", action="store_true")
    p.add_argument("--dry-run", action="store_true", default=True)
    args = p.parse_args()
    mint = (args.mint or settings.TRADE_POOL_ALLOWED_MINT or "").strip()
    if not mint:
        print("Provide --mint or set TRADE_POOL_ALLOWED_MINT")
        return 1
    live = bool(args.live)
    return asyncio.run(run_tier(args.tier, mint, live))


if __name__ == "__main__":
    raise SystemExit(main())
