import os
import hmac
import hashlib
import json as _json
import urllib.parse
import random
import time
from pathlib import Path
from typing import Optional

import jwt as _jwt
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from backend.database import (
    add_follower,
    add_referral_reward,
    get_follower_by_wallet_id,
    get_follower_wallet,
    get_follower_summary,
    get_referral_summary,
    get_setting,
    get_trade_history,
    get_trade_metrics,
    get_user,
    init_db,
    log_trade,
    set_setting,
)
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from backend.market_data import get_current_market_state
from backend.trade_service import run_trade_cycle
from backend.trade_executor import execute_transfer
from backend.agents import get_agent_catalog, get_agent_profile
from backend.user_wallets import ensure_user_wallet, normalize_user_id
from backend.wallet_manager import initialize_circle_client
from circle.web3.developer_controlled_wallets.api import TransactionsApi, WalletsApi
from backend.config import (
    AGENT_WALLET_ADDRESS,
    AGENT_WALLET_ID,
    API_AUTH_TOKEN,
    BOT_TOKEN,
    CORS_ALLOWED_ORIGINS,
    PRIVY_APP_ID,
    PRIVY_APP_SECRET,
    PRIVY_CLIENT_ID,
    WALLETCONNECT_PROJECT_ID,
    RATE_LIMIT_PER_MINUTE,
    TRADE_DRY_RUN,
)
from backend.email_otp import send_otp_email
from backend.privy_jwt import verify_privy_access_token
from backend.logger import get_logger

log = get_logger("api")

init_db()

app = FastAPI(
    title="Arc_Tic Whale",
    description="AI-driven copy-trading app on Circle Arc Testnet.",
)

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

# --- Telegram initData verification ---
def _verify_telegram_init_data(init_data: str) -> str | None:
    """Verify Telegram WebApp initData HMAC-SHA256 signature.
    Returns 'tg_{user_id}' if valid, None otherwise.
    """
    if not BOT_TOKEN or not init_data:
        return None
    try:
        parsed = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        received_hash = parsed.pop("hash", "")
        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(parsed.items()))
        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(received_hash, expected_hash):
            user = _json.loads(parsed.get("user", "{}"))
            uid = str(user.get("id", ""))
            return f"tg_{uid}" if uid else None
        return None
    except Exception:
        return None

# --- Auth dependency ---
def verify_privy_token(request: Request) -> str:
    """Accepts Telegram WebApp initData (X-Telegram-Init-Data header) or Privy JWT (Bearer token)."""
    if TRADE_DRY_RUN:
        return "dryrun_user"
    if request.client and request.client.host in ("127.0.0.1", "::1", "localhost"):
        return "localhost_user"

    # Telegram Mini App mode — verify HMAC signature from Telegram
    tg_init_data = request.headers.get("X-Telegram-Init-Data", "")
    if tg_init_data:
        tg_user_id = _verify_telegram_init_data(tg_init_data)
        if tg_user_id:
            return tg_user_id
        raise HTTPException(status_code=401, detail="Invalid Telegram auth data")

    # Web / Privy mode — Bearer token
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = auth.removeprefix("Bearer ")

    # Legacy static token (admin endpoints / CI)
    if API_AUTH_TOKEN and token == API_AUTH_TOKEN:
        return "admin"

    # Wallet-login pseudo-token (not a JWT)
    if token.startswith("wallet_"):
        return token

    # Privy access token (JWT)
    if not PRIVY_APP_ID:
        raise HTTPException(status_code=503, detail="Privy auth is not configured")
    try:
        return verify_privy_access_token(token)
    except _jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

# --- API Models ---
class TradeResponse(BaseModel):
    status: str
    decision: str
    message: str

class FollowRequest(BaseModel):
    username: str
    allocation: float
    asset: str
    referral_code: Optional[str] = None
    agent_id: str = "Conservative_Whale"

class EnsureUserRequest(BaseModel):
    username: str
    referral_code: Optional[str] = None

class WithdrawRequest(BaseModel):
    destination_address: str
    amount: float
    asset: str = "USDC"
    username: Optional[str] = None

class TriggerTradeRequest(BaseModel):
    username: Optional[str] = None
    agent_id: str = "Conservative_Whale"

class ProfitRequest(BaseModel):
    username: str
    profit_amount: float
    tx_id: Optional[str] = None

class WalletStats(BaseModel):
    total_balance_usd: float
    token_balances: list
    performance: dict

class MarketDataResponse(BaseModel):
    assets: dict
    macro_news: str

# --- API Endpoints ---
AGENT_NAME = "Conservative_Whale"
TRADE_ACTIONS = {"BUY", "SELL", "HOLD"}
WALLET_ACTIONS = {"DEPOSIT", "WITHDRAW"}


def _wallet_from_response(response):
    data = getattr(response, "data", None)
    if data is None:
        return None
    for attr in ("wallet", "wallets"):
        value = getattr(data, attr, None)
        if value is None:
            continue
        if isinstance(value, list):
            return value[0].to_dict() if value and hasattr(value[0], "to_dict") else (value[0] if value else None)
        return value.to_dict() if hasattr(value, "to_dict") else value
    return data.to_dict() if hasattr(data, "to_dict") else None


def _lookup_wallet_address(wallet_id: Optional[str]) -> Optional[str]:
    if not wallet_id:
        return None
    try:
        client = initialize_circle_client()
        wallets_api = WalletsApi(client)
        for method_name in ("get_wallet", "get_wallet_by_id"):
            method = getattr(wallets_api, method_name, None)
            if not method:
                continue
            try:
                wallet = _wallet_from_response(method(id=wallet_id))
            except TypeError:
                wallet = _wallet_from_response(method(wallet_id))
            if wallet:
                return wallet.get("address")
    except Exception as e:
        log.warning("Unable to look up wallet address", wallet_id=wallet_id, error=str(e))
    return None


def get_active_wallet_context(username: Optional[str] = None) -> dict:
    user_id = normalize_user_id(username) if username else None
    user_wallet = get_user(user_id) if user_id else None
    follower_wallet = get_follower_wallet(AGENT_NAME, user_id) if user_id else None
    wallet_id = (
        follower_wallet["user_wallet_id"]
        if follower_wallet else user_wallet["wallet_id"]
        if user_wallet else AGENT_WALLET_ID
    )
    wallet_address = (
        follower_wallet.get("user_wallet_address")
        if follower_wallet else AGENT_WALLET_ADDRESS
        if not user_wallet else user_wallet["wallet_address"]
    )
    if not wallet_address and wallet_id == AGENT_WALLET_ID:
        wallet_address = AGENT_WALLET_ADDRESS
    if not wallet_address:
        wallet_address = _lookup_wallet_address(wallet_id)

    return {
        "wallet_id": wallet_id,
        "address": wallet_address,
        "source": "user" if follower_wallet or user_wallet else "configured_agent_wallet",
        "follower": follower_wallet,
        "user": user_wallet,
    }


def _circle_transaction_items(response) -> list[dict]:
    data = getattr(response, "data", None)
    if data is None:
        return []

    transactions = getattr(data, "transactions", None)
    if transactions is None and hasattr(data, "to_dict"):
        data_dict = data.to_dict()
        transactions = data_dict.get("transactions")
    if transactions is None and hasattr(response, "to_dict"):
        transactions = response.to_dict().get("data", {}).get("transactions")
    if not transactions:
        return []

    items = []
    for tx in transactions:
        items.append(tx.to_dict() if hasattr(tx, "to_dict") else tx)
    return items


def get_wallet_activity(wallet_id: Optional[str], agent: Optional[str], limit: int = 20) -> list[dict]:
    local_activity = get_trade_history(limit=limit, agent=agent, actions=WALLET_ACTIONS)
    circle_activity = []

    if wallet_id and not TRADE_DRY_RUN:
        try:
            client = initialize_circle_client()
            transactions_api = TransactionsApi(client)
            response = transactions_api.list_transactions(
                wallet_ids=wallet_id,
                operation="TRANSFER",
                page_size=min(limit, 50),
                order="DESC",
            )
            for tx in _circle_transaction_items(response):
                tx_type = str(tx.get("transactionType") or "").upper()
                action = "DEPOSIT" if tx_type == "INBOUND" else "WITHDRAW" if tx_type == "OUTBOUND" else None
                if not action:
                    continue
                circle_activity.append({
                    "id": tx.get("id"),
                    "agent": agent or AGENT_NAME,
                    "action": action,
                    "asset": "USDC",
                    "tx_id": tx.get("txHash") or tx.get("id"),
                    "reason": tx.get("state") or "Circle transfer",
                    "timestamp": str(tx.get("createDate") or tx.get("updateDate") or ""),
                    "source": "circle",
                    "amount": (tx.get("amounts") or [None])[0],
                })
        except Exception as e:
            log.warning("Circle transaction history unavailable", wallet_id=wallet_id, error=str(e))

    seen = set()
    activity = []
    for item in circle_activity + local_activity:
        key = item.get("tx_id") or f"{item.get('action')}:{item.get('timestamp')}:{item.get('reason')}"
        if key in seen:
            continue
        seen.add(key)
        activity.append(item)

    return sorted(activity, key=lambda item: item.get("timestamp") or "", reverse=True)[:limit]


@app.get("/")
def read_root():
    return {
        "message": "Arc_Tic Whale API is online",
        "title": "Arc_Tic Whale",
        "description": "AI-driven copy-trading app on Circle Arc Testnet",
        "agent_address": AGENT_WALLET_ADDRESS,
        "dry_run": TRADE_DRY_RUN,
        "auth_required": not TRADE_DRY_RUN,
    }

@app.post("/users/ensure")
@limiter.limit(lambda: f"{RATE_LIMIT_PER_MINUTE}/minute")
def ensure_user(request: Request, req: EnsureUserRequest):
    wallet = ensure_user_wallet(req.username, referral_code=req.referral_code)
    if not wallet:
        return {"status": "error", "message": "Failed to create or load user wallet."}
    return {
        "status": "success",
        "user": wallet,
        "referrals": get_referral_summary(wallet["user_id"]),
        "live_transactions": not TRADE_DRY_RUN,
    }

@app.get("/referrals")
def get_referrals(username: str):
    user_id = normalize_user_id(username)
    user = get_user(user_id)
    if not user:
        return {"status": "error", "message": "User wallet not found."}
    return {
        "status": "success",
        "user": user,
        "referrals": get_referral_summary(user_id),
    }

@app.post("/referrals/record-profit")
@limiter.limit(lambda: f"{RATE_LIMIT_PER_MINUTE}/minute")
def record_referral_profit(request: Request, req: ProfitRequest, _: str = Depends(verify_privy_token)):
    user = get_user(normalize_user_id(req.username))
    if not user:
        return {"status": "error", "message": "User wallet not found."}
    if not user.get("referred_by"):
        return {"status": "skipped", "message": "User has no referrer."}
    if req.profit_amount <= 0:
        return {"status": "skipped", "message": "Referral rewards only apply to positive profit."}

    reward = round(req.profit_amount * 0.10, 6)
    referrer = get_user(user["referred_by"])
    if not referrer:
        return {"status": "error", "message": "Referrer wallet not found."}

    reward_tx_id = execute_transfer(
        wallet_id=user["wallet_id"],
        destination_address=referrer["wallet_address"],
        asset_symbol="USDC",
        amount=str(reward),
    )
    if not reward_tx_id:
        return {"status": "error", "message": "Referral reward transfer failed."}

    add_referral_reward(
        referrer_user_id=user["referred_by"],
        referred_user_id=user["user_id"],
        profit_amount=req.profit_amount,
        reward_amount=reward,
        tx_id=reward_tx_id,
    )
    log_trade(
        agent=f"Referral:{user['referred_by']}",
        action="REFERRAL_REWARD",
        asset="USDC",
        tx_id=reward_tx_id,
        reason=f"10% of {user['user_id']} profit: {reward} USDC",
    )
    return {
        "status": "success",
        "referrer": user["referred_by"],
        "referred_user": user["user_id"],
        "profit_amount": req.profit_amount,
        "reward_amount": reward,
        "tx_hash": reward_tx_id,
    }

@app.get("/trade-history")
def get_history(username: Optional[str] = None):
    wallet = get_active_wallet_context(username)
    agent = f"Follower:{wallet['wallet_id']}" if wallet["source"] == "user" else None
    return {"trades": get_trade_history(limit=10, agent=agent, actions=TRADE_ACTIONS)}

@app.get("/dashboard")
def get_dashboard(username: Optional[str] = None):
    wallet = get_active_wallet_context(username)
    stats = get_wallet_stats(wallet_id=wallet["wallet_id"])
    trades = get_trade_history(limit=20, actions=TRADE_ACTIONS)
    history_agent = f"Follower:{wallet['wallet_id']}" if wallet["source"] == "user" else None
    wallet_trades = (
        get_trade_history(limit=20, agent=history_agent, actions=TRADE_ACTIONS)
        if wallet["source"] == "user"
        else trades
    )
    wallet_activity = get_wallet_activity(wallet["wallet_id"], history_agent, limit=20)
    metrics = get_trade_metrics(AGENT_NAME)
    followers = get_follower_summary(AGENT_NAME)
    agent_cards = []
    for profile in get_agent_catalog():
        agent_metrics = get_trade_metrics(profile["id"])
        agent_followers = get_follower_summary(profile["id"])
        agent_cards.append({
            "id": profile["id"],
            "name": profile["name"],
            "avatar": profile["avatar"],
            "risk": profile["risk"],
            "risk_label": profile["risk_label"],
            "description": profile["description"],
            "win_rate": agent_metrics["win_rate"],
            "trades": agent_metrics["total_trades"],
            "followers": agent_followers["total_followers"],
            "roi_30d": stats["performance"].get("24h", "0.00%"),
        })

    total_balance = stats["total_balance_usd"]
    if wallet["follower"]:
        allocated = float(wallet["follower"]["allocation_amount"])
    elif wallet["source"] == "user":
        allocated = 0.0
    else:
        allocated = followers["total_allocation"]
    free_balance = max(0.0, total_balance - allocated)
    allocation_pct = round((allocated / total_balance) * 100, 1) if total_balance else 0.0

    agent_lookup = {a["id"]: a for a in get_agent_catalog()}

    feed = []
    for trade in trades[:10]:
        action = trade["action"]
        asset = trade.get("asset") or "market"
        reason = trade.get("reason") or "No reason recorded."
        agent_id = trade.get("agent", AGENT_NAME)
        profile = agent_lookup.get(agent_id, {"name": agent_id, "avatar": "🤖"})
        feed.append({
            "agent": profile["name"],
            "avatar": profile["avatar"],
            "action": action,
            "asset": asset,
            "timestamp": trade["timestamp"],
            "body": f"{action} {asset}: {reason}",
            "tx_id": trade.get("tx_id"),
        })

    if not feed:
        feed.append({
            "agent": "Arc_Tic Whale",
            "avatar": "🐋",
            "action": "IDLE",
            "asset": None,
            "timestamp": None,
            "body": "No agent decisions recorded yet. Trigger a market check to populate the live feed.",
            "tx_id": None,
        })

    _dashboard_user = wallet.get("user") or {}
    _dashboard_uid = _dashboard_user.get("user_id")

    return {
        "wallet": {
            "wallet_id": wallet["wallet_id"],
            "total_balance_usd": total_balance,
            "token_balances": stats["token_balances"],
            "performance": stats["performance"],
            "address": wallet["address"],
            "source": wallet["source"],
            "live_transactions": not TRADE_DRY_RUN,
        },
        "metrics": metrics,
        "followers": followers,
        "allocation": {
            "allocated": allocated,
            "free": free_balance,
            "allocated_pct": allocation_pct,
            "by_asset": followers["by_asset"],
        },
        "agents": agent_cards,
        "feed": feed,
        "trades": wallet_trades,
        "transactions": wallet_activity,
        "referrals": get_referral_summary(_dashboard_uid) if _dashboard_uid else None,
    }

@app.get("/settings")
def get_app_settings():
    return {
        "kill_switch": get_setting("kill_switch"),
        "trade_alerts": get_setting("trade_alerts"),
        "daily_summary": get_setting("daily_summary")
    }

@app.post("/settings/{key}")
def update_setting(key: str, value: str, _: str = Depends(verify_privy_token)):
    if key not in ["kill_switch", "trade_alerts", "daily_summary"]:
        raise HTTPException(status_code=400, detail="Invalid setting")
    set_setting(key, value)
    return {"status": "success"}

@app.post("/deposit")
def deposit(username: Optional[str] = None, _: str = Depends(verify_privy_token)):
    wallet = get_active_wallet_context(username)
    if not wallet["address"]:
        return {"status": "error", "message": "No deposit address is configured for the active wallet."}
    return {
        "status": "success",
        "message": "Send Arc Testnet USDC to the displayed wallet address.",
        "address": wallet["address"],
        "wallet_id": wallet["wallet_id"],
        "asset": "USDC",
        "network": "ARC-TESTNET",
    }

@app.post("/withdraw")
def withdraw(req: Optional[WithdrawRequest] = None, _: str = Depends(verify_privy_token)):
    if req is None:
        return {
            "status": "needs_input",
            "message": "Withdraw requires destination address, asset, and amount.",
        }

    wallet = get_active_wallet_context(req.username)
    tx_id = execute_transfer(
        wallet_id=wallet["wallet_id"],
        destination_address=req.destination_address,
        asset_symbol=req.asset,
        amount=str(req.amount),
    )
    if not tx_id:
        return {"status": "error", "message": "Withdraw transaction failed."}

    history_agent = (
        f"Follower:{wallet['wallet_id']}"
        if wallet["source"] == "user"
        else AGENT_NAME
    )
    log_trade(
        agent=history_agent,
        action="WITHDRAW",
        asset=req.asset.upper(),
        tx_id=tx_id,
        reason=f"Transfer to {req.destination_address}",
    )

    return {
        "status": "success",
        "message": "Withdraw submitted on Arc Testnet.",
        "tx_hash": tx_id,
        "wallet_id": wallet["wallet_id"],
        "live_transactions": not TRADE_DRY_RUN,
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
            follower = get_follower_by_wallet_id(AGENT_NAME, target_wallet_id)
            summary = get_follower_summary(AGENT_NAME)
            total_balance_usd = float(
                follower["allocation_amount"]
                if follower else summary["total_allocation"] or 0.0
            )
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
    html_path = os.path.join(os.path.dirname(__file__), "../frontend/index.html")
    html = Path(html_path).read_text()
    html = html.replace("window.PRIVY_APP_ID = '';", f"window.PRIVY_APP_ID = '{PRIVY_APP_ID}';")
    html = html.replace("window.PRIVY_CLIENT_ID = '';", f"window.PRIVY_CLIENT_ID = '{PRIVY_CLIENT_ID}';")
    html = html.replace("window.WC_PROJECT_ID = '';", f"window.WC_PROJECT_ID = '{WALLETCONNECT_PROJECT_ID}';")
    return HTMLResponse(content=html)


import base64
import httpx
from eth_account import Account
from eth_account.messages import encode_defunct

_PRIVY_API = "https://auth.privy.io/api/v1"

# In-memory OTP store: email -> (code, expires_at)
# Falls back to this when Privy's passwordless API is unavailable.
_otp_store: dict[str, tuple[str, float]] = {}
_otp_last_sent: dict[str, float] = {}  # email -> unix timestamp of last send
_OTP_TTL = 600  # 10 minutes
_OTP_RESEND_COOLDOWN = 60  # seconds before the same email can request another code

def _privy_basic_auth() -> str:
    return "Basic " + base64.b64encode(f"{PRIVY_APP_ID}:{PRIVY_APP_SECRET}".encode()).decode()


def _privy_error_detail(response: httpx.Response) -> str:
    try:
        data = response.json()
        return data.get("error") or data.get("message") or str(data)
    except Exception:
        return response.text[:200] if response.text else f"HTTP {response.status_code}"


@app.post("/auth/request-otp")
async def request_otp(body: dict):
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Valid email required")

    now = time.time()
    last_sent = _otp_last_sent.get(email)
    if last_sent is not None:
        elapsed = now - last_sent
        if elapsed < _OTP_RESEND_COOLDOWN:
            wait = int(_OTP_RESEND_COOLDOWN - elapsed) + 1
            raise HTTPException(
                status_code=429,
                detail=f"Please wait {wait}s before requesting another code.",
                headers={"Retry-After": str(wait)},
            )

    privy_error: str | None = None

    # Try Privy server API first; fall through to custom OTP on failure
    if PRIVY_APP_ID and PRIVY_APP_SECRET:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.post(
                    f"{_PRIVY_API}/passwordless/send",
                    json={"email": email, "locale": "en"},
                    headers={
                        "Authorization": _privy_basic_auth(),
                        "privy-app-id": PRIVY_APP_ID,
                        "Content-Type": "application/json",
                    },
                )
            if r.status_code in (200, 204):
                _otp_last_sent[email] = now
                return {
                    "status": "sent",
                    "provider": "privy",
                    "resend_available_in": _OTP_RESEND_COOLDOWN,
                }
            privy_error = _privy_error_detail(r)
            log.warning(
                "Privy OTP send failed (%s): %s — using custom fallback",
                r.status_code,
                privy_error,
            )
        except Exception as exc:
            privy_error = str(exc)
            log.warning("Privy OTP request failed (%s) — using custom OTP fallback", exc)

    # Custom OTP fallback
    code = str(random.randint(100000, 999999))
    _otp_store[email] = (code, time.time() + _OTP_TTL)
    emailed = await send_otp_email(email, code)
    _otp_last_sent[email] = now
    if emailed:
        log.info("OTP emailed to %s via Resend", email)
        return {
            "status": "sent",
            "provider": "resend",
            "hint": "Check your inbox for the 6-digit code.",
            "resend_available_in": _OTP_RESEND_COOLDOWN,
        }

    log.info("=== OTP for %s: %s (valid 10 min) ===", email, code)
    return {
        "status": "sent",
        "provider": "custom",
        "hint": "Privy email is unavailable. Check the server console for your code.",
        "privy_error": privy_error,
        "resend_available_in": _OTP_RESEND_COOLDOWN,
    }


@app.post("/auth/verify-otp")
async def verify_otp(body: dict):
    email = (body.get("email") or "").strip().lower()
    code  = (body.get("code") or "").strip()
    if not email or not code:
        raise HTTPException(status_code=400, detail="Email and code required")

    # Try Privy first
    if PRIVY_APP_ID and PRIVY_APP_SECRET:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.post(
                    f"{_PRIVY_API}/passwordless/authenticate",
                    json={"email": email, "code": code},
                    headers={"Authorization": _privy_basic_auth(), "privy-app-id": PRIVY_APP_ID},
                )
            if r.status_code == 200:
                data = r.json()
                user = data.get("user", {})
                linked = user.get("linked_accounts", [{}])
                user_email = next((a.get("address") for a in linked if a.get("type") == "email"), email)
                return {"token": data.get("token", ""), "user_id": user.get("id", ""), "email": user_email}
            log.warning("Privy OTP verify returned %s — trying custom OTP store", r.status_code)
        except Exception as exc:
            log.warning("Privy OTP verify failed (%s) — trying custom OTP store", exc)

    # Custom OTP fallback
    stored = _otp_store.get(email)
    if not stored:
        raise HTTPException(status_code=401, detail="No code found for this email — request a new one")
    stored_code, expires_at = stored
    if time.time() > expires_at:
        _otp_store.pop(email, None)
        raise HTTPException(status_code=401, detail="Code expired — request a new one")
    if not hmac.compare_digest(stored_code, code):
        raise HTTPException(status_code=401, detail="Invalid code")
    _otp_store.pop(email, None)
    user_id = f"email_{email.split('@')[0]}"
    token = base64.b64encode(f"{user_id}:{time.time()}".encode()).decode()
    return {"token": token, "user_id": user_id, "email": email}

@app.post("/auth/wallet")
async def wallet_auth(body: dict):
    address   = (body.get("address") or "").lower().strip()
    message   = (body.get("message") or "").strip()
    signature = (body.get("signature") or "").strip()
    if not address or not message or not signature:
        raise HTTPException(status_code=400, detail="address, message, and signature required")
    msg = encode_defunct(text=message)
    try:
        recovered = Account.recover_message(msg, signature=signature).lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature format")
    if recovered != address:
        raise HTTPException(status_code=401, detail="Signature verification failed")
    wallet = ensure_user_wallet(f"wallet_{address[:8]}", referral_code=body.get("referral_code"))
    return {
        "token": f"wallet_{address}",
        "user_id": wallet["user_id"],
        "email": "",
        "display_name": "Trading Wallet",
        "wallet_address": address,
    }


@app.get("/auth/callback")
def auth_callback():
    callback_path = os.path.join(os.path.dirname(__file__), "../frontend/callback.html")
    html = Path(callback_path).read_text()
    html = html.replace("window.PRIVY_APP_ID = '';", f"window.PRIVY_APP_ID = '{PRIVY_APP_ID}';")
    html = html.replace("window.PRIVY_CLIENT_ID = '';", f"window.PRIVY_CLIENT_ID = '{PRIVY_CLIENT_ID}';")
    html = html.replace("window.PRIVY_AUTH_ORIGIN = '';", f"window.PRIVY_AUTH_ORIGIN = '{os.getenv('PRIVY_AUTH_ORIGIN', '')}';")
    return HTMLResponse(content=html)


@app.post("/trigger-trade")
@limiter.limit(lambda: f"{RATE_LIMIT_PER_MINUTE}/minute")
def trigger_trade(request: Request, req: Optional[TriggerTradeRequest] = None, _: str = Depends(verify_privy_token)):
    log.info("Manual trade trigger received")

    active_wallet = get_active_wallet_context(req.username if req else None)
    agent_ids = {agent["id"] for agent in get_agent_catalog()}
    agent_id = req.agent_id if req and req.agent_id in agent_ids else AGENT_NAME
    result = run_trade_cycle(
        wallet_id=active_wallet["wallet_id"],
        recipient_address=active_wallet["address"],
        rate_limit_sleep=5,
        agent_name=agent_id,
    )

    if result["status"] == "error":
        return {"status": "error", "message": result.get("reason", "Trade cycle failed.")}

    return {
        "status": "success" if result["status"] == "success" else "hold",
        "action": result["action"],
        "tx_hash": result.get("tx_hash"),
        "wallet_id": active_wallet["wallet_id"],
        "wallet_source": active_wallet["source"],
        "agent_id": agent_id,
        "agent_name": get_agent_profile(agent_id)["name"],
        "live_transactions": not TRADE_DRY_RUN,
    }

@app.post("/follow")
@limiter.limit(lambda: f"{RATE_LIMIT_PER_MINUTE}/minute")
def follow_agent(request: Request, req: FollowRequest, _: str = Depends(verify_privy_token)):
    agent_ids = {agent["id"] for agent in get_agent_catalog()}
    agent_id = req.agent_id if req.agent_id in agent_ids else AGENT_NAME
    log.info("Follow request from @%s for %s USDC on %s via %s", req.username, req.allocation, req.asset, agent_id)

    wallet_record = ensure_user_wallet(req.username, referral_code=req.referral_code)
    real_wallet_id = wallet_record.get("wallet_id") if wallet_record else None
    real_wallet_address = wallet_record.get("wallet_address") if wallet_record else None

    if not real_wallet_id or not real_wallet_address:
        return {"status": "error", "message": "Failed to generate Web3 wallet for user."}

    add_follower(
        user_id=wallet_record["user_id"],
        user_wallet_id=real_wallet_id,
        target_agent=agent_id,
        allocation_amount=req.allocation,
        asset=req.asset,
        user_wallet_address=real_wallet_address,
    )

    return {
        "status": "success",
        "message": f"User {req.username} secured to smart contract.",
        "wallet_id": real_wallet_id,
        "address": real_wallet_address,
        "referral_code": wallet_record.get("referral_code"),
        "agent_id": agent_id,
        "agent_name": get_agent_profile(agent_id)["name"],
        "live_transactions": not TRADE_DRY_RUN,
    }
