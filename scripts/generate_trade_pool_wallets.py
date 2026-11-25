"""
Generate the 100-wallet trade-pool vault (once).

Usage:
  python scripts/generate_trade_pool_wallets.py
  python scripts/generate_trade_pool_wallets.py --force   # overwrite existing vault
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from execution.trade_pool import (
    TIERS,
    generate_wallets,
    inventory_path,
    save_wallets,
    wallets_path,
    working_capital_sol,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Kevin self-buy trade pool wallets")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    path = wallets_path()
    if path.exists() and not args.force:
        print(f"Vault already exists: {path}")
        print("Pass --force to overwrite (destroys old keys).")
        return 1

    wallets = generate_wallets(args.count)
    save_wallets(wallets)
    print(f"Wrote {len(wallets)} wallets → {path}")
    print(f"Pubkey inventory → {inventory_path()}")
    print("Working capital (Kevin sheet):")
    for tier in TIERS:
        print(f"  tier {tier:>3}: ~{working_capital_sol(tier):.2f} SOL @ configured per-wallet size")
    print("Keep secrets/ out of git. Fund only after enabling a tier.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
