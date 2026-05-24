from __future__ import annotations

import re
from typing import Optional

from backend.agents import get_agent_catalog, get_agent_profile
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
from backend.wallet_summary import get_wallet_stats_safe


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


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
    return best or {"id": "Conservative_Whale", "name": "Arc_Tic Whale", "avatar": "🐋", "metrics": {"win_rate": 0, "total_trades": 0}}


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
        lines.append(f"- {item['agent_name']}: {item['allocation']:.2f} USDC on {item['asset']}")
    return "\n".join(lines)


def _extract_bool_toggle(command: str) -> Optional[bool]:
    normalized = _clean(command)
    if any(token in normalized for token in ["alerts on", "turn alerts on", "enable alerts", "trade alerts on"]):
        return True
    if any(token in normalized for token in ["alerts off", "turn alerts off", "disable alerts", "trade alerts off"]):
        return False
    return None


def _assistant_reply_to_qa(command_text: str, normalized: str, wallet_stats: dict, allocations: list[dict]) -> Optional[dict]:
    if any(token in normalized for token in ["what is copy trading", "what is copy-trading", "copy trading", "copytrade"]):
        return {
            "status": "success",
            "reply": (
                "Copy trading means you attach your wallet to an agent and automatically mirror its trades. "
                "You choose the allocation, and the platform mirrors buys and sells proportionally."
            ),
            "suggestions": ["What is stop loss?", "How do allocations work?", "Show my copied agents"],
        }

    if any(token in normalized for token in ["what is stop loss", "what is stop-loss", "stop loss", "stop-loss"]):
        return {
            "status": "success",
            "reply": (
                "A stop loss is a safety limit. If the copied position drops past your threshold, the bot can auto-exit "
                "and detach that agent relationship."
            ),
            "suggestions": ["How does take profit work?", "Explain P&L", "What is copy trading?"],
        }

    if any(token in normalized for token in ["what is pnl", "what does pnl mean", "p&l meaning", "profit and loss", "pnl"]):
        pnl = wallet_stats.get("performance", {}).get("24h", "0.00%")
        return {
            "status": "success",
            "reply": (
                f"P&L means profit and loss. In your wallet, the recent change is {pnl}, and your current balance is "
                f"{float(wallet_stats.get('total_balance_usd') or 0):.2f} USDC."
            ),
            "suggestions": ["What is copy trading?", "Show my copied agents", "Which agent is best this week?"],
        }

    if any(token in normalized for token in ["what is allocation", "how does allocation work", "allocation", "allocated"]):
        total = float(wallet_stats.get("total_balance_usd") or 0)
        allocated = sum(float(item.get("allocation") or 0) for item in allocations)
        return {
            "status": "success",
            "reply": (
                f"Allocation is the amount of USDC you attach to an agent. Right now you have {allocated:.2f} USDC allocated "
                f"out of about {total:.2f} USDC in the wallet."
            ),
            "suggestions": ["What is P&L?", "How does stop loss work?", "Show my copied agents"],
        }

    if any(token in normalized for token in ["what is mcp", "what does mcp mean", "mcp"]):
        return {
            "status": "success",
            "reply": (
                "MCP is the tool layer that lets me read your dashboard data and take actions like checking P&L, "
                "showing copied agents, or detaching from an agent."
            ),
            "suggestions": ["Who are you?", "What can you do?", "Show my copied agents"],
        }

    if any(token in normalized for token in ["what is attach", "what is detach", "attach detach", "attach / detach"]):
        return {
            "status": "success",
            "reply": (
                "Attach means connecting your wallet to an agent so its trades are mirrored. Detach means stopping the copy link."
            ),
            "suggestions": ["Show my copied agents", "How do allocations work?", "What is stop loss?"],
        }

    if any(token in normalized for token in ["how does proportional", "proportional execution", "mirror trades", "real-time mirroring"]):
        return {
            "status": "success",
            "reply": (
                "Proportional execution means the copied trade scales to your allocation. If the agent trades 1 unit, your wallet trades your share of that size."
            ),
            "suggestions": ["What is allocation?", "What is copy trading?", "What is P&L?"],
        }

    if any(token in normalized for token in ["what can you do", "who are you", "hello", "hi", "help"]):
        return {
            "status": "success",
            "reply": (
                "I’m the MCP Agent for Arc_Tic Whale. I can explain copy trading, check your wallet and P&L, list copied agents, "
                "show recent trades, and help you manage alerts or detach from agents."
            ),
            "suggestions": ["What is copy trading?", "Show my copied agents", "What is stop loss?"],
        }

    return None


def _context_suggestions(page: str | None, selected_agent: str | None, allocations: list[dict]) -> list[str]:
    page_key = _clean(page)
    agent_label = (selected_agent or "").replace("_", " ").strip() or "this agent"
    suggestions = ["What is copy trading?", "Show my copied agents", "What is P&L?"]

    if page_key == "home":
        suggestions = [
            f"Copy {agent_label}",
            f"What is {agent_label}?",
            "What is stop loss?",
        ]
    elif page_key == "market":
        suggestions = [
            f"Which agent is best this week?",
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
            "What is P&L?",
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


def handle_assistant_command(user_id: str, command: str, context: Optional[dict] = None) -> dict:
    user = get_user(user_id)
    if not user:
        return {
            "status": "error",
            "reply": "I couldn't load your account yet. Refresh once and I’ll sync your wallet.",
        }

    command_text = command.strip()
    normalized = _clean(command_text)
    context = context or {}
    wallet_stats = get_wallet_stats_safe(user.get("wallet_id"))
    preferences = get_user_preferences(user_id)
    allocations = _copied_agents(user_id)

    if not command_text:
        return {
            "status": "error",
            "reply": "Tell me what you want me to do, like 'What is my P&L?' or 'Detach me from Arc_Tic Whale'.",
        }

    if any(phrase in normalized for phrase in ["who are you", "what are you", "hello", "hi", "help", "what can you do"]):
        return {
            "status": "success",
            "reply": (
                "I’m the MCP Agent for Arc_Tic Whale. I can check your P&L, show copied agents, "
                "list recent trades, help you detach from an agent, and toggle alerts. "
                "Try: 'What is my P&L?' or 'Show my copied agents'."
            ),
            "suggestions": _context_suggestions(context.get("page"), context.get("selected_agent"), allocations),
        }

    qa = _assistant_reply_to_qa(command_text, normalized, wallet_stats, allocations)
    if qa:
        qa["suggestions"] = qa.get("suggestions") or _context_suggestions(context.get("page"), context.get("selected_agent"), allocations)
        return qa

    if "p&l" in normalized or "pnl" in normalized or "profit" in normalized or "balance" in normalized:
        pnl = wallet_stats.get("performance", {}).get("24h", "0.00%")
        return {
            "status": "success",
            "reply": (
                f"Your wallet is at {float(wallet_stats.get('total_balance_usd') or 0):.2f} USDC. "
                f"Recent P&L is {pnl}. "
                f"Copied agents: {len(allocations)}."
            ),
            "data": {
                "wallet": wallet_stats,
                "copied_agents": allocations,
            },
            "suggestions": _context_suggestions(context.get("page"), context.get("selected_agent"), allocations),
        }

    if "best" in normalized or "top agent" in normalized or "best this week" in normalized:
        best = _best_agent()
        metrics = best["metrics"]
        return {
            "status": "success",
            "reply": (
                f"{best['name']} looks strongest right now with a {metrics.get('win_rate', 0)}% win rate "
                f"across {metrics.get('total_trades', 0)} trades."
            ),
            "data": best,
            "suggestions": _context_suggestions(context.get("page"), context.get("selected_agent"), allocations),
        }

    if "copied" in normalized or "following" in normalized or "copying" in normalized:
        return {
            "status": "success",
            "reply": _format_agent_list(allocations),
            "data": {"copied_agents": allocations},
            "suggestions": _context_suggestions(context.get("page"), context.get("selected_agent"), allocations),
        }

    if "recent trade" in normalized or "trade history" in normalized or "what did i trade" in normalized:
        history = get_follower_trade_history(user["wallet_id"], limit=5, actions={"BUY", "SELL", "HOLD"})
        if not history:
            reply = "No trades yet for this wallet."
        else:
            reply = "\n".join(
                f"- {row['action']} {row.get('asset') or 'market'}: {row.get('reason') or 'No reason recorded.'}"
                for row in history
            )
        return {
            "status": "success",
            "reply": reply,
            "data": {"trades": history},
            "suggestions": _context_suggestions(context.get("page"), context.get("selected_agent"), allocations),
        }

    detach_target = None
    if any(word in normalized for word in ["detach", "stop copying", "remove"]):
        detach_target = _match_agent_name(command_text)
        if not detach_target:
            return {
                "status": "needs_input",
                "reply": "Which agent should I detach? Try using the agent name.",
                "suggestions": _context_suggestions(context.get("page"), context.get("selected_agent"), allocations),
            }
        detached = deactivate_follower(user_id, detach_target)
        if not detached:
            return {
                "status": "skipped",
                "reply": f"You're not actively copying {get_agent_profile(detach_target)['name']}.",
                "suggestions": _context_suggestions(context.get("page"), context.get("selected_agent"), allocations),
            }
        return {
            "status": "success",
            "reply": f"Detached you from {get_agent_profile(detach_target)['name']}.",
            "data": {"agent_id": detach_target},
            "suggestions": _context_suggestions(context.get("page"), context.get("selected_agent"), allocations),
        }

    alert_toggle = _extract_bool_toggle(command_text)
    if "daily summary" in normalized and alert_toggle is not None:
        set_user_preferences(user_id, daily_summary=alert_toggle)
        return {
            "status": "success",
            "reply": f"Daily summary is now {'on' if alert_toggle else 'off'}.",
            "data": {"daily_summary": alert_toggle},
            "suggestions": _context_suggestions(context.get("page"), context.get("selected_agent"), allocations),
        }

    if alert_toggle is not None and "alert" in normalized:
        set_user_preferences(user_id, trade_alerts=alert_toggle)
        return {
            "status": "success",
            "reply": f"Trade alerts are now {'on' if alert_toggle else 'off'}.",
            "data": {"trade_alerts": alert_toggle},
            "suggestions": _context_suggestions(context.get("page"), context.get("selected_agent"), allocations),
        }

    return {
        "status": "unsupported",
        "reply": (
            "I can answer P&L, best agent, copied agents, and recent trades, "
            "or help you detach from an agent and toggle alerts."
        ),
        "suggestions": _context_suggestions(context.get("page"), context.get("selected_agent"), allocations),
    }
