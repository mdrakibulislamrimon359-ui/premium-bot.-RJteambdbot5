# RJ OTP Universe Bot

Render-ready Telegram bot starter with user profiles, balance ledger,
referrals, admin controls, blocking and broadcast.

This version intentionally does not automate buying, intercepting, or forwarding
third-party verification OTPs. The OTP module is only a safe provider boundary.

## Render
Build: `pip install -r requirements.txt`
Start: `python bot.py`

Environment:
- BOT_TOKEN = Telegram bot token
- ADMIN_USERNAME = RJteam1 (or your admin username)
- ADMIN_IDS = optional comma-separated Telegram numeric IDs
- DATABASE_PATH = optional SQLite path
