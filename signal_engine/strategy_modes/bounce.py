"""ارتداد / DCA: أولوية تشبع البيع وسلم شراء أوسع قبل مطاردة الزخم."""
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
        bear_wait,
        oversold(4),
        bull,
        trend_early,
        sells_cluster,
        light_sell,
    ]


def _trend_up(swing: bool) -> list[Step]:
    if swing:
        return [
            bear_wait,
            oversold(3),
            bull,
            trend_early,
            sells_cluster,
            light_sell,
        ]
    return [
        bear_wait,
        oversold(3),
        trend_early,
        bull,
        sells_cluster,
        light_sell,
    ]


def _trend_down(swing: bool) -> list[Step]:
    if swing:
        return [
            bear_wait,
            sells_cluster,
            oversold(2),
            bull,
            light_sell,
        ]
    return [
        bear_wait,
        sells_cluster,
        oversold(2),
        bull,
        light_sell,
    ]
