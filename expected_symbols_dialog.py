# قائمة «العملات المتوقعة» — منفصلة عن المفضلة + دراسة سوق (24h + مؤشرات)
from __future__ import annotations

import re

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from config import load_config, save_config
from translations import tr

_MAX_ITEMS = 50
_SYM_RE = re.compile(r"^[A-Z0-9]{2,32}USDT$")


def _normalize_symbol(raw: str) -> str:
    return str(raw or "").strip().upper().replace(" ", "")


def _tr_study_interval_code(iv_code: str) -> str:
    """اسم الإطار للعرض (يتفادى لبس رموز مثل 15m مع 15)."""
    c = str(iv_code or "").strip().lower()
    if c == "1h":
        return tr("expected_study_iv_1h")
    if c == "4h":
        return tr("expected_study_iv_4h")
    if c == "1d":
        return tr("expected_study_iv_1d")
    return c


class ExpectedStudyThread(QThread):
    finished_ok = pyqtSignal(list)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)

    def __init__(self, chart_interval: str, parent=None):
        super().__init__(parent)
        self._chart_interval = str(chart_interval or "4h").strip().lower()

    def run(self):
        from expected_market_scanner import analyze_symbol, fetch_expected_study_pool

        try:
            cfg = load_config()
            self.progress.emit(tr("expected_study_fetching"))
            pool = fetch_expected_study_pool(cfg)
            if not pool:
                self.failed.emit(tr("expected_study_no_pool"))
                return
            try:
                n_ind = int(cfg.get("expected_study_indicator_count", 18) or 18)
            except (TypeError, ValueError):
                n_ind = 18
            n_ind = max(5, min(35, n_ind))
            interval = self._chart_interval
            if interval not in ("1h", "4h", "1d"):
                interval = "4h"
            slice_pool = pool[:n_ind]
            rows: list[dict] = []
            for i, entry in enumerate(slice_pool):
                sym = str(entry.get("symbol") or "")
                self.progress.emit(
                    tr("expected_study_progress").format(
                        i=i + 1,
                        n=len(slice_pool),
                        sym=sym,
                        iv=_tr_study_interval_code(interval),
                    )
                )
                chg = entry.get("chg_pct")
                try:
                    chg_f = float(chg) if chg is not None else None
                except (TypeError, ValueError):
                    chg_f = None
                r = analyze_symbol(
                    sym,
                    interval,
                    cfg,
                    chg_24h=chg_f,
                    quote_volume_usdt=entry.get("quote_volume_usdt"),
                )
                if r:
                    rows.append(r)
            rows.sort(key=lambda x: float(x.get("expected_upside_pct", 0.0) or 0.0), reverse=True)
            self.finished_ok.emit(rows)
        except Exception as e:
            self.failed.emit(str(e))


class ExpectedSymbolsDialog(QDialog):
    """حوار لإدارة قائمة `expected_symbols` + دراسة مرشحي السوق."""

    symbol_chosen = pyqtSignal(str)
    favorites_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setWindowTitle(tr("expected_symbols_title"))
        self.setMinimumSize(420, 560)

        self._last_study_rows: list[dict] = []
        self._study_thread: ExpectedStudyThread | None = None

        root = QVBoxLayout(self)
        hint = QLabel(tr("expected_symbols_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8b95a0; font-size: 11px;")
        root.addWidget(hint)

        study_blurb = QLabel(tr("expected_study_blurb"))
        study_blurb.setWordWrap(True)
        study_blurb.setStyleSheet("color: #a0a8b0; font-size: 10px;")
        root.addWidget(study_blurb)

        row_iv = QHBoxLayout()
        lbl_iv = QLabel(tr("expected_study_interval_label"))
        lbl_iv.setStyleSheet("font-size: 11px; color: #c5cad3;")
        self._combo_interval = QComboBox()
        self._combo_interval.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self._combo_interval.addItem(tr("expected_study_iv_1h"), "1h")
        self._combo_interval.addItem(tr("expected_study_iv_4h"), "4h")
        self._combo_interval.addItem(tr("expected_study_iv_1d"), "1d")
        _cfg0 = load_config()
        _raw_iv = str(_cfg0.get("expected_study_chart_interval", "4h") or "4h").strip().lower()
        if _raw_iv not in ("1h", "4h", "1d"):
            _cfg0["expected_study_chart_interval"] = "4h"
            save_config(_cfg0)
            _iv0 = "4h"
        else:
            _iv0 = _raw_iv
        _ix = self._combo_interval.findData(_iv0, Qt.ItemDataRole.UserRole)
        self._combo_interval.setCurrentIndex(max(0, _ix))
        self._combo_interval.currentIndexChanged.connect(self._on_study_interval_saved)
        row_iv.addWidget(lbl_iv)
        row_iv.addWidget(self._combo_interval, 1)
        root.addLayout(row_iv)

        row_study = QHBoxLayout()
        self._btn_study = QPushButton(tr("expected_study_btn"))
        self._btn_study.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_study.clicked.connect(self._on_study_clicked)
        self._btn_merge = QPushButton(tr("expected_study_merge_top"))
        self._btn_merge.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_merge.clicked.connect(self._on_merge_top)
        self._btn_merge.setEnabled(False)
        row_study.addWidget(self._btn_study)
        row_study.addWidget(self._btn_merge)
        row_study.addStretch(1)
        root.addLayout(row_study)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #7a8699; font-size: 10px;")
        root.addWidget(self._status)

        lbl_res = QLabel(tr("expected_study_results_label"))
        lbl_res.setStyleSheet("font-size: 11px; font-weight: bold; color: #c5cad3;")
        root.addWidget(lbl_res)

        self._results = QListWidget()
        self._results.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self._results.setMinimumHeight(160)
        self._results.itemDoubleClicked.connect(self._on_result_double_clicked)
        root.addWidget(self._results, 1)

        lbl_my = QLabel(tr("expected_symbols_my_list_label"))
        lbl_my.setStyleSheet("font-size: 11px; font-weight: bold; color: #c5cad3;")
        root.addWidget(lbl_my)

        self._list = QListWidget()
        self._list.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self._list.itemDoubleClicked.connect(self._on_double_clicked)
        root.addWidget(self._list, 1)

        row_pick = QHBoxLayout()
        self._btn_pick_symbol = QPushButton(tr("expected_pick_symbol_btn"))
        self._btn_pick_symbol.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_pick_symbol.clicked.connect(self._on_pick_symbol)
        self._btn_add_favorite = QPushButton(tr("expected_add_favorite_btn"))
        self._btn_add_favorite.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_add_favorite.clicked.connect(self._on_add_selection_to_favorites)
        row_pick.addWidget(self._btn_pick_symbol)
        row_pick.addWidget(self._btn_add_favorite)
        row_pick.addStretch(1)
        root.addLayout(row_pick)

        row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self._input.setPlaceholderText(tr("expected_symbols_placeholder"))
        self._input.returnPressed.connect(self._on_add)
        btn_add = QPushButton(tr("expected_symbols_add"))
        btn_add.clicked.connect(self._on_add)
        row.addWidget(self._input, 1)
        row.addWidget(btn_add)
        root.addLayout(row)

        row2 = QHBoxLayout()
        btn_del = QPushButton(tr("expected_symbols_remove"))
        btn_del.clicked.connect(self._on_remove)
        btn_close = QPushButton(tr("expected_symbols_close"))
        btn_close.clicked.connect(self.accept)
        row2.addWidget(btn_del)
        row2.addStretch(1)
        row2.addWidget(btn_close)
        root.addLayout(row2)

        self._refresh_list()

    def _study_interval_data(self) -> str:
        idx = self._combo_interval.currentIndex()
        d = self._combo_interval.itemData(idx, Qt.ItemDataRole.UserRole)
        if d is None:
            d = self._combo_interval.currentData(Qt.ItemDataRole.UserRole)
        s = str(d).strip().lower() if d is not None else ""
        return s if s in ("1h", "4h", "1d") else "4h"

    def _on_study_interval_saved(self) -> None:
        iv = self._study_interval_data()
        try:
            cfg = load_config()
            cfg["expected_study_chart_interval"] = iv
            save_config(cfg)
        except Exception:
            pass

    def _current_selected_symbol(self) -> str | None:
        rr = self._results.currentRow()
        if rr >= 0:
            it = self._results.item(rr)
            if it is not None:
                s = str(it.data(Qt.ItemDataRole.UserRole) or "").strip().upper()
                if s:
                    return s
        lr = self._list.currentRow()
        if lr >= 0:
            it = self._list.item(lr)
            if it is not None:
                return _normalize_symbol(it.text())
        return None

    def _try_append_expected(self, sym: str) -> str:
        """يُرجع '' إن أُضيف، أو: exists | invalid | max"""
        sym = _normalize_symbol(sym)
        if not sym or not _SYM_RE.match(sym):
            return "invalid"
        items = self._load_ordered()
        if sym in items:
            return "exists"
        if len(items) >= _MAX_ITEMS:
            return "max"
        items.append(sym)
        self._persist(items)
        self._refresh_list()
        return ""

    def _apply_picked_symbol_from_row_or_selector(self, raw_sym: str) -> None:
        """تطبيق الرمز على الشارت + حقل الإدخال + محاولة إضافة للقائمة."""
        sym = _normalize_symbol(raw_sym)
        if not _SYM_RE.match(sym):
            QMessageBox.warning(self, tr("expected_symbols_title"), tr("expected_symbols_invalid"))
            return
        self.symbol_chosen.emit(sym)
        self._input.setText(sym)
        code = self._try_append_expected(sym)
        base = tr("expected_pick_chart_applied").format(symbol=sym)
        if code == "":
            self._status.setText(base + tr("expected_pick_suffix_list_added"))
        elif code == "exists":
            self._status.setText(base + tr("expected_pick_suffix_list_already"))
        elif code == "max":
            self._status.setText(base + tr("expected_pick_suffix_list_full"))

    def _on_pick_symbol(self) -> None:
        # إن وُجد صف محدد في التقرير أو «قائمتك» — نطبّقه مباشرة دون فتح منتقي الرموز
        pre = self._current_selected_symbol()
        if pre:
            u = _normalize_symbol(pre)
            if _SYM_RE.match(u):
                self._apply_picked_symbol_from_row_or_selector(u)
                return
        from symbol_selector import SymbolSelector

        sel = SymbolSelector(self)
        if not sel.exec() or not sel.selected_symbol:
            return
        self._apply_picked_symbol_from_row_or_selector(sel.selected_symbol)

    def _on_add_selection_to_favorites(self) -> None:
        sym = self._current_selected_symbol()
        if not sym:
            QMessageBox.information(
                self,
                tr("expected_symbols_title"),
                tr("expected_select_symbol_first"),
            )
            return
        cfg = load_config()
        fav_raw = cfg.get("favorite_symbols") or []
        fav = [str(s or "").strip().upper() for s in fav_raw if str(s or "").strip()]
        if sym in fav:
            QMessageBox.information(
                self,
                tr("expected_symbols_title"),
                tr("ai_suggested_symbol_already_favorite"),
            )
            return
        fav.insert(0, sym)
        cfg["favorite_symbols"] = fav[:50]
        save_config(cfg)
        self.favorites_changed.emit()
        QMessageBox.information(
            self,
            tr("expected_symbols_title"),
            tr("ai_suggested_symbol_added_favorite").format(symbol=sym),
        )

    def _format_study_row(self, r: dict) -> str:
        sym = str(r.get("symbol") or "")
        try:
            exp = float(r.get("expected_upside_pct") or 0.0)
        except (TypeError, ValueError):
            exp = 0.0
        return tr("expected_study_row").format(sym=sym, exp=exp)

    def _format_study_tooltip(self, r: dict) -> str:
        iv = str(r.get("chart_interval") or "")
        try:
            exp = float(r.get("expected_upside_pct") or 0.0)
        except (TypeError, ValueError):
            exp = 0.0
        qv = r.get("quote_volume_usdt")
        try:
            qv_m = float(qv) / 1_000_000.0 if qv is not None else 0.0
        except (TypeError, ValueError):
            qv_m = 0.0
        detail = tr("expected_study_tooltip_detail")
        try:
            chg_v = r.get("chg_24h_pct")
            if chg_v is not None:
                detail = (
                    tr("expected_study_tooltip_chg24_note").format(chg=float(chg_v))
                    + "\n"
                    + detail
                )
        except (TypeError, ValueError):
            pass
        return tr("expected_study_tooltip").format(
            sym=r.get("symbol", ""),
            exp=exp,
            iv=_tr_study_interval_code(iv),
            qv_m=qv_m,
            detail=detail,
        )

    def _on_study_clicked(self) -> None:
        if self._study_thread is not None and self._study_thread.isRunning():
            return
        self._results.clear()
        self._last_study_rows = []
        self._btn_merge.setEnabled(False)
        self._btn_study.setEnabled(False)
        _ivc = self._study_interval_data()
        self._status.setText(tr("expected_study_started").format(iv=_tr_study_interval_code(_ivc)))
        self._on_study_interval_saved()
        self._study_thread = ExpectedStudyThread(_ivc, self)
        self._study_thread.progress.connect(self._status.setText)
        self._study_thread.finished_ok.connect(self._on_study_done)
        self._study_thread.failed.connect(self._on_study_failed)
        self._study_thread.finished.connect(self._study_cleanup_thread)
        self._study_thread.start()

    def _study_cleanup_thread(self) -> None:
        self._btn_study.setEnabled(True)
        t = self._study_thread
        self._study_thread = None
        if t is not None:
            t.deleteLater()

    def _on_study_done(self, rows: list) -> None:
        self._last_study_rows = list(rows) if isinstance(rows, list) else []
        self._results.clear()
        if not self._last_study_rows:
            self._status.setText(tr("expected_study_done_empty"))
            return
        self._status.setText(tr("expected_study_done_ok").format(n=len(self._last_study_rows)))
        self._btn_merge.setEnabled(True)
        for r in self._last_study_rows:
            if not isinstance(r, dict):
                continue
            sym = str(r.get("symbol") or "")
            it = QListWidgetItem(self._format_study_row(r))
            it.setData(Qt.ItemDataRole.UserRole, sym)
            it.setToolTip(self._format_study_tooltip(r))
            self._results.addItem(it)

    def _on_study_failed(self, msg: str) -> None:
        self._last_study_rows = []
        self._results.clear()
        self._btn_merge.setEnabled(False)
        self._status.setText("")
        QMessageBox.warning(self, tr("expected_symbols_title"), str(msg or tr("expected_study_failed")))

    def _on_merge_top(self) -> None:
        if not self._last_study_rows:
            return
        cfg = load_config()
        try:
            n = int(cfg.get("expected_study_merge_count", 5) or 5)
        except (TypeError, ValueError):
            n = 5
        n = max(1, min(15, n))
        new_items: list[str] = []
        seen: set[str] = set()
        for r in self._last_study_rows:
            if len(new_items) >= n:
                break
            sym = _normalize_symbol(str(r.get("symbol") or ""))
            if not sym or not _SYM_RE.match(sym) or sym in seen:
                continue
            seen.add(sym)
            new_items.append(sym)
        if not new_items:
            return
        if len(new_items) > _MAX_ITEMS:
            new_items = new_items[:_MAX_ITEMS]
            QMessageBox.information(self, tr("expected_symbols_title"), tr("expected_symbols_max"))
        self._persist(new_items)
        self._refresh_list()
        self._status.setText(tr("expected_study_merged").format(n=len(new_items)))

    def _on_result_double_clicked(self, item: QListWidgetItem) -> None:
        if not item:
            return
        sym = str(item.data(Qt.ItemDataRole.UserRole) or "").strip().upper()
        if not sym:
            sym = _normalize_symbol(item.text().split("|")[0].strip())
        if sym:
            self.symbol_chosen.emit(sym)

    def _load_ordered(self) -> list[str]:
        cfg = load_config()
        raw = cfg.get("expected_symbols") or []
        out: list[str] = []
        seen: set[str] = set()
        for s in raw:
            u = _normalize_symbol(s)
            if not u or u in seen:
                continue
            seen.add(u)
            out.append(u)
        return out[:_MAX_ITEMS]

    def _persist(self, items: list[str]) -> None:
        cfg = load_config()
        cfg["expected_symbols"] = items[:_MAX_ITEMS]
        save_config(cfg)

    def _refresh_list(self) -> None:
        self._list.clear()
        for s in self._load_ordered():
            self._list.addItem(s)

    def _on_add(self) -> None:
        sym = _normalize_symbol(self._input.text())
        if not sym:
            return
        code = self._try_append_expected(sym)
        if code == "":
            self._input.clear()
        elif code == "exists":
            self._input.clear()
        elif code == "invalid":
            QMessageBox.warning(self, tr("expected_symbols_title"), tr("expected_symbols_invalid"))
        elif code == "max":
            QMessageBox.information(self, tr("expected_symbols_title"), tr("expected_symbols_max"))

    def _on_remove(self) -> None:
        row = self._list.currentRow()
        if row < 0:
            return
        it = self._list.item(row)
        if not it:
            return
        sym = _normalize_symbol(it.text())
        items = [x for x in self._load_ordered() if x != sym]
        self._persist(items)
        self._refresh_list()

    def _on_double_clicked(self, item: QListWidgetItem) -> None:
        if not item:
            return
        sym = _normalize_symbol(item.text())
        if sym:
            self.symbol_chosen.emit(sym)
            self.accept()
