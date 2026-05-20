# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# AI Keys
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

# Circle Keys
CIRCLE_API_KEY = os.getenv("CIRCLE_API_KEY")
CIRCLE_ENTITY_SECRET = os.getenv("CIRCLE_ENTITY_SECRET")

# Agent Keys
AGENT_WALLET_ADDRESS = os.getenv("AGENT_WALLET_ADDRESS")
AGENT_WALLET_ID = os.getenv("AGENT_WALLET_ID")

# Telegram Keys
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Force the Gemini key into the system environment for LangChain
if GOOGLE_API_KEY:
    os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY
    print(f"✅ DEBUG: Config loaded successfully.")
else:
    print("❌ WARNING: Keys missing from .env!")