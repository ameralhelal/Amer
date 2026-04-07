# نص/HTML لقسم «حالة السوق» (مؤشرات ADX/VWAP/ATR/StochRSI/RSI/Supertrend/الشموع) — مشترك بين PyQt والويب.
from __future__ import annotations

from html import escape as html_escape

from config import load_config_cached
from format_utils import format_price
from translations import get_language
from candlestick_patterns import pattern_to_ar
from ui_palette import TOP_TEXT_MUTED, UI_GREEN, UI_RED, UI_AMBER, UI_INFO

_MARKET_READOUT_DEFAULTS: tuple[tuple[str, float], ...] = (
    ("market_readout_adx_strong_min", 30.0),
    ("market_readout_rsi_overbought", 70.0),
    ("market_readout_rsi_oversold", 30.0),
    ("market_readout_rsi_ctx_high", 55.0),
    ("market_readout_rsi_ctx_low", 45.0),
    ("market_readout_stoch_overbought", 74.0),
    ("market_readout_stoch_oversold", 26.0),
    ("market_readout_stoch_band_lo", 45.0),
    ("market_readout_stoch_band_hi", 55.0),
    ("market_readout_stoch_mid_lo", 40.0),
    ("market_readout_stoch_mid_hi", 60.0),
    ("market_readout_stoch_kd_eps", 0.25),
    ("market_readout_stoch_k_bull_min", 55.0),
    ("market_readout_stoch_k_bear_max", 45.0),
    ("market_readout_atr_high_vol_pct", 0.8),
    ("market_readout_supertrend_near_ratio", 0.002),
)


def market_readout_thresholds(cfg: dict | None = None) -> dict[str, float]:
    """عتبات عرض «حالة السوق»؛ حقول RSI تُستخرج أيضاً عبر signal_engine_rsi_zones للمحرّك."""
    c = cfg if isinstance(cfg, dict) else load_config_cached()
    out: dict[str, float] = {}
    for key, default in _MARKET_READOUT_DEFAULTS:
        try:
            v = c.get(key, default)
            out[key] = float(v) if v is not None else float(default)
        except (TypeError, ValueError):
            out[key] = float(default)
    return out


def signal_engine_rsi_zones(cfg: dict | None = None, *, trade_horizon: str = "short") -> dict[str, float]:
    """
    عتبات RSI المشتقة لـ signal_engine (سلم شراء/بيع، مناطق بيع ساخنة، Inverse H&S، منتصف ثقة WAIT).
    مصدر الأرقام الأساسية: نفس مفاتيح market_readout_* المستخدمة في عرض حالة السوق.
    """
    th = market_readout_thresholds(cfg)
    rsi_ob = float(th["market_readout_rsi_overbought"])
    rsi_os = float(th["market_readout_rsi_oversold"])
    rsi_hi = float(th["market_readout_rsi_ctx_high"])
    rsi_lo = float(th["market_readout_rsi_ctx_low"])
    rsi_sell_hot = max(55.0, min(85.0, rsi_ob - 2.0))
    rsi_sell_st_bear = max(52.0, min(82.0, rsi_ob - 4.0))
    rsi_neutral_mid = (rsi_hi + rsi_lo) / 2.0

    h = (trade_horizon or "short").strip().lower()
    if h not in ("short", "swing"):
        h = "short"
    if h == "swing":
        sell_rsi_1 = max(55.0, min(88.0, rsi_ob + 8.0))
    else:
        sell_rsi_1 = max(55.0, min(88.0, rsi_ob + 2.0))
    sell_rsi_2 = max(40.0, sell_rsi_1 - 5.0)
    sell_rsi_3 = max(35.0, sell_rsi_1 - 10.0)
    if sell_rsi_3 >= sell_rsi_2:
        sell_rsi_3 = sell_rsi_2 - 3.0
    if sell_rsi_2 >= sell_rsi_1:
        sell_rsi_2 = sell_rsi_1 - 3.0

    buy_top_guard_rsi = max(40.0, min(78.0, rsi_hi))
    buy_rsi_1 = max(12.0, min(48.0, rsi_os + 3.0))
    buy_rsi_2 = max(buy_rsi_1 + 2.0, min(52.0, rsi_os + 8.0))
    buy_rsi_3 = max(buy_rsi_2 + 2.0, min(58.0, rsi_os + 13.0))
    buy_rsi_4 = max(buy_rsi_3 + 2.0, min(64.0, rsi_os + 18.0))
    _cap_buy4 = buy_top_guard_rsi - 3.0
    if buy_rsi_4 > _cap_buy4:
        buy_rsi_4 = max(buy_rsi_3 + 2.0, _cap_buy4)
    if buy_rsi_3 >= buy_rsi_4:
        buy_rsi_3 = buy_rsi_4 - 3.0
    if buy_rsi_2 >= buy_rsi_3:
        buy_rsi_2 = buy_rsi_3 - 3.0
    if buy_rsi_1 >= buy_rsi_2:
        buy_rsi_1 = max(12.0, buy_rsi_2 - 3.0)

    return {
        "rsi_ob_thr": rsi_ob,
        "rsi_os_thr": rsi_os,
        "rsi_hi_thr": rsi_hi,
        "rsi_lo_thr": rsi_lo,
        "rsi_sell_hot": rsi_sell_hot,
        "rsi_sell_st_bear": rsi_sell_st_bear,
        "rsi_neutral_mid": rsi_neutral_mid,
        "inverse_hs_oversold_max": max(28.0, min(52.0, min(rsi_lo, rsi_os + 14.0))),
        "inverse_hs_st_bear_rsi": max(38.0, min(52.0, rsi_lo)),
        "inverse_hs_momo_max": max(52.0, min(75.0, rsi_ob - 8.0)),
        "inverse_hs_chase_max": max(58.0, min(82.0, rsi_ob - 4.0)),
        "sell_rsi_1": sell_rsi_1,
        "sell_rsi_2": sell_rsi_2,
        "sell_rsi_3": sell_rsi_3,
        "buy_rsi_1": buy_rsi_1,
        "buy_rsi_2": buy_rsi_2,
        "buy_rsi_3": buy_rsi_3,
        "buy_rsi_4": buy_rsi_4,
        "buy_top_guard_rsi": buy_top_guard_rsi,
    }


def engine_market_readout_bundle(cfg: dict | None = None, *, trade_horizon: str = "short") -> dict[str, float]:
    """
    دمج signal_engine_rsi_zones مع كل عتبات market_readout_* المستخدمة في التوصية والقرار.
    مفاتيح mr_*: ADX (نظام السوق)، StochRSI، ATR%، قرب Supertrend، وعتبات مساعدة في decide.
    """
    rz = signal_engine_rsi_zones(cfg, trade_horizon=trade_horizon)
    th = market_readout_thresholds(cfg)
    adx_s = float(th["market_readout_adx_strong_min"])
    st_ob = float(th["market_readout_stoch_overbought"])
    st_os = float(th["market_readout_stoch_oversold"])
    st_kb = float(th["market_readout_stoch_k_bull_min"])
    rsi_mid = float(rz["rsi_neutral_mid"])
    rsi_lo = float(rz["rsi_lo_thr"])
    rsi_hi = float(rz["rsi_hi_thr"])
    rsi_ob = float(rz["rsi_ob_thr"])
    rsi_os = float(rz["rsi_os_thr"])
    extra: dict[str, float] = {
        "mr_adx_strong": adx_s,
        "mr_adx_trend_floor": max(10.0, min(32.0, adx_s - 12.0)),
        "mr_adx_range_max": max(12.0, min(35.0, adx_s - 10.0)),
        "mr_adx_range_max2": max(14.0, min(38.0, adx_s - 8.0)),
        "mr_adx_chop_max": max(8.0, min(26.0, adx_s - 14.0)),
        "mr_chop_rsi_lo": max(35.0, min(55.0, rsi_mid - 5.0)),
        "mr_chop_rsi_hi": max(45.0, min(68.0, rsi_mid + 8.0)),
        "mr_trend_rsi_relax": max(50.0, min(65.0, rsi_lo + 12.0)),
        "mr_stoch_eps": float(th["market_readout_stoch_kd_eps"]),
        "mr_stoch_ob": st_ob,
        "mr_stoch_os": st_os,
        "mr_stoch_k_bull": st_kb,
        "mr_stoch_k_bear": float(th["market_readout_stoch_k_bear_max"]),
        "mr_stoch_band_lo": float(th["market_readout_stoch_band_lo"]),
        "mr_stoch_band_hi": float(th["market_readout_stoch_band_hi"]),
        "mr_stoch_mid_lo": float(th["market_readout_stoch_mid_lo"]),
        "mr_stoch_mid_hi": float(th["market_readout_stoch_mid_hi"]),
        "mr_atr_hi_pct": float(th["market_readout_atr_high_vol_pct"]),
        "mr_st_near_ratio": float(th["market_readout_supertrend_near_ratio"]),
        "mr_fast_top_stoch_k": min(99.0, max(80.0, st_ob + 17.0)),
        "mr_fast_top_stoch_d": min(98.0, max(75.0, st_ob + 14.0)),
        "mr_fast_bottom_stoch": max(8.0, min(48.0, st_os + 2.0)),
        "mr_loser_stoch": max(10.0, min(45.0, st_os + 4.0)),
        "mr_vwap_chase_rsi": max(58.0, min(88.0, rsi_ob - 1.0)),
        "mr_near_oversold_rsi": max(35.0, min(58.0, min(rsi_lo + 5.0, rsi_os + 20.0))),
        "mr_bear_two_candle_rsi": max(52.0, min(72.0, rsi_hi + 3.0)),
        "mr_aggr_rebound_rsi": max(42.0, min(58.0, rsi_os + 16.0)),
        "mr_bot_aggr_hint_rsi": max(50.0, min(58.0, rsi_os + 24.0)),
        "mr_aggr_rebound_stoch_max": max(52.0, min(72.0, st_kb + 3.0)),
        "mr_pa_stoch_hi": max(58.0, min(78.0, st_ob - 8.0)),
        "mr_composite_stoch_os_bounce": max(15.0, min(42.0, st_os + 2.0)),
        "mr_composite_stoch_ob_pullback": min(92.0, max(65.0, st_ob + 4.0)),
        "mr_composite_rsi_stack_lo": max(38.0, min(52.0, rsi_lo)),
        "mr_composite_deep_os_rsi": max(22.0, min(40.0, rsi_os - 3.0)),
        "mr_composite_rebound_rsi": max(28.0, min(48.0, rsi_os + 6.0)),
        "mr_fast_bottom_rsi_line": max(38.0, min(56.0, rsi_os + 18.0)),
    }
    out = dict(rz)
    out.update(extra)
    return out


def build_market_indicators_readout_html(
    ind: dict,
    info: dict | None,
    *,
    guard_line_html: str = "",
) -> str | None:
    """
    يُرجع نفس HTML الذي يضعه TradingPanel في market_indicators_label.
    None إن لم تُمرَّر مؤشرات كافية.
    """
    ind = ind if isinstance(ind, dict) else {}
    info = info if isinstance(info, dict) else {}
    if not ind:
        return None
    try:
        price = float(ind.get("close", 0) or 0)
        vwap = float(ind.get("vwap", 0) or 0)
        atr = float(ind.get("atr14", 0) or 0)
        adx = float(ind.get("adx14", 0) or 0)
        pdi = float(ind.get("plus_di14", 0) or 0)
        mdi = float(ind.get("minus_di14", 0) or 0)
        st_k = float(ind.get("stoch_rsi_k", 0) or 0)
        st_d = float(ind.get("stoch_rsi_d", 0) or 0)
    except (TypeError, ValueError):
        return None

    trend = (info.get("trend") or "").upper()
    try:
        st_dir_ctx = int(ind.get("supertrend_dir", 0) or 0)
    except (TypeError, ValueError):
        st_dir_ctx = 0
    bearish_ctx = (trend == "DOWN") or (st_dir_ctx == -1)
    bullish_ctx = (trend == "UP") or (st_dir_ctx == 1)
    vwap_side = "↑" if (price and vwap and price >= vwap) else ("↓" if (price and vwap and price < vwap) else "—")
    di_side = "↑" if pdi > mdi else ("↓" if mdi > pdi else "—")
    try:
        _cfg = load_config_cached()
        adx_min = float(_cfg.get("scalp_adx_min", 25) or 25)
    except Exception:
        _cfg = {}
        adx_min = 25.0
    th = market_readout_thresholds(_cfg)
    adx_strong = th["market_readout_adx_strong_min"]
    rsi_ob = th["market_readout_rsi_overbought"]
    rsi_os = th["market_readout_rsi_oversold"]
    rsi_hi = th["market_readout_rsi_ctx_high"]
    rsi_lo = th["market_readout_rsi_ctx_low"]
    st_ob = th["market_readout_stoch_overbought"]
    st_os = th["market_readout_stoch_oversold"]
    st_band_lo = th["market_readout_stoch_band_lo"]
    st_band_hi = th["market_readout_stoch_band_hi"]
    st_mid_lo = th["market_readout_stoch_mid_lo"]
    st_mid_hi = th["market_readout_stoch_mid_hi"]
    st_kd_eps = th["market_readout_stoch_kd_eps"]
    st_k_bull = th["market_readout_stoch_k_bull_min"]
    st_k_bear = th["market_readout_stoch_k_bear_max"]
    atr_hi_pct = th["market_readout_atr_high_vol_pct"]
    st_near_ratio = th["market_readout_supertrend_near_ratio"]
    rsi_mid = (rsi_hi + rsi_lo) / 2.0
    rsi_mid_s = f"{rsi_mid:.0f}" if abs(rsi_mid - round(rsi_mid)) < 0.01 else f"{rsi_mid:.1f}"
    adx_ok = adx >= adx_min

    def color_span(value_str: str, color: str) -> str:
        return f'<span style="color:{color}; font-weight:bold;">{value_str}</span>'

    adx_txt = color_span(f"{adx:.1f}", UI_GREEN) if adx_ok else color_span(f"{adx:.1f}", "#aaaaaa")
    if adx >= adx_strong:
        adx_forecast = color_span("اتجاه قوي (احتمال استمرار الحركة)", UI_GREEN)
    elif adx >= adx_min:
        adx_forecast = color_span("اتجاه متوسط (قابل للاستمرار)", UI_AMBER)
    elif adx > 0:
        adx_forecast = color_span("اتجاه ضعيف (تذبذب/اختراقات كاذبة)", "#aaaaaa")
    else:
        adx_forecast = color_span("غير متاح", "#aaaaaa")

    pdi_color = UI_GREEN if pdi > mdi else (UI_RED if pdi < mdi else "#aaaaaa")
    mdi_color = UI_RED if mdi > pdi else (UI_GREEN if mdi < pdi else "#aaaaaa")
    pdi_txt = color_span(f"{pdi:.1f}", pdi_color)
    mdi_txt = color_span(f"{mdi:.1f}", mdi_color)
    if pdi > mdi:
        di_forecast = color_span("ضغط شرائي (ميل صعود)", UI_GREEN)
    elif mdi > pdi:
        di_forecast = color_span("ضغط بيعي (ميل هبوط)", UI_RED)
    else:
        di_forecast = color_span("توازن (محايد)", "#aaaaaa")

    if price and vwap:
        vwap_color = UI_GREEN if price >= vwap else UI_RED
    else:
        vwap_color = "#aaaaaa"
    vwap_txt = color_span(format_price(vwap), vwap_color)
    if price and vwap:
        if price >= vwap and bearish_ctx:
            vwap_forecast = color_span("فوق VWAP وسط ترند هابط — غالباً ارتداد/اختبار وليس انعكاساً تلقائياً", UI_AMBER)
        elif price < vwap and bullish_ctx:
            vwap_forecast = color_span("تحت VWAP وسط ترند صاعد — غالباً تصحيح/اختبار دعم", UI_AMBER)
        elif price >= vwap:
            vwap_forecast = color_span("فوق VWAP — سيولة/ضغط صاعد نسبي", UI_GREEN)
        else:
            vwap_forecast = color_span("تحت VWAP — ضغط هابط نسبي", UI_RED)
    else:
        vwap_forecast = color_span("غير متاح", "#aaaaaa")

    _st_eps = st_kd_eps
    _near = abs(st_k - st_d) < _st_eps
    if st_d > st_k and st_k >= st_ob:
        st_color = UI_RED
    elif st_k > st_d and st_k <= st_os:
        st_color = UI_GREEN
    elif _near or (st_band_lo <= st_k <= st_band_hi and st_band_lo <= st_d <= st_band_hi):
        st_color = "#aaaaaa"
    elif st_k > st_d:
        st_color = UI_AMBER
    else:
        st_color = UI_AMBER
    st_txt = color_span(f"{st_k:.1f}/{st_d:.1f}", st_color)
    if _near:
        st_forecast_label = "محايد (K≈D)"
        st_forecast_color = "#aaaaaa"
    elif st_d > st_k and st_k >= st_ob:
        st_forecast_label = "تصحيح/هبوط محتمل (تشبع شراء)"
        st_forecast_color = UI_RED
    elif st_k > st_d and st_k <= st_os:
        st_forecast_label = "صعود محتمل (تشبع بيع)"
        st_forecast_color = UI_GREEN
    elif st_mid_lo <= st_k <= st_mid_hi and st_mid_lo <= st_d <= st_mid_hi:
        st_forecast_label = "تذبذب (منطقة وسطى)"
        st_forecast_color = "#aaaaaa"
    elif st_k > st_d and st_k > st_k_bull:
        if bearish_ctx:
            st_forecast_label = "K>D وسط ترند هابط — تصحيح محتمل لا يعني انعكاساً صاعداً"
            st_forecast_color = UI_AMBER
        else:
            st_forecast_label = "ميل صاعد (K فوق D)"
            st_forecast_color = UI_GREEN
    elif st_d > st_k and st_k < st_k_bear:
        if bullish_ctx:
            st_forecast_label = "D>K وسط ترند صاعد — تجميع محتمل لا يعني انعكاساً هابطاً"
            st_forecast_color = UI_AMBER
        else:
            st_forecast_label = "ميل هابط (D فوق K)"
            st_forecast_color = UI_RED
    elif st_k > st_d:
        st_forecast_label = "ضغط صعودي خفيف"
        st_forecast_color = UI_AMBER
    else:
        st_forecast_label = "ضغط هبوطي خفيف"
        st_forecast_color = UI_AMBER
    st_forecast_txt = color_span(st_forecast_label, st_forecast_color)

    rsi = float(ind.get("rsi", 50) or 50)
    if rsi > rsi_ob:
        rsi_color = UI_RED
    elif rsi < rsi_os:
        rsi_color = UI_INFO
    else:
        rsi_color = "#aaaaaa"
    rsi_txt = color_span(f"{rsi:.1f}", rsi_color)
    if rsi >= rsi_ob:
        if bearish_ctx:
            rsi_forecast = color_span("تشبع شراء وسط ترند هابط — تصحيح محتمل (الترند الأعمق هابط)", UI_AMBER)
        else:
            rsi_forecast = color_span("تشبع شراء (تصحيح/هبوط محتمل)", UI_RED)
    elif rsi <= rsi_os:
        if bullish_ctx:
            rsi_forecast = color_span("تشبع بيع وسط ترند صاعد — ارتداد محتمل (الترند الأعمق صاعد)", UI_AMBER)
        else:
            rsi_forecast = color_span("تشبع بيع (ارتداد/صعود محتمل)", UI_INFO)
    elif bearish_ctx and rsi >= rsi_hi:
        rsi_forecast = color_span("RSI فوق الوسط وسط ترند هابط — ليس بالضرورة صعوداً جديداً", UI_AMBER)
    elif bullish_ctx and rsi <= rsi_lo:
        rsi_forecast = color_span("RSI تحت الوسط وسط ترند صاعد — تجميع/تصحيح محتمل", UI_AMBER)
    elif bullish_ctx and rsi >= rsi_hi:
        rsi_forecast = color_span("فوق الوسط يتماشى مع ترند صاعد", UI_GREEN)
    elif bearish_ctx and rsi <= rsi_lo:
        rsi_forecast = color_span(
            f"RSI تحت {rsi_mid_s} — زخم ضعيف يتوافق مع ترند هابط (ليست إشارة شراء؛ التشبع البيعي الشديد غالباً دون {rsi_os:.0f})",
            UI_RED,
        )
    elif rsi >= rsi_hi:
        rsi_forecast = color_span("ميل صاعد نسبي (بلا ترند عام واضح)", UI_GREEN)
    elif rsi <= rsi_lo:
        rsi_forecast = color_span("ميل هابط نسبي (بلا ترند عام واضح)", UI_RED)
    else:
        rsi_forecast = color_span("محايد", "#aaaaaa")
    if trend == "UP" and rsi > rsi_ob:
        trend_label = "صاعد — تشبع شراء"
        trend_color = UI_AMBER
    elif trend == "DOWN" and rsi < rsi_os:
        trend_label = "هابط — تشبع بيع"
        trend_color = UI_INFO
    elif trend == "UP":
        trend_label = "صاعد"
        trend_color = UI_GREEN
    elif trend == "DOWN":
        trend_label = "هابط"
        trend_color = UI_RED
    else:
        trend_label = "محايد"
        trend_color = "#aaaaaa"
    trend_txt = color_span(trend_label, trend_color)

    guard_line = guard_line_html or ""

    line1 = f"ADX: {adx_txt} (الحد {adx_min:.0f}+ {'✓' if adx_ok else '—'}) | {adx_forecast}"
    line2 = f"+DI/-DI: {pdi_txt}/{mdi_txt} {di_side} | {di_forecast}"
    line2b = f"VWAP: {vwap_txt} ({vwap_side}) | {vwap_forecast}"
    atr_pct_ok = atr > 0 and price > 0 and (atr / max(price, 1e-9)) * 100.0 >= atr_hi_pct
    line2c = f"ATR14: {format_price(atr)} | {color_span('تذبذب أعلى' if atr_pct_ok else 'تذبذب طبيعي/منخفض', UI_AMBER if atr_pct_ok else '#aaaaaa')}"
    line3 = f"StochRSI K/D: {st_txt} | التوقّع اللحظي: {st_forecast_txt} | الاتجاه العام: {trend_txt}"
    st_val = float(ind.get("supertrend", 0) or 0)
    st_dir = int(ind.get("supertrend_dir", 0) or 0)
    line_rsi = f"RSI: {rsi_txt} | {rsi_forecast}"
    candle_score = float(ind.get("candle_pattern_score", 0) or 0)
    bull_list = list(ind.get("candle_pattern_bullish") or [])
    bear_list = list(ind.get("candle_pattern_bearish") or [])
    candle_summary_raw = str(ind.get("candle_pattern_summary") or "").strip()
    _candle_summary_lines = [x.strip() for x in candle_summary_raw.splitlines() if x.strip()]
    _mtf_rows_raw = ind.get("candle_pattern_mtf_rows")
    _mtf_rows = _mtf_rows_raw if isinstance(_mtf_rows_raw, list) else []
    use_mtf_colored_rows = len(_mtf_rows) > 0
    use_mtf_candle_lines = use_mtf_colored_rows or len(_candle_summary_lines) > 1

    def _fmt_pat(lst: list) -> str:
        names = []
        for x in lst[:3]:
            sx = str(x)
            names.append(pattern_to_ar(sx))
        return "، ".join(names)

    if candle_score > 0 and bull_list:
        c_color = UI_GREEN
        c_arrow = "⬆️"
        c_names = _fmt_pat(bull_list)
    elif candle_score < 0 and bear_list:
        c_color = UI_RED
        c_arrow = "⬇️"
        c_names = _fmt_pat(bear_list)
    elif candle_score > 0:
        c_color = UI_GREEN
        c_arrow = "⬆️"
        c_names = "إشارة صعودية"
    elif candle_score < 0:
        c_color = UI_RED
        c_arrow = "⬇️"
        c_names = "إشارة هبوطية"
    else:
        c_color = "#aaaaaa"
        c_arrow = "↔️"
        c_names = "محايد"
    _ar_ui = get_language() == "ar"
    if not use_mtf_candle_lines:
        if bearish_ctx and (candle_score > 0 or bool(bull_list)):
            c_color = UI_AMBER
            c_arrow = "↔️"
            if _ar_ui:
                c_suffix = " — ترند عام هابط: الأنماط الصاعدة غالباً ارتداد/بناء مؤقت"
            else:
                c_suffix = " — downtrend: bullish patterns often mean bounce only"
            if len(str(c_names)) > 52:
                c_names = str(c_names)[:51].rstrip("، ") + "…"
            c_names = f"{c_names}{c_suffix}"
        elif bullish_ctx and (candle_score < 0 or bool(bear_list)):
            c_color = UI_AMBER
            c_arrow = "↔️"
            if _ar_ui:
                c_suffix = " — ترند عام صاعد: الأنماط الهابطة غالباً تصحيح مؤقت"
            else:
                c_suffix = " — uptrend: bearish patterns often mean pullback only"
            if len(str(c_names)) > 52:
                c_names = str(c_names)[:51].rstrip("، ") + "…"
            c_names = f"{c_names}{c_suffix}"
    if use_mtf_candle_lines:
        if use_mtf_colored_rows:
            _tone_colors = {
                "bull": UI_GREEN,
                "bear": UI_RED,
                "neutral": "#aaaaaa",
                "mixed": UI_AMBER,
            }
            _mtf_parts: list[str] = []
            for _row in _mtf_rows:
                if not isinstance(_row, dict):
                    continue
                _tone = str(_row.get("tone") or "neutral").strip().lower()
                _col = _tone_colors.get(_tone, "#aaaaaa")
                _mtf_parts.append(color_span(html_escape(str(_row.get("text") or "")), _col))
            _mtf_body = "<br>".join(_mtf_parts)
        else:
            _mtf_body = "<br>".join(html_escape(s) for s in _candle_summary_lines)
        _mtf_hdr = f"{color_span('الشموع', c_color)} {color_span(c_arrow, c_color)}"
        line_candle = f"{_mtf_hdr}<br>{_mtf_body}"
        if bearish_ctx and (candle_score > 0 or bool(bull_list)):
            _ctx = (
                "ترند عام هابط: الصعود في الأسطر أعلاه غالباً ارتداد/بناء مؤقت"
                if _ar_ui
                else "Downtrend: bullish lines above often mean bounce only"
            )
            line_candle += f"<br>{color_span(html_escape(_ctx), UI_AMBER)}"
        elif bullish_ctx and (candle_score < 0 or bool(bear_list)):
            _ctx = (
                "ترند عام صاعد: الهبوط في الأسطر أعلاه غالباً تصحيح مؤقت"
                if _ar_ui
                else "Uptrend: bearish lines above often mean pullback only"
            )
            line_candle += f"<br>{color_span(html_escape(_ctx), UI_AMBER)}"
    else:
        line_candle = f"الشموع: {color_span(c_names, c_color)} {color_span(c_arrow, c_color)}"
    if st_dir != 0:
        st_label = "صاعد ↑" if st_dir == 1 else "هابط ↓"
        _px = float(price or 0)
        _st = float(st_val or 0)
        _near_st = _px > 0 and _st > 0 and abs(_px - _st) / _px <= st_near_ratio
        if _near_st:
            st_color = UI_AMBER
            st_forecast = color_span("قرب خط Supertrend — تذبذب أو انعكاس محتمل", UI_AMBER)
        else:
            st_color = UI_GREEN if st_dir == 1 else UI_RED
            st_forecast = color_span(
                "اتجاه صاعد (الخط كدعم تحت السعر)" if st_dir == 1 else "اتجاه هابط (الخط كمقاومة فوق السعر)",
                st_color,
            )
        line4 = f"Supertrend: {color_span(format_price(st_val), st_color)} ({color_span(st_label, st_color)}) | {st_forecast}"
        html = (
            f"<div style='font-size:11px; color:{TOP_TEXT_MUTED}; line-height:1.45; text-align:right;'>"
            f"{guard_line + '<br>' if guard_line else ''}{line1}<br>{line2}<br>{line2b}<br>{line2c}<br>{line3}<br>{line4}<br>{line_rsi}<br>{line_candle}</div>"
        )
    else:
        html = (
            f"<div style='font-size:11px; color:{TOP_TEXT_MUTED}; line-height:1.45; text-align:right;'>"
            f"{guard_line + '<br>' if guard_line else ''}{line1}<br>{line2}<br>{line2b}<br>{line2c}<br>{line3}<br>{line_rsi}<br>{line_candle}</div>"
        )
    return html
