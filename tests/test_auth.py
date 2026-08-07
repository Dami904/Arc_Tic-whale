from unittest.mock import patch

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
# only touches local DB helpers (get_setting/get_user_preferences) - no
# external service calls - so it's used consistently as the auth probe here.


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

    def test_message_must_exactly_equal_canonical_form(self, client):
        nonce = client.get("/auth/wallet-nonce").json()["nonce"]
        r = self._login(client, message="Sign in to Arc_Tic Whale\nNonce: something-else", nonce=nonce)
        assert r.status_code == 401

    def test_message_with_extra_prepended_text_rejected(self, client):
        nonce = client.get("/auth/wallet-nonce").json()["nonce"]
        r = self._login(client, message=f"EVIL PREFIX\nSign in to Arc_Tic Whale\nNonce: {nonce}", nonce=nonce)
        assert r.status_code == 401

    def test_signature_from_different_key_rejected(self, client, monkeypatch):
        import server.api as api
        monkeypatch.setattr(api, "ensure_user_wallet", lambda uid: {"user_id": uid, "wallet_id": "w1", "wallet_address": "0x0"})
        nonce = client.get("/auth/wallet-nonce").json()["nonce"]
        message = f"Sign in to Arc_Tic Whale\nNonce: {nonce}"
        other_key = "0x" + "22" * 32
        r = client.post("/auth/wallet", json={
            "address": Account.from_key(self.KEY).address.lower(),
            "message": message,
            "signature": _sign(message, other_key),
            "nonce": nonce,
        })
        assert r.status_code == 401

    def test_expired_nonce_rejected(self, client):
        import backend.database as db
        nonce = db.create_auth_nonce(ttl_seconds=-1)
        message = f"Sign in to Arc_Tic Whale\nNonce: {nonce}"
        r = self._login(client, message=message, nonce=nonce)
        assert r.status_code == 401


class TestSessionStore503:
    def test_db_failure_returns_503_not_401(self, client, monkeypatch):
        import server.api as api
        def boom(token):
            raise RuntimeError("db down")
        monkeypatch.setattr(api, "get_session_user", boom)
        r = client.get("/settings", headers={"Authorization": "Bearer sess_whatever"})
        assert r.status_code == 503


class TestKillSwitchAdminOnly:
    def test_non_admin_cannot_set_kill_switch(self, client):
        token = db.create_session("email_test_user")
        r = client.post(
            "/settings/kill_switch",
            params={"value": "1"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 403

    def test_admin_can_set_kill_switch(self, client, monkeypatch):
        import server.api as api
        monkeypatch.setattr(api, "API_AUTH_TOKEN", "test-admin-token")
        r = client.post(
            "/settings/kill_switch",
            params={"value": "1"},
            headers={"Authorization": "Bearer test-admin-token"},
        )
        assert r.status_code == 200
        assert r.json()["kill_switch"] == "1"

    def test_non_admin_can_still_set_own_trade_alerts(self, client):
        token = db.create_session("email_test_user")
        r = client.post(
            "/settings/trade_alerts",
            params={"value": "0"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200


class TestExitAllPositionsEndpoint:
    def test_requires_auth(self, client):
        r = client.post("/positions/exit-all")
        assert r.status_code == 401

    def test_authenticated_user_with_no_positions_gets_empty_result(self, client):
        token = db.create_session("email_test_user")
        r = client.post("/positions/exit-all", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        body = r.json()
        assert body["exited"] == []
        assert body["failed"] == []


class TestAuthenticatedWalletAuthorization:
    def _seed_users(self):
        db.upsert_user_wallet(
            user_id="alice",
            wallet_id="wallet_alice",
            wallet_address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            referral_code="ref_alice",
        )
        db.upsert_user_wallet(
            user_id="bob",
            wallet_id="wallet_bob",
            wallet_address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            referral_code="ref_bob",
        )

    def test_users_ensure_requires_auth(self, client):
        r = client.post("/users/ensure", json={"username": "alice"})
        assert r.status_code == 401

    def test_deposit_ignores_username_query_and_uses_authenticated_user(self, client):
        self._seed_users()
        token = db.create_session("alice")

        r = client.post(
            "/deposit?username=bob",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        assert body["wallet_id"] == "wallet_alice"
        assert body["address"] == "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    def test_withdraw_ignores_request_username_and_uses_authenticated_wallet(self, client, monkeypatch):
        import server.api as api

        self._seed_users()
        token = db.create_session("alice")
        calls = []

        def fake_execute_transfer(**kwargs):
            calls.append(kwargs)
            return "tx_alice"

        monkeypatch.setattr(api, "execute_transfer", fake_execute_transfer)

        r = client.post(
            "/withdraw",
            json={
                "username": "bob",
                "destination_address": "0xcccccccccccccccccccccccccccccccccccccccc",
                "amount": 1.25,
                "asset": "USDC",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 200
        assert r.json()["status"] == "success"
        assert calls[0]["wallet_id"] == "wallet_alice"

    def test_follow_ignores_request_username_and_records_authenticated_user(self, client, monkeypatch):
        import server.api as api

        self._seed_users()
        token = db.create_session("alice")
        monkeypatch.setattr(api, "get_wallet_stats_safe", lambda wallet_id: {
            "total_balance_usd": 10.0,
            "token_balances": [{"symbol": "USDC", "amount": "10.0"}],
            "performance": {},
        })

        r = client.post(
            "/follow",
            json={
                "username": "bob",
                "allocation": 5.0,
                "stop_loss_pct": 10.0,
                "agent_id": "Conservative_Whale",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "success"
        assert body["wallet_id"] == "wallet_alice"
        assert db.get_follower_wallet("Conservative_Whale", "alice")["user_wallet_id"] == "wallet_alice"
        assert db.get_follower_wallet("Conservative_Whale", "bob") is None

    def test_trigger_trade_ignores_request_username_and_uses_authenticated_wallet(self, client, monkeypatch):
        import server.api as api

        self._seed_users()
        token = db.create_session("alice")
        calls = []

        def fake_run_trade_cycle(**kwargs):
            calls.append(kwargs)
            return {"status": "hold", "action": "HOLD", "tx_hash": None}

        monkeypatch.setattr(api, "run_trade_cycle", fake_run_trade_cycle)

        r = client.post(
            "/trigger-trade",
            json={"username": "bob", "agent_id": "Conservative_Whale"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 200
        assert r.json()["status"] == "hold"
        assert calls[0]["wallet_id"] == "wallet_alice"
        assert calls[0]["recipient_address"] == "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

    def test_trade_history_requires_auth_and_ignores_username_query(self, client):
        self._seed_users()
        db.log_trade(agent="Follower:wallet_alice", action="BUY", asset="BTC", tx_id="tx_a", reason="alice trade")
        db.log_trade(agent="Follower:wallet_bob", action="BUY", asset="ETH", tx_id="tx_b", reason="bob trade")

        assert client.get("/trade-history?username=bob").status_code == 401

        token = db.create_session("alice")
        r = client.get(
            "/trade-history?username=bob",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert r.status_code == 200
        trades = r.json()["trades"]
        assert [trade["tx_id"] for trade in trades] == ["tx_a"]


class TestTelegramWebhookEndpoint:
    def test_no_secret_configured_accepts_any_request(self, client, monkeypatch):
        import server.api as api
        monkeypatch.setattr(api, "TELEGRAM_WEBHOOK_SECRET", "")
        with patch("server.api.process_webhook_update") as mock_process:
            r = client.post("/telegram-webhook", json={"update_id": 1})
        assert r.status_code == 200
        mock_process.assert_called_once_with({"update_id": 1})

    def test_rejects_missing_secret_header_when_configured(self, client, monkeypatch):
        import server.api as api
        monkeypatch.setattr(api, "TELEGRAM_WEBHOOK_SECRET", "supersecret")
        with patch("server.api.process_webhook_update") as mock_process:
            r = client.post("/telegram-webhook", json={"update_id": 1})
        assert r.status_code == 401
        mock_process.assert_not_called()

    def test_rejects_wrong_secret_header(self, client, monkeypatch):
        import server.api as api
        monkeypatch.setattr(api, "TELEGRAM_WEBHOOK_SECRET", "supersecret")
        r = client.post(
            "/telegram-webhook",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        assert r.status_code == 401

    def test_accepts_correct_secret_header(self, client, monkeypatch):
        import server.api as api
        monkeypatch.setattr(api, "TELEGRAM_WEBHOOK_SECRET", "supersecret")
        with patch("server.api.process_webhook_update") as mock_process:
            r = client.post(
                "/telegram-webhook",
                json={"update_id": 1},
                headers={"X-Telegram-Bot-Api-Secret-Token": "supersecret"},
            )
        assert r.status_code == 200
        mock_process.assert_called_once_with({"update_id": 1})
