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
