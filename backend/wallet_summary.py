from __future__ import annotations

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
            market_state = get_current_market_state()

            # Only show Arc Testnet tokens, then aggregate by symbol.
            # Circle returns one row per contract address, so same-symbol tokens
            # (e.g. native USDC + wrapped USDC) are summed into a single display row.
            ARC_CHAIN = "ARC-TESTNET"
            aggregated: dict[str, dict] = {}
            for tb in balances_response.data.token_balances:
                chain_val = getattr(tb.token.blockchain, "value", str(tb.token.blockchain))
                if chain_val != ARC_CHAIN:
                    continue          # skip tokens from other chains
                sym = tb.token.symbol
                amount = float(tb.amount)
                if sym in aggregated:
                    aggregated[sym]["amount"] += amount
                else:
                    aggregated[sym] = {
                        "symbol": sym,
                        "name": tb.token.name,
                        "amount": amount,
                        "decimals": tb.token.decimals,
                    }

            # Build the display list and compute USD total
            for sym, entry in aggregated.items():
                amount = entry["amount"]
                if sym == "USDC":
                    total_balance_usd += amount
                else:
                    price = market_state.get(sym, {}).get("PRICE", 0.0) or 0.0
                    total_balance_usd += amount * price

            token_balances = [
                {**entry, "amount": str(round(entry["amount"], entry["decimals"]))}
                for entry in aggregated.values()
            ]
    except Exception as e:
        log.warning("Circle API error", error=str(e))
        # Fall back to allocation-based balance when Circle API is unavailable
        user_record = get_user_by_wallet_id(target_wallet_id) if target_wallet_id else None
        allocations = get_user_allocations(user_record["user_id"]) if user_record else []
        total_allocated = sum(float(row.get("allocation_amount") or 0) for row in allocations)
        total_balance_usd = float(total_allocated)
        if total_balance_usd:
            token_balances = [{
                "symbol": "USDC",
                "name": "USD Coin",
                "amount": str(round(total_balance_usd, 2)),
                "decimals": 6,
            }]

    eth_performance_data = get_current_market_state().get("ETH", {})
    performance = {
        "24h": eth_performance_data.get("24H_CHANGE", "0.00%"),
        "7d": eth_performance_data.get("7D_CHANGE", "0.00%"),
        "1y": eth_performance_data.get("1Y_CHANGE", "0.00%"),
    }

    return {"total_balance_usd": total_balance_usd, "token_balances": token_balances, "performance": performance}
