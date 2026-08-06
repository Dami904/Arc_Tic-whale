# market_data.py
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor

import httpx

from backend.config import COINGECKO_API_KEY

# CoinGecko markets endpoint - the only endpoint that actually returns 7d/1y
# change data ("/simple/price" silently ignores include_7d_change and always
# reports 0.00%, which is why the fix uses this endpoint instead).
COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"

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

# Simple 60-second in-process cache to avoid CoinGecko/Yahoo rate limits
# and redundant round-trips (get_current_market_state() is called once per
# agent in several API loops - see backend/performance.py's current_prices
# sharing pattern for the other half of that fix).
_cache: dict = {}
_cache_ts: float = 0.0
_stock_cache: dict = {}
_stock_cache_ts: float = 0.0
_CACHE_TTL = 60  # seconds

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
STOCK_SYMBOLS = ["AAPL", "SPY"]

# Keyless RSS feeds - no API key/signup needed, unlike CryptoPanic/Alpha
# Vantage. Headlines only (no sentiment score); Gemini reads and interprets
# them itself in the agent prompt (see backend/agents.py).
NEWS_FEED_URLS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
]
_HEADLINES_PER_FEED = 5
_MAX_HEADLINES = 6
_news_cache: list = []
_news_cache_ts: float = 0.0
_NEWS_CACHE_TTL = 1800  # 30 min - news moves slower than price, and trade cycles run every 2h

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
            "vs_currency": "usd",
            "ids": ",".join(CRYPTO_MAPPING.keys()),
            "price_change_percentage": "24h,7d,1y",
        }
        headers = {"x-cg-demo-api-key": COINGECKO_API_KEY} if COINGECKO_API_KEY else {}
        response = httpx.get(COINGECKO_MARKETS_URL, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        rows_by_id = {row["id"]: row for row in response.json()}

        for crypto_id, symbol in CRYPTO_MAPPING.items():
            asset_data = rows_by_id.get(crypto_id, {})
            price      = asset_data.get("current_price", 0.0) or 0.0
            change_24h = asset_data.get("price_change_percentage_24h_in_currency", 0.0) or 0.0
            change_7d  = asset_data.get("price_change_percentage_7d_in_currency", 0.0) or 0.0
            change_1y  = asset_data.get("price_change_percentage_1y_in_currency")
            fallback   = _FALLBACK_PRICES.get(symbol, {})
            crypto_data[symbol] = {
                "PRICE":      price if price > 0 else fallback.get("PRICE", 0.0),
                "24H_CHANGE": _format_change(change_24h),
                "7D_CHANGE":  _format_change(change_7d),
                "1Y_CHANGE":  _format_change(change_1y) if change_1y is not None else fallback.get("1Y_CHANGE", "N/A"),
                "TYPE":   "CRYPTO",
                "SOURCE": "CoinGecko",
            }

        _cache = crypto_data
        _cache_ts = time.time()

    except Exception as e:
        print(f"❌ Market Data (CoinGecko): {e} - using fallback prices")
        for symbol, fb in _FALLBACK_PRICES.items():
            crypto_data[symbol] = {**fb, "TYPE": "CRYPTO", "SOURCE": "CoinGecko (Cached)"}

    return crypto_data

def _percent_change(current, previous):
    if previous in (None, 0):
        return 0.0
    return ((current - previous) / previous) * 100

def _fetch_one_stock(symbol):
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

        return symbol, {
            "PRICE": round(price, 2),
            "24H_CHANGE": _format_change(change_24h),
            "7D_CHANGE": _format_change(change_7d),
            "1Y_CHANGE": _format_change(change_1y),
            "TYPE": "STOCK",
            "SOURCE": "Yahoo Finance"
        }
    except Exception as e:
        print(f"❌ Market Data (Yahoo): Failed to fetch {symbol}: {e}")
        return symbol, {
            "PRICE": 0.0,
            "24H_CHANGE": "N/A",
            "7D_CHANGE": "N/A",
            "1Y_CHANGE": "N/A",
            "TYPE": "STOCK",
            "SOURCE": "Yahoo Finance (Fallback)"
        }

def _get_stock_data():
    """
    Fetches stock/ETF price data from Yahoo's public chart endpoint, one
    request per symbol issued concurrently (they're independent and each
    can take several seconds - sequential fetching was the main contributor
    to /agents taking ~20-30s on a cold cache).
    Results are cached for 60 s - same rationale as _get_crypto_data().
    Returns a dictionary with symbols as keys.
    """
    global _stock_cache, _stock_cache_ts
    if _stock_cache and (time.time() - _stock_cache_ts) < _CACHE_TTL:
        return _stock_cache

    with ThreadPoolExecutor(max_workers=len(STOCK_SYMBOLS)) as pool:
        stock_data = dict(pool.map(_fetch_one_stock, STOCK_SYMBOLS))

    _stock_cache = stock_data
    _stock_cache_ts = time.time()
    return stock_data

def _fetch_one_feed(url):
    try:
        # follow_redirects: coindesk's feed URL 308-redirects to a trailing-
        # slash-less path; httpx doesn't follow redirects by default.
        response = httpx.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10, follow_redirects=True)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        titles = [
            title.text.strip()
            for title in root.findall("./channel/item/title")
            if title.text and title.text.strip()
        ]
        return titles[:_HEADLINES_PER_FEED]
    except Exception as e:
        print(f"Market Data (News RSS): Failed to fetch {url}: {e}")
        return []

def _get_news_headlines():
    """
    Fetches recent crypto headlines from a couple of keyless RSS feeds (no
    API key/signup, unlike CryptoPanic/Alpha Vantage). Best-effort: a feed
    that fails is just skipped, and an empty list here means the agent
    prompt simply omits the headlines section rather than erroring.
    Cached for 30 min.
    """
    global _news_cache, _news_cache_ts
    if _news_cache and (time.time() - _news_cache_ts) < _NEWS_CACHE_TTL:
        return _news_cache

    with ThreadPoolExecutor(max_workers=len(NEWS_FEED_URLS)) as pool:
        results = pool.map(_fetch_one_feed, NEWS_FEED_URLS)

    headlines = []
    for feed_titles in results:
        headlines.extend(feed_titles)
    headlines = headlines[:_MAX_HEADLINES]

    # Only cache a non-empty result - an all-feeds-down blip shouldn't lock
    # out headlines for the next 30 min once the feeds recover.
    if headlines:
        _news_cache = headlines
        _news_cache_ts = time.time()
    return headlines

def get_current_market_state():
    """
    Aggregates real-time cryptocurrency data and mock stock data.
    Returns a single dictionary containing market data for all tracked assets.
    """
    all_market_data = {}

    # Crypto (CoinGecko), stocks (Yahoo), and news (RSS) are independent
    # sources - fetch them concurrently instead of back-to-back.
    with ThreadPoolExecutor(max_workers=3) as pool:
        crypto_future = pool.submit(_get_crypto_data)
        stock_future = pool.submit(_get_stock_data)
        news_future = pool.submit(_get_news_headlines)
        all_market_data.update(crypto_future.result())
        all_market_data.update(stock_future.result())
        all_market_data["NEWS_HEADLINES"] = news_future.result()

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
