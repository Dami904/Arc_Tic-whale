from unittest.mock import patch

from scripts.send_daily_summaries import run_all


@patch("scripts.send_daily_summaries.init_db")
@patch("scripts.send_daily_summaries.send_daily_summary_reports", return_value=[{"user_id": "u1", "email": True, "telegram": False}])
def test_success_returns_zero(mock_send, mock_init, monkeypatch):
    monkeypatch.setenv("TRADE_DRY_RUN", "true")  # neutralize local .env for this test
    assert run_all() == 0
    mock_send.assert_called_once()


def test_missing_database_url_in_live_mode_blocks(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("TRADE_DRY_RUN", "false")
    with patch("scripts.send_daily_summaries.init_db") as mock_init:
        exit_code = run_all()
    assert exit_code == 1
    mock_init.assert_not_called()
