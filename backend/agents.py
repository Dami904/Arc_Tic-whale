# agents.py
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage
from backend.config import AGENT_DEV_MODE, GOOGLE_API_KEY
from backend.market_data import get_current_market_state

AGENT_PROFILES = {
    "Conservative_Whale": {
        "name": "Arc_Tic Whale",
        "avatar": "🐋",
        "risk": "low",
        "risk_label": "Conservative",
        "temperature": 0.2,
        "description": "Macro-driven, patient accumulator. Targets blue-chip assets with low drawdown tolerance.",
        "prompt": """
        You are 'Arc_Tic Whale', a conservative AI investing agent.
        You are highly risk-averse. You prefer holding stablecoins (USDC) and only buy major blue-chip assets like BTC and ETH when there is a confirmed market dip.
        You also consider broader market sentiment from stocks (AAPL, SPY) but prioritize crypto (BTC, ETH, EURC) for trading decisions. EURC is a euro-backed stablecoin — consider it a EUR/USD play.
        """,
    },
    "Macro_Economist": {
        "name": "Macro Economist",
        "avatar": "📈",
        "risk": "medium",
        "risk_label": "Macro",
        "temperature": 0.35,
        "description": "Fed-watching swing trader. Uses market breadth, macro news, and risk-on/risk-off signals.",
        "prompt": """
        You are 'Macro Economist', a balanced macro trading agent.
        You react to broad risk conditions, macro news, BTC/ETH momentum, and stock-market proxies.
        You can BUY when macro and crypto momentum align, SELL into overheated moves, and HOLD when signals conflict.
        """,
    },
    "Aggressive_Degen": {
        "name": "Aggressive Degen",
        "avatar": "⚡",
        "risk": "high",
        "risk_label": "Aggressive",
        "temperature": 0.55,
        "description": "High-conviction momentum trader. Moves faster and accepts higher drawdown risk.",
        "prompt": """
        You are 'Aggressive Degen', a high-risk momentum investing agent.
        You actively seek strong 24h and 7d momentum in BTC, ETH, and EURC (euro stablecoin — a EUR/USD directional play).
        You are willing to BUY breakouts sooner than conservative agents and SELL quickly when momentum fades.
        """,
    },
    "Yield_Farmer": {
        "name": "Yield Farmer",
        "avatar": "🌊",
        "risk": "low",
        "risk_label": "Yield",
        "temperature": 0.25,
        "description": "Stablecoin-first optimizer. Prefers USDC and only rotates into majors on unusually attractive setups.",
        "prompt": """
        You are 'Yield Farmer', a stablecoin-first investing agent.
        You prefer capital preservation, USDC, and low-volatility entries.
        You only BUY BTC or ETH when downside appears overextended, and you SELL when risk-adjusted upside weakens.
        """,
    },
}


def get_agent_profile(agent_name="Conservative_Whale"):
    return AGENT_PROFILES.get(agent_name, AGENT_PROFILES["Conservative_Whale"])


def get_agent_catalog():
    return [
        {"id": agent_id, **profile}
        for agent_id, profile in AGENT_PROFILES.items()
    ]


def initialize_agent(temperature=0.2):
    # We initialize the model here using the key from config.py
    return ChatGoogleGenerativeAI(
        model="gemini-3.1-flash-lite",
        temperature=temperature,
        google_api_key=GOOGLE_API_KEY
    )
DEV_MODE = AGENT_DEV_MODE

def ask_agent(market_data, agent_name="Conservative_Whale"):
    profile = get_agent_profile(agent_name)
    if DEV_MODE:
        return "DECISION: BUY BTC\nREASON: Dev mode mock data bypass."
    
    llm = initialize_agent(temperature=profile["temperature"])

    system_prompt = SystemMessage(content=f"""
    {profile["prompt"]}
    Based on the data provided, reply strictly in this format:
    DECISION: [BUY, SELL, or HOLD]
    REASON: [1 sentence explanation]
    If you decide to BUY or SELL, you MUST specify the asset you are targeting (e.g., "DECISION: BUY ETH").
    """)

    # Convert the multi-asset dictionary to a structured string for the AI
    data_string_parts = [f"Overall Macro News: {market_data.get('MACRO_NEWS', 'N/A')}"]
    for asset, data in market_data.items():
        if asset == "MACRO_NEWS":
            continue # Already handled
        data_string_parts.append(
            f"  - {asset} ({data.get('TYPE')}): Price=${float(data.get('PRICE') or 0):.2f}, 24H_Change={data.get('24H_CHANGE')}, 7D_Change={data.get('7D_CHANGE')}, 1Y_Change={data.get('1Y_CHANGE')}"
        )

    data_string = "Current Market State:\n" + "\n".join(data_string_parts)
    user_message = HumanMessage(content=data_string)

    try:
        response = llm.invoke([system_prompt, user_message])
        # gemini-3.1-flash-lite is a thinking-capable model: response.content
        # can be a list of content blocks (text + thinking parts) rather than
        # a plain string. .text normalizes to just the text parts.
        return response.text
    except Exception as e:
        return fallback_market_decision(market_data)


def ask_conservative_whale(market_data):
    return ask_agent(market_data, "Conservative_Whale")


def fallback_market_decision(market_data):
    """
    Keeps the trading pipeline alive when the AI provider is unavailable.
    Conservative logic: buy ETH/BTC only on meaningful pullbacks; otherwise hold.
    """
    candidates = []
    for symbol in ("ETH", "BTC", "EURC"):
        data = market_data.get(symbol, {})
        change = str(data.get("24H_CHANGE", "0")).replace("%", "").replace("+", "")
        try:
            candidates.append((float(change), symbol))
        except ValueError:
            continue

    if not candidates:
        return "DECISION: HOLD\nREASON: Market data unavailable and AI provider unavailable."

    weakest_change, weakest_symbol = min(candidates)
    strongest_change, strongest_symbol = max(candidates)

    if weakest_change <= -2.0 and weakest_symbol in {"ETH", "BTC"}:
        return f"DECISION: BUY {weakest_symbol}\nREASON: Rule fallback detected a blue-chip pullback greater than 2%."

    if strongest_change >= 5.0:
        return f"DECISION: SELL {strongest_symbol}\nREASON: Rule fallback detected an overheated 24h move greater than 5%."

    return "DECISION: HOLD\nREASON: Rule fallback found no conservative entry."

# Add this to agents.py

def generate_social_post(agent_name, action, reasoning, tx_id):
    """
    Generates an in-character social media post detailing the latest on-chain activity.
    """
    llm = initialize_agent()
    
    social_prompt = SystemMessage(content=f"You are the social manager for the AI trading entity '{agent_name}'. "
                                          f"Write a sharp, short social media post (max 200 characters) announcing that you just executed a {action}. "
                                          f"Incorporate this core reason: '{reasoning}'. Use a style suited for crypto-native social networks. "
                                          f"Do not include hashes or raw JSON.")
    
    response = llm.invoke([social_prompt])
    post_content = response.text.strip()
    
    # Append the short transaction identifier for verification
    short_tx = f"{tx_id[:6]}...{tx_id[-4:]}" if tx_id else "Pending"
    return f"🤖 {post_content}\n\n🔗 Tx: {short_tx}"
# --- EASY TESTING BLOCK ---
if __name__ == "__main__":
    print("Testing the Arc_Tic Whale logic...")
    dummy_data = get_current_market_state() # Use real data for testing this!
    
    try:
        result = ask_conservative_whale(dummy_data)
        print("\n--- Agent Response ---")
        print(result)
    except Exception as e:
        print("\n❌ GOOGLE API ERROR DETAILS:")
        print(e)
