import sys
from unittest.mock import patch

import pytest


def test_bot_module_requires_bot_token(monkeypatch):
    monkeypatch.setattr("backend.config.BOT_TOKEN", "")
    sys.modules.pop("server.bot", None)
    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        import server.bot  # noqa: F401


def test_bot_calls_a_real_telebot_method(monkeypatch, tmp_path):
    """Regression test: server/bot.py once called bot.infinite_polling(),
    which doesn't exist on TeleBot (the real method is infinity_polling) —
    this crashed the worker on every deploy. The previous test never caught
    it because it only exercises the BOT_TOKEN guard; infinity_polling()
    blocks forever in real use, so it's mocked here to safely execute the
    module's final line and assert the call actually resolves to a real,
    callable TeleBot attribute."""
    import telebot
    import backend.database as db

    monkeypatch.setattr(db, "_USE_PG", False)
    monkeypatch.setattr(db, "_PH", "?")
    monkeypatch.setattr(db, "DB_NAME", str(tmp_path / "test.db"))
    # A shape telebot's local format check accepts (colon-separated, no
    # network call) without needing a real bot token.
    monkeypatch.setattr("backend.config.BOT_TOKEN", "123456:FAKE-TOKEN-FOR-TESTING-ONLY")
    sys.modules.pop("server.bot", None)
    with patch.object(telebot.TeleBot, "infinity_polling") as mock_poll:
        import server.bot  # noqa: F401
    mock_poll.assert_called_once()
