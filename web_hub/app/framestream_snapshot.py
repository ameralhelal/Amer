"""
لقطة مؤشرات مطابقة لتطبيق سطح المكتب: يعيد نفس dict الذي يبنيه FrameStream.compute_indicators.
"""
from __future__ import annotations

import math
from typing import Any


def _sanitize_json_obj(o: Any) -> Any:
    """يمنع NaN/Inf من كسر JSON ويحوّل القيم غير المعروفة لنص."""
    if o is None:
        return None
    if isinstance(o, bool):
        return o
    if isinstance(o, int) and not isinstance(o, bool):
        return int(o)
    if isinstance(o, float):
        return float(o) if math.isfinite(o) else None
    if isinstance(o, str):
        return o
    if isinstance(o, dict):
        return {str(k): _sanitize_json_obj(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitize_json_obj(x) for x in o]
    try:
        if hasattr(o, "item"):
            return _sanitize_json_obj(o.item())
    except Exception:
        pass
    return str(o)


def indicators_and_market_from_klines(
    symbol: str,
    interval: str,
    raw_klines: list,
) -> tuple[dict | None, dict | None]:
    """
    raw_klines: صفوف Binance GET /api/v3/klines.
    يُرجع (indicators, market_info) كما في websocket_manager.FrameStream.
    """
    candles: list[tuple] = []
    for row in raw_klines if isinstance(raw_klines, list) else []:
        try:
            candles.append(
                (
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                    float(row[5]),
                    int(row[0]),
                )
            )
        except (IndexError, TypeError, ValueError):
            continue
    if len(candles) < 20:
        return None, None

    from websocket_manager import FrameStream

    sym = str(symbol or "BTCUSDT").strip().upper().replace("/", "").lower()
    iv = str(interval or "15m").strip()
    fs = FrameStream(sym, iv)
    fs.candles = candles
    fs.last_price = float(candles[-1][3])

    captured_ind: dict = {}
    captured_info: dict = {}

    def on_ind(d: dict) -> None:
        captured_ind.clear()
        captured_ind.update(d)

    def on_info(d: dict) -> None:
        captured_info.clear()
        captured_info.update(d)

    fs.on_indicators = on_ind
    fs.on_market_info = on_info
    try:
        fs.compute_market_info()
        fs.compute_indicators()
    except Exception:
        return None, None

    if not captured_ind:
        return None, None

    return _sanitize_json_obj(captured_ind), _sanitize_json_obj(dict(captured_info))
