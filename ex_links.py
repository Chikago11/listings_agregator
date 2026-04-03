from __future__ import annotations

import re
from urllib.parse import urlparse

# РќРѕСЂРјР°Р»РёР·СѓРµРј РЅР°Р·РІР°РЅРёСЏ Р±РёСЂР¶ (С‡С‚РѕР±С‹ "рџ”µMEXC", "MEXC", "mexc" -> "mexc")
def norm_ex(ex: str) -> str:
    ex = (ex or "").strip()
    ex = re.sub(r"^[^\w]+", "", ex)  # СѓР±РёСЂР°РµРј СЌРјРѕРґР·Рё/СЃРёРјРІРѕР»С‹ РІ РЅР°С‡Р°Р»Рµ
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
    # okx spot РѕР±С‹С‡РЅРѕ lower: eth-usdt
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
    if ex == "binance alpha":
        return "https://www.binance.com/ru/alpha"

    if ex == "binance":
        if mt == "futures":
            # https://www.binance.com/ru/futures/UAIUSDT
            return f"https://www.binance.com/ru/futures/{sym_usdt_concat(base, quote)}"
        if mt == "spot":
            # РїСЂРёРјРµСЂ Сѓ С‚РµР±СЏ: /trade/USDC_USDT?type=spot
            return f"https://www.binance.com/ru/trade/{sym_usdt_underscore(base, quote)}?type=spot"

    # -------- KCEX --------
    if ex == "kcex":
        if mt == "spot":
            # https://www.kcex.com/ru-RU/exchange/BTC_USDT
            return f"https://www.kcex.com/ru-RU/exchange/{sym_usdt_underscore(base, quote)}"
        if mt == "futures":
            # РЈ KCEX С„СЊСЋС‡Рё РЅРµ РїСЂСЏРјРѕР№ СЃРёРјРІРѕР»СЊРЅС‹Р№ URL РєР°Рє Сѓ mexc/gate, С‡Р°С‰Рµ С‡РµСЂРµР· СЃС‚СЂР°РЅРёС†Сѓ exchange.
            # Р”РµР»Р°РµРј вЂњbest effortвЂќ: search С‡РµСЂРµР· РїР°СЂР°РјРµС‚СЂ symbol, РµСЃР»Рё СЃСЂР°Р±РѕС‚Р°РµС‚.
            # Р•СЃР»Рё РЅРµС‚ вЂ” С…РѕС‚СЏ Р±С‹ РѕС‚РєСЂРѕРµС‚СЃСЏ С„СЊСЋС‡-СЂР°Р·РґРµР».
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
            # РЈ KuCoin С„СЊСЋС‡Рё С‡Р°СЃС‚Рѕ РёРјРµСЋС‚ СЃСѓС„С„РёРєСЃ M / MM (XBTUSDTM / XBTUSDTMM).
            # Р”Р»СЏ СѓРЅРёРІРµСЂСЃР°Р»СЊРЅРѕСЃС‚Рё РІРµРґС‘Рј РЅР° futures РїРѕРёСЃРє РїРѕ С‚РёРєРµСЂСѓ (С‡Р°СЃС‚Рѕ РѕС‚РєСЂС‹РІР°РµС‚СЃСЏ РЅСѓР¶РЅР°СЏ РєР°СЂС‚РѕС‡РєР°).
            # Р•СЃР»Рё Сѓ С‚РµР±СЏ РїРѕСЏРІРёС‚СЃСЏ С‚РѕС‡РЅС‹Р№ С„РѕСЂРјР°С‚ РґР»СЏ РІСЃРµС… вЂ” Р·Р°РјРµРЅРёРј.
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
            # РЈ WEEX С„СЊСЋС‡Рё С‡Р°СЃС‚Рѕ РїРѕ id (РЅРµ РїРѕ СЃРёРјРІРѕР»Сѓ). РЈРЅРёРІРµСЂСЃР°Р»СЊРЅРѕРіРѕ СЃРёРјРІРѕР»СЊРЅРѕРіРѕ URL РЅРµС‚.
            # Р”РµР»Р°РµРј РїРѕР»РµР·РЅС‹Р№ fallback: С„СЊСЋС‡-СЂР°Р·РґРµР» + РїРѕРёСЃРє РїРѕ СЃРёРјРІРѕР»Сѓ РІ query.
            return f"https://www.weex.com/futures/{sym_dash(base, quote)}"

    # -------- LBank --------
    if ex == "lbank":
        if mt == "spot":
            # https://www.lbank.com/trade/eth_usdt
            return f"https://www.lbank.com/trade/{base.lower()}_{quote.lower()}"
        if mt == "futures":
            # https://www.lbank.com/futures/ethusdt
            return f"https://www.lbank.com/futures/{base.lower()}{quote.lower()}"

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
            # РЈ Bybit spot С„РѕСЂРјР°С‚ С‡Р°С‰Рµ /trade/spot/BTC/USDC, РЅРѕ РґР»СЏ СѓРЅРёРІРµСЂСЃР°Р»СЊРЅРѕСЃС‚Рё Р»СѓС‡С€Рµ РІРµСЃС‚Рё РЅР° РїРѕРёСЃРє.
            return f"https://www.bybit.com/en/trade/spot/{base}/{quote}"
        if mt == "futures":
            # https://www.bybit.com/trade/usdt/RIVERUSDT
            return f"https://www.bybit.com/trade/usdt/{sym_usdt_concat(base, quote)}"

    # -------- CoinEx --------
    if ex == "coinex":
        if mt == "spot":
            # РЈ CoinEx spot С„РѕСЂРјР°С‚ Р±С‹РІР°РµС‚ С‡РµСЂРµР· exchange#spot, Р° РєРѕРЅРєСЂРµС‚РЅР°СЏ РїР°СЂР° С‡Р°СЃС‚Рѕ РїР°СЂР°РјРµС‚СЂР°РјРё/СЂРѕСѓС‚РѕРј.
            # Р”Р°РµРј СѓРЅРёРІРµСЂСЃР°Р»СЊРЅС‹Р№ вЂњРїРѕРёСЃРєРѕРІС‹Р№вЂќ РІРёРґ С‡РµСЂРµР· market=... (СЂР°Р±РѕС‚Р°РµС‚ РЅР° futures, spot РјРѕР¶РµС‚ СЂРµРґРёСЂРµРєС‚РёС‚СЊ).
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
            # РЈ Bitmart derivatives С‡Р°СЃС‚Рѕ РЅРµ РїСЂСЏРјРѕР№ С‚РёРєРµСЂ РІ url. Р”Р°РґРёРј РїРѕР»РµР·РЅС‹Р№ fallback РЅР° С„СЊСЋС‡-СЂР°Р·РґРµР».
            return "https://derivatives.bitmart.com/ru-RU/futures"

    # -------- Aster --------
    if ex == "aster":
        if mt == "futures":
            # https://www.asterdex.com/en/trade/pro/futures/OPNUSDT
            return f"https://www.asterdex.com/en/trade/pro/futures/{sym_usdt_concat(base, quote)}"
        return "https://www.asterdex.com/en/trade/pro/futures"
    # --- PERP / DEX list fallback (РµСЃР»Рё РІ CSV РїРѕРїР°РґС‘С‚ С‚Р°РєРѕР№ exchange) ---
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


def infer_market_type_from_url(url: str) -> str | None:
    """
    Best-effort inference of market type from an exchange URL.

    Returns: 'spot' | 'futures' | None
    """
    if not url:
        return None

    try:
        p = urlparse(url.strip())
    except Exception:
        return None

    host = (p.netloc or "").lower()
    path = (p.path or "").lower()
    query = (p.query or "").lower()

    # Ourbit: separate futures subdomain
    if host.startswith("futures.ourbit.com"):
        return "futures"
    # Some exchanges also use dedicated futures subdomains, e.g. futures.mexc.com/exchange/...
    if host.startswith("futures."):
        return "futures"

    # Exchange-specific patterns
    if "binance.com" in host:
        if "/futures/" in path:
            return "futures"
        if "/trade/" in path and ("type=spot" in query or "type=spot" in url.lower()):
            return "spot"
        if "/trade/" in path:
            return "spot"

    if "okx.com" in host:
        if "/trade-swap/" in path:
            return "futures"
        if "/trade-spot/" in path:
            return "spot"

    if "mexc.com" in host:
        if "/futures/" in path:
            return "futures"
        if "/exchange/" in path:
            return "spot"

    if "gate.com" in host:
        if "/futures/" in path:
            return "futures"
        if "/trade/" in path:
            return "spot"

    if "bitget.com" in host:
        if "/futures/" in path:
            return "futures"
        if "/spot/" in path:
            return "spot"

    if "bybit.com" in host:
        if "/trade/usdt/" in path:
            return "futures"
        if "/trade/spot/" in path:
            return "spot"

    if "xt.com" in host:
        if "/futures/" in path:
            return "futures"
        if "/trade/" in path:
            return "spot"

    if "coinex.com" in host:
        if "/futures/" in path:
            return "futures"
        if "/exchange/" in path:
            return "spot"

    if "bitmart.com" in host:
        if "type=spot" in query:
            return "spot"
    if "derivatives.bitmart.com" in host:
        return "futures"

    if "kcex.com" in host:
        if "/futures/" in path:
            return "futures"
        if "/exchange/" in path:
            return "spot"

    if "kucoin.com" in host:
        if "/trade/futures/" in path:
            return "futures"
        if "/trade/" in path:
            return "spot"

    if "lbank.com" in host:
        if "/futures/" in path:
            return "futures"
        if "/trade/" in path:
            return "spot"

    # Generic fallback (less precise)
    if any(x in path for x in ("/futures/", "/perpetual/", "/contract/", "/derivatives/")):
        return "futures"
    if any(x in path for x in ("/spot/", "/exchange/")):
        return "spot"

    return None


