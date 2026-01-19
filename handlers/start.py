# handlers/start.py
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ChatMemberUpdated
from config import Config, app
from database import Database
from datetime import datetime, timezone, timedelta
import asyncio
import os
import sqlite3

db = Database()

# image paths (keep your filenames)
LOG_IMAGE_PATH = "log.jpg"
WELCOME_IMAGE_PATH = "welcome.jpg"
GROUP_LOG_IMAGE = "photo_2025-08-22_11-52-42.jpg"

# small emoji animation (fallback when reactions not available)
REACTION_SEQUENCE = ["🌸", "⛈️", "☀️"]
DELAY_BETWEEN = 0.5
EPHEMERAL_LIFETIME = 1.0

# In-process guards to avoid duplicate sends inside same process
_NOTIFIED_USERS = set()
_NOTIFIED_GROUPS = set()

# Ensure helper small tables to track notifications exist (best-effort)
def ensure_tracking_tables():
    try:
        cur = getattr(db, "cursor", None)
        conn = getattr(db, "conn", None)
        if cur is None:
            return
        cur.execute("""
        CREATE TABLE IF NOT EXISTS user_logs (
            user_id INTEGER PRIMARY KEY,
            started_at TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS group_logs (
            chat_id INTEGER PRIMARY KEY,
            title TEXT,
            added_at TEXT
        )
        """)
        if conn:
            conn.commit()
    except Exception:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

ensure_tracking_tables()

# Helpers
def is_private_chat(message) -> bool:
    try:
        t = getattr(message.chat, "type", None)
        if hasattr(t, "value"):
            return str(t.value).lower() == "private"
        if isinstance(t, str):
            return t.lower() == "private"
        if t is not None:
            return "private" in str(t).lower()
    except Exception:
        pass
    return False

def _atomic_insert_one(table: str, key_col: str, key_val, extra_cols=None):
    """
    Generic atomic insert-or-ignore using db.conn (BEGIN IMMEDIATE).
    Returns True if inserted (i.e. was not present), False otherwise.
    """
    try:
        conn = getattr(db, "conn", None)
        cur = getattr(db, "cursor", None)
        if conn is None or cur is None:
            # fallback non-atomic: try select + insert
            try:
                cur = getattr(db, "cursor")
                cur.execute(f"SELECT 1 FROM {table} WHERE {key_col} = ?", (key_val,))
                if cur.fetchone():
                    return False
                cols = [key_col]
                vals = [key_val]
                if extra_cols:
                    for c, v in extra_cols.items():
                        cols.append(c)
                        vals.append(v)
                placeholders = ",".join("?" for _ in cols)
                cur.execute(f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({placeholders})", tuple(vals))
                getattr(db, "conn").commit()
                return True
            except Exception:
                return False

        conn.execute("BEGIN IMMEDIATE")
        cur.execute(f"SELECT 1 FROM {table} WHERE {key_col} = ?", (key_val,))
        if cur.fetchone():
            conn.commit()
            return False

        cols = [key_col]
        vals = [key_val]
        if extra_cols:
            for c, v in extra_cols.items():
                cols.append(c)
                vals.append(v)
        placeholders = ",".join("?" for _ in cols)
        cur.execute(f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({placeholders})", tuple(vals))
        conn.commit()
        return True
    except sqlite3.OperationalError:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        return False
    except Exception:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        return False

async def download_profile_photo(client, user_id: int):
    # return local path or None
    try:
        async for p in client.get_chat_photos(user_id, limit=1):
            path = await client.download_media(p.file_id)
            return path
    except Exception:
        return None
    return None

# Atomic mark user started
def mark_user_started_atomic(user_id: int):
    # returns True if newly inserted
    return _atomic_insert_one("user_logs", "user_id", user_id, {"started_at": datetime.utcnow().isoformat()})

# Atomic mark group added
def mark_group_added_atomic(chat_id: int, title: str):
    return _atomic_insert_one("group_logs", "chat_id", chat_id, {"title": title, "added_at": datetime.utcnow().isoformat()})

# ---------------- START Handler ----------------
@app.on_message(filters.command("start"))
async def start_cmd(client, message):
    # ignore other bots
    if message.from_user and getattr(message.from_user, "is_bot", False):
        return

    # small emoji animation or reactions
    msg_id = getattr(message, "id", None) or getattr(message, "message_id", None)
    reacted = False
    if msg_id:
        for em in REACTION_SEQUENCE:
            try:
                await client.send_reaction(chat_id=message.chat.id, message_id=msg_id, emoji=em)
                reacted = True
            except Exception:
                reacted = False
                break
            await asyncio.sleep(DELAY_BETWEEN)

    if not reacted and msg_id:
        try:
            ephemeral = await client.send_message(chat_id=message.chat.id, reply_to_message_id=msg_id, text=REACTION_SEQUENCE[0])
            for frame in REACTION_SEQUENCE[1:]:
                await asyncio.sleep(DELAY_BETWEEN)
                try:
                    await client.edit_message_text(chat_id=ephemeral.chat.id, message_id=getattr(ephemeral, "id", getattr(ephemeral, "message_id", None)), text=frame)
                except Exception:
                    break
            if EPHEMERAL_LIFETIME > 0:
                await asyncio.sleep(EPHEMERAL_LIFETIME)
                try:
                    await client.delete_messages(chat_id=ephemeral.chat.id, message_ids=getattr(ephemeral, "id", getattr(ephemeral, "message_id", None)))
                except Exception:
                    pass
        except Exception:
            pass

    user = message.from_user
    user_id = user.id
    username = user.username if user.username else "None"
    first_name = user.first_name if user.first_name else "Unknown"

    # Save user via your DB helper if exists
    try:
        if hasattr(db, "add_user"):
            db.add_user(user_id, username, first_name)
    except Exception:
        pass

    # One-time support notification: attempt atomic mark, then if newly inserted send support message
    try:
        # fast in-process guard
        if user_id in _NOTIFIED_USERS:
            newly = False
        else:
            newly = mark_user_started_atomic(user_id)
            if newly:
                _NOTIFIED_USERS.add(user_id)

        if newly:
            source = "private" if is_private_chat(message) else getattr(message.chat, "title", "group")
            now = datetime.utcnow()
            try:
                ist = now.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))
                dt_str = ist.strftime("%d/%m/%Y %H:%M:%S")
            except Exception:
                dt_str = now.strftime("%d/%m/%Y %H:%M:%S")

            caption = (
                "🎀 NEW SOUL JOINED ALISA 🎀\n\n"
                f"👤 Name: {first_name}\n"
                f"🔗 Username: @{username}\n"
                f"🆔 User ID: `{user_id}`\n"
                f"📍 Source: {source}\n"
                f"📅 First Interaction: {dt_str} (IST)\n\n"
                "Welcome message delivered successfully 💖"
            )

            # try attach profile photo
            ppath = None
            try:
                ppath = await download_profile_photo(client, user_id)
                if ppath and os.path.exists(ppath):
                    try:
                        await client.send_photo(chat_id=Config.SUPPORT_CHAT_ID, photo=ppath, caption=caption)
                    except Exception:
                        await client.send_message(chat_id=Config.SUPPORT_CHAT_ID, text=caption)
                    finally:
                        try:
                            os.remove(ppath)
                        except Exception:
                            pass
                else:
                    await client.send_message(chat_id=Config.SUPPORT_CHAT_ID, text=caption)
            except Exception:
                try:
                    await client.send_message(chat_id=Config.SUPPORT_CHAT_ID, text=caption)
                except Exception:
                    pass
    except Exception:
        pass

    # Welcome message to user (or in group when /start used in group DM deep link)
    welcome_text = f"""
🌸 𝒲𝑒𝓁𝒸𝑜𝓂𝑒, 𝒟𝒶𝓇𝓁𝒾𝓃𝑔! 🌸

🍰 You’ve been warmly greeted by **Alisa Mikhailovna Kujou** 💕

👤 **User Info**:
🌸 Name: {first_name}
🏷️ Username: @{username}
🆔 ID: {user_id}

📜 **Available Commands:**
Type /help to explore 🎀

✨ “Let’s collect waifus and build memories together~” 💫
"""
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Add me to Group", url=f"https://t.me/{Config.BOT_USERNAME}?startgroup=true")],
        [InlineKeyboardButton("💬 Support Group", url=Config.SUPPORT_GROUP),
         InlineKeyboardButton("📢 Update Channel", url=Config.UPDATE_CHANNEL)],
        [InlineKeyboardButton("👑 Owner", url=f"https://t.me/{Config.OWNER_USERNAME.strip('@')}")]
    ])

    try:
        if os.path.exists(WELCOME_IMAGE_PATH):
            await message.reply_photo(photo=WELCOME_IMAGE_PATH, caption=welcome_text, reply_markup=buttons)
        else:
            await message.reply_text(text=welcome_text, reply_markup=buttons)
    except Exception:
        # ignore send errors (permissions, flood, etc.)
        pass

# ---------------- GROUP-ADDED Handler ----------------
@app.on_chat_member_updated()
async def bot_added_to_group(client, event: ChatMemberUpdated):
    try:
        new = getattr(event, "new_chat_member", None) or getattr(event, "new_chat_member", None)
        # Pyrogram shapes differ; check object user id
        new_user = None
        try:
            if new and getattr(new, "user", None):
                new_user = new.user
            elif getattr(event, "new_chat_member", None) and getattr(event.new_chat_member, "user", None):
                new_user = event.new_chat_member.user
        except Exception:
            new_user = None

        if not new_user:
            return

        if new_user.id != client.me.id:
            return

        chat = getattr(event, "chat", None) or getattr(event, "chat", None)
        if not chat:
            return
        chat_id = getattr(chat, "id", None)
        chat_title = getattr(chat, "title", "Unknown Group")

        # fast in-process guard
        if chat_id in _NOTIFIED_GROUPS:
            already = False
        else:
            already = mark_group_added_atomic(chat_id, chat_title)
            if already:
                _NOTIFIED_GROUPS.add(chat_id)

        # Send greeting inside the group (try once)
        try:
            group_greeting = (
                "✨ Thank you for welcoming me into this lovely group! ✨\n\n"
                "I’m *Alisa* 💕\n"
                "A waifu who brings beauty, fun, and magic to your chats 🌸\n\n"
                "🎴 Start collecting waifus TODAY\n"
                "🎮 Play games & earn rewards\n"
                "💞 Compete, trade, and rise together\n\n"
                "Type /start in private to begin your personal journey\nor use /help to see what I can do here 💫\n\n"
                "Let’s make this group more alive and adorable together~ 💖"
            )
            group_buttons = InlineKeyboardMarkup(
                [[InlineKeyboardButton("➕ Add me to your group", url=f"https://t.me/{Config.BOT_USERNAME}?startgroup=true")],
                 [InlineKeyboardButton("💬 Support Group", url=Config.SUPPORT_GROUP),
                  InlineKeyboardButton("📢 Update Channel", url=Config.UPDATE_CHANNEL)]]
            )
            await client.send_message(chat_id=chat_id, text=group_greeting, reply_markup=group_buttons)
        except Exception:
            pass

        # If we newly recorded this group, notify support group about the add (owner/inviter info best-effort)
        if already:
            inviter_name = "Unknown"
            inviter_username = "Unknown"
            inviter_id = "Unknown"
            try:
                inviter = getattr(event, "from_user", None) or getattr(event, "actor", None) or None
                if inviter:
                    inviter_name = getattr(inviter, "first_name", "Unknown")
                    inviter_username = getattr(inviter, "username", "Unknown")
                    inviter_id = getattr(inviter, "id", "Unknown")
            except Exception:
                pass

            now = datetime.utcnow()
            try:
                ist = now.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=5, minutes=30)))
                dt_str = ist.strftime("%d/%m/%Y %H:%M:%S")
            except Exception:
                dt_str = now.strftime("%d/%m/%Y %H:%M:%S")

            support_caption = (
                "🚀 BOT ADDED TO A NEW GROUP 🚀\n\n"
                f"💬 Group Name: **{chat_title}**\n"
                f"🆔 Group ID: `{chat_id}`\n\n"
                "👤 Added By:\n"
                f"• Name: {inviter_name}\n"
                f"• Username: @{inviter_username}\n"
                f"• User ID: `{inviter_id}`\n\n"
                f"📅 Time: {dt_str} (IST)\n\n"
                "Status:\n• Group greeting sent\n• Bot active\n\n— Alisa System 🤍"
            )
            try:
                if os.path.exists(GROUP_LOG_IMAGE):
                    await client.send_photo(chat_id=Config.SUPPORT_CHAT_ID, photo=GROUP_LOG_IMAGE, caption=support_caption)
                else:
                    await client.send_message(chat_id=Config.SUPPORT_CHAT_ID, text=support_caption)
            except Exception:
                pass

    except Exception:
        # protect bot from crashing due to unexpected event shapes
        pass