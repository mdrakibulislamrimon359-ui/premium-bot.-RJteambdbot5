import os
import asyncio
import logging
import random
import json
from datetime import datetime

import firebase_admin
from firebase_admin import credentials, firestore
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# =========================================================
# CONFIG
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is missing")

# =========================================================
# EARLY LOGGING (needed by Firebase startup)
# =========================================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# =========================================================
# AUTO GROUP POST CONFIG
# =========================================================

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "RJteam1").strip().lstrip("@")
ADMIN_USER_ID = os.getenv("ADMIN_USER_ID", "").strip()
TARGET_CHAT_ID = os.getenv("TARGET_CHAT_ID", "").strip()
AUTOPOST_FILE = os.getenv("AUTOPOST_FILE", "autoposts.json")
BD_TZ = ZoneInfo("Asia/Dhaka")

# Global auto-post switch. Use /autopost on or /autopost off.
AUTOPOST_ENABLED = os.getenv("AUTOPOST_ENABLED", "true").strip().lower() == "true"

# =========================================================
# FIREBASE PERSISTENT AUTO POST STORAGE
# =========================================================
# Render deploy/restart হলেও Auto Post list, ID, time, photo file_id
# এবং ON/OFF status Firestore-এ থাকবে।

FIREBASE_SERVICE_ACCOUNT_JSON = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
FIREBASE_COLLECTION = os.getenv("AUTOPOST_FIREBASE_COLLECTION", "rj_bot_config").strip()
FIREBASE_DOCUMENT = os.getenv("AUTOPOST_FIREBASE_DOCUMENT", "autopost").strip()

def init_firebase():
    if not FIREBASE_SERVICE_ACCOUNT_JSON:
        logger.warning("FIREBASE_SERVICE_ACCOUNT_JSON is not set. Using local autopost file only.")
        return None
    try:
        if not firebase_admin._apps:
            service_account = json.loads(FIREBASE_SERVICE_ACCOUNT_JSON)
            cred = credentials.Certificate(service_account)
            firebase_admin.initialize_app(cred)
        return firestore.client()
    except Exception as e:
        logger.error("Firebase initialization failed: %s", e, exc_info=True)
        return None

FIRESTORE_DB = init_firebase()

# =========================================================
# AUTO POST STORAGE
# =========================================================

def _local_load_autopost_data():
    default = {"posts": [], "next_id": 1, "enabled": AUTOPOST_ENABLED}
    try:
        if not os.path.exists(AUTOPOST_FILE):
            return default
        with open(AUTOPOST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("posts", [])
        data.setdefault("next_id", 1)
        data.setdefault("enabled", AUTOPOST_ENABLED)
        return data
    except Exception as e:
        logger.error("Could not load local autopost data: %s", e)
        return default

def load_autopost_data():
    local_data = _local_load_autopost_data()
    if FIRESTORE_DB is None:
        return local_data
    try:
        snap = FIRESTORE_DB.collection(FIREBASE_COLLECTION).document(FIREBASE_DOCUMENT).get()
        if snap.exists:
            data = snap.to_dict() or {}
            data.setdefault("posts", [])
            data.setdefault("next_id", 1)
            data.setdefault("enabled", AUTOPOST_ENABLED)
            return data
        # First run: migrate any existing local data into Firebase.
        FIRESTORE_DB.collection(FIREBASE_COLLECTION).document(FIREBASE_DOCUMENT).set(local_data)
        return local_data
    except Exception as e:
        logger.error("Could not load Firebase autopost data: %s", e, exc_info=True)
        return local_data

AUTOPOST_DATA = load_autopost_data()


def normalize_autopost_ids(save=True):
    """Make visible Auto Post IDs exactly 1..N, with no duplicates."""
    posts = AUTOPOST_DATA.get("posts", []) or []

    if not posts:
        changed = AUTOPOST_DATA.get("next_id") != 1
        AUTOPOST_DATA["posts"] = []
        AUTOPOST_DATA["next_id"] = 1
        if changed and save:
            save_autopost_data()
        return

    # Preserve existing order first; this is important when old data contains
    # duplicate/missing IDs. Then assign one unique visible ID to every post.
    ordered = list(posts)
    changed = False

    for new_id, post in enumerate(ordered, start=1):
        try:
            old_id = int(post.get("id", 0))
        except Exception:
            old_id = 0
        if old_id != new_id:
            post["id"] = new_id
            changed = True

    if AUTOPOST_DATA.get("posts") != ordered:
        AUTOPOST_DATA["posts"] = ordered
        changed = True
    else:
        AUTOPOST_DATA["posts"] = ordered

    expected_next = len(ordered) + 1
    try:
        current_next = int(AUTOPOST_DATA.get("next_id", 1))
    except Exception:
        current_next = 1

    if current_next != expected_next:
        AUTOPOST_DATA["next_id"] = expected_next
        changed = True

    if changed and save:
        save_autopost_data()


# Repair old IDs immediately after loading.
normalize_autopost_ids(save=False)

def save_autopost_data():
    data = {
        "posts": AUTOPOST_DATA.get("posts", []),
        "next_id": int(AUTOPOST_DATA.get("next_id", 1)),
        "enabled": bool(AUTOPOST_DATA.get("enabled", True)),
    }
    # Always keep a local backup too.
    try:
        temp_file = AUTOPOST_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, AUTOPOST_FILE)
    except Exception as e:
        logger.error("Could not save local autopost backup: %s", e)

    if FIRESTORE_DB is not None:
        try:
            FIRESTORE_DB.collection(FIREBASE_COLLECTION).document(FIREBASE_DOCUMENT).set(data)
        except Exception as e:
            logger.error("Could not save Firebase autopost data: %s", e, exc_info=True)

# Persist any repaired legacy IDs (for example 8, 9 -> 1, 2).
normalize_autopost_ids(save=True)

def is_admin(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False

    if ADMIN_USER_ID and str(user.id) == ADMIN_USER_ID:
        return True

    username = (user.username or "").strip().lstrip("@")
    return bool(ADMIN_USERNAME) and username.lower() == ADMIN_USERNAME.lower()

def parse_ampm_time(value: str):
    value = value.strip().upper().replace(".", "")
    for fmt in ("%I:%M %p", "%I %p", "%H:%M"):
        try:
            dt = datetime.strptime(value, fmt)
            return dt.strftime("%H:%M")
        except ValueError:
            continue
    return None

def display_time(hhmm: str) -> str:
    try:
        dt = datetime.strptime(hhmm, "%H:%M")
        return dt.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return hhmm

def target_chat_id():
    if not TARGET_CHAT_ID:
        return None
    try:
        return int(TARGET_CHAT_ID)
    except ValueError:
        return TARGET_CHAT_ID

def add_autopost(time_hhmm, post_type, text="", photo_file_id=None):
    """Add one new post and always allocate the next unused visible ID."""
    posts = AUTOPOST_DATA.setdefault("posts", [])

    # Repair any old/corrupt IDs first.
    normalize_autopost_ids(save=False)

    used_ids = set()
    for p in posts:
        try:
            used_ids.add(int(p.get("id")))
        except Exception:
            pass

    # The next ID is based on the number of currently saved posts and then
    # checked against every existing ID. This prevents ID 2 from being reused
    # while another ID 2 already exists.
    post_id = len(posts) + 1
    while post_id in used_ids:
        post_id += 1

    posts.append({
        "id": post_id,
        "time": time_hhmm,
        "type": post_type,
        "text": text or "",
        "photo_file_id": photo_file_id,
        "last_sent": ""
    })

    # Rebuild the visible sequence immediately after the append.
    normalize_autopost_ids(save=False)
    AUTOPOST_DATA["next_id"] = len(AUTOPOST_DATA["posts"]) + 1
    save_autopost_data()
    return post_id


def find_autopost(post_id):
    for post in AUTOPOST_DATA.get("posts", []):
        try:
            if int(post.get("id", -1)) == int(post_id):
                return post
        except Exception:
            continue
    return None


def split_long_text(text, limit=3900):
    """Telegram message limit-এর জন্য বড় list কয়েকটি message-এ ভাগ করে।"""
    text = text or ""
    chunks = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < 500:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip()
    if text:
        chunks.append(text)
    return chunks or [""]


def autopost_detail_text(post):
    pid = post.get("id")
    ptype = "📷 Photo Post" if post.get("type") == "photo" else "📝 Text Post"
    caption = post.get("text") or "(কোনো caption নেই)"
    status = "🟢 ON" if AUTOPOST_DATA.get("enabled", True) else "🔴 OFF"
    return (
        "📋 Saved Auto Post\n\n"
        f"🆔 ID: {pid}\n"
        f"⏰ Time: {display_time(post.get('time', ''))}\n"
        f"📌 Type: {ptype}\n"
        f"⚙️ Auto Post: {status}\n\n"
        "📝 Caption:\n"
        f"{caption}"
    )


def autopost_detail_keyboard(post_id):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Edit", callback_data=f"autopost_edit:{post_id}"),
            InlineKeyboardButton("🗑️ Delete", callback_data=f"autopost_delete:{post_id}"),
        ],
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"autopost_refresh:{post_id}")],
    ])


def format_autopost_list(post_id=None):
    """Return the saved Auto Post list/detail with continuous unique IDs."""
    normalize_autopost_ids(save=True)

    posts = sorted(
        AUTOPOST_DATA.get("posts", []),
        key=lambda x: int(x.get("id", 0)),
    )
    status = "ON 🟢" if AUTOPOST_DATA.get("enabled", True) else "OFF 🔴"

    if post_id is not None:
        post = find_autopost(post_id)
        if not post:
            return f"❌ ID {post_id} পাওয়া যায়নি।"
        return autopost_detail_text(post)

    if not posts:
        return (
            "📋 ALL SAVED AUTO POSTS\n\n"
            f"⚙️ Status: {status}\n"
            "📦 Total: 0\n\n"
            "কোনো saved post/caption নেই।"
        )

    lines = [
        "📋 ALL SAVED AUTO POSTS",
        f"⚙️ Status: {status}",
        f"📦 Total: {len(posts)}",
        "",
        "👇 ID অনুযায়ী সব saved post/caption:",
    ]

    for post in posts:
        pid = int(post.get("id", 0))
        ptype = "📷 Photo" if post.get("type") == "photo" else "📝 Text"
        caption = (post.get("text") or "(কোনো caption নেই)").strip()
        lines.extend([
            "",
            f"🆔 ID {pid}",
            f"⏰ {display_time(post.get('time', ''))}",
            f"📌 {ptype}",
            f"📝 {caption}",
        ])

    lines.extend([
        "",
        "👆 নির্দিষ্ট পোস্ট খুলতে /list ID লিখুন।",
        "🗑️ Delete করলে পরের সব ID স্বয়ংক্রিয়ভাবে 1,2,3... হবে।",
    ])
    return "\n".join(lines)


async def auto_post_worker(application: Application):
    logger = logging.getLogger(__name__)
    logger.info("Automatic group post system started.")
    while True:
        try:
            if TARGET_CHAT_ID and AUTOPOST_DATA.get("enabled", True):
                now = datetime.now(BD_TZ)
                now_hhmm = now.strftime("%H:%M")
                today_key = now.strftime("%Y-%m-%d")

                changed = False
                for post in AUTOPOST_DATA.get("posts", []):
                    if post.get("time") != now_hhmm:
                        continue
                    if post.get("last_sent") == today_key:
                        continue

                    try:
                        chat_id = target_chat_id()
                        if post.get("type") == "photo" and post.get("photo_file_id"):
                            await application.bot.send_photo(
                                chat_id=chat_id,
                                photo=post["photo_file_id"],
                                caption=post.get("text", "")[:1024] or None,
                            )
                        else:
                            await application.bot.send_message(
                                chat_id=chat_id,
                                text=post.get("text", "")[:4096],
                                disable_web_page_preview=True,
                            )

                        post["last_sent"] = today_key
                        changed = True
                        logger.info(
                            "Auto post sent | id=%s | time=%s",
                            post.get("id"), post.get("time")
                        )
                    except Exception as e:
                        logger.error(
                            "Auto post failed | id=%s | %s",
                            post.get("id"), e
                        )

                if changed:
                    save_autopost_data()

            await asyncio.sleep(20)

        except asyncio.CancelledError:
            logger.info("Automatic group post system stopped.")
            raise
        except Exception as e:
            logger.error("Auto post worker error: %s", e, exc_info=True)
            await asyncio.sleep(20)

async def post_init(application: Application):
    application.bot_data["autopost_task"] = asyncio.create_task(
        auto_post_worker(application)
    )

async def post_shutdown(application: Application):
    task = application.bot_data.get("autopost_task")
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

# =========================================================
# GEMINI CLIENT
# =========================================================

client = genai.Client(
    api_key=GEMINI_API_KEY
)


# =========================================================
# GEMINI MODELS
# =========================================================
#
# একটার সমস্যা হলে পরেরটায় যাবে।
#
# Primary:
# 3.8 Flash
#
# Fallback:
# 3.7 Flash
# 3.6 Flash
# 2.5 Flash
# 3.5 Flash-Lite
#
# =========================================================

GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-2.5-flash",
    "gemini-3.5-flash-lite",
]


# =========================================================
# REQUEST CONTROL
# =========================================================
#
# একই সময়ে সর্বোচ্চ 3টি Gemini request।
#
# এতে অনেক user একসাথে SMS পাঠালে চাপ কমে।
#
# =========================================================

GEMINI_SEMAPHORE = asyncio.Semaphore(3)


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format=(
        "%(asctime)s - %(name)s - "
        "%(levelname)s - %(message)s"
    ),
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# BOT INFORMATION
# =========================================================

ABOUT_TEXT = """
🤖 About AI Assistant

আমি তোমার AI Assistant। ❤️

আমি RJ Team Bangladesh Hacker Community-এর পক্ষ থেকে
তোমার বন্ধু হিসেবে তোমার বিভিন্ন প্রশ্নের উত্তর দেওয়ার
চেষ্টা করি। 😊

💬 সাধারণ প্রশ্ন → সুন্দর উত্তর
😂 মজার SMS → মজার reply
❤️ Emotional SMS → emotional reply
🌐 Translation → যেকোনো ভাষার অনুবাদ

👑 Owner: @RJteam1
📢 Channel: @RJteam123890

🤝 RJ Team Bangladesh Hacker Community
"""


# =========================================================
# AI PERSONALITY
# =========================================================

SYSTEM_PROMPT = """
তুমি একটি বন্ধুসুলভ Telegram AI Assistant।

তুমি RJ Team Bangladesh Hacker Community-এর পক্ষ থেকে
ব্যবহারকারীর সাথে বন্ধুর মতো কথা বলবে।

সবচেয়ে গুরুত্বপূর্ণ নিয়ম:

ব্যবহারকারী যে ভাষাতেই SMS পাঠাক না কেন,
সাধারণ AI reply অবশ্যই বাংলায় দিতে হবে।

English SMS → বাংলায় উত্তর
Banglish SMS → বাংলায় উত্তর
বাংলা SMS → বাংলায় উত্তর
অন্য ভাষার SMS → বাংলায় উত্তর

RULES:

1. সাধারণ প্রশ্ন হলে সরাসরি, পরিষ্কার ও সুন্দর বাংলায় উত্তর দাও।

2. মজার SMS হলে বাংলায় মজার, playful এবং হাস্যকর reply দাও।
প্রয়োজনে 😂 😄 🤣 😆 ব্যবহার করতে পারো।

3. Emotional SMS হলে বাংলায় আন্তরিক, সুন্দর ও emotional reply দাও।
প্রয়োজনে ❤️ 🥺 😔 💔 ব্যবহার করতে পারো।

4. দুঃখের SMS হলে সহানুভূতিশীল বাংলায় উত্তর দাও।

5. ভালোবাসা বা romantic SMS হলে কোমল ও সুন্দর বাংলায় উত্তর দাও।

6. রাগের SMS হলে শান্ত ও ভদ্র বাংলায় উত্তর দাও।

7. সব SMS-কে emotional বানাবে না।
শুধু সত্যিই emotional SMS হলে emotional tone ব্যবহার করবে।

8. প্রশ্ন করলে প্রশ্নের উত্তর সরাসরি বাংলায় দাও।

9. ব্যবহারকারী English-এ প্রশ্ন করলেও উত্তর বাংলায় দাও।

10. ব্যবহারকারী Banglish-এ লিখলেও উত্তর বাংলা অক্ষরে দেওয়ার চেষ্টা করো।

11. উত্তর natural এবং মানুষের মতো হবে।

12. সাধারণ SMS-এর উত্তর খুব বড় করবে না।

13. প্রয়োজন অনুযায়ী 1-3টি emoji ব্যবহার করো।

14. একই reply বারবার হুবহু ব্যবহার করবে না।

15. গুরুতর বিষয়ে মজা করবে না।

16. ব্যবহারকারীর মূল বক্তব্য বুঝে reply করবে।

17. সাধারণ AI reply-এর মধ্যে অপ্রয়োজনীয় English ব্যবহার করবে না।

18. Translation command ব্যবহার করলে Translation-এর নির্দেশনা অনুসরণ করবে।

19. উত্তর সংক্ষিপ্ত, স্বাভাবিক এবং Telegram chat-এর উপযোগী রাখবে।

20. অপ্রয়োজনীয় heading বা দীর্ঘ explanation দেবে না,
যদি ব্যবহারকারী বিস্তারিত না চায়।
"""


# =========================================================
# TRANSIENT ERROR CHECK
# =========================================================

def is_transient_error(error_text: str) -> bool:

    text = error_text.lower()

    patterns = [
        "429",
        "500",
        "502",
        "503",
        "504",
        "resource_exhausted",
        "unavailable",
        "service unavailable",
        "internal server error",
        "bad gateway",
        "gateway timeout",
        "high demand",
        "temporarily unavailable",
        "timeout",
        "rate limit",
        "too many requests",
    ]

    return any(
        pattern in text
        for pattern in patterns
    )


# =========================================================
# GEMINI GENERATOR
# =========================================================

async def generate_gemini(
    prompt,
    system_instruction=None,
):

    async with GEMINI_SEMAPHORE:

        # -------------------------------------------------
        # প্রতিটি model একবার করে চেষ্টা করবে।
        # Transient error হলে প্রয়োজনে retry করবে।
        # -------------------------------------------------

        for model_index, model_name in enumerate(
            GEMINI_MODELS
        ):

            # Primary model:
            # দ্রুত fallback করার জন্য 1 retry
            #
            # অন্য model:
            # 2 attempt
            #
            max_attempts = (
                1 if model_index == 0 else 2
            )

            for attempt in range(
                1,
                max_attempts + 1
            ):

                try:

                    logger.info(
                        "Gemini request | "
                        "model=%s | attempt=%s/%s",
                        model_name,
                        attempt,
                        max_attempts,
                    )

                    # -------------------------------------------------
                    # AFC সম্পূর্ণ বন্ধ
                    # এই bot-এ function/tool দরকার নেই।
                    # -------------------------------------------------

                    config = types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        max_output_tokens=500,
                        automatic_function_calling=(
                            types.AutomaticFunctionCallingConfig(
                                disable=True
                            )
                        ),
                    )

                    response = (
                        await client.aio.models.generate_content(
                            model=model_name,
                            contents=prompt,
                            config=config,
                        )
                    )

                    answer = (
                        response.text or ""
                    ).strip()

                    # -------------------------------------------------
                    # Empty response
                    # -------------------------------------------------

                    if answer:

                        logger.info(
                            "Gemini success | model=%s",
                            model_name,
                        )

                        return answer

                    logger.warning(
                        "Gemini empty response | model=%s",
                        model_name,
                    )

                    # Empty হলে পরের model
                    break

                except Exception as e:

                    error_text = str(e)

                    logger.error(
                        "Gemini error | "
                        "model=%s | "
                        "attempt=%s/%s | %s",
                        model_name,
                        attempt,
                        max_attempts,
                        error_text,
                    )

                    # =================================================
                    # TRANSIENT ERROR
                    # =================================================

                    if is_transient_error(
                        error_text
                    ):

                        # -------------------------------------------------
                        # শেষ attempt না হলে retry
                        # -------------------------------------------------

                        if attempt < max_attempts:

                            # Exponential backoff:
                            #
                            # 1st retry ≈ 2 sec
                            # 2nd retry ≈ 4 sec
                            #
                            base_wait = (
                                2 ** attempt
                            )

                            jitter = random.uniform(
                                0.2,
                                0.8,
                            )

                            wait_time = (
                                base_wait + jitter
                            )

                            logger.warning(
                                "Transient Gemini error. "
                                "Retrying model=%s in %.1f seconds...",
                                model_name,
                                wait_time,
                            )

                            await asyncio.sleep(
                                wait_time
                            )

                            continue

                        # -------------------------------------------------
                        # এই model unavailable।
                        # পরের model-এ যাবে।
                        # -------------------------------------------------

                        logger.warning(
                            "Model %s unavailable. "
                            "Trying next model...",
                            model_name,
                        )

                        break

                    # =================================================
                    # NON-TRANSIENT ERROR
                    # =================================================

                    logger.warning(
                        "Non-transient Gemini error. "
                        "Trying next model..."
                    )

                    break

    # =========================================================
    # ALL MODELS FAILED
    # =========================================================

    logger.error(
        "ALL GEMINI MODELS FAILED."
    )

    return None


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "ℹ️ About",
                callback_data="about",
            ),
            InlineKeyboardButton(
                "🌐 Translate",
                callback_data="translate_help",
            ),
        ],
        [
            InlineKeyboardButton(
                "📢 Channel",
                url="https://t.me/RJteam123890",
            ),
            InlineKeyboardButton(
                "👑 Owner",
                url="https://t.me/RJteam1",
            ),
        ],
    ]

    await update.message.reply_text(
        "👋 হ্যালো বন্ধু! ❤️\n\n"
        "আমি তোমার AI Assistant। 🤖\n\n"
        "💬 যেকোনো SMS পাঠাও।\n"
        "😂 মজার হলে মজার reply\n"
        "❤️ Emotional হলে emotional reply\n"
        "🤔 প্রশ্ন হলে সুন্দর উত্তর\n"
        "🌐 Translation-ও করা যাবে।\n\n"
        "নিচের Button ব্যবহার করতে পারো 👇",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
    )


# =========================================================
# ADMIN AUTO POST COMMANDS
# =========================================================

async def _admin_only(update: Update) -> bool:
    if is_admin(update):
        return True
    if update.message:
        await update.message.reply_text("⛔ এই command শুধু Admin ব্যবহার করতে পারবে।")
    return False


async def autopost_delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin_only(update):
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("ব্যবহার: /delete ID")
        return
    pid = int(context.args[0])
    if delete_autopost(pid):
        await update.message.reply_text(f"✅ Auto Post ID {pid} delete হয়েছে।")
    else:
        await update.message.reply_text(f"❌ ID {pid} পাওয়া যায়নি।")


async def autopost_clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin_only(update):
        return
    AUTOPOST_DATA["posts"] = []
    AUTOPOST_DATA["next_id"] = 1
    save_autopost_data()
    await update.message.reply_text("🗑️ সব Auto Post delete হয়েছে।")


async def autopost_on_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin_only(update):
        return
    AUTOPOST_DATA["enabled"] = True
    save_autopost_data()
    await update.message.reply_text("✅ Auto Post ON 🟢")


async def autopost_off_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin_only(update):
        return
    AUTOPOST_DATA["enabled"] = False
    save_autopost_data()
    await update.message.reply_text("⛔ Auto Post OFF 🔴")


async def autopost_test_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin_only(update):
        return
    chat_id = target_chat_id()
    if not chat_id:
        await update.message.reply_text("❌ TARGET_CHAT_ID সেট করা নেই।")
        return
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text="🧪 RJ Team Auto Post Test সফল হয়েছে।",
        )
        await update.message.reply_text("✅ Group-এ test post পাঠানো হয়েছে।")
    except Exception as e:
        await update.message.reply_text(f"❌ Test failed: {e}")



async def autopost_list_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _admin_only(update):
        return

    normalize_autopost_ids(save=True)

    post_id = None
    if context.args and context.args[0].isdigit():
        post_id = int(context.args[0])

    if post_id is not None:
        post = find_autopost(post_id)
        if not post:
            await update.message.reply_text(
                f"❌ ID {post_id} পাওয়া যায়নি।\nবর্তমান ID গুলো /list দিয়ে দেখুন।"
            )
            return

        if post.get("type") == "photo" and post.get("photo_file_id"):
            try:
                await update.message.reply_photo(
                    photo=post["photo_file_id"],
                    caption=(post.get("text") or "(ক্যাপশন নেই)")[:1024],
                    reply_markup=autopost_detail_keyboard(post_id),
                )
                return
            except Exception as e:
                logger.error("Could not display saved photo ID %s: %s", post_id, e, exc_info=True)

        await update.message.reply_text(
            autopost_detail_text(post),
            reply_markup=autopost_detail_keyboard(post_id),
            disable_web_page_preview=True,
        )
        return

    posts = list(AUTOPOST_DATA.get("posts", []) or [])
    if not posts:
        await update.message.reply_text("📋 ALL SAVED AUTO POSTS\n\n📦 Total: 0")
        return

    status = "ON 🟢" if AUTOPOST_DATA.get("enabled", True) else "OFF 🔴"
    header = (
        "📋 ALL SAVED AUTO POSTS\n\n"
        f"⚙️ Status: {status}\n"
        f"📦 Total: {len(posts)}\n\n"
        "👇 সব saved post/caption:"
    )

    # Send the complete captions in chunks so Telegram's 4096-character
    # message limit cannot hide later saved captions.
    chunks = [header]
    current = header

    for post in posts:
        pid = int(post.get("id", 0))
        ptype = "📷 Photo" if post.get("type") == "photo" else "📝 Text"
        caption = (post.get("text") or "(ক্যাপশন নেই)").strip()
        entry = f"\n\n🆔 LIST {pid}\n{ptype}\n📝 {caption}"

        if len(current) + len(entry) > 3800:
            chunks.append(current)
            current = f"📋 CONTINUED — SAVED POSTS\n{entry.lstrip()}"
        else:
            current += entry

    if current:
        chunks.append(current)

    # The ID buttons stay in a compact grid, so every saved item is touchable.
    keyboard = []
    row = []
    for post in posts:
        pid = int(post.get("id", 0))
        row.append(
            InlineKeyboardButton(
                f"🆔 LIST {pid}",
                callback_data=f"autopost_open:{pid}",
            )
        )
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([
        InlineKeyboardButton("🔄 Refresh List", callback_data="autopost_list")
    ])

    for i, chunk in enumerate(chunks):
        await update.message.reply_text(
            chunk,
            reply_markup=InlineKeyboardMarkup(keyboard) if i == len(chunks) - 1 else None,
            disable_web_page_preview=True,
        )


async def autopost_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not update.message:
        return

    if not is_admin(update):
        await update.message.reply_text("⛔ এই command শুধু Admin ব্যবহার করতে পারবে।")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "🤖 Auto Post Admin\n"
            "/autopost on - Auto post চালু\n"
            "/autopost off - Auto post বন্ধ\n"
            "/autopost list - সব scheduled post\n"
            "/autopost add 8:30 PM আপনার পোস্ট - Text post যোগ\n"
            "/autopost addphoto 8:30 PM - Photo post যোগ (photo-তে reply করে)\n"
            "/autopost delete 1 - ID দিয়ে delete"
        )
        return

    action = args[0].lower()

    if action == "on":
        AUTOPOST_DATA["enabled"] = True
        save_autopost_data()
        await update.message.reply_text("✅ Auto Post ON করা হয়েছে।")
        return

    if action == "off":
        AUTOPOST_DATA["enabled"] = False
        save_autopost_data()
        await update.message.reply_text("🛑 Auto Post OFF করা হয়েছে।")
        return

    if action == "list":
        normalize_autopost_ids(save=True)
        posts = list(AUTOPOST_DATA.get("posts", []) or [])
        if not posts:
            await update.message.reply_text("📋 ALL SAVED AUTO POSTS\n\n📦 Total: 0")
            return

        status = "ON 🟢" if AUTOPOST_DATA.get("enabled", True) else "OFF 🔴"
        lines = [
            "📋 ALL SAVED AUTO POSTS",
            "",
            f"⚙️ Status: {status}",
            f"📦 Total: {len(posts)}",
            "",
        ]
        keyboard = []
        row = []

        for post in posts:
            pid = int(post.get("id", 0))
            ptype = "📷 Photo" if post.get("type") == "photo" else "📝 Text"
            caption = (post.get("text") or "(ক্যাপশন নেই)").strip()
            lines.append(f"🆔 LIST {pid}\n{ptype}\n📝 {caption}")

            row.append(
                InlineKeyboardButton(
                    f"🆔 LIST {pid}",
                    callback_data=f"autopost_open:{pid}",
                )
            )
            if len(row) == 2:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)
        keyboard.append([
            InlineKeyboardButton("🔄 Refresh List", callback_data="autopost_list")
        ])

        # Split if needed to respect Telegram's text limit.
        full = "\n\n".join(lines)
        if len(full) <= 3800:
            await update.message.reply_text(
                full,
                reply_markup=InlineKeyboardMarkup(keyboard),
                disable_web_page_preview=True,
            )
        else:
            current = "📋 ALL SAVED AUTO POSTS\n\n"
            for line in lines[4:]:
                block = line + "\n\n"
                if len(current) + len(block) > 3800:
                    await update.message.reply_text(current, disable_web_page_preview=True)
                    current = "📋 CONTINUED — SAVED POSTS\n\n"
                current += block
            await update.message.reply_text(
                current,
                reply_markup=InlineKeyboardMarkup(keyboard),
                disable_web_page_preview=True,
            )
        return

    if action == "delete":
        if len(args) < 2 or not args[1].isdigit():
            await update.message.reply_text("❌ ব্যবহার: /autopost delete ID")
            return
        post_id = int(args[1])
        if delete_autopost(post_id):
            await update.message.reply_text(f"🗑️ Auto Post ID {post_id} delete করা হয়েছে।")
        else:
            await update.message.reply_text(f"❌ ID {post_id} পাওয়া যায়নি।")
        return

    if action == "add":
        if len(args) < 3:
            await update.message.reply_text(
                "❌ ব্যবহার:\n/autopost add 8:30 PM আপনার পোস্ট"
            )
            return

        # Supports: 8:30 PM text OR 20:30 text
        time_arg_count = 2
        if len(args) >= 3 and args[2].upper() in ("AM", "PM"):
            time_text = f"{args[1]} {args[2]}"
            time_arg_count = 3
        else:
            time_text = args[1]

        hhmm = parse_ampm_time(time_text)
        if not hhmm:
            await update.message.reply_text(
                "❌ সময় ঠিক নয়। উদাহরণ: 8:30 PM বা 20:30"
            )
            return

        post_text = " ".join(args[time_arg_count:]).strip()
        if not post_text:
            await update.message.reply_text("❌ পোস্টের লেখা দিন।")
            return

        post_id = add_autopost(hhmm, "text", text=post_text)
        await update.message.reply_text(
            f"✅ Auto Post যোগ হয়েছে।\n🆔 ID: {post_id}\n⏰ Time: {display_time(hhmm)}"
        )
        return

    if action == "addphoto":
        if len(args) < 2:
            await update.message.reply_text(
                "❌ ব্যবহার:\n1) একটি photo পাঠান\n"
                "2) photo-টির reply দিয়ে লিখুন: /autopost addphoto 8:30 PM"
            )
            return

        time_arg_count = 1
        if len(args) >= 3 and args[2].upper() in ("AM", "PM"):
            time_text = f"{args[1]} {args[2]}"
            time_arg_count = 3
        else:
            time_text = args[1]

        hhmm = parse_ampm_time(time_text)
        if not hhmm:
            await update.message.reply_text(
                "❌ সময় ঠিক নয়। উদাহরণ: 8:30 PM বা 20:30"
            )
            return

        replied = update.message.reply_to_message
        if not replied or not replied.photo:
            await update.message.reply_text(
                "❌ আগে একটি photo-তে reply করে এই command দিন:\n"
                "/autopost addphoto 8:30 PM"
            )
            return

        photo_file_id = replied.photo[-1].file_id
        caption = replied.caption or ""
        # Optional extra caption after time
        extra_caption = " ".join(args[time_arg_count:]).strip()
        if extra_caption:
            caption = extra_caption

        post_id = add_autopost(
            hhmm,
            "photo",
            text=caption,
            photo_file_id=photo_file_id,
        )
        await update.message.reply_text(
            f"✅ Photo Auto Post যোগ হয়েছে।\n"
            f"🆔 ID: {post_id}\n"
            f"⏰ Time: {display_time(hhmm)}"
        )
        return

    await update.message.reply_text(
        "❌ Command পাওয়া যায়নি। /autopost লিখলে সব command দেখাবে।"
    )

# =========================================================
# HELP
# =========================================================

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    await update.message.reply_text(
        "🤖 Bot Help\n\n"
        "💬 যেকোনো SMS পাঠাও।\n"
        "🇧🇩 AI reply সবসময় বাংলায় হবে।\n"
        "😂 মজার হলে মজার reply\n"
        "❤️ Emotional হলে emotional reply\n"
        "🤔 প্রশ্ন হলে বাংলায় উত্তর\n\n"
        "🌐 Translate:\n"
        "/translate Hello, how are you?\n\n"
        "ℹ️ About:\n"
        "/about"
    )


# =========================================================
# ABOUT
# =========================================================

async def about_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "👑 Owner",
                url="https://t.me/RJteam1",
            ),
            InlineKeyboardButton(
                "📢 Channel",
                url="https://t.me/RJteam123890",
            ),
        ]
    ]

    await update.message.reply_text(
        ABOUT_TEXT,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
        disable_web_page_preview=True,
    )


# =========================================================
# AI REPLY
# =========================================================

async def ai_reply(
    text,
):

    answer = await generate_gemini(
        prompt=text,
        system_instruction=SYSTEM_PROMPT,
    )

    if not answer:

        return (
            "😅 এই মুহূর্তে AI সার্ভারগুলো ব্যস্ত আছে।\n\n"
            "একটু পরে আবার SMS পাঠাও। ❤️"
        )

    return answer


# =========================================================
# TRANSLATE
# =========================================================

async def translate_text(
    text,
):

    prompt = f"""
Translate the following text naturally.

Automatically detect the source language.

If the user explicitly specifies a target language,
translate into that target language.

If no target language is specified,
translate into Bangla.

Return ONLY the translated text.

Do not explain.
Do not add quotation marks.

Text:
{text}
"""

    result = await generate_gemini(
        prompt=prompt,
        system_instruction=(
            "You are a professional translation assistant. "
            "Return only the requested translation."
        ),
    )

    if not result:

        return (
            "❌ Translation সার্ভার এই মুহূর্তে ব্যস্ত।\n"
            "কিছুক্ষণ পরে আবার চেষ্টা করুন।"
        )

    return result


# =========================================================
# TRANSLATE COMMAND
# =========================================================

async def translate_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    if not context.args:

        await update.message.reply_text(
            "🌐 Translate ব্যবহার করার নিয়ম:\n\n"
            "/translate Hello, how are you?\n\n"
            "ডিফল্টভাবে বাংলা translation দেওয়া হবে। ❤️"
        )

        return

    text = " ".join(
        context.args
    )

    try:

        await update.message.chat.send_action(
            "typing"
        )

    except Exception:
        pass

    result = await translate_text(
        text
    )

    await update.message.reply_text(
        "🌐 Translation:\n\n" + result,
        disable_web_page_preview=True,
    )


# =========================================================
# AUTO POST EDIT / DELETE BUTTONS
# =========================================================

async def autopost_cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    if context.user_data.pop("autopost_edit_id", None) is not None:
        await update.message.reply_text("❌ Edit বাতিল করা হয়েছে।")
    else:
        await update.message.reply_text("ℹ️ কোনো Auto Post edit mode চালু নেই।")


async def autopost_edit_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit mode-এ থাকা admin-এর নতুন time|caption নেয়।"""
    if not update.message or not update.message.text:
        return False
    if not is_admin(update):
        return False

    post_id = context.user_data.get("autopost_edit_id")
    if post_id is None:
        return False

    raw = update.message.text.strip()
    if raw.lower() == "/cancel":
        context.user_data.pop("autopost_edit_id", None)
        await update.message.reply_text("❌ Edit বাতিল করা হয়েছে।")
        return True

    if "|" not in raw:
        await update.message.reply_text(
            "❌ Format ভুল।\n\n"
            "এভাবে দিন:\n"
            "10:30 PM|নতুন caption\n\n"
            "অথবা:\n"
            "22:30|নতুন caption\n\n"
            "বাতিল করতে /cancel লিখুন।"
        )
        return True

    time_text, caption = raw.split("|", 1)
    hhmm = parse_ampm_time(time_text.strip())
    caption = caption.strip()

    if not hhmm:
        await update.message.reply_text(
            "❌ সময় ঠিক নয়। উদাহরণ: 10:30 PM|নতুন caption"
        )
        return True

    if not caption:
        await update.message.reply_text("❌ Caption খালি রাখা যাবে না।")
        return True

    post = find_autopost(post_id)
    if not post:
        context.user_data.pop("autopost_edit_id", None)
        await update.message.reply_text(f"❌ ID {post_id} পাওয়া যায়নি।")
        return True

    post["time"] = hhmm
    post["text"] = caption
    # সময়/caption বদলানোর পর আজকের পুরনো send-state reset করা হবে,
    # যাতে নতুন schedule-টি আবার কাজ করতে পারে।
    post["last_sent"] = ""
    save_autopost_data()
    context.user_data.pop("autopost_edit_id", None)

    await update.message.reply_text(
        f"✅ ID {post_id} update হয়েছে।\n\n"
        f"⏰ Time: {display_time(hhmm)}\n"
        f"📝 Caption:\n{caption}",
        reply_markup=autopost_detail_keyboard(post_id),
        disable_web_page_preview=True,
    )
    return True


async def autopost_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return

    if not is_admin(update):
        await query.answer("⛔ শুধু Admin ব্যবহার করতে পারবে।", show_alert=True)
        return

    if query.data == "autopost_list":
        normalize_autopost_ids(save=True)
        posts = sorted(
            AUTOPOST_DATA.get("posts", []),
            key=lambda x: int(x.get("id", 0)),
        )

        if not posts:
            await query.edit_message_text(format_autopost_list())
            return

        keyboard = []
        row = []
        for saved_post in posts:
            pid = int(saved_post.get("id", 0))
            row.append(
                InlineKeyboardButton(
                    f"🆔 ID {pid}",
                    callback_data=f"autopost_open:{pid}",
                )
            )
            if len(row) == 2:
                keyboard.append(row)
                row = []

        if row:
            keyboard.append(row)

        keyboard.append([
            InlineKeyboardButton(
                "🔄 Refresh List",
                callback_data="autopost_list",
            )
        ])

        status = "ON 🟢" if AUTOPOST_DATA.get("enabled", True) else "OFF 🔴"
        await query.edit_message_text(
            f"📋 ALL SAVED AUTO POSTS\n\n"
            f"⚙️ Status: {status}\n"
            f"📦 Total: {len(posts)}\n\n"
            "👇 যে ID দেখতে চান সেটিতে Touch করুন:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True,
        )
        return

    if (query.data or "").startswith("autopost_open:"):
        try:
            post_id = int(query.data.split(":", 1)[1])
        except Exception:
            await query.answer("❌ Invalid ID", show_alert=True)
            return

        normalize_autopost_ids(save=True)
        post = find_autopost(post_id)
        if not post:
            await query.answer("❌ এই ID আর নেই।", show_alert=True)
            await query.edit_message_text(format_autopost_list(), reply_markup=None)
            return

        await query.answer(f"ID {post_id}")
        if post.get("type") == "photo" and post.get("photo_file_id"):
            try:
                await query.message.reply_photo(
                    photo=post["photo_file_id"],
                    caption=(post.get("text") or "(ক্যাপশন নেই)")[:1024],
                    reply_markup=autopost_detail_keyboard(post_id),
                )
                return
            except Exception as e:
                logger.error("Could not display saved photo ID %s: %s", post_id, e, exc_info=True)

        await query.edit_message_text(
            autopost_detail_text(post),
            reply_markup=autopost_detail_keyboard(post_id),
            disable_web_page_preview=True,
        )
        return

    data = query.data or ""
    try:
        action, raw_id = data.split(":", 1)
        post_id = int(raw_id)
    except Exception:
        await query.answer("❌ Invalid request", show_alert=True)
        return

    post = find_autopost(post_id)

    if action == "autopost_refresh":
        await query.answer("🔄 Refresh")
        if not post:
            await query.edit_message_text(f"❌ ID {post_id} পাওয়া যায়নি।")
            return
        await query.edit_message_text(
            autopost_detail_text(post),
            reply_markup=autopost_detail_keyboard(post_id),
            disable_web_page_preview=True,
        )
        return

    if action == "autopost_edit":
        await query.answer("✏️ Edit mode")
        if not post:
            await query.edit_message_text(f"❌ ID {post_id} পাওয়া যায়নি।")
            return

        context.user_data["autopost_edit_id"] = post_id
        await query.message.reply_text(
            f"✏️ ID {post_id} Edit Mode\n\n"
            f"বর্তমান সময়: {display_time(post.get('time', ''))}\n"
            f"বর্তমান caption:\n{post.get('text') or '(খালি)'}\n\n"
            "নতুন format লিখুন:\n"
            "10:30 PM|নতুন caption\n\n"
            "অথবা:\n"
            "22:30|নতুন caption\n\n"
            "❌ Cancel করতে /cancel লিখুন।"
        )
        return

    if action == "autopost_delete":
        await query.answer("🗑️ Delete")
        if not post:
            await query.edit_message_text(f"❌ ID {post_id} পাওয়া যায়নি।")
            return

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, Delete", callback_data=f"autopost_delete_yes:{post_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"autopost_delete_no:{post_id}"),
            ]
        ])
        await query.edit_message_text(
            f"⚠️ ID {post_id} কি সত্যিই delete করবেন?\n\n"
            f"⏰ {display_time(post.get('time', ''))}\n"
            f"📝 {post.get('text') or '(কোনো caption নেই)'}",
            reply_markup=keyboard,
        )
        return

    if action == "autopost_delete_yes":
        if delete_autopost(post_id):
            await query.answer("✅ Deleted")
            await query.edit_message_text(f"🗑️ ID {post_id} সফলভাবে delete হয়েছে।")
        else:
            await query.answer("ID পাওয়া যায়নি", show_alert=True)
            await query.edit_message_text(f"❌ ID {post_id} পাওয়া যায়নি।")
        return

    if action == "autopost_delete_no":
        await query.answer("❌ Cancelled")
        post = find_autopost(post_id)
        if post:
            await query.edit_message_text(
                autopost_detail_text(post),
                reply_markup=autopost_detail_keyboard(post_id),
                disable_web_page_preview=True,
            )
        else:
            await query.edit_message_text(f"❌ ID {post_id} পাওয়া যায়নি।")
        return


# =========================================================
# BUTTON HANDLER
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    # =====================================================
    # ABOUT
    # =====================================================

    if query.data == "about":

        keyboard = [
            [
                InlineKeyboardButton(
                    "👑 Owner",
                    url="https://t.me/RJteam1",
                ),
                InlineKeyboardButton(
                    "📢 Channel",
                    url="https://t.me/RJteam123890",
                ),
            ]
        ]

        await query.message.reply_text(
            ABOUT_TEXT,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            ),
            disable_web_page_preview=True,
        )

        return

    # =====================================================
    # TRANSLATE HELP
    # =====================================================

    if query.data == "translate_help":

        await query.message.reply_text(
            "🌐 Translate\n\n"
            "যেকোনো ভাষার লেখা translate করতে পারো।\n\n"
            "উদাহরণ:\n"
            "/translate Hello, how are you?\n\n"
            "ডিফল্টভাবে বাংলা translation দেওয়া হবে। ❤️"
        )

        return


# =========================================================
# MESSAGE HANDLER
# =========================================================

async def handle_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if not update.message:
        return

    if not update.message.text:
        return

    text = update.message.text.strip()

    if not text:
        return

    # Admin Auto Post edit mode আগে handle হবে; AI-তে যাবে না।
    if context.user_data.get("autopost_edit_id") is not None:
        handled = await autopost_edit_message(update, context)
        if handled:
            return

    try:

        await update.message.chat.send_action(
            "typing"
        )

    except Exception:
        pass

    # -----------------------------------------------------
    # AI
    # -----------------------------------------------------

    answer = await ai_reply(
        text
    )

    # -----------------------------------------------------
    # Save last reply
    # -----------------------------------------------------

    context.user_data[
        "last_ai_reply"
    ] = answer

    # -----------------------------------------------------
    # Translate button
    # -----------------------------------------------------

    keyboard = [
        [
            InlineKeyboardButton(
                "🌐 Translate",
                callback_data="translate_last",
            )
        ]
    ]

    await update.message.reply_text(
        answer,
        reply_markup=InlineKeyboardMarkup(
            keyboard
        ),
        disable_web_page_preview=True,
    )


# =========================================================
# TRANSLATE LAST AI REPLY
# =========================================================

async def translate_last(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    if not query:
        return

    await query.answer()

    text = context.user_data.get(
        "last_ai_reply"
    )

    if not text:

        await query.message.reply_text(
            "❌ আগের reply পাওয়া যাচ্ছে না।"
        )

        return

    try:

        await query.message.chat.send_action(
            "typing"
        )

    except Exception:
        pass

    result = await translate_text(
        text
    )

    await query.message.reply_text(
        "🌐 Translation:\n\n" + result,
        disable_web_page_preview=True,
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):

    logger.error(
        "Telegram error: %s",
        context.error,
        exc_info=True,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # =====================================================
    # COMMANDS
    # =====================================================

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "help",
            help_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "about",
            about_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "translate",
            translate_command,
        )
    )

    # =====================================================
    # ADMIN AUTO POST
    # =====================================================

    application.add_handler(
        CommandHandler(
            "autopost",
            autopost_command,
        )
    )

    # Short admin aliases: /list /delete /clear /on /off /test
    application.add_handler(CommandHandler("list", autopost_list_command))
    application.add_handler(CommandHandler("delete", autopost_delete_command))
    application.add_handler(CommandHandler("clear", autopost_clear_command))
    application.add_handler(CommandHandler("on", autopost_on_command))
    application.add_handler(CommandHandler("off", autopost_off_command))
    application.add_handler(CommandHandler("test", autopost_test_command))
    application.add_handler(CommandHandler("cancel", autopost_cancel_command))

    # =====================================================
    # BUTTONS
    # =====================================================

    application.add_handler(
        CallbackQueryHandler(
            autopost_callback_handler,
            pattern=r"^(autopost_list|autopost_open:\d+|autopost_(edit|delete|refresh|delete_yes|delete_no):\d+)$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            translate_last,
            pattern=r"^translate_last$",
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button_handler,
            pattern=r"^(about|translate_help)$",
        )
    )

    # =====================================================
    # TEXT MESSAGES
    # =====================================================

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_message,
        )
    )

    # =====================================================
    # ERROR
    # =====================================================

    application.add_error_handler(
        error_handler
    )

    # =====================================================
    # RENDER
    # =====================================================

    port = int(
        os.getenv(
            "PORT",
            "10000",
        )
    )

    render_url = os.getenv(
        "RENDER_EXTERNAL_URL"
    )

    # =====================================================
    # WEBHOOK
    # =====================================================

    if render_url:

        webhook_url = (
            render_url.rstrip("/")
            + "/telegram/"
            + BOT_TOKEN
        )

        logger.info(
            "Starting Render webhook..."
        )

        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="telegram/" + BOT_TOKEN,
            webhook_url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )

    # =====================================================
    # POLLING
    # =====================================================

    else:

        logger.info(
            "RENDER_EXTERNAL_URL not found."
        )

        logger.info(
            "Starting Telegram polling..."
        )

        application.run_polling(
            drop_pending_updates=True,
            allowed_updates=Update.ALL_TYPES,
        )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
