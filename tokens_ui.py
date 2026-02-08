from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from storage_csv import get_tokens_sorted, get_token_row
from ex_links import build_exchange_link


PAGE_SIZE = 6


def split_exchanges(s: str) -> list[str]:
    s = (s or "").strip()
    if not s or s in ("—", "-"):
        return []
    return [p.strip() for p in s.split("/") if p.strip()]


def tokens_keyboard(page: int = 0):
    tokens = get_tokens_sorted()

    page = max(0, page)
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    chunk = tokens[start:end]

    rows = [[InlineKeyboardButton(t, callback_data=f"tok:{t}")] for t in chunk]

    nav = []
    if start > 0:
        nav.append(InlineKeyboardButton("⬅️", callback_data=f"tokpage:{page-1}"))
    if end < len(tokens):
        nav.append(InlineKeyboardButton("➡️", callback_data=f"tokpage:{page+1}"))
    if nav:
        rows.append(nav)

    return InlineKeyboardMarkup(rows)


def token_card_text(token: str) -> str:
    row = get_token_row(token) or {}

    # ✅ futures/spot в CSV — это строки вида "Gate/Bybit/Binance"
    fut = split_exchanges(row.get("futures", ""))
    spot = split_exchanges(row.get("spot", ""))

    # пока по умолчанию USDT (после улучшения парсера будем подставлять реальный quote)
    quote_default = "USDT"

    def render(exchange_list: list[str], market_type: str) -> str:
        if not exchange_list:
            return "—"

        parts = []
        for ex in exchange_list:
            ex_name = (ex or "").strip()
            if not ex_name:
                continue

            url = build_exchange_link(ex_name, market_type, base=token, quote=quote_default)

            # если не смогли построить ссылку — оставим просто текст
            if url:
                parts.append(f'<a href="{url}">{ex_name}</a>')
            else:
                parts.append(ex_name)

        return " / ".join(parts) if parts else "—"

    return (
        f"🪙 <b>{token}</b>\n\n"
        f"📈 <b>Futures</b>: {render(fut, 'futures')}\n"
        f"💱 <b>Spot</b>: {render(spot, 'spot')}"
    )
