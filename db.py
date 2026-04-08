import time
import aiosqlite

DB_PATH = "agg.sqlite"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS seen (
  k TEXT PRIMARY KEY,
  expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS subscribers (
  chat_id INTEGER PRIMARY KEY,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriber_alerts (
  chat_id INTEGER PRIMARY KEY,
  listings_enabled INTEGER NOT NULL DEFAULT 1,
  delistings_enabled INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);
"""

ALERT_LISTING = "listing"
ALERT_DELISTING = "delisting"


def _alert_column(alert_type: str) -> str:
    if alert_type == ALERT_LISTING:
        return "listings_enabled"
    if alert_type == ALERT_DELISTING:
        return "delistings_enabled"
    raise ValueError(f"Unknown alert_type: {alert_type}")


async def _ensure_alert_settings_row(chat_id: int):
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO subscriber_alerts(
                chat_id, listings_enabled, delistings_enabled, updated_at
            ) VALUES(?, 1, 0, ?)
            """,
            (chat_id, now),
        )
        await db.commit()


async def init_db():
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SQL)
        # Backfill settings for subscribers from old schema.
        await db.execute(
            """
            INSERT OR IGNORE INTO subscriber_alerts(
                chat_id, listings_enabled, delistings_enabled, updated_at
            )
            SELECT chat_id, 1, 0, ?
            FROM subscribers
            """,
            (now,),
        )
        await db.commit()


# ---------- dedup ----------
async def is_seen(key: str) -> bool:
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT expires_at FROM seen WHERE k=?", (key,))
        row = await cur.fetchone()
        await cur.close()

        if not row:
            return False

        expires_at = int(row[0])
        if expires_at <= now:
            await db.execute("DELETE FROM seen WHERE k=?", (key,))
            await db.commit()
            return False

        return True


async def mark_seen(key: str, ttl_sec: int):
    now = int(time.time())
    expires_at = now + int(ttl_sec)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO seen(k, expires_at) VALUES(?, ?)",
            (key, expires_at),
        )
        await db.commit()


async def gc():
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM seen WHERE expires_at <= ?", (now,))
        await db.commit()


# ---------- subscribers ----------
async def add_subscriber(chat_id: int):
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO subscribers(chat_id, created_at) VALUES(?, ?)",
            (chat_id, now),
        )
        await db.execute(
            """
            INSERT OR IGNORE INTO subscriber_alerts(
                chat_id, listings_enabled, delistings_enabled, updated_at
            ) VALUES(?, 1, 0, ?)
            """,
            (chat_id, now),
        )
        await db.commit()


async def remove_subscriber(chat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM subscribers WHERE chat_id=?", (chat_id,))
        await db.execute("DELETE FROM subscriber_alerts WHERE chat_id=?", (chat_id,))
        await db.commit()


async def get_subscribers(alert_type: str | None = None) -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        if alert_type is None:
            cur = await db.execute("SELECT chat_id FROM subscribers")
        else:
            col = _alert_column(alert_type)
            cur = await db.execute(
                f"""
                SELECT s.chat_id
                FROM subscribers s
                LEFT JOIN subscriber_alerts a ON a.chat_id = s.chat_id
                WHERE COALESCE(a.{col}, 0) = 1
                """
            )
        rows = await cur.fetchall()
        await cur.close()
        return [int(r[0]) for r in rows]


async def get_subscriber_alert_settings(chat_id: int) -> dict[str, bool]:
    await _ensure_alert_settings_row(chat_id)
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT listings_enabled, delistings_enabled
            FROM subscriber_alerts
            WHERE chat_id=?
            """,
            (chat_id,),
        )
        row = await cur.fetchone()
        await cur.close()

    if not row:
        return {"listing": True, "delisting": False}

    return {
        ALERT_LISTING: bool(int(row[0])),
        ALERT_DELISTING: bool(int(row[1])),
    }


async def toggle_subscriber_alert(chat_id: int, alert_type: str) -> dict[str, bool]:
    col = _alert_column(alert_type)
    await _ensure_alert_settings_row(chat_id)
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"""
            UPDATE subscriber_alerts
            SET {col} = CASE WHEN {col}=1 THEN 0 ELSE 1 END,
                updated_at=?
            WHERE chat_id=?
            """,
            (now, chat_id),
        )
        await db.commit()
    return await get_subscriber_alert_settings(chat_id)
