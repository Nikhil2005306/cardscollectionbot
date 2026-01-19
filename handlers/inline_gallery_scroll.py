# handlers/inline_gallery_scroll.py
import sqlite3
import time
from pyrogram import filters
from pyrogram.types import (
    InlineQuery,
    InlineQueryResultCachedPhoto,
    InlineQueryResultCachedVideo,
    InputTextMessageContent
)
from config import app

DB_PATH = "waifu_bot.db"

# Short-lived guard for processed inline query ids.
# Maps inline_query_id -> timestamp (seconds)
PROCESSED_INLINE_IDS = {}
# How long to treat an inline query id as processed (seconds)
INLINE_GUARD_TTL = 5.0

def _conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def fetch_waifu_cards(search: str = "", limit: int = 50, offset: int = 0):
    conn = _conn()
    cur = conn.cursor()
    if search:
        q = f"%{search.lower()}%"
        cur.execute(
            "SELECT id, name, anime, rarity, event, media_type, media_file FROM waifu_cards "
            "WHERE LOWER(name) LIKE ? OR LOWER(anime) LIKE ? ORDER BY id ASC LIMIT ? OFFSET ?",
            (q, q, limit, offset)
        )
    else:
        cur.execute(
            "SELECT id, name, anime, rarity, event, media_type, media_file FROM waifu_cards ORDER BY id ASC LIMIT ? OFFSET ?",
            (limit, offset)
        )
    rows = cur.fetchall()
    conn.close()
    return rows

@app.on_inline_query()
async def inline_waifu_gallery(client, iq: InlineQuery):
    # Defensive dedupe: ignore duplicate inline queries with same id for a short window.
    iq_id = getattr(iq, "id", None)
    now = time.time()
    if iq_id:
        # cleanup old entries
        to_delete = [k for k, t in PROCESSED_INLINE_IDS.items() if now - t > INLINE_GUARD_TTL]
        for k in to_delete:
            PROCESSED_INLINE_IDS.pop(k, None)

        # if already processed recently, ignore this duplicate delivery
        last = PROCESSED_INLINE_IDS.get(iq_id)
        if last and (now - last) <= INLINE_GUARD_TTL:
            return

        # mark as processed now
        PROCESSED_INLINE_IDS[iq_id] = now

    query = (iq.query or "").strip()
    offset = int(iq.offset or 0)
    limit = 50
    cards = fetch_waifu_cards(query, limit=limit, offset=offset)

    if not cards:
        await iq.answer(
            [],
            switch_pm_text="No waifus found 😢",
            switch_pm_parameter="start",
            cache_time=30
        )
        return

    results = []
    for wid, name, anime, rarity, event, media_type, media_file in cards:
        caption = (
            f"🆔 ID: {wid}\n"
            f"👤 Name: {name}\n"
            f"🤝 Anime: {anime}\n"
            f"💎 Rarity: {rarity}\n"
            f"🎀 Event/Theme: {event}"
        )

        try:
            if media_type in ("photo", "image"):
                results.append(
                    InlineQueryResultCachedPhoto(
                        id=str(wid),
                        photo_file_id=media_file,
                        caption=caption
                    )
                )
            elif media_type in ("video", "animation"):
                results.append(
                    InlineQueryResultCachedVideo(
                        id=str(wid),
                        video_file_id=media_file,
                        title=f"{name} [{rarity}]",
                        caption=caption
                    )
                )
        except Exception as e:
            # Keep this print to help debugging without touching other parts of the bot.
            print(f"[inline_gallery_scroll] error creating result for {name}: {e}")

    next_offset = str(offset + limit) if len(cards) == limit else ""

    await iq.answer(
        results,
        cache_time=30,
        is_personal=True,
        next_offset=next_offset
    )