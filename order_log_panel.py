from PyQt6.QtWidgets import (
    QWidget,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)
from PyQt6.QtCore import Qt

from config import load_config, save_config
from format_utils import format_price
from translations import tr


class OrderLogPanel(QWidget):
    """سجل أوامر حد الشراء والبيع مع إمكانية التعديل والإلغاء."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)

        self.setObjectName("OrderLogPanel")
        self.setMinimumWidth(680)
        self.setMinimumHeight(260)
        _cfg0 = load_config()
        self._active_symbol: str = str(_cfg0.get("last_symbol") or "").strip().upper()

        self._build_ui()
        self.refresh_from_config()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        title = QLabel(tr("center_orders_log"))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 13px; font-weight: bold;")
        layout.addWidget(title)

        sub = QLabel(tr("order_log_panel_subtitle"))
        sub.setWordWrap(True)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub.setStyleSheet("color: #9aa0a6; font-size: 10px; padding: 2px 4px 6px 4px;")
        layout.addWidget(sub)

        self._pair_banner = QLabel()
        self._pair_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pair_banner.setWordWrap(True)
        self._pair_banner.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: #dbeafe; padding: 6px 8px; "
            "background-color: #1e293b; border-radius: 8px; border: 1px solid #334155;"
        )
        layout.addWidget(self._pair_banner)

        self._pair_hint = QLabel()
        self._pair_hint.setWordWrap(True)
        self._pair_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._pair_hint.setStyleSheet("color: #8b9cb3; font-size: 10px; padding: 0 4px 6px 4px;")
        layout.addWidget(self._pair_hint)

        bind_row = QHBoxLayout()
        bind_row.setSpacing(8)
        bind_lbl = QLabel(tr("order_log_bind_symbol_label"))
        bind_lbl.setStyleSheet("font-size: 11px;")
        self._bind_symbol_edit = QLineEdit()
        self._bind_symbol_edit.setPlaceholderText(tr("order_log_bind_symbol_placeholder"))
        self._bind_symbol_edit.setClearButtonEnabled(True)
        self._bind_symbol_edit.setMinimumWidth(200)
        self._bind_symbol_edit.setStyleSheet("font-size: 11px; padding: 4px 8px;")
        bind_save = QPushButton(tr("order_log_bind_save"))
        bind_save.setStyleSheet("font-size: 10px; padding: 4px 12px;")
        bind_save.clicked.connect(self._save_bind_symbol)
        self._bind_symbol_edit.returnPressed.connect(self._save_bind_symbol)
        bind_row.addWidget(bind_lbl)
        bind_row.addWidget(self._bind_symbol_edit, 1)
        bind_row.addWidget(bind_save)
        layout.addLayout(bind_row)

        self.table = QTableWidget(2, 3, self)
        self.table.setHorizontalHeaderLabels(
            [
                tr("order_log_col_order_kind"),
                tr("order_log_col_limit_target"),
                tr("trading_actions"),
            ]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        # حد أدنى لعرض عمود الإجراءات حتى تتسع الأزرار
        header.setMinimumSectionSize(180)
        header.setStretchLastSection(False)

        # تكبير ارتفاع الصفوف حتى تظهر الأزرار والنصوص بوضوح
        self.table.verticalHeader().setDefaultSectionSize(38)

        self.row_labels = [
            tr("order_log_kind_buy_limit"),
            tr("order_log_kind_sell_limit"),
        ]

        layout.addWidget(self.table)

        hint = QLabel(tr("quick_limit_hint_cancel"))
        hint.setAlignment(Qt.AlignmentFlag.AlignLeft)
        hint.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(hint)

    def change_symbol(self, symbol: str) -> None:
        """يُستدعى من CenterPanel عند تغيير رمز الشارت."""
        self._active_symbol = str(symbol or "").strip().upper()
        self._refresh_bind_banner()

    def _refresh_bind_banner(self) -> None:
        cfg = load_config()
        bind = str(cfg.get("limit_orders_bind_symbol") or "").strip().upper()
        chart_sym = (getattr(self, "_active_symbol", "") or "").strip().upper()
        if not chart_sym:
            chart_sym = str(cfg.get("last_symbol") or "").strip().upper() or "—"
        if getattr(self, "_bind_symbol_edit", None) is not None:
            self._bind_symbol_edit.blockSignals(True)
            self._bind_symbol_edit.setText(bind)
            self._bind_symbol_edit.blockSignals(False)
        if bind:
            self._pair_banner.setText(tr("order_log_bound_pair_banner").format(symbol=bind))
            self._pair_hint.setText(
                tr("order_log_bound_pair_hint").format(symbol=bind, chart=chart_sym or "—")
            )
        else:
            self._pair_banner.setText(tr("order_log_active_pair_banner").format(symbol=chart_sym or "—"))
            self._pair_hint.setText(tr("order_log_active_pair_hint"))

    def _save_bind_symbol(self) -> None:
        raw = (self._bind_symbol_edit.text() or "").strip().upper().replace(" ", "")
        cfg = load_config()
        if raw:
            cfg["limit_orders_bind_symbol"] = raw
        else:
            cfg["limit_orders_bind_symbol"] = ""
        save_config(cfg)
        self._refresh_bind_banner()

    # ------------------------------------------------------------
    # تحديث الجدول من ملف الإعدادات
    # ------------------------------------------------------------
    def refresh_from_config(self) -> None:
        self._refresh_bind_banner()
        cfg = load_config()

        # حد الشراء
        buy_type = (cfg.get("limit_buy_type") or "percent").strip() or "percent"
        buy_val = float(cfg.get("limit_buy_value", 0.0) or 0.0)
        buy_price = float(cfg.get("limit_buy_price", 0.0) or 0.0)

        self._set_row(
            row=0,
            label=self.row_labels[0],
            typ=buy_type,
            value=buy_val,
            price=buy_price,
            is_buy=True,
        )

        # حد البيع
        sell_type = (cfg.get("limit_sell_type") or "percent").strip() or "percent"
        sell_val = float(cfg.get("limit_sell_value", 0.0) or 0.0)
        sell_price = float(cfg.get("limit_sell_price", 0.0) or 0.0)

        self._set_row(
            row=1,
            label=self.row_labels[1],
            typ=sell_type,
            value=sell_val,
            price=sell_price,
            is_buy=False,
        )

    def _set_row(
        self,
        row: int,
        label: str,
        typ: str,
        value: float,
        price: float,
        is_buy: bool,
    ) -> None:
        label_item = QTableWidgetItem(label)
        label_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(row, 0, label_item)

        if typ == "percent":
            if value == 0:
                val_text = "—"
            else:
                sign = "+" if (not is_buy and value > 0) else ""
                val_text = f"{sign}{value:.2f}%"
        else:
            if price <= 0:
                val_text = "—"
            else:
                val_text = format_price(price)

        value_item = QTableWidgetItem(val_text)
        value_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        self.table.setItem(row, 1, value_item)

        edit_btn = QPushButton(tr("risk_edit"))
        cancel_btn = QPushButton(tr("risk_close"))
        btn_style = "font-size: 10px; padding: 2px 6px;"
        edit_btn.setStyleSheet(btn_style)
        cancel_btn.setStyleSheet(btn_style)

        btn_container = QWidget()
        btn_layout = QHBoxLayout(btn_container)
        btn_layout.setContentsMargins(0, 0, 0, 0)
        btn_layout.setSpacing(4)
        btn_layout.addWidget(edit_btn)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addStretch(1)
        self.table.setCellWidget(row, 2, btn_container)

        if is_buy:
            edit_btn.clicked.connect(self._edit_limit_buy)
            cancel_btn.clicked.connect(self._cancel_limit_buy)
        else:
            edit_btn.clicked.connect(self._edit_limit_sell)
            cancel_btn.clicked.connect(self._cancel_limit_sell)

    # ------------------------------------------------------------
    # إجراءات التعديل / الإلغاء
    # ------------------------------------------------------------
    def _edit_limit_buy(self) -> None:
        try:
            from quick_settings_dialogs import LimitBuyDialog

            d = LimitBuyDialog(self)
            d.config_saved.connect(lambda _cfg: self.refresh_from_config())
            d.exec()
        except Exception:
            pass
        self.refresh_from_config()

    def _edit_limit_sell(self) -> None:
        try:
            from quick_settings_dialogs import LimitSellDialog

            d = LimitSellDialog(self)
            d.config_saved.connect(lambda _cfg: self.refresh_from_config())
            d.exec()
        except Exception:
            pass
        self.refresh_from_config()

    def _cancel_limit_buy(self) -> None:
        cfg = load_config()
        cfg["limit_buy_type"] = "percent"
        cfg["limit_buy_value"] = 0.0
        cfg["limit_buy_price"] = 0.0
        cfg["limit_buy_anchor_price"] = 0.0
        save_config(cfg)
        self.refresh_from_config()

    def _cancel_limit_sell(self) -> None:
        cfg = load_config()
        cfg["limit_sell_type"] = "percent"
        cfg["limit_sell_value"] = 0.0
        cfg["limit_sell_price"] = 0.0
        save_config(cfg)
        self.refresh_from_config()

