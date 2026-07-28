from unittest.mock import MagicMock, patch

import backend.agents as agents_module
from backend.agents import ask_agent
from backend.performance import get_open_positions


class TestAskAgentRetryAndLogging:
    def test_retries_once_then_returns_success(self, monkeypatch):
        monkeypatch.setattr(agents_module, "DEV_MODE", False)
        mock_response = MagicMock()
        mock_response.text = "DECISION: HOLD\nREASON: ok"
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = [Exception("rate limited"), mock_response]

        with patch.object(agents_module, "initialize_agent", return_value=mock_llm), \
             patch.object(agents_module.time, "sleep") as mock_sleep:
            result = ask_agent({"BTC": {"PRICE": 1}}, agent_name="Conservative_Whale")

        assert result == "DECISION: HOLD\nREASON: ok"
        assert mock_llm.invoke.call_count == 2
        mock_sleep.assert_called_once()

    def test_falls_back_to_rule_engine_after_two_failures(self, monkeypatch):
        monkeypatch.setattr(agents_module, "DEV_MODE", False)
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("down")

        with patch.object(agents_module, "initialize_agent", return_value=mock_llm), \
             patch.object(agents_module.time, "sleep"):
            result = ask_agent({"ETH": {"24H_CHANGE": "-3%"}}, agent_name="Conservative_Whale")

        assert result.startswith("DECISION:")
        assert mock_llm.invoke.call_count == 2

    def test_logs_each_failed_attempt(self, monkeypatch):
        monkeypatch.setattr(agents_module, "DEV_MODE", False)
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("boom")

        with patch.object(agents_module, "initialize_agent", return_value=mock_llm), \
             patch.object(agents_module.time, "sleep"), \
             patch.object(agents_module.log, "error") as mock_log_error:
            ask_agent({}, agent_name="Conservative_Whale")

        assert mock_log_error.call_count == 2


class TestPositionStateInPrompt:
    def _system_prompt_for(self, open_positions):
        mock_response = MagicMock()
        mock_response.text = "DECISION: HOLD\nREASON: ok"
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = mock_response

        with patch.object(agents_module, "DEV_MODE", False), \
             patch.object(agents_module, "initialize_agent", return_value=mock_llm):
            ask_agent({"BTC": {"PRICE": 1}}, agent_name="Aggressive_Degen", open_positions=open_positions)

        messages = mock_llm.invoke.call_args[0][0]
        return messages[0].content

    def test_no_position_state_says_fully_in_usdc(self):
        prompt = self._system_prompt_for(None)
        assert "fully in USDC" in prompt
        assert "Never BUY if you already hold a position" in prompt

    def test_open_position_is_named_with_entry_price(self):
        prompt = self._system_prompt_for({"BTC": 65000.0})
        assert "BTC (entered at $65,000.00)" in prompt


class TestGetOpenPositions:
    def _rows(self, *actions):
        return [{"action": a, "asset": s, "price": p, "timestamp": f"t{i}"}
                for i, (a, s, p) in enumerate(actions)]

    def test_buy_opens_position(self):
        with patch("backend.performance.get_trade_rows_for_performance",
                   return_value=self._rows(("BUY", "BTC", 65000.0))):
            assert get_open_positions("A") == {"BTC": 65000.0}

    def test_sell_closes_position(self):
        with patch("backend.performance.get_trade_rows_for_performance",
                   return_value=self._rows(("BUY", "BTC", 65000.0), ("SELL", "BTC", 70000.0))):
            assert get_open_positions("A") == {}

    def test_repeat_buy_keeps_first_entry_price(self):
        with patch("backend.performance.get_trade_rows_for_performance",
                   return_value=self._rows(("BUY", "BTC", 65000.0), ("BUY", "BTC", 60000.0))):
            assert get_open_positions("A") == {"BTC": 65000.0}

    def test_hold_rows_ignored(self):
        with patch("backend.performance.get_trade_rows_for_performance",
                   return_value=self._rows(("HOLD", "BTC", 65000.0))):
            assert get_open_positions("A") == {}
