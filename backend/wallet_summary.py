from __future__ import annotations

from backend.database import get_user_allocations, get_user_by_wallet_id
from backend.logger import get_logger
from backend.market_data import get_current_market_state
from backend.wallet_manager import initialize_circle_client
from circle.web3.developer_controlled_wallets.api import WalletsApi

log = get_logger("wallet_summary")

# Canonical Arc Testnet contract addresses → symbol mapping.
# Circle sometimes returns multiple rows for the same symbol (native + wrapped).
# We use the contract address to identify the real token and ignore duplicates.
_ARC_CANONICAL: dict[str, str] = {
    "0x3600000000000000000000000000000000000000": "USDC",
    "0x4ccccd3220ac80c07a8b575a4cb494c0e77606ed": "WETH",
    "0xf0c4a4ce82a5746abaad9425360ab04fbba432bf": "WBTC",
    "0x89b50855aa3be2f677cd6303cec089b5f319d72a": "EURC",
}
_ARC_CHAIN = "ARC-TESTNET"


def _token_address(tb) -> str | None:
    """Try every known attribute path to get the contract address."""
    token = tb.token
    for attr in ("token_address", "address", "contract_address"):
        val = getattr(token, attr, None)
        if val:
            return str(val).lower()
    return None


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

            # Pass 1 — try to match by canonical contract address.
            # This avoids double-counting when Circle returns both a native
            # and a wrapped version of the same token on Arc Testnet.
            canonical: dict[str, dict] = {}   # symbol → entry
            unmatched: list = []               # rows we couldn't match by address

            for tb in balances_response.data.token_balances:
                chain_val = getattr(tb.token.blockchain, "value", str(tb.token.blockchain))
                if chain_val != _ARC_CHAIN:
                    continue

                addr = _token_address(tb)
                sym = None
                if addr:
                    sym = _ARC_CANONICAL.get(addr)

                if sym:
                    # Canonical match — always prefer this over an unmatched row
                    canonical[sym] = {
                        "symbol": sym,
                        "name": tb.token.name,
                        "amount": float(tb.amount),
                        "decimals": tb.token.decimals,
                    }
                    log.debug("Canonical token: addr=%s sym=%s amount=%s", addr, sym, tb.amount)
                else:
                    unmatched.append(tb)
                    log.debug("Unmatched token: addr=%s sym=%s amount=%s",
                              addr, getattr(tb.token, "symbol", "?"), tb.amount)

            # Pass 2 — for unmatched rows (address unknown / not in canonical list)
            # only add if we don't already have that symbol from a canonical row,
            # and if the amount is non-zero to avoid ghost entries.
            for tb in unmatched:
                sym = getattr(tb.token, "symbol", None) or "UNKNOWN"
                amount = float(tb.amount)
                if sym not in canonical and amount > 0:
                    canonical[sym] = {
                        "symbol": sym,
                        "name": getattr(tb.token, "name", sym),
                        "amount": amount,
                        "decimals": getattr(tb.token, "decimals", 6),
                    }

            # Build display list and compute USD total
            for sym, entry in canonical.items():
                amount = entry["amount"]
                if sym == "USDC":
                    total_balance_usd += amount
                else:
                    price = market_state.get(sym, {}).get("PRICE", 0.0) or 0.0
                    total_balance_usd += amount * price

            token_balances = [
                {**entry, "amount": str(round(entry["amount"], entry["decimals"]))}
                for entry in canonical.values()
                if entry["amount"] > 0   # hide zero-balance tokens
            ]

    except Exception as e:
        log.warning("Circle API error: %s", e)
        # Fallback: derive balance from on-chain allocation records
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

    return {"total_balance_usd": total_balance_usd, "token_balances": token_balances}
