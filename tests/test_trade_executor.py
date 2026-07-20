from decimal import Decimal
from unittest.mock import MagicMock, patch

import backend.trade_executor as trade_executor
from backend.trade_executor import execute_trade


class TestSellPriceConversion:
    def test_sell_without_price_returns_none(self):
        result = execute_trade(
            wallet_id="w1", action="SELL", target_asset_symbol="BTC",
            amount="1.0", recipient_address="0xaddr", current_price=None,
        )
        assert result is None

    def test_sell_with_zero_price_returns_none(self):
        result = execute_trade(
            wallet_id="w1", action="SELL", target_asset_symbol="BTC",
            amount="1.0", recipient_address="0xaddr", current_price=0,
        )
        assert result is None

    def test_buy_does_not_require_price(self, monkeypatch):
        monkeypatch.setattr(trade_executor, "TRADE_DRY_RUN", True)
        result = execute_trade(
            wallet_id="w1", action="BUY", target_asset_symbol="BTC",
            amount="1.0", recipient_address="0xaddr", current_price=None,
        )
        assert result is not None
        assert result.startswith("dryrun-")

    def test_sell_with_valid_price_succeeds_in_dry_run(self, monkeypatch):
        monkeypatch.setattr(trade_executor, "TRADE_DRY_RUN", True)
        result = execute_trade(
            wallet_id="w1", action="SELL", target_asset_symbol="BTC",
            amount="1.0", recipient_address="0xaddr", current_price=68000.0,
        )
        assert result is not None
        assert result.startswith("dryrun-")

    def test_sell_converts_usd_amount_to_asset_units(self, monkeypatch):
        monkeypatch.setattr(trade_executor, "TRADE_DRY_RUN", False)
        monkeypatch.setattr(trade_executor, "initialize_circle_client", lambda: MagicMock())

        captured = {}

        def fake_build_swap_calldata(action, target_asset_symbol, amount, recipient_address):
            captured["swap_amount"] = amount
            return "0xcalldata"

        def fake_build_approval_calldata(token_symbol, amount):
            captured["approval_amount"] = amount
            return ("0xtoken", "0xapprovecalldata")

        with patch.object(trade_executor, "developer_controlled_wallets") as mock_dcw, \
             patch.object(trade_executor, "_build_swap_calldata", side_effect=fake_build_swap_calldata), \
             patch.object(trade_executor, "_build_approval_calldata", side_effect=fake_build_approval_calldata), \
             patch.object(trade_executor, "_submit_contract_execution", return_value="0xtxid"):
            mock_dcw.TransactionsApi.return_value = MagicMock()
            result = execute_trade(
                wallet_id="w1", action="SELL", target_asset_symbol="BTC",
                amount="2.0", recipient_address="0xaddr", current_price=100.0,
            )

        assert result == "0xtxid"
        # amount=2.0 (within the 0.5-2.0 clamp) at price=100 -> 2.0 / 100 = 0.02 BTC
        assert captured["swap_amount"] == Decimal("0.02")
        assert captured["approval_amount"] == Decimal("0.02")

    def test_buy_amount_stays_in_usd_not_converted(self, monkeypatch):
        monkeypatch.setattr(trade_executor, "TRADE_DRY_RUN", False)
        monkeypatch.setattr(trade_executor, "initialize_circle_client", lambda: MagicMock())

        captured = {}

        def fake_build_swap_calldata(action, target_asset_symbol, amount, recipient_address):
            captured["swap_amount"] = amount
            return "0xcalldata"

        def fake_build_approval_calldata(token_symbol, amount):
            captured["approval_amount"] = amount
            return ("0xtoken", "0xapprovecalldata")

        with patch.object(trade_executor, "developer_controlled_wallets") as mock_dcw, \
             patch.object(trade_executor, "_build_swap_calldata", side_effect=fake_build_swap_calldata), \
             patch.object(trade_executor, "_build_approval_calldata", side_effect=fake_build_approval_calldata), \
             patch.object(trade_executor, "_submit_contract_execution", return_value="0xtxid"):
            mock_dcw.TransactionsApi.return_value = MagicMock()
            # No current_price passed at all — BUY must not need it.
            result = execute_trade(
                wallet_id="w1", action="BUY", target_asset_symbol="BTC",
                amount="1.5", recipient_address="0xaddr",
            )

        assert result == "0xtxid"
        assert captured["swap_amount"] == Decimal("1.5")
        assert captured["approval_amount"] == Decimal("1.5")
