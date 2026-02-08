import re

EXCHANGES = {
    "binance",
    "bybit",
    "okx",
    "gate",
    "mexc",
    "kucoin",
    "bitget",
    "bingx",
    "hyperliquid",
    "coinbase",
    "kraken",
    "upbit",
    "htx",
    "bitfinex",
    "bitmart",
    "Robinhood",
    "phemex",
    "Aster",
    "kcex",
    "Binance Alpha",
    "CryptoCom",
    "Lighter",
}

# что считаем "котировкой" (quote)
QUOTES = {
    "USDT",
    "USDC",
    "USD",
    "KRW",
    "BTC",
    "ETH",
    "EUR",
    "GBP",
    "JPY",
}

STABLE_QUOTES = {"USDT", "USDC", "USD"}

FUTURES_WORDS = {
    "futures",
    "future",
    "fut",
    "perp",
    "perps",
    "perpetual",
    "swap",
    "contract",
    "usdⓈ-m",
    "usds-m",
    "usd-m",
    "coin-m",
}

SPOT_URL_HINTS = {
    "/spot/",
    "/trade/",  # многие spot/trade страницы
    "trade-spot",  # okx spot
    "exchange",  # mexc spot часто /exchange/
}

FUT_URL_HINTS = {
    "/futures/",
    "/perpetual/",
    "trade-swap",  # okx swap
    "swap",
    "contract",
    "derivatives",
    "umcbl",  # bitget фьюч-идентификатор часто в symbolId
}


def normalize_text(text: str) -> str:
    if not text:
        return ""
    t = str(text).replace("\n", " ")
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _extract_urls(raw: str) -> list[str]:
    if not raw:
        return []
    return re.findall(r"https?://\S+", raw)


def _infer_market_type_from_urls(raw: str) -> str | None:
    urls = _extract_urls(raw)
    if not urls:
        return None

    ujoin = " ".join(urls).lower()

    # приоритет: если явно фьючи — futures
    if any(h in ujoin for h in FUT_URL_HINTS):
        return "futures"

    # иначе spot
    if any(h in ujoin for h in SPOT_URL_HINTS):
        return "spot"

    return None


def _display_symbol(base: str | None, quote: str | None) -> str | None:
    if not base:
        return None
    if quote and quote.upper() not in STABLE_QUOTES:
        # non-stable quote: показываем полную пару (BIRBKRW, ETHBTC)
        return f"{base.upper()}{quote.upper()}"
    # stable quote или quote отсутствует: показываем токен (VZON)
    return base.upper()


def extract(text: str) -> dict:
    raw = text or ""
    t = normalize_text(raw).lower()

    # --- special: Binance Alpha (must be before generic "binance") ---
    exchange = None
    if (
        re.search(r"\bbinance\s+alpha\b", t)
        or "binancewallet" in t
        or "twitter.com/binancewallet" in t
        or "x.com/binancewallet" in t
    ):
        exchange = "Binance Alpha"

    # exchange
    if not exchange:
        for ex in EXCHANGES:
            if re.search(rf"\b{re.escape(ex)}\b", t):
                exchange = ex
                break
        # --- special: Aster (domain/keywords) ---
        if not exchange:
            if "asterdex.com" in t or "asterfutures" in t or re.search(r"\baster\b", t):
                exchange = "Aster"

    # market type
    market_type = None

    # приоритетные маркеры (F)/(S)
    if re.search(r"\(\s*f\s*\)", t) or re.search(r"\[\s*f\s*\]", t):
        market_type = "futures"
    elif re.search(r"\(\s*s\s*\)", t) or re.search(r"\[\s*s\s*\]", t):
        market_type = "spot"
    else:
        if any(w in t for w in FUTURES_WORDS):
            market_type = "futures"
        elif "spot" in t:
            market_type = "spot"
        # --- fallback: market_type по ссылкам (если словами не нашли) ---
        if market_type is None:
            mt_url = _infer_market_type_from_urls(raw)
            if mt_url:
                market_type = mt_url

    base = None
    quote = None
    # Binance Alpha чаще всего означает спот-листинг
    if exchange == "Binance Alpha" and market_type is None:
        market_type = "spot"

    # 1) Futures "XPD" — токен в кавычках
    m = re.search(
        r'\b(?:futures|perps?)\b\s*["“”]([A-Za-z0-9]{2,20})["“”]', raw, re.IGNORECASE
    )
    if m:
        base = m.group(1).upper()

    # 2) $XPD
    if not base:
        m = re.search(r"\$([A-Z0-9]{2,20})\b", raw)
        if m:
            base = m.group(1).upper()

    # 3) BASE/QUOTE или BASE-QUOTE или BASE_QUOTE
    if not base:
        m = re.search(r"\b([A-Z0-9]{2,20})\s*[/\-_]\s*([A-Z]{2,6})\b", raw)
        if m:
            b = m.group(1).upper()
            q = m.group(2).upper()
            if q in QUOTES:
                base, quote = b, q
            else:
                # если вторая часть не похожа на quote — считаем это просто base
                base = b

    # 4) Слитно BASEQUOTE (BTCUSDT, BIRBKRW, ETHBTC)
    if not base:
        m = re.search(
            r"\b([A-Z0-9]{2,20})(USDT|USDC|USD|KRW|BTC|ETH|EUR|GBP|JPY)\b",
            raw,
            re.IGNORECASE,
        )
        if m:
            base = m.group(1).upper()
            quote = m.group(2).upper()

    # 5) просто "XPD" в кавычках как запасной вариант
    if not base:
        m = re.search(r'["“”]([A-Za-z0-9]{2,20})["“”]', raw)
        if m:
            base = m.group(1).upper()

    display = _display_symbol(base, quote)

    conf = "low"
    if exchange and market_type and base:
        conf = "high"
    elif (exchange and base) or (market_type and base):
        conf = "mid"

    return {
        "exchange": exchange,
        "market_type": market_type,
        "base": base,  # токен (или base пары)
        "quote": quote,  # котировка (USDT/KRW/BTC/...)
        "display": display,  # как показывать пользователю
        "confidence": conf,
    }


CHECKLINE_RE = re.compile(
    r"^[^\S\r\n]*✅\s*([A-Za-z0-9/_-]{2,40})\s*:\s*(.+)$",
    re.MULTILINE,
)

EX_IN_LIST_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9 ]{1,30})\s*\(\s*([FS])\s*\)",
    re.IGNORECASE,
)


def _parse_symbol_from_checkline(sym_raw: str):
    sym_raw = (sym_raw or "").strip()

    # BASE/QUOTE или BASE-QUOTE или BASE_QUOTE
    m = re.search(
        r"\b([A-Z0-9]{2,20})\s*[/\-_]\s*([A-Z]{2,6})\b", sym_raw, re.IGNORECASE
    )
    if m:
        b = m.group(1).upper()
        q = m.group(2).upper()
        if q in QUOTES:
            return b, q, _display_symbol(b, q)
        return b, None, _display_symbol(b, None)

    # Слитно BASEQUOTE (BTCUSDT, BIRBKRW, ETHBTC)
    m = re.search(
        r"\b([A-Z0-9]{2,20})(USDT|USDC|USD|KRW|BTC|ETH|EUR|GBP|JPY)\b",
        sym_raw,
        re.IGNORECASE,
    )
    if m:
        b = m.group(1).upper()
        q = m.group(2).upper()
        return b, q, _display_symbol(b, q)

    # иначе берём целиком как base (универсально)
    b = re.sub(r"[^A-Za-z0-9]", "", sym_raw).upper()
    return (b if b else None), None, _display_symbol(b if b else None, None)


def extract_many(text: str) -> list[dict]:
    """
    Возвращает список токенов в одном сообщении metascalp-формата.
    [
      {base, quote, display, futures_exchanges:[...], spot_exchanges:[...]},
      ...
    ]
    """
    raw = text or ""
    items = []

    for m in CHECKLINE_RE.finditer(raw):
        sym_raw = m.group(1).strip()
        rhs = m.group(2).strip()

        base, quote, display = _parse_symbol_from_checkline(sym_raw)
        if not base:
            continue

        ex_parts = EX_IN_LIST_RE.findall(rhs)
        if not ex_parts:
            continue

        fut, spot = [], []
        for ex_name, tag in ex_parts:
            ex = (ex_name or "").strip()
            tag = (tag or "").upper()

            # чистим хвосты типа "1️⃣"
            ex = re.sub(r"\s*\d+️⃣$", "", ex).strip()

            if tag == "F":
                fut.append(ex)
            elif tag == "S":
                spot.append(ex)

        def uniq(lst: list[str]) -> list[str]:
            seen = set()
            out = []
            for x in lst:
                k = x.strip().lower()
                if k and k not in seen:
                    seen.add(k)
                    out.append(x.strip())
            return out

        items.append(
            {
                "base": base,
                "quote": quote,
                "display": display,
                "futures_exchanges": uniq(fut),
                "spot_exchanges": uniq(spot),
            }
        )

    return items
