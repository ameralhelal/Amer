# دمج المؤشر المركّب مع توصية القواعد — منفصل عن الواجهة لتفادي استيراد دائري
from __future__ import annotations

from composite_signal import compute_composite_signal, get_composite_thresholds
from config import load_config_cached


def merge_composite_into_recommendation(
    rule_rec: str,
    rule_conf: float,
    ind: dict,
    info: dict,
    *,
    lang_ar: bool = True,
) -> tuple[str, float, str | None]:
    """
    دمج المؤشر المركّب مع ناتج القواعد.
    يُرجع (توصية، ثقة، مفتاح_ترجمة_لسبب_الدمج|None) لعرضه في حالة السوق.
    عند تعطيل ai_promote_wait_from_composite: لا ترقية WAIT→BUY/SELL.
    """
    try:
        _cfg_m = load_config_cached()
        promote_wait = bool(_cfg_m.get("ai_promote_wait_from_composite", False))
        _block_st_htf = bool(_cfg_m.get("bot_block_buy_st_bear_htf_enabled", True))
        _htf_raw = _cfg_m.get("bot_st_bear_block_chart_intervals")
        if isinstance(_htf_raw, list) and _htf_raw:
            _htf_iv = {str(x).strip().lower() for x in _htf_raw if str(x).strip()}
        else:
            _htf_iv = {"4h", "1d"}
        _struct_need_mid = bool(_cfg_m.get("bot_structural_bear_require_mid_composite", True))
    except Exception:
        promote_wait = False
        _block_st_htf = True
        _htf_iv = {"4h", "1d"}
        _struct_need_mid = True
    th = get_composite_thresholds()
    _b, _m, _s = th["buy"], th["mid"], th["strong"]
    try:
        comp = compute_composite_signal(ind, info, lang_ar=lang_ar)
        sc = float(comp.get("score") or 0.0)
        level = str(comp.get("level") or "neutral")
    except Exception:
        return str(rule_rec or "WAIT").strip().upper(), float(rule_conf or 50.0), None

    r = str(rule_rec or "WAIT").strip().upper()
    c = max(0.0, min(100.0, float(rule_conf or 50.0)))

    if level == "strong_sell" and r == "BUY":
        return ("WAIT", min(c, 54.0), "m_merge_strong_sell_blocked_buy")
    if level == "strong_buy" and r == "SELL":
        return ("WAIT", min(c, 54.0), "m_merge_strong_buy_blocked_sell")

    trend_down = str((info or {}).get("trend") or "").upper() == "DOWN"
    st_bear = int((ind or {}).get("supertrend_dir", 0) or 0) <= -1
    macd_now = float((ind or {}).get("macd", 0) or 0)
    sig_now = float((ind or {}).get("signal", 0) or 0)
    hist_now = float((ind or {}).get("hist", 0) or 0)
    close_now = float((ind or {}).get("close", 0) or 0)
    vwap_now = float((ind or {}).get("vwap", 0) or 0)
    structural_bear = bool(
        st_bear
        and (
            trend_down
            or (close_now > 0 and vwap_now > 0 and close_now < vwap_now and macd_now < sig_now and hist_now < 0)
        )
    )

    chart_iv = str((ind or {}).get("chart_interval") or "").strip().lower()
    htf_st_bear_block = bool(_block_st_htf and chart_iv in _htf_iv and st_bear)

    if r == "BUY":
        # مركّب «قوي» (≥ عتبة strong) يتجاوز حجب ST هابط على 4h/1d والهيكل الهابط — يوافق عرض «شراء قوي»
        if htf_st_bear_block and sc < _s:
            return ("WAIT", min(c, 50.0), "m_merge_htf_st_bear_blocked_buy")
        _bear_floor = _m if (_struct_need_mid and structural_bear) else _b
        if structural_bear and sc < _bear_floor and sc < _s:
            return ("WAIT", min(c, 52.0), "m_merge_struct_bear_buy_to_wait")
        if sc <= -_s:
            return ("WAIT", min(c, 54.0), "m_merge_comp_score_blocked_buy")
        elif -_s < sc <= -_m:
            c = max(44.0, c - 9.0)
        elif sc >= _b:
            c = min(100.0, c + 5.0 + min(10.0, (sc - _b) * 0.12))
        elif sc < -8:
            c = max(44.0, c - 4.0)

    elif r == "SELL":
        if sc >= _s:
            return ("WAIT", min(c, 54.0), "m_merge_comp_score_blocked_sell")
        elif _m <= sc < _s:
            c = max(44.0, c - 9.0)
        elif sc <= -_b:
            c = min(100.0, c + 5.0 + min(10.0, (-_b - sc) * 0.12))
        elif sc > 8:
            c = max(44.0, c - 4.0)

    elif not promote_wait:
        pass
    else:
        macd_diff_now = macd_now - sig_now
        hist_prev_m = float((ind or {}).get("hist_prev", hist_now) or hist_now)
        hist_rising_m = hist_now > hist_prev_m
        momentum_ok = (hist_now > 0 and macd_diff_now > 0) or (
            hist_rising_m
            and macd_diff_now > -0.022
            and int((ind or {}).get("supertrend_dir", 0) or 0) >= 0
        )

        allow_comp_buy = not htf_st_bear_block

        if allow_comp_buy and sc >= _s:
            if st_bear and not momentum_ok:
                return ("WAIT", min(c, 52.0), "m_merge_st_bear_high_composite_need_momentum")
            cap = 82.0 if structural_bear else 88.0
            base = 54.0 if structural_bear else 56.0
            return ("BUY", min(cap, base + sc * 0.2), "m_merge_wait_to_buy_composite_high")
        if sc <= -_s:
            return ("SELL", min(88.0, 56.0 + (-sc) * 0.22), "m_merge_wait_to_sell_composite_low")
        if allow_comp_buy and sc >= _m:
            if structural_bear:
                return ("WAIT", min(c, 48.0), "m_merge_struct_bear_blocked_composite_buy")
            if st_bear and not momentum_ok:
                return ("WAIT", min(c, 47.0), "m_merge_st_bear_mid_composite_need_momentum")
            return ("BUY", min(80.0, 52.0 + (sc - _b) * 0.35), "m_merge_wait_to_buy_composite_mid")
        if sc <= -_m:
            return ("SELL", min(80.0, 52.0 + (-_b - sc) * 0.35), "m_merge_wait_to_sell_composite_mid")

    return (r, max(0.0, min(100.0, c)), None)
