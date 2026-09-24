import os

BOT_NAME = os.getenv("BOT_NAME", "RJ OTP Universe Bot")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "RJteam1").lstrip("@")
DATABASE_PATH = os.getenv("DATABASE_PATH", "rj_otp_universe.db")

ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}
