"""تشبع صاعد وهبوط زخم: سلم بيع RSI + بيع خفيف MACD."""
from __future__ import annotations

from typing import Optional

from signal_engine.context import PanelContext

RawSignal = tuple[str, float, str]

_STRONG_BULL = frozenset({
    "MorningStar", "BullishEngulfing", "ThreeWhiteSoldiers", "Hammer", "InvertedHammer",
    "PiercingLine", "InverseHeadAndShoulders", "TweezerBottoms", "BullishKicker",
    "ThreeInsideUp", "ThreeOutsideUp", "MarubozuBull", "AbandonedBabyBull",
    "DoubleBottom", "RoundingBottom",
})


def try_overbought_and_structure_sells(ctx: PanelContext) -> Optional[RawSignal]:
    rsi, macd_diff, hist = ctx.rsi, ctx.macd_diff, ctx.hist
    _hot = float(ctx.rsi_sell_hot)
    _stb = float(ctx.rsi_sell_st_bear)
    if rsi >= _hot and macd_diff > 0 and hist < 0:
        return None
    if rsi > ctx.sell_rsi_1 and macd_diff < 0:
        return ("SELL", min(95, 70 + (rsi - ctx.sell_rsi_1) * 0.5 + min(20, -macd_diff * 100)), "rule_sell_rsi_gt70_macd")
    if rsi > ctx.sell_rsi_2 and macd_diff < 0:
        return ("SELL", min(85, 60 + (rsi - ctx.sell_rsi_2) * 0.8 + min(15, -macd_diff * 80)), "rule_sell_rsi_gt65_macd")
    if rsi > ctx.sell_rsi_3 and macd_diff < -0.01:
        return ("SELL", min(75, 55 + (rsi - ctx.sell_rsi_3) * 0.5), "rule_sell_rsi_gt60_macd")
    if rsi >= _hot and hist < 0:
        conf_h = 56.0 + (rsi - _hot) * 1.1 + min(18.0, max(0.0, -float(hist)) * 350.0)
        if macd_diff < 0:
            conf_h += 7.0
        if ctx.candle_score <= -1:
            conf_h += 4.0
        return ("SELL", min(90.0, conf_h), "rule_sell_rsi_high_neg_hist")
    if rsi >= _hot and ctx.candle_score <= -2:
        return ("SELL", min(84.0, 54.0 + (rsi - _hot) * 0.85), "rule_sell_rsi_high_bear_candles")
    if rsi >= _stb and ctx.st_dir < 0 and (macd_diff <= 0 or hist < 0):
        return ("SELL", min(80.0, 58.0 + (rsi - _stb) * 0.7), "rule_sell_supertrend_bear_rsi")
    return None


def try_light_macd_sell(ctx: PanelContext) -> Optional[RawSignal]:
    rsi, macd_diff = ctx.rsi, ctx.macd_diff
    if not (rsi > 52 and macd_diff < 0):
        return None
    strong_bull_hit = bool(ctx.bull_names & _STRONG_BULL)
    if strong_bull_hit and ctx.candle_score >= 1 and rsi < 62:
        return ("WAIT", 52.0, "rule_wait_bull_pattern_vs_weak_sell")
    if ctx.trend_up:
        if rsi > ctx.buy_top_guard_rsi and (ctx.hist < 0 or rsi > (ctx.buy_top_guard_rsi + 4.0)):
            return ("SELL", 62.0, "rule_sell_light_uptrend_extended")
        return None
    return ("SELL", 62.0, "rule_sell_light_downtrend_macd")
