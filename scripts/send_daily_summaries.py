"""
send_daily_summaries.py — Entry point for the scheduled GitHub Actions daily-summary job.

Replaces the old in-process threading.Thread scheduler (backend/daily_summary.py's
start_daily_summary_scheduler), which died whenever Render slept the dyno — the
same class of bug Phase 1 fixed for trading. Run once daily via
.github/workflows/daily-summary.yml.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.daily_summary import send_daily_summary_reports
from backend.database import init_db
from backend.logger import get_logger

log = get_logger("send_daily_summaries")


def _is_live_mode() -> bool:
    return os.getenv("TRADE_DRY_RUN", "true").strip().lower() in {"0", "false", "no", "off"}


def run_all() -> int:
    if _is_live_mode():
        missing = [name for name in ("DATABASE_URL",) if not os.getenv(name)]
        if missing:
            print(f"FATAL: missing required env in live mode: {missing}")
            return 1

    init_db()
    results = send_daily_summary_reports()
    print(f"Sent {len(results)} daily summary report(s).")
    for r in results:
        print(f"  {r}")
    return 0


if __name__ == "__main__":
    sys.exit(run_all())
