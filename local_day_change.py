# -*- coding: utf-8 -*-
"""سعر مرجعي لبداية «اليوم» بتوقيت الجهاز المحلي، عبر شموع Binance 1m.

القاعدة: منتصف الليل المحلي = بداية اليوم التقويمي المحلي (datetime المحلي).
نأخذ **سعر فتح (open)** أول شمعة 1m على Binance يبدأ وقتها (open time) عند أو بعد هذا
الحد — أي أقرب لقطة تداول رسمية بعد 00:00:00 محلياً.
"""
from __future__ import annotations

import threading
from datetime import date, datetime

import requests

from binance_chart_aliases import binance_spot_pair_symbol

_session = requests.Session()
_lock = threading.Lock()
_cache: dict[str, tuple[str, float]] = {}  # SYMBOL -> (local_date_iso, open_price)


def local_today_iso() -> str:
    return date.today().isoformat()


def local_midnight_start_ms() -> int:
    t = date.today()
    dt = datetime(t.year, t.month, t.day, 0, 0, 0)
    return int(dt.timestamp() * 1000)


def invalidate_symbol(symbol: str) -> None:
    sym = (symbol or "").strip().upper()
    if not sym:
        return
    with _lock:
        _cache.pop(sym, None)


def get_open_at_local_midnight(symbol_usdt: str) -> float | None:
    sym = (symbol_usdt or "").strip().upper()
    if not sym or sym.startswith("ETORO_"):
        return None
    today = local_today_iso()
    with _lock:
        hit = _cache.get(sym)
        if hit and hit[0] == today:
            return hit[1]
    api_sym = binance_spot_pair_symbol(sym)
    start_ms = local_midnight_start_ms()
    # نبحث في نافذة قبل منتصف الليل لتغطية أزواج ضعيفة السيولة
    for start_time, limit in (
        (start_ms - 2 * 3600 * 1000, 500),
        (start_ms - 26 * 3600 * 1000, 1000),
    ):
        try:
            r = _session.get(
                "https://api.binance.com/api/v3/klines",
                params={
                    "symbol": api_sym,
                    "interval": "1m",
                    "startTime": start_time,
                    "limit": limit,
                },
                timeout=10,
            )
            if r.status_code != 200:
                continue
            rows = r.json()
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not row or len(row) < 2:
                    continue
                t0 = int(row[0])
                if t0 >= start_ms:
                    o = float(row[1])
                    if o > 0:
                        with _lock:
                            _cache[sym] = (today, o)
                        return o
        except (TypeError, ValueError, OSError, requests.RequestException):
            continue
    return None
