"""
scripts/deep_audit.py
=====================
Diagnostic check: RPC health, wallet balances, $LYC holding, marketing/sniper switches, landing-page assets.
Everything is read from .env — run from the project root: python scripts/deep_audit.py
"""

import sys
import os
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import time

import httpx
from solders.keypair import Keypair

from config import settings

ASSETS = ["pivit_slow_smooth_loop.mp4", "master_frame.png"]


def rpc(method: str, params: list | None = None):
    resp = httpx.post(
        settings.SOLANA_RPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []},
        timeout=10,
    )
    return resp.json().get("result")


def configured_wallets() -> list[tuple[str, str]]:
    """(label, pubkey) for every wallet whose key is configured in .env."""
    wallets: list[tuple[str, str]] = []
    for label, key in [
        ("Wallet 1 (Elora memes)", settings.PUMPPORTAL_WALLET_PRIVATE_KEY),
        ("Sniper Wallet", settings.SNIPER_WALLET_PRIVATE_KEY),
    ]:
        if key:
            pubkey = str(Keypair.from_base58_string(key).pubkey())
            if pubkey not in (p for _, p in wallets):
                wallets.append((label, pubkey))
    return wallets


def print_wallet_balances() -> None:
    print("Wallets:")
    for label, pubkey in configured_wallets():
        sol = (rpc("getBalance", [pubkey]) or {}).get("value", 0) / 1e9
        print(f"   {label}: {pubkey} | {sol:.5f} SOL")


def main() -> None:
    t0 = time.time()
    health = rpc("getHealth")
    latency_ms = (time.time() - t0) * 1000
    print(f"Solana RPC ({settings.SOLANA_NETWORK}): {health} ({latency_ms:.0f} ms)")

    print_wallet_balances()

    mint = settings.LYC_TOKEN_MINT
    creator = str(Keypair.from_base58_string(settings.PUMPPORTAL_WALLET_PRIVATE_KEY).pubkey())
    accounts = (rpc("getTokenAccountsByOwner", [creator, {"mint": mint}, {"encoding": "jsonParsed"}]) or {}).get("value", [])
    holding = sum(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"] or 0 for a in accounts)
    pairs = httpx.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=10).json().get("pairs") or []
    fdv = float(pairs[0].get("fdv", 0) or 0) if pairs else 0.0
    print(f"$LYC: {mint}")
    print(f"   Wallet 1 holding: {holding:,.2f} | DexScreener pairs: {len(pairs)} | FDV: ${fdv:,.2f}")

    print(f"Marketing enabled: {settings.MARKETING_ENABLED} | dry run: {settings.MARKETING_DRY_RUN}")
    size = (f"{settings.SNIPER_BALANCE_PCT:g}% of balance" if settings.SNIPER_BALANCE_PCT > 0
            else f"{settings.SNIPER_BUY_SOL} SOL")
    print(f"Sniper enabled: {settings.SNIPER_ENABLED} | trade size: {size}")

    print("Landing page assets:")
    for name in ASSETS:
        path = os.path.join(ROOT, name)
        size = f"{os.path.getsize(path) / (1024 * 1024):.2f} MB" if os.path.exists(path) else "MISSING"
        print(f"   {name}: {size}")


if __name__ == "__main__":
    main()
