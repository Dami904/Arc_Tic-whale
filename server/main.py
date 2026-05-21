import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.config import AGENT_WALLET_ADDRESS, AGENT_WALLET_ID
from backend.trade_service import run_trade_cycle

def run_trading_bot():
    print("Starting AI Trading Agent on Arc Testnet...")
    print(f"Agent Address: {AGENT_WALLET_ADDRESS}")
    print("-" * 40)
    print(run_trade_cycle(wallet_id=AGENT_WALLET_ID, rate_limit_sleep=20))

if __name__ == "__main__":
    if not AGENT_WALLET_ID:
        print("ERROR: Please set AGENT_WALLET_ID in your .env file.")
    else:
        run_trading_bot()
