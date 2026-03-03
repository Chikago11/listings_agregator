import asyncio
import hashlib
import re
import traceback
from datetime import datetime, timezone
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
    MAX_EDIT_MESSAGE_AGE_SEC,
    OLD_EDIT_BYPASS_CHANNELS,
    POSTS_LOG_PATH,
)
from channels import CHANNELS
from db import init_db, is_seen, mark_seen, gc
from parser import normalize_text, extract  # extract_many если подключишь позже
from sender import send_alert
from ex_links import build_exchange_link
from post_log import append_post_log


def msg_url(channel_username: str, message_id: int) -> str:
    return f"https://t.me/{channel_username}/{message_id}"

def msg_url_fallback(chat_id: int | None, message_id: int) -> str | None:
    # For channels without username, Telegram web uses /c/<internal_id>/<msg_id>.
    if not chat_id:
        return None
    cid = str(chat_id)
    if cid.startswith("-100"):
        cid = cid[4:]
    elif cid.startswith("-"):
        cid = cid[1:]
    if not cid:
        return None
    return f"https://t.me/c/{cid}/{message_id}"


def sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def build_preview_text(message) -> str:
    media = getattr(message, "media", None)
    webpage = getattr(media, "webpage", None) if media else None
    if not webpage:
        return ""

    parts = []
    for field in ("title", "description", "site_name"):
        value = getattr(webpage, field, None)
        if isinstance(value, str):
            value = value.strip()
            if value:
                parts.append(value)

    if not parts:
        return ""
    return " | ".join(dict.fromkeys(parts))


def extract_hidden_urls(body_html: str) -> list[str]:
    if not body_html:
        return []
    urls = re.findall(r'href="(https?://[^"]+)"', body_html, flags=re.IGNORECASE)
    seen = set()
    out = []
    for u in urls:
        url = u.replace("&amp;", "&").strip()
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def shorten_for_html(body_html: str, parse_text: str, limit: int = 3000) -> str:
    if len(body_html) <= limit:
        return body_html
    # Avoid broken HTML by truncating escaped plain text fallback.
    flat = " ".join((parse_text or "").split())
    if not flat:
        return body_html[: max(0, limit - 1)] + "…"
    clipped = flat[: max(0, limit - 1)].rstrip()
    return html.escape(clipped) + "…"


def log_post_event(
    *,
    source: str,
    original_post: str,
    status: str,
    post_for_user: str = "",
) -> None:
    try:
        append_post_log(
            log_path=POSTS_LOG_PATH,
            source=source,
            original_post=original_post,
            status=status,
            post_for_user=post_for_user,
        )
    except Exception as e:
        print("Post log error:", repr(e))


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
        @client.on(events.MessageEdited(chats=CHANNELS))
        async def handler(event):
            try:
                ch = getattr(event.chat, "username", None)
                source_tag = f"@{ch}" if ch else str(getattr(event, "chat_id", "unknown"))

                msg_id = event.message.id

                # Old posts in listing channels are sometimes edited days later.
                # We keep fresh edits (useful for placeholder/reserved posts) but
                # ignore edits of old messages to prevent duplicate alerts.
                edit_date = getattr(event.message, "edit_date", None)
                msg_date = getattr(event.message, "date", None)
                ch_norm = (ch or "").lstrip("@").lower()
                bypass_old_edit_age = ch_norm in OLD_EDIT_BYPASS_CHANNELS
                if edit_date and msg_date and MAX_EDIT_MESSAGE_AGE_SEC > 0 and not bypass_old_edit_age:
                    try:
                        if msg_date.tzinfo is None:
                            msg_date = msg_date.replace(tzinfo=timezone.utc)
                        age_sec = (datetime.now(timezone.utc) - msg_date.astimezone(timezone.utc)).total_seconds()
                        if age_sec > MAX_EDIT_MESSAGE_AGE_SEC:
                            print(
                                f"Skip old edit: source={source_tag} msg_id={msg_id} age_sec={int(age_sec)}"
                            )
                            log_post_event(
                                source=str(source_tag),
                                original_post=(event.message.message or event.message.raw_text or "").strip(),
                                status="пропуск: старый edit",
                            )
                            return
                    except Exception as e:
                        print("Edit age check error:", repr(e))

                raw_text = event.message.message or event.message.raw_text or ""
                preview_text = build_preview_text(event.message)
                parse_text = raw_text if not preview_text else f"{raw_text}\n{preview_text}"

                # body_html сохраняет "вшитые" ссылки
                entities = event.message.entities or []
                try:
                    body_html = html.unparse(raw_text, entities).strip()
                except Exception:
                    # Keep processing even if entity offsets are malformed.
                    body_html = html.escape(raw_text).replace("\n", "<br>")

                hidden_urls = extract_hidden_urls(body_html)
                if hidden_urls:
                    parse_text = f"{parse_text}\n" + "\n".join(hidden_urls)

                original_post = (raw_text or "").strip() or (parse_text or "").strip()

                if not parse_text.strip():
                    print(f"Skip empty message: source={source_tag} msg_id={msg_id}")
                    return

                if not body_html:
                    body_html = html.escape(parse_text).replace("\n", "<br>")

                # --- text dedup ---
                norm = normalize_text(raw_text).lower()
                if not norm:
                    norm = normalize_text(parse_text).lower()
                if not norm:
                    # URL-only posts are common in listings feeds.
                    norm = " ".join(parse_text.split()).lower()
                if not norm:
                    return
                # Keep text dedup per source channel so mirrored posts from
                # another feed do not fully suppress this source.
                text_key = f"t:{source_tag}:" + sha256(norm)
                if await is_seen(text_key):
                    print(f"Skip text dedup: source={source_tag} msg_id={msg_id}")
                    log_post_event(
                        source=str(source_tag),
                        original_post=original_post,
                        status="дубликат",
                    )
                    return

                meta = extract(parse_text)

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
                        print(f"Skip struct dedup: source={source_tag} msg_id={msg_id} key={k}")
                        await mark_seen(text_key, DEDUP_TEXT_TTL_SEC)
                        log_post_event(
                            source=str(source_tag),
                            original_post=original_post,
                            status="дубликат",
                        )
                        return
                    await mark_seen(k, DEDUP_STRUCT_TTL_SEC)

                mt_raw = (meta.get("market_type") or "").strip().lower()
                sym = meta.get("display") or (meta.get("base") or "?")


                source_url = msg_url(ch, msg_id) if ch else msg_url_fallback(getattr(event, "chat_id", None), msg_id)
                # ✅ ссылка зашита в название канала

                token_emoji = "\U0001FA99"  # 🪙
                source_emoji = "\U0001F517"  # 🔗

                tag = "?"
                if mt_raw == "futures":
                    tag = "F"
                elif mt_raw == "spot":
                    tag = "S"

                ex_name = (meta.get("exchange") or "unknown").strip()
                base = (meta.get("base") or "").strip()
                quote = (meta.get("quote") or "USDT").strip().upper()

                ex_url = None
                if ex_name and base and mt_raw in ("spot", "futures"):
                    ex_url = build_exchange_link(ex_name, mt_raw, base=base, quote=quote)

                if ex_url:
                    ex_part = f'<a href="{html.escape(ex_url, quote=True)}">{html.escape(ex_name)}</a>({tag})'
                else:
                    ex_part = f"{html.escape(ex_name)}({tag})"

                line1 = f'{token_emoji}<b>{html.escape(str(sym))}</b>: {ex_part}'

                src_title = getattr(event.chat, "title", None) or (f"@{ch}" if ch else "channel")
                if source_url:
                    src_line = f'{source_emoji} <b>Источник:</b> <a href="{html.escape(source_url, quote=True)}">{html.escape(str(src_title))}</a>'
                else:
                    src_line = f"{source_emoji} <b>Источник:</b> {html.escape(str(src_title))}"

                body_html = shorten_for_html(body_html, parse_text, limit=3000)

                alert_html = f"{line1}\n{src_line}\n\n{body_html}"

                # шлём без кнопок
                await send_alert(alert_html, parse_mode="HTML")
                log_post_event(
                    source=str(src_title),
                    original_post=original_post,
                    status="отправлен пользователю",
                    post_for_user=alert_html,
                )

                # помечаем текст как увиденный
                await mark_seen(text_key, DEDUP_TEXT_TTL_SEC)

            except Exception as e:
                print("Handler error:", repr(e))
                traceback.print_exc()

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
