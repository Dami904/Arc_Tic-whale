from backend.database import (
    adjust_follower_remaining_capital,
    deactivate_follower,
    get_active_follower_rows,
    get_active_followers,
    get_follower_by_wallet_id,
    get_latest_follower_trade,
    get_user,
    get_user_allocations,
    log_trade,
    follower_trade_agent_key,
)
from backend.trade_executor import execute_trade
from backend.market_data import get_current_market_state
from backend.notifications import notify_trade_alert, build_notification_reminder
from backend.agents import get_agent_profile
from backend.logger import get_logger

log = get_logger("copy_engine")


def _parse_pct(value: object) -> float:
    try:
        return float(str(value or "0").replace("%", "").replace("+", "").strip())
    except ValueError:
        return 0.0


ENTRY_FRACTION = 0.10  # every BUY deploys 10% of the follower's remaining capital
_MIN_VIABLE_TRADE = 0.5  # matches trade_executor's MIN_TRADE_AMOUNT floor


def _entry_notional(remaining_capital: float) -> float:
    """
    USDC to deploy on a BUY: 10% of what's left in the follower's pool.
    Sizes naturally shrink in drawdown and grow as wins compound. Below the
    executor's $0.50 floor we deploy the floor if the pool can cover it,
    else 0 (skip the trade rather than overdraw).
    """
    amount = round(remaining_capital * ENTRY_FRACTION, 2)
    if amount >= _MIN_VIABLE_TRADE:
        return amount
    return _MIN_VIABLE_TRADE if remaining_capital >= _MIN_VIABLE_TRADE else 0.0


def _exit_notional(latest_trade: dict | None, current_price: float | None) -> float:
    """
    USDC notional that sells the WHOLE position: the deployed amount marked to
    the current price (deployed * current/entry), since the executor converts
    a USDC notional to asset units at the current price. Without a recorded
    entry price (legacy rows), fall back to the deployed amount unadjusted.
    """
    trade = latest_trade or {}
    deployed = float(trade.get("amount_usdc") or 0.0)
    if not deployed:
        return 0.0
    entry_price = float(trade.get("price") or 0.0)
    if entry_price > 0 and current_price and current_price > 0:
        return round(deployed * (current_price / entry_price), 2)
    return deployed


def _maybe_stop_out_follower(agent_name: str, follower_row: dict, market_state: dict) -> bool:
    stop_loss_pct = _parse_pct(follower_row.get("stop_loss_pct"))
    if stop_loss_pct <= 0:
        return False

    latest_trade = get_latest_follower_trade(follower_row["user_wallet_id"], agent_name)
    if not latest_trade or str(latest_trade.get("action") or "").upper() != "BUY":
        return False

    held_asset = str(latest_trade.get("asset") or "").upper()
    if not held_asset or held_asset not in market_state:
        return False

    change = _parse_pct(market_state.get(held_asset, {}).get("24H_CHANGE"))
    if change > -abs(stop_loss_pct):
        return False

    follower_wallet_id = follower_row.get("user_wallet_id")
    follower_wallet_address = follower_row.get("user_wallet_address")
    if not follower_wallet_id or not follower_wallet_address:
        return False

    log.warning(
        "Stop loss triggered for %s on %s: %s%% <= -%s%%",
        follower_wallet_id,
        held_asset,
        change,
        stop_loss_pct,
    )

    current_price = market_state.get(held_asset, {}).get("PRICE")
    sell_amount = _exit_notional(latest_trade, current_price)
    if sell_amount <= 0:
        log.error("Stop loss for %s: no recorded position size to sell.", follower_wallet_id)
        return False

    tx_id = execute_trade(
        wallet_id=follower_wallet_id,
        action="SELL",
        target_asset_symbol=held_asset,
        amount=str(sell_amount),
        recipient_address=follower_wallet_address,
        current_price=current_price,
    )
    if not tx_id:
        log.error("Stop loss sell failed for %s", follower_wallet_id)
        return False

    log_trade(
        agent=follower_trade_agent_key(follower_wallet_id, agent_name),
        action="SELL",
        asset=held_asset,
        tx_id=tx_id,
        reason=f"Stop loss triggered at {change:.2f}% with threshold -{stop_loss_pct:.2f}%",
        price=current_price,
        amount_usdc=sell_amount,
    )
    adjust_follower_remaining_capital(follower_wallet_id, agent_name, sell_amount)

    follower_user_id = follower_row.get("user_id")
    if follower_user_id:
        deactivate_follower(follower_user_id, agent_name)
        user = get_user(follower_user_id)
        if user and int(user.get("trade_alerts") or 0) == 1:
            notify_trade_alert(
                user,
                agent_name=get_agent_profile(agent_name)["name"],
                action="SELL",
                token=held_asset,
                amount_usdc=sell_amount,
                entry_price=market_state.get(held_asset, {}).get("PRICE", 0),
            )

    return True


def evaluate_stop_losses(agent_name: str):
    market_state = get_current_market_state()
    stopped = []
    for follower_row in get_active_follower_rows(agent_name):
        if _maybe_stop_out_follower(agent_name, follower_row, market_state):
            stopped.append(follower_row.get("user_wallet_id"))
    return stopped


def mirror_agent_trade(agent_name, action, target_asset_symbol):
    log.info("Scanning for users copying %s...", agent_name)

    evaluate_stop_losses(agent_name)
    followers = get_active_followers(agent_name)
    market_state = get_current_market_state()
    entry_price = market_state.get(target_asset_symbol, {}).get("PRICE", 0)
    agent_label = get_agent_profile(agent_name)["name"]

    if not followers:
        log.info("No active followers found for %s. Moving on.", agent_name)
        return

    log.info("Found %d active follower(s). Mirroring %s %s order...", len(followers), action, target_asset_symbol)

    for follower_item in followers:
        follower_wallet_id = follower_item[0]
        follower_wallet_address = follower_item[1]
        remaining_capital = float(follower_item[4] if len(follower_item) > 4 else follower_item[2] or 0.0)
        if not follower_wallet_address:
            log.error("Mirror trade skipped for %s: missing wallet address.", follower_wallet_id)
            continue

        # BUY deploys 10% of the follower's remaining capital; SELL closes the
        # whole position at its marked value (recorded notional * price ratio).
        if action == "BUY":
            trade_amount = _entry_notional(remaining_capital)
        else:
            latest_trade = get_latest_follower_trade(follower_wallet_id, agent_name)
            trade_amount = _exit_notional(latest_trade, entry_price)

        if trade_amount <= 0:
            log.warning("Mirror %s skipped for %s: nothing to trade (remaining %.2f USDC).",
                        action, follower_wallet_id, remaining_capital)
            continue

        log.info("Mirroring %s for wallet %s with amount: %s USDC (remaining pool %s)",
                 action, follower_wallet_id, trade_amount, remaining_capital)

        tx_id = execute_trade(
            wallet_id=follower_wallet_id,
            action=action,
            target_asset_symbol=target_asset_symbol,
            amount=str(trade_amount),
            recipient_address=follower_wallet_address,
            current_price=entry_price,
        )

        if tx_id:
            log.info("Mirror trade successful for %s! Tx: %s", follower_wallet_id, tx_id)
            log_trade(
                agent=follower_trade_agent_key(follower_wallet_id, agent_name),
                action=action,
                asset=target_asset_symbol,
                tx_id=tx_id,
                reason=f"Mirrored {agent_name} trade",
                price=entry_price,
                amount_usdc=trade_amount,
            )
            adjust_follower_remaining_capital(
                follower_wallet_id, agent_name,
                -trade_amount if action == "BUY" else trade_amount,
            )
            follower_row = get_follower_by_wallet_id(agent_name, follower_wallet_id) or {}
            user = get_user(follower_row.get("user_id")) if follower_row.get("user_id") else None
            if user and int(user.get("trade_alerts") or 0) == 1:
                result = notify_trade_alert(
                    user,
                    agent_name=agent_label,
                    action=action,
                    token=target_asset_symbol,
                    amount_usdc=trade_amount,
                    entry_price=entry_price,
                )
                if not (result.get("email") or result.get("telegram")):
                    reminder = build_notification_reminder(user)
                    if reminder:
                        log.info("Notification reminder for %s: %s", follower_wallet_id, reminder)
        else:
            log.error("Mirror trade failed for %s", follower_wallet_id)


def exit_all_positions(user_id: str) -> list[dict]:
    """
    User-initiated emergency exit: sells back to USDC whatever asset the user
    currently holds across every agent they follow (determined by each
    follow's most recent mirrored trade). Does NOT unfollow the agent - the
    user keeps following, they just exit their current holding. A follow
    with no open position (last action was SELL, or no trades yet) is
    skipped, not an error.
    """
    market_state = get_current_market_state()
    results: list[dict] = []

    for row in get_user_allocations(user_id):
        agent_name = row["target_agent"]
        wallet_id = row.get("user_wallet_id")
        wallet_address = row.get("user_wallet_address")
        if not wallet_id or not wallet_address:
            continue

        latest_trade = get_latest_follower_trade(wallet_id, agent_name)
        if not latest_trade or str(latest_trade.get("action") or "").upper() != "BUY":
            continue  # already in USDC, or never traded - nothing to exit

        held_asset = str(latest_trade.get("asset") or "").upper()
        if not held_asset:
            continue

        current_price = market_state.get(held_asset, {}).get("PRICE")
        sell_amount = _exit_notional(latest_trade, current_price)
        if sell_amount <= 0:
            log.error("Exit-all for %s on %s: no recorded position size to sell.", wallet_id, agent_name)
            results.append({"agent": agent_name, "asset": held_asset, "status": "error"})
            continue

        tx_id = execute_trade(
            wallet_id=wallet_id,
            action="SELL",
            target_asset_symbol=held_asset,
            amount=str(sell_amount),
            recipient_address=wallet_address,
            current_price=current_price,
        )

        if not tx_id:
            log.error("Exit-all sell failed for %s on %s", wallet_id, agent_name)
            results.append({"agent": agent_name, "asset": held_asset, "status": "error"})
            continue

        log_trade(
            agent=follower_trade_agent_key(wallet_id, agent_name),
            action="SELL",
            asset=held_asset,
            tx_id=tx_id,
            reason="User-initiated exit-all",
            price=current_price,
            amount_usdc=sell_amount,
        )
        adjust_follower_remaining_capital(wallet_id, agent_name, sell_amount)
        results.append({"agent": agent_name, "asset": held_asset, "status": "success", "tx_id": tx_id})

        user = get_user(user_id)
        if user and int(user.get("trade_alerts") or 0) == 1:
            notify_trade_alert(
                user,
                agent_name=get_agent_profile(agent_name)["name"],
                action="SELL",
                token=held_asset,
                amount_usdc=sell_amount,
                entry_price=market_state.get(held_asset, {}).get("PRICE", 0),
            )

    return results
