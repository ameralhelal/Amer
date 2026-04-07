# binance_chart_aliases.py — مرادفات للشموع/الويب سوكت Binance (لا يوجد XAUUSDT سبوت؛ الذهب ≈ Pax Gold)
from __future__ import annotations

# مفتاح: ما يكتبه المستخدم — قيمة: زوج Binance سبوت الفعلي
_BINANCE_SPOT_CANONICAL: dict[str, str] = {
    "XAUUSDT": "PAXGUSDT",
    "GOLDUSDT": "PAXGUSDT",
    "XAUUSD": "PAXGUSDT",
}

# أخطاء إملائية شائعة في واجهة المستخدم → زوج Binance سبوت صالح
_BINANCE_SYMBOL_TYPOS: dict[str, str] = {
    "AMPPUSDT": "AMPUSDT",
}

# تُضاف للقائمة/البحث حتى يجد المستخدم «ذهب» ثم يُغذّى الشارت من PAXGUSDT
SEARCH_SYNONYMS_USDT: tuple[str, ...] = ("XAUUSDT", "GOLDUSDT")


def binance_spot_pair_symbol(symbol: str) -> str:
    """رمز زوج Binance سبوت للتنفيذ/API (حروف كبيرة)."""
    u = (symbol or "").strip().upper().replace(" ", "")
    u = _BINANCE_SYMBOL_TYPOS.get(u, u)
    return _BINANCE_SPOT_CANONICAL.get(u, u)


def binance_kline_stream_symbol(symbol: str) -> str:
    """رمز تدفق الشموع (صغير) لـ WebSocket وطلبات klines."""
    return binance_spot_pair_symbol(symbol).lower()
