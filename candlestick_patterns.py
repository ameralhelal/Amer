# candlestick_patterns.py — تحليل أنماط الشموع اليابانية (كل ما يُدرّس في التحليل الفني)
# Single, 2-candle, 3-candle patterns. Candles: list of (open, high, low, close, volume) or dict.

from __future__ import annotations
import logging
from typing import Sequence, Any

log = logging.getLogger("trading.patterns")

# رأس وكتفين (هبوطي): يُفعَّل فقط على هذه الفواصل — على 1m/3m/5m كثير من الإيجابيات الكاذبة
HEAD_SHOULDERS_BEARISH_INTERVALS = frozenset({
    "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w",
})
# رأس وكتفين معكوس (صعودي): نفس التقييد لتقليل الإيجابيات الكاذبة على الفواصل الصغيرة.
HEAD_SHOULDERS_BULLISH_INTERVALS = HEAD_SHOULDERS_BEARISH_INTERVALS

_PATTERN_AR: dict[str, str] = {
    "Doji": "دوجي",
    "Hammer": "المطرقة",
    "InvertedHammer": "المطرقة المعكوسة",
    "ShootingStar": "نجمة ساقطة",
    "HangingMan": "الرجل المشنوق",
    "DragonflyDoji": "دوجي اليعسوب",
    "GravestoneDoji": "دوجي الشاهد",
    "MarubozuBull": "ماروبوزو صاعد",
    "MarubozuBear": "ماروبوزو هابط",
    "SpinningTop": "القمة الدوارة",
    "BullishEngulfing": "ابتلاع صاعد",
    "BearishEngulfing": "ابتلاع هابط",
    "BullishHarami": "الحرامي الأخضر",
    "BearishHarami": "الحرامي الأحمر",
    "PiercingLine": "خط الاختراق",
    "DarkCloudCover": "السحابة الداكنة",
    "TweezerBottoms": "قاعان متساويان",
    "TweezerTops": "قمتان متساويتان",
    "MorningStar": "نجمة الصباح",
    "EveningStar": "نجمة المساء",
    "ThreeWhiteSoldiers": "الجنود الثلاثة",
    "ThreeBlackCrows": "الغربان الثلاثة",
    "GreenPeaksAcceleration": "تسارع القمم الخضراء",
    "HeadAndShoulders": "الرأس والكتفين",
    "InverseHeadAndShoulders": "الرأس والكتفين المعكوس",
    "BullishKicker": "كيكر صاعد",
    "BearishKicker": "كيكر هابط",
    "ThreeInsideUp": "ثلاثة داخلية صاعدة",
    "ThreeInsideDown": "ثلاثة داخلية هابطة",
    "ThreeOutsideUp": "ثلاثة خارجية صاعدة",
    "ThreeOutsideDown": "ثلاثة خارجية هابطة",
    "AbandonedBabyBull": "طفل مهجور صاعد",
    "AbandonedBabyBear": "طفل مهجور هابط",
    "RisingThreeMethods": "ثلاث طرق صاعدة",
    "FallingThreeMethods": "ثلاث طرق هابطة",
    "BullishTasukiGap": "فجوة تاسوكي صاعدة",
    "BearishTasukiGap": "فجوة تاسوكي هابطة",
    "DoubleTop": "قمة مزدوجة",
    "DoubleBottom": "قاع مزدوج",
    "TripleTop": "قمة ثلاثية",
    "TripleBottom": "قاع ثلاثي",
    "AscendingTriangle": "مثلث صاعد",
    "DescendingTriangle": "مثلث هابط",
    "SymmetricalTriangle": "مثلث متماثل",
    "RisingWedge": "وتد صاعد",
    "FallingWedge": "وتد هابط",
    "BullFlag": "علم صاعد",
    "BearFlag": "علم هابط",
    "BullPennant": "راية صاعدة",
    "BearPennant": "راية هابطة",
    "CupAndHandle": "كوب وعروة",
    "RoundingBottom": "قاع دائري",
    "RectangleRange": "مستطيل/تذبذب عرضي",
    "BreakoutRetestBull": "اختراق ثم إعادة اختبار صاعد",
    "BreakoutRetestBear": "كسر ثم إعادة اختبار هابط",
}


def pattern_to_ar(name: str) -> str:
    """تحويل اسم النمط إلى العربية (أو نفس الاسم إذا لم يوجد)."""
    s = str(name or "").strip()
    if not s:
        return ""
    return _PATTERN_AR.get(s, s)


def _get_ohlc(candle: Any, index: int) -> tuple[float, float, float, float] | None:
    """استخراج OHLC من شمعة: tuple (o,h,l,c,v) أو dict."""
    try:
        if isinstance(candle, (list, tuple)) and len(candle) >= 4:
            return float(candle[0]), float(candle[1]), float(candle[2]), float(candle[3])
        if isinstance(candle, dict):
            o = candle.get("open", candle.get("o"))
            h = candle.get("high", candle.get("h"))
            lo = candle.get("low", candle.get("l"))
            c = candle.get("close", candle.get("c"))
            if None in (o, h, lo, c):
                return None
            return float(o), float(h), float(lo), float(c)
    except (TypeError, ValueError, IndexError):
        pass
    return None


def _candle_open_ms(candle: Any) -> int | None:
    """وقت فتح الشمعة (ms) إن وُجد في الصف (الفهرس 5 في tuple من websocket_manager)."""
    try:
        if isinstance(candle, (list, tuple)) and len(candle) >= 6:
            return int(candle[5])
        if isinstance(candle, dict):
            t = candle.get("t", candle.get("open_time", candle.get("T")))
            if t is not None:
                return int(t)
    except (TypeError, ValueError):
        pass
    return None


def _body_wick(candle: tuple[float, float, float, float]):
    o, h, lo, c = candle
    body = abs(c - o)
    range_ = h - lo if h > lo else 1e-9
    upper = h - max(o, c)
    lower = min(o, c) - lo
    is_bullish = c > o
    return body, range_, upper, lower, is_bullish


def _to_ohlc_series(candles: Sequence[Any], max_len: int | None = None) -> list[tuple[float, float, float, float]]:
    """تحويل سلسلة شموع إلى OHLC صالحة فقط (اختياري: آخر max_len)."""
    out: list[tuple[float, float, float, float]] = []
    src = candles[-max_len:] if (max_len and max_len > 0) else candles
    for i, c in enumerate(src):
        ohlc = _get_ohlc(c, i)
        if ohlc is not None:
            out.append(ohlc)
    return out


# ---------------------------------------------------------------------------
# شموع مفردة (Single Candle)
# ---------------------------------------------------------------------------

def is_doji(candle: tuple[float, float, float, float], body_ratio: float = 0.08) -> bool:
    """دوجي: جسم صغير جداً (تردد)."""
    body, range_, _, _, _ = _body_wick(candle)
    return range_ > 0 and body / range_ <= body_ratio


def is_hammer(candle: tuple[float, float, float, float], ratio: float = 2.0) -> bool:
    """مطرقة: جسم صغير في الأعلى، ظل سفلي طويل (إشارة صعودية بعد نزول)."""
    body, range_, upper, lower, is_bullish = _body_wick(candle)
    if range_ <= 0 or body <= 0:
        return False
    return lower >= ratio * body and upper <= body * 0.4 and (is_bullish or body / range_ < 0.35)


def is_inverted_hammer(candle: tuple[float, float, float, float], ratio: float = 2.0) -> bool:
    """مطرقة معكوسة: جسم في الأسفل، ظل علوي طويل (ارتداد صاعد محتمل)."""
    body, range_, upper, lower, _ = _body_wick(candle)
    if range_ <= 0 or body <= 0:
        return False
    return upper >= ratio * body and lower <= body * 0.4


def is_shooting_star(candle: tuple[float, float, float, float], ratio: float = 2.0) -> bool:
    """نجمة إطلاق: جسم صغير في الأسفل، ظل علوي طويل (إشارة هبوطية بعد صعود)."""
    body, range_, upper, lower, _ = _body_wick(candle)
    if range_ <= 0 or body <= 0:
        return False
    return upper >= ratio * body and lower <= body * 0.4


def is_hanging_man(candle: tuple[float, float, float, float], ratio: float = 2.0) -> bool:
    """رجل مشنوق: نفس شكل المطرقة لكن بعد صعود (هبوط محتمل)."""
    return is_hammer(candle, ratio)


def is_dragonfly_doji(candle: tuple[float, float, float, float]) -> bool:
    """دوجي اليعسوب: دوجي مع ظل سفلي طويل، ظل علوي شبه معدوم."""
    body, range_, upper, lower, _ = _body_wick(candle)
    if range_ <= 0:
        return False
    return body / range_ <= 0.1 and lower >= range_ * 0.6 and upper <= range_ * 0.1


def is_gravestone_doji(candle: tuple[float, float, float, float]) -> bool:
    """دوجي الشاهد: دوجي مع ظل علوي طويل، ظل سفلي شبه معدوم."""
    body, range_, upper, lower, _ = _body_wick(candle)
    if range_ <= 0:
        return False
    return body / range_ <= 0.1 and upper >= range_ * 0.6 and lower <= range_ * 0.1


def is_marubozu_bullish(candle: tuple[float, float, float, float], wick_ratio: float = 0.1) -> bool:
    """ماروبوزو صاعد: جسم كامل، ظلال صغيرة (قوة شراء)."""
    body, range_, upper, lower, is_bullish = _body_wick(candle)
    if range_ <= 0 or not is_bullish:
        return False
    return body >= range_ * 0.9 and upper <= range_ * wick_ratio and lower <= range_ * wick_ratio


def is_marubozu_bearish(candle: tuple[float, float, float, float], wick_ratio: float = 0.1) -> bool:
    """ماروبوزو هابط: جسم كامل هابط، ظلال صغيرة (قوة بيع)."""
    body, range_, upper, lower, is_bullish = _body_wick(candle)
    if range_ <= 0 or is_bullish:
        return False
    return body >= range_ * 0.9 and upper <= range_ * wick_ratio and lower <= range_ * wick_ratio


def is_spinning_top(candle: tuple[float, float, float, float]) -> bool:
    """قمة دوّارة: جسم صغير، ظلال من الطرفين (تردد)."""
    body, range_, upper, lower, _ = _body_wick(candle)
    if range_ <= 0:
        return False
    return 0.1 <= body / range_ <= 0.35 and upper >= range_ * 0.2 and lower >= range_ * 0.2


# ---------------------------------------------------------------------------
# نمطان بشمعتين (Two Candle)
# ---------------------------------------------------------------------------

def is_bullish_engulfing(c1: tuple, c2: tuple) -> bool:
    """ابتلاع صاعد: شمعة صاعدة تبتلع جسم الشمعة الهابطة السابقة."""
    a, b = _get_ohlc(c1, 0), _get_ohlc(c2, 1)
    if not a or not b:
        return False
    _, _, _, _, bull1 = _body_wick(a)
    _, _, _, _, bull2 = _body_wick(b)
    o2, h2, l2, c2 = b
    o1, _, _, c1 = a
    return not bull1 and bull2 and o2 <= c1 and c2 >= o1 and c2 > o2 and o2 < o1


def is_bearish_engulfing(c1: tuple, c2: tuple) -> bool:
    """ابتلاع هابط: شمعة هابطة تبتلع جسم الشمعة الصاعدة السابقة."""
    a, b = _get_ohlc(c1, 0), _get_ohlc(c2, 1)
    if not a or not b:
        return False
    _, _, _, _, bull1 = _body_wick(a)
    _, _, _, _, bull2 = _body_wick(b)
    o2, h2, l2, c2 = b
    o1, _, _, c1 = a
    return bull1 and not bull2 and o2 >= c1 and c2 <= o1 and c2 < o2 and o2 > o1


def is_bullish_harami(c1: tuple, c2: tuple) -> bool:
    """هارامي صاعد: شمعة صغيرة صاعدة داخل جسم الشمعة الكبيرة الهابطة السابقة."""
    a, b = _get_ohlc(c1, 0), _get_ohlc(c2, 1)
    if not a or not b:
        return False
    _, _, _, _, bull1 = _body_wick(a)
    _, _, _, _, bull2 = _body_wick(b)
    o2, c2 = b[0], b[3]
    o1, c1 = a[0], a[3]
    body2 = abs(c2 - o2)
    body1 = abs(c1 - o1)
    if body1 <= 0:
        return False
    return not bull1 and bull2 and body2 < body1 and o2 > c1 and c2 < o1


def is_bearish_harami(c1: tuple, c2: tuple) -> bool:
    """هارامي هابط: شمعة صغيرة هابطة داخل جسم الشمعة الكبيرة الصاعدة السابقة."""
    a, b = _get_ohlc(c1, 0), _get_ohlc(c2, 1)
    if not a or not b:
        return False
    _, _, _, _, bull1 = _body_wick(a)
    _, _, _, _, bull2 = _body_wick(b)
    o2, c2 = b[0], b[3]
    o1, c1 = a[0], a[3]
    body2 = abs(c2 - o2)
    body1 = abs(c1 - o1)
    if body1 <= 0:
        return False
    return bull1 and not bull2 and body2 < body1 and o2 < c1 and c2 > o1


def is_piercing_line(c1: tuple, c2: tuple) -> bool:
    """خط الاختراق: شمعة هابطة ثم صاعدة تفتح تحت قاع الأولى وتغلق فوق منتصف جسم الأولى."""
    a, b = _get_ohlc(c1, 0), _get_ohlc(c2, 1)
    if not a or not b:
        return False
    _, _, _, _, bull1 = _body_wick(a)
    _, _, _, _, bull2 = _body_wick(b)
    o1, h1, l1, c1 = a
    o2, c2 = b[0], b[3]
    mid1 = (o1 + c1) / 2
    return not bull1 and bull2 and o2 < l1 and c2 > mid1 and c2 < o1


def is_dark_cloud_cover(c1: tuple, c2: tuple) -> bool:
    """السحابة المظلمة: شمعة صاعدة ثم هابطة تفتح فوق قمة الأولى وتغلق تحت منتصف جسم الأولى."""
    a, b = _get_ohlc(c1, 0), _get_ohlc(c2, 1)
    if not a or not b:
        return False
    _, _, _, _, bull1 = _body_wick(a)
    _, _, _, _, bull2 = _body_wick(b)
    o1, h1, l1, c1 = a
    o2, c2 = b[0], b[3]
    mid1 = (o1 + c1) / 2
    return bull1 and not bull2 and o2 > h1 and c2 < mid1 and c2 > o1


def is_tweezer_bottoms(c1: tuple, c2: tuple, tol_pct: float = 0.002) -> bool:
    """قاعان متماثلان: قاعا الشمعتين متقاربان (ارتداد صاعد محتمل)."""
    a, b = _get_ohlc(c1, 0), _get_ohlc(c2, 1)
    if not a or not b:
        return False
    l1, l2 = a[2], b[2]
    return abs(l1 - l2) / (l1 + 1e-9) <= tol_pct


def is_tweezer_tops(c1: tuple, c2: tuple, tol_pct: float = 0.002) -> bool:
    """قمتان متماثلتان: قمتا الشمعتين متقاربتان (انعكاس هابط محتمل)."""
    a, b = _get_ohlc(c1, 0), _get_ohlc(c2, 1)
    if not a or not b:
        return False
    h1, h2 = a[1], b[1]
    return abs(h1 - h2) / (h1 + 1e-9) <= tol_pct


# ---------------------------------------------------------------------------
# أنماط بثلاث شموع (Three Candle)
# ---------------------------------------------------------------------------

def is_morning_star(c1: tuple, c2: tuple, c3: tuple, mid_body_ratio: float = 0.35) -> bool:
    """نجمة الصباح: هابطة كبيرة، ثم شمعة صغيرة، ثم صاعدة كبيرة (انعكاس صاعد)."""
    a, b, g = _get_ohlc(c1, 0), _get_ohlc(c2, 1), _get_ohlc(c3, 2)
    if not a or not b or not g:
        return False
    _, r1, _, _, bull1 = _body_wick(a)
    body2, r2, _, _, bull2 = _body_wick(b)
    _, r3, _, _, bull3 = _body_wick(g)
    o1, c1 = a[0], a[3]
    c3 = g[3]
    if r1 <= 0 or r3 <= 0:
        return False
    small_mid = body2 / (r2 + 1e-9) <= mid_body_ratio
    return not bull1 and small_mid and bull3 and c3 > (o1 + c1) / 2


def is_evening_star(c1: tuple, c2: tuple, c3: tuple, mid_body_ratio: float = 0.35) -> bool:
    """نجمة المساء: صاعدة كبيرة، ثم شمعة صغيرة، ثم هابطة كبيرة (انعكاس هابط)."""
    a, b, g = _get_ohlc(c1, 0), _get_ohlc(c2, 1), _get_ohlc(c3, 2)
    if not a or not b or not g:
        return False
    _, r1, _, _, bull1 = _body_wick(a)
    body2, r2, _, _, bull2 = _body_wick(b)
    _, r3, _, _, bull3 = _body_wick(g)
    o1, c1 = a[0], a[3]
    c3 = g[3]
    if r1 <= 0 or r3 <= 0:
        return False
    small_mid = body2 / (r2 + 1e-9) <= mid_body_ratio
    return bull1 and small_mid and not bull3 and c3 < (o1 + c1) / 2


def is_three_white_soldiers(c1: tuple, c2: tuple, c3: tuple) -> bool:
    """الجنود الثلاثة البيض: ثلاث شموع صاعدة متتالية بإغلاقات أعلى (استمرار صعود)."""
    a, b, g = _get_ohlc(c1, 0), _get_ohlc(c2, 1), _get_ohlc(c3, 2)
    if not a or not b or not g:
        return False
    _, _, _, _, bull1 = _body_wick(a)
    _, _, _, _, bull2 = _body_wick(b)
    _, _, _, _, bull3 = _body_wick(g)
    cl1, cl2, cl3 = a[3], b[3], g[3]
    return bull1 and bull2 and bull3 and cl1 < cl2 < cl3


def is_three_black_crows(c1: tuple, c2: tuple, c3: tuple) -> bool:
    """الغربان الثلاث: ثلاث شموع هابطة متتالية بإغلاقات أدنى (استمرار هبوط)."""
    a, b, g = _get_ohlc(c1, 0), _get_ohlc(c2, 1), _get_ohlc(c3, 2)
    if not a or not b or not g:
        return False
    _, _, _, _, bull1 = _body_wick(a)
    _, _, _, _, bull2 = _body_wick(b)
    _, _, _, _, bull3 = _body_wick(g)
    cl1, cl2, cl3 = a[3], b[3], g[3]
    return not bull1 and not bull2 and not bull3 and cl1 > cl2 > cl3


def _find_local_extrema(vals: list[float], is_peak: bool, order: int = 2) -> list[int]:
    """
    إيجاد قمم/قيعان محلية بسيطة بدون تبعيات خارجية.
    order=2 يعني نقارن مع شمعتين قبل وبعد.
    """
    idxs: list[int] = []
    n = len(vals)
    if n < order * 2 + 1:
        return idxs
    for i in range(order, n - order):
        v = vals[i]
        ok = True
        for j in range(i - order, i + order + 1):
            if j == i:
                continue
            if is_peak and v <= vals[j]:
                ok = False
                break
            if (not is_peak) and v >= vals[j]:
                ok = False
                break
        if ok:
            idxs.append(i)
    return idxs


def has_head_and_shoulders(
    candles: Sequence[Any],
    *,
    require_neckline_breakdown: bool = True,
    breakdown_buffer_pct: float = 0.0015,
) -> bool:
    """
    Head & Shoulders (هبوطي): 3 قمم متتالية، الوسطى أعلى، والكتفان متقاربان.

    افتراضياً يُشترط كسر خط العنق هبوطاً (إغلاق تحت العنق) مثل منطق المعكوس مع الاختراق.
    بذلك لا يبقى اسم النمط ظاهراً بعد انتهاء السيناريو أو ارتداد السعر فوق العنق (إبطال).
    """
    ohlc = _to_ohlc_series(candles, max_len=40)
    if len(ohlc) < 12:
        return False
    highs = [x[1] for x in ohlc]
    lows = [x[2] for x in ohlc]
    closes = [x[3] for x in ohlc]
    peaks = _find_local_extrema(highs, is_peak=True, order=2)
    if len(peaks) < 3:
        return False
    for i in range(len(peaks) - 2):
        p1, p2, p3 = peaks[i], peaks[i + 1], peaks[i + 2]
        h1, h2, h3 = highs[p1], highs[p2], highs[p3]
        if not (h2 > h1 and h2 > h3):
            continue
        shoulders_close = abs(h1 - h3) / max(h2, 1e-9) <= 0.06
        spacing_ok = 1 <= (p2 - p1) <= 12 and 1 <= (p3 - p2) <= 12
        if not (shoulders_close and spacing_ok):
            continue
        if not require_neckline_breakdown:
            return True
        # أدنى سعر بين (كتف أيسر→رأس) و(رأس→كتف أيمن) ≈ خط العنق
        left_neck = min(lows[p1 : p2 + 1]) if p2 >= p1 else lows[p2]
        right_neck = min(lows[p2 : p3 + 1]) if p3 >= p2 else lows[p3]
        neckline = min(left_neck, right_neck)
        last_close = closes[-1]
        if neckline <= 0:
            continue
        # كسر هبوطي: إغلاق تحت العنق؛ عند العودة فوق العنق لا يُعرض النمط (انتهى/أُبطل)
        if last_close >= neckline * (1.0 - breakdown_buffer_pct):
            continue
        return True
    return False


def has_head_shoulders_bearish_post_rebound(
    candles: Sequence[Any],
    *,
    breakdown_buffer_pct: float = 0.0015,
) -> bool:
    """
    رأس وكتفين هابط: بعد كسر خط العنق هبوطاً، ارتد السعر وأعاد الإغلاق فوق منطقة العنق
    (إبطال الكسر / إعادة اختبار من الأعلى — «بعد انتهاء النمط وارتداد السعر»).

    يختلف عن has_head_and_shoulders: ذلك يعيد True فقط طالما الإغلاق لا يزال تحت العنق؛
    هنا نطلب تسلسلاً: حدث كسر تحت العنق ثم الإغلاق الحالي فوق عتبة العنق.
    """
    ohlc = _to_ohlc_series(candles, max_len=40)
    if len(ohlc) < 12:
        return False
    highs = [x[1] for x in ohlc]
    lows = [x[2] for x in ohlc]
    closes = [x[3] for x in ohlc]
    peaks = _find_local_extrema(highs, is_peak=True, order=2)
    if len(peaks) < 3:
        return False

    def neck_line(neck: float) -> float:
        return neck * (1.0 - breakdown_buffer_pct)

    for i in range(len(peaks) - 3, -1, -1):
        p1, p2, p3 = peaks[i], peaks[i + 1], peaks[i + 2]
        h1, h2, h3 = highs[p1], highs[p2], highs[p3]
        if not (h2 > h1 and h2 > h3):
            continue
        shoulders_close = abs(h1 - h3) / max(h2, 1e-9) <= 0.06
        spacing_ok = 1 <= (p2 - p1) <= 12 and 1 <= (p3 - p2) <= 12
        if not (shoulders_close and spacing_ok):
            continue
        left_neck = min(lows[p1 : p2 + 1]) if p2 >= p1 else lows[p2]
        right_neck = min(lows[p2 : p3 + 1]) if p3 >= p2 else lows[p3]
        neckline = min(left_neck, right_neck)
        if neckline <= 0:
            continue
        line = neck_line(neckline)
        had_breakdown = False
        for j in range(p3, len(closes)):
            if closes[j] < line:
                had_breakdown = True
                break
        if not had_breakdown:
            continue
        if closes[-1] >= line:
            return True
    return False


def has_inverse_head_and_shoulders(
    candles: Sequence[Any],
    *,
    require_neckline_breakout: bool = True,
    breakout_buffer_pct: float = 0.0015,
) -> bool:
    """
    Inverse Head & Shoulders (صعودي): 3 قيعان، الأوسط أدنى، والكتفان متقاربان.
    """
    ohlc = _to_ohlc_series(candles, max_len=40)
    if len(ohlc) < 12:
        return False
    lows = [x[2] for x in ohlc]
    troughs = _find_local_extrema(lows, is_peak=False, order=2)
    if len(troughs) < 3:
        return False
    closes = [x[3] for x in ohlc]
    highs = [x[1] for x in ohlc]
    for i in range(len(troughs) - 2):
        p1, p2, p3 = troughs[i], troughs[i + 1], troughs[i + 2]
        l1, l2, l3 = lows[p1], lows[p2], lows[p3]
        if not (l2 < l1 and l2 < l3):
            continue
        shoulders_close = abs(l1 - l3) / max(max(l1, l3), 1e-9) <= 0.06
        spacing_ok = 1 <= (p2 - p1) <= 12 and 1 <= (p3 - p2) <= 12
        if not (shoulders_close and spacing_ok):
            continue
        if require_neckline_breakout:
            # خط العنق التقريبي: أعلى قمة بين (الكتف الأيسر-الرأس) وبين (الرأس-الكتف الأيمن).
            left_neck = max(highs[p1:p2 + 1]) if p2 >= p1 else highs[p2]
            right_neck = max(highs[p2:p3 + 1]) if p3 >= p2 else highs[p3]
            neckline = min(left_neck, right_neck)
            last_close = closes[-1]
            if neckline <= 0 or last_close < neckline * (1.0 + breakout_buffer_pct):
                continue
            return True
    return False


def _close_dir(c: tuple[float, float, float, float]) -> int:
    return 1 if c[3] > c[0] else (-1 if c[3] < c[0] else 0)


def _body(c: tuple[float, float, float, float]) -> float:
    return abs(c[3] - c[0])


def is_bullish_kicker(c1: tuple, c2: tuple) -> bool:
    a, b = _get_ohlc(c1, 0), _get_ohlc(c2, 1)
    if not a or not b:
        return False
    return _close_dir(a) == -1 and _close_dir(b) == 1 and b[0] > a[1]


def is_bearish_kicker(c1: tuple, c2: tuple) -> bool:
    a, b = _get_ohlc(c1, 0), _get_ohlc(c2, 1)
    if not a or not b:
        return False
    return _close_dir(a) == 1 and _close_dir(b) == -1 and b[0] < a[2]


def is_three_inside_up(c1: tuple, c2: tuple, c3: tuple) -> bool:
    return is_bullish_harami(c1, c2) and _get_ohlc(c3, 2) is not None and _close_dir(_get_ohlc(c3, 2)) == 1 and _get_ohlc(c3, 2)[3] > _get_ohlc(c1, 0)[0]


def is_three_inside_down(c1: tuple, c2: tuple, c3: tuple) -> bool:
    return is_bearish_harami(c1, c2) and _get_ohlc(c3, 2) is not None and _close_dir(_get_ohlc(c3, 2)) == -1 and _get_ohlc(c3, 2)[3] < _get_ohlc(c1, 0)[0]


def is_three_outside_up(c1: tuple, c2: tuple, c3: tuple) -> bool:
    return is_bullish_engulfing(c1, c2) and _get_ohlc(c3, 2) is not None and _close_dir(_get_ohlc(c3, 2)) == 1 and _get_ohlc(c3, 2)[3] > _get_ohlc(c2, 1)[3]


def is_three_outside_down(c1: tuple, c2: tuple, c3: tuple) -> bool:
    return is_bearish_engulfing(c1, c2) and _get_ohlc(c3, 2) is not None and _close_dir(_get_ohlc(c3, 2)) == -1 and _get_ohlc(c3, 2)[3] < _get_ohlc(c2, 1)[3]


def is_abandoned_baby_bull(c1: tuple, c2: tuple, c3: tuple) -> bool:
    a, b, g = _get_ohlc(c1, 0), _get_ohlc(c2, 1), _get_ohlc(c3, 2)
    if not a or not b or not g:
        return False
    return _close_dir(a) == -1 and is_doji(b) and b[1] < a[2] and _close_dir(g) == 1 and g[0] > b[1]


def is_abandoned_baby_bear(c1: tuple, c2: tuple, c3: tuple) -> bool:
    a, b, g = _get_ohlc(c1, 0), _get_ohlc(c2, 1), _get_ohlc(c3, 2)
    if not a or not b or not g:
        return False
    return _close_dir(a) == 1 and is_doji(b) and b[2] > a[1] and _close_dir(g) == -1 and g[0] < b[2]


def is_rising_three_methods(c1: tuple, c2: tuple, c3: tuple, c4: tuple, c5: tuple) -> bool:
    a, b, g, d, e = [_get_ohlc(x, i) for i, x in enumerate((c1, c2, c3, c4, c5))]
    if not all((a, b, g, d, e)):
        return False
    if _close_dir(a) != 1 or _close_dir(e) != 1 or e[3] <= a[3]:
        return False
    return all(_close_dir(x) <= 0 and x[1] <= a[1] and x[2] >= a[2] for x in (b, g, d))


def is_falling_three_methods(c1: tuple, c2: tuple, c3: tuple, c4: tuple, c5: tuple) -> bool:
    a, b, g, d, e = [_get_ohlc(x, i) for i, x in enumerate((c1, c2, c3, c4, c5))]
    if not all((a, b, g, d, e)):
        return False
    if _close_dir(a) != -1 or _close_dir(e) != -1 or e[3] >= a[3]:
        return False
    return all(_close_dir(x) >= 0 and x[1] <= a[1] and x[2] >= a[2] for x in (b, g, d))


def is_bullish_tasuki_gap(c1: tuple, c2: tuple, c3: tuple) -> bool:
    a, b, g = _get_ohlc(c1, 0), _get_ohlc(c2, 1), _get_ohlc(c3, 2)
    if not a or not b or not g:
        return False
    up_gap = b[2] > a[1]
    return _close_dir(a) == 1 and _close_dir(b) == 1 and up_gap and _close_dir(g) == -1 and g[3] > a[1]


def is_bearish_tasuki_gap(c1: tuple, c2: tuple, c3: tuple) -> bool:
    a, b, g = _get_ohlc(c1, 0), _get_ohlc(c2, 1), _get_ohlc(c3, 2)
    if not a or not b or not g:
        return False
    down_gap = b[1] < a[2]
    return _close_dir(a) == -1 and _close_dir(b) == -1 and down_gap and _close_dir(g) == 1 and g[3] < a[2]


def _detect_chart_patterns(candles: Sequence[Any]) -> tuple[list[str], list[str], list[str]]:
    bullish: list[str] = []
    bearish: list[str] = []
    neutral: list[str] = []
    ohlc = _to_ohlc_series(candles, max_len=80)
    if len(ohlc) < 20:
        return bullish, bearish, neutral
    highs = [x[1] for x in ohlc]
    lows = [x[2] for x in ohlc]
    closes = [x[3] for x in ohlc]
    n = len(ohlc)
    last = closes[-1]
    hi = max(highs)
    lo = min(lows)
    rng = max(hi - lo, 1e-9)

    # Double/Triple top-bottom
    peaks = _find_local_extrema(highs, is_peak=True, order=2)
    troughs = _find_local_extrema(lows, is_peak=False, order=2)
    # قمة مزدوجة/ثلاثية: تُلغى التسمية إذا أغلق السعر فوق قمم النمط (كسر صعودي — لم يعد «مقاومة مزدوجة جاهزة»)
    _top_break_eps = 1.0025  # ~0.25% فوق أعلى قمة في النمط
    # قاع مزدوج/ثلاثي: يشترط إغلاقاً فوق قاعَي النمط بقليل — بدون ذلك كانت أي قاعين متقاربين تُسمّى «مزدوج» حتى أثناء استمرار الهبوط (مضلّل).
    _bottom_confirm_eps = 1.0025
    if len(peaks) >= 2:
        p1, p2 = peaks[-2], peaks[-1]
        h1, h2 = highs[p1], highs[p2]
        if abs(h1 - h2) / max(h1, h2, 1e-9) <= 0.01:
            peak_max = max(h1, h2)
            if peak_max > 0 and last <= peak_max * _top_break_eps:
                bearish.append("DoubleTop")
    if len(troughs) >= 2:
        t1, t2 = troughs[-2], troughs[-1]
        if abs(lows[t1] - lows[t2]) / max(lows[t1], 1e-9) <= 0.01:
            trough_floor = min(lows[t1], lows[t2])
            if trough_floor > 0 and last >= trough_floor * _bottom_confirm_eps:
                bullish.append("DoubleBottom")
    if len(peaks) >= 3:
        p = peaks[-3:]
        hm = max(highs[i] for i in p)
        if hm > 0 and max(highs[i] for i in p) - min(highs[i] for i in p) <= 0.015 * hm:
            if last <= hm * _top_break_eps:
                bearish.append("TripleTop")
    if len(troughs) >= 3:
        t = troughs[-3:]
        if max(lows[i] for i in t) - min(lows[i] for i in t) <= 0.015 * max(lows[i] for i in t):
            tmin = min(lows[i] for i in t)
            if tmin > 0 and last >= tmin * _bottom_confirm_eps:
                bullish.append("TripleBottom")

    # Triangles/wedges via simple slope estimate
    k = min(25, n)
    xs = list(range(k))
    hs = highs[-k:]
    ls = lows[-k:]
    def _slope(vals: list[float]) -> float:
        x_mean = sum(xs) / len(xs)
        y_mean = sum(vals) / len(vals)
        num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, vals))
        den = sum((x - x_mean) ** 2 for x in xs) + 1e-9
        return num / den
    sh = _slope(hs)
    sl = _slope(ls)
    if abs(sh) < rng * 0.002 and sl > rng * 0.004:
        bullish.append("AscendingTriangle")
    if abs(sl) < rng * 0.002 and sh < -rng * 0.004:
        bearish.append("DescendingTriangle")
    if sh < 0 and sl > 0:
        neutral.append("SymmetricalTriangle")
    if sh > 0 and sl > 0 and sl > sh * 1.2:
        bearish.append("RisingWedge")
    if sh < 0 and sl < 0 and abs(sh) > abs(sl) * 1.2:
        bullish.append("FallingWedge")

    # Flags / pennants (approx)
    trend_up = closes[-12] > closes[-20] if n >= 20 else False
    trend_down = closes[-12] < closes[-20] if n >= 20 else False
    tight_last = (max(highs[-8:]) - min(lows[-8:])) / max(last, 1e-9) < 0.018
    if trend_up and tight_last:
        bullish.append("BullFlag")
    if trend_down and tight_last:
        bearish.append("BearFlag")
    if trend_up and sh < 0 and sl > 0:
        bullish.append("BullPennant")
    if trend_down and sh < 0 and sl > 0:
        bearish.append("BearPennant")

    # Cup & handle / rounding bottom
    mid = n // 2
    if n >= 40:
        left = closes[:mid]
        right = closes[mid:]
        if left and right:
            left_drop = (max(left) - min(left)) / max(max(left), 1e-9)
            right_recover = (right[-1] - min(left)) / max(max(left), 1e-9)
            handle_pullback = (max(right[-8:]) - min(right[-8:])) / max(max(right[-8:]), 1e-9)
            if left_drop > 0.02 and right_recover > left_drop * 0.7 and handle_pullback < 0.02:
                bullish.append("CupAndHandle")
    if n >= 30:
        first_half = closes[: n // 2]
        second_half = closes[n // 2 :]
        if first_half and second_half and min(first_half) >= min(closes) and second_half[-1] > second_half[0] and first_half[0] > min(closes):
            bullish.append("RoundingBottom")

    # Rectangle/range
    range_pct = (max(highs[-25:]) - min(lows[-25:])) / max(last, 1e-9)
    if range_pct < 0.03:
        neutral.append("RectangleRange")

    # Breakout + retest (simple)
    prev_hi = max(highs[-30:-5]) if n >= 35 else max(highs[:-3])
    prev_lo = min(lows[-30:-5]) if n >= 35 else min(lows[:-3])
    if last > prev_hi and min(lows[-4:-1]) <= prev_hi * 1.005:
        bullish.append("BreakoutRetestBull")
    if last < prev_lo and max(highs[-4:-1]) >= prev_lo * 0.995:
        bearish.append("BreakoutRetestBear")

    return bullish, bearish, neutral


# ---------------------------------------------------------------------------
# الدالة الرئيسية: فحص آخر الشموع وإرجاع كل الأنماط المكتشفة
# ---------------------------------------------------------------------------

def detect_all(
    candles: Sequence[Any],
    interval: str | None = None,
    sensitivity_profile: str | None = None,
    *,
    pattern_modes: frozenset[str] | None = None,
) -> dict:
    """
    يفحص آخر الشموع ويكتشف كل الأنماط المعروفة.
    المدخل: قائمة شموع (tuple أو dict) بنفس ترتيب Binance: الأقدم أولاً، الأحدث آخراً (candles[-1] = الشمعة الحالية).
    ليست مرتبة «من اليمين لليسار» كعرض الشارت؛ إن بدا التفسير معكوساً فالغالب اختلاف توقع الترتيب وليس عكس المنطق هنا.
    interval: فريم الشارت (مثل 15m، 1h) — يُستخدم لرأس وكتفين الهابط فقط على فواصل ≥15m.
    pattern_modes: compact = شمعة/2/3/5؛ structural = رأس وكتفين + أنماط السعر (أعلام، مثلثات، …).
    المخرج: dict فيه قوائم bullish / bearish / neutral وأسماء الأنماط ونص للعرض.
    """
    result = {
        "bullish": [],   # أسماء أنماط صعودية
        "bearish": [],   # أسماء أنماط هبوطية
        "neutral": [],   # دوجي، قمة دوّارة، إلخ
        "summary": "",   # جملة للواجهة
        "score": 0,      # +1 لكل صعودي، -1 لكل هبوطي (للتوصية)
        "hs_bearish_rebound": False,  # كسر عنق H&S هابط ثم ارتداد فوق العنق (15m+)
    }
    pm = frozenset({"compact", "structural"}) if pattern_modes is None else frozenset(pattern_modes)
    if not pm:
        pm = frozenset({"compact", "structural"})

    if not candles:
        return result

    n = len(candles)
    if n < 2 and "compact" in pm and "structural" not in pm:
        return result

    sens = (str(sensitivity_profile or "balanced").strip().lower() or "balanced")
    if sens == "standard":
        sens = "balanced"
    if sens not in ("conservative", "balanced", "fast"):
        sens = "balanced"
    if sens == "fast":
        doji_body_ratio = 0.10
        hammer_ratio = 1.7
        wick_ratio = 0.12
        tweezer_tol_pct = 0.0035
        star_mid_small_ratio = 0.42
    elif sens == "conservative":
        doji_body_ratio = 0.06
        hammer_ratio = 2.4
        wick_ratio = 0.08
        tweezer_tol_pct = 0.0015
        star_mid_small_ratio = 0.30
    else:
        doji_body_ratio = 0.08
        hammer_ratio = 2.0
        wick_ratio = 0.10
        tweezer_tol_pct = 0.0020
        star_mid_small_ratio = 0.35

    def _append_single_candle_patterns(
        bar: tuple[float, float, float, float],
        candle_before: tuple[float, float, float, float] | None,
    ) -> None:
        """أنماط شمعة واحدة (مطرقة، دوجي، …) مع سياق الشمعة السابقة لمطرقة/مشنوق/نجمة."""
        ctx_bullish = False
        if candle_before is not None:
            _, _, _, _, ctx_bullish = _body_wick(candle_before)
        if is_doji(bar, body_ratio=doji_body_ratio):
            result["neutral"].append("Doji")
        if is_hammer(bar, ratio=hammer_ratio):
            if candle_before is not None and ctx_bullish:
                result["bearish"].append("HangingMan")
            else:
                result["bullish"].append("Hammer")
        inv_or_star_shape = is_inverted_hammer(bar, ratio=hammer_ratio)
        if inv_or_star_shape:
            if candle_before is not None and ctx_bullish:
                result["bearish"].append("ShootingStar")
            else:
                result["bullish"].append("InvertedHammer")
        if is_dragonfly_doji(bar):
            result["bullish"].append("DragonflyDoji")
        if is_gravestone_doji(bar):
            result["bearish"].append("GravestoneDoji")
        if is_marubozu_bullish(bar, wick_ratio=wick_ratio):
            result["bullish"].append("MarubozuBull")
        if is_marubozu_bearish(bar, wick_ratio=wick_ratio):
            result["bearish"].append("MarubozuBear")
        if is_spinning_top(bar):
            result["neutral"].append("SpinningTop")

    if "compact" in pm and n >= 1:
        last = [_get_ohlc(candles[n - 1 - i], n - 1 - i) for i in range(min(3, n))]
        last = [x for x in last if x is not None]
        if last:
            # شمعة واحدة: آخر شمعة في السلسلة (غالباً المفتوحة)
            cur = last[0]
            prev_ohlc = _get_ohlc(candles[n - 2], n - 2) if n >= 2 else None
            _append_single_candle_patterns(cur, prev_ohlc)

            # شمعة واحدة: السابقة إن كانت فترة مغلقة مختلفة عن الأخيرة (بعد فتح شمعة جديدة)
            if n >= 2:
                t_last = _candle_open_ms(candles[-1])
                t_prev = _candle_open_ms(candles[-2])
                if t_last is not None and t_prev is not None and t_last != t_prev:
                    closed_bar = _get_ohlc(candles[n - 2], n - 2)
                    before_closed = _get_ohlc(candles[n - 3], n - 3) if n >= 3 else None
                    if closed_bar is not None:
                        _append_single_candle_patterns(closed_bar, before_closed)

        # شمعتان
        if n >= 2:
            c1, c2 = candles[-2], candles[-1]
            if is_bullish_engulfing(c1, c2):
                result["bullish"].append("BullishEngulfing")
            if is_bearish_engulfing(c1, c2):
                result["bearish"].append("BearishEngulfing")
            if is_bullish_harami(c1, c2):
                result["bullish"].append("BullishHarami")
            if is_bearish_harami(c1, c2):
                result["bearish"].append("BearishHarami")
            if is_piercing_line(c1, c2):
                result["bullish"].append("PiercingLine")
            if is_dark_cloud_cover(c1, c2):
                result["bearish"].append("DarkCloudCover")
            if is_tweezer_bottoms(c1, c2, tol_pct=tweezer_tol_pct):
                result["bullish"].append("TweezerBottoms")
            if is_tweezer_tops(c1, c2, tol_pct=tweezer_tol_pct):
                result["bearish"].append("TweezerTops")

        # ثلاث شموع
        if n >= 3:
            c1, c2, c3 = candles[-3], candles[-2], candles[-1]
            if is_morning_star(c1, c2, c3, mid_body_ratio=star_mid_small_ratio):
                result["bullish"].append("MorningStar")
            if is_evening_star(c1, c2, c3, mid_body_ratio=star_mid_small_ratio):
                result["bearish"].append("EveningStar")
            if is_three_white_soldiers(c1, c2, c3):
                result["bullish"].append("ThreeWhiteSoldiers")
                # اسم متداول عربياً: تسارع/تتابع القمم الخضراء
                result["bullish"].append("GreenPeaksAcceleration")
            if is_three_black_crows(c1, c2, c3):
                result["bearish"].append("ThreeBlackCrows")
            if is_three_inside_up(c1, c2, c3):
                result["bullish"].append("ThreeInsideUp")
            if is_three_inside_down(c1, c2, c3):
                result["bearish"].append("ThreeInsideDown")
            if is_three_outside_up(c1, c2, c3):
                result["bullish"].append("ThreeOutsideUp")
            if is_three_outside_down(c1, c2, c3):
                result["bearish"].append("ThreeOutsideDown")
            if is_abandoned_baby_bull(c1, c2, c3):
                result["bullish"].append("AbandonedBabyBull")
            if is_abandoned_baby_bear(c1, c2, c3):
                result["bearish"].append("AbandonedBabyBear")
            if is_bullish_tasuki_gap(c1, c2, c3):
                result["bullish"].append("BullishTasukiGap")
            if is_bearish_tasuki_gap(c1, c2, c3):
                result["bearish"].append("BearishTasukiGap")
        if n >= 2:
            c1, c2 = candles[-2], candles[-1]
            if is_bullish_kicker(c1, c2):
                result["bullish"].append("BullishKicker")
            if is_bearish_kicker(c1, c2):
                result["bearish"].append("BearishKicker")
        if n >= 5:
            c1, c2, c3, c4, c5 = candles[-5], candles[-4], candles[-3], candles[-2], candles[-1]
            if is_rising_three_methods(c1, c2, c3, c4, c5):
                result["bullish"].append("RisingThreeMethods")
            if is_falling_three_methods(c1, c2, c3, c4, c5):
                result["bearish"].append("FallingThreeMethods")

    if "structural" in pm:
        # أنماط سعرية (ليست شمعة واحدة) على نافذة أوسع
        # HeadAndShoulders (هبوطي): فقط على 15m / 1h / … — على 1m/3m/5m مُعطّل (إيجابيات كاذبة)
        iv = (str(interval).strip().lower() if interval else "") or ""
        if iv in HEAD_SHOULDERS_BEARISH_INTERVALS and has_head_and_shoulders(candles):
            result["bearish"].append("HeadAndShoulders")
        if iv in HEAD_SHOULDERS_BEARISH_INTERVALS and has_head_shoulders_bearish_post_rebound(candles):
            result["hs_bearish_rebound"] = True
        if iv in HEAD_SHOULDERS_BULLISH_INTERVALS and has_inverse_head_and_shoulders(candles):
            result["bullish"].append("InverseHeadAndShoulders")
        cp_bull, cp_bear, cp_neutral = _detect_chart_patterns(candles)
        result["bullish"].extend(cp_bull)
        result["bearish"].extend(cp_bear)
        result["neutral"].extend(cp_neutral)

    # إزالة التكرارات مع الحفاظ على الترتيب
    def _uniq(xs: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in xs:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    result["bullish"] = _uniq(result["bullish"])
    result["bearish"] = _uniq(result["bearish"])
    result["neutral"] = _uniq(result["neutral"])

    result["score"] = len(result["bullish"]) - len(result["bearish"])
    parts = []
    def _names(items: list[str]) -> list[str]:
        return [pattern_to_ar(x) for x in items]

    if result["bullish"]:
        parts.append("صعود: " + "، ".join(_names(result["bullish"])))
    if result["bearish"]:
        parts.append("هبوط: " + "، ".join(_names(result["bearish"])))
    if result["neutral"]:
        parts.append("محايد: " + "، ".join(_names(result["neutral"])))
    result["summary"] = " | ".join(parts) if parts else "—"

    return result


# حد أدنى لسلسلة الفريمات العليا قبل عرض أنماط السعر (يتوافق مع _detect_chart_patterns)
MTF_STRUCTURAL_MIN_CANDLES = 20


def _candle_line_tone_from_pat(p: dict) -> str:
    """
    نبرة سطر واحد للعرض: bull / bear / neutral / mixed.
    mixed = إشارات صعود وهبوط معاً أو تعادل العدد مع وجود الطرفين.
    """
    try:
        sc = int(p.get("score", 0) or 0)
    except (TypeError, ValueError):
        sc = 0
    bull = p.get("bullish") or []
    bear = p.get("bearish") or []
    neu = p.get("neutral") or []
    nb, nk, nn = len(bull), len(bear), len(neu)
    if sc > 0:
        return "bull"
    if sc < 0:
        return "bear"
    if nb > 0 and nk > 0:
        return "mixed"
    if nn > 0 and nb == 0 and nk == 0:
        return "neutral"
    if nb > 0 or nk > 0:
        return "mixed"
    return "neutral"


def _merge_pattern_results(parts: list[dict]) -> dict:
    bull: list[str] = []
    bear: list[str] = []
    neut: list[str] = []
    hs_rb = False
    for p in parts:
        bull.extend(list(p.get("bullish") or []))
        bear.extend(list(p.get("bearish") or []))
        neut.extend(list(p.get("neutral") or []))
        if p.get("hs_bearish_rebound"):
            hs_rb = True

    def _uniq(xs: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in xs:
            if x in seen:
                continue
            seen.add(x)
            out.append(x)
        return out

    bull = _uniq(bull)
    bear = _uniq(bear)
    neut = _uniq(neut)
    return {
        "bullish": bull,
        "bearish": bear,
        "neutral": neut,
        "score": len(bull) - len(bear),
        "hs_bearish_rebound": hs_rb,
    }


def build_multi_timeframe_pattern_report(
    chart_candles: Sequence[Any],
    chart_interval: str,
    *,
    candles_4h: Sequence[Any] | None = None,
    candles_1h: Sequence[Any] | None = None,
    candles_15m: Sequence[Any] | None = None,
    sensitivity_profile: str | None = None,
) -> dict:
    """
    تقرير أربعة أسطر: هيكل ٤س / ١س / ١٥د + أنماط مدمجة (١–٣ شموع و٥) على الشموع المغلقة لفريم الشارت فقط.
    القوائم والدرجة مدمجة من الفروع الأربعة (بدون تكرار اسم النمط).
    """
    lines: list[str] = []
    mtf_rows: list[dict[str, str]] = []
    pat_parts: list[dict] = []
    iv_ch = (str(chart_interval).strip().lower() if chart_interval else "") or ""

    # U+200E (LRM) حول معرّف الفريم اللاتيني حتى لا تختلط الأرقام/الحروف مع العربي (BiDi)
    _iv_tag = "\u200e ({0})\u200e"

    def _struct_line(label: str, series: Sequence[Any] | None, iv: str) -> None:
        tag = _iv_tag.format(iv)
        if not series or len(series) < MTF_STRUCTURAL_MIN_CANDLES:
            txt = f"{label}{tag}: — (بانتظار بيانات، ≥{MTF_STRUCTURAL_MIN_CANDLES} شمعة)"
            lines.append(txt)
            mtf_rows.append({"text": txt, "tone": "neutral"})
            pat_parts.append(
                {"bullish": [], "bearish": [], "neutral": [], "hs_bearish_rebound": False}
            )
            return
        p = detect_all(
            series,
            interval=iv,
            sensitivity_profile=sensitivity_profile,
            pattern_modes=frozenset({"structural"}),
        )
        txt = f"{label}{tag}: {p.get('summary', '—')}"
        lines.append(txt)
        mtf_rows.append({"text": txt, "tone": _candle_line_tone_from_pat(p)})
        pat_parts.append(p)

    _struct_line("٤ ساعات", candles_4h, "4h")
    _struct_line("١ ساعة", candles_1h, "1h")
    _struct_line("١٥ دقيقة", candles_15m, "15m")

    if len(chart_candles) >= 2:
        closed = chart_candles[:-1]
    else:
        closed = chart_candles

    if not closed:
        cpat = {
            "bullish": [],
            "bearish": [],
            "neutral": [],
            "summary": "—",
            "score": 0,
            "hs_bearish_rebound": False,
        }
        txt = f"مغلق\u200e ({iv_ch})\u200e: — (لا شمعة مغلقة بعد)"
        lines.append(txt)
        mtf_rows.append({"text": txt, "tone": "neutral"})
    else:
        cpat = detect_all(
            closed,
            interval=iv_ch,
            sensitivity_profile=sensitivity_profile,
            pattern_modes=frozenset({"compact"}),
        )
        txt = f"مغلق\u200e ({iv_ch})\u200e: {cpat.get('summary', '—')}"
        lines.append(txt)
        mtf_rows.append({"text": txt, "tone": _candle_line_tone_from_pat(cpat)})
    pat_parts.append(cpat)

    merged = _merge_pattern_results(pat_parts)
    merged["summary"] = "\n".join(lines)
    merged["candle_pattern_mtf_rows"] = mtf_rows
    return merged
