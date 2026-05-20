# test_setup.py
from database import init_db, add_follower

print("🔧 Setting up the test environment...")

# Ensure the database exists
init_db()

# Add a mock human user following the Conservative Whale
# We will use a fake Wallet UUID for this test. 
# (Note: Circle will reject a totally fake UUID formatting, so we'll just mock the function call if needed, 
# but let's try with a formatted UUID string first).
mock_user_id = "user_99_demo"
mock_wallet_id = "2e59a8c8-6db3-5d37-9343-f31ba3fcf883" # Dummy UUID format

add_follower(
    user_id=mock_user_id,
    user_wallet_id=mock_wallet_id,
    target_agent="Conservative_Whale",
    allocation_amount=1.0 # We will mirror trades using 1 USDC
)

print("✅ Test user injected successfully. You can now run main.py!")
