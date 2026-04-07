"""سكالبينغ: زخم سريع، سلم شراء ضيق، بيع مبكر نسبياً."""
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
            trend_early,
            oversold(2),
            bull,
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


def _trend_up(swing: bool) -> list[Step]:
    if swing:
        return [
            trend_early,
            bull,
            oversold(2),
            bear_wait,
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
            sells_cluster,
            bear_wait,
            oversold(1),
            bull,
            light_sell,
        ]
    return [
        sells_cluster,
        bear_wait,
        oversold(1),
        bull,
        light_sell,
    ]
