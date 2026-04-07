"""
تنسيق المسارات:
1) هبوط crash → بيع حماية
2) نظام سوق (اختياري): ترند صاعد/هابط/عرضي
3) strategy_mode: scalping | bounce | dca | trend | breakout | grid | 3commas | custom | auto
4) custom → بروفايل فقط؛ auto → سلسلة حسب regime (أو بروفايل إذا الموجّه معطّل)
"""
from __future__ import annotations

from signal_engine.context import PanelContext
from signal_engine.paths.crash_path import try_hard_bear_crash
from signal_engine.paths.wait_fallback_path import default_wait, try_hard_bear_momentum_wait
from signal_engine.postprocess import finalize
from signal_engine.strategy_modes import resolve_full_chain
from signal_engine.regime import MarketRegime, detect_regime


def _run_chain(ctx: PanelContext, steps) -> tuple[str, float, str, str | None]:
    for fn in steps:
        out = fn(ctx)
        if out is not None:
            return finalize(ctx, *out)
    w = try_hard_bear_momentum_wait(ctx)
    if w is not None:
        return finalize(ctx, *w)
    return finalize(ctx, *default_wait(ctx))


def evaluate_with_trace(
    ind: dict,
    info: dict,
    cfg: dict | None,
    lang_ar: bool,
) -> tuple[str, float, str, str | None]:
    ctx = PanelContext.build(ind, info, cfg, lang_ar)
    crash = try_hard_bear_crash(ctx)
    if crash is not None:
        return finalize(ctx, *crash)

    regime: MarketRegime | None = detect_regime(ctx) if ctx.use_regime_router else None
    steps = resolve_full_chain(ctx, regime)
    return _run_chain(ctx, steps)
