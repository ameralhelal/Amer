# etoro_symbols.py — تصفية أزواج Binance عند اختيار eToro (معظم قائمة Binance غير مدرجة على eToro)
from __future__ import annotations

import logging

from exchange_etoro import _symbol_to_etoro

log = logging.getLogger("trading.etoro_symbols")

# مفاتيح البحث على eToro (internalSymbolFull) لعملات يُتوقع تداولها كأصول أساسية.
# القائمة ليست شاملة — المفضلة في الإعدادات تُضاف دائماً.
_ETORO_CRYPTO_SEARCH_KEYS = frozenset(
    {
        "BTC",
        "ETH",
        "XRP",
        "LTC",
        "BCH",
        "XLM",
        "EOS",
        "ADA",
        "TRX",
        "BNB",
        "LINK",
        "UNI",
        "AAVE",
        "COMP",
        "MKR",
        "SNX",
        "YFI",
        "SUSHI",
        "CRV",
        "1INCH",
        "BAT",
        "ZEC",
        "DASH",
        "NEO",
        "IOTA",
        "QTUM",
        "OMG",
        "ENJ",
        "MANA",
        "SAND",
        "GALA",
        "AXS",
        "CHZ",
        "ALGO",
        "DOGE",
        "SHIB",
        "SOL",
        "MATIC",
        "POL",
        "AVAX",
        "ATOM",
        "NEAR",
        "FTM",
        "APT",
        "SUI",
        "SEI",
        "TIA",
        "ARB",
        "OP",
        "DOT",
        "ETC",
        "XTZ",
        "HBAR",
        "VET",
        "ICP",
        "FIL",
        "EGLD",
        "RUNE",
        "KAVA",
        "ROSE",
        "ONE",
        "STORJ",
        "ANKR",
        "GRT",
        "APE",
        "LDO",
        "RNDR",
        "IMX",
        "STX",
        "FLOW",
        "WAVES",
        "ZIL",
        "ONT",
        "SKL",
        "COTI",
        "CHR",
        "GOLD",  # PAXG / XAU عبر _symbol_to_etoro
    }
)

_MIN_LIST_SIZE = 18
_FALLBACK_MAJORS = [
    "BTCUSDT",
    "ETHUSDT",
    "XRPUSDT",
    "LTCUSDT",
    "BCHUSDT",
    "ADAUSDT",
    "SOLUSDT",
    "DOGEUSDT",
    "BNBUSDT",
    "LINKUSDT",
    "AVAXUSDT",
    "DOTUSDT",
    "ATOMUSDT",
    "NEARUSDT",
    "UNIUSDT",
    "XLMUSDT",
    "TRXUSDT",
    "ETCUSDT",
]


def _etoro_search_key_for_pair(symbol_usdt: str) -> str:
    return str(_symbol_to_etoro(symbol_usdt) or "").strip().upper()


def symbol_passes_etoro_allowlist(
    symbol_usdt: str,
    *,
    favorites: set[str] | None = None,
) -> bool:
    """
    هل الزوج يُعتبر ضمن قائمة eToro المعروفة لدى التطبيق أو في المفضلة.
    تقريبي (ليست مزامنة حية مع كتالوج eToro) — يُستخدم للتنبيه وتفادي شراء البوت العبثي.
    """
    su = str(symbol_usdt or "").strip().upper()
    if not su.endswith("USDT"):
        return True
    fav = {str(s or "").strip().upper() for s in (favorites or set()) if str(s or "").strip()}
    if su in fav:
        return True
    key = _etoro_search_key_for_pair(su)
    if not key:
        return False
    return key in _ETORO_CRYPTO_SEARCH_KEYS


def filter_symbols_for_etoro_trading(
    symbols: list[str],
    *,
    favorites: set[str] | None = None,
    extra_keep: set[str] | None = None,
) -> list[str]:
    """يحافظ على المفضلة + last_symbol + أزواج يطابق مفتاحها قائمة eToro المتوقعة."""
    fav = {str(s or "").strip().upper() for s in (favorites or set()) if str(s or "").strip()}
    extra = {str(s or "").strip().upper() for s in (extra_keep or set()) if str(s or "").strip()}
    out: list[str] = []
    seen: set[str] = set()

    def _add(su: str) -> None:
        if not su.endswith("USDT") or su in seen:
            return
        seen.add(su)
        out.append(su)

    for sym in symbols or []:
        su = str(sym or "").strip().upper()
        if not su.endswith("USDT"):
            continue
        if su in fav or su in extra:
            _add(su)
            continue
        key = _etoro_search_key_for_pair(su)
        if key in _ETORO_CRYPTO_SEARCH_KEYS:
            _add(su)

    for fb in _FALLBACK_MAJORS:
        if len(out) >= _MIN_LIST_SIZE:
            break
        _add(fb)

    if symbols and len(out) < len(symbols) * 0.05:
        log.warning(
            "eToro symbol filter produced very few pairs (%s) — check allowlist / favorites",
            len(out),
        )
    return out
