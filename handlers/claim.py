# handlers/claim.py
"""
Claim handler (clean, patched).
- Adds rewarded waifu into user inventory (user_waifus).
- Owner bypasses cooldown (unlimited claims).
- Random selection limited to allowed rarities.
- Emoji animation: ⛅ -> ⛅⛈️ -> ⛅⛈️⛅
- Ensures necessary DB tables exist.
"""

import sqlite3
import random
import time
import asyncio
from pyrogram import filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from config import app, Config

# ---------- DB ----------
DB_PATH = getattr(Config, "DB_PATH", "waifu_bot.db")
db = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = db.cursor()

# Ensure required tables exist (best-effort)
cursor.execute("""
CREATE TABLE IF NOT EXISTS user_claims (
    user_id INTEGER PRIMARY KEY,
    last_claim INTEGER DEFAULT 0
)
""")
cursor.execute("""
CREATE TABLE IF NOT EXISTS user_waifus (
    user_id INTEGER,
    waifu_id INTEGER,
    amount INTEGER DEFAULT 0,
    last_collected INTEGER DEFAULT NULL,
    PRIMARY KEY (user_id, waifu_id)
)
""")
# waifu_cards expected to already exist. We won't re-create it here.
db.commit()

# ---------- Config / constants ----------
SUPPORT_USERNAME = "Alisabotsupport"                   # without @
UPDATE_CHANNEL_USERNAME = "AlisaMikhailovnaKujoui"     # without @
SUPPORT_LINK = "https://t.me/Alisabotsupport"
UPDATE_LINK = "https://t.me/AlisaMikhailovnaKujoui"

COOLDOWN = 86400  # 24 hours

# Animation sequence (frames)
ANIM_FRAMES = ["⛅", "⛅⛈️", "⛅⛈️⛅"]

# Allowed rarities for claim (human-readable keywords without emoji)
ALLOWED_RARITIES = [
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
    "Volt Resonant",
]

def like_params(keywords):
    return [f"%{k.strip()}%" for k in keywords]

# ---------- Helpers ----------
def get_remaining_cooldown(user_id: int) -> int:
    try:
        cursor.execute("SELECT last_claim FROM user_claims WHERE user_id = ?", (user_id,))
        row = cursor.fetchone()
        now_ts = int(time.time())
        if row and row[0]:
            last = int(row[0])
            if now_ts - last < COOLDOWN:
                return COOLDOWN - (now_ts - last)
    except Exception:
        pass
    return 0

def add_waifu_to_inventory(user_id: int, waifu_id: int, qty: int = 1) -> bool:
    """
    Insert/update user_waifus. Returns True if success.
    """
    try:
        cursor.execute("SELECT amount FROM user_waifus WHERE user_id = ? AND waifu_id = ?", (user_id, waifu_id))
        row = cursor.fetchone()
        now_ts = int(time.time())
        if row:
            cursor.execute(
                "UPDATE user_waifus SET amount = amount + ?, last_collected = ? WHERE user_id = ? AND waifu_id = ?",
                (qty, now_ts, user_id, waifu_id)
            )
        else:
            cursor.execute(
                "INSERT INTO user_waifus (user_id, waifu_id, amount, last_collected) VALUES (?, ?, ?, ?)",
                (user_id, waifu_id, qty, now_ts)
            )
        db.commit()
        return True
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        return False

async def is_member_of(client, chat_username: str, user_id: int) -> bool:
    """
    Check membership for a public username (returns False on error).
    """
    try:
        chat = chat_username if chat_username.startswith("@") else "@" + chat_username
        member = await client.get_chat_member(chat, user_id)
        status = getattr(member, "status", "")
        if status in ("left", "kicked"):
            return False
        return True
    except Exception:
        return False

# ---------- Core reward flow ----------
async def give_reward(client, chat_id: int, user_id: int, username: str, reply_to_message_id: int = None):
    """
    - Enforces cooldown (unless owner).
    - Picks random waifu from ALLOWED_RARITIES (LIKE matching).
    - Adds it to user's inventory and updates last_claim (unless owner).
    - Plays a small emoji animation and sends the media/caption.
    Returns (success: bool, err_text_or_None)
    """
    try:
        owner_id = getattr(Config, "OWNER_ID", None)
        is_owner = (owner_id is not None and int(owner_id) == int(user_id))

        # Cooldown check for non-owner
        if not is_owner:
            remaining = get_remaining_cooldown(user_id)
            if remaining > 0:
                hrs = remaining // 3600
                mins = (remaining % 3600) // 60
                return False, f"⏳ You already claimed a waifu! Come back in {hrs}h {mins}m."

        # select random waifu from allowed rarities with LIKE
        allowed_clause = " OR ".join("rarity LIKE ?" for _ in ALLOWED_RARITIES)
        query = f"""
            SELECT id, name, anime, rarity, event, media_type, media_file
            FROM waifu_cards
            WHERE ({allowed_clause})
            ORDER BY RANDOM()
            LIMIT 1
        """
        params = like_params(ALLOWED_RARITIES)
        cursor.execute(query, params)
        row = cursor.fetchone()

        # fallback: any waifu
        if not row:
            cursor.execute("""
                SELECT id, name, anime, rarity, event, media_type, media_file
                FROM waifu_cards
                ORDER BY RANDOM()
                LIMIT 1
            """)
            row = cursor.fetchone()

        if not row:
            return False, "❌ No waifu cards available."

        waifu_id, name, anime, rarity, event, media_type, media_file = row

        # add to inventory first (persistence)
        added = add_waifu_to_inventory(user_id, waifu_id, qty=1)
        if not added:
            return False, "❌ Failed to add waifu to your inventory (DB error)."

        # update last_claim for non-owner
        if not is_owner:
            try:
                now_ts = int(time.time())
                cursor.execute("INSERT OR REPLACE INTO user_claims (user_id, last_claim) VALUES (?, ?)", (user_id, now_ts))
                db.commit()
            except Exception:
                try:
                    db.rollback()
                except Exception:
                    pass

        # Try to send native reactions if message id given; ignore failures
        if reply_to_message_id is not None:
            try:
                for em in ANIM_FRAMES:
                    try:
                        await client.send_reaction(chat_id=chat_id, message_id=reply_to_message_id, emoji=em)
                    except Exception:
                        # continue even if reaction not supported
                        pass
            except Exception:
                pass

        # Fallback animation: send editable message frames then delete
        try:
            anim_msg = None
            try:
                if reply_to_message_id is not None:
                    anim_msg = await client.send_message(chat_id=chat_id, text=ANIM_FRAMES[0], reply_to_message_id=reply_to_message_id)
                else:
                    anim_msg = await client.send_message(chat_id=chat_id, text=ANIM_FRAMES[0])
            except Exception:
                anim_msg = None

            if anim_msg:
                for frame in ANIM_FRAMES[1:]:
                    await asyncio.sleep(0.6)
                    try:
                        await client.edit_message_text(chat_id=anim_msg.chat.id, message_id=getattr(anim_msg, "message_id", getattr(anim_msg, "id", None)), text=frame)
                    except Exception:
                        break
                await asyncio.sleep(0.6)
                try:
                    await client.delete_messages(anim_msg.chat.id, getattr(anim_msg, "message_id", getattr(anim_msg, "id", None)))
                except Exception:
                    pass
        except Exception:
            pass

        # Build caption
        caption = (
            f"🌸 Yay~ you caught a cutie! 🌸\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 ID: {waifu_id}\n"
            f"✨ Name: {name}\n"
            f"⛩️ Anime: {anime}\n"
            f"💖 Rarity: {rarity}\n"
            f"🎀 Event/Theme: {event}\n"
            f"🕊️ Claimed by: {username}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"{'👑 Owner unlimited claims applied' if is_owner else '⏳ Next claim ready in 24h~ 💫🎀'}"
        )

        # Send media (best-effort)
        try:
            if media_type and media_type.lower() == "photo":
                await client.send_photo(chat_id=chat_id, photo=media_file, caption=caption, reply_to_message_id=reply_to_message_id)
            elif media_type and media_type.lower() in ("video", "animation"):
                await client.send_video(chat_id=chat_id, video=media_file, caption=caption, reply_to_message_id=reply_to_message_id)
            else:
                await client.send_message(chat_id=chat_id, text=caption, reply_to_message_id=reply_to_message_id)
        except Exception:
            try:
                await client.send_message(chat_id=chat_id, text="✅ Claimed (but media delivery failed).", reply_to_message_id=reply_to_message_id)
            except Exception:
                pass

        return True, None

    except Exception as exc:
        try:
            db.rollback()
        except Exception:
            pass
        return False, f"❌ Unexpected error: {exc}"

# ---------- /claim command ----------
@app.on_message(filters.command("claim"))
async def claim_command(client, message: Message):
    try:
        user = message.from_user
        if not user:
            return
        user_id = user.id
        username = user.first_name or user.username or str(user_id)

        owner_id = getattr(Config, "OWNER_ID", None)
        is_owner = (owner_id is not None and int(owner_id) == int(user_id))

        # cooldown check for normal users
        if not is_owner:
            remaining = get_remaining_cooldown(user_id)
            if remaining > 0:
                hrs = remaining // 3600
                mins = (remaining % 3600) // 60
                await message.reply_text(f"⏳ You already claimed a waifu! Come back in {hrs}h {mins}m.")
                return

        # For non-owner, require joining support & update channels
        if not is_owner:
            joined_support = await is_member_of(client, SUPPORT_USERNAME, user_id)
            joined_update = await is_member_of(client, UPDATE_CHANNEL_USERNAME, user_id)
            if not (joined_support and joined_update):
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("💬 Join Support Group", url=SUPPORT_LINK)],
                    [InlineKeyboardButton("📢 Join Update Channel", url=UPDATE_LINK)],
                    [InlineKeyboardButton("✅ I've joined", callback_data=f"claim_joined:{user_id}")]
                ])
                await message.reply_text(
                    "🔒 You must join both the Support Group and the Update Channel to claim rewards.\n\n"
                    "Use the buttons below to join, then press ✅ I've joined to re-check membership.",
                    reply_markup=kb
                )
                return

        # All checks passed → give reward
        msg_id = getattr(message, "message_id", getattr(message, "id", None))
        success, info = await give_reward(client, message.chat.id, user_id, username, reply_to_message_id=msg_id)
        if not success and info:
            await message.reply_text(info)

    except Exception as e:
        try:
            await message.reply_text("❌ Error while processing claim. Try again later.")
        except Exception:
            pass
        print(f"claim_command error: {e}")

# ---------- callback for "I've joined" ----------
@app.on_callback_query(filters.regex(r"^claim_joined:(\d+)$"))
async def claim_joined_cb(client, callback: CallbackQuery):
    try:
        data = callback.data or ""
        parts = data.split(":")
        if len(parts) != 2:
            await callback.answer("Invalid data.", show_alert=True)
            return

        expected_user_id = int(parts[1])
        pressing_user_id = callback.from_user.id

        if pressing_user_id != expected_user_id:
            await callback.answer("This button isn't for you.", show_alert=True)
            return

        # cooldown re-check
        remaining = get_remaining_cooldown(pressing_user_id)
        if remaining > 0:
            hrs = remaining // 3600
            mins = (remaining % 3600) // 60
            await callback.answer(f"⏳ You already claimed a waifu! Come back in {hrs}h {mins}m.", show_alert=True)
            return

        joined_support = await is_member_of(client, SUPPORT_USERNAME, pressing_user_id)
        joined_update = await is_member_of(client, UPDATE_CHANNEL_USERNAME, pressing_user_id)
        if not (joined_support and joined_update):
            try:
                await callback.message.edit_text(
                    "🔒 You're still not a member of both chats. Use the buttons to join, then press ✅ I've joined again.",
                    reply_markup=callback.message.reply_markup
                )
            except Exception:
                pass
            await callback.answer("Still not a member of both chats.", show_alert=True)
            return

        msg_id = getattr(callback.message, "message_id", getattr(callback.message, "id", None))
        success, info = await give_reward(client, callback.message.chat.id, pressing_user_id, callback.from_user.first_name, reply_to_message_id=msg_id)
        if not success and info:
            await callback.answer(info, show_alert=True)
            return

        try:
            await callback.message.edit_text("✅ Claim successful! Check the waifu card sent above.")
        except Exception:
            pass

        await callback.answer("Claim granted! 🎉")

    except Exception as e:
        try:
            await callback.answer("An error occurred. Try /claim again.", show_alert=True)
        except Exception:
            pass
        print(f"claim_joined_cb error: {e}")