# طبقة واحدة بعد القواعد: مطاردة مقاومة، 15m، مركّب+شموع، دمج مركّب
from __future__ import annotations

from composite_merge import merge_composite_into_recommendation
from composite_signal import get_composite_thresholds
from signal_engine.context import PanelContext

# ترقية WAIT→BUY من المركّب — لا تُعاد قتلها بمطاردة المقاومة بعد الدمج
_MERGE_PROMO_BUY_KEYS = frozenset({"m_merge_wait_to_buy_composite_high", "m_merge_wait_to_buy_composite_mid"})


def finalize(ctx: PanelContext, rec: str, conf: float, rule_key: str) -> tuple[str, float, str, str | None]:
    from bot_logic import apply_execution_filters

    if not apply_execution_filters(ctx.cfg if isinstance(ctx.cfg, dict) else {}):
        # بدون دمج مركّب ولا انتظارات مركّب/شموع في هذه الطبقة — توصية السلسلة كما خرجت من قواعد الاستراتيجية
        return str(rec or "WAIT").strip().upper(), float(conf or 50.0), rule_key, None

    ind, info = ctx.ind, ctx.info
    chart_iv = ctx.chart_iv
    close, pivot_r1 = ctx.close, ctx.pivot_r1
    macd_diff, hist = ctx.macd_diff, ctx.hist
    comp_score_here = ctx.comp_score
    buy_comp_need = ctx.buy_comp_need
    candle_score = ctx.candle_score
    bottom_rebound_rsi = ctx.bottom_rebound_rsi
    sell_comp_need = ctx.sell_comp_need
    strong_buy_gate = ctx.strong_buy_gate
    rsi = ctx.rsi
    vwap = ctx.vwap
    cfg0 = ctx.cfg if isinstance(ctx.cfg, dict) else {}
    _m_pp = ctx.mr if isinstance(ctx.mr, dict) else {}
    _pp_rhi = float(_m_pp.get("mr_chop_rsi_hi", 56.0))
    _pp_rmid = float(_m_pp.get("rsi_neutral_mid", 50.0))
    comp_on = bool(cfg0.get("bot_merge_composite", False))
    if comp_on:
        try:
            _thr_strong = float(get_composite_thresholds()["strong"])
        except (TypeError, ValueError):
            _thr_strong = 28.0
        comp_strong_bull = float(comp_score_here) >= _thr_strong
    else:
        comp_strong_bull = True  # لا حجب يعتمد على «المركّب ليس قوياً»

    def _price_chase_buy_block() -> bool:
        cls = float(ind.get("close", 0) or 0)
        rsi_f = float(ind.get("rsi", 50) or 50)
        bb_u = float(ind.get("bb_upper", 0) or 0)
        r1p = float(ind.get("pivot_r1", 0) or 0)
        vw = float(ind.get("vwap", 0) or 0)
        pct_b = float(ind.get("pct_below_window_high", 999.0) or 999.0)
        ext_bb = bb_u > 0 and cls > 0 and cls >= bb_u * 0.9975 and rsi_f >= max(50.0, _pp_rhi - 1.0)
        ext_r1 = r1p > 0 and cls > 0 and cls >= r1p * 0.998 and rsi_f >= max(48.0, _pp_rmid + 2.0)
        ext_vw = vw > 0 and cls > 0 and cls > vw * ctx._p(1.010, 1.013, 1.016) and rsi_f >= max(50.0, _pp_rmid + 4.0)
        near_win_high = pct_b < ctx._p(0.17, 0.14, 0.12) and rsi_f >= max(44.0, _pp_rmid - 4.0)
        near_win_high_vw = near_win_high and vw > 0 and cls > vw * 1.0015
        return bool(ext_bb or ext_r1 or ext_vw or near_win_high_vw)

    if comp_on and rec == "BUY" and _price_chase_buy_block() and not comp_strong_bull:
        rec = "WAIT"
        conf = min(float(conf), 52.0)
        rule_key = "rule_wait_chase_resistance_bb"
    if comp_on and rec == "BUY" and not comp_strong_bull and chart_iv == "15m" and float(conf) >= strong_buy_gate:
        breakout_ok = bool(
            close > 0
            and pivot_r1 > 0
            and close > pivot_r1
            and macd_diff > 0
            and hist > 0
        )
        if not breakout_ok:
            rec = "WAIT"
            conf = min(float(conf), 58.0)
            rule_key = "rule_wait_no_15m_breakout_confirmation"
    if comp_on and rec == "BUY" and not comp_strong_bull and comp_score_here < buy_comp_need and candle_score <= 0:
        rec = "WAIT"
        conf = min(float(conf), 56.0)
        rule_key = "rule_wait_need_composite_candle_buy"
    if rec == "SELL":
        near_bottom_rebound = bool(
            vwap > 0 and close > 0 and close < vwap * 0.992 and rsi <= bottom_rebound_rsi and macd_diff > -0.015
        )
        if near_bottom_rebound:
            rec = "WAIT"
            conf = min(float(conf), 54.0)
            rule_key = "rule_wait_late_sell_near_bottom"
        elif comp_on and comp_score_here > sell_comp_need and candle_score >= 0:
            rec = "WAIT"
            conf = min(float(conf), 56.0)
            rule_key = "rule_wait_need_composite_candle_sell"
    if comp_on:
        fr, fc, mk = merge_composite_into_recommendation(
            rec, float(conf), ind, info, lang_ar=ctx.lang_ar
        )
    else:
        fr = str(rec or "WAIT").strip().upper()
        fc = max(0.0, min(100.0, float(conf or 50.0)))
        mk = None
    promo_buy = mk in _MERGE_PROMO_BUY_KEYS
    if comp_on and fr == "BUY" and _price_chase_buy_block() and not promo_buy and not comp_strong_bull:
        fr = "WAIT"
        fc = min(float(fc), 52.0)
        rule_key = "rule_wait_chase_resistance_bb"
    # ثقة الانتظار للعرض/الملخّص: تتبع المركّب وRSI حتى لا تبقى نفس الرقم (مثلاً 52) بلا حركة
    if fr == "WAIT":
        try:
            _rsi_mid = float(getattr(ctx, "rsi_neutral_mid", 50.0) or 50.0)
            if comp_on:
                tilt = max(
                    -5.0,
                    min(9.0, float(comp_score_here) * 0.2 + (float(rsi) - _rsi_mid) * 0.065),
                )
            else:
                tilt = max(-5.0, min(9.0, (float(rsi) - _rsi_mid) * 0.065))
            fc = max(34.0, min(73.0, float(fc) + tilt))
        except (TypeError, ValueError):
            pass
    return fr, fc, rule_key, mk
