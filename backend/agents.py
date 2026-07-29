# agents.py
import time

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage
from backend.config import AGENT_DEV_MODE, GOOGLE_API_KEY
from backend.logger import get_logger
from backend.market_data import get_current_market_state

log = get_logger("agents")

AGENT_PROFILES = {
    "Conservative_Whale": {
        "name": "Arc_Tic Whale",
        "avatar": "🐋",
        "risk": "low",
        "risk_label": "Conservative",
        "temperature": 0.2,
        "description": "Macro-driven, patient accumulator. Targets blue-chip assets with low drawdown tolerance.",
        "prompt": """
        You are 'Arc_Tic Whale', a conservative spot-trading agent. You hold USDC by default
        and buy only genuine overextended dips in BTC or ETH.
        ENTRY (only if you hold no position): BUY BTC or ETH when its 24H change is -1.50% or
        lower, OR its 7D change is -4.00% or lower. If both BTC and ETH qualify, pick the one
        with the deeper 7D drawdown.
        EXIT (only if you hold a position): SELL when the held asset's 24H change is +2.00% or
        higher (take profit into strength), or its 7D change is -6.00% or lower (thesis failed).
        Otherwise HOLD. Stock data (AAPL, SPY) is context only - it must never veto a crypto
        entry or exit that meets the thresholds above.
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
        You are 'Macro Economist', a swing trader who trades crypto based on risk regime.
        Define the regime yourself from the data: risk-ON when SPY's 24H change is positive AND
        BTC's 7D change is -2.50% or higher; risk-OFF when SPY's 24H change is -0.50% or lower OR
        BTC's 7D change is -3.50% or lower; otherwise NEUTRAL.
        ENTRY (only if you hold no position): in risk-ON, BUY BTC (or ETH if its 7D momentum is
        stronger). In risk-OFF: BUY EURC only when SPY's 24H change is -0.75% or lower (defensive
        EUR/USD rotation); if SPY's 24H change is above -0.75%, you must NOT buy anything - HOLD.
        EXIT (only if you hold a position): SELL crypto when the regime turns risk-OFF. SELL EURC
        when the regime turns risk-ON.
        In NEUTRAL, HOLD. Do not require every signal to agree - the regime definition above IS
        the decision rule.
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
        You are 'Aggressive Degen', a high-risk spot momentum trader. You chase strength and cut
        quickly. You do not buy dips.
        ENTRY (only if you hold no position): BUY the asset (BTC or ETH) whose 24H change is
        +1.00% or higher OR whose 7D change is +2.50% or higher. Prefer the stronger 24H mover.
        EXIT (only if you hold a position): SELL the moment the held asset's 24H change turns
        -0.50% or lower - momentum is gone, do not wait for it to come back.
        HOLD only when nothing meets an entry and you hold nothing, or you hold a position whose
        momentum is still positive. Ignore stocks and macro news entirely - you trade price, not
        narrative.
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
        You are 'Yield Farmer', a stablecoin-first agent. USDC is your home; you make brief,
        rare excursions into majors only on capitulation-grade dips, and return to USDC fast.
        ENTRY (only if you hold no position): BUY BTC or ETH only when its 24H change is -3.00%
        or lower, OR its 7D change is -6.00% or lower. These are meant to be less common than the
        other agents' entries, but should still fire on a real pullback.
        EXIT (only if you hold a position): SELL as soon as the held asset's 24H change is
        +1.50% or higher (bank the bounce), or its 7D change falls -10.00% or lower
        (capitulation continued - preserve capital and exit).
        Otherwise HOLD in USDC. That is your job, not a failure.
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

def _describe_positions(open_positions: dict | None) -> str:
    if not open_positions:
        return "You currently hold NO position - you are fully in USDC."
    held = ", ".join(
        f"{asset} (entered at ${entry:,.2f})" if entry else asset
        for asset, entry in open_positions.items()
    )
    return f"You currently hold: {held}."


def ask_agent(market_data, agent_name="Conservative_Whale", open_positions=None):
    profile = get_agent_profile(agent_name)
    if DEV_MODE:
        return "DECISION: BUY BTC\nREASON: Dev mode mock data bypass."

    llm = initialize_agent(temperature=profile["temperature"])

    system_prompt = SystemMessage(content=f"""
    {profile["prompt"]}
    POSITION STATE: {_describe_positions(open_positions)}
    HARD RULES (these override everything above):
    - Never BUY if you already hold a position. One position at a time.
    - Never SELL an asset you do not currently hold.
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

    for attempt in (1, 2):
        try:
            response = llm.invoke([system_prompt, user_message])
            # gemini-3.1-flash-lite is a thinking-capable model: response.content
            # can be a list of content blocks (text + thinking parts) rather than
            # a plain string. .text normalizes to just the text parts.
            return response.text
        except Exception as e:
            log.error("Gemini call failed for %s (attempt %d/2): %s", agent_name, attempt, e)
            if attempt == 1:
                time.sleep(8)  # transient rate limits are the common case; brief backoff then retry once

    return fallback_market_decision(market_data)


def ask_conservative_whale(market_data, open_positions=None):
    return ask_agent(market_data, "Conservative_Whale", open_positions=open_positions)


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
