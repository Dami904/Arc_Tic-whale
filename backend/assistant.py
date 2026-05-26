from __future__ import annotations

import re
from typing import Optional

from google import genai

from backend.agents import get_agent_catalog, get_agent_profile
from backend.config import GOOGLE_API_KEY
from backend.database import (
    deactivate_follower,
    get_follower_trade_history,
    get_follower_summary,
    get_trade_metrics,
    get_user,
    get_user_allocations,
    get_user_preferences,
    set_user_preferences,
)
from backend.logger import get_logger
from backend.wallet_summary import get_wallet_stats_safe

log = get_logger("assistant")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _word_in(word: str, text: str) -> bool:
    return bool(re.search(rf"\b{re.escape(word)}\b", text))


def _agent_index() -> list[dict]:
    return list(get_agent_catalog())


def _match_agent_name(command: str) -> Optional[str]:
    normalized = _clean(command)
    for agent in _agent_index():
        agent_id = _clean(agent["id"])
        agent_name = _clean(agent["name"])
        if agent_id and agent_id in normalized:
            return agent["id"]
        if agent_name and agent_name in normalized:
            return agent["id"]
    return None


def _best_agent() -> dict:
    best = None
    for agent in _agent_index():
        metrics = get_trade_metrics(agent["id"])
        followers = get_follower_summary(agent["id"])
        score = (
            float(metrics.get("win_rate") or 0),
            float(metrics.get("total_trades") or 0),
            float(followers.get("total_followers") or 0),
        )
        candidate = {
            "id": agent["id"],
            "name": agent["name"],
            "avatar": agent["avatar"],
            "metrics": metrics,
            "score": score,
        }
        if best is None or candidate["score"] > best["score"]:
            best = candidate
    return best or {
        "id": "Conservative_Whale",
        "name": "Arc_Tic Whale",
        "avatar": "🐋",
        "metrics": {"win_rate": 0, "total_trades": 0},
    }


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


def _format_agent_list(items: list[dict]) -> str:
    if not items:
        return "You are not copying any agents yet."
    lines = []
    for item in items:
        lines.append(f"- {item['agent_name']}: {item['allocation']:.2f} USDC")
    return "\n".join(lines)


def _extract_bool_toggle(command: str) -> Optional[bool]:
    normalized = _clean(command)
    if any(token in normalized for token in ["alerts on", "turn alerts on", "enable alerts", "trade alerts on"]):
        return True
    if any(token in normalized for token in ["alerts off", "turn alerts off", "disable alerts", "trade alerts off"]):
        return False
    return None


def _context_suggestions(page: str | None, selected_agent: str | None, allocations: list[dict]) -> list[str]:
    page_key = _clean(page)
    agent_label = (selected_agent or "").replace("_", " ").strip() or "this agent"
    suggestions = ["What is copy trading?", "Show my copied agents", "What is my P&L?"]

    if page_key == "home":
        suggestions = [
            f"Copy {agent_label}",
            f"What is {agent_label}?",
            "What is stop loss?",
        ]
    elif page_key == "market":
        suggestions = [
            "Which agent is best this week?",
            f"Copy {agent_label}",
            "How does proportional execution work?",
        ]
    elif page_key == "feed":
        suggestions = [
            "Explain this trade",
            "Show recent trades",
            "What is copy trading?",
        ]
    elif page_key == "trades":
        suggestions = [
            "What is my P&L?",
            "What is stop loss?",
            "Show my copied agents",
        ]
    elif page_key == "wallet":
        suggestions = [
            "What is my P&L?",
            "How does allocation work?",
            "What is copy trading?",
        ]

    if allocations:
        suggestions.append("Detach me from " + allocations[0]["agent_name"])
    return suggestions[:4]


# ---------------------------------------------------------------------------
# Gemini-powered natural language reply
# ---------------------------------------------------------------------------

def _build_system_prompt(
    wallet_stats: dict,
    allocations: list[dict],
    preferences: dict,
    user_id: str,
) -> str:
    """Build the system prompt injected with the user's live account context."""
    total = float(wallet_stats.get("total_balance_usd") or 0)
    pnl_24h = wallet_stats.get("performance", {}).get("24h", "0.00%")
    pnl_7d  = wallet_stats.get("performance", {}).get("7d",  "0.00%")
    pnl_1y  = wallet_stats.get("performance", {}).get("1y",  "0.00%")

    tokens = wallet_stats.get("token_balances") or []
    token_str = ", ".join(
        f"{t.get('amount', '0')} {t.get('symbol', '')}" for t in tokens
    ) or "no tokens"

    agent_str = _format_agent_list(allocations)

    alerts_on = bool(preferences.get("trade_alerts", True))
    daily_on  = bool(preferences.get("daily_summary", True))

    # Fetch last 3 trades for context (lightweight)
    try:
        wallet_id = None
        from backend.database import get_user
        u = get_user(user_id)
        if u:
            wallet_id = u.get("wallet_id")
        trade_rows = []
        if wallet_id:
            from backend.database import get_follower_trade_history
            trade_rows = get_follower_trade_history(wallet_id, limit=3, actions={"BUY", "SELL", "HOLD"})
    except Exception:
        trade_rows = []

    if trade_rows:
        trade_lines = []
        for row in trade_rows:
            ts = (row.get("timestamp") or "")[:10]
            trade_lines.append(
                f"  [{ts}] {row['action']} {row.get('asset') or 'market'} — {row.get('reason') or 'no reason'}"
            )
        trade_str = "\n".join(trade_lines)
    else:
        trade_str = "  No trades recorded yet."

    return f"""You are Agent Wale — the AI assistant built into Arc_Tic Whale, a copy trading platform on Arc Testnet (blockchain).

YOUR SCOPE: You ONLY answer questions about Arc_Tic Whale — the user's wallet, their trades, copied agents, platform concepts (copy trading, P&L, allocations, stop loss, take profit, Uniswap swaps), or how the app works. If the user asks about ANYTHING ELSE (coding, general trivia, news, weather, math problems, other apps, politics, entertainment, etc.), politely refuse and say something like: "I can only help with Arc_Tic Whale questions. Ask me about your wallet, trades, or how copy trading works."

PLATFORM OVERVIEW:
- Arc_Tic Whale is a DeFi copy trading platform on Arc Testnet (EVM chain by Circle)
- Users connect a Circle Developer-Controlled Wallet and allocate USDC to AI trading agents
- When an agent trades on Uniswap V3 (BUY/SELL), the user's wallet mirrors it proportionally
- Supported tokens: USDC, WETH, WBTC, EURC on Arc Testnet
- Users can detach from agents at any time (stops copy trading)
- Trade alerts and daily summaries are configurable per user

USER'S LIVE ACCOUNT DATA (right now):
- Wallet total: ${total:.2f} USDC equivalent
- Token breakdown: {token_str}
- 24h performance: {pnl_24h}
- 7d performance: {pnl_7d}
- 1y performance: {pnl_1y}
- Copied agents ({len(allocations)}):
{agent_str}
- Recent trades:
{trade_str}
- Trade alerts: {'ON' if alerts_on else 'OFF'}
- Daily summary: {'ON' if daily_on else 'OFF'}

RESPONSE RULES:
1. Be helpful, concise, and confident. Answer in 1–4 plain text sentences.
2. Use the live data above to give specific, personalised answers (not generic).
3. Never make up trade data, prices, or events not shown above.
4. If you don't know something specific (e.g., a future price), say so honestly.
5. If the user wants to take an action (detach, toggle alerts), confirm clearly and tell them it will be applied.
6. Do NOT use markdown bullet points or headers in replies — plain conversational text only.
7. Refuse off-topic questions gracefully and redirect to the platform."""


def _gemini_reply(question: str, system_prompt: str) -> str:
    """Call Gemini 2.5 Flash and return the text response."""
    if not GOOGLE_API_KEY:
        raise RuntimeError("GOOGLE_API_KEY not configured")

    client = genai.Client(api_key=GOOGLE_API_KEY)
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=question,
        config=genai.types.GenerateContentConfig(
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
        return {
            "status": "error",
            "reply": "I couldn't load your account yet. Refresh once and I'll sync your wallet.",
        }

    command_text = command.strip()
    if not command_text:
        return {
            "status": "error",
            "reply": "Tell me what you want to know — try 'What is my P&L?' or 'Show my copied agents'.",
        }

    normalized   = _clean(command_text)
    context      = context or {}
    wallet_stats = get_wallet_stats_safe(user.get("wallet_id"))
    preferences  = get_user_preferences(user_id)
    allocations  = _copied_agents(user_id)
    suggestions  = _context_suggestions(context.get("page"), context.get("selected_agent"), allocations)

    # ------------------------------------------------------------------
    # 1. Hard-action handlers — keyword-triggered, no LLM needed
    # ------------------------------------------------------------------

    # Detach from agent
    if any(word in normalized for word in ["detach", "stop copying", "remove"]):
        detach_target = _match_agent_name(command_text)
        if not detach_target:
            return {
                "status": "needs_input",
                "reply": "Which agent should I detach? Try using the agent name.",
                "suggestions": suggestions,
            }
        detached = deactivate_follower(user_id, detach_target)
        if not detached:
            return {
                "status": "skipped",
                "reply": f"You're not actively copying {get_agent_profile(detach_target)['name']}.",
                "suggestions": suggestions,
            }
        return {
            "status": "success",
            "reply": f"Done — detached you from {get_agent_profile(detach_target)['name']}.",
            "data": {"agent_id": detach_target},
            "suggestions": suggestions,
        }

    # Toggle daily summary
    alert_toggle = _extract_bool_toggle(command_text)
    if "daily summary" in normalized and alert_toggle is not None:
        set_user_preferences(user_id, daily_summary=alert_toggle)
        return {
            "status": "success",
            "reply": f"Daily summary is now {'on' if alert_toggle else 'off'}.",
            "data": {"daily_summary": alert_toggle},
            "suggestions": suggestions,
        }

    # Toggle trade alerts
    if alert_toggle is not None and "alert" in normalized:
        set_user_preferences(user_id, trade_alerts=alert_toggle)
        return {
            "status": "success",
            "reply": f"Trade alerts are now {'on' if alert_toggle else 'off'}.",
            "data": {"trade_alerts": alert_toggle},
            "suggestions": suggestions,
        }

    # ------------------------------------------------------------------
    # 2. Gemini LLM — handles all Q&A naturally and within scope
    # ------------------------------------------------------------------
    try:
        system_prompt = _build_system_prompt(wallet_stats, allocations, preferences, user_id)
        reply = _gemini_reply(command_text, system_prompt)
        if not reply:
            raise ValueError("Empty reply from Gemini")
        return {
            "status": "success",
            "reply": reply,
            "suggestions": suggestions,
        }
    except Exception as exc:
        log.warning("Gemini assistant failed, using fallback: %s", exc)

    # ------------------------------------------------------------------
    # 3. Static fallback (if Gemini is unavailable / key missing)
    # ------------------------------------------------------------------
    total   = float(wallet_stats.get("total_balance_usd") or 0)
    pnl_24h = wallet_stats.get("performance", {}).get("24h", "0.00%")

    _pnl_signals = [
        "p&l", "pnl", "profit", "balance", "how much", "earned", "earn",
        "gain", "loss", "returns", "performance", "worth", "wallet value",
        "my money", "funds", "usdc",
    ]
    if any(s in normalized for s in _pnl_signals):
        tokens = wallet_stats.get("token_balances") or []
        token_lines = ", ".join(f"{t.get('amount','0')} {t.get('symbol','')}" for t in tokens) or "none"
        return {
            "status": "success",
            "reply": (
                f"Your wallet holds {total:.2f} USDC equivalent (tokens: {token_lines}). "
                f"24h P&L: {pnl_24h}."
            ),
            "data": {"wallet": wallet_stats},
            "suggestions": suggestions,
        }

    if any(s in normalized for s in ["copied", "following", "copying", "my agents", "which agents"]):
        return {
            "status": "success",
            "reply": _format_agent_list(allocations),
            "data": {"copied_agents": allocations},
            "suggestions": suggestions,
        }

    if any(s in normalized for s in ["trade history", "recent trade", "my trades", "show trades"]):
        wallet_id = user.get("wallet_id")
        history = get_follower_trade_history(wallet_id, limit=5, actions={"BUY", "SELL", "HOLD"}) if wallet_id else []
        if not history:
            reply = "No trades yet for this wallet."
        else:
            lines = [
                f"[{(row.get('timestamp') or '')[:10]}] {row['action']} {row.get('asset') or 'market'}: {row.get('reason') or 'No reason recorded.'}"
                for row in history
            ]
            reply = "\n".join(lines)
        return {
            "status": "success",
            "reply": reply,
            "data": {"trades": history},
            "suggestions": suggestions,
        }

    return {
        "status": "unsupported",
        "reply": (
            "I can answer questions about your wallet, P&L, copied agents, and recent trades, "
            "or help you detach from an agent and toggle alerts."
        ),
        "suggestions": suggestions,
    }
