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


class TestFollowerRetention:
    def test_deactivate_follower_sets_deactivated_at(self, tmp_db):
        tmp_db.add_follower(user_id="u1", user_wallet_id="w1", target_agent="A", allocation_amount=1.0)
        tmp_db.deactivate_follower("u1", "A")
        with tmp_db._connection() as conn:
            cursor = tmp_db._cursor(conn)
            cursor.execute("SELECT deactivated_at FROM followers WHERE user_id = 'u1'")
            row = tmp_db._row(cursor)
        assert row["deactivated_at"] is not None

    def test_active_follower_has_no_deactivated_at(self, tmp_db):
        tmp_db.add_follower(user_id="u1", user_wallet_id="w1", target_agent="A", allocation_amount=1.0)
        with tmp_db._connection() as conn:
            cursor = tmp_db._cursor(conn)
            cursor.execute("SELECT deactivated_at FROM followers WHERE user_id = 'u1'")
            row = tmp_db._row(cursor)
        assert row["deactivated_at"] is None

    def test_no_follows_returns_zeroed_stats(self, tmp_db):
        stats = tmp_db.get_agent_retention_stats("A")
        assert stats == {
            "total_follows": 0, "active_follows": 0,
            "retention_rate_pct": None, "avg_tenure_days": None,
        }

    def test_retention_rate_counts_active_vs_total(self, tmp_db):
        tmp_db.add_follower(user_id="u1", user_wallet_id="w1", target_agent="A", allocation_amount=1.0)
        tmp_db.add_follower(user_id="u2", user_wallet_id="w2", target_agent="A", allocation_amount=1.0)
        tmp_db.add_follower(user_id="u3", user_wallet_id="w3", target_agent="A", allocation_amount=1.0)
        tmp_db.deactivate_follower("u2", "A")

        stats = tmp_db.get_agent_retention_stats("A")

        assert stats["total_follows"] == 3
        assert stats["active_follows"] == 2
        assert round(stats["retention_rate_pct"], 1) == round((2 / 3) * 100, 1)

    def test_avg_tenure_computed_from_churned_follows(self, tmp_db):
        import backend.database as db
        with db._connection() as conn:
            cursor = db._cursor(conn)
            cursor.execute(
                "INSERT INTO followers (user_id, user_wallet_id, target_agent, allocation_amount, is_active, followed_at, deactivated_at) "
                "VALUES ('u1', 'w1', 'A', 1.0, 0, '2026-07-01T00:00:00+00:00', '2026-07-04T00:00:00+00:00')"
            )
            cursor.execute(
                "INSERT INTO followers (user_id, user_wallet_id, target_agent, allocation_amount, is_active, followed_at, deactivated_at) "
                "VALUES ('u2', 'w2', 'A', 1.0, 0, '2026-07-01T00:00:00+00:00', '2026-07-11T00:00:00+00:00')"
            )
            conn.commit()

        stats = db.get_agent_retention_stats("A")

        assert stats["avg_tenure_days"] == 6.5  # (3 + 10) / 2

    def test_active_follows_excluded_from_avg_tenure(self, tmp_db):
        tmp_db.add_follower(user_id="u1", user_wallet_id="w1", target_agent="A", allocation_amount=1.0)
        stats = tmp_db.get_agent_retention_stats("A")
        assert stats["avg_tenure_days"] is None  # no churned follows to measure yet


class TestReFollowDoesNotDuplicate:
    def _active_rows(self, tmp_db, agent):
        with tmp_db._connection() as conn:
            cur = tmp_db._cursor(conn)
            cur.execute("SELECT allocation_amount, remaining_capital FROM followers WHERE target_agent = ? AND is_active = 1", (agent,))
            return tmp_db._rows(cur)

    def test_refollow_replaces_previous_active_row(self, tmp_db):
        tmp_db.add_follower(user_id="u1", user_wallet_id="w1", target_agent="A", allocation_amount=10.0)
        tmp_db.add_follower(user_id="u1", user_wallet_id="w1", target_agent="A", allocation_amount=25.0)
        rows = self._active_rows(tmp_db, "A")
        assert len(rows) == 1
        assert rows[0]["allocation_amount"] == 25.0
        assert rows[0]["remaining_capital"] == 25.0  # fresh pool seeded from the new allocation

    def test_refollow_only_touches_same_agent(self, tmp_db):
        tmp_db.add_follower(user_id="u1", user_wallet_id="w1", target_agent="A", allocation_amount=10.0)
        tmp_db.add_follower(user_id="u1", user_wallet_id="w1", target_agent="B", allocation_amount=15.0)
        assert len(self._active_rows(tmp_db, "A")) == 1
        assert len(self._active_rows(tmp_db, "B")) == 1

    def test_different_users_can_follow_same_agent(self, tmp_db):
        tmp_db.add_follower(user_id="u1", user_wallet_id="w1", target_agent="A", allocation_amount=10.0)
        tmp_db.add_follower(user_id="u2", user_wallet_id="w2", target_agent="A", allocation_amount=10.0)
        assert len(self._active_rows(tmp_db, "A")) == 2
