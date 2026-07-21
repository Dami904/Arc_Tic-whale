from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _fresh_telegram_bot_module(monkeypatch):
    """Every test gets a fresh import with a fake-but-valid-shaped token, so
    module-level `bot = telebot.TeleBot(BOT_TOKEN)` construction succeeds
    without hitting the network (telebot's local token check just wants a
    colon-separated string - see backend/config.py's real validation for
    the same pattern used in tests/test_bot_startup.py)."""
    import sys
    monkeypatch.setattr("backend.config.BOT_TOKEN", "123456:FAKE-TOKEN-FOR-TESTING-ONLY")
    sys.modules.pop("backend.telegram_bot", None)
    yield
    sys.modules.pop("backend.telegram_bot", None)


class TestProcessWebhookUpdate:
    def test_parses_and_dispatches_update(self):
        import backend.telegram_bot as tb

        with patch.object(tb.bot, "process_new_updates") as mock_process:
            tb.process_webhook_update({"update_id": 1, "message": {
                "message_id": 1, "date": 0, "chat": {"id": 1, "type": "private"},
                "text": "/start", "from": {"id": 1, "is_bot": False, "first_name": "T"},
            }})
        mock_process.assert_called_once()
        updates_arg = mock_process.call_args.args[0]
        assert len(updates_arg) == 1
        assert updates_arg[0].update_id == 1

    def test_swallows_exceptions_without_raising(self):
        import backend.telegram_bot as tb

        with patch.object(tb.bot, "process_new_updates", side_effect=RuntimeError("boom")):
            tb.process_webhook_update({"update_id": 1, "message": {
                "message_id": 1, "date": 0, "chat": {"id": 1, "type": "private"},
                "text": "/start", "from": {"id": 1, "is_bot": False, "first_name": "T"},
            }})  # must not raise

    def test_malformed_payload_does_not_raise(self):
        import backend.telegram_bot as tb
        tb.process_webhook_update({"not": "a valid update"})  # must not raise


class TestEnsureWebhookRegistered:
    def test_skips_when_bot_token_missing(self, monkeypatch):
        monkeypatch.setattr("backend.config.BOT_TOKEN", "")
        import sys
        sys.modules.pop("backend.telegram_bot", None)
        import backend.telegram_bot as tb
        assert tb.bot is None
        # Should not raise even though there's no bot instance to call set_webhook on.
        tb.ensure_webhook_registered()

    def test_skips_when_backend_url_is_localhost(self, monkeypatch):
        # Import BEFORE patching attributes on it via the module object
        # (not a string path) - monkeypatch's string-path setattr triggers
        # the module's own (re-)import as a side effect when it isn't
        # already cached, and that import can race with attribute patching
        # applied afterward. Importing first sidesteps it entirely.
        import backend.telegram_bot as tb
        monkeypatch.setattr(tb, "PYTHON_BACKEND_URL", "http://localhost:8765")
        with patch.object(tb.bot, "set_webhook") as mock_set:
            tb.ensure_webhook_registered()
        mock_set.assert_not_called()

    def test_registers_webhook_with_public_url_and_secret(self, monkeypatch):
        import backend.telegram_bot as tb
        monkeypatch.setattr(tb, "PYTHON_BACKEND_URL", "https://arc-tic-whale-api.onrender.com")
        monkeypatch.setattr(tb, "TELEGRAM_WEBHOOK_SECRET", "supersecret")
        with patch.object(tb.bot, "set_webhook") as mock_set:
            tb.ensure_webhook_registered()
        mock_set.assert_called_once_with(
            url="https://arc-tic-whale-api.onrender.com/telegram-webhook",
            secret_token="supersecret",
        )
