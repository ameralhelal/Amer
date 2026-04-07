# indicator_chart_page.py — صفحة شارت المؤشر: شموع حقيقية + المؤشر مرسوم + التوضيحات (مثل ملخص الذكاء)
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QFrame, QScrollArea,
    QSplitter,
)
from PyQt6.QtCore import Qt

from candlestick_widget import CandlestickChart
from indicator_chart_widget import IndicatorChartWidget, INDICATOR_CONFIG
from indicator_detail_dialog import _get_indicator_content


class IndicatorChartPage(QWidget):
    """صفحة تبويب تعرض: شارت الشموع الحقيقية + شارت المؤشر (خط مرسوم) + نقاط القوة/الضعف وأماكن الشراء/البيع."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._indicator_key = ""
        self.setStyleSheet("background-color: #1a1d24;")

        main = QVBoxLayout(self)
        main.setSpacing(8)
        main.setContentsMargins(8, 8, 8, 8)

        self._title_label = QLabel("شارت المؤشر — اختر مؤشراً من تبويب المؤشرات")
        self._title_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #fff;")
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        main.addWidget(self._title_label)

        split = QSplitter(Qt.Orientation.Vertical)

        # شارت الشموع الحقيقية
        self.candle_chart = CandlestickChart()
        self.candle_chart.setMinimumHeight(220)
        split.addWidget(self.candle_chart)

        # شارت المؤشر (خط المؤشر + خطوط الشراء/البيع)
        self.indicator_chart = IndicatorChartWidget()
        self.indicator_chart.setMinimumHeight(180)
        split.addWidget(self.indicator_chart)

        split.setSizes([320, 200])
        main.addWidget(split, 1)

        # قسم التوضيحات: نقاط القوة والضعف، متى صعود/هبوط، أماكن الشراء والبيع
        expl_frame = QFrame()
        expl_frame.setObjectName("ExplFrame")
        expl_frame.setStyleSheet("#ExplFrame { background-color: #252530; border-radius: 8px; padding: 10px; }")
        expl_layout = QVBoxLayout(expl_frame)
        expl_layout.setSpacing(6)
        self._expl_strength = QLabel("—")
        self._expl_strength.setWordWrap(True)
        self._expl_strength.setStyleSheet("color: #ccc; font-size: 12px;")
        self._expl_up_down = QLabel("—")
        self._expl_up_down.setWordWrap(True)
        self._expl_up_down.setStyleSheet("color: #ccc; font-size: 12px;")
        self._expl_buy_sell = QLabel("—")
        self._expl_buy_sell.setWordWrap(True)
        self._expl_buy_sell.setStyleSheet("color: #ccc; font-size: 12px;")
        expl_layout.addWidget(QLabel("نقاط القوة والضعف:"))
        expl_layout.addWidget(self._expl_strength)
        expl_layout.addWidget(QLabel("متى الصعود والهبوط:"))
        expl_layout.addWidget(self._expl_up_down)
        expl_layout.addWidget(QLabel("أماكن الشراء والبيع:"))
        expl_layout.addWidget(self._expl_buy_sell)
        scroll = QScrollArea()
        scroll.setWidget(expl_frame)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMaximumHeight(180)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        main.addWidget(scroll)

    def set_indicator(self, key: str, candles: list, indicators: dict = None):
        """ضبط المؤشر المعروض وتحديث الشارت والتوضيحات."""
        self._indicator_key = key or ""
        candles = list(candles) if candles else []
        indicators = indicators or {}

        if not key:
            self._title_label.setText("شارت المؤشر — اختر مؤشراً من تبويب المؤشرات")
            self._expl_strength.setText("—")
            self._expl_up_down.setText("—")
            self._expl_buy_sell.setText("—")
            return

        titles = {"rsi": "RSI", "macd": "MACD", "bb": "نطاقات بولينجر", "vwap": "VWAP", "pivot": "Pivot", "supertrend": "Supertrend"}
        title = titles.get(key, key)
        self._title_label.setText(f"شارت المؤشر — {title}")

        # شموع حقيقية
        if candles:
            self.candle_chart.setCandles(candles)

        # رسم المؤشر لجميع المؤشرات المعرّفة
        if key and key in INDICATOR_CONFIG and candles:
            self.indicator_chart.set_indicator(key, candles)
            self.indicator_chart.setVisible(True)
        else:
            self.indicator_chart.setVisible(False)

        # التوضيحات من _get_indicator_content
        c = _get_indicator_content(key, indicators)
        self._expl_strength.setText(c.get("strength_weakness") or "—")
        self._expl_up_down.setText(c.get("when_up_down") or "—")
        self._expl_buy_sell.setText(c.get("buy_sell_zones") or "—")

    def set_candles(self, candles: list):
        """تحديث الشموع فقط (للزامن مع الشارت الرئيسي)."""
        if not candles:
            return
        self.candle_chart.setCandles(candles)
        if self._indicator_key in ("rsi", "macd", "bb"):
            self.indicator_chart.set_indicator(self._indicator_key, candles)
