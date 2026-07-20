# Phase 2 Real Performance Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fake "win rate" (execution-success rate) and fake "performance" (global ETH price change) numbers with genuine simulated-signal P&L/win-rate stats, at both agent and follower level, and make the daily-summary notification scheduler survive Render sleep.

**Architecture:** A new pure computation engine (`backend/performance.py`) walks an agent's own `trade_history` rows (price now captured at signal time) as a simulated single stake, producing a cumulative return multiplier and win rate. Agent-level and follower-level stats are both thin wrappers around this engine, differing only in the `since` timestamp and window baselines. A new daily GitHub Actions job snapshots each agent's live multiplier so real 24h/7d/1y windows become computable — mirroring the Phase 1 trade-cycle pattern exactly. The same pattern retires the in-process daily-summary thread scheduler.

**Tech Stack:** Python 3.12, FastAPI, pytest, SQLite locally / Postgres (Neon) in prod, GitHub Actions cron.

**Spec:** `docs/superpowers/specs/2026-07-18-phase2-real-performance-stats-design.md`

**Conventions for every task:**
- Run tests with `venv/bin/python -m pytest ...` from repo root.
- Tests that touch the DB must monkeypatch `backend.database.DB_NAME` to a tmp path AND `backend.database._USE_PG = False` / `backend.database._PH = "?"` (see `tests/test_sessions.py`'s `tmp_db` fixture for the exact pattern — `_USE_PG`/`_PH` are module globals fixed at import time from `DATABASE_URL`, so patching `DB_NAME` alone is not sufficient if `DATABASE_URL` happens to be set in the environment).
- Commit after each task with the message given in the task.
- Branch: continue on `phase1-production-foundation` (already checked out) — Phase 1 is implemented but unmerged, and this phase depends on it (session-token auth, GitHub Actions pattern). Do not create a new branch.

---

### Task 1: Schema — `price` column, `followed_at` column, `agent_nav_snapshots` table, DB helpers

**Files:**
- Modify: `backend/database.py`
- Test: `tests/test_performance_db.py` (create)

- [ ] **Step 1: Write failing tests**

Create `tests/test_performance_db.py`:

```python
import pytest

import backend.database as db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_USE_PG", False)
    monkeypatch.setattr(db, "_PH", "?")
    monkeypatch.setattr(db, "DB_NAME", str(tmp_path / "test.db"))
    db.init_db()
    yield db


class TestPriceColumn:
    def test_log_trade_stores_price(self, tmp_db):
        tmp_db.log_trade(agent="A", action="BUY", asset="BTC", tx_id="tx1", reason="r", price=68000.0)
        rows = tmp_db.get_trade_rows_for_performance("A")
        assert rows[0]["price"] == 68000.0

    def test_log_trade_price_defaults_to_none(self, tmp_db):
        tmp_db.log_trade(agent="A", action="HOLD", asset="BTC", tx_id=None, reason="r")
        rows = tmp_db.get_trade_history(limit=1, agent="A")
        assert rows[0]["tx_id"] is None  # sanity: existing call shape still works


class TestTradeRowsForPerformance:
    def test_orders_ascending_and_excludes_null_price(self, tmp_db):
        tmp_db.log_trade(agent="A", action="BUY", asset="BTC", tx_id="t2", reason="", price=100.0)
        tmp_db.log_trade(agent="A", action="HOLD", asset="BTC", tx_id=None, reason="")  # no price
        tmp_db.log_trade(agent="A", action="SELL", asset="BTC", tx_id="t3", reason="", price=110.0)
        rows = tmp_db.get_trade_rows_for_performance("A")
        assert [r["price"] for r in rows] == [100.0, 110.0]
        assert rows[0]["timestamp"] <= rows[1]["timestamp"]

    def test_excludes_other_agents(self, tmp_db):
        tmp_db.log_trade(agent="A", action="BUY", asset="BTC", tx_id="t1", reason="", price=100.0)
        tmp_db.log_trade(agent="Follower:wallet1:A", action="BUY", asset="BTC", tx_id="t2", reason="", price=100.0)
        rows = tmp_db.get_trade_rows_for_performance("A")
        assert len(rows) == 1

    def test_since_filters_by_timestamp(self, tmp_db):
        tmp_db.log_trade(agent="A", action="BUY", asset="BTC", tx_id="t1", reason="", price=100.0)
        import time; time.sleep(0.01)
        from datetime import datetime, timezone
        cutoff = datetime.now(timezone.utc).isoformat()
        time.sleep(0.01)
        tmp_db.log_trade(agent="A", action="SELL", asset="BTC", tx_id="t2", reason="", price=110.0)
        rows = tmp_db.get_trade_rows_for_performance("A", since=cutoff)
        assert len(rows) == 1
        assert rows[0]["price"] == 110.0


class TestPriceAtOrBefore:
    def test_finds_most_recent_price_before_cutoff(self, tmp_db):
        tmp_db.log_trade(agent="A", action="BUY", asset="BTC", tx_id="t1", reason="", price=100.0)
        import time; time.sleep(0.01)
        from datetime import datetime, timezone
        cutoff = datetime.now(timezone.utc).isoformat()
        time.sleep(0.01)
        tmp_db.log_trade(agent="A", action="SELL", asset="BTC", tx_id="t2", reason="", price=110.0)
        assert tmp_db.get_price_at_or_before("A", cutoff) == 100.0

    def test_returns_none_when_nothing_before(self, tmp_db):
        from datetime import datetime, timezone
        past = "2000-01-01T00:00:00+00:00"
        tmp_db.log_trade(agent="A", action="BUY", asset="BTC", tx_id="t1", reason="", price=100.0)
        assert tmp_db.get_price_at_or_before("A", past) is None


class TestFollowedAt:
    def test_add_follower_sets_followed_at(self, tmp_db):
        tmp_db.add_follower(user_id="u1", user_wallet_id="w1", target_agent="A", allocation_amount=1.0)
        assert tmp_db.get_followed_at("u1", "A") is not None

    def test_returns_none_when_not_following(self, tmp_db):
        assert tmp_db.get_followed_at("u1", "A") is None

    def test_returns_none_after_unfollow(self, tmp_db):
        tmp_db.add_follower(user_id="u1", user_wallet_id="w1", target_agent="A", allocation_amount=1.0)
        tmp_db.deactivate_follower("u1", "A")
        assert tmp_db.get_followed_at("u1", "A") is None


class TestNavSnapshots:
    def test_upsert_then_read(self, tmp_db):
        tmp_db.upsert_nav_snapshot("A", "2026-07-18", 1.05)
        snap = tmp_db.get_nav_snapshot_on_or_before("A", "2026-07-18")
        assert snap["nav_multiplier"] == 1.05

    def test_upsert_same_day_replaces(self, tmp_db):
        tmp_db.upsert_nav_snapshot("A", "2026-07-18", 1.05)
        tmp_db.upsert_nav_snapshot("A", "2026-07-18", 1.10)
        snap = tmp_db.get_nav_snapshot_on_or_before("A", "2026-07-18")
        assert snap["nav_multiplier"] == 1.10

    def test_nearest_on_or_before_target_date(self, tmp_db):
        tmp_db.upsert_nav_snapshot("A", "2026-07-10", 1.02)
        tmp_db.upsert_nav_snapshot("A", "2026-07-15", 1.08)
        snap = tmp_db.get_nav_snapshot_on_or_before("A", "2026-07-17")
        assert snap["nav_multiplier"] == 1.08  # nearest <= target, not the closest overall

    def test_returns_none_when_no_snapshot_exists(self, tmp_db):
        assert tmp_db.get_nav_snapshot_on_or_before("A", "2026-07-18") is None
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `venv/bin/python -m pytest tests/test_performance_db.py -v`
Expected: FAIL — `log_trade() got an unexpected keyword argument 'price'`, `AttributeError` for the new functions.

- [ ] **Step 3: Add `price` column to `trade_history` DDL (both branches)**

In `backend/database.py`, find the `trade_history` `CREATE TABLE` in the Postgres branch of `init_db()` and add a column:

```python
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS trade_history (
                    id SERIAL PRIMARY KEY,
                    agent TEXT NOT NULL,
                    action TEXT NOT NULL,
                    asset TEXT,
                    tx_id TEXT,
                    reason TEXT,
                    timestamp TEXT NOT NULL
                )
            ''')
            cursor.execute("ALTER TABLE trade_history ADD COLUMN IF NOT EXISTS price DOUBLE PRECISION")
```

Find the SQLite `trade_history` `CREATE TABLE` block and add, immediately after it (following the existing `PRAGMA table_info` pattern used elsewhere in the SQLite branch for other tables):

```python
            cursor.execute("PRAGMA table_info(trade_history)")
            trade_history_columns = {row[1] for row in cursor.fetchall()}
            if "price" not in trade_history_columns:
                cursor.execute("ALTER TABLE trade_history ADD COLUMN price REAL")
```

- [ ] **Step 4: Add `followed_at` column to `followers` DDL (both branches)**

Postgres branch, right after the existing `followers` `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` lines:

```python
            cursor.execute("ALTER TABLE followers ADD COLUMN IF NOT EXISTS followed_at TEXT")
```

SQLite branch, in the existing `PRAGMA table_info(followers)` / `follower_columns` block, add:

```python
            if "followed_at" not in follower_columns:
                cursor.execute("ALTER TABLE followers ADD COLUMN followed_at TEXT")
```

- [ ] **Step 5: Add `agent_nav_snapshots` table DDL (both branches)**

Postgres branch, alongside the other table creations (near `sessions`/`auth_nonces` from Phase 1):

```python
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agent_nav_snapshots (
                    id SERIAL PRIMARY KEY,
                    agent TEXT NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    nav_multiplier DOUBLE PRECISION NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (agent, snapshot_date)
                )
            ''')
```

SQLite branch:

```python
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS agent_nav_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent TEXT NOT NULL,
                    snapshot_date TEXT NOT NULL,
                    nav_multiplier REAL NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (agent, snapshot_date)
                )
            ''')
```

- [ ] **Step 6: Update `log_trade` to accept `price`**

Find `log_trade` (currently `def log_trade(agent: str, action: str, asset: str | None, tx_id: str | None, reason: str = ""):`) and replace with:

```python
def log_trade(agent: str, action: str, asset: str | None, tx_id: str | None, reason: str = "", price: float | None = None):
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"INSERT INTO trade_history (agent, action, asset, tx_id, reason, timestamp, price) VALUES ({_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH})",
            (agent, action, asset, tx_id, reason, datetime.now(timezone.utc).isoformat(), price),
        )
        conn.commit()
```

- [ ] **Step 7: Update `add_follower` to set `followed_at`**

Replace the `add_follower` INSERT to include `followed_at`:

```python
def add_follower(
    user_id,
    user_wallet_id,
    target_agent,
    allocation_amount,
    asset=None,
    user_wallet_address=None,
    stop_loss_pct: float | None = 10.0,
):
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"INSERT INTO followers (user_id, user_wallet_id, user_wallet_address, target_agent, allocation_amount, asset, stop_loss_pct, followed_at) VALUES ({_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH}, {_PH})",
            (user_id, user_wallet_id, user_wallet_address, target_agent, allocation_amount, asset,
             stop_loss_pct if stop_loss_pct is not None else 10.0, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    log.info("User %s is now following %s.", user_id, target_agent)
```

- [ ] **Step 8: Add new query functions**

Add these at the end of `backend/database.py`:

```python
# ── Performance stats support ───────────────────────────────────────────────

def get_trade_rows_for_performance(agent: str, since: str | None = None) -> list[dict]:
    with _connection() as conn:
        cursor = _cursor(conn)
        if since:
            cursor.execute(
                f"SELECT action, asset, price, timestamp FROM trade_history "
                f"WHERE agent = {_PH} AND price IS NOT NULL AND timestamp >= {_PH} ORDER BY timestamp ASC",
                (agent, since),
            )
        else:
            cursor.execute(
                f"SELECT action, asset, price, timestamp FROM trade_history "
                f"WHERE agent = {_PH} AND price IS NOT NULL ORDER BY timestamp ASC",
                (agent,),
            )
        return _rows(cursor)


def get_price_at_or_before(agent: str, before_timestamp: str) -> float | None:
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"SELECT price FROM trade_history WHERE agent = {_PH} AND price IS NOT NULL "
            f"AND timestamp <= {_PH} ORDER BY timestamp DESC LIMIT 1",
            (agent, before_timestamp),
        )
        row = _row(cursor)
    return row["price"] if row else None


def get_followed_at(user_id: str, target_agent: str) -> str | None:
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"SELECT followed_at FROM followers WHERE user_id = {_PH} AND target_agent = {_PH} AND is_active = 1",
            (user_id, target_agent),
        )
        row = _row(cursor)
    return row["followed_at"] if row else None


def upsert_nav_snapshot(agent: str, snapshot_date: str, nav_multiplier: float) -> None:
    with _connection() as conn:
        cursor = _cursor(conn)
        if _USE_PG:
            cursor.execute(
                "INSERT INTO agent_nav_snapshots (agent, snapshot_date, nav_multiplier, created_at) "
                "VALUES (%s, %s, %s, %s) ON CONFLICT (agent, snapshot_date) "
                "DO UPDATE SET nav_multiplier = EXCLUDED.nav_multiplier, created_at = EXCLUDED.created_at",
                (agent, snapshot_date, nav_multiplier, datetime.now(timezone.utc).isoformat()),
            )
        else:
            cursor.execute(
                "INSERT INTO agent_nav_snapshots (agent, snapshot_date, nav_multiplier, created_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT (agent, snapshot_date) "
                "DO UPDATE SET nav_multiplier = excluded.nav_multiplier, created_at = excluded.created_at",
                (agent, snapshot_date, nav_multiplier, datetime.now(timezone.utc).isoformat()),
            )
        conn.commit()


def get_nav_snapshot_on_or_before(agent: str, target_date: str) -> dict | None:
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"SELECT nav_multiplier, snapshot_date FROM agent_nav_snapshots "
            f"WHERE agent = {_PH} AND snapshot_date <= {_PH} ORDER BY snapshot_date DESC LIMIT 1",
            (agent, target_date),
        )
        return _row(cursor)
```

- [ ] **Step 9: Run tests, verify pass**

Run: `venv/bin/python -m pytest tests/test_performance_db.py -v`
Expected: 13 passed.

- [ ] **Step 10: Run full suite, commit**

Run: `venv/bin/python -m pytest -q` — expected: all pass (existing 73 + 13 new = 86; run_cycle guard test etc. already counted in the 73).

```bash
git add backend/database.py tests/test_performance_db.py
git commit -m "feat: add price/followed_at columns, agent_nav_snapshots table, performance DB helpers"
```

---

### Task 2: Pure walk engine — `backend/performance.py`

**Files:**
- Create: `backend/performance.py`
- Test: `tests/test_performance_engine.py` (create)

- [ ] **Step 1: Write failing tests**

Create `tests/test_performance_engine.py`:

```python
from backend.performance import _walk_trade_rows


def _row(action, asset, price, ts):
    return {"action": action, "asset": asset, "price": price, "timestamp": ts}


class TestClosedRoundTrips:
    def test_single_profitable_round_trip(self):
        rows = [_row("BUY", "BTC", 100.0, "t1"), _row("SELL", "BTC", 110.0, "t2")]
        result = _walk_trade_rows(rows)
        assert result["closed_trades"] == 1
        assert result["win_rate"] == 100.0
        assert round(result["multiplier"], 4) == 1.10
        assert result["has_open_position"] is False

    def test_single_losing_round_trip(self):
        rows = [_row("BUY", "BTC", 100.0, "t1"), _row("SELL", "BTC", 90.0, "t2")]
        result = _walk_trade_rows(rows)
        assert result["win_rate"] == 0.0
        assert round(result["multiplier"], 4) == 0.90

    def test_multiple_round_trips_compound(self):
        rows = [
            _row("BUY", "BTC", 100.0, "t1"), _row("SELL", "BTC", 110.0, "t2"),
            _row("BUY", "ETH", 10.0, "t3"), _row("SELL", "ETH", 9.0, "t4"),
        ]
        result = _walk_trade_rows(rows)
        assert result["closed_trades"] == 2
        assert result["win_rate"] == 50.0
        assert round(result["multiplier"], 4) == round(1.10 * 0.90, 4)

    def test_hold_rows_are_noops(self):
        rows = [_row("BUY", "BTC", 100.0, "t1"), _row("HOLD", "BTC", 105.0, "t2"), _row("SELL", "BTC", 110.0, "t3")]
        result = _walk_trade_rows(rows)
        assert result["closed_trades"] == 1
        assert round(result["multiplier"], 4) == 1.10


class TestOpenPosition:
    def test_open_position_excluded_without_current_prices(self):
        rows = [_row("BUY", "BTC", 100.0, "t1")]
        result = _walk_trade_rows(rows)
        assert result["has_open_position"] is True
        assert result["closed_trades"] == 0
        assert result["multiplier"] == 1.0

    def test_open_position_marked_to_current_price(self):
        rows = [_row("BUY", "BTC", 100.0, "t1")]
        result = _walk_trade_rows(rows, current_prices={"BTC": {"PRICE": 120.0}})
        assert result["has_open_position"] is True
        assert round(result["multiplier"], 4) == 1.20


class TestDefensiveNoops:
    def test_buy_while_already_holding_is_noop(self):
        rows = [_row("BUY", "BTC", 100.0, "t1"), _row("BUY", "BTC", 200.0, "t2"), _row("SELL", "BTC", 150.0, "t3")]
        result = _walk_trade_rows(rows)
        assert result["closed_trades"] == 1
        assert round(result["multiplier"], 4) == 1.50  # closed against the FIRST buy price

    def test_sell_while_not_holding_is_noop_without_synthetic_entry(self):
        rows = [_row("SELL", "BTC", 100.0, "t1")]
        result = _walk_trade_rows(rows)
        assert result["closed_trades"] == 0
        assert result["multiplier"] == 1.0


class TestSyntheticEntry:
    def test_leading_sell_uses_synthetic_entry_price_when_provided(self):
        rows = [_row("SELL", "BTC", 110.0, "t1")]
        result = _walk_trade_rows(rows, assume_open_position_price=100.0)
        assert result["closed_trades"] == 1
        assert round(result["multiplier"], 4) == 1.10
        assert result["win_rate"] == 100.0

    def test_synthetic_entry_only_applies_to_leading_sell(self):
        rows = [_row("BUY", "BTC", 50.0, "t0"), _row("SELL", "BTC", 55.0, "t1")]
        # assume_open_position_price should be ignored once a real BUY has already occurred first
        result = _walk_trade_rows(rows, assume_open_position_price=100.0)
        assert round(result["multiplier"], 4) == 1.10  # 55/50, not 55/100


class TestTotalTrades:
    def test_total_trades_counts_buy_and_sell_rows(self):
        rows = [_row("BUY", "BTC", 100.0, "t1"), _row("HOLD", "BTC", 105.0, "t2"), _row("SELL", "BTC", 110.0, "t3")]
        result = _walk_trade_rows(rows)
        assert result["total_trades"] == 2


class TestEmptyInput:
    def test_no_rows(self):
        result = _walk_trade_rows([])
        assert result == {
            "multiplier": 1.0, "win_rate": 0.0, "closed_trades": 0,
            "total_trades": 0, "has_open_position": False,
        }
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `venv/bin/python -m pytest tests/test_performance_engine.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.performance'`

- [ ] **Step 3: Implement `backend/performance.py` (walk engine only for this task)**

```python
"""
performance.py — Simulated signal-following performance stats.

Computes a hypothetical single-stake return by walking an agent's own
BUY/SELL/HOLD trade_history in timestamp order, rather than reconciling
real on-chain amounts (which aren't reliably comparable between an
agent's BUY and SELL orders — see docs/superpowers/specs/2026-07-18-
phase2-real-performance-stats-design.md for why).
"""
from __future__ import annotations

from backend.logger import get_logger

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
```

- [ ] **Step 4: Run tests, verify pass**

Run: `venv/bin/python -m pytest tests/test_performance_engine.py -v`
Expected: 11 passed.

- [ ] **Step 5: Run full suite, commit**

Run: `venv/bin/python -m pytest -q` — expected: all pass.

```bash
git add backend/performance.py tests/test_performance_engine.py
git commit -m "feat: add pure simulated-signal walk engine for performance stats"
```

---

### Task 3: Agent-level and follower-level performance wrappers

**Files:**
- Modify: `backend/performance.py`
- Test: `tests/test_performance_engine.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_performance_engine.py`:

```python
from unittest.mock import patch

from backend.performance import get_agent_performance, get_follower_performance


class TestGetAgentPerformance:
    @patch("backend.performance.get_current_market_state", return_value={"BTC": {"PRICE": 120.0}})
    @patch("backend.performance.get_nav_snapshot_on_or_before")
    @patch("backend.performance.get_trade_rows_for_performance")
    def test_all_time_stats_from_rows(self, mock_rows, mock_snap, mock_market):
        mock_rows.return_value = [
            {"action": "BUY", "asset": "BTC", "price": 100.0, "timestamp": "t1"},
            {"action": "SELL", "asset": "BTC", "price": 110.0, "timestamp": "t2"},
        ]
        mock_snap.return_value = None
        result = get_agent_performance("Conservative_Whale")
        assert result["win_rate"] == 100.0
        assert result["closed_trades"] == 1
        assert result["total_trades"] == 2
        assert result["24h"] is None  # no snapshot yet
        assert result["7d"] is None
        assert result["1y"] is None

    @patch("backend.performance.get_current_market_state", return_value={"BTC": {"PRICE": 120.0}})
    @patch("backend.performance.get_nav_snapshot_on_or_before")
    @patch("backend.performance.get_trade_rows_for_performance")
    def test_window_computed_from_snapshot(self, mock_rows, mock_snap, mock_market):
        mock_rows.return_value = [
            {"action": "BUY", "asset": "BTC", "price": 100.0, "timestamp": "t1"},
            {"action": "SELL", "asset": "BTC", "price": 121.0, "timestamp": "t2"},
        ]
        mock_snap.return_value = {"nav_multiplier": 1.10, "snapshot_date": "2026-07-11"}
        result = get_agent_performance("Conservative_Whale")
        # live multiplier 1.21 vs snapshot 1.10 => +10.00%
        assert result["24h"] == "+10.00%"


class TestGetFollowerPerformance:
    @patch("backend.performance.get_current_market_state", return_value={"BTC": {"PRICE": 100.0}})
    @patch("backend.performance.get_price_at_or_before", return_value=None)
    @patch("backend.performance.get_nav_snapshot_on_or_before", return_value=None)
    @patch("backend.performance.get_trade_rows_for_performance")
    @patch("backend.performance.get_followed_at", return_value="2026-07-15T00:00:00+00:00")
    def test_uses_followed_at_as_since(self, mock_followed, mock_rows, mock_snap, mock_price, mock_market):
        mock_rows.return_value = [
            {"action": "BUY", "asset": "BTC", "price": 90.0, "timestamp": "2026-07-16T00:00:00+00:00"},
        ]
        result = get_follower_performance("user1", "Conservative_Whale")
        mock_rows.assert_called_once_with("Conservative_Whale", since="2026-07-15T00:00:00+00:00")
        assert result["has_open_position"] is True

    def test_not_following_returns_none_stats(self):
        with patch("backend.performance.get_followed_at", return_value=None):
            result = get_follower_performance("user1", "Conservative_Whale")
        assert result["closed_trades"] == 0
        assert result["24h"] is None
        assert result["win_rate"] == 0.0
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `venv/bin/python -m pytest tests/test_performance_engine.py -v -k "AgentPerformance or FollowerPerformance"`
Expected: FAIL — `ImportError: cannot import name 'get_agent_performance'`

- [ ] **Step 3: Add wrapper functions to `backend/performance.py`**

Add imports at the top (below the existing `from backend.logger import get_logger`):

```python
from datetime import datetime, timedelta, timezone

from backend.database import (
    get_followed_at,
    get_nav_snapshot_on_or_before,
    get_price_at_or_before,
    get_trade_rows_for_performance,
)
from backend.market_data import get_current_market_state
```

Add at the end of the file:

```python
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


def get_agent_performance(agent_name: str) -> dict:
    rows = get_trade_rows_for_performance(agent_name)
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


def get_follower_performance(user_id: str, agent_name: str) -> dict:
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
```

- [ ] **Step 4: Run tests, verify pass**

Run: `venv/bin/python -m pytest tests/test_performance_engine.py -v`
Expected: 15 passed (11 from Task 2 + 4 new).

- [ ] **Step 5: Run full suite, commit**

Run: `venv/bin/python -m pytest -q` — expected: all pass.

```bash
git add backend/performance.py tests/test_performance_engine.py
git commit -m "feat: add agent-level and follower-level performance wrappers with windowed returns"
```

---

### Task 4: Capture price at trade time in `run_trade_cycle`

**Files:**
- Modify: `backend/trade_service.py`
- Test: `tests/test_trade_service.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_trade_service.py`, inside `class TestTradeDecisionFlow`:

```python
    def test_hold_logs_price_from_market_data(self, mock_trade_cycle):
        mock_trade_cycle["market"].return_value = {"BTC": {"PRICE": 68000}}
        mock_trade_cycle["ai"].return_value = "DECISION: HOLD\nREASON: No clear signal."
        mock_trade_cycle["parse"].return_value = {"decision": "HOLD", "asset": "BTC", "reason": "No clear signal."}

        from backend.trade_service import run_trade_cycle
        run_trade_cycle(wallet_id="test-wallet", rate_limit_sleep=0)

        mock_trade_cycle["log"].assert_called_once_with(
            agent="Conservative_Whale", action="HOLD", asset="BTC", tx_id=None,
            reason="No clear signal.", price=68000,
        )

    def test_buy_logs_price_from_market_data(self, mock_trade_cycle):
        mock_trade_cycle["market"].return_value = {"BTC": {"PRICE": 68000}}
        mock_trade_cycle["ai"].return_value = "DECISION: BUY BTC\nREASON: Dip."
        mock_trade_cycle["parse"].return_value = {"decision": "BUY", "asset": "BTC", "reason": "Dip."}
        mock_trade_cycle["exec"].return_value = "0xdeadbeef"

        from backend.trade_service import run_trade_cycle
        run_trade_cycle(wallet_id="test-wallet", rate_limit_sleep=0)

        mock_trade_cycle["log"].assert_called_once_with(
            agent="Conservative_Whale", action="BUY", asset="BTC", tx_id="0xdeadbeef",
            reason="Dip.", price=68000,
        )
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `venv/bin/python -m pytest tests/test_trade_service.py -v -k "logs_price"`
Expected: FAIL — `log_trade` called without a `price` kwarg (assert_called_once_with mismatch).

- [ ] **Step 3: Update `run_trade_cycle` in `backend/trade_service.py`**

Replace the HOLD-path `log_trade` call:

```python
    if action == "HOLD":
        log.info("Action: HOLD. No on-chain transaction required.")
        hold_price = current_data.get(asset, {}).get("PRICE") if asset else None
        log_trade(agent=agent_name, action="HOLD", asset=asset, tx_id=None, reason=reason, price=hold_price)
        return {"status": "hold", "action": "HOLD", "asset": asset, "tx_hash": None, "reason": reason}
```

Replace the success-path `log_trade` call (step 3b):

```python
    # ── 3b. Persist to history immediately ───────────────────────────────────
    # Logged right after a successful on-chain trade so a runner killed
    # mid-cycle (before mirroring/social) can never leave an executed trade
    # with no trade_history row.
    trade_price = current_data.get(asset, {}).get("PRICE") if asset else None
    log_trade(agent=agent_name, action=action, asset=asset, tx_id=agent_tx, reason=reason, price=trade_price)
```

- [ ] **Step 4: Run tests, verify pass**

Run: `venv/bin/python -m pytest tests/test_trade_service.py -v`
Expected: all pass (existing 9 + 2 new = 11).

- [ ] **Step 5: Run full suite, commit**

Run: `venv/bin/python -m pytest -q` — expected: all pass.

```bash
git add backend/trade_service.py tests/test_trade_service.py
git commit -m "feat: capture market price at trade-decision time in trade_history"
```

---

### Task 5: Daily NAV snapshot job

**Files:**
- Create: `scripts/snapshot_nav.py`
- Create: `.github/workflows/nav-snapshot.yml`
- Test: `tests/test_snapshot_nav.py` (create)

- [ ] **Step 1: Write failing tests**

Create `tests/test_snapshot_nav.py`:

```python
from unittest.mock import patch

from scripts.snapshot_nav import run_all


@patch("scripts.snapshot_nav.init_db")
@patch("scripts.snapshot_nav.upsert_nav_snapshot")
@patch("scripts.snapshot_nav.get_current_market_state", return_value={"BTC": {"PRICE": 100.0}})
@patch("scripts.snapshot_nav.get_trade_rows_for_performance")
def test_snapshots_every_agent(mock_rows, mock_market, mock_upsert, mock_init):
    mock_rows.return_value = [
        {"action": "BUY", "asset": "BTC", "price": 90.0, "timestamp": "t1"},
    ]
    exit_code = run_all()
    assert exit_code == 0
    assert mock_upsert.call_count == len(__import__("backend.agents", fromlist=["AGENT_PROFILES"]).AGENT_PROFILES)


@patch("scripts.snapshot_nav.init_db")
@patch("scripts.snapshot_nav.upsert_nav_snapshot")
@patch("scripts.snapshot_nav.get_current_market_state", return_value={"BTC": {"PRICE": 100.0}})
@patch("scripts.snapshot_nav.get_trade_rows_for_performance", side_effect=Exception("db down"))
def test_one_agent_failing_does_not_abort_others(mock_rows, mock_market, mock_upsert, mock_init):
    exit_code = run_all()
    assert exit_code == 1  # all failed in this case since the mock always raises
    assert mock_upsert.call_count == 0


@patch.dict("os.environ", {"DATABASE_URL": "", "TRADE_DRY_RUN": "false"}, clear=False)
def test_missing_database_url_in_live_mode_blocks(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("TRADE_DRY_RUN", "false")
    with patch("scripts.snapshot_nav.init_db") as mock_init:
        exit_code = run_all()
    assert exit_code == 1
    mock_init.assert_not_called()
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `venv/bin/python -m pytest tests/test_snapshot_nav.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.snapshot_nav'`

- [ ] **Step 3: Create `scripts/snapshot_nav.py`**

```python
"""
snapshot_nav.py — Daily NAV snapshot for each agent's simulated performance.

Records today's cumulative return multiplier per agent into agent_nav_snapshots,
so backend/performance.py can compute real 24h/7d/1y windows. Run once daily via
.github/workflows/nav-snapshot.yml. Idempotent — safe to rerun same-day (upsert).
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
            print(f"{agent}: FAILED — {exc}")

    return 1 if failures == len(AGENT_PROFILES) else 0


if __name__ == "__main__":
    sys.exit(run_all())
```

- [ ] **Step 4: Run tests, verify pass**

Run: `venv/bin/python -m pytest tests/test_snapshot_nav.py -v`
Expected: 3 passed.

- [ ] **Step 5: Create `.github/workflows/nav-snapshot.yml`**

```yaml
name: NAV Snapshot

on:
  schedule:
    - cron: "10 0 * * *"   # once daily, 00:10 UTC
  workflow_dispatch: {}

concurrency:
  group: nav-snapshot
  cancel-in-progress: false

permissions: {}

jobs:
  snapshot:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    env:
      DATABASE_URL: ${{ secrets.DATABASE_URL }}
      TRADE_DRY_RUN: "false"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt
      - run: python scripts/snapshot_nav.py
```

- [ ] **Step 6: Run full suite, boot check, commit**

Run: `venv/bin/python -m pytest -q` — expected: all pass.
Run: `venv/bin/python -c "from server.api import app; print('boots ok')"` — expected: `boots ok`.

```bash
git add scripts/snapshot_nav.py .github/workflows/nav-snapshot.yml tests/test_snapshot_nav.py
git commit -m "feat: daily NAV snapshot job for real 24h/7d/1y performance windows"
```

---

### Task 6: Wire real stats into the API

**Files:**
- Modify: `server/api.py`

**Context for this task:** `server/api.py` currently has three call sites using the fake stats:
1. `/dashboard` (~line 590-655): agent cards use `get_trade_metrics(profile["id"])` for `win_rate`/`trades`, and `stats["performance"].get("24h", ...)` (the *viewer's own wallet*, not per-agent) for each card's `roi_24h`. The top-level `pnl_pct`/`pnl_amount` also derive from that same wallet-wide fake `stats["performance"]`.
2. `/agents` (~line 1180-1212): same `get_trade_metrics()` pattern for `win_rate`/`trades`.

**Decision for the top-level dashboard P&L (not fully pinned by the spec):** when the viewer follows more than one agent, use the follow with the largest `allocation_amount` as the representative for the headline `pnl_pct`/`pnl_amount`/`performance` figures — simplest deterministic choice, avoids inventing a weighted-blend formula nothing asked for. When following zero agents, or viewing the platform's own configured wallet (`wallet["source"] != "user"`), use `get_agent_performance(AGENT_NAME)` (matches the existing fallback pattern where `metrics = get_trade_metrics(AGENT_NAME)` is used).

- [ ] **Step 1: Add the import**

In `server/api.py`, add to the imports (near `from backend.wallet_summary import get_wallet_stats_safe`):

```python
from backend.performance import get_agent_performance, get_follower_performance
```

- [ ] **Step 2: Replace agent-card stats in `/dashboard`**

Find the loop building `agent_cards` (around line 616-645). Replace:

```python
    for profile in get_agent_catalog():
        agent_metrics = get_trade_metrics(profile["id"])
        agent_followers = get_follower_summary(profile["id"])
```

with:

```python
    for profile in get_agent_catalog():
        agent_metrics = get_agent_performance(profile["id"])
        agent_followers = get_follower_summary(profile["id"])
```

And in the same loop's `agent_cards.append({...})` block, replace:

```python
            "win_rate": agent_metrics["win_rate"],
            "trades": agent_metrics["total_trades"],
            "followers": agent_followers["total_followers"],
            "roi_24h": stats["performance"].get("24h", "0.00%"),
```

with:

```python
            "win_rate": agent_metrics["win_rate"],
            "trades": agent_metrics["total_trades"],
            "followers": agent_followers["total_followers"],
            "roi_24h": agent_metrics["24h"] or "N/A",
```

- [ ] **Step 3: Replace the top-level dashboard performance figures**

Find (near where `metrics = get_trade_metrics(AGENT_NAME)` is assigned, before the agent_cards loop):

```python
    metrics = get_trade_metrics(AGENT_NAME)
```

Replace with a resolution of which agent's performance represents "your" performance:

```python
    if wallet["source"] == "user" and wallet["allocations"] and user_id:
        _primary_follow = max(wallet["allocations"], key=lambda row: float(row.get("allocation_amount") or 0.0))
        performance = get_follower_performance(user_id, _primary_follow["target_agent"])
        metrics = get_agent_performance(_primary_follow["target_agent"])
    else:
        performance = get_agent_performance(AGENT_NAME)
        metrics = performance
```

Note: `dashboard_user`/`user_id` are assigned a few lines below this point in the current code (`dashboard_user = wallet.get("user") or {}` then `user_id = dashboard_user.get("user_id")`) — move those two lines to **above** this new block so `user_id` is available here. The full surrounding order should read: `wallet = get_active_wallet_context(...)` → `stats = get_wallet_stats_safe(...)` → `dashboard_user = wallet.get("user") or {}` → `user_id = dashboard_user.get("user_id")` → the new performance-resolution block above → `followers = get_follower_summary(AGENT_NAME)` → the `agent_cards` loop.

Then find the later lines that compute `pnl_pct`/`pnl_amount` from the fake wallet stats:

```python
    pnl_pct = _parse_percent(stats["performance"].get("24h"))
    pnl_amount = round(total_balance * (pnl_pct / 100.0), 2)
```

Replace with:

```python
    pnl_pct = _parse_percent(performance.get("24h"))
    pnl_amount = round(total_balance * (pnl_pct / 100.0), 2)
```

Further down, in the returned JSON's `"wallet": {...}` block, find:

```python
            "performance": stats["performance"],
```

Replace with:

```python
            "performance": performance,
```

Run `grep -n 'stats\["performance"\]' server/api.py` afterward — expected: no matches remain in this function (there should be none anywhere else in the file either, but confirm).

- [ ] **Step 4: Replace stats in `/agents`**

Find the `list_agents` handler (`@app.get("/agents")`). Replace:

```python
    for profile in catalog:
        metrics = get_trade_metrics(profile["id"])
```

with:

```python
    for profile in catalog:
        metrics = get_agent_performance(profile["id"])
```

The rest of that function's field access (`metrics["win_rate"]`, `metrics["total_trades"]`) is unchanged since the new function preserves those keys.

- [ ] **Step 5: Remove the now-unused `get_trade_metrics` import if nothing else uses it**

Run: `grep -n "get_trade_metrics" server/api.py`
If no remaining call sites, remove `get_trade_metrics` from the `from backend.database import (...)` block. If any remain, leave the import.

- [ ] **Step 6: Boot check and manual smoke test**

Run: `venv/bin/python -c "from server.api import app; print('boots ok')"` — expected: `boots ok`.
Run the full suite: `venv/bin/python -m pytest -q` — expected: all pass (this task has no new automated tests of its own since it's wiring against already-tested functions; the boot check plus existing endpoint-shape tests, if any, are the safety net — if `tests/test_auth.py`'s `/settings`-based fixture pattern doesn't cover `/dashboard`, that's expected and fine, this step is verification-only per the plan's scope).

- [ ] **Step 7: Commit**

```bash
git add server/api.py
git commit -m "feat: wire real agent/follower performance stats into dashboard and agents API"
```

---

### Task 7: Notification-scheduler reliability — retire in-process thread, add GitHub Actions cron

**Files:**
- Create: `scripts/send_daily_summaries.py`
- Create: `.github/workflows/daily-summary.yml`
- Modify: `server/api.py` (remove `start_daily_summary_scheduler` import and lifespan call)
- Modify: `backend/daily_summary.py` (remove the thread scheduler; keep `send_daily_summary_reports`)
- Test: `tests/test_send_daily_summaries.py` (create)

- [ ] **Step 1: Write failing tests**

Create `tests/test_send_daily_summaries.py`:

```python
from unittest.mock import patch

from scripts.send_daily_summaries import run_all


@patch("scripts.send_daily_summaries.init_db")
@patch("scripts.send_daily_summaries.send_daily_summary_reports", return_value=[{"user_id": "u1", "email": True, "telegram": False}])
def test_success_returns_zero(mock_send, mock_init):
    assert run_all() == 0
    mock_send.assert_called_once()


@patch.dict("os.environ", {}, clear=False)
def test_missing_database_url_in_live_mode_blocks(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("TRADE_DRY_RUN", "false")
    with patch("scripts.send_daily_summaries.init_db") as mock_init:
        exit_code = run_all()
    assert exit_code == 1
    mock_init.assert_not_called()
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `venv/bin/python -m pytest tests/test_send_daily_summaries.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.send_daily_summaries'`

- [ ] **Step 3: Create `scripts/send_daily_summaries.py`**

```python
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
```

- [ ] **Step 4: Run tests, verify pass**

Run: `venv/bin/python -m pytest tests/test_send_daily_summaries.py -v`
Expected: 2 passed.

- [ ] **Step 5: Create `.github/workflows/daily-summary.yml`**

Note: `send_daily_summary_reports()` internally computes "now" in `Africa/Lagos` time and sends to users regardless of the job's own trigger time — the cron below picks a UTC time that's evening in Lagos (UTC+1), close to the original 20:00 Lagos design intent.

```yaml
name: Daily Summary

on:
  schedule:
    - cron: "0 19 * * *"   # ~20:00 Africa/Lagos (UTC+1)
  workflow_dispatch: {}

concurrency:
  group: daily-summary
  cancel-in-progress: false

permissions: {}

jobs:
  summary:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    env:
      DATABASE_URL: ${{ secrets.DATABASE_URL }}
      BOT_TOKEN: ${{ secrets.BOT_TOKEN }}
      RESEND_API_KEY: ${{ secrets.RESEND_API_KEY }}
      RESEND_FROM_EMAIL: ${{ secrets.RESEND_FROM_EMAIL }}
      TRADE_DRY_RUN: "false"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt
      - run: python scripts/send_daily_summaries.py
```

- [ ] **Step 6: Remove the in-process thread scheduler**

In `backend/daily_summary.py`, remove the `threading`/`time` imports if nothing else in the file uses them, and remove these functions entirely: `_next_run_time`, `_summary_loop`, `start_daily_summary_scheduler`, and the `_scheduler_started` module-level flag. Keep `send_daily_summary_reports`, `_current_lagos_time`, `_user_trade_count_today`, and the `LAGOS_TZ` constant (still used by `send_daily_summary_reports`).

Run `grep -n "^import\|^from" backend/daily_summary.py` first to confirm which imports (`os`, `threading`, `time`) become unused after the removal, and delete only those.

- [ ] **Step 7: Remove the scheduler call from `server/api.py`**

Change the import line:

```python
from backend.daily_summary import start_daily_summary_scheduler, send_daily_summary_reports
```

to:

```python
from backend.daily_summary import send_daily_summary_reports
```

Change the lifespan:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    start_daily_summary_scheduler()
    # Trade cycles run in GitHub Actions (.github/workflows/trade-cycle.yml),
    # not in this process — the web service is allowed to sleep on Render.
    yield
```

to:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Trade cycles and daily summaries both run in GitHub Actions
    # (.github/workflows/trade-cycle.yml, daily-summary.yml), not in this
    # process — the web service is allowed to sleep on Render.
    yield
```

- [ ] **Step 8: Run full suite, boot check, commit**

Run: `venv/bin/python -m pytest -q` — expected: all pass.
Run: `venv/bin/python -c "from server.api import app; print('boots ok')"` — expected: `boots ok`.
Run: `grep -rn "start_daily_summary_scheduler\|_summary_loop\|_scheduler_started" server/ backend/` — expected: no matches.

```bash
git add scripts/send_daily_summaries.py .github/workflows/daily-summary.yml server/api.py backend/daily_summary.py tests/test_send_daily_summaries.py
git commit -m "fix: retire in-process daily-summary thread scheduler; run via GitHub Actions cron"
```

---

### Task 8: README update

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Document the two new scheduled jobs**

In the "Scheduled Trading (GitHub Actions)" section added in Phase 1, add below the existing kill-switch paragraph:

```markdown
Two more daily jobs follow the same pattern:

- `.github/workflows/nav-snapshot.yml` — once daily, records each agent's cumulative
  simulated-return multiplier so `backend/performance.py` can compute real 24h/7d/1y
  performance windows. Needs only the `DATABASE_URL` secret.
- `.github/workflows/daily-summary.yml` — once daily (~20:00 Africa/Lagos), sends
  daily summary notifications. Replaces the old in-process scheduler, which died
  whenever Render slept the dyno. Needs `DATABASE_URL` plus `BOT_TOKEN`/`RESEND_API_KEY`/
  `RESEND_FROM_EMAIL`.

Performance numbers are simulated signal-following returns (see
`docs/superpowers/specs/2026-07-18-phase2-real-performance-stats-design.md`), not
reconciled real on-chain trade amounts — the on-chain BUY/SELL sizing has a known
unit inconsistency (out of scope to fix) that would make real-amount P&L noisy
rather than meaningful.
```

- [ ] **Step 2: Verify and commit**

Run: `venv/bin/python -m pytest -q` — expected: all pass.

```bash
git add README.md
git commit -m "docs: document NAV snapshot and daily-summary GitHub Actions jobs"
```

## Self-review notes (already applied above)

- **Spec coverage:** schema (Task 1), computation engine (Tasks 2-3), price capture (Task 4), snapshot job (Task 5), notification reliability (Task 7), API/frontend wiring (Task 6 — frontend needs no changes per the spec's decision to keep field names identical), verification criteria (covered by each task's own tests plus Task 5's idempotency test). All spec sections have a task.
- **Type consistency:** `_walk_trade_rows` return shape (`multiplier`, `win_rate`, `closed_trades`, `total_trades`, `has_open_position`) is identical across Task 2's tests, Task 3's wrappers, and Task 5's snapshot script. `get_agent_performance`/`get_follower_performance` both return `win_rate`, `total_trades`, `closed_trades`, `has_open_position`, `24h`, `7d`, `1y` — consistent between Task 3's tests and Task 6's API usage.
- **No placeholders:** every step has complete code, exact commands, and expected output.
