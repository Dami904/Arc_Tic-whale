from unittest.mock import patch

import pytest


@pytest.fixture
def mock_trade_cycle():
    with patch("backend.trade_service.get_current_market_state") as mock_market, \
         patch("backend.trade_service.ask_conservative_whale") as mock_ai, \
         patch("backend.trade_service.parse_ai_decision") as mock_parse, \
         patch("backend.trade_service.execute_trade") as mock_exec, \
         patch("backend.trade_service.mirror_agent_trade") as mock_mirror, \
         patch("backend.trade_service.generate_canteen_post") as mock_social, \
         patch("backend.trade_service.log_social_post") as mock_social_log, \
         patch("backend.trade_service.log_trade") as mock_log, \
         patch("backend.trade_service.init_db") as mock_init_db, \
         patch("backend.trade_service.is_kill_switch_active", return_value=False) as mock_kill:
        yield {
            "market": mock_market,
            "ai": mock_ai,
            "parse": mock_parse,
            "exec": mock_exec,
            "mirror": mock_mirror,
            "social": mock_social,
            "social_log": mock_social_log,
            "log": mock_log,
            "init_db": mock_init_db,
            "kill": mock_kill,
        }


class TestTradeDecisionFlow:
    def test_buy_flow(self, mock_trade_cycle):
        mock_trade_cycle["market"].return_value = {"BTC": {"PRICE": 68000}}
        mock_trade_cycle["ai"].return_value = "DECISION: BUY BTC\nREASON: Dip."
        mock_trade_cycle["parse"].return_value = {"decision": "BUY", "asset": "BTC", "reason": "Dip."}
        mock_trade_cycle["exec"].return_value = "0xdeadbeef"

        from backend.trade_service import run_trade_cycle
        result = run_trade_cycle(wallet_id="test-wallet", rate_limit_sleep=0)

        assert result["status"] == "success"
        assert result["action"] == "BUY"
        assert result["asset"] == "BTC"
        assert result["tx_hash"] == "0xdeadbeef"

        mock_trade_cycle["exec"].assert_called_once()
        mock_trade_cycle["mirror"].assert_called_once()
        mock_trade_cycle["social"].assert_called_once()

    def test_sell_flow(self, mock_trade_cycle):
        mock_trade_cycle["market"].return_value = {"ETH": {"PRICE": 3500}}
        mock_trade_cycle["ai"].return_value = "DECISION: SELL ETH\nREASON: Take profit."
        mock_trade_cycle["parse"].return_value = {"decision": "SELL", "asset": "ETH", "reason": "Take profit."}
        mock_trade_cycle["exec"].return_value = "0xcafebabe"

        from backend.trade_service import run_trade_cycle
        result = run_trade_cycle(wallet_id="test-wallet", rate_limit_sleep=0)

        assert result["status"] == "success"
        assert result["action"] == "SELL"
        assert result["asset"] == "ETH"
        assert result["tx_hash"] == "0xcafebabe"

        mock_trade_cycle["exec"].assert_called_once()
        mock_trade_cycle["mirror"].assert_called_once()
        mock_trade_cycle["social"].assert_called_once()

    def test_hold_flow(self, mock_trade_cycle):
        mock_trade_cycle["market"].return_value = {"BTC": {"PRICE": 68000}}
        mock_trade_cycle["ai"].return_value = "DECISION: HOLD\nREASON: No clear signal."
        mock_trade_cycle["parse"].return_value = {"decision": "HOLD", "asset": None, "reason": "No clear signal."}

        from backend.trade_service import run_trade_cycle
        result = run_trade_cycle(wallet_id="test-wallet", rate_limit_sleep=0)

        assert result["status"] == "hold"
        assert result["action"] == "HOLD"
        assert result["tx_hash"] is None

        mock_trade_cycle["exec"].assert_not_called()
        mock_trade_cycle["mirror"].assert_not_called()
        mock_trade_cycle["social"].assert_not_called()

    def test_execution_failure(self, mock_trade_cycle):
        mock_trade_cycle["market"].return_value = {"SOL": {"PRICE": 150}}
        mock_trade_cycle["ai"].return_value = "DECISION: BUY SOL\nREASON: Breakout."
        mock_trade_cycle["parse"].return_value = {"decision": "BUY", "asset": "SOL", "reason": "Breakout."}
        mock_trade_cycle["exec"].return_value = None

        from backend.trade_service import run_trade_cycle
        result = run_trade_cycle(wallet_id="test-wallet", rate_limit_sleep=0)

        assert result["status"] == "error"
        assert result["tx_hash"] is None

    def test_missing_wallet_id(self, mock_trade_cycle):
        from backend.trade_service import run_trade_cycle
        with patch("backend.trade_service.AGENT_WALLET_ID", None):
            result = run_trade_cycle(wallet_id=None, rate_limit_sleep=0)
            assert result["status"] == "error"
            assert "AGENT_WALLET_ID missing" in result["reason"]

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


class TestFallbackDecision:
    def test_fallback_buy_on_dip(self, sample_negative_market):
        from backend.agents import fallback_market_decision
        result = fallback_market_decision(sample_negative_market)
        assert "BUY" in result
        assert "BTC" in result or "ETH" in result

    def test_fallback_hold_on_flat(self, sample_flat_market):
        from backend.agents import fallback_market_decision
        result = fallback_market_decision(sample_flat_market)
        assert "HOLD" in result

    def test_fallback_hold_on_empty(self):
        from backend.agents import fallback_market_decision
        result = fallback_market_decision({})
        assert "HOLD" in result
