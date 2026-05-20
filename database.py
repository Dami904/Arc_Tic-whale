# database.py
import sqlite3

DB_NAME = "agora_marketplace.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Create table for tracked copy-traders
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS followers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,          -- Unique ID for the human user
            user_wallet_id TEXT NOT NULL,   -- Circle Wallet ID of the follower
            target_agent TEXT NOT NULL,     -- e.g., 'Conservative_Whale'
            allocation_amount REAL NOT NULL,-- Amount of USDC to use per trade
            is_active INTEGER DEFAULT 1     -- 1 = Active, 0 = Paused
        )
    ''')
    conn.commit()
    conn.close()
    print("💾 Copy-trading database initialized successfully.")

def add_follower(user_id, user_wallet_id, target_agent, allocation_amount):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO followers (user_id, user_wallet_id, target_agent, allocation_amount)
        VALUES (?, ?, ?, ?)
    ''', (user_id, user_wallet_id, target_agent, allocation_amount))
    conn.commit()
    conn.close()
    print(f"➕ User {user_id} is now following {target_agent}.")

def get_active_followers(agent_name):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_wallet_id, allocation_amount FROM followers 
        WHERE target_agent = ? AND is_active = 1
    ''', (agent_name,))
    followers = cursor.fetchall()
    conn.close()
    return followers

if __name__ == "__main__":
    init_db()