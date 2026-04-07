from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QPushButton, QMessageBox,
    QStatusBar, QFrame,
)
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QCloseEvent

from trading_panel import TradingPanel
from api_settings_window import load_api_settings
from center_panel import CenterPanel
from config import load_config, save_config
from translations import tr
from ui_messages import show_auto_close_message
from ui_palette import TOP_PANEL_BORDER


class MainWindow(QMainWindow):

    def __init__(self):
        super().__init__()

        self.setWindowTitle(tr("main_title"))
        # ارتفاع أدنى يتسع لحدّ لوحة التداول + شارت + تبويبات دون طي العلوي
        self.setMinimumSize(1024, 640)
        self._api_warning_shown = False

        # قائمة أدوات — اختبار استراتيجية على بيانات تاريخية
        menubar = self.menuBar()
        tools_menu = menubar.addMenu(tr("menu_tools"))
        backtest_action = tools_menu.addAction(tr("menu_backtest"))
        backtest_action.triggered.connect(self._open_backtest_dialog)

        central = QWidget()
        central.setObjectName("MainCentral")
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(12, 12, 12, 10)
        main_layout.setSpacing(0)

        # قسمان: علوي 53 / سفلي 47 — بدون QSplitter (كان setSizes يتجاوز الارتفاع فيخفي العلوي).
        self.trading_panel = TradingPanel()
        main_layout.addWidget(self.trading_panel, 53)

        separator = QFrame()
        separator.setObjectName("MainSectionSeparator")
        separator.setFixedHeight(2)
        separator.setStyleSheet(
            f"#MainSectionSeparator {{ background-color: {TOP_PANEL_BORDER}; border: none; }}"
        )
        self._main_separator = separator
        main_layout.addWidget(separator)

        self.center_panel = CenterPanel()
        main_layout.addWidget(self.center_panel, 47)

        # -----------------------------
        # SIGNAL CONNECTIONS (NEW SYSTEM)
        # -----------------------------
        self.center_panel.connect_signals(self.trading_panel)
        self.center_panel.change_symbol(self.trading_panel.current_symbol)

        # -----------------------------
        # POSITIONS CONNECTIONS
        # -----------------------------
        def _on_new_open_position(sym, entry_px, qty, pid, oid):
            """بعد الشراء: ربط الصف فوراً بسعر السوق حتى يُحسب الربح (خصوصاً مع الرافعة حيث المزامنة قد تتأخر)."""
            self.center_panel.page_positions.add_or_update_position(
                sym, entry_px, qty, position_id=pid, etoro_open_order_id=oid
            )
            lp = float(getattr(self.trading_panel, "_last_price", 0) or 0)
            if lp <= 0:
                try:
                    lp = float(entry_px or 0)
                except (TypeError, ValueError):
                    lp = 0.0
            if lp > 0:
                self.center_panel.page_positions.update_price(lp)

        self.trading_panel.new_position.connect(_on_new_open_position)
        self.trading_panel.set_positions_panel(self.center_panel.page_positions)
        def on_close_all():
            self.center_panel.page_positions.table.setRowCount(0)
            self.trading_panel.set_daily_pnl(0.0)

        self.trading_panel.close_all_positions.connect(on_close_all)
        self.center_panel.page_positions.pnl_updated.connect(self.trading_panel.set_daily_pnl)
        self.center_panel.page_positions.refresh_positions_requested.connect(
            self.trading_panel._sync_open_positions_from_exchange
        )
        self.trading_panel.risk_settings_saved.connect(self._on_risk_config_saved)
        self.trading_panel.open_ai_requested.connect(self.open_ai_panel)
        self.trading_panel.show_history_tab_requested.connect(self.center_panel.show_history_tab)

        # -----------------------------
        # SYMBOL CHANGE — من لوحة التداول
        # -----------------------------
        self.trading_panel.symbol_changed.connect(self.center_panel.change_symbol)

        # شريط الحالة أسفل النافذة (الرمز، الاتصال، حالة البوت)
        self._status_bar = QStatusBar(self)
        self.setStatusBar(self._status_bar)
        self.trading_panel.status_bar_message.connect(self._status_bar.showMessage)
        if hasattr(self.trading_panel, "_emit_status_message"):
            self.trading_panel._emit_status_message()
        QTimer.singleShot(0, self._apply_fixed_section_ratio)
        QTimer.singleShot(0, self._apply_ui_scale)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_fixed_section_ratio()
        self._apply_ui_scale()

    def _apply_fixed_section_ratio(self):
        """تثبيت نسبة العلوي/السفلي بصرياً (55/45) مع حدود دنيا معقولة."""
        try:
            central = self.centralWidget()
            if central is None:
                return
            total_h = int(central.height() or 0)
            if total_h <= 0:
                return
            # نفس هوامش QVBoxLayout في __init__
            margins_v = 12 + 10
            spacing_v = 0
            sep_h = int(getattr(self, "_main_separator", None).height() or 2)
            available = total_h - margins_v - spacing_v - sep_h
            if available <= 0:
                return
            top_min = 320
            bottom_min = 220
            top_h = int(available * 0.58)
            bottom_h = available - top_h
            if top_h < top_min:
                top_h = top_min
                bottom_h = available - top_h
            if bottom_h < bottom_min:
                bottom_h = bottom_min
                top_h = available - bottom_h
            if top_h < top_min or bottom_h < bottom_min:
                # نافذة صغيرة جداً: نقبل التوازن المتاح دون كسر.
                top_h = max(200, int(available * 0.52))
                bottom_h = max(160, available - top_h)
            self.trading_panel.setFixedHeight(max(180, top_h))
            self.center_panel.setFixedHeight(max(150, bottom_h))
        except Exception:
            pass

    def _apply_ui_scale(self):
        """ربط أحجام الأقسام بحجم الشاشة (خطوط/هوامش/أبعاد)."""
        try:
            w = max(650, int(self.width() or 0))
            scale = max(0.80, min(1.10, w / 1024.0))
            # تمرير المقياس للأقسام إذا كانت تدعم ذلك
            tp = getattr(self, "trading_panel", None)
            if tp:
                if hasattr(tp, "_sync_side_column_widths"):
                    tp._sync_side_column_widths()
                if hasattr(tp, "_apply_quick_actions_responsive"):
                    tp._apply_quick_actions_responsive()
            cp = getattr(self, "center_panel", None)
            if cp and hasattr(cp, "apply_scale"):
                cp.apply_scale(scale)
        except Exception:
            pass

    def showEvent(self, event):
        super().showEvent(event)
        if self._api_warning_shown:
            return
        self._api_warning_shown = True
        # إظهار تذكير إعداد مفاتيح الـ API مرة واحدة فقط عند أول تشغيل للبرنامج
        cfg = load_config()
        if not cfg.get("api_hint_shown", False):
            key, secret = load_api_settings()
            if not (key and secret):
                show_auto_close_message(
                    self,
                    tr("main_api_msg_title"),
                    tr("main_api_msg_body"),
                    icon=QMessageBox.Icon.Information,
                    timeout_ms=5000,
                )
            cfg["api_hint_shown"] = True
            save_config(cfg)
        # استعادة مستوى قياس/زوم الشارت بعد تحميل البيانات (تأخير قصير)
        QTimer.singleShot(1200, self._restore_chart_state)

    def _restore_chart_state(self):
        """تطبيق حالة الشارت المحفوظة: الإطار (15m إلخ)، عدد الشموع، التكبير، الإزاحة."""
        try:
            cfg = load_config()
            # استعادة إطار الشارت (1m, 5m, 15m, 1h, 4h, 1d) من الإعدادات المحفوظة
            saved_interval = (cfg.get("chart_interval") or "1m").strip()
            if saved_interval in ("1m", "5m", "15m", "1h", "4h", "1d"):
                tp = self.trading_panel
                if getattr(tp, "_chart_interval", None) != saved_interval:
                    tp._chart_interval = saved_interval
                    if hasattr(tp, "interval_combo") and tp.interval_combo.findText(saved_interval) >= 0:
                        tp.interval_combo.blockSignals(True)
                        tp.interval_combo.setCurrentText(saved_interval)
                        tp.interval_combo.blockSignals(False)
                    if hasattr(tp, "_apply_chart_interval"):
                        tp._apply_chart_interval()
            # استعادة زوم/موقع الشارت
            chart = getattr(self.center_panel, "page_chart", None) and getattr(self.center_panel.page_chart, "candle_chart", None)
            if chart and hasattr(chart, "set_chart_state"):
                vc = int(cfg.get("chart_visible_count", 0) or 0)
                vs = int(cfg.get("chart_view_start", 0) or 0)
                yz = float(cfg.get("chart_y_zoom", 1.0) or 1.0)
                yp = float(cfg.get("chart_y_pan", 0.0) or 0.0)
                if vc > 0 or yz != 1.0 or yp != 0.0:
                    chart.set_chart_state(visible_count=vc, view_start=vs, y_zoom=yz, y_pan=yp)
        except Exception:
            pass

    def closeEvent(self, event: QCloseEvent):
        """حفظ حالة البرنامج (الرمز، الإعدادات، الشارت) ثم إيقاف WebSocket."""
        try:
            cfg = load_config()
            cfg["last_symbol"] = getattr(self.trading_panel, "current_symbol", "BTCUSDT")
            cfg["chart_interval"] = getattr(self.trading_panel, "_chart_interval", "1m")
            chart = getattr(self.center_panel, "page_chart", None) and getattr(self.center_panel.page_chart, "candle_chart", None)
            if chart and hasattr(chart, "get_chart_state"):
                st = chart.get_chart_state()
                cfg["chart_visible_count"] = st.get("visible_count", 0)
                cfg["chart_view_start"] = st.get("view_start", 0)
                cfg["chart_y_zoom"] = st.get("y_zoom", 1.0)
                cfg["chart_y_pan"] = st.get("y_pan", 0.0)
            save_config(cfg)
        except Exception:
            pass
        if hasattr(self, "trading_panel") and self.trading_panel is not None:
            try:
                self.trading_panel.toggle_button.setChecked(False)
            except Exception:
                pass
            try:
                self.trading_panel.shutdown_background()
            except Exception:
                pass
        try:
            aw = getattr(self, "ai_window", None)
            if aw is not None:
                aw.close()
                self.ai_window = None
        except Exception:
            pass
        event.accept()

    def _on_status_symbol(self, symbol: str):
        """شريط الحالة يُحدَّث عبر trading_panel.status_bar_message."""
        pass

    def _on_risk_config_saved(self, cfg: dict):
        """تحديث نصوص المبلغ والرافعة في لوحة التداول."""
        self.trading_panel.update_risk_display(cfg)

    def open_ai_panel(self):
        from ai_panel import AIPanel
        self.ai_window = AIPanel()
        self.ai_window.show()

    def _open_backtest_dialog(self):
        from backtest_dialog import BacktestDialog
        dlg = BacktestDialog(self)
        dlg.exec()

    # -----------------------------
    # STYLING HELPERS
    # -----------------------------
    def _style_symbol_combo(self):
        self.symbol_combo.setFixedHeight(40)
        self.symbol_combo.setMinimumWidth(140)
        self.symbol_combo.setStyleSheet(
            """
            QComboBox {
                background-color: #2A2A2D;
                color: white;
                border: 1px solid #3A3A3D;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 13px;
            }
            QComboBox:hover {
                border-color: #5A5A5F;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
            }
            QComboBox QAbstractItemView {
                background-color: #2A2A2D;
                color: white;
            }
            """
        )

    def _style_trade_button(self, btn: QPushButton):
        btn.setFixedHeight(40)
        btn.setStyleSheet(
            "background-color: #2A2A2D; color: white; border: 1px solid #3A3A3D; "
            "border-radius: 6px; padding: 6px 12px; font-size: 13px;"
        )

    def _style_top_button(self, btn: QPushButton):
        btn.setFixedHeight(34)
        btn.setStyleSheet(
            "background-color: #0A84FF; color: white; border-radius: 6px; "
            "padding: 6px 14px; font-weight: bold;"
        )
