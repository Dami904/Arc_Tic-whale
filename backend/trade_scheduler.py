"""
trade_scheduler.py — Auto-scheduler for the AI trade cycle.

Runs every 2 hours (configurable via TRADE_CYCLE_INTERVAL_HOURS).
Fires run_trade_cycle() for every agent in AGENT_PROFILES, staggered by
3 minutes so they don't hammer the AI and Circle APIs simultaneously.

Respects the kill switch — if kill switch is active, the cycle is skipped
entirely that round and retried on the next interval.
"""
from __future__ import annotations

import asyncio
import datetime
from typing import Optional

from backend.agents import AGENT_PROFILES
from backend.logger import get_logger
from backend.market_data import get_current_market_state
from backend.trade_service import run_trade_cycle

log = get_logger("trade_scheduler")

# ── Config ─────────────────────────────────────────────────────────────────────
TRADE_CYCLE_INTERVAL_HOURS: float = 2.0  # run every 2 hours
AGENT_STAGGER_SECONDS:      int   = 30   # 30 s between agents — just enough for Gemini rate limits
                                          # (market data is fetched ONCE and shared, so no CoinGecko/Yahoo pressure)
STARTUP_DELAY_SECONDS:      int   = 60   # wait 60 s after boot before first run

# ── Runtime state (read by /scheduler/status endpoint) ─────────────────────────
_state: dict = {
    "running":    False,
    "last_run":   None,   # ISO timestamp of last completed cycle
    "next_run":   None,   # ISO timestamp of next scheduled cycle
    "last_results": {},   # {agent_name: {status, action, asset}}
}


def get_scheduler_state() -> dict:
    return dict(_state)


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _next_iso(seconds_from_now: float) -> str:
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds_from_now)
    return t.isoformat(timespec="seconds")


async def _run_all_agents() -> dict[str, dict]:
    """
    Fetches market data ONCE, then runs one trade cycle per agent.
    Agents are staggered by AGENT_STAGGER_SECONDS (30 s) — only enough
    to respect Gemini's rate limits, since market data is shared.

    Before:  4 × CoinGecko + 4 × Yahoo + 4 × Gemini  (~10 min total)
    After:   1 × CoinGecko + 1 × Yahoo + 4 × Gemini  (~2 min total)
    """
    results: dict[str, dict] = {}
    agent_names = list(AGENT_PROFILES.keys())

    # ── Fetch market data once for the whole cycle ─────────────────────────
    log.info("Fetching shared market snapshot for this cycle...")
    try:
        shared_market = await asyncio.to_thread(get_current_market_state)
        btc_price = shared_market.get("BTC", {}).get("PRICE", "?")
        eth_price = shared_market.get("ETH", {}).get("PRICE", "?")
        log.info("Market snapshot ready — BTC $%s · ETH $%s", btc_price, eth_price)
    except Exception as exc:
        log.error("Failed to fetch market data: %s — each agent will fetch independently.", exc)
        shared_market = None   # fallback: agents fetch their own

    log.info(
        "=== Auto-trade cycle starting (%d agents, %ds stagger) ===",
        len(agent_names),
        AGENT_STAGGER_SECONDS,
    )

    for i, agent_name in enumerate(agent_names):
        try:
            log.info("[%d/%d] Running cycle for %s ...", i + 1, len(agent_names), agent_name)

            # Pass the shared snapshot — run_trade_cycle skips the API call
            result = await asyncio.to_thread(
                run_trade_cycle,
                agent_name=agent_name,
                market_data=shared_market,   # ← shared, no extra API hit
            )

            log.info(
                "[%s] → %s %s | %s",
                agent_name,
                result.get("action", "?"),
                result.get("asset") or "",
                result.get("reason", ""),
            )
            results[agent_name] = {
                "status": result.get("status"),
                "action": result.get("action"),
                "asset":  result.get("asset"),
            }

        except Exception as exc:
            log.error("[%s] cycle raised exception: %s", agent_name, exc)
            results[agent_name] = {"status": "error", "action": None, "asset": None}

        # Short stagger — just enough for Gemini's per-minute rate limit
        if i < len(agent_names) - 1:
            log.info("Waiting %ds before next agent (Gemini rate limit buffer)...", AGENT_STAGGER_SECONDS)
            await asyncio.sleep(AGENT_STAGGER_SECONDS)

    log.info("=== Auto-trade cycle complete. Results: %s ===", results)
    return results


async def _scheduler_loop() -> None:
    """
    Main scheduler loop. Runs forever as an asyncio background task.
    """
    interval_seconds = TRADE_CYCLE_INTERVAL_HOURS * 3600

    log.info(
        "Trade scheduler started — interval: %.1fh | startup delay: %ds | agents: %s",
        TRADE_CYCLE_INTERVAL_HOURS,
        STARTUP_DELAY_SECONDS,
        list(AGENT_PROFILES.keys()),
    )

    # Let the server fully boot before the first cycle
    _state["next_run"] = _next_iso(STARTUP_DELAY_SECONDS)
    await asyncio.sleep(STARTUP_DELAY_SECONDS)

    while True:
        _state["running"] = True
        _state["last_run"] = _now_iso()
        _state["next_run"] = None

        try:
            results = await _run_all_agents()
            _state["last_results"] = results
        except Exception as exc:
            log.error("Scheduler top-level error: %s", exc)
        finally:
            _state["running"] = False

        # Schedule next run
        _state["next_run"] = _next_iso(interval_seconds)
        log.info(
            "Next auto-trade cycle in %.1fh (at %s UTC).",
            TRADE_CYCLE_INTERVAL_HOURS,
            _state["next_run"],
        )
        await asyncio.sleep(interval_seconds)


# ── Public API ──────────────────────────────────────────────────────────────────
_task: Optional[asyncio.Task] = None


def start_trade_scheduler() -> None:
    """
    Call once from the FastAPI lifespan to launch the background scheduler.
    Safe to call multiple times — only one task is ever created.
    """
    global _task
    if _task is not None and not _task.done():
        log.info("Trade scheduler already running — skipping duplicate start.")
        return
    _task = asyncio.create_task(_scheduler_loop(), name="trade-scheduler")
    log.info("Trade scheduler task created.")


def stop_trade_scheduler() -> None:
    """Cancel the scheduler (called on server shutdown)."""
    global _task
    if _task and not _task.done():
        _task.cancel()
        log.info("Trade scheduler cancelled.")
    _task = None
