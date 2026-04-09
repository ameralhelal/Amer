# composite_signal.py — مؤشر مركّب من عدة مؤشرات مع 3 أسطر تفسير (للوحة والشارت)
from __future__ import annotations

import os
from typing import Any


def _f(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _i(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


_THRESHOLDS_CACHE: dict[str, float] | None = None
_THRESHOLDS_CACHE_MTIME: float | None = None
_PROFILE_CACHE: dict[str, str] | None = None
_PROFILE_CACHE_MTIME: float | None = None


def clamp_composite_thresholds(
    buy: float, strong: float, mid: float, adx_di: float
) -> dict[str, float]:
    """ضبط عتبات المؤشر المركّب إلى نطاقات صالحة (نفس قواعد التشغيل)."""
    buy = max(1.0, min(60.0, float(buy)))
    strong = max(buy + 1.0, min(90.0, float(strong)))
    mid = max(buy + 0.5, min(strong - 0.5, float(mid)))
    adx_di = max(5.0, min(40.0, float(adx_di)))
    return {"buy": buy, "strong": strong, "mid": mid, "adx_di": adx_di}


def get_composite_thresholds() -> dict[str, float]:
    """
    عتبات المؤشر المركّب من الإعدادات (مع ضبط تلقائي إن كانت القيم متعارضة).
    المفاتيح: composite_score_buy, composite_score_strong, composite_score_mid, composite_adx_for_di
    """
    global _THRESHOLDS_CACHE, _THRESHOLDS_CACHE_MTIME

    # الأداء: هذه الدالة كانت تقرأ config.json من القرص في كل استدعاء.
    # هذا يحدث كثيراً أثناء تحديث المؤشرات (websocket)، وقد يجمّد الواجهة.
    # لذلك نُخزن النتيجة مع mtime ونُعيد استخدامها طالما الكونفيج لم يتغير.
    try:
        import config as _cfg_mod

        path = _cfg_mod._config_path()  # موجود داخل config.py
        mtime = os.path.getmtime(path) if os.path.isfile(path) else None
        if _THRESHOLDS_CACHE is not None and _THRESHOLDS_CACHE_MTIME == mtime:
            return _THRESHOLDS_CACHE
    except Exception:
        mtime = None

    try:
        from config import DEFAULTS, load_config_cached

        cfg = load_config_cached()
        d = DEFAULTS
        buy = float(cfg.get("composite_score_buy", d.get("composite_score_buy", 12.0)) or 12.0)
        strong = float(cfg.get("composite_score_strong", d.get("composite_score_strong", 31.0)) or 31.0)
        mid = float(cfg.get("composite_score_mid", d.get("composite_score_mid", 21.0)) or 21.0)
        adx_di = float(cfg.get("composite_adx_for_di", d.get("composite_adx_for_di", 20.0)) or 20.0)
    except Exception:
        buy, strong, mid, adx_di = 12.0, 31.0, 21.0, 20.0

    out = clamp_composite_thresholds(buy, strong, mid, adx_di)
    _THRESHOLDS_CACHE = out
    _THRESHOLDS_CACHE_MTIME = mtime
    return out


def _get_runtime_profiles() -> dict[str, str]:
    """قراءة بروفايل البوت وحساسية المؤشرات مع كاش على mtime."""
    global _PROFILE_CACHE, _PROFILE_CACHE_MTIME
    try:
        import config as _cfg_mod

        path = _cfg_mod._config_path()
        mtime = os.path.getmtime(path) if os.path.isfile(path) else None
        if _PROFILE_CACHE is not None and _PROFILE_CACHE_MTIME == mtime:
            return _PROFILE_CACHE
    except Exception:
        mtime = None

    try:
        from config import load_config_cached

        cfg = load_config_cached()
        master = str(cfg.get("bot_master_profile", "aggressive") or "aggressive").strip().lower()
        speed = str(cfg.get("indicator_speed_profile", "balanced") or "balanced").strip().lower()
    except Exception:
        master, speed = "aggressive", "balanced"

    if master != "aggressive":
        master = "aggressive"
    if speed == "standard":
        speed = "balanced"
    if speed not in ("conservative", "balanced", "fast"):
        speed = "balanced"
    out = {"master_profile": master, "speed_profile": speed}
    _PROFILE_CACHE = out
    _PROFILE_CACHE_MTIME = mtime
    return out


def compute_composite_signal(
    indicators: dict | None,
    market_info: dict | None = None,
    *,
    lang_ar: bool = True,
) -> dict:
    """
    يُرجع:
      level: strong_buy | buy | neutral | sell | strong_sell
      label_ar, label_en: العنوان الكامل
      short_ar, short_en: نص قصير للشارة على الشارت
      score: −100…100
      reasons_ar, reasons_en: 3 أسطر تفسير
      badge_bg, badge_fg: ألوان hex للشارة
    عتبات التصنيف من الإعدادات (انظر get_composite_thresholds) — ضعف ADX يخصم نقاطاً ثابتة ولا يضرب كل النقاط.
    """
    ind = indicators or {}
    try:
        from config import load_config_cached
        from market_status_readout import engine_market_readout_bundle

        _cfg_mr = load_config_cached()
        _hz = str(_cfg_mr.get("bot_trade_horizon") or "short").strip().lower()
        if _hz not in ("short", "swing"):
            _hz = "short"
        mr = engine_market_readout_bundle(_cfg_mr, trade_horizon=_hz)
    except Exception:
        mr = {}
    th = get_composite_thresholds()
    cb, cs, cadx = th["buy"], th["strong"], th["adx_di"]
    prof = _get_runtime_profiles()
    mp = prof.get("master_profile", "aggressive")
    sp = prof.get("speed_profile", "balanced")
    # نفس بروفايل الحساسية الذي حُسبت به المؤشرات في websocket (مصدر المدخلات الفعلي).
    _raw_sp = ind.get("indicator_speed_profile")
    if isinstance(_raw_sp, str) and _raw_sp.strip():
        _rsp = _raw_sp.strip().lower()
        if _rsp == "standard":
            _rsp = "balanced"
        if _rsp in ("conservative", "balanced", "fast"):
            sp = _rsp
    # بروفايل رئيسي ثابت هجومي — عتبات المركّب الأخف فقط (استجابة أسرع).
    if mp == "aggressive":
        cb -= 2.0
        cs -= 4.0
        cadx -= 1.0
    # حساسية LOW تقلّل الاندفاع، HI تسمح باستجابة أسرع.
    # الوضع المتوازن يستخدم فترات RSI/MACD أطول من «fast» فتتحرك الدرجة الخام ببطء؛
    # نخفّض العتبات قليلاً (دون الوصول لحد «fast») ليبقى تصنيف المركّب حاضراً.
    if sp == "conservative":
        cb += 1.0
        cs += 2.0
        cadx += 1.0
    elif sp == "fast":
        cb -= 1.0
        cs -= 2.0
        cadx -= 1.0
    elif sp == "balanced":
        cb -= 0.7
        cs -= 1.4
        cadx -= 0.4
    cb = max(1.0, min(60.0, cb))
    cs = max(cb + 1.0, min(90.0, cs))
    cadx = max(5.0, min(40.0, cadx))
    reasons_pairs: list[tuple[float, str, str]] = []

    def add(w: float, ar: str, en: str) -> None:
        if w != 0:
            reasons_pairs.append((w, ar, en))

    def _p(cons: float, bal: float, agg: float) -> float:
        return agg

    score = 0.0

    macd = _f(ind.get("macd"))
    sig = _f(ind.get("signal"))
    hist = _f(ind.get("hist"))
    hist_prev = _f(ind.get("hist_prev"), hist)
    macd_diff = macd - sig
    if hist > 0 and macd_diff >= 0:
        score += 12
        add(12, "MACD فوق الإشارة وهستوجرام موجب — زخم صاعد", "MACD above signal, positive hist — bullish momentum")
    elif hist < 0 and macd_diff <= 0:
        score -= 12
        add(-12, "MACD تحت الإشارة وهستوجرام سالب — زخم هابط", "MACD below signal, negative hist — bearish momentum")
    elif hist < 0 and macd_diff > 0:
        # كان يُحسب كـ +5 بسبب macd_diff>0 فقط — يؤخر البيع عند بداية الهبوط (هستو سالب قبل التقاطع)
        score -= 7
        add(
            -7,
            "هستوجرام سالب مع MACD فوق الإشارة — غالباً بداية انعكاس/هبوط مبكر",
            "Negative histogram while MACD above signal — early bearish turn often starts here",
        )
    elif hist > 0 and macd_diff < 0:
        score += 7
        add(
            7,
            "هستوجرام موجب مع MACD تحت الإشارة — غالباً بداية انعكاس/صعود مبكر",
            "Positive histogram while MACD below signal — early bullish turn often starts here",
        )
    elif macd_diff > 0:
        score += 5
        add(5, "MACD أعلى من خط الإشارة", "MACD above signal line")
    elif macd_diff < 0:
        score -= 5
        add(-5, "MACD أقل من خط الإشارة", "MACD below signal line")
    # تفاقم الهستوجرام الهابط مباشرة بعد تقاطع تحت الصفر — حساسية إضافية دون انتظار باقي المؤشرات
    if hist < 0 and hist_prev >= 0 and macd_diff <= 0.02:
        score -= 5
        add(
            -5,
            "تقاطع هستوجرام تحت الصفر للتو — تأكيد زخم هابط مبكر",
            "Histogram just crossed below zero — early bearish momentum confirmation",
        )

    st_dir = _i(ind.get("supertrend_dir"), 0)
    if st_dir == 1:
        score += 14
        add(14, "Supertrend صاعد — الاتجاه المفضل شرائياً", "Supertrend up — trend favors longs")
    elif st_dir == -1:
        score -= 14
        add(-14, "Supertrend هابط — الاتجاه المفضل بيعياً", "Supertrend down — trend favors shorts")

    adx = _f(ind.get("adx14"))
    pdi = _f(ind.get("plus_di14"))
    mdi = _f(ind.get("minus_di14"))
    if adx >= cadx:
        if pdi > mdi + 1:
            score += 10
            add(10, "+DI أعلى من −DI مع ADX مقبول — ضغط صاعد", "+DI > −DI with ADX OK — upward pressure")
        elif mdi > pdi + 1:
            score -= 10
            add(-10, "−DI أعلى من +DI مع ADX مقبول — ضغط هابط", "−DI > +DI with ADX OK — downward pressure")
    else:
        score -= 3
        add(-3, "ADX ضعيف — اتجاه أقل وضوحاً", "Weak ADX — trend less reliable")

    # RSI بعد اتجاه/زخم/DI: في ترند صاعد قوي يبقى التشبع شراء «تحذير تصحيح» لا بيعاً كأن الانعكاس حتمي.
    rsi = _f(ind.get("rsi"), 50.0)
    if rsi < 28:
        score += 18
        add(18, "RSI قريب من تشبع البيع (<28) — احتمال ارتداد", "RSI near oversold (<28) — bounce potential")
    elif rsi > 72:
        trend_long = st_dir == 1 and pdi > mdi
        momentum_up = hist > 0 or macd_diff > 0
        adx_trending = adx >= cadx
        if trend_long and momentum_up and adx_trending:
            score -= 5
            add(
                -5,
                "RSI مرتفع داخل ترند صاعد وزخم موجب — تشبع نسبي غالباً مع تمديد السعر (ليست بيعاً تلقائياً)",
                "RSI high in uptrend with positive momentum — overbought often persists in trends (not automatic sell)",
            )
        elif trend_long and momentum_up:
            score -= 10
            add(
                -10,
                "RSI مرتفع مع اتجاه وزخم صاعد — احتمال تصحيح خفيف أكثر من انعكاس",
                "RSI high with bullish trend/momentum — mild pullback risk more than reversal",
            )
        elif st_dir == 1:
            score -= 14
            add(
                -14,
                "RSI تشبع شراء مع Supertrend صاعد — راقب ضعف الزخم قبل اعتبار البيع",
                "RSI overbought while Supertrend up — watch momentum fade before treating as sell",
            )
        else:
            score -= 18
            add(-18, "RSI قريب من تشبع الشراء (>72) — احتمال تصحيح", "RSI near overbought (>72) — pullback risk")
    elif rsi < 42:
        score += 7
        add(7, "RSI تحت منطقة الوسط — ميل صعودي خفيف", "RSI below mid — slight bullish lean")
    elif rsi > 58:
        if st_dir == 1 and hist > 0 and adx >= cadx and pdi > mdi:
            score -= 4
            add(
                -4,
                "RSI فوق الوسط في ترند صاعد — تحيز هبوطي خفيف فقط",
                "RSI above mid in uptrend — only slight bearish lean",
            )
        else:
            score -= 7
            add(-7, "RSI فوق منطقة الوسط — ميل هبوطي خفيف", "RSI above mid — slight bearish lean")

    close = _f(ind.get("close"))
    vwap = _f(ind.get("vwap"))
    if close > 0 and vwap > 0:
        if close >= vwap:
            score += 6
            add(6, "السعر فوق VWAP — سيولة صاعدة نسبياً", "Price above VWAP — relatively bullish auction")
        else:
            score -= 6
            add(-6, "السعر تحت VWAP — ضغط هبوطي نسبي", "Price below VWAP — relatively bearish auction")

    cscore = _f(ind.get("candle_pattern_score"))
    _csp_raw = str(ind.get("candle_pattern_summary") or "").strip()
    if "\n" in _csp_raw:
        _csp_flat = " · ".join(x.strip() for x in _csp_raw.split("\n") if x.strip())
    else:
        _csp_flat = _csp_raw
    if len(_csp_flat) > 200:
        _csp_flat = _csp_flat[:197] + "…"

    if cscore > 0:
        w = max(-14.0, min(14.0, cscore * 2.8))
        score += w
        if _csp_flat and _csp_flat != "—":
            add(w, f"أنماط شموع: {_csp_flat}", f"Candle patterns: {_csp_flat}")
        else:
            add(w, "أنماط شموع صاعدة في آخر الشمعة", "Bullish candle patterns on last bars")
    elif cscore < 0:
        w = max(-14.0, min(14.0, cscore * 2.8))
        score += w
        if _csp_flat and _csp_flat != "—":
            add(w, f"أنماط شموع: {_csp_flat}", f"Candle patterns: {_csp_flat}")
        else:
            add(w, "أنماط شموع هابطة في آخر الشمعة", "Bearish candle patterns on last bars")

    st_k = _f(ind.get("stoch_rsi_k"))
    st_d = _f(ind.get("stoch_rsi_d"))
    _st_os_b = float(mr.get("mr_composite_stoch_os_bounce", 28.0))
    _st_ob_p = float(mr.get("mr_composite_stoch_ob_pullback", 78.0))
    if st_k > 0 and st_d > 0:
        if st_k < _st_os_b and st_k > st_d:
            score += 6
            add(6, "StochRSI منخفض مع K>D — خروج من تشبع بيع محتمل", "StochRSI low, K>D — possible exit from oversold")
        elif st_k > _st_ob_p and st_d > st_k:
            score -= 6
            add(-6, "StochRSI مرتفع مع D>K — خروج من تشبع شراء محتمل", "StochRSI high, D>K — possible exit from overbought")

    # قوة شراء مركّبة (MFI + حجم) — تُحسب في websocket_manager؛ تُدمج هنا في درجة المركّب فقط
    _bps = ind.get("buy_pressure_score")
    if _bps is not None:
        try:
            bpv = float(_bps)
        except (TypeError, ValueError):
            bpv = None
        if bpv is not None:
            adj_bp = max(-6.0, min(6.0, (bpv - 50.0) * 0.12))
            if abs(adj_bp) >= 0.5:
                score += adj_bp
                add(
                    adj_bp,
                    f"قوة شراء مركّبة {bpv:.0f}/100 — {'ضغط شراء' if adj_bp > 0 else 'ضغط أضعف'}",
                    f"Composite buy pressure {bpv:.0f}/100 — {'buying lean' if adj_bp > 0 else 'weaker pressure'}",
                )

    if market_info and isinstance(market_info, dict):
        tr = str(market_info.get("trend") or "").upper()
        if tr == "UP" and score > -5:
            score += 4
            add(4, "بنية السوق (حجم/تدفق) تميل للصعود", "Market structure leans up (volume/flow)")
        elif tr == "DOWN" and score < 5:
            score -= 4
            add(-4, "بنية السوق (حجم/تدفق) تميل للهبوط", "Market structure leans down (volume/flow)")
        # تشديد الهبوط المؤكد حتى لا يبقى التصنيف «محايد» أثناء نزول واضح
        st_dir = _i(ind.get("supertrend_dir"), 0)
        # لا نبالغ بالعقوبة إذا ظهرت إشارات خضراء معتبرة في نفس اللحظة.
        _rsi_stack = float(mr.get("mr_composite_rsi_stack_lo", 45.0))
        _st_stack = max(20.0, float(mr.get("mr_stoch_band_lo", 45.0)) - 1.0)
        bullish_stack_now = int(
            (rsi < _rsi_stack)
            + (macd_diff > 0)
            + (hist > 0)
            + (st_dir == 1)
            + (close > vwap > 0)
            + (cscore > 0)
            + (st_k > st_d and st_k < _st_stack)
        )
        if (
            st_dir == -1
            and close > 0
            and vwap > 0
            and close < vwap
            and hist < 0
            and macd_diff < 0
            and (tr == "DOWN" or abs(score) <= 20)
            and bullish_stack_now <= 2
        ):
            bear_stack_penalty = _p(16.0, 14.0, 8.0)
            score -= bear_stack_penalty
            add(
                -bear_stack_penalty,
                "هبوط مؤكد: اتجاه السوق + Supertrend + MACD تحت الإشارة + السعر تحت VWAP",
                "Confirmed bearish stack: trend + Supertrend + MACD below signal + price below VWAP",
            )

    score = max(-100.0, min(100.0, score))
    # حارس ارتداد القاع: في الارتداد المبكر لا نصنّف «بيع قوي» حتى لا يفوّت القاع.
    s_levels = [_f(ind.get(k)) for k in ("pivot_s1", "pivot_s2", "pivot_s3", "pivot_s4")]
    near_support = any((s > 0 and close > 0 and abs(close - s) / s <= _p(0.010, 0.016, 0.026)) for s in s_levels)
    deep_oversold = bool(rsi <= float(mr.get("mr_composite_deep_os_rsi", 32.0)))
    rebound_macd_ok = (macd_diff > _p(-0.006, -0.014, -0.025)) or (hist > _p(-0.004, -0.012, -0.022))
    # Price action سريع لالتقاط رفض القاع (Aggressive):
    prev_low = _f(ind.get("prev_low"))
    prev_high = _f(ind.get("prev_high"))
    lc_open = _f(ind.get("last_candle_open"))
    lc_close = _f(ind.get("last_candle_close"))
    lc_low = _f(ind.get("last_candle_low"))
    candle_bull = bool(lc_close > lc_open and lc_open > 0 and lc_close > 0)
    lower_wick_reject = bool(
        candle_bull
        and lc_open > 0
        and lc_low > 0
        and (lc_open - lc_low) / lc_open >= 0.0018
        and (lc_close - lc_open) / lc_open >= 0.0006
    )
    micro_break_prev_high = bool(prev_high > 0 and close >= prev_high * 1.0002)
    sweep_prev_low = bool(prev_low > 0 and lc_low > 0 and lc_low <= prev_low * 1.0005 and close >= prev_low * 1.0004)
    rebound_zone = bool(
        close > 0
        and vwap > 0
        and close <= vwap * _p(0.994, 0.996, 0.998)
        and rsi <= float(mr.get("mr_composite_rebound_rsi", 36.0))
        and (near_support or deep_oversold)
        and rebound_macd_ok
    )
    # في الوضع الهجومي: اسمح بالتقاط الارتداد المبكر بشكل أسرع (حتى قبل اختراق VWAP الكامل).
    aggressive_early_rebound = bool(
        mp == "aggressive"
        and close > 0
        and vwap > 0
        and close <= vwap * 1.003
        and rsi <= float(mr.get("mr_aggr_rebound_rsi", 46.0))
        and st_k > st_d
        and st_k < float(mr.get("mr_aggr_rebound_stoch_max", 58.0))
        and (cscore > 0 or hist > -0.01 or macd_diff > -0.015 or lower_wick_reject or sweep_prev_low)
    )
    aggressive_price_action_rebound = bool(
        mp == "aggressive"
        and close > 0
        and (near_support or (vwap > 0 and close <= vwap * 1.006))
        and (lower_wick_reject or sweep_prev_low or micro_break_prev_high)
        and st_k >= st_d
        and st_k <= float(mr.get("mr_pa_stoch_hi", 66.0))
        and (hist > -0.015 or macd_diff > -0.02 or cscore > 0)
    )
    rebound_zone = bool(rebound_zone or aggressive_early_rebound or aggressive_price_action_rebound)
    if rebound_zone and score <= -cs:
        floor = -(cb - _p(0.2, 0.5, 1.2))
        score = max(score, floor)
        add(
            abs(floor),
            "حارس الارتداد: قرب دعم + RSI منخفض + تحسن زخم => خفض «بيع قوي» لتفادي تفويت القاع",
            "Rebound guard: near support + low RSI + improving momentum => downgraded strong sell to avoid missing bottom",
        )
    # حماية أقوى في الهجومي: عند Price Action ارتدادي لا نسمح حتى بـ SELL سريعاً.
    if mp == "aggressive" and aggressive_price_action_rebound and score < 0:
        score = max(score, -0.25)
        add(
            6.0,
            "ارتداد سعري سريع (رفض قاع/اختراق ميكرو) — تهدئة البيع لإتاحة شراء القاع",
            "Fast price-action rebound (bottom rejection/micro break) — softened sell bias for bottom entries",
        )
    # حماية إضافية للوضع الهجومي: أثناء ارتداد القاع لا نندفع إلى «بيع قوي».
    if mp == "aggressive" and rebound_zone and score <= -cs:
        score = max(score, -(cb - 0.1))

    bullish_stack = int(
        (rsi < 45) + (macd_diff > 0) + (hist > 0) + (st_dir == 1) + (close > vwap > 0) + (cscore > 0) + (st_k > st_d and st_k < 44)
    )
    bearish_stack = int(
        (rsi > 55) + (macd_diff < 0) + (hist < 0) + (st_dir == -1) + (close < vwap and close > 0 and vwap > 0) + (cscore < 0) + (st_d > st_k and st_k > 65)
    )
    # إذا كانت الإشارات متضاربة (أحمر قوي + أخضر قوي) لا نصنّف «قوي» مباشرة.
    if bullish_stack >= 3 and bearish_stack >= 3:
        if score <= -cs:
            score = max(score, -(cs - 0.5))
        elif score >= cs:
            score = min(score, cs - 0.5)

    if score >= cs:
        level = "strong_buy"
        label_ar, label_en = "شراء قوي", "Strong buy"
        short_ar, short_en = "شراء قوي", "STRONG BUY"
        bg, fg = "#0a5c36", "#ffffff"
    elif score >= cb:
        level = "buy"
        label_ar, label_en = "شراء", "Buy"
        short_ar, short_en = "شراء", "BUY"
        bg, fg = "#1a6b45", "#ffffff"
    elif score > -cb:
        level = "neutral"
        label_ar, label_en = "محايد", "Neutral"
        short_ar, short_en = "محايد", "NEUTRAL"
        bg, fg = "#3d4555", "#eeeeee"
    elif score > -cs:
        level = "sell"
        label_ar, label_en = "بيع", "Sell"
        short_ar, short_en = "بيع", "SELL"
        bg, fg = "#7a3030", "#ffffff"
    else:
        level = "strong_sell"
        label_ar, label_en = "بيع قوي", "Strong sell"
        short_ar, short_en = "بيع قوي", "STRONG SELL"
        bg, fg = "#5a1818", "#ffffff"

    reasons_pairs.sort(key=lambda t: abs(t[0]), reverse=True)
    seen: set[str] = set()
    ra: list[str] = []
    re: list[str] = []
    for _w, a, e in reasons_pairs:
        key = a[:40]
        if key in seen:
            continue
        seen.add(key)
        ra.append(f"• {a}")
        re.append(f"• {e}")
        if len(ra) >= 3:
            break
    fill_ar = [
        "• دمج RSI وMACD وSupertrend وVWAP وأنماط الشموع.",
        "• تلميح: راجع الدعم/المقاومة والأطر الأعلى قبل الصفقة.",
        "• تعليمي فقط — ليس توصية استثمارية.",
    ]
    fill_en = [
        "• Blends RSI, MACD, Supertrend, VWAP, candle patterns.",
        "• Check S/R and higher timeframes before trading.",
        "• Educational only — not investment advice.",
    ]
    idx = 0
    while len(ra) < 3:
        ra.append(fill_ar[min(idx, len(fill_ar) - 1)])
        idx += 1
    idx = 0
    while len(re) < 3:
        re.append(fill_en[min(idx, len(fill_en) - 1)])
        idx += 1

    return {
        "level": level,
        "label_ar": label_ar,
        "label_en": label_en,
        "short_ar": short_ar,
        "short_en": short_en,
        "score": round(score, 1),
        "reasons_ar": ra[:3],
        "reasons_en": re[:3],
        "badge_bg": bg,
        "badge_fg": fg,
        "rebound_guard": bool(rebound_zone),
    }
