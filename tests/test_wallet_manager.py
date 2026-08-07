import backend.wallet_manager as wallet_manager


class FakeResponse:
    def __init__(self, payload, status_ok=True):
        self._payload = payload
        self._status_ok = status_ok

    def raise_for_status(self):
        if not self._status_ok:
            raise RuntimeError("service error")

    def json(self):
        return self._payload


def test_create_wallet_with_policy_rejects_missing_agent_service_url(monkeypatch):
    monkeypatch.setattr(wallet_manager, "AGENT_SERVICE_URL", "")

    assert wallet_manager.create_wallet_with_policy("User_alice") is None


def test_create_wallet_with_policy_rejects_unprotected_wallet_response(monkeypatch):
    monkeypatch.setattr(wallet_manager, "AGENT_SERVICE_URL", "http://agent-service")
    monkeypatch.setattr(wallet_manager, "AGENT_SERVICE_SECRET", "secret")
    monkeypatch.setattr(wallet_manager.httpx, "post", lambda *args, **kwargs: FakeResponse({
        "wallet_id": "wallet_1",
        "address": "0xabc",
        "policy_attached": False,
    }))

    assert wallet_manager.create_wallet_with_policy("User_alice") is None


def test_create_wallet_with_policy_returns_policy_protected_wallet(monkeypatch):
    calls = []
    monkeypatch.setattr(wallet_manager, "AGENT_SERVICE_URL", "http://agent-service")
    monkeypatch.setattr(wallet_manager, "AGENT_SERVICE_SECRET", "secret")

    def fake_post(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse({
            "wallet_id": "wallet_1",
            "address": "0xabc",
            "policy_attached": True,
        })

    monkeypatch.setattr(wallet_manager.httpx, "post", fake_post)

    wallet = wallet_manager.create_wallet_with_policy("User_alice")

    assert wallet == {"wallet_id": "wallet_1", "address": "0xabc"}
    assert calls[0][1]["headers"] == {"x-agent-secret": "secret"}


def test_create_wallet_with_policy_rejects_agent_service_failure(monkeypatch):
    monkeypatch.setattr(wallet_manager, "AGENT_SERVICE_URL", "http://agent-service")
    monkeypatch.setattr(wallet_manager, "AGENT_SERVICE_SECRET", "")
    monkeypatch.setattr(wallet_manager.httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))

    assert wallet_manager.create_wallet_with_policy("User_alice") is None
