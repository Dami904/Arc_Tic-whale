from __future__ import annotations

from backend.config import TRADE_DRY_RUN
from backend.database import get_user_allocations, get_user_by_wallet_id
from backend.logger import get_logger
from backend.market_data import get_current_market_state
from backend.wallet_manager import initialize_circle_client
from circle.web3.developer_controlled_wallets.api import WalletsApi

log = get_logger("wallet_summary")


def get_wallet_stats_safe(wallet_id: str | None) -> dict:
    token_balances = []
    total_balance_usd = 0.0
    target_wallet_id = wallet_id

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
                    "decimals": tb.token.decimals,
                }
                for tb in balances_response.data.token_balances
            ]
            for tb in balances_response.data.token_balances:
                if tb.token.symbol == "USDC":
                    total_balance_usd += float(tb.amount)
    except Exception as e:
        log.warning("Circle API error", error=str(e))
        if TRADE_DRY_RUN:
            user_record = get_user_by_wallet_id(target_wallet_id) if target_wallet_id else None
            allocations = get_user_allocations(user_record["user_id"]) if user_record else []
            total_allocated = sum(float(row.get("allocation_amount") or 0) for row in allocations)
            total_balance_usd = float(total_allocated)
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
        "1y": eth_performance_data.get("1Y_CHANGE", "0.00%"),
    }

    return {"total_balance_usd": total_balance_usd, "token_balances": token_balances, "performance": performance}
