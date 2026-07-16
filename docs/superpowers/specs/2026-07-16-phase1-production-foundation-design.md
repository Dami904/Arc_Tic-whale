# Phase 1 — Production Foundation for Arc_Tic Whale

**Date:** 2026-07-16
**Status:** Approved design, pending implementation plan
**Goal:** Take the app from hackathon-grade to a reliably-running public testnet beta on a $0/month budget. After Phase 1, the four AI agents trade every 2 hours without any server needing to stay awake, auth has no impersonation holes, and all displayed data is real.

## Context and decisions already made

- **Product target:** polished public beta on Arc **Testnet** (fake USDC). No mainnet, no real funds, no custody/regulatory scope.
- **Budget:** $0/month. Render free tier (~750 pooled instance-hours/month), Neon free Postgres, Vercel free frontend, GitHub Actions free CI.
- **Chosen architecture (approach A, GitHub-Actions variant):** trading is decoupled from web hosting. A scheduled GitHub Actions workflow runs the trade cycle directly (repo checkout + `run_trade_cycle()`), writing to Neon Postgres. Render only serves user traffic and is allowed to sleep.
- **Later phases (out of scope here):** Phase 2 = real per-agent P&L/win-rate stats + trade notifications (Telegram/email). Phase 3 = Telegram Mini App as primary surface + frontend polish.

## Components

### 1. Scheduled trading via GitHub Actions

**What:** A workflow (`.github/workflows/trade-cycle.yml`) on `schedule: cron "0 */2 * * *"` plus `workflow_dispatch` for manual runs. It checks out the repo, installs Python deps (with pip cache), and runs a new entrypoint script `scripts/run_cycle.py` that:

1. Connects to Neon Postgres via `DATABASE_URL` (from GitHub Actions secrets).
2. Checks the kill switch (existing `is_kill_switch_active()`); exits cleanly if active.
3. Fetches market data once (`get_current_market_state()`).
4. Runs `run_trade_cycle()` for each agent in `AGENT_PROFILES`, staggered ~30 s (same pattern as `backend/trade_scheduler.py`), passing the shared market snapshot.
5. Prints a per-agent summary table to stdout (GitHub run log = free observability) and exits non-zero if every agent errored (so the run shows red).

**Secrets in GitHub Actions:** `DATABASE_URL`, `GOOGLE_API_KEY`, `CIRCLE_API_KEY`, `CIRCLE_ENTITY_SECRET`, `AGENT_WALLET_ID`, `AGENT_WALLET_ADDRESS`, `TRADE_DRY_RUN=false`.

**Consequences:**
- The in-process scheduler (`start_trade_scheduler()` in the FastAPI lifespan) is **removed** from the API startup. `get_scheduler_state()`/`/scheduler/status` is repointed to report the last cycle from `trade_history` timestamps instead of in-memory state.
- The manual `/trigger-trade` endpoint stays (admin-token-gated) as a backup lever.
- Cron schedules on GitHub can drift/skip under load; that's acceptable — a cycle 20 minutes late is harmless, and any skipped run is visible in the Actions history.

### 2. Auth hardening

**What:** Close the impersonation hole and make sessions survive restarts.

- **Remove the `wallet_` pseudo-token path** in `verify_privy_token` (`server/api.py:170-172`). Wallet login must prove ownership: frontend requests a nonce (`GET /auth/wallet-nonce`), signs it with `personal_sign`, and posts signature + address to `POST /auth/wallet-verify`. Backend verifies with `eth_account.Account.recover_message` (already available via the `web3` dependency), then issues the same session-token type OTP login uses. Nonces are single-use, stored in the DB with a 5-minute expiry.
- **Move OTP/wallet session tokens from the in-process dict (`_otp_session_store`) to a `sessions` table** in Postgres (token hash, user_id, expires_at). Fixes both the multi-worker limitation the code already flags and the fact that every Render sleep currently logs all OTP users out. Tokens are stored hashed (SHA-256) so a DB leak doesn't leak sessions. Expired rows cleaned up opportunistically on lookup.
- `TRADE_DRY_RUN` auth bypass (`return "dryrun_user"`) is narrowed: it only applies when `AGENT_DEV_MODE=true` as well, so a misconfigured prod deploy with dry-run on doesn't also disable auth.

### 3. Data honesty — remove demo seams

- Delete seeded demo rows (`wallet_dryrun_001`, `wallet_demo_001`, `0xabc*`/`0xf0*` tx ids) from the production DB; keep `scripts/seed_demo.py` but make it refuse to run when `DATABASE_URL` points at a non-local host unless `--force` is passed.
- The repo-root stale artifacts (`circle1.db`, `agora_marketplace.db`, root `agents.py`, empty `main.js`) are deleted; `.gitignore` gains `*.db`.

### 4. Render right-sizing

- **API service:** keeps serving the frontend's API. No keep-alive pings — it sleeps freely. Expected usage well under free-tier hours since trading no longer touches it.
- **Agent service (Node):** stays deployed but sleeps; it's only called at signup (wallet creation), and the existing Python-SDK fallback already covers the cold-start window if a signup hits a sleeping service.
- `render.yaml` updated: remove scheduler-related expectations, document that `TRADE_DRY_RUN=false` matters only for user-initiated actions now.

### 5. Verification / done criteria

- GitHub Actions history shows green trade-cycle runs every 2 hours for 24+ hours, and `trade_history` in Neon gains rows with real Circle transaction ids (66-char hashes, not `0xabc001`).
- A fresh user can: sign up via Privy on the Vercel URL (cold Render start included), get a wallet, follow an agent, and see that agent's next real cycle mirrored to them.
- `Bearer wallet_<address>` without a signature is rejected with 401 (regression test).
- Existing pytest suite still passes; new tests cover nonce sign-in flow and DB-backed sessions.

## Error handling

- Trade cycle: per-agent failures are isolated (existing behavior in `_run_all_agents`); the workflow fails only if all agents fail. Gemini/CoinGecko outages therefore show as partial results, not red runs.
- Wallet verify: invalid/expired nonce, bad signature, and replayed nonce each return distinct 401 details; rate-limited via existing slowapi limiter.
- Session lookups treat DB unavailability as 503, not silent auth failure.

## Explicitly out of scope (Phase 2/3)

Per-agent P&L and win-rate stats, trade notifications, Telegram Mini App work, frontend redesign, splitting `server/api.py` into routers (will happen naturally when Phase 2 touches it).
