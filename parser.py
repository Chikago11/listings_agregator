import re

from ex_links import infer_market_type_from_url

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

# С‡С‚Рѕ СЃС‡РёС‚Р°РµРј "РєРѕС‚РёСЂРѕРІРєРѕР№" (quote)
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
    "/trade/",  # РјРЅРѕРіРёРµ spot/trade СЃС‚СЂР°РЅРёС†С‹
    "trade-spot",  # okx spot
    "exchange",  # mexc spot С‡Р°СЃС‚Рѕ /exchange/
}

FUT_URL_HINTS = {
    "/futures/",
    "/perpetual/",
    "trade-swap",  # okx swap
    "swap",
    "contract",
    "derivatives",
    "umcbl",  # bitget С„СЊСЋС‡-РёРґРµРЅС‚РёС„РёРєР°С‚РѕСЂ С‡Р°СЃС‚Рѕ РІ symbolId
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

    # Prefer exchange-aware heuristics based on known exchange URL patterns/templates.
    have_spot = False
    for u in urls:
        mt = infer_market_type_from_url(u)
        if mt == "futures":
            return "futures"
        if mt == "spot":
            have_spot = True
    if have_spot:
        return "spot"

    ujoin = " ".join(urls).lower()

    # РїСЂРёРѕСЂРёС‚РµС‚: РµСЃР»Рё СЏРІРЅРѕ С„СЊСЋС‡Рё вЂ” futures
    if any(h in ujoin for h in FUT_URL_HINTS):
        return "futures"

    # РёРЅР°С‡Рµ spot
    if any(h in ujoin for h in SPOT_URL_HINTS):
        return "spot"

    return None


def _display_symbol(base: str | None, quote: str | None) -> str | None:
    if not base:
        return None
    if quote and quote.upper() not in STABLE_QUOTES:
        # non-stable quote: РїРѕРєР°Р·С‹РІР°РµРј РїРѕР»РЅСѓСЋ РїР°СЂСѓ (BIRBKRW, ETHBTC)
        return f"{base.upper()}{quote.upper()}"
    # stable quote РёР»Рё quote РѕС‚СЃСѓС‚СЃС‚РІСѓРµС‚: РїРѕРєР°Р·С‹РІР°РµРј С‚РѕРєРµРЅ (VZON)
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
            if re.search(rf"\b{re.escape(ex)}\b", t, re.IGNORECASE):
                exchange = ex
                break
        # --- special: Aster (domain/keywords) ---
        if not exchange:
            if "asterdex.com" in t or "asterfutures" in t or re.search(r"\baster\b", t):
                exchange = "Aster"

    # market type
    market_type = None

    # РїСЂРёРѕСЂРёС‚РµС‚РЅС‹Рµ РјР°СЂРєРµСЂС‹ (F)/(S)
    if re.search(r"\(\s*f\s*\)", t) or re.search(r"\[\s*f\s*\]", t):
        market_type = "futures"
    elif re.search(r"\(\s*s\s*\)", t) or re.search(r"\[\s*s\s*\]", t):
        market_type = "spot"
    else:
        mt_url = _infer_market_type_from_urls(raw)
        if mt_url:
            market_type = mt_url
        elif any(w in t for w in FUTURES_WORDS):
            market_type = "futures"
        elif "spot" in t:
            market_type = "spot"
        elif re.search(r"\bpre(?:\s|[-\u2010-\u2015])*market\b", t):
            market_type = "futures"
        # --- fallback: market_type РїРѕ СЃСЃС‹Р»РєР°Рј (РµСЃР»Рё СЃР»РѕРІР°РјРё РЅРµ РЅР°С€Р»Рё) ---
    base = None
    quote = None
    # Binance Alpha С‡Р°С‰Рµ РІСЃРµРіРѕ РѕР·РЅР°С‡Р°РµС‚ СЃРїРѕС‚-Р»РёСЃС‚РёРЅРі
    if exchange == "Binance Alpha" and market_type is None:
        market_type = "spot"

    # 1) Futures "XPD" вЂ” С‚РѕРєРµРЅ РІ РєР°РІС‹С‡РєР°С…
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

    # 2.1) #XPD
    if not base:
        m = re.search(r"#([A-Z0-9]{2,20})\b", raw, re.IGNORECASE)
        if m:
            base = m.group(1).upper()

    # 3) BASE/QUOTE РёР»Рё BASE-QUOTE РёР»Рё BASE_QUOTE
    if not base:
        m = re.search(r"\b([A-Z0-9]{2,20})\s*[/\-_]\s*([A-Z]{2,6})\b", raw)
        if m:
            b = m.group(1).upper()
            q = m.group(2).upper()
            if q in QUOTES:
                base, quote = b, q
            else:
                # РµСЃР»Рё РІС‚РѕСЂР°СЏ С‡Р°СЃС‚СЊ РЅРµ РїРѕС…РѕР¶Р° РЅР° quote вЂ” СЃС‡РёС‚Р°РµРј СЌС‚Рѕ РїСЂРѕСЃС‚Рѕ base
                base = b

    # 4) РЎР»РёС‚РЅРѕ BASEQUOTE (BTCUSDT, BIRBKRW, ETHBTC)
    if not base:
        m = re.search(
            r"\b([A-Z0-9]{2,20})(USDT|USDC|USD|KRW|BTC|ETH|EUR|GBP|JPY)\b",
            raw,
            re.IGNORECASE,
        )
        if m:
            base = m.group(1).upper()
            quote = m.group(2).upper()

    # 5) РїСЂРѕСЃС‚Рѕ "XPD" РІ РєР°РІС‹С‡РєР°С… РєР°Рє Р·Р°РїР°СЃРЅРѕР№ РІР°СЂРёР°РЅС‚
    if not base:
        m = re.search(r'["“”]([A-Za-z0-9]{2,20})["“”]', raw)
        if m:
            base = m.group(1).upper()

    # 6) checklist line: "✅ XCU: Lighter (F)"
    if not base:
        m = re.search(r"^[^\S\r\n]*[✅✔☑]\s*([A-Za-z0-9/_-]{2,40})\s*:", raw, re.MULTILINE)
        if m:
            b, q, _ = _parse_symbol_from_checkline(m.group(1).strip())
            base, quote = b, q

    display = _display_symbol(base, quote)

    conf = "low"
    if exchange and market_type and base:
        conf = "high"
    elif (exchange and base) or (market_type and base):
        conf = "mid"

    return {
        "exchange": exchange,
        "market_type": market_type,
        "base": base,  # С‚РѕРєРµРЅ (РёР»Рё base РїР°СЂС‹)
        "quote": quote,  # РєРѕС‚РёСЂРѕРІРєР° (USDT/KRW/BTC/...)
        "display": display,  # РєР°Рє РїРѕРєР°Р·С‹РІР°С‚СЊ РїРѕР»СЊР·РѕРІР°С‚РµР»СЋ
        "confidence": conf,
    }


CHECKLINE_RE = re.compile(
    r"^[^\S\r\n]*(?:\u2705|\u2714|\u2611)\s*([A-Za-z0-9/_-]{2,40})\s*:\s*(.+)$",
    re.MULTILINE,
)

EX_IN_LIST_RE = re.compile(
    r"([A-Za-z][A-Za-z0-9 ]{1,30})\s*\(\s*([FS])\s*\)",
    re.IGNORECASE,
)


def _parse_symbol_from_checkline(sym_raw: str):
    sym_raw = (sym_raw or "").strip()

    # BASE/QUOTE РёР»Рё BASE-QUOTE РёР»Рё BASE_QUOTE
    m = re.search(
        r"\b([A-Z0-9]{2,20})\s*[/\-_]\s*([A-Z]{2,6})\b", sym_raw, re.IGNORECASE
    )
    if m:
        b = m.group(1).upper()
        q = m.group(2).upper()
        if q in QUOTES:
            return b, q, _display_symbol(b, q)
        return b, None, _display_symbol(b, None)

    # РЎР»РёС‚РЅРѕ BASEQUOTE (BTCUSDT, BIRBKRW, ETHBTC)
    m = re.search(
        r"\b([A-Z0-9]{2,20})(USDT|USDC|USD|KRW|BTC|ETH|EUR|GBP|JPY)\b",
        sym_raw,
        re.IGNORECASE,
    )
    if m:
        b = m.group(1).upper()
        q = m.group(2).upper()
        return b, q, _display_symbol(b, q)

    # РёРЅР°С‡Рµ Р±РµСЂС‘Рј С†РµР»РёРєРѕРј РєР°Рє base (СѓРЅРёРІРµСЂСЃР°Р»СЊРЅРѕ)
    b = re.sub(r"[^A-Za-z0-9]", "", sym_raw).upper()
    return (b if b else None), None, _display_symbol(b if b else None, None)


def extract_many(text: str) -> list[dict]:
    """
    Р’РѕР·РІСЂР°С‰Р°РµС‚ СЃРїРёСЃРѕРє С‚РѕРєРµРЅРѕРІ РІ РѕРґРЅРѕРј СЃРѕРѕР±С‰РµРЅРёРё metascalp-С„РѕСЂРјР°С‚Р°.
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

            # С‡РёСЃС‚РёРј С…РІРѕСЃС‚С‹ С‚РёРїР° "1пёЏвѓЈ"
            ex = re.sub(r"\s*\d+\ufe0f\u20e3$", "", ex).strip()

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
