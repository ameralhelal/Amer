# -*- coding: utf-8 -*-
"""تفاصيل رقمية لمؤشر واحد/سبب واضح — لسطر «قرار اللوحة» (منع شراء/بيع/انتظار)."""
from __future__ import annotations

from typing import Any

from composite_signal import compute_composite_signal, get_composite_thresholds
from signal_engine.context import PanelContext


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _chase_trigger_labels(ctx: PanelContext, ar: bool) -> list[str]:
    ind = ctx.ind
    cls = _f(ind.get("close", 0))
    rsi_f = ctx.rsi
    bb_u = _f(ind.get("bb_upper", 0))
    r1p = _f(ind.get("pivot_r1", 0))
    vw = _f(ind.get("vwap", 0))
    pct_b = _f(ind.get("pct_below_window_high", 999.0))
    rs_thr_bb = ctx._p(54.0, 55.0, 56.0)
    rs_thr_r1 = ctx._p(52.0, 53.0, 54.0)
    rs_thr_vw = ctx._p(52.0, 54.0, 56.0)
    rs_thr_nh = ctx._p(46.0, 48.0, 50.0)
    mul_vw = ctx._p(1.010, 1.013, 1.016)
    pct_gate = ctx._p(0.17, 0.14, 0.12)
    parts: list[str] = []
    if bb_u > 0 and cls > 0 and cls >= bb_u * 0.9975 and rsi_f >= rs_thr_bb:
        parts.append(
            (f"قرب BB علوي، RSI={rsi_f:.1f}≥حد{rs_thr_bb:.0f}" if ar else f"Near BB up, RSI={rsi_f:.1f}≥{rs_thr_bb:.0f}")
        )
    if r1p > 0 and cls > 0 and cls >= r1p * 0.998 and rsi_f >= rs_thr_r1:
        parts.append(
            (f"قرب R1، RSI={rsi_f:.1f}≥حد{rs_thr_r1:.0f}" if ar else f"Near R1, RSI={rsi_f:.1f}≥{rs_thr_r1:.0f}")
        )
    if vw > 0 and cls > 0 and cls > vw * mul_vw and rsi_f >= rs_thr_vw:
        parts.append(
            (f"فوق VWAP ممتد (×{mul_vw:.3f}) RSI={rsi_f:.1f}≥حد{rs_thr_vw:.0f}" if ar else f"Stretched above VWAP, RSI={rsi_f:.1f}≥{rs_thr_vw:.0f}")
        )
    near_win_high = pct_b < pct_gate and rsi_f >= rs_thr_nh
    near_win_high_vw = near_win_high and vw > 0 and cls > vw * 1.0015
    if near_win_high_vw:
        parts.append(
            (f"قرب قمة النافذة (%تحتالقمة={pct_b:.2f}<حد{pct_gate:.2f}) RSI={rsi_f:.1f}" if ar else f"Near window high (pct={pct_b:.2f}), RSI={rsi_f:.1f}")
        )
    return parts


def _explain_rule_key(ctx: PanelContext, rk: str, ar: bool) -> str:
    rsi, hist, macd_diff = ctx.rsi, ctx.hist, ctx.macd_diff
    close, vwap, r1 = ctx.close, ctx.vwap, ctx.pivot_r1
    candle_score = ctx.candle_score
    comp = ctx.comp_score
    chart_iv = ctx.chart_iv

    if rk == "rule_wait_chase_resistance_bb":
        bits = _chase_trigger_labels(ctx, ar)
        if ar:
            return "؛ ".join(bits) if bits else ""
        return "; ".join(bits) if bits else ""

    if rk == "rule_wait_no_15m_breakout_confirmation":
        ok_r1 = close > 0 and r1 > 0 and close > r1
        ok_macd = macd_diff > 0
        ok_hist = hist > 0
        if ar:
            return (
                f"15m: إغلاق={close:.6g} R1={r1:.6g} ({'فوق' if ok_r1 else 'تحت/عند'})، "
                f"MACD−إشارة={macd_diff:+.4f} ({'موجب' if ok_macd else 'غير موجب'})، هستو={hist:+.4f} ({'موجب' if ok_hist else 'غير موجب'})، حد قوة شراء={ctx.strong_buy_gate:.0f}%"
            )
        return (
            f"15m: close={close:.6g} vs R1={r1:.6g} ({'above' if ok_r1 else 'not above'}), "
            f"MACD−sig={macd_diff:+.4f}, hist={hist:+.4f}, strong-BUY gate={ctx.strong_buy_gate:.0f}%"
        )

    if rk == "rule_wait_need_composite_candle_buy":
        if ar:
            return (
                f"منع شراء: المركّب {comp:+.1f} < حد الدعم {ctx.buy_comp_need:.1f} ودرجة الشموع {candle_score:+.1f}≤0"
            )
        return f"BUY blocked: composite {comp:+.1f} < need {ctx.buy_comp_need:.1f} and candle score {candle_score:+.1f}≤0"

    if rk == "rule_wait_need_composite_candle_sell":
        if ar:
            return (
                f"منع بيع: المركّب {comp:+.1f} > حد {ctx.sell_comp_need:+.1f} مع شموع {candle_score:+.1f}≥0"
            )
        return f"SELL blocked: composite {comp:+.1f} > need {ctx.sell_comp_need:+.1f} with candle score {candle_score:+.1f}≥0"

    if rk == "rule_wait_late_sell_near_bottom":
        below_vw = vwap > 0 and close > 0 and close < vwap * 0.992
        rsi_ok = rsi <= ctx.bottom_rebound_rsi
        macd_ok = macd_diff > -0.015
        if ar:
            return (
                f"منع بيع متأخر: سعر={close:.6g} VWAP={vwap:.6g} (تحت×0.992={'نعم' if below_vw else 'لا'})، "
                f"RSI={rsi:.1f} حد≤{ctx.bottom_rebound_rsi:.0f} ({'يتحقق' if rsi_ok else 'لا'})، MACD−إشارة>{-0.015} ({'نعم' if macd_ok else 'لا'})"
            )
        return (
            f"Late SELL block: close={close:.6g}, VWAP={vwap:.6g}, RSI={rsi:.1f} vs max {ctx.bottom_rebound_rsi:.0f}, "
            f"MACD−sig={macd_diff:+.4f}"
        )

    if rk == "rule_wait_hard_bear_no_hist_strong_bull":
        if ar:
            return f"منع شراء: هبوط قوي + هستو={hist:+.4f}≤0 مع RSI={rsi:.1f}>42 (نمط صاعد قوي)"
        return f"No BUY: hard bear, hist={hist:+.4f}≤0, RSI={rsi:.1f}>42 (strong bull pattern)"

    if rk == "rule_wait_hard_bear_candle_bull_score2":
        if ar:
            return f"منع شراء: هبوط قوي بدون هستو موجب — شموع={candle_score:+.1f} RSI={rsi:.1f} هستو={hist:+.4f}"
        return f"No BUY: hard bear, hist≤0 — candle={candle_score:+.1f} RSI={rsi:.1f} hist={hist:+.4f}"

    if rk == "rule_wait_hard_bear_candle_bull_score1":
        if ar:
            return f"منع شراء: هبوط قوي + هستو≤0 — شموع≥1 RSI={rsi:.1f}<52 هستو={hist:+.4f}"
        return f"No BUY: hard bear, hist≤0 — candles≥1 RSI={rsi:.1f} hist={hist:+.4f}"

    if rk == "rule_wait_bear_heavy_patterns":
        if ar:
            return f"انتظار: أنماط هابطة + شموع≤−1 RSI={rsi:.1f} (بين40–68) MACD−إشارة={macd_diff:+.4f}"
        return f"WAIT: bear patterns, candle≤-1 RSI={rsi:.1f}, MACD−sig={macd_diff:+.4f}"

    if rk == "rule_wait_bear_engulf_dark_rsi":
        if ar:
            return f"انتظار: ابتلاع/غيمة + RSI={rsi:.1f} في [58،68) شموع≤−1 MACD−إشارة≤0"
        return f"WAIT: engulf/dark cloud RSI={rsi:.1f} in [58,68), MACD−sig≤0"

    if rk == "rule_wait_ihs_conservative":
        if ar:
            return (
                f"انتظار رأس/كتفين معكوس: RSI={rsi:.1f} MACD−إشارة={macd_diff:+.4f} هستو={hist:+.4f} مركّب={comp:+.1f} ST={'هابط' if ctx.st_dir < 0 else 'غير هابط'}"
            )
        return f"WAIT inverse H&S: RSI={rsi:.1f} MACD−sig={macd_diff:+.4f} hist={hist:+.4f} composite={comp:+.1f}"

    if rk == "rule_wait_hard_bear_no_momentum":
        if ar:
            return (
                f"انتظار: ترند هابط بدون زخم — RSI={rsi:.1f} MACD−إشارة={macd_diff:+.4f} هستو={hist:+.4f} (لا شراء من التشبع فقط)"
            )
        return f"WAIT: bear trend, no momentum — RSI={rsi:.1f} MACD−sig={macd_diff:+.4f} hist={hist:+.4f}"

    if rk == "rule_wait_no_clear_signal":
        if ar:
            return (
                f"لا قاعدة شراء/بيع حاسمة — RSI={rsi:.1f} MACD−إشارة={macd_diff:+.4f} هستو={hist:+.4f} شموع={candle_score:+.1f} مركّب={comp:+.1f} إطار={chart_iv or '؟'}"
            )
        return f"No decisive rule — RSI={rsi:.1f} MACD−sig={macd_diff:+.4f} hist={hist:+.4f} candles={candle_score:+.1f} composite={comp:+.1f}"

    if rk == "rule_wait_bull_pattern_vs_weak_sell":
        if ar:
            return (
                f"انتظار بيع خفيف: نمط صاعد قوي + شموع≥1 وRSI={rsi:.1f}<62 وMACD−إشارة<0"
            )
        return f"WAIT weak sell vs bull pattern: RSI={rsi:.1f}<62, MACD−sig<0, candle score≥1"

    return ""


def _explain_merge_key(mk: str, comp_score: float, ar: bool) -> str:
    th = get_composite_thresholds()
    _b = th["buy"]
    _m = th["mid"]
    _s = th["strong"]

    if mk == "m_merge_strong_sell_blocked_buy":
        if ar:
            return f"المركّب: تصنيف «بيع قوي» — الدرجة {comp_score:+.1f} (حدود الإعدادات: شراء≥{_b:.0f} قوي≥{_s:.0f})"
        return f"Composite strong SELL tier — score {comp_score:+.1f} (cfg buy≥{_b:.0f} strong≥{_s:.0f})"

    if mk == "m_merge_strong_buy_blocked_sell":
        if ar:
            return f"المركّب: تصنيف «شراء قوي» — الدرجة {comp_score:+.1f} ألغت البيع"
        return f"Composite strong BUY — score {comp_score:+.1f} vetoed SELL"

    if mk == "m_merge_comp_score_blocked_buy":
        if ar:
            return f"المركّب {comp_score:+.1f} ≤ −{_s:.0f} (عتبة «قوي» سالبة) — منع شراء"
        return f"Composite {comp_score:+.1f} ≤ −{_s:.0f} — BUY blocked"

    if mk == "m_merge_comp_score_blocked_sell":
        if ar:
            return f"المركّب {comp_score:+.1f} ≥ +{_s:.0f} (عتبة «قوي») — منع بيع"
        return f"Composite {comp_score:+.1f} ≥ +{_s:.0f} — SELL blocked"

    if mk == "m_merge_struct_bear_buy_to_wait":
        if ar:
            return f"هبوط هيكلي + مركّب {comp_score:+.1f} < عتبة وسطى {_m:.0f} — منع شراء"
        return f"Structural bear + composite {comp_score:+.1f} < mid threshold {_m:.0f}"

    if mk == "m_merge_htf_st_bear_blocked_buy":
        if ar:
            return f"Supertrend هابط على إطار طويل (4h/1d) — لا شراء مهما كانت درجة المركّب ({comp_score:+.1f})"
        return f"Bear Supertrend on HTF — no BUY (composite {comp_score:+.1f})"

    if mk == "m_merge_st_bear_high_composite_need_momentum":
        if ar:
            return "مركّب عالٍ لكن Supertrend هابط بدون زخم (هستو/MACD) — انتظار"
        return "High composite but bear ST without momentum — WAIT"

    if mk == "m_merge_struct_bear_blocked_composite_buy":
        if ar:
            return f"هبوط هيكلي يمنع ترقية المركّب إلى شراء — درجة {comp_score:+.1f}"
        return f"Structural bear blocks composite BUY path — score {comp_score:+.1f}"

    if mk == "m_merge_st_bear_mid_composite_need_momentum":
        if ar:
            return "Supertrend هابط + مركّب متوسط — ينقص زخم (هستو/MACD) — انتظار"
        return "Bear ST + mid composite — needs momentum — WAIT"

    return ""


def build_decision_indicator_explain(
    *,
    rule_key: str | None,
    merge_key: str | None,
    ind: dict,
    info: dict,
    cfg: dict,
    lang_ar: bool,
) -> str:
    """
    سطر واحد يلخّص المؤشر/الشرط الرقمي الأهم (بدون تكرار عنوان الترجمة الطويل).
    """
    rk = str(rule_key or "").strip()
    mk = str(merge_key or "").strip()
    try:
        ctx = PanelContext.build(ind, info, cfg, lang_ar)
    except Exception:
        ctx = None

    chunks: list[str] = []

    if ctx and rk:
        d = _explain_rule_key(ctx, rk, lang_ar)
        if d:
            chunks.append(d)

    if mk:
        try:
            comp = float(compute_composite_signal(ind, info, lang_ar=lang_ar).get("score", 0.0) or 0.0)
        except Exception:
            comp = _f(ind.get("composite_score"))
        d2 = _explain_merge_key(mk, comp, lang_ar)
        if d2:
            chunks.append(d2)

    return " | ".join(chunks) if chunks else ""
