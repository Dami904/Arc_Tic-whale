# Phase 2 — Real Performance Stats & Notification Reliability

**Date:** 2026-07-18
**Status:** Approved design, pending implementation plan
**Goal:** Replace the currently misleading "performance"/"win rate" numbers with genuine, honestly-computed statistics, and make the daily-summary notification scheduler survive Render sleep the same way Phase 1 fixed trading.

## Context and decisions already made

- Phase 1 (production foundation: sessions, auth hardening, GitHub Actions trading, data hygiene) is implemented on branch `phase1-production-foundation`, not yet merged/deployed.
- Investigation found trade *notifications* are already fully built and wired (`backend/notifications.py`, called from `backend/copy_engine.py`) — no work needed there beyond the scheduler reliability fix below.
- Investigation found the two currently-displayed "performance" numbers are not real:
  - `backend/database.py:710` `get_trade_metrics()`'s `win_rate` is actually *execution success rate* (got a tx_id), unrelated to profitability.
  - `backend/wallet_summary.py:122-127`'s `performance` (24h/7d/1y) is literally global ETH price change, unrelated to the user's own trades or the agent they follow.
- `run_trade_cycle()` calls `execute_trade()` without an explicit `amount`, so BUY and SELL orders aren't reliably comparable in real on-chain notional (a pre-existing sizing quirk, out of scope to fix here). Decision: compute performance as **simulated signal-following returns** (using recorded market price at signal time), not reconciled on-chain amounts.
- Decision: include unrealized P&L on currently-open positions (marked to live price), not realized-only.
- Decision: build both agent-level (public, same for everyone) and follower-level ("since I started following") stats — they share one computation engine.
- Decision: keep real 24h/7d/1y time windows (not collapse to all-time-only), backed by a new daily NAV snapshot table populated via a GitHub Actions cron job, mirroring the Phase 1 trade-cycle pattern.

## Components

### 1. Schema changes

- `trade_history` gains a nullable `price REAL` column: the market price at the moment of the BUY/SELL/HOLD decision, populated going forward by `run_trade_cycle()` (which already has `market_data` in scope when it calls `log_trade`). Historical rows before this change stay NULL and are excluded from performance math — no backfill attempted.
- `followers` gains a `followed_at TEXT` column (ISO timestamp): set in `add_follower()`, and reset whenever a user re-follows after unfollowing, so "since following" always reflects the current follow period.
- New table `agent_nav_snapshots`: `(id, agent TEXT, snapshot_date TEXT, nav_multiplier REAL, created_at TEXT)`, unique constraint on `(agent, snapshot_date)`. One row per agent per day; `nav_multiplier` is a cumulative return multiplier (1.0 = breakeven) for a hypothetical stake that followed every signal from that agent's first recorded trade.
- All DDL added to both the Postgres and SQLite branches of `init_db()`, following the existing dual-backend pattern (see `sessions`/`auth_nonces` from Phase 1 for the template).

### 2. Computation engine — `backend/performance.py` (new module)

Core primitive: `compute_cumulative_multiplier(agent_name, since=None, current_prices=None) -> dict`. Walks that agent's `trade_history` rows in timestamp order (filtered to `timestamp >= since` when given, and to rows with a non-NULL `price`), tracking a simulated single stake per asset:
- A BUY while not holding that asset opens a position at the recorded price.
- A SELL while holding closes it — multiply the running total by `sell_price / buy_price`; record a "win" if that ratio is > 1.
- A BUY while already holding, or a SELL while not holding, is a no-op (defensive — shouldn't happen given agent signal semantics, but avoids corrupting the walk on unexpected data).
- HOLD rows are no-ops.
- If the walk ends with an open position (last action was an unmatched BUY), mark it to `current_prices[asset]` instead of a recorded price — this is the unrealized leg. `current_prices` is optional; when omitted, the open leg is excluded (used for point-in-time historical snapshots where "now" isn't meaningful).

Returns `{multiplier: float, win_rate: float, closed_trades: int, has_open_position: bool}`. `win_rate` = wins / closed_trades × 100 (0.0 if no closed trades).

Two callers built on top of this:
- `get_agent_performance(agent_name) -> dict` — all-time stats via `compute_cumulative_multiplier(agent_name, current_prices=live_prices)`, plus `24h`/`7d`/`1y` by dividing today's live multiplier by the nearest `agent_nav_snapshots` row at each lookback distance (nearest-available, not exact-day). A window reports `None` when no snapshot exists far enough back yet (surfaced by the API as "insufficient data", not a fabricated 0%).
- `get_follower_performance(user_id, agent_name) -> dict` — identical shape, but `since=followed_at` for the all-time figure, and each window's baseline is `max(followed_at, snapshot at N days ago)` so a follower who joined 3 days ago gets a genuine 3-day "7d" number instead of a distorted one.

### 3. Daily NAV snapshot job

`scripts/snapshot_nav.py`: for each agent in `AGENT_PROFILES`, compute today's live multiplier (`compute_cumulative_multiplier` with current market prices for the unrealized leg) and upsert into `agent_nav_snapshots` for today's UTC date — idempotent, safe to rerun same-day (`ON CONFLICT (agent, snapshot_date) DO UPDATE`/`INSERT OR REPLACE`, matching backend engine). `.github/workflows/nav-snapshot.yml` runs it once daily via cron; needs only the `DATABASE_URL` secret (market data is unauthenticated CoinGecko/Yahoo — no Circle/Gemini calls). Missing-`DATABASE_URL` guard mirrors `scripts/run_cycle.py`'s pattern from Phase 1.

### 4. Notification-scheduler reliability

`backend/daily_summary.py`'s `start_daily_summary_scheduler()` starts an in-process `threading.Thread` from the FastAPI lifespan — dies whenever Render sleeps the dyno, same class of bug Phase 1 fixed for trading. Fix: remove the thread and its lifespan call; add `scripts/send_daily_summaries.py` (thin wrapper calling the existing `send_daily_summary_reports()`) and `.github/workflows/daily-summary.yml` on a daily cron, reusing the Phase 1 secrets-guard pattern (`DATABASE_URL` + `BOT_TOKEN`/`RESEND_API_KEY`/`RESEND_FROM_EMAIL`).

### 5. API / frontend wiring

- `server/api.py`: replace `get_trade_metrics(agent)` call sites (dashboard ~line 609/617-633, copy modal ~line 1196-1206) with `get_agent_performance(agent)`.
- Replace `get_wallet_stats_safe(...)`'s fake `performance` field in the `/dashboard` response with `get_follower_performance(user_id, agent_name)` for the user's active follow(s).
- `frontend/index.html`: field names (`win_rate`, `performance['24h']/['7d']/['1y']`) stay the same so existing display code doesn't need rewriting; relabel copy that implies "market performance" to "your return since following" where the value is follower-scoped. Handle `None` window values with an "insufficient data yet" display state instead of `0%`.

### 6. Verification / done criteria

- New `trade_history` rows carry a `price`; existing pytest suite plus new tests cover the walk engine (closed round trips, open-position marking, `since` filtering) against hand-constructed trade histories with known expected multipliers/win-rates.
- `snapshot_nav.py` run twice same-day is idempotent (second run doesn't duplicate or corrupt the row).
- A fresh agent with only 2 days of snapshot history shows a real `7d` figure of `None`/"insufficient data", not a fabricated number.
- Daily summary notifications keep firing when verified via manual `workflow_dispatch` runs (can't wait for real Render-sleep-during-scheduled-time in CI).

## Explicitly out of scope

Fixing the BUY/SELL trade-sizing unit inconsistency in `trade_executor.py` (pre-existing, unrelated to stats honesty — the simulated-signal model sidesteps it by design). Backfilling `price` for historical trade rows. Per-trade real-dollar P&L reconciliation. Telegram Mini App and frontend redesign (Phase 3).
