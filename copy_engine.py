# copy_engine.py
from database import get_active_followers
from trade_executor import execute_trade

def mirror_agent_trade(agent_name, action):
    """
    Finds all human users following an agent and mirrors the trade for them.
    """
    print(f"📡 Copy-Engine: Scanning for users copying {agent_name}...")
    
    # 1. Fetch all active followers from the database
    followers = get_active_followers(agent_name)
    
    if not followers:
        print(f"ℹ️ No active followers found for {agent_name}. Moving on.")
        return

    print(f"👥 Found {len(followers)} active follower(s). Mirroring {action} order...")

    # 2. Loop through each follower and execute a trade using their Circle Wallet ID
    for follower_wallet_id, allocation in followers:
        print(f"⚡ Mirroring trade for User Wallet: {follower_wallet_id} with amount: {allocation} USDC")
        
        # Call your existing self-healing trade executor using the user's specific wallet ID
        tx_id = execute_trade(
            wallet_id=follower_wallet_id, 
            action=action, 
            amount=str(allocation)
        )
        
        if tx_id:
            print(f"✅ Mirror Trade Successful for {follower_wallet_id}! Tx: {tx_id}")
        else:
            print(f"❌ Mirror Trade Failed for {follower_wallet_id}")
