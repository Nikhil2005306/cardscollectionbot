# handlers/reset.py
import sqlite3
import time
import random
import traceback
from typing import Dict, Any, List
import json

from pyrogram import filters
from pyrogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from config import app, Config

DB_PATH = "waifu_bot.db"
pending_resets: Dict[str, Dict[str, Any]] = {}  # nonce -> info


def _conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.cursor()
    try:
        cur.execute(f"PRAGMA table_info({table})")
        cols = [r[1] for r in cur.fetchall()]
        return column in cols
    except Exception:
        return False


def get_user_collection_count(conn: sqlite3.Connection, user_id: int) -> int:
    """
    Return total number of card units the user has (using 'amount' column if present),
    otherwise return row-count.
    """
    cur = conn.cursor()
    if table_exists(conn, "user_waifus") and column_exists(conn, "user_waifus", "amount"):
        cur.execute("SELECT COALESCE(SUM(amount),0) FROM user_waifus WHERE user_id=?", (user_id,))
        r = cur.fetchone()
        return int(r[0]) if r and r[0] is not None else 0
    elif table_exists(conn, "user_waifus"):
        cur.execute("SELECT COUNT(*) FROM user_waifus WHERE user_id=?", (user_id,))
        r = cur.fetchone()
        return int(r[0]) if r else 0
    else:
        # try some common alternative tables
        alt_tables = ["collections", "user_cards", "user_collection", "inventory", "user_inventory"]
        total = 0
        for t in alt_tables:
            if table_exists(conn, t):
                if column_exists(conn, t, "amount"):
                    cur.execute(f"SELECT COALESCE(SUM(amount),0) FROM {t} WHERE user_id=?", (user_id,))
                    r = cur.fetchone()
                    total += int(r[0]) if r and r[0] is not None else 0
                else:
                    cur.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id=?", (user_id,))
                    r = cur.fetchone()
                    total += int(r[0]) if r else 0
        return total


def _ensure_backup_and_marker_tables(conn: sqlite3.Connection):
    """
    Create both backup and marker tables.
    backup: deleted_collections_backup (stores rows contents so we can restore)
    marker: collection_deletion_marker (flags user as deleted + metadata)
    """
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS deleted_collections_backup (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            table_name TEXT,
            columns_json TEXT,
            values_json TEXT,
            deleted_at INTEGER,
            meta TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS collection_deletion_marker (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            deleted_at INTEGER,
            nonce TEXT,
            removed_units INTEGER,
            meta TEXT
        )
        """
    )
    conn.commit()


def _fetch_table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return [r[1] for r in cur.fetchall()]


def delete_user_collections(conn: sqlite3.Connection, user_id: int, *, make_backup: bool = False, backup_meta: Dict[str, Any] = None, nonce: str = None) -> int:
    """
    Soft-delete user's collection:
      - If make_backup: backup rows into deleted_collections_backup
      - Remove rows from live tables so the user no longer sees them
      - Insert a row into collection_deletion_marker so the deletion is visible to admins and can be restored

    Returns total units removed (sum(amount) if present, else row counts).
    """
    cur = conn.cursor()
    total_removed_units = 0
    if make_backup:
        _ensure_backup_and_marker_tables(conn)
    deleted_at = int(time.time())
    meta_json = json.dumps(backup_meta or {})

    # Primary known table
    if table_exists(conn, "user_waifus"):
        # backup rows if requested
        if make_backup:
            cols = _fetch_table_columns(conn, "user_waifus")
            sel = "SELECT " + ", ".join([f'"{c}"' for c in cols]) + " FROM user_waifus WHERE user_id=?"
            cur.execute(sel, (user_id,))
            rows = cur.fetchall()
            for row in rows:
                cur.execute(
                    "INSERT INTO deleted_collections_backup (user_id, table_name, columns_json, values_json, deleted_at, meta) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, "user_waifus", json.dumps(cols), json.dumps([None if v is None else v for v in row], default=str), deleted_at, meta_json),
                )

        if column_exists(conn, "user_waifus", "amount"):
            cur.execute("SELECT COALESCE(SUM(amount),0) FROM user_waifus WHERE user_id=?", (user_id,))
            r = cur.fetchone()
            removed_units = int(r[0]) if r and r[0] is not None else 0
            total_removed_units += removed_units
        else:
            cur.execute("SELECT COUNT(*) FROM user_waifus WHERE user_id=?", (user_id,))
            r = cur.fetchone()
            removed_units = int(r[0]) if r else 0
            total_removed_units += removed_units

        cur.execute("DELETE FROM user_waifus WHERE user_id=?", (user_id,))

    # Try alternate tables
    alt_tables = ["collections", "user_cards", "user_collection", "inventory", "user_inventory"]
    for t in alt_tables:
        if table_exists(conn, t) and column_exists(conn, t, "user_id"):
            if make_backup:
                cols = _fetch_table_columns(conn, t)
                sel = "SELECT " + ", ".join([f'"{c}"' for c in cols]) + f" FROM {t} WHERE user_id=?"
                cur.execute(sel, (user_id,))
                rows = cur.fetchall()
                for row in rows:
                    cur.execute(
                        "INSERT INTO deleted_collections_backup (user_id, table_name, columns_json, values_json, deleted_at, meta) VALUES (?, ?, ?, ?, ?, ?)",
                        (user_id, t, json.dumps(cols), json.dumps([None if v is None else v for v in row], default=str), deleted_at, meta_json),
                    )

            if column_exists(conn, t, "amount"):
                cur.execute(f"SELECT COALESCE(SUM(amount),0) FROM {t} WHERE user_id=?", (user_id,))
                r = cur.fetchone()
                removed = int(r[0]) if r and r[0] is not None else 0
                total_removed_units += removed
            else:
                cur.execute(f"SELECT COUNT(*) FROM {t} WHERE user_id=?", (user_id,))
                r = cur.fetchone()
                removed = int(r[0]) if r else 0
                total_removed_units += removed

            cur.execute(f"DELETE FROM {t} WHERE user_id=?", (user_id,))

    # insert or update the deletion marker so the user is effectively 'soft-deleted'
    if make_backup:
        try:
            cur.execute(
                "INSERT OR REPLACE INTO collection_deletion_marker (user_id, deleted_at, nonce, removed_units, meta) VALUES (?, ?, ?, ?, ?)",
                (user_id, deleted_at, nonce or "", total_removed_units, meta_json),
            )
        except Exception:
            # ignore marker failures but keep operation successful
            pass

    conn.commit()
    return total_removed_units


# ----------------- /reset command -----------------
@app.on_message(filters.command("reset"))
async def cmd_reset(client, message: Message):
    """
    Usage: Reply to a user's message with /reset
    Only owner or admins allowed to run. Shows Confirm / Cancel inline buttons.
    """
    try:
        issuer = message.from_user
        issuer_id = issuer.id if issuer else None

        # permission check
        allowed = False
        if issuer_id == Config.OWNER_ID:
            allowed = True
        else:
            if hasattr(Config, "ADMINS") and Config.ADMINS:
                try:
                    if issuer_id in Config.ADMINS:
                        allowed = True
                except Exception:
                    allowed = False

        if not allowed:
            await message.reply_text("❌ Only the Owner or Admins can use /reset.")
            return

        # must be a reply
        if not message.reply_to_message or not message.reply_to_message.from_user:
            await message.reply_text("❌ Usage: Reply to the target user's message with `/reset` to wipe their collection.")
            return

        target = message.reply_to_message.from_user
        target_id = target.id

        # protective checks
        if target_id == Config.OWNER_ID:
            await message.reply_text("⛔ You cannot reset the Owner's collection.")
            return

        if hasattr(Config, "ADMINS") and Config.ADMINS and target_id in Config.ADMINS and issuer_id != Config.OWNER_ID:
            await message.reply_text("⛔ Only the Owner can reset an Admin's collection.")
            return

        if getattr(target, "is_bot", False):
            await message.reply_text("❌ You cannot reset a bot account.")
            return

        first = getattr(target, "first_name", "") or "Unknown"
        uname = ("@" + target.username) if getattr(target, "username", None) else ""
        prompt_lines = [
            "⚠️ Confirm collection reset ⚠️",
            "",
            f"Target: {first} {uname}".strip(),
            f"User ID: {target_id}",
            "",
            "This will temporarily REMOVE the user's collection from normal view (admin can restore with /restore).",
            "Only confirm if you are sure.",
            "",
            "Press ✅ Confirm to proceed or ❌ Cancel to abort."
        ]
        prompt = "\n".join(prompt_lines)

        # nonce for this operation
        nonce = str(int(time.time() * 1000)) + str(random.randint(1000, 9999))
        pending_resets[nonce] = {
            "issuer": issuer_id,
            "target": target_id,
            "chat_id": message.chat.id,
            "created": time.time(),
            "nonce": nonce,
        }

        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Confirm", callback_data=f"reset_confirm:{nonce}"),
                    InlineKeyboardButton("❌ Cancel", callback_data=f"reset_cancel:{nonce}"),
                ]
            ]
        )

        try:
            await message.reply_text(prompt, reply_markup=kb)
        except Exception:
            await client.send_message(message.chat.id, prompt, reply_markup=kb)

    except Exception:
        traceback.print_exc()
        try:
            await message.reply_text("❌ Internal error while preparing reset. Check logs.")
        except:
            pass


# ----------------- Callback handler -----------------
@app.on_callback_query(filters.regex(r"^reset_(confirm|cancel):"))
async def cb_reset(client, callback: CallbackQuery):
    try:
        data = callback.data  # e.g. "reset_confirm:12345"
        action, nonce = data.split(":", 1)
        info = pending_resets.get(nonce)
        if not info:
            await callback.answer("⚠️ This reset request has expired or is invalid.", show_alert=True)
            return

        issuer_id = info["issuer"]
        target_id = info["target"]
        created = info.get("created", 0)

        # only the issuer or owner can confirm/cancel
        user_id = callback.from_user.id
        if user_id != issuer_id and user_id != Config.OWNER_ID:
            await callback.answer("⛔ Only the admin who initiated this reset (or the Owner) may confirm/cancel.", show_alert=True)
            return

        # expiry (e.g., 5 minutes)
        if time.time() - created > 300:
            pending_resets.pop(nonce, None)
            try:
                await callback.message.edit_text("⛔ Reset request expired.")
            except:
                pass
            await callback.answer("Reset request expired.", show_alert=True)
            return

        if action == "reset_cancel":
            pending_resets.pop(nonce, None)
            try:
                await callback.message.edit_text("❌ Reset cancelled by admin.")
            except:
                pass
            await callback.answer("Reset cancelled.", show_alert=False)
            return

        # action == confirm -> perform deletion with backup + marker
        conn = _conn()
        try:
            before_count = get_user_collection_count(conn, target_id)
            backup_meta = {"requested_by": issuer_id, "confirmed_by": callback.from_user.id, "nonce": nonce}
            removed_units = delete_user_collections(conn, target_id, make_backup=True, backup_meta=backup_meta, nonce=nonce)
            if removed_units == 0 and before_count:
                removed_units = before_count

            pending_resets.pop(nonce, None)

            # edit callback message to show result (single notification)
            try:
                await callback.message.edit_text(
                    f"✅ Reset completed!\n\nTarget ID: {target_id}\nRemoved units: {removed_units}\n\nYou can restore this deletion with /restore (reply to a user or /restore <user_id>) — it will restore the most recent deletion for that user."
                )
            except:
                pass

            # attempt to DM the target (best-effort)
            try:
                await client.send_message(target_id, f"⚠️ Your collection has been temporarily removed by an admin. If you think this is a mistake contact support.")
            except:
                pass

            await callback.answer("Reset completed.", show_alert=False)
        finally:
            try:
                conn.close()
            except:
                pass

    except Exception:
        traceback.print_exc()
        try:
            await callback.answer("❌ Internal error while processing reset.", show_alert=True)
        except:
            pass


# ----------------- /restore command -----------------
@app.on_message(filters.command("restore"))
async def cmd_restore(client, message: Message):
    """
    Usage:
      - Reply to a user's message with /restore  -> restores the most recent deletion for that user
      - Or: /restore <user_id>                 -> restores the most recent deletion for that user

    Only Owner or Admins can restore.
    Restores the most recent deletion batch for that user (all rows with the same deleted_at timestamp).
    """
    try:
        issuer = message.from_user
        issuer_id = issuer.id if issuer else None

        # permission check
        allowed = False
        if issuer_id == Config.OWNER_ID:
            allowed = True
        else:
            if hasattr(Config, "ADMINS") and Config.ADMINS:
                try:
                    if issuer_id in Config.ADMINS:
                        allowed = True
                except Exception:
                    allowed = False

        if not allowed:
            await message.reply_text("❌ Only the Owner or Admins can use /restore.")
            return

        # determine target: reply or argument
        target_id = None
        if message.reply_to_message and message.reply_to_message.from_user:
            target_id = message.reply_to_message.from_user.id
        else:
            parts = (message.text or "").split()
            if len(parts) >= 2 and parts[1].isdigit():
                target_id = int(parts[1])

        if not target_id:
            await message.reply_text("❌ Usage: Reply to a user's message with /restore or use `/restore <user_id>`.")
            return

        if target_id == Config.OWNER_ID:
            await message.reply_text("⛔ You cannot restore the Owner's collection (not needed).")
            return

        conn = _conn()
        cur = conn.cursor()
        _ensure_backup_and_marker_tables(conn)

        # find the most recent deletion time for this user (from marker table)
        cur.execute("SELECT deleted_at FROM collection_deletion_marker WHERE user_id=?", (target_id,))
        row = cur.fetchone()
        if not row or row[0] is None:
            await message.reply_text("❌ No deletion marker/backups found for this user.")
            conn.close()
            return

        deleted_at = int(row[0])

        # fetch all backup rows for this user with that deleted_at
        cur.execute(
            "SELECT id, table_name, columns_json, values_json, meta FROM deleted_collections_backup WHERE user_id=? AND deleted_at=?",
            (target_id, deleted_at),
        )
        rows = cur.fetchall()
        if not rows:
            await message.reply_text("❌ No backups found for this user.")
            conn.close()
            return

        restored_count = 0
        failed = 0
        for backup_id, table_name, cols_json, vals_json, meta in rows:
            try:
                cols = json.loads(cols_json)
                vals = json.loads(vals_json)
                if not table_exists(conn, table_name):
                    failed += 1
                    continue
                existing_cols = _fetch_table_columns(conn, table_name)
                insert_cols = []
                insert_vals = []
                for c_name, c_val in zip(cols, vals):
                    if c_name in existing_cols:
                        insert_cols.append(c_name)
                        insert_vals.append(c_val)
                if not insert_cols:
                    failed += 1
                    continue
                placeholders = ",".join(["?"] * len(insert_vals))
                col_list_sql = ",".join([f'"{c}"' for c in insert_cols])
                sql = f"INSERT OR REPLACE INTO {table_name} ({col_list_sql}) VALUES ({placeholders})"
                cur.execute(sql, tuple(insert_vals))
                restored_count += 1
            except Exception:
                failed += 1

        conn.commit()

        # remove restored backup rows and marker
        try:
            cur.execute("DELETE FROM deleted_collections_backup WHERE user_id=? AND deleted_at=?", (target_id, deleted_at))
            cur.execute("DELETE FROM collection_deletion_marker WHERE user_id=?", (target_id,))
            conn.commit()
        except Exception:
            pass

        conn.close()

        msg_lines = [f"✅ Restore completed for user ID {target_id}."]
        msg_lines.append(f"Rows restored: {restored_count}")
        if failed:
            msg_lines.append(f"Rows skipped/failed: {failed} (schema changed or table missing).")
        await message.reply_text("\n".join(msg_lines))
    except Exception:
        traceback.print_exc()
        try:
            await message.reply_text("❌ Internal error while attempting restore. Check logs.")
        except:
            pass