import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import time
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)

# Optional Firebase
db = None
try:
    import firebase_admin
    from firebase_admin import credentials, firestore
    if os.path.exists("serviceAccountKey.json"):
        firebase_admin.initialize_app(credentials.Certificate("serviceAccountKey.json"))
        db = firestore.client()
except Exception:
    db = None

from google import genai

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "RJteam1")
client = genai.Client(api_key=GEMINI_API_KEY)
BD = ZoneInfo("Asia/Dhaka")

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"RJ Team Bangladesh Bot Running")

threading.Thread(
    target=lambda: HTTPServer(("0.0.0.0", int(os.getenv("PORT","10000"))), Handler).serve_forever(),
    daemon=True
).start()

async def save_user(user):
    if db:
        db.collection("users").document(str(user.id)).set({
            "id": user.id,
            "name": user.full_name,
            "username": user.username or ""
        })

def is_admin(update):
    return (update.effective_user.username or "") == ADMIN_USERNAME

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await save_user(update.effective_user)
    kb = [[InlineKeyboardButton("🤖 AI Chat", callback_data="ai")],
          [InlineKeyboardButton("👑 Admin", callback_data="admin")],
          [InlineKeyboardButton("ℹ️ About", callback_data="about")]]
    await update.message.reply_text(
        "🤖 RJ Team Bangladesh Premium AI Bot\n\nWelcome!",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "ai":
        await q.edit_message_text("💬 প্রশ্ন লিখুন।")
    elif q.data == "admin":
        await q.edit_message_text("👑 Admin Commands:\n/status\n/users\n/broadcast")
    else:
        await q.edit_message_text("RJ Team Bangladesh Premium Bot")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
"""/start
/help
/status
/admin
/users
/broadcast
/about"""
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = 0
    if db:
        total = len(list(db.collection("users").stream()))
    await update.message.reply_text(f"✅ Online\n👥 Users: {total}")

async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await update.message.reply_text("Admin only.")
    total = 0
    if db:
        total = len(list(db.collection("users").stream()))
    await update.message.reply_text(f"Users: {total}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return await update.message.reply_text("Admin only.")
    if not db:
        return await update.message.reply_text("Firebase not configured.")
    msg = " ".join(context.args)
    if not msg:
        return await update.message.reply_text("Use: /broadcast your message")
    for doc in db.collection("users").stream():
        try:
            await context.bot.send_message(int(doc.id), msg)
        except:
            pass
    await update.message.reply_text("Broadcast complete.")

async def ai_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await save_user(update.effective_user)
    try:
        res = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=update.message.text
        )
        await update.message.reply_text(res.text)
    except Exception:
        await update.message.reply_text("AI Error")

async def photo_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📷 ছবি পেয়েছি।")

async def video_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎥 ভিডিও পেয়েছি।")

async def auto_post(context: ContextTypes.DEFAULT_TYPE):
    if TARGET_CHAT_ID:
        await context.bot.send_message(TARGET_CHAT_ID, "✅ Daily Auto Post")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("users", users))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.PHOTO, photo_reply))
    app.add_handler(MessageHandler(filters.VIDEO, video_reply))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_reply))
    app.job_queue.run_daily(auto_post, time=time(hour=8, minute=0, tzinfo=BD))
    print("Bot Started")
    app.run_polling()

if __name__ == "__main__":
    main()
