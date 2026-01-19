# config.py
import os
from pyrogram import Client

class Config:
    # credentials (DO NOT hardcode)
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    API_ID = int(os.getenv("API_ID", 0))
    API_HASH = os.getenv("API_HASH")

    # database
    DB_PATH = os.getenv("DB_PATH", "waifu_bot.db")

    # owner & admin
    OWNER_ID = int(os.getenv("OWNER_ID", 0))
    ADMINS = [7606646849, 7558715645, 6398668820]

    OWNER_USERNAME = "@Professornikhil"
    SUPPORT_GROUP = "https://t.me/Alisabotsupport"
    SUPPORT_CHAT_ID = -1002669919337
    UPDATE_CHANNEL = "https://t.me/AlisaMikhailovnaKujoui"
    BOT_USERNAME = "Waifusscollectionbot"

    # rewards
    DAILY_CRYSTAL = 5000
    WEEKLY_CRYSTAL = 25000
    MONTHLY_CRYSTAL = 50000

# keep app creation same style (minimal change)
app = Client(
    "waifu_bot",
    api_id=Config.API_ID,
    api_hash=Config.API_HASH,
    bot_token=Config.BOT_TOKEN,
)

OWNER_ID = Config.OWNER_ID
ADMINS = Config.ADMINS
