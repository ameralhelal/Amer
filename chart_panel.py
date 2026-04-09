import logging
from datetime import datetime, timezone

from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel
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

        # عمود اليسار: SR + فيبوناتشي فقط (بدون أنواع شارت ولا باقي أدوات الرسم)
        draw_col = QVBoxLayout()
        draw_col.setSpacing(4)
        _ar = get_language() == "ar"
        _btn_style = "font-size: 10px; min-width: 26px; max-width: 26px; min-height: 22px; max-height: 22px; padding: 0;"
        tools_lbl = QLabel("SR · %")
        tools_lbl.setStyleSheet("color: #aaa; font-size: 9px;")
        draw_col.addWidget(tools_lbl)

        self._btn_toggle_sr = QPushButton("SR")
        self._btn_toggle_sr.setStyleSheet(_btn_style)
        self._btn_toggle_sr.setFixedSize(26, 22)
        self._btn_toggle_sr.setCheckable(True)
        self._btn_toggle_sr.setToolTip("إظهار/إخفاء الدعم والمقاومة" if _ar else "Show/Hide support & resistance")
        draw_col.addWidget(self._btn_toggle_sr)

        self._btn_draw_fib = QPushButton("%")
        self._btn_draw_fib.setStyleSheet(_btn_style)
        self._btn_draw_fib.setFixedSize(26, 22)
        self._btn_draw_fib.setCheckable(True)
        self._btn_draw_fib.setToolTip(
            "تفعيل فيبوناتشي: اسحب على الشارت بين نقطتين. إلغاء التفعيل لتحريك الشارت."
            if _ar
            else "Fib: drag on chart between two points. Uncheck to pan/zoom."
        )
        draw_col.addWidget(self._btn_draw_fib)
        draw_col.addStretch(1)
        main_layout.addLayout(draw_col)

        self.candle_chart = CandlestickChart()
        # أقل من 280 يعطي مساحة للقسم العلوي عند نوافذ صغيرة (كان 280 يفرض سفلاً عريضاً)
        self.candle_chart.setMinimumHeight(250)
        main_layout.addWidget(self.candle_chart, 1)

        self._btn_draw_fib.toggled.connect(self._on_fib_mode_toggled)

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

    def _on_fib_mode_toggled(self, checked: bool):
        self.candle_chart.setDrawMode("fib" if checked else None)

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
        try:
            self._btn_draw_fib.blockSignals(True)
            self._btn_draw_fib.setChecked(False)
        finally:
            self._btn_draw_fib.blockSignals(False)
        self.candle_chart.setDrawMode(None)
        self.candle_chart.resetView()
        log.debug("ChartPanel switched to symbol: %s", symbol)
