import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.database import init_db, add_follower, get_follower_wallet

print("Setting up the test environment...")

init_db()

mock_user_id = "user_99_demo"
mock_wallet_id = "2e59a8c8-6db3-5d37-9343-f31ba3fcf883"
mock_wallet_address = "0x0000000000000000000000000000000000000099"
target_agent = "Conservative_Whale"

if get_follower_wallet(mock_user_id, target_agent):
    print("Test user already exists, skipping insert.")
else:
    try:
        add_follower(
            user_id=mock_user_id,
            user_wallet_id=mock_wallet_id,
            user_wallet_address=mock_wallet_address,
            target_agent=target_agent,
            allocation_amount=1.0,
        )
        print("Test user injected successfully. You can now run main.py!")
    except Exception as e:
        print(f"Failed to inject test user: {e}")
        sys.exit(1)
