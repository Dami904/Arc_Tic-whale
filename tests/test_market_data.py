from unittest.mock import MagicMock, patch

import pytest

import backend.market_data as market_data_module
from backend.market_data import _format_change, _get_crypto_data, _get_news_headlines, _percent_change


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


class TestGetCryptoData:
    """
    Regression coverage for the /coins/markets fix: the old /simple/price
    endpoint silently ignored include_7d_change and always returned 0.00%.
    """

    def _mock_markets_response(self):
        return [
            {
                "id": "bitcoin", "current_price": 62946.0,
                "price_change_percentage_24h_in_currency": -3.1,
                "price_change_percentage_7d_in_currency": -5.2,
                "price_change_percentage_1y_in_currency": -47.0,
            },
            {
                "id": "ethereum", "current_price": 1868.25,
                "price_change_percentage_24h_in_currency": -4.4,
                "price_change_percentage_7d_in_currency": -3.6,
                "price_change_percentage_1y_in_currency": -51.7,
            },
            {
                "id": "euro-coin", "current_price": 1.14,
                "price_change_percentage_24h_in_currency": -0.1,
                "price_change_percentage_7d_in_currency": -0.4,
                "price_change_percentage_1y_in_currency": -2.5,
            },
        ]

    def test_7d_change_is_real_not_always_zero(self, monkeypatch):
        monkeypatch.setattr(market_data_module, "_cache", {})
        monkeypatch.setattr(market_data_module, "_cache_ts", 0.0)
        mock_response = MagicMock()
        mock_response.json.return_value = self._mock_markets_response()
        mock_response.raise_for_status.return_value = None

        with patch.object(market_data_module.httpx, "get", return_value=mock_response):
            data = _get_crypto_data()

        assert data["BTC"]["7D_CHANGE"] == "-5.20%"
        assert data["ETH"]["7D_CHANGE"] == "-3.60%"
        assert data["EURC"]["7D_CHANGE"] == "-0.40%"

    def test_1y_change_comes_from_live_data(self, monkeypatch):
        monkeypatch.setattr(market_data_module, "_cache", {})
        monkeypatch.setattr(market_data_module, "_cache_ts", 0.0)
        mock_response = MagicMock()
        mock_response.json.return_value = self._mock_markets_response()
        mock_response.raise_for_status.return_value = None

        with patch.object(market_data_module.httpx, "get", return_value=mock_response):
            data = _get_crypto_data()

        assert data["BTC"]["1Y_CHANGE"] == "-47.00%"

    def test_falls_back_to_hardcoded_prices_on_api_failure(self, monkeypatch):
        monkeypatch.setattr(market_data_module, "_cache", {})
        monkeypatch.setattr(market_data_module, "_cache_ts", 0.0)

        with patch.object(market_data_module.httpx, "get", side_effect=Exception("network down")):
            data = _get_crypto_data()

        assert data["BTC"]["SOURCE"] == "CoinGecko (Cached)"
        assert data["BTC"]["PRICE"] > 0


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


class TestGetNewsHeadlines:
    def _mock_rss(self, titles):
        items = "".join(f"<item><title>{t}</title></item>" for t in titles)
        xml = f"<rss><channel>{items}</channel></rss>"
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.content = xml.encode("utf-8")
        return mock_response

    def test_combines_headlines_from_both_feeds(self, monkeypatch):
        monkeypatch.setattr(market_data_module, "_news_cache", [])
        monkeypatch.setattr(market_data_module, "_news_cache_ts", 0.0)
        feed_a = self._mock_rss(["Headline A1", "Headline A2"])
        feed_b = self._mock_rss(["Headline B1"])

        def fake_get(url, **kwargs):
            return feed_a if "coindesk" in url else feed_b

        with patch.object(market_data_module.httpx, "get", side_effect=fake_get):
            headlines = _get_news_headlines()

        assert "Headline A1" in headlines
        assert "Headline B1" in headlines

    def test_caps_total_headlines(self, monkeypatch):
        monkeypatch.setattr(market_data_module, "_news_cache", [])
        monkeypatch.setattr(market_data_module, "_news_cache_ts", 0.0)
        feed = self._mock_rss([f"H{i}" for i in range(8)])

        with patch.object(market_data_module.httpx, "get", return_value=feed):
            headlines = _get_news_headlines()

        assert len(headlines) <= market_data_module._MAX_HEADLINES

    def test_returns_empty_list_when_all_feeds_fail(self, monkeypatch):
        monkeypatch.setattr(market_data_module, "_news_cache", [])
        monkeypatch.setattr(market_data_module, "_news_cache_ts", 0.0)

        with patch.object(market_data_module.httpx, "get", side_effect=Exception("network down")):
            headlines = _get_news_headlines()

        assert headlines == []

    def test_failed_fetch_does_not_poison_cache(self, monkeypatch):
        """An all-feeds-down blip shouldn't lock out headlines for 30 min once feeds recover."""
        monkeypatch.setattr(market_data_module, "_news_cache", [])
        monkeypatch.setattr(market_data_module, "_news_cache_ts", 0.0)

        with patch.object(market_data_module.httpx, "get", side_effect=Exception("down")):
            _get_news_headlines()

        assert market_data_module._news_cache == []
        assert market_data_module._news_cache_ts == 0.0
