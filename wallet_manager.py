# wallet_manager.py
from circle.web3 import utils, developer_controlled_wallets
from config import CIRCLE_API_KEY, CIRCLE_ENTITY_SECRET

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
    
    print(f"Creating a new Arc Testnet wallet for {agent_name}...")
    
    try:
        # Step 1: Create a Wallet Set
        set_request = developer_controlled_wallets.CreateWalletSetRequest.from_dict({
            "name": f"{agent_name}_Wallet_Set"
        })
        set_response = wallet_sets_api.create_wallet_set(set_request)
        
        wallet_set_dict = set_response.data.wallet_set.to_dict()
        wallet_set_id = wallet_set_dict['id']
        print(f"Wallet Set generated: {wallet_set_id}")
        
        # Step 2: Create the wallet on Arc Testnet
        wallet_request = developer_controlled_wallets.CreateWalletRequest.from_dict({
            "blockchains": ["ARC-TESTNET"],
            "walletSetId": wallet_set_id,
            "count": 1,
            "accountType": "SCA" 
        })
        
        # 🛠️ THE FIX: This method must also be singular!
        wallet_response = wallets_api.create_wallet(wallet_request)
        
        # 🛠️ SAFETY NET: Handle both singular and plural response objects
        try:
            wallet_dict = wallet_response.data.wallets[0].to_dict()
        except AttributeError:
            wallet_dict = wallet_response.data.wallet.to_dict()
            
        wallet_address = wallet_dict['address']
        
        print(f"✅ Success! Wallet created for {agent_name}")
        print(f"Address: {wallet_address}")
        
        return wallet_address

    except Exception as e:
        print(f"❌ Error creating wallet: {e}")
        # Bulletproof debug string to tell us exactly what methods exist if it fails again
        if "has no attribute" in str(e):
             print(f"Available API methods: {[m for m in dir(wallets_api) if 'create' in m]}")
        return None

# --- EASY TESTING BLOCK ---
if __name__ == "__main__":
    print("Testing Circle Wallet Creation...")
    create_agent_wallet("Conservative_Whale")