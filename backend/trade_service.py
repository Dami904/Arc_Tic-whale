# trade_service.py — Upgrade 2: Shared trade-cycle service used by both CLI (main.py) and API (api.py)
"""
Single source of truth for the full AI → trade → copy → social pipeline.
Both main.py and api.py import and call run_trade_cycle() so the logic
never drifts between the two entry-points.
"""
import time
from typing import Optional

from backend.logger import get_logger
from backend.market_data import get_current_market_state
from backend.agents import ask_conservative_whale
from backend.utils import parse_ai_decision
from backend.trade_executor import execute_trade
from backend.copy_engine import mirror_agent_trade
from backend.social import generate_canteen_post
from backend.database import init_db, is_kill_switch_active, log_trade
from backend.config import AGENT_WALLET_ID

log = get_logger("trade_service")

AGENT_NAME = "Conservative_Whale"


def run_trade_cycle(
    wallet_id: Optional[str] = None,
    rate_limit_sleep: int = 5,
) -> dict:
    """
    Execute one full AI trading cycle.

    Returns a dict with keys:
        status   : "success" | "hold" | "error"
        action   : "BUY" | "SELL" | "HOLD"
        asset    : str | None
        tx_hash  : str | None
        reason   : str
    """
    wid = wallet_id or AGENT_WALLET_ID
    if not wid:
        log.error("AGENT_WALLET_ID is not set. Aborting trade cycle.")
        return {"status": "error", "action": None, "asset": None, "tx_hash": None,
                "reason": "AGENT_WALLET_ID missing"}

    init_db()

    if is_kill_switch_active():
        log.warning("Kill switch is active. Aborting trade cycle.")
        return {"status": "error", "action": None, "asset": None, "tx_hash": None,
                "reason": "Kill switch active"}

    # ── 1. Market data ──────────────────────────────────────────────────────
    log.info("Fetching latest market data...")
    current_data = get_current_market_state()

    # ── 2. AI decision ──────────────────────────────────────────────────────
    log.info("Passing data to The Conservative Whale for analysis...")
    raw_ai_response = ask_conservative_whale(current_data)
    parsed = parse_ai_decision(raw_ai_response)

    action = parsed["decision"]
    asset  = parsed["asset"]
    reason = parsed.get("reason", "")

    log.info("Whale decision: %s %s — %s", action, asset or "", reason)

    if action == "HOLD":
        log.info("Action: HOLD. No on-chain transaction required.")
        log_trade(agent=AGENT_NAME, action="HOLD", asset=asset, tx_id=None, reason=reason)
        return {"status": "hold", "action": "HOLD", "asset": asset, "tx_hash": None, "reason": reason}

    # ── 3. Execute agent trade ───────────────────────────────────────────────
    log.info("Executing %s %s order on Circle infrastructure...", action, asset or "")
    agent_tx = execute_trade(wallet_id=wid, action=action, target_asset_symbol=asset)

    if not agent_tx:
        log.error("Blockchain execution failed for %s %s.", action, asset)
        return {"status": "error", "action": action, "asset": asset, "tx_hash": None,
                "reason": "Blockchain execution failed"}

    # ── 4. Mirror to followers ───────────────────────────────────────────────
    mirror_agent_trade(agent_name=AGENT_NAME, action=action, target_asset_symbol=asset)

    # ── 5. Rate-limit cooldown ───────────────────────────────────────────────
    if rate_limit_sleep > 0:
        log.info("Pausing %ds to respect API rate limits...", rate_limit_sleep)
        time.sleep(rate_limit_sleep)

    # ── 6. Social broadcast ──────────────────────────────────────────────────
    post = generate_canteen_post(AGENT_NAME, action, agent_tx, reason=reason)
    log.info("Social post: %s", post)

    # ── 7. Persist to history ────────────────────────────────────────────────
    log_trade(agent=AGENT_NAME, action=action, asset=asset, tx_id=agent_tx, reason=reason)

    return {
        "status": "success",
        "action": action,
        "asset": asset,
        "tx_hash": agent_tx,
        "reason": reason,
    }
