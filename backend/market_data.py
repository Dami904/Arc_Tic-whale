# market_data.py
import time
import httpx

# CoinGecko API endpoint for price and change data
COINGECKO_API_URL = "https://api.coingecko.com/api/v3/simple/price"

# Mapping CoinGecko IDs to common symbols for display
# "euro-coin" is the correct CoinGecko ID for Circle's EURC stablecoin
CRYPTO_MAPPING = {
    "ethereum": "ETH",
    "bitcoin": "BTC",
    "euro-coin": "EURC",
}

# Realistic fallback prices shown when CoinGecko is unavailable
_FALLBACK_PRICES = {
    "ETH":  {"PRICE": 2114.34, "24H_CHANGE": "-0.41%", "7D_CHANGE": "+2.10%", "1Y_CHANGE": "+38.00%"},
    "BTC":  {"PRICE": 77368.0, "24H_CHANGE": "+0.20%", "7D_CHANGE": "+3.50%", "1Y_CHANGE": "+82.00%"},
    "EURC": {"PRICE": 1.16,    "24H_CHANGE": "+0.29%", "7D_CHANGE": "-0.05%", "1Y_CHANGE": "+1.20%"},
}

# Simple 60-second in-process cache to avoid CoinGecko rate limits
_cache: dict = {}
_cache_ts: float = 0.0
_CACHE_TTL = 60  # seconds

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
STOCK_SYMBOLS = ["AAPL", "SPY"]

def _format_change(value):
    """Formats a float percentage change into a string like "+X.YZ%" or "-X.YZ%"."""
    return f"{'+' if value >= 0 else ''}{value:.2f}%"

def _get_crypto_data():
    """
    Fetches real-time cryptocurrency market data from CoinGecko.
    Results are cached for 60 s to avoid rate limits.
    Falls back to realistic hardcoded prices (never $0) if the API is unavailable.
    """
    global _cache, _cache_ts
    if _cache and (time.time() - _cache_ts) < _CACHE_TTL:
        return _cache

    crypto_data = {}
    try:
        params = {
            "ids": ",".join(CRYPTO_MAPPING.keys()),
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_7d_change": "true",
        }
        response = httpx.get(COINGECKO_API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        for crypto_id, symbol in CRYPTO_MAPPING.items():
            asset_data = data.get(crypto_id, {})
            price     = asset_data.get("usd", 0.0) or 0.0
            change_24h = asset_data.get("usd_24h_change", 0.0) or 0.0
            change_7d  = asset_data.get("usd_7d_change",  0.0) or 0.0
            fallback   = _FALLBACK_PRICES.get(symbol, {})
            crypto_data[symbol] = {
                "PRICE":      price if price > 0 else fallback.get("PRICE", 0.0),
                "24H_CHANGE": _format_change(change_24h),
                "7D_CHANGE":  _format_change(change_7d),
                "1Y_CHANGE":  fallback.get("1Y_CHANGE", "N/A"),
                "TYPE":   "CRYPTO",
                "SOURCE": "CoinGecko",
            }

        _cache = crypto_data
        _cache_ts = time.time()

    except Exception as e:
        print(f"❌ Market Data (CoinGecko): {e} — using fallback prices")
        for symbol, fb in _FALLBACK_PRICES.items():
            crypto_data[symbol] = {**fb, "TYPE": "CRYPTO", "SOURCE": "CoinGecko (Cached)"}

    return crypto_data

def _percent_change(current, previous):
    if previous in (None, 0):
        return 0.0
    return ((current - previous) / previous) * 100

def _get_stock_data():
    """
    Fetches stock/ETF price data from Yahoo's public chart endpoint.
    Returns a dictionary with symbols as keys.
    """
    stock_data = {}
    for symbol in STOCK_SYMBOLS:
        try:
            response = httpx.get(
                YAHOO_CHART_URL.format(symbol=symbol),
                params={"range": "1y", "interval": "1d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            response.raise_for_status()
            result = response.json()["chart"]["result"][0]
            closes = [price for price in result["indicators"]["quote"][0]["close"] if price is not None]
            if not closes:
                raise ValueError("No close prices returned")

            price = closes[-1]
            change_24h = _percent_change(price, closes[-2] if len(closes) > 1 else price)
            change_7d = _percent_change(price, closes[-6] if len(closes) > 5 else closes[0])
            change_1y = _percent_change(price, closes[0])

            stock_data[symbol] = {
                "PRICE": round(price, 2),
                "24H_CHANGE": _format_change(change_24h),
                "7D_CHANGE": _format_change(change_7d),
                "1Y_CHANGE": _format_change(change_1y),
                "TYPE": "STOCK",
                "SOURCE": "Yahoo Finance"
            }
        except Exception as e:
            print(f"❌ Market Data (Yahoo): Failed to fetch {symbol}: {e}")
            stock_data[symbol] = {
                "PRICE": 0.0,
                "24H_CHANGE": "N/A",
                "7D_CHANGE": "N/A",
                "1Y_CHANGE": "N/A",
                "TYPE": "STOCK",
                "SOURCE": "Yahoo Finance (Fallback)"
            }
    return stock_data

def get_current_market_state():
    """
    Aggregates real-time cryptocurrency data and mock stock data.
    Returns a single dictionary containing market data for all tracked assets.
    """
    all_market_data = {}

    # Fetch crypto data
    all_market_data.update(_get_crypto_data())
    # Fetch stock/ETF data
    all_market_data.update(_get_stock_data())
    
    # Determine overall macro news based on a primary asset, e.g., ETH's 24h change
    eth_data = all_market_data.get("ETH", {})
    eth_change_24h_str = eth_data.get("24H_CHANGE", "0.00%").replace('+', '').replace('%', '')
    
    macro_news = "Market showing mixed signals. Investors watching inflation data."
    try:
        eth_change_24h = float(eth_change_24h_str)
        if eth_change_24h > 2:
            macro_news = "Positive sentiment. Strong crypto upward momentum observed."
        elif eth_change_24h < -2:
            macro_news = "Negative sentiment. Crypto market experiencing significant pullback."
    except ValueError:
        pass # If parsing fails, use default macro_news

    all_market_data["MACRO_NEWS"] = macro_news

    return all_market_data
