# Elora / Sniper — Solana Automation Stack

Headless Python services for Pump.fun on Solana mainnet. Every threshold, size
and toggle is read from `.env` (see `.env.example`).

Two completely separate systems — own names, config, wallets and processes:

- **Elora** (memes + marketing, Wallet 1 / XVYG) — meme launches only for memes Kevin
  provides (`scripts/launch_meme.py`); `main.py` runs campaign marketing. No trading,
  no automatic token creation.
- **Sniper** (`scripts/run_prescope_sniper.py`, `SNIPER_WALLET_PRIVATE_KEY`) — pre-scope & terminal sniping.

Stopping one never stops the other.

---

## Engines

| Engine | Entry point | Behaviour |
|---|---|---|
| Meme launch (manual) | `scripts/launch_meme.py` | Launches a provided meme (name, ticker, description, image) from Wallet 1, then Telegram + Discord alert. |
| Pre-Scope & Terminal Sniper | `scripts/run_prescope_sniper.py` | Pre-scope alert at `SNIPER_PRESCOPE_PCT`, buy inside `SNIPER_BUY_MIN_PCT`–`SNIPER_BUY_MAX_PCT` with `SNIPER_BALANCE_PCT` of the wallet (results compound) if the buy filters pass, exit on sell-curve / take-profit / stop-loss / migration / max hold. Every opportunity is journaled. |
| Campaign marketing | `main.py` | Per campaign in `marketing/campaigns.json`: find relevant trending X posts → draft tagging the creator → Telegram preview (`MARKETING_DRY_RUN=true`) or admin approval → post. |

Landing page assets: `pivit_slow_smooth_loop.mp4`, `master_frame.png`. Token image: `lyc_token.jpeg`.

---

## Architecture

```
PumpPortal WebSocket (new tokens, migrations — free)
        ─► RPC bonding-curve scan every SNIPER_SCAN_INTERVAL_SECONDS
        ─► optional: tokens ≥ SNIPER_WATCH_PCT get the real-time trade stream (metered)
        ─► SniperRunner ─► TerminalSniper rules ─► PumpPortal buy / sell
        ─► SNIPER_TRADE_LOG journal + Telegram

campaigns.json ─► PostFinder (X search + relevance filter) ─► ContentGenerator
        ─► Telegram preview / approval ─► XAccount (media upload or quote) ─► X post
```

All transactions are built by the PumpPortal Local Transaction API and signed
locally (`integrations/pumpfun_client.sign_transaction`). Priority fees come from
Helius `getPriorityFeeEstimate` (`PRIORITY_FEE_LEVEL`, capped by
`MAX_PRIORITY_FEE_MICROLAMPORTS`).

---

## Directory Structure

```
kevin/
├── main.py                       Elora marketing process
├── config/settings.py            .env loader
├── logging_config/logger.py      Structured JSON logging
├── execution/
│   ├── rpc_client.py             Solana RPC with primary/backup failover
│   ├── priority_fee.py           Helius priority fee estimate
│   ├── terminal_sniper.py        Entry / exit / trade size rules
│   └── sniper_runner.py          Live tracking, execution, trade journal
├── integrations/                 PumpPortal, Telegram (alerts + approvals), Discord
├── marketing/
│   ├── campaigns.json            Campaigns (frog, squirrel, rat)
│   ├── discovery.py              Relevant trending X posts
│   ├── content_generator.py      Drafts (template or LLM hook)
│   └── publisher.py              Marketing bot + X posting
├── shield/fail_safe.py           Service watchdog (auto-restart)
├── scripts/
│   ├── run_prescope_sniper.py    Standalone sniper
│   ├── launch_meme.py            Manual meme launch (Wallet 1)
│   ├── test_marketing.py         Read-only campaign test (nothing is posted)
│   ├── deep_audit.py             RPC, wallets, $LYC, flags, assets
│   └── audit_all.py              Running processes + wallet balances
└── tests/
```

---

## Setup

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # then fill in values
```

Required:
- `SOLANA_RPC_URL` — Helius mainnet endpoint (needed for priority fee estimates)
- `PUMPPORTAL_WALLET_PRIVATE_KEY` — Wallet 1 / Elora (base58), meme launches
- `SNIPER_WALLET_PRIVATE_KEY` — sniper wallet (base58)

Optional:
- `PUMPPORTAL_API_KEY` — only when `SNIPER_WATCH_PCT > 0`; must be valid and funded with ≥ 0.02 SOL (trade stream costs 0.01 SOL per 10k events)
- `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHANNEL_ID` — meme alerts, previews and approval buttons (bot must be channel admin)
- `SNIPER_TELEGRAM_CHANNEL_ID` — sniper alerts only; empty = `TELEGRAM_CHANNEL_ID`
- `DISCORD_BOT_TOKEN` + `DISCORD_CHANNEL_ID` — launch alerts
- `X_BEARER_TOKEN` — campaign search; `X_API_KEY` / `X_API_SECRET` + `X_<ACCOUNT>_ACCESS_TOKEN[_SECRET]` — posting

---

## Running

```bash
# Elora marketing (MARKETING_ENABLED, MARKETING_DRY_RUN)
python main.py

# Sniper (own process, own wallet)
python scripts/run_prescope_sniper.py

# Launch a provided meme from Wallet 1
python scripts/launch_meme.py --name "Token Name" --symbol TICKER --description "..." --image meme.png

# Test a campaign: account tokens, discovery, drafts — nothing is posted
python scripts/test_marketing.py frog

# Read-only health checks
python scripts/deep_audit.py
python scripts/audit_all.py
```

PM2:

```bash
pm2 start ecosystem.config.js
pm2 stop sniper            # pause the sniper only
pm2 stop elora-main        # pause Elora only
pm2 logs sniper --lines 100
```

---

## Sniper Trade Size & Journal

`SNIPER_BALANCE_PCT` (default 10 — starting value approved by Kevin)
spends that % of `balance - SNIPER_RESERVE_SOL` on each buy, leaving room for
`SNIPER_BUY_SLIPPAGE_PCT` and fees, so results compound. Raise it only after the
journal shows net profit. Set it to `0` to use the fixed `SNIPER_BUY_SOL` instead.

`SNIPER_MIN_BALANCE_SOL` is the loss limit: while wallet SOL + open position value is
below it, no new buys are made (journal `buy_skipped` with `LOSS_LIMIT`, one Telegram
alert). Open positions are still managed. `0` turns it off.

Buy filters (against bundled / instant-pump launches that appear straight in the
buy zone and dump): `SNIPER_REQUIRE_PRESCOPE` (token must have been seen between
`SNIPER_PRESCOPE_PCT` and `SNIPER_BUY_MIN_PCT` first), `SNIPER_MAX_RISE_PCT` / `SNIPER_RISE_WINDOW_SECONDS`
(skip for good if the curve climbed more than 8 points in 10 s — pump-and-dump),
`SNIPER_MIN_TOKEN_AGE_SECONDS` (default 60) and `SNIPER_REQUIRE_RISING` (curve must be
higher than at the previous read).

`SNIPER_SELL_CURVE_PCT` sells when the bonding curve reaches that level (`0` = off).
`SNIPER_TAKE_PROFIT_PCT` / `SNIPER_STOP_LOSS_PCT` stay position P&L %: the SOL the held
tokens would return on the live curve vs. the SOL actually spent on the confirmed buy.
The curve sell is checked first. Open
positions are saved to `SNIPER_STATE_FILE`; delete that file only if a position was
closed manually.

`SNIPER_TRADE_LOG` (JSON lines) records per opportunity:
- `signal` — detection time, curve %, market cap, spot price, token age, source (scan / stream)
- `missed` — token jumped past the buy zone between two reads
- `buy_filtered` — buy signal blocked by a filter (`NOT_PRE_SCOPED`, `TOO_FAST`, `TOO_YOUNG`, `NOT_RISING`), once per token and reason
- `buy` / `sell` — read from the confirmed transaction:
  - `spent_sol` / `received_sol` — total wallet SOL change
  - `execution_price` — curve price actually paid / received (no fees)
  - `slippage_vs_signal_pct` — execution price vs. spot price at the signal (buy: + is worse, sell: − is worse)
  - `all_in_price`, `all_in_vs_signal_pct` — including protocol fees
  - `protocol_fees_sol` (pump.fun + creator + PumpPortal), `network_fee_sol` (incl. priority fee), `token_account_rent_sol` (+ paid / − refunded)
  - `latency_secs` — signal → confirmed
- `sell` also: reason, `trigger_pnl_pct`, hold time, `round_trip_fees_sol`, `net_pnl_sol` / `net_pnl_pct` (received − spent)
- `buy_skipped` / `buy_failed` / `sell_failed`

Sells after migration don't touch the bonding curve, so `execution_price` and
`protocol_fees_sol` are `null` there (all-in values are still recorded).

---

## Campaign Marketing

Each campaign in `marketing/campaigns.json` sets: `enabled`, posting `account`,
X search `query`, relevance `keywords` / `exclude_keywords`, `min_engagement`,
`tag_accounts`, `media` (image attached to every post; empty = quote the source post),
`link`, `hashtags`, `hooks` and the post `template`. Posts with $cashtags (other
tokens' promotions) are never referenced, and a source post is only used once.

Go-live order: `python scripts/test_marketing.py <campaign>` → `MARKETING_ENABLED=true`
with `MARKETING_DRY_RUN=true` (Telegram previews) → `MARKETING_DRY_RUN=false`
(every post still needs admin approval). Stays in dry-run until Kevin confirms the
X credentials and approves going live.

Marketing uses X (Twitter) only.

---

## Tests

```bash
pytest -q
```

---

## Security

- Secrets live only in `.env`; never commit it.
- Marketing posts go live only after Telegram approval from a channel admin.
