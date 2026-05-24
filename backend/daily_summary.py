from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from backend.database import get_all_users, get_follower_trade_history, get_user_allocations
from backend.logger import get_logger
from backend.notifications import notify_daily_summary, build_notification_reminder, user_has_notification_channel
from backend.wallet_summary import get_wallet_stats_safe

log = get_logger("daily_summary")

LAGOS_TZ = ZoneInfo("Africa/Lagos")
_scheduler_started = False


def _current_lagos_time() -> datetime:
    return datetime.now(LAGOS_TZ)


def _next_run_time(now: datetime | None = None) -> datetime:
    now = now or _current_lagos_time()
    target = now.replace(hour=20, minute=0, second=0, microsecond=0)
    if now >= target:
        target = target + timedelta(days=1)
    return target


def _user_trade_count_today(user_id: str, wallet_id: str, now: datetime | None = None) -> int:
    now = now or _current_lagos_time()
    date_prefix = now.date().isoformat()
    trades = get_follower_trade_history(wallet_id, limit=200, actions={"BUY", "SELL"})
    return sum(1 for trade in trades if str(trade.get("timestamp", "")).startswith(date_prefix))


def send_daily_summary_reports(now: datetime | None = None) -> list[dict]:
    now = now or _current_lagos_time()
    results: list[dict] = []
    for user in get_all_users():
        if int(user.get("daily_summary") or 0) != 1:
            continue

        allocations = get_user_allocations(user["user_id"])
        active_agents = len(allocations)
        wallet_stats = get_wallet_stats_safe(user.get("wallet_id"))
        total_balance = float(wallet_stats.get("total_balance_usd") or 0)
        performance = wallet_stats.get("performance") or {}
        pct_value = str(performance.get("24h") or "0").replace("%", "")
        try:
            pct_float = float(pct_value)
        except ValueError:
            pct_float = 0.0
        pnl_amount = round(total_balance * (pct_float / 100.0), 2)
        free_usdc = max(0.0, total_balance - sum(float(row.get("allocation_amount") or 0) for row in allocations))
        trades_today = _user_trade_count_today(user["user_id"], user["wallet_id"], now=now)

        if not user_has_notification_channel(user):
            reminder = build_notification_reminder(user)
            if reminder:
                log.info("Daily summary reminder for %s: %s", user["user_id"], reminder)
            continue

        result = notify_daily_summary(
            user,
            date_label=now.strftime("%Y-%m-%d"),
            trades_executed=trades_today,
            pnl_amount=pnl_amount,
            pnl_pct=pct_float,
            free_usdc=free_usdc,
            active_agents=active_agents,
        )
        results.append({"user_id": user["user_id"], **result})
    return results


def _summary_loop():
    while True:
        now = _current_lagos_time()
        next_run = _next_run_time(now)
        sleep_for = max(1, int((next_run - now).total_seconds()))
        time.sleep(sleep_for)
        try:
            send_daily_summary_reports()
        except Exception as exc:
            log.exception("Daily summary run failed: %s", exc)


def start_daily_summary_scheduler():
    global _scheduler_started
    if _scheduler_started:
        return
    if os.getenv("RUN_DAILY_SUMMARY_SCHEDULER", "1").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    _scheduler_started = True
    thread = threading.Thread(target=_summary_loop, name="daily-summary-scheduler", daemon=True)
    thread.start()
    log.info("Daily summary scheduler started for 20:00 Africa/Lagos.")
