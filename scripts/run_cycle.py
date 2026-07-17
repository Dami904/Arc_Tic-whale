"""
run_cycle.py — Entry point for the scheduled GitHub Actions trade cycle.

Runs one trade cycle per agent in AGENT_PROFILES against the configured
DATABASE_URL (Neon Postgres in production). Market data is fetched once
and shared. Exits 0 if at least one agent completed (or kill switch is on),
1 if every agent errored — so the Actions run shows red only on total failure.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.agents import AGENT_PROFILES
from backend.database import init_db, is_kill_switch_active
from backend.logger import get_logger
from backend.market_data import get_current_market_state
from backend.trade_service import run_trade_cycle

log = get_logger("run_cycle")

STAGGER_SECONDS = 30  # between agents, for Gemini rate limits


def _is_live_mode() -> bool:
    return os.getenv("TRADE_DRY_RUN", "true").strip().lower() in {"0", "false", "no", "off"}


def run_all() -> int:
    if _is_live_mode():
        missing = [
            name for name in (
                "DATABASE_URL", "GOOGLE_API_KEY", "CIRCLE_API_KEY",
                "CIRCLE_ENTITY_SECRET", "AGENT_WALLET_ID", "AGENT_WALLET_ADDRESS",
            ) if not os.getenv(name)
        ]
        if missing:
            log.error("FATAL: missing required env: %s", missing)
            print(f"FATAL: missing required env: {missing}")
            return 1

    init_db()
    if is_kill_switch_active():
        log.warning("Kill switch active — skipping this cycle.")
        print("KILL SWITCH ACTIVE — cycle skipped.")
        return 0

    market = get_current_market_state()
    results: dict[str, dict] = {}
    agents = list(AGENT_PROFILES.keys())
    for i, agent in enumerate(agents):
        try:
            results[agent] = run_trade_cycle(
                agent_name=agent, market_data=market, rate_limit_sleep=0
            )
        except Exception as exc:
            log.error("Agent %s crashed: %s", agent, exc)
            results[agent] = {"status": "error", "action": None, "asset": None,
                              "tx_hash": None, "reason": str(exc)}
        if i < len(agents) - 1 and STAGGER_SECONDS:
            time.sleep(STAGGER_SECONDS)

    print(f"\n{'AGENT':<22} {'STATUS':<9} {'ACTION':<6} {'ASSET':<6} REASON")
    for agent, r in results.items():
        print(f"{agent:<22} {r['status']:<9} {str(r['action']):<6} "
              f"{str(r['asset']):<6} {str(r.get('reason') or '')[:80]}")

    all_failed = all(r["status"] == "error" for r in results.values())
    return 1 if all_failed else 0


if __name__ == "__main__":
    sys.exit(run_all())
