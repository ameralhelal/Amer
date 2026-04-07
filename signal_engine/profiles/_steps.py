"""مكوّنات السلسلة المشتركة — تُستورد من بروفايلات محافظ/متوازن/هجومي فقط."""
from __future__ import annotations

from collections.abc import Callable

from signal_engine.context import PanelContext
from signal_engine.candle_guards import inverse_hs_buy_ok
from signal_engine.paths.mean_reversion_path import try_oversold_buy_ladder
from signal_engine.paths.patterns_path import try_bear_pattern_wait, try_bull_pattern_buys
from signal_engine.paths.sells_path import try_light_macd_sell, try_overbought_and_structure_sells
from signal_engine.paths.trend_path import try_trend_momentum_early_buy

Step = Callable[[PanelContext], tuple[str, float, str] | None]

bear_wait = try_bear_pattern_wait
trend_early = try_trend_momentum_early_buy
sells_cluster = try_overbought_and_structure_sells
light_sell = try_light_macd_sell


def oversold(max_tier: int) -> Step:
    return lambda c: try_oversold_buy_ladder(c, max_tier=max_tier)


def bull(c: PanelContext) -> tuple[str, float, str] | None:
    def hs_check(ind, info, bull_names, **kwargs):
        return inverse_hs_buy_ok(
            ind,
            info,
            bull_names,
            oversold_max=float(c.inverse_hs_oversold_max),
            st_bear_rsi=float(c.inverse_hs_st_bear_rsi),
            momo_max=float(c.inverse_hs_momo_max),
            chase_max=float(c.inverse_hs_chase_max),
            **kwargs,
        )

    return try_bull_pattern_buys(c, hs_check=hs_check)
