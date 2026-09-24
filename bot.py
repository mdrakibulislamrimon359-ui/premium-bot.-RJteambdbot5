# Main Telegram bot
import os
from telegram.ext import Application
BOT_TOKEN=os.getenv("BOT_TOKEN")
app=Application.builder().token(BOT_TOKEN).build()
print("RJ OTP Universe Bot")
app.run_polling()
