from unittest.mock import patch

from scripts.run_cycle import run_all


def _fake_cycle(results):
    def fake(wallet_id=None, agent_name=None, market_data=None, rate_limit_sleep=0, **kw):
        return results.get(agent_name, {"status": "error", "action": None, "asset": None,
                                        "tx_hash": None, "reason": "boom"})
    return fake


# NOTE: the repo's .env sets TRADE_DRY_RUN=false and does not define DATABASE_URL,
# so every test here must explicitly control TRADE_DRY_RUN via monkeypatch to get
# a deterministic result from the missing-secrets guard in run_all().


@patch("scripts.run_cycle.init_db")
@patch("scripts.run_cycle.STAGGER_SECONDS", 0)
@patch("scripts.run_cycle.get_current_market_state", return_value={"BTC": {"PRICE": 1}})
@patch("scripts.run_cycle.is_kill_switch_active", return_value=False)
def test_exit_code_zero_when_any_agent_succeeds(_kill, _mkt, _init, monkeypatch):
    monkeypatch.setenv("TRADE_DRY_RUN", "true")
    ok = {"status": "hold", "action": "HOLD", "asset": "BTC", "tx_hash": None, "reason": "flat"}
    with patch("scripts.run_cycle.run_trade_cycle", side_effect=_fake_cycle({"Conservative_Whale": ok})):
        assert run_all() == 0


@patch("scripts.run_cycle.init_db")
@patch("scripts.run_cycle.STAGGER_SECONDS", 0)
@patch("scripts.run_cycle.get_current_market_state", return_value={"BTC": {"PRICE": 1}})
@patch("scripts.run_cycle.is_kill_switch_active", return_value=False)
def test_exit_code_one_when_all_agents_fail(_kill, _mkt, _init, monkeypatch):
    monkeypatch.setenv("TRADE_DRY_RUN", "true")
    with patch("scripts.run_cycle.run_trade_cycle", side_effect=_fake_cycle({})):
        assert run_all() == 1


@patch("scripts.run_cycle.init_db")
@patch("scripts.run_cycle.is_kill_switch_active", return_value=True)
def test_kill_switch_skips_cleanly(_kill, _init, monkeypatch):
    monkeypatch.setenv("TRADE_DRY_RUN", "true")
    assert run_all() == 0


@patch("scripts.run_cycle.init_db")
def test_missing_secrets_blocks_live_run(_init, monkeypatch):
    monkeypatch.setenv("TRADE_DRY_RUN", "false")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "x")
    monkeypatch.setenv("CIRCLE_API_KEY", "x")
    monkeypatch.setenv("CIRCLE_ENTITY_SECRET", "x")
    monkeypatch.setenv("AGENT_WALLET_ID", "x")
    assert run_all() == 1
    _init.assert_not_called()
