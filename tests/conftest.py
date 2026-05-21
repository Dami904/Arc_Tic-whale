import pytest


@pytest.fixture
def sample_market_data():
    return {
        "BTC": {
            "PRICE": 68420.50,
            "24H_CHANGE": "+2.34%",
            "7D_CHANGE": "-1.12%",
            "1Y_CHANGE": "+145.80%",
            "TYPE": "CRYPTO",
            "SOURCE": "CoinGecko",
        },
        "ETH": {
            "PRICE": 3520.10,
            "24H_CHANGE": "-3.21%",
            "7D_CHANGE": "+5.67%",
            "1Y_CHANGE": "+89.40%",
            "TYPE": "CRYPTO",
            "SOURCE": "CoinGecko",
        },
        "SOL": {
            "PRICE": 148.75,
            "24H_CHANGE": "+1.05%",
            "7D_CHANGE": "-4.50%",
            "1Y_CHANGE": "+320.00%",
            "TYPE": "CRYPTO",
            "SOURCE": "CoinGecko",
        },
        "AAPL": {
            "PRICE": 198.50,
            "24H_CHANGE": "+0.45%",
            "7D_CHANGE": "+2.10%",
            "1Y_CHANGE": "+28.30%",
            "TYPE": "STOCK",
            "SOURCE": "Yahoo Finance",
        },
        "MACRO_NEWS": "Positive sentiment. Strong crypto upward momentum observed.",
    }


@pytest.fixture
def sample_negative_market():
    return {
        "BTC": {
            "PRICE": 62000.00,
            "24H_CHANGE": "-3.50%",
            "7D_CHANGE": "-8.20%",
            "TYPE": "CRYPTO",
            "SOURCE": "CoinGecko",
        },
        "ETH": {
            "PRICE": 3100.00,
            "24H_CHANGE": "-5.10%",
            "7D_CHANGE": "-12.40%",
            "TYPE": "CRYPTO",
            "SOURCE": "CoinGecko",
        },
        "SOL": {
            "PRICE": 130.00,
            "24H_CHANGE": "-0.80%",
            "7D_CHANGE": "-6.30%",
            "TYPE": "CRYPTO",
            "SOURCE": "CoinGecko",
        },
        "MACRO_NEWS": "Negative sentiment. Crypto market experiencing significant pullback.",
    }


@pytest.fixture
def sample_flat_market():
    return {
        "BTC": {
            "PRICE": 68420.50,
            "24H_CHANGE": "+0.34%",
            "7D_CHANGE": "-1.12%",
            "TYPE": "CRYPTO",
            "SOURCE": "CoinGecko",
        },
        "ETH": {
            "PRICE": 3520.10,
            "24H_CHANGE": "+1.21%",
            "7D_CHANGE": "+0.67%",
            "TYPE": "CRYPTO",
            "SOURCE": "CoinGecko",
        },
        "SOL": {
            "PRICE": 148.75,
            "24H_CHANGE": "+0.05%",
            "7D_CHANGE": "-0.50%",
            "TYPE": "CRYPTO",
            "SOURCE": "CoinGecko",
        },
        "MACRO_NEWS": "Market stable. No strong signals.",
    }
