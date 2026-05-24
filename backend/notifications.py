from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import httpx

from backend.config import BOT_TOKEN, RESEND_API_KEY, RESEND_FROM_EMAIL
from backend.logger import get_logger

log = get_logger("notifications")


def _clean_text(value: object, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text or fallback


def user_has_email(user: dict | None) -> bool:
    return bool(_clean_text((user or {}).get("email")))


def user_has_telegram(user: dict | None) -> bool:
    chat_id = _clean_text((user or {}).get("telegram_chat_id"))
    return bool(chat_id)


def user_has_notification_channel(user: dict | None) -> bool:
    return user_has_email(user) or user_has_telegram(user)


def build_notification_reminder(user: dict | None) -> str:
    if user_has_notification_channel(user):
        return ""
    user = user or {}
    alerts_on = int(user.get("trade_alerts") or 0) == 1
    summary_on = int(user.get("daily_summary") or 0) == 1
    if not (alerts_on or summary_on):
        return ""
    return "Add an email in Profile or connect Telegram to receive trade alerts and daily summaries."


def build_trade_alert_message(
    agent_name: str,
    action: str,
    token: str,
    amount_usdc: float | int | str,
    entry_price: float | int | str,
    timestamp: str | None = None,
) -> str:
    time_label = _clean_text(timestamp, datetime.now(timezone.utc).isoformat())
    amount = f"${float(amount_usdc):,.2f}" if str(amount_usdc).replace(".", "", 1).isdigit() else f"${amount_usdc}"
    entry = f"${float(entry_price):,.2f}" if str(entry_price).replace(".", "", 1).isdigit() else f"${entry_price}"
    return (
        "🚨 Trade Alert — Arc_Tic_Whale\n"
        f"Agent: {agent_name}\n"
        f"Action: {action} {token}\n"
        f"Amount: {amount} USDC\n"
        f"Entry Price: {entry}\n"
        f"Time: {time_label}"
    )


def build_daily_summary_message(
    date_label: str,
    trades_executed: int,
    pnl_amount: float | int | str,
    pnl_pct: float | int | str,
    free_usdc: float | int | str,
    active_agents: int,
) -> str:
    pnl_value = float(pnl_amount)
    pct_value = float(pnl_pct)
    sign = "+" if pnl_value >= 0 else "-"
    return (
        "📊 Daily Summary — Arc_Tic_Whale\n"
        f"Date: {date_label}\n"
        f"Trades executed today: {int(trades_executed)}\n"
        f"Today's P&L: {sign}${abs(pnl_value):,.2f} ({sign}{abs(pct_value):.2f}%)\n"
        f"Free USDC remaining: ${float(free_usdc):,.2f}\n"
        f"Active agents: {int(active_agents)}"
    )


def _send_resend_email(to_email: str, subject: str, text: str) -> bool:
    if not RESEND_API_KEY or not to_email:
        return False

    payload = {
        "from": RESEND_FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "text": text,
        "html": "<pre style='font-family:monospace;white-space:pre-wrap'>{}</pre>".format(
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        ),
    }
    try:
        with httpx.Client(timeout=12) as client:
            response = client.post(
                "https://api.resend.com/emails",
                json=payload,
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
            )
        if response.status_code in (200, 201):
            return True
        log.warning("Resend returned %s: %s", response.status_code, response.text[:300])
    except Exception as exc:
        log.warning("Resend send failed: %s", exc)
    return False


def _send_telegram_message(chat_id: str, text: str) -> bool:
    if not BOT_TOKEN or not chat_id:
        return False
    try:
        with httpx.Client(timeout=12) as client:
            response = client.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                    "disable_web_page_preview": True,
                },
            )
        if response.status_code in (200, 201):
            return True
        log.warning("Telegram returned %s: %s", response.status_code, response.text[:300])
    except Exception as exc:
        log.warning("Telegram send failed: %s", exc)
    return False


def send_notification(user: dict | None, subject: str, text: str) -> dict:
    result = {"email": False, "telegram": False}
    user = user or {}
    email = _clean_text(user.get("email"))
    chat_id = _clean_text(user.get("telegram_chat_id"))
    if email:
        result["email"] = _send_resend_email(email, subject, text)
    if chat_id:
        result["telegram"] = _send_telegram_message(chat_id, text)
    return result


def notify_trade_alert(
    user: dict | None,
    agent_name: str,
    action: str,
    token: str,
    amount_usdc: float | int | str,
    entry_price: float | int | str,
    timestamp: str | None = None,
) -> dict:
    subject = "Arc_Tic_Whale Trade Alert"
    text = build_trade_alert_message(agent_name, action, token, amount_usdc, entry_price, timestamp)
    return send_notification(user, subject, text)


def notify_daily_summary(
    user: dict | None,
    date_label: str,
    trades_executed: int,
    pnl_amount: float | int | str,
    pnl_pct: float | int | str,
    free_usdc: float | int | str,
    active_agents: int,
) -> dict:
    subject = "Arc_Tic_Whale Daily Summary"
    text = build_daily_summary_message(date_label, trades_executed, pnl_amount, pnl_pct, free_usdc, active_agents)
    return send_notification(user, subject, text)


def iter_notification_targets(users: Iterable[dict]) -> list[dict]:
    return [user for user in users if user_has_notification_channel(user)]
