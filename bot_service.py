import asyncio
import logging
import traceback

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from channels import CHANNELS, DELISTING_CHANNELS
from config import BOT_TOKEN
from db import (
    ALERT_DELISTING,
    ALERT_LISTING,
    add_subscriber,
    get_subscriber_alert_settings,
    get_subscribers,
    remove_subscriber,
    toggle_subscriber_alert,
)
from tokens_ui import token_card_text, tokens_keyboard


logging.basicConfig(level=logging.INFO)

_app: Application | None = None
_ALERTS_TEXT = (
    "\u041d\u0430\u0441\u0442\u0440\u043e\u0439\u043a\u0438 \u043e\u043f\u043e\u0432\u0435\u0449\u0435\u043d\u0438\u0439:\n"
    "\u0414\u0435\u043b\u0438\u0441\u0442\u0438\u043d\u0433\u0438 \u043f\u043e\u043a\u0430 \u0432 \u0440\u0435\u0436\u0438\u043c\u0435 \u0437\u0430\u0433\u043b\u0443\u0448\u043a\u0438."
)
_ALERT_TOGGLE_PREFIX = "alerts:toggle:"


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


def alerts_keyboard(settings: dict[str, bool]) -> InlineKeyboardMarkup:
    listing_mark = "\u2705" if settings.get(ALERT_LISTING, True) else "\u2B1C"
    delisting_mark = "\u2705" if settings.get(ALERT_DELISTING, False) else "\u2B1C"
    rows = [
        [
            InlineKeyboardButton(
                f"{listing_mark} \u041b\u0438\u0441\u0442\u0438\u043d\u0433\u0438",
                callback_data=f"{_ALERT_TOGGLE_PREFIX}{ALERT_LISTING}",
            )
        ],
        [
            InlineKeyboardButton(
                f"{delisting_mark} \u0414\u0435\u043b\u0438\u0441\u0442\u0438\u043d\u0433\u0438",
                callback_data=f"{_ALERT_TOGGLE_PREFIX}{ALERT_DELISTING}",
            )
        ],
    ]
    return InlineKeyboardMarkup(rows)


async def alerts_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    settings = await get_subscriber_alert_settings(chat_id)
    await update.message.reply_text(_ALERTS_TEXT, reply_markup=alerts_keyboard(settings))


async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        q = update.callback_query
        data = q.data or ""
        print("CB:", data)
        await q.answer("OK")

        if data.startswith(_ALERT_TOGGLE_PREFIX):
            alert_type = data.replace(_ALERT_TOGGLE_PREFIX, "", 1)
            settings = await toggle_subscriber_alert(update.effective_chat.id, alert_type)
            await q.edit_message_reply_markup(reply_markup=alerts_keyboard(settings))
            return

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
):
    global _app
    if _app is None:
        return

    subs = await get_subscribers(alert_type=alert_type)
    dead = []

    for chat_id in subs:
        try:
            await _app.bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=True,
            )
            await asyncio.sleep(0.05)
        except Exception:
            dead.append(chat_id)

    for chat_id in dead:
        await remove_subscriber(chat_id)


def run_bot_polling_blocking():
    """Запускается в отдельном потоке. Создаем свой event loop и живем в нем."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _runner():
        global _app
        _app = Application.builder().token(BOT_TOKEN).build()

        _app.add_handler(CommandHandler("start", start_cmd))
        _app.add_handler(CommandHandler("stop", stop_cmd))
        _app.add_handler(CommandHandler("subs", subs_cmd))
        _app.add_handler(CommandHandler("tokens", tokens_cmd))
        _app.add_handler(CommandHandler("alerts", alerts_cmd))
        _app.add_handler(CommandHandler("about", about_cmd))
        _app.add_handler(CommandHandler("channels", channels_cmd))
        _app.add_handler(CallbackQueryHandler(cb_handler))

        await _app.initialize()
        await _app.bot.delete_webhook(drop_pending_updates=True)
        await _app.start()
        await _app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        await asyncio.Event().wait()  # держим поток живым

    loop.run_until_complete(_runner())