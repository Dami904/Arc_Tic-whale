"""
snapshot_nav.py - Daily NAV snapshot for each agent's simulated performance.

Records today's cumulative return multiplier per agent into agent_nav_snapshots,
so backend/performance.py can compute real 24h/7d/1y windows. Run once daily via
.github/workflows/nav-snapshot.yml. Idempotent - safe to rerun same-day (upsert).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.agents import AGENT_PROFILES
from backend.database import get_trade_rows_for_performance, init_db, upsert_nav_snapshot
from backend.logger import get_logger
from backend.market_data import get_current_market_state
from backend.performance import _walk_trade_rows

log = get_logger("snapshot_nav")


def _is_live_mode() -> bool:
    return os.getenv("TRADE_DRY_RUN", "true").strip().lower() in {"0", "false", "no", "off"}


def run_all() -> int:
    if _is_live_mode():
        missing = [name for name in ("DATABASE_URL",) if not os.getenv(name)]
        if missing:
            print(f"FATAL: missing required env in live mode: {missing}")
            return 1

    init_db()
    today = datetime.now(timezone.utc).date().isoformat()
    market = get_current_market_state()

    failures = 0
    for agent in AGENT_PROFILES:
        try:
            rows = get_trade_rows_for_performance(agent)
            walk = _walk_trade_rows(rows, current_prices=market)
            upsert_nav_snapshot(agent, today, walk["multiplier"])
            print(f"{agent}: snapshot {today} multiplier={walk['multiplier']:.4f}")
        except Exception as exc:
            failures += 1
            log.error("Snapshot failed for %s: %s", agent, exc)
            print(f"{agent}: FAILED - {exc}")

    return 1 if failures == len(AGENT_PROFILES) else 0


if __name__ == "__main__":
    sys.exit(run_all())
