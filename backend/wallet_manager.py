from circle.web3 import utils, developer_controlled_wallets
from backend.config import CIRCLE_API_KEY, CIRCLE_ENTITY_SECRET
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
            wallet_dict = wallet_response.data.wallets[0].to_dict()
        except AttributeError:
            wallet_dict = wallet_response.data.wallet.to_dict()

        wallet_id = wallet_dict['id']
        wallet_address = wallet_dict['address']

        log.info("Success! Wallet created for %s: id=%s address=%s", agent_name, wallet_id, wallet_address)

        return wallet_id

    except Exception as e:
        log.error("Error creating wallet", error=str(e))
        if "has no attribute" in str(e):
            log.debug("Available API methods: %s", [m for m in dir(wallets_api) if 'create' in m])
        return None

if __name__ == "__main__":
    log.info("Testing Circle Wallet Creation...")
    create_agent_wallet("Conservative_Whale")
