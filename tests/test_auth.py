import pytest
from fastapi.testclient import TestClient

import backend.database as db


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_USE_PG", False)
    monkeypatch.setattr(db, "_PH", "?")
    monkeypatch.setattr(db, "DB_NAME", str(tmp_path / "test.db"))
    db.init_db()
    import server.api as api
    monkeypatch.setattr(api, "TRADE_DRY_RUN", False)
    monkeypatch.setattr(api, "AGENT_DEV_MODE", False, raising=False)
    return TestClient(api.app)


# NOTE: the spec's example used GET /profile, but the actual route in
# server/api.py is GET /settings (there is no bare /profile route; the
# closest is /user/profile). /settings is protected by verify_privy_token,
# always returns 200 regardless of whether the user exists in the DB, and
# only touches local DB helpers (get_setting/get_user_preferences) — no
# external service calls — so it's used consistently as the auth probe here.


class TestWalletPseudoTokenRemoved:
    def test_bare_wallet_token_rejected(self, client):
        r = client.get(
            "/settings",
            headers={"Authorization": "Bearer wallet_0xabcdef1234567890abcdef1234567890abcdef12"},
        )
        assert r.status_code == 401


class TestDbSessionAccepted:
    def test_session_token_authenticates(self, client):
        token = db.create_session("email_test_user")
        r = client.get("/settings", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200

    def test_expired_session_rejected(self, client):
        token = db.create_session("email_test_user", ttl_seconds=-1)
        r = client.get("/settings", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401


class TestDryRunBypassNarrowed:
    def test_dry_run_alone_does_not_bypass_auth(self, client, monkeypatch):
        import server.api as api
        monkeypatch.setattr(api, "TRADE_DRY_RUN", True)
        monkeypatch.setattr(api, "AGENT_DEV_MODE", False, raising=False)
        r = client.get("/settings")
        assert r.status_code == 401

    def test_dry_run_plus_dev_mode_bypasses(self, client, monkeypatch):
        import server.api as api
        monkeypatch.setattr(api, "TRADE_DRY_RUN", True)
        monkeypatch.setattr(api, "AGENT_DEV_MODE", True, raising=False)
        r = client.get("/settings")
        assert r.status_code == 200


from eth_account import Account
from eth_account.messages import encode_defunct


def _sign(message: str, private_key: str) -> str:
    signed = Account.sign_message(encode_defunct(text=message), private_key=private_key)
    sig = signed.signature.hex()
    return sig if sig.startswith("0x") else "0x" + sig


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


class TestSessionStore503:
    def test_db_failure_returns_503_not_401(self, client, monkeypatch):
        import server.api as api
        def boom(token):
            raise RuntimeError("db down")
        monkeypatch.setattr(api, "get_session_user", boom)
        r = client.get("/settings", headers={"Authorization": "Bearer sess_whatever"})
        assert r.status_code == 503
