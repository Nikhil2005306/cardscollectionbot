# handlers/reward.py
from pyrogram import filters
from config import app, Config
import sqlite3
import time
import asyncio

DB_PATH = getattr(Config, "DB_PATH", "waifu_bot.db")

# In-memory processing guard to avoid re-entrancy for same user (per process)
PROCESSING = set()

# Ensure user_claims table exists and has waifu_id column
def ensure_user_claims_table():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_claims (
                user_id INTEGER PRIMARY KEY,
                last_claim INTEGER,
                waifu_id INTEGER
            )
        """)
        conn.commit()

        # Ensure waifu_id column exists (older DBs might not have it)
        cur.execute("PRAGMA table_info(user_claims)")
        cols = [r[1] for r in cur.fetchall()]
        if "waifu_id" not in cols:
            try:
                cur.execute("ALTER TABLE user_claims ADD COLUMN waifu_id INTEGER")
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
    except Exception:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass

ensure_user_claims_table()


def add_waifu_to_inventory(user_id: int, waifu_id: int):
    """Insert or update user_waifus with given waifu_id"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        cur.execute("""
            UPDATE user_waifus
               SET amount = amount + 1,
                   last_collected = strftime('%s','now')
             WHERE user_id = ? AND waifu_id = ?
        """, (user_id, waifu_id))

        if cur.rowcount == 0:
            cur.execute("""
                INSERT INTO user_waifus (user_id, waifu_id, amount, last_collected)
                VALUES (?, ?, 1, strftime('%s','now'))
            """, (user_id, waifu_id))

        conn.commit()
    except Exception:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


async def reserve_claim(user_id: int, retries: int = 5, backoff: float = 0.15) -> bool:
    """
    Atomically reserve the user's claim if not already claimed.
    Returns True if reservation succeeded (user did NOT have a claim before).
    Returns False if user already had a claim or reservation failed.
    Uses INSERT ... SELECT WHERE NOT EXISTS(...) to be atomic.
    """
    attempt = 0
    while attempt < retries:
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH, timeout=5)
            cur = conn.cursor()
            # Atomic insert-if-not-exists
            cur.execute("BEGIN IMMEDIATE")
            cur.execute("""
                INSERT INTO user_claims (user_id, last_claim)
                SELECT ?, ?
                WHERE NOT EXISTS (SELECT 1 FROM user_claims WHERE user_id = ?)
            """, (user_id, int(time.time()), user_id))
            inserted = cur.rowcount  # 1 if inserted, 0 if already existed
            conn.commit()
            return inserted == 1
        except sqlite3.OperationalError as e:
            # might be "database is locked" transiently — wait and retry
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass
            attempt += 1
            await asyncio.sleep(backoff)
            backoff *= 1.5
            continue
        except Exception:
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass
            return False
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
    return False


def attach_waifu_to_claim(user_id: int, waifu_id: int):
    """Store the assigned waifu_id into the user's claim row (best-effort)."""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("UPDATE user_claims SET waifu_id = ? WHERE user_id = ?", (waifu_id, user_id))
        conn.commit()
    except Exception:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def rollback_claim(user_id: int):
    """Delete claim row (used when we reserved but failed to give reward)."""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM user_claims WHERE user_id = ?", (user_id,))
        conn.commit()
    except Exception:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


async def pick_reward_video():
    """
    Pick a waifu video id from DB.
    Returns (waifu_id, name, anime, theme, media_file) or None.
    """
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        # Prefer Cinematic Legend video
        cur.execute("""
            SELECT id, name, anime, event, media_file
              FROM waifu_cards
             WHERE rarity = 'Cinematic Legend' AND LOWER(media_type) = 'video'
             ORDER BY RANDOM() LIMIT 1
        """)
        r = cur.fetchone()
        if r:
            return r

        cur.execute("""
            SELECT id, name, anime, event, media_file
              FROM waifu_cards
             WHERE LOWER(media_type) = 'video'
             ORDER BY RANDOM() LIMIT 1
        """)
        r = cur.fetchone()
        return r
    except Exception:
        return None
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


@app.on_message(filters.command("reward"))
async def reward_command(client, message):
    if not message.from_user:
        return
    user_id = message.from_user.id

    # quick in-process guard
    if user_id in PROCESSING:
        try:
            await message.reply_text("⌛ Your reward request is already being processed. Please wait...")
        except Exception:
            pass
        return

    PROCESSING.add(user_id)
    try:
        # Attempt atomic reservation
        reserved = await reserve_claim(user_id)
        if not reserved:
            await message.reply_text("❌ You have already claimed your special reward!")
            return

        # We have a reservation; pick a waifu video to give
        row = await pick_reward_video()
        if not row:
            # No video available: rollback claim so user can try later
            rollback_claim(user_id)
            await message.reply_text("❌ No video cards available in the database. Try again later.")
            return

        waifu_id, name, anime, theme, media_file = row

        # Attach waifu_id to claim record (so any concurrent attempt sees assigned id)
        attach_waifu_to_claim(user_id, waifu_id)

        # Add to inventory (idempotent enough)
        add_waifu_to_inventory(user_id, waifu_id)

        caption = (
            "🎉 You received a special reward!\n\n"
            f"🆔 ID: {waifu_id}\n"
            f"💖 Waifu: {name}\n"
            f"📺 Anime: {anime}\n"
            f"🎭 Theme: {theme}\n\n"
            "✨ Added to your inventory!"
        )

        # Send the video once. If sending fails, we keep the claim (user already got the item).
        try:
            await message.reply_video(media_file, caption=caption)
        except Exception:
            # fallback to text-only message
            try:
                await message.reply_text(caption)
            except Exception:
                pass

    finally:
        PROCESSING.discard(user_id)


# Owner-only reset: allow everyone to claim again
@app.on_message(filters.command(["allow", "Allow"]))
async def allow_again_command(client, message):
    if not message.from_user:
        return
    caller = message.from_user
    owner_id = getattr(Config, "OWNER_ID", None)
    try:
        is_owner = (int(owner_id) == int(caller.id))
    except Exception:
        is_owner = (str(owner_id) == str(caller.id))

    if not is_owner:
        await message.reply_text("❌ Only the bot owner can use this command.")
        return

    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("DELETE FROM user_claims")
        conn.commit()
        await message.reply_text("✅ All user reward claims have been reset. Everyone can claim /reward again.")
    except Exception:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        try:
            await message.reply_text("❌ Failed to reset claims. Check logs.")
        except Exception:
            pass
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass