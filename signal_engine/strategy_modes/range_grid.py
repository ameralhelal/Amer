"""شبكة / أسلوب قريب من 3Commas: يعادل ترتيب المتوازن (عرضي + سلم كامل)."""
from __future__ import annotations

from signal_engine.context import PanelContext
from signal_engine.profiles import balanced as _balanced
from signal_engine.regime import MarketRegime


def build_chain(ctx: PanelContext, regime: MarketRegime | None) -> list:
    return _balanced.build_chain(ctx, regime)
