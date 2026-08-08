import os
import time
import uuid
from decimal import Decimal, InvalidOperation

from circle.web3 import developer_controlled_wallets

from backend.config import (
    AGENT_WALLET_ADDRESS,
    EURC_ARC_ADDRESS,
    TRADE_DRY_RUN,
    UNISWAP_ROUTER_ARC_ADDRESS,
    USDC_ARC_ADDRESS,
    WBTC_ARC_ADDRESS,
    WETH_ARC_ADDRESS,
)
from backend.wallet_manager import initialize_circle_client
from backend.logger import get_logger

log = get_logger("trade_executor")

ASSET_CONTRACT_ADDRESSES = {
    "USDC": USDC_ARC_ADDRESS,
    "ETH":  WETH_ARC_ADDRESS,
    "BTC":  WBTC_ARC_ADDRESS,
    "EURC": EURC_ARC_ADDRESS,
}

ASSET_DECIMALS = {
    "USDC": 6,
    "ETH":  18,
    "BTC":  8,
    "EURC": 6,
}

UNISWAP_V3_POOL_FEE = 3000
DEFAULT_SLIPPAGE_BPS = 100
BASIS_POINTS = 10_000
TRANSACTION_CONFIRM_TIMEOUT_SECONDS = int(os.getenv("TRANSACTION_CONFIRM_TIMEOUT_SECONDS", "90"))
TRANSACTION_CONFIRM_POLL_SECONDS = float(os.getenv("TRANSACTION_CONFIRM_POLL_SECONDS", "3"))
TRANSACTION_SUCCESS_STATES = {"CONFIRMED", "COMPLETE"}
TRANSACTION_FAILURE_STATES = {"CANCELLED", "DENIED", "FAILED", "STUCK"}
MIN_TRADE_AMOUNT = Decimal("0.5")
# Safety ceiling per trade, in USDC notional. Overridable so follower
# allocations aren't silently capped at the old $2 testnet rail - a follower
# allocating $20 now actually deploys their agent's trade_size_pct of it.
MAX_TRADE_AMOUNT = Decimal(os.getenv("MAX_TRADE_NOTIONAL", "100"))

UNISWAP_ROUTER_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "tokenIn", "type": "address"},
                    {"internalType": "address", "name": "tokenOut", "type": "address"},
                    {"internalType": "uint24", "name": "fee", "type": "uint24"},
                    {"internalType": "address", "name": "recipient", "type": "address"},
                    {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
                    {"internalType": "uint256", "name": "amountOutMinimum", "type": "uint256"},
                    {"internalType": "uint160", "name": "sqrtPriceLimitX96", "type": "uint160"},
                ],
                "internalType": "struct ISwapRouter.ExactInputSingleParams",
                "name": "params",
                "type": "tuple",
            }
        ],
        "name": "exactInputSingle",
        "outputs": [{"internalType": "uint256", "name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    }
]

ERC20_APPROVE_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    }
]

ERC20_TRANSFER_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "to", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    }
]


def _normalize_amount(amount: str) -> Decimal:
    try:
        requested = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        log.warning("Invalid amount provided: %s. Defaulting to 1.0 USDC.", amount)
        requested = Decimal("1.0")

    final_amount = max(MIN_TRADE_AMOUNT, min(MAX_TRADE_AMOUNT, requested))
    if requested != final_amount:
        log.info("Limit enforced: adjusted %s to %s USDC.", requested, final_amount)
    return final_amount


def _to_token_units(amount: Decimal, decimals: int) -> int:
    return int(amount * (Decimal(10) ** decimals))


def _expected_output_amount(
    action: str,
    target_asset_symbol: str,
    amount_in: Decimal,
    current_price: float | Decimal,
) -> Decimal:
    price = Decimal(str(current_price))
    if price <= 0:
        raise ValueError("current_price must be positive")

    if action == "BUY":
        # USDC in -> target asset out.
        return amount_in / price
    if action == "SELL":
        # Target asset in -> USDC out.
        return amount_in * price
    raise ValueError(f"Unsupported swap action: {action}")


def _minimum_output_units(
    action: str,
    target_asset_symbol: str,
    amount_in: Decimal,
    current_price: float | Decimal,
    slippage_bps: int = DEFAULT_SLIPPAGE_BPS,
) -> int:
    output_symbol = target_asset_symbol if action == "BUY" else "USDC"
    expected_output = _expected_output_amount(action, target_asset_symbol, amount_in, current_price)
    min_output = expected_output * Decimal(BASIS_POINTS - slippage_bps) / Decimal(BASIS_POINTS)
    return _to_token_units(min_output, ASSET_DECIMALS[output_symbol])


def _build_swap_calldata(
    action: str,
    target_asset_symbol: str,
    amount: Decimal,
    recipient_address: str,
    current_price: float | Decimal,
) -> str:
    try:
        from web3 import Web3
    except ImportError as exc:
        raise RuntimeError("Live swap execution requires the web3 package. Install it or keep TRADE_DRY_RUN=true.") from exc

    w3 = Web3()
    router = w3.eth.contract(
        address=Web3.to_checksum_address(UNISWAP_ROUTER_ARC_ADDRESS),
        abi=UNISWAP_ROUTER_ABI,
    )

    target_address = Web3.to_checksum_address(ASSET_CONTRACT_ADDRESSES[target_asset_symbol])
    usdc_address = Web3.to_checksum_address(USDC_ARC_ADDRESS)
    recipient = Web3.to_checksum_address(recipient_address)

    if action == "BUY":
        token_in = usdc_address
        token_out = target_address
        amount_in = _to_token_units(amount, ASSET_DECIMALS["USDC"])
    else:
        token_in = target_address
        token_out = usdc_address
        amount_in = _to_token_units(amount, ASSET_DECIMALS[target_asset_symbol])

    amount_out_minimum = _minimum_output_units(action, target_asset_symbol, amount, current_price)
    params = {
        "tokenIn": token_in,
        "tokenOut": token_out,
        "fee": UNISWAP_V3_POOL_FEE,
        "recipient": recipient,
        "amountIn": amount_in,
        "amountOutMinimum": amount_out_minimum,
        "sqrtPriceLimitX96": 0,
    }
    return router.functions.exactInputSingle(params)._encode_transaction_data()


def _build_approval_calldata(token_symbol: str, amount: Decimal) -> tuple[str, str]:
    from web3 import Web3

    token_address = Web3.to_checksum_address(ASSET_CONTRACT_ADDRESSES[token_symbol])
    router_address = Web3.to_checksum_address(UNISWAP_ROUTER_ARC_ADDRESS)
    token = Web3().eth.contract(address=token_address, abi=ERC20_APPROVE_ABI)
    token_units = _to_token_units(amount, ASSET_DECIMALS[token_symbol])
    return token_address, token.functions.approve(router_address, token_units)._encode_transaction_data()


def _build_transfer_calldata(token_symbol: str, amount: Decimal, destination_address: str) -> tuple[str, str]:
    from web3 import Web3

    token_address = Web3.to_checksum_address(ASSET_CONTRACT_ADDRESSES[token_symbol])
    destination = Web3.to_checksum_address(destination_address)
    token = Web3().eth.contract(address=token_address, abi=ERC20_TRANSFER_ABI)
    token_units = _to_token_units(amount, ASSET_DECIMALS[token_symbol])
    return token_address, token.functions.transfer(destination, token_units)._encode_transaction_data()


def _extract_transaction_id(response) -> str:
    data = getattr(response, "data", None)
    if data is None:
        return "unknown"

    for attr in ("transaction", "transaction_id", "id"):
        value = getattr(data, attr, None)
        if value is None:
            continue
        if hasattr(value, "to_dict"):
            tx_dict = value.to_dict()
            return tx_dict.get("id") or tx_dict.get("transactionId") or "unknown"
        return str(value)

    if hasattr(data, "to_dict"):
        data_dict = data.to_dict()
        return data_dict.get("id") or data_dict.get("transactionId") or "unknown"

    return "unknown"


def _extract_transaction_state(response) -> str | None:
    data = getattr(response, "data", None)
    transaction = getattr(data, "transaction", None) if data is not None else None
    if transaction is None and data is not None and hasattr(data, "to_dict"):
        transaction = data.to_dict().get("transaction")
    if transaction is None:
        return None

    state = getattr(transaction, "state", None)
    if state is None and isinstance(transaction, dict):
        state = transaction.get("state")
    if hasattr(state, "value"):
        state = state.value
    return str(state).upper() if state else None


def _submit_contract_execution(transactions_api, wallet_id: str, contract_address: str, call_data: str, ref_id: str) -> str:
    request = developer_controlled_wallets.CreateContractExecutionTransactionForDeveloperRequest.from_dict(
        {
            "idempotencyKey": str(uuid.uuid4()),
            "walletId": wallet_id,
            "contractAddress": contract_address,
            "callData": call_data,
            "feeLevel": "MEDIUM",
            "refId": ref_id,
        }
    )
    response = transactions_api.create_developer_transaction_contract_execution(request)
    return _extract_transaction_id(response)


def _wait_for_transaction_success(
    transactions_api,
    tx_id: str,
    timeout_seconds: int = TRANSACTION_CONFIRM_TIMEOUT_SECONDS,
    poll_seconds: float = TRANSACTION_CONFIRM_POLL_SECONDS,
) -> bool:
    if not tx_id or tx_id == "unknown":
        log.error("Cannot confirm transaction without a Circle transaction id: %s", tx_id)
        return False

    deadline = time.monotonic() + max(0, timeout_seconds)
    while True:
        try:
            response = transactions_api.get_transaction(tx_id)
            state = _extract_transaction_state(response)
        except Exception as exc:
            log.warning("Transaction confirmation lookup failed for %s: %s", tx_id, exc)
            state = None

        if state in TRANSACTION_SUCCESS_STATES:
            log.info("Transaction %s confirmed with state %s.", tx_id, state)
            return True
        if state in TRANSACTION_FAILURE_STATES:
            log.error("Transaction %s reached failure state %s.", tx_id, state)
            return False

        if time.monotonic() >= deadline:
            log.error("Transaction %s did not confirm before timeout; last state=%s.", tx_id, state or "unknown")
            return False
        time.sleep(poll_seconds)


def execute_trade(
    wallet_id: str,
    action: str,
    target_asset_symbol: str,
    amount: str = "1.0",
    recipient_address: str | None = None,
    current_price: float | None = None,
) -> str | None:
    """
    amount is always a USDC notional (e.g. "1.0" = $1 worth), matching what
    every caller actually passes (a follower's sized deployment, or the
    agent's own ~$1 signal trade). For a BUY that's already the unit Uniswap
    needs (spend $X of USDC). For a SELL, current_price (the asset's USDC
    price) is required to convert that same $X notional into the asset's
    own native units - without it, "amount" would be misread as raw units
    of the asset (e.g. "sell 1.0" meaning 1.0 whole BTC instead of $1 of
    BTC), so a SELL with no price is refused rather than guessed at.
    Live BUYs also require current_price so Uniswap's amountOutMinimum can
    be computed in the output token's decimals instead of the input token's.
    """
    action = (action or "").upper().strip()
    target_asset_symbol = (target_asset_symbol or "").upper().strip()

    if action not in {"BUY", "SELL"}:
        log.error("Invalid action: %s. Expected BUY or SELL.", action)
        return None

    if target_asset_symbol not in ASSET_CONTRACT_ADDRESSES or target_asset_symbol == "USDC":
        log.error("Invalid or unsupported target asset: %s.", target_asset_symbol)
        return None

    if not wallet_id:
        log.error("Missing Circle wallet_id.")
        return None

    final_amount = _normalize_amount(amount)  # USDC notional, clamped to MIN/MAX_TRADE_AMOUNT
    recipient = recipient_address or AGENT_WALLET_ADDRESS
    if not recipient:
        log.error("Missing recipient wallet address.")
        return None

    if action == "SELL":
        if not current_price or current_price <= 0:
            log.error(
                "Missing current_price for SELL %s - refusing to guess the USDC-to-asset-units conversion.",
                target_asset_symbol,
            )
            return None
        swap_amount = final_amount / Decimal(str(current_price))
    else:
        if not TRADE_DRY_RUN and (not current_price or current_price <= 0):
            log.error(
                "Missing current_price for BUY %s - refusing to build an unsafe slippage limit.",
                target_asset_symbol,
            )
            return None
        swap_amount = final_amount

    log.info(
        "Preparing %s %s for wallet %s: %s USDC notional (%s %s).",
        action, target_asset_symbol, wallet_id, final_amount, swap_amount,
        "USDC" if action == "BUY" else target_asset_symbol,
    )

    if TRADE_DRY_RUN:
        dry_run_id = f"dryrun-{uuid.uuid4()}"
        log.info("DRY RUN: simulated %s %s. Tx: %s", action, target_asset_symbol, dry_run_id)
        return dry_run_id

    try:
        client = initialize_circle_client()
        transactions_api = developer_controlled_wallets.TransactionsApi(client)

        token_to_approve = "USDC" if action == "BUY" else target_asset_symbol
        approval_contract, approval_call_data = _build_approval_calldata(token_to_approve, swap_amount)
        approval_tx_id = _submit_contract_execution(
            transactions_api,
            wallet_id,
            approval_contract,
            approval_call_data,
            f"approve-{token_to_approve}-for-{action.lower()}",
        )
        log.info("Approval submitted. Tx: %s", approval_tx_id)
        if not _wait_for_transaction_success(transactions_api, approval_tx_id):
            log.error("Approval did not confirm for %s %s. Aborting swap.", action, target_asset_symbol)
            return None

        call_data = _build_swap_calldata(action, target_asset_symbol, swap_amount, recipient, current_price)
        tx_id = _submit_contract_execution(
            transactions_api,
            wallet_id,
            UNISWAP_ROUTER_ARC_ADDRESS,
            call_data,
            f"{action.lower()}-{target_asset_symbol.lower()}-swap",
        )

        if not _wait_for_transaction_success(transactions_api, tx_id):
            log.error("%s %s swap did not confirm. Tx: %s", action, target_asset_symbol, tx_id)
            return None

        log.info("SUCCESS: %s %s confirmed. Tx: %s", action, target_asset_symbol, tx_id)
        return tx_id
    except Exception as exc:
        log.error("Transaction failed", error=str(exc))
        return None


def execute_transfer(
    wallet_id: str,
    destination_address: str,
    asset_symbol: str = "USDC",
    amount: str = "0",
) -> str | None:
    asset_symbol = (asset_symbol or "").upper().strip()

    if asset_symbol not in ASSET_CONTRACT_ADDRESSES:
        log.error("Invalid or unsupported transfer asset: %s.", asset_symbol)
        return None

    if not wallet_id:
        log.error("Missing Circle wallet_id for transfer.")
        return None

    if not destination_address:
        log.error("Missing transfer destination address.")
        return None

    try:
        transfer_amount = Decimal(str(amount))
    except (InvalidOperation, ValueError):
        log.error("Invalid transfer amount: %s.", amount)
        return None

    if transfer_amount <= 0:
        log.error("Transfer amount must be positive: %s.", amount)
        return None

    log.info("Preparing %s %s transfer from wallet %s.", transfer_amount, asset_symbol, wallet_id)

    if TRADE_DRY_RUN:
        dry_run_id = f"dryrun-transfer-{uuid.uuid4()}"
        log.info("DRY RUN: simulated %s transfer. Tx: %s", asset_symbol, dry_run_id)
        return dry_run_id

    try:
        client = initialize_circle_client()
        transactions_api = developer_controlled_wallets.TransactionsApi(client)
        token_contract, call_data = _build_transfer_calldata(asset_symbol, transfer_amount, destination_address)
        tx_id = _submit_contract_execution(
            transactions_api,
            wallet_id,
            token_contract,
            call_data,
            f"transfer-{asset_symbol.lower()}",
        )
        if not _wait_for_transaction_success(transactions_api, tx_id):
            log.error("%s transfer did not confirm. Tx: %s", asset_symbol, tx_id)
            return None
        log.info("SUCCESS: %s transfer confirmed. Tx: %s", asset_symbol, tx_id)
        return tx_id
    except Exception as exc:
        log.error("Transfer failed", error=str(exc))
        return None
