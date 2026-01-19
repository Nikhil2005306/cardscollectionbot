# handlers/search.py
from main import app
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import Database

db = Database()


def format_user_label(user_row):
    """Return the best display name for a user row (username / first_name / id)."""
    if not user_row:
        return "Unknown"
    user_id, username, first_name = user_row
    if username:
        return f"@{username}"
    if first_name:
        return first_name
    return str(user_id)


async def send_waifu_details(client, chat_id, waifu_row):
    """
    waifu_row is a tuple:
    (id, name, anime, rarity, event, media_type, media_file, media_file_id)
    """
    (wid, name, anime, rarity, event, media_type, media_file, media_file_id) = waifu_row

    # Top 5 collectors for this waifu
    db.cursor.execute("""
        SELECT uw.user_id, uw.amount, u.username, u.first_name
        FROM user_waifus uw
        LEFT JOIN users u ON uw.user_id = u.user_id
        WHERE uw.waifu_id = ?
        ORDER BY uw.amount DESC
        LIMIT 5
    """, (wid,))
    collectors = db.cursor.fetchall()

    collectors_lines = []
    if collectors:
        for i, (uid, amount, uname, fname) in enumerate(collectors, start=1):
            label = f"@{uname}" if uname else (fname if fname else str(uid))
            collectors_lines.append(f"{i}. {label} — {amount}×")
    else:
        collectors_lines.append("No collectors yet.")

    caption = (
        f"📛 ID: {wid}\n"
        f"💠 Name: {name}\n"
        f"🎬 Anime: {anime or '—'}\n"
        f"✨ Rarity: {rarity or '—'}\n"
        f"🎭 Theme: {event or '—'}\n\n"
        f"🏆 Top collectors:\n" + "\n".join(collectors_lines)
    )

    # Choose media to send (prefer media_file_id if present)
    media = media_file_id or media_file

    try:
        if media_type == "photo":
            if media:
                await client.send_photo(chat_id, media, caption=caption)
            else:
                await client.send_message(chat_id, caption)
        elif media_type == "video":
            if media:
                await client.send_video(chat_id, media, caption=caption)
            else:
                await client.send_message(chat_id, caption)
        else:
            # unknown media type: just send caption and try to send media if any
            if media:
                await client.send_photo(chat_id, media, caption=caption)
            else:
                await client.send_message(chat_id, caption)
    except Exception:
        # fallback: send caption only
        await client.send_message(chat_id, caption)


@app.on_message(filters.command("search"))
async def search_handler(client, message):
    text = message.text or ""
    parts = text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return await message.reply_text("Usage: /search <waifu name>\nExample: /search rem")

    query = parts[1].strip()
    like = f"%{query}%"

    # Find matching waifu cards (case-insensitive)
    db.cursor.execute("""
        SELECT id, name, anime, rarity, event, media_type, media_file, media_file_id
        FROM waifu_cards
        WHERE name LIKE ? COLLATE NOCASE
        LIMIT 50
    """, (like,))
    rows = db.cursor.fetchall()

    if not rows:
        return await message.reply_text(f"No waifu found matching `{query}`.", quote=True)

    if len(rows) == 1:
        # only one result — show it directly
        await send_waifu_details(client, message.chat.id, rows[0])
        return

    # multiple results — show list with buttons to pick the correct waifu
    buttons = []
    for r in rows[:10]:  # show up to 10 matches inline
        wid, name, *_ = r
        label = f"{name} (ID:{wid})"
        buttons.append([InlineKeyboardButton(label[:40], callback_data=f"search_select:{wid}")])

    # extra row: cancel
    buttons.append([InlineKeyboardButton("❌ Close", callback_data="search_close")])

    preview_lines = [f"Found {len(rows)} matches for `{query}`. Tap one to view details (showing up to 10)."]
    await message.reply_text("\n".join(preview_lines), reply_markup=InlineKeyboardMarkup(buttons), quote=True)


@app.on_callback_query(filters.regex(r"^search_select:"))
async def search_select_cb(client, callback):
    try:
        wid = int(callback.data.split(":")[1])
    except Exception:
        await callback.answer("Invalid selection.", show_alert=True)
        return

    db.cursor.execute("""
        SELECT id, name, anime, rarity, event, media_type, media_file, media_file_id
        FROM waifu_cards
        WHERE id = ?
    """, (wid,))
    row = db.cursor.fetchone()
    if not row:
        await callback.answer("Waifu not found.", show_alert=True)
        return

    # show the selected waifu details
    await send_waifu_details(client, callback.message.chat.id, row)
    # optionally remove the selection message
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()


@app.on_callback_query(filters.regex(r"^search_close"))
async def search_close_cb(client, callback):
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()
