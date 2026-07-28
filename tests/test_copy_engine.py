from unittest.mock import patch

from backend.copy_engine import _entry_notional, _exit_notional, exit_all_positions


def _allocation_row(target_agent="Conservative_Whale", wallet_id="w1", wallet_address="0xaddr", amount=25.0):
    return {
        "target_agent": target_agent,
        "allocation_amount": amount,
        "asset": None,
        "stop_loss_pct": 10.0,
        "user_wallet_id": wallet_id,
        "user_wallet_address": wallet_address,
        "remaining_capital": amount,
    }


class TestEntryNotional:
    def test_ten_percent_of_remaining(self):
        assert _entry_notional(20.0) == 2.0

    def test_shrinks_with_depleted_pool(self):
        assert _entry_notional(15.0) == 1.5

    def test_floors_at_min_viable_trade(self):
        assert _entry_notional(3.0) == 0.5  # 10% would be 0.30, below the $0.50 executor floor

    def test_skips_when_pool_cannot_cover_floor(self):
        assert _entry_notional(0.4) == 0.0


class TestExitNotional:
    def test_marks_position_to_current_price(self):
        latest = {"action": "BUY", "asset": "BTC", "amount_usdc": 2.0, "price": 65000.0}
        assert _exit_notional(latest, 70000.0) == 2.15  # 2.0 * 70000/65000

    def test_falls_back_to_deployed_amount_without_entry_price(self):
        latest = {"action": "BUY", "asset": "BTC", "amount_usdc": 12.5}
        assert _exit_notional(latest, 70000.0) == 12.5

    def test_zero_when_no_recorded_amount(self):
        assert _exit_notional({"action": "BUY", "asset": "BTC"}, 70000.0) == 0.0
        assert _exit_notional(None, 70000.0) == 0.0


class TestExitAllPositions:
    @patch("backend.copy_engine.adjust_follower_remaining_capital")
    @patch("backend.copy_engine.get_current_market_state", return_value={"BTC": {"PRICE": 68000}})
    @patch("backend.copy_engine.notify_trade_alert")
    @patch("backend.copy_engine.get_user", return_value={"trade_alerts": 0})
    @patch("backend.copy_engine.log_trade")
    @patch("backend.copy_engine.execute_trade", return_value="0xsell123")
    @patch("backend.copy_engine.get_latest_follower_trade")
    @patch("backend.copy_engine.get_user_allocations")
    def test_sells_held_asset_back_to_usdc(
        self, mock_alloc, mock_latest, mock_exec, mock_log, mock_user, mock_notify, mock_market, mock_adjust
    ):
        mock_alloc.return_value = [_allocation_row()]
        mock_latest.return_value = {"action": "BUY", "asset": "BTC", "amount_usdc": 2.5, "price": 68000}

        results = exit_all_positions("user1")

        mock_exec.assert_called_once_with(
            wallet_id="w1", action="SELL", target_asset_symbol="BTC",
            amount="2.5", recipient_address="0xaddr", current_price=68000,
        )
        assert results == [{"agent": "Conservative_Whale", "asset": "BTC", "status": "success", "tx_id": "0xsell123"}]
        mock_adjust.assert_called_once_with("w1", "Conservative_Whale", 2.5)

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

    @patch("backend.copy_engine.adjust_follower_remaining_capital")
    @patch("backend.copy_engine.get_current_market_state", return_value={"BTC": {"PRICE": 68000}, "ETH": {"PRICE": 3500}})
    @patch("backend.copy_engine.notify_trade_alert")
    @patch("backend.copy_engine.get_user", return_value={"trade_alerts": 0})
    @patch("backend.copy_engine.log_trade")
    @patch("backend.copy_engine.execute_trade", return_value="0xtx")
    @patch("backend.copy_engine.get_latest_follower_trade")
    @patch("backend.copy_engine.get_user_allocations")
    def test_exits_across_multiple_followed_agents(
        self, mock_alloc, mock_latest, mock_exec, mock_log, mock_user, mock_notify, mock_market, mock_adjust
    ):
        mock_alloc.return_value = [
            _allocation_row(target_agent="Conservative_Whale", wallet_id="w1"),
            _allocation_row(target_agent="Aggressive_Degen", wallet_id="w2"),
        ]
        mock_latest.side_effect = [
            {"action": "BUY", "asset": "BTC", "amount_usdc": 2.0, "price": 68000},
            {"action": "BUY", "asset": "ETH", "amount_usdc": 2.0, "price": 3500},
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
        mock_latest.return_value = {"action": "BUY", "asset": "BTC", "amount_usdc": 2.0, "price": 68000}

        results = exit_all_positions("user1")

        assert results == [{"agent": "Conservative_Whale", "asset": "BTC", "status": "error"}]

    @patch("backend.copy_engine.get_current_market_state", return_value={})
    @patch("backend.copy_engine.get_user_allocations", return_value=[])
    def test_no_follows_returns_empty_list(self, mock_alloc, mock_market):
        assert exit_all_positions("user1") == []


class TestPositionSizing:
    """10%-of-remaining-capital sizing: BUY deploys 10% of the follower's
    current pool; SELL closes the whole position at its marked value and
    returns proceeds to the pool."""

    @patch("backend.copy_engine.adjust_follower_remaining_capital")
    @patch("backend.copy_engine.notify_trade_alert")
    @patch("backend.copy_engine.get_user", return_value=None)
    @patch("backend.copy_engine.get_follower_by_wallet_id", return_value={})
    @patch("backend.copy_engine.log_trade")
    @patch("backend.copy_engine.execute_trade", return_value="0xbuy")
    @patch("backend.copy_engine.get_current_market_state", return_value={"BTC": {"PRICE": 68000}})
    @patch("backend.copy_engine.get_active_followers", return_value=[("w1", "0xaddr", 20.0, 10.0, 20.0)])
    @patch("backend.copy_engine.evaluate_stop_losses")
    def test_buy_deploys_ten_percent_of_remaining(
        self, mock_stops, mock_followers, mock_market, mock_exec, mock_log, mock_row, mock_user, mock_notify, mock_adjust
    ):
        from backend.copy_engine import mirror_agent_trade
        mirror_agent_trade("Conservative_Whale", "BUY", "BTC")

        assert mock_exec.call_args.kwargs["amount"] == "2.0"
        assert mock_log.call_args.kwargs["amount_usdc"] == 2.0
        assert mock_log.call_args.kwargs["price"] == 68000
        mock_adjust.assert_called_once_with("w1", "Conservative_Whale", -2.0)

    @patch("backend.copy_engine.adjust_follower_remaining_capital")
    @patch("backend.copy_engine.notify_trade_alert")
    @patch("backend.copy_engine.get_user", return_value=None)
    @patch("backend.copy_engine.get_follower_by_wallet_id", return_value={})
    @patch("backend.copy_engine.log_trade")
    @patch("backend.copy_engine.execute_trade", return_value="0xbuy")
    @patch("backend.copy_engine.get_current_market_state", return_value={"BTC": {"PRICE": 68000}})
    @patch("backend.copy_engine.get_active_followers", return_value=[("w1", "0xaddr", 20.0, 10.0, 15.0)])
    @patch("backend.copy_engine.evaluate_stop_losses")
    def test_buy_sizes_from_depleted_pool_not_original_allocation(
        self, mock_stops, mock_followers, mock_market, mock_exec, mock_log, mock_row, mock_user, mock_notify, mock_adjust
    ):
        # Allocated 20, pool now 15 after losses: next entry is 1.50, not 2.00.
        from backend.copy_engine import mirror_agent_trade
        mirror_agent_trade("Conservative_Whale", "BUY", "BTC")

        assert mock_exec.call_args.kwargs["amount"] == "1.5"
        mock_adjust.assert_called_once_with("w1", "Conservative_Whale", -1.5)

    @patch("backend.copy_engine.adjust_follower_remaining_capital")
    @patch("backend.copy_engine.notify_trade_alert")
    @patch("backend.copy_engine.get_user", return_value=None)
    @patch("backend.copy_engine.get_follower_by_wallet_id", return_value={})
    @patch("backend.copy_engine.log_trade")
    @patch("backend.copy_engine.execute_trade", return_value="0xbuy")
    @patch("backend.copy_engine.get_current_market_state", return_value={"BTC": {"PRICE": 68000}})
    @patch("backend.copy_engine.get_active_followers", return_value=[("w1", "0xaddr", 20.0, 10.0, 0.4)])
    @patch("backend.copy_engine.evaluate_stop_losses")
    def test_buy_skipped_when_pool_exhausted(
        self, mock_stops, mock_followers, mock_market, mock_exec, mock_log, mock_row, mock_user, mock_notify, mock_adjust
    ):
        from backend.copy_engine import mirror_agent_trade
        mirror_agent_trade("Conservative_Whale", "BUY", "BTC")

        mock_exec.assert_not_called()
        mock_adjust.assert_not_called()

    @patch("backend.copy_engine.adjust_follower_remaining_capital")
    @patch("backend.copy_engine.notify_trade_alert")
    @patch("backend.copy_engine.get_user", return_value=None)
    @patch("backend.copy_engine.get_follower_by_wallet_id", return_value={})
    @patch("backend.copy_engine.log_trade")
    @patch("backend.copy_engine.execute_trade", return_value="0xsell")
    @patch("backend.copy_engine.get_latest_follower_trade",
           return_value={"action": "BUY", "asset": "BTC", "amount_usdc": 2.0, "price": 65000.0})
    @patch("backend.copy_engine.get_current_market_state", return_value={"BTC": {"PRICE": 70000}})
    @patch("backend.copy_engine.get_active_followers", return_value=[("w1", "0xaddr", 20.0, 10.0, 18.0)])
    @patch("backend.copy_engine.evaluate_stop_losses")
    def test_sell_returns_marked_proceeds_to_pool(
        self, mock_stops, mock_followers, mock_market, mock_latest, mock_exec, mock_log, mock_row, mock_user, mock_notify, mock_adjust
    ):
        # Bought $2.00 at 65000; selling at 70000 -> proceeds 2.15 back to the pool.
        from backend.copy_engine import mirror_agent_trade
        mirror_agent_trade("Conservative_Whale", "SELL", "BTC")

        assert mock_exec.call_args.kwargs["amount"] == "2.15"
        mock_adjust.assert_called_once_with("w1", "Conservative_Whale", 2.15)

    @patch("backend.copy_engine.adjust_follower_remaining_capital")
    @patch("backend.copy_engine.notify_trade_alert")
    @patch("backend.copy_engine.get_user", return_value=None)
    @patch("backend.copy_engine.get_follower_by_wallet_id", return_value={})
    @patch("backend.copy_engine.log_trade")
    @patch("backend.copy_engine.execute_trade", return_value="0xsell")
    @patch("backend.copy_engine.get_latest_follower_trade", return_value=None)
    @patch("backend.copy_engine.get_current_market_state", return_value={"BTC": {"PRICE": 70000}})
    @patch("backend.copy_engine.get_active_followers", return_value=[("w1", "0xaddr", 20.0, 10.0, 20.0)])
    @patch("backend.copy_engine.evaluate_stop_losses")
    def test_sell_skipped_when_no_position_recorded(
        self, mock_stops, mock_followers, mock_market, mock_latest, mock_exec, mock_log, mock_row, mock_user, mock_notify, mock_adjust
    ):
        from backend.copy_engine import mirror_agent_trade
        mirror_agent_trade("Conservative_Whale", "SELL", "BTC")

        mock_exec.assert_not_called()
        mock_adjust.assert_not_called()
