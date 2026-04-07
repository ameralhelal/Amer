"""
بروفايل محافظ: أولوية للانتظار عند الشموع الهابطة والبيع عند التشبع؛ شراء RSI ضيق جداً؛ أقل اندفاع في الصعود.
سوينغ: نفس الفلسفة مع تقديم أنماط الشموع قبل أي زخم قصير.
"""
from __future__ import annotations

from signal_engine.context import PanelContext
from signal_engine.profiles._steps import (
    Step,
    bear_wait,
    bull,
    light_sell,
    oversold,
    sells_cluster,
    trend_early,
)
from signal_engine.regime import MarketRegime


def build_chain(ctx: PanelContext, regime: MarketRegime | None) -> list[Step]:
    swing = ctx.trade_horizon == "swing"
    if regime is None:
        return _unified(swing)
    if regime == MarketRegime.TREND_UP:
        return _trend_up(swing)
    if regime == MarketRegime.TREND_DOWN:
        return _trend_down(swing)
    return _unified(swing)


def _unified(swing: bool) -> list[Step]:
    # عرضي/موحّد: لا نبدأ ببيع تشبع قبل استنفاد فرص شراء انعكاس آمنة
    if swing:
        return [
            bear_wait,
            oversold(2),
            bull,
            trend_early,
            sells_cluster,
            light_sell,
        ]
    return [
        bear_wait,
        oversold(2),
        trend_early,
        bull,
        sells_cluster,
        light_sell,
    ]


def _trend_up(swing: bool) -> list[Step]:
    if swing:
        return [
            bear_wait,
            bull,
            trend_early,
            oversold(1),
            sells_cluster,
            light_sell,
        ]
    return [
        bear_wait,
        trend_early,
        oversold(1),
        bull,
        sells_cluster,
        light_sell,
    ]


def _trend_down(swing: bool) -> list[Step]:
    # هابط: بيع/انتظار ثم سكين ضيق جداً
    if swing:
        return [
            bear_wait,
            sells_cluster,
            oversold(1),
            bull,
            light_sell,
        ]
    return [
        bear_wait,
        sells_cluster,
        oversold(1),
        bull,
        light_sell,
    ]
