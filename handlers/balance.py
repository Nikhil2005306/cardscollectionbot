# handlers/balance.py
from pyrogram import filters
from config import app
from database import Database

import time
import os
import tempfile

db = Database()

# ----------------- Helpers: ban & admin checks -----------------
def is_user_banned(user_id: int) -> bool:
    """
    Return True if user currently banned (banned_until > now).
    If ban expired, removes the ban row and returns False.
    """
    try:
        db.cursor.execute("SELECT banned_until FROM banned_users WHERE user_id = ?", (user_id,))
        row = db.cursor.fetchone()
        if not row:
            return False
        banned_until = row[0] or 0
        now = int(time.time())
        if banned_until > now:
            return True
        # expired -> cleanup
        db.cursor.execute("DELETE FROM banned_users WHERE user_id = ?", (user_id,))
        db.conn.commit()
        return False
    except Exception:
        return False


def is_admin(user_id: int) -> bool:
    """
    Returns True if user is admin.
    Checks config.ADMIN_IDS / config.ADMINS (single id or list) first, then DB table 'admins'.
    """
    try:
        import config as cfg
        for attr in ("ADMIN_IDS", "ADMINS", "ADMIN", "ADMIN_ID", "OWNER_ID"):
            val = getattr(cfg, attr, None)
            if val:
                if isinstance(val, (list, tuple, set)):
                    if user_id in val:
                        return True
                else:
                    try:
                        if int(val) == int(user_id):
                            return True
                    except Exception:
                        pass
    except Exception:
        pass

    try:
        db.cursor.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
        if db.cursor.fetchone():
            return True
    except Exception:
        pass

    return False


# Optional: create small support tables if missing (tolerant)
try:
    db.cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS banned_users (
            user_id INTEGER PRIMARY KEY,
            banned_until INTEGER
        )
        """
    )
    db.cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        )
        """
    )
    db.conn.commit()
except Exception:
    pass


# ----------------- Balance handler -----------------
@app.on_message(filters.command("balance"))
async def balance_cmd(client, message):
    user = message.from_user
    user_id = user.id

    # Respect ban filter: ignore banned users silently
    if is_user_banned(user_id):
        # Optionally remove the invoking message to reduce spam/noise
        try:
            await message.delete()
        except Exception:
            pass
        return

    # fetch crystals from DB (your existing method)
    try:
        daily, weekly, monthly, total, last_claim, given = db.get_crystals(user_id)
    except Exception:
        daily = weekly = monthly = given = 0
        total = 0
        last_claim = None

    display_name = user.first_name or (user.username or str(user_id))
    last_claim_str = last_claim if last_claim else "Never"

    caption_lines = [
        "🍄 Account Balance",
        "━━━━━━━━━━━━━━━━━",
        f"❖ User: {display_name}",
        "",
        f"ꔷ Crystal Available: {total}",
        "",
        "━━━━━━━━━━━━━━━━━",
    ]
    caption = "\n".join(caption_lines)

    # Try to get the largest profile photo and send it.
    # Download -> send -> delete the local file. Ensure exactly ONE message is sent.
    tmp_path = None
    try:
        photos = await client.get_profile_photos(user_id, limit=1)
        has_photo = bool(photos and getattr(photos, "total_count", 0) > 0 and photos.photos)
        if has_photo:
            sizes = photos.photos[0]  # list of PhotoSize objects
            if sizes:
                largest = sizes[-1]
                file_id = getattr(largest, "file_id", None)
                if file_id:
                    # download to temporary file
                    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                    tmp_path = tmp.name
                    tmp.close()
                    await client.download_media(file_id, file_name=tmp_path)
                    # send the photo with caption once, then return (so no duplicates)
                    await client.send_photo(message.chat.id, tmp_path, caption=caption)
                    # deletion handled in finally block
                    return
    except Exception:
        # If any error occurs in download/send, we'll fallback to text send below
        pass
    finally:
        # always remove downloaded file if exists
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

    # If we reach here, photo wasn't sent (no profile photo or failed) -> send single text message.
    try:
        await client.send_message(message.chat.id, caption)
    except Exception:
        # as last attempt, reply to the message (still single message)
        try:
            await message.reply_text(caption)
        except Exception:
            pass

    # done