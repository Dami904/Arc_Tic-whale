from decimal import Decimal, InvalidOperation

from backend.config import PARENT_WALLET_ID, USER_WALLET_SIGNUP_FUND_AMOUNT
from backend.logger import get_logger
from backend.trade_executor import execute_transfer

log = get_logger("wallet_funding")


def _signup_amount() -> Decimal:
    try:
        return Decimal(str(USER_WALLET_SIGNUP_FUND_AMOUNT or "0"))
    except (InvalidOperation, ValueError):
        log.warning("Invalid USER_WALLET_SIGNUP_FUND_AMOUNT=%s", USER_WALLET_SIGNUP_FUND_AMOUNT)
        return Decimal("0")


def fund_new_user_wallet(user_id: str, destination_address: str, asset: str = "USDC") -> str | None:
    amount = _signup_amount()
    if amount <= 0:
        return None
    if not PARENT_WALLET_ID:
        log.warning("Signup funding skipped for %s: PARENT_WALLET_ID is not configured", user_id)
        return None
    if not destination_address:
        log.warning("Signup funding skipped for %s: destination address missing", user_id)
        return None

    tx_id = execute_transfer(
        wallet_id=PARENT_WALLET_ID,
        destination_address=destination_address,
        asset_symbol=asset,
        amount=str(amount),
    )
    if tx_id:
        log.info("Funded new user wallet for %s with %s %s. Tx: %s", user_id, amount, asset, tx_id)
    else:
        log.warning("Signup funding failed for %s", user_id)
    return tx_id
