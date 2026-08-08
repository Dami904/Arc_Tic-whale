import sys
import types
from decimal import Decimal
from types import SimpleNamespace
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

    def test_live_buy_without_price_returns_none(self, monkeypatch):
        monkeypatch.setattr(trade_executor, "TRADE_DRY_RUN", False)
        result = execute_trade(
            wallet_id="w1", action="BUY", target_asset_symbol="BTC",
            amount="1.0", recipient_address="0xaddr", current_price=None,
        )
        assert result is None

    def test_sell_converts_usd_amount_to_asset_units(self, monkeypatch):
        monkeypatch.setattr(trade_executor, "TRADE_DRY_RUN", False)
        monkeypatch.setattr(trade_executor, "initialize_circle_client", lambda: MagicMock())

        captured = {}

        def fake_build_swap_calldata(action, target_asset_symbol, amount, recipient_address, current_price):
            captured["swap_amount"] = amount
            captured["swap_price"] = current_price
            return "0xcalldata"

        def fake_build_approval_calldata(token_symbol, amount):
            captured["approval_amount"] = amount
            return ("0xtoken", "0xapprovecalldata")

        with patch.object(trade_executor, "developer_controlled_wallets") as mock_dcw, \
             patch.object(trade_executor, "_build_swap_calldata", side_effect=fake_build_swap_calldata), \
             patch.object(trade_executor, "_build_approval_calldata", side_effect=fake_build_approval_calldata), \
             patch.object(trade_executor, "_submit_contract_execution", return_value="0xtxid"), \
             patch.object(trade_executor, "_wait_for_transaction_success", return_value=True):
            mock_dcw.TransactionsApi.return_value = MagicMock()
            result = execute_trade(
                wallet_id="w1", action="SELL", target_asset_symbol="BTC",
                amount="2.0", recipient_address="0xaddr", current_price=100.0,
            )

        assert result == "0xtxid"
        # amount=2.0 (within the 0.5-2.0 clamp) at price=100 -> 2.0 / 100 = 0.02 BTC
        assert captured["swap_amount"] == Decimal("0.02")
        assert captured["swap_price"] == 100.0
        assert captured["approval_amount"] == Decimal("0.02")

    def test_live_buy_amount_stays_in_usd_not_converted(self, monkeypatch):
        monkeypatch.setattr(trade_executor, "TRADE_DRY_RUN", False)
        monkeypatch.setattr(trade_executor, "initialize_circle_client", lambda: MagicMock())

        captured = {}

        def fake_build_swap_calldata(action, target_asset_symbol, amount, recipient_address, current_price):
            captured["swap_amount"] = amount
            captured["swap_price"] = current_price
            return "0xcalldata"

        def fake_build_approval_calldata(token_symbol, amount):
            captured["approval_amount"] = amount
            return ("0xtoken", "0xapprovecalldata")

        with patch.object(trade_executor, "developer_controlled_wallets") as mock_dcw, \
             patch.object(trade_executor, "_build_swap_calldata", side_effect=fake_build_swap_calldata), \
             patch.object(trade_executor, "_build_approval_calldata", side_effect=fake_build_approval_calldata), \
             patch.object(trade_executor, "_submit_contract_execution", return_value="0xtxid"), \
             patch.object(trade_executor, "_wait_for_transaction_success", return_value=True):
            mock_dcw.TransactionsApi.return_value = MagicMock()
            result = execute_trade(
                wallet_id="w1", action="BUY", target_asset_symbol="BTC",
                amount="1.5", recipient_address="0xaddr", current_price=68000.0,
            )

        assert result == "0xtxid"
        assert captured["swap_amount"] == Decimal("1.5")
        assert captured["swap_price"] == 68000.0
        assert captured["approval_amount"] == Decimal("1.5")

    def test_swap_is_not_built_when_approval_does_not_confirm(self, monkeypatch):
        monkeypatch.setattr(trade_executor, "TRADE_DRY_RUN", False)
        monkeypatch.setattr(trade_executor, "initialize_circle_client", lambda: MagicMock())

        with patch.object(trade_executor, "developer_controlled_wallets") as mock_dcw, \
             patch.object(trade_executor, "_build_swap_calldata") as mock_swap, \
             patch.object(trade_executor, "_build_approval_calldata", return_value=("0xtoken", "0xapprove")), \
             patch.object(trade_executor, "_submit_contract_execution", return_value="approval_tx"), \
             patch.object(trade_executor, "_wait_for_transaction_success", return_value=False):
            mock_dcw.TransactionsApi.return_value = MagicMock()
            result = execute_trade(
                wallet_id="w1", action="BUY", target_asset_symbol="BTC",
                amount="1.5", recipient_address="0xaddr", current_price=68000.0,
            )

        assert result is None
        mock_swap.assert_not_called()

    def test_failed_swap_confirmation_returns_none(self, monkeypatch):
        monkeypatch.setattr(trade_executor, "TRADE_DRY_RUN", False)
        monkeypatch.setattr(trade_executor, "initialize_circle_client", lambda: MagicMock())

        with patch.object(trade_executor, "developer_controlled_wallets") as mock_dcw, \
             patch.object(trade_executor, "_build_swap_calldata", return_value="0xswap"), \
             patch.object(trade_executor, "_build_approval_calldata", return_value=("0xtoken", "0xapprove")), \
             patch.object(trade_executor, "_submit_contract_execution", side_effect=["approval_tx", "swap_tx"]), \
             patch.object(trade_executor, "_wait_for_transaction_success", side_effect=[True, False]):
            mock_dcw.TransactionsApi.return_value = MagicMock()
            result = execute_trade(
                wallet_id="w1", action="BUY", target_asset_symbol="BTC",
                amount="1.5", recipient_address="0xaddr", current_price=68000.0,
            )

        assert result is None


class TestSwapSlippageMath:
    def test_buy_minimum_output_uses_target_asset_decimals(self):
        result = trade_executor._minimum_output_units(
            "BUY", "BTC", Decimal("1.0"), Decimal("68000")
        )

        assert result == 1455

    def test_sell_minimum_output_uses_usdc_decimals(self):
        result = trade_executor._minimum_output_units(
            "SELL", "ETH", Decimal("0.001"), Decimal("2000")
        )

        assert result == 1_980_000

    def test_build_swap_calldata_sets_buy_minimum_from_expected_output(self, monkeypatch):
        captured = {}
        monkeypatch.setitem(sys.modules, "web3", _fake_web3_module(captured))

        call_data = trade_executor._build_swap_calldata(
            "BUY",
            "BTC",
            Decimal("1.0"),
            "0x0000000000000000000000000000000000000001",
            Decimal("68000"),
        )

        params = captured["params"]
        assert call_data == "0xswap"
        assert params["amountIn"] == 1_000_000
        assert params["amountOutMinimum"] == 1455

    def test_build_swap_calldata_sets_sell_minimum_from_expected_output(self, monkeypatch):
        captured = {}
        monkeypatch.setitem(sys.modules, "web3", _fake_web3_module(captured))

        call_data = trade_executor._build_swap_calldata(
            "SELL",
            "ETH",
            Decimal("0.001"),
            "0x0000000000000000000000000000000000000001",
            Decimal("2000"),
        )

        params = captured["params"]
        assert call_data == "0xswap"
        assert params["amountIn"] == 1_000_000_000_000_000
        assert params["amountOutMinimum"] == 1_980_000


class TestTransactionConfirmation:
    def test_wait_returns_true_for_complete_state(self):
        api = MagicMock()
        api.get_transaction.return_value = _tx_response("COMPLETE")

        assert trade_executor._wait_for_transaction_success(api, "tx1", timeout_seconds=0, poll_seconds=0)

    def test_wait_returns_false_for_failed_state(self):
        api = MagicMock()
        api.get_transaction.return_value = _tx_response("FAILED")

        assert not trade_executor._wait_for_transaction_success(api, "tx1", timeout_seconds=0, poll_seconds=0)

    def test_wait_returns_false_on_timeout(self):
        api = MagicMock()
        api.get_transaction.return_value = _tx_response("SENT")

        assert not trade_executor._wait_for_transaction_success(api, "tx1", timeout_seconds=0, poll_seconds=0)


def _tx_response(state):
    return SimpleNamespace(data=SimpleNamespace(transaction=SimpleNamespace(state=state)))


def _fake_web3_module(captured):
    class FakeEncodedCall:
        def __init__(self, params):
            self.params = params

        def _encode_transaction_data(self):
            captured["params"] = self.params
            return "0xswap"

    class FakeFunctions:
        def exactInputSingle(self, params):
            return FakeEncodedCall(params)

    class FakeContract:
        functions = FakeFunctions()

    class FakeEth:
        def contract(self, address, abi):
            captured["contract_address"] = address
            captured["abi"] = abi
            return FakeContract()

    class FakeWeb3:
        def __init__(self):
            self.eth = FakeEth()

        @staticmethod
        def to_checksum_address(address):
            return address

    return types.SimpleNamespace(Web3=FakeWeb3)
