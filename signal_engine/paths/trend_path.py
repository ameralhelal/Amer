"""متابعة ترند: شراء زخم مبكر دون مطاردة بولنجر/R1/VWAP."""
from __future__ import annotations

from typing import Optional

from signal_engine.context import PanelContext

RawSignal = tuple[str, float, str]


def try_trend_momentum_early_buy(ctx: PanelContext) -> Optional[RawSignal]:
    from bot_logic import apply_execution_filters

    if not apply_execution_filters(ctx.cfg if isinstance(ctx.cfg, dict) else {}):
        return None
    rsi = ctx.rsi
    p = ctx._p
    m_early_rsi_hi = ctx._h(p(52.0, 54.0, 56.0), p(54.0, 56.0, 58.0))
    bb_u = float(ctx.ind.get("bb_upper", 0) or 0)
    close, pivot_r1, vwap = ctx.close, ctx.pivot_r1, ctx.vwap
    not_bb_chase = not (close > 0 and bb_u > 0 and close >= bb_u * 0.996)
    not_r1_chase = not (close > 0 and pivot_r1 > 0 and close >= pivot_r1 * 0.997)
    vwap_mom_ok = not (close > 0 and vwap > 0 and close > vwap * p(1.006, 1.009, 1.012))
    macd_diff, hist = ctx.macd_diff, ctx.hist
    hist_rising = ctx.hist_rising
    st_dir = ctx.st_dir
    _macd_mom_early = (macd_diff > 0 and hist > 0) or (
        hist_rising
        and macd_diff > p(-0.028, -0.022, -0.018)
        and st_dir == 1
    )
    m = ctx.mr if isinstance(ctx.mr, dict) else {}
    _st_eps = float(m.get("mr_stoch_eps", 0.25))
    _rsi_relax = float(m.get("mr_trend_rsi_relax", 57.0))
    _near_kd = abs(float(ctx.st_k) - float(ctx.st_d)) < _st_eps
    if (
        ctx.trend_up
        and 48 <= rsi <= m_early_rsi_hi
        and _macd_mom_early
        and not_bb_chase
        and not_r1_chase
        and vwap_mom_ok
    ):
        if _near_kd or ctx.st_k >= ctx.st_d or rsi <= _rsi_relax:
            _hist_boost = min(8.0, max(0.0, hist) * 450) if hist > 0 else (3.0 if hist_rising else 0.0)
            conf = min(68, 52 + (m_early_rsi_hi - rsi) * 0.45 + _hist_boost)
            return ("BUY", float(conf), "rule_buy_trend_momentum_early")
    return None
