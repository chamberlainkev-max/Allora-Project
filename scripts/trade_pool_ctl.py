"""
On/off + status for trade-pool tiers (10 / 20 / 50 / 100).

Usage:
  python scripts/trade_pool_ctl.py status
  python scripts/trade_pool_ctl.py enable 10
  python scripts/trade_pool_ctl.py disable 10
  python scripts/trade_pool_ctl.py enable 20 --also-env   # prints .env lines to set
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings
from execution.trade_pool import (
    TIERS,
    is_tier_enabled,
    load_state,
    load_wallets,
    set_tier_enabled,
    wallets_path,
    working_capital_sol,
)


def cmd_status() -> int:
    print(f"Vault file: {wallets_path()}")
    try:
        wallets = load_wallets()
        print(f"Wallets on disk: {len(wallets)}")
    except FileNotFoundError as exc:
        print(f"Wallets on disk: MISSING ({exc})")
        wallets = []

    state = load_state()
    print(f"State file: {settings.TRADE_POOL_STATE_FILE}")
    print(f"Allowed mint: {settings.TRADE_POOL_ALLOWED_MINT or '(unset — live buys blocked)'}")
    print(f"SOL/wallet: {settings.TRADE_POOL_SOL_PER_WALLET} | buy size: {settings.TRADE_POOL_BUY_SOL}")
    print()
    print(f"{'tier':>5}  {'state':>7}  {'env':>7}  {'armed':>7}  {'capital':>10}")
    for tier in TIERS:
        st = bool(state.get("enabled", {}).get(str(tier), False))
        env = os.getenv(f"TRADE_POOL_TIER_{tier}_ENABLED", "false").lower() == "true"
        armed = is_tier_enabled(tier)
        print(
            f"{tier:>5}  {str(st):>7}  {str(env):>7}  {str(armed):>7}  "
            f"~{working_capital_sol(tier):.2f} SOL"
        )
    print()
    print("Armed = state ON and env TRADE_POOL_TIER_<N>_ENABLED=true")
    print("PM2: pm2 start|stop trade-pool-10|20|50|100")
    if wallets:
        print(f"First pubkey: {wallets[0].pubkey}")
    return 0


def cmd_set(tier: int, enabled: bool, also_env: bool) -> int:
    set_tier_enabled(tier, enabled)
    verb = "ENABLED" if enabled else "DISABLED"
    print(f"Tier {tier} {verb} in state file.")
    if also_env or True:
        val = "true" if enabled else "false"
        print(f"Also set in .env / PM2 env: TRADE_POOL_TIER_{tier}_ENABLED={val}")
        if enabled:
            print(f"Then: pm2 restart trade-pool-{tier} --update-env")
        else:
            print(f"Then: pm2 stop trade-pool-{tier}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Trade pool on/off control")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status")

    p_en = sub.add_parser("enable")
    p_en.add_argument("tier", type=int, choices=TIERS)
    p_en.add_argument("--also-env", action="store_true")

    p_dis = sub.add_parser("disable")
    p_dis.add_argument("tier", type=int, choices=TIERS)
    p_dis.add_argument("--also-env", action="store_true")

    args = parser.parse_args()
    if args.cmd == "status":
        return cmd_status()
    if args.cmd == "enable":
        return cmd_set(args.tier, True, args.also_env)
    if args.cmd == "disable":
        return cmd_set(args.tier, False, args.also_env)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
