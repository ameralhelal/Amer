from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QAbstractItemView,
    QPushButton,
    QMenu,
    QDialog,
)
from PyQt6.QtCore import Qt, QPoint, pyqtSignal, QTimer
from format_utils import format_price, format_currency
from translations import tr
from ui_palette import TOP_PANEL_BG, TOP_PANEL_BORDER, TOP_INNER_BG, TOP_TEXT_PRIMARY
from position_row_targets_dialog import PerPositionTakeProfitDialog, PerPositionStopLossDialog
from trade_history import suspect_placeholder_entry_price
from binance_chart_aliases import binance_spot_pair_symbol

log = logging.getLogger("trading.positions")

# استطلاع سعر سبوت Binance لرموز المراكز غير المعروضة على الشارت (خفيف، بدون WebSocket إضافي)
_OPEN_POS_PRICE_POLL_MS = 2800
_BINANCE_SPOT_TICKER_URL = "https://api.binance.com/api/v3/ticker/price"


def _fetch_binance_spot_price(symbol: str) -> float | None:
    sym = (symbol or "").strip().upper()
    if not sym or not sym.isalnum():
        return None
    for attempt in range(2):
        try:
            url = f"{_BINANCE_SPOT_TICKER_URL}?symbol={sym}"
            with urllib.request.urlopen(url, timeout=2.8) as resp:
                j = json.load(resp)
            p = float(j.get("price") or 0)
            if p > 0:
                return p
        except Exception:
            pass
        if attempt == 0:
            time.sleep(0.18)
    return None


# معرّف طلب فتح المركز في eToro (orderID) — لإغلاق الصف عندما لا يتوفر position_id ولا get_positions
_ETORO_OPEN_ORDER_ROLE = int(Qt.ItemDataRole.UserRole) + 10
_ETORO_RAW_SYMBOL_ROLE = int(Qt.ItemDataRole.UserRole) + 11
# أهداف لكل صف: حد بيع / وقف خسارة (لا تُحفظ في الإعدادات العامة)
_ROW_TP_TYPE_ROLE = int(Qt.ItemDataRole.UserRole) + 30
_ROW_TP_VALUE_ROLE = int(Qt.ItemDataRole.UserRole) + 31
_ROW_SL_TYPE_ROLE = int(Qt.ItemDataRole.UserRole) + 32
_ROW_SL_VALUE_ROLE = int(Qt.ItemDataRole.UserRole) + 33
# زوج Binance المستخدم لسعر الصف والربح — ثابت عند فتح الصف ولا يتبع تغيير عملة الشارت
ROW_PRICE_SYMBOL_ROLE = int(Qt.ItemDataRole.UserRole) + 34

# --- عقد سعر المراكز (اقرأ قبل أي تعديل هنا أو في المزامنة) ---
# المصدران: (1) last_price لـ current_symbol من الشارت → _price_by_symbol[ck] في update_pnl
# (2) استطلاع Binance لكل مفتاح في _row_binance_price_key غير المملوء.
# ترتيب _row_binance_price_key: النص المعروض بزوج USDT صالح يغلب ROW_PRICE_SYMBOL_ROLE حتى لا يتعارض
# كاش/دمج eToro مع العمود؛ ثم الدور؛ ثم raw/disp.
# إذا غيّرت هذا الترتيب راجع: merge_etoro_positions_from_exchange، add_or_update_position،
# _set_row_price_symbol، وtrading_panel._persist_etoro_positions_cache_from_table (price_symbol).
# إغلاق/مطابقة الرمز على eToro منطق منفصل في exchange_etoro (لا تخلط افتراضات Binance هناك).


class OpenPositionsPanel(QWidget):
    pnl_updated = pyqtSignal(float)  # يُبعث بعد تحديث PnL لربط حد الخسارة اليومية
    close_row_requested = pyqtSignal(dict)  # إغلاق صف: symbol, entry_price, quantity, position_id?, row
    refresh_positions_requested = pyqtSignal()  # طلب تحديث المراكز من المنصة (زر «تحديث المراكز»)

    def __init__(self):
        super().__init__()

        self.current_symbol = "BTCUSDT"   # ← إضافة مهمة
        self.last_price = None
        # سعر آخر معروف لكل زوج Binance (مفتاح كبير مثل BTCUSDT) — الشارت يحدّث الرمز الحي، والباقي عبر استطلاع دوري
        self._price_by_symbol: dict[str, float] = {}
        self._price_poll_busy = False
        self._price_poll_timer = QTimer(self)
        self._price_poll_timer.setInterval(_OPEN_POS_PRICE_POLL_MS)
        self._price_poll_timer.timeout.connect(self._schedule_price_poll_for_open_rows)
        self._price_poll_timer.start()

        self.setObjectName("OpenPositionsPanel")

        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)

        title = QLabel("📊 المراكز المفتوحة — الربح/الخسارة")
        title.setObjectName("PositionsTitle")
        self.refresh_positions_btn = QPushButton("تحديث المراكز")
        self.refresh_positions_btn.setMinimumHeight(28)
        self.refresh_positions_btn.setToolTip("إعادة جلب المراكز المفتوحة من المنصة")
        self.refresh_positions_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.refresh_positions_btn.setStyleSheet(
            "background-color:#2d4a3e; color:#b8e6cf; border:1px solid #3d6b52; border-radius:4px; font-weight:bold;"
        )
        self.refresh_positions_btn.clicked.connect(self._on_refresh_clicked)
        title_row = QHBoxLayout()
        title_row.addWidget(title, 1)
        title_row.addWidget(self.refresh_positions_btn, 0)
        layout.addLayout(title_row)
        # توضيح: تغيير عملة الشارت لا يمسح المراكز ولا ينفّذ بيعاً — فقط يوقف تحديث السعر الحي لغير رمز الشارت.
        self._positions_hint = QLabel(
            "تغيير عملة الشارت لا يُغلق المراكز ولا يُسجّل بيعاً في السجل. "
            "«السعر الحالي / التغير % / الربح» لكل صف حسب زوج السعر المثبّت لذلك المركز (لا يتبع تغيير الشارت) — تدفق Binance للزوج + استطلاع خفيف عند الحاجة. "
            "وقف الخسارة وحد البيع والتتبع والبوت التلقائي ما زالت مرتبطة برمز الشارت الحالي وسعره الحي فقط — مركز برمز آخر لا يُغلق تلقائياً حتى تعود لذلك الرمز أو تغلق يدوياً."
        )
        self._positions_hint.setWordWrap(True)
        self._positions_hint.setObjectName("PositionsHint")
        self._positions_hint.setStyleSheet("color: #999; font-size: 11px; padding: 2px 4px 6px 4px;")

        layout.addWidget(self._positions_hint)

        # جدول الصفقات (… السعر الحالي، تغير السعر نسبة/فرق، الربح/الخسارة، إغلاق)
        self.table = QTableWidget()
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            [
                "الرمز",
                "سعر الدخول",
                "الكمية",
                "القيمة",
                "السعر الحالي",
                "التغير %",
                "الربح/الخسارة",
                "إغلاق",
            ]
        )
        self.table.setObjectName("PositionsTable")
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignCenter)
        # توزيع الأعمدة على كامل عرض الشاشة
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(7, 52)
        self.table.verticalHeader().setVisible(False)
        # ارتفاع يكفي لزر الإغلاق + حدود الخلية (6px padding على العناصر) دون قص أسفل الزر
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.verticalHeader().setMinimumSectionSize(34)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        def _on_pos_cell_clicked(r, _c):
            self.table.selectRow(r)
            self.table.setCurrentCell(r, 0)

        self.table.cellClicked.connect(_on_pos_cell_clicked)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_positions_context_menu)

        layout.addWidget(self.table, 1)
        self.setLayout(layout)

        def _ci(text):
            it = QTableWidgetItem(str(text))
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            return it
        self._ci = _ci

        # -----------------------------
        # QSS (ستايل احترافي)
        # -----------------------------
        self.setStyleSheet(f"""
            #OpenPositionsPanel {{
                background-color: {TOP_PANEL_BG};
                border: 1px solid {TOP_PANEL_BORDER};
                border-radius: 12px;
            }}

            #PositionsTitle {{
                font-size: 18px;
                font-weight: bold;
                color: {TOP_TEXT_PRIMARY};
            }}

            #PositionsTable {{
                background-color: {TOP_INNER_BG};
                color: {TOP_TEXT_PRIMARY};
                gridline-color: {TOP_PANEL_BORDER};
                selection-background-color: #2563eb;
                selection-color: #ffffff;
                border: 1px solid {TOP_PANEL_BORDER};
            }}
            QHeaderView::section {{
                background-color: {TOP_PANEL_BG};
                color: {TOP_TEXT_PRIMARY};
                padding: 6px;
                border: none;
            }}

            QTableWidget::item {{
                padding: 6px;
                border: 1px solid {TOP_PANEL_BORDER};
            }}

            QTableWidget::item:selected {{
                background-color: #2563eb;
                color: #ffffff;
            }}
            QTableWidget::item:selected:active {{
                background-color: #3b82f6;
                color: #ffffff;
            }}
            QTableWidget::item:hover {{
                background-color: #232a36;
            }}
            QTableWidget::item:hover:selected {{
                background-color: #3b82f6;
                color: #ffffff;
            }}
        """)

    # ----------------------------------------------------
    # تحديث السعر
    # ----------------------------------------------------
    def update_price(self, price: float):
        self.last_price = price
        self.update_pnl()

    def _row_binance_price_key(self, row: int) -> str | None:
        """مفتاح زوج Binance لاستخدامه في خريطة الأسعار (العمود المعروض أو الرمز الخام)."""
        sym_item = self.table.item(row, 0)
        if sym_item is None:
            return None
        disp = (sym_item.text() or "").strip().upper()
        # النص الظاهر في عمود الرمز يجب أن يغلب ROW_PRICE_SYMBOL_ROLE: كاش قديم أو دمج eToro قد
        # يترك دوراً لا يطابق العرض (مثلاً رمز الشارت السابق)، نُفضّل زوج USDT الصريح للاستطلاع.
        if len(disp) >= 7 and disp.endswith("USDT") and disp.isalnum():
            try:
                return binance_spot_pair_symbol(disp).upper()
            except Exception:
                return disp if len(disp) >= 4 else None
        ps = sym_item.data(ROW_PRICE_SYMBOL_ROLE)
        if ps:
            try:
                s = str(ps).strip().upper()
            except Exception:
                s = ""
            if s and not s.startswith("ETORO_"):
                try:
                    return binance_spot_pair_symbol(s).upper()
                except Exception:
                    return s if len(s) >= 4 else None
        raw = str(sym_item.data(_ETORO_RAW_SYMBOL_ROLE) or "").strip().upper()
        if raw.startswith("ETORO_"):
            s = disp
        else:
            s = disp or raw
        if not s or s.startswith("ETORO_"):
            return None
        try:
            return binance_spot_pair_symbol(s).upper()
        except Exception:
            return s if len(s) >= 4 else None

    def _set_row_price_symbol(self, sym_item: QTableWidgetItem, sym_u: str, sym_disp: str) -> None:
        """يُثبّت زوج السعر المرجعي لـ Binance — لا يتغير عند تبديل عملة الشارت."""
        if sym_item is None:
            return
        try:
            if sym_item.data(ROW_PRICE_SYMBOL_ROLE):
                return
        except Exception:
            pass
        if sym_u.startswith("ETORO_"):
            s = (sym_disp or "").strip().upper()
            if not s or s.startswith("ETORO_"):
                return
        else:
            s = (sym_u or "").strip().upper()
        if not s:
            return
        try:
            sym_item.setData(ROW_PRICE_SYMBOL_ROLE, binance_spot_pair_symbol(s).upper())
        except Exception:
            sym_item.setData(ROW_PRICE_SYMBOL_ROLE, s.upper())

    def _schedule_price_poll_for_open_rows(self):
        """جلب أسعار سبوت Binance لرموز الجدول التي لا يتوفر لها سعر لحظي بعد.

        سابقاً كنا نستثني رمز الشارت من الاستطلاع لأن WebSocket يغذّيه؛ لكن إذا تأخر
        التدفق أو لم يُحمَّل السعر بعد، يبقى عمود السعر/الربح «—». نستطلع أي رمز
        ما دام لا يوجد له قيمة صالحة في `_price_by_symbol` (بما فيه رمز الشارت).
        """
        if self._price_poll_busy:
            return
        keys: set[str] = set()
        for row in range(self.table.rowCount()):
            k = self._row_binance_price_key(row)
            if k:
                keys.add(k)
        if not keys:
            return
        to_fetch: list[str] = []
        for k in sorted(keys):
            px = self._price_by_symbol.get(k)
            try:
                px_f = float(px) if px is not None else 0.0
            except (TypeError, ValueError):
                px_f = 0.0
            if px_f <= 0:
                to_fetch.append(k)
        if not to_fetch:
            return
        self._price_poll_busy = True

        def _run():
            updates: dict[str, float] = {}
            for sym in to_fetch:
                px = _fetch_binance_spot_price(sym)
                if px is not None and px > 0:
                    updates[sym] = px

            def _done():
                self._price_poll_busy = False
                for k, v in updates.items():
                    if v and v > 0:
                        self._price_by_symbol[k] = float(v)
                self.update_pnl()

            QTimer.singleShot(0, _done)

        threading.Thread(target=_run, daemon=True).start()

    def _on_refresh_clicked(self):
        """إشارة بصرية فورية: الزر تحرّك حتى قبل رجوع المنصة."""
        try:
            self.refresh_positions_btn.setEnabled(False)
            self.refresh_positions_btn.setText("جاري التحديث…")
            QTimer.singleShot(1500, self._restore_refresh_button_state)
        except Exception:
            pass
        self.refresh_positions_requested.emit()

    def _restore_refresh_button_state(self):
        try:
            self.refresh_positions_btn.setEnabled(True)
            self.refresh_positions_btn.setText("تحديث المراكز")
        except Exception:
            pass

    # ----------------------------------------------------
    # تحميل المراكز من المنصة (بعد جلبها من API)
    # ----------------------------------------------------
    def set_positions_from_exchange(self, positions: list):
        """استبدال جدول المراكز بقائمة من المنصة. كل عنصر: symbol, entry_price, quantity، واختياري position_id (eToro)."""
        self.table.setRowCount(0)
        for p in positions or []:
            sym = p.get("symbol") or ""
            entry = float(p.get("entry_price") or 0)
            qty = float(p.get("quantity") or 0)
            pid = p.get("position_id")
            try:
                pid_i = int(pid) if pid is not None else None
            except (TypeError, ValueError):
                pid_i = None
            oid = p.get("order_id") or p.get("orderID") or p.get("OrderID")
            try:
                oid_i = int(oid) if oid is not None else None
            except (TypeError, ValueError):
                oid_i = None
            ps = (p.get("price_symbol") or "").strip() or None
            if sym and qty > 0:
                self.add_or_update_position(
                    sym,
                    entry,
                    qty,
                    position_id=pid_i,
                    etoro_open_order_id=oid_i,
                    price_symbol=ps,
                )
        self.update_pnl()

    def has_pending_etoro_open_rows(self) -> bool:
        """صف بلا position_id لكن مع order فتح eToro — شراء حديث قبل أن تُحدّث المنصة القائمة."""
        for r in range(self.table.rowCount()):
            it0 = self.table.item(r, 0)
            if it0 is None:
                continue
            try:
                pid = int(it0.data(Qt.ItemDataRole.UserRole) or 0)
            except (TypeError, ValueError):
                pid = 0
            if pid > 0:
                continue
            try:
                oid = int(it0.data(_ETORO_OPEN_ORDER_ROLE) or 0)
            except (TypeError, ValueError):
                oid = 0
            if oid > 0:
                return True
        return False

    def _etoro_api_symbol_matches_row(self, it0: QTableWidgetItem, api_sym: str) -> bool:
        """تطابق رمز صف الجدول مع رمز الـ API رغم اختلاف الشكل (BTCUSDT مقابل ETORO_<id>)."""
        au = (api_sym or "").strip().upper()
        if not au or it0 is None:
            return False
        rs = (it0.text() or "").strip().upper()
        rr = str(it0.data(_ETORO_RAW_SYMBOL_ROLE) or "").strip().upper()
        if rs == au or rr == au:
            return True
        chart = (self.current_symbol or "").strip().upper()
        if not chart:
            return False
        if au.startswith("ETORO_") and not chart.startswith("ETORO_"):
            if rs == chart or rr == chart:
                return True
        if (rs.startswith("ETORO_") or rr.startswith("ETORO_")) and not au.startswith("ETORO_"):
            if au == chart or rs == au or rr == au:
                return True
        return False

    def merge_etoro_positions_from_exchange(self, positions: list):
        """
        دمج استجابة eToro مع الجدول دون مسحه بالكامل.
        يحل تأخر API عند الصفقة الثانية: استبدال الجدول بمركز واحد فقط كان يحذف الصف الجديد مؤقتاً.
        """
        api_rows: list[dict] = []
        for p in positions or []:
            sym = p.get("symbol") or ""
            entry = float(p.get("entry_price") or 0)
            qty = float(p.get("quantity") or 0)
            pid = p.get("position_id")
            try:
                pid_i = int(pid) if pid is not None else None
            except (TypeError, ValueError):
                pid_i = None
            oid = p.get("order_id") or p.get("orderID") or p.get("OrderID")
            try:
                oid_i = int(oid) if oid is not None else None
            except (TypeError, ValueError):
                oid_i = None
            if sym and qty > 0:
                api_rows.append(
                    {
                        "symbol": sym,
                        "entry_price": entry,
                        "quantity": qty,
                        "position_id": pid_i,
                        "order_id": oid_i,
                    }
                )

        api_by_pid: dict[int, dict] = {}
        for a in api_rows:
            pi = a.get("position_id")
            if pi is not None and int(pi) > 0:
                api_by_pid[int(pi)] = a

        matched: set[int] = set()
        for r in range(self.table.rowCount()):
            it0 = self.table.item(r, 0)
            if it0 is None:
                continue
            try:
                pid = int(it0.data(Qt.ItemDataRole.UserRole) or 0)
            except (TypeError, ValueError):
                pid = 0
            if pid <= 0 or pid not in api_by_pid:
                continue
            a = api_by_pid[pid]
            entry_price = float(a["entry_price"])
            quantity = float(a["quantity"])
            symbol = str(a["symbol"] or "").strip().upper()
            qty_str = f"{quantity:.8f}".rstrip("0").rstrip(".") if quantity else "0"
            value_usdt = entry_price * quantity
            sym_u = symbol
            sym_disp = sym_u
            if sym_u.startswith("ETORO_"):
                ps_existing = it0.data(ROW_PRICE_SYMBOL_ROLE)
                prev_txt = (it0.text() or "").strip().upper()
                if ps_existing:
                    sym_disp = str(ps_existing).strip().upper()
                elif prev_txt and not prev_txt.startswith("ETORO_"):
                    sym_disp = prev_txt
                else:
                    cur = (self.current_symbol or "").strip().upper()
                    if cur and not cur.startswith("ETORO_"):
                        sym_disp = cur
                    else:
                        sym_disp = sym_u
            it0.setText(sym_disp)
            if sym_disp != sym_u:
                it0.setData(_ETORO_RAW_SYMBOL_ROLE, sym_u)
            else:
                it0.setData(_ETORO_RAW_SYMBOL_ROLE, None)
            if not sym_u.startswith("ETORO_"):
                try:
                    it0.setData(ROW_PRICE_SYMBOL_ROLE, binance_spot_pair_symbol(sym_u).upper())
                except Exception:
                    pass
            else:
                self._set_row_price_symbol(it0, sym_u, sym_disp)
            it0.setData(Qt.ItemDataRole.UserRole, pid)
            oid_i = a.get("order_id")
            if oid_i is not None:
                try:
                    oi = int(oid_i)
                    if oi > 0 and it0.data(_ETORO_OPEN_ORDER_ROLE) in (None, 0, ""):
                        it0.setData(_ETORO_OPEN_ORDER_ROLE, oi)
                except (TypeError, ValueError):
                    pass
            it1 = self.table.item(r, 1)
            it2 = self.table.item(r, 2)
            if it1 is not None:
                it1.setText(str(entry_price))
            if it2 is not None:
                it2.setText(qty_str)
            val_it = self.table.item(r, 3)
            if val_it is not None:
                val_it.setText(format_currency(value_usdt))
            matched.add(pid)
            self._refresh_row_targets_tooltip(r)

        for a in api_rows:
            pi = a.get("position_id")
            if pi is None or int(pi) <= 0:
                continue
            if int(pi) in matched:
                continue
            # إذا كان هناك صف محلي معلّق (بدون position_id) لنفس الرمز/طلب الفتح،
            # اربطه بالمركز القادم من API بدل إضافة صف جديد (منع تضاعف 20k/40k الظاهري).
            linked_pending = False
            sym_u = str(a.get("symbol") or "").strip().upper()
            oid_a = a.get("order_id")
            try:
                oid_ai = int(oid_a) if oid_a is not None else 0
            except (TypeError, ValueError):
                oid_ai = 0
            for r in range(self.table.rowCount() - 1, -1, -1):
                it0 = self.table.item(r, 0)
                it1 = self.table.item(r, 1)
                it2 = self.table.item(r, 2)
                if it0 is None or it1 is None or it2 is None:
                    continue
                try:
                    row_pid = int(it0.data(Qt.ItemDataRole.UserRole) or 0)
                except (TypeError, ValueError):
                    row_pid = 0
                if row_pid > 0:
                    continue
                try:
                    row_oid = int(it0.data(_ETORO_OPEN_ORDER_ROLE) or 0)
                except (TypeError, ValueError):
                    row_oid = 0
                # orderID فريد على eToro: يربط الصف المعلق فوراً بغض النظر عن شكل الرمز (BTCUSDT vs ETORO_n)
                if oid_ai > 0 and row_oid > 0:
                    if row_oid != oid_ai:
                        continue
                elif oid_ai > 0:
                    if not self._etoro_api_symbol_matches_row(it0, sym_u):
                        continue
                else:
                    if not self._etoro_api_symbol_matches_row(it0, sym_u):
                        continue
                qty = float(a["quantity"])
                entry = float(a["entry_price"])
                qty_str = f"{qty:.8f}".rstrip("0").rstrip(".") if qty else "0"
                it0.setData(Qt.ItemDataRole.UserRole, int(pi))
                if oid_ai > 0:
                    it0.setData(_ETORO_OPEN_ORDER_ROLE, oid_ai)
                it1.setText(str(entry))
                it2.setText(qty_str)
                val_it = self.table.item(r, 3)
                if val_it is not None:
                    val_it.setText(format_currency(entry * qty))
                sym_u2 = str(sym_u).strip().upper()
                sym_disp2 = sym_u2
                if sym_u2.startswith("ETORO_"):
                    prev_txt = (it0.text() or "").strip().upper()
                    ps_existing = it0.data(ROW_PRICE_SYMBOL_ROLE)
                    if ps_existing:
                        sym_disp2 = str(ps_existing).strip().upper()
                    elif prev_txt and not prev_txt.startswith("ETORO_"):
                        sym_disp2 = prev_txt
                    else:
                        cur = (self.current_symbol or "").strip().upper()
                        sym_disp2 = cur if (cur and not cur.startswith("ETORO_")) else sym_u2
                    it0.setText(sym_disp2)
                    if sym_disp2 != sym_u2:
                        it0.setData(_ETORO_RAW_SYMBOL_ROLE, sym_u2)
                else:
                    it0.setText(sym_u2)
                    it0.setData(_ETORO_RAW_SYMBOL_ROLE, None)
                    try:
                        it0.setData(ROW_PRICE_SYMBOL_ROLE, binance_spot_pair_symbol(sym_u2).upper())
                    except Exception:
                        pass
                if sym_u2.startswith("ETORO_"):
                    self._set_row_price_symbol(it0, sym_u2, sym_disp2)
                matched.add(int(pi))
                linked_pending = True
                self._refresh_row_targets_tooltip(r)
                log.debug("[مراكز] ربط صف معلّق بمركز API position_id=%s", int(pi))
                break
            if linked_pending:
                continue
            self.add_or_update_position(
                a["symbol"],
                a["entry_price"],
                a["quantity"],
                position_id=a["position_id"],
                etoro_open_order_id=a.get("order_id"),
            )

        self.update_pnl()

    # ----------------------------------------------------
    # إضافة أو تحديث صفقة (symbol, entry_price, quantity)
    # ----------------------------------------------------
    def add_or_update_position(
        self,
        symbol,
        entry_price,
        quantity=0.0,
        position_id=None,
        etoro_open_order_id=None,
        *,
        price_symbol: str | None = None,
    ):
        quantity = float(quantity) if quantity else 0.0
        entry_price = float(entry_price) if entry_price else 0.0
        value_usdt = entry_price * quantity
        qty_str = f"{quantity:.8f}".rstrip("0").rstrip(".") if quantity else "0"
        sym_u = str(symbol or "").strip().upper()
        sym_disp = sym_u
        # توحيد العرض: إذا eToro أعاد ETORO_<instrumentId> نعرض رمز الشاشة الحالي (مثل BTCUSDT).
        if sym_u.startswith("ETORO_"):
            cur = (self.current_symbol or "").strip().upper()
            if cur and not cur.startswith("ETORO_"):
                sym_disp = cur
        # كل صفقة شراء = صف مستقل (دخول/خروج واضح). لا ندمج حسب الرمز/السعر/الكمية.
        # الاستثناء: إعادة نفس position_id من المنصة (مزامنة/إشعار مكرر) → نحدّث الصف الموجود فقط.
        try:
            pi_in = int(position_id) if position_id is not None else 0
        except (TypeError, ValueError):
            pi_in = 0
        if pi_in > 0:
            for r in range(self.table.rowCount()):
                it0 = self.table.item(r, 0)
                it1 = self.table.item(r, 1)
                it2 = self.table.item(r, 2)
                if it0 is None or it1 is None or it2 is None:
                    continue
                try:
                    er = it0.data(Qt.ItemDataRole.UserRole)
                    if er is None or int(er) != pi_in:
                        continue
                except (TypeError, ValueError):
                    continue
                it1.setText(str(entry_price))
                it2.setText(qty_str)
                val_it = self.table.item(r, 3)
                if val_it is not None:
                    val_it.setText(format_currency(value_usdt))
                if etoro_open_order_id is not None:
                    try:
                        oi = int(etoro_open_order_id)
                        if oi > 0 and it0.data(_ETORO_OPEN_ORDER_ROLE) in (None, 0, ""):
                            it0.setData(_ETORO_OPEN_ORDER_ROLE, oi)
                    except (TypeError, ValueError):
                        pass
                if price_symbol:
                    try:
                        it0.setData(
                            ROW_PRICE_SYMBOL_ROLE,
                            binance_spot_pair_symbol(str(price_symbol).strip()).upper(),
                        )
                    except Exception:
                        it0.setData(ROW_PRICE_SYMBOL_ROLE, str(price_symbol).strip().upper())
                elif not sym_u.startswith("ETORO_"):
                    try:
                        it0.setData(ROW_PRICE_SYMBOL_ROLE, binance_spot_pair_symbol(sym_u).upper())
                    except Exception:
                        pass
                log.debug("[مراكز] تحديث صف موجود لنفس position_id=%s", pi_in)
                self.update_pnl()
                self._refresh_row_targets_tooltip(r)
                return
        try:
            oi_in = int(etoro_open_order_id) if etoro_open_order_id is not None else 0
        except (TypeError, ValueError):
            oi_in = 0
        if pi_in <= 0 and oi_in > 0:
            for r in range(self.table.rowCount()):
                it0 = self.table.item(r, 0)
                it1 = self.table.item(r, 1)
                it2 = self.table.item(r, 2)
                if it0 is None or it1 is None or it2 is None:
                    continue
                try:
                    rp = int(it0.data(Qt.ItemDataRole.UserRole) or 0)
                except (TypeError, ValueError):
                    rp = 0
                if rp > 0:
                    continue
                try:
                    ro = int(it0.data(_ETORO_OPEN_ORDER_ROLE) or 0)
                except (TypeError, ValueError):
                    ro = 0
                if ro != oi_in:
                    continue
                it1.setText(str(entry_price))
                it2.setText(qty_str)
                val_it = self.table.item(r, 3)
                if val_it is not None:
                    val_it.setText(format_currency(value_usdt))
                self.update_pnl()
                self._refresh_row_targets_tooltip(r)
                log.debug("[مراكز] تحديث صف معلّق لنفس etoro_open_order_id=%s (بدل صف ثانٍ)", oi_in)
                return
        row = self.table.rowCount()
        self.table.insertRow(row)
        sym_item = self._ci(sym_disp)
        if sym_disp != sym_u:
            sym_item.setData(_ETORO_RAW_SYMBOL_ROLE, sym_u)
        if position_id is not None:
            try:
                sym_item.setData(Qt.ItemDataRole.UserRole, int(position_id))
            except (TypeError, ValueError):
                pass
        if etoro_open_order_id is not None:
            try:
                oi = int(etoro_open_order_id)
                if oi > 0:
                    sym_item.setData(_ETORO_OPEN_ORDER_ROLE, oi)
            except (TypeError, ValueError):
                pass
        if price_symbol:
            try:
                sym_item.setData(
                    ROW_PRICE_SYMBOL_ROLE,
                    binance_spot_pair_symbol(str(price_symbol).strip()).upper(),
                )
            except Exception:
                sym_item.setData(ROW_PRICE_SYMBOL_ROLE, str(price_symbol).strip().upper())
        elif not sym_u.startswith("ETORO_"):
            try:
                sym_item.setData(ROW_PRICE_SYMBOL_ROLE, binance_spot_pair_symbol(sym_u).upper())
            except Exception:
                pass
        else:
            self._set_row_price_symbol(sym_item, sym_u, sym_disp)
        self.table.setItem(row, 0, sym_item)
        self.table.setItem(row, 1, self._ci(str(entry_price)))
        self.table.setItem(row, 2, self._ci(qty_str))
        self.table.setItem(row, 3, self._ci(format_currency(value_usdt)))
        self.table.setItem(row, 4, self._ci("-"))
        self.table.setItem(row, 5, self._ci("-"))
        self.table.setItem(row, 6, self._ci("-"))
        self._attach_close_button(row)

        self.update_pnl()
        self._refresh_row_targets_tooltip(row)
        # لا ننتظر دورة المؤقت فقط: إن كان رمز المركز = الشارت ولم يصل بعد سعر WS
        QTimer.singleShot(120, self._schedule_price_poll_for_open_rows)

    def apply_position_id_after_buy(
        self,
        symbol: str,
        entry_price: float,
        quantity: float,
        position_id: int,
        etoro_open_order_id: int | None = None,
    ) -> bool:
        """يضبط position_id على الصف المناسب بعد الحل في الخلفية.
        مع عدة صفوف بنفس الرمز/السعر/الكمية يُربَط عبر order_id إن وُجد."""
        try:
            pid = int(position_id)
        except (TypeError, ValueError):
            return False
        if pid <= 0:
            return False
        sym_u = (symbol or "").strip().upper()
        ep = float(entry_price or 0)
        q = float(quantity or 0)
        tol_e = max(abs(ep) * 1e-4, 0.5)
        tol_q = max(abs(q) * 1e-5, 1e-8)

        def _sym_ok(it0: QTableWidgetItem) -> bool:
            rs = (it0.text() or "").strip().upper()
            rr = str(it0.data(_ETORO_RAW_SYMBOL_ROLE) or "").strip().upper()
            return rs == sym_u or rr == sym_u

        try:
            oid_w = int(etoro_open_order_id) if etoro_open_order_id is not None else 0
        except (TypeError, ValueError):
            oid_w = 0
        if oid_w > 0:
            for row in range(self.table.rowCount() - 1, -1, -1):
                it0 = self.table.item(row, 0)
                if it0 is None or not _sym_ok(it0):
                    continue
                try:
                    ro = int(it0.data(_ETORO_OPEN_ORDER_ROLE) or 0)
                except (TypeError, ValueError):
                    ro = 0
                if ro != oid_w:
                    continue
                try:
                    raw = it0.data(Qt.ItemDataRole.UserRole)
                    if raw is not None and int(raw) > 0:
                        continue
                except (TypeError, ValueError):
                    pass
                it0.setData(Qt.ItemDataRole.UserRole, pid)
                log.info("[مراكز] تم ربط position_id=%s بصف order_id=%s", pid, oid_w)
                return True

        for row in range(self.table.rowCount() - 1, -1, -1):
            it0 = self.table.item(row, 0)
            if it0 is None or not _sym_ok(it0):
                continue
            try:
                raw = it0.data(Qt.ItemDataRole.UserRole)
                if raw is not None and int(raw) > 0:
                    continue
            except (TypeError, ValueError):
                pass
            try:
                re = float(str(self.table.item(row, 1).text() or "0").replace(",", ""))
                rq = float(str(self.table.item(row, 2).text() or "0").replace(",", ""))
            except (ValueError, TypeError, AttributeError):
                continue
            if abs(re - ep) <= tol_e and abs(rq - q) <= tol_q:
                it0.setData(Qt.ItemDataRole.UserRole, pid)
                log.info("[مراكز] تم ربط position_id=%s بالصف %s", pid, sym_u)
                return True
        return False

    def _attach_close_button(self, row: int):
        btn = QPushButton("✕")
        btn.setToolTip("إغلاق هذا المركز")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setFixedSize(22, 17)
        btn.setStyleSheet(
            "background-color:#5c2a2a; color:#fff; border:1px solid #7a3d3d; "
            "border-radius:3px; padding:0; margin:0; font-size:10px; font-weight:bold;"
        )

        def _emit_close():
            # الصف الحقيقي بعد حذف صفوف سابقة (لا نعتمد على row المُغلق عند إنشاء الزر)
            sender_btn = self.sender()
            row_now = -1
            if sender_btn is not None:
                for r in range(self.table.rowCount()):
                    if self.table.cellWidget(r, 7) is sender_btn:
                        row_now = r
                        break
            if row_now < 0:
                row_now = row
            sym_item = self.table.item(row_now, 0)
            entry_item = self.table.item(row_now, 1)
            qty_item = self.table.item(row_now, 2)
            if not sym_item or not entry_item or not qty_item:
                return
            try:
                sym = (sym_item.text() or "").strip().upper()
                entry = float(entry_item.text() or 0)
                qty = float(qty_item.text() or 0)
            except (ValueError, TypeError):
                return
            if not sym or qty <= 0:
                return
            pid = None
            try:
                raw = sym_item.data(Qt.ItemDataRole.UserRole)
                if raw is not None:
                    pid = int(raw)
            except (TypeError, ValueError):
                pid = None
            d = {"symbol": sym, "entry_price": entry, "quantity": qty, "row": row_now}
            if pid is not None and pid > 0:
                d["position_id"] = pid
            try:
                oid_raw = sym_item.data(_ETORO_OPEN_ORDER_ROLE)
                if oid_raw is not None:
                    oi = int(oid_raw)
                    if oi > 0:
                        d["etoro_open_order_id"] = oi
            except (TypeError, ValueError):
                pass
            log.info(
                "[إغلاق صف] symbol=%s entry=%s qty=%s position_id=%s order_id=%s",
                (self.current_symbol if str(sym).startswith("ETORO_") and self.current_symbol else sym),
                entry,
                qty,
                d.get("position_id"),
                d.get("etoro_open_order_id"),
            )
            self.close_row_requested.emit(d)

        btn.clicked.connect(_emit_close)
        cell = QWidget()
        cell.setObjectName("PositionsCloseCell")
        cell_layout = QHBoxLayout(cell)
        # هامش عمودي أوضح حتى لا يلتصق الزر بخط شبكة الصف السفلي فيُرى وكأنه مقصوص
        cell_layout.setContentsMargins(3, 4, 3, 4)
        cell_layout.setSpacing(0)
        cell_layout.addStretch(1)
        cell_layout.addWidget(btn, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter)
        cell_layout.addStretch(1)
        self.table.setCellWidget(row, 7, cell)
        # تأكيد ارتفاع الصف بعد وضع الودجت (أحياناً لا يُحسب خلية الزر في القيمة الافتراضية)
        rh = self.table.rowHeight(row)
        need = max(38, rh)
        self.table.setRowHeight(row, need)

    def _row_matches_chart_symbol(self, row: int) -> bool:
        """هل صف المركز يطابق رمز الشارت الحالي (لعرض السعر الحي والتغير والربح لهذا الرمز فقط)."""
        sym_item = self.table.item(row, 0)
        if sym_item is None:
            return False
        cur = (self.current_symbol or "").strip().upper()
        if not cur:
            return False
        disp = (sym_item.text() or "").strip().upper()
        if disp == cur:
            return True
        raw = str(sym_item.data(_ETORO_RAW_SYMBOL_ROLE) or "").strip().upper()
        return bool(raw and raw == cur)

    # ----------------------------------------------------
    # تحديث الأرباح والخسائر
    # ----------------------------------------------------
    def update_pnl(self):
        if self.last_price is not None:
            try:
                lp = float(self.last_price)
                if lp > 0:
                    ck = binance_spot_pair_symbol(self.current_symbol or "").upper()
                    if ck:
                        self._price_by_symbol[ck] = lp
            except (TypeError, ValueError):
                pass

        # عندما التبويب غير ظاهر (QStackedWidget يخفي الصفحة) لا نُعيد رسم خلايا الجدول كل تيك؛
        # نُحدّث خريطة السعر أعلاه ونُصدِر إجمالي الربح فقط — SL/التتبع يعتمدان على _last_price
        # في لوحة التداول وعلى هذه الخريطة في find_any_row_stop_loss_hit.
        if not self.isVisible():
            try:
                self.pnl_updated.emit(self.get_total_pnl())
            except Exception:
                pass
            return

        for row in range(self.table.rowCount()):
            entry_item = self.table.item(row, 1)
            qty_item = self.table.item(row, 2)
            if entry_item is None or qty_item is None:
                continue
            try:
                entry_price = float(entry_item.text() or 0)
                qty = float(qty_item.text() or 0)
            except (ValueError, TypeError):
                continue
            sym_key = self._row_binance_price_key(row)
            if not sym_key:
                self.table.setItem(row, 4, self._ci("—"))
                self.table.setItem(row, 5, self._ci("—"))
                self.table.setItem(row, 6, self._ci("—"))
                continue
            cur_px = self._price_by_symbol.get(sym_key)
            if cur_px is None or cur_px <= 0:
                self.table.setItem(row, 4, self._ci("—"))
                self.table.setItem(row, 5, self._ci("—"))
                self.table.setItem(row, 6, self._ci("—"))
                continue
            cur_px = float(cur_px)
            pnl_usdt = (cur_px - entry_price) * qty if qty else 0.0
            self.table.setItem(row, 4, self._ci(format_price(cur_px)))
            delta = cur_px - entry_price
            if entry_price:
                pct = delta / entry_price * 100.0
                chg_item = self._ci(f"{pct:+.2f}٪")
            else:
                chg_item = self._ci("—")
            chg_item.setToolTip(
                "نسبة تغيّر السعر الحالي عن سعر الشراء (دخول المركز)."
            )
            if not entry_price:
                chg_item.setForeground(Qt.GlobalColor.lightGray)
            elif delta > 0:
                chg_item.setForeground(Qt.GlobalColor.green)
            elif delta < 0:
                chg_item.setForeground(Qt.GlobalColor.red)
            else:
                chg_item.setForeground(Qt.GlobalColor.lightGray)
            self.table.setItem(row, 5, chg_item)
            pnl_item = self._ci(format_currency(pnl_usdt, signed=True))
            if pnl_usdt > 0:
                pnl_item.setForeground(Qt.GlobalColor.green)
            elif pnl_usdt < 0:
                pnl_item.setForeground(Qt.GlobalColor.red)
            else:
                pnl_item.setForeground(Qt.GlobalColor.lightGray)
            self.table.setItem(row, 6, pnl_item)
        self.pnl_updated.emit(self.get_total_pnl())

    # ----------------------------------------------------
    # إجمالي الأرباح/الخسائر الحالية (لحد الخسارة اليومية)
    # ----------------------------------------------------
    def get_total_pnl(self) -> float:
        """إجمالي الربح/الخسارة بالـ USDT لجميع الصفوف التي يتوفر لها سعر لحظي (شارت + استطلاع)."""
        total = 0.0
        for row in range(self.table.rowCount()):
            entry_item = self.table.item(row, 1)
            qty_item = self.table.item(row, 2)
            if entry_item is None or qty_item is None:
                continue
            sk = self._row_binance_price_key(row)
            if not sk:
                continue
            px = self._price_by_symbol.get(sk)
            if px is None or px <= 0:
                continue
            try:
                entry = float(entry_item.text())
                qty = float(qty_item.text() or 0)
                total += (float(px) - entry) * qty
            except (ValueError, TypeError):
                pass
        return total

    def logical_open_row_count(self) -> int:
        """
        عدد المراكز «المنطقية» في الجدول: صفوف مكررة لنفس position_id أو نفس طلب فتح eToro
        تُحسب كمركز واحد (يُصلح عرض «صفقتان» بينما المنصة مركز واحد).
        """
        seen_pid: set[int] = set()
        seen_oid: set[int] = set()
        n = 0
        for row in range(self.table.rowCount()):
            sym_item = self.table.item(row, 0)
            qty_item = self.table.item(row, 2)
            if sym_item is None or qty_item is None:
                continue
            try:
                qty = float(qty_item.text() or 0)
            except (TypeError, ValueError):
                qty = 0.0
            if qty <= 0:
                continue
            pid = 0
            try:
                raw = sym_item.data(Qt.ItemDataRole.UserRole)
                if raw is not None:
                    pid = int(raw)
            except (TypeError, ValueError):
                pid = 0
            if pid > 0:
                if pid in seen_pid:
                    continue
                seen_pid.add(pid)
                n += 1
                continue
            oid = 0
            try:
                oraw = sym_item.data(_ETORO_OPEN_ORDER_ROLE)
                if oraw is not None:
                    oid = int(oraw)
            except (TypeError, ValueError):
                oid = 0
            if oid > 0:
                if oid in seen_oid:
                    continue
                seen_oid.add(oid)
                n += 1
                continue
            n += 1
        return n

    def logical_rows_for_symbol_count(self, symbol: str) -> int:
        """مثل logical_open_row_count لكن للصفوف التي تطابق الرمز (عرض أو ETORO_* الخام)."""
        su = str(symbol or "").strip().upper()
        if not su:
            return 0
        seen_pid: set[int] = set()
        seen_oid: set[int] = set()
        n = 0
        for row in range(self.table.rowCount()):
            sym_item = self.table.item(row, 0)
            qty_item = self.table.item(row, 2)
            if sym_item is None or qty_item is None:
                continue
            try:
                qty = float(qty_item.text() or 0)
            except (TypeError, ValueError):
                qty = 0.0
            if qty <= 0:
                continue
            row_sym = (sym_item.text() or "").strip().upper()
            row_raw = str(sym_item.data(_ETORO_RAW_SYMBOL_ROLE) or "").strip().upper()
            if row_sym != su and row_raw != su:
                continue
            pid = 0
            try:
                raw = sym_item.data(Qt.ItemDataRole.UserRole)
                if raw is not None:
                    pid = int(raw)
            except (TypeError, ValueError):
                pid = 0
            if pid > 0:
                if pid in seen_pid:
                    continue
                seen_pid.add(pid)
                n += 1
                continue
            oid = 0
            try:
                oraw = sym_item.data(_ETORO_OPEN_ORDER_ROLE)
                if oraw is not None:
                    oid = int(oraw)
            except (TypeError, ValueError):
                oid = 0
            if oid > 0:
                if oid in seen_oid:
                    continue
                seen_oid.add(oid)
                n += 1
                continue
            n += 1
        return n

    def get_position_for_symbol(self, symbol: str) -> dict | None:
        """إرجاع مركز مجمّع للرمز (متوسط سعر الدخول + مجموع الكمية + position_id إن وُجد) لـ SL/Trailing/بيع eToro."""
        sym_u = (symbol or "").strip().upper()
        if not sym_u:
            return None
        def _row_matches_chart(sym_item: QTableWidgetItem, want: str) -> bool:
            row_sym = (sym_item.text() or "").strip().upper()
            row_raw = str(sym_item.data(_ETORO_RAW_SYMBOL_ROLE) or "").strip().upper()
            if row_sym == want or row_raw == want:
                return True
            try:
                ps = sym_item.data(ROW_PRICE_SYMBOL_ROLE)
                if ps and str(ps).strip().upper() == want:
                    return True
            except Exception:
                pass
            try:
                return binance_spot_pair_symbol(row_sym) == binance_spot_pair_symbol(want)
            except Exception:
                return False

        def _collect(matcher) -> dict | None:
            total_qty = 0.0
            total_cost = 0.0
            pids_row: list[int] = []
            oids_row: list[int] = []
            for row in range(self.table.rowCount()):
                sym_item = self.table.item(row, 0)
                if sym_item is None:
                    continue
                if not matcher(sym_item):
                    continue
                entry_item = self.table.item(row, 1)
                qty_item = self.table.item(row, 2)
                if not entry_item or not qty_item:
                    continue
                try:
                    entry_price = float(entry_item.text())
                    qty = float(qty_item.text() or 0)
                except (ValueError, TypeError):
                    continue
                if qty <= 0:
                    continue
                total_qty += qty
                total_cost += entry_price * qty
                try:
                    raw = sym_item.data(Qt.ItemDataRole.UserRole)
                    if raw is not None:
                        pi = int(raw)
                        if pi > 0:
                            pids_row.append(pi)
                except (TypeError, ValueError):
                    pass
                try:
                    oid_raw = sym_item.data(_ETORO_OPEN_ORDER_ROLE)
                    if oid_raw is not None:
                        oi = int(oid_raw)
                        if oi > 0:
                            oids_row.append(oi)
                except (TypeError, ValueError):
                    pass
            if total_qty <= 0:
                return None
            avg_entry = total_cost / total_qty
            out: dict = {"entry_price": avg_entry, "quantity": total_qty}
            distinct = sorted(set(pids_row))
            if len(distinct) == 1:
                out["position_id"] = distinct[0]
            distinct_o = sorted(set(oids_row))
            if len(distinct_o) == 1:
                out["etoro_open_order_id"] = distinct_o[0]
            return out

        # 1) مطابقة الرمز الحالي للشارت مع النص الخام / زوج السعر المثبّت / مرادفات Binance
        exact = _collect(lambda si: _row_matches_chart(si, sym_u))
        if exact is not None:
            return exact

        # 2) fallback لـ eToro: إن كان الجدول يعرض رموز ETORO_* ولم نجد تطابقاً مباشراً،
        #    نُرجع المركز فقط إذا كان هناك رمز eToro واحد مفتوح (حتى لا نخلط بين عملات متعددة).
        if not sym_u.startswith("ETORO_"):
            etoro_syms: set[str] = set()
            for row in range(self.table.rowCount()):
                sym_item = self.table.item(row, 0)
                qty_item = self.table.item(row, 2)
                if sym_item is None or qty_item is None:
                    continue
                rs = (sym_item.text() or "").strip().upper()
                if not rs.startswith("ETORO_"):
                    continue
                try:
                    qv = float(qty_item.text() or 0)
                except (TypeError, ValueError):
                    qv = 0.0
                if qv > 0:
                    etoro_syms.add(rs)
            if len(etoro_syms) == 1:
                only_sym = next(iter(etoro_syms))

                def _m_only(si: QTableWidgetItem) -> bool:
                    rs = (si.text() or "").strip().upper()
                    rr = str(si.data(_ETORO_RAW_SYMBOL_ROLE) or "").strip().upper()
                    return rs == only_sym or rr == only_sym

                return _collect(_m_only)
        return None

    def get_fifo_lots_for_symbol(self, symbol: str) -> list[tuple[float, float]]:
        """
        (سعر الدخول، الكمية) لكل صف مطابق للرمز بترتيب الجدول من الأعلى للأسفل.
        يُستخدم عند بيع مجمّع لعدة لوتات لتسجيل عدة صفوف بيع في السجل بدل دمجها في صف واحد.
        """
        sym_u = (symbol or "").strip().upper()
        if not sym_u:
            return []

        def _rows(matcher) -> list[tuple[float, float]]:
            out: list[tuple[float, float]] = []
            for row in range(self.table.rowCount()):
                sym_item = self.table.item(row, 0)
                if sym_item is None:
                    continue
                row_sym = (sym_item.text() or "").strip().upper()
                row_raw = str(sym_item.data(_ETORO_RAW_SYMBOL_ROLE) or "").strip().upper()
                if not matcher(row_sym, row_raw):
                    continue
                entry_item = self.table.item(row, 1)
                qty_item = self.table.item(row, 2)
                if not entry_item or not qty_item:
                    continue
                try:
                    ep = float(entry_item.text())
                    q = float(qty_item.text() or 0)
                except (ValueError, TypeError):
                    continue
                if q <= 0 or ep <= 0:
                    continue
                out.append((ep, q))
            return out

        rows = _rows(lambda rs, rr: (rs == sym_u or rr == sym_u))
        if rows:
            return rows
        if not sym_u.startswith("ETORO_"):
            etoro_syms: set[str] = set()
            for row in range(self.table.rowCount()):
                sym_item = self.table.item(row, 0)
                qty_item = self.table.item(row, 2)
                if sym_item is None or qty_item is None:
                    continue
                rs = (sym_item.text() or "").strip().upper()
                if not rs.startswith("ETORO_"):
                    continue
                try:
                    qv = float(qty_item.text() or 0)
                except (TypeError, ValueError):
                    qv = 0.0
                if qv > 0:
                    etoro_syms.add(rs)
            if len(etoro_syms) == 1:
                only_sym = next(iter(etoro_syms))
                return _rows(lambda row_sym, row_raw: (row_sym == only_sym or row_raw == only_sym))
        return []

    def get_selected_trade(self) -> dict | None:
        """إرجاع الصفقة المحددة (سعر دخول + كمية) من الجدول كما هي (بدون تجميع)."""
        try:
            row = -1
            sm = self.table.selectionModel()
            if sm is not None:
                rows = sm.selectedRows()
                if rows:
                    row = rows[0].row()
            if row < 0:
                row = self.table.currentRow()
            if row < 0:
                return None
            sym_item = self.table.item(row, 0)
            entry_item = self.table.item(row, 1)
            qty_item = self.table.item(row, 2)
            if not sym_item or not entry_item or not qty_item:
                return None
            sym = (sym_item.text() or "").strip().upper()
            entry = float(entry_item.text() or 0)
            qty = float(qty_item.text() or 0)
            if not sym or entry <= 0 or qty <= 0:
                return None
            pid = None
            try:
                raw = sym_item.data(Qt.ItemDataRole.UserRole)
                if raw is not None:
                    pid = int(raw)
            except (TypeError, ValueError):
                pid = None
            out = {"symbol": sym, "entry_price": entry, "quantity": qty, "row": row}
            if pid is not None and pid > 0:
                out["position_id"] = pid
            try:
                oid_raw = sym_item.data(_ETORO_OPEN_ORDER_ROLE)
                if oid_raw is not None:
                    oi = int(oid_raw)
                    if oi > 0:
                        out["etoro_open_order_id"] = oi
            except (TypeError, ValueError):
                pass
            return out
        except Exception:
            return None

    def get_any_position_hint_for_symbol(self, symbol: str) -> dict | None:
        """إرجاع أول position_id/order_id من صفوف الرمز كحل احتياطي للبيع."""
        sym_u = (symbol or "").strip().upper()
        if not sym_u:
            return None
        out: dict = {"symbol": sym_u}
        seen = False
        for row in range(self.table.rowCount()):
            it = self.table.item(row, 0)
            if it is None or (it.text() or "").strip().upper() != sym_u:
                continue
            seen = True
            if "position_id" not in out:
                try:
                    rv = it.data(Qt.ItemDataRole.UserRole)
                    if rv is not None:
                        pid = int(rv)
                        if pid > 0:
                            out["position_id"] = pid
                except (TypeError, ValueError):
                    pass
            if "etoro_open_order_id" not in out:
                try:
                    ov = it.data(_ETORO_OPEN_ORDER_ROLE)
                    if ov is not None:
                        oid = int(ov)
                        if oid > 0:
                            out["etoro_open_order_id"] = oid
                except (TypeError, ValueError):
                    pass
            if "position_id" in out and "etoro_open_order_id" in out:
                break
        if not seen:
            return None
        return out

    def remove_trade(self, symbol: str, entry_price: float, quantity: float) -> bool:
        """إزالة كل الصفوف التي تطابق (symbol, entry_price, quantity) — يدعم الصفوف المكررة."""
        try:
            sym_u = str(symbol or "").strip().upper()
            ep = float(entry_price or 0)
            q = float(quantity or 0)
        except Exception:
            return False
        if not sym_u or ep <= 0 or q <= 0:
            return False
        removed_any = False
        row = 0
        while row < self.table.rowCount():
            sym_item = self.table.item(row, 0)
            entry_item = self.table.item(row, 1)
            qty_item = self.table.item(row, 2)
            if not sym_item or not entry_item or not qty_item:
                row += 1
                continue
            try:
                sym = (sym_item.text() or "").strip().upper()
                row_raw = str(sym_item.data(_ETORO_RAW_SYMBOL_ROLE) or "").strip().upper()
                entry = float(entry_item.text() or 0)
                qty = float(qty_item.text() or 0)
            except Exception:
                row += 1
                continue
            if sym != sym_u and row_raw != sym_u:
                row += 1
                continue
            # سماحية أكبر لأن عرض الكمية/السعر قد يقصّ أرقاماً عشرية (وتختلف عن القيمة المنفذة)
            entry_tol = max(1e-6, ep * 1e-6)
            qty_tol = max(1e-8, q * 1e-6)
            if abs(entry - ep) <= entry_tol and abs(qty - q) <= qty_tol:
                self.table.removeRow(row)
                removed_any = True
                continue
            row += 1
        if removed_any:
            self.pnl_updated.emit(self.get_total_pnl())
        return removed_any

    # ----------------------------------------------------
    # إزالة مركز عند إغلاق الصفقة (بيع)
    # ----------------------------------------------------
    def remove_position(self, symbol: str):
        """إزالة أول صف للرمز من جدول المراكز (بعد تنفيذ بيع كامل)."""
        row = self.find_row(symbol)
        if row >= 0:
            self.table.removeRow(row)
            self.pnl_updated.emit(self.get_total_pnl())

    def remove_row_at(self, row_index: int | None) -> bool:
        """إزالة صف واحد بالرقم فقط (لحالة إغلاق صف واحد عندما لا نستطيع إغلاقه على المنصة)."""
        if row_index is None or row_index < 0:
            return False
        if row_index >= self.table.rowCount():
            return False
        self.table.removeRow(row_index)
        self.pnl_updated.emit(self.get_total_pnl())
        return True

    def remove_positions_for_symbol(self, symbol: str):
        """إزالة كل الصفوق المرتبطة بالرمز (بعد إغلاق مركز هذا الرمز بالكامل)."""
        sym_u = (symbol or "").strip().upper()
        if not sym_u:
            return
        removed = 0
        row = 0
        while row < self.table.rowCount():
            item = self.table.item(row, 0)
            if item is not None:
                row_sym = (item.text() or "").strip().upper()
                row_raw = str(item.data(_ETORO_RAW_SYMBOL_ROLE) or "").strip().upper()
                if row_sym == sym_u or (row_raw and row_raw == sym_u):
                    self.table.removeRow(row)
                    removed += 1
                    continue
            row += 1
        if removed:
            self.pnl_updated.emit(self.get_total_pnl())

    def remove_position_by_position_id(self, position_id: int) -> bool:
        """إزالة كل الصفوف التي تطابق معرّف المركز (تكرار واجهة / مزامنة)."""
        try:
            pid = int(position_id)
        except (TypeError, ValueError):
            return False
        removed = False
        row = 0
        while row < self.table.rowCount():
            it = self.table.item(row, 0)
            if it is None:
                row += 1
                continue
            try:
                v = it.data(Qt.ItemDataRole.UserRole)
                if v is not None and int(v) == pid:
                    self.table.removeRow(row)
                    removed = True
                    continue
            except (TypeError, ValueError):
                pass
            row += 1
        if removed:
            self.pnl_updated.emit(self.get_total_pnl())
        return removed

    # ----------------------------------------------------
    # إيجاد الصف
    # ----------------------------------------------------
    def find_row(self, symbol: str) -> int:
        sym_u = (symbol or "").strip().upper()
        if not sym_u:
            return -1
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and (item.text() or "").strip().upper() == sym_u:
                return row
        return -1

    # ----------------------------------------------------
    # قائمة يمين: حد بيع / وقف خسارة لكل صف
    # ----------------------------------------------------
    def _sym_item(self, row: int) -> QTableWidgetItem | None:
        return self.table.item(row, 0)

    def _read_row_tp_sl(self, row: int) -> tuple[str | None, float, str | None, float]:
        it = self._sym_item(row)
        if it is None:
            return None, 0.0, None, 0.0
        try:
            ttp = it.data(_ROW_TP_TYPE_ROLE)
            ttp_s = str(ttp).strip().lower() if ttp else ""
            if ttp_s not in ("percent", "price", "usdt"):
                ttp_s = ""
            tpv = float(it.data(_ROW_TP_VALUE_ROLE) or 0)
        except (TypeError, ValueError):
            ttp_s, tpv = "", 0.0
        try:
            tsl = it.data(_ROW_SL_TYPE_ROLE)
            tsl_s = str(tsl).strip().lower() if tsl else ""
            if tsl_s not in ("percent", "price"):
                tsl_s = ""
            slv = float(it.data(_ROW_SL_VALUE_ROLE) or 0)
        except (TypeError, ValueError):
            tsl_s, slv = "", 0.0
        return (ttp_s or None), tpv, (tsl_s or None), slv

    def _refresh_row_targets_tooltip(self, row: int) -> None:
        it = self._sym_item(row)
        if it is None:
            return
        ttp, tpv, tsl, slv = self._read_row_tp_sl(row)
        parts = []
        if ttp == "percent":
            parts.append(tr("pos_row_tt_tp_pct").format(v=tpv))
        elif ttp == "price":
            parts.append(tr("pos_row_tt_tp_px").format(p=format_price(tpv)))
        if tsl == "percent":
            parts.append(tr("pos_row_tt_sl_pct").format(v=slv))
        elif tsl == "price":
            parts.append(tr("pos_row_tt_sl_px").format(p=format_price(slv)))
        base = (it.text() or "").strip()
        it.setToolTip("\n".join(parts) if parts else base)

    def _set_row_take_profit(self, row: int, typ: str, value: float) -> None:
        it = self._sym_item(row)
        if it is None:
            return
        it.setData(_ROW_TP_TYPE_ROLE, str(typ).lower())
        it.setData(_ROW_TP_VALUE_ROLE, float(value))
        self._refresh_row_targets_tooltip(row)

    def _clear_row_take_profit(self, row: int) -> None:
        it = self._sym_item(row)
        if it is None:
            return
        it.setData(_ROW_TP_TYPE_ROLE, None)
        it.setData(_ROW_TP_VALUE_ROLE, None)
        self._refresh_row_targets_tooltip(row)

    def _set_row_stop_loss(self, row: int, typ: str, value: float) -> None:
        it = self._sym_item(row)
        if it is None:
            return
        it.setData(_ROW_SL_TYPE_ROLE, str(typ).lower())
        it.setData(_ROW_SL_VALUE_ROLE, float(value))
        self._refresh_row_targets_tooltip(row)

    def _clear_row_stop_loss(self, row: int) -> None:
        it = self._sym_item(row)
        if it is None:
            return
        it.setData(_ROW_SL_TYPE_ROLE, None)
        it.setData(_ROW_SL_VALUE_ROLE, None)
        self._refresh_row_targets_tooltip(row)

    def _remove_row_local_only(self, row: int) -> None:
        """حذف صف من واجهة المراكز فقط — لا يُنفَّذ بيع على المنصة (لتصحيح مزامنة أو صف مكرر)."""
        if row < 0 or row >= self.table.rowCount():
            return
        self.table.removeRow(row)
        self.pnl_updated.emit(self.get_total_pnl())

    def _on_positions_context_menu(self, pos: QPoint) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        sym_item = self._sym_item(row)
        if sym_item is None:
            return
        menu = QMenu(self)
        menu.addAction(tr("pos_row_menu_tp"), lambda r=row: self._open_row_tp_dialog(r))
        menu.addAction(tr("pos_row_menu_sl"), lambda r=row: self._open_row_sl_dialog(r))
        menu.addSeparator()
        menu.addAction(tr("pos_row_menu_clear_tp"), lambda r=row: self._clear_row_take_profit(r))
        menu.addAction(tr("pos_row_menu_clear_sl"), lambda r=row: self._clear_row_stop_loss(r))
        menu.addSeparator()
        menu.addAction(tr("pos_row_menu_remove_local"), lambda r=row: self._remove_row_local_only(r))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _open_row_tp_dialog(self, row: int) -> None:
        try:
            entry_item = self.table.item(row, 1)
            if entry_item is None:
                return
            entry = float(entry_item.text() or 0)
        except (TypeError, ValueError):
            return
        ttp, tpv, _, _ = self._read_row_tp_sl(row)
        d = PerPositionTakeProfitDialog(
            self,
            entry_price=entry,
            type_init=ttp or "percent",
            value_init=tpv if ttp else 1.0,
        )
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        pair = d.result_pair()
        if pair:
            self._set_row_take_profit(row, pair[0], pair[1])

    def _open_row_sl_dialog(self, row: int) -> None:
        try:
            entry_item = self.table.item(row, 1)
            if entry_item is None:
                return
            entry = float(entry_item.text() or 0)
        except (TypeError, ValueError):
            return
        _, _, tsl, slv = self._read_row_tp_sl(row)
        d = PerPositionStopLossDialog(
            self,
            entry_price=entry,
            type_init=tsl or "percent",
            value_init=slv if tsl else -1.0,
        )
        if d.exec() != QDialog.DialogCode.Accepted:
            return
        pair = d.result_pair()
        if pair:
            self._set_row_stop_loss(row, pair[0], pair[1])

    def get_close_trade_dict(self, row: int) -> dict | None:
        """نفس بيانات إغلاق الصف (زر ✕) لاستدعاء التنفيذ من لوحة التداول."""
        sym_item = self.table.item(row, 0)
        entry_item = self.table.item(row, 1)
        qty_item = self.table.item(row, 2)
        if not sym_item or not entry_item or not qty_item:
            return None
        try:
            sym = (sym_item.text() or "").strip().upper()
            entry = float(entry_item.text() or 0)
            qty = float(qty_item.text() or 0)
        except (ValueError, TypeError):
            return None
        if not sym or qty <= 0:
            return None
        pid = None
        try:
            raw = sym_item.data(Qt.ItemDataRole.UserRole)
            if raw is not None:
                pid = int(raw)
        except (TypeError, ValueError):
            pid = None
        out: dict = {"symbol": sym, "entry_price": entry, "quantity": qty, "row": row}
        if pid is not None and pid > 0:
            out["position_id"] = pid
        try:
            oid_raw = sym_item.data(_ETORO_OPEN_ORDER_ROLE)
            if oid_raw is not None:
                oi = int(oid_raw)
                if oi > 0:
                    out["etoro_open_order_id"] = oi
        except (TypeError, ValueError):
            pass
        return out

    def find_row_stop_loss_hit(self, last_price: float, chart_symbol: str) -> dict | None:
        """أول صف يطابق الشارت وله وقف خسارة وتحقق الشرط."""
        if last_price <= 0:
            return None
        cur = (chart_symbol or "").strip().upper()
        if not cur:
            return None
        for row in range(self.table.rowCount()):
            if not self._row_matches_chart_symbol(row):
                continue
            _, _, tsl, slv = self._read_row_tp_sl(row)
            if not tsl:
                continue
            entry_item = self.table.item(row, 1)
            qty_item = self.table.item(row, 2)
            if entry_item is None or qty_item is None:
                continue
            try:
                entry = float(entry_item.text() or 0)
                qty = float(qty_item.text() or 0)
            except (ValueError, TypeError):
                continue
            if entry <= 0 or qty <= 0:
                continue
            if suspect_placeholder_entry_price(entry, last_price):
                log.warning(
                    "[مراكز] تجاهل وقف الخسارة للصف — دخول مشبوه entry=%s سوق=%s",
                    entry,
                    last_price,
                )
                continue
            if tsl == "percent":
                if slv >= 0:
                    continue
                sl_level = entry * (1.0 + slv / 100.0)
            else:
                sl_level = float(slv)
                if sl_level <= 0:
                    continue
            if last_price > sl_level:
                continue
            return self.get_close_trade_dict(row)
        return None

    def find_any_row_stop_loss_hit(
        self,
        global_sl_type: str,
        global_sl_value: float,
        *,
        chart_symbol: str = "",
        chart_price: float = 0.0,
    ) -> dict | None:
        """أول صف في الجدول تحقق له وقف الخسارة باستخدام سعره الفعلي.

        الأولوية:
        - إذا كان للصف SL مخصص (من قائمة المراكز) نستخدمه.
        - وإلا نستخدم SL العام القادم من الإعدادات السريعة/المخاطر.

        عند غياب مفتاح سعر للصف: إن وافق الصف رمز الشارت يُستخدم ``chart_price`` (تيك الشارت)
        حتى لا نسقط إلى بيع مجمّع يغلق كل صفوف الرمز دفعة واحدة.
        """
        gsl_t = str(global_sl_type or "percent").strip().lower()
        try:
            gsl_v = float(global_sl_value or 0.0)
        except (TypeError, ValueError):
            gsl_v = 0.0
        c_sym = (chart_symbol or "").strip().upper()
        try:
            c_px = float(chart_price or 0.0)
        except (TypeError, ValueError):
            c_px = 0.0
        for row in range(self.table.rowCount()):
            entry_item = self.table.item(row, 1)
            qty_item = self.table.item(row, 2)
            if entry_item is None or qty_item is None:
                continue
            try:
                entry = float(entry_item.text() or 0)
                qty = float(qty_item.text() or 0)
            except (TypeError, ValueError):
                continue
            if entry <= 0 or qty <= 0:
                continue
            sk = self._row_binance_price_key(row)
            px = self._price_by_symbol.get(sk) if sk else None
            try:
                last_price = float(px or 0)
            except (TypeError, ValueError):
                last_price = 0.0
            if last_price <= 0 and c_px > 0 and c_sym and self._row_matches_chart_symbol(row):
                last_price = c_px
            if last_price <= 0:
                continue
            if suspect_placeholder_entry_price(entry, last_price):
                continue
            _, _, row_sl_t, row_sl_v = self._read_row_tp_sl(row)
            sl_t = row_sl_t or gsl_t
            sl_v = float(row_sl_v if row_sl_t else gsl_v)
            if sl_t == "percent":
                if sl_v >= 0:
                    continue
                sl_level = entry * (1.0 + sl_v / 100.0)
            else:
                if sl_v <= 0:
                    continue
                sl_level = sl_v
            if last_price > sl_level:
                continue
            return self.get_close_trade_dict(row)
        return None

    def find_row_take_profit_hit(self, last_price: float, chart_symbol: str) -> dict | None:
        """أول صف يطابق الشارت وله حد بيع وتحقق الشرط."""
        if last_price <= 0:
            return None
        cur = (chart_symbol or "").strip().upper()
        if not cur:
            return None
        for row in range(self.table.rowCount()):
            if not self._row_matches_chart_symbol(row):
                continue
            ttp, tpv, _, _ = self._read_row_tp_sl(row)
            if not ttp:
                continue
            entry_item = self.table.item(row, 1)
            qty_item = self.table.item(row, 2)
            if entry_item is None or qty_item is None:
                continue
            try:
                entry = float(entry_item.text() or 0)
                qty = float(qty_item.text() or 0)
            except (ValueError, TypeError):
                continue
            if entry <= 0 or qty <= 0:
                continue
            if suspect_placeholder_entry_price(entry, last_price):
                log.warning(
                    "[مراكز] تجاهل حد البيع للصف — دخول مشبوه entry=%s سوق=%s",
                    entry,
                    last_price,
                )
                continue
            if ttp == "percent":
                if tpv <= 0:
                    continue
                target = entry * (1.0 + tpv / 100.0)
                if last_price < target:
                    continue
            elif ttp == "usdt":
                if tpv <= 0:
                    continue
                pnl_usdt = (last_price - entry) * qty
                if pnl_usdt < tpv:
                    continue
            else:
                target = float(tpv)
                if target <= 0:
                    continue
                if last_price < target:
                    continue
            return self.get_close_trade_dict(row)
        return None

    def take_profit_still_pending_for_chart(self, last_price: float, chart_symbol: str) -> bool:
        """
        هل يوجد صف لرمز الشارت فيه حد بيع مضبوط ولم يتحقق بعد، والصف في ربح على سعر الدخول؟
        يُستخدم لمنع التتبع وإشارة SELL من البيع قبل الهدف (في الخسارة لا نمنع الخروج).
        """
        if last_price <= 0:
            return False
        cur = (chart_symbol or "").strip().upper()
        if not cur:
            return False
        for row in range(self.table.rowCount()):
            if not self._row_matches_chart_symbol(row):
                continue
            ttp, tpv, _, _ = self._read_row_tp_sl(row)
            if not ttp:
                continue
            entry_item = self.table.item(row, 1)
            qty_item = self.table.item(row, 2)
            if entry_item is None or qty_item is None:
                continue
            try:
                entry = float(entry_item.text() or 0)
                qty = float(qty_item.text() or 0)
            except (ValueError, TypeError):
                continue
            if entry <= 0 or qty <= 0:
                continue
            if suspect_placeholder_entry_price(entry, last_price):
                continue
            if last_price < entry:
                continue
            if ttp == "percent":
                if tpv <= 0:
                    continue
                target = entry * (1.0 + tpv / 100.0)
                if last_price < target:
                    return True
            elif ttp == "usdt":
                if tpv <= 0:
                    continue
                pnl_usdt = (last_price - entry) * qty
                if pnl_usdt <= 0:
                    continue
                if pnl_usdt < tpv:
                    return True
            else:
                target = float(tpv)
                if target <= 0:
                    continue
                if last_price < target:
                    return True
        return False

    # ----------------------------------------------------
    # دالة تغيير العملة عند اختيار المستخدم لرمز جديد
    # ----------------------------------------------------
    def change_symbol(self, symbol: str):
        self.current_symbol = symbol
        # لا نمسح جدول المراكز عند تغيير العملة — المراكز تخص عدة عملات ويمكن أن تبقى مفتوحة
        log.info("Switched to symbol: %s", symbol)
