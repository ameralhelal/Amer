"""
بروفايلات لوحة الذكاء: كل ملف يحدّد ترتيب القواعد و«ذكاءه» الخاص — لا يُخلط مع بروفايل آخر.
أنماط الصفقة (قصير / سوينغ) تُختار داخل كل بروفايل عبر ctx.trade_horizon.
"""
from __future__ import annotations

from signal_engine.context import PanelContext
from signal_engine.regime import MarketRegime

from signal_engine.profiles import aggressive as _aggressive


def resolve_profile_steps(ctx: PanelContext, regime: MarketRegime | None):
    """بروفايل فقط (بدون طبقة strategy_mode) — للاختبار أو استدعاءات خاصة."""
    return _aggressive.build_chain(ctx, regime)


def resolve_steps(ctx: PanelContext, regime: MarketRegime | None):
    """نفس resolve_full_chain — سلسلة ثابتة في strategy_modes (بروفايل هجومي، regime يُتجاهَل)."""
    from signal_engine.strategy_modes import resolve_full_chain

    return resolve_full_chain(ctx, regime)
