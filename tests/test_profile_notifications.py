from __future__ import annotations

from pathlib import Path

import backend.database as db
import backend.assistant as assistant
import backend.notifications as notifications


def _use_temp_db(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(db, "DB_NAME", str(tmp_path / "test.db"))
    monkeypatch.setattr(db, "_USE_PG", False)
    monkeypatch.setattr(db, "_PH", "?")
    db.init_db()


def test_user_schema_migration_and_profile_update(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)

    db.upsert_user_wallet(
        user_id="user_1",
        wallet_id="wallet_1",
        wallet_address="0xabc",
        referral_code="ref_user_1",
        email="first@example.com",
        display_name="First User",
        avatar_url="https://example.com/avatar.png",
    )

    user = db.get_user("user_1")
    assert user["email"] == "first@example.com"
    assert user["display_name"] == "First User"
    assert user["avatar_url"] == "https://example.com/avatar.png"
    assert int(user["trade_alerts"]) == 1
    assert int(user["daily_summary"]) == 1

    updated = db.update_user_profile("user_1", email="new@example.com", display_name="Updated User")
    assert updated["email"] == "new@example.com"
    assert updated["display_name"] == "Updated User"

    db.set_user_preferences("user_1", trade_alerts=False, daily_summary=True)
    prefs = db.get_user_preferences("user_1")
    assert prefs == {"trade_alerts": 0, "daily_summary": 1}


def test_detach_marks_follower_inactive(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)

    db.upsert_user_wallet(
        user_id="user_2",
        wallet_id="wallet_2",
        wallet_address="0xdef",
        referral_code="ref_user_2",
    )
    db.add_follower("user_2", "wallet_2", "Conservative_Whale", 25.0, asset="USDC", user_wallet_address="0xdef")
    summary_before = db.get_follower_summary("Conservative_Whale")
    assert summary_before["total_followers"] == 1
    assert float(summary_before["total_allocation"]) == 25.0

    assert db.deactivate_follower("user_2", "Conservative_Whale") is True
    summary_after = db.get_follower_summary("Conservative_Whale")
    assert summary_after["total_followers"] == 0
    assert float(summary_after["total_allocation"]) == 0.0


def test_notification_helpers_and_channel_reminder(monkeypatch):
    user = {"email": "member@example.com", "telegram_chat_id": "", "trade_alerts": 1, "daily_summary": 1}
    reminder = notifications.build_notification_reminder(user)
    assert "Add an email in Profile" not in reminder

    empty_user = {"email": "", "telegram_chat_id": "", "trade_alerts": 1, "daily_summary": 0}
    reminder = notifications.build_notification_reminder(empty_user)
    assert "Add an email in Profile" in reminder

    trade_text = notifications.build_trade_alert_message("Arc_Tic Whale", "BUY", "BTC", 50, 68420.5, "2026-05-24T20:00:00")
    assert "🚨 Trade Alert - Arc_Tic_Whale" in trade_text
    assert "Agent: Arc_Tic Whale" in trade_text
    assert "Action: BUY BTC" in trade_text
    assert "Amount: $50.00 USDC" in trade_text

    summary_text = notifications.build_daily_summary_message("2026-05-24", 3, 12.5, 1.2, 88.0, 2)
    assert "📊 Daily Summary - Arc_Tic_Whale" in summary_text
    assert "Trades executed today: 3" in summary_text
    assert "Today's P&L: +$12.50 (+1.20%)" in summary_text


def test_trade_alert_uses_email_channel_when_available(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        text = "ok"

    class FakeClient:
        def __init__(self, timeout=12):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json=None, headers=None):
            calls.append((url, json, headers))
            return FakeResponse()

    monkeypatch.setattr(notifications, "RESEND_API_KEY", "resend_key")
    monkeypatch.setattr(notifications, "RESEND_FROM_EMAIL", "Arc_Tic Whale <alerts@example.com>")
    monkeypatch.setattr(notifications, "BOT_TOKEN", "")
    monkeypatch.setattr(notifications.httpx, "Client", FakeClient)

    result = notifications.send_notification(
        {"email": "member@example.com", "telegram_chat_id": ""},
        "Arc_Tic Whale Trade Alert",
        "hello world",
    )

    assert result["email"] is True
    assert result["telegram"] is False
    assert len(calls) == 1
    assert calls[0][0] == "https://api.resend.com/emails"


def test_social_posts_and_assistant_commands(monkeypatch, tmp_path):
    _use_temp_db(monkeypatch, tmp_path)

    db.upsert_user_wallet(
        user_id="user_3",
        wallet_id="wallet_3",
        wallet_address="0x123",
        referral_code="ref_user_3",
        email="helper@example.com",
    )
    db.add_follower("user_3", "wallet_3", "Conservative_Whale", 40.0, asset="USDC", user_wallet_address="0x123")
    db.log_trade(agent="Follower:wallet_3", action="BUY", asset="BTC", tx_id="0xabc", reason="Momentum")
    db.log_social_post(agent="Conservative_Whale", action="BUY", post_text="Arc_Tic Whale is buying BTC.", tx_id="0xabc", reason="Momentum")

    posts = db.get_social_posts(limit=5)
    assert len(posts) == 1
    assert posts[0]["post_text"] == "Arc_Tic Whale is buying BTC."
    latest_trade = db.get_latest_trade("Follower:wallet_3")
    assert latest_trade["action"] == "BUY"

    monkeypatch.setattr(assistant, "get_wallet_stats_safe", lambda wallet_id: {
        "total_balance_usd": 40.0,
        "token_balances": [{"symbol": "USDC", "name": "USD Coin", "amount": "40.0", "decimals": 6}],
        "performance": {"24h": "2.00%", "7d": "4.00%", "1y": "9.00%"},
    })

    pnl = assistant.handle_assistant_command("user_3", "What is my P&L?")
    assert pnl["status"] == "success"
    assert "40.00 USDC" in pnl["reply"]

    alerts = assistant.handle_assistant_command("user_3", "Turn trade alerts off")
    assert alerts["status"] == "success"
    assert db.get_user_preferences("user_3")["trade_alerts"] == 0

    detach = assistant.handle_assistant_command("user_3", "Detach me from Arc_Tic Whale")
    assert detach["status"] == "success"
    assert db.get_follower_summary("Conservative_Whale")["total_followers"] == 0


def test_copy_engine_alerts_fire_for_each_active_follower(monkeypatch):
    import backend.copy_engine as copy_engine

    notified = []

    monkeypatch.setattr(copy_engine, "get_active_followers", lambda agent_name: [("wallet_a", "0x1", 42.0, 10.0, 42.0)])
    monkeypatch.setattr(copy_engine, "get_active_follower_rows", lambda agent_name: [])
    monkeypatch.setattr(copy_engine, "adjust_follower_remaining_capital", lambda *a, **k: None)
    monkeypatch.setattr(copy_engine, "get_current_market_state", lambda: {"BTC": {"PRICE": 68000}})
    monkeypatch.setattr(copy_engine, "execute_trade", lambda **kwargs: "tx_123")
    monkeypatch.setattr(copy_engine, "log_trade", lambda **kwargs: None)
    monkeypatch.setattr(copy_engine, "get_follower_by_wallet_id", lambda agent_name, wallet_id: {"user_id": "user_alert"})
    monkeypatch.setattr(copy_engine, "get_latest_follower_trade", lambda wallet_id, target_agent=None: None)
    monkeypatch.setattr(copy_engine, "get_user", lambda user_id: {
        "user_id": "user_alert",
        "email": "alert@example.com",
        "telegram_chat_id": "",
        "trade_alerts": 1,
        "daily_summary": 1,
    })
    monkeypatch.setattr(copy_engine, "notify_trade_alert", lambda *args, **kwargs: notified.append((args, kwargs)) or {"email": True, "telegram": False})
    monkeypatch.setattr(copy_engine, "build_notification_reminder", lambda user: "")

    copy_engine.mirror_agent_trade("Conservative_Whale", "BUY", "BTC")

    assert len(notified) == 1
    _, kwargs = notified[0]
    assert kwargs["agent_name"] == "Arc_Tic Whale"
    # Deployed notional, not the raw allocation: every entry deploys 10%
    # of the follower's remaining capital pool (42.0 -> 4.2).
    assert kwargs["amount_usdc"] == 4.2
    assert kwargs["token"] == "BTC"


def test_stop_loss_triggers_auto_sell_and_detach(monkeypatch):
    import backend.copy_engine as copy_engine

    actions = []

    monkeypatch.setattr(copy_engine, "get_active_follower_rows", lambda agent_name: [{
        "user_id": "user_stop",
        "user_wallet_id": "wallet_stop",
        "user_wallet_address": "0x9",
        "target_agent": agent_name,
        "allocation_amount": 33.0,
        "asset": "USDC",
        "stop_loss_pct": 10.0,
        "is_active": 1,
    }])
    monkeypatch.setattr(copy_engine, "get_latest_follower_trade", lambda wallet_id, target_agent=None: {"action": "BUY", "asset": "BTC", "timestamp": "2026-05-24T12:00:00", "amount_usdc": 3.3, "price": 68000})
    monkeypatch.setattr(copy_engine, "get_current_market_state", lambda: {"BTC": {"PRICE": 60000, "24H_CHANGE": "-12.5%"}})
    monkeypatch.setattr(copy_engine, "adjust_follower_remaining_capital", lambda *a, **k: None)
    monkeypatch.setattr(copy_engine, "execute_trade", lambda **kwargs: actions.append(("execute", kwargs)) or "tx_stop")
    monkeypatch.setattr(copy_engine, "log_trade", lambda **kwargs: actions.append(("log", kwargs)))
    monkeypatch.setattr(copy_engine, "deactivate_follower", lambda user_id, target_agent: actions.append(("detach", user_id, target_agent)) or True)
    monkeypatch.setattr(copy_engine, "get_user", lambda user_id: {"user_id": user_id, "email": "stop@example.com", "telegram_chat_id": "", "trade_alerts": 1, "daily_summary": 1})
    monkeypatch.setattr(copy_engine, "notify_trade_alert", lambda *args, **kwargs: actions.append(("notify", kwargs)) or {"email": True, "telegram": False})
    monkeypatch.setattr(copy_engine, "build_notification_reminder", lambda user: "")

    stopped = copy_engine.evaluate_stop_losses("Conservative_Whale")

    assert stopped == ["wallet_stop"]
    assert any(kind == "execute" for kind, _ in [(a[0], a[1]) for a in actions if a[0] == "execute"])
    assert any(entry[0] == "detach" for entry in actions)
    assert any(entry[0] == "notify" for entry in actions)


def test_daily_summary_job_filters_by_preferences_and_channel(monkeypatch):
    import backend.daily_summary as daily_summary
    from datetime import datetime
    from zoneinfo import ZoneInfo

    # Pin 'now' to the same day as the mocked trade timestamp so the date filter matches.
    fixed_now = datetime(2026, 5, 24, 19, 0, 0, tzinfo=ZoneInfo("Africa/Lagos"))

    sent = []
    reminders = []

    monkeypatch.setattr(daily_summary, "get_all_users", lambda: [
        {
            "user_id": "user_daily",
            "wallet_id": "wallet_daily",
            "email": "daily@example.com",
            "telegram_chat_id": "",
            "trade_alerts": 0,
            "daily_summary": 1,
        },
        {
            "user_id": "user_skip",
            "wallet_id": "wallet_skip",
            "email": "",
            "telegram_chat_id": "",
            "trade_alerts": 0,
            "daily_summary": 1,
        },
    ])
    monkeypatch.setattr(daily_summary, "get_user_allocations", lambda user_id: [{"allocation_amount": 40.0, "target_agent": "Conservative_Whale"}] if user_id == "user_daily" else [])
    monkeypatch.setattr(daily_summary, "get_wallet_stats_safe", lambda wallet_id: {"total_balance_usd": 100.0})
    monkeypatch.setattr(daily_summary, "get_follower_performance", lambda user_id, agent_name: {"24h": "5.0%"})
    monkeypatch.setattr(daily_summary, "get_follower_trade_history", lambda wallet_id, **kwargs: [{"timestamp": "2026-05-24T08:00:00"}])
    monkeypatch.setattr(daily_summary, "notify_daily_summary", lambda user, **kwargs: sent.append((user, kwargs)) or {"email": True, "telegram": False})
    monkeypatch.setattr(daily_summary, "user_has_notification_channel", lambda user: bool(user.get("email") or user.get("telegram_chat_id")))
    monkeypatch.setattr(daily_summary, "build_notification_reminder", lambda user: reminders.append(user["user_id"]) or "Add an email in Profile or connect Telegram to receive trade alerts and daily summaries.")

    result = daily_summary.send_daily_summary_reports(now=fixed_now)

    assert len(result) == 1
    assert len(sent) == 1
    assert sent[0][0]["user_id"] == "user_daily"
    assert sent[0][1]["trades_executed"] == 1
    assert sent[0][1]["free_usdc"] == 60.0
    assert "user_skip" in reminders
