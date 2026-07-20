import sys

import pytest


def test_bot_module_requires_bot_token(monkeypatch):
    monkeypatch.setattr("backend.config.BOT_TOKEN", "")
    sys.modules.pop("server.bot", None)
    with pytest.raises(RuntimeError, match="BOT_TOKEN"):
        import server.bot  # noqa: F401
