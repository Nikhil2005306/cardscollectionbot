# handlers/addwaifu.py
# Interactive /addwaifu flow with duplicate-handler & race fixes,
# plus support-group notification after a card is added.
#
# Usage:
# 1) /addwaifu
# 2) send photo/video
# 3) send waifu name
# 4) send anime name
# 5) choose rarity (inline)
# 6) choose event (inline)
# 7) confirm preview -> saved to DB and notify support group (if configured)

from pyrogram import filters
from pyrogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    Message,
    CallbackQuery
)
from config import Config, app
from database import Database
import uuid
import typing

db = Database()
db.ensure_waifu_cards_schema()

# In-memory state
SESSIONS: typing.Dict[str, dict] = {}
TOKENS: typing.Dict[str, str] = {}
PENDING_ADDS: typing.Dict[str, dict] = {}
# Prevent duplicate callback handling across duplicate-registered handlers
PROCESSING_TOKENS: typing.Set[str] = set()

# Allowed users
ALLOWED_IDS = {getattr(Config, "OWNER_ID", 0)}
ALLOWED_IDS.update(getattr(Config, "ADMINS", []))  # ensure Config.ADMINS is list-like

def is_allowed(user_id: int) -> bool:
    return user_id in ALLOWED_IDS

def short_token() -> str:
    return uuid.uuid4().hex[:10]

# Rarities & Events per your requested labels
RARITIES = [
    "🌸 Common Blossom", "🌼 Charming Glow", "🌹 Elegant Rose", "💫 Rare Sparkle",
    "🔥 Enchanted Flame", "🎐 Animated Spirit", "🌈 Chroma Pulse", "🧚 Mythical Grace",
    "🦋 Ethereal Whisper", "🧊 Frozen Aurora", "⚡️ Volt Resonant", "🪞 Holographic Mirage",
    "🌪 Phantom Tempest", "🕊 Celestia Bloom", "👑 Divine Ascendant", "🔮 Timewoven Relic",
    "💋 Forbidden Desire", "📽 Cinematic Legend"
]

EVENTS = [
    "🩺 Nurse","🐰 Bunny","🎀 Maid","🎃 Halloween","🎄 Christmas","🤵 Tuxedo","❄️ Winter","👘 Kimono",
    "🏫 School","🎀 Saree","☀️ Summer","🏀 Basketball","⚽ Football","🐫 Egypt","❤️ Valentine",
    "👥 Duo","👨‍👩‍👧‍👦 Group","🏮 Chinese","📚 Manhwa","👙 Bikini","📣 Cheerleaders","🎮 Game",
    "💍 Married","🕷️ Spider","🧸 Chibi","🧛 Vampire","🙏 Nun","🏐 Volleyball"
]

# Helpers
def make_session_key(user_id: int, chat_id: int) -> str:
    return f"{user_id}:{chat_id}"

def build_keyboard_from_list(items: list, prefix: str, token: str, per_row: int = 3):
    buttons = []
    row = []
    for idx, label in enumerate(items):
        cb = f"{prefix}:{token}:{idx}"
        row.append(InlineKeyboardButton(label, callback_data=cb))
        if len(row) >= per_row:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(buttons)

# Start command
@app.on_message(filters.command("addwaifu"))
async def add_waifu_start(client, message: Message):
    user = message.from_user
    user_id = user.id if user else 0
    if not is_allowed(user_id):
        await message.reply_text("⛔ Owner/Admin only command.")
        return

    chat_id = message.chat.id
    sk = make_session_key(user_id, chat_id)

    # Prevent duplicate sessions being created (helps if handler registered twice)
    if sk in SESSIONS:
        await message.reply_text("ℹ️ You already have an active add-waifu session. Send /canceladd to cancel it.")
        return

    SESSIONS[sk] = {
        "owner": user_id,
        "chat_id": chat_id,
        "state": "await_media",
        "media_type": None,
        "media_file_id": None,
        "name": None,
        "anime": None,
        "rarity": None,
        "event": None,
        "prompt_message_id": None
    }

    msg = await message.reply_text(
        "📸 Please send the waifu image or video now.\n\n"
        "Send a photo or a video in this chat. To cancel at any time, send /canceladd."
    )
    # pyrogram versions differ: try .message_id then .id
    msg_id = getattr(msg, "message_id", None) or getattr(msg, "id", None)
    SESSIONS[sk]["prompt_message_id"] = msg_id

# Cancel command
@app.on_message(filters.command("canceladd"))
async def cancel_add_command(client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    sk = make_session_key(user_id, message.chat.id)
    if sk in SESSIONS:
        SESSIONS.pop(sk, None)
        await message.reply_text("❌ Add-waifu session cancelled.")
    else:
        await message.reply_text("ℹ️ You don't have an active add-waifu session.")

# Media handler - exclude commands using regex ^/
@app.on_message((filters.photo | filters.video) & (~filters.regex(r"^/")))
async def handle_media_messages(client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    sk = make_session_key(user_id, message.chat.id)
    session = SESSIONS.get(sk)
    if not session:
        return
    if session.get("state") != "await_media":
        return

    media_type = None
    media_file_id = None

    # message.photo may be a list or a Photo object depending on pyrogram version
    if message.photo:
        media_type = "photo"
        try:
            if isinstance(message.photo, (list, tuple)):
                media_file_id = message.photo[-1].file_id
            else:
                media_file_id = message.photo.file_id
        except Exception:
            media_file_id = getattr(message.photo, "file_id", None)
    elif message.video:
        media_type = "video"
        media_file_id = getattr(message.video, "file_id", None)

    if not media_file_id:
        await message.reply_text("❌ Could not identify uploaded media. Please send again.")
        return

    session["media_type"] = media_type
    session["media_file_id"] = media_file_id
    session["state"] = "await_name"

    await message.reply_text("✏️ Got it. Now send the **Waifu Name** (text).")

# Text handler for name/anime steps
@app.on_message(filters.text & (~filters.regex(r"^/")))
async def handle_text_steps(client, message: Message):
    user_id = message.from_user.id if message.from_user else 0
    sk = make_session_key(user_id, message.chat.id)
    session = SESSIONS.get(sk)
    if not session:
        return

    # Only session owner may continue
    if user_id != session.get("owner"):
        return

    state = session.get("state")
    text = message.text.strip()

    if state == "await_name":
        session["name"] = text
        session["state"] = "await_anime"
        await message.reply_text("🏯 Name saved. Now send the **Anime Name** (text).")
        return

    if state == "await_anime":
        session["anime"] = text
        session["state"] = "rarity_choice"

        token = short_token()
        TOKENS[token] = sk
        kb = build_keyboard_from_list(RARITIES, prefix="aw_rarity", token=token, per_row=2)
        await message.reply_text("💎 Choose the rarity for this waifu (tap a button):", reply_markup=kb)
        return

    # ignore other text in other states
    return

# Rarity callback
@app.on_callback_query(filters.regex(r"^aw_rarity:"))
async def rarity_chosen(client, cq: CallbackQuery):
    # parse and protect against duplicate handler runs
    try:
        _, token, idx_s = cq.data.split(":")
        idx = int(idx_s)
    except Exception:
        await cq.answer("Invalid selection.", show_alert=True)
        return

    # If another handler is already processing this token, silently return
    if token in PROCESSING_TOKENS:
        await cq.answer()  # brief response to stop loading spinner
        return

    PROCESSING_TOKENS.add(token)
    try:
        sk = TOKENS.get(token)
        if not sk:
            await cq.answer("This selection expired. Please restart with /addwaifu.", show_alert=True)
            return

        session = SESSIONS.get(sk)
        if not session:
            await cq.answer("Session not found or expired.", show_alert=True)
            TOKENS.pop(token, None)
            return

        user_id = cq.from_user.id if cq.from_user else 0
        if user_id != session.get("owner") or not is_allowed(user_id):
            await cq.answer("⛔ Owner/Admin only.", show_alert=True)
            return

        try:
            rarity = RARITIES[idx]
        except Exception:
            await cq.answer("Invalid rarity index.", show_alert=True)
            return

        session["rarity"] = rarity
        session["state"] = "event_choice"
        # one-time token consumption for rarity stage
        TOKENS.pop(token, None)

        token_ev = short_token()
        TOKENS[token_ev] = sk
        kb_ev = build_keyboard_from_list(EVENTS, prefix="aw_event", token=token_ev, per_row=3)

        await cq.answer()
        try:
            await cq.message.reply_text(
                f"Selected rarity: {rarity}\n\nNow choose an Event/Theme (tap a button):",
                reply_markup=kb_ev
            )
        except Exception:
            try:
                await cq.message.edit_text(
                    f"Selected rarity: {rarity}\n\nNow choose an Event/Theme (tap a button):",
                    reply_markup=kb_ev
                )
            except:
                pass
    finally:
        PROCESSING_TOKENS.discard(token)

# Event callback -> preview
@app.on_callback_query(filters.regex(r"^aw_event:"))
async def event_chosen(client, cq: CallbackQuery):
    try:
        _, token, idx_s = cq.data.split(":")
        idx = int(idx_s)
    except Exception:
        await cq.answer("Invalid selection.", show_alert=True)
        return

    if token in PROCESSING_TOKENS:
        await cq.answer()
        return

    PROCESSING_TOKENS.add(token)
    try:
        sk = TOKENS.get(token)
        if not sk:
            await cq.answer("This selection expired. Please restart with /addwaifu.", show_alert=True)
            return

        session = SESSIONS.get(sk)
        if not session:
            await cq.answer("Session not found or expired.", show_alert=True)
            TOKENS.pop(token, None)
            return

        user_id = cq.from_user.id if cq.from_user else 0
        if user_id != session.get("owner") or not is_allowed(user_id):
            await cq.answer("⛔ Owner/Admin only.", show_alert=True)
            return

        try:
            event = EVENTS[idx]
        except Exception:
            await cq.answer("Invalid event index.", show_alert=True)
            return

        session["event"] = event
        session["state"] = "preview"

        preview_token = short_token()
        payload = {
            "name": session["name"],
            "anime": session["anime"],
            "rarity": session["rarity"],
            "event": session["event"],
            "media_type": session["media_type"],
            "media_file_id": session["media_file_id"],
            "owner": session["owner"]
        }
        PENDING_ADDS[preview_token] = payload

        # cleanup session & token (we won't need session anymore)
        SESSIONS.pop(sk, None)
        TOKENS.pop(token, None)

        caption = (
            "🌸 New Waifu Card Preview 🌸\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"✨ Name: {payload['name']}\n"
            f"⛩️ Anime: {payload['anime']}\n"
            f"💎 Rarity: {payload['rarity']}\n"
            f"🎀 Event/Theme: {payload['event']}\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"{'📷 [Image Attached]' if payload['media_type'] == 'photo' else '📽️ [Video Attached]'}\n\n"
            "👉 Do you want to add this waifu to the collection?"
        )

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm", callback_data=f"aw_ok:{preview_token}")],
            [InlineKeyboardButton("❌ Cancel",  callback_data=f"aw_no:{preview_token}")]
        ])

        await cq.answer()
        try:
            if payload["media_type"] == "photo":
                await cq.message.reply_photo(payload["media_file_id"], caption=caption, reply_markup=buttons)
            else:
                await cq.message.reply_video(payload["media_file_id"], caption=caption, reply_markup=buttons)
        except Exception as e:
            await cq.message.reply_text(f"{caption}\n\n(Note: failed to attach media: {e})", reply_markup=buttons)
    finally:
        PROCESSING_TOKENS.discard(token)

# Final confirm / cancel callback
@app.on_callback_query(filters.regex(r"^aw_(ok|no):"))
async def add_waifu_callback(client, cq: CallbackQuery):
    # parse
    try:
        action, token = cq.data.split(":")
    except Exception:
        await cq.answer("Invalid action.", show_alert=True)
        return

    # Prevent duplicate processing from duplicate handler registration
    if token in PROCESSING_TOKENS:
        await cq.answer()
        return

    PROCESSING_TOKENS.add(token)
    try:
        user_id = cq.from_user.id if cq.from_user else 0
        if not is_allowed(user_id):
            await cq.answer("⛔ Owner/Admin only.", show_alert=True)
            return

        # Atomically claim the preview (pop)
        payload = PENDING_ADDS.pop(token, None)

        if not payload:
            await cq.answer("❌ This preview expired or was already handled. Please run /addwaifu again.", show_alert=True)
            try:
                await cq.message.edit_reply_markup(None)
            except:
                pass
            return

        # Only owner may confirm/cancel
        if user_id != payload.get("owner"):
            # don't re-insert payload: deny
            await cq.answer("⛔ Only the user who started this add can confirm/cancel.", show_alert=True)
            return

        if action == "aw_no":
            await cq.answer("❌ Cancelled.")
            try:
                await cq.message.edit_text("❌ Waifu addition cancelled.")
            except:
                try:
                    await cq.message.edit_reply_markup(None)
                except:
                    pass
            return

        # Confirm -> save DB
        try:
            db.ensure_waifu_cards_schema()
            db.cursor.execute("""
                INSERT INTO waifu_cards (name, anime, rarity, event, media_type, media_file, media_file_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                payload["name"],
                payload["anime"],
                payload["rarity"],
                payload["event"],
                payload["media_type"],
                payload["media_file_id"],
                payload["media_file_id"]
            ))
            db.conn.commit()
            new_id = db.cursor.lastrowid

            await cq.answer("✅ Saved!", show_alert=False)
            final_caption = (
                "✅ Waifu Saved!\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                f"🆔 Waifu ID: {new_id}\n"
                f"✨ Name: {payload['name']}\n"
                f"⛩️ Anime: {payload['anime']}\n"
                f"💎 Rarity: {payload['rarity']}\n"
                f"🎀 Event/Theme: {payload['event']}\n"
            )
            try:
                await cq.message.edit_text(final_caption)
            except:
                await cq.message.reply_text(final_caption)

            # Notify support group if configured
            support_chat = getattr(Config, "SUPPORT_CHAT_ID", None)
            if support_chat:
                try:
                    admin = cq.from_user
                    admin_name = f"@{admin.username}" if getattr(admin, "username", None) else (admin.first_name or "Unknown")
                    notify_text = (
                        f"📣 New Waifu Card Added\n"
                        f"🆔 ID: {new_id}\n"
                        f"👤 Added by: {admin_name}\n"
                        f"✨ Name: {payload['name']}\n"
                        f"⛩️ Anime: {payload['anime']}\n"
                        f"💎 Rarity: {payload['rarity']}\n"
                        f"🎀 Event: {payload['event']}\n"
                    )
                    # send to support group/chat
                    await app.send_message(int(support_chat), notify_text)
                except Exception:
                    # ignore notify errors (do not crash)
                    pass

        except Exception as e:
            await cq.answer("❌ Failed to save.", show_alert=True)
            try:
                await cq.message.edit_text(f"❌ Error while saving: {e}")
            except:
                pass
    finally:
        PROCESSING_TOKENS.discard(token)