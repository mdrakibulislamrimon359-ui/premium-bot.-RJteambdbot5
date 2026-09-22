import os
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# ============================================================
# RJ TEAM BANGLADESH - PREMIUM AI BOT
# Fixed version:
# - Does NOT depend on Application.job_queue
# - Fixes: AttributeError: 'NoneType' object has no attribute 'run_daily'
# - Keeps daily auto-post at 08:00 Asia/Dhaka
# ============================================================

# -------------------- Environment --------------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID", "").strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "RJteam1").strip().lstrip("@")
PORT = int(os.getenv("PORT", "10000"))

BD = ZoneInfo("Asia/Dhaka")

# -------------------- Optional Gemini --------------------

client = None
if GEMINI_API_KEY:
    try:
        from google import genai
        client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"[WARNING] Gemini initialization failed: {e}")
        client = None
else:
    print("[WARNING] GEMINI_API_KEY is not set.")

# -------------------- Optional Firebase --------------------

db = None

try:
    import firebase_admin
    from firebase_admin import credentials, firestore

    if os.path.exists("serviceAccountKey.json"):
        if not firebase_admin._apps:
            firebase_admin.initialize_app(
                credentials.Certificate("serviceAccountKey.json")
            )
        db = firestore.client()
        print("[INFO] Firebase connected.")
    else:
        print("[INFO] serviceAccountKey.json not found. Firebase disabled.")
except Exception as e:
    db = None
    print(f"[WARNING] Firebase disabled: {e}")


# -------------------- Render Health Server --------------------

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"RJ Team Bangladesh Premium Bot Running")

    def log_message(self, format, *args):
        # Keep Render logs clean.
        return


def start_health_server():
    try:
        server = HTTPServer(("0.0.0.0", PORT), Handler)
        print(f"[INFO] Health server running on port {PORT}")
        server.serve_forever()
    except Exception as e:
        print(f"[WARNING] Health server stopped: {e}")


threading.Thread(target=start_health_server, daemon=True).start()


# -------------------- Helpers --------------------

async def save_user(user):
    if not db or not user:
        return

    try:
        data = {
            "id": user.id,
            "name": user.full_name or "",
            "username": user.username or "",
        }

        # Firestore is synchronous, so keep it outside the bot event loop.
        await asyncio.to_thread(
            db.collection("users").document(str(user.id)).set,
            data,
        )
    except Exception as e:
        print(f"[WARNING] Could not save user: {e}")


def is_admin(update: Update):
    user = update.effective_user
    if not user or not user.username:
        return False

    return user.username.lower() == ADMIN_USERNAME.lower()


async def get_user_count():
    if not db:
        return 0

    try:
        docs = await asyncio.to_thread(
            lambda: list(db.collection("users").stream())
        )
        return len(docs)
    except Exception as e:
        print(f"[WARNING] Could not count users: {e}")
        return 0


# -------------------- Commands --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user:
        await save_user(update.effective_user)

    kb = [
        [InlineKeyboardButton("🤖 AI Chat", callback_data="ai")],
        [InlineKeyboardButton("👑 Admin", callback_data="admin")],
        [InlineKeyboardButton("ℹ️ About", callback_data="about")],
    ]

    await update.message.reply_text(
        "🤖 RJ Team Bangladesh Premium AI Bot\n\n"
        "স্বাগতম! আপনার প্রশ্ন লিখুন, আমি AI দিয়ে উত্তর দেওয়ার চেষ্টা করব।",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("⛔ Admin only.")
        return

    await update.message.reply_text(
        "👑 Admin Commands:\n\n"
        "/status - Bot status\n"
        "/users - Total users\n"
        "/broadcast your message - Send message to users\n"
        "/about - Bot information"
    )


async def about_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 RJ Team Bangladesh Premium AI Bot\n\n"
        "• AI Chat\n"
        "• Firebase user storage (optional)\n"
        "• Admin commands\n"
        "• Daily auto post\n"
        "• Render health server\n\n"
        "Powered by Telegram + Gemini."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "/start\n"
        "/help\n"
        "/status\n"
        "/admin\n"
        "/users\n"
        "/broadcast\n"
        "/about"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    total = await get_user_count()

    firebase_status = "Connected" if db else "Disabled"
    gemini_status = "Connected" if client else "Disabled"

    await update.message.reply_text(
        "✅ Bot Online\n"
        f"👥 Users: {total}\n"
        f"🔥 Firebase: {firebase_status}\n"
        f"🤖 Gemini: {gemini_status}"
    )


async def users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("⛔ Admin only.")
        return

    total = await get_user_count()
    await update.message.reply_text(f"👥 Total Users: {total}")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text("⛔ Admin only.")
        return

    if not db:
        await update.message.reply_text(
            "❌ Firebase not configured. "
            "Add serviceAccountKey.json to enable broadcast."
        )
        return

    msg = " ".join(context.args).strip()

    if not msg:
        await update.message.reply_text(
            "ব্যবহার:\n/broadcast আপনার মেসেজ"
        )
        return

    try:
        docs = await asyncio.to_thread(
            lambda: list(db.collection("users").stream())
        )
    except Exception as e:
        print(f"[ERROR] Broadcast user fetch failed: {e}")
        await update.message.reply_text("❌ Could not load users.")
        return

    sent = 0
    failed = 0

    for doc in docs:
        try:
            await context.bot.send_message(
                chat_id=int(doc.id),
                text=msg,
            )
            sent += 1
        except Exception as e:
            failed += 1
            print(f"[WARNING] Broadcast failed for {doc.id}: {e}")

    await update.message.reply_text(
        "📢 Broadcast complete.\n\n"
        f"✅ Sent: {sent}\n"
        f"❌ Failed: {failed}"
    )


# -------------------- Buttons --------------------

async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "ai":
        await query.edit_message_text(
            "💬 আপনার প্রশ্ন লিখুন।\n"
            "আমি Gemini AI দিয়ে উত্তর দেওয়ার চেষ্টা করব।"
        )

    elif query.data == "admin":
        if not is_admin(update):
            await query.edit_message_text("⛔ Admin only.")
            return

        await query.edit_message_text(
            "👑 Admin Commands:\n\n"
            "/status\n"
            "/users\n"
            "/broadcast আপনার মেসেজ\n"
            "/about"
        )

    elif query.data == "about":
        await query.edit_message_text(
            "🤖 RJ Team Bangladesh Premium AI Bot\n\n"
            "AI Chat • Firebase • Admin • Daily Auto Post"
        )


# -------------------- AI --------------------

async def ai_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user:
        await save_user(update.effective_user)

    text = (update.message.text or "").strip()

    if not text:
        return

    if not client:
        await update.message.reply_text(
            "⚠️ Gemini AI এখন configured নেই।\n"
            "Render Environment Variables-এ GEMINI_API_KEY সেট করুন।"
        )
        return

    try:
        # google-genai SDK call is synchronous, so run it in a worker thread.
        result = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=text,
        )

        answer = getattr(result, "text", None)

        if not answer:
            answer = "দুঃখিত, AI কোনো উত্তর দিতে পারেনি।"

        await update.message.reply_text(answer)

    except Exception as e:
        print(f"[ERROR] Gemini error: {e}")
        await update.message.reply_text(
            "❌ AI Error\n"
            "কিছুক্ষণ পরে আবার চেষ্টা করুন।"
        )


# -------------------- Media --------------------

async def photo_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user:
        await save_user(update.effective_user)

    await update.message.reply_text("📷 ছবি পেয়েছি।")


async def video_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user:
        await save_user(update.effective_user)

    await update.message.reply_text("🎥 ভিডিও পেয়েছি।")


# -------------------- Daily Auto Post --------------------
# IMPORTANT:
# We intentionally do NOT use app.job_queue.run_daily().
# That was the source of:
# AttributeError: 'NoneType' object has no attribute 'run_daily'
#
# This implementation uses a normal asyncio task instead, so the
# bot works even when python-telegram-bot's optional job-queue extra
# is not installed.

async def auto_post(bot):
    if not TARGET_CHAT_ID:
        print("[INFO] TARGET_CHAT_ID is not set. Daily post skipped.")
        return

    try:
        await bot.send_message(
            chat_id=TARGET_CHAT_ID,
            text="✅ Daily Auto Post",
        )
        print("[INFO] Daily auto post sent.")
    except Exception as e:
        print(f"[WARNING] Daily auto post failed: {e}")


async def daily_auto_post_loop(app: Application):
    while True:
        try:
            now = datetime.now(BD)

            next_run = datetime.combine(
                now.date(),
                time(hour=8, minute=0, tzinfo=BD),
            )

            # If today's 08:00 has already passed, schedule tomorrow.
            if now >= next_run:
                next_run += timedelta(days=1)

            seconds = max(
                1,
                (next_run - now).total_seconds(),
            )

            print(
                f"[INFO] Next daily auto post: "
                f"{next_run.strftime('%Y-%m-%d %H:%M:%S %Z')}"
            )

            await asyncio.sleep(seconds)

            await auto_post(app.bot)

        except asyncio.CancelledError:
            print("[INFO] Daily auto-post task stopped.")
            raise

        except Exception as e:
            print(f"[WARNING] Daily auto-post loop error: {e}")
            await asyncio.sleep(60)


async def post_init(app: Application):
    # Start daily scheduler without JobQueue.
    task = app.create_task(
        daily_auto_post_loop(app),
        name="rj_daily_auto_post",
    )
    app.bot_data["daily_auto_post_task"] = task


async def post_shutdown(app: Application):
    task = app.bot_data.get("daily_auto_post_task")

    if task and not task.done():
        task.cancel()

        try:
            await task
        except asyncio.CancelledError:
            pass


# -------------------- Error Handler --------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    print(f"[ERROR] Telegram update error: {context.error}")


# -------------------- Main --------------------

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is not set. "
            "Add BOT_TOKEN to Render Environment Variables."
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("admin", admin_command))
    app.add_handler(CommandHandler("users", users))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CommandHandler("about", about_command))

    # Buttons
    app.add_handler(CallbackQueryHandler(buttons))

    # Media
    app.add_handler(MessageHandler(filters.PHOTO, photo_reply))
    app.add_handler(MessageHandler(filters.VIDEO, video_reply))

    # Normal text -> AI
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            ai_reply,
        )
    )

    app.add_error_handler(error_handler)

    print("========================================")
    print(" RJ Team Bangladesh Premium Bot")
    print(" Bot Started Successfully")
    print(" Daily post time: 08:00 Asia/Dhaka")
    print(" JobQueue dependency NOT required")
    print("========================================")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
