# handlers/fav.py

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from config import app
from database import Database
import traceback

db = Database()

# ---------------- /fav Command ----------------
@app.on_message(filters.command("fav"))
async def set_favorite(client, message: Message):
    """
    Usage: /fav <waifu_id>
    Checks if the user owns the waifu before allowing them to set it as favourite.
    """
    try:
        if not message.from_user:
            return
        user_id = message.from_user.id
        username = message.from_user.first_name or "Unknown"
        text = (message.text or "").split(" ", 1)[1].strip()
        waifu_id = int(text)
    except (IndexError, ValueError):
        await message.reply_text("❌ Usage: /fav <waifu_id>")
        return

    # Ensure required tables exist (best-effort; won't overwrite existing schema)
    try:
        db.cursor.execute("""
            CREATE TABLE IF NOT EXISTS waifu_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT,
                anime TEXT,
                rarity TEXT,
                event TEXT,
                media_type TEXT,
                media_file TEXT
            )
        """)
        db.cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_fav (
                user_id INTEGER PRIMARY KEY,
                waifu_id INTEGER
            )
        """)
        # Do not create user_waifus if your DB already has it; only ensure exists if absent
        db.cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_waifus (
                user_id INTEGER,
                waifu_id INTEGER,
                amount INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, waifu_id)
            )
        """)
        db.conn.commit()
    except Exception:
        try:
            db.conn.rollback()
        except Exception:
            pass

    # Fetch waifu card and capture column names immediately
    try:
        db.cursor.execute("SELECT * FROM waifu_cards WHERE id = ?", (waifu_id,))
        waifu = db.cursor.fetchone()
        if not waifu:
            await message.reply_text("❌ Waifu card not found!")
            return
        # capture column names for this waifu query BEFORE any other execute()
        col_names = [desc[0] for desc in db.cursor.description] if db.cursor.description else []
        waifu_data = dict(zip(col_names, waifu))
    except Exception:
        traceback.print_exc()
        await message.reply_text("❌ Failed to fetch waifu. Try again later.")
        return

    # Check ownership: user must have amount > 0
    try:
        db.cursor.execute("SELECT amount FROM user_waifus WHERE user_id = ? AND waifu_id = ?", (user_id, waifu_id))
        r = db.cursor.fetchone()
        owned = int(r[0]) if r and r[0] is not None else 0
    except Exception:
        owned = 0

    if owned <= 0:
        await message.reply_text("❌ You don't own this waifu in your inventory. You can only favourite waifus you own.")
        return

    # Map values safely from waifu_data
    waifu_id = waifu_data.get("id", waifu_id)
    name = waifu_data.get("name", "Unknown")
    anime = waifu_data.get("anime", "—")
    rarity = waifu_data.get("rarity", "—")
    event = waifu_data.get("event", "—")
    media_type = (waifu_data.get("media_type") or "").lower()
    media_file = waifu_data.get("media_file")

    # Prepare preview caption
    caption = (
        f"🌸 Favorite Waifu Preview 🌸\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 𝐖𝐚𝐢𝐟𝐮 𝐈𝐃: {waifu_id}\n"
        f"✨ 𝐍𝐚𝐦𝐞: {name}\n"
        f"⛩️ 𝐀𝐧𝐢𝐦𝐞: {anime}\n"
        f"💖 𝐑𝐚𝐫𝐢𝐭𝐲: {rarity}\n"
        f"🎀 𝐄𝐯𝐞𝐧𝐭/𝐓𝐡𝐞𝐦𝐞: {event}\n"
        f"🕊️ Requested by: {username}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"Do you want to set this as your favorite waifu?"
    )

    # Inline buttons for confirmation
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data=f"fav_confirm|{user_id}|{waifu_id}"),
            InlineKeyboardButton("❌ Decline", callback_data=f"fav_decline|{user_id}|{waifu_id}")
        ]
    ])

    # Send media preview; fallback to text if media missing
    try:
        if media_type == "photo" and media_file:
            await message.reply_photo(media_file, caption=caption, reply_markup=buttons)
        elif media_type in ("video", "animation") and media_file:
            await message.reply_video(media_file, caption=caption, reply_markup=buttons)
        elif media_file:
            # unknown media type but file exists -> send as document
            await message.reply_document(media_file, caption=caption, reply_markup=buttons)
        else:
            await message.reply_text(caption, reply_markup=buttons)
    except Exception:
        # final fallback
        try:
            await message.reply_text(caption, reply_markup=buttons)
        except Exception:
            await message.reply_text("❌ Failed to send preview. Try again later.")


# ---------------- Callback Handler ----------------
@app.on_callback_query(filters.regex(r"^fav_"))
async def fav_callback(client, callback):
    data = callback.data.split("|")
    action = data[0] if len(data) > 0 else None

    # Ensure callback came from the same user who requested
    try:
        requested_user_id = int(data[1]) if len(data) > 1 else None
    except Exception:
        requested_user_id = None

    caller_id = callback.from_user.id if callback.from_user else None
    if requested_user_id is None or caller_id != requested_user_id:
        await callback.answer("Only the user who requested this can confirm/decline.", show_alert=True)
        return

    if action == "fav_confirm":
        try:
            waifu_id = int(data[2])
        except Exception:
            await callback.answer("Invalid data.", show_alert=True)
            return

        try:
            db.cursor.execute("REPLACE INTO user_fav (user_id, waifu_id) VALUES (?, ?)", (requested_user_id, waifu_id))
            db.conn.commit()
            await callback.answer("💞 Favorite waifu set successfully!", show_alert=True)
            try:
                await callback.message.delete()
            except Exception:
                pass
        except Exception:
            try:
                db.conn.rollback()
            except Exception:
                pass
            await callback.answer("❌ Failed to set favourite (DB error).", show_alert=True)

    elif action == "fav_decline":
        # decline flow — do not change DB
        try:
            await callback.answer("❌ Favorite waifu selection cancelled.", show_alert=True)
            try:
                await callback.message.delete()
            except Exception:
                pass
        except Exception:
            try:
                await callback.answer("❌ Could not cancel.", show_alert=True)
            except Exception:
                pass