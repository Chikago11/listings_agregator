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
    DELISTING_STRUCT_TTL_SEC,
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
    CHANNEL_SKIP_ALL_WORDS,
    DELISTING_CHANNELS,
    DELISTING_KEYWORD_CHANNELS,
    DELISTING_CHANNEL_SKIP_PHRASES,
    MONITORED_CHANNELS,
)
from db import init_db, is_seen, mark_seen, gc
from parser import (
    normalize_text,
    extract,
    extract_many,
    extract_delisting,
    has_delisting_keyword,
    extract_binance_wallet_announcement,
)
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
    # Age checks must be based on original post creation time.
    # Otherwise an edit of a very old post looks "fresh" and can be resent.
    dt = as_utc(getattr(message, "date", None))
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
        channel_exchange_override = {
            "bitget_listings": "Bitget",
            "ourbit_listings": "Ourbit",
            "hyperliquid_announcements": "Hyperliquid",
        }
        channel_market_type_override = {
            "hyperliquid_announcements": "futures",
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
            forced_exchange = channel_exchange_override.get(ch_norm)
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
                and has_delisting_keyword(raw_text)
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
                hidden_block = "\n".join(hidden_urls)
                parse_text = f"{parse_text}\n{hidden_block}" if parse_text else hidden_block
            else:
                hidden_block = ""

            # Delisting parser should rely on source post body and hidden links.
            # Web preview snippets may contain unrelated "delist" words.
            delisting_parse_text = raw_text.strip()
            if not delisting_parse_text:
                delisting_parse_text = preview_text.strip()
            if hidden_block:
                delisting_parse_text = (
                    f"{delisting_parse_text}\n{hidden_block}"
                    if delisting_parse_text
                    else hidden_block
                )

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

            # Channel-specific AND exceptions: skip when all words in a group exist.
            skip_all_words_groups = CHANNEL_SKIP_ALL_WORDS.get(ch_norm, ())
            if skip_all_words_groups and any(
                all(word in parse_text_lower for word in group)
                for group in skip_all_words_groups
            ):
                print(f"Skip channel exception (all words): source={source_tag} msg_id={msg_id}")
                log_post_event(
                    source=str(source_tag),
                    original_post=original_post,
                    status="skip: channel exception all words",
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
                dmeta = extract_delisting(delisting_parse_text)
                tokens = dmeta.get("tokens") or []
                exchange = (forced_exchange or dmeta.get("exchange") or "").strip()
                market = (
                    dmeta.get("market_type")
                    or channel_market_type_override.get(ch_norm)
                    or ""
                ).strip().lower()
                action = (dmeta.get("action") or "").strip()
                event_url = (dmeta.get("event_url") or "").strip()

                if not action or not exchange or market not in ("spot", "futures"):
                    print(f"Skip delisting parse miss: source={source_tag} msg_id={msg_id}")
                    log_post_event(
                        source=str(src_title),
                        original_post=original_post,
                        status="skip: delisting parse miss",
                    )
                    await mark_seen(text_key, DEDUP_TEXT_TTL_SEC)
                    await mark_seen(msg_key, MESSAGE_SEEN_TTL_SEC)
                    return

                # Tokens that survive per-token dedup are the only ones worth
                # announcing: a post can repeat pairs that were already sent.
                alert_tokens = []
                if tokens:
                    fresh_tokens = []
                    for tok in tokens:
                        dkey = f"d:{exchange}:{market}:{tok}"
                        if await is_seen(dkey):
                            continue
                        fresh_tokens.append(tok)
                        await mark_seen(dkey, DELISTING_STRUCT_TTL_SEC)

                    alert_tokens = fresh_tokens

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
                    await mark_seen(dkey, DELISTING_STRUCT_TTL_SEC)

                token_text = (
                    ", ".join(f"${t}" for t in alert_tokens)
                    if alert_tokens
                    else "MULTIPLE PAIRS"
                )
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
                await send_alert(
                    alert_html,
                    parse_mode="HTML",
                    alert_type="delisting",
                    exchange=exchange,
                )
                log_post_event(
                    source=str(src_title),
                    original_post=original_post,
                    status="sent: delisting",
                    post_for_user=alert_html,
                )

                # Hyperliquid weekly updates can contain listing and delisting
                # events in one Telegram post. Preserve both alert types.
                if ch_norm == "hyperliquid_announcements":
                    lmeta = extract(parse_text)
                    if forced_exchange:
                        lmeta["exchange"] = forced_exchange
                    if not lmeta.get("market_type"):
                        lmeta["market_type"] = channel_market_type_override[ch_norm]

                    if lmeta.get("base") and re.search(r"\b(?:was|were)\s+listed\b", parse_text, re.IGNORECASE):
                        sym_key = lmeta.get("display") or lmeta.get("base") or ""
                        lkey = f"s:{lmeta.get('exchange')}:{lmeta.get('market_type')}:{sym_key}"
                        if not await is_seen(lkey):
                            await mark_seen(lkey, DEDUP_STRUCT_TTL_SEC)

                            lmt_raw = (lmeta.get("market_type") or "").strip().lower()
                            ltag = "F" if lmt_raw == "futures" else "S" if lmt_raw == "spot" else "?"
                            lex_known = (lmeta.get("exchange") or "").strip()
                            lex_name = lex_known or "unknown"
                            lbase = (lmeta.get("base") or "").strip()
                            lquote = (lmeta.get("quote") or "USDC").strip().upper()
                            lex_url = None
                            if lex_name and lbase and lmt_raw in ("spot", "futures"):
                                lex_url = build_exchange_link(lex_name, lmt_raw, base=lbase, quote=lquote)

                            if lex_url:
                                lex_part = f'<a href="{html.escape(lex_url, quote=True)}">{html.escape(lex_name)}</a>({ltag})'
                            else:
                                lex_part = f"{html.escape(lex_name)}({ltag})"

                            lline1 = f'\U0001FA99<b>{html.escape(str(sym_key))}</b>: {lex_part}'
                            lalert_html = f"{lline1}\n{src_line}\n\n{body_html}"
                            await send_alert(
                                lalert_html,
                                parse_mode="HTML",
                                alert_type="listing",
                                exchange=lex_known,
                            )
                            log_post_event(
                                source=str(src_title),
                                original_post=original_post,
                                status="sent: listing",
                                post_for_user=lalert_html,
                            )
                            if lbase and lex_known and lmt_raw in ("spot", "futures"):
                                try:
                                    upsert_listing(
                                        token=lbase,
                                        market_type=lmt_raw,
                                        exchange=lex_name,
                                    )
                                except Exception as e:
                                    print("CSV store error:", repr(e))

                await mark_seen(text_key, DEDUP_TEXT_TTL_SEC)
                await mark_seen(msg_key, MESSAGE_SEEN_TTL_SEC)
                return

            # Metascalp publishes a daily digest holding every listing of the
            # day. extract() reports only its first line, so the other venues in
            # the post never reach anyone. Fan the digest out into one alert per
            # (symbol, market, exchange) — the granularity subscribers filter by.
            digest_items = [] if forced_exchange else extract_many(parse_text)
            if len(digest_items) > 1:
                digest_sent = 0
                for item in digest_items:
                    ibase = (item.get("base") or "").strip()
                    if not ibase:
                        continue

                    isym = item.get("display") or ibase
                    iquote = (item.get("quote") or "USDT").strip().upper()
                    # Quote the entry's own line rather than the whole digest.
                    ibody = html.escape(item.get("line") or "") or body_html

                    for imarket, ivenues in (
                        ("futures", item.get("futures_exchanges") or []),
                        ("spot", item.get("spot_exchanges") or []),
                    ):
                        for venue in ivenues:
                            iex = (venue or "").strip()
                            if not iex:
                                continue

                            ikey = f"s:{iex}:{imarket}:{isym}"
                            if await is_seen(ikey):
                                continue
                            await mark_seen(ikey, DEDUP_STRUCT_TTL_SEC)

                            itag = "F" if imarket == "futures" else "S"
                            iurl = build_exchange_link(
                                iex, imarket, base=ibase, quote=iquote
                            )
                            if iurl:
                                ipart = (
                                    f'<a href="{html.escape(iurl, quote=True)}">'
                                    f"{html.escape(iex)}</a>({itag})"
                                )
                            else:
                                ipart = f"{html.escape(iex)}({itag})"

                            ialert = (
                                f"\U0001FA99<b>{html.escape(str(isym))}</b>: {ipart}"
                                f"\n{src_line}\n\n{ibody}"
                            )
                            await send_alert(
                                ialert,
                                parse_mode="HTML",
                                alert_type="listing",
                                exchange=iex,
                            )
                            digest_sent += 1
                            log_post_event(
                                source=str(src_title),
                                original_post=original_post,
                                status="sent: digest",
                                post_for_user=ialert,
                            )
                            try:
                                upsert_listing(
                                    token=ibase,
                                    market_type=imarket,
                                    exchange=iex,
                                )
                            except Exception as e:
                                print("CSV store error:", repr(e))

                if digest_sent:
                    print(
                        f"Digest expanded: source={source_tag} msg_id={msg_id} "
                        f"items={len(digest_items)} alerts={digest_sent}"
                    )
                else:
                    print(f"Skip digest dedup: source={source_tag} msg_id={msg_id}")
                    log_post_event(
                        source=str(src_title),
                        original_post=original_post,
                        status="duplicate",
                    )

                await mark_seen(text_key, DEDUP_TEXT_TTL_SEC)
                await mark_seen(msg_key, MESSAGE_SEEN_TTL_SEC)
                return

            if ch_norm == "binance_wallet_announcements":
                meta = extract_binance_wallet_announcement(parse_text)
                if not meta.get("base"):
                    print(f"Skip first-platform parse miss: source={source_tag} msg_id={msg_id}")
                    log_post_event(
                        source=str(src_title),
                        original_post=original_post,
                        status="skip: first-platform parse miss",
                    )
                    await mark_seen(text_key, DEDUP_TEXT_TTL_SEC)
                    await mark_seen(msg_key, MESSAGE_SEEN_TTL_SEC)
                    return
            else:
                meta = extract(parse_text)
            if forced_exchange:
                meta["exchange"] = forced_exchange
            if not meta.get("market_type") and ch_norm in channel_market_type_override:
                meta["market_type"] = channel_market_type_override[ch_norm]

            # No symbol means there is nothing to announce: the alert would read
            # "?" and tell the subscriber nothing. The delisting branch above
            # applies the same rule to its own required fields.
            if not meta.get("base"):
                print(f"Skip listing parse miss: source={source_tag} msg_id={msg_id}")
                log_post_event(
                    source=str(src_title),
                    original_post=original_post,
                    status="skip: listing parse miss",
                )
                await mark_seen(text_key, DEDUP_TEXT_TTL_SEC)
                await mark_seen(msg_key, MESSAGE_SEEN_TTL_SEC)
                return

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

            ex_known = (meta.get("exchange") or "").strip()
            ex_name = ex_known or "unknown"
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

            await send_alert(
                alert_html,
                parse_mode="HTML",
                alert_type="listing",
                exchange=ex_known,
            )
            log_post_event(
                source=str(src_title),
                original_post=original_post,
                status="sent",
                post_for_user=alert_html,
            )
            # Keep token state in sync with what was actually sent by the bot.
            # An unrecognised venue must not reach the CSV: "unknown" would show
            # up in the /tokens card as if it were an exchange.
            if base and ex_known and mt_raw in ("spot", "futures"):
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
