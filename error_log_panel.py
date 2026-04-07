# error_log_panel.py — سجل الأخطاء + تقارير التنفيذ
import json
import logging
import os

from PyQt6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QDialog,
    QDialogButtonBox,
)
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont

from format_utils import format_price

_EXEC_ROW_PAYLOAD_ROLE = int(Qt.ItemDataRole.UserRole) + 87


class _LogEmitter(QObject):
    message = pyqtSignal(str)


class _QtLogHandler(logging.Handler):
    """معالج logging يرسل الرسائل إلى الواجهة عبر الإشارة."""
    def __init__(self, emitter: _LogEmitter):
        super().__init__()
        self._emitter = emitter

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        self._emitter.message.emit(msg)


class ErrorLogPanel(QWidget):
    """لوحة سجل الأخطاء — تعرض تحذيرات وأخطاء البرنامج. تُضاف في اللوحة المركزية بجانب السجل اليومي."""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        title = QLabel("سجل الأخطاء")
        title.setStyleSheet("font-size: 13px; font-weight: bold; color: #e8eaed;")
        layout.addWidget(title)

        self.errors_box = QTextEdit()
        self.errors_box.setReadOnly(True)
        self.errors_box.setPlaceholderText("سيتم عرض أخطاء البرنامج هنا تلقائياً...")
        self.errors_box.setStyleSheet(
            "QTextEdit { background-color: #111; color: #eaeaea; border: 1px solid #333; "
            "border-radius: 8px; padding: 8px; font-family: Consolas, 'Segoe UI'; font-size: 12px; }"
        )
        layout.addWidget(self.errors_box, 1)

        exec_title = QLabel("تقارير التنفيذ")
        exec_title.setStyleSheet("font-size: 12px; font-weight: bold; color: #d2d7de; padding-top: 4px;")
        layout.addWidget(exec_title)

        self.exec_table = QTableWidget()
        self.exec_table.setObjectName("ExecutionReportInErrorLog")
        self.exec_table.setColumnCount(9)
        self.exec_table.setHorizontalHeaderLabels(
            ["الوقت", "الرمز", "العملية", "الحالة", "مطلوب", "منفذ", "انزلاق %", "تأخير ms", "السبب"]
        )
        self.exec_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        self.exec_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.exec_table.verticalHeader().setVisible(False)
        self.exec_table.setStyleSheet(
            "QTableWidget { background:#141414; color:#e0e0e0; border:1px solid #333; }"
            "QHeaderView::section { background:#222; color:#fff; padding:5px; border:none; }"
            "QTableWidget::item { border:1px solid #333; padding:4px; }"
        )
        self.exec_table.setMinimumHeight(190)
        self.exec_table.cellClicked.connect(self._on_exec_table_cell_clicked)
        layout.addWidget(self.exec_table)
        self.exec_summary_label = QLabel("")
        self.exec_summary_label.setStyleSheet("color: #aeb6c2; font-size: 11px; padding: 4px 2px;")
        layout.addWidget(self.exec_summary_label)
        self._exec_click_hint = QLabel(
            "💡 انقر خانة «السبب» لعرض لقطة المؤشرات والسياق (إن وُجدت) — مفيدة لتحليل صفقات «توصية البوت»."
        )
        self._exec_click_hint.setWordWrap(True)
        self._exec_click_hint.setStyleSheet("color: #7a8a9e; font-size: 10px; padding: 2px 0;")
        layout.addWidget(self._exec_click_hint)

        btn_row = QHBoxLayout()
        exec_refresh_btn = QPushButton("تحديث تقرير التنفيذ")
        exec_refresh_btn.clicked.connect(self.refresh_execution_reports)
        btn_row.addWidget(exec_refresh_btn)
        open_exec_btn = QPushButton("فتح ملف التقرير")
        open_exec_btn.clicked.connect(self._open_execution_report_file)
        btn_row.addWidget(open_exec_btn)
        btn_row.addStretch()
        clear_btn = QPushButton("مسح السجل")
        clear_btn.clicked.connect(self.errors_box.clear)
        btn_row.addWidget(clear_btn)
        layout.addLayout(btn_row)

        self._install_error_logger()
        self.refresh_execution_reports()

    def _install_error_logger(self):
        """ربط سجل الأخطاء مع logging لعرض التحذيرات والأخطاء تلقائياً."""
        emitter = _LogEmitter(self)
        emitter.message.connect(self._append_error_log)
        self._log_emitter = emitter

        handler = _QtLogHandler(emitter)
        handler.setLevel(logging.WARNING)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S")
        )
        self._qt_log_handler = handler

        root = logging.getLogger()
        for h in list(root.handlers):
            if isinstance(h, _QtLogHandler):
                return
        root.addHandler(handler)

    def _append_error_log(self, text: str):
        try:
            if self.errors_box:
                self.errors_box.append(text)
        except Exception:
            pass

    @staticmethod
    def _execution_report_path() -> str:
        base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
        folder = os.path.join(base, "CryptoTrading")
        os.makedirs(folder, exist_ok=True)
        return os.path.join(folder, "execution_reports.jsonl")

    def _load_execution_reports(self, limit: int = 30) -> list[dict]:
        path = self._execution_report_path()
        if not os.path.isfile(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return []
        out: list[dict] = []
        for ln in reversed(lines):
            s = (ln or "").strip()
            if not s:
                continue
            try:
                row = json.loads(s)
            except Exception:
                continue
            if isinstance(row, dict):
                out.append(row)
                if len(out) >= limit:
                    break
        return out

    def refresh_execution_reports(self):
        rows = self._load_execution_reports(limit=30)
        self.exec_table.setRowCount(len(rows))
        ok_count = 0
        fail_count = 0
        slip_sum = 0.0
        slip_n = 0
        for i, r in enumerate(rows):
            t = str(r.get("time_utc", "") or "").replace("Z", "").replace("+00:00", "")
            time_text = t[11:16] if len(t) >= 16 else (t or "—")
            sym = str(r.get("symbol", "") or "—")
            side = str(r.get("side", "") or "").upper()
            side_text = "شراء" if side == "BUY" else ("بيع" if side == "SELL" else "—")
            ok = bool(r.get("ok", False))
            state_text = "نجاح" if ok else "فشل"
            if ok:
                ok_count += 1
            else:
                fail_count += 1
            req = r.get("requested_price")
            exe = r.get("executed_price")
            slip = r.get("slippage_pct")
            lat = r.get("latency_ms")
            reason = str(r.get("reason", "") or "—")

            self.exec_table.setItem(i, 0, self._centered_item(time_text))
            self.exec_table.setItem(i, 1, self._centered_item(sym))
            self.exec_table.setItem(i, 2, self._centered_item(side_text))
            state_item = self._centered_item(state_text)
            state_item.setForeground(QColor("#00aa00" if ok else "#cc0000"))
            self.exec_table.setItem(i, 3, state_item)
            self.exec_table.setItem(i, 4, self._centered_item(format_price(float(req)) if req else "—"))
            self.exec_table.setItem(i, 5, self._centered_item(format_price(float(exe)) if exe else "—"))
            slip_item = self._centered_item(f"{float(slip):+.3f}%" if slip is not None else "—")
            try:
                slip_v = float(slip)
                slip_sum += slip_v
                slip_n += 1
                if slip_v > 0:
                    slip_item.setForeground(QColor("#cc0000"))
                elif slip_v < 0:
                    slip_item.setForeground(QColor("#00aa00"))
            except (TypeError, ValueError):
                pass
            self.exec_table.setItem(i, 6, slip_item)
            self.exec_table.setItem(i, 7, self._centered_item(f"{float(lat):.1f}" if lat is not None else "—"))
            reason_it = self._centered_item(reason)
            ctx = r.get("execution_context")
            if isinstance(ctx, dict) and ctx:
                try:
                    payload = {
                        "reason": r.get("reason"),
                        "message": r.get("message"),
                        "execution_context": ctx,
                        "symbol": r.get("symbol"),
                        "side": r.get("side"),
                        "ok": r.get("ok"),
                    }
                    reason_it.setData(_EXEC_ROW_PAYLOAD_ROLE, json.dumps(payload, ensure_ascii=False))
                except Exception:
                    pass
                f = QFont(reason_it.font())
                if f.pointSize() <= 0:
                    app_f = QApplication.font()
                    ps = app_f.pointSize() if app_f.pointSize() > 0 else 9
                    f.setPointSize(ps)
                f.setUnderline(True)
                reason_it.setFont(f)
                reason_it.setForeground(QColor("#6eb5ff"))
                reason_it.setToolTip("انقر لعرض تقرير المؤشرات والتفاصيل المحفوظة مع هذا التنفيذ")
            self.exec_table.setItem(i, 8, reason_it)
        avg_slip = (slip_sum / slip_n) if slip_n > 0 else 0.0
        self.exec_summary_label.setText(
            f"آخر {len(rows)} تنفيذ | نجاح: {ok_count} | فشل: {fail_count} | متوسط الانزلاق: {avg_slip:+.3f}%"
        )

    def _open_execution_report_file(self):
        path = self._execution_report_path()
        try:
            if not os.path.isfile(path):
                with open(path, "a", encoding="utf-8"):
                    pass
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as e:
            QMessageBox.warning(self, "تقرير التنفيذ", f"تعذر فتح الملف:\n{path}\n\n{e}")

    def _on_exec_table_cell_clicked(self, row: int, column: int) -> None:
        if column != 8:
            return
        it = self.exec_table.item(row, 8)
        if it is None:
            return
        raw = it.data(_EXEC_ROW_PAYLOAD_ROLE)
        if not raw or not isinstance(raw, str):
            QMessageBox.information(
                self,
                "تقرير التنفيذ",
                "لا توجد لقطة مؤشرات محفوظة لهذا السطر.\n\n"
                "الصفوف القديمة (قبل التحديث) أو بعض الأوامر اليدوية قد لا تحتوي على حقل execution_context.",
            )
            return
        try:
            data = json.loads(raw)
        except Exception:
            QMessageBox.warning(self, "تقرير التنفيذ", "تعذر قراءة بيانات السطر.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("تفاصيل التنفيذ — مؤشرات وسياق البوت")
        dlg.resize(760, 560)
        v = QVBoxLayout(dlg)
        header = QLabel(
            f"الرمز: {data.get('symbol') or '—'} | العملية: {data.get('side') or '—'} | "
            f"الحالة: {'نجاح' if data.get('ok') else 'فشل'}"
        )
        header.setStyleSheet("color: #cfd6e6; font-weight: bold;")
        header.setWordWrap(True)
        v.addWidget(header)
        body = QTextEdit()
        body.setReadOnly(True)
        body.setStyleSheet(
            "QTextEdit { background-color: #1a1d24; color: #e8eaed; font-family: Consolas, 'Cascadia Mono', monospace; "
            "font-size: 11px; border: 1px solid #3a4555; border-radius: 6px; }"
        )
        reason = str(data.get("reason") or "—")
        msg = str(data.get("message") or "—")
        ctx = data.get("execution_context")
        parts: list[str] = [
            "── سبب الصف (العمود) ──\n",
            reason,
            "\n\n── رسالة المنصة / التنفيذ ──\n",
            msg,
            "\n",
        ]
        if isinstance(ctx, dict) and ctx:
            parts.append("\n── لقطة السياق: مؤشرات، سوق، ثقة، مركّب، تفاصيل حالة البوت (إن وُجدت) ──\n\n")
            try:
                parts.append(json.dumps(ctx, ensure_ascii=False, indent=2))
            except Exception:
                parts.append(str(ctx))
        else:
            parts.append("\n(لا يوجد حقل execution_context في هذا السجل.)")
        body.setPlainText("".join(parts))
        v.addWidget(body, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(dlg.accept)
        v.addWidget(bb)
        dlg.exec()

    @staticmethod
    def _centered_item(text: str) -> QTableWidgetItem:
        it = QTableWidgetItem(str(text))
        it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        return it
