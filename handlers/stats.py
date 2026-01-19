# handlers/stats.py
from pyrogram import filters
from config import app, Config
from database import Database
import os

db = Database()

# Rarities and matching emojis (ordering used to present counts nicely)
RARITIES = [
    ("🌸", "Common Blossom"),
    ("🌼", "Charming Glow"),
    ("🌹", "Elegant Rose"),
    ("💫", "Rare Sparkle"),
    ("🔥", "Enchanted Flame"),
    ("🎐", "Animated Spirit"),
    ("🌈", "Chroma Pulse"),
    ("🧚", "Mythical Grace"),
    ("🦋", "Ethereal Whisper"),
    ("🧊", "Frozen Aurora"),
    ("⚡️", "Volt Resonant"),
    ("🪞", "Holographic Mirage"),
    ("🌪", "Phantom Tempest"),
    ("🕊", "Celestia Bloom"),
    ("👑", "Divine Ascendant"),
    ("🔮", "Timewoven Relic"),
    ("💋", "Forbidden Desire"),
    ("📽", "Cinematic Legend"),
]

@ app.on_message(filters.command("stats"))
async def stats_cmd(client, message):
    user_id = message.from_user.id

    # Owner only
    if user_id != Config.OWNER_ID:
        await message.reply_text("❌ This command is **Owner only**.")
        return

    try:
        # Total users (best-effort, fallback to 0)
        try:
            db.cursor.execute("SELECT COUNT(*) FROM users")
            total_users = db.cursor.fetchone()[0] or 0
        except Exception:
            total_users = 0

        # Total groups (try Database helper, otherwise query groups table)
        try:
            total_groups = db.get_total_groups()
        except Exception:
            try:
                db.cursor.execute("SELECT COUNT(*) FROM groups")
                total_groups = db.cursor.fetchone()[0] or 0
            except Exception:
                total_groups = 0

        # Per-rarity counts: we will try to use the defined RARITIES ordering first.
        rarity_counts = {}
        try:
            # Query counts grouped by rarity (raw string matching)
            db.cursor.execute("SELECT rarity, COUNT(*) FROM waifu_cards GROUP BY rarity")
            rows = db.cursor.fetchall()
            # normalize mapping
            for r, c in rows:
                if r is None:
                    continue
                rarity_counts[str(r).strip()] = int(c)
        except Exception:
            rarity_counts = {}

        # Build rarities text in our preferred order (fall back to any remaining DB rarities)
        rarities_lines = []
        for emoji, human in RARITIES:
            cnt = rarity_counts.get(human, 0)
            rarities_lines.append(f"{emoji} {human} → {cnt}")

        # If there are rarities in DB not in our list, show them too
        extra = []
        for r, cnt in rarity_counts.items():
            if r not in [h for _, h in RARITIES]:
                extra.append(f"• {r} → {cnt}")
        if extra:
            rarities_lines.append("\n-- Other rarities in DB --")
            rarities_lines.extend(extra)

        # Recently added 3 waifus (by id DESC)
        recent_lines = []
        try:
            db.cursor.execute("SELECT id, name, anime, rarity, added_by FROM waifu_cards ORDER BY id DESC LIMIT 3")
            recent = db.cursor.fetchall()
            if recent:
                for row in recent:
                    wid = row[0]
                    name = row[1] or "Unknown"
                    anime = row[2] or "Unknown"
                    rarity = row[3] or "Unknown"
                    added_by = row[4] if len(row) > 4 else None
                    by_text = f" (added by {added_by})" if added_by else ""
                    recent_lines.append(f"#{wid} — {name} | {anime} | {rarity}{by_text}")
            else:
                recent_lines.append("No recently added waifus found.")
        except Exception:
            recent_lines.append("Failed to fetch recent waifus.")

        # Build final message
        stats_text = "📊 **Bot Stats**\n\n"
        stats_text += f"👥 Total Users: {total_users}\n"
        stats_text += f"👑 Total Groups Bot Added: {total_groups}\n\n"
        stats_text += "🌸 **Rarity Breakdown**\n"
        stats_text += "\n".join(rarities_lines) + "\n\n"
        stats_text += "🆕 **Recently added (latest 3)**\n"
        stats_text += "\n".join(recent_lines) + "\n"

        # Send with Stats.jpg if present in working directory (will be deleted locally if found)
        image_path = "Stats.jpg"
        if os.path.exists(image_path):
            try:
                await message.reply_photo(photo=image_path, caption=stats_text)
                # remove local copy to avoid filling storage
                try:
                    os.remove(image_path)
                except Exception:
                    pass
            except Exception:
                # fallback to text if photo send failed
                await message.reply_text(stats_text)
        else:
            await message.reply_text(stats_text)

    except Exception as e:
        # Generic fallback
        try:
            await message.reply_text("❌ Failed to fetch stats. Check logs.")
        except Exception:
            pass
        print(f"stats_cmd error: {e}")