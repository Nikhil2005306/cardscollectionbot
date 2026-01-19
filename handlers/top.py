# handlers/top.py
try:
    from main import app
except Exception:
    from config import app

from pyrogram import filters
from database import Database
from datetime import datetime, timedelta

db = Database()


def _display_name(cur, user_id: int) -> str:
    """Get a safe plain-text display name: @username or first_name or user_id."""
    cur.execute("SELECT username, first_name FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row:
        username, first_name = row[0], row[1]
        if username:
            return f"@{username}"
        if first_name:
            # sanitize basic newlines/brackets to keep one-line output clean
            name = str(first_name).replace("\n", " ").replace("\r", " ")
            name = name.replace("[", "").replace("]", "").strip()
            return name
    return f"User {user_id}"


# ---------------- /top — Global Top Collectors ----------------
@app.on_message(filters.command("top"))
async def top_collectors_handler(client, message):
    """
    Show top 10 users by total owned waifus (summing user_waifus.amount).
    """
    cur = db.cursor
    cur.execute(
        """
        SELECT uw.user_id, COALESCE(SUM(CAST(uw.amount AS INTEGER)), 0) AS total
        FROM user_waifus uw
        GROUP BY uw.user_id
        ORDER BY total DESC
        LIMIT 10
        """
    )
    rows = cur.fetchall()

    if not rows:
        await message.reply_text("👑 No waifu collections found yet.")
        return

    lines = ["👑 Global Top Collectors", ""]
    for idx, (user_id, total) in enumerate(rows, start=1):
        name = _display_name(cur, user_id)
        lines.append(f"{idx}. {name} — {int(total):,} waifus")

    await message.reply_text("\n".join(lines))


# ---------------- /tdtop — Today’s Top Collectors (IST) ----------------
@app.on_message(filters.command("tdtop"))
async def todays_top_collectors_handler(client, message):
    """
    Show top 10 users who collected since 00:00 Asia/Kolkata today.
    Assumes user_waifus.last_collected stores epoch seconds (TEXT/INTEGER).
    """
    # Compute today's midnight in Asia/Kolkata (UTC+5:30) and convert to UTC epoch
    utc_now = datetime.utcnow()
    ist_now = utc_now + timedelta(hours=5, minutes=30)
    ist_midnight = datetime(ist_now.year, ist_now.month, ist_now.day, 0, 0, 0)
    utc_equiv = ist_midnight - timedelta(hours=5, minutes=30)
    start_ts = int(utc_equiv.timestamp())

    cur = db.cursor
    # If your table lacks last_collected, this query will fail; add that column or tell me to change logic.
    cur.execute(
        """
        SELECT uw.user_id, COALESCE(SUM(CAST(uw.amount AS INTEGER)),0) AS today_total
        FROM user_waifus uw
        WHERE uw.last_collected IS NOT NULL
          AND CAST(uw.last_collected AS INTEGER) >= ?
        GROUP BY uw.user_id
        ORDER BY today_total DESC
        LIMIT 10
        """,
        (start_ts,),
    )
    rows = cur.fetchall()

    date_label = ist_midnight.strftime("%Y-%m-%d")
    if not rows:
        await message.reply_text(f"🌙 No collections recorded today (IST 00:00) yet. [{date_label}]")
        return

    lines = [f"🌙 Today's Top Collectors — {date_label} (Asia/Kolkata)", ""]
    for idx, (user_id, total) in enumerate(rows, start=1):
        name = _display_name(cur, user_id)
        lines.append(f"{idx}. {name} — {int(total):,} waifus today")

    await message.reply_text("\n".join(lines))


# ---------------- /ctop — Top Crystal Holders ----------------
@app.on_message(filters.command("ctop"))
async def crystal_top_handler(client, message):
    """
    Show top 10 users by crystals balance.
    Sum: daily_crystals + weekly_crystals + monthly_crystals + given_crystals + user_profiles.balance
    """
    cur = db.cursor
    cur.execute(
        """
        SELECT u.user_id,
               COALESCE(CAST(u.daily_crystals   AS INTEGER),0)
             + COALESCE(CAST(u.weekly_crystals  AS INTEGER),0)
             + COALESCE(CAST(u.monthly_crystals AS INTEGER),0)
             + COALESCE(CAST(u.given_crystals   AS INTEGER),0)
             + COALESCE(CAST(up.balance         AS INTEGER),0) AS total_balance
        FROM users u
        LEFT JOIN user_profiles up ON up.user_id = u.user_id
        ORDER BY total_balance DESC
        LIMIT 10
        """
    )
    rows = cur.fetchall()

    if not rows:
        await message.reply_text("🏮 No crystal data found.")
        return

    lines = ["🏮 Top Crystal Holders", ""]
    for idx, (user_id, total) in enumerate(rows, start=1):
        name = _display_name(cur, user_id)
        lines.append(f"{idx}. {name} — {int(total):,} 💎")

    await message.reply_text("\n".join(lines))
