"""
telegram_bot.py — Telegram bot logic, delivered via webhook rather than
long-polling.

Long-polling needs an always-running process, which Render only offers on
paid Background Worker plans. A webhook is just an HTTP route Telegram POSTs
to — it rides on the already-deployed, already-free arctic-whale-api web
service instead of needing a separate worker, and it wakes correctly from
Render's free-tier sleep on the next incoming message (see
server/api.py's POST /telegram-webhook route and lifespan startup call to
ensure_webhook_registered()).
"""
from __future__ import annotations

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo

from backend.config import BOT_TOKEN, PYTHON_BACKEND_URL, TELEGRAM_WEBHOOK_SECRET, WEBAPP_URL
from backend.logger import get_logger
from backend.user_wallets import ensure_user_wallet

log = get_logger("telegram_bot")

bot: telebot.TeleBot | None = telebot.TeleBot(BOT_TOKEN) if BOT_TOKEN else None


def _telegram_user_id(message):
    user = message.from_user
    return user.username or f"tg_{user.id}"


def _start_referral_code(message):
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else None


if bot is not None:
    @bot.message_handler(commands=["start"])
    def send_welcome(message):
        username = _telegram_user_id(message)
        referral_code = _start_referral_code(message)
        log.info("User %s sent /start", username)

        wallet = ensure_user_wallet(
            username,
            referral_code=referral_code,
            telegram_chat_id=str(message.chat.id),
            display_name=message.from_user.first_name or message.from_user.username or "Telegram User",
        )
        if not wallet or not wallet.get("user_id"):
            bot.send_message(
                message.chat.id,
                "Sorry, we couldn't set up your wallet right now. Please try /start again in a moment."
            )
            return

        webapp_url = WEBAPP_URL
        separator = "&" if "?" in webapp_url else "?"
        webapp_url = f"{webapp_url}{separator}username={wallet['user_id']}"

        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton(
            text="Open Copy Trading",
            web_app=WebAppInfo(url=webapp_url)
        ))

        welcome_text = (
            "Welcome to *Arc\\_Tic Whale* — an AI-driven copy-trading app on Arc Testnet.\n\n"
            "Follow automated investing agents and mirror their trades from your wallet.\n\n"
            "Open the dashboard below to allocate capital and enable copy trading.\n\n"
            f"Referral code: `{wallet.get('referral_code', 'pending')}`"
        )

        bot.reply_to(message, welcome_text, reply_markup=markup, parse_mode="Markdown")


def process_webhook_update(update_json: dict) -> None:
    """Parse one Telegram update and dispatch it to the registered handlers.
    Never raises — Telegram expects a fast 200 regardless of what happened
    inside, and a bug here shouldn't take down the request or trigger
    Telegram's retry storm."""
    if bot is None:
        log.warning("Received a Telegram webhook update but no BOT_TOKEN is configured — ignoring.")
        return
    try:
        update = telebot.types.Update.de_json(update_json)
        bot.process_new_updates([update])
    except Exception as exc:
        log.error("Failed to process Telegram webhook update: %s", exc)


def ensure_webhook_registered() -> None:
    """Registers this service's /telegram-webhook URL with Telegram. Safe to
    call on every boot — idempotent, and skips cleanly (rather than crashing
    startup) when there's no bot configured or the backend URL isn't
    publicly reachable (local dev)."""
    if bot is None:
        log.info("Skipping Telegram webhook registration: BOT_TOKEN not configured.")
        return
    if "localhost" in PYTHON_BACKEND_URL or "127.0.0.1" in PYTHON_BACKEND_URL:
        log.info("Skipping Telegram webhook registration: PYTHON_BACKEND_URL (%s) isn't publicly reachable.", PYTHON_BACKEND_URL)
        return
    if not TELEGRAM_WEBHOOK_SECRET:
        log.warning("TELEGRAM_WEBHOOK_SECRET is not set — the webhook endpoint will accept unauthenticated requests.")

    webhook_url = f"{PYTHON_BACKEND_URL.rstrip('/')}/telegram-webhook"
    try:
        bot.set_webhook(url=webhook_url, secret_token=TELEGRAM_WEBHOOK_SECRET or None)
        log.info("Telegram webhook registered at %s", webhook_url)
    except Exception as exc:
        log.error("Failed to register Telegram webhook: %s", exc)
