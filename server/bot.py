import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from backend.config import BOT_TOKEN, WEBAPP_URL

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not configured")

bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start'])
def send_welcome(message):
    print(f"🔔 User {message.from_user.username} sent /start")
    
    # Create an inline keyboard with the WebApp attachment
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton(
        text="🐋 Launch Whale Terminal", 
        web_app=WebAppInfo(url=WEBAPP_URL)
    ))
    
    welcome_text = (
        "Welcome to the **Agora Crypto Network**.\n\n"
        "I am the Conservative Whale. I track market dips and accumulate blue-chip assets on the Arc Testnet.\n\n"
        "Click below to open your vault and follow my trades."
    )
    
    bot.reply_to(message, welcome_text, reply_markup=markup, parse_mode="Markdown")

print("🤖 Telegram Bot is listening for /start commands...")
bot.polling()
