# indicator_detail_dialog.py — نافذة تفاصيل المؤشر: شارت حي + نقاط القوة/الضعف + الشراء/البيع
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QLabel, QFrame, QScrollArea, QWidget,
    QPushButton,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from format_utils import format_price
from candlestick_widget import CandlestickChart

# أحجام موحّدة لجميع نوافذ المؤشرات (نفس indicator_chart_widget)
INDICATOR_DIALOG_MIN_SIZE = (820, 680)
INDICATOR_DIALOG_DEFAULT_SIZE = (860, 720)
INDICATOR_CHART_PIXMAP_WIDTH = 780


def _get_indicator_content(key: str, ind: dict) -> dict:
    """يرجع عنوان المؤشر، القيمة الحالية، نقاط القوة/الضعف، متى صعود/هبوط، أماكن شراء/بيع."""
    ind = ind or {}
    close = float(ind.get("close") or 0)

    def val(k, default=0):
        return float(ind.get(k, default) or default)

    content = {
        "title": "",
        "value_text": "",
        "strength_weakness": "",
        "when_up_down": "",
        "buy_sell_zones": "",
    }

    if key == "ai_summary":
        content["title"] = "ملخص الذكاء الاصطناعي"
        content["value_text"] = "التوصية الحالية والتحليل المركب من المؤشرات."
        content["strength_weakness"] = "يجمع بين RSI، MACD، ADX، VWAP، Pivot والدعم/المقاومة لتقديم توصية موحدة."
        content["when_up_down"] = "الصعود عند توافق المؤشرات على الشراء؛ الهبوط عند توافقها على البيع."
        content["buy_sell_zones"] = "مناطق الشراء عند الدعم (S1/S2) مع إشارات صاعدة؛ البيع عند المقاومة (R1/R2) أو عند حد الخسارة."
        return content

    if key == "rsi":
        r = val("rsi", 50)
        content["title"] = "مؤشر RSI (قوة نسبية)"
        content["value_text"] = f"القيمة الحالية: {r:.1f}"
        if r >= 70:
            content["strength_weakness"] = "ضعف: تشبع شراء — السعر قد يصحح هبوطاً."
        elif r <= 30:
            content["strength_weakness"] = "قوة: تشبع بيع — فرصة ارتداد صاعد محتملة."
        else:
            content["strength_weakness"] = "منطقة محايدة — انتظر إشارة أوضح."
        content["when_up_down"] = "صعود محتمل عند RSI < 30 ثم ارتداد. هبوط محتمل عند RSI > 70 ثم تصحيح."
        content["buy_sell_zones"] = "شراء: عند RSI تحت 30 أو خروج من تشبع البيع. بيع: عند RSI فوق 70 أو خروج من تشبع الشراء."
        return content

    if key == "macd":
        m = val("macd")
        sig = val("signal")
        h = val("hist")
        content["title"] = "مؤشر MACD"
        content["value_text"] = f"MACD: {m:.5f} | الإشارة: {sig:.5f} | الهيستوغرام: {h:.5f}"
        if h > 0:
            content["strength_weakness"] = "قوة: زخم صاعد — MACD فوق خط الإشارة."
        elif h < 0:
            content["strength_weakness"] = "ضعف: زخم هابط — MACD تحت خط الإشارة."
        else:
            content["strength_weakness"] = "محايد — انتظر تقاطع."
        content["when_up_down"] = "صعود عند تقاطع MACD من أسفل إلى فوق الإشارة. هبوط عند التقاطع من أعلى إلى تحت."
        content["buy_sell_zones"] = "شراء: عند التقاطع الصاعد. بيع: عند التقاطع الهابط أو عند تشبع مع RSI."
        return content

    if key == "bb":
        u, bb_lower = val("bb_upper"), val("bb_lower")
        content["title"] = "نطاقات بولينجر"
        content["value_text"] = f"الحد الأعلى: {format_price(u)} | الحد الأدنى: {format_price(bb_lower)}"
        if close >= u:
            content["strength_weakness"] = "ضعف: السعر عند أو فوق الحد الأعلى — احتمال تصحيح."
        elif close <= bb_lower:
            content["strength_weakness"] = "قوة: السعر عند أو تحت الحد الأدنى — احتمال ارتداد."
        else:
            content["strength_weakness"] = "السعر داخل النطاق — حركة عادية."
        content["when_up_down"] = "صعود عند لمس الحد الأدنى ثم ارتداد. هبوط عند لمس الحد الأعلى ثم انعكاس."
        content["buy_sell_zones"] = "شراء: عند لمس الحد الأدنى مع إشارة ارتداد. بيع: عند لمس الحد الأعلى أو كسر هابط."
        return content

    if key == "vwap":
        v = val("vwap")
        content["title"] = "VWAP (متوسط السعر المرجح بالحجم)"
        content["value_text"] = f"VWAP: {format_price(v)} | السعر: {format_price(close)}"
        if close > v:
            content["strength_weakness"] = "قوة: السعر فوق VWAP — بيئة شراء."
        elif close < v:
            content["strength_weakness"] = "ضعف: السعر تحت VWAP — ضغط أو فرصة شراء عند القاع."
        else:
            content["strength_weakness"] = "السعر قرب VWAP — محايد."
        content["when_up_down"] = "صعود عندما يثبت السعر فوق VWAP. هبوط عند البقاء تحته."
        content["buy_sell_zones"] = "شراء: عند النزول إلى VWAP كدعم. بيع: عند المقاومة أو بعد صعود قوي فوق VWAP."
        return content

    if key == "adx":
        adx = val("adx14", 0)
        pdi = val("plus_di14", 0)
        mdi = val("minus_di14", 0)
        content["title"] = "ADX (قوة الاتجاه)"
        content["value_text"] = f"ADX: {adx:.1f} | +DI: {pdi:.1f} | -DI: {mdi:.1f}"
        if adx >= 25:
            content["strength_weakness"] = "قوة: اتجاه واضح — ADX فوق 25."
        else:
            content["strength_weakness"] = "ضعف: سوق جانبي — ADX تحت 25، حذر من التقلب."
        content["when_up_down"] = "صعود عندما +DI > -DI والاتجاه قوي. هبوط عندما -DI > +DI."
        content["buy_sell_zones"] = "شراء: عند +DI فوق -DI مع ADX مرتفع. بيع: عند -DI فوق +DI مع ADX مرتفع."
        return content

    if key == "stoch_rsi":
        k = val("stoch_rsi_k", 50)
        d = val("stoch_rsi_d", 50)
        content["title"] = "Stochastic RSI"
        content["value_text"] = f"K: {k:.1f} | D: {d:.1f}"
        if k < 20 and k > d:
            content["strength_weakness"] = "قوة: خروج من تشبع بيع — إشارة صاعدة."
        elif k > 80 and k < d:
            content["strength_weakness"] = "ضعف: خروج من تشبع شراء — إشارة هابطة."
        else:
            content["strength_weakness"] = "منطقة وسطى — انتظر تقاطع أو تشبع."
        content["when_up_down"] = "صعود عند تقاطع K فوق D في منطقة التشبع البيع. هبوط عند تقاطع K تحت D في التشبع الشراء."
        content["buy_sell_zones"] = "شراء: K و D تحت 20 ثم تقاطع صاعد. بيع: K و D فوق 80 ثم تقاطع هابط."
        return content

    if key == "atr":
        a = val("atr14")
        content["title"] = "ATR (متوسط المدى الحقيقي)"
        content["value_text"] = f"ATR(14): {format_price(a)}"
        content["strength_weakness"] = "يقيس التقلب: ATR مرتفع = تقلب كبير؛ منخفض = هدوء."
        content["when_up_down"] = "لا يحدد الاتجاه — يستخدم لوضع وقف الخسارة وأهداف واقعية."
        content["buy_sell_zones"] = "ضع وقف الخسارة بعيداً عن السعر بمقدار 1–2 ATR. الهدف الربح غالباً 1–2 ATR."
        return content

    if key == "cci":
        c = val("cci20", 0)
        content["title"] = "CCI (مؤشر قناة السلع)"
        content["value_text"] = f"CCI(20): {c:.1f}"
        if c > 100:
            content["strength_weakness"] = "ضعف: تشبع شراء."
        elif c < -100:
            content["strength_weakness"] = "قوة: تشبع بيع."
        else:
            content["strength_weakness"] = "منطقة محايدة."
        content["when_up_down"] = "صعود عند خروج CCI من تحت -100. هبوط عند خروجه من فوق +100."
        content["buy_sell_zones"] = "شراء: CCI يعبر -100 للأعلى. بيع: CCI يعبر +100 للأسفل."
        return content

    if key == "supertrend":
        st = val("supertrend")
        dir_ = int(ind.get("supertrend_dir", 0))
        content["title"] = "Supertrend"
        content["value_text"] = f"القيمة: {format_price(st)} | الاتجاه: {'صاعد ↑' if dir_ == 1 else 'هابط ↓'}"
        if dir_ == 1:
            content["strength_weakness"] = "قوة: اتجاه صاعد — السعر فوق خط Supertrend."
        else:
            content["strength_weakness"] = "ضعف: اتجاه هابط — السعر تحت الخط."
        content["when_up_down"] = "صعود عند انعكاس الخط للأعلى. هبوط عند انعكاسه للأسفل."
        content["buy_sell_zones"] = "شراء: عند ظهور الخط تحت السعر (انعكاس صاعد). بيع: عند ظهور الخط فوق السعر."
        return content

    if key == "mfi":
        m = val("mfi", 50)
        content["title"] = "MFI (تدفق الأموال)"
        content["value_text"] = f"MFI: {m:.1f}"
        if m >= 80:
            content["strength_weakness"] = "ضعف: تشبع شراء مرجّح بالحجم."
        elif m <= 20:
            content["strength_weakness"] = "قوة: تشبع بيع — فرصة مع الحجم."
        else:
            content["strength_weakness"] = "منطقة متوسطة."
        content["when_up_down"] = "صعود عند خروج MFI من تحت 20. هبوط عند خروجه من فوق 80."
        content["buy_sell_zones"] = "شراء: MFI تحت 20 ثم ارتداد. بيع: MFI فوق 80 ثم انعكاس."
        return content

    if key == "willr":
        w = val("willr", -50)
        content["title"] = "Williams %R"
        content["value_text"] = f"Williams %R: {w:.1f}"
        if w <= -80:
            content["strength_weakness"] = "قوة: تشبع بيع."
        elif w >= -20:
            content["strength_weakness"] = "ضعف: تشبع شراء."
        else:
            content["strength_weakness"] = "منطقة وسطى."
        content["when_up_down"] = "صعود عند خروج من تحت -80. هبوط عند خروج من فوق -20."
        content["buy_sell_zones"] = "شراء: عند -80 أو أقل ثم ارتداد. بيع: عند -20 أو أعلى ثم انعكاس."
        return content

    if key == "pivot":
        pv = val("pivot")
        r1, r2 = val("pivot_r1"), val("pivot_r2")
        s1, s2 = val("pivot_s1"), val("pivot_s2")
        content["title"] = "نقاط Pivot (الدعم والمقاومة)"
        content["value_text"] = f"المحور: {format_price(pv)} | R1: {format_price(r1)} R2: {format_price(r2)} | S1: {format_price(s1)} S2: {format_price(s2)}"
        content["strength_weakness"] = "المحور ومستويات R/S تعطي مناطق دعم ومقاومة يومية."
        content["when_up_down"] = "صعود عند ارتداد من S1 أو S2. هبوط عند رفض من R1 أو R2."
        content["buy_sell_zones"] = "شراء: عند S1/S2 مع إشارة ارتداد. بيع: عند R1/R2 أو وقف خسارة تحت S1/S2."
        return content

    if key == "ema":
        e9 = val("ema9")
        e21 = val("ema21")
        e50 = val("ema50")
        e200 = val("ema200")
        content["title"] = "المتوسطات الأسية (EMA)"
        content["value_text"] = f"EMA9: {format_price(e9)} | EMA21: {format_price(e21)} | EMA50: {format_price(e50)} | EMA200: {format_price(e200)}"
        content["strength_weakness"] = "ترتيب EMAs يحدد الاتجاه: فوق بعض = صعود، تحت بعض = هبوط."
        content["when_up_down"] = "صعود عندما السعر فوق EMAs قصيرة الأجل. هبوط عند كسر للأسفل."
        content["buy_sell_zones"] = "شراء: عند ارتداد من EMA21 أو EMA50. بيع: عند المقاومة أو كسر EMA للأسفل."
        return content

    if key == "ichimoku":
        t = val("ichimoku_tenkan")
        k = val("ichimoku_kijun")
        content["title"] = "Ichimoku"
        content["value_text"] = f"Tenkan: {format_price(t)} | Kijun: {format_price(k)}"
        content["strength_weakness"] = "سحابة وإشارات تقاطعات — فوق السحابة = صعود، تحتها = هبوط."
        content["when_up_down"] = "صعود عند Tenkan فوق Kijun والسعر فوق السحابة. هبوط في الحالة المعاكسة."
        content["buy_sell_zones"] = "شراء: تقاطع Tenkan فوق Kijun مع سعر فوق السحابة. بيع: العكس."
        return content

    content["title"] = key
    content["value_text"] = "—"
    content["strength_weakness"] = "—"
    content["when_up_down"] = "—"
    content["buy_sell_zones"] = "—"
    return content


class IndicatorDetailDialog(QDialog):
    """نافذة تفاصيل مؤشر: شارت حي يعكس الشموع الحقيقية + القيمة + نقاط القوة/الضعف + الشراء/البيع."""

    def __init__(self, parent=None, indicator_key: str = "", indicators: dict = None, chart_pixmap: QPixmap = None, candles: list = None):
        super().__init__(parent)
        self.setWindowTitle("تفاصيل المؤشر")
        self.setMinimumSize(*INDICATOR_DIALOG_MIN_SIZE)
        self.resize(*INDICATOR_DIALOG_DEFAULT_SIZE)
        self.setStyleSheet("""
            QDialog { background-color: #1e1e22; }
            QLabel { color: #e0e0e0; font-size: 13px; }
            QFrame#section { background-color: #252530; border-radius: 6px; padding: 8px; margin: 4px 0; }
            QPushButton { background-color: #0d7dd6; color: white; border: none; padding: 8px 16px; border-radius: 6px; }
            QPushButton:hover { background-color: #0a6bb8; }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(16, 16, 16, 16)

        self._candle_chart = None
        # شارت حي يعكس الشموع الحقيقية — يُحدَّث عند وصول بيانات جديدة
        if candles is not None and len(candles) > 0:
            self._candle_chart = CandlestickChart()
            self._candle_chart.setMinimumHeight(320)
            self._candle_chart.setCandles(candles)
            self._candle_chart.setStyleSheet("background-color: #1a1d24; border-radius: 6px;")
            layout.addWidget(self._candle_chart)
        elif chart_pixmap and not chart_pixmap.isNull():
            chart_label = QLabel()
            scaled = chart_pixmap.scaledToWidth(INDICATOR_CHART_PIXMAP_WIDTH, Qt.TransformationMode.SmoothTransformation)
            chart_label.setPixmap(scaled)
            chart_label.setMinimumSize(scaled.size())
            chart_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            chart_label.setStyleSheet("background-color: #1a1d24; border-radius: 6px;")
            layout.addWidget(chart_label)

        c = _get_indicator_content(indicator_key, indicators or {})

        title = QLabel(c["title"])
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #fff;")
        layout.addWidget(title)

        value_label = QLabel(c["value_text"])
        value_label.setStyleSheet("color: #87CEEB; font-size: 14px;")
        layout.addWidget(value_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        inner = QWidget()
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 0)

        def add_section(label_text: str, body: str):
            fr = QFrame()
            fr.setObjectName("section")
            fr_lay = QVBoxLayout(fr)
            lbl = QLabel(label_text)
            lbl.setStyleSheet("font-weight: bold; color: #aaa; font-size: 12px;")
            fr_lay.addWidget(lbl)
            body_lbl = QLabel(body)
            body_lbl.setWordWrap(True)
            body_lbl.setStyleSheet("color: #ccc;")
            fr_lay.addWidget(body_lbl)
            inner_layout.addWidget(fr)

        add_section("نقاط القوة والضعف", c["strength_weakness"])
        add_section("متى الصعود والهبوط", c["when_up_down"])
        add_section("أماكن الشراء والبيع", c["buy_sell_zones"])

        inner_layout.addStretch()
        scroll.setWidget(inner)
        layout.addWidget(scroll, 1)

        close_btn = QPushButton("إغلاق")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def update_candles(self, candles: list):
        """تحديث الشارت الحي بالشموع الجديدة (يُستدعى عند وصول بيانات حية)."""
        if self._candle_chart and candles:
            self._candle_chart.setCandles(candles)
