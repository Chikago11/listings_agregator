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

-- Holds the exchanges a subscriber switched OFF, not the ones they kept.
-- Every exchange is on by default, so a new venue starts out visible to
-- everyone instead of silently missing for existing subscribers.
CREATE TABLE IF NOT EXISTS subscriber_exchange_filters (
  chat_id INTEGER NOT NULL,
  alert_type TEXT NOT NULL,
  exchange TEXT NOT NULL,
  PRIMARY KEY (chat_id, alert_type, exchange)
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
        await db.execute(
            "DELETE FROM subscriber_exchange_filters WHERE chat_id=?", (chat_id,)
        )
        await db.commit()


async def get_subscribers(
    alert_type: str | None = None,
    exchange: str | None = None,
) -> list[int]:
    """Subscribers to notify.

    alert_type=None returns everyone. With an alert_type, the subscriber must
    have that alert enabled; with an exchange on top of that, they must not
    have switched this particular exchange off.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        if alert_type is None:
            cur = await db.execute("SELECT chat_id FROM subscribers")
        else:
            col = _alert_column(alert_type)
            sql = f"""
                SELECT s.chat_id
                FROM subscribers s
                LEFT JOIN subscriber_alerts a ON a.chat_id = s.chat_id
                WHERE COALESCE(a.{col}, 0) = 1
            """
            params: tuple = ()
            if exchange:
                sql += """
                  AND s.chat_id NOT IN (
                    SELECT chat_id
                    FROM subscriber_exchange_filters
                    WHERE alert_type = ? AND exchange = ?
                  )
                """
                params = (alert_type, exchange)
            cur = await db.execute(sql, params)
        rows = await cur.fetchall()
        await cur.close()
        return [int(r[0]) for r in rows]


# ---------- per-exchange filters ----------
async def get_disabled_exchanges(chat_id: int, alert_type: str) -> set[str]:
    _alert_column(alert_type)  # validates alert_type
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            SELECT exchange
            FROM subscriber_exchange_filters
            WHERE chat_id=? AND alert_type=?
            """,
            (chat_id, alert_type),
        )
        rows = await cur.fetchall()
        await cur.close()
        return {str(r[0]) for r in rows}


async def set_exchange_selection(
    chat_id: int,
    alert_type: str,
    disabled: set[str],
) -> None:
    """Replace the stored filter and keep the alert-type switch in sync.

    An alert type with no exchanges left selected is the same thing as the
    alert type being off, so callers pair this with set_alert_enabled().
    """
    _alert_column(alert_type)  # validates alert_type
    await _ensure_alert_settings_row(chat_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM subscriber_exchange_filters WHERE chat_id=? AND alert_type=?",
            (chat_id, alert_type),
        )
        await db.executemany(
            """
            INSERT OR IGNORE INTO subscriber_exchange_filters(
                chat_id, alert_type, exchange
            ) VALUES(?, ?, ?)
            """,
            [(chat_id, alert_type, ex) for ex in sorted(disabled)],
        )
        await db.commit()


async def set_alert_enabled(chat_id: int, alert_type: str, enabled: bool) -> None:
    col = _alert_column(alert_type)
    now = int(time.time())
    await _ensure_alert_settings_row(chat_id)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE subscriber_alerts SET {col}=?, updated_at=? WHERE chat_id=?",
            (1 if enabled else 0, now, chat_id),
        )
        await db.commit()


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


