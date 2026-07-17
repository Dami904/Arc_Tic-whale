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
