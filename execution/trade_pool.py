"""
execution/trade_pool.py
=======================
Self-buy trade pool for Kevin's own launches.

Tiers: 10 / 20 / 50 / 100 wallets (prefix of a single 100-wallet vault).
On/off is per tier via TRADE_POOL_TIER_<N>_ENABLED + PM2 app control.

Scope (hard rules):
  - Buys only against an explicit mint Kevin owns / launches
  - No wallet-to-wallet wash loops
  - No fake organic volume / rug dump tooling
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from solders.keypair import Keypair

from config import settings
from execution.atomic_json import write_json_atomic

TIERS: tuple[int, ...] = (10, 20, 50, 100)


@dataclass(frozen=True)
class PoolWallet:
    index: int
    pubkey: str
    secret_base58: str

    def keypair(self) -> Keypair:
        return Keypair.from_base58_string(self.secret_base58)


def wallets_path() -> Path:
    return Path(settings.TRADE_POOL_WALLETS_FILE)


def state_path() -> Path:
    return Path(settings.TRADE_POOL_STATE_FILE)


def inventory_path() -> Path:
    """Pubkeys-only inventory (safe to keep beside the vault)."""
    return wallets_path().with_name("trade_pool_pubkeys.json")


def generate_wallets(count: int = 100) -> list[PoolWallet]:
    if count < 1:
        raise ValueError("count must be >= 1")
    out: list[PoolWallet] = []
    for i in range(count):
        kp = Keypair()
        # solders Keypair exposes secret via to_bytes / base58 helpers
        secret = str(kp)  # base58 secret key string used elsewhere in this repo
        out.append(PoolWallet(index=i, pubkey=str(kp.pubkey()), secret_base58=secret))
    return out


def save_wallets(wallets: Iterable[PoolWallet], path: Path | None = None) -> Path:
    path = path or wallets_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "purpose": "kevin_self_buy_trade_pool",
        "count": 0,
        "wallets": [],
    }
    rows = []
    for w in wallets:
        rows.append({"index": w.index, "pubkey": w.pubkey, "secret_base58": w.secret_base58})
    payload["count"] = len(rows)
    payload["wallets"] = rows
    write_json_atomic(str(path), payload)

    inv = {
        "version": 1,
        "count": len(rows),
        "pubkeys": [{"index": r["index"], "pubkey": r["pubkey"]} for r in rows],
    }
    write_json_atomic(str(inventory_path()), inv)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def load_wallets(path: Path | None = None) -> list[PoolWallet]:
    path = path or wallets_path()
    if not path.exists():
        raise FileNotFoundError(
            f"trade pool vault missing: {path} — run scripts/generate_trade_pool_wallets.py"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    wallets = [
        PoolWallet(
            index=int(row["index"]),
            pubkey=str(row["pubkey"]),
            secret_base58=str(row["secret_base58"]),
        )
        for row in data["wallets"]
    ]
    wallets.sort(key=lambda w: w.index)
    return wallets


def default_state() -> dict:
    return {
        "version": 1,
        "enabled": {str(t): False for t in TIERS},
        "last_mint": "",
        "notes": "Self-buy pool for Kevin launches only. Enable a tier before funding/running.",
    }


def load_state() -> dict:
    path = state_path()
    if not path.exists():
        state = default_state()
        write_json_atomic(str(path), state)
        return state
    state = json.loads(path.read_text(encoding="utf-8"))
    enabled = state.setdefault("enabled", {})
    for t in TIERS:
        enabled.setdefault(str(t), False)
    return state


def save_state(state: dict) -> None:
    write_json_atomic(str(state_path()), state)


def set_tier_enabled(tier: int, enabled: bool) -> dict:
    if tier not in TIERS:
        raise ValueError(f"unsupported tier {tier}; choose from {TIERS}")
    state = load_state()
    state["enabled"][str(tier)] = bool(enabled)
    save_state(state)
    return state


def is_tier_enabled(tier: int) -> bool:
    """State file AND env flag must allow the tier (fail closed)."""
    if tier not in TIERS:
        return False
    env_key = f"TRADE_POOL_TIER_{tier}_ENABLED"
    env_ok = os.getenv(env_key, "false").strip().lower() == "true"
    state_ok = bool(load_state().get("enabled", {}).get(str(tier), False))
    return env_ok and state_ok


def wallets_for_tier(tier: int, wallets: list[PoolWallet] | None = None) -> list[PoolWallet]:
    if tier not in TIERS:
        raise ValueError(f"unsupported tier {tier}; choose from {TIERS}")
    all_wallets = wallets if wallets is not None else load_wallets()
    if len(all_wallets) < tier:
        raise ValueError(f"vault has {len(all_wallets)} wallets; need at least {tier}")
    return all_wallets[:tier]


def sol_per_wallet(tier: int | None = None) -> float:
    return float(settings.TRADE_POOL_SOL_PER_WALLET)


def working_capital_sol(tier: int) -> float:
    return sol_per_wallet(tier) * tier
