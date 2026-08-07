from circle.web3 import utils, developer_controlled_wallets
import httpx
from backend.config import AGENT_SERVICE_SECRET, AGENT_SERVICE_URL, CIRCLE_API_KEY, CIRCLE_ENTITY_SECRET
from backend.logger import get_logger

log = get_logger("wallet_manager")

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
    daily_limit: float = 50.0,
    max_per_tx: float = 2.0,
    monthly_limit: float = 500.0,
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
                "policy": {
                    "dailyLimit": str(daily_limit),
                    "maxPerTx": str(max_per_tx),
                    "monthlyLimit": str(monthly_limit),
                },
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


if __name__ == "__main__":
    log.info("Testing Circle Wallet Creation...")
    create_agent_wallet("Conservative_Whale")
