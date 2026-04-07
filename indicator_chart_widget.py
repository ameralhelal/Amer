# indicator_chart_widget.py — شارت المؤشر: رسم خط المؤشر + خطوط مناطق الشراء والبيع
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QDialog, QPushButton
from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import QPainter, QColor, QPen, QFont
import numpy as np

try:
    import pandas as pd
except ImportError:
    pd = None


def _candles_to_arrays(candles):
    """تحويل قائمة شموع (dict أو tuple) إلى مصفوفات."""
    if not candles:
        return None, None, None, None, None
    opens, highs, lows, closes, volumes = [], [], [], [], []
    for c in candles:
        if isinstance(c, dict):
            o = float(c.get("open", 0))
            h = float(c.get("high", 0))
            l_ = float(c.get("low", 0))
            cl = float(c.get("close", 0))
            v = float(c.get("volume", 0))
        else:
            o, h, l_, cl, v = float(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])
        opens.append(o)
        highs.append(h)
        lows.append(l_)
        closes.append(cl)
        volumes.append(v)
    return (np.array(opens), np.array(highs), np.array(lows), np.array(closes), np.array(volumes))


def compute_rsi_series(candles, period=5):
    """سلسلة RSI لكل شمعة (نفس منطق websocket_manager)."""
    o, h, l_, closes, v = _candles_to_arrays(candles)
    if closes is None or len(closes) < period + 1 or pd is None:
        return []
    delta = np.diff(closes)
    gain = np.where(delta > 0, delta, 0)
    loss = np.where(delta < 0, -delta, 0)
    out = [50.0] * (period + 1)
    for i in range(period + 1, len(closes)):
        avg_g = np.mean(gain[i - period:i])
        avg_l = np.mean(loss[i - period:i]) + 1e-9
        rs = avg_g / avg_l
        out.append(100 - (100 / (1 + rs)))
    return out


def compute_macd_series(candles, fast=6, slow=13, sig=5):
    """سلسلة MACD و Signal و Histogram."""
    o, h, l_, closes, v = _candles_to_arrays(candles)
    if closes is None or len(closes) < slow + 1 or pd is None:
        return [], [], []
    s = pd.Series(closes)
    ema_f = s.ewm(span=fast, adjust=False).mean()
    ema_s = s.ewm(span=slow, adjust=False).mean()
    macd_line = ema_f - ema_s
    signal_line = macd_line.ewm(span=sig, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line.tolist(), signal_line.tolist(), hist.tolist()


def compute_bb_series(candles, period=20, mult=2):
    """سلسلة بولينجر: أعلى، وسط، أدنى (طول كل سلسلة = عدد الشموع)."""
    o, h, l_, closes, v = _candles_to_arrays(candles)
    if closes is None or len(closes) < period:
        return [], [], []
    n = len(closes)
    ma = np.convolve(closes, np.ones(period) / period, mode="valid")
    std = np.array([np.std(closes[i - period:i]) for i in range(period, n + 1)])
    upper = ma + mult * std
    lower = ma - mult * std
    prefix_len = period - 1
    prefix = [float(closes[0])] * prefix_len
    ma_full = list(prefix) + list(ma)
    up_full = list(prefix) + list(upper)
    low_full = list(prefix) + list(lower)
    return up_full[:n], ma_full[:n], low_full[:n]


def compute_vwap_series(candles):
    """سلسلة VWAP التراكمي عند كل شمعة."""
    o, h, l_, closes, volumes = _candles_to_arrays(candles)
    if closes is None or len(closes) == 0:
        return []
    typical = (o + h + l_) / 3.0
    cum_tpv = np.cumsum(typical * volumes)
    cum_vol = np.cumsum(volumes)
    vwap = np.where(cum_vol > 1e-9, cum_tpv / cum_vol, typical)
    return vwap.tolist()


def compute_atr_series(candles, period=7):
    """سلسلة ATR."""
    o, highs, lows, closes, v = _candles_to_arrays(candles)
    if closes is None or len(closes) < 2 or pd is None:
        return []
    prev = np.roll(closes, 1)
    prev[0] = closes[0]
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - prev), np.abs(lows - prev)))
    atr = pd.Series(tr).ewm(span=period, adjust=False).mean()
    return atr.tolist()


def compute_adx_series(candles, period=None):
    """سلسلة ADX و +DI و -DI — نفس تنعيم Wilder (alpha) المستخدم في websocket_manager."""
    try:
        from config import load_config, DEFAULTS

        cfg = load_config()
        if period is None:
            period = max(2, int(cfg.get("dmi_adx_period", DEFAULTS.get("dmi_adx_period", 14)) or 14))
    except Exception:
        period = period if period is not None else 14
    o, highs, lows, closes, v = _candles_to_arrays(candles)
    if closes is None or len(closes) < period + 2 or pd is None:
        return [], [], []
    up = highs[1:] - highs[:-1]
    down = lows[:-1] - lows[1:]
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr1 = highs[1:] - lows[1:]
    tr2 = np.abs(highs[1:] - closes[:-1])
    tr3 = np.abs(lows[1:] - closes[:-1])
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    _a = 1.0 / float(period)
    tr_s = pd.Series(tr).ewm(alpha=_a, adjust=False).mean()
    plus_s = pd.Series(plus_dm).ewm(alpha=_a, adjust=False).mean()
    minus_s = pd.Series(minus_dm).ewm(alpha=_a, adjust=False).mean()
    plus_di = (100 * plus_s / (tr_s + 1e-9)).tolist()
    minus_di = (100 * minus_s / (tr_s + 1e-9)).tolist()
    p = np.array(plus_di, dtype=float)
    m = np.array(minus_di, dtype=float)
    dx = 100.0 * np.abs(p - m) / (p + m + 1e-9)
    dx = np.nan_to_num(dx, nan=0.0, posinf=0.0, neginf=0.0)
    adx_s = pd.Series(dx).ewm(alpha=_a, adjust=False).mean()
    adx = [None] + adx_s.tolist()
    return adx, [None] + plus_di, [None] + minus_di


def compute_stoch_rsi_series(candles, period=5):
    """سلسلة Stoch RSI K و D."""
    o, h, l_, closes, v = _candles_to_arrays(candles)
    if closes is None or len(closes) < period + 5 or pd is None:
        return [], []
    delta = pd.Series(closes).diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_g = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_l = loss.ewm(alpha=1/period, adjust=False).mean() + 1e-9
    rs = avg_g / avg_l
    rsi = 100 - (100 / (1 + rs))
    rsi_min = rsi.rolling(period).min()
    rsi_max = rsi.rolling(period).max()
    stoch = ((rsi - rsi_min) / (rsi_max - rsi_min + 1e-9)) * 100
    k = stoch.rolling(3).mean().tolist()
    d = stoch.rolling(3).mean().rolling(3).mean().tolist()
    return k, d


def compute_cci_series(candles, period=20):
    """سلسلة CCI."""
    o, highs, lows, closes, v = _candles_to_arrays(candles)
    if closes is None or len(closes) < period or pd is None:
        return []
    tp = (highs + lows + closes) / 3.0
    tp_s = pd.Series(tp)
    sma = tp_s.rolling(period).mean()
    mad = (tp_s - sma).abs().rolling(period).mean()
    cci = ((tp_s - sma) / (0.015 * (mad + 1e-9))).tolist()
    return cci


def compute_mfi_series(candles, period=14):
    """سلسلة MFI."""
    o, h, l_, closes, volumes = _candles_to_arrays(candles)
    if closes is None or len(closes) < period + 1:
        return []
    typical = (o + h + l_) / 3.0
    raw = typical * volumes
    out = [50.0] * (period + 1)
    for i in range(period + 1, len(closes)):
        pos = sum(raw[j] for j in range(i - period, i) if typical[j] > typical[j - 1])
        neg = sum(raw[j] for j in range(i - period, i) if typical[j] < typical[j - 1])
        if neg > 1e-9:
            out.append(100.0 - (100.0 / (1.0 + pos / neg)))
        else:
            out.append(100.0 if pos > 0 else 50.0)
    return out


def compute_willr_series(candles, period=14):
    """سلسلة Williams %R."""
    o, highs, lows, closes, v = _candles_to_arrays(candles)
    if closes is None or len(closes) < period:
        return []
    out = []
    for i in range(len(closes)):
        if i < period - 1:
            out.append(-50.0)
            continue
        hh = np.max(highs[i - period + 1:i + 1])
        ll = np.min(lows[i - period + 1:i + 1])
        rng = hh - ll
        if rng > 1e-9:
            out.append(-100.0 * (hh - closes[i]) / rng)
        else:
            out.append(-50.0)
    return out


def compute_supertrend_series(candles, period=None, mult=None):
    """سلسلة Supertrend — نفس منطق websocket_manager (بدون مقارنة float بالمساواة)."""
    try:
        from config import load_config, DEFAULTS

        cfg = load_config()
        if period is None:
            period = max(2, int(cfg.get("supertrend_atr_period", DEFAULTS.get("supertrend_atr_period", 7)) or 7))
        if mult is None:
            mult = max(0.5, float(cfg.get("supertrend_multiplier", DEFAULTS.get("supertrend_multiplier", 2.0)) or 2.0))
    except Exception:
        period = period if period is not None else 7
        mult = mult if mult is not None else 2.0
    o, highs, lows, closes, v = _candles_to_arrays(candles)
    if closes is None or len(closes) < period + 1 or pd is None:
        return []
    hl2 = (highs + lows) / 2.0
    tr = np.maximum(highs - lows, np.maximum(np.abs(highs - np.roll(closes, 1)), np.abs(lows - np.roll(closes, 1))))
    tr[0] = highs[0] - lows[0]
    atr = pd.Series(tr).ewm(alpha=1.0 / float(period), adjust=False).mean().values
    basic_upper = hl2 + mult * atr
    basic_lower = hl2 - mult * atr
    n = len(closes)
    final_upper = np.zeros(n)
    final_lower = np.zeros(n)
    supertrend = np.zeros(n)
    final_upper[0] = basic_upper[0]
    final_lower[0] = basic_lower[0]
    supertrend[0] = basic_upper[0]
    prev_on_upper = True
    for i in range(1, n):
        if basic_upper[i] < final_upper[i - 1] or closes[i - 1] > final_upper[i - 1]:
            final_upper[i] = basic_upper[i]
        else:
            final_upper[i] = final_upper[i - 1]
        if basic_lower[i] > final_lower[i - 1] or closes[i - 1] < final_lower[i - 1]:
            final_lower[i] = basic_lower[i]
        else:
            final_lower[i] = final_lower[i - 1]
        if prev_on_upper:
            if closes[i] <= final_upper[i]:
                supertrend[i] = final_upper[i]
                prev_on_upper = True
            else:
                supertrend[i] = final_lower[i]
                prev_on_upper = False
        else:
            if closes[i] >= final_lower[i]:
                supertrend[i] = final_lower[i]
                prev_on_upper = False
            else:
                supertrend[i] = final_upper[i]
                prev_on_upper = True
    return supertrend.tolist()


def compute_pivot_series(candles):
    """خطوط Pivot ثابتة (نفس القيمة لكل الشموع من الشمعة السابقة)."""
    o, highs, lows, closes, v = _candles_to_arrays(candles)
    if closes is None or len(closes) < 2:
        return [], [], [], [], [], []
    ph = float(highs[-2])
    pl = float(lows[-2])
    pc = float(closes[-2])
    pivot = (ph + pl + pc) / 3.0
    r1 = 2 * pivot - pl
    r2 = pivot + (ph - pl)
    s1 = 2 * pivot - ph
    s2 = pivot - (ph - pl)
    n = len(closes)
    return [pivot]*n, [r1]*n, [r2]*n, [s1]*n, [s2]*n, [pc]*n


def compute_ema_series(candles):
    """سلاسل EMA 9, 21, 50, 200."""
    o, h, l_, closes, v = _candles_to_arrays(candles)
    if closes is None or len(closes) == 0 or pd is None:
        return [], [], [], []
    s = pd.Series(closes)
    e9 = s.ewm(span=9, adjust=False).mean().tolist() if len(closes) >= 9 else []
    e21 = s.ewm(span=21, adjust=False).mean().tolist() if len(closes) >= 21 else []
    e50 = s.ewm(span=50, adjust=False).mean().tolist() if len(closes) >= 50 else []
    e200 = s.ewm(span=200, adjust=False).mean().tolist() if len(closes) >= 200 else []
    n = len(closes)
    if len(e9) < n:
        e9 = [closes[0]] * (n - len(e9)) + e9
    if len(e21) < n:
        e21 = [closes[0]] * (n - len(e21)) + e21
    if len(e50) < n:
        e50 = [closes[0]] * (n - len(e50)) + e50
    if len(e200) < n:
        e200 = [closes[0]] * (n - len(e200)) + e200
    return e9[:n], e21[:n], e50[:n], e200[:n]


def compute_ichimoku_series(candles):
    """سلاسل Ichimoku: Tenkan, Kijun, Senkou A, Senkou B."""
    o, highs, lows, closes, v = _candles_to_arrays(candles)
    if closes is None or len(closes) < 52:
        return [], [], [], []
    n = len(closes)
    tenkan = []
    kijun = []
    for i in range(n):
        if i < 8:
            tenkan.append(closes[i])
        else:
            tenkan.append((np.max(highs[i-9:i+1]) + np.min(lows[i-9:i+1])) / 2.0)
        if i < 25:
            kijun.append(closes[i])
        else:
            kijun.append((np.max(highs[i-26:i+1]) + np.min(lows[i-26:i+1])) / 2.0)
    senkou_a = [None] * 26 + [(tenkan[i] + kijun[i]) / 2.0 for i in range(26, n)]
    if len(senkou_a) < n:
        senkou_a += [senkou_a[-1] if senkou_a else 0] * (n - len(senkou_a))
    senkou_b = []
    for i in range(n):
        if i < 51:
            senkou_b.append(closes[i])
        else:
            senkou_b.append((np.max(highs[i-52:i+1]) + np.min(lows[i-52:i+1])) / 2.0)
    senkou_b = [None] * 26 + senkou_b[26:]
    if len(senkou_b) < n:
        senkou_b += [senkou_b[-1] if senkou_b else 0] * (n - len(senkou_b))
    return tenkan, kijun, senkou_a[:n], senkou_b[:n]


# لكل مؤشر: عنوان، خطوط شراء/بيع، نطاق Y
INDICATOR_CONFIG = {
    "rsi": {
        "title": "RSI",
        "levels": [(30, "منطقة شراء", QColor(0, 180, 100)), (70, "منطقة بيع", QColor(200, 80, 80))],
        "y_range": (0, 100),
    },
    "macd": {
        "title": "MACD",
        "levels": [(0, "خط الصفر", QColor(120, 120, 120))],
        "y_range": None,
    },
    "bb": {
        "title": "نطاقات بولينجر",
        "levels": [],
        "y_range": None,
    },
    "vwap": {"title": "VWAP", "levels": [], "y_range": None},
    "atr": {"title": "ATR", "levels": [], "y_range": None},
    "adx": {"title": "ADX", "levels": [(25, "اتجاه قوي", QColor(0, 180, 100))], "y_range": (0, 60)},
    "stoch_rsi": {
        "title": "Stoch RSI",
        "levels": [(20, "شراء", QColor(0, 180, 100)), (80, "بيع", QColor(200, 80, 80))],
        "y_range": (0, 100),
    },
    "cci": {
        "title": "CCI",
        "levels": [(-100, "شراء", QColor(0, 180, 100)), (100, "بيع", QColor(200, 80, 80))],
        "y_range": None,
    },
    "mfi": {
        "title": "MFI",
        "levels": [(20, "شراء", QColor(0, 180, 100)), (80, "بيع", QColor(200, 80, 80))],
        "y_range": (0, 100),
    },
    "willr": {
        "title": "Williams %R",
        "levels": [(-80, "شراء", QColor(0, 180, 100)), (-20, "بيع", QColor(200, 80, 80))],
        "y_range": (-100, 0),
    },
    "supertrend": {"title": "Supertrend", "levels": [], "y_range": None},
    "pivot": {"title": "Pivot", "levels": [], "y_range": None},
    "ema": {"title": "EMAs", "levels": [], "y_range": None},
    "ichimoku": {"title": "Ichimoku", "levels": [], "y_range": None},
}


class IndicatorChartWidget(QWidget):
    """ويدجت ترسم سلسلة المؤشر مع خطوط الشراء/البيع."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(720, 380)
        self.setStyleSheet("background-color: #1a1d24;")
        self._indicator_key = ""
        self._candles = []
        self._series = []
        self._series_extra = []
        self._series_lower = []
        self._multi_series = []  # [(series, color), ...] لمؤشرات متعددة الخطوط
        self._levels = []
        self._y_range = None
        self._title = ""

    def set_indicator(self, key: str, candles: list):
        """ضبط المؤشر والبيانات ثم إعادة الرسم."""
        self._indicator_key = key or ""
        self._candles = list(candles) if candles else []
        self._series = []
        self._series_extra = []
        self._series_lower = []
        self._multi_series = []
        self._levels = []
        self._y_range = None
        self._title = INDICATOR_CONFIG.get(key, {}).get("title", key)

        if not self._candles:
            self.update()
            return

        cfg = INDICATOR_CONFIG.get(key, {})
        self._levels = list(cfg.get("levels", []))
        self._y_range = cfg.get("y_range")

        if key == "rsi":
            self._series = compute_rsi_series(self._candles)
        elif key == "macd":
            macd_l, sig_l, hist_l = compute_macd_series(self._candles)
            self._series = macd_l
            self._series_extra = sig_l
            self._levels = [(0, "خط الصفر", QColor(120, 120, 120))]
        elif key == "bb":
            up, mid, low = compute_bb_series(self._candles)
            self._series = mid
            self._series_extra = up
            self._series_lower = low
            self._y_range = (float(np.nanmin(low)) if len(low) else 0, float(np.nanmax(up)) if len(up) else 1)
        elif key == "vwap":
            self._series = compute_vwap_series(self._candles)
        elif key == "atr":
            self._series = compute_atr_series(self._candles)
        elif key == "adx":
            adx_l, pdi, mdi = compute_adx_series(self._candles)
            self._series = adx_l
            self._multi_series = [(pdi, QColor(0, 200, 100)), (mdi, QColor(200, 80, 80))]
        elif key == "stoch_rsi":
            k, d = compute_stoch_rsi_series(self._candles)
            self._series = k
            self._series_extra = d
        elif key == "cci":
            self._series = compute_cci_series(self._candles)
        elif key == "mfi":
            self._series = compute_mfi_series(self._candles)
        elif key == "willr":
            self._series = compute_willr_series(self._candles)
        elif key == "supertrend":
            self._series = compute_supertrend_series(self._candles)
        elif key == "pivot":
            pv, r1, r2, s1, s2, pc = compute_pivot_series(self._candles)
            self._series = pv
            self._multi_series = [
                (r1, QColor(220, 100, 100)),
                (r2, QColor(200, 60, 60)),
                (s1, QColor(100, 220, 120)),
                (s2, QColor(60, 180, 80)),
            ]
        elif key == "ema":
            e9, e21, e50, e200 = compute_ema_series(self._candles)
            self._series = e9
            self._multi_series = [
                (e21, QColor(255, 180, 80)),
                (e50, QColor(100, 180, 255)),
                (e200, QColor(180, 100, 255)),
            ]
        elif key == "ichimoku":
            t, k, sa, sb = compute_ichimoku_series(self._candles)
            self._series = t
            self._multi_series = [
                (k, QColor(255, 100, 100)),
                (sa, QColor(80, 200, 80)),
                (sb, QColor(200, 80, 80)),
            ]
        elif key == "pivot":
            o, h, l_, closes, v = _candles_to_arrays(self._candles)
            if closes is not None and len(closes) > 0:
                self._y_range = (float(np.min(closes)) * 0.998, float(np.max(closes)) * 1.002)

        if self._y_range is None and self._series:
            valid = [float(x) for x in self._series if x is not None and not (isinstance(x, float) and np.isnan(x))]
            if self._series_extra:
                valid.extend([float(x) for x in self._series_extra if x is not None and not (isinstance(x, float) and np.isnan(x))])
            for s, _ in self._multi_series:
                if s:
                    valid.extend([float(x) for x in s if x is not None and not (isinstance(x, float) and np.isnan(x))])
            if valid:
                mn, mx = min(valid), max(valid)
                pad = (mx - mn) * 0.1 if (mx - mn) > 0 else 1
                self._y_range = (mn - pad, mx + pad)
            else:
                self._y_range = (0, 100)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._series:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        left, right = 40, w - 16
        top, bottom = 28, h - 24
        chart_w = max(1, right - left)
        chart_h = max(1, bottom - top)

        # عنوان
        painter.setPen(QColor(230, 230, 230))
        f = QFont()
        f.setPointSize(11)
        painter.setFont(f)
        painter.drawText(QRectF(0, 2, w, 22), Qt.AlignmentFlag.AlignCenter, f"شارت {self._title} — مناطق الشراء والبيع")

        y_min, y_max = self._y_range or (0, 100)
        if y_max <= y_min:
            y_max = y_min + 1

        # رسم خطوط المستويات (شراء/بيع)
        for val, label, color in self._levels:
            y = bottom - (float(val) - y_min) / (y_max - y_min) * chart_h
            if top <= y <= bottom:
                painter.setPen(QPen(color, 1, Qt.PenStyle.DashLine))
                painter.drawLine(int(left), int(y), int(right), int(y))
                painter.setPen(color)
                painter.drawText(int(left) - 36, int(y) - 6, 34, 14, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, str(val))

        # رسم السلسلة
        n = len(self._series)
        if n < 2:
            return
        step = chart_w / max(1, n - 1)
        points = []
        for i, v in enumerate(self._series):
            if v is None or (isinstance(v, float) and np.isnan(v)):
                continue
            x = left + i * step
            y = bottom - (float(v) - y_min) / (y_max - y_min) * chart_h
            points.append((x, y))

        if len(points) < 2:
            return
        color_line = QColor(0, 180, 220)
        painter.setPen(QPen(color_line, 2, Qt.PenStyle.SolidLine))
        for j in range(len(points) - 1):
            painter.drawLine(int(points[j][0]), int(points[j][1]), int(points[j + 1][0]), int(points[j + 1][1]))

        # خط الإشارة للمACD (إن وُجد)
        if self._indicator_key == "macd" and self._series_extra and len(self._series_extra) == len(self._series):
            sig_vals = [float(x) for x in self._series_extra if x is not None and not (isinstance(x, float) and np.isnan(x))]
            sig_min = min(sig_vals) if sig_vals else y_min
            sig_max = max(sig_vals) if sig_vals else y_max
            y_min2 = sig_min
            y_max2 = sig_max if sig_max > sig_min else sig_min + 1
            points2 = []
            for i, v in enumerate(self._series_extra):
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    continue
                x = left + i * step
                y = bottom - (float(v) - y_min2) / (y_max2 - y_min2 or 1) * chart_h
                points2.append((x, y))
            if len(points2) >= 2:
                painter.setPen(QPen(QColor(255, 180, 80), 1, Qt.PenStyle.SolidLine))
                for j in range(len(points2) - 1):
                    painter.drawLine(int(points2[j][0]), int(points2[j][1]), int(points2[j + 1][0]), int(points2[j + 1][1]))

        # خطوط إضافية (_multi_series) — ADX +DI/-DI، Pivot R/S، EMAs، Ichimoku
        for series, color in getattr(self, "_multi_series", []):
            if not series or len(series) != n:
                continue
            pts = []
            for i, v in enumerate(series):
                if v is None or (isinstance(v, float) and np.isnan(v)):
                    continue
                x = left + i * step
                y = bottom - (float(v) - y_min) / (y_max - y_min) * chart_h
                pts.append((x, y))
            if len(pts) >= 2:
                painter.setPen(QPen(color, 1, Qt.PenStyle.SolidLine))
                for j in range(len(pts) - 1):
                    painter.drawLine(int(pts[j][0]), int(pts[j][1]), int(pts[j + 1][0]), int(pts[j + 1][1]))

        # نطاقات بولينجر: ثلاثة خطوط (أعلى، وسط، أدنى) — منطقة شراء قرب الأدنى، بيع قرب الأعلى
        if self._indicator_key == "bb" and self._series and self._series_extra and getattr(self, "_series_lower", []):
            for series, color in [
                (self._series_extra, QColor(220, 100, 100)),
                (self._series, QColor(100, 180, 220)),
                (self._series_lower, QColor(100, 220, 120)),
            ]:
                if len(series) != n:
                    continue
                pts = []
                for i, v in enumerate(series):
                    if v is None or (isinstance(v, float) and np.isnan(v)):
                        continue
                    x = left + i * step
                    y = bottom - (float(v) - y_min) / (y_max - y_min) * chart_h
                    pts.append((x, y))
                if len(pts) >= 2:
                    painter.setPen(QPen(color, 1, Qt.PenStyle.SolidLine))
                    for j in range(len(pts) - 1):
                        painter.drawLine(int(pts[j][0]), int(pts[j][1]), int(pts[j + 1][0]), int(pts[j + 1][1]))

        painter.end()


# أحجام موحّدة لجميع نوافذ المؤشرات — أكبر درجة للوضوح
INDICATOR_DIALOG_MIN_SIZE = (820, 680)
INDICATOR_DIALOG_DEFAULT_SIZE = (860, 720)


class IndicatorChartDialog(QDialog):
    """نافذة شارت المؤشر: تعرض خط المؤشر وخطوط الشراء/البيع — مؤشر حي يعكس الشموع الحقيقية."""
    def __init__(self, parent=None, indicator_key: str = "", candles: list = None):
        super().__init__(parent)
        self._indicator_key = indicator_key or ""
        self.setWindowTitle(f"شارت المؤشر — {INDICATOR_CONFIG.get(indicator_key, {}).get('title', indicator_key)}")
        self.setMinimumSize(*INDICATOR_DIALOG_MIN_SIZE)
        self.resize(*INDICATOR_DIALOG_DEFAULT_SIZE)
        self.setStyleSheet("QDialog { background-color: #1a1d24; }")
        layout = QVBoxLayout(self)
        self.chart = IndicatorChartWidget(self)
        self.chart.set_indicator(self._indicator_key, candles or [])
        layout.addWidget(self.chart, 1)
        btn = QPushButton("إغلاق")
        btn.clicked.connect(self.accept)
        layout.addWidget(btn)

    def update_candles(self, candles: list):
        """تحديث شارت المؤشر بالشموع الجديدة (مؤشر حي يعكس حالة الشموع الحقيقية)."""
        if candles is not None and self._indicator_key:
            self.chart.set_indicator(self._indicator_key, candles)
