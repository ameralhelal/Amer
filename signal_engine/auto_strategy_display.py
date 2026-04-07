# تسمية مسار «تلقائي» لعرض الواجهة — يطابق resolve_full_chain عند strategy_mode=auto
from __future__ import annotations

from signal_engine.context import PanelContext
from signal_engine.regime import MarketRegime, detect_regime


def auto_active_chain_kind(ctx: PanelContext) -> tuple[str, str]:
    """
    يعيد (النوع, بروفايل) بنفس منطق _auto_chain_from_regime:
    - grid / trend / profile (سلسلة البروفايل فقط؛ extra = master_profile)
    """
    if not ctx.use_regime_router:
        return "profile", ctx.master_profile
    regime = detect_regime(ctx)
    if regime == MarketRegime.RANGE:
        return "grid", ""
    if regime in (MarketRegime.TREND_UP, MarketRegime.TREND_DOWN):
        return "trend", ""
    return "profile", ctx.master_profile
