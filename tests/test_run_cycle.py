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
