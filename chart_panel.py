import logging
from datetime import datetime, timezone

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QButtonGroup, QLabel
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QWheelEvent

from candlestick_widget import CandlestickChart
from config import load_config, save_config
from translations import get_language, tr

log = logging.getLogger("trading.chart")

# ثوانٍ حتى إغلاق الشمعة التالية حسب الإطار (Binance UTC)
INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def _seconds_until_next_candle(interval: str) -> int:
    """الوقت المتبقي حتى إغلاق الشمعة الحالية (بالثواني)."""
    sec = INTERVAL_SECONDS.get(interval, 60)
    now = datetime.now(timezone.utc)
    ts = now.timestamp()
    return int(sec - (ts % sec))


class ChartPanel(QWidget):
    def __init__(self):
        super().__init__()

        self.current_symbol = "BTCUSDT"
        self._last_close = None
        self._live_price = None
        self._chart_interval = "1m"

        self.setObjectName("ChartPanel")

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(8)

        # عمود اليسار: نوع الشارت ثم أدوات الرسم
        draw_col = QVBoxLayout()
        draw_col.setSpacing(2)
        _ar = get_language() == "ar"
        _btn_style = "font-size: 10px; min-width: 26px; max-width: 26px; min-height: 22px; max-height: 22px; padding: 0;"
        chart_type_label = QLabel(tr("chart_type"))
        chart_type_label.setStyleSheet("color: #aaa; font-size: 9px;")
        draw_col.addWidget(chart_type_label)
        self._chart_type_group = QButtonGroup(self)
        self._chart_type_group.setExclusive(True)
        _chart_btns = []
        for key, symbol, tip_ar, tip_en in [
            ("candle", "🕯", "شموع يابانية", "Candles"),
            ("heikin_ashi", "⬛", "هايكين آشي", "Heikin Ashi"),
            ("line", "〰", "خط الإغلاق", "Line (Close)"),
            ("area", "▀", "منطقة تحت السعر", "Area"),
            ("hollow", "▢", "شموع مجوفة", "Hollow candles"),
        ]:
            b = QPushButton(symbol)
            b.setStyleSheet(_btn_style)
            b.setFixedSize(26, 22)
            b.setCheckable(True)
            b.setToolTip(tip_ar if _ar else tip_en)
            b.setProperty("chart_type", key)
            self._chart_type_group.addButton(b)
            draw_col.addWidget(b)
            _chart_btns.append((key, b))
        self._chart_type_buttons = dict(_chart_btns)
        self._chart_type_buttons["candle"].setChecked(True)
        draw_col.addSpacing(6)
        draw_tools_label = QLabel(tr("draw_tools"))
        draw_tools_label.setStyleSheet("color: #aaa; font-size: 9px;")
        draw_col.addWidget(draw_tools_label)

        # زر إظهار/إخفاء الدعم والمقاومة (Pivot/S/R)
        self._btn_toggle_sr = QPushButton("SR")
        self._btn_toggle_sr.setStyleSheet(_btn_style)
        self._btn_toggle_sr.setFixedSize(26, 22)
        self._btn_toggle_sr.setCheckable(True)
        self._btn_toggle_sr.setToolTip("إظهار/إخفاء الدعم والمقاومة" if _ar else "Show/Hide support & resistance")
        draw_col.addWidget(self._btn_toggle_sr)
        self._draw_btn_group = QButtonGroup(self)
        self._draw_btn_group.setExclusive(True)
        self._btn_draw_hline = QPushButton("―")
        self._btn_draw_line = QPushButton("∕")
        self._btn_draw_channel = QPushButton("∥")
        self._btn_draw_fib = QPushButton("%")
        self._btn_draw_rect = QPushButton("▭")
        self._btn_draw_pan = QPushButton("✋")
        self._btn_draw_pan.setCheckable(True)
        self._btn_draw_pan.setChecked(True)
        self._btn_draw_clear = QPushButton("✕")
        for btn in (self._btn_draw_hline, self._btn_draw_line, self._btn_draw_channel, self._btn_draw_fib, self._btn_draw_rect, self._btn_draw_pan, self._btn_draw_clear):
            btn.setStyleSheet(_btn_style)
            btn.setFixedSize(26, 22)
        for btn in (self._btn_draw_hline, self._btn_draw_line, self._btn_draw_channel, self._btn_draw_fib, self._btn_draw_rect, self._btn_draw_pan):
            btn.setCheckable(True)
            self._draw_btn_group.addButton(btn)
        self._btn_draw_hline.setToolTip("خط أفقي — انقر مرة على الشارت لرسم خط عند السعر" if _ar else "Horizontal line — click once on chart to draw at price")
        self._btn_draw_line.setToolTip("خط مستقيم — اسحب من نقطة إلى نقطة" if _ar else "Straight line — drag from point to point")
        self._btn_draw_channel.setToolTip("قناة تلقائية من الشموع — للإخفاء اضغط تحريك (✋)" if _ar else "Auto channel from candles — click Pan (✋) to hide")
        self._btn_draw_fib.setToolTip(("فيبوناتشي تلقائي من الشموع — للإخفاء اضغط تحريك (✋)" if _ar else "Auto Fibonacci from candles — click Pan (✋) to hide"))
        self._btn_draw_rect.setToolTip("مستطيل منطقة — اسحب لرسم منطقة دعم/مقاومة" if _ar else "Rectangle — drag to draw support/resistance zone")
        self._btn_draw_pan.setToolTip("تحريك/تكبير الشارت" if _ar else "Pan/zoom chart")
        self._btn_draw_clear.setToolTip("حذف كل الخطوط والقنوات والمستطيلات وفيبوناتشي" if _ar else "Remove all drawings")
        draw_col.addWidget(self._btn_draw_hline)
        draw_col.addWidget(self._btn_draw_line)
        draw_col.addWidget(self._btn_draw_channel)
        draw_col.addWidget(self._btn_draw_fib)
        draw_col.addWidget(self._btn_draw_rect)
        draw_col.addWidget(self._btn_draw_pan)
        draw_col.addWidget(self._btn_draw_clear)
        draw_col.addStretch(1)
        main_layout.addLayout(draw_col)

        self.candle_chart = CandlestickChart()
        # أقل من 280 يعطي مساحة للقسم العلوي عند نوافذ صغيرة (كان 280 يفرض سفلاً عريضاً)
        self.candle_chart.setMinimumHeight(250)
        main_layout.addWidget(self.candle_chart, 1)

        for key, btn in self._chart_type_buttons.items():
            btn.clicked.connect(lambda checked, k=key: self.candle_chart.setChartType(k))
        self._btn_draw_hline.clicked.connect(lambda: self.candle_chart.setDrawMode("hline"))
        self._btn_draw_line.clicked.connect(lambda: self.candle_chart.setDrawMode("line"))
        self._btn_draw_channel.clicked.connect(lambda: self.candle_chart.setDrawMode("channel"))
        self._btn_draw_fib.clicked.connect(lambda: self.candle_chart.setDrawMode("fib"))
        self._btn_draw_rect.clicked.connect(lambda: self.candle_chart.setDrawMode("rect"))
        self._btn_draw_pan.clicked.connect(lambda: self.candle_chart.setDrawMode(None))
        self._btn_draw_clear.clicked.connect(self.candle_chart.clearDrawings)

        # تحميل الحالة الافتراضية من الإعدادات (لا نقرأ الملف في كل تحديث مؤشر — كان يسبب تجمّداً)
        try:
            cfg = load_config()
            show_sr = bool(cfg.get("show_sr_levels", True))
        except Exception:
            show_sr = True
        self._show_sr_levels_setting = show_sr
        self._btn_toggle_sr.setChecked(show_sr)
        if hasattr(self.candle_chart, "setShowAnalysisLevels"):
            self.candle_chart.setShowAnalysisLevels(show_sr)
        self._btn_toggle_sr.toggled.connect(self._on_toggle_sr)

        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._update_countdown)
        self._countdown_timer.start(1000)

    def wheelEvent(self, event: QWheelEvent):
        """توجيه العجلة إلى الشارت حتى يعمل التكبير (أفقي وعمودي مع Ctrl)."""
        self.candle_chart.setFocus(Qt.FocusReason.MouseFocusReason)
        self.candle_chart.wheelEvent(event)
        event.accept()

    # ----------------------------------------------------
    # دالة استقبال الشموع من WebSocket عبر TradingPanel
    # ----------------------------------------------------
    def _update_countdown(self):
        """تحديث وقت انتهاء الشمعة داخلياً (لا يُعرض — المساحة للشموع فقط)."""
        try:
            sec = _seconds_until_next_candle(self._chart_interval)
            if hasattr(self, "candle_chart") and self.candle_chart:
                self.candle_chart.setCandleCountdown(sec, self._chart_interval)
        except Exception:
            pass

    def set_composite_signal(self, payload: dict):
        """شارة المؤشر المركّب على الشارت (من TradingPanel)."""
        cc = getattr(self, "candle_chart", None)
        if cc is None or not hasattr(cc, "setCompositeBadge"):
            return
        if not isinstance(payload, dict) or payload.get("clear"):
            cc.setCompositeBadge("")
            return
        ar = get_language() == "ar"
        txt = (payload.get("short_ar") if ar else payload.get("short_en")) or ""
        cc.setCompositeBadge(
            str(txt),
            bg_hex=str(payload.get("bg") or "#2d3748"),
            fg_hex=str(payload.get("fg") or "#ffffff"),
            explainer=tr("composite_chart_badge_tooltip"),
        )

    def set_analysis_levels(self, indicators: dict):
        """رسم مستويات الدعم/المقاومة/المحور + VWAP على الشارت الرئيسي من مؤشرات التحليل."""
        if not getattr(self, "candle_chart", None):
            return
        show_sr = bool(getattr(self, "_show_sr_levels_setting", True))
        pivot = float(indicators.get("pivot", 0) or 0)
        r1 = float(indicators.get("pivot_r1", 0) or 0)
        r2 = float(indicators.get("pivot_r2", 0) or 0)
        r3 = float(indicators.get("pivot_r3", 0) or 0)
        s1 = float(indicators.get("pivot_s1", 0) or 0)
        s2 = float(indicators.get("pivot_s2", 0) or 0)
        s3 = float(indicators.get("pivot_s3", 0) or 0)
        # نحدّث الأرقام دائماً؛ الإظهار/الإخفاء فقط عبر setShowAnalysisLevels (زر SR)
        self.candle_chart.setAnalysisLevels(pivot or None, r1 or None, r2 or None, s1 or None, s2 or None, r3 or None, s3 or None)
        vwap = indicators.get("vwap")
        if hasattr(self.candle_chart, "setVwap"):
            self.candle_chart.setVwap(vwap)
        if hasattr(self.candle_chart, "setShowAnalysisLevels"):
            self.candle_chart.setShowAnalysisLevels(show_sr)

    def _on_toggle_sr(self, checked: bool):
        self._show_sr_levels_setting = bool(checked)
        try:
            cfg = load_config()
            cfg["show_sr_levels"] = bool(checked)
            save_config(cfg)
        except Exception:
            pass
        if hasattr(self.candle_chart, "setShowAnalysisLevels"):
            self.candle_chart.setShowAnalysisLevels(bool(checked))

    def update_candle(self, *args):
        """
        candles = قائمة شموع تأتي من WebSocketManager
        كل عنصر يمكن أن يكون dict يحتوي open/high/low/close/volume
        أو tuple بنفس الترتيب.
        """
        try:
            # يدعم: update_candle(candles) أو update_candle(interval, candles)
            if len(args) == 2:
                self._chart_interval = args[0] if args[0] in INTERVAL_SECONDS else self._chart_interval
                candles = args[1]
            else:
                candles = args[0]

            if not candles or not isinstance(candles, list):
                return

            last = candles[-1]
            if isinstance(last, dict):
                close_price = last.get("close", 0)
            else:
                close_price = last[3]

            self._last_close = close_price
            self._refresh_price_label()

            # إرسال القائمة الكاملة للشارت لملء المنطقة بدل شمعة واحدة
            try:
                self.candle_chart.set_chart_interval(self._chart_interval)
            except Exception:
                pass
            self.candle_chart.setCandles(candles)
        except Exception as e:
            log.warning("ChartPanel update_candle error: %s", e)

    def _refresh_price_label(self):
        """محفوظ للتحديث الداخلي (لا يُعرض — تم إزالة العمود الأيسر)."""
        pass

    def update_price(self, price: float):
        """تحديث خط السعر الحالي من التاكر المباشر (ليتطابق مع Market Status)."""
        self._live_price = price
        self._refresh_price_label()
        if hasattr(self, "candle_chart") and self.candle_chart and price:
            self.candle_chart.setCurrentPrice(price)

    def set_recommendation_prices(self, buy_price, sell_price):
        """تعيين سعري الشراء والبيع من التوصية (ملخص الذكاء) لعرض مثلثين على مقياس الشارت."""
        if hasattr(self, "candle_chart") and self.candle_chart:
            self.candle_chart.setRecommendationPrices(buy_price, sell_price)

    # ----------------------------------------------------
    # دالة تغيير العملة عند اختيار المستخدم لرمز جديد
    # ----------------------------------------------------
    def change_symbol(self, symbol: str):
        self.current_symbol = symbol
        self.candle_chart.resetView()
        log.debug("ChartPanel switched to symbol: %s", symbol)
