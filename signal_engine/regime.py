# تصنيف بسيط لنظام السوق — يحدد أي مسار قواعد يُشغَّل أولاً ويقلّل التضارب
from __future__ import annotations

from enum import Enum

from signal_engine.context import PanelContext


class MarketRegime(str, Enum):
    TREND_UP = "trend_up"
    TREND_DOWN = "trend_down"
    RANGE = "range"
    NEUTRAL = "neutral"


def detect_regime(ctx: PanelContext) -> MarketRegime:
    """يُستدعى بعد معالجة crash_path — لا يُعاد هنا CRASH."""
    adx = ctx.adx
    m = ctx.mr if isinstance(ctx.mr, dict) else {}
    adx_tf = float(m.get("mr_adx_trend_floor", 18.0))
    adx_r1 = float(m.get("mr_adx_range_max", 20.0))
    adx_r2 = float(m.get("mr_adx_range_max2", 22.0))
    # ترند صاعد: اتجاه السعر أو ADX مع ST صاعد — نفضّل مسار الزخم ونشدّد على عدم البيع الخفيف
    if ctx.st_dir == 1 and (ctx.trend_up or adx >= adx_tf):
        if ctx.close > 0 and ctx.vwap > 0 and ctx.close < ctx.vwap * 0.985 and ctx.hist < 0 and ctx.macd_diff < 0:
            return MarketRegime.TREND_DOWN
        return MarketRegime.TREND_UP
    if ctx.st_dir == -1 and (ctx.trend_down or adx >= adx_tf):
        return MarketRegime.TREND_DOWN
    if adx < adx_r1 and not ctx.trend_up and not ctx.trend_down:
        return MarketRegime.RANGE
    if adx < adx_r2:
        return MarketRegime.RANGE
    return MarketRegime.NEUTRAL


def detect_regime_from_snapshots(
    indicators: dict | None,
    market_info: dict | None,
    cfg: dict | None = None,
    *,
    lang_ar: bool = True,
) -> str:
    """
    نفس منطق detect_regime على لقطة مؤشرات/سوق (مثلاً عند تسجيل الدخول أو بناء ميزات ML).
    يُرجع قيمة نصية: trend_up | trend_down | range | neutral
    """
    ind = indicators if isinstance(indicators, dict) else {}
    info = market_info if isinstance(market_info, dict) else {}
    ctx = PanelContext.build(ind, info, cfg, lang_ar)
    return detect_regime(ctx).value
