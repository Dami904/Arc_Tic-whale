"""
Seed the local SQLite database with realistic demo data for a camera-ready demo.
Run once: python scripts/seed_demo.py
"""
from __future__ import annotations
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backend.database as db

db.init_db()

# ── Demo user — matches dryrun_user returned by verify_privy_token in dry-run mode
USER_ID     = "dryrun_user"
WALLET_ID   = "wallet_dryrun_001"
WALLET_ADDR = "0xDEMO1234567890abcdef1234567890abcdef1234"

db.upsert_user_wallet(
    user_id=USER_ID,
    wallet_id=WALLET_ID,
    wallet_address=WALLET_ADDR,
    referral_code="DEMO2026",
    email="demo@arcticwhale.xyz",
    display_name="Demo Trader",
)
print(f"✅ User created: {USER_ID}")

# ── Follow the main agent ────────────────────────────────────────────────────
try:
    db.add_follower(
        USER_ID, WALLET_ID, "Conservative_Whale",
        allocation_amount=20.0, asset="USDC",
        user_wallet_address=WALLET_ADDR,
        stop_loss_pct=10.0,
    )
    print("✅ Follower added: Conservative_Whale (20 USDC)")
except Exception as e:
    print(f"ℹ️  Follower already exists: {e}")

# ── Agent trades (what the AI agent executed) ─────────────────────────────────
agent_trades = [
    ("Conservative_Whale", "BUY",  "BTC",  "0xabc001", "BTC breaking out above key resistance. Strong volume confirmation."),
    ("Conservative_Whale", "HOLD", "BTC",  "0xabc002", "Holding BTC position. RSI neutral, no clear exit signal yet."),
    ("Conservative_Whale", "SELL", "BTC",  "0xabc003", "Taking profit on BTC. Target hit at +4.2%. Rotating to EURC."),
    ("Conservative_Whale", "BUY",  "ETH",  "0xabc004", "ETH dip buy. Oversold on 4H, support holding at $2,050."),
    ("Conservative_Whale", "BUY",  "EURC", "0xabc005", "Reducing risk. Parking gains in EURC ahead of macro data."),
    ("Conservative_Whale", "SELL", "ETH",  "0xabc006", "ETH reached resistance. Locking in +3.8% gain."),
    ("Conservative_Whale", "BUY",  "BTC",  "0xabc007", "Re-entering BTC. New higher low established. Trend intact."),
]
for agent, action, asset, txid, reason in agent_trades:
    try:
        db.log_trade(agent=agent, action=action, asset=asset, tx_id=txid, reason=reason)
    except Exception as e:
        print(f"ℹ️  Agent trade {txid}: {e}")
print(f"✅ Agent trades: {len(agent_trades)}")

# ── Follower (copy) trades for demo user ─────────────────────────────────────
follower_trades = [
    (f"Follower:{WALLET_ID}", "BUY",  "BTC",  "0xf001", "Copied Conservative_Whale — BTC breakout entry"),
    (f"Follower:{WALLET_ID}", "HOLD", "BTC",  "0xf002", "Copied Conservative_Whale — holding BTC"),
    (f"Follower:{WALLET_ID}", "SELL", "BTC",  "0xf003", "Copied Conservative_Whale — profit take +4.2%"),
    (f"Follower:{WALLET_ID}", "BUY",  "ETH",  "0xf004", "Copied Conservative_Whale — ETH dip buy"),
    (f"Follower:{WALLET_ID}", "BUY",  "EURC", "0xf005", "Copied Conservative_Whale — rotating to EURC"),
    (f"Follower:{WALLET_ID}", "SELL", "ETH",  "0xf006", "Copied Conservative_Whale — ETH profit take +3.8%"),
    (f"Follower:{WALLET_ID}", "BUY",  "BTC",  "0xf007", "Copied Conservative_Whale — BTC re-entry"),
]
for agent, action, asset, txid, reason in follower_trades:
    try:
        db.log_trade(agent=agent, action=action, asset=asset, tx_id=txid, reason=reason)
    except Exception as e:
        print(f"ℹ️  Follower trade {txid}: {e}")
print(f"✅ Follower trades: {len(follower_trades)}")

# ── Social feed posts ─────────────────────────────────────────────────────────
posts = [
    ("Conservative_Whale", "BUY",  "0xabc001", "BTC breaking out above key resistance. Strong volume confirmation. Entered long 🐋 #BTC #CopyTrading"),
    ("Conservative_Whale", "SELL", "0xabc003", "Profit locked on BTC +4.2% 🎯 Rotating gains to EURC. Patience pays."),
    ("Conservative_Whale", "BUY",  "0xabc004", "ETH oversold on the 4H chart. Support holding at $2,050. Loading up 🔵 #ETH"),
    ("Conservative_Whale", "SELL", "0xabc006", "ETH resistance hit — taking the +3.8% and moving on. Never get greedy. 📊"),
    ("Conservative_Whale", "BUY",  "0xabc007", "BTC higher low confirmed. Trend is your friend. Re-entering 🐋 #Bitcoin"),
]
for agent, action, txid, text in posts:
    try:
        db.log_social_post(agent=agent, action=action, post_text=text, tx_id=txid)
    except Exception as e:
        print(f"ℹ️  Post {txid}: {e}")
print(f"✅ Social feed posts: {len(posts)}")

# ── Verify ───────────────────────────────────────────────────────────────────
user = db.get_user(USER_ID)
prefs = db.get_user_preferences(USER_ID)
summary = db.get_follower_summary("Conservative_Whale")
all_posts = db.get_social_posts(limit=10)
print(f"\n📊 Verification:")
print(f"   User:          {user['display_name']} ({user['user_id']})")
print(f"   Email:         {user['email']}")
print(f"   Followers:     {summary['total_followers']} | Allocation: {summary['total_allocation']} USDC")
print(f"   Social posts:  {len(all_posts)} in DB")
print(f"\n✅ App is camera-ready. Open http://localhost:8765")
