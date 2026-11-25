"""
Fund trade-pool wallets from the Elora / PumpPortal wallet.

Default is dry-run (no SOL moved). Live funding requires --live.

Usage:
  python scripts/fund_trade_pool.py --tier 10
  python scripts/fund_trade_pool.py --tier 10 --live
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from config import settings
from execution.rpc_client import SolanaRPCClient
from execution.trade_pool import (
    TIERS,
    is_tier_enabled,
    load_state,
    sol_per_wallet,
    wallets_for_tier,
    working_capital_sol,
)
from logging_config.logger import get_logger

log = get_logger(__name__)


async def fund(tier: int, live: bool) -> int:
    if tier not in TIERS:
        print(f"Bad tier {tier}")
        return 1
    if not is_tier_enabled(tier):
        print(f"Tier {tier} is not armed (state+env). Run: python scripts/trade_pool_ctl.py enable {tier}")
        print("And set TRADE_POOL_TIER_%s_ENABLED=true" % tier)
        return 1

    wallets = wallets_for_tier(tier)
    per = sol_per_wallet()
    total = working_capital_sol(tier)
    funder = Keypair.from_base58_string(settings.PUMPPORTAL_WALLET_PRIVATE_KEY)

    print(f"Funder: {funder.pubkey()}")
    print(f"Tier {tier}: {len(wallets)} wallets × {per} SOL = {total:.3f} SOL")
    print(f"Mode: {'LIVE' if live else 'DRY-RUN'}")

    async with SolanaRPCClient() as rpc:
        bal = await rpc.get_balance(funder.pubkey()) / 1e9
        print(f"Funder balance: {bal:.5f} SOL")
        if bal < total + 0.05:
            print("ERROR: funder balance too low (need working capital + fees).")
            return 1

        ok = 0
        for w in wallets:
            dest = Pubkey.from_string(w.pubkey)
            dest_bal = await rpc.get_balance(dest) / 1e9
            need = max(0.0, per - dest_bal)
            if need < 0.001:
                print(f"  [{w.index}] {w.pubkey[:8]}… already funded ({dest_bal:.4f} SOL)")
                ok += 1
                continue
            lamports = int(need * 1e9)
            print(f"  [{w.index}] send {need:.4f} SOL → {w.pubkey}")
            if not live:
                continue

            ix = transfer(
                TransferParams(
                    from_pubkey=funder.pubkey(),
                    to_pubkey=dest,
                    lamports=lamports,
                )
            )
            # recent blockhash via RPC client underlying call
            client = rpc._clients[rpc._active_idx]
            bh = (await client.get_latest_blockhash()).value.blockhash
            msg = MessageV0.try_compile(funder.pubkey(), [ix], [], bh)
            raw = bytes(VersionedTransaction(msg, [funder]))
            sig = await rpc.send_raw_transaction(raw)
            landed = await rpc.confirm_transaction(sig)
            print(f"       sig={sig} landed={landed}")
            if landed:
                ok += 1

    state = load_state()
    state["last_fund_tier"] = tier
    from execution.trade_pool import save_state
    save_state(state)
    print(f"Done. Funded/ready: {ok}/{len(wallets)} ({'dry-run' if not live else 'live'})")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--tier", type=int, required=True, choices=TIERS)
    p.add_argument("--live", action="store_true")
    args = p.parse_args()
    return asyncio.run(fund(args.tier, args.live))


if __name__ == "__main__":
    raise SystemExit(main())
