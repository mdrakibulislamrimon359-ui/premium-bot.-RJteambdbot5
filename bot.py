import os
import asyncio
import logging
import random

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
    # BUTTONS
    # =====================================================

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
