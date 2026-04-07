# searchable_combobox.py — كومبو للقوائم الطويلة: إكمال أثناء الكتابة + تأكيد الرمز
import re

from PyQt6.QtCore import Qt, QStringListModel, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QCompleter


class SearchableComboBox(QComboBox):
    """
    - إكمال تلقائي أثناء الكتابة (يحتوي على النص، بدون حساسية لحالة الأحرف)
    - symbolConfirmed: عند اختيار من القائمة، أو من نافذة الإكمال، أو Enter برمز صالح
    - اتجاه LTR لتفادي مشاكل الواجهة العربية مع الرموز اللاتينية
    """

    symbolConfirmed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self._all_items: list[str] = []
        self._last_valid: str = ""

        self._completer = QCompleter(self)
        self._completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._completer.setMaxVisibleItems(20)
        self.setCompleter(self._completer)
        self._completer.activated.connect(self._on_completer_picked)

        # اختيار من القائمة المنسدلة (يعمل بثبات أكبر مع الحقول القابلة للتحرير من textActivated)
        self.textActivated.connect(self._on_text_activated)

        le = self.lineEdit()
        if le is not None:
            le.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            le.returnPressed.connect(self._try_confirm_typed)
            # لا نربط editingFinished: عند فقدان التركيز أثناء الكتابة كان يُعاد النص ويُلغى الإكمال

    def set_items(self, items):
        """استبدال كامل: يفرّغ ثم يعيّن القائمة الجديدة."""
        le = self.lineEdit()
        if le is not None:
            le.blockSignals(True)
        try:
            self._all_items = [str(x).strip().upper() for x in (items or []) if str(x).strip()]
            self.clear()
            if self._all_items:
                self.addItems(self._all_items)
            self._completer.setModel(QStringListModel(self._all_items, self))
            if self._all_items:
                self._last_valid = self._all_items[0]
        finally:
            if le is not None:
                le.blockSignals(False)

    def add_items(self, items):
        """إضافة عناصر دون مسح الحالية."""
        le = self.lineEdit()
        if le is not None:
            le.blockSignals(True)
        try:
            for x in items or []:
                u = str(x).strip().upper()
                if u and self.findText(u, Qt.MatchFlag.MatchExactly) < 0:
                    self.addItem(u)
            self._all_items = [self.itemText(i) for i in range(self.count())]
            self._completer.setModel(QStringListModel(self._all_items, self))
        finally:
            if le is not None:
                le.blockSignals(False)

    def clear_items(self):
        self.clear()
        self._all_items = []
        self._completer.setModel(QStringListModel([], self))

    def _emit_confirmed(self, t: str) -> None:
        if self.signalsBlocked():
            return
        t = self._normalize_candidate(t)
        if not t:
            return
        self._last_valid = t
        self.symbolConfirmed.emit(t)

    def _on_text_activated(self, text: str) -> None:
        t = self._normalize_candidate(text)
        if not t:
            return
        self.setCurrentText(t)
        self._emit_confirmed(t)

    def _on_completer_picked(self, text: str) -> None:
        t = self._normalize_candidate(text)
        if not t:
            return
        if self.findText(t, Qt.MatchFlag.MatchExactly) < 0:
            self.addItem(t)
            self._all_items = [self.itemText(i) for i in range(self.count())]
            self._completer.setModel(QStringListModel(self._all_items, self))
        self.setCurrentText(t)
        self._emit_confirmed(t)

    def _normalize_candidate(self, raw: str) -> str:
        return (raw or "").strip().upper().replace(" ", "")

    def _is_acceptable_symbol(self, t: str) -> bool:
        if not t:
            return False
        if t in self._all_items:
            return True
        if t.startswith("ETORO_") and len(t) > 6:
            return True
        if re.fullmatch(r"[A-Z0-9]{2,32}USDT", t):
            return True
        # رموز Binance النادرة (أحرف غير لاتينية في اسم الرمز) + أي XXXUSDT معقول
        if t.endswith("USDT"):
            base = t[:-4]
            if len(base) >= 1:
                return True
        return False

    def _try_confirm_typed(self) -> None:
        if self.signalsBlocked():
            return
        t = self._normalize_candidate(self.currentText())
        if not t:
            self._restore_line()
            return
        if not self._is_acceptable_symbol(t):
            self._restore_line()
            return
        if self.findText(t, Qt.MatchFlag.MatchExactly) < 0:
            self.addItem(t)
            self._all_items = [self.itemText(i) for i in range(self.count())]
            self._completer.setModel(QStringListModel(self._all_items, self))
        self.setCurrentText(t)
        self._emit_confirmed(t)

    def _restore_line(self) -> None:
        le = self.lineEdit()
        if le is not None and self._last_valid:
            le.setText(self._last_valid)

    def setCurrentText(self, text: str) -> None:
        super().setCurrentText(text)
        t = self._normalize_candidate(text)
        if t and self._is_acceptable_symbol(t):
            self._last_valid = t
