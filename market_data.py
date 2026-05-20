# market_data.py
def get_current_market_state():
    """
    For testing: Returns simulated market data. 
    Later, we will connect this to a real API.
    """
    return {
        "ETH_PRICE": 3100,
        "24H_CHANGE": "-58.5%",
        "MACRO_NEWS": "CPI data shows higher than expected inflation."
    }