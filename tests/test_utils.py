import pytest
from backend.utils import parse_ai_decision


class TestParseAIDecision:
    def test_parse_buy_eth(self):
        response = "DECISION: BUY ETH\nREASON: ETH showing strong support at $3,400."
        result = parse_ai_decision(response)
        assert result["decision"] == "BUY"
        assert result["asset"] == "ETH"
        assert "strong support" in result["reason"]

    def test_parse_sell_btc(self):
        response = "DECISION: SELL BTC\nREASON: BTC overbought above $70K."
        result = parse_ai_decision(response)
        assert result["decision"] == "SELL"
        assert result["asset"] == "BTC"

    def test_parse_hold(self):
        response = "DECISION: HOLD\nREASON: No clear signal in current market conditions."
        result = parse_ai_decision(response)
        assert result["decision"] == "HOLD"
        assert result["asset"] is None

    def test_parse_buy_sol_defaults_to_hold(self):
        # SOL has no on-chain contract; the parser must not silently trade
        # a different asset.
        response = "DECISION: BUY SOL\nREASON: SOL breakout above resistance."
        result = parse_ai_decision(response)
        assert result["decision"] == "HOLD"
        assert result["asset"] is None

    def test_parse_case_insensitive(self):
        response = "decision: buy eth\nreason: dip buy opportunity."
        result = parse_ai_decision(response)
        assert result["decision"] == "BUY"
        assert result["asset"] == "ETH"

    def test_parse_no_asset_with_buy_defaults_to_hold(self):
        response = "DECISION: BUY\nREASON: General market dip."
        result = parse_ai_decision(response)
        assert result["decision"] == "HOLD"
        assert result["asset"] is None

    def test_parse_invalid_decision_defaults_hold(self):
        response = "Some random text without a decision."
        result = parse_ai_decision(response)
        assert result["decision"] == "HOLD"
        assert result["asset"] is None

    def test_parse_empty_string(self):
        result = parse_ai_decision("")
        assert result["decision"] == "HOLD"
        assert result["asset"] is None

    def test_parse_none_input(self):
        result = parse_ai_decision(None)
        assert result["decision"] == "HOLD"
        assert result["asset"] is None

    def test_parse_unsupported_asset_defaults_to_hold(self):
        response = "DECISION: BUY DOGE\nREASON: Doge mooning."
        result = parse_ai_decision(response)
        assert result["decision"] == "HOLD"
        assert result["asset"] is None

    def test_parse_reason_with_newlines(self):
        response = "DECISION: HOLD\nREASON: Multiple factors:\n1. High volatility\n2. Low volume\nWaiting for confirmation."
        result = parse_ai_decision(response)
        assert result["decision"] == "HOLD"
        assert "Multiple factors" in result["reason"]

    def test_parse_extra_whitespace(self):
        response = "  DECISION:   BUY   ETH  \nREASON:  Dip.  "
        result = parse_ai_decision(response)
        assert result["decision"] == "BUY"
        assert result["asset"] == "ETH"
