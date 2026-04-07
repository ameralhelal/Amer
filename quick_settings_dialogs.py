# quick_settings_dialogs.py — نوافذ سريعة للمبلغ، الرافعة، هدف الربح، وقف الخسارة
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSpinBox, QDoubleSpinBox, QComboBox, QFormLayout,
)
from PyQt6.QtCore import pyqtSignal

from config import load_config, save_config, DEFAULTS
from translations import tr


class AmountDialog(QDialog):
    """المبلغ: قيمة ثابتة (USDT) أو نسبة مئوية من الرصيد."""
    config_saved = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("quick_amount_title"))
        self.setMinimumWidth(280)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItem(tr("quick_type_value"), "value")
        self.type_combo.addItem(tr("quick_type_percent"), "percent")
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow(tr("main_amount"), self.type_combo)
        self.value_spin = QSpinBox()
        self.value_spin.setRange(1, 10_000_000)
        self.value_spin.setSuffix(" USDT")
        form.addRow(QLabel(""), self.value_spin)
        self.percent_spin = QDoubleSpinBox()
        self.percent_spin.setRange(0.1, 100.0)
        self.percent_spin.setDecimals(1)
        self.percent_spin.setSuffix(" %")
        form.addRow(QLabel(""), self.percent_spin)
        layout.addLayout(form)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton(tr("risk_save"))
        ok_btn.clicked.connect(self._save)
        close_btn = QPushButton(tr("risk_close"))
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        self._load()
        self._on_type_changed(self.type_combo.currentIndex())

    def _on_type_changed(self, _idx):
        is_value = self.type_combo.currentData() == "value"
        self.value_spin.setVisible(is_value)
        self.percent_spin.setVisible(not is_value)

    def _load(self):
        cfg = load_config()
        at = cfg.get("amount_type", DEFAULTS.get("amount_type", "value"))
        idx = self.type_combo.findData(at)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        self.value_spin.setValue(int(cfg.get("amount_usdt", DEFAULTS["amount_usdt"])))
        self.percent_spin.setValue(float(cfg.get("amount_percent", DEFAULTS.get("amount_percent", 10.0))))

    def _save(self):
        cfg = load_config()
        cfg["amount_type"] = self.type_combo.currentData() or "value"
        cfg["amount_usdt"] = self.value_spin.value()
        cfg["amount_percent"] = self.percent_spin.value()
        save_config(cfg)
        self.config_saved.emit(cfg)
        self.accept()


class LeverageDialog(QDialog):
    """الرافعة من 1 حتى 125."""
    config_saved = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("quick_leverage_title"))
        self.setMinimumWidth(260)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.leverage_spin = QSpinBox()
        self.leverage_spin.setRange(1, 125)
        self.leverage_spin.setSuffix("x")
        form.addRow(tr("main_leverage"), self.leverage_spin)
        layout.addLayout(form)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton(tr("risk_save"))
        ok_btn.clicked.connect(self._save)
        close_btn = QPushButton(tr("risk_close"))
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        self._load()

    def _load(self):
        cfg = load_config()
        self.leverage_spin.setValue(int(cfg.get("leverage", DEFAULTS["leverage"])))

    def _save(self):
        cfg = load_config()
        cfg["leverage"] = self.leverage_spin.value()
        save_config(cfg)
        self.config_saved.emit(cfg)
        self.accept()


class TPDialog(QDialog):
    """هدف الربح: سعر أو نسبة مئوية."""
    config_saved = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("quick_tp_title"))
        self.setMinimumWidth(280)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItem(tr("quick_tp_percent"), "percent")
        self.type_combo.addItem(tr("quick_tp_price"), "price")
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow(tr("quick_type"), self.type_combo)
        self.percent_spin = QDoubleSpinBox()
        self.percent_spin.setRange(0.01, 1000.0)
        self.percent_spin.setDecimals(2)
        # خطوة أصغر (0.10%) حتى لا يبدو أن أقل قيمة هي 1%
        self.percent_spin.setSingleStep(0.10)
        self.percent_spin.setSuffix(" %")
        form.addRow(QLabel(""), self.percent_spin)
        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0.0, 10_000_000.0)
        self.price_spin.setDecimals(4)
        form.addRow(QLabel(""), self.price_spin)
        layout.addLayout(form)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton(tr("risk_save"))
        ok_btn.clicked.connect(self._save)
        close_btn = QPushButton(tr("risk_close"))
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        self._load()
        self._on_type_changed(self.type_combo.currentIndex())

    def _on_type_changed(self, _idx):
        is_percent = self.type_combo.currentData() == "percent"
        self.percent_spin.setVisible(is_percent)
        self.price_spin.setVisible(not is_percent)

    def _load(self):
        cfg = load_config()
        tp = cfg.get("tp_type", DEFAULTS.get("tp_type", "percent"))
        idx = self.type_combo.findData(tp)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        v = float(cfg.get("tp_value", DEFAULTS.get("tp_value", 2.0)))
        if tp == "percent":
            self.percent_spin.setValue(v)
            self.price_spin.setValue(0.0)
        else:
            self.price_spin.setValue(v)
            self.percent_spin.setValue(2.0)

    def _save(self):
        cfg = load_config()
        cfg["tp_type"] = self.type_combo.currentData() or "percent"
        if cfg["tp_type"] == "percent":
            cfg["tp_value"] = self.percent_spin.value()
        else:
            cfg["tp_value"] = self.price_spin.value()
        save_config(cfg)
        self.config_saved.emit(cfg)
        self.accept()


class SLDialog(QDialog):
    """وقف الخسارة: سعر أو نسبة مئوية."""
    config_saved = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("quick_sl_title"))
        self.setMinimumWidth(280)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItem(tr("quick_sl_percent"), "percent")
        self.type_combo.addItem(tr("quick_sl_price"), "price")
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow(tr("quick_type"), self.type_combo)
        self.percent_spin = QDoubleSpinBox()
        self.percent_spin.setRange(-100.0, 0.0)
        self.percent_spin.setSingleStep(0.1)
        self.percent_spin.setDecimals(2)
        self.percent_spin.setSuffix(" %")
        form.addRow(QLabel(""), self.percent_spin)
        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0.0, 10_000_000.0)
        self.price_spin.setDecimals(4)
        form.addRow(QLabel(""), self.price_spin)
        layout.addLayout(form)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton(tr("risk_save"))
        ok_btn.clicked.connect(self._save)
        close_btn = QPushButton(tr("risk_close"))
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        self._load()
        self._on_type_changed(self.type_combo.currentIndex())

    def _on_type_changed(self, _idx):
        is_percent = self.type_combo.currentData() == "percent"
        self.percent_spin.setVisible(is_percent)
        self.price_spin.setVisible(not is_percent)

    def _load(self):
        cfg = load_config()
        sl = cfg.get("sl_type", DEFAULTS.get("sl_type", "percent"))
        idx = self.type_combo.findData(sl)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        v = float(cfg.get("sl_value", DEFAULTS.get("sl_value", -1.0)))
        if sl == "percent":
            self.percent_spin.setValue(v)
            self.price_spin.setValue(0.0)
        else:
            self.price_spin.setValue(v)
            self.percent_spin.setValue(-1.0)

    def _save(self):
        cfg = load_config()
        cfg["sl_type"] = self.type_combo.currentData() or "percent"
        if cfg["sl_type"] == "percent":
            cfg["sl_value"] = self.percent_spin.value()
        else:
            cfg["sl_value"] = self.price_spin.value()
        # منطقياً: من يضبط SL من الزر السريع يتوقع أن وقف الخسارة يعمل مباشرة.
        cfg["bot_auto_sl"] = True
        save_config(cfg)
        self.config_saved.emit(cfg)
        self.accept()


class MarketTypeDialog(QDialog):
    """سبوت (فوري) مقابل عقود (Futures)، أو تلقائي حسب الرافعة (>1 = عقود)."""
    config_saved = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("quick_market_type_title"))
        self.setMinimumWidth(320)
        layout = QVBoxLayout(self)
        self.type_combo = QComboBox()
        self.type_combo.addItem(tr("market_type_auto_label"), "auto")
        self.type_combo.addItem(tr("market_type_spot_label"), "spot")
        self.type_combo.addItem(tr("market_type_futures_label"), "futures")
        layout.addWidget(self.type_combo)
        hint = QLabel(tr("market_type_dialog_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #aaa; font-size: 11px;")
        layout.addWidget(hint)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton(tr("risk_save"))
        ok_btn.clicked.connect(self._save)
        close_btn = QPushButton(tr("risk_close"))
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        self._load()

    def _load(self):
        cfg = load_config()
        mt = (cfg.get("market_type") or DEFAULTS.get("market_type", "auto") or "auto").strip().lower()
        idx = self.type_combo.findData(mt)
        if idx < 0:
            idx = self.type_combo.findData("auto")
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)

    def _save(self):
        cfg = load_config()
        cfg["market_type"] = self.type_combo.currentData() or "auto"
        save_config(cfg)
        self.config_saved.emit(cfg)
        self.accept()


class LimitBuyDialog(QDialog):
    """حد الشراء: سعر أو نسبة مئوية (مثل وقف الخسارة)."""
    config_saved = pyqtSignal(dict)

    def __init__(self, parent=None, ref_price: float = 0.0):
        super().__init__(parent)
        self._ref_price = float(ref_price or 0.0)
        self.setWindowTitle(tr("quick_limit_buy_title"))
        self.setMinimumWidth(280)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItem(tr("quick_sl_percent"), "percent")
        self.type_combo.addItem(tr("quick_tp_price"), "price")
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow(tr("quick_type"), self.type_combo)
        self.percent_spin = QDoubleSpinBox()
        self.percent_spin.setRange(-50.0, 0.0)
        self.percent_spin.setDecimals(2)
        self.percent_spin.setSuffix(" %")
        self.percent_spin.setSpecialValueText("—")
        form.addRow(QLabel(""), self.percent_spin)
        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0.0, 10_000_000.0)
        self.price_spin.setDecimals(4)
        form.addRow(QLabel(""), self.price_spin)
        layout.addLayout(form)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton(tr("risk_save"))
        ok_btn.clicked.connect(self._save)
        close_btn = QPushButton(tr("risk_close"))
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        self._load()
        self._on_type_changed(self.type_combo.currentIndex())

    def _on_type_changed(self, _idx):
        is_percent = self.type_combo.currentData() == "percent"
        self.percent_spin.setVisible(is_percent)
        self.price_spin.setVisible(not is_percent)

    def _load(self):
        cfg = load_config()
        typ = cfg.get("limit_buy_type", "percent")
        p = float(cfg.get("limit_buy_price", 0) or 0)
        if p > 0 and typ == "percent":
            typ = "price"
        idx = self.type_combo.findData(typ)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        v = float(cfg.get("limit_buy_value", -2.0) or -2.0)
        if typ == "percent":
            self.percent_spin.setValue(v)
            self.price_spin.setValue(p)
        else:
            self.price_spin.setValue(p if p > 0 else v)
            self.percent_spin.setValue(-2.0)

    def _save(self):
        cfg = load_config()
        cfg["limit_buy_type"] = self.type_combo.currentData() or "percent"
        if cfg["limit_buy_type"] == "percent":
            cfg["limit_buy_value"] = self.percent_spin.value()
            rp = float(getattr(self, "_ref_price", 0.0) or 0.0)
            if rp > 0:
                cfg["limit_buy_anchor_price"] = rp
            else:
                cfg["limit_buy_anchor_price"] = 0.0
        else:
            cfg["limit_buy_value"] = self.price_spin.value()
            cfg["limit_buy_price"] = self.price_spin.value()
            cfg["limit_buy_anchor_price"] = 0.0
        save_config(cfg)
        self.config_saved.emit(cfg)
        self.accept()


class MaxTradesDialog(QDialog):
    """أقصى عدد صفقات مفتوحة في نفس الوقت للروبوت."""
    config_saved = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("quick_max_trades"))
        self.setMinimumWidth(260)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.trades_spin = QSpinBox()
        self.trades_spin.setRange(0, 50)
        self.trades_spin.setSpecialValueText(tr("quick_max_trades_unlimited"))
        self.trades_spin.setToolTip(tr("quick_max_trades_unlimited_hint"))
        form.addRow(tr("quick_max_trades"), self.trades_spin)
        layout.addLayout(form)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton(tr("risk_save"))
        ok_btn.clicked.connect(self._save)
        close_btn = QPushButton(tr("risk_close"))
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        self._load()

    def _load(self):
        cfg = load_config()
        v = int(cfg.get("bot_max_open_trades", DEFAULTS.get("bot_max_open_trades", 1)))
        self.trades_spin.setValue(max(0, min(50, v)))

    def _save(self):
        cfg = load_config()
        cfg["bot_max_open_trades"] = int(self.trades_spin.value())
        save_config(cfg)
        self.config_saved.emit(cfg)
        self.accept()


class LimitSellDialog(QDialog):
    """حد البيع: سعر أو نسبة مئوية (مثل حد الشراء)."""
    config_saved = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("quick_limit_sell_title"))
        self.setMinimumWidth(280)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.type_combo = QComboBox()
        self.type_combo.addItem(tr("quick_tp_percent"), "percent")
        self.type_combo.addItem(tr("quick_tp_price"), "price")
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        form.addRow(tr("quick_type"), self.type_combo)
        self.percent_spin = QDoubleSpinBox()
        self.percent_spin.setRange(0.0, 200.0)
        self.percent_spin.setDecimals(2)
        self.percent_spin.setSuffix(" %")
        form.addRow(QLabel(""), self.percent_spin)
        self.price_spin = QDoubleSpinBox()
        self.price_spin.setRange(0.0, 10_000_000.0)
        self.price_spin.setDecimals(4)
        form.addRow(QLabel(""), self.price_spin)
        layout.addLayout(form)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton(tr("risk_save"))
        ok_btn.clicked.connect(self._save)
        close_btn = QPushButton(tr("risk_close"))
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(ok_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)
        self._load()
        self._on_type_changed(self.type_combo.currentIndex())

    def _on_type_changed(self, _idx):
        is_percent = self.type_combo.currentData() == "percent"
        self.percent_spin.setVisible(is_percent)
        self.price_spin.setVisible(not is_percent)

    def _load(self):
        cfg = load_config()
        typ = cfg.get("limit_sell_type", "percent")
        p = float(cfg.get("limit_sell_price", 0) or 0)
        idx = self.type_combo.findData(typ)
        if idx >= 0:
            self.type_combo.setCurrentIndex(idx)
        v = float(cfg.get("limit_sell_value", 0.0) or 0.0)
        if typ == "percent":
            self.percent_spin.setValue(v)
            self.price_spin.setValue(p)
        else:
            self.price_spin.setValue(p if p > 0 else v)
            self.percent_spin.setValue(1.0)

    def _save(self):
        cfg = load_config()
        cfg["limit_sell_type"] = self.type_combo.currentData() or "percent"
        if cfg["limit_sell_type"] == "percent":
            cfg["limit_sell_value"] = self.percent_spin.value()
            cfg["limit_sell_price"] = 0.0
        else:
            cfg["limit_sell_value"] = self.price_spin.value()
            cfg["limit_sell_price"] = self.price_spin.value()
        save_config(cfg)
        self.config_saved.emit(cfg)
        self.accept()
