"""
بروفايل متوازن: توازن بين انعكاس RSI وزخم الترند والشموع — مرجع السلوك الافتراضي السابق.
سوينغ: يقدّم الشموع والانتظار الهابط قبل مطاردة الزخم القصير.
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
    if swing:
        return [
            bear_wait,
            oversold(4),
            bull,
            trend_early,
            sells_cluster,
            light_sell,
        ]
    return [
        oversold(4),
        trend_early,
        bear_wait,
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
            oversold(2),
            sells_cluster,
            light_sell,
        ]
    return [
        trend_early,
        oversold(2),
        bear_wait,
        bull,
        sells_cluster,
        light_sell,
    ]


def _trend_down(swing: bool) -> list[Step]:
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
