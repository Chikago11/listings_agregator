import asyncio
import hashlib
from telethon import TelegramClient, events
from telethon.extensions import html
from telethon.tl.functions.channels import JoinChannelRequest

from storage_csv import upsert_listing, purge_old_tokens
from bot_service import run_bot_polling_blocking

from config import (
    API_ID,
    API_HASH,
    SESSION_NAME,
    DEDUP_TEXT_TTL_SEC,
    DEDUP_STRUCT_TTL_SEC,
)
from channels import CHANNELS
from db import init_db, is_seen, mark_seen, gc
from parser import normalize_text, extract  # extract_many если подключишь позже
from sender import send_alert


def msg_url(channel_username: str, message_id: int) -> str:
    return f"https://t.me/{channel_username}/{message_id}"


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


async def join_channels(client: TelegramClient):
    for ch in CHANNELS:
        try:
            await client(JoinChannelRequest(ch))
            print("Joined:", ch)
        except Exception as e:
            print("Join skipped:", ch, "->", str(e))


async def run():
    await init_db()

    async with TelegramClient(SESSION_NAME, API_ID, API_HASH) as client:
        await join_channels(client)

        @client.on(events.NewMessage(chats=CHANNELS))
        async def handler(event):
            try:
                ch = getattr(event.chat, "username", None)
                if not ch:
                    return

                msg_id = event.message.id

                raw_text = event.message.message or event.message.raw_text or ""
                if not raw_text.strip():
                    return

                # body_html сохраняет "вшитые" ссылки
                entities = event.message.entities or []
                body_html = html.unparse(raw_text, entities).strip()
                if not body_html:
                    return

                # --- text dedup ---
                norm = normalize_text(raw_text).lower()
                if not norm:
                    return
                text_key = "t:" + sha256(norm)
                if await is_seen(text_key):
                    return

                meta = extract(raw_text)

                # --- CSV store ---
                token = meta.get("base")
                exchange = meta.get("exchange")
                mtype = meta.get("market_type")
                if token and exchange and mtype:
                    try:
                        upsert_listing(
                            token=token,
                            market_type=mtype,
                            exchange=exchange.strip(),
                        )
                    except Exception as e:
                        print("CSV store error:", repr(e))

                # --- structured dedup ---
                if meta.get("base") and meta.get("exchange") and meta.get("market_type"):
                    sym_key = meta.get("display") or meta.get("base") or ""
                    k = f"s:{meta.get('exchange')}:{meta.get('market_type')}:{sym_key}"
                    if await is_seen(k):
                        await mark_seen(text_key, DEDUP_TEXT_TTL_SEC)
                        return
                    await mark_seen(k, DEDUP_STRUCT_TTL_SEC)

                ex = (meta.get("exchange") or "unknown").upper()
                mt = (meta.get("market_type") or "unknown").upper()
                sym = meta.get("display") or (meta.get("base") or "?")

                head = f"{ex} | {mt} | {sym}"

                source_url = msg_url(ch, msg_id)
                # ✅ ссылка зашита в название канала
                src_line = f'🔗 <b>Источник:</b> <a href="{source_url}">@{ch}</a>'

                if len(body_html) > 3000:
                    body_html = body_html[:3000] + "…"

                alert_html = f"{head}\n{src_line}\n\n{body_html}"

                # шлём без кнопок
                await send_alert(alert_html, parse_mode="HTML")

                # помечаем текст как увиденный
                await mark_seen(text_key, DEDUP_TEXT_TTL_SEC)

            except Exception as e:
                print("Handler error:", repr(e))

        async def gc_loop():
            while True:
                try:
                    removed = purge_old_tokens(days=7)
                    if removed:
                        print(f"purge_old_tokens: removed={removed}")
                except Exception as e:
                    print("purge_old_tokens error:", repr(e))

                try:
                    await gc()
                except Exception:
                    pass

                await asyncio.sleep(600)

        print("Listening...")

        await asyncio.gather(
            client.run_until_disconnected(),
            gc_loop(),
            asyncio.to_thread(run_bot_polling_blocking),  # PTB-бот отдельным потоком
        )


if __name__ == "__main__":
    asyncio.run(run())
