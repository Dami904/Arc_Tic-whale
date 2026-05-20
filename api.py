# api.py
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from database import add_follower
import time

# Import our modular engines
from market_data import get_current_market_state
from agents import ask_conservative_whale
from trade_executor import execute_trade
from copy_engine import mirror_agent_trade
from social import generate_canteen_post
from database import init_db

from database import init_db 
from wallet_manager import initialize_circle_client
from circle.web3.developer_controlled_wallets.api import WalletsApi
from config import AGENT_WALLET_ADDRESS, AGENT_WALLET_ID

# Initialize the database when the server starts
init_db()

app = FastAPI(title="Arc Tic Whale")

app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- API Models ---
class TradeResponse(BaseModel):
    status: str
    decision: str
    message: str

class FollowRequest(BaseModel):
    username: str
    allocation: float
    asset: str

class WalletStats(BaseModel):
    total_balance_usd: float
    token_balances: list
    performance: dict

# --- Background Task ---
# We run the heavy lifting in the background so the frontend doesn't freeze waiting for blockchains and AI
def background_trading_pipeline():
    print("📡 Fetching latest market data...")
    current_data = get_current_market_state()
    
    print("🧠 Passing data to The Conservative Whale...")
    ai_response = ask_conservative_whale(current_data)
    
    if "DECISION: BUY" in ai_response:
        action = "BUY"
    elif "DECISION: SELL" in ai_response:
        action = "SELL"
    else:
        print("⏸️ Action: HOLD.")
        return

    print(f"⚡ Executing {action} order for the Agent...")
    agent_tx = execute_trade(wallet_id=AGENT_WALLET_ID, action=action)
    
    if agent_tx:
        mirror_agent_trade(agent_name="Conservative_Whale", action=action)
        
        # We can still keep a small sleep here just in case DEV_MODE is off
        time.sleep(5) 
        
        post = generate_canteen_post("Conservative_Whale", action, agent_tx)
        print(post)

# --- API Endpoints ---
@app.get("/")
def read_root():
    return {"message": "Conservative Whale API is online", "agent_address": AGENT_WALLET_ADDRESS}

@app.get("/stats", response_model=WalletStats)
def get_wallet_stats():
    """
    Returns the wallet's token balances and performance metrics.
    """
    # 1. Get Wallet Balance from Circle
    token_balances = []
    total_balance_usd = 0.0
    try:
        client = initialize_circle_client()
        wallets_api = WalletsApi(client)
        balances_response = wallets_api.list_wallet_balance(id=AGENT_WALLET_ID)
        
        if balances_response.data and balances_response.data.token_balances:
            token_balances = [
                {
                    "symbol": tb.token.symbol,
                    "name": tb.token.name,
                    "amount": tb.amount,
                    "decimals": tb.token.decimals
                } 
                for tb in balances_response.data.token_balances
            ]
            # This is a simplified calculation. A real app would fetch prices for each token.
            for tb in balances_response.data.token_balances:
                if tb.token.symbol == "USDC":
                    total_balance_usd += float(tb.amount)
    except Exception as e:
        print(f"⚠️ Circle API Error: {e}. Returning empty balance.")
        
    # 2. Get Market Performance Data
    market_data = get_current_market_state()
    performance = {
        "24h": market_data.get("24H_CHANGE", "0.00%"),
        "7d": market_data.get("7D_CHANGE", "0.00%"),
        "1y": market_data.get("1Y_CHANGE", "0.00%")
    }
    
    return {"total_balance_usd": total_balance_usd, "token_balances": token_balances, "performance": performance}

@app.get("/webapp")
def serve_telegram_webapp():
    """
    Serves the HTML frontend for the Telegram Mini App.
    """
    return FileResponse("index.html")

# api.py (Replace your background task and /trigger-trade endpoint with this)

@app.post("/trigger-trade")
def trigger_trade():
    print("📡 Fetching latest market data...")
    current_data = get_current_market_state()
    
    print("🧠 Passing data to The Conservative Whale...")
    ai_response = ask_conservative_whale(current_data)
    
    action = "HOLD"
    if "DECISION: BUY" in ai_response:
        action = "BUY"
    elif "DECISION: SELL" in ai_response:
        action = "SELL"
        
    if action == "HOLD":
        return {"status": "success", "action": "HOLD", "tx_hash": None}

    print(f"⚡ Executing {action} order for the Agent...")
    # Execute the trade and capture the returned hash!
    agent_tx = execute_trade(wallet_id=AGENT_WALLET_ID, action=action)
    
    if agent_tx:
        # Run the copy engine
        mirror_agent_trade(agent_name="Conservative_Whale", action=action)
        
        # Fire off the social post
        post = generate_canteen_post("Conservative_Whale", action, agent_tx)
        print(post)
        
        # 🚀 Send the actual hash back to the frontend!
        return {"status": "success", "action": action, "tx_hash": agent_tx}
    else:
        return {"status": "error", "message": "Blockchain execution failed."}
    
@app.post("/follow")
def follow_agent(req: FollowRequest):
    print(f"📥 Received follow request from @{req.username} for {req.allocation} USDC on {req.asset}")
    
    # We generate a mock wallet ID for the user based on their Telegram name
    mock_wallet = f"tg-wallet-{req.username.lower()}"
    
    # Save them to the database!
    add_follower(
        user_id=req.username,
        user_wallet_id=mock_wallet,
        target_agent="Conservative_Whale",
        allocation_amount=req.allocation
    )
    
    return {"status": "success", "message": f"User {req.username} secured to smart contract."}