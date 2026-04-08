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
    MESSAGE_SEEN_TTL_SEC,
    BACKFILL_CHANNELS,
    BACKFILL_LIMIT,
    BACKFILL_INTERVAL_SEC,
    BACKFILL_MAX_AGE_SEC,
    POSTS_LOG_PATH,
    TOKEN_TTL_DAYS,
)
from channels import (
    CHANNELS,
    CHANNEL_SKIP_PHRASES,
    DELISTING_CHANNELS,
    DELISTING_KEYWORD_CHANNELS,
    DELISTING_CHANNEL_SKIP_PHRASES,
    MONITORED_CHANNELS,
)
from db import init_db, is_seen, mark_seen, gc
from parser import normalize_text, extract, extract_delisting  # extract_many если подключишь позже
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


def as_utc(dt):
    if not dt:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def message_version_ts(message) -> int:
    dt = as_utc(getattr(message, "edit_date", None) or getattr(message, "date", None))
    if not dt:
        return 0
    return int(dt.timestamp())


def is_older_than(message, max_age_sec: int) -> bool:
    if max_age_sec <= 0:
        return False
    dt = as_utc(getattr(message, "edit_date", None) or getattr(message, "date", None))
    if not dt:
        return False
    age_sec = (datetime.now(timezone.utc) - dt).total_seconds()
    return age_sec > max_age_sec


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
    for ch in MONITORED_CHANNELS:
        try:
            await client(JoinChannelRequest(ch))
            print("Joined:", ch)
        except Exception as e:
            print("Join skipped:", ch, "->", str(e))


async def run():
    await init_db()

    async with TelegramClient(SESSION_NAME, API_ID, API_HASH) as client:
        await join_channels(client)
        process_lock = asyncio.Lock()
        listing_channels_set = {c.lstrip("@").lower() for c in CHANNELS}
        delisting_channels_set = {c.lstrip("@").lower() for c in DELISTING_CHANNELS}
        dual_routing_channels_set = listing_channels_set & delisting_channels_set
        delisting_keyword_channels_set = {
            c.lstrip("@").lower() for c in DELISTING_KEYWORD_CHANNELS
        }

        async def process_message(message, chat, source_chat_id=None):
            ch = getattr(chat, "username", None)
            chat_id = getattr(message, "chat_id", None) or source_chat_id
            source_tag = f"@{ch}" if ch else str(chat_id or "unknown")

            msg_id = getattr(message, "id", None)
            if not msg_id:
                return

            msg_version = message_version_ts(message)
            msg_key = f"m:{source_tag}:{msg_id}:{msg_version}"
            if await is_seen(msg_key):
                return

            # Old posts in monitored channels are sometimes edited days later.
            # We keep fresh edits (useful for placeholder/reserved posts) but
            # ignore edits of old messages to prevent duplicate alerts.
            edit_date = getattr(message, "edit_date", None)
            ch_norm = (ch or "").lstrip("@").lower()
            feed_type = "delisting" if ch_norm in delisting_channels_set else "listing"
            if ch_norm in dual_routing_channels_set:
                feed_type = "listing"
            bypass_old_edit_age = ch_norm in OLD_EDIT_BYPASS_CHANNELS
            if edit_date and MAX_EDIT_MESSAGE_AGE_SEC > 0 and not bypass_old_edit_age:
                if is_older_than(message, MAX_EDIT_MESSAGE_AGE_SEC):
                    dt = as_utc(getattr(message, "date", None))
                    age_sec = int((datetime.now(timezone.utc) - dt).total_seconds()) if dt else -1
                    print(f"Skip old edit: source={source_tag} msg_id={msg_id} age_sec={age_sec}")
                    log_post_event(
                        source=str(source_tag),
                        original_post=(message.message or message.raw_text or "").strip(),
                        status="skip: old edit",
                    )
                    await mark_seen(msg_key, MESSAGE_SEEN_TTL_SEC)
                    return

            raw_text = message.message or message.raw_text or ""
            preview_text = build_preview_text(message)
            parse_text = raw_text if not preview_text else f"{raw_text}\n{preview_text}"
            if (
                feed_type == "listing"
                and (
                    ch_norm in delisting_keyword_channels_set
                    or ch_norm in dual_routing_channels_set
                )
                and re.search(
                    r"\bdelisted\b|\bdelist\b|\bdelisting\b|\bdelistings\b|\u0434\u0435\u043b\u0438\u0441\u0442\u0438\u043d\u0433",
                    parse_text,
                    re.IGNORECASE,
                )
            ):
                feed_type = "delisting"

            # body_html keeps hidden links from entity markup.
            entities = message.entities or []
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
                await mark_seen(msg_key, MESSAGE_SEEN_TTL_SEC)
                return

            # Channel-specific content exceptions (case-insensitive).
            skip_map = (
                DELISTING_CHANNEL_SKIP_PHRASES
                if feed_type == "delisting"
                else CHANNEL_SKIP_PHRASES
            )
            skip_phrases = skip_map.get(ch_norm, ())
            parse_text_lower = parse_text.lower()
            if skip_phrases and any(phrase in parse_text_lower for phrase in skip_phrases):
                print(f"Skip channel exception: source={source_tag} msg_id={msg_id}")
                log_post_event(
                    source=str(source_tag),
                    original_post=original_post,
                    status="skip: channel exception",
                )
                await mark_seen(msg_key, MESSAGE_SEEN_TTL_SEC)
                return

            if not body_html:
                body_html = html.escape(parse_text).replace("\n", "<br>")

            # --- text dedup ---
            norm = normalize_text(raw_text).lower()
            if not norm:
                norm = normalize_text(parse_text).lower()
            if not norm:
                # URL-only posts are common in feed channels.
                norm = " ".join(parse_text.split()).lower()
            if not norm:
                await mark_seen(msg_key, MESSAGE_SEEN_TTL_SEC)
                return

            # Keep text dedup per source channel so mirrored posts from
            # another feed do not fully suppress this source.
            text_key = f"t:{feed_type}:{source_tag}:" + sha256(norm)
            if await is_seen(text_key):
                print(f"Skip text dedup: source={source_tag} msg_id={msg_id}")
                log_post_event(
                    source=str(source_tag),
                    original_post=original_post,
                    status="duplicate",
                )
                await mark_seen(msg_key, MESSAGE_SEEN_TTL_SEC)
                return

            source_url = msg_url(ch, msg_id) if ch else msg_url_fallback(chat_id, msg_id)
            source_emoji = "\U0001F517"
            src_title = getattr(chat, "title", None) or (f"@{ch}" if ch else "channel")
            if source_url:
                src_line = (
                    f'{source_emoji} <b>Source:</b> '
                    f'<a href="{html.escape(source_url, quote=True)}">{html.escape(str(src_title))}</a>'
                )
            else:
                src_line = f"{source_emoji} <b>Source:</b> {html.escape(str(src_title))}"

            if feed_type == "delisting":
                dmeta = extract_delisting(parse_text)
                tokens = dmeta.get("tokens") or []
                exchange = (dmeta.get("exchange") or "").strip()
                market = (dmeta.get("market_type") or "").strip().lower()
                action = (dmeta.get("action") or "delisted").strip()
                event_url = (dmeta.get("event_url") or "").strip()

                if not exchange or market not in ("spot", "futures"):
                    print(f"Skip delisting parse miss: source={source_tag} msg_id={msg_id}")
                    log_post_event(
                        source=str(src_title),
                        original_post=original_post,
                        status="skip: delisting parse miss",
                    )
                    await mark_seen(text_key, DEDUP_TEXT_TTL_SEC)
                    await mark_seen(msg_key, MESSAGE_SEEN_TTL_SEC)
                    return

                if tokens:
                    fresh_tokens = []
                    for tok in tokens:
                        dkey = f"d:{exchange}:{market}:{tok}"
                        if await is_seen(dkey):
                            continue
                        fresh_tokens.append(tok)
                        await mark_seen(dkey, DEDUP_STRUCT_TTL_SEC)

                    if not fresh_tokens:
                        print(f"Skip delisting struct dedup: source={source_tag} msg_id={msg_id}")
                        log_post_event(
                            source=str(src_title),
                            original_post=original_post,
                            status="duplicate",
                        )
                        await mark_seen(text_key, DEDUP_TEXT_TTL_SEC)
                        await mark_seen(msg_key, MESSAGE_SEEN_TTL_SEC)
                        return
                else:
                    # Some announcements describe "multiple contracts" without explicit symbols.
                    # Use exchange+market+normalized text as fallback dedup key.
                    dkey = f"d:{exchange}:{market}:bulk:{sha256(norm)}"
                    if await is_seen(dkey):
                        print(f"Skip delisting bulk dedup: source={source_tag} msg_id={msg_id}")
                        log_post_event(
                            source=str(src_title),
                            original_post=original_post,
                            status="duplicate",
                        )
                        await mark_seen(text_key, DEDUP_TEXT_TTL_SEC)
                        await mark_seen(msg_key, MESSAGE_SEEN_TTL_SEC)
                        return
                    await mark_seen(dkey, DEDUP_STRUCT_TTL_SEC)

                token_text = ", ".join(f"${t}" for t in tokens) if tokens else "MULTIPLE PAIRS"
                market_label = "Futures" if market == "futures" else "Spot"
                tag = "F" if market == "futures" else "S"
                notice_emoji = "\U0001F4E2"

                body_html = shorten_for_html(body_html, parse_text, limit=3000)
                line1 = (
                    f'\u26a0\ufe0f<b>Delisting</b>: '
                    f'<b>{html.escape(token_text)}</b> ({html.escape(action)})'
                )
                line2 = f'\U0001F3E6 <b>Exchange:</b> {html.escape(exchange)}({tag})'
                if event_url:
                    line3 = (
                        f'{notice_emoji} <b>Market:</b> '
                        f'<a href="{html.escape(event_url, quote=True)}">{html.escape(market_label)}</a>'
                    )
                else:
                    line3 = f"{notice_emoji} <b>Market:</b> {html.escape(market_label)}"

                alert_html = f"{line1}\n{line2}\n{line3}\n{src_line}\n\n{body_html}"
                await send_alert(alert_html, parse_mode="HTML", alert_type="delisting")
                log_post_event(
                    source=str(src_title),
                    original_post=original_post,
                    status="sent: delisting",
                    post_for_user=alert_html,
                )
                await mark_seen(text_key, DEDUP_TEXT_TTL_SEC)
                await mark_seen(msg_key, MESSAGE_SEEN_TTL_SEC)
                return

            meta = extract(parse_text)

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
                        status="duplicate",
                    )
                    await mark_seen(msg_key, MESSAGE_SEEN_TTL_SEC)
                    return
                await mark_seen(k, DEDUP_STRUCT_TTL_SEC)

            mt_raw = (meta.get("market_type") or "").strip().lower()
            sym = meta.get("display") or (meta.get("base") or "?")

            token_emoji = "\U0001FA99"

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

            body_html = shorten_for_html(body_html, parse_text, limit=3000)
            alert_html = f"{line1}\n{src_line}\n\n{body_html}"

            await send_alert(alert_html, parse_mode="HTML", alert_type="listing")
            log_post_event(
                source=str(src_title),
                original_post=original_post,
                status="sent",
                post_for_user=alert_html,
            )
            # Keep token state in sync with what was actually sent by the bot.
            if base and ex_name and mt_raw in ("spot", "futures"):
                try:
                    upsert_listing(
                        token=base,
                        market_type=mt_raw,
                        exchange=ex_name,
                    )
                except Exception as e:
                    print("CSV store error:", repr(e))

            # Mark as processed by text and by message-version identity.
            await mark_seen(text_key, DEDUP_TEXT_TTL_SEC)
            await mark_seen(msg_key, MESSAGE_SEEN_TTL_SEC)

        async def process_message_locked(message, chat, source_chat_id=None):
            async with process_lock:
                try:
                    await process_message(message, chat, source_chat_id=source_chat_id)
                except Exception as e:
                    print("Handler error:", repr(e))
                    traceback.print_exc()

        @client.on(events.NewMessage(chats=MONITORED_CHANNELS))
        @client.on(events.MessageEdited(chats=MONITORED_CHANNELS))
        async def handler(event):
            await process_message_locked(
                event.message,
                event.chat,
                source_chat_id=getattr(event, "chat_id", None),
            )

        async def backfill_channel(channel_name: str):
            try:
                entity = await client.get_entity(channel_name)
            except Exception as e:
                print("Backfill entity error:", channel_name, "->", str(e))
                return

            messages = []
            async for msg in client.iter_messages(entity, limit=BACKFILL_LIMIT):
                messages.append(msg)

            # iter_messages yields newest-first; process oldest-first to keep order.
            for msg in reversed(messages):
                if BACKFILL_MAX_AGE_SEC > 0 and is_older_than(msg, BACKFILL_MAX_AGE_SEC):
                    continue
                await process_message_locked(
                    msg,
                    entity,
                    source_chat_id=getattr(msg, "chat_id", None) or getattr(entity, "id", None),
                )

        async def backfill_loop():
            if not BACKFILL_CHANNELS:
                return
            while True:
                for ch_name in BACKFILL_CHANNELS:
                    try:
                        await backfill_channel(ch_name)
                    except Exception as e:
                        print("Backfill loop error:", ch_name, "->", repr(e))
                await asyncio.sleep(max(15, BACKFILL_INTERVAL_SEC))

        async def gc_loop():
            while True:
                try:
                    removed = purge_old_tokens(days=TOKEN_TTL_DAYS)
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
            backfill_loop(),
            asyncio.to_thread(run_bot_polling_blocking),
        )


if __name__ == "__main__":
    asyncio.run(run())
