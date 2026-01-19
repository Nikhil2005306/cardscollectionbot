# handlers/game.py
"""
Simple mini-games with per-user per-command cooldowns, emoji "throw" animations, and crystal rewards.

Commands:
 - /toss <h|t>       : Guess coin flip (h=heads, t=tails). 50% win. Shows a small coin-flip emoji animation in chat.
 - /basket           : Throw a basket. If goal -> +500 crystals. Shows a short throw animation with emojis.
 - /dice <1-6>       : Guess dice roll (1-6). If correct -> +500 crystals. Shows rolling dice emoji animation.
 - /football         : Shoot a football. If goal -> +500 crystals. Emoji animation included.
 - /dart             : Throw a dart. If hits center -> +500 crystals. Emoji animation included.
 - /ping             : Owner-only. Check bot latency (quick ping).

Notes:
 - Each game has its own 60-second cooldown per user.
 - Winning reward: 500 crystals (added to user_balances table).
 - Uses the same SQLite DB file used elsewhere: "waifu_bot.db".
 - Owner id is read from Config.OWNER_ID.
 - This file creates user_balances table if it doesn't exist.
"""

import time
import random
import sqlite3
import asyncio

from datetime import datetime
from pyrogram import filters
from pyrogram.types import Message
from config import app, Config

DB_PATH = "waifu_bot.db"
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
cursor = conn.cursor()

# ensure user_balances table exists
cursor.execute("""
CREATE TABLE IF NOT EXISTS user_balances (
    user_id INTEGER PRIMARY KEY,
    balance INTEGER DEFAULT 0
)
""")
conn.commit()

OWNER_ID = getattr(Config, "OWNER_ID", None)

# cooldown storage: {(user_id, command_name): last_timestamp}
_cooldowns = {}

COOLDOWN_SECONDS = 60
WIN_REWARD = 500


# ----------------- Helpers -----------------
def _check_cooldown(user_id: int, cmd: str):
    key = (user_id, cmd)
    now_ts = time.time()
    last = _cooldowns.get(key, 0)
    elapsed = now_ts - last
    if elapsed < COOLDOWN_SECONDS:
        return False, int(COOLDOWN_SECONDS - elapsed)
    return True, 0


def _set_cooldown(user_id: int, cmd: str):
    _cooldowns[(user_id, cmd)] = time.time()


def _get_balance(user_id: int) -> int:
    cursor.execute("SELECT balance FROM user_balances WHERE user_id = ?", (user_id,))
    r = cursor.fetchone()
    return int(r[0]) if r and r[0] is not None else 0


def _set_balance(user_id: int, new_balance: int):
    cursor.execute("INSERT OR REPLACE INTO user_balances (user_id, balance) VALUES (?, ?)", (user_id, new_balance))
    conn.commit()


def _add_balance(user_id: int, amount: int):
    cur = _get_balance(user_id)
    nb = cur + amount
    _set_balance(user_id, nb)
    return nb


# ----------------- Emoji helpers for animations -----------------
DICE_FACE = {
    1: "⚀",
    2: "⚁",
    3: "⚂",
    4: "⚃",
    5: "⚄",
    6: "⚅"
}

async def _animate_message(msg, frames, delay=0.45):
    """
    Edit a message through a list of `frames` (strings) with `delay` seconds between them.
    Returns after final frame is shown.
    """
    try:
        for f in frames:
            await msg.edit_text(f)
            await asyncio.sleep(delay)
    except Exception:
        # If editing fails (e.g., message deleted), ignore and continue
        pass


# ----------------- Games -----------------
@app.on_message(filters.command("toss"))
async def toss_cmd(client, message: Message):
    user = message.from_user
    if not user:
        return

    ok, rem = _check_cooldown(user.id, "toss")
    if not ok:
        await message.reply_text(f"⏳ Please wait {rem}s before playing /toss again.")
        return

    parts = (message.text or "").strip().split()
    if len(parts) < 2 or parts[1].lower() not in ("h", "t", "head", "tails", "heads", "tail"):
        await message.reply_text("Usage: /toss <h|t>  (h = heads, t = tails)\nExample: /toss h")
        return

    guess_token = parts[1].lower()
    guess = "h" if guess_token.startswith("h") else "t"

    # create an animation message
    anim_msg = await message.reply_text("🪙 Flipping the coin...")
    # coin animation frames
    frames = [
        "🪙",
        "🪙🪙",
        "🪙🪙🪙",
        "🪙 🌀",
    ]
    await _animate_message(anim_msg, frames, delay=0.4)

    flip = random.choice(["h", "t"])
    _set_cooldown(user.id, "toss")

    flip_word = "Heads" if flip == "h" else "Tails"
    guess_word = "Heads" if guess == "h" else "Tails"

    if flip == guess:
        new_bal = _add_balance(user.id, WIN_REWARD)
        try:
            await anim_msg.edit_text(f"🪙 {flip_word} — You guessed {guess_word}! 🎉\nYou win {WIN_REWARD} crystals.\nBalance: {new_bal}")
        except Exception:
            await message.reply_text(f"🪙 {flip_word} — You win {WIN_REWARD} crystals! Balance: {new_bal}")
    else:
        try:
            await anim_msg.edit_text(f"🪙 {flip_word} — You guessed {guess_word}. Better luck next time!")
        except Exception:
            await message.reply_text(f"🪙 {flip_word} — You guessed {guess_word}. Better luck next time!")


@app.on_message(filters.command("basket"))
async def basket_cmd(client, message: Message):
    user = message.from_user
    if not user:
        return

    ok, rem = _check_cooldown(user.id, "basket")
    if not ok:
        await message.reply_text(f"⏳ Please wait {rem}s before playing /basket again.")
        return

    anim_msg = await message.reply_text("🏀 You throw the ball...")
    frames = [
        "🏀",
        "🏀 ➡️",
        "🏀 ➡️ 🥅",
    ]
    await _animate_message(anim_msg, frames, delay=0.5)

    _set_cooldown(user.id, "basket")

    # chance to score - 40%
    scored = random.random() < 0.40
    if scored:
        new_bal = _add_balance(user.id, WIN_REWARD)
        try:
            await anim_msg.edit_text(f"🏀 ⛹️‍♂️ — Net! You win {WIN_REWARD} crystals.\nBalance: {new_bal}")
        except Exception:
            await message.reply_text(f"🏀 Net! You win {WIN_REWARD} crystals. Balance: {new_bal}")
    else:
        try:
            await anim_msg.edit_text("🏀 Missed the hoop. No crystals this time.")
        except Exception:
            await message.reply_text("🏀 Missed the hoop. No crystals this time.")


@app.on_message(filters.command("dice"))
async def dice_cmd(client, message: Message):
    user = message.from_user
    if not user:
        return

    ok, rem = _check_cooldown(user.id, "dice")
    if not ok:
        await message.reply_text(f"⏳ Please wait {rem}s before playing /dice again.")
        return

    parts = (message.text or "").strip().split()
    if len(parts) < 2:
        await message.reply_text("Usage: /dice <1-6>\nExample: /dice 4")
        return
    try:
        guess = int(parts[1])
        if not 1 <= guess <= 6:
            raise ValueError()
    except Exception:
        await message.reply_text("Invalid guess. Provide an integer between 1 and 6.")
        return

    anim_msg = await message.reply_text("🎲 Rolling the dice...")
    frames = [
        "🎲",
        "🎲🎲",
        "🎲🎲🎲",
    ]
    await _animate_message(anim_msg, frames, delay=0.35)

    roll = random.randint(1, 6)
    _set_cooldown(user.id, "dice")

    die_emoji = DICE_FACE.get(roll, "🎲")
    if roll == guess:
        new_bal = _add_balance(user.id, WIN_REWARD)
        try:
            await anim_msg.edit_text(f"🎲 Rolled {die_emoji} ({roll}) — You guessed {guess}! 🎉\nYou win {WIN_REWARD} crystals.\nBalance: {new_bal}")
        except Exception:
            await message.reply_text(f"🎲 Rolled {die_emoji} ({roll}) — You guessed right! You win {WIN_REWARD} crystals. Balance: {new_bal}")
    else:
        try:
            await anim_msg.edit_text(f"🎲 Rolled {die_emoji} ({roll}). You guessed {guess}. No win this time.")
        except Exception:
            await message.reply_text(f"🎲 Rolled {die_emoji} ({roll}). You guessed {guess}. No win this time.")


@app.on_message(filters.command("football"))
async def football_cmd(client, message: Message):
    user = message.from_user
    if not user:
        return

    ok, rem = _check_cooldown(user.id, "football")
    if not ok:
        await message.reply_text(f"⏳ Please wait {rem}s before playing /football again.")
        return

    anim_msg = await message.reply_text("⚽ You take the shot...")
    frames = [
        "⚽",
        "⚽ ➡️",
        "⚽ ➡️ 🥅",
    ]
    await _animate_message(anim_msg, frames, delay=0.45)

    _set_cooldown(user.id, "football")

    # chance to score - 45%
    scored = random.random() < 0.45
    if scored:
        new_bal = _add_balance(user.id, WIN_REWARD)
        try:
            await anim_msg.edit_text(f"⚽ GOAL! You win {WIN_REWARD} crystals.\nBalance: {new_bal}")
        except Exception:
            await message.reply_text(f"⚽ GOAL! You win {WIN_REWARD} crystals. Balance: {new_bal}")
    else:
        try:
            await anim_msg.edit_text("⚽ Missed the goal. No crystals this time.")
        except Exception:
            await message.reply_text("⚽ Missed the goal. No crystals this time.")


@app.on_message(filters.command("dart"))
async def dart_cmd(client, message: Message):
    user = message.from_user
    if not user:
        return

    ok, rem = _check_cooldown(user.id, "dart")
    if not ok:
        await message.reply_text(f"⏳ Please wait {rem}s before playing /dart again.")
        return

    anim_msg = await message.reply_text("🎯 You throw the dart...")
    frames = [
        "🎯",
        "🎯 ➡️",
        "🎯 ➡️ 🎯",
    ]
    await _animate_message(anim_msg, frames, delay=0.45)

    _set_cooldown(user.id, "dart")

    # chance to hit center - 30%
    hit_center = random.random() < 0.30
    if hit_center:
        new_bal = _add_balance(user.id, WIN_REWARD)
        try:
            await anim_msg.edit_text(f"🎯 Bullseye! You win {WIN_REWARD} crystals.\nBalance: {new_bal}")
        except Exception:
            await message.reply_text(f"🎯 Bullseye! You win {WIN_REWARD} crystals. Balance: {new_bal}")
    else:
        try:
            await anim_msg.edit_text("🎯 Missed the bullseye. Try again later.")
        except Exception:
            await message.reply_text("🎯 Missed the bullseye. Try again later.")


# ----------------- Owner-only ping -----------------
@app.on_message(filters.command("ping"))
async def ping_cmd(client, message: Message):
    if not message.from_user or message.from_user.id != OWNER_ID:
        await message.reply_text("❌ This command is owner-only.")
        return

    t0 = time.time()
    msg = await message.reply_text("🏓 Pinging...")
    t1 = time.time()
    latency = int((t1 - t0) * 1000)
    try:
        # also try a quick minimal API call to gauge responsiveness (get_me)
        await client.get_me()
        t2 = time.time()
        api_latency = int((t2 - t1) * 1000)
    except Exception:
        api_latency = None

    txt = f"🏓 Pong!\nReply latency: {latency} ms"
    if api_latency is not None:
        txt += f"\nAPI call: {api_latency} ms"
    await msg.edit_text(txt)
