# handlers/setdrop.py

import sqlite3
from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import Config, app

DB_PATH = "waifu_bot.db"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

# Ensure current_drops table exists
cursor.execute("""
CREATE TABLE IF NOT EXISTS current_drops (
    chat_id INTEGER PRIMARY KEY,
    waifu_id INTEGER,
    collected_by INTEGER DEFAULT NULL
)
""")
conn.commit()

# In-memory drop counter
drop_settings = {}  # {chat_id: {"target": int, "count": int}}

# ---------------- Allowed rarities (human keywords, no emoji) ----------------
# We'll match these with SQL LIKE to be robust against emoji/spacing differences in DB.
ALLOWED_KEYWORDS = [
    "Common Blossom",
    "Charming Glow",
    "Elegant Rose",
    "Rare Sparkle",
    "Enchanted Flame",
    "Animated Spirit",
    "Chroma Pulse",
    "Mythical Grace",
    "Ethereal Whisper",
    "Frozen Aurora",
]

# ---------------- Blocked rarities (human keywords, no emoji) ----------------
BLOCKED_KEYWORDS = [
    "Volt Resonant",
    "Holographic Mirage",
    "Phantom Tempest",
    "Celestia Bloom",
    "Divine Ascendant",
    "Timewoven Relic",
    "Forbidden Desire",
    "Cinematic Legend",
]

# Helper to build LIKE params
def like_params(keywords):
    return [f"%{k.strip()}%" for k in keywords]


# ---------------- /setdrop Command ----------------
@app.on_message(filters.command("setdrop") & filters.group, group=1)
async def set_drop(client, message: Message):
    user_id = message.from_user.id
    chat_id = message.chat.id

    # Validate input
    try:
        target_msg = int(message.text.split(" ", 1)[1])
    except (IndexError, ValueError):
        await message.reply_text("❌ Usage: /setdrop <number_of_messages>")
        return

    # -------- Proper Limits --------
    if user_id == Config.OWNER_ID:
        if target_msg < 1:
            await message.reply_text("👑 Owner can set drop to minimum 1 message.")
            return
    elif user_id in Config.ADMINS:
        if target_msg < 20:
            await message.reply_text("⚠️ Admins cannot set drop below 20 messages.")
            return
    else:
        if target_msg < 60:
            await message.reply_text("⚠️ Normal users cannot set drop below 60 messages.")
            return

    # Set drop
    drop_settings[chat_id] = {"target": target_msg, "count": 0}
    await message.reply_text(f"✅ Card drop set! A random card will drop after {target_msg} messages in this group.")


# ---------------- /dropcount Command ----------------
@app.on_message(filters.command("dropcount") & filters.group, group=1)
async def drop_count(client, message: Message):
    chat_id = message.chat.id
    if chat_id not in drop_settings:
        await message.reply_text("ℹ️ No card drop is configured for this group. Use /setdrop to enable drops.")
        return

    remaining = drop_settings[chat_id]["target"] - drop_settings[chat_id]["count"]
    if remaining < 0:
        remaining = 0
    await message.reply_text(f"🎴 Messages remaining until next drop: {remaining}")


# ---------------- Message Tracker ----------------
@app.on_message(filters.group, group=2)  # lower priority, runs after /start
async def drop_tracker(client, message: Message):
    chat_id = message.chat.id

    # Ignore service messages
    if message.service:
        return

    # Ignore commands so /start and other commands are not blocked
    if message.text and message.text.startswith("/"):
        return

    if chat_id not in drop_settings:
        return

    drop_settings[chat_id]["count"] += 1
    if drop_settings[chat_id]["count"] < drop_settings[chat_id]["target"]:
        return

    # Reset counter
    drop_settings[chat_id]["count"] = 0

    # Try 1: select random card matching allowed keywords and NOT matching blocked keywords
    try:
        allowed_clause = " OR ".join("rarity LIKE ?" for _ in ALLOWED_KEYWORDS)
        blocked_clause = " OR ".join("rarity LIKE ?" for _ in BLOCKED_KEYWORDS)

        query = f"""
            SELECT id, name, anime, rarity, event, media_type, media_file
            FROM waifu_cards
            WHERE ({allowed_clause})
              AND NOT ({blocked_clause})
            ORDER BY RANDOM()
            LIMIT 1
        """
        params = like_params(ALLOWED_KEYWORDS) + like_params(BLOCKED_KEYWORDS)
        cursor.execute(query, params)
        card = cursor.fetchone()

        # If nothing found, fallback to selecting any card that does NOT match blocked keywords
        if not card:
            blocked_clause_only = " OR ".join("rarity LIKE ?" for _ in BLOCKED_KEYWORDS)
            query2 = f"""
                SELECT id, name, anime, rarity, event, media_type, media_file
                FROM waifu_cards
                WHERE NOT ({blocked_clause_only})
                ORDER BY RANDOM()
                LIMIT 1
            """
            params2 = like_params(BLOCKED_KEYWORDS)
            cursor.execute(query2, params2)
            card = cursor.fetchone()

        if not card:
            # Still none — there are no allowed cards in DB (or DB rarities are very different).
            # Do not drop in this case (prevents sending forbidden rarities).
            print("❌ setdrop: no eligible cards found for drop (allowed/blocklist filtering).")
            return

    except Exception as e:
        print(f"❌ Error fetching card: {e}")
        return

    # Save drop
    cursor.execute(
        "INSERT OR REPLACE INTO current_drops (chat_id, waifu_id, collected_by) VALUES (?, ?, NULL)",
        (chat_id, card[0])
    )
    conn.commit()

    # Prepare single deep-link PM button (only one button)
    try:
        me = await client.get_me()
        bot_username = me.username if me and me.username else None
    except Exception:
        bot_username = None

    buttons = None
    if bot_username:
        pm_url = f"https://t.me/{bot_username}?start=card_{card[0]}"
        buttons = InlineKeyboardMarkup(
            [[InlineKeyboardButton("Open in PM for details", url=pm_url)]]
        )

    # Send drop message
    drop_text = "🎉 A new waifu card has appeared! 🎴\nType /collect <name> to claim it before someone else!"
    try:
        if card[5] == "photo":
            await message.reply_photo(card[6], caption=drop_text, reply_markup=buttons)
        else:
            await message.reply_video(card[6], caption=drop_text, reply_markup=buttons)
    except Exception as e:
        print(f"❌ Failed to send drop: {e}")


# ---------------- Private /start handler to show card details when opened via deep link ----------------
# NOTE: This handler only reacts to start parameters that begin with `card_` and will send the card's details in PM.
# It is intentionally minimal and only runs in private chats so it won't interfere with group /start behavior.
@app.on_message(filters.private & filters.command("start"), group=3)
async def start_with_card(client, message: Message):
    # message.command -> ['/start', 'payload'] if any
    if len(message.command) < 2:
        return
    payload = message.command[1]
    if not payload.startswith("card_"):
        return

    try:
        waifu_id = int(payload.split("_", 1)[1])
    except Exception:
        return

    try:
        cursor.execute(
            "SELECT id, name, anime, rarity, event, media_type, media_file FROM waifu_cards WHERE id = ?",
            (waifu_id,)
        )
        card = cursor.fetchone()
        if not card:
            await message.reply_text("❌ Card not found.")
            return

        caption_lines = [
            f"🎴 Name: {card[1]}",
            f"📺 Anime: {card[2]}",
            f"✨ Rarity: {card[3]}",
        ]
        if card[4]:
            caption_lines.append(f"🏷 Event: {card[4]}")

        caption = "\n".join(caption_lines)

        if card[5] == "photo":
            await client.send_photo(message.chat.id, card[6], caption=caption)
        else:
            await client.send_video(message.chat.id, card[6], caption=caption)
    except Exception as e:
        print(f"❌ Failed to send PM card details: {e}")
        try:
            await message.reply_text("❌ Failed to fetch card details. Try again later.")
        except Exception:
            pass
