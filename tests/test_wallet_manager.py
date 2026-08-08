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
    assert calls[0][1]["json"]["policy"]["maxPerTx"] == str(wallet_manager.DEFAULT_POLICY_MAX_PER_TX)


def test_create_wallet_with_policy_rejects_agent_service_failure(monkeypatch):
    monkeypatch.setattr(wallet_manager, "AGENT_SERVICE_URL", "http://agent-service")
    monkeypatch.setattr(wallet_manager, "AGENT_SERVICE_SECRET", "")
    monkeypatch.setattr(wallet_manager.httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))

    assert wallet_manager.create_wallet_with_policy("User_alice") is None


def test_policy_for_copy_allocation_scales_with_entry_size():
    policy = wallet_manager.policy_for_copy_allocation(2000)

    assert policy["maxPerTx"] == "200.0"
    assert policy["dailyLimit"] == "2000.0"
    assert policy["monthlyLimit"] == "10000.0"


def test_update_wallet_policy_sends_authenticated_put(monkeypatch):
    calls = []
    monkeypatch.setattr(wallet_manager, "AGENT_SERVICE_URL", "http://agent-service")
    monkeypatch.setattr(wallet_manager, "AGENT_SERVICE_SECRET", "secret")

    def fake_put(*args, **kwargs):
        calls.append((args, kwargs))
        return FakeResponse({"updated": True})

    monkeypatch.setattr(wallet_manager.httpx, "put", fake_put)

    assert wallet_manager.update_wallet_policy(
        "wallet_1",
        daily_limit="100",
        max_per_tx="10",
        monthly_limit="500",
    )
    assert calls[0][0][0] == "http://agent-service/wallets/wallet_1/policy"
    assert calls[0][1]["headers"] == {"x-agent-secret": "secret"}
    assert calls[0][1]["json"] == {
        "dailyLimit": "100",
        "maxPerTx": "10",
        "monthlyLimit": "500",
    }


def test_update_wallet_policy_rejects_unapplied_response(monkeypatch):
    monkeypatch.setattr(wallet_manager, "AGENT_SERVICE_URL", "http://agent-service")
    monkeypatch.setattr(wallet_manager, "AGENT_SERVICE_SECRET", "secret")
    monkeypatch.setattr(wallet_manager.httpx, "put", lambda *args, **kwargs: FakeResponse({"updated": False}))

    assert not wallet_manager.update_wallet_policy("wallet_1", daily_limit="100", max_per_tx="10", monthly_limit="500")
