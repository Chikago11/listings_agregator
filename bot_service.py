import asyncio
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler

from config import BOT_TOKEN
from db import add_subscriber, remove_subscriber, get_subscribers
from tokens_ui import tokens_keyboard, token_card_text

from telegram.constants import ParseMode
from channels import CHANNELS

import traceback




logging.basicConfig(level=logging.INFO)

_app: Application | None = None

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    print("PTB ERROR:", repr(context.error))
    traceback.print_exc()

async def channels_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📌 <b>Каналы, которые я парсю:</b>\n"]
    for ch in CHANNELS:
        url = f"https://t.me/{ch}"
        lines.append(f'• <a href="{url}">@{ch}</a>')
    text = "\n".join(lines)
    await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)

async def about_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        'Создатель бота <a href="https://t.me/Chikago_11">Chikago1</a> '
        'из <a href="https://t.me/MetaMors1">Metamors</a>'
    )
    await update.message.reply_text(text, parse_mode="HTML", disable_web_page_preview=True)



async def tokens_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Tokens:", reply_markup=tokens_keyboard(page=0))


async def cb_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        q = update.callback_query
        data = (q.data or "")
        print("CB:", data)
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
        "✅ Подписал.\n"
        "Буду присылать листинги из каналов.\n\n"
        "Чтобы отписаться: /stop"
    )


async def stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await remove_subscriber(chat_id)
    await update.message.reply_text("🛑 Ок, отписал. Чтобы снова подписаться: /start")


async def subs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subs = await get_subscribers()
    await update.message.reply_text(f"Подписчиков: {len(subs)}")


async def broadcast(text: str, reply_markup=None, parse_mode: str | None = None):
    global _app
    if _app is None:
        return

    subs = await get_subscribers()
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
    """Запускается в отдельном потоке. Создаём свой event loop и живём в нём."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def _runner():
        global _app
        _app = Application.builder().token(BOT_TOKEN).build()

        _app.add_handler(CommandHandler("start", start_cmd))
        _app.add_handler(CommandHandler("stop", stop_cmd))
        _app.add_handler(CommandHandler("subs", subs_cmd))
        _app.add_handler(CommandHandler("tokens", tokens_cmd))
        _app.add_handler(CommandHandler("about", about_cmd))
        _app.add_handler(CommandHandler("channels", channels_cmd))
        _app.add_handler(CallbackQueryHandler(cb_handler))

        await _app.initialize()
        await _app.bot.delete_webhook(drop_pending_updates=True)
        await _app.start()
        await _app.updater.start_polling(allowed_updates=Update.ALL_TYPES)

        await asyncio.Event().wait()  # держим поток живым

    loop.run_until_complete(_runner())
