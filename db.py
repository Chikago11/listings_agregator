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
"""


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(CREATE_SQL)
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
        await db.commit()


async def remove_subscriber(chat_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM subscribers WHERE chat_id=?", (chat_id,))
        await db.commit()


async def get_subscribers() -> list[int]:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT chat_id FROM subscribers")
        rows = await cur.fetchall()
        await cur.close()
        return [int(r[0]) for r in rows]
