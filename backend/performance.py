"""
performance.py — Simulated signal-following performance stats.

Computes a hypothetical single-stake return by walking an agent's own
BUY/SELL/HOLD trade_history in timestamp order, rather than reconciling
real on-chain amounts (which aren't reliably comparable between an
agent's BUY and SELL orders — see docs/superpowers/specs/2026-07-18-
phase2-real-performance-stats-design.md for why).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from backend.database import (
    get_followed_at,
    get_nav_snapshot_on_or_before,
    get_price_at_or_before,
    get_trade_rows_for_performance,
)
from backend.logger import get_logger
from backend.market_data import get_current_market_state

log = get_logger("performance")


def _walk_trade_rows(
    rows: list[dict],
    current_prices: dict | None = None,
    assume_open_position_price: float | None = None,
) -> dict:
    """
    Pure function: walk trade rows (each {"action","asset","price","timestamp"},
    already ordered ascending by timestamp) as a simulated single stake per asset.

    assume_open_position_price: if the very first row is a SELL (the agent already
    held a position when this window starts — e.g. a follower joining mid-position),
    treat it as if a BUY happened at this price. Ignored once any real BUY has
    already opened the walk.
    """
    multiplier = 1.0
    wins = 0
    closed_trades = 0
    total_trades = 0
    open_positions: dict[str, float] = {}  # asset -> entry price

    for i, row in enumerate(rows):
        action = row["action"]
        asset = row["asset"]
        price = row["price"]

        if action not in ("BUY", "SELL"):
            continue
        total_trades += 1

        if action == "BUY":
            if asset not in open_positions:
                open_positions[asset] = price
            # else: already holding — no-op (defensive)
        elif action == "SELL":
            if asset in open_positions:
                entry = open_positions.pop(asset)
                ratio = price / entry
                multiplier *= ratio
                closed_trades += 1
                if ratio > 1:
                    wins += 1
            elif i == 0 and assume_open_position_price is not None:
                entry = assume_open_position_price
                ratio = price / entry
                multiplier *= ratio
                closed_trades += 1
                if ratio > 1:
                    wins += 1
            # else: SELL with nothing held and no synthetic entry — no-op (defensive)

    has_open_position = len(open_positions) > 0
    if has_open_position and current_prices:
        for asset, entry in open_positions.items():
            live_price = (current_prices.get(asset) or {}).get("PRICE")
            if live_price:
                multiplier *= live_price / entry

    win_rate = round((wins / closed_trades) * 100, 1) if closed_trades else 0.0

    return {
        "multiplier": multiplier,
        "win_rate": win_rate,
        "closed_trades": closed_trades,
        "total_trades": total_trades,
        "has_open_position": has_open_position,
    }


_WINDOW_DAYS = {"24h": 1, "7d": 7, "1y": 365}


def _format_pct(pct: float) -> str:
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def _windows_from_snapshots(agent: str, live_multiplier: float, earliest_baseline: str | None = None) -> dict:
    windows: dict[str, str | None] = {}
    now = datetime.now(timezone.utc)
    for label, days in _WINDOW_DAYS.items():
        target_date = (now - timedelta(days=days)).date().isoformat()
        snap = get_nav_snapshot_on_or_before(agent, target_date)
        if not snap:
            windows[label] = None
            continue
        if earliest_baseline and snap["snapshot_date"] < earliest_baseline[:10]:
            # Follower joined after this snapshot — no meaningful window that far back.
            windows[label] = None
            continue
        baseline = snap["nav_multiplier"]
        if not baseline:
            windows[label] = None
            continue
        windows[label] = _format_pct(((live_multiplier / baseline) - 1) * 100)
    return windows


def get_agent_performance(agent_name: str, current_prices: dict | None = None) -> dict:
    """current_prices: pass a pre-fetched get_current_market_state() result when
    computing this for multiple agents in a loop, to avoid redundant external
    API calls (each is several real CoinGecko/Yahoo requests)."""
    rows = get_trade_rows_for_performance(agent_name)
    if current_prices is None:
        current_prices = get_current_market_state()
    walk = _walk_trade_rows(rows, current_prices=current_prices)
    windows = _windows_from_snapshots(agent_name, walk["multiplier"])
    return {
        "win_rate": walk["win_rate"],
        "total_trades": walk["total_trades"],
        "closed_trades": walk["closed_trades"],
        "has_open_position": walk["has_open_position"],
        **windows,
    }


def get_follower_performance(user_id: str, agent_name: str, current_prices: dict | None = None) -> dict:
    """current_prices: see get_agent_performance."""
    followed_at = get_followed_at(user_id, agent_name)
    if not followed_at:
        return {
            "win_rate": 0.0, "total_trades": 0, "closed_trades": 0,
            "has_open_position": False, "24h": None, "7d": None, "1y": None,
        }

    rows = get_trade_rows_for_performance(agent_name, since=followed_at)
    synthetic_entry = None
    if rows and rows[0]["action"] == "SELL":
        synthetic_entry = get_price_at_or_before(agent_name, followed_at)

    if current_prices is None:
        current_prices = get_current_market_state()
    walk = _walk_trade_rows(rows, current_prices=current_prices, assume_open_position_price=synthetic_entry)
    windows = _windows_from_snapshots(agent_name, walk["multiplier"], earliest_baseline=followed_at)
    return {
        "win_rate": walk["win_rate"],
        "total_trades": walk["total_trades"],
        "closed_trades": walk["closed_trades"],
        "has_open_position": walk["has_open_position"],
        **windows,
    }
