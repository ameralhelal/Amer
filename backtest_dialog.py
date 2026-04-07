# backtest_dialog.py — نافذة اختبار الاستراتيجية على البيانات التاريخية
import csv
from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QProgressBar,
    QGroupBox,
    QGridLayout,
    QMessageBox,
    QHeaderView,
    QTextEdit,
    QFileDialog,
    QWidget,
    QSizePolicy,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QRectF
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QBrush

from config import load_config
from backtest_engine import fetch_historical_candles, run_backtest
from translations import get_language
from format_utils import format_currency


class EquityCurveWidget(QWidget):
    """منحنى رصيد بسيط بدون مكتبات خارجية."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._points: list[float] = []
        self.setMinimumHeight(150)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_equity_curve(self, values: list[float]) -> None:
        self._points = list(values) if values else []
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        pr = QPainter(self)
        pr.fillRect(self.rect(), QColor(22, 26, 31))
        if len(self._points) < 2:
            pr.setPen(QColor(100, 110, 125))
            pr.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), "—")
            return
        vals = self._points
        mn, mx = min(vals), max(vals)
        span = mx - mn
        if span < 1e-9:
            mx = mn + 1.0
            span = 1.0
        w, h = self.width(), self.height()
        pad_l, pad_r, pad_t, pad_b = 10, 14, 12, 22

        def x_for(i: int) -> float:
            return pad_l + (w - pad_l - pad_r) * i / (len(vals) - 1)

        def y_for(v: float) -> float:
            t = (v - mn) / span
            return pad_t + (h - pad_t - pad_b) * (1.0 - t)

        path = QPainterPath()
        path.moveTo(x_for(0), y_for(vals[0]))
        for i in range(1, len(vals)):
            path.lineTo(x_for(i), y_for(vals[i]))

        # تظليل تحت المنحنى
        fill_path = QPainterPath(path)
        fill_path.lineTo(x_for(len(vals) - 1), h - pad_b)
        fill_path.lineTo(x_for(0), h - pad_b)
        fill_path.closeSubpath()
        pr.setPen(Qt.PenStyle.NoPen)
        pr.setBrush(QBrush(QColor(59, 130, 246, 45)))
        pr.drawPath(fill_path)

        pr.setPen(QPen(QColor(59, 130, 246), 2))
        pr.setBrush(Qt.BrushStyle.NoBrush)
        pr.drawPath(path)

        pr.setPen(QColor(120, 130, 145))
        f = QFont()
        f.setPointSize(8)
        pr.setFont(f)
        pr.drawText(
            QRectF(pad_l, h - 20, w - pad_l - pad_r, 16),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            f"{mn:.0f} → {mx:.0f}",
        )


class BacktestWorker(QThread):
    finished = pyqtSignal(list, dict)
    progress = pyqtSignal(int)

    def __init__(
        self,
        symbol: str,
        interval: str,
        limit: int,
        notional_usd: float,
        initial_capital_usd: float,
        fee_roundtrip_pct: float,
        parent=None,
    ):
        super().__init__(parent)
        self.symbol = symbol
        self.interval = interval
        self.limit = limit
        self.notional_usd = notional_usd
        self.initial_capital_usd = initial_capital_usd
        self.fee_roundtrip_pct = fee_roundtrip_pct

    def run(self):
        try:
            candles = fetch_historical_candles(self.symbol, self.interval, self.limit)
            if not candles:
                self.finished.emit([], {"error": "Failed to fetch data"})
                return
            cfg = load_config()
            trades, summary = run_backtest(
                candles,
                cfg,
                symbol=self.symbol,
                interval=self.interval,
                progress_callback=lambda pct: self.progress.emit(pct),
                notional_usd=self.notional_usd,
                initial_capital_usd=self.initial_capital_usd,
                fee_roundtrip_pct=self.fee_roundtrip_pct,
            )
            self.finished.emit(trades, summary)
        except Exception as e:
            self.finished.emit([], {"error": str(e)})


def _ar(t_ar: str, t_en: str) -> str:
    return t_ar if get_language() == "ar" else t_en


def _parse_money_field(edit: QLineEdit, default: float, minimum: float = 0.0) -> float:
    try:
        t = (edit.text() or "").strip().replace(",", ".")
        v = float(t) if t else default
        return max(minimum, v)
    except ValueError:
        return default


class BacktestDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_ar("اختبار استراتيجية على بيانات تاريخية", "Backtest Strategy on Historical Data"))
        self.setMinimumSize(820, 640)
        self._last_trades: list = []
        self._last_summary: dict = {}
        self.setStyleSheet("""
            QDialog { background-color: #1a1d24; }
            QGroupBox { font-weight: bold; color: #c0c8d0; }
            QLabel { color: #e0e0e0; }
            QTableWidget { background-color: #161a1f; gridline-color: #252a32; }
            QHeaderView::section { background-color: #252a32; color: #8b95a0; padding: 6px; }
            QTextEdit { background-color: #161a1f; color: #e0e0e0; border: 1px solid #252a32; }
        """)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 14)

        params_group = QGroupBox(_ar("معاملات الاختبار", "Backtest Parameters"))
        params_layout = QGridLayout(params_group)
        params_layout.addWidget(QLabel(_ar("الرمز:", "Symbol:")), 0, 0)
        self.symbol_edit = QLineEdit("BTCUSDT")
        self.symbol_edit.setPlaceholderText("BTCUSDT")
        self.symbol_edit.setMaximumWidth(140)
        params_layout.addWidget(self.symbol_edit, 0, 1)
        params_layout.addWidget(QLabel(_ar("الإطار:", "Interval:")), 0, 2)
        self.interval_combo = QComboBox()
        self.interval_combo.addItems(["1m", "5m", "15m", "1h", "4h", "1d"])
        self.interval_combo.setCurrentText("15m")
        self.interval_combo.setMaximumWidth(80)
        params_layout.addWidget(self.interval_combo, 0, 3)
        params_layout.addWidget(QLabel(_ar("عدد الشموع:", "Candles:")), 1, 0)
        self.candles_edit = QLineEdit("10000")
        self.candles_edit.setPlaceholderText("10000")
        self.candles_edit.setMaximumWidth(100)
        params_layout.addWidget(self.candles_edit, 1, 1)
        params_layout.addWidget(QLabel(_ar("(من 60 حتى 20000)", "(60 to 20000)")), 1, 2, 1, 2)

        params_layout.addWidget(QLabel(_ar("حجم الصفقة ($):", "Notional ($):")), 2, 0)
        self.notional_edit = QLineEdit("1000")
        self.notional_edit.setMaximumWidth(100)
        self.notional_edit.setToolTip(
            _ar("حجم كل صفقة بالدولار لحساب الربح/الخسارة والرسوم.", "Per-trade size in USD for PnL and fees.")
        )
        params_layout.addWidget(self.notional_edit, 2, 1)
        params_layout.addWidget(QLabel(_ar("رصيد البداية ($):", "Initial ($):")), 2, 2)
        self.initial_edit = QLineEdit("10000")
        self.initial_edit.setMaximumWidth(100)
        self.initial_edit.setToolTip(
            _ar("نقطة انطلاق منحنى الرصيد وأقصى تراجع.", "Starting equity for curve and max drawdown.")
        )
        params_layout.addWidget(self.initial_edit, 2, 3)

        params_layout.addWidget(QLabel(_ar("رسوم ذهاب/إياب %:", "Round-trip fee %:")), 3, 0)
        self.fee_edit = QLineEdit("0.2")
        self.fee_edit.setMaximumWidth(100)
        self.fee_edit.setToolTip(
            _ar("نسبة من حجم الصفقة لكل إغلاق (مثلاً 0.2 = 0.2٪).", "Percent of notional per closed trade (e.g. 0.2 = 0.2%).")
        )
        params_layout.addWidget(self.fee_edit, 3, 1)

        hint = QLabel(
            _ar(
                "ملاحظة: الاختبار يعمل على الإطار المختار فقط. للتقييم على عدة أطر شغّل الاختبار مرة لكل إطار. الأفضل أن تختار الإطار الذي تتداول عليه فعلياً.",
                "Note: Backtest runs for the selected interval only. Run once per interval to compare. Best: use the timeframe you trade on.",
            )
        )
        hint.setStyleSheet("color: #8b95a0; font-size: 11px;")
        hint.setWordWrap(True)
        params_layout.addWidget(hint, 4, 0, 1, 4)
        layout.addWidget(params_group)

        btn_layout = QHBoxLayout()
        self.run_btn = QPushButton(_ar("تشغيل الاختبار", "Run Backtest"))
        self.run_btn.setMinimumHeight(36)
        self.run_btn.clicked.connect(self._run_backtest)
        btn_layout.addWidget(self.run_btn)
        self.export_btn = QPushButton(_ar("تصدير CSV", "Export CSV"))
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export_csv)
        btn_layout.addWidget(self.export_btn)
        btn_layout.addStretch(1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setMaximumWidth(220)
        btn_layout.addWidget(self.progress_bar)
        layout.addLayout(btn_layout)

        summary_group = QGroupBox(_ar("ملخص النتائج", "Summary"))
        summary_outer = QVBoxLayout(summary_group)
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        self.summary_text.setMaximumHeight(200)
        self.summary_text.setPlaceholderText(_ar("شغّل الاختبار لعرض الملخص.", "Run backtest to see summary."))
        summary_outer.addWidget(self.summary_text)
        chart_caption = QLabel(_ar("منحنى الرصيد (بعد كل صفقة)", "Equity curve (after each trade)"))
        chart_caption.setStyleSheet("color: #8b95a0; font-size: 11px;")
        summary_outer.addWidget(chart_caption)
        self.equity_chart = EquityCurveWidget()
        self.equity_chart.setToolTip(_ar("منحنى الرصيد بعد كل صفقة (صافٍ بعد الرسوم).", "Equity after each trade (net of fees)."))
        summary_outer.addWidget(self.equity_chart)
        layout.addWidget(summary_group)

        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            [
                _ar("دخول #", "Entry #"),
                _ar("خروج #", "Exit #"),
                _ar("سعر الدخول", "Entry Price"),
                _ar("سعر الخروج", "Exit Price"),
                _ar("الربح/الخسارة %", "PnL %"),
                _ar("الربح/الخسارة", "PnL"),
                _ar("سبب الخروج", "Exit Reason"),
            ]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

    def _run_backtest(self):
        symbol = (self.symbol_edit.text() or "BTCUSDT").strip().upper()
        interval = self.interval_combo.currentText()
        try:
            limit = int(self.candles_edit.text() or "10000")
            limit = max(60, min(20000, limit))
        except ValueError:
            limit = 10000
        notional = _parse_money_field(self.notional_edit, 1000.0, minimum=1.0)
        initial = _parse_money_field(self.initial_edit, 10000.0, minimum=1.0)
        fee_rt = _parse_money_field(self.fee_edit, 0.2, minimum=0.0)

        self.run_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.summary_text.setPlainText(_ar("جاري جلب البيانات وتشغيل المحاكاة...", "Fetching data and running simulation..."))
        self.summary_text.setStyleSheet(
            "QTextEdit { background-color: #161a1f; color: #9ca3af; border: 1px solid #252a32; }"
        )
        self.equity_chart.set_equity_curve([])

        self._worker = BacktestWorker(symbol, interval, limit, notional, initial, fee_rt)
        self._worker.progress.connect(self.progress_bar.setValue)
        self._worker.finished.connect(self._on_backtest_finished)
        self._worker.start()

    def _on_backtest_finished(self, trades: list, summary: dict):
        self.run_btn.setEnabled(True)
        self.progress_bar.setValue(100)
        self._last_trades = trades
        self._last_summary = summary
        self.export_btn.setEnabled(bool(trades) and not summary.get("error"))
        self._show_results(trades, summary)

    def _summary_plain_text(self, summary: dict) -> str:
        err = summary.get("error")
        if err:
            return str(err)
        pf = summary.get("profit_factor")
        pf_s = "—" if pf is None else str(pf)
        rr = summary.get("rr_ratio_pct")
        rr_s = "—" if rr is None else str(rr)
        reasons = summary.get("exit_reasons") or {}
        reason_s = ", ".join(f"{k}: {v}" for k, v in sorted(reasons.items()))

        if get_language() == "ar":
            lines = [
                f"صفقات: {summary.get('total_trades', 0)}  |  فوز: {summary.get('win_count', 0)}  |  خسارة: {summary.get('loss_count', 0)}",
                f"نسبة النجاح %: {summary.get('win_rate_pct', 0)}",
                f"عائد رأس المال %: {summary.get('total_pnl_pct', 0):+.2f}  |  {format_currency(summary.get('total_pnl_usd', 0), signed=True)} (صافٍ بعد الرسوم، يطابق الرصيد النهائي)",
                f"متوسط ربح الفائز %: {summary.get('avg_win_pct', 0):+.3f}  |  متوسط خسارة الخاسر %: {summary.get('avg_loss_pct', 0):+.3f}",
                f"متوسط ربح الفائز: {format_currency(summary.get('avg_win_usd', 0), signed=True)}  |  متوسط خسارة الخاسر: {format_currency(summary.get('avg_loss_usd', 0), signed=True)}",
                f"نسبة R (متوسط ربح٪ / |متوسط خسارة٪|): {rr_s}  |  معامل الربح (صافٍ $): {pf_s}",
                f"أقصى تراجع %: {summary.get('max_drawdown_pct', 0)}  |  رصيد نهائي: {format_currency(summary.get('final_equity_usd', 0))}",
                f"حجم الصفقة: {summary.get('notional_usd', 0)} $  |  رسوم ذهاب/إياب: {summary.get('fee_roundtrip_pct', 0)}٪",
                f"توزيع الخروج: {reason_s or '—'}",
            ]
        else:
            lines = [
                f"Trades: {summary.get('total_trades', 0)}  |  Wins: {summary.get('win_count', 0)}  |  Losses: {summary.get('loss_count', 0)}",
                f"Win rate %: {summary.get('win_rate_pct', 0)}",
                f"Return on capital %: {summary.get('total_pnl_pct', 0):+.2f}  |  {format_currency(summary.get('total_pnl_usd', 0), signed=True)} (net of fees, matches final equity)",
                f"Avg win %: {summary.get('avg_win_pct', 0):+.3f}  |  Avg loss %: {summary.get('avg_loss_pct', 0):+.3f}",
                f"Avg win: {format_currency(summary.get('avg_win_usd', 0), signed=True)}  |  Avg loss: {format_currency(summary.get('avg_loss_usd', 0), signed=True)}",
                f"R (avg win% / |avg loss%|): {rr_s}  |  Profit factor (net $): {pf_s}",
                f"Max drawdown %: {summary.get('max_drawdown_pct', 0)}  |  Final equity: {format_currency(summary.get('final_equity_usd', 0))}",
                f"Notional: {summary.get('notional_usd', 0)} $  |  Round-trip fee: {summary.get('fee_roundtrip_pct', 0)}%",
                f"Exit mix: {reason_s or '—'}",
            ]
        return "\n".join(lines)

    def _show_results(self, trades: list, summary: dict):
        self.table.setRowCount(len(trades))
        for row, t in enumerate(trades):
            self.table.setItem(row, 0, QTableWidgetItem(str(t.get("entry_idx", ""))))
            self.table.setItem(row, 1, QTableWidgetItem(str(t.get("exit_idx", ""))))
            self.table.setItem(row, 2, QTableWidgetItem(f"{t.get('entry_price', 0):.4f}"))
            self.table.setItem(row, 3, QTableWidgetItem(f"{t.get('exit_price', 0):.4f}"))
            pnl = t.get("pnl_pct", 0)
            pnl_item = QTableWidgetItem(f"{pnl:+.2f}%")
            if pnl > 0:
                pnl_item.setForeground(QColor(0, 200, 120))
            elif pnl < 0:
                pnl_item.setForeground(QColor(220, 80, 80))
            self.table.setItem(row, 4, pnl_item)
            usd = float(t.get("pnl_usd", 0))
            usd_item = QTableWidgetItem(format_currency(usd, signed=True))
            if usd > 0:
                usd_item.setForeground(QColor(0, 200, 120))
            elif usd < 0:
                usd_item.setForeground(QColor(220, 80, 80))
            self.table.setItem(row, 5, usd_item)
            self.table.setItem(row, 6, QTableWidgetItem(t.get("exit_reason", "")))

        self.summary_text.setPlainText(self._summary_plain_text(summary))
        curve = summary.get("equity_curve") if not summary.get("error") else []
        self.equity_chart.set_equity_curve(curve if isinstance(curve, list) else [])

        base = "QTextEdit {{ background-color: #161a1f; border: 1px solid #252a32; font-weight: bold; {} }}"
        if not summary.get("error"):
            net_usd = float(summary.get("total_pnl_usd") or 0)
            if net_usd >= 0:
                self.summary_text.setStyleSheet(base.format("color: #00cc66;"))
            else:
                self.summary_text.setStyleSheet(base.format("color: #ff8888;"))
        else:
            self.summary_text.setStyleSheet(base.format("color: #ff5555; font-weight: normal;"))

    def _export_csv(self):
        if not self._last_trades:
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            _ar("حفظ نتائج الاختبار", "Save backtest results"),
            "backtest_results.csv",
            "CSV (*.csv)",
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(
                    [
                        "entry_idx",
                        "exit_idx",
                        "entry_price",
                        "exit_price",
                        "pnl_pct",
                        "pnl_usd_gross",
                        "fee_usd",
                        "pnl_usd_net",
                        "exit_reason",
                    ]
                )
                for t in self._last_trades:
                    w.writerow(
                        [
                            t.get("entry_idx"),
                            t.get("exit_idx"),
                            t.get("entry_price"),
                            t.get("exit_price"),
                            t.get("pnl_pct"),
                            t.get("pnl_usd_gross"),
                            t.get("fee_usd"),
                            t.get("pnl_usd"),
                            t.get("exit_reason"),
                        ]
                    )
                w.writerow([])
                w.writerow(["key", "value"])
                for k, v in self._last_summary.items():
                    if k == "equity_curve":
                        continue
                    if isinstance(v, dict):
                        w.writerow([k, repr(v)])
                    else:
                        w.writerow([k, v])
        except OSError as e:
            QMessageBox.warning(self, _ar("تصدير CSV", "Export CSV"), str(e))
