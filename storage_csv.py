import csv
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

TOKENS_CSV_PATH = os.getenv("TOKENS_CSV_PATH", "tokens.csv")
CSV_FIELDS = ["token", "created_at", "futures", "spot"]


def _now_iso() -> str:
    # локальное время, но в ISO — нам главное консистентность
    return datetime.now().replace(microsecond=0).isoformat()


def read_all_rows() -> List[Dict[str, str]]:
    """Читает tokens.csv в список dict. Если файла нет — возвращает []."""
    if not os.path.exists(TOKENS_CSV_PATH):
        return []

    rows: List[Dict[str, str]] = []
    with open(TOKENS_CSV_PATH, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if not r:
                continue
            # нормализуем ключи
            row = {k.strip(): (v.strip() if isinstance(v, str) else "") for k, v in r.items()}
            if row.get("token"):
                rows.append(row)
    return rows


def write_all_rows(rows: List[Dict[str, str]]) -> None:
    """Полностью перезаписывает tokens.csv."""
    with open(TOKENS_CSV_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for r in rows:
            out = {k: (r.get(k, "") or "") for k in CSV_FIELDS}
            writer.writerow(out)


def _parse_dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.min


def get_tokens_sorted() -> List[str]:
    """Токены от новых к старым по created_at."""
    rows = read_all_rows()
    rows.sort(key=lambda r: _parse_dt(r.get("created_at", "")), reverse=True)
    return [r["token"] for r in rows if r.get("token")]


def get_token_row(token: str) -> Optional[Dict[str, str]]:
    token = (token or "").strip().upper()
    if not token:
        return None
    for r in read_all_rows():
        if (r.get("token") or "").strip().upper() == token:
            return r
    return None


def _merge_exchange_list(current: str, new_exchange: str) -> str:
    """
    current: 'Gate/Bybit'
    new_exchange: 'MEXC'
    -> 'Gate/Bybit/MEXC' (без дублей, порядок сохраняем)
    """
    new_exchange = (new_exchange or "").strip()
    if not new_exchange:
        return current or ""

    cur = (current or "").strip()
    if not cur or cur == "—":
        return new_exchange

    parts = [p.strip() for p in cur.split("/") if p.strip()]
    if new_exchange not in parts:
        parts.append(new_exchange)
    return "/".join(parts)


def upsert_listing(token: str, market_type: str, exchange: str) -> None:
    """
    Добавляет биржу в futures/spot для токена.
    created_at ставим только если токена ещё не было (первое появление).
    """
    token = (token or "").strip().upper()
    market_type = (market_type or "").strip().lower()
    exchange = (exchange or "").strip()

    if not token or not market_type or not exchange:
        return

    rows = read_all_rows()

    # ищем существующую строку
    idx = None
    for i, r in enumerate(rows):
        if (r.get("token") or "").strip().upper() == token:
            idx = i
            break

    if idx is None:
        # новая строка
        row = {"token": token, "created_at": _now_iso(), "futures": "", "spot": ""}
        rows.append(row)
        idx = len(rows) - 1

    # гарантируем created_at
    if not rows[idx].get("created_at"):
        rows[idx]["created_at"] = _now_iso()

    if market_type in ("futures", "perp", "perps"):
        rows[idx]["futures"] = _merge_exchange_list(rows[idx].get("futures", ""), exchange)
    elif market_type in ("spot",):
        rows[idx]["spot"] = _merge_exchange_list(rows[idx].get("spot", ""), exchange)
    else:
        # unknown — не пишем
        return

    write_all_rows(rows)


def purge_old_tokens(days: int = 7) -> int:
    """Удаляет токены, чей created_at старше N дней. Возвращает число удалённых."""
    rows = read_all_rows()
    if not rows:
        return 0

    cutoff = datetime.now() - timedelta(days=days)
    kept = []
    removed = 0

    for r in rows:
        dt = _parse_dt(r.get("created_at", ""))
        if dt >= cutoff:
            kept.append(r)
        else:
            removed += 1

    if removed:
        write_all_rows(kept)
    return removed
