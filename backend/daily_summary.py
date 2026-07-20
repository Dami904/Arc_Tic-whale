from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backend.database import get_all_users, get_follower_trade_history, get_user_allocations
from backend.logger import get_logger
from backend.notifications import notify_daily_summary, build_notification_reminder, user_has_notification_channel
from backend.performance import get_follower_performance
from backend.wallet_summary import get_wallet_stats_safe

log = get_logger("daily_summary")

LAGOS_TZ = ZoneInfo("Africa/Lagos")


def _current_lagos_time() -> datetime:
    return datetime.now(LAGOS_TZ)


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
        if allocations:
            primary_follow = max(allocations, key=lambda row: float(row.get("allocation_amount") or 0.0))
            performance = get_follower_performance(user["user_id"], primary_follow["target_agent"])
        else:
            performance = {}
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
