# position_row_targets_dialog.py — حد بيع / وقف خسارة لصف واحد في جدول المراكز (لا يغيّر الإعدادات العامة)
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
)

from format_utils import format_price
from translations import tr


class PerPositionTakeProfitDialog(QDialog):
    """حد بيع: سعر أو نسبة من سعر الدخول لهذه الصفقة فقط."""

    def __init__(self, parent=None, *, entry_price: float = 0.0, type_init: str = "percent", value_init: float = 1.0):
        super().__init__(parent)
        self.setWindowTitle(tr("pos_row_tp_title"))
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        if entry_price and entry_price > 0:
            layout.addWidget(
                QLabel(tr("pos_row_entry_hint").format(price=format_price(float(entry_price))))
            )
        form = QFormLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItem(tr("quick_tp_percent"), "percent")
        self.type_combo.addItem(tr("quick_tp_price"), "price")
        self.type_combo.addItem(tr("pos_row_tp_usdt"), "usdt")
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow(tr("quick_type"), self.type_combo)
        self.percent_spin = QDoubleSpinBox()
        self.percent_spin.setRange(0.01, 500.0)
        self.percent_spin.setDecimals(2)
        self.percent_spin.setSingleStep(0.1)
        self.percent_spin.setSuffix(" %")
        form.addRow(QLabel(""), self.percent_spin)
        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0.0, 10_000_000.0)
        self.price_spin.setDecimals(8)
        form.addRow(QLabel(""), self.price_spin)
        self.usdt_spin = QDoubleSpinBox()
        self.usdt_spin.setRange(0.01, 9_999_999.0)
        self.usdt_spin.setDecimals(2)
        self.usdt_spin.setSingleStep(1.0)
        self.usdt_spin.setSuffix(" USDT")
        form.addRow(QLabel(""), self.usdt_spin)
        layout.addLayout(form)
        hint = QLabel(tr("pos_row_tp_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton(tr("risk_save"))
        ok_btn.clicked.connect(self.accept)
        close_btn = QPushButton(tr("risk_close"))
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        ti = (type_init or "percent").strip().lower()
        idx = self.type_combo.findData(ti if ti in ("percent", "price", "usdt") else "percent")
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        self.percent_spin.setValue(float(value_init) if ti == "percent" else 1.0)
        self.price_spin.setValue(float(value_init) if ti == "price" and value_init > 0 else float(entry_price or 0) * 1.01)
        self.usdt_spin.setValue(float(value_init) if ti == "usdt" and value_init > 0 else 100.0)
        self._on_type_changed(self.type_combo.currentIndex())

    def _on_type_changed(self, _idx: int):
        typ = self.type_combo.currentData()
        self.percent_spin.setVisible(typ == "percent")
        self.price_spin.setVisible(typ == "price")
        self.usdt_spin.setVisible(typ == "usdt")

    def result_pair(self) -> tuple[str, float] | None:
        if self.result() != QDialog.DialogCode.Accepted:
            return None
        typ = self.type_combo.currentData() or "percent"
        if typ == "percent":
            return "percent", float(self.percent_spin.value())
        if typ == "usdt":
            return "usdt", float(self.usdt_spin.value())
        return "price", float(self.price_spin.value())


class PerPositionStopLossDialog(QDialog):
    """وقف خسارة: سعر أو نسبة سالبة من سعر الدخول لهذه الصفقة فقط."""

    def __init__(self, parent=None, *, entry_price: float = 0.0, type_init: str = "percent", value_init: float = -1.0):
        super().__init__(parent)
        self.setWindowTitle(tr("pos_row_sl_title"))
        self.setMinimumWidth(300)
        layout = QVBoxLayout(self)
        if entry_price and entry_price > 0:
            layout.addWidget(
                QLabel(tr("pos_row_entry_hint").format(price=format_price(float(entry_price))))
            )
        form = QFormLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItem(tr("quick_sl_percent"), "percent")
        self.type_combo.addItem(tr("quick_tp_price"), "price")
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow(tr("quick_type"), self.type_combo)
        self.percent_spin = QDoubleSpinBox()
        self.percent_spin.setRange(-100.0, -0.01)
        self.percent_spin.setDecimals(2)
        self.percent_spin.setSingleStep(0.1)
        self.percent_spin.setSuffix(" %")
        form.addRow(QLabel(""), self.percent_spin)
        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0.0, 10_000_000.0)
        self.price_spin.setDecimals(8)
        form.addRow(QLabel(""), self.price_spin)
        layout.addLayout(form)
        hint = QLabel(tr("pos_row_sl_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888; font-size: 11px;")
        layout.addWidget(hint)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton(tr("risk_save"))
        ok_btn.clicked.connect(self.accept)
        close_btn = QPushButton(tr("risk_close"))
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        ti = (type_init or "percent").strip().lower()
        idx = self.type_combo.findData(ti if ti in ("percent", "price") else "percent")
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        self.percent_spin.setValue(float(value_init) if ti == "percent" else -1.0)
        self.price_spin.setValue(float(value_init) if ti == "price" and value_init > 0 else float(entry_price or 0) * 0.99)
        self._on_type_changed(self.type_combo.currentIndex())

    def _on_type_changed(self, _idx: int):
        is_percent = self.type_combo.currentData() == "percent"
        self.percent_spin.setVisible(is_percent)
        self.price_spin.setVisible(not is_percent)

    def result_pair(self) -> tuple[str, float] | None:
        if self.result() != QDialog.DialogCode.Accepted:
            return None
        typ = self.type_combo.currentData() or "percent"
        if typ == "percent":
            return "percent", float(self.percent_spin.value())
        return "price", float(self.price_spin.value())
