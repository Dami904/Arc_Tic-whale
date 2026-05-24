from backend.database import (
    deactivate_follower,
    get_active_follower_rows,
    get_active_followers,
    get_follower_by_wallet_id,
    get_latest_follower_trade,
    get_user,
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

    allocation = float(follower_row.get("allocation_amount") or 0.0)
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

    tx_id = execute_trade(
        wallet_id=follower_wallet_id,
        action="SELL",
        target_asset_symbol=held_asset,
        amount=str(allocation),
        recipient_address=follower_wallet_address,
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
    )

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
                amount_usdc=allocation,
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
        allocation = follower_item[2]
        log.info("Mirroring trade for wallet %s with amount: %s USDC", follower_wallet_id, allocation)
        if not follower_wallet_address:
            log.error("Mirror trade skipped for %s: missing wallet address.", follower_wallet_id)
            continue

        tx_id = execute_trade(
            wallet_id=follower_wallet_id,
            action=action,
            target_asset_symbol=target_asset_symbol,
            amount=str(allocation),
            recipient_address=follower_wallet_address,
        )

        if tx_id:
            log.info("Mirror trade successful for %s! Tx: %s", follower_wallet_id, tx_id)
            log_trade(
                agent=follower_trade_agent_key(follower_wallet_id, agent_name),
                action=action,
                asset=target_asset_symbol,
                tx_id=tx_id,
                reason=f"Mirrored {agent_name} trade",
            )
            follower_row = get_follower_by_wallet_id(agent_name, follower_wallet_id) or {}
            user = get_user(follower_row.get("user_id")) if follower_row.get("user_id") else None
            if user and int(user.get("trade_alerts") or 0) == 1:
                result = notify_trade_alert(
                    user,
                    agent_name=agent_label,
                    action=action,
                    token=target_asset_symbol,
                    amount_usdc=allocation,
                    entry_price=entry_price,
                )
                if not (result.get("email") or result.get("telegram")):
                    reminder = build_notification_reminder(user)
                    if reminder:
                        log.info("Notification reminder for %s: %s", follower_wallet_id, reminder)
        else:
            log.error("Mirror trade failed for %s", follower_wallet_id)
