"""انعكاس من تشبع: سلم شراء RSI + شروط MACD/هستو — max_tier يحد أعلى الخطوات (في ترند صاعد نستخدم 2 فقط لتقليل شراء متأخر)."""
from __future__ import annotations

from typing import Optional

from signal_engine.context import PanelContext

RawSignal = tuple[str, float, str]


def try_oversold_buy_ladder(ctx: PanelContext, *, max_tier: int = 4) -> Optional[RawSignal]:
    rsi = ctx.rsi
    if ctx.hard_bear and ctx.hist <= 0.0:
        return None
    p = ctx._p
    macd_diff = ctx.macd_diff
    hist_rising = ctx.hist_rising
    st_dir = ctx.st_dir
    _mac_early_1 = macd_diff > 0 or (hist_rising and macd_diff > p(-0.05, -0.04, -0.032))
    _mac_early_2 = macd_diff > 0 or (hist_rising and macd_diff > p(-0.042, -0.034, -0.028))
    _mac_early_3 = macd_diff > 0.01 or (hist_rising and macd_diff > p(-0.035, -0.028, -0.022))
    _mac_early_4 = macd_diff > 0 or (hist_rising and macd_diff > p(-0.03, -0.024, -0.018) and st_dir >= 0)

    if max_tier >= 1 and rsi < ctx.buy_rsi_1 and _mac_early_1:
        _mb = max(0.0, macd_diff) * 100 + (6.0 if hist_rising and macd_diff <= 0 else 0.0)
        return ("BUY", min(95, 68 + (ctx.buy_rsi_1 - rsi) * 0.5 + min(20, _mb)), "rule_buy_rsi_lt30_macd")
    if max_tier >= 2 and rsi < ctx.buy_rsi_2 and _mac_early_2:
        _mb = max(0.0, macd_diff) * 80 + (5.0 if hist_rising and macd_diff <= 0 else 0.0)
        return ("BUY", min(85, 58 + (ctx.buy_rsi_2 - rsi) * 0.8 + min(15, _mb)), "rule_buy_rsi_lt35_macd")
    if max_tier >= 3 and rsi < ctx.buy_rsi_3 and _mac_early_3:
        return (
            "BUY",
            min(75, 53 + (ctx.buy_rsi_3 - rsi) * 0.5 + (4.0 if hist_rising and macd_diff <= 0.01 else 0.0)),
            "rule_buy_rsi_lt40_macd",
        )
    if max_tier >= 4 and rsi < ctx.buy_rsi_4 and _mac_early_4:
        return ("BUY", p(58.0, 60.0, 62.0), "rule_buy_rsi_lt48_light")
    return None
