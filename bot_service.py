import asyncio
import logging
import traceback

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest, Forbidden, RetryAfter, TelegramError, TimedOut
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from channels import CHANNELS, DELISTING_CHANNELS
from config import BOT_TOKEN
from db import (
    ALERT_DELISTING,
    ALERT_LISTING,
    add_subscriber,
    get_disabled_exchanges,
    get_subscriber_alert_settings,
    get_subscribers,
    remove_subscriber,
    set_alert_enabled,
    set_exchange_selection,
)
from parser import KNOWN_EXCHANGES
from tokens_ui import token_card_text, tokens_keyboard


logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telethon").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

_app: Application | None = None
_bot_loop: asyncio.AbstractEventLoop | None = None

# Alerts whose venue the parser could not identify are still worth delivering,
# so subscribers get an explicit switch for them instead of a silent leak.
OTHER_EXCHANGE_KEY = "__other__"
OTHER_EXCHANGE_LABEL = "\u041f\u0440\u043e\u0447\u0438\u0435"
ALERT_EXCHANGES: list[str] = [*KNOWN_EXCHANGES, OTHER_EXCHANGE_KEY]

_ALERT_TITLES = {
    ALERT_LISTING: "\u041b\u0438\u0441\u0442\u0438\u043d\u0433\u0438",
    ALERT_DELISTING: "\u0414\u0435\u043b\u0438\u0441\u0442\u0438\u043d\u0433\u0438",
}
_ALERTS_ROOT_TEXT = (
    "\U0001F514 <b>\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 "
    "\u043e\u043f\u043e\u0432\u0435\u0449\u0435\u043d\u0438\u0439</b>\n\n"
    "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0440\u0430\u0437\u0434\u0435\u043b, "
    "\u0447\u0442\u043e\u0431\u044b \u043e\u0442\u043c\u0435\u0442\u0438\u0442\u044c \u0431\u0438\u0440\u0436\u0438."
)
_PENDING_KEY = "alerts_pending"


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("PTB ERROR:", repr(context.error))
    traceback.print_exc()


async def channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = [
        "\U0001F4CC <b>\u041a\u0430\u043d\u0430\u043b\u044b, \u043a\u043e\u0442\u043e\u0440\u044b\u0435 \u044f \u043f\u0430\u0440\u0441\u044e:</b>\n",
        "<b>Listings:</b>",
    ]
    for ch in CHANNELS:
        url = f"https://t.me/{ch}"
        lines.append(f'• <a href="{url}">@{ch}</a>')

    lines.append("")
    lines.append("<b>Delistings:</b>")
    if DELISTING_CHANNELS:
        for ch in DELISTING_CHANNELS:
            url = f"https://t.me/{ch}"
            lines.append(f'• <a href="{url}">@{ch}</a>')
    else:
        lines.append("• —")

    text = "\n".join(lines)
    await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)


async def about_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "\u0421\u043e\u0437\u0434\u0430\u0442\u0435\u043b\u044c \u0431\u043e\u0442\u0430 "
        '<a href="https://t.me/Chikago_11">Chikago1</a> '
        '\u0438\u0437 <a href="https://t.me/MetaMors1">Metamors</a>'
    )
    await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)


async def tokens_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Tokens:", reply_markup=tokens_keyboard(page=0))


def exchange_label(key: str) -> str:
    return OTHER_EXCHANGE_LABEL if key == OTHER_EXCHANGE_KEY else key


def exchange_filter_key(exchange: str | None) -> str:
    """Map the exchange on an alert to the switch that controls it."""
    name = (exchange or "").strip()
    return name if name in KNOWN_EXCHANGES else OTHER_EXCHANGE_KEY


async def stored_selection(chat_id: int, alert_type: str) -> set[str]:
    """Exchanges currently switched on for this subscriber.

    A disabled alert type reads as "nothing selected", so the menu always shows
    the same state the broadcaster acts on.
    """
    settings = await get_subscriber_alert_settings(chat_id)
    if not settings.get(alert_type, False):
        return set()
    disabled = await get_disabled_exchanges(chat_id, alert_type)
    return {ex for ex in ALERT_EXCHANGES if ex not in disabled}


def selection_summary(selected: set[str]) -> str:
    total = len(ALERT_EXCHANGES)
    if not selected:
        return "\u0432\u044b\u043a\u043b\u044e\u0447\u0435\u043d\u043e"
    if len(selected) >= total:
        return "\u0432\u0441\u0435 \u0431\u0438\u0440\u0436\u0438"
    return f"{len(selected)} \u0438\u0437 {total}"


def alerts_root_keyboard(counts: dict[str, set[str]]) -> InlineKeyboardMarkup:
    rows = []
    for alert_type in (ALERT_LISTING, ALERT_DELISTING):
        selected = counts.get(alert_type, set())
        mark = "\u2705" if selected else "\u2B1C"
        rows.append(
            [
                InlineKeyboardButton(
                    f"{mark} {_ALERT_TITLES[alert_type]} \u2014 {selection_summary(selected)}",
                    callback_data=f"al:open:{alert_type}",
                )
            ]
        )
    return InlineKeyboardMarkup(rows)


def exchanges_keyboard(alert_type: str, selected: set[str]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("\u0412\u044b\u0431\u0440\u0430\u0442\u044c \u0432\u0441\u0435", callback_data=f"al:all:{alert_type}"),
            InlineKeyboardButton("\u0421\u0431\u0440\u043e\u0441\u0438\u0442\u044c \u0432\u0441\u0435", callback_data=f"al:none:{alert_type}"),
        ]
    ]

    pair: list[InlineKeyboardButton] = []
    for key in ALERT_EXCHANGES:
        mark = "\u2705" if key in selected else "\u2B1C"
        # Carry the exchange name, not its position: adding a venue must not
        # turn the buttons of an already-open menu into their neighbours.
        pair.append(
            InlineKeyboardButton(
                f"{mark} {exchange_label(key)}",
                callback_data=f"al:tog:{alert_type}:{key}",
            )
        )
        if len(pair) == 2:
            rows.append(pair)
            pair = []
    if pair:
        rows.append(pair)

    rows.append(
        [
            InlineKeyboardButton("\u25c0\ufe0f \u041d\u0430\u0437\u0430\u0434", callback_data="al:back"),
            InlineKeyboardButton("\u041f\u0440\u0438\u043c\u0435\u043d\u0438\u0442\u044c", callback_data=f"al:apply:{alert_type}"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def exchanges_text(alert_type: str, selected: set[str]) -> str:
    return (
        f"<b>{_ALERT_TITLES[alert_type]}</b>\n\n"
        f"\u041e\u0442\u043c\u0435\u0447\u0435\u043d\u043e: {selection_summary(selected)}\n"
        "\u0418\u0437\u043c\u0435\u043d\u0435\u043d\u0438\u044f \u0432\u0441\u0442\u0443\u043f\u044f\u0442 \u0432 \u0441\u0438\u043b\u0443 \u043f\u043e\u0441\u043b\u0435 \u00ab\u041f\u0440\u0438\u043c\u0435\u043d\u0438\u0442\u044c\u00bb."
    )


async def alerts_root_markup(chat_id: int) -> InlineKeyboardMarkup:
    counts = {
        alert_type: await stored_selection(chat_id, alert_type)
        for alert_type in (ALERT_LISTING, ALERT_DELISTING)
    }
    return alerts_root_keyboard(counts)


async def alerts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    context.user_data.pop(_PENDING_KEY, None)
    await update.message.reply_text(
        _ALERTS_ROOT_TEXT,
        parse_mode="HTML",
        reply_markup=await alerts_root_markup(chat_id),
    )


async def _edit(q, text: str, markup: InlineKeyboardMarkup):
    """Repainting a screen that already looks like this is not an error."""
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    except BadRequest as e:
        if "not modified" not in str(e).lower():
            raise


async def alerts_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str):
    chat_id = update.effective_chat.id
    q = update.callback_query
    parts = data.split(":", 3)
    action = parts[1] if len(parts) > 1 else ""
    alert_type = parts[2] if len(parts) > 2 else ""

    # A callback query may be answered exactly once; "apply" answers with a
    # toast of its own further down.
    if action != "apply":
        await q.answer()

    # Edits live in user_data until "Применить", so "Назад" really discards them.
    pending: dict[str, set[str]] = context.user_data.setdefault(_PENDING_KEY, {})

    if action == "back":
        context.user_data.pop(_PENDING_KEY, None)
        await _edit(q, _ALERTS_ROOT_TEXT, await alerts_root_markup(chat_id))
        return

    if alert_type not in _ALERT_TITLES:
        return

    if action == "open":
        # A restart drops pending edits; fall back to what is stored.
        pending[alert_type] = await stored_selection(chat_id, alert_type)
    elif action == "all":
        pending[alert_type] = set(ALERT_EXCHANGES)
    elif action == "none":
        pending[alert_type] = set()
    elif action == "tog":
        selected = pending.get(alert_type)
        if selected is None:
            selected = await stored_selection(chat_id, alert_type)
        key = parts[3] if len(parts) > 3 else ""
        if key not in ALERT_EXCHANGES:
            return
        selected = set(selected)
        if key in selected:
            selected.discard(key)
        else:
            selected.add(key)
        pending[alert_type] = selected
    elif action == "apply":
        selected = pending.get(alert_type)
        if selected is None:
            selected = await stored_selection(chat_id, alert_type)
        disabled = {ex for ex in ALERT_EXCHANGES if ex not in selected}
        await set_exchange_selection(chat_id, alert_type, disabled)
        await set_alert_enabled(chat_id, alert_type, bool(selected))
        context.user_data.pop(_PENDING_KEY, None)
        await q.answer(f"Сохранено: {selection_summary(selected)}")
        await _edit(q, _ALERTS_ROOT_TEXT, await alerts_root_markup(chat_id))
        return
    else:
        return

    selected = pending[alert_type]
    await _edit(
        q,
        exchanges_text(alert_type, selected),
        exchanges_keyboard(alert_type, selected),
    )


async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        q = update.callback_query
        data = q.data or ""
        print("CB:", data)

        if data.startswith("al:"):
            await alerts_cb(update, context, data)
            return

        await q.answer("OK")

        if data.startswith("tokpage:"):
            page = int(data.split(":")[1])
            await q.edit_message_reply_markup(reply_markup=tokens_keyboard(page=page))
            return

        if data.startswith("tok:"):
            token = data.split(":")[1]
            text = token_card_text(token)
            await q.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)
            return

    except Exception as e:
        print("CB ERROR:", repr(e))
        try:
            await update.callback_query.answer("ERR", show_alert=True)
        except Exception:
            pass


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await add_subscriber(chat_id)
    await update.message.reply_text(
        "\u2705 \u041f\u043e\u0434\u043f\u0438\u0441\u0430\u043b.\n"
        "\u0411\u0443\u0434\u0443 \u043f\u0440\u0438\u0441\u044b\u043b\u0430\u0442\u044c \u043b\u0438\u0441\u0442\u0438\u043d\u0433\u0438 \u0438\u0437 \u043a\u0430\u043d\u0430\u043b\u043e\u0432.\n\n"
        "\u0427\u0442\u043e\u0431\u044b \u043e\u0442\u043f\u0438\u0441\u0430\u0442\u044c\u0441\u044f: /stop"
    )


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await remove_subscriber(chat_id)
    await update.message.reply_text(
        "\U0001F6D1 \u041e\u043a, \u043e\u0442\u043f\u0438\u0441\u0430\u043b. "
        "\u0427\u0442\u043e\u0431\u044b \u0441\u043d\u043e\u0432\u0430 \u043f\u043e\u0434\u043f\u0438\u0441\u0430\u0442\u044c\u0441\u044f: /start"
    )


async def subs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subs = await get_subscribers()
    await update.message.reply_text(f"\u041f\u043e\u0434\u043f\u0438\u0441\u0447\u0438\u043a\u043e\u0432: {len(subs)}")


async def broadcast(
    text: str,
    reply_markup=None,
    parse_mode: str | None = None,
    alert_type: str | None = None,
    exchange: str | None = None,
):
    if _app is None:
        logger.error("Broadcast skipped: bot application is not initialized")
        return

    loop = _bot_loop
    if loop is not None and loop is not asyncio.get_running_loop():
        future = asyncio.run_coroutine_threadsafe(
            _broadcast_on_bot_loop(text, reply_markup, parse_mode, alert_type, exchange),
            loop,
        )
        await asyncio.wrap_future(future)
        return

    await _broadcast_on_bot_loop(text, reply_markup, parse_mode, alert_type, exchange)


async def _broadcast_on_bot_loop(
    text: str,
    reply_markup=None,
    parse_mode: str | None = None,
    alert_type: str | None = None,
    exchange: str | None = None,
):
    if _app is None:
        logger.error("Broadcast skipped: bot application is not initialized")
        return

    filter_key = exchange_filter_key(exchange) if alert_type else None
    subs = await get_subscribers(alert_type=alert_type, exchange=filter_key)
    dead = []
    sent = 0
    failed = 0

    for chat_id in subs:
        try:
            await _app.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
            sent += 1
            await asyncio.sleep(0.05)
        except RetryAfter as e:
            failed += 1
            retry_after = int(getattr(e, "retry_after", 1) or 1)
            logger.warning("Telegram flood limit while sending to %s: retry_after=%s", chat_id, retry_after)
            await asyncio.sleep(max(1, retry_after))
        except Forbidden as e:
            failed += 1
            logger.warning("Removing inactive subscriber %s: %s", chat_id, e)
            dead.append(chat_id)
        except BadRequest as e:
            failed += 1
            logger.error("Telegram bad request for subscriber %s: %s", chat_id, e)
        except TimedOut as e:
            failed += 1
            logger.warning("Telegram timeout for subscriber %s: %s", chat_id, e)
        except TelegramError as e:
            failed += 1
            logger.exception("Telegram send failed for subscriber %s: %s", chat_id, e)
        except Exception as e:
            failed += 1
            logger.exception("Unexpected send failed for subscriber %s: %s", chat_id, e)

    for chat_id in dead:
        await remove_subscriber(chat_id)

    logger.info(
        "Broadcast finished: alert_type=%s exchange=%s subscribers=%s sent=%s failed=%s removed=%s",
        alert_type,
        filter_key,
        len(subs),
        sent,
        failed,
        len(dead),
    )


def run_bot_polling_blocking():
    """Запускается в отдельном потоке. Создаем свой event loop и живем в нем."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _runner():
        global _app, _bot_loop
        _bot_loop = asyncio.get_running_loop()
        _app = Application.builder().token(BOT_TOKEN).build()
        fatal_polling_error: TelegramError | None = None
        fatal_polling_event = asyncio.Event()

        def polling_error_callback(error: TelegramError) -> None:
            nonlocal fatal_polling_error
            logger.exception("Telegram polling error: %s", error, exc_info=error)

            # This means the event loop/executor is already shutting down.
            # Retrying inside PTB leaves the bot dead while the process stays alive.
            if "cannot schedule new futures after shutdown" in str(error):
                fatal_polling_error = error
                fatal_polling_event.set()

        _app.add_handler(CommandHandler("start", start_cmd))
        _app.add_handler(CommandHandler("stop", stop_cmd))
        _app.add_handler(CommandHandler("subs", subs_cmd))
        _app.add_handler(CommandHandler("tokens", tokens_cmd))
        _app.add_handler(CommandHandler("alerts", alerts_cmd))
        _app.add_handler(CommandHandler("about", about_cmd))
        _app.add_handler(CommandHandler("channels", channels_cmd))
        _app.add_handler(CallbackQueryHandler(cb_handler))

        try:
            await _app.initialize()
            await _app.bot.delete_webhook(drop_pending_updates=True)
            await _app.start()
            await _app.updater.start_polling(
                allowed_updates=Update.ALL_TYPES,
                error_callback=polling_error_callback,
            )

            keep_alive_task = asyncio.create_task(asyncio.Event().wait())
            fatal_task = asyncio.create_task(fatal_polling_event.wait())
            watch_tasks = [keep_alive_task, fatal_task]
            polling_task = getattr(_app.updater, "_Updater__polling_task", None)
            if polling_task is not None:
                watch_tasks.append(polling_task)

            done, pending = await asyncio.wait(
                watch_tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            if fatal_task in done:
                raise RuntimeError(
                    "Fatal Telegram polling error; exiting for supervisor restart"
                ) from fatal_polling_error

            if polling_task is not None and polling_task in done:
                exc = polling_task.exception()
                if exc:
                    raise RuntimeError("Telegram polling task failed") from exc
                raise RuntimeError("Telegram polling task stopped unexpectedly")
        finally:
            app = _app
            _app = None
            _bot_loop = None
            if app is not None:
                try:
                    if app.updater and app.updater.running:
                        await app.updater.stop()
                except Exception:
                    logger.exception("Failed to stop Telegram updater")
                try:
                    if app.running:
                        await app.stop()
                except Exception:
                    logger.exception("Failed to stop Telegram application")
                try:
                    await app.shutdown()
                except Exception:
                    logger.exception("Failed to shutdown Telegram application")

    try:
        loop.run_until_complete(_runner())
    finally:
        asyncio.set_event_loop(None)
        loop.close()
