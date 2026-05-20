# trade_executor.py
import uuid
from circle.web3 import developer_controlled_wallets
from wallet_manager import initialize_circle_client

def execute_trade(wallet_id, action, amount="1.0"):
    client = initialize_circle_client()
    transactions_api = developer_controlled_wallets.TransactionsApi(client)
    
    # --- 🛡️ SAFETY CLAMP ---
    trade_val = float(amount)
    clamped_val = max(0.5, min(2.0, trade_val))
    final_amount = str(clamped_val)
    
    if trade_val != clamped_val:
         print(f"⚠️ Limit Enforced: Adjusted requested amount {trade_val} to {final_amount} USDC")
    # -----------------------
    
    MOCK_DEX_ADDRESS = "0x000000000000000000000000000000000000dEaD"
    
    print(f"🔄 Constructing {action} transaction for Wallet ID: {wallet_id}...")

    try:
        # 🕵️ SDK X-Ray: Find the hidden classes dynamically
        available_classes = dir(developer_controlled_wallets)
        possible_requests = [c for c in available_classes if 'Request' in c and 'Create' in c and ('Transfer' in c or 'Transaction' in c)]
        
        target_class = None
        for req in possible_requests:
            if 'Transfer' in req:
                target_class = req
                break
        if not target_class and possible_requests:
            target_class = possible_requests[0]
            
        if not target_class:
            raise ValueError("Could not locate the Transaction Request model in the SDK!")
            
        # Define the RequestModel so the payload builder can use it!
        RequestModel = getattr(developer_controlled_wallets, target_class)

        # Build the payload using the clamped final_amount and explicit routing
        payload = {
            "idempotencyKey": str(uuid.uuid4()),
            "amounts": [final_amount],
            "destinationAddress": MOCK_DEX_ADDRESS,
            "feeLevel": "MEDIUM",
            "walletId": wallet_id,
            "blockchain": "ARC-TESTNET", 
            "currency": "USDC"            
        }
        
        tx_request = RequestModel.from_dict(payload)
        
        # 🕵️ SDK X-Ray: Find the execution method dynamically
        api_methods = [m for m in dir(transactions_api) if 'create' in m and ('transaction' in m or 'transfer' in m)]
        target_method = next((m for m in api_methods if 'transfer' in m), api_methods[0] if api_methods else 'create_transaction')
        execution_function = getattr(transactions_api, target_method)

        # 🚀 Sign and Broadcast!
        response = execution_function(tx_request)
        
        # Safely handle dynamic response objects
        try:
            tx_dict = response.data.to_dict()
        except AttributeError:
            tx_dict = response.data.transaction.to_dict() if hasattr(response.data, 'transaction') else {}
            
        tx_id = tx_dict.get('id', 'Unknown ID')
        
        print(f"✅ SUCCESS! {action} Order executed on-chain.")
        print(f"🔗 Transaction ID: {tx_id}")
        return tx_id

    except Exception as e:
        print(f"❌ Transaction failed: {e}")
        return None