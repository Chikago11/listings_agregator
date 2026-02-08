from __future__ import annotations

import re
from urllib.parse import quote as url_quote

# Нормализуем названия бирж (чтобы "🔵MEXC", "MEXC", "mexc" -> "mexc")
def norm_ex(ex: str) -> str:
    ex = (ex or "").strip()
    ex = re.sub(r"^[^\w]+", "", ex)  # убираем эмодзи/символы в начале
    ex = ex.strip().lower()
    ex = ex.replace("kucoin", "kucoin")
    ex = ex.replace("bitmart", "bitmart")
    ex = ex.replace("coinex", "coinex")
    ex = ex.replace("kcex", "kcex")
    ex = ex.replace("ourbit", "ourbit")
    return ex


def sym_usdt_underscore(base: str, quote: str) -> str:
    return f"{base}_{quote}".upper()

def sym_usdt_concat(base: str, quote: str) -> str:
    return f"{base}{quote}".upper()

def sym_dash(base: str, quote: str) -> str:
    return f"{base}-{quote}".upper()

def sym_dash_lower(base: str, quote: str) -> str:
    return f"{base}-{quote}".lower()

def sym_okx_spot(base: str, quote: str) -> str:
    # okx spot обычно lower: eth-usdt
    return f"{base}-{quote}".lower()

def sym_okx_swap(base: str, quote: str) -> str:
    # okx swap: zkp-usdt-swap (lower)
    return f"{base}-{quote}-swap".lower()


# --- CEX templates: returns url or None ---
def build_exchange_link(exchange: str, market_type: str, base: str, quote: str = "USDT") -> str | None:
    """
    market_type: 'spot' or 'futures'
    base/quote: e.g. PAXG/USDT
    """
    ex = norm_ex(exchange)
    mt = (market_type or "").strip().lower()
    base = (base or "").strip().upper()
    quote = (quote or "").strip().upper()

    if not ex or not base:
        return None

    # -------- MEXC --------
    if ex == "mexc":
        if mt == "futures":
            # https://www.mexc.com/ru-RU/futures/WHITEWHALE_USDT
            return f"https://www.mexc.com/ru-RU/futures/{sym_usdt_underscore(base, quote)}"
        if mt == "spot":
            # https://www.mexc.com/ru-RU/exchange/BREV_USDT?_from=header
            return f"https://www.mexc.com/ru-RU/exchange/{sym_usdt_underscore(base, quote)}?_from=header"

    # -------- Gate --------
    if ex == "gate":
        if mt == "futures":
            # https://www.gate.com/ru/futures/USDT/ETH_USDT
            return f"https://www.gate.com/ru/futures/{quote}/{sym_usdt_underscore(base, quote)}"
        if mt == "spot":
            # https://www.gate.com/ru/trade/BTC_USDT
            return f"https://www.gate.com/ru/trade/{sym_usdt_underscore(base, quote)}"

    # -------- Binance --------
    if ex == "binance":
        if mt == "futures":
            # https://www.binance.com/ru/futures/UAIUSDT
            return f"https://www.binance.com/ru/futures/{sym_usdt_concat(base, quote)}"
        if mt == "spot":
            # пример у тебя: /trade/USDC_USDT?type=spot
            return f"https://www.binance.com/ru/trade/{sym_usdt_underscore(base, quote)}?type=spot"

    # -------- KCEX --------
    if ex == "kcex":
        if mt == "spot":
            # https://www.kcex.com/ru-RU/exchange/BTC_USDT
            return f"https://www.kcex.com/ru-RU/exchange/{sym_usdt_underscore(base, quote)}"
        if mt == "futures":
            # У KCEX фьючи не прямой символьный URL как у mexc/gate, чаще через страницу exchange.
            # Делаем “best effort”: search через параметр symbol, если сработает.
            # Если нет — хотя бы откроется фьюч-раздел.
            return f"https://www.kcex.com/ru-RU/futures/exchange?type=linear_swap&symbol={sym_usdt_underscore(base, quote)}"

    # -------- Bitget --------
    if ex == "bitget":
        if mt == "spot":
            # https://www.bitget.com/ru/spot/BYTEUSDT
            return f"https://www.bitget.com/ru/spot/{sym_usdt_concat(base, quote)}"
        if mt == "futures":
            # https://www.bitget.com/ru/futures/usdt/BTCUSDT
            return f"https://www.bitget.com/ru/futures/usdt/{sym_usdt_concat(base, quote)}"

    # -------- KuCoin --------
    if ex == "kucoin":
        if mt == "spot":
            # https://www.kucoin.com/ru/trade/BYTE-USDT
            return f"https://www.kucoin.com/ru/trade/{sym_dash(base, quote)}"
        if mt == "futures":
            # У KuCoin фьючи часто имеют суффикс M / MM (XBTUSDTM / XBTUSDTMM).
            # Для универсальности ведём на futures поиск по тикеру (часто открывается нужная карточка).
            # Если у тебя появится точный формат для всех — заменим.
            return f"https://www.kucoin.com/ru/trade/futures/{sym_usdt_concat(base, quote)}M"

    # -------- BingX --------
    if ex == "bingx":
        if mt == "spot":
            # https://bingx.com/ru-ru/spot/ETHUSDT
            return f"https://bingx.com/ru-ru/spot/{sym_usdt_concat(base, quote)}"
        if mt == "futures":
            # https://bingx.com/ru-ru/perpetual/ZTC-USDT
            return f"https://bingx.com/ru-ru/perpetual/{sym_dash(base, quote)}"

    # -------- WEEX --------
    if ex == "weex":
        if mt == "spot":
            # https://www.weex.com/spot/BTC-USDT
            return f"https://www.weex.com/spot/{sym_dash(base, quote)}"
        if mt == "futures":
            # У WEEX фьючи часто по id (не по символу). Универсального символьного URL нет.
            # Делаем полезный fallback: фьюч-раздел + поиск по символу в query.
            return f"https://www.weex.com/futures?symbol={url_quote(sym_dash(base, quote))}"

    # -------- OKX --------
    if ex == "okx":
        if mt == "spot":
            # https://www.okx.com/ru/trade-spot/eth-usdt
            return f"https://www.okx.com/ru/trade-spot/{sym_okx_spot(base, quote)}"
        if mt == "futures":
            # swap: https://www.okx.com/ru/trade-swap/zkp-usdt-swap
            return f"https://www.okx.com/ru/trade-swap/{sym_okx_swap(base, quote)}"

    # -------- Bybit --------
    if ex == "bybit":
        if mt == "spot":
            # У Bybit spot формат чаще /trade/spot/BTC/USDC, но для универсальности лучше вести на поиск.
            return f"https://www.bybit.com/en/trade/spot/{base}/{quote}"
        if mt == "futures":
            # https://www.bybit.com/trade/usdt/RIVERUSDT
            return f"https://www.bybit.com/trade/usdt/{sym_usdt_concat(base, quote)}"

    # -------- CoinEx --------
    if ex == "coinex":
        if mt == "spot":
            # У CoinEx spot формат бывает через exchange#spot, а конкретная пара часто параметрами/роутом.
            # Даем универсальный “поисковый” вид через market=... (работает на futures, spot может редиректить).
            return f"https://www.coinex.com/en/exchange/{sym_usdt_underscore(base, quote)}"
        if mt == "futures":
            # https://www.coinex.com/en/futures/pippin-usdt  (lower + dash)
            return f"https://www.coinex.com/en/futures/{sym_dash_lower(base, quote)}"

    # -------- Ourbit --------
    if ex == "ourbit":
        if mt == "spot":
            return f"https://www.ourbit.com/ru-RU/exchange/{sym_usdt_underscore(base, quote)}"
        if mt == "futures":
            return f"https://futures.ourbit.com/ru-RU/exchange/{sym_usdt_underscore(base, quote)}"

    # -------- XT --------
    if ex == "xt":
        if mt == "spot":
            # https://www.xt.com/ru/trade/btc_usdt
            return f"https://www.xt.com/ru/trade/{base.lower()}_{quote.lower()}"
        if mt == "futures":
            # https://www.xt.com/ru/futures/trade/btc_usdt
            return f"https://www.xt.com/ru/futures/trade/{base.lower()}_{quote.lower()}"

    # -------- Bitmart --------
    if ex == "bitmart":
        if mt == "spot":
            # https://www.bitmart.com/ru-RU/trade/BTC_USDT?type=spot
            return f"https://www.bitmart.com/ru-RU/trade/{sym_usdt_underscore(base, quote)}?type=spot"
        if mt == "futures":
            # У Bitmart derivatives часто не прямой тикер в url. Дадим полезный fallback на фьюч-раздел.
            return "https://derivatives.bitmart.com/ru-RU/futures"

    # --- PERP / DEX list fallback (если в CSV попадёт такой exchange) ---
    PERP_EXCHANGES = {
        "edgex": "https://www.edgex.exchange/",
        "elfi": "https://elfi.xyz/",
        "variational": "https://variational.io/",
        "privex": "https://prvx.io/",
        "lighter": "https://lighter.xyz/",
        "quanto": "https://quanto.trade/",
        "hyperliquid": "https://app.hyperliquid.xyz/",
        "satori": "https://satori.finance/",
        "aster": "https://www.asterdex.com/",
        "reya": "https://reya.xyz/",
        "polynomial": "https://www.polynomial.finance/",
        "extended": "https://app.extended.exchange/",
        "backpack": "https://backpack.exchange/trade/",
        "paradex": "https://app.paradex.trade/trade/",
        "ostium": "https://ostium.com/",
        "storm": "https://storm.tg/",
        "merkle": "https://app.merkle.trade/",
    }

    if ex in PERP_EXCHANGES:
        return PERP_EXCHANGES[ex]

    return None
