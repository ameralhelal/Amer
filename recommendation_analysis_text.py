# نص التحليل المفصّل (لوحة التوصية + حالة السوق) — مصدر واحد لتفادي التكرار
from __future__ import annotations

from dataclasses import dataclass

from translations import tr


def suggest_strategy_from_market(indicators: dict, interval: str) -> tuple[str, str]:
    """
    اقتراح الاستراتيجية المناسبة حسب حركة الشموع والسوق.
    يُرجع (مفتاح الاستراتيجية، مفتاح الترجمة للسبب).
    """
    if not indicators:
        return "custom", "rec_strategy_reason_insufficient"
    close = float(indicators.get("close", 0) or 0)
    rsi = float(indicators.get("rsi", 50))
    adx = float(indicators.get("adx14", 0))
    macd = float(indicators.get("macd", 0))
    signal = float(indicators.get("signal", 0))
    macd_diff = macd - signal
    r1 = float(indicators.get("pivot_r1", 0) or 0)
    s1 = float(indicators.get("pivot_s1", 0) or 0)
    vol = float(indicators.get("volume_strength", 1.0) or 1.0)
    atr = float(indicators.get("atr14", 0) or 0)
    atr_pct = (atr / (close + 1e-9)) * 100.0 if close > 0 else 0
    prev_low = float(indicators.get("prev_low", 0) or 0)
    tol = 0.005
    tol_touch = 0.003

    if close > 0 and r1 > 0 and close > r1 * 1.002 and vol >= 0.9:
        return "breakout", "rec_strategy_reason_breakout_r1"
    if close > 0 and s1 > 0 and close < s1 * 0.998 and vol >= 0.9:
        return "breakout", "rec_strategy_reason_breakout_s1"

    if adx >= 25 and abs(macd_diff) > 0.02:
        if macd_diff > 0:
            return "trend", "rec_strategy_reason_trend_up"
        return "trend", "rec_strategy_reason_trend_down"

    if adx < 22 and r1 > 0 and s1 > 0 and s1 < close < r1:
        return "grid", "rec_strategy_reason_grid"

    if interval in ("1m", "5m") and (adx >= 20 or atr_pct > 0.5):
        return "scalping", "rec_strategy_reason_scalping"

    if atr_pct > 2.0 and close > 0:
        return "dca", "rec_strategy_reason_dca"

    if close > 0 and s1 > 0 and s1 * (1 - tol) <= close <= s1 * (1 + tol) and rsi < 40:
        return "bounce", "rec_strategy_reason_bounce_s1"
    if prev_low > 0 and s1 > 0 and s1 * (1 - tol_touch) <= prev_low <= s1 * (1 + tol_touch):
        if close > prev_low and (close - prev_low) / (prev_low + 1e-9) >= 0.002 and rsi < 50:
            return "bounce", "rec_strategy_reason_bounce_prev"

    return "3commas", "rec_strategy_reason_default"


@dataclass
class RecommendationAnalysisResult:
    text: str
    suggested_key: str
    rec_ar: str
    close: float
    s1: float
    r1: float
    atr: float


def build_recommendation_analysis_result(
    price: float,
    indicators: dict,
    market_info: dict,
    interval: str,
) -> RecommendationAnalysisResult | None:
    """يبني نفس نص التحليل المعروض في صفحة التوصية. يُرجع None إن لم تُمرَّر مؤشرات."""
    if not indicators:
        return None
    try:
        from format_utils import format_price
    except ImportError:

        def format_price(x):
            return str(round(float(x), 4))

    ind = indicators if isinstance(indicators, dict) else {}
    info = market_info if isinstance(market_info, dict) else {}

    rsi = float(ind.get("rsi", 50))
    macd = float(ind.get("macd", 0))
    signal = float(ind.get("signal", 0))
    adx = float(ind.get("adx14", 0))
    vwap = float(ind.get("vwap", 0) or 0)
    close = float(ind.get("close", 0) or price or 0)
    macd_diff = macd - signal
    pivot = float(ind.get("pivot", 0) or 0)
    r1 = float(ind.get("pivot_r1", 0) or 0)
    r2 = float(ind.get("pivot_r2", 0) or 0)
    s1 = float(ind.get("pivot_s1", 0) or 0)
    s2 = float(ind.get("pivot_s2", 0) or 0)
    atr = float(ind.get("atr14", 0) or 0)

    try:
        from ai_panel import AIPanel

        rec_code, _conf_ai = AIPanel.get_recommendation(ind, info)
        rec_code = str(rec_code or "WAIT").strip().upper()
    except Exception:
        rec_code = "WAIT"
    if rec_code == "BUY":
        direction = "صعود محتمل"
        rec = "شراء"
    elif rec_code == "SELL":
        direction = "هبوط محتمل"
        rec = "بيع"
    else:
        direction = "غير واضح — انتظار"
        rec = "انتظر"

    interval_label = {
        "1m": "دقيقة",
        "5m": "5 دقائق",
        "15m": "15 دقيقة",
        "1h": "ساعة",
        "4h": "4 ساعات",
        "1d": "يوم",
    }.get(interval, interval)

    lines: list[str] = []
    lines.append(f"📊 التحليل على إطار: {interval_label}")
    lines.append("")
    lines.append(f"▶ اتجاه السعر: {direction}")
    lines.append("")

    if pivot > 0 and (r1 or s1):
        lines.append("📐 الدعم والمقاومة (مستويات المحور):")
        if s2 > 0:
            lines.append(f"   دعم 2: {format_price(s2)}")
        if s1 > 0:
            lines.append(f"   دعم 1: {format_price(s1)}")
        lines.append(f"   محور: {format_price(pivot)}")
        if r1 > 0:
            lines.append(f"   مقاومة 1: {format_price(r1)}")
        if r2 > 0:
            lines.append(f"   مقاومة 2: {format_price(r2)}")
        if close > 0:
            if s1 > 0 and close <= s1:
                lines.append("   السعر الحالي قريب من دعم 1 — مراقبة ارتداد صاعد.")
            elif r1 > 0 and close >= r1:
                lines.append("   السعر الحالي قريب من مقاومة 1 — مراقبة انعكاس أو اختراق.")
            elif pivot > 0 and abs(close - pivot) / pivot < 0.005:
                lines.append("   السعر عند منطقة المحور — انتظار خروج واضح.")
        lines.append("")

    vol_strength = float(ind.get("volume_strength", 1.0) or 1.0)
    lines.append("📈 حسب المؤشرات:")
    if rsi < 30:
        lines.append(f"   • حسب RSI ({rsi:.1f}): تشبع بيع، إذن فرصة شراء عند ارتداد.")
    elif rsi < 45:
        lines.append(f"   • حسب RSI ({rsi:.1f}): المنطقة داعمة لصعود محتمل.")
    elif rsi > 70:
        lines.append(f"   • حسب RSI ({rsi:.1f}): تشبع شراء، إذن احتمال هبوط أو تصحيح.")
    elif rsi > 55:
        lines.append(f"   • حسب RSI ({rsi:.1f}): ضغط بيع محتمل.")
    else:
        lines.append(f"   • حسب RSI ({rsi:.1f}): محايد.")
    if macd_diff > 0:
        lines.append("   • حسب MACD: زخم صاعد، إذن يدعم الصعود.")
    elif macd_diff < 0:
        lines.append("   • حسب MACD: زخم هابط، إذن يدعم الهبوط.")
    if close and vwap > 0:
        if close >= vwap:
            lines.append("   • حسب VWAP: السعر فوق المتوسط المرجح بالحجم، إذن البيئة تميل للشراء.")
        else:
            lines.append("   • حسب VWAP: السعر تحت المتوسط المرجح، إذن ضغط أو فرصة شراء عند القاع.")
    if vol_strength >= 1.2:
        lines.append(f"   • حسب قوة الحجم (Volume): مرتفعة ({vol_strength:.2f})، إذن الحركة مدعومة بحجم.")
    elif vol_strength <= 0.8 and vol_strength > 0:
        lines.append(f"   • حسب قوة الحجم (Volume): منخفضة ({vol_strength:.2f})، إذن حذر من ضعف السيولة.")
    if adx >= 25:
        lines.append(f"   • حسب ADX ({adx:.1f}): اتجاه قوي، إذن إشارات أوضح.")
    else:
        lines.append(f"   • حسب ADX ({adx:.1f}): اتجاه ضعيف، إذن حذر من التقلب.")

    _bps = ind.get("buy_pressure_score")
    if _bps is not None:
        try:
            lines.append(tr("rec_line_buy_pressure").format(v=float(_bps)))
        except (TypeError, ValueError):
            pass
    _fgi = ind.get("fear_greed_index")
    if _fgi is not None:
        try:
            iv = int(_fgi)
            fgc = str(ind.get("fear_greed_classification") or "").strip()
            tail = f" ({fgc})" if fgc else ""
            lines.append(tr("rec_line_fear_greed").format(v=iv, tail=tail))
        except (TypeError, ValueError):
            pass

    candle_summary = (ind.get("candle_pattern_summary") or "").strip()
    if candle_summary and candle_summary != "—":
        lines.append("")
        lines.append(f"🕯 من الشموع: {candle_summary}")

    suggested_key, reason_key = suggest_strategy_from_market(ind, interval)
    strategy_tr_key = "risk_strategy_" + suggested_key
    lines.append("")
    lines.append(f"🎯 {tr('rec_strategy_heading')} {tr(strategy_tr_key)}")
    lines.append(f"   {tr('rec_strategy_reason')} {tr(reason_key)}")

    lines.append("")
    if rec == "شراء قوي":
        conclusion = "التوصية: شراء — تشبع بيع مع زخم صاعد، السعر قد يصعد."
    elif rec == "شراء":
        conclusion = "التوصية: شراء — مؤشرات ودعم/مقاومة تدعم صعوداً محتملاً."
    elif rec == "بيع قوي":
        conclusion = "التوصية: بيع — تشبع شراء مع ضعف الزخم، السعر قد يهبط."
    elif rec == "بيع":
        conclusion = "التوصية: بيع — مؤشرات ومقاومة تدعم هبوطاً محتملاً."
    else:
        conclusion = "التوصية: انتظار — انتظر إعداد أوضح من المستويات والمؤشرات."
    lines.append(f"▶ {conclusion}")

    lines.append("")
    lines.append(f"💰 {tr('rec_prices_heading')}")
    if close > 0 and (s1 > 0 or r1 > 0 or atr > 0):
        if rec in ("شراء", "شراء قوي"):
            if s1 > 0:
                lines.append(f"   • الشراء: عند السعر الحالي أو عند وصول السعر إلى {format_price(s1)} (دعم 1).")
                lines.append(
                    f"   • البيع (هدف الربح): عند وصول السعر إلى {format_price(r1) if r1 > 0 else format_price(close + atr * 1.2)} (مقاومة 1 أو أعلى)."
                )
                if s2 > 0 and s2 < close:
                    lines.append(f"   • إن هبط السعر إلى {format_price(s2)} ثم ارتد: يمكنك وضع حد الشراء عند {format_price(s2)}.")
            else:
                buy_zone = close - atr * 0.5 if atr > 0 else close
                sell_zone = close + atr * 1.2 if atr > 0 else close * 1.02
                lines.append(f"   • الشراء: حول {format_price(close)} أو عند انخفاض إلى {format_price(buy_zone)}.")
                lines.append(f"   • البيع (هدف الربح): عند وصول السعر إلى {format_price(sell_zone)}.")
        elif rec in ("بيع", "بيع قوي"):
            if r1 > 0:
                lines.append(f"   • البيع: عند السعر الحالي أو عند وصول السعر إلى {format_price(r1)} (مقاومة 1).")
            else:
                lines.append(f"   • البيع: حول {format_price(close)}.")
            if s1 > 0 and s1 < close:
                lines.append(f"   • إن هبط السعر إلى {format_price(s1)} وارتد: يمكنك وضع حد الشراء عند {format_price(s1)} للدخول صاعداً.")
        else:
            if s1 > 0:
                lines.append(f"   • حد شراء محتمل: إن هبط السعر إلى {format_price(s1)} وارتد، ضع حد الشراء عند {format_price(s1)}.")
            if r1 > 0:
                lines.append(f"   • هدف بيع محتمل: إن صعد السعر إلى {format_price(r1)}، يمكن البيع عند {format_price(r1)}.")
            if s1 <= 0 and r1 <= 0 and atr > 0:
                lines.append(f"   • مراقبة: شراء قرب {format_price(close - atr * 0.3)}، بيع قرب {format_price(close + atr * 0.8)}.")
    else:
        lines.append("   • في انتظار بيانات كافية (دعم/مقاومة أو شموع) لاقتراح أسعار دقيقة.")

    if interval in ("1m", "5m"):
        lines.append("")
        lines.append(
            "💡 للتحليل على مستوى 15 دقيقة أو أعلى (أوضح للمقاومات والدعم)، اختر الإطار 15m أو 1h من قائمة الإطار الزمني."
        )

    text = "\n".join(lines)
    return RecommendationAnalysisResult(
        text=text,
        suggested_key=suggested_key,
        rec_ar=rec,
        close=float(close),
        s1=float(s1),
        r1=float(r1),
        atr=float(atr),
    )
