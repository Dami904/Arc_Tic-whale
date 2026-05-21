import pytest
from backend.market_data import _format_change, _percent_change


class TestFormatChange:
    def test_positive_change(self):
        assert _format_change(2.34) == "+2.34%"

    def test_negative_change(self):
        assert _format_change(-3.21) == "-3.21%"

    def test_zero_change(self):
        assert _format_change(0.0) == "+0.00%"

    def test_small_positive(self):
        assert _format_change(0.05) == "+0.05%"

    def test_small_negative(self):
        assert _format_change(-0.05) == "-0.05%"

    def test_large_positive(self):
        assert _format_change(145.8) == "+145.80%"

    def test_large_negative(self):
        assert _format_change(-89.4) == "-89.40%"


class TestPercentChange:
    def test_normal_increase(self):
        assert _percent_change(110, 100) == 10.0

    def test_normal_decrease(self):
        assert _percent_change(90, 100) == -10.0

    def test_no_change(self):
        assert _percent_change(100, 100) == 0.0

    def test_from_zero_previous(self):
        assert _percent_change(100, 0) == 0.0

    def test_from_none_previous(self):
        assert _percent_change(100, None) == 0.0

    def test_doubling(self):
        assert _percent_change(200, 100) == 100.0

    def test_halving(self):
        assert _percent_change(50, 100) == -50.0


class TestMarketDataStructure:
    def test_market_data_has_required_keys(self, sample_market_data):
        for symbol in ("BTC", "ETH", "SOL"):
            asset = sample_market_data[symbol]
            assert "PRICE" in asset
            assert "24H_CHANGE" in asset
            assert "TYPE" in asset
            assert "SOURCE" in asset
            assert asset["TYPE"] == "CRYPTO"
            assert asset["SOURCE"] == "CoinGecko"

    def test_market_data_stock_keys(self, sample_market_data):
        aapl = sample_market_data["AAPL"]
        assert aapl["TYPE"] == "STOCK"
        assert aapl["SOURCE"] == "Yahoo Finance"
        assert isinstance(aapl["PRICE"], (int, float))

    def test_macro_news_present(self, sample_market_data):
        assert "MACRO_NEWS" in sample_market_data
        assert isinstance(sample_market_data["MACRO_NEWS"], str)
        assert len(sample_market_data["MACRO_NEWS"]) > 0

    def test_price_types(self, sample_market_data):
        for symbol in ("BTC", "ETH", "SOL", "AAPL"):
            price = sample_market_data[symbol]["PRICE"]
            assert isinstance(price, (int, float)), f"{symbol} price should be numeric"
            assert price >= 0, f"{symbol} price should be non-negative"

    def test_change_format(self, sample_market_data):
        for symbol in ("BTC", "ETH", "SOL", "AAPL"):
            change = sample_market_data[symbol]["24H_CHANGE"]
            assert change.endswith("%"), f"{symbol} 24H_CHANGE should end with %"
            assert change[0] in ("+", "-"), f"{symbol} 24H_CHANGE should start with + or -"
