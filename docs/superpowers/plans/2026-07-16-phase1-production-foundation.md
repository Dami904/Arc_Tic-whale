# Phase 1 Production Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Arc_Tic Whale a reliably-running public testnet beta on $0/month: agents trade every 2h via GitHub Actions, auth has no impersonation holes, sessions survive restarts, and all displayed data is real.

**Architecture:** Trading moves out of the web process into a scheduled GitHub Actions workflow that runs `run_trade_cycle()` directly against Neon Postgres. The Render-hosted FastAPI app becomes sleep-tolerant: sessions and auth nonces move from in-process dicts to DB tables, and the guessable `wallet_<address>` bearer token is replaced by server-issued session tokens gated behind a signed-nonce flow.

**Tech Stack:** Python 3.12, FastAPI, pytest, eth_account (already dep of web3), SQLite locally / Postgres (Neon) in prod, GitHub Actions cron.

**Spec:** `docs/superpowers/specs/2026-07-16-phase1-production-foundation-design.md`

**Conventions for every task:**
- Run tests with the project venv: `venv/bin/python -m pytest ...` from repo root.
- Local runs use SQLite (no `DATABASE_URL` in the test env); tests must monkeypatch `backend.database.DB_NAME` to a tmp file — never touch `backend/agora_marketplace.db`.
- The local `.env` has `TRADE_DRY_RUN=false` and `AGENT_DEV_MODE=false`, so auth is enforced in tests hitting the API.
- Commit after each task with the message given in the task.

---

### Task 1: DB-backed sessions and auth nonces

**Files:**
- Modify: `backend/database.py` (add table DDL in `init_db()`, add functions at end of file)
- Test: `tests/test_sessions.py` (create)

- [ ] **Step 1: Write failing tests**

Create `tests/test_sessions.py`:

```python
import time

import pytest

import backend.database as db


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_NAME", str(tmp_path / "test.db"))
    db.init_db()
    yield db


class TestSessions:
    def test_create_and_lookup(self, tmp_db):
        token = tmp_db.create_session("user_abc", ttl_seconds=60)
        assert token.startswith("sess_")
        assert tmp_db.get_session_user(token) == "user_abc"

    def test_expired_session_returns_none(self, tmp_db):
        token = tmp_db.create_session("user_abc", ttl_seconds=-1)
        assert tmp_db.get_session_user(token) is None

    def test_unknown_token_returns_none(self, tmp_db):
        assert tmp_db.get_session_user("sess_doesnotexist") is None

    def test_token_stored_hashed(self, tmp_db):
        token = tmp_db.create_session("user_abc", ttl_seconds=60)
        with db._connection() as conn:
            cur = db._cursor(conn)
            cur.execute("SELECT token_hash FROM sessions")
            rows = db._rows(cur)
        assert len(rows) == 1
        assert token not in rows[0]["token_hash"]


class TestAuthNonces:
    def test_consume_valid_nonce_once(self, tmp_db):
        nonce = tmp_db.create_auth_nonce(ttl_seconds=300)
        assert tmp_db.consume_auth_nonce(nonce) is True
        assert tmp_db.consume_auth_nonce(nonce) is False  # single-use

    def test_expired_nonce_rejected(self, tmp_db):
        nonce = tmp_db.create_auth_nonce(ttl_seconds=-1)
        assert tmp_db.consume_auth_nonce(nonce) is False

    def test_unknown_nonce_rejected(self, tmp_db):
        assert tmp_db.consume_auth_nonce("nope") is False
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `venv/bin/python -m pytest tests/test_sessions.py -v`
Expected: FAIL — `AttributeError: module 'backend.database' has no attribute 'create_session'`

- [ ] **Step 3: Add table DDL to `init_db()`**

In `backend/database.py`, inside `init_db()` there are two branches (`if _USE_PG:` and the SQLite `else`). Add to the **Postgres branch** (after the existing `CREATE TABLE` statements, before the settings `INSERT`s):

```python
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at DOUBLE PRECISION NOT NULL
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS auth_nonces (
                    nonce TEXT PRIMARY KEY,
                    expires_at DOUBLE PRECISION NOT NULL
                )
            ''')
```

Add the same to the **SQLite branch** (types: `REAL` instead of `DOUBLE PRECISION`):

```python
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    expires_at REAL NOT NULL
                )
            ''')
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS auth_nonces (
                    nonce TEXT PRIMARY KEY,
                    expires_at REAL NOT NULL
                )
            ''')
```

- [ ] **Step 4: Add session/nonce functions**

At the end of `backend/database.py` add (note: `hashlib`, `secrets`, `time` need importing at the top of the file alongside the existing imports):

```python
# ── Sessions & auth nonces (DB-backed so they survive restarts/sleep) ─────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: str, ttl_seconds: int = 86_400) -> str:
    """Issue a new opaque session token for user_id. Only the hash is stored."""
    token = f"sess_{secrets.token_urlsafe(32)}"
    expires_at = time.time() + ttl_seconds
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"INSERT INTO sessions (token_hash, user_id, expires_at) VALUES ({_PH}, {_PH}, {_PH})",
            (_hash_token(token), user_id, expires_at),
        )
        conn.commit()
    return token


def get_session_user(token: str) -> str | None:
    """Return the user_id for a valid session token, or None. Cleans expired rows opportunistically."""
    now = time.time()
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(f"DELETE FROM sessions WHERE expires_at < {_PH}", (now,))
        cursor.execute(
            f"SELECT user_id FROM sessions WHERE token_hash = {_PH} AND expires_at >= {_PH}",
            (_hash_token(token), now),
        )
        row = _row(cursor)
        conn.commit()
    return row["user_id"] if row else None


def create_auth_nonce(ttl_seconds: int = 300) -> str:
    nonce = secrets.token_urlsafe(24)
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(
            f"INSERT INTO auth_nonces (nonce, expires_at) VALUES ({_PH}, {_PH})",
            (nonce, time.time() + ttl_seconds),
        )
        conn.commit()
    return nonce


def consume_auth_nonce(nonce: str) -> bool:
    """Atomically delete the nonce; True only if it existed and was unexpired (single-use)."""
    now = time.time()
    with _connection() as conn:
        cursor = _cursor(conn)
        cursor.execute(f"DELETE FROM auth_nonces WHERE expires_at < {_PH}", (now,))
        cursor.execute(f"DELETE FROM auth_nonces WHERE nonce = {_PH} AND expires_at >= {_PH}", (nonce, now))
        consumed = cursor.rowcount == 1
        conn.commit()
    return consumed
```

- [ ] **Step 5: Run tests, verify pass**

Run: `venv/bin/python -m pytest tests/test_sessions.py -v`
Expected: 7 passed

- [ ] **Step 6: Run full suite, then commit**

Run: `venv/bin/python -m pytest -q` — expected: all pass (47 existing + 7 new).

```bash
git add backend/database.py tests/test_sessions.py
git commit -m "feat: DB-backed sessions and single-use auth nonces"
```

---

### Task 2: Harden `verify_privy_token` — remove pseudo-token, DB sessions, narrowed dry-run bypass

**Files:**
- Modify: `server/api.py` (auth dependency ~lines 137–192; `/auth/verify-otp` ~line 1016)
- Test: `tests/test_auth.py` (create)

- [ ] **Step 1: Write failing tests**

Create `tests/test_auth.py`:

```python
import pytest
from fastapi.testclient import TestClient

import backend.database as db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_NAME", str(tmp_path / "test.db"))
    db.init_db()
    import server.api as api
    monkeypatch.setattr(api, "TRADE_DRY_RUN", False)
    monkeypatch.setattr(api, "AGENT_DEV_MODE", False, raising=False)
    return TestClient(api.app)


class TestWalletPseudoTokenRemoved:
    def test_bare_wallet_token_rejected(self, client):
        r = client.get(
            "/profile",
            headers={"Authorization": "Bearer wallet_0xabcdef1234567890abcdef1234567890abcdef12"},
        )
        assert r.status_code == 401


class TestDbSessionAccepted:
    def test_session_token_authenticates(self, client):
        token = db.create_session("email_test_user")
        r = client.get("/profile", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200

    def test_expired_session_rejected(self, client):
        token = db.create_session("email_test_user", ttl_seconds=-1)
        r = client.get("/profile", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401


class TestDryRunBypassNarrowed:
    def test_dry_run_alone_does_not_bypass_auth(self, client, monkeypatch):
        import server.api as api
        monkeypatch.setattr(api, "TRADE_DRY_RUN", True)
        monkeypatch.setattr(api, "AGENT_DEV_MODE", False, raising=False)
        r = client.get("/profile")
        assert r.status_code == 401

    def test_dry_run_plus_dev_mode_bypasses(self, client, monkeypatch):
        import server.api as api
        monkeypatch.setattr(api, "TRADE_DRY_RUN", True)
        monkeypatch.setattr(api, "AGENT_DEV_MODE", True, raising=False)
        r = client.get("/profile")
        assert r.status_code == 200
```

Note for the implementer: if `/profile` returns something other than 200 for a valid-but-unknown user (e.g. it auto-creates a profile), assert `r.status_code != 401` instead of `== 200` for the positive cases — the thing under test is auth, not the profile handler.

- [ ] **Step 2: Run tests, verify the pseudo-token test fails**

Run: `venv/bin/python -m pytest tests/test_auth.py -v`
Expected: `test_bare_wallet_token_rejected` FAILS (currently 200), `test_dry_run_alone_does_not_bypass_auth` FAILS (currently bypassed). Session tests fail too (`sess_` not handled).

- [ ] **Step 3: Rewrite the auth dependency**

In `server/api.py`:

1. Add `AGENT_DEV_MODE` to the `from backend.config import (...)` block.
2. Add `create_session, get_session_user, create_auth_nonce, consume_auth_nonce` to the `from backend.database import (...)` block.
3. Delete the `_OTP_SESSION_TTL` / `_otp_session_store` module-level dict and its comment block (~lines 137–142).
4. Replace the body of `verify_privy_token` between the Telegram block and the Privy JWT block so the whole function reads:

```python
def verify_privy_token(request: Request) -> str:
    """Accepts Telegram WebApp initData (X-Telegram-Init-Data header) or Privy JWT / session token (Bearer)."""
    if TRADE_DRY_RUN and AGENT_DEV_MODE:
        # Local development only. Production sets both to false (render.yaml).
        return "dryrun_user"

    # Telegram Mini App mode — verify HMAC signature from Telegram
    tg_init_data = request.headers.get("X-Telegram-Init-Data", "")
    if tg_init_data:
        tg_user_id = _verify_telegram_init_data(tg_init_data)
        if tg_user_id:
            return tg_user_id
        raise HTTPException(status_code=401, detail="Invalid Telegram auth data")

    # Web / Privy mode — Bearer token
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = auth.removeprefix("Bearer ")

    # Legacy static token (admin endpoints / CI)
    if API_AUTH_TOKEN and token == API_AUTH_TOKEN:
        return "admin"

    # Server-issued session token (email OTP fallback or wallet sign-in)
    if token.startswith("sess_"):
        try:
            user_id = get_session_user(token)
        except Exception:
            raise HTTPException(status_code=503, detail="Session store unavailable")
        if user_id:
            return user_id
        raise HTTPException(status_code=401, detail="Invalid or expired session token")

    # Privy access token (JWT)
    if not PRIVY_APP_ID:
        raise HTTPException(status_code=503, detail="Privy auth is not configured")
    try:
        return verify_privy_access_token(token)
    except _jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
```

This deletes both the `wallet_` pseudo-token branch and the `otp_` in-memory branch.

5. In `/auth/verify-otp` (custom-OTP fallback tail, ~line 1016), replace:

```python
    session_token = f"otp_{secrets.token_urlsafe(32)}"
    _otp_session_store[session_token] = (user_id, time.time() + _OTP_SESSION_TTL)
    return {"token": session_token, "user_id": user_id, "email": email}
```

with:

```python
    session_token = create_session(user_id)
    return {"token": session_token, "user_id": user_id, "email": email}
```

- [ ] **Step 4: Run tests, verify pass**

Run: `venv/bin/python -m pytest tests/test_auth.py -v`
Expected: 5 passed (or with the `!= 401` variant per the note).

- [ ] **Step 5: Grep for stragglers**

Run: `grep -n "_otp_session_store\|_OTP_SESSION_TTL" server/ backend/ -r`
Expected: no matches. If any remain, remove them.

- [ ] **Step 6: Run full suite, commit**

Run: `venv/bin/python -m pytest -q` — expected: all pass.

```bash
git add server/api.py tests/test_auth.py
git commit -m "fix: remove wallet pseudo-token auth bypass; DB-backed sessions; narrow dry-run bypass"
```

---

### Task 3: Signed-nonce wallet login (backend + frontend)

**Files:**
- Modify: `server/api.py` (`/auth/wallet` ~lines 1020–1041, add `/auth/wallet-nonce` above it)
- Modify: `frontend/index.html` (~lines 5168–5172, wallet login message construction)
- Test: `tests/test_auth.py` (extend)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_auth.py`:

```python
from eth_account import Account
from eth_account.messages import encode_defunct


def _sign(message: str, private_key: str) -> str:
    signed = Account.sign_message(encode_defunct(text=message), private_key=private_key)
    return signed.signature.hex()


class TestWalletNonceLogin:
    KEY = "0x" + "11" * 32  # deterministic test key

    def _login(self, client, message=None, nonce=None):
        acct = Account.from_key(self.KEY)
        if nonce is None:
            nonce = client.get("/auth/wallet-nonce").json()["nonce"]
        if message is None:
            message = f"Sign in to Arc_Tic Whale\nNonce: {nonce}"
        return client.post("/auth/wallet", json={
            "address": acct.address.lower(),
            "message": message,
            "signature": _sign(message, self.KEY),
            "nonce": nonce,
        })

    def test_valid_signed_nonce_returns_session(self, client, monkeypatch):
        import server.api as api
        monkeypatch.setattr(api, "ensure_user_wallet", lambda uid: {"user_id": uid, "wallet_id": "w1", "wallet_address": "0x0"})
        r = self._login(client)
        assert r.status_code == 200
        assert r.json()["token"].startswith("sess_")

    def test_nonce_cannot_be_replayed(self, client, monkeypatch):
        import server.api as api
        monkeypatch.setattr(api, "ensure_user_wallet", lambda uid: {"user_id": uid, "wallet_id": "w1", "wallet_address": "0x0"})
        nonce = client.get("/auth/wallet-nonce").json()["nonce"]
        assert self._login(client, nonce=nonce).status_code == 200
        assert self._login(client, nonce=nonce).status_code == 401

    def test_unknown_nonce_rejected(self, client):
        r = self._login(client, nonce="forged-nonce-value")
        assert r.status_code == 401

    def test_message_must_contain_nonce(self, client):
        nonce = client.get("/auth/wallet-nonce").json()["nonce"]
        r = self._login(client, message="Sign in to Arc_Tic Whale\nNonce: something-else", nonce=nonce)
        assert r.status_code == 401
```

(`ensure_user_wallet` is monkeypatched because it calls the Circle API; auth logic is what's under test.)

- [ ] **Step 2: Run tests, verify they fail**

Run: `venv/bin/python -m pytest tests/test_auth.py -v -k WalletNonce`
Expected: FAIL — 404 on `/auth/wallet-nonce`.

- [ ] **Step 3: Implement backend**

In `server/api.py`, add above the existing `/auth/wallet` endpoint:

```python
@app.get("/auth/wallet-nonce")
def wallet_nonce():
    """Issue a single-use, 5-minute nonce the wallet must sign to log in."""
    nonce = create_auth_nonce(ttl_seconds=300)
    return {"nonce": nonce, "message": f"Sign in to Arc_Tic Whale\nNonce: {nonce}"}
```

Replace the body of `wallet_auth` with:

```python
@app.post("/auth/wallet")
async def wallet_auth(body: dict):
    address   = (body.get("address") or "").lower().strip()
    message   = (body.get("message") or "").strip()
    signature = (body.get("signature") or "").strip()
    nonce     = (body.get("nonce") or "").strip()
    if not address or not message or not signature or not nonce:
        raise HTTPException(status_code=400, detail="address, message, signature, and nonce required")
    if nonce not in message:
        raise HTTPException(status_code=401, detail="Signed message does not contain the issued nonce")
    if not consume_auth_nonce(nonce):
        raise HTTPException(status_code=401, detail="Invalid, expired, or already-used nonce")
    msg = encode_defunct(text=message)
    try:
        recovered = Account.recover_message(msg, signature=signature).lower()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid signature format")
    if recovered != address:
        raise HTTPException(status_code=401, detail="Signature verification failed")
    wallet = ensure_user_wallet(f"wallet_{address[:8]}")
    session_token = create_session(wallet["user_id"])
    return {
        "token": session_token,
        "user_id": wallet["user_id"],
        "email": "",
        "display_name": "Trading Wallet",
        "wallet_address": address,
    }
```

- [ ] **Step 4: Run tests, verify pass**

Run: `venv/bin/python -m pytest tests/test_auth.py -v`
Expected: all pass.

- [ ] **Step 5: Update frontend**

In `frontend/index.html` (~line 5168), replace:

```javascript
                const message = `Sign in to Arc_Tic Whale — ${Date.now()}`;
                const signature = await provider.request({ method: 'personal_sign', params: [message, address] });
                const res = await apiFetch('/auth/wallet', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ address, message, signature }),
                });
```

with:

```javascript
                const nonceRes = await apiFetch('/auth/wallet-nonce');
                const { nonce, message } = await nonceRes.json();
                const signature = await provider.request({ method: 'personal_sign', params: [message, address] });
                const res = await apiFetch('/auth/wallet', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ address, message, signature, nonce }),
                });
```

- [ ] **Step 6: Run full suite, commit**

Run: `venv/bin/python -m pytest -q` — expected: all pass.

```bash
git add server/api.py frontend/index.html tests/test_auth.py
git commit -m "feat: signed-nonce wallet login issuing DB-backed session tokens"
```

---

### Task 4: GitHub Actions trade cycle; retire in-process scheduler

**Files:**
- Create: `scripts/run_cycle.py`
- Create: `.github/workflows/trade-cycle.yml`
- Modify: `server/api.py` (lifespan ~lines 86–90; `/scheduler/status` ~lines 1101–1112; remove scheduler import line 50)
- Test: `tests/test_run_cycle.py` (create)

- [ ] **Step 1: Write failing test for the cycle runner**

Create `tests/test_run_cycle.py`:

```python
from unittest.mock import patch

from scripts.run_cycle import run_all


def _fake_cycle(results):
    def fake(wallet_id=None, agent_name=None, market_data=None, rate_limit_sleep=0, **kw):
        return results.get(agent_name, {"status": "error", "action": None, "asset": None,
                                        "tx_hash": None, "reason": "boom"})
    return fake


@patch("scripts.run_cycle.init_db")
@patch("scripts.run_cycle.STAGGER_SECONDS", 0)
@patch("scripts.run_cycle.get_current_market_state", return_value={"BTC": {"PRICE": 1}})
@patch("scripts.run_cycle.is_kill_switch_active", return_value=False)
def test_exit_code_zero_when_any_agent_succeeds(_kill, _mkt, _init):
    ok = {"status": "hold", "action": "HOLD", "asset": "BTC", "tx_hash": None, "reason": "flat"}
    with patch("scripts.run_cycle.run_trade_cycle", side_effect=_fake_cycle({"Conservative_Whale": ok})):
        assert run_all() == 0


@patch("scripts.run_cycle.init_db")
@patch("scripts.run_cycle.STAGGER_SECONDS", 0)
@patch("scripts.run_cycle.get_current_market_state", return_value={"BTC": {"PRICE": 1}})
@patch("scripts.run_cycle.is_kill_switch_active", return_value=False)
def test_exit_code_one_when_all_agents_fail(_kill, _mkt, _init):
    with patch("scripts.run_cycle.run_trade_cycle", side_effect=_fake_cycle({})):
        assert run_all() == 1


@patch("scripts.run_cycle.init_db")
@patch("scripts.run_cycle.is_kill_switch_active", return_value=True)
def test_kill_switch_skips_cleanly(_kill, _init):
    assert run_all() == 0
```

- [ ] **Step 2: Run test, verify it fails**

Run: `venv/bin/python -m pytest tests/test_run_cycle.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.run_cycle'`

- [ ] **Step 3: Implement `scripts/run_cycle.py`**

```python
"""
run_cycle.py — Entry point for the scheduled GitHub Actions trade cycle.

Runs one trade cycle per agent in AGENT_PROFILES against the configured
DATABASE_URL (Neon Postgres in production). Market data is fetched once
and shared. Exits 0 if at least one agent completed (or kill switch is on),
1 if every agent errored — so the Actions run shows red only on total failure.
"""
from __future__ import annotations

import sys
import time
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.agents import AGENT_PROFILES
from backend.database import init_db, is_kill_switch_active
from backend.logger import get_logger
from backend.market_data import get_current_market_state
from backend.trade_service import run_trade_cycle

log = get_logger("run_cycle")

STAGGER_SECONDS = 30  # between agents, for Gemini rate limits


def run_all() -> int:
    init_db()
    if is_kill_switch_active():
        log.warning("Kill switch active — skipping this cycle.")
        print("KILL SWITCH ACTIVE — cycle skipped.")
        return 0

    market = get_current_market_state()
    results: dict[str, dict] = {}
    agents = list(AGENT_PROFILES.keys())
    for i, agent in enumerate(agents):
        try:
            results[agent] = run_trade_cycle(
                agent_name=agent, market_data=market, rate_limit_sleep=0
            )
        except Exception as exc:
            log.error("Agent %s crashed: %s", agent, exc)
            results[agent] = {"status": "error", "action": None, "asset": None,
                              "tx_hash": None, "reason": str(exc)}
        if i < len(agents) - 1 and STAGGER_SECONDS:
            time.sleep(STAGGER_SECONDS)

    print(f"\n{'AGENT':<22} {'STATUS':<9} {'ACTION':<6} {'ASSET':<6} REASON")
    for agent, r in results.items():
        print(f"{agent:<22} {r['status']:<9} {str(r['action']):<6} "
              f"{str(r['asset']):<6} {r['reason'][:80]}")

    all_failed = all(r["status"] == "error" for r in results.values())
    return 1 if all_failed else 0


if __name__ == "__main__":
    sys.exit(run_all())
```

- [ ] **Step 4: Run test, verify pass**

Run: `venv/bin/python -m pytest tests/test_run_cycle.py -v`
Expected: 3 passed

- [ ] **Step 5: Create the workflow**

Create `.github/workflows/trade-cycle.yml`:

```yaml
name: Trade Cycle

on:
  schedule:
    - cron: "0 */2 * * *"   # every 2 hours (UTC); GitHub may delay under load — acceptable
  workflow_dispatch: {}      # manual trigger for testing

concurrency:
  group: trade-cycle
  cancel-in-progress: false

jobs:
  trade:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    env:
      DATABASE_URL: ${{ secrets.DATABASE_URL }}
      GOOGLE_API_KEY: ${{ secrets.GOOGLE_API_KEY }}
      CIRCLE_API_KEY: ${{ secrets.CIRCLE_API_KEY }}
      CIRCLE_ENTITY_SECRET: ${{ secrets.CIRCLE_ENTITY_SECRET }}
      AGENT_WALLET_ID: ${{ secrets.AGENT_WALLET_ID }}
      AGENT_WALLET_ADDRESS: ${{ secrets.AGENT_WALLET_ADDRESS }}
      TRADE_DRY_RUN: "false"
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip
      - run: pip install -r requirements.txt
      - run: python scripts/run_cycle.py
```

- [ ] **Step 6: Retire the in-process scheduler and repoint `/scheduler/status`**

In `server/api.py`:

1. Delete the import on line 50: `from backend.trade_scheduler import start_trade_scheduler, stop_trade_scheduler, get_scheduler_state`.
2. Add `get_latest_trade` — already imported (line 32). No change needed.
3. Replace the lifespan body:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    start_daily_summary_scheduler()
    # Trade cycles run in GitHub Actions (.github/workflows/trade-cycle.yml),
    # not in this process — the web service is allowed to sleep on Render.
    yield
```

4. Replace the `/scheduler/status` handler:

```python
@app.get("/scheduler/status")
def scheduler_status(_: str = Depends(verify_privy_token)):
    """Reports the last trade cycle, derived from trade_history (cycles run in GitHub Actions)."""
    latest = get_latest_trade()
    return {
        "interval_hours": 2.0,
        "runner":         "github-actions",
        "last_trade":     latest,
        "mode":           "dry-run" if TRADE_DRY_RUN else "live",
    }
```

Note: check `get_latest_trade`'s signature in `backend/database.py` first — if it requires an agent argument, call it in a loop over `AGENT_PROFILES` and return the newest, e.g. `max(rows, key=lambda r: r["timestamp"])`.

5. `backend/trade_scheduler.py` stays in the repo (unused by the API now) — do NOT delete it; `scripts` history and rollback value.

- [ ] **Step 7: Verify API still boots and tests pass**

Run: `venv/bin/python -c "from server.api import app; print('boots ok')"`
Expected: `boots ok`
Run: `venv/bin/python -m pytest -q` — expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add scripts/run_cycle.py .github/workflows/trade-cycle.yml server/api.py tests/test_run_cycle.py
git commit -m "feat: run trade cycles via GitHub Actions cron; retire in-process scheduler"
```

---

### Task 5: Data hygiene — seed guard, demo purge script, stale file removal

**Files:**
- Modify: `scripts/seed_demo.py` (top of file)
- Create: `scripts/purge_demo_data.py`
- Modify: `.gitignore`
- Delete (git rm): `circle1.db`, `agora_marketplace.db` (repo root), `agents.py` (repo root), `main.js` (repo root)

- [ ] **Step 1: Guard the seed script**

At the top of `scripts/seed_demo.py`, after the `sys.path.insert` line, add:

```python
_db_url = os.getenv("DATABASE_URL", "")
_is_remote = _db_url and "localhost" not in _db_url and "127.0.0.1" not in _db_url
if _is_remote and "--force" not in sys.argv:
    sys.exit(
        "REFUSING to seed demo data: DATABASE_URL points at a remote database.\n"
        "This would inject fake trades into production. Pass --force to override."
    )
```

- [ ] **Step 2: Create the purge script**

Create `scripts/purge_demo_data.py`:

```python
"""
purge_demo_data.py — One-time cleanup of seeded demo rows before public beta.
Deletes trade_history/social_posts/followers/users rows created by seed_demo.py.
Run: python scripts/purge_demo_data.py            (prints what it WOULD delete)
     python scripts/purge_demo_data.py --apply    (actually deletes)
"""
from __future__ import annotations

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backend.database as db

APPLY = "--apply" in sys.argv

DEMO_PREDICATES = [
    ("trade_history", f"tx_id LIKE '0xabc%' OR tx_id LIKE '0xf0%' OR agent LIKE '%dryrun%' OR agent LIKE '%demo%'"),
    ("social_posts",  f"tx_id LIKE '0xabc%' OR tx_id LIKE '0xf0%'"),
    ("followers",     f"user_id IN ('dryrun_user') OR user_wallet_id IN ('wallet_dryrun_001', 'wallet_demo_001')"),
    ("users",         f"user_id IN ('dryrun_user') OR wallet_id IN ('wallet_dryrun_001', 'wallet_demo_001')"),
]

with db._connection() as conn:
    cur = db._cursor(conn)
    for table, predicate in DEMO_PREDICATES:
        cur.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE {predicate}")
        n = db._row(cur)["n"]
        if APPLY:
            cur.execute(f"DELETE FROM {table} WHERE {predicate}")
            print(f"{table}: deleted {n} demo rows")
        else:
            print(f"{table}: WOULD delete {n} demo rows (run with --apply)")
    if APPLY:
        conn.commit()
```

(Predicates are fixed strings, not user input — f-strings are safe here.)

- [ ] **Step 3: Verify purge dry-run against the local SQLite DB**

Run: `venv/bin/python scripts/purge_demo_data.py`
Expected: counts > 0 for trade_history and users (the seeded rows found earlier). Then run with `--apply` and re-run the dry-run to confirm 0s.

- [ ] **Step 4: Remove stale root files**

First verify nothing imports them:

Run: `grep -rn "^import agents\|^from agents import\|require.*main\.js" server/ backend/ scripts/ tests/ frontend/*.html frontend/*.mjs | grep -v backend.agents`
Expected: no matches. Then:

```bash
git rm --cached circle1.db agora_marketplace.db 2>/dev/null; rm -f circle1.db agora_marketplace.db
git rm agents.py main.js
```

(Root `agents.py` is a 1-line shim; root `main.js` is empty. `backend/agora_marketplace.db` — the live local DB — is NOT touched and stays gitignored.)

- [ ] **Step 5: Tighten `.gitignore`**

Replace the line `agora_marketplace.db` with `*.db` in `.gitignore`.

- [ ] **Step 6: Run full suite, commit**

Run: `venv/bin/python -m pytest -q` — expected: all pass.

```bash
git add .gitignore scripts/seed_demo.py scripts/purge_demo_data.py
git commit -m "chore: guard seed script, add demo-data purge, remove stale root artifacts"
```

---

### Task 6: Deploy config + docs

**Files:**
- Modify: `render.yaml`
- Modify: `README.md` (deployment section)

- [ ] **Step 1: Update `render.yaml`**

In the `arctic-whale-api` service env vars, add after `TRADE_DRY_RUN`:

```yaml
      - key: AGENT_DEV_MODE
        value: "false"
```

Add a comment above the `arctic-whale-api` service block:

```yaml
  # NOTE: Trade cycles do NOT run in this service. They run in GitHub Actions
  # (.github/workflows/trade-cycle.yml) directly against the Neon DATABASE_URL,
  # so this service can sleep on the free tier without missing trades.
```

- [ ] **Step 2: Update README deployment notes**

In `README.md`, add a short section after the Architecture section:

```markdown
## Scheduled Trading (Production)

Trade cycles run every 2 hours via GitHub Actions (`.github/workflows/trade-cycle.yml`),
not inside the web service — so Render free-tier sleep never stops the agents.

Required GitHub Actions secrets (repo → Settings → Secrets → Actions):
`DATABASE_URL`, `GOOGLE_API_KEY`, `CIRCLE_API_KEY`, `CIRCLE_ENTITY_SECRET`,
`AGENT_WALLET_ID`, `AGENT_WALLET_ADDRESS`.

Manual run: Actions tab → "Trade Cycle" → Run workflow.
Kill switch: set the `kill_switch` setting to `1` (via the app's admin toggle) — cycles skip cleanly.
```

- [ ] **Step 3: Full suite + boot check, commit**

Run: `venv/bin/python -m pytest -q` — expected: all pass.
Run: `venv/bin/python -c "from server.api import app; print('boots ok')"` — expected: `boots ok`.

```bash
git add render.yaml README.md
git commit -m "docs: document GitHub Actions trading runner; pin AGENT_DEV_MODE=false in prod"
```

---

## Manual steps after implementation (user or operator, not automatable from this repo)

1. Add the six secrets to GitHub Actions (repo Settings → Secrets and variables → Actions).
2. Push to GitHub; trigger "Trade Cycle" manually once via `workflow_dispatch` and confirm a green run + new `trade_history` rows in Neon.
3. On Render: set `AGENT_DEV_MODE=false` on the live service (matches render.yaml).
4. Run `python scripts/purge_demo_data.py --apply` once against production `DATABASE_URL`.
5. Watch the Actions tab for 24h of green 2-hourly runs (done criterion from the spec).
