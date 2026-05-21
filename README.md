# Agora Crypto Network — Arc-Tic Whale

AI-powered copy-trading platform on **Circle Arc Testnet**. The Conservative Whale agent uses Google Gemini to analyze market data, execute swaps via Uniswap V3, and lets users mirror its trades automatically.

---

## Architecture

```mermaid
flowchart TD
    CG[CoinGecko API] --> MD[market_data.py]
    YF[Yahoo Finance] --> MD

    MD --> TS[trade_service.py<br/>run_trade_cycle]
    TS --> AI[agents.py<br/>ask_conservative_whale]
    AI --> GL[Google Gemini 2.5 Flash]

    TS --> TE[trade_executor.py<br/>Uniswap V3 swap]
    TE --> CW[Circle Developer Wallets<br/>Arc Testnet]
    TE --> WM[wallet_manager.py]

    TS --> CE[copy_engine.py]
    CE --> DB[(SQLite<br/>agora_marketplace.db)]

    TS --> SO[social.py]
    SO --> CP[@thecanteenapp]

    API[api.py<br/>FastAPI] --> TS
    CLI[main.py<br/>CLI] --> TS

    WEB[index.html<br/>Telegram Mini App] --> API
    TB[bot.py<br/>Telegram Bot] --> WEB
```

### Data Flow

```
Market Data (CoinGecko/Yahoo)
       │
       ▼
  AI Agent (Gemini 2.5 Flash)
  ──► DECISION: BUY / SELL / HOLD
       │
       ▼
  Trade Executor (Circle Wallet → Uniswap V3 on Arc Testnet)
       │
       ├──► Copy Engine (mirror to followers)
       └──► Social Post (@thecanteenapp)
```

---

## Setup

### Prerequisites

- Python 3.11+
- A [Circle API](https://console.circle.com) account with Arc Testnet access
- A [Google AI](https://aistudio.google.com) API key (Gemini)
- Optional: Telegram Bot Token for the Mini App

### Installation

```bash
git clone <repo-url>
cd circle1

python -m venv venv
# Windows: venv\Scripts\activate
# Linux/macOS: source venv/bin/activate

pip install -r requirements.txt
```

### Configuration

Copy `.env` and fill in your keys:

```env
# Required
GOOGLE_API_KEY=your_gemini_key
CIRCLE_API_KEY=your_circle_api_key
CIRCLE_ENTITY_SECRET=your_circle_entity_secret
AGENT_WALLET_ADDRESS=0x...
AGENT_WALLET_ID=uuid-from-circle

# Optional
BOT_TOKEN=your_telegram_bot_token
API_AUTH_TOKEN=secure-token-for-api-endpoints
LOG_LEVEL=INFO                  # DEBUG / INFO / WARNING / ERROR
TRADE_DRY_RUN=true              # true = no real blockchain tx
AGENT_DEV_MODE=false            # true = skip Gemini, return mock BUY
SOCIAL_DEV_MODE=false           # true = skip Gemini social post
RATE_LIMIT_PER_MINUTE=30        # max API calls per minute
CORS_ALLOWED_ORIGINS=http://127.0.0.1:8765,https://yourdomain.com
```

---

## Run Modes

### 1. CLI (single trade cycle)

```bash
python -m server.main
```

Runs one full AI → trade → copy → social cycle and exits.

### 2. API Server (FastAPI)

```bash
uvicorn server.api:app --host 127.0.0.1 --port 8765 --reload
```

| Endpoint | Method | Auth | Description |
|---|---|---|---|
| `/` | GET | No | Health check + agent address |
| `/stats` | GET | No | Wallet balance + performance |
| `/market-data` | GET | Rate-limited | Live crypto/stock prices |
| `/webapp` | GET | No | Telegram Mini App UI |
| `/trigger-trade` | POST | Bearer token | Force one AI trade cycle |
| `/follow` | POST | Bearer token + rate-limited | Register as copy-trader |

### 3. Telegram Bot

```bash
python -m server.bot
```

Serves the Mini App via `/start` command.

### 4. Development Mode

Set `AGENT_DEV_MODE=true` and `TRADE_DRY_RUN=true` to test the full pipeline without real API calls or blockchain transactions:

```bash
AGENT_DEV_MODE=true TRADE_DRY_RUN=true python -m server.main
```

---

## Project Structure

```
circle1/
├── backend/
│   ├── trade_service.py     # Shared run_trade_cycle() pipeline
│   ├── agents.py            # Gemini AI agent + fallback logic
│   ├── market_data.py       # CoinGecko & Yahoo Finance fetcher
│   ├── trade_executor.py    # Uniswap V3 swap via Circle wallets
│   ├── copy_engine.py       # Mirror trades to follower wallets
│   ├── social.py            # Auto-generated social posts
│   ├── wallet_manager.py    # Circle wallet creation
│   ├── database.py          # SQLite (followers, trades, settings)
│   ├── logger.py            # Structured logging (structlog)
│   ├── config.py            # Env vars & contract addresses
│   └── utils.py             # parse_ai_decision parser
├── server/
│   ├── api.py               # FastAPI server with security hardening
│   ├── bot.py               # Telegram bot
│   ├── main.py              # CLI entry point → run_trade_cycle()
│   └── start_server.py      # Local DB initializer helper
├── frontend/
│   └── index.html           # Telegram Mini App UI
├── scripts/                 # Utility scripts
├── agents.py                # Backward-compatible import wrapper
├── requirements.txt     # Python dependencies
├── tests/
│   ├── test_utils.py            # parse_ai_decision unit tests
│   ├── test_market_data.py      # Formatting & structure tests
│   └── test_trade_service.py    # Full trade flow tests
└── .env                 # Secrets (git-ignored)
```

---

## Testing

```bash
# Install test deps
pip install pytest pytest-asyncio

# Run all tests
pytest tests/ -v

# Run specific test file
pytest tests/test_utils.py -v
```

### Test coverage

- **`test_utils.py`** — 13 test cases for `parse_ai_decision()` (BUY/SELL/HOLD, case insensitivity, malformed input, fallback asset, reason extraction)
- **`test_market_data.py`** — 14 test cases for `_format_change()`, `_percent_change()`, and market data dictionary structure
- **`test_trade_service.py`** — 6 test cases for the full trade cycle (BUY flow, SELL flow, HOLD flow, execution failure, missing wallet, fallback decisions)

---

## Security

- **CORS**: Restricted to origins in `CORS_ALLOWED_ORIGINS`
- **Auth**: `/trigger-trade`, `/follow`, `/settings/*`, `/deposit`, and `/withdraw` require `Authorization: Bearer <API_AUTH_TOKEN>` header in live mode. For easier local testing, auth is bypassed whenever `TRADE_DRY_RUN=true`.
- **Web app auth**: If auth is required, protected actions in the local Mini App prompt once for `API_AUTH_TOKEN` and store it in browser `localStorage`.
- **Rate limiting**: All POST endpoints and `/market-data` are rate-limited (configurable via `RATE_LIMIT_PER_MINUTE`)
- **Kill switch**: `kill_switch=1` prevents the shared trade cycle from executing.
- **Dry-run**: `TRADE_DRY_RUN=true` prevents real blockchain transactions. Set `TRADE_DRY_RUN=false` only when the configured Circle wallet is funded and you want Arc Testnet transactions submitted.
- **Wallet stats**: Dashboard balance is read from the configured `AGENT_WALLET_ID` test wallet by default. In production, `/dashboard?username=<telegram-user>` can reflect the wallet created for that Telegram username after `/follow`.
- **Trade history**: Successful agent and follower executions store the Circle transaction id in SQLite so the tx can be checked on Arc Testnet.
- **Alerts/summaries**: `trade_alerts` and `daily_summary` are persisted settings today; outbound notifications and scheduled daily summary jobs still need bot/scheduler wiring.
- **Deposit/withdraw**: Deposit returns the active wallet address. Withdraw requires a destination/amount UI and Circle transfer API wiring before funds should move.
- **Structured logging**: All events logged via structlog with ISO timestamps, configurable log levels

---

## Tech Stack

- **Backend**: Python, FastAPI, Uvicorn
- **AI**: Google Gemini 2.5 Flash, LangChain
- **Blockchain**: Circle Developer-Controlled Wallets, Uniswap V3 on Arc Testnet
- **Market Data**: CoinGecko (crypto), Yahoo Finance (stocks)
- **Frontend**: Telegram Mini App, vanilla HTML/CSS/JS
- **Bot**: pyTelegramBotAPI
- **Database**: SQLite
- **Logging**: structlog
- **Testing**: pytest
