# main.py
import time
from market_data import get_current_market_state
from agents import ask_conservative_whale
from trade_executor import execute_trade
from copy_engine import mirror_agent_trade
from social import generate_canteen_post

# Import the variables directly from your config file
from config import AGENT_WALLET_ADDRESS, AGENT_WALLET_ID

def run_trading_bot():
    print(f"🟢 Starting AI Trading Agent on Arc Testnet...")
    print(f"💼 Agent Address: {AGENT_WALLET_ADDRESS}")
    print("-" * 40)
    
    print("📡 Fetching latest market data...")
    current_data = get_current_market_state()
    time.sleep(1) 
    
    print(f"🧠 Passing data to The Conservative Whale for analysis...")
    time.sleep(2)
    
    ai_response = ask_conservative_whale(current_data)
    print("\n" + "="*40)
    print(f"🐋 WHALE DECISION:\n{ai_response}")
    print("="*40 + "\n")
    
   
    # --- THE EXECUTION LOGIC ---
    if "DECISION: BUY" in ai_response:
        print("⚡ Action Triggered: Executing BUY order on Circle infrastructure...")
        agent_tx = execute_trade(wallet_id=AGENT_WALLET_ID, action="BUY")
        
        if agent_tx:
            mirror_agent_trade(agent_name="Conservative_Whale", action="BUY")

            # 🛡️ NEW: Rate Limit Cooldown
            print("⏳ Pausing for 20 seconds to respect API rate limits...")
            time.sleep(20)

            # 📢 NEW: Broadcast to Socials
            post = generate_canteen_post("Conservative_Whale", "BUY", agent_tx)
            print(post)
        
    elif "DECISION: SELL" in ai_response:
        print("⚡ Action Triggered: Executing SELL order on Circle infrastructure...")
        agent_tx = execute_trade(wallet_id=AGENT_WALLET_ID, action="SELL")
        
        if agent_tx:
            mirror_agent_trade(agent_name="Conservative_Whale", action="SELL")
            
            # 🛡️ NEW: Rate Limit Cooldown
            print("⏳ Pausing for 20 seconds to respect API rate limits...")
            time.sleep(20)
            
            # 📢 NEW: Broadcast to Socials
            post = generate_canteen_post("Conservative_Whale", "SELL", agent_tx)
            print(post)
        
    else:
        print("⏸️ Action: HOLD. No on-chain transaction required.")

if __name__ == "__main__":
    if not AGENT_WALLET_ID:
        print("❌ ERROR: Please set AGENT_WALLET_ID in your .env file!")
    else:
        run_trading_bot()