from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QListWidget,
    QPushButton,
    QLineEdit,
    QLabel,
    QWidget,
    QHBoxLayout,
    QToolButton,
    QListWidgetItem,
)
from PyQt6.QtCore import Qt, QEvent
from translations import tr
from binance_symbols import load_all_usdt_spot_trading_symbols
from config import load_config, save_config


class SymbolSelector(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.setWindowTitle("اختيار العملة")
        self.setMinimumSize(380, 560)

        layout = QVBoxLayout(self)

        self._hint = QLabel()
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: #8b95a0; font-size: 11px;")
        layout.addWidget(self._hint)

        self.search_box = QLineEdit()
        self.search_box.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.search_box.setPlaceholderText(
            "اكتب للبحث (لاتيني)، مثال: BTC أو PEPE أو 1000PEPE — اضغط Enter لعرض كل القائمة إن كانت فارغة"
        )
        self.search_box.textChanged.connect(self.filter_symbols)
        layout.addWidget(self.search_box)

        self.btn_favorites_only = QPushButton("المفضلة فقط: إيقاف")
        self.btn_favorites_only.setCheckable(True)
        self.btn_favorites_only.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_favorites_only.clicked.connect(self._toggle_favorites_only)
        layout.addWidget(self.btn_favorites_only)

        self.list = QListWidget()
        self.list.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.list.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list)

        self.btn_select = QPushButton("اختيار")
        self.btn_select.clicked.connect(self.select_symbol)
        layout.addWidget(self.btn_select)

        self.selected_symbol = None
        self.all_symbols = []
        self._favorite_symbols = self._load_favorites()
        self._favorites_only = False

        self.load_symbols()

    def _load_favorites(self) -> set[str]:
        cfg = load_config()
        fav = cfg.get("favorite_symbols") or []
        out: set[str] = set()
        for s in fav:
            u = str(s or "").strip().upper()
            if u:
                out.add(u)
        return out

    def _save_favorites(self) -> None:
        cfg = load_config()
        cfg["favorite_symbols"] = sorted(self._favorite_symbols)
        save_config(cfg)

    def _ordered_symbols(self, symbols: list[str]) -> list[str]:
        if self._favorites_only:
            symbols = [s for s in symbols if s in self._favorite_symbols]
        fav = [s for s in symbols if s in self._favorite_symbols]
        rest = [s for s in symbols if s not in self._favorite_symbols]
        return fav + rest

    def _render_symbols(self, symbols: list[str]) -> None:
        self.list.clear()
        for sym in self._ordered_symbols(symbols):
            item = QListWidgetItem(self.list)
            item.setData(Qt.ItemDataRole.UserRole, sym)

            row = QWidget(self.list)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(6, 2, 6, 2)
            row_layout.setSpacing(6)

            sym_label = QLabel(sym, row)
            sym_label.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            row_layout.addWidget(sym_label, 1)

            star_btn = QToolButton(row)
            star_btn.setText("★" if sym in self._favorite_symbols else "☆")
            star_btn.setToolTip("إزالة من المفضلة" if sym in self._favorite_symbols else "إضافة إلى المفضلة")
            star_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            star_btn.setAutoRaise(True)
            star_btn.setStyleSheet(
                "QToolButton { border: none; font-size: 16px; color: #f6c85f; padding: 0 2px; }"
            )
            star_btn.clicked.connect(lambda _=False, s=sym: self._toggle_favorite(s))
            row_layout.addWidget(star_btn, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            # النقر على التسمية/النجمة لا يحدّد QListWidgetItem تلقائياً — فيبقى زر «اختيار» بلا عمل
            for w in (row, sym_label, star_btn):
                w.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            for i in range(self.list.count()):
                it = self.list.item(i)
                wgt = self.list.itemWidget(it)
                if wgt is None:
                    continue
                if watched is wgt or wgt.isAncestorOf(watched):
                    self.list.setCurrentItem(it)
                    self.list.scrollToItem(it)
                    break
        elif event.type() == QEvent.Type.MouseButtonDblClick:
            for i in range(self.list.count()):
                it = self.list.item(i)
                wgt = self.list.itemWidget(it)
                if wgt is None:
                    continue
                if watched is wgt or wgt.isAncestorOf(watched):
                    t = str(it.data(Qt.ItemDataRole.UserRole) or "").strip().upper()
                    if t and not t.startswith("—") and t != tr("symbol_error_load"):
                        self.selected_symbol = t
                        self.accept()
                        return True
                    break
        return super().eventFilter(watched, event)

    def _toggle_favorite(self, symbol: str) -> None:
        sym = (symbol or "").strip().upper()
        if not sym:
            return
        if sym in self._favorite_symbols:
            self._favorite_symbols.remove(sym)
        else:
            self._favorite_symbols.add(sym)
        self._save_favorites()
        self.filter_symbols(self.search_box.text())

    def _toggle_favorites_only(self) -> None:
        self._favorites_only = bool(self.btn_favorites_only.isChecked())
        self.btn_favorites_only.setText("المفضلة فقط: تشغيل" if self._favorites_only else "المفضلة فقط: إيقاف")
        self.filter_symbols(self.search_box.text())

    def load_symbols(self):
        try:
            cfg = load_config()
            symbols = load_all_usdt_spot_trading_symbols()
            self.all_symbols = list(symbols) if symbols else []
            if (cfg.get("exchange") or "").lower() == "etoro" and self.all_symbols:
                from etoro_symbols import filter_symbols_for_etoro_trading

                fav = self._favorite_symbols
                last = {str(cfg.get("last_symbol") or "").strip().upper()}
                last.discard("")
                self.all_symbols = filter_symbols_for_etoro_trading(
                    self.all_symbols, favorites=fav, extra_keep=last
                )
            if not self.all_symbols:
                if symbols:
                    self._hint.setText(
                        "قائمة الأزواج فارغة بعد التصفية (eToro). أضف الرمز للمفضلة أو غيّر المنصة في الإعدادات."
                    )
                else:
                    self._hint.setText(
                        "تعذر جلب قائمة Binance (شبكة/حظر إقليمي). جرّب VPN أو شبكة أخرى، أو اكتب الرمز كاملاً في مربع الرمز الرئيسي واضغط Enter."
                    )
                self.list.addItem(tr("symbol_error_load"))
                self.setWindowTitle("اختيار العملة — لا توجد بيانات")
                return
            self.setWindowTitle(f"اختيار العملة — {len(self.all_symbols)} زوج USDT")
            if (cfg.get("exchange") or "").lower() == "etoro":
                self._hint.setText(
                    f"منصة eToro: عُرض {len(self.all_symbols)} زوجاً متوافقاً تقريباً (الشارت من Binance؛ التنفيذ على eToro). أضف ★ للمفضلة لإبقاء أي زوج تحتاجه."
                )
            else:
                self._hint.setText(
                    f"تم تحميل {len(self.all_symbols)} زوجاً. اضغط ★ لإضافة/إزالة المفضلة، وامسح البحث لعرض الكل."
                )
            self._render_symbols(self.all_symbols)
        except Exception as e:
            self.all_symbols = []
            self._hint.setText(f"خطأ تحميل: {e}")
            self.list.addItem(tr("symbol_error_load"))
            print("Symbol load error:", e)

    def filter_symbols(self, text):
        if not self.all_symbols:
            self.list.clear()
            self.list.addItem(tr("symbol_error_load"))
            return
        q = (text or "").strip().upper()
        if not q:
            self._render_symbols(self.all_symbols)
            if self._favorites_only and self.list.count() == 0:
                self.list.addItem("— لا توجد رموز مفضلة بعد —")
            return
        filtered: list[str] = []
        for sym in self.all_symbols:
            if q in sym:
                filtered.append(sym)
        if not filtered:
            self.list.clear()
            if self._favorites_only:
                self.list.addItem(f"— لا نتائج في المفضلة لـ «{q}» —")
            else:
                self.list.addItem(f"— لا نتائج لـ «{q}» — جرّب 1000PEPE أو الاسم الكامل من Binance")
            return
        self._render_symbols(filtered)
        if self._favorites_only and self.list.count() == 0:
            self.list.addItem("— لا توجد رموز مفضلة بعد —")

    def _on_item_double_clicked(self, item):
        t = str(item.data(Qt.ItemDataRole.UserRole) or item.text() or "").strip().upper()
        if t.startswith("—") or t == tr("symbol_error_load"):
            return
        self.selected_symbol = t
        self.accept()

    def select_symbol(self):
        item = self.list.currentItem()
        if item is None and self.list.count() == 1:
            only = self.list.item(0)
            if only is not None and self.list.itemWidget(only) is not None:
                self.list.setCurrentItem(only)
                item = only
        if not item:
            return
        t = str(item.data(Qt.ItemDataRole.UserRole) or item.text() or "").strip().upper()
        if t.startswith("—") or t == tr("symbol_error_load"):
            return
        self.selected_symbol = t
        self.accept()
