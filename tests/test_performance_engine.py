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

    @patch("backend.performance.get_current_market_state")
    @patch("backend.performance.get_nav_snapshot_on_or_before", return_value=None)
    @patch("backend.performance.get_trade_rows_for_performance")
    def test_passed_current_prices_skips_market_fetch(self, mock_rows, mock_snap, mock_market):
        mock_rows.return_value = [{"action": "BUY", "asset": "BTC", "price": 100.0, "timestamp": "t1"}]
        get_agent_performance("Conservative_Whale", current_prices={"BTC": {"PRICE": 150.0}})
        mock_market.assert_not_called()

    @patch("backend.performance.compute_agent_trend")
    @patch("backend.performance.get_current_market_state", return_value={"BTC": {"PRICE": 120.0}})
    @patch("backend.performance.get_nav_snapshot_on_or_before", return_value=None)
    @patch("backend.performance.get_trade_rows_for_performance")
    def test_includes_degradation_trend(self, mock_rows, mock_snap, mock_market, mock_trend):
        mock_rows.return_value = []
        mock_trend.return_value = {"trend": "declining", "recent_return_pct": -3.0, "prior_return_pct": 8.0}
        result = get_agent_performance("Conservative_Whale")
        mock_trend.assert_called_once_with("Conservative_Whale")
        assert result["trend"] == "declining"
        assert result["recent_return_pct"] == -3.0
        assert result["prior_return_pct"] == 8.0

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


from backend.performance import compute_agent_trend


class TestComputeAgentTrend:
    def test_insufficient_data_when_no_snapshots_at_all(self):
        with patch("backend.performance.get_nav_snapshot_on_or_before", return_value=None):
            result = compute_agent_trend("Conservative_Whale")
        assert result["trend"] == "insufficient_data"
        assert result["recent_return_pct"] is None
        assert result["prior_return_pct"] is None

    def test_insufficient_data_when_week_and_two_week_snapshots_coincide(self):
        # A gap in snapshot history (e.g. a missed daily run) means both the
        # "week ago" and "two weeks ago" lookups clamp to the same earlier
        # row — not a real two-window comparison, so don't claim a trend.
        same_snap = {"nav_multiplier": 1.00, "snapshot_date": "2026-06-20"}
        today_snap = {"nav_multiplier": 1.05, "snapshot_date": "2026-07-20"}
        with patch("backend.performance.get_nav_snapshot_on_or_before", side_effect=[today_snap, same_snap, same_snap]):
            result = compute_agent_trend("Conservative_Whale")
        assert result["trend"] == "insufficient_data"

    def test_declining_trend_flagged_past_threshold(self):
        # today: 1.05, week ago: 1.10 (recent week: -4.5%), two weeks ago: 1.00 (prior week: +10%)
        snapshots = {
            "today": {"nav_multiplier": 1.05, "snapshot_date": "2026-07-20"},
            "week": {"nav_multiplier": 1.10, "snapshot_date": "2026-07-13"},
            "two_week": {"nav_multiplier": 1.00, "snapshot_date": "2026-07-06"},
        }
        with patch("backend.performance.get_nav_snapshot_on_or_before", side_effect=[
            snapshots["today"], snapshots["week"], snapshots["two_week"],
        ]):
            result = compute_agent_trend("Conservative_Whale")
        assert result["trend"] == "declining"
        assert round(result["recent_return_pct"], 2) == round(((1.05 / 1.10) - 1) * 100, 2)
        assert round(result["prior_return_pct"], 2) == round(((1.10 / 1.00) - 1) * 100, 2)

    def test_stable_trend_when_drop_under_threshold(self):
        # recent week +8%, prior week +10% — a 2pt drop, under the 5pt threshold
        snapshots = [
            {"nav_multiplier": 1.188, "snapshot_date": "2026-07-20"},  # today
            {"nav_multiplier": 1.10, "snapshot_date": "2026-07-13"},   # week ago
            {"nav_multiplier": 1.00, "snapshot_date": "2026-07-06"},   # two weeks ago
        ]
        with patch("backend.performance.get_nav_snapshot_on_or_before", side_effect=snapshots):
            result = compute_agent_trend("Conservative_Whale")
        assert result["trend"] == "stable"

    def test_stable_trend_when_recent_beats_prior(self):
        snapshots = [
            {"nav_multiplier": 1.20, "snapshot_date": "2026-07-20"},  # today
            {"nav_multiplier": 1.05, "snapshot_date": "2026-07-13"},  # week ago
            {"nav_multiplier": 1.00, "snapshot_date": "2026-07-06"},  # two weeks ago
        ]
        with patch("backend.performance.get_nav_snapshot_on_or_before", side_effect=snapshots):
            result = compute_agent_trend("Conservative_Whale")
        assert result["trend"] == "stable"
