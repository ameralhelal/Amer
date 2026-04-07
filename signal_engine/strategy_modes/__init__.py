"""
طبقة استراتيجية الواجهة (`strategy_mode` من الإعدادات) فوق البروفايل والنظام.

- custom: ترتيب بروفايل المحافظ/المتوازن/الهجومي فقط.
- auto: حسب نظام السوق (regime) عند تفعيل الموجّه؛ وإلا بروفايل فقط.
- scalping | bounce | dca | trend | breakout | grid | 3commas: سلاسل مخصّصة.
"""
from __future__ import annotations

from signal_engine.context import PanelContext
from signal_engine.profiles import aggressive as _aggressive
from signal_engine.regime import MarketRegime

from signal_engine.strategy_modes import bounce as _bounce
from signal_engine.strategy_modes import range_grid as _range_grid
from signal_engine.strategy_modes import scalping as _scalping
from signal_engine.strategy_modes import trend_follow as _trend_follow


def _profile_only_chain(ctx: PanelContext, regime: MarketRegime | None):
    return _aggressive.build_chain(ctx, regime)


def _auto_chain_from_regime(ctx: PanelContext, regime: MarketRegime | None):
    """اختيار سلسلة القواعد حسب نظام السوق — يطابق فكرة «تلقائي حسب السوق»."""
    if regime is None:
        return _profile_only_chain(ctx, regime)
    if regime == MarketRegime.RANGE:
        return _range_grid.build_chain(ctx, regime)
    if regime in (MarketRegime.TREND_UP, MarketRegime.TREND_DOWN):
        return _trend_follow.build_chain(ctx, regime)
    return _profile_only_chain(ctx, regime)


def resolve_full_chain(ctx: PanelContext, regime: MarketRegime | None):
    sm = (ctx.strategy_mode or "custom").strip().lower()
    if sm == "custom":
        return _profile_only_chain(ctx, regime)
    if sm == "auto":
        return _auto_chain_from_regime(ctx, regime)
    if sm == "scalping":
        return _scalping.build_chain(ctx, regime)
    if sm in ("bounce", "dca"):
        return _bounce.build_chain(ctx, regime)
    if sm in ("trend", "breakout"):
        return _trend_follow.build_chain(ctx, regime)
    if sm in ("grid", "3commas"):
        return _range_grid.build_chain(ctx, regime)
    return _profile_only_chain(ctx, regime)
