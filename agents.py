# agents.py
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage, SystemMessage
from config import GOOGLE_API_KEY

def initialize_agent():
    # We initialize the model here using the key from config.py
    return ChatGoogleGenerativeAI(
        model="gemini-2.5-flash",
        temperature=0.2,
        google_api_key=GOOGLE_API_KEY
    )
DEV_MODE = True

def ask_conservative_whale(market_data):
    if DEV_MODE:
        print("🛠️ [DEV MODE ACTIVE]: Bypassing Gemini API to save rate limits...")
        # Toggle this between BUY and SELL to test your different logic paths
        return "DECISION: BUY\nREASON: Dev mode mock data bypass."
    
    llm = initialize_agent()
    
    system_prompt = SystemMessage(content="""
    You are 'The Conservative Whale', an AI trading agent. 
    You are highly risk-averse. You prefer holding stablecoins (USDC) and only buy major blue-chip assets like BTC and ETH when there is a confirmed market dip.
    Based on the data provided, reply strictly in this format:
    DECISION: [BUY, SELL, or HOLD]
    REASON: [1 sentence explanation]
    """)

    # Convert the python dictionary to a string for the AI to read
    data_string = f"Current Market State: {market_data_dict}"
    user_message = HumanMessage(content=data_string)

    response = llm.invoke([system_prompt, user_message])
    return response.content

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
    post_content = response.content.strip()
    
    # Append the short transaction identifier for verification
    short_tx = f"{tx_id[:6]}...{tx_id[-4:]}" if tx_id else "Pending"
    return f"🤖 {post_content}\n\n🔗 Tx: {short_tx}"
# --- EASY TESTING BLOCK ---
if __name__ == "__main__":
    print("Testing the Conservative Whale logic...")
    dummy_data = {"ETH_PRICE": 3100, "24H_CHANGE": "-8.5%", "MACRO_NEWS": "Inflation is high"}
    
    try:
        result = ask_conservative_whale(dummy_data)
        print("\n--- Agent Response ---")
        print(result)
    except Exception as e:
        print("\n❌ GOOGLE API ERROR DETAILS:")
        print(e)