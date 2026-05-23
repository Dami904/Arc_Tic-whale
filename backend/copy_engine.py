from backend.database import get_active_followers, log_trade
from backend.trade_executor import execute_trade
from backend.logger import get_logger

log = get_logger("copy_engine")

def mirror_agent_trade(agent_name, action, target_asset_symbol):
    log.info("Scanning for users copying %s...", agent_name)

    followers = get_active_followers(agent_name)

    if not followers:
        log.info("No active followers found for %s. Moving on.", agent_name)
        return

    log.info("Found %d active follower(s). Mirroring %s %s order...", len(followers), action, target_asset_symbol)

    for follower_wallet_id, follower_wallet_address, allocation in followers:
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
                agent=f"Follower:{follower_wallet_id}",
                action=action,
                asset=target_asset_symbol,
                tx_id=tx_id,
                reason=f"Mirrored {agent_name} trade",
            )
        else:
            log.error("Mirror trade failed for %s", follower_wallet_id)
