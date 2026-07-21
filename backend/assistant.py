from __future__ import annotations

import re
from typing import Optional

from google import genai
from google.genai import types as genai_types

from backend.agents import get_agent_catalog, get_agent_profile
from backend.config import GOOGLE_API_KEY
from backend.database import (
    deactivate_follower,
    get_follower_trade_history,
    get_follower_summary,
    get_user,
    get_user_allocations,
    get_user_preferences,
    set_user_preferences,
)
from backend.logger import get_logger
from backend.performance import get_agent_performance, get_follower_performance
from backend.wallet_summary import get_wallet_stats_safe

log = get_logger("assistant")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _agent_index() -> list[dict]:
    return list(get_agent_catalog())


def _match_agent_name(command: str) -> Optional[str]:
    """Match an agent name - exact substring first, then key-word fallback."""
    normalized = _clean(command)
    # Pass 1: exact substring (id or full name)
    for agent in _agent_index():
        if _clean(agent["id"]) in normalized:
            return agent["id"]
        if _clean(agent["name"]) in normalized:
            return agent["id"]
    # Pass 2: any significant word from the agent name (≥4 chars)
    for agent in _agent_index():
        words = [w for w in _clean(agent["name"]).split() if len(w) >= 4]
        if any(w in normalized for w in words):
            return agent["id"]
    return None


def _copied_agents(user_id: str) -> list[dict]:
    allocations = get_user_allocations(user_id)
    agent_lookup = {agent["id"]: agent for agent in _agent_index()}
    copied = []
    for allocation in allocations:
        agent_id = allocation["target_agent"]
        profile = agent_lookup.get(agent_id, get_agent_profile(agent_id))
        copied.append({
            "agent_id": agent_id,
            "agent_name": profile["name"],
            "avatar": profile["avatar"],
            "allocation": float(allocation.get("allocation_amount") or 0),
            "asset": allocation.get("asset") or "USDC",
        })
    return copied


def _primary_follow_agent_id(allocations: list[dict]) -> Optional[str]:
    """Largest-allocation agent among the user's active follows - same
    'primary follow' convention used for the dashboard and daily summary."""
    if not allocations:
        return None
    return max(allocations, key=lambda a: a.get("allocation") or 0)["agent_id"]


def _user_pnl_windows(user_id: str, allocations: list[dict]) -> dict:
    """Real 24h/7d/1y P&L for the user's primary (largest-allocation) follow.
    Not following anyone, or too little history, both read as honest
    'insufficient data' rather than a fabricated number."""
    agent_id = _primary_follow_agent_id(allocations)
    if not agent_id:
        return {"24h": "N/A (not following an agent yet)", "7d": "N/A", "1y": "N/A"}
    perf = get_follower_performance(user_id, agent_id)
    return {
        "24h": perf.get("24h") or "insufficient data yet",
        "7d": perf.get("7d") or "insufficient data yet",
        "1y": perf.get("1y") or "insufficient data yet",
    }


def _format_agent_list(items: list[dict]) -> str:
    if not items:
        return "You are not copying any agents yet."
    return "\n".join(f"- {item['agent_name']}: {item['allocation']:.2f} USDC" for item in items)


def _extract_bool_toggle(command: str) -> Optional[bool]:
    normalized = _clean(command)
    _on = [
        "alerts on", "turn alerts on", "enable alerts", "trade alerts on",
        "enable trade alerts", "summary on", "turn summary on",
        "notification on", "turn notification on",
    ]
    _off = [
        "alerts off", "turn alerts off", "disable alerts", "trade alerts off",
        "disable trade alerts", "summary off", "turn summary off",
        "notification off", "turn notification off",
    ]
    if any(t in normalized for t in _on):
        return True
    if any(t in normalized for t in _off):
        return False
    # Generic "… on" / "… off" at end of sentence
    if re.search(r"\bon\b\s*$", normalized):
        return True
    if re.search(r"\boff\b\s*$", normalized):
        return False
    return None


def _parse_copy_intent(command_text: str):
    """
    Detect 'copy [agent] with [amount] at [stop_loss]%' patterns.
    Returns (agent_id, amount, stop_loss_pct) or None if not a copy command.
    """
    normalized = _clean(command_text)
    # Must contain a copy-intent word AND an agent name
    _copy_words = ["copy", "start copying", "follow", "attach to", "mirror", "i want to copy", "want to copy"]
    if not any(w in normalized for w in _copy_words):
        return None
    # Don't trigger on negations, detach intents, or educational questions
    _exclusions = [
        "what is copy", "how does copy", "explain copy", "what's copy",
        "stop cop", "stop follow", "dont copy", "don't copy", "no longer copy",
        "detach", "remove", "exit copy", "unfollow", "stop mirror",
    ]
    if any(q in normalized for q in _exclusions):
        return None
    agent_id = _match_agent_name(command_text)
    if not agent_id:
        return None
    # Parse optional amount
    amount_match = re.search(r"\$?\s*(\d+(?:\.\d+)?)\s*(?:usdc|usd|\$)?", command_text, re.IGNORECASE)
    amount = float(amount_match.group(1)) if amount_match else None
    # Parse optional stop loss
    sl_match = re.search(r"(\d+(?:\.\d+)?)\s*%?\s*stop[\s\-]?loss", command_text, re.IGNORECASE)
    stop_loss = float(sl_match.group(1)) if sl_match else 10.0
    return agent_id, amount, stop_loss


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

def _build_system_prompt(
    wallet_stats: dict,
    allocations: list[dict],
    preferences: dict,
    user_id: str,
) -> str:
    total   = float(wallet_stats.get("total_balance_usd") or 0)
    _pnl    = _user_pnl_windows(user_id, allocations)
    pnl_24h = _pnl["24h"]
    pnl_7d  = _pnl["7d"]
    pnl_1y  = _pnl["1y"]

    tokens = wallet_stats.get("token_balances") or []
    token_str = ", ".join(f"{t.get('amount','0')} {t.get('symbol','')}" for t in tokens) or "no tokens"

    alerts_on = bool(preferences.get("trade_alerts", True))
    daily_on  = bool(preferences.get("daily_summary", True))

    # Copied agents
    agent_copy_str = _format_agent_list(allocations) if allocations else "  None yet."

    # Recent trades (last 3)
    try:
        u = get_user(user_id)
        wallet_id = u.get("wallet_id") if u else None
        trade_rows = get_follower_trade_history(wallet_id, limit=3, actions={"BUY", "SELL", "HOLD"}) if wallet_id else []
    except Exception:
        trade_rows = []
    trade_str = "\n".join(
        f"  [{(r.get('timestamp') or '')[:10]}] {r['action']} {r.get('asset') or 'market'} - {r.get('reason') or 'no reason'}"
        for r in trade_rows
    ) or "  No trades recorded yet."

    # Agent catalog with live metrics
    agent_lines = []
    for agent in _agent_index():
        metrics = get_agent_performance(agent["id"])
        followers = get_follower_summary(agent["id"])
        agent_lines.append(
            f"  - {agent['name']} ({agent['id']}): "
            f"win rate {metrics.get('win_rate', 0)}%, "
            f"{metrics.get('total_trades', 0)} trades, "
            f"{followers.get('total_followers', 0)} followers, "
            f"risk: {agent.get('risk_label', 'unknown')}"
        )
    agent_catalog_str = "\n".join(agent_lines) or "  No agents available."

    return f"""You are Agent Wale - the AI assistant inside Arc_Tic Whale, a copy trading platform on Arc Testnet (blockchain by Circle).

YOUR SCOPE: ONLY answer questions about Arc_Tic Whale - wallet, trades, copied agents, platform concepts (copy trading, P&L, allocations, stop loss, Uniswap swaps, how the app works), or the agents listed below. If the user asks about ANYTHING ELSE (coding, news, weather, math, other apps, politics, entertainment, random facts), politely decline: "I can only help with Arc_Tic Whale questions."

GREETINGS: If the user sends a greeting - ANY casual opener like hi, hello, hey, sup, wassup, yo, what's good, howdy, or any slang - respond warmly, introduce yourself, and offer to help. Never refuse a greeting as off-topic.

PLATFORM OVERVIEW:
- Arc_Tic Whale is a DeFi copy trading platform on Arc Testnet (EVM chain by Circle)
- Users connect a Circle Developer-Controlled Wallet and allocate USDC to AI trading agents
- When an agent trades on Uniswap V3 (BUY/SELL), the user's wallet mirrors it proportionally
- Supported tokens: USDC, WETH, WBTC, EURC on Arc Testnet
- Users can copy agents, detach from agents, and adjust stop-loss % at any time

AVAILABLE AGENTS (live metrics):
{agent_catalog_str}

USER'S LIVE ACCOUNT DATA:
- Wallet total: ${total:.2f} USDC equivalent
- Token breakdown: {token_str}
- 24h P&L: {pnl_24h} | 7d: {pnl_7d} | 1y: {pnl_1y}
- Copied agents ({len(allocations)}):
{agent_copy_str}
- Recent trades:
{trade_str}
- Trade alerts: {'ON' if alerts_on else 'OFF'} | Daily summary: {'ON' if daily_on else 'OFF'}

RESPONSE RULES:
1. Be helpful, direct, and confident. Answer in 1–4 plain text sentences - no bullet points, no markdown headers.
2. Use the live data above to give specific, personalised answers. Never invent numbers.
3. If the user asks about agent performance or "which is best", use the AVAILABLE AGENTS metrics above.
4. If you don't know something specific, say so honestly.
5. Treat greetings warmly - never refuse them.
6. Refuse off-topic questions gracefully and redirect to the platform."""


# ---------------------------------------------------------------------------
# Gemini call
# ---------------------------------------------------------------------------

def _gemini_reply(question: str, system_prompt: str) -> str:
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY not configured")
    client = genai.Client(api_key=GOOGLE_API_KEY)
    response = client.models.generate_content(
        model="gemini-3.1-flash-lite",
        contents=question,
        config=genai_types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.4,
            max_output_tokens=300,
        ),
    )
    return (response.text or "").strip()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def handle_assistant_command(user_id: str, command: str, context: Optional[dict] = None) -> dict:
    user = get_user(user_id)
    if not user:
        return {"status": "error", "reply": "I couldn't load your account yet. Refresh and try again."}

    command_text = command.strip()
    if not command_text:
        return {"status": "error", "reply": "Type a question or command - e.g. 'What is my P&L?' or 'Copy Arc_Tic Whale'."}

    normalized   = _clean(command_text)
    context      = context or {}
    wallet_stats = get_wallet_stats_safe(user.get("wallet_id"))
    preferences  = get_user_preferences(user_id)
    allocations  = _copied_agents(user_id)

    # ------------------------------------------------------------------
    # 1. Copy intent - open the copy modal pre-filled
    # ------------------------------------------------------------------
    copy_intent = _parse_copy_intent(command_text)
    if copy_intent:
        agent_id, amount, stop_loss = copy_intent
        profile = get_agent_profile(agent_id)
        if amount:
            reply = (
                f"Opening copy setup for {profile['name']} - "
                f"{amount:.0f} USDC with {stop_loss:.0f}% stop loss. Review and confirm in the modal."
            )
        else:
            reply = f"Opening copy setup for {profile['name']}. Set your USDC allocation in the modal."
        return {
            "status": "action_required",
            "reply": reply,
            "data": {
                "action": "open_copy_modal",
                "agent_id": agent_id,
                "agent_name": profile["name"],
                "agent_avatar": profile.get("avatar", "🤖"),
                "allocation": amount,
                "stop_loss_pct": stop_loss,
            },
        }

    # ------------------------------------------------------------------
    # 2. Detach from agent
    # ------------------------------------------------------------------
    if any(w in normalized for w in ["detach", "stop copying", "remove"]):
        detach_target = _match_agent_name(command_text)
        if not detach_target:
            return {"status": "needs_input", "reply": "Which agent should I detach? Use the agent name."}
        detached = deactivate_follower(user_id, detach_target)
        if not detached:
            return {"status": "skipped", "reply": f"You're not actively copying {get_agent_profile(detach_target)['name']}."}
        return {"status": "success", "reply": f"Done - detached you from {get_agent_profile(detach_target)['name']}."}

    # ------------------------------------------------------------------
    # 3. Toggle daily summary
    # ------------------------------------------------------------------
    alert_toggle = _extract_bool_toggle(command_text)
    if "daily summary" in normalized and alert_toggle is not None:
        set_user_preferences(user_id, daily_summary=alert_toggle)
        return {"status": "success", "reply": f"Daily summary is now {'on' if alert_toggle else 'off'}."}

    # ------------------------------------------------------------------
    # 4. Toggle trade alerts
    # ------------------------------------------------------------------
    if alert_toggle is not None and "alert" in normalized:
        set_user_preferences(user_id, trade_alerts=alert_toggle)
        return {"status": "success", "reply": f"Trade alerts are now {'on' if alert_toggle else 'off'}."}

    # ------------------------------------------------------------------
    # 5. Gemini LLM - all Q&A, greetings, agent comparisons, etc.
    # ------------------------------------------------------------------
    try:
        system_prompt = _build_system_prompt(wallet_stats, allocations, preferences, user_id)
        reply = _gemini_reply(command_text, system_prompt)
        if reply:
            return {"status": "success", "reply": reply}
    except Exception as exc:
        log.warning("Gemini assistant failed: %s", exc)

    # ------------------------------------------------------------------
    # 6. Static fallback (if Gemini is unavailable)
    # ------------------------------------------------------------------
    total   = float(wallet_stats.get("total_balance_usd") or 0)
    pnl_24h = _user_pnl_windows(user_id, allocations)["24h"]
    if any(s in normalized for s in ["p&l", "pnl", "profit", "balance", "how much", "wallet", "funds"]):
        tokens = wallet_stats.get("token_balances") or []
        token_lines = ", ".join(f"{t.get('amount','0')} {t.get('symbol','')}" for t in tokens) or "none"
        return {"status": "success", "reply": f"Wallet: {total:.2f} USDC ({token_lines}). 24h P&L: {pnl_24h}."}
    if any(s in normalized for s in ["copied", "following", "copying", "my agents"]):
        return {"status": "success", "reply": _format_agent_list(allocations)}
    if any(s in normalized for s in ["trade history", "recent trade", "my trades"]):
        wallet_id = user.get("wallet_id")
        history = get_follower_trade_history(wallet_id, limit=5, actions={"BUY", "SELL", "HOLD"}) if wallet_id else []
        if not history:
            return {"status": "success", "reply": "No trades yet for this wallet."}
        lines = [f"[{(r.get('timestamp') or '')[:10]}] {r['action']} {r.get('asset') or 'market'}: {r.get('reason') or 'No reason.'}" for r in history]
        return {"status": "success", "reply": "\n".join(lines)}

    return {"status": "success", "reply": "I can help with your wallet, P&L, agents, and trades. What would you like to know?"}
