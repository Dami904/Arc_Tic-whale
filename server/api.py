import os
import hmac
import hashlib
import json as _json
import re
import urllib.parse
import random
import time
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Optional

import jwt as _jwt
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from backend.database import (
    add_follower,
    deactivate_follower,
    update_follower_stop_loss,
    get_all_users,
    get_follower_by_wallet_id,
    get_follower_wallet,
    get_follower_summary,
    get_follower_trade_history,
    get_setting,
    get_trade_history,
    get_latest_trade,
    get_social_posts,
    get_user,
    get_user_allocations,
    get_user_preferences,
    init_db,
    log_trade,
    log_social_post,
    set_setting,
    set_user_preferences,
    update_user_profile,
    create_session,
    get_session_user,
    create_auth_nonce,
    consume_auth_nonce,
)
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from backend.market_data import get_current_market_state
from backend.daily_summary import start_daily_summary_scheduler, send_daily_summary_reports
from backend.assistant import handle_assistant_command
from backend.notifications import build_notification_reminder, user_has_notification_channel
from backend.trade_service import run_trade_cycle
from backend.trade_executor import execute_transfer
from backend.copy_engine import evaluate_stop_losses
from backend.agents import get_agent_catalog, get_agent_profile, AGENT_PROFILES
from backend.user_wallets import ensure_user_wallet, normalize_user_id
from backend.wallet_summary import get_wallet_stats_safe
from backend.performance import get_agent_performance, get_follower_performance
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
    PRIVY_AUTH_ORIGIN,
    PRIVY_CLIENT_ID,
    WALLETCONNECT_PROJECT_ID,
    RATE_LIMIT_PER_MINUTE,
    TRADE_DRY_RUN,
    AGENT_DEV_MODE,
)
from backend.email_otp import send_otp_email
from backend.privy_jwt import verify_privy_access_token
from backend.logger import get_logger

log = get_logger("api")

_WALLET_ADDRESS_RE = re.compile(r"0x[a-fA-F0-9]{12,}")

init_db()

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_daily_summary_scheduler()
    # Trade cycles run in GitHub Actions (.github/workflows/trade-cycle.yml),
    # not in this process — the web service is allowed to sleep on Render.
    yield


app = FastAPI(
    title="Arc_Tic Whale",
    description="AI-driven copy-trading app on Circle Arc Testnet.",
    lifespan=lifespan,
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
        # Correct order per Telegram spec: HMAC_SHA256(key=bot_token, msg="WebAppData")
        secret_key = hmac.new(BOT_TOKEN.encode(), b"WebAppData", hashlib.sha256).digest()
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
    """Accepts Telegram WebApp initData (X-Telegram-Init-Data header) or Privy JWT / session token (Bearer)."""
    if TRADE_DRY_RUN and AGENT_DEV_MODE:
        # Local development only. Production sets both to false (render.yaml).
        return "dryrun_user"

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

    # Server-issued session token (email OTP fallback or wallet sign-in)
    if token.startswith("sess_"):
        try:
            user_id = get_session_user(token)
        except Exception:
            raise HTTPException(status_code=503, detail="Session store unavailable")
        if user_id:
            return user_id
        raise HTTPException(status_code=401, detail="Invalid or expired session token")

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
    stop_loss_pct: Optional[float] = 10.0
    agent_id: str = "Conservative_Whale"

class EnsureUserRequest(BaseModel):
    username: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None

class UpdateStopLossRequest(BaseModel):
    agent_id: str
    stop_loss_pct: float

class WithdrawRequest(BaseModel):
    destination_address: str
    amount: float
    asset: str = "USDC"
    username: Optional[str] = None

class TriggerTradeRequest(BaseModel):
    username: Optional[str] = None
    agent_id: str = "Conservative_Whale"

class WalletStats(BaseModel):
    total_balance_usd: float
    token_balances: list
    performance: dict

class MarketDataResponse(BaseModel):
    assets: dict
    macro_news: str

class UpdateProfileRequest(BaseModel):
    email: Optional[str] = None
    display_name: Optional[str] = None
    avatar_url: Optional[str] = None

class UpdatePreferencesRequest(BaseModel):
    trade_alerts: Optional[bool] = None
    daily_summary: Optional[bool] = None

class DetachRequest(BaseModel):
    agent_id: str

class AssistantCommandRequest(BaseModel):
    command: str
    page: Optional[str] = None
    selected_agent: Optional[str] = None

# --- API Endpoints ---
AGENT_NAME = "Conservative_Whale"
TRADE_ACTIONS = {"BUY", "SELL", "HOLD"}
FEED_ACTIONS   = {"BUY", "SELL"}          # HOLD is scheduler noise — not feed-worthy
WALLET_ACTIONS = {"DEPOSIT", "WITHDRAW"}

# ── Circle token-ID → symbol resolver ──────────────────────────────────────
# Circle transactions only carry a tokenId UUID — no symbol.
# wallet balance responses carry both the UUID (token.id) and the symbol.
# We build this map from the balances call and reuse it for transactions.
_token_id_cache: dict[str, str] = {}  # tokenId UUID → symbol string

# Known Arc Testnet address → symbol (lowercase keys)
_ARC_ADDRESS_MAP: dict[str, str] = {
    "0x3600000000000000000000000000000000000000": "USDC",
    "0x4ccccd3220ac80c07a8b575a4cb494c0e77606ed": "WETH",
    "0xf0c4a4ce82a5746abaad9425360ab04fbba432bf": "WBTC",
    "0x89b50855aa3be2f677cd6303cec089b5f319d72a": "EURC",
}


def _seed_token_cache_from_balances(wallet_id: str) -> None:
    """Populate _token_id_cache from the wallet's token balances.
    The balance response includes tb.token.id (UUID) + tb.token.symbol — no extra API call needed."""
    if not wallet_id:
        return
    try:
        client = initialize_circle_client()
        resp = WalletsApi(client).list_wallet_balance(id=wallet_id)
        if resp.data and resp.data.token_balances:
            for tb in resp.data.token_balances:
                token_uuid = getattr(tb.token, "id", None)
                symbol = getattr(tb.token, "symbol", None)
                if token_uuid and symbol:
                    _token_id_cache[str(token_uuid)] = str(symbol).upper()
    except Exception:
        pass  # cache stays empty; fall through to address map


def _resolve_token_symbol(tx: dict) -> str:
    """Return the token symbol for a Circle transaction dict."""
    # 1. Nested token object (some SDK versions expand this)
    token_obj = tx.get("token") or {}
    sym = token_obj.get("symbol") or token_obj.get("name")
    if sym:
        return str(sym).upper()

    # 2. Flat symbol fields
    for key in ("tokenSymbol", "token_symbol", "symbol"):
        if tx.get(key):
            return str(tx[key]).upper()

    # 3. UUID cache (populated by _seed_token_cache_from_balances)
    token_id = tx.get("tokenId") or tx.get("token_id")
    if token_id and str(token_id) in _token_id_cache:
        return _token_id_cache[str(token_id)]

    # 4. Token address fallback
    addr = (tx.get("tokenAddress") or tx.get("token_address") or "").lower()
    if addr and addr in _ARC_ADDRESS_MAP:
        return _ARC_ADDRESS_MAP[addr]

    return "USDC"  # safe default (Arc Testnet base currency)


def _parse_percent(value: object) -> float:
    try:
        return float(str(value or "0").replace("%", "").replace("+", "").strip())
    except ValueError:
        return 0.0


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
    wallet_id = user_wallet["wallet_id"] if user_wallet else AGENT_WALLET_ID
    wallet_address = user_wallet["wallet_address"] if user_wallet else AGENT_WALLET_ADDRESS
    if not wallet_address and wallet_id == AGENT_WALLET_ID:
        wallet_address = AGENT_WALLET_ADDRESS
    if not wallet_address:
        wallet_address = _lookup_wallet_address(wallet_id)

    return {
        "wallet_id": wallet_id,
        "address": wallet_address,
        "source": "user" if user_wallet else "configured_agent_wallet",
        "user": user_wallet,
        "allocations": get_user_allocations(user_id) if user_id else [],
    }


def _agent_status(profile: dict) -> dict:
    latest_trade = get_latest_trade(profile["id"])
    if not latest_trade:
        return {
            "label": "Ready to trade",
            "detail": "No live trades yet",
            "action": None,
            "asset": None,
            "timestamp": None,
        }

    action = latest_trade.get("action") or "HOLD"
    asset = latest_trade.get("asset") or "market"
    timestamp = latest_trade.get("timestamp")
    if action == "HOLD":
        label = "Watching the market"
        detail = latest_trade.get("reason") or "Waiting for a clean setup"
    else:
        label = f"{action} {asset}"
        detail = latest_trade.get("reason") or "Latest executed trade"
    return {
        "label": label,
        "detail": _WALLET_ADDRESS_RE.sub("0x[redacted]", str(detail or "")),
        "action": action,
        "asset": asset,
        "timestamp": timestamp,
    }


def _build_feed_entry_from_trade(trade: dict, agent_lookup: dict[str, dict]) -> dict:
    agent_id = trade.get("agent", AGENT_NAME)
    profile = agent_lookup.get(agent_id, {"name": agent_id, "avatar": "🤖"})
    action = trade.get("action") or "HOLD"
    asset = trade.get("asset") or "market"
    reason = _WALLET_ADDRESS_RE.sub("0x[redacted]", str(trade.get("reason") or "No reason recorded."))
    return {
        "type": "trade",
        "agent_id": agent_id,
        "agent": profile["name"],
        "avatar": profile["avatar"],
        "action": action,
        "asset": asset,
        "timestamp": trade.get("timestamp"),
        "body": f"{action} {asset}: {reason}",
        "tx_id": trade.get("tx_id"),
        "reason": reason,
    }


def _build_feed_entry_from_social(post: dict, agent_lookup: dict[str, dict]) -> dict:
    agent_id = post.get("agent", AGENT_NAME)
    profile = agent_lookup.get(agent_id, {"name": agent_id, "avatar": "🤖"})
    action = post.get("action") or "HOLD"
    body = _WALLET_ADDRESS_RE.sub("0x[redacted]", str(post.get("post_text") or post.get("reason") or "Agent commentary unavailable."))
    return {
        "type": "post",
        "agent_id": agent_id,
        "agent": profile["name"],
        "avatar": profile["avatar"],
        "action": action,
        "asset": None,
        "timestamp": post.get("timestamp"),
        "body": body,
        "tx_id": post.get("tx_id"),
        "reason": _WALLET_ADDRESS_RE.sub("0x[redacted]", str(post.get("reason") or "")),
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
    local_activity = (
        get_follower_trade_history(wallet_id, limit=limit, actions=WALLET_ACTIONS)
        if wallet_id and agent is None
        else get_trade_history(limit=limit, agent=agent, actions=WALLET_ACTIONS)
    )
    circle_activity = []

    if wallet_id and not TRADE_DRY_RUN:
        # Pre-populate token UUID→symbol cache from wallet balances so
        # _resolve_token_symbol can work without a separate API call per tx.
        _seed_token_cache_from_balances(wallet_id)
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
                    "asset": _resolve_token_symbol(tx),
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

@app.api_route("/ping", methods=["GET", "HEAD"])
def ping():
    """Lightweight keep-alive endpoint for UptimeRobot / health checks. No auth, no rate limit."""
    return {"status": "ok"}

@app.post("/users/ensure")
@limiter.limit(lambda: f"{RATE_LIMIT_PER_MINUTE}/minute")
def ensure_user(request: Request, req: EnsureUserRequest):
    wallet = ensure_user_wallet(
        req.username,
        email=req.email,
        display_name=req.display_name,
        avatar_url=req.avatar_url,
    )
    if not wallet:
        return {"status": "error", "message": "Failed to create or load user wallet."}
    return {
        "status": "success",
        "user": wallet,
        "live_transactions": not TRADE_DRY_RUN,
    }


@app.get("/user/profile")
def get_profile(current_user_id: str = Depends(verify_privy_token)):
    user_id = normalize_user_id(current_user_id)
    user = get_user(user_id)
    if not user:
        return {"status": "error", "message": "User not found."}
    return {
        "status": "success",
        "profile": user,
        "reminder": build_notification_reminder(user),
        "channels": {
            "email": bool(user.get("email")),
            "telegram": bool(user.get("telegram_chat_id")),
        },
    }


@app.put("/user/profile")
def update_profile(req: UpdateProfileRequest, current_user_id: str = Depends(verify_privy_token)):
    user_id = normalize_user_id(current_user_id)
    user = update_user_profile(
        user_id,
        email=req.email.strip().lower() if req.email else None,
        display_name=req.display_name.strip() if req.display_name else None,
        avatar_url=req.avatar_url.strip() if req.avatar_url else None,
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    reminder = build_notification_reminder(user)
    return {
        "status": "success",
        "profile": user,
        "reminder": reminder,
        "channels": {
            "email": bool(user.get("email")),
            "telegram": bool(user.get("telegram_chat_id")),
        },
    }

@app.get("/trade-history")
def get_history(username: Optional[str] = None):
    wallet = get_active_wallet_context(username)
    if wallet["source"] == "user":
        return {"trades": get_follower_trade_history(wallet["wallet_id"], limit=10, actions=TRADE_ACTIONS)}
    return {"trades": get_trade_history(limit=10, agent=AGENT_NAME, actions=TRADE_ACTIONS)}

@app.get("/dashboard")
def get_dashboard(username: Optional[str] = None, current_user_id: str = Depends(verify_privy_token)):
    for profile in get_agent_catalog():
        try:
            evaluate_stop_losses(profile["id"])
        except Exception as exc:
            log.warning("Stop loss evaluation failed for %s: %s", profile["id"], exc)
    # Use the authenticated user_id when no explicit username is supplied
    effective_username = username or normalize_user_id(current_user_id)
    wallet = get_active_wallet_context(effective_username)
    stats = get_wallet_stats_safe(wallet["wallet_id"])
    trades = get_trade_history(limit=20, actions=FEED_ACTIONS)   # BUY/SELL only — no HOLD noise
    social_posts = get_social_posts(limit=20)
    wallet_trades = (
        get_follower_trade_history(wallet["wallet_id"], limit=20, actions=TRADE_ACTIONS)
        if wallet["source"] == "user"
        else get_trade_history(limit=20, agent=AGENT_NAME, actions=TRADE_ACTIONS)
    )
    wallet_activity = get_wallet_activity(wallet["wallet_id"], AGENT_NAME if wallet["source"] != "user" else None, limit=20)
    dashboard_user = wallet.get("user") or {}
    user_id = dashboard_user.get("user_id")
    if wallet["source"] == "user" and wallet["allocations"] and user_id:
        _primary_follow = max(wallet["allocations"], key=lambda row: float(row.get("allocation_amount") or 0.0))
        performance = get_follower_performance(user_id, _primary_follow["target_agent"])
        metrics = get_agent_performance(_primary_follow["target_agent"])
    else:
        performance = get_agent_performance(AGENT_NAME)
        metrics = performance
    followers = get_follower_summary(AGENT_NAME)
    preferences = get_user_preferences(user_id) if user_id else {"trade_alerts": 1, "daily_summary": 1}
    agent_cards = []
    agent_lookup = {profile["id"]: profile for profile in get_agent_catalog()}
    for profile in get_agent_catalog():
        agent_metrics = get_agent_performance(profile["id"])
        agent_followers = get_follower_summary(profile["id"])
        user_allocation = 0.0
        user_allocation_row = None
        if user_id:
            user_allocation_row = get_follower_wallet(profile["id"], user_id)
            if user_allocation_row:
                user_allocation = float(user_allocation_row.get("allocation_amount") or 0.0)
        status = _agent_status(profile)
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
            "roi_24h": agent_metrics["24h"] or "N/A",
            "allocated_usdc": user_allocation,
            "has_allocation": bool(user_allocation_row),
            "stop_loss_pct": float(user_allocation_row.get("stop_loss_pct") or 10.0) if user_allocation_row else 10.0,
            "status": status["label"],
            "status_detail": status["detail"],
            "last_action": status["action"],
            "last_asset": status["asset"],
            "last_trade_at": status["timestamp"],
        })

    total_balance = stats["total_balance_usd"]
    if wallet["source"] == "user":
        allocated = sum(float(row.get("allocation_amount") or 0.0) for row in wallet["allocations"])
    else:
        allocated = followers["total_allocation"]
    free_balance = max(0.0, total_balance - allocated)
    allocation_pct = round((allocated / total_balance) * 100, 1) if total_balance else 0.0
    pnl_pct = _parse_percent(performance.get("24h"))
    pnl_amount = round(total_balance * (pnl_pct / 100.0), 2)

    feed = []
    for post in social_posts[:10]:
        feed.append(_build_feed_entry_from_social(post, agent_lookup))
    for trade in trades[:10]:
        feed.append(_build_feed_entry_from_trade(trade, agent_lookup))
    feed = sorted(feed, key=lambda item: item.get("timestamp") or "", reverse=True)[:10]

    if not feed:
        feed.append({
            "agent": None,
            "avatar": None,
            "action": "IDLE",
            "asset": None,
            "timestamp": None,
            "body": None,
            "tx_id": None,
            "type": "placeholder",
            "agent_id": None,
            "is_placeholder": True,
        })

    return {
        "profile": {
            "user_id": user_id,
            "display_name": dashboard_user.get("display_name") or dashboard_user.get("email") or dashboard_user.get("user_id") or "Member",
            "avatar_url": dashboard_user.get("avatar_url") or "",
            "email": dashboard_user.get("email") or "",
            "telegram_chat_id": dashboard_user.get("telegram_chat_id") or "",
            "member_since": dashboard_user.get("created_at") or "",
        },
        "preferences": {
            "trade_alerts": bool(preferences.get("trade_alerts")),
            "daily_summary": bool(preferences.get("daily_summary")),
        },
        "wallet": {
            "wallet_id": wallet["wallet_id"],
            "total_balance_usd": total_balance,
            "token_balances": stats["token_balances"],
            "performance": performance,
            "portfolio_pnl": {
                "amount": pnl_amount,
                "pct": pnl_pct,
            },
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
        "notification_reminder": build_notification_reminder(dashboard_user),
        "notification_channels": {
            "email": bool(dashboard_user.get("email")),
        },
    }

@app.get("/settings")
def get_app_settings(current_user_id: str = Depends(verify_privy_token)):
    user = get_user(normalize_user_id(current_user_id))
    prefs = get_user_preferences(user["user_id"]) if user else {"trade_alerts": 1, "daily_summary": 1}
    return {
        "kill_switch": get_setting("kill_switch"),
        "trade_alerts": str(int(prefs.get("trade_alerts") or 0)),
        "daily_summary": str(int(prefs.get("daily_summary") or 0)),
        "notification_reminder": build_notification_reminder(user),
        "notification_channels": {
            "email": bool(user and user.get("email")),
            "telegram": bool(user and user.get("telegram_chat_id")),
        },
    }

@app.post("/settings/{key}")
def update_setting(key: str, value: str, current_user_id: str = Depends(verify_privy_token)):
    if key not in ["kill_switch", "trade_alerts", "daily_summary"]:
        raise HTTPException(status_code=400, detail="Invalid setting")
    if key == "kill_switch":
        set_setting(key, value)
    else:
        user_id = normalize_user_id(current_user_id)
        set_user_preferences(user_id, **{key: value})
    user = get_user(normalize_user_id(current_user_id))
    return {
        "status": "success",
        "kill_switch": get_setting("kill_switch"),
        "trade_alerts": str(int((user or {}).get("trade_alerts") or 0)),
        "daily_summary": str(int((user or {}).get("daily_summary") or 0)),
        "notification_reminder": build_notification_reminder(user),
        "notification_channels": {
            "email": bool(user and user.get("email")),
            "telegram": bool(user and user.get("telegram_chat_id")),
        },
    }

@app.post("/assistant/command")
def assistant_command(req: AssistantCommandRequest, current_user_id: str = Depends(verify_privy_token)):
    user_id = normalize_user_id(current_user_id)
    ensure_user_wallet(user_id)
    result = handle_assistant_command(
        user_id,
        req.command,
        context={
            "page": req.page,
            "selected_agent": req.selected_agent,
        },
    )
    return result

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
        reason="Transfer submitted",
    )
    log_social_post(
        agent=AGENT_NAME,
        action="SELL",
        post_text=(
            f"Funds on the move — {req.amount} {req.asset.upper()} withdrawn from the vault. "
            f"Capital rotation or profit taking. Watching closely. 🐋"
        ),
        tx_id=tx_id,
        reason=f"Withdrawal of {req.amount} {req.asset.upper()} submitted.",
    )

    return {
        "status": "success",
        "message": "Withdraw submitted on Arc Testnet.",
        "tx_hash": tx_id,
        "wallet_id": wallet["wallet_id"],
        "live_transactions": not TRADE_DRY_RUN,
        "request": req.model_dump(),
    }

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
    # index.html loads /static/runtime-config.js which contains empty placeholder values.
    # We replace that tag with an inline script injecting real env vars at serve time.
    inline_config = (
        "<script>\n"
        f"  window.PRIVY_APP_ID = '{PRIVY_APP_ID}';\n"
        f"  window.PRIVY_CLIENT_ID = '{PRIVY_CLIENT_ID}';\n"
        f"  window.PRIVY_AUTH_ORIGIN = '{PRIVY_AUTH_ORIGIN}';\n"
        f"  window.WC_PROJECT_ID = '{WALLETCONNECT_PROJECT_ID}';\n"
        f"  window.API_BASE = '';\n"  # same-origin: API lives on the same server
        "</script>"
    )
    html = html.replace(
        '<script src="/static/runtime-config.js"></script>',
        inline_config,
    )
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
    # Use the full email (normalised) so alice@gmail.com and alice@yahoo.com are different users
    safe_email = email.replace("@", "_at_").replace(".", "_")
    user_id = f"email_{safe_email}"[:64]
    # Generate a cryptographically secure, unguessable session token
    session_token = create_session(user_id)
    return {"token": session_token, "user_id": user_id, "email": email}

WALLET_LOGIN_MESSAGE = "Sign in to Arc_Tic Whale\nNonce: {nonce}"


@app.get("/auth/wallet-nonce")
@limiter.limit(lambda: f"{RATE_LIMIT_PER_MINUTE}/minute")
def wallet_nonce(request: Request):
    """Issue a single-use, 5-minute nonce the wallet must sign to log in."""
    nonce = create_auth_nonce(ttl_seconds=300)
    return {"nonce": nonce, "message": WALLET_LOGIN_MESSAGE.format(nonce=nonce)}


@app.post("/auth/wallet")
@limiter.limit(lambda: f"{RATE_LIMIT_PER_MINUTE}/minute")
async def wallet_auth(request: Request, body: dict):
    address   = (body.get("address") or "").lower().strip()
    message   = (body.get("message") or "").strip()
    signature = (body.get("signature") or "").strip()
    nonce     = (body.get("nonce") or "").strip()
    if not address or not message or not signature or not nonce:
        raise HTTPException(status_code=400, detail="address, message, signature, and nonce required")
    if message != WALLET_LOGIN_MESSAGE.format(nonce=nonce):
        raise HTTPException(status_code=401, detail="Signed message does not match the expected format")
    if not consume_auth_nonce(nonce):
        raise HTTPException(status_code=401, detail="Invalid, expired, or already-used nonce")
    msg = encode_defunct(text=message)
    try:
        recovered = Account.recover_message(msg, signature=signature).lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature format")
    if recovered != address:
        raise HTTPException(status_code=401, detail="Signature verification failed")
    wallet = ensure_user_wallet(f"wallet_{address}")
    if not wallet:
        raise HTTPException(status_code=503, detail="Wallet provisioning unavailable")
    session_token = create_session(wallet["user_id"])
    return {
        "token": session_token,
        "user_id": wallet["user_id"],
        "email": "",
        "display_name": "Trading Wallet",
        "wallet_address": address,
    }


@app.get("/auth/callback")
def auth_callback():
    callback_candidates = [
        os.path.join(os.path.dirname(__file__), "../frontend/auth/callback.html"),
        os.path.join(os.path.dirname(__file__), "../frontend/callback.html"),
    ]
    for callback_path in callback_candidates:
        if Path(callback_path).is_file():
            html = Path(callback_path).read_text()
            # Inject Privy credentials at serve time so the popup can complete OAuth.
            # callback.html loads /static/runtime-config.js which has empty strings —
            # we replace that tag with an inline script containing the real values.
            inline_config = (
                "<script>\n"
                f"  window.PRIVY_APP_ID = '{PRIVY_APP_ID}';\n"
                f"  window.PRIVY_CLIENT_ID = '{PRIVY_CLIENT_ID}';\n"
                f"  window.PRIVY_AUTH_ORIGIN = '{PRIVY_AUTH_ORIGIN}';\n"
                "</script>"
            )
            # Replace the runtime-config.js script tag with the inline version
            html = html.replace(
                '<script src="/static/runtime-config.js"></script>',
                inline_config,
            )
            return HTMLResponse(content=html)
    raise HTTPException(status_code=404, detail="Callback page not found")


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

@app.get("/scheduler/status")
def scheduler_status(_: str = Depends(verify_privy_token)):
    """Reports the last trade cycle, derived from trade_history (cycles run in GitHub Actions)."""
    latest = None
    for agent_id in AGENT_PROFILES:
        trade = get_latest_trade(agent_id)
        if trade and (latest is None or trade["timestamp"] > latest["timestamp"]):
            latest = trade
    return {
        "interval_hours": 2.0,
        "runner":         "github-actions",
        "last_trade":     latest,
        # Trades execute in the Actions job, which pins TRADE_DRY_RUN=false —
        # this web process's own TRADE_DRY_RUN value is not what actually runs.
        "mode":           "live",
    }


@app.post("/follow")
@limiter.limit(lambda: f"{RATE_LIMIT_PER_MINUTE}/minute")
def follow_agent(request: Request, req: FollowRequest, _: str = Depends(verify_privy_token)):
    agent_ids = {agent["id"] for agent in get_agent_catalog()}
    agent_id = req.agent_id if req.agent_id in agent_ids else AGENT_NAME
    log.info("Follow request from @%s for %s USDC on %s via %s", req.username, req.allocation, req.asset, agent_id)

    wallet_record = ensure_user_wallet(req.username)
    real_wallet_id = wallet_record.get("wallet_id") if wallet_record else None
    real_wallet_address = wallet_record.get("wallet_address") if wallet_record else None

    if not real_wallet_id or not real_wallet_address:
        return {"status": "error", "message": "Failed to generate Web3 wallet for user."}

    # ── Server-side balance check (authoritative — frontend cache can be stale) ──
    wallet_stats = get_wallet_stats_safe(real_wallet_id)
    token_list   = wallet_stats.get("token_balances") or []
    usdc_token   = next((t for t in token_list if str(t.get("symbol", "")).upper() == "USDC"), None)
    usdc_balance = float(usdc_token["amount"]) if usdc_token else float(wallet_stats.get("total_balance_usd") or 0)
    if req.allocation > usdc_balance:
        return {
            "status": "error",
            "message": f"Insufficient balance — you have {usdc_balance:.2f} USDC available.",
        }

    add_follower(
        user_id=wallet_record["user_id"],
        user_wallet_id=real_wallet_id,
        target_agent=agent_id,
        allocation_amount=req.allocation,
        asset=req.asset,
        user_wallet_address=real_wallet_address,
        stop_loss_pct=req.stop_loss_pct,
    )

    # ── Feed entry: new copy event (single social post only — no duplicate trade log) ──
    agent_profile = get_agent_profile(agent_id)
    _copy_reason = f"New follower allocated {req.allocation} USDC to copy {agent_profile['name']}."
    log_social_post(
        agent=agent_id,
        action="BUY",
        post_text=(
            f"New capital in the stream. A follower just allocated {req.allocation} USDC "
            f"to mirror my moves. The thesis holds. 🐋"
        ),
        tx_id=None,
        reason=_copy_reason,
    )

    return {
        "status": "success",
        "message": f"User {req.username} secured to smart contract.",
        "wallet_id": real_wallet_id,
        "address": real_wallet_address,
        "stop_loss_pct": req.stop_loss_pct,
        "agent_id": agent_id,
        "agent_name": get_agent_profile(agent_id)["name"],
        "live_transactions": not TRADE_DRY_RUN,
    }


@app.get("/agents")
def list_agents():
    """Public endpoint — returns all agent profiles with live metrics. No auth required."""
    catalog = get_agent_catalog()
    result = []
    for profile in catalog:
        metrics = get_agent_performance(profile["id"])
        followers = get_follower_summary(profile["id"])
        status = _agent_status(profile)
        result.append({
            "id":           profile["id"],
            "name":         profile["name"],
            "avatar":       profile["avatar"],
            "risk":         profile["risk"],
            "risk_label":   profile["risk_label"],
            "description":  profile["description"],
            "win_rate":     metrics["win_rate"],
            "trades":       metrics["total_trades"],
            "followers":    followers["total_followers"],
            "status":       status["label"],
            "status_detail":status["detail"],
            "last_action":  status["action"],
            "last_asset":   status["asset"],
            "last_trade_at":status["timestamp"],
        })
    return {"agents": result}


@app.post("/agents/update-stop-loss")
def update_stop_loss(req: UpdateStopLossRequest, current_user_id: str = Depends(verify_privy_token)):
    """Update stop-loss % on an active allocation without detaching and re-following."""
    agent_ids = {agent["id"] for agent in get_agent_catalog()}
    if req.agent_id not in agent_ids:
        raise HTTPException(status_code=400, detail="Invalid agent")
    if not (0 < req.stop_loss_pct <= 100):
        raise HTTPException(status_code=400, detail="stop_loss_pct must be between 0 and 100")
    user_id = normalize_user_id(current_user_id)
    updated = update_follower_stop_loss(user_id, req.agent_id, req.stop_loss_pct)
    if not updated:
        return {"status": "skipped", "message": "No active allocation found for this agent."}
    return {
        "status": "success",
        "agent_id":      req.agent_id,
        "stop_loss_pct": req.stop_loss_pct,
        "message":       f"Stop-loss updated to {req.stop_loss_pct:.1f}% for {req.agent_id}.",
    }


@app.post("/agents/detach")
def detach_agent(req: DetachRequest, current_user_id: str = Depends(verify_privy_token)):
    agent_ids = {agent["id"] for agent in get_agent_catalog()}
    if req.agent_id not in agent_ids:
        raise HTTPException(status_code=400, detail="Invalid agent")
    user_id = normalize_user_id(current_user_id)
    detached = deactivate_follower(user_id, req.agent_id)
    if not detached:
        return {"status": "skipped", "message": "No active allocation found for this agent."}

    # ── Feed entry: detach event ────────────────────────────────────────────
    _det_profile = get_agent_profile(req.agent_id)
    _det_reason = f"A follower exited their copy position from {_det_profile['name']}."
    log_social_post(
        agent=req.agent_id,
        action="SELL",
        post_text=(
            "A position was closed. A follower detached from my copy stream — "
            "risk management at play. The strategy continues. 🐋"
        ),
        tx_id=None,
        reason=_det_reason,
    )

    return {
        "status": "success",
        "message": f"Detached from {req.agent_id}.",
    }
