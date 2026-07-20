from unittest.mock import patch

from scripts.snapshot_nav import run_all


@patch("scripts.snapshot_nav.init_db")
@patch("scripts.snapshot_nav.upsert_nav_snapshot")
@patch("scripts.snapshot_nav.get_current_market_state", return_value={"BTC": {"PRICE": 100.0}})
@patch("scripts.snapshot_nav.get_trade_rows_for_performance")
def test_snapshots_every_agent(mock_rows, mock_market, mock_upsert, mock_init, monkeypatch):
    monkeypatch.setenv("TRADE_DRY_RUN", "true")  # neutralize local .env for this test
    mock_rows.return_value = [
        {"action": "BUY", "asset": "BTC", "price": 90.0, "timestamp": "t1"},
    ]
    exit_code = run_all()
    assert exit_code == 0
    assert mock_upsert.call_count == len(__import__("backend.agents", fromlist=["AGENT_PROFILES"]).AGENT_PROFILES)


@patch("scripts.snapshot_nav.init_db")
@patch("scripts.snapshot_nav.upsert_nav_snapshot")
@patch("scripts.snapshot_nav.get_current_market_state", return_value={"BTC": {"PRICE": 100.0}})
@patch("scripts.snapshot_nav.get_trade_rows_for_performance", side_effect=Exception("db down"))
def test_one_agent_failing_does_not_abort_others(mock_rows, mock_market, mock_upsert, mock_init, monkeypatch):
    monkeypatch.setenv("TRADE_DRY_RUN", "true")  # neutralize local .env for this test
    exit_code = run_all()
    assert exit_code == 1  # all failed in this case since the mock always raises
    assert mock_upsert.call_count == 0


@patch.dict("os.environ", {"DATABASE_URL": "", "TRADE_DRY_RUN": "false"}, clear=False)
def test_missing_database_url_in_live_mode_blocks(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("TRADE_DRY_RUN", "false")
    with patch("scripts.snapshot_nav.init_db") as mock_init:
        exit_code = run_all()
    assert exit_code == 1
    mock_init.assert_not_called()
