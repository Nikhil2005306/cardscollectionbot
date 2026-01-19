# handlers/gift.py
"""
Gift / MassGift handlers for Waifu bot.

Commands:
  /gift <waifu_id>    (reply to a user's message)
    - Shows a preview to the sender with Confirm / Decline inline buttons.
    - On Confirm: transfers 1 of the specified waifu from sender -> recipient.
    - Only the sender who initiated can press the buttons.

  /massgift <waifu_id> <quantity>
  /massgift <id1,id2,id3>
    - Reply to a user's message to gift multiple cards (either many of one card
      or a list of different card IDs).
    - Shows a text-only preview (names + ids) and Confirm / Decline inline buttons.
    - On Confirm: transfers the specified quantities / ids from sender -> recipient.

Notes:
 - Uses sqlite DB at Config.DB_PATH (fallback to "waifu_bot.db").
 - Requires tables:
     waifu_cards (id, name, anime, rarity, media_type, media_file)
     user_waifus (user_id, waifu_id, amount, last_collected)
 - All DB operations are transactional.
 - In-memory sessions kept in PENDING_GIFTS keyed by token (UUID hex).
"""

import sqlite3
import traceback
import uuid
from typing import List, Tuple, Dict, Any

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message, CallbackQuery
from config import app, Config

DB_PATH = getattr(Config, "DB_PATH", "waifu_bot.db")

# Map token -> gift session data
PENDING_GIFTS: Dict[str, Dict[str, Any]] = {}


# ---------------- DB helpers ----------------
def _get_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def get_card_by_id(waifu_id: int):
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, name, anime, rarity, media_type, media_file FROM waifu_cards WHERE id = ?",
            (waifu_id,)
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "anime": row[2],
            "rarity": row[3],
            "media_type": row[4],
            "media_file": row[5],
        }
    finally:
        conn.close()


def user_has_waifu_amount(user_id: int, waifu_id: int) -> int:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT SUM(amount) FROM user_waifus WHERE user_id = ? AND waifu_id = ?", (user_id, waifu_id))
        r = cur.fetchone()
        return int(r[0]) if r and r[0] is not None else 0
    finally:
        conn.close()


def remove_waifu_from_user(user_id: int, waifu_id: int, qty: int) -> bool:
    """
    Decrease user's waifu amount by qty; delete row if amount goes to 0.
    Returns True if sufficient and updated, False otherwise.
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT amount FROM user_waifus WHERE user_id = ? AND waifu_id = ?", (user_id, waifu_id))
        row = cur.fetchone()
        if not row or row[0] < qty:
            return False
        new_amt = row[0] - qty
        if new_amt > 0:
            cur.execute("UPDATE user_waifus SET amount = ? WHERE user_id = ? AND waifu_id = ?", (new_amt, user_id, waifu_id))
        else:
            cur.execute("DELETE FROM user_waifus WHERE user_id = ? AND waifu_id = ?", (user_id, waifu_id))
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        traceback.print_exc()
        return False
    finally:
        conn.close()


def add_waifu_to_user(user_id: int, waifu_id: int, qty: int) -> bool:
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute("SELECT amount FROM user_waifus WHERE user_id = ? AND waifu_id = ?", (user_id, waifu_id))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE user_waifus SET amount = amount + ? WHERE user_id = ? AND waifu_id = ?", (qty, user_id, waifu_id))
        else:
            cur.execute("INSERT INTO user_waifus (user_id, waifu_id, amount, last_collected) VALUES (?, ?, ?, strftime('%s','now'))", (user_id, waifu_id, qty))
        conn.commit()
        return True
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        traceback.print_exc()
        return False
    finally:
        conn.close()


# ---------------- util ----------------
def _gen_token() -> str:
    return uuid.uuid4().hex


def _build_confirm_kb(token: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm", callback_data=f"gift_confirm:{token}"),
         InlineKeyboardButton("❌ Decline", callback_data=f"gift_decline:{token}")]
    ])


# ---------------- /gift handler ----------------
@app.on_message(filters.command("gift"))
async def cmd_gift(client, message: Message):
    try:
        user = message.from_user
        if not user:
            return

        if not message.reply_to_message or not getattr(message.reply_to_message, "from_user", None):
            await message.reply_text("❌ Reply to the user's message you want to gift to.")
            return
        target_user = message.reply_to_message.from_user

        # parse waifu id
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.reply_text("❌ Usage: /gift <waifu_id> (reply to recipient's message)")
            return
        try:
            waifu_id = int(parts[1].strip())
        except Exception:
            await message.reply_text("❌ Invalid waifu id. Provide the numeric card id.")
            return

        # check ownership
        have = user_has_waifu_amount(user.id, waifu_id)
        if have < 1:
            await message.reply_text("❌ You don't have that card to gift.")
            return

        card = get_card_by_id(waifu_id)
        if not card:
            await message.reply_text("❌ Card not found.")
            return

        # build preview (with media if available)
        caption = (
            f"🎁 Gift Preview\n\n"
            f"From: {user.first_name} (id: {user.id})\n"
            f"To: {target_user.first_name} (id: {target_user.id})\n\n"
            f"🆔 ID: {card['id']}\n"
            f"📛 Name: {card['name']}\n"
            f"📺 Anime: {card['anime']}\n"
            f"✨ Rarity: {card['rarity']}\n\n"
            "Only the sender can Confirm / Decline."
        )

        token = _gen_token()
        PENDING_GIFTS[token] = {
            "type": "single",
            "from_user": user.id,
            "to_user": target_user.id,
            "items": [(waifu_id, 1)],
            "message_chat_id": message.chat.id,
            "message_id": getattr(message, "id", None) or getattr(message, "message_id", None),
        }

        kb = _build_confirm_kb(token)

        # send preview (media if available)
        try:
            if card.get("media_type") and card.get("media_file"):
                mtype = (card.get("media_type") or "").lower()
                if mtype == "video":
                    await message.reply_video(card["media_file"], caption=caption, reply_markup=kb)
                else:
                    await message.reply_photo(card["media_file"], caption=caption, reply_markup=kb)
            else:
                await message.reply_text(caption, reply_markup=kb)
        except Exception:
            # fallback to simple text
            await message.reply_text(caption, reply_markup=kb)

    except Exception:
        traceback.print_exc()
        try:
            await message.reply_text("❌ Failed to create gift preview.")
        except Exception:
            pass


# ---------------- /massgift handler ----------------
@app.on_message(filters.command("massgift"))
async def cmd_massgift(client, message: Message):
    """
    Usage variants:
      /massgift <waifu_id> <qty>      -> gift qty copies of waifu_id
      /massgift <id1,id2,id3>        -> gift one of each id in list
      /massgift <waifu_id>           -> gift one copy
    Reply to recipient's message.
    """
    try:
        user = message.from_user
        if not user:
            return
        if not message.reply_to_message or not getattr(message.reply_to_message, "from_user", None):
            await message.reply_text("❌ Reply to the recipient's message to use /massgift.")
            return
        target_user = message.reply_to_message.from_user

        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.reply_text("❌ Usage: /massgift <waifu_id> <qty>  OR  /massgift id1,id2,id3  (reply to recipient).")
            return
        body = parts[1].strip()

        items: List[Tuple[int, int]] = []  # list of (waifu_id, qty)

        # comma-separated list?
        if "," in body and not body.strip().isdigit():
            # treat as list of ids
            raw_ids = [p.strip() for p in body.split(",") if p.strip()]
            for rid in raw_ids:
                try:
                    iid = int(rid)
                except Exception:
                    await message.reply_text(f"❌ Invalid id in list: {rid}")
                    return
                items.append((iid, 1))
        else:
            # either "id qty" or just "id"
            tokens = body.split()
            try:
                waifu_id = int(tokens[0])
            except Exception:
                await message.reply_text("❌ Invalid waifu id.")
                return
            qty = 1
            if len(tokens) >= 2:
                try:
                    qty = int(tokens[1])
                    if qty < 1:
                        raise ValueError()
                except Exception:
                    await message.reply_text("❌ Invalid quantity. Provide a positive integer.")
                    return
            items.append((waifu_id, qty))

        # Validate items exist & sender has enough
        not_found = []
        insufficient = []
        names_preview = []
        for wid, q in items:
            card = get_card_by_id(wid)
            if not card:
                not_found.append(wid)
                continue
            have = user_has_waifu_amount(user.id, wid)
            if have < q:
                insufficient.append((wid, have, q))
            names_preview.append((wid, card["name"], q, card.get("media_type"), card.get("media_file")))

        if not_found:
            await message.reply_text(f"❌ These IDs were not found: {', '.join(str(x) for x in not_found)}")
            return
        if insufficient:
            msgs = [f"ID {wid}: have {have}, need {need}" for (wid, have, need) in insufficient]
            await message.reply_text("❌ Insufficient cards:\n" + "\n".join(msgs))
            return

        # Build preview text
        preview_lines = [
            f"🎁 Mass Gift Preview",
            f"From: {user.first_name} (id: {user.id})",
            f"To: {target_user.first_name} (id: {target_user.id})",
            "",
            "Items:"
        ]
        for wid, name, q, *_ in names_preview:
            preview_lines.append(f" - {name} (ID {wid}) x{q}")
        preview_lines.append("\nOnly the sender can Confirm / Decline.")

        caption = "\n".join(preview_lines)
        token = _gen_token()
        PENDING_GIFTS[token] = {
            "type": "mass",
            "from_user": user.id,
            "to_user": target_user.id,
            "items": items,
            "message_chat_id": message.chat.id,
            "message_id": getattr(message, "id", None) or getattr(message, "message_id", None),
        }
        kb = _build_confirm_kb(token)
        await message.reply_text(caption, reply_markup=kb)

    except Exception:
        traceback.print_exc()
        try:
            await message.reply_text("❌ Failed to create mass gift preview.")
        except Exception:
            pass


# ---------------- Decline callback ----------------
@app.on_callback_query(filters.regex(r"^gift_decline:([0-9a-fA-F]+)$"))
async def cb_gift_decline(client, callback: CallbackQuery):
    try:
        token = callback.matches[0].group(1)
        session = PENDING_GIFTS.get(token)
        if not session:
            await callback.answer("This gift session expired or is invalid.", show_alert=True)
            return
        caller = callback.from_user
        if caller.id != session["from_user"]:
            await callback.answer("Only the sender can decline this gift.", show_alert=True)
            return

        # cleanup
        PENDING_GIFTS.pop(token, None)
        try:
            await callback.message.edit_reply_markup(None)
        except Exception:
            pass
        await callback.answer("Gift cancelled.")
    except Exception:
        traceback.print_exc()
        try:
            await callback.answer("Failed to cancel gift.", show_alert=True)
        except Exception:
            pass


# ---------------- Confirm callback ----------------
@app.on_callback_query(filters.regex(r"^gift_confirm:([0-9a-fA-F]+)$"))
async def cb_gift_confirm(client, callback: CallbackQuery):
    try:
        token = callback.matches[0].group(1)
        session = PENDING_GIFTS.get(token)
        if not session:
            await callback.answer("Gift expired or invalid.", show_alert=True)
            return

        caller = callback.from_user
        if caller.id != session["from_user"]:
            await callback.answer("Only the sender can confirm this gift.", show_alert=True)
            return

        from_user = session["from_user"]
        to_user = session["to_user"]
        items = session["items"]  # list of (waifu_id, qty)

        # perform transfer atomically: ensure all removals possible then apply
        conn = _get_conn()
        try:
            cur = conn.cursor()
            # verify availability
            for wid, qty in items:
                cur.execute("SELECT SUM(amount) FROM user_waifus WHERE user_id = ? AND waifu_id = ?", (from_user, wid))
                r = cur.fetchone()
                have = int(r[0]) if r and r[0] is not None else 0
                if have < qty:
                    raise RuntimeError(f"Insufficient amount for ID {wid}: have {have}, need {qty}")

            # apply removals and additions
            for wid, qty in items:
                # decrement from sender
                cur.execute("SELECT amount FROM user_waifus WHERE user_id = ? AND waifu_id = ?", (from_user, wid))
                r = cur.fetchone()
                if r:
                    new_amt = r[0] - qty
                    if new_amt > 0:
                        cur.execute("UPDATE user_waifus SET amount = ? WHERE user_id = ? AND waifu_id = ?", (new_amt, from_user, wid))
                    else:
                        cur.execute("DELETE FROM user_waifus WHERE user_id = ? AND waifu_id = ?", (from_user, wid))
                # add to recipient
                cur.execute("SELECT amount FROM user_waifus WHERE user_id = ? AND waifu_id = ?", (to_user, wid))
                r2 = cur.fetchone()
                if r2:
                    cur.execute("UPDATE user_waifus SET amount = amount + ? WHERE user_id = ? AND waifu_id = ?", (qty, to_user, wid))
                else:
                    cur.execute("INSERT INTO user_waifus (user_id, waifu_id, amount, last_collected) VALUES (?, ?, ?, strftime('%s','now'))", (to_user, wid, qty))

            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            traceback.print_exc()
            await callback.answer(f"❌ Transfer failed: {e}", show_alert=True)
            return
        finally:
            conn.close()

        # Notify both parties
        try:
            # send DM to recipient with details (try to include media of first item if available)
            first_item = items[0] if items else None
            if first_item:
                wid, qty = first_item
                card = get_card_by_id(wid)
            else:
                card = None

            gift_text_lines = [
                f"🎁 You've received a gift!",
                f"From: {caller.first_name} (id: {caller.id})",
                f"Items:"
            ]
            for wid, qty in items:
                c = get_card_by_id(wid)
                if c:
                    gift_text_lines.append(f" - {c['name']} (ID {wid}) x{qty}")
                else:
                    gift_text_lines.append(f" - ID {wid} x{qty}")
            gift_text = "\n".join(gift_text_lines)

            # DM recipient
            try:
                if card and card.get("media_type") and card.get("media_file"):
                    mtype = (card.get("media_type") or "").lower()
                    if mtype == "video":
                        await client.send_video(to_user, card["media_file"], caption=gift_text)
                    else:
                        await client.send_photo(to_user, card["media_file"], caption=gift_text)
                else:
                    await client.send_message(to_user, gift_text)
            except Exception:
                # recipient may have privacy settings or blocked bot; still continue
                pass

            # Edit the preview message in original chat to show success
            try:
                await callback.message.edit_reply_markup(None)
                await callback.message.reply_text(f"✅ Gift sent to {to_user} by {caller.first_name}.")
            except Exception:
                pass

            # Notify support chat (best-effort)
            try:
                support_chat = getattr(Config, "SUPPORT_CHAT_ID", None)
                if support_chat:
                    support_msg_lines = [
                        f"🎁 Gift: {caller.first_name} (id:{caller.id}) -> {to_user}",
                        "Items:"
                    ]
                    for wid, qty in items:
                        c = get_card_by_id(wid)
                        if c:
                            support_msg_lines.append(f" - {c['name']} (ID {wid}) x{qty}")
                        else:
                            support_msg_lines.append(f" - ID {wid} x{qty}")
                    support_msg = "\n".join(support_msg_lines)
                    await client.send_message(support_chat, support_msg)
            except Exception:
                pass

        except Exception:
            traceback.print_exc()

        # cleanup
        PENDING_GIFTS.pop(token, None)
        await callback.answer("✅ Gift completed.")
    except Exception:
        traceback.print_exc()
        try:
            await callback.answer("Failed to complete gift.", show_alert=True)
        except Exception:
            pass