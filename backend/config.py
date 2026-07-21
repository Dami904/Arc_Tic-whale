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
PARENT_WALLET_ID = os.getenv("PARENT_WALLET_ID", AGENT_WALLET_ID or "")

# Telegram Keys (optional - only needed to run the Telegram bot)
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBAPP_URL = os.getenv("WEBAPP_URL", "http://127.0.0.1:8765/webapp")
# Verifies incoming webhook POSTs actually came from Telegram (sent back as
# the X-Telegram-Bot-Api-Secret-Token header). Optional but strongly
# recommended once BOT_TOKEN is set - without it, POST /telegram-webhook
# accepts unauthenticated requests.
TELEGRAM_WEBHOOK_SECRET = os.getenv("TELEGRAM_WEBHOOK_SECRET", "")

# Database
DATABASE_URL = os.getenv("DATABASE_URL", "")  # PostgreSQL on Railway; falls back to SQLite if empty

# Privy Auth
PRIVY_APP_ID = os.getenv("PRIVY_APP_ID", "")
PRIVY_APP_SECRET = os.getenv("PRIVY_APP_SECRET", "")
PRIVY_CLIENT_ID = os.getenv("PRIVY_CLIENT_ID", "")
# Allowed origin for postMessage from OAuth popup. Must match actual app URL.
PRIVY_AUTH_ORIGIN = os.getenv("PRIVY_AUTH_ORIGIN", "").rstrip("/")
# Optional PEM from Privy Dashboard → Configuration → App settings → Verification key
PRIVY_VERIFICATION_KEY = os.getenv("PRIVY_VERIFICATION_KEY", "").replace("\\n", "\n")
WALLETCONNECT_PROJECT_ID = os.getenv("WALLETCONNECT_PROJECT_ID", "")

# Optional: real email for custom OTP fallback (when Privy server API fails)
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "Arc_Tic Whale <onboarding@resend.dev>")

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
USER_WALLET_SIGNUP_FUND_AMOUNT = os.getenv("USER_WALLET_SIGNUP_FUND_AMOUNT", "0")

API_AUTH_TOKEN = os.getenv("API_AUTH_TOKEN", "")
AGENT_SERVICE_URL      = os.getenv("AGENT_SERVICE_URL", "")
AGENT_SERVICE_SECRET   = os.getenv("AGENT_SERVICE_SECRET", "")
PYTHON_BACKEND_URL     = os.getenv("PYTHON_BACKEND_URL", "http://localhost:8765")
RATE_LIMIT_PER_MINUTE = env_int("RATE_LIMIT_PER_MINUTE", 30)
CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS",
    ["http://127.0.0.1:8765", "http://localhost:8765"],
)

# Arc Testnet Contract Addresses
USDC_ARC_ADDRESS            = "0x3600000000000000000000000000000000000000"
WETH_ARC_ADDRESS            = "0x4ccccd3220ac80c07a8B575A4cb494c0E77606Ed"
WBTC_ARC_ADDRESS            = "0xf0C4a4CE82A5746AbAAd9425360Ab04fbBA432BF"
EURC_ARC_ADDRESS            = "0x89B50855Aa3bE2F677cD6303Cec089B5F319D72a"
UNISWAP_ROUTER_ARC_ADDRESS  = "0x25aB2aB7b5c13bC7d90e9d40C8d655f46B23F1C4"

if GOOGLE_API_KEY:
    os.environ["GOOGLE_API_KEY"] = GOOGLE_API_KEY
