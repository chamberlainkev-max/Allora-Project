"""
config/settings.py
==================
Centralised configuration loader.
Reads from environment variables (populated by .env or the VPS environment).
No secrets are hardcoded here.
"""

import os
from dotenv import load_dotenv

load_dotenv()  # loads .env if present; production VPS uses real env vars


# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------
def _str(key: str, default: str = "") -> str:
    """Env var value; an empty value falls back to the default."""
    return os.getenv(key, "").strip() or default


def _int(key: str, default: int) -> int:
    try:
        return int(_str(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(_str(key, str(default)))
    except ValueError:
        return default


def _bool(key: str, default: bool) -> bool:
    return _str(key, str(default)).lower() == "true"


# ---------------------------------------------------------------
# General
# ---------------------------------------------------------------
ENV: str = _str("ENV", "development")
LOG_LEVEL: str = _str("LOG_LEVEL", "INFO").upper()
LOG_FILE: str = _str("LOG_FILE", "logs/moneybanque.log")

# ---------------------------------------------------------------
# Solana RPC
# ---------------------------------------------------------------
SOLANA_RPC_URL: str = _str("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
SOLANA_RPC_URL_BACKUP: str = _str("SOLANA_RPC_URL_BACKUP", SOLANA_RPC_URL)
SOLANA_NETWORK: str = _str("SOLANA_NETWORK", "mainnet")
RPC_TIMEOUT: int = _int("RPC_TIMEOUT", 10)
RPC_MAX_RETRIES: int = _int("RPC_MAX_RETRIES", 5)

# ---------------------------------------------------------------
# Priority Fee (Helius getPriorityFeeEstimate)
# ---------------------------------------------------------------
# Min / Low / Medium / High / VeryHigh / UnsafeMax
PRIORITY_FEE_LEVEL: str = _str("PRIORITY_FEE_LEVEL", "High")
MAX_PRIORITY_FEE_MICROLAMPORTS: int = _int("MAX_PRIORITY_FEE_MICROLAMPORTS", 500_000)

# ---------------------------------------------------------------
# PumpPortal (Method 2 — Local Transaction API)
# ---------------------------------------------------------------
# Elora meme wallet (XVYG) — creator / fee payer for meme launches.
# SECURITY: This key must ONLY be in the .env file on the VPS.
PUMPPORTAL_WALLET_PRIVATE_KEY: str = _str("PUMPPORTAL_WALLET_PRIVATE_KEY")
# Only needed when SNIPER_WATCH_PCT > 0 (metered subscribeTokenTrade stream)
PUMPPORTAL_API_KEY: str = _str("PUMPPORTAL_API_KEY")

# ---------------------------------------------------------------
# Pinata IPFS (token image + metadata upload)
# ---------------------------------------------------------------
PINATA_JWT: str = _str("PINATA_JWT")

# ---------------------------------------------------------------
# Elora meme launches (manual only — scripts/launch_meme.py)
# ---------------------------------------------------------------
INITIAL_BUY_SOL: float = _float("INITIAL_BUY_SOL", 0.02)
LAUNCH_SLIPPAGE_PCT: int = _int("LAUNCH_SLIPPAGE_PCT", 10)

# ---------------------------------------------------------------
# Pre-Scope & Terminal Sniper
# ---------------------------------------------------------------
SNIPER_ENABLED: bool = _bool("SNIPER_ENABLED", True)
# Dedicated sniper wallet — kept separate from the meme launch wallet
SNIPER_WALLET_PRIVATE_KEY: str = _str("SNIPER_WALLET_PRIVATE_KEY")
# % of (wallet balance - SNIPER_RESERVE_SOL) per trade, so results compound.
# Starting value 10% (approved by Kevin). Raise only after the trade journal
# shows net profit. 0 = use the fixed SNIPER_BUY_SOL instead.
SNIPER_BALANCE_PCT: float = _float("SNIPER_BALANCE_PCT", 10.0)
# SOL always left in the wallet for network fees + token account rent
SNIPER_RESERVE_SOL: float = _float("SNIPER_RESERVE_SOL", 0.03)
# Loss limit: no new buys while wallet SOL + open position value is below this (0 = off).
# Open positions are still managed (TP / SL / migration / timeout).
SNIPER_MIN_BALANCE_SOL: float = _float("SNIPER_MIN_BALANCE_SOL", 0.0)
# Fixed trade size when SNIPER_BALANCE_PCT=0, otherwise the minimum trade size
SNIPER_BUY_SOL: float = _float("SNIPER_BUY_SOL", 0.05)
SNIPER_PRESCOPE_PCT: float = _float("SNIPER_PRESCOPE_PCT", 68.0)
SNIPER_BUY_MIN_PCT: float = _float("SNIPER_BUY_MIN_PCT", 70.0)
SNIPER_BUY_MAX_PCT: float = _float("SNIPER_BUY_MAX_PCT", 90.0)
# Sell when the bonding curve reaches this %. 0 = off (P&L take-profit only).
SNIPER_SELL_CURVE_PCT: float = _float("SNIPER_SELL_CURVE_PCT", 0.0)
# Buy filters against bundled / instant-pump launches:
#   only buy tokens that were seen in the pre-scope zone (SNIPER_PRESCOPE_PCT–SNIPER_BUY_MIN_PCT) first,
#   that are at least N seconds old, and whose curve is still rising at the buy read
SNIPER_REQUIRE_PRESCOPE: bool = _bool("SNIPER_REQUIRE_PRESCOPE", True)
SNIPER_MIN_TOKEN_AGE_SECONDS: float = _float("SNIPER_MIN_TOKEN_AGE_SECONDS", 60.0)
SNIPER_REQUIRE_RISING: bool = _bool("SNIPER_REQUIRE_RISING", True)
# Skip for good tokens whose curve climbed more than SNIPER_MAX_RISE_PCT points within the last
# SNIPER_RISE_WINDOW_SECONDS (pump-and-dump pattern). 0 = off.
SNIPER_MAX_RISE_PCT: float = _float("SNIPER_MAX_RISE_PCT", 8.0)
SNIPER_RISE_WINDOW_SECONDS: float = _float("SNIPER_RISE_WINDOW_SECONDS", 10.0)
# Position P&L % (live sell value of the tokens vs. SOL spent on the buy) —
# NOT bonding curve levels. Confirmed by Kevin.
SNIPER_TAKE_PROFIT_PCT: float = _float("SNIPER_TAKE_PROFIT_PCT", 85.0)
SNIPER_STOP_LOSS_PCT: float = _float("SNIPER_STOP_LOSS_PCT", 15.0)
SNIPER_EXIT_ON_MIGRATION: bool = _bool("SNIPER_EXIT_ON_MIGRATION", True)
SNIPER_MAX_HOLD_SECONDS: int = _int("SNIPER_MAX_HOLD_SECONDS", 900)
SNIPER_BUY_SLIPPAGE_PCT: int = _int("SNIPER_BUY_SLIPPAGE_PCT", 10)
SNIPER_SELL_SLIPPAGE_PCT: int = _int("SNIPER_SELL_SLIPPAGE_PCT", 15)
# Every N seconds all tracked bonding curves are read via RPC (1 credit per 100 tokens)
SNIPER_SCAN_INTERVAL_SECONDS: float = _float("SNIPER_SCAN_INTERVAL_SECONDS", 5.0)
# Curve % at which a token also gets the real-time PumpPortal trade stream
# (metered, needs PUMPPORTAL_API_KEY). 0 = off, RPC scans only.
SNIPER_WATCH_PCT: float = _float("SNIPER_WATCH_PCT", 0.0)
# Stop tracking a token whose curve has not moved for this many seconds
SNIPER_TRACK_IDLE_SECONDS: int = _int("SNIPER_TRACK_IDLE_SECONDS", 180)
# Open positions are saved here so a restart keeps managing them
SNIPER_STATE_FILE: str = _str("SNIPER_STATE_FILE", "logs/sniper_positions.json")
# In-flight buy/sell signatures awaiting confirmation (F02 pending ledger)
SNIPER_PENDING_FILE: str = _str("SNIPER_PENDING_FILE", "logs/sniper_pending.json")
# Trade journal (JSON lines): signals, missed opportunities, fills, fees, net P&L
SNIPER_TRADE_LOG: str = _str("SNIPER_TRADE_LOG", "logs/sniper_trades.jsonl")
# F04 — single-instance locks (second launch / bad restart must not stack workers)
SNIPER_LOCK_FILE: str = _str("SNIPER_LOCK_FILE", "logs/sniper.lock")
ELORA_LOCK_FILE: str = _str("ELORA_LOCK_FILE", "logs/elora.lock")

# ---------------------------------------------------------------
# Trade pool (Kevin self-buy wallets — NOT wash / fake volume)
# ---------------------------------------------------------------
# Vault of 100 keypairs (gitignored secrets/). Generate once, never commit.
TRADE_POOL_WALLETS_FILE: str = _str("TRADE_POOL_WALLETS_FILE", "secrets/trade_pool_wallets.json")
TRADE_POOL_STATE_FILE: str = _str("TRADE_POOL_STATE_FILE", "logs/trade_pool_state.json")
# Working capital per wallet (~0.10–0.15 SOL per Kevin's sheet)
TRADE_POOL_SOL_PER_WALLET: float = _float("TRADE_POOL_SOL_PER_WALLET", 0.12)
TRADE_POOL_BUY_SOL: float = _float("TRADE_POOL_BUY_SOL", 0.10)
TRADE_POOL_BUY_SLIPPAGE_PCT: int = _int("TRADE_POOL_BUY_SLIPPAGE_PCT", 10)
TRADE_POOL_STAGGER_SECONDS: float = _float("TRADE_POOL_STAGGER_SECONDS", 1.5)
# Fail-closed: tiers off unless both state file AND env say true
TRADE_POOL_TIER_10_ENABLED: bool = _bool("TRADE_POOL_TIER_10_ENABLED", False)
TRADE_POOL_TIER_20_ENABLED: bool = _bool("TRADE_POOL_TIER_20_ENABLED", False)
TRADE_POOL_TIER_50_ENABLED: bool = _bool("TRADE_POOL_TIER_50_ENABLED", False)
TRADE_POOL_TIER_100_ENABLED: bool = _bool("TRADE_POOL_TIER_100_ENABLED", False)
# Required mint for live buys (empty = refuse live runs)
TRADE_POOL_ALLOWED_MINT: str = _str("TRADE_POOL_ALLOWED_MINT")

# ---------------------------------------------------------------
# Audit scripts
# ---------------------------------------------------------------
LYC_TOKEN_MINT: str = _str("LYC_TOKEN_MINT", "HJjWmTp5bR4YYKpCtVW9nJ4qmoL9AgqL6JnNJEYW9nBm")

# ---------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------
TELEGRAM_BOT_TOKEN: str = _str("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID: str = _str("TELEGRAM_CHANNEL_ID")
# Sniper alerts; empty = TELEGRAM_CHANNEL_ID (meme channel)
SNIPER_TELEGRAM_CHANNEL_ID: str = _str("SNIPER_TELEGRAM_CHANNEL_ID")

# ---------------------------------------------------------------
# Discord
# ---------------------------------------------------------------
DISCORD_BOT_TOKEN: str = _str("DISCORD_BOT_TOKEN")
DISCORD_CHANNEL_ID: str = _str("DISCORD_CHANNEL_ID")

# ---------------------------------------------------------------
# Marketing (campaigns in MARKETING_CAMPAIGNS_FILE)
# ---------------------------------------------------------------
# X app credentials: bearer token for search, consumer key/secret for posting.
X_BEARER_TOKEN: str = _str("X_BEARER_TOKEN")
X_API_KEY: str = _str("X_API_KEY")
X_API_SECRET: str = _str("X_API_SECRET")
MARKETING_ENABLED: bool = _bool("MARKETING_ENABLED", False)
# Test mode: find posts + draft + Telegram preview, never publishes
MARKETING_DRY_RUN: bool = _bool("MARKETING_DRY_RUN", True)
MARKETING_CAMPAIGNS_FILE: str = _str("MARKETING_CAMPAIGNS_FILE", "marketing/campaigns.json")
# Source posts already drafted — never referenced twice, survives restarts
MARKETING_STATE_FILE: str = _str("MARKETING_STATE_FILE", "logs/marketing_seen.json")
MARKETING_POLL_INTERVAL: int = _int("MARKETING_POLL_INTERVAL", 900)
MARKETING_MAX_POSTS: int = _int("MARKETING_MAX_POSTS", 3)
MARKETING_APPROVAL_TIMEOUT_MINUTES: int = _int("MARKETING_APPROVAL_TIMEOUT_MINUTES", 60)
# Optional LLM drafting — falls back to the campaign template if empty
OPENAI_API_KEY: str = _str("OPENAI_API_KEY")
OPENAI_BASE_URL: str = _str("OPENAI_BASE_URL", "https://api.openai.com/v1")
OPENAI_MODEL: str = _str("OPENAI_MODEL", "gpt-4o-mini")


def x_account_tokens(account: str) -> tuple[str, str]:
    """(access token, access token secret) for a posting account: X_<ACCOUNT>_ACCESS_TOKEN[_SECRET]."""
    prefix = f"X_{account.upper()}_"
    return _str(prefix + "ACCESS_TOKEN"), _str(prefix + "ACCESS_TOKEN_SECRET")
