# handlers/inventory.py
from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from config import app
from database import Database
import urllib.parse

db = Database()

ITEMS_PER_PAGE = 5

# Rarity names -> emoji mapping
RARITY_EMOJIS = {
    "Common Blossom": "🌸",
    "Charming Glow": "🌼",
    "Elegant Rose": "🌹",
    "Rare Sparkle": "💫",
    "Enchanted Flame": "🔥",
    "Animated Spirit": "🎐",
    "Chroma Pulse": "🌈",
    "Mythical Grace": "🧚",
    "Ethereal Whisper": "🦋",
    "Frozen Aurora": "🧊",
    "Volt Resonant": "⚡️",
    "Holographic Mirage": "🪞",
    "Phantom Tempest": "🌪",
    "Celestia Bloom": "🕊",
    "Divine Ascendant": "👑",
    "Timewoven Relic": "🔮",
    "Forbidden Desire": "💋",
    "Cinematic Legend": "📽",
}

RARITY_ORDER = [
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
    "Holographic Mirage",
    "Phantom Tempest",
    "Celestia Bloom",
    "Divine Ascendant",
    "Timewoven Relic",
    "Forbidden Desire",
    "Cinematic Legend",
]

# ensure user_settings exists (tolerant)
try:
    db.cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id INTEGER PRIMARY KEY,
            rarity_filter TEXT DEFAULT NULL,
            anime_filter TEXT DEFAULT NULL
        )
        """
    )
    db.conn.commit()
except Exception:
    try:
        conn = getattr(db, "conn", None)
        cur = getattr(db, "cursor", None) or (conn.cursor() if conn else None)
        if cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    rarity_filter TEXT DEFAULT NULL,
                    anime_filter TEXT DEFAULT NULL
                )
                """
            )
            conn.commit()
    except Exception:
        pass


# ---------------- Helpers ----------------
def encode_cb(s: str) -> str:
    return urllib.parse.quote_plus(s)


def decode_cb(s: str) -> str:
    return urllib.parse.unquote_plus(s)


def get_user_settings(user_id: int):
    cur = db.cursor
    cur.execute("SELECT rarity_filter, anime_filter FROM user_settings WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row:
        return {"rarity": row[0], "anime": row[1]}
    return {"rarity": None, "anime": None}


def set_user_settings(user_id: int, rarity=None, anime=None):
    cur = db.cursor
    cur.execute(
        "INSERT OR REPLACE INTO user_settings (user_id, rarity_filter, anime_filter) VALUES (?, ?, ?)",
        (user_id, rarity, anime),
    )
    db.conn.commit()


# ---------------- Inventory view builder ----------------
def build_inventory_view(user_id: int, page: int):
    """
    Returns tuple: (text, markup, fav_card, rows_count, total_cards)
    fav_card is None or dict with keys: id, name, anime, rarity, event, media_type, media_file
    """
    offset = page * ITEMS_PER_PAGE

    settings = get_user_settings(user_id)
    rarity_filter = settings.get("rarity")
    anime_filter = settings.get("anime")

    # favorite
    fav_card = None
    fav_owned_count = 0
    try:
        db.cursor.execute("SELECT waifu_id FROM user_fav WHERE user_id = ?", (user_id,))
        fav_row = db.cursor.fetchone()
        if fav_row:
            fav_id = fav_row[0]
            db.cursor.execute(
                "SELECT id, name, anime, rarity, event, media_type, media_file FROM waifu_cards WHERE id = ?",
                (fav_id,),
            )
            fav_card_row = db.cursor.fetchone()
            if fav_card_row:
                f_id, f_name, f_anime, f_rarity, f_event, f_media_type, f_media_file = fav_card_row
                fav_card = {
                    "id": f_id,
                    "name": f_name,
                    "anime": f_anime,
                    "rarity": f_rarity,
                    "event": f_event,
                    "media_type": f_media_type,
                    "media_file": f_media_file,
                }
                db.cursor.execute(
                    "SELECT amount FROM user_waifus WHERE user_id = ? AND waifu_id = ?", (user_id, fav_id)
                )
                r = db.cursor.fetchone()
                fav_owned_count = r[0] if r else 0
    except Exception:
        fav_card = None
        fav_owned_count = 0

    # build where clause
    where_clauses = ["uw.user_id = ?"]
    params = [user_id]
    if rarity_filter:
        where_clauses.append("wc.rarity = ?")
        params.append(rarity_filter)
    if anime_filter:
        where_clauses.append("wc.anime = ?")
        params.append(anime_filter)
    where_sql = " AND ".join(where_clauses)

    # rows
    query = f"""
        SELECT uw.waifu_id, wc.name, wc.rarity, uw.amount
        FROM user_waifus uw
        JOIN waifu_cards wc ON uw.waifu_id = wc.id
        WHERE {where_sql}
        ORDER BY uw.amount DESC, wc.name ASC
        LIMIT ? OFFSET ?
    """
    params_page = params + [ITEMS_PER_PAGE, offset]
    db.cursor.execute(query, tuple(params_page))
    rows = db.cursor.fetchall()

    # total count
    sum_query = f"SELECT SUM(amount) FROM user_waifus uw JOIN waifu_cards wc ON uw.waifu_id = wc.id WHERE {where_sql}"
    db.cursor.execute(sum_query, tuple(params))
    total_cards = db.cursor.fetchone()[0] or 0

    # empty case
    if not rows and not fav_card:
        text = "❌ You have no waifus yet!"
        return text, None, None, 0, total_cards

    # build text
    lines = []
    lines.append("🌌 ✦ Waifu Collection Gallery ✦ 🌌")
    lines.append("╭───────────────────╮")
    lines.append(f"📜 Showing {ITEMS_PER_PAGE} Waifus every page ")
    lines.append("╰───────────────────╯")
    lines.append("")

    if fav_card:
        rarity_emoji = RARITY_EMOJIS.get(fav_card["rarity"], "")
        lines.append("💖 Favorite Waifu 💖")
        lines.append("╭━━━♡━━━╮")
        lines.append(f"✨ Name: {fav_card['name']}")
        lines.append(f"🆔 ID: {fav_card['id']}")
        lines.append(f"🌸 Rarity: {rarity_emoji} {fav_card['rarity']}")
        lines.append(f"📦 Owned: x{fav_owned_count}")
        lines.append("╰━━━♡━━━╯")
        lines.append("")

    lines.append("🌺 Your Collection 🌺")
    lines.append("───────────────────────")
    if rows:
        for idx, row in enumerate(rows, start=1 + page * ITEMS_PER_PAGE):
            waifu_id, name, rarity, amount = row
            rarity_emoji = RARITY_EMOJIS.get(rarity, "")
            emoji_digits = {
                1: "1️⃣",
                2: "2️⃣",
                3: "3️⃣",
                4: "4️⃣",
                5: "5️⃣",
                6: "6️⃣",
                7: "7️⃣",
                8: "8️⃣",
                9: "9️⃣",
                10: "🔟",
            }
            list_index = ((idx - 1) % ITEMS_PER_PAGE) + 1
            num_display = emoji_digits.get(list_index, f"{list_index}.")
            lines.append(f"{num_display} {rarity_emoji} {name}")
            lines.append(f"　🆔 {waifu_id} | {rarity_emoji} {rarity} | 📦 x{amount}")
            lines.append("")
    else:
        lines.append("You have no waifus matching the current filters.")

    lines.append("───────────────────────")
    lines.append(f"📊 Total Collected: 💕 {total_cards}")

    active_filters = []
    if rarity_filter:
        rf_emoji = RARITY_EMOJIS.get(rarity_filter, "")
        active_filters.append(f"Rarity: {rf_emoji} {rarity_filter}")
    if anime_filter:
        active_filters.append(f"Anime: {anime_filter}")
    if active_filters:
        lines.append("")
        lines.append("Active Filters: " + " | ".join(active_filters))

    text = "\n".join(lines)

    # pagination buttons (wmode removed)
    buttons = []
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Back", callback_data=f"inventory_page:{page-1}"))
    if len(rows) == ITEMS_PER_PAGE:
        nav_row.append(InlineKeyboardButton("➡️ Next", callback_data=f"inventory_page:{page+1}"))
    if nav_row:
        buttons.append(nav_row)
    markup = InlineKeyboardMarkup(buttons) if buttons else None

    return text, markup, fav_card, len(rows), total_cards


# ---------------- /inventory Command ----------------
@app.on_message(filters.command("inventory"))
async def inventory(client, message):
    user_id = message.from_user.id
    # send first page as a fresh message
    await send_inventory_page(client, message.chat.id, user_id, 0)


async def send_inventory_page(client, chat_id, user_id, page):
    """
    Send a new inventory message (used by command or when replacement is required).
    """
    text, markup, fav_card, rows_count, total = build_inventory_view(user_id, page)

    if fav_card:
        f_media_type = fav_card.get("media_type")
        f_media_file = fav_card.get("media_file")
        try:
            if f_media_type == "photo":
                await client.send_photo(chat_id, f_media_file, caption=text, reply_markup=markup)
                return
            elif f_media_type in ("video", "animation"):
                await client.send_video(chat_id, f_media_file, caption=text, reply_markup=markup)
                return
        except Exception:
            # fallback to text below
            pass

    await client.send_message(chat_id, text, reply_markup=markup)


# ---------------- Callback for pagination ----------------
@app.on_callback_query(filters.regex(r"^inventory_page:"))
async def inventory_page_callback(client, callback: CallbackQuery):
    """
    Try to EDIT the existing message in-place. If that fails, send a replacement and delete the old one.
    This avoids duplicate inventory messages.
    """
    try:
        page = int(callback.data.split(":")[1])
    except Exception:
        try:
            await callback.answer()
        except Exception:
            pass
        return

    user_id = callback.from_user.id
    chat_id = callback.message.chat.id
    orig_msg = callback.message

    text, markup, fav_card, rows_count, total = build_inventory_view(user_id, page)

    try:
        # If original message had media and new view still has media -> edit_caption
        orig_has_media = bool(getattr(orig_msg, "photo", None) or getattr(orig_msg, "video", None) or getattr(orig_msg, "animation", None))
        new_has_media = bool(fav_card and fav_card.get("media_type") in ("photo", "video", "animation"))

        if orig_has_media and new_has_media:
            # both media: try edit_caption
            try:
                await orig_msg.edit_caption(text, reply_markup=markup)
                await callback.answer()
                return
            except Exception:
                # fall through to replacement
                pass

        if not orig_has_media and not new_has_media:
            # both text-only: edit_text
            try:
                await orig_msg.edit_text(text, reply_markup=markup)
                await callback.answer()
                return
            except Exception:
                # fall through to replacement
                pass

        # incompatible types (media <-> text): send replacement and delete original
        if fav_card:
            f_media_type = fav_card.get("media_type")
            f_media_file = fav_card.get("media_file")
            if f_media_type == "photo":
                new_msg = await client.send_photo(chat_id, f_media_file, caption=text, reply_markup=markup)
            elif f_media_type in ("video", "animation"):
                new_msg = await client.send_video(chat_id, f_media_file, caption=text, reply_markup=markup)
            else:
                new_msg = await client.send_message(chat_id, text, reply_markup=markup)
        else:
            new_msg = await client.send_message(chat_id, text, reply_markup=markup)

        # delete old message (best-effort)
        try:
            await orig_msg.delete()
        except Exception:
            pass

        try:
            await callback.answer()
        except Exception:
            pass
        return

    except Exception:
        # fallback: try sending a new message if all editing failed
        try:
            if fav_card:
                f_media_type = fav_card.get("media_type")
                f_media_file = fav_card.get("media_file")
                if f_media_type == "photo":
                    await client.send_photo(chat_id, f_media_file, caption=text, reply_markup=markup)
                elif f_media_type in ("video", "animation"):
                    await client.send_video(chat_id, f_media_file, caption=text, reply_markup=markup)
                else:
                    await client.send_message(chat_id, text, reply_markup=markup)
            else:
                await client.send_message(chat_id, text, reply_markup=markup)
        except Exception:
            pass
        finally:
            try:
                await callback.answer()
            except Exception:
                pass


# ---------------- /wmode command and callbacks ----------------
@app.on_message(filters.command("wmode"))
async def wmode_cmd(client, message):
    user_id = message.from_user.id
    await send_wmode_menu(client, message.chat.id, user_id)


async def send_wmode_menu(client, chat_id, user_id):
    settings = get_user_settings(user_id)
    rarity = settings.get("rarity")
    anime = settings.get("anime")
    lines = ["⚙️ Inventory Mode /wmode", "", "Choose filters for your inventory display:", ""]
    if rarity:
        lines.append(f"Current rarity filter: {RARITY_EMOJIS.get(rarity,'')} {rarity}")
    else:
        lines.append("Current rarity filter: All")
    if anime:
        lines.append(f"Current anime filter: {anime}")
    else:
        lines.append("Current anime filter: All")

    kb = [
        [InlineKeyboardButton("🎚 Select Rarity", callback_data="wmode_select_rarity")],
        [InlineKeyboardButton("🎬 Select Anime", callback_data="wmode_select_anime")],
        [
            InlineKeyboardButton("❌ Clear Rarity", callback_data="wmode_clear_rarity"),
            InlineKeyboardButton("❌ Clear Anime", callback_data="wmode_clear_anime"),
        ],
        [InlineKeyboardButton("✅ Done", callback_data="wmode_done")],
    ]
    await client.send_message(chat_id, "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))


@app.on_callback_query(filters.regex(r"^wmode_select_rarity$"))
async def wmode_select_rarity_cb(client, callback: CallbackQuery):
    kb = []
    row = []
    for r in RARITY_ORDER:
        display = f"{RARITY_EMOJIS.get(r,'')} {r}"
        encoded = encode_cb(r)
        row.append(InlineKeyboardButton(display, callback_data=f"wmode_set_rarity:{encoded}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="wmode_menu")])
    try:
        await callback.message.edit_text("Select a rarity to filter by:", reply_markup=InlineKeyboardMarkup(kb))
    except Exception:
        await callback.message.reply_text("Select a rarity to filter by:", reply_markup=InlineKeyboardMarkup(kb))
    await callback.answer()


@app.on_callback_query(filters.regex(r"^wmode_set_rarity:(.+)$"))
async def wmode_set_rarity_cb(client, callback: CallbackQuery):
    try:
        enc = callback.data.split(":", 1)[1]
        rarity = decode_cb(enc)
        user_id = callback.from_user.id
        cur = db.cursor
        cur.execute("SELECT anime_filter FROM user_settings WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        anime_val = row[0] if row else None
        set_user_settings(user_id, rarity=rarity, anime=anime_val)
        try:
            await callback.message.edit_text(f"✅ Rarity filter set to: {RARITY_EMOJIS.get(rarity,'')} {rarity}")
        except Exception:
            await callback.message.reply_text(f"✅ Rarity filter set to: {RARITY_EMOJIS.get(rarity,'')} {rarity}")
        await callback.answer("Rarity set.")
    except Exception:
        await callback.answer("Failed to set rarity.", show_alert=True)


@app.on_callback_query(filters.regex(r"^wmode_select_anime$"))
async def wmode_select_anime_cb(client, callback: CallbackQuery):
    try:
        db.cursor.execute(
            "SELECT DISTINCT anime FROM waifu_cards WHERE anime IS NOT NULL AND TRIM(anime) != '' ORDER BY anime COLLATE NOCASE"
        )
        animes = [r[0] for r in db.cursor.fetchall()]
    except Exception:
        animes = []

    if not animes:
        await callback.answer("No anime entries found in database.", show_alert=True)
        return

    kb = []
    row = []
    for a in animes:
        enc = encode_cb(a)
        label = a if len(a) <= 20 else a[:17] + "..."
        row.append(InlineKeyboardButton(label, callback_data=f"wmode_set_anime:{enc}"))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="wmode_menu")])

    try:
        await callback.message.edit_text("Select anime to filter by:", reply_markup=InlineKeyboardMarkup(kb))
    except Exception:
        await callback.message.reply_text("Select anime to filter by:", reply_markup=InlineKeyboardMarkup(kb))
    await callback.answer()


@app.on_callback_query(filters.regex(r"^wmode_set_anime:(.+)$"))
async def wmode_set_anime_cb(client, callback: CallbackQuery):
    try:
        enc = callback.data.split(":", 1)[1]
        anime = decode_cb(enc)
        user_id = callback.from_user.id
        cur = db.cursor
        cur.execute("SELECT rarity_filter FROM user_settings WHERE user_id = ?", (user_id,))
        row = cur.fetchone()
        rarity_val = row[0] if row else None
        set_user_settings(user_id, rarity=rarity_val, anime=anime)
        try:
            await callback.message.edit_text(f"✅ Anime filter set to: {anime}")
        except Exception:
            await callback.message.reply_text(f"✅ Anime filter set to: {anime}")
        await callback.answer("Anime set.")
    except Exception:
        await callback.answer("Failed to set anime.", show_alert=True)


@app.on_callback_query(filters.regex(r"^wmode_clear_rarity$"))
async def wmode_clear_rarity_cb(client, callback: CallbackQuery):
    user_id = callback.from_user.id
    cur = db.cursor
    cur.execute("SELECT anime_filter FROM user_settings WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    anime_val = row[0] if row else None
    set_user_settings(user_id, rarity=None, anime=anime_val)
    try:
        await callback.message.edit_text("✅ Rarity filter cleared.")
    except Exception:
        await callback.message.reply_text("✅ Rarity filter cleared.")
    await callback.answer()


@app.on_callback_query(filters.regex(r"^wmode_clear_anime$"))
async def wmode_clear_anime_cb(client, callback: CallbackQuery):
    user_id = callback.from_user.id
    cur = db.cursor
    cur.execute("SELECT rarity_filter FROM user_settings WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    rarity_val = row[0] if row else None
    set_user_settings(user_id, rarity=rarity_val, anime=None)
    try:
        await callback.message.edit_text("✅ Anime filter cleared.")
    except Exception:
        await callback.message.reply_text("✅ Anime filter cleared.")
    await callback.answer()


@app.on_callback_query(filters.regex(r"^wmode_done$|^wmode_menu$"))
async def wmode_done_cb(client, callback: CallbackQuery):
    user_id = callback.from_user.id
    # send inventory first page (new message)
    await send_inventory_page(client, callback.message.chat.id, user_id, 0)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.answer()