import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from config import BOT_NAME
from admin import is_admin
from database import init_db, upsert_user, get_user, all_users, set_blocked, stats
from payments import admin_credit, admin_debit

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing.")

def menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👤 Profile", callback_data="profile"),
         InlineKeyboardButton("💰 Balance", callback_data="balance")],
        [InlineKeyboardButton("👥 Referral", callback_data="referral"),
         InlineKeyboardButton("ℹ️ Help", callback_data="help")]
    ])

async def start(update, context):
    user = update.effective_user
    ref = None
    if context.args and context.args[0].startswith("ref_"):
        x = context.args[0][4:]
        if x.isdigit() and int(x) != user.id:
            ref = int(x)
    upsert_user(user, ref)
    row = get_user(user.id)
    if row["blocked"]:
        await update.message.reply_text("❌ আপনার অ্যাকাউন্টটি ব্লক করা আছে।")
        return
    await update.message.reply_text(
        f"🤖 {BOT_NAME}\n\nস্বাগতম! নিচের মেনু ব্যবহার করুন।",
        reply_markup=menu()
    )

async def help_cmd(update, context):
    await update.message.reply_text(
        "/start - Start\n/profile - Profile\n/balance - Balance\n"
        "/referral - Referral\n/help - Help\n\n"
        "Admin:\n/stats\n/users\n/credit USER_ID AMOUNT\n"
        "/debit USER_ID AMOUNT\n/block USER_ID\n/unblock USER_ID\n"
        "/broadcast TEXT"
    )

async def profile(update, context):
    u = update.effective_user
    upsert_user(u)
    r = get_user(u.id)
    await update.message.reply_text(
        f"👤 Profile\nID: {u.id}\nUsername: @{u.username or 'none'}\n"
        f"Balance: {float(r['balance']):.2f}"
    )

async def balance(update, context):
    u = update.effective_user
    upsert_user(u)
    r = get_user(u.id)
    await update.message.reply_text(f"💰 Balance: {float(r['balance']):.2f}")

async def referral(update, context):
    u = update.effective_user
    upsert_user(u)
    me = await context.bot.get_me()
    await update.message.reply_text(
        f"👥 Referral Link:\nhttps://t.me/{me.username}?start=ref_{u.id}"
    )

def admin_only(update):
    return is_admin(update.effective_user)

async def stats_cmd(update, context):
    if not admin_only(update):
        await update.message.reply_text("❌ Admin permission required.")
        return
    total, blocked, bal = stats()
    await update.message.reply_text(
        f"📊 Users: {total}\nBlocked: {blocked}\nTotal balance: {bal:.2f}"
    )

async def users_cmd(update, context):
    if not admin_only(update):
        await update.message.reply_text("❌ Admin permission required.")
        return
    rows = all_users()[:30]
    text = ["👥 Latest users:"]
    for r in rows:
        text.append(
            f"{r['telegram_id']} | @{r['username'] or '-'} | "
            f"{float(r['balance']):.2f} | "
            f"{'BLOCKED' if r['blocked'] else 'ACTIVE'}"
        )
    await update.message.reply_text("\n".join(text) if rows else "No users.")

async def credit_cmd(update, context):
    if not admin_only(update):
        await update.message.reply_text("❌ Admin permission required.")
        return
    if len(context.args) != 2 or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /credit USER_ID AMOUNT")
        return
    ok, bal = admin_credit(int(context.args[0]), float(context.args[1]))
    await update.message.reply_text(
        f"✅ New balance: {bal:.2f}" if ok else "❌ User not found/invalid amount."
    )

async def debit_cmd(update, context):
    if not admin_only(update):
        await update.message.reply_text("❌ Admin permission required.")
        return
    if len(context.args) != 2 or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /debit USER_ID AMOUNT")
        return
    ok, bal = admin_debit(int(context.args[0]), float(context.args[1]))
    await update.message.reply_text(
        f"✅ New balance: {bal:.2f}" if ok else "❌ User not found/insufficient balance."
    )

async def block_cmd(update, context):
    if not admin_only(update):
        await update.message.reply_text("❌ Admin permission required.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /block USER_ID")
        return
    set_blocked(int(context.args[0]), True)
    await update.message.reply_text("✅ User blocked.")

async def unblock_cmd(update, context):
    if not admin_only(update):
        await update.message.reply_text("❌ Admin permission required.")
        return
    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /unblock USER_ID")
        return
    set_blocked(int(context.args[0]), False)
    await update.message.reply_text("✅ User unblocked.")

async def broadcast_cmd(update, context):
    if not admin_only(update):
        await update.message.reply_text("❌ Admin permission required.")
        return
    msg = " ".join(context.args).strip()
    if not msg:
        await update.message.reply_text("Usage: /broadcast TEXT")
        return
    sent = failed = 0
    for r in all_users():
        if r["blocked"]:
            continue
        try:
            await context.bot.send_message(r["telegram_id"], msg)
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"📢 Sent: {sent}\nFailed: {failed}")

async def callback(update, context):
    q = update.callback_query
    await q.answer()
    u = q.from_user
    upsert_user(u)
    r = get_user(u.id)
    if q.data == "profile":
        await q.edit_message_text(
            f"👤 ID: {u.id}\nUsername: @{u.username or 'none'}\n"
            f"Balance: {float(r['balance']):.2f}"
        )
    elif q.data == "balance":
        await q.edit_message_text(f"💰 Balance: {float(r['balance']):.2f}")
    elif q.data == "referral":
        me = await context.bot.get_me()
        await q.edit_message_text(
            f"https://t.me/{me.username}?start=ref_{u.id}"
        )
    else:
        await q.edit_message_text("Use /help to see commands.")

def main():
    init_db()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("profile", profile))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("referral", referral))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("users", users_cmd))
    app.add_handler(CommandHandler("credit", credit_cmd))
    app.add_handler(CommandHandler("debit", debit_cmd))
    app.add_handler(CommandHandler("block", block_cmd))
    app.add_handler(CommandHandler("unblock", unblock_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CallbackQueryHandler(callback))
    log.info("RJ OTP Universe Bot started")
    app.run_polling()

if __name__ == "__main__":
    main()
