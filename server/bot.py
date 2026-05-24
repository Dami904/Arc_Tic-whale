import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from backend.config import BOT_TOKEN, WEBAPP_URL
from backend.database import init_db
from backend.user_wallets import ensure_user_wallet

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not configured")

bot = telebot.TeleBot(BOT_TOKEN)
init_db()


def _telegram_user_id(message):
    user = message.from_user
    return user.username or f"tg_{user.id}"


def _start_referral_code(message):
    parts = (message.text or "").split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else None

@bot.message_handler(commands=['start'])
def send_welcome(message):
    username = _telegram_user_id(message)
    referral_code = _start_referral_code(message)
    print(f"🔔 User {username} sent /start")

    wallet = ensure_user_wallet(
        username,
        referral_code=referral_code,
        telegram_chat_id=str(message.chat.id),
        display_name=message.from_user.first_name or message.from_user.username or "Telegram User",
    )
    if not wallet or not wallet.get('user_id'):
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

print("🤖 Telegram Bot is listening for /start commands...")
bot.polling()
