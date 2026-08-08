import os
from decimal import Decimal, InvalidOperation

from circle.web3 import utils, developer_controlled_wallets
import httpx
from backend.config import AGENT_SERVICE_SECRET, AGENT_SERVICE_URL, CIRCLE_API_KEY, CIRCLE_ENTITY_SECRET
from backend.logger import get_logger

log = get_logger("wallet_manager")


def _env_decimal(name: str, default: str, hard_default: str | None = None) -> Decimal:
    hard_default = hard_default or default
    try:
        return Decimal(os.getenv(name, default))
    except InvalidOperation:
        log.warning("Invalid decimal env %s=%s; using %s.", name, os.getenv(name), hard_default)
        return Decimal(hard_default)


DEFAULT_POLICY_MAX_PER_TX = _env_decimal("USER_WALLET_MAX_PER_TX_LIMIT", os.getenv("MAX_TRADE_NOTIONAL", "100"), "100")
DEFAULT_POLICY_DAILY_LIMIT = _env_decimal("USER_WALLET_DAILY_LIMIT", "1000")
DEFAULT_POLICY_MONTHLY_LIMIT = _env_decimal("USER_WALLET_MONTHLY_LIMIT", "5000")

def initialize_circle_client():
    if not CIRCLE_API_KEY or not CIRCLE_ENTITY_SECRET:
        raise ValueError("Circle API Key or Entity Secret missing in .env!")

    return utils.init_developer_controlled_wallets_client(
        api_key=CIRCLE_API_KEY,
        entity_secret=CIRCLE_ENTITY_SECRET
    )

def create_agent_wallet(agent_name):
    client = initialize_circle_client()

    wallet_sets_api = developer_controlled_wallets.WalletSetsApi(client)
    wallets_api = developer_controlled_wallets.WalletsApi(client)

    log.info("Creating a new Arc Testnet wallet for %s...", agent_name)

    try:
        set_request = developer_controlled_wallets.CreateWalletSetRequest.from_dict({
            "name": f"{agent_name}_Wallet_Set"
        })
        set_response = wallet_sets_api.create_wallet_set(set_request)

        wallet_set_dict = set_response.data.wallet_set.to_dict()
        wallet_set_id = wallet_set_dict['id']
        log.info("Wallet Set generated: %s", wallet_set_id)

        wallet_request = developer_controlled_wallets.CreateWalletRequest.from_dict({
            "blockchains": ["ARC-TESTNET"],
            "walletSetId": wallet_set_id,
            "count": 1,
            "accountType": "SCA"
        })

        wallet_response = wallets_api.create_wallet(wallet_request)

        try:
            raw = wallet_response.data.wallets[0]
        except (AttributeError, IndexError, TypeError):
            try:
                raw = wallet_response.data.wallet
            except AttributeError:
                log.error("Unexpected Circle wallet response structure", response=str(wallet_response))
                return None
        wallet_dict = raw.to_dict() if hasattr(raw, 'to_dict') else raw
        if not wallet_dict.get("id") or not wallet_dict.get("address"):
            log.error("Wallet response missing id or address", wallet=wallet_dict)
            return None

        wallet_id = wallet_dict['id']
        wallet_address = wallet_dict['address']

        log.info("Success! Wallet created for %s: id=%s address=%s", agent_name, wallet_id, wallet_address)

        return {
            "wallet_id": wallet_id,
            "address": wallet_address,
        }

    except Exception as e:
        log.error("Error creating wallet", error=str(e))
        if "has no attribute" in str(e):
            log.debug("Available API methods: %s", [m for m in dir(wallets_api) if 'create' in m])
        return None

def create_wallet_with_policy(
    agent_name: str,
    daily_limit: float | Decimal = DEFAULT_POLICY_DAILY_LIMIT,
    max_per_tx: float | Decimal = DEFAULT_POLICY_MAX_PER_TX,
    monthly_limit: float | Decimal = DEFAULT_POLICY_MONTHLY_LIMIT,
) -> dict | None:
    """
    Creates a wallet via the Node.js Agent Service (spending policies enforced).
    Fails closed if the service cannot attach the requested spending policy.
    """
    if not AGENT_SERVICE_URL:
        log.error("AGENT_SERVICE_URL is required to create policy-protected user wallets.")
        return None
    try:
        headers = {"x-agent-secret": AGENT_SERVICE_SECRET} if AGENT_SERVICE_SECRET else {}
        res = httpx.post(
            f"{AGENT_SERVICE_URL}/wallets",
            json={
                "name": agent_name,
                "blockchain": "ARC-TESTNET",
                "policy": _policy_payload(daily_limit, max_per_tx, monthly_limit),
            },
            headers=headers,
            timeout=15,
        )
        res.raise_for_status()
        data = res.json()
        if data.get("policy_attached") is not True:
            log.error(
                "Agent Service returned wallet without spending policy for %s: id=%s",
                agent_name, data.get("wallet_id"),
            )
            return None
        log.info(
            "Agent Service wallet created for %s: id=%s policy_attached=%s",
            agent_name, data.get("wallet_id"), data.get("policy_attached"),
        )
        return {"wallet_id": data["wallet_id"], "address": data["address"]}
    except Exception as e:
        log.error("Policy-protected wallet creation failed via Agent Service: %s", e)
        return None


def _policy_payload(
    daily_limit: float | Decimal,
    max_per_tx: float | Decimal,
    monthly_limit: float | Decimal,
) -> dict:
    return {
        "dailyLimit": str(Decimal(str(daily_limit))),
        "maxPerTx": str(Decimal(str(max_per_tx))),
        "monthlyLimit": str(Decimal(str(monthly_limit))),
    }


def policy_for_copy_allocation(allocation: float | Decimal) -> dict:
    try:
        allocation_amount = Decimal(str(allocation))
    except (InvalidOperation, ValueError):
        allocation_amount = Decimal("0")

    entry_notional = max(Decimal("0.5"), allocation_amount * Decimal("0.10"))
    max_per_tx = max(DEFAULT_POLICY_MAX_PER_TX, entry_notional)
    daily_limit = max(DEFAULT_POLICY_DAILY_LIMIT, max_per_tx * Decimal("10"))
    monthly_limit = max(DEFAULT_POLICY_MONTHLY_LIMIT, daily_limit * Decimal("5"))
    return _policy_payload(daily_limit, max_per_tx, monthly_limit)


def update_wallet_policy(
    wallet_id: str,
    daily_limit: float | Decimal,
    max_per_tx: float | Decimal,
    monthly_limit: float | Decimal,
) -> bool:
    if not AGENT_SERVICE_URL:
        log.error("AGENT_SERVICE_URL is required to update wallet spending policy.")
        return False
    if not wallet_id:
        log.error("wallet_id is required to update wallet spending policy.")
        return False
    try:
        headers = {"x-agent-secret": AGENT_SERVICE_SECRET} if AGENT_SERVICE_SECRET else {}
        res = httpx.put(
            f"{AGENT_SERVICE_URL}/wallets/{wallet_id}/policy",
            json=_policy_payload(daily_limit, max_per_tx, monthly_limit),
            headers=headers,
            timeout=15,
        )
        res.raise_for_status()
        data = res.json()
        if data.get("updated") is not True:
            log.error("Agent Service policy update was not applied for wallet %s: %s", wallet_id, data)
            return False
        return True
    except Exception as e:
        log.error("Policy update failed via Agent Service for wallet %s: %s", wallet_id, e)
        return False


if __name__ == "__main__":
    log.info("Testing Circle Wallet Creation...")
    create_agent_wallet("Conservative_Whale")
