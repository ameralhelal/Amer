# binance_symbols.py — قائمة موحّدة بجميع أزواج USDT النشطة (سبوت) من Binance
import logging
from typing import List

import requests

from binance_chart_aliases import SEARCH_SYNONYMS_USDT

log = logging.getLogger("trading.binance_symbols")

# عدة نقاط نهاية — إن حُظِر api.binance.com إقليمياً قد ينجح api1/api2/api3
BINANCE_EXCHANGE_INFO_URLS = (
    "https://api.binance.com/api/v3/exchangeInfo",
    "https://api1.binance.com/api/v3/exchangeInfo",
    "https://api2.binance.com/api/v3/exchangeInfo",
    "https://api3.binance.com/api/v3/exchangeInfo",
)


def load_all_usdt_spot_trading_symbols(timeout: float = 20.0) -> List[str]:
    """
    كل الرموز ذات الاقتباس USDT وحالة TRADING من exchangeInfo العام (سبوت Binance).
    يُستخدم للشارت/القائمة؛ يتوافق مع WebSocket كلاين Binance @kline_*
    """
    last_err: Exception | None = None
    for url in BINANCE_EXCHANGE_INFO_URLS:
        try:
            r = requests.get(url, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            symbols: list[str] = []
            for item in data.get("symbols") or []:
                if not isinstance(item, dict):
                    continue
                if (item.get("quoteAsset") or "").upper() != "USDT":
                    continue
                if (item.get("status") or "").upper() != "TRADING":
                    continue
                sym = item.get("symbol")
                if isinstance(sym, str) and sym:
                    symbols.append(sym.upper())

            symbols = sorted(set(symbols) | set(SEARCH_SYNONYMS_USDT))
            if symbols:
                log.info("Loaded %d USDT spot TRADING symbols from %s", len(symbols), url.split("/")[2])
                return symbols
        except Exception as e:
            last_err = e
            log.debug("Binance exchangeInfo failed (%s): %s", url, e)
            continue

    log.warning("All Binance exchangeInfo endpoints failed: %s", last_err)
    return []
