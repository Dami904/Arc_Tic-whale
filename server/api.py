import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from backend.database import (
    add_follower,
    get_follower_wallet,
    get_follower_summary,
    get_setting,
    get_trade_history,
    get_trade_metrics,
    init_db,
    set_setting,
)
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from backend.market_data import get_current_market_state
from backend.trade_service import run_trade_cycle
from backend.wallet_manager import initialize_circle_client, create_agent_wallet
from circle.web3.developer_controlled_wallets.api import WalletsApi
from backend.config import (
    AGENT_WALLET_ADDRESS,
    AGENT_WALLET_ID,
    API_AUTH_TOKEN,
    CORS_ALLOWED_ORIGINS,
    RATE_LIMIT_PER_MINUTE,
    TRADE_DRY_RUN,
)
from backend.logger import get_logger

log = get_logger("api")

init_db()

app = FastAPI(title="Arc Tic Whale")

limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

static_dir = os.path.join(os.path.dirname(__file__), "../frontend/static")
if Path(static_dir).is_dir():
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Auth dependency ---
def verify_auth(request: Request):
    auth = request.headers.get("Authorization", "")
    if TRADE_DRY_RUN:
        return
    if not API_AUTH_TOKEN:
        raise HTTPException(status_code=503, detail="API_AUTH_TOKEN is not configured")
    if not auth.startswith("Bearer ") or auth.removeprefix("Bearer ") != API_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid or missing API token")

# --- API Models ---
class TradeResponse(BaseModel):
    status: str
    decision: str
    message: str

class FollowRequest(BaseModel):
    username: str
    allocation: float
    asset: str

class WithdrawRequest(BaseModel):
    destination_address: str
    amount: float
    asset: str = "USDC"

class WalletStats(BaseModel):
    total_balance_usd: float
    token_balances: list
    performance: dict

class MarketDataResponse(BaseModel):
    assets: dict
    macro_news: str

# --- API Endpoints ---
@app.get("/")
def read_root():
    return {
        "message": "Conservative Whale API is online",
        "agent_address": AGENT_WALLET_ADDRESS,
        "dry_run": TRADE_DRY_RUN,
        "auth_required": not TRADE_DRY_RUN,
    }

@app.get("/trade-history")
def get_history():
    return {"trades": get_trade_history(limit=10)}

@app.get("/dashboard")
def get_dashboard(username: Optional[str] = None):
    agent_name = "Conservative_Whale"
    follower_wallet = get_follower_wallet(agent_name, username) if username else None
    wallet_id = follower_wallet["user_wallet_id"] if follower_wallet else AGENT_WALLET_ID
    stats = get_wallet_stats(wallet_id=wallet_id)
    trades = get_trade_history(limit=20)
    metrics = get_trade_metrics(agent_name)
    followers = get_follower_summary(agent_name)

    total_balance = stats["total_balance_usd"]
    allocated = followers["total_allocation"]
    free_balance = max(0.0, total_balance - allocated)
    allocation_pct = round((allocated / total_balance) * 100, 1) if total_balance else 0.0

    feed = []
    for trade in trades[:10]:
        action = trade["action"]
        asset = trade.get("asset") or "market"
        reason = trade.get("reason") or "No reason recorded."
        feed.append({
            "agent": "Arc-Tic Whale",
            "avatar": "🐋",
            "action": action,
            "asset": asset,
            "timestamp": trade["timestamp"],
            "body": f"{action} {asset}: {reason}",
            "tx_id": trade.get("tx_id"),
        })

    if not feed:
        feed.append({
            "agent": "Arc-Tic Whale",
            "avatar": "🐋",
            "action": "IDLE",
            "asset": None,
            "timestamp": None,
            "body": "No agent decisions recorded yet. Trigger a market check to populate the live feed.",
            "tx_id": None,
        })

    return {
        "wallet": {
            "wallet_id": wallet_id,
            "total_balance_usd": total_balance,
            "token_balances": stats["token_balances"],
            "performance": stats["performance"],
            "address": AGENT_WALLET_ADDRESS,
            "source": "telegram_user" if follower_wallet else "configured_agent_wallet",
        },
        "metrics": metrics,
        "followers": followers,
        "allocation": {
            "allocated": allocated,
            "free": free_balance,
            "allocated_pct": allocation_pct,
            "by_asset": followers["by_asset"],
        },
        "agents": [
            {
                "id": agent_name,
                "name": "Arc-Tic Whale",
                "avatar": "🐋",
                "risk": "low",
                "risk_label": "Conservative",
                "description": "Macro-driven, patient accumulator. Targets blue-chip assets with low drawdown tolerance.",
                "win_rate": metrics["win_rate"],
                "trades": metrics["total_trades"],
                "followers": followers["total_followers"],
                "roi_30d": stats["performance"].get("24h", "0.00%"),
            }
        ],
        "feed": feed,
        "transactions": trades,
    }

@app.get("/settings")
def get_app_settings():
    return {
        "kill_switch": get_setting("kill_switch"),
        "trade_alerts": get_setting("trade_alerts"),
        "daily_summary": get_setting("daily_summary")
    }

@app.post("/settings/{key}")
def update_setting(key: str, value: str, _=Depends(verify_auth)):
    if key not in ["kill_switch", "trade_alerts", "daily_summary"]:
        raise HTTPException(status_code=400, detail="Invalid setting")
    set_setting(key, value)
    return {"status": "success"}

@app.post("/deposit")
def deposit(_=Depends(verify_auth)):
    if not AGENT_WALLET_ADDRESS:
        return {"status": "error", "message": "No deposit address is configured for the active wallet."}
    return {
        "status": "success",
        "message": "Send Arc Testnet USDC to the displayed wallet address.",
        "address": AGENT_WALLET_ADDRESS,
        "asset": "USDC",
        "network": "ARC-TESTNET",
    }

@app.post("/withdraw")
def withdraw(req: Optional[WithdrawRequest] = None, _=Depends(verify_auth)):
    if req is None:
        return {
            "status": "needs_input",
            "message": "Withdraw requires destination address, asset, and amount. The UI form is not implemented yet.",
        }
    return {
        "status": "pending_integration",
        "message": "Withdraw submission requires Circle transfer API wiring before funds can move.",
        "request": req.model_dump(),
    }

@app.get("/stats", response_model=WalletStats)
def get_wallet_stats(wallet_id: Optional[str] = None):
    token_balances = []
    total_balance_usd = 0.0
    target_wallet_id = wallet_id or AGENT_WALLET_ID

    try:
        if not target_wallet_id:
            raise ValueError("No wallet id configured")
        client = initialize_circle_client()
        wallets_api = WalletsApi(client)
        balances_response = wallets_api.list_wallet_balance(id=target_wallet_id)

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
            for tb in balances_response.data.token_balances:
                if tb.token.symbol == "USDC":
                    total_balance_usd += float(tb.amount)
    except Exception as e:
        log.warning("Circle API error", error=str(e))
        if TRADE_DRY_RUN:
            summary = get_follower_summary("Conservative_Whale")
            total_balance_usd = float(summary["total_allocation"] or 0.0)
            if total_balance_usd:
                token_balances = [{
                    "symbol": "USDC",
                    "name": "USD Coin",
                    "amount": str(total_balance_usd),
                    "decimals": 6,
                }]

    eth_performance_data = get_current_market_state().get("ETH", {})
    performance = {
        "24h": eth_performance_data.get("24H_CHANGE", "0.00%"),
        "7d": eth_performance_data.get("7D_CHANGE", "0.00%"),
        "1y": eth_performance_data.get("1Y_CHANGE", "0.00%")
    }

    return {"total_balance_usd": total_balance_usd, "token_balances": token_balances, "performance": performance}

@app.get("/market-data", response_model=MarketDataResponse)
@limiter.limit(lambda: f"{RATE_LIMIT_PER_MINUTE}/minute")
def get_market_data(request: Request):
    market_state = get_current_market_state()
    macro_news = market_state.pop("MACRO_NEWS", "Market data unavailable.")
    return {"assets": market_state, "macro_news": macro_news}

@app.get("/webapp")
def serve_telegram_webapp():
    return FileResponse(os.path.join(os.path.dirname(__file__), "../frontend/index.html"))

@app.post("/trigger-trade")
@limiter.limit(lambda: f"{RATE_LIMIT_PER_MINUTE}/minute")
def trigger_trade(request: Request, _=Depends(verify_auth)):
    log.info("Manual trade trigger received")

    result = run_trade_cycle(wallet_id=AGENT_WALLET_ID, rate_limit_sleep=5)

    if result["status"] == "error":
        return {"status": "error", "message": result.get("reason", "Trade cycle failed.")}

    return {
        "status": "success" if result["status"] == "success" else "hold",
        "action": result["action"],
        "tx_hash": result.get("tx_hash"),
    }

@app.post("/follow")
@limiter.limit(lambda: f"{RATE_LIMIT_PER_MINUTE}/minute")
def follow_agent(request: Request, req: FollowRequest, _=Depends(verify_auth)):
    log.info("Follow request from @%s for %s USDC on %s", req.username, req.allocation, req.asset)

    if TRADE_DRY_RUN:
        real_wallet_id = f"dryrun-user-{req.username}"
    else:
        log.info("Provisioning Arc Testnet wallet for user @%s...", req.username)
        real_wallet_id = create_agent_wallet(f"User_{req.username}")

    if not real_wallet_id:
        return {"status": "error", "message": "Failed to generate Web3 wallet for user."}

    add_follower(
        user_id=req.username,
        user_wallet_id=real_wallet_id,
        target_agent="Conservative_Whale",
        allocation_amount=req.allocation,
        asset=req.asset,
    )

    return {"status": "success", "message": f"User {req.username} secured to smart contract."}
