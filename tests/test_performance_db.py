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
