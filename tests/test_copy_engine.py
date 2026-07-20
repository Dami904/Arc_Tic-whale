from unittest.mock import patch

from backend.copy_engine import exit_all_positions


def _allocation_row(target_agent="Conservative_Whale", wallet_id="w1", wallet_address="0xaddr", amount=25.0):
    return {
        "target_agent": target_agent,
        "allocation_amount": amount,
        "asset": None,
        "stop_loss_pct": 10.0,
        "user_wallet_id": wallet_id,
        "user_wallet_address": wallet_address,
    }


class TestExitAllPositions:
    @patch("backend.copy_engine.get_current_market_state", return_value={"BTC": {"PRICE": 68000}})
    @patch("backend.copy_engine.notify_trade_alert")
    @patch("backend.copy_engine.get_user", return_value={"trade_alerts": 0})
    @patch("backend.copy_engine.log_trade")
    @patch("backend.copy_engine.execute_trade", return_value="0xsell123")
    @patch("backend.copy_engine.get_latest_follower_trade")
    @patch("backend.copy_engine.get_user_allocations")
    def test_sells_held_asset_back_to_usdc(
        self, mock_alloc, mock_latest, mock_exec, mock_log, mock_user, mock_notify, mock_market
    ):
        mock_alloc.return_value = [_allocation_row()]
        mock_latest.return_value = {"action": "BUY", "asset": "BTC"}

        results = exit_all_positions("user1")

        mock_exec.assert_called_once_with(
            wallet_id="w1", action="SELL", target_asset_symbol="BTC",
            amount="25.0", recipient_address="0xaddr",
        )
        assert results == [{"agent": "Conservative_Whale", "asset": "BTC", "status": "success", "tx_id": "0xsell123"}]

    @patch("backend.copy_engine.get_current_market_state", return_value={})
    @patch("backend.copy_engine.execute_trade")
    @patch("backend.copy_engine.get_latest_follower_trade")
    @patch("backend.copy_engine.get_user_allocations")
    def test_skips_follows_already_in_usdc(self, mock_alloc, mock_latest, mock_exec, mock_market):
        mock_alloc.return_value = [_allocation_row()]
        mock_latest.return_value = {"action": "SELL", "asset": "BTC"}  # already exited

        results = exit_all_positions("user1")

        mock_exec.assert_not_called()
        assert results == []

    @patch("backend.copy_engine.get_current_market_state", return_value={})
    @patch("backend.copy_engine.execute_trade")
    @patch("backend.copy_engine.get_latest_follower_trade", return_value=None)
    @patch("backend.copy_engine.get_user_allocations")
    def test_skips_follows_with_no_trades_yet(self, mock_alloc, mock_latest, mock_exec, mock_market):
        mock_alloc.return_value = [_allocation_row()]

        results = exit_all_positions("user1")

        mock_exec.assert_not_called()
        assert results == []

    @patch("backend.copy_engine.get_current_market_state", return_value={"BTC": {"PRICE": 68000}, "ETH": {"PRICE": 3500}})
    @patch("backend.copy_engine.notify_trade_alert")
    @patch("backend.copy_engine.get_user", return_value={"trade_alerts": 0})
    @patch("backend.copy_engine.log_trade")
    @patch("backend.copy_engine.execute_trade", return_value="0xtx")
    @patch("backend.copy_engine.get_latest_follower_trade")
    @patch("backend.copy_engine.get_user_allocations")
    def test_exits_across_multiple_followed_agents(
        self, mock_alloc, mock_latest, mock_exec, mock_log, mock_user, mock_notify, mock_market
    ):
        mock_alloc.return_value = [
            _allocation_row(target_agent="Conservative_Whale", wallet_id="w1"),
            _allocation_row(target_agent="Aggressive_Degen", wallet_id="w2"),
        ]
        mock_latest.side_effect = [
            {"action": "BUY", "asset": "BTC"},
            {"action": "BUY", "asset": "ETH"},
        ]

        results = exit_all_positions("user1")

        assert mock_exec.call_count == 2
        assert {r["asset"] for r in results} == {"BTC", "ETH"}

    @patch("backend.copy_engine.get_current_market_state", return_value={"BTC": {"PRICE": 68000}})
    @patch("backend.copy_engine.execute_trade", return_value=None)
    @patch("backend.copy_engine.get_latest_follower_trade")
    @patch("backend.copy_engine.get_user_allocations")
    def test_records_error_when_execute_trade_fails(self, mock_alloc, mock_latest, mock_exec, mock_market):
        mock_alloc.return_value = [_allocation_row()]
        mock_latest.return_value = {"action": "BUY", "asset": "BTC"}

        results = exit_all_positions("user1")

        assert results == [{"agent": "Conservative_Whale", "asset": "BTC", "status": "error"}]

    @patch("backend.copy_engine.get_current_market_state", return_value={})
    @patch("backend.copy_engine.get_user_allocations", return_value=[])
    def test_no_follows_returns_empty_list(self, mock_alloc, mock_market):
        assert exit_all_positions("user1") == []
