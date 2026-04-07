import logging

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QStackedWidget,
    QApplication, QMainWindow,
)
from PyQt6.QtCore import Qt, QEvent

from chart_panel import ChartPanel
from ai_dashboard import ChartRecommendationBridge
from open_positions import OpenPositionsPanel
from ai_panel import AIPanel
from indicators_panel import IndicatorsPanel
from indicator_detail_dialog import IndicatorDetailDialog
from market_info_panel import MarketInfoPanel
from trade_history import TradeHistoryPanel, DailyLogPanel
from error_log_panel import ErrorLogPanel
from order_log_panel import OrderLogPanel
from translations import tr, get_language
from ui_palette import (
    TOP_PANEL_BG,
    TOP_PANEL_BORDER,
    TOP_INNER_BG,
    TOP_INNER_BORDER,
    TOP_TEXT_PRIMARY,
)

log = logging.getLogger("trading.center")


class CenterPanel(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("CenterPanel")
        self._scale = 1.0
        self._popout_windows = {}  # index -> window
        self._last_indicators = None  # آخر مؤشرات للقسم السفلي ونافذة التفاصيل

        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 12, 10, 10)

        # -----------------------------
        # صف أزرار التبويبات — مصدر واحد للحقيقة:
        # 0=شارت، 1=مراكز، 2=سجل الأوامر، 3=سجل الصفقات، 4=يومي، 5=أخطاء
        # (المؤشرات / معلومات السوق / لوحة AI أُزيلت من التبويبات؛ تُحدَّث في الخلفية للربط مع الشارت والتوصية)
        # -----------------------------
        _btn_height = 32
        buttons_row = QHBoxLayout()
        buttons_row.setSpacing(4)
        buttons_row.setContentsMargins(0, 4, 0, 6)

        _tab_spec_ar = (
            ("الشارت", "center_chart"),
            ("المراكز", "center_positions"),
            ("سجل الأوامر", "center_orders_log"),
            ("سجل الصفقات", "trading_history"),
            ("السجل اليومي", "history_daily_log_title"),
            ("سجل الأخطاء", "error_log"),
        )
        _labels = [t[0] if get_language() == "ar" else tr(t[1]) for t in _tab_spec_ar]
        self.btn_chart = QPushButton(_labels[0])
        self.btn_positions = QPushButton(_labels[1])
        self.btn_orders_log = QPushButton(_labels[2])
        self.btn_history = QPushButton(_labels[3])
        self.btn_daily_log = QPushButton(_labels[4])
        self.btn_errors = QPushButton(_labels[5])

        _tab_buttons_list = [
            self.btn_chart,
            self.btn_positions,
            self.btn_orders_log,
            self.btn_history,
            self.btn_daily_log,
            self.btn_errors,
        ]
        for b in _tab_buttons_list:
            b.setFixedHeight(_btn_height)
            b.setMinimumWidth(72)
            b.setSizePolicy(b.sizePolicy().horizontalPolicy(), b.sizePolicy().verticalPolicy())
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            # لا نضع setStyleSheet هنا؛ الستايل يُطبَّق من الـ parent في apply_scale لتجنب تعارض يسبب "Could not parse stylesheet"
            buttons_row.addWidget(b)

        # زر فتح التبويب في نافذة
        self.btn_popout = QPushButton("↗")
        self.btn_popout.setToolTip(tr("trading_history"))
        self.btn_popout.setFixedHeight(_btn_height)
        self.btn_popout.setFixedWidth(36)
        self.btn_popout.setCheckable(False)
        self.btn_popout.setCursor(Qt.CursorShape.PointingHandCursor)
        buttons_row.addStretch(1)
        buttons_row.addWidget(self.btn_popout)

        layout.addLayout(buttons_row)

        # -----------------------------
        # Stacked Screens
        # -----------------------------
        self.stacked = QStackedWidget()
        self.stacked.setObjectName("CenterStacked")

        self.page_chart = ChartPanel()
        self._chart_rec_bridge = ChartRecommendationBridge(self)
        self.page_indicators = IndicatorsPanel()
        self.page_market = MarketInfoPanel()
        self.page_ai_panel = AIPanel()
        # لا تُعرض في التبويب السفلي؛ تبقى مربوطة بالإشارات (توصية، مؤشرات، سياق السوق)
        for _p in (self.page_indicators, self.page_market, self.page_ai_panel):
            _p.setParent(self)
            _p.hide()
        self.page_orders_log = OrderLogPanel()
        self.page_history = TradeHistoryPanel()
        self.page_daily_log = DailyLogPanel()
        self.page_errors = ErrorLogPanel()
        self.page_positions = OpenPositionsPanel()

        # ترتيب الصفحات المنطقي (بالنسبة للأزرار) — نستخدمه دائماً بدل الاعتماد على فهارس QStackedWidget المتغيرة مع النوافذ المنبثقة
        self._pages_order = [
            self.page_chart,       # 0
            self.page_positions,   # 1
            self.page_orders_log,  # 2
            self.page_history,     # 3
            self.page_daily_log,   # 4
            self.page_errors,      # 5
        ]

        # إضافة الصفحات إلى الـ stacked بنفس الترتيب في البداية
        for p in self._pages_order:
            self.stacked.addWidget(p)

        layout.addWidget(self.stacked, 1)

        self.setLayout(layout)

        # من قسم المؤشرات (تبويب المؤشرات): النقر يفتح شارت المؤشر أو نافذة التفاصيل
        self.page_indicators.indicator_clicked.connect(self._on_indicator_panel_clicked)

        self._chart_widget = self.page_chart.candle_chart
        app = QApplication.instance()
        if app:
            app.installEventFilter(self)

        # ستايل موحّد مع الصف العلوي (خلفية + إطار + أزرار داخلية)
        self.setStyleSheet(
            f"""
            #CenterPanel {{
                background-color: {TOP_PANEL_BG};
                border: 1px solid {TOP_PANEL_BORDER};
                border-radius: 12px;
            }}
            #CenterStacked {{
                background-color: {TOP_PANEL_BG};
            }}
            QPushButton {{
                background-color: {TOP_INNER_BG};
                color: {TOP_TEXT_PRIMARY};
                border: 1px solid {TOP_INNER_BORDER};
                border-radius: 6px;
                padding: 4px 10px;
                font-family: Segoe UI, Arial;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background-color: #232a36;
                border-color: {TOP_PANEL_BORDER};
            }}
            QPushButton:checked {{
                background-color: #0d7dd6;
                border-color: #0a6bb8;
                color: white;
                font-weight: bold;
            }}
            """
        )

        self._tab_buttons = _tab_buttons_list

        # -----------------------------
        # Button Actions (نفس ترتيب الأزرار أعلاه و stacked)
        # -----------------------------
        self.btn_chart.clicked.connect(lambda: self._switch_page(0))
        self.btn_positions.clicked.connect(lambda: self._switch_page(1))
        self.btn_orders_log.clicked.connect(lambda: self._switch_page(2))
        self.btn_history.clicked.connect(lambda: self._switch_page(3))
        self.btn_daily_log.clicked.connect(lambda: self._switch_page(4))
        self.btn_errors.clicked.connect(lambda: self._switch_page(5))
        self.btn_popout.clicked.connect(self._popout_current_page)

        # أول زر مفعّل
        self._switch_page(0)

    def _save_indicators(self, interval: str, indicators: dict):
        """حفظ آخر المؤشرات لاستخدامها في شريط المؤشرات ونافذة التفاصيل."""
        if isinstance(indicators, dict):
            self._last_indicators = indicators

    def _on_indicator_section_clicked(self, key: str):
        """ملخص الذكاء أُزيل؛ أي مؤشر → نافذة منفصلة (شارت المؤشر + التفاصيل)."""
        if key == "ai_summary":
            self._switch_page(0)
            return
        self._open_indicator_window(key)

    def _on_indicator_panel_clicked(self, key: str):
        """عند النقر على مؤشر في تبويب المؤشرات: فتح نافذة منفصلة (شارت المؤشر + التوضيحات)."""
        self._open_indicator_window(key)

    def _on_candle_updated_for_indicator_dialog(self, interval, candles):
        """تحديث نافذة المؤشر المفتوحة بالشموع الحية (مؤشر حي يعكس الشموع الحقيقية)."""
        dlg = getattr(self, "_open_indicator_dialog", None)
        if dlg and getattr(dlg, "isVisible", lambda: False)() and hasattr(dlg, "update_candles") and candles:
            dlg.update_candles(candles)

    def _open_indicator_window(self, key: str):
        """فتح نافذة المؤشر: شارت حي + التفاصيل (نفس مبدأ VWAP لجميع المؤشرات بما فيها RSI و MACD وبولينجر)."""
        candles = []
        try:
            if self.page_chart and self.page_chart.candle_chart and hasattr(self.page_chart.candle_chart, "candles"):
                candles = getattr(self.page_chart.candle_chart, "candles", []) or []
        except Exception:
            pass
        dlg = IndicatorDetailDialog(
            self,
            indicator_key=key,
            indicators=self._last_indicators,
            chart_pixmap=None,
            candles=candles if candles else None,
        )
        self._open_indicator_dialog = dlg
        try:
            if getattr(self, "_trading_panel", None):
                self._trading_panel.candle_updated.connect(self._on_candle_updated_for_indicator_dialog)
        except Exception:
            pass
        dlg.exec()
        try:
            if getattr(self, "_trading_panel", None):
                self._trading_panel.candle_updated.disconnect(self._on_candle_updated_for_indicator_dialog)
        except Exception:
            pass
        self._open_indicator_dialog = None

    class _PopoutWindow(QMainWindow):
        def __init__(self, title: str, on_close):
            super().__init__()
            self.setWindowTitle(title)
            self._on_close = on_close
            # أغلق/احذف النافذة بالكامل حتى لا تبقى مراجع قديمة
            try:
                self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            except Exception:
                pass

        def closeEvent(self, event):
            try:
                if callable(self._on_close):
                    self._on_close()
            finally:
                event.accept()

    def _popout_current_page(self):
        """فتح الصفحة الحالية في نافذة مستقلة."""
        current_widget = self.stacked.currentWidget()
        if current_widget is None:
            return
        try:
            idx = self._pages_order.index(current_widget)
        except ValueError:
            return

        if idx in self._popout_windows and self._popout_windows[idx] is not None:
            try:
                self._popout_windows[idx].raise_()
                self._popout_windows[idx].activateWindow()
            except Exception:
                pass
            return

        page = current_widget

        # إزالة الصفحة من الـ stacked وإعادة parent إلى نافذة مستقلة (مع الاحتفاظ بترتيب منطقي منفصل)
        self.stacked.removeWidget(page)
        page.setParent(None)

        title = (self._tab_buttons[idx].text() if 0 <= idx < len(self._tab_buttons) else "") or "Panel"

        def restore():
            # إعادة الصفحة لمكانها عند إغلاق النافذة
            try:
                # إدراجها في نفس الموضع المنطقي إن لم تكن موجودة بالفعل
                if self.stacked.indexOf(page) == -1:
                    self.stacked.insertWidget(idx, page)
                self._switch_page(idx)
            except Exception:
                # fallback
                if self.stacked.indexOf(page) == -1:
                    self.stacked.addWidget(page)
            self._popout_windows.pop(idx, None)

        win = CenterPanel._PopoutWindow(title=title, on_close=restore)
        # ضع الصفحة داخل container مع layout لضمان ظهورها وعدم تحولها لصفحة سوداء
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)
        container_layout.addWidget(page)
        win.setCentralWidget(container)
        win.resize(900, 650)
        self._popout_windows[idx] = win
        page.show()
        page.update()
        win.show()
        try:
            win.raise_()
            win.activateWindow()
        except Exception:
            pass

    def apply_scale(self, scale: float):
        """تصغير/تكبير عناصر اللوحة المركزية حسب حجم الشاشة."""
        try:
            self._scale = float(scale)
        except Exception:
            self._scale = 1.0
        s = max(0.80, min(1.10, self._scale))
        font_px = max(9, min(12, int(round(11 * s))))
        btn_h = max(24, min(34, int(round(30 * s))))
        pad_v = max(3, min(6, int(round(4 * s))))
        pad_h = max(6, min(10, int(round(8 * s))))
        # تحديث ستايل الأزرار (بدون قيود عرض ثابتة)
        self.setStyleSheet(
            f"""
            #CenterPanel {{
                background-color: {TOP_PANEL_BG};
                border: 1px solid {TOP_PANEL_BORDER};
                border-radius: 12px;
            }}
            #CenterStacked {{
                background-color: {TOP_PANEL_BG};
            }}
            QPushButton {{
                background-color: {TOP_INNER_BG};
                color: {TOP_TEXT_PRIMARY};
                border: 1px solid {TOP_INNER_BORDER};
                border-radius: 6px;
                padding: {pad_v}px {pad_h}px;
                font-family: Segoe UI, Arial;
                font-size: {font_px}px;
                min-height: {max(18, btn_h - 8)}px;
            }}
            QPushButton:hover {{
                background-color: #232a36;
                border-color: {TOP_PANEL_BORDER};
            }}
            QPushButton:checked {{
                background-color: #0d7dd6;
                border-color: #0a6bb8;
                color: white;
                font-weight: bold;
            }}
            """
        )
        for b in self._tab_buttons:
            b.setFixedHeight(btn_h)

    def eventFilter(self, obj, event):
        # تمرير عجلة الماوس للشارت فقط عندما يكون الشارت فعلاً داخل الـ stacked ومُحدد حالياً
        if event.type() == QEvent.Type.Wheel:
            chart_index = self.stacked.indexOf(self.page_chart)
            if chart_index != -1 and self.stacked.currentIndex() == chart_index:
                chart = self._chart_widget
                if not chart or not chart.isVisible():
                    return super().eventFilter(obj, event)
                if obj is chart:
                    return False
                if isinstance(obj, QWidget) and (self.page_chart.isAncestorOf(obj) or obj == self.page_chart):
                    chart.setFocus(Qt.FocusReason.MouseFocusReason)
                    chart.wheelEvent(event)
                    return True
        return super().eventFilter(obj, event)

    def _switch_page(self, index: int):
        for i, b in enumerate(self._tab_buttons):
            b.setChecked(i == index)

        # اعثر على الـ widget المنطقي لهذه الصفحة
        page = self._pages_order[index] if 0 <= index < len(self._pages_order) else None
        if page is not None:
            stack_idx = self.stacked.indexOf(page)
            if stack_idx != -1:
                self.stacked.setCurrentIndex(stack_idx)
                if index == 1:
                    try:
                        self.page_positions.update_pnl()
                    except Exception:
                        pass
            else:
                # الصفحة منبثقة خارج الـ stacked — إن وُجدت نافذة منبثقة لها، ركّزها
                win = self._popout_windows.get(index)
                if win is not None:
                    try:
                        win.raise_()
                        win.activateWindow()
                    except Exception:
                        pass
                return

        if index == 0:
            self.page_chart.candle_chart.resetView()
            self.page_chart.candle_chart.setFocus(Qt.FocusReason.OtherFocusReason)
        if index == 3:
            self.page_history.refresh()
        if index == 4:
            self.page_daily_log.refresh()

    def show_history_tab(self):
        """الانتقال إلى صفحة سجل الصفقات (للاستدعاء من القائمة أو أزرار أخرى)."""
        self._switch_page(3)

    # ----------------------------------------------------
    # ربط الإشارات القادمة من TradingPanel
    # ----------------------------------------------------
    def connect_signals(self, trading_panel):
        self._trading_panel = trading_panel
        # indicators_updated: (interval, indicators) — interval = إطار الشارت المختار
        trading_panel.price_updated.connect(self._chart_rec_bridge.update_price)
        trading_panel.price_updated.connect(self.page_positions.update_price)
        trading_panel.price_updated.connect(self.page_chart.update_price)

        trading_panel.candle_updated.connect(self.page_chart.update_candle)
        trading_panel.indicators_updated.connect(self._save_indicators)
        trading_panel.indicators_updated.connect(self.page_indicators.update_indicators)
        trading_panel.indicators_updated.connect(
            lambda iv, ind: self.page_chart.set_analysis_levels(ind)
        )
        trading_panel.composite_signal_updated.connect(self.page_chart.set_composite_signal)
        trading_panel.market_info_updated.connect(self.page_market.update_market_info)
        # لوحة التوصية (AI Panel) أولاً حتى تُصدِر التوصية الرسمية قبل تحديث ملخص الذكاء
        trading_panel.indicators_updated.connect(self.page_ai_panel.update_indicators)
        trading_panel.indicators_updated.connect(self._chart_rec_bridge.update_indicators)
        trading_panel.market_info_updated.connect(
            lambda info: self.page_ai_panel.update_market_info(
                getattr(trading_panel, "_chart_interval", "1m"), info
            )
        )
        trading_panel.market_info_updated.connect(
            lambda info: self._chart_rec_bridge.set_market_info_snapshot(
                info if isinstance(info, dict) else {}
            )
        )
        # تحديث النص في الأعلى قبل منطق البوت حتى لا يمنع استثناء في البوت ظهور التوصية
        self.page_ai_panel.recommendation_updated.connect(trading_panel.update_ai_panel_display)
        self.page_ai_panel.recommendation_updated.connect(trading_panel.on_ai_recommendation)
        self.page_ai_panel.recommendation_updated.connect(
            lambda rec, _c, _i, _m: self._chart_rec_bridge.set_official_recommendation(rec)
        )
        self._chart_rec_bridge.recommendation_prices_updated.connect(self.page_chart.set_recommendation_prices)
        self._chart_rec_bridge.suggested_strategy_updated.connect(trading_panel.set_suggested_strategy)
        self.page_chart.candle_chart.recommendation_clicked.connect(
            lambda price, side: self._on_recommendation_price_clicked(price, side, trading_panel)
        )
        self.page_chart.candle_chart.right_click_stop_loss.connect(
            lambda price: self._on_chart_right_click_stop_loss(price, trading_panel)
        )
        self.page_chart.candle_chart.right_click_limit_buy.connect(
            lambda price: self._on_chart_right_click_limit_buy(price, trading_panel)
        )
        # تحديث سجل الصفقات فور تسجيل أي صفقة (بدون الحاجة لفتح تبويب السجل)
        try:
            # QueuedConnection: يُشغَّل التحديث بعد إغلاق الملف وانتهاء حلقة الأحداث — يظهر البيع فوراً
            trading_panel.history_refresh_requested.connect(
                self.page_history.refresh,
                Qt.ConnectionType.QueuedConnection,
            )
        except Exception:
            pass

    def _on_chart_right_click_limit_buy(self, price: float, trading_panel):
        """كليك يمين → تعيين كحد شراء: ضبط سعر حد الشراء عند المؤشر."""
        from config import load_config, save_config
        from format_utils import format_price
        cfg = load_config()
        cfg["limit_buy_type"] = "price"
        cfg["limit_buy_value"] = float(price)
        cfg["limit_buy_price"] = float(price)
        cfg["limit_buy_anchor_price"] = 0.0
        save_config(cfg)
        if hasattr(trading_panel, "_limit_buy_pct_runtime_anchor"):
            trading_panel._limit_buy_pct_runtime_anchor = None
        if hasattr(trading_panel, "_refresh_quick_buttons"):
            trading_panel._refresh_quick_buttons(load_config())
        if hasattr(trading_panel, "status_bar_message"):
            trading_panel.status_bar_message.emit(f"حد الشراء = {format_price(price)} (سعر)")

    def _on_chart_right_click_stop_loss(self, price: float, trading_panel):
        """كليك يمين على الشارت: تعيين السعر عند المؤشر كوقف خسارة (سعر ثابت)."""
        from config import load_config, save_config
        from format_utils import format_price
        cfg = load_config()
        cfg["sl_type"] = "price"
        cfg["sl_value"] = float(price)
        save_config(cfg)
        if hasattr(trading_panel, "_refresh_quick_buttons"):
            trading_panel._refresh_quick_buttons(load_config())
        if hasattr(trading_panel, "status_bar_message"):
            trading_panel.status_bar_message.emit(f"وقف الخسارة = {format_price(price)} (سعر)")

    def _on_recommendation_price_clicked(self, price: float, side: str, trading_panel):
        """عند النقر على الأخضر: ضبط حد الشراء. على الأحمر: ضبط حد الخسارة (SL)."""
        from config import load_config, save_config
        cfg = load_config()
        if side == "buy":
            cfg["limit_buy_type"] = "price"
            cfg["limit_buy_value"] = float(price)
            cfg["limit_buy_price"] = float(price)
            cfg["limit_buy_anchor_price"] = 0.0
            if hasattr(trading_panel, "_limit_buy_pct_runtime_anchor"):
                trading_panel._limit_buy_pct_runtime_anchor = None
        else:
            cfg["sl_type"] = "price"
            cfg["sl_value"] = float(price)
        save_config(cfg)
        if hasattr(trading_panel, "_refresh_quick_buttons"):
            trading_panel._refresh_quick_buttons(load_config())

    # ----------------------------------------------------
    # تغيير العملة
    # ----------------------------------------------------
    def change_symbol(self, symbol: str):
        for name in ("page_chart", "page_indicators", "page_market", "page_positions", "page_orders_log"):
            try:
                page = getattr(self, name, None)
                if page is not None and hasattr(page, "change_symbol"):
                    page.change_symbol(symbol)
            except Exception:
                pass

        log.debug("CenterPanel switched to symbol: %s", symbol)
