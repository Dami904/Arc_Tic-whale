import os
from dotenv import load_dotenv

env_path = os.path.join(os.path.dirname(__file__), '../.env')
load_dotenv(env_path)

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
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://127.0.0.1:8765/webapp")

def env_bool(name, default=False):
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}

def env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default

def env_list(name, default):
    raw = os.getenv(name)
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]

AGENT_DEV_MODE = env_bool("AGENT_DEV_MODE", False)
SOCIAL_DEV_MODE = env_bool("SOCIAL_DEV_MODE", False)
TRADE_DRY_RUN = env_bool("TRADE_DRY_RUN", True)

API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "")
RATE_LIMIT_PER_MINUTE = env_int("RATE_LIMIT_PER_MINUTE", 30)
CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS",
    ["http://127.0.0.1:8765", "http://localhost:8765"],
)

# Arc Testnet Contract Addresses (These are actual Arc Testnet addresses)
USDC_ARC_ADDRESS = "0x87796d11e5904D36eF0B93e8705F0721382A028a" # Circle's official USDC for Arc Testnet
WETH_ARC_ADDRESS = "0x0A92500445d4791E4276A8D25a1B4F4697F48467" # Wrapped ETH on Arc Testnet
WBTC_ARC_ADDRESS = "0x2eF7365F495471F854D228e9C050f2F9a0D6A7b7" # Wrapped BTC on Arc Testnet
# NOTE: Arc Testnet might not have a direct "WSOL". Using WUSDC as a common wrapper if needed, or a placeholder.
# If there's a specific WSOL on Arc Testnet, update this. For now, let's assume it's like a generic other wrapped asset.
WSOL_ARC_ADDRESS = "0x87796d11e5904D36eF0B93e8705F0721382A028a" # Placeholder: using USDC for simplicity. Replace if true WSOL found.
UNISWAP_ROUTER_ARC_ADDRESS = "0x25aB2aB7b5c13bC7d90e9d40C8d655f46B23F1C4" # Uniswap V3 Router on Arc Testnet

if GOOGLE_API_KEY:
    os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY
