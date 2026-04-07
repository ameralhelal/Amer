# risk_settings_window.py — نافذة إعدادات المخاطر (مبلغ، حدود، شروط البوت؛ الرافعة من لوحة سريعة)
import json
import logging
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QWidget,
    QSpinBox, QGroupBox, QFormLayout, QComboBox, QCheckBox, QDoubleSpinBox, QLineEdit,
    QScrollArea, QStackedWidget, QMessageBox, QFileDialog, QListWidget, QFrame,
    QListWidgetItem, QTextEdit, QPlainTextEdit, QDialogButtonBox, QButtonGroup,
)
from PyQt6.QtCore import pyqtSignal, Qt, QUrl
from PyQt6.QtGui import QCloseEvent, QDesktopServices

from config import load_config, save_config, DEFAULTS, STRATEGY_PRESETS, get_circuit_breaker_config
from bot_logic import apply_execution_filters
from composite_signal import clamp_composite_thresholds
from translations import tr, get_language
from telegram_notifier import send_telegram_test_message

log = logging.getLogger("trading.risk")


class RiskSettingsWindow(QDialog):
    """تحرير: مبلغ التداول، الرافعة، حد الخسارة اليومية، لغة الواجهة."""
    config_saved = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("risk_title"))
        self.setMinimumSize(400, 380)
        self._dirty = False  # تغييرات غير محفوظة

        # تخطيط خارجي + منطقة قابلة للتمرير حتى لا تطول النافذة
        outer_layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        # مربعات الاختيار: مربع أصغر، خلفية سماوية عند التفعيل، علامة الصح سوداء
        _cb_style = (
            "QCheckBox { color: #e8e8e8; font-size: 13px; } "
            "QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #888; border-radius: 3px; background-color: #404040; } "
            "QCheckBox::indicator:checked { background-color: #87CEEB; border-color: #5ba3d0; color: #000000; } "
            "QCheckBox::indicator:hover { border-color: #aaa; background-color: #505050; } "
            "QCheckBox::indicator:checked:hover { background-color: #9ed5f0; border-color: #5ba3d0; color: #000000; } "
        )
        content.setStyleSheet((content.styleSheet() or "") + " " + _cb_style)

        # صف المستطيلات: عامة | متقدمة — عند الضغط يتلون بالأزرق
        _box_style = (
            "QPushButton { background-color: #2A2A2D; color: white; border: 1px solid #3A3A3D; "
            "border-radius: 5px; padding: 8px 14px; font-weight: bold; min-height: 22px; font-size: 11px; } "
            "QPushButton:hover { background-color: #3D3D42; } "
            "QPushButton:checked { background-color: #0A84FF; border-color: #0066CC; }"
        )
        section_row = QHBoxLayout()
        section_row.setSpacing(8)
        self.btn_section_general = QPushButton(tr("risk_bot_general_group"))
        self.btn_section_advanced = QPushButton(tr("risk_bot_advanced_group"))
        self.btn_section_guide = QPushButton(tr("risk_guide_tab"))
        for btn in (self.btn_section_general, self.btn_section_advanced, self.btn_section_guide):
            btn.setCheckable(True)
            btn.setStyleSheet(_box_style)
        self.btn_section_general.setChecked(True)

        def _go_section(idx):
            self._set_main_risk_section(idx)

        self.btn_section_general.clicked.connect(lambda: _go_section(0))
        self.btn_section_advanced.clicked.connect(lambda: _go_section(1))
        self.btn_section_guide.clicked.connect(lambda: _go_section(2))
        section_row.addWidget(self.btn_section_general)
        section_row.addWidget(self.btn_section_advanced)
        section_row.addWidget(self.btn_section_guide)
        section_row.addStretch(1)

        self.main_sections_frame = QFrame()
        self.main_sections_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.main_sections_frame.setObjectName("riskMainSectionFrame")
        self.main_sections_frame.setStyleSheet(
            "QFrame#riskMainSectionFrame { "
            "border: 1px solid #3a3a3d; border-radius: 8px; background-color: #26262a; padding: 2px; }"
        )
        main_sections_outer = QVBoxLayout(self.main_sections_frame)
        main_sections_outer.setContentsMargins(10, 10, 10, 10)
        main_sections_outer.setSpacing(10)
        main_sections_outer.addLayout(section_row)

        self._stack = QStackedWidget()
        main_sections_outer.addWidget(self._stack, 1)

        # صفحة دليل الإعدادات (شرح الحقول والقيم المقترحة)
        guide_page = QWidget()
        guide_layout = QVBoxLayout(guide_page)
        guide_intro = QLabel(tr("risk_guide_intro"))
        guide_intro.setStyleSheet("color: #9ab; font-size: 12px; padding: 4px 2px; font-weight: bold;")
        guide_intro.setWordWrap(True)
        guide_layout.addWidget(guide_intro)
        self.guide_text = QTextEdit()
        self.guide_text.setReadOnly(True)
        self.guide_text.setPlainText(
            "\n\n".join(
                [tr(f"risk_guide_part{i}") for i in range(1, 8)]
            )
        )
        self.guide_text.setMinimumHeight(320)
        self.guide_text.setStyleSheet(
            "QTextEdit { background-color: #1e1e22; color: #ddd; font-size: 12px; "
            "border: 1px solid #3a3a3d; border-radius: 6px; padding: 10px; }"
        )
        guide_layout.addWidget(self.guide_text)

        # صفحة الإعدادات العامة للبوت — تبويبات فرعية + تيليجرام أسفلها
        general_page = QWidget()
        general_layout = QVBoxLayout(general_page)
        # أقسام «عام» بنفس أسلوب أزرار عام/متقدم/دليل: إطار + أزرار تتبدل للأزرق
        self.general_sub_frame = QFrame()
        self.general_sub_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self.general_sub_frame.setStyleSheet(
            "QFrame#riskGeneralSubFrame { "
            "border: 1px solid #3a3a3d; border-radius: 8px; background-color: #26262a; padding: 2px; }"
        )
        self.general_sub_frame.setObjectName("riskGeneralSubFrame")
        general_sub_outer = QVBoxLayout(self.general_sub_frame)
        general_sub_outer.setContentsMargins(10, 10, 10, 10)
        general_sub_outer.setSpacing(10)
        general_sub_btn_row = QHBoxLayout()
        general_sub_btn_row.setSpacing(8)
        self._general_sub_stack = QStackedWidget()
        self._general_sub_btn_group = QButtonGroup(self)
        self._general_sub_btn_group.setExclusive(True)
        for idx, lab in enumerate(
            (
                tr("risk_general_subtab_basic"),
                tr("risk_general_subtab_buy_sell"),
            )
        ):
            b = QPushButton(lab)
            b.setCheckable(True)
            b.setStyleSheet(_box_style)
            self._general_sub_btn_group.addButton(b, idx)
            general_sub_btn_row.addWidget(b)
        general_sub_btn_row.addStretch(1)
        first_general_sub = self._general_sub_btn_group.button(0)
        if first_general_sub:
            first_general_sub.setChecked(True)
        self._general_sub_btn_group.idClicked.connect(self._general_sub_stack.setCurrentIndex)
        general_sub_outer.addLayout(general_sub_btn_row)
        restore_signal_exit_row = QHBoxLayout()
        self.btn_restore_signal_exit_defaults = QPushButton(tr("risk_restore_defaults_signal_exit"))
        self.btn_restore_signal_exit_defaults.setToolTip(tr("risk_restore_defaults_signal_exit_hint"))
        self.btn_restore_signal_exit_defaults.setStyleSheet(
            "QPushButton { background-color: #2A2A2D; color: #8fc7ff; border: 1px solid #4a6a8a; "
            "border-radius: 5px; padding: 6px 12px; font-size: 11px; } "
            "QPushButton:hover { background-color: #333338; }"
        )
        self.btn_restore_signal_exit_defaults.clicked.connect(self._restore_defaults_signal_and_exit)
        restore_signal_exit_row.addWidget(self.btn_restore_signal_exit_defaults)
        restore_signal_exit_row.addStretch(1)
        general_sub_outer.addLayout(restore_signal_exit_row)
        general_sub_outer.addWidget(self._general_sub_stack)

        def _add_sub_form_section(form: QFormLayout, title_key: str):
            sep = QFrame()
            sep.setFrameShape(QFrame.Shape.HLine)
            sep.setFrameShadow(QFrame.Shadow.Sunken)
            title = QLabel(tr(title_key))
            title.setStyleSheet("color: #8fc7ff; font-size: 12px; font-weight: bold; padding-top: 6px;")
            form.addRow("", sep)
            form.addRow("", title)

        basic_page = QWidget()
        basic_layout = QVBoxLayout(basic_page)
        trade_horizon_group = QGroupBox(tr("risk_trade_horizon_section"))
        trade_horizon_layout = QVBoxLayout()
        horizon_row = QHBoxLayout()
        self.trade_horizon_combo = QComboBox()
        self.trade_horizon_combo.addItem(tr("risk_trade_horizon_short"), "short")
        self.trade_horizon_combo.addItem(tr("risk_trade_horizon_swing"), "swing")
        self.trade_horizon_combo.currentIndexChanged.connect(self._on_trade_horizon_changed)
        horizon_row.addWidget(QLabel(tr("risk_trade_horizon_label")))
        horizon_row.addWidget(self.trade_horizon_combo, 1)
        trade_horizon_layout.addLayout(horizon_row)
        horizon_hint = QLabel(tr("risk_trade_horizon_hint"))
        horizon_hint.setStyleSheet("color: #888; font-size: 11px;")
        horizon_hint.setWordWrap(True)
        trade_horizon_layout.addWidget(horizon_hint)
        trade_horizon_group.setLayout(trade_horizon_layout)
        basic_layout.addWidget(trade_horizon_group)
        strategy_group = QGroupBox(tr("risk_strategy_group"))
        strategy_layout = QFormLayout()
        self.strategy_combo = QComboBox()
        # وضع الاستراتيجية:
        # - custom  : إعداداتك الحالية كما هي
        # - auto    : سلسلة قواعد الذكاء حسب نظام السوق (regime) عند تفعيل الموجّه؛ وإلا بروفايل فقط
        # - باقي القيم: قوالب ثابتة (سكالبينغ، ارتداد، اتباع الاتجاه، …)
        self.strategy_combo.addItem(tr("risk_strategy_custom"), "custom")
        self.strategy_combo.addItem(tr("risk_strategy_auto"), "auto")
        self.strategy_combo.addItem(tr("risk_strategy_scalping"), "scalping")
        self.strategy_combo.addItem(tr("risk_strategy_bounce"), "bounce")
        self.strategy_combo.addItem(tr("risk_strategy_trend"), "trend")
        self.strategy_combo.addItem(tr("risk_strategy_dca"), "dca")
        self.strategy_combo.addItem(tr("risk_strategy_grid"), "grid")
        self.strategy_combo.addItem(tr("risk_strategy_3commas"), "3commas")
        self.strategy_combo.addItem(tr("risk_strategy_breakout"), "breakout")
        self.strategy_combo.currentIndexChanged.connect(self._on_strategy_changed)
        strategy_layout.addRow(tr("risk_strategy_label"), self.strategy_combo)
        self.strategy_apply_btn = QPushButton(tr("risk_strategy_apply_preset"))
        self.strategy_apply_btn.setToolTip(tr("risk_strategy_apply_preset_hint"))
        self.strategy_apply_btn.clicked.connect(self._apply_strategy_preset)
        strategy_layout.addRow("", self.strategy_apply_btn)
        strategy_hint = QLabel(tr("risk_strategy_apply_preset_hint"))
        strategy_hint.setStyleSheet("color: #888; font-size: 11px;")
        strategy_hint.setWordWrap(True)
        strategy_layout.addRow("", strategy_hint)
        self.apply_conditions_presets_btn = QPushButton()
        self.apply_conditions_presets_btn.setCheckable(True)
        self.apply_conditions_presets_btn.setToolTip(tr("risk_apply_conditions_presets_hint"))
        self.apply_conditions_presets_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.apply_conditions_presets_btn.toggled.connect(self._on_apply_conditions_presets_toggled)
        strategy_layout.addRow(tr("risk_apply_conditions_presets") + ":", self.apply_conditions_presets_btn)
        apply_presets_hint = QLabel(tr("risk_apply_conditions_presets_hint"))
        apply_presets_hint.setStyleSheet("color: #888; font-size: 11px;")
        apply_presets_hint.setWordWrap(True)
        strategy_layout.addRow("", apply_presets_hint)
        self.bot_follow_suggested_strategy_check = QCheckBox(tr("risk_bot_follow_suggested_strategy"))
        self.bot_follow_suggested_strategy_check.setToolTip(tr("risk_bot_follow_suggested_strategy_hint"))
        self.bot_follow_suggested_strategy_check.stateChanged.connect(self._set_dirty)
        strategy_layout.addRow("", self.bot_follow_suggested_strategy_check)
        self.bot_follow_strategy_sec_spin = QSpinBox()
        self.bot_follow_strategy_sec_spin.setRange(15, 300)
        self.bot_follow_strategy_sec_spin.setSuffix(" ث" if get_language() == "ar" else " s")
        self.bot_follow_strategy_sec_spin.valueChanged.connect(self._set_dirty)
        strategy_layout.addRow(tr("risk_bot_follow_suggested_sec"), self.bot_follow_strategy_sec_spin)
        strategy_group.setLayout(strategy_layout)
        basic_layout.addWidget(strategy_group)

        limits_intro = QLabel(tr("risk_general_subtab_limits_intro"))
        limits_intro.setStyleSheet("color: #9ab; font-size: 11px; padding: 4px 2px;")
        limits_intro.setWordWrap(True)
        basic_layout.addWidget(limits_intro)

        group_amount = QGroupBox(tr("risk_group"))
        form_amt = QFormLayout()
        self.amount_spin = QSpinBox()
        self.amount_spin.setRange(1, 1_000_000)
        self.amount_spin.setSuffix(" USDT")
        form_amt.addRow(tr("risk_amount"), self.amount_spin)
        self.daily_loss_spin = QSpinBox()
        self.daily_loss_spin.setRange(0, 1_000_000)
        self.daily_loss_spin.setSuffix(
            " USDT (0 = " + ("معطّل" if get_language() == "ar" else "disabled") + ")"
        )
        self.daily_loss_spin.setSpecialValueText(tr("risk_disabled"))
        form_amt.addRow(tr("risk_daily_limit"), self.daily_loss_spin)
        self.max_trades_per_day_spin = QSpinBox()
        self.max_trades_per_day_spin.setRange(0, 999)
        self.max_trades_per_day_spin.setSpecialValueText(tr("risk_disabled"))
        form_amt.addRow(tr("risk_max_trades_per_day"), self.max_trades_per_day_spin)
        self.bot_max_open_trades_spin = QSpinBox()
        self.bot_max_open_trades_spin.setRange(0, 50)
        self.bot_max_open_trades_spin.setSpecialValueText(tr("quick_max_trades_unlimited"))
        self.bot_max_open_trades_spin.setToolTip(tr("quick_tooltip_max_trades"))
        form_amt.addRow(tr("risk_bot_max_open_trades"), self.bot_max_open_trades_spin)
        _mopen_hint = QLabel(tr("risk_bot_max_open_trades_hint"))
        _mopen_hint.setStyleSheet("color: #888; font-size: 11px;")
        _mopen_hint.setWordWrap(True)
        form_amt.addRow("", _mopen_hint)
        self.max_consecutive_losses_spin = QSpinBox()
        self.max_consecutive_losses_spin.setRange(0, 50)
        self.max_consecutive_losses_spin.setSpecialValueText(tr("risk_disabled"))
        form_amt.addRow(tr("risk_max_consecutive_losses"), self.max_consecutive_losses_spin)
        self.cb_enabled_check = QCheckBox()
        form_amt.addRow(tr("risk_cb_enabled"), self.cb_enabled_check)
        cb_hint = QLabel(tr("risk_cb_enabled_hint"))
        cb_hint.setStyleSheet("color: #888; font-size: 11px;")
        cb_hint.setWordWrap(True)
        form_amt.addRow("", cb_hint)
        self.cb_volatility_spin = QDoubleSpinBox()
        self.cb_volatility_spin.setRange(0.1, 20.0)
        self.cb_volatility_spin.setDecimals(2)
        self.cb_volatility_spin.setSingleStep(0.1)
        self.cb_volatility_spin.setSuffix(" %")
        form_amt.addRow(tr("risk_cb_volatility_pct_max"), self.cb_volatility_spin)
        self.cb_adx_spin = QDoubleSpinBox()
        self.cb_adx_spin.setRange(5.0, 60.0)
        self.cb_adx_spin.setDecimals(1)
        self.cb_adx_spin.setSingleStep(0.5)
        form_amt.addRow(tr("risk_cb_adx_min"), self.cb_adx_spin)
        cb_vol_adx_hint = QLabel(tr("risk_cb_vol_adx_pair_hint"))
        cb_vol_adx_hint.setStyleSheet("color: #888; font-size: 11px;")
        cb_vol_adx_hint.setWordWrap(True)
        form_amt.addRow("", cb_vol_adx_hint)
        self.cb_mtf_bias_spin = QDoubleSpinBox()
        self.cb_mtf_bias_spin.setRange(-2.0, 0.0)
        self.cb_mtf_bias_spin.setDecimals(2)
        self.cb_mtf_bias_spin.setSingleStep(0.05)
        form_amt.addRow(tr("risk_cb_mtf_bias_floor"), self.cb_mtf_bias_spin)
        self.cb_mtf_rsi_spin = QDoubleSpinBox()
        self.cb_mtf_rsi_spin.setRange(30.0, 70.0)
        self.cb_mtf_rsi_spin.setDecimals(1)
        self.cb_mtf_rsi_spin.setSingleStep(0.5)
        form_amt.addRow(tr("risk_cb_mtf_rsi_threshold"), self.cb_mtf_rsi_spin)
        cb_mtf_rsi_hint = QLabel(tr("risk_cb_mtf_rsi_threshold_hint"))
        cb_mtf_rsi_hint.setStyleSheet("color: #888; font-size: 11px;")
        cb_mtf_rsi_hint.setWordWrap(True)
        form_amt.addRow("", cb_mtf_rsi_hint)
        self.cb_pause_spin = QSpinBox()
        self.cb_pause_spin.setRange(1, 240)
        self.cb_pause_spin.setSuffix(" min")
        form_amt.addRow(tr("risk_cb_pause_minutes"), self.cb_pause_spin)
        self.max_trades_per_symbol_spin = QSpinBox()
        self.max_trades_per_symbol_spin.setRange(0, 999)
        self.max_trades_per_symbol_spin.setSpecialValueText(tr("risk_disabled"))
        form_amt.addRow(tr("risk_max_trades_per_symbol"), self.max_trades_per_symbol_spin)
        self.portfolio_max_exposure_spin = QSpinBox()
        self.portfolio_max_exposure_spin.setRange(0, 10_000_000)
        self.portfolio_max_exposure_spin.setSingleStep(100)
        self.portfolio_max_exposure_spin.setSuffix(" USDT")
        self.portfolio_max_exposure_spin.setSpecialValueText(tr("risk_disabled"))
        form_amt.addRow(tr("risk_portfolio_max_exposure_usdt"), self.portfolio_max_exposure_spin)
        portfolio_exp_hint = QLabel(tr("risk_portfolio_max_exposure_hint"))
        portfolio_exp_hint.setStyleSheet("color: #888; font-size: 11px;")
        portfolio_exp_hint.setWordWrap(True)
        form_amt.addRow("", portfolio_exp_hint)
        self.bot_same_symbol_buy_min_interval_spin = QSpinBox()
        self.bot_same_symbol_buy_min_interval_spin.setRange(0, 240)
        self.bot_same_symbol_buy_min_interval_spin.setSuffix(" min")
        self.bot_same_symbol_buy_min_interval_spin.setSpecialValueText(tr("risk_disabled"))
        form_amt.addRow(
            tr("risk_bot_same_symbol_buy_min_interval_min"),
            self.bot_same_symbol_buy_min_interval_spin,
        )
        same_sym_hint = QLabel(tr("risk_bot_same_symbol_buy_min_interval_hint"))
        same_sym_hint.setStyleSheet("color: #888; font-size: 11px;")
        same_sym_hint.setWordWrap(True)
        form_amt.addRow("", same_sym_hint)
        group_amount.setLayout(form_amt)
        basic_layout.addWidget(group_amount)

        market_intro = QLabel(tr("risk_general_subtab_market_intro"))
        market_intro.setStyleSheet("color: #9ab; font-size: 11px; padding: 4px 2px;")
        market_intro.setWordWrap(True)
        basic_layout.addWidget(market_intro)
        group_currency = QGroupBox(
            tr("risk_display_currency").split(":")[0].strip()
            if ":" in tr("risk_display_currency")
            else tr("risk_display_currency")
        )
        currency_layout = QFormLayout()
        self.display_currency_combo = QComboBox()
        self.display_currency_combo.addItem(tr("risk_display_currency_usd"), "USD")
        self.display_currency_combo.addItem(tr("risk_display_currency_eur"), "EUR")
        self.display_currency_combo.currentIndexChanged.connect(self._set_dirty)
        currency_layout.addRow(tr("risk_display_currency"), self.display_currency_combo)
        self.currency_rate_eur_spin = QDoubleSpinBox()
        self.currency_rate_eur_spin.setRange(0.01, 2.0)
        self.currency_rate_eur_spin.setDecimals(4)
        self.currency_rate_eur_spin.setSingleStep(0.01)
        self.currency_rate_eur_spin.setSuffix(" €/USDT")
        currency_layout.addRow(tr("risk_currency_rate_eur"), self.currency_rate_eur_spin)
        group_currency.setLayout(currency_layout)
        basic_layout.addWidget(group_currency)

        scanner_form_w = QWidget()
        scanner_form = QFormLayout(scanner_form_w)
        _add_sub_form_section(scanner_form, "risk_section_market_scanner")
        self.market_scanner_pool_size_spin = QSpinBox()
        self.market_scanner_pool_size_spin.setRange(10, 200)
        self.market_scanner_pool_size_spin.setSingleStep(5)
        scanner_form.addRow(tr("risk_market_scanner_pool_size"), self.market_scanner_pool_size_spin)
        self.market_scanner_min_quote_volume_spin = QSpinBox()
        self.market_scanner_min_quote_volume_spin.setRange(100_000, 1_000_000_000)
        self.market_scanner_min_quote_volume_spin.setSingleStep(500_000)
        self.market_scanner_min_quote_volume_spin.setSuffix(" USDT")
        scanner_form.addRow(tr("risk_market_scanner_min_quote_volume"), self.market_scanner_min_quote_volume_spin)
        self.market_scanner_min_change_spin = QDoubleSpinBox()
        self.market_scanner_min_change_spin.setRange(-20.0, 20.0)
        self.market_scanner_min_change_spin.setDecimals(2)
        self.market_scanner_min_change_spin.setSingleStep(0.05)
        self.market_scanner_min_change_spin.setSuffix(" %")
        scanner_form.addRow(tr("risk_market_scanner_min_change_pct"), self.market_scanner_min_change_spin)
        self.market_scanner_min_range_spin = QDoubleSpinBox()
        self.market_scanner_min_range_spin.setRange(0.0, 30.0)
        self.market_scanner_min_range_spin.setDecimals(2)
        self.market_scanner_min_range_spin.setSingleStep(0.10)
        self.market_scanner_min_range_spin.setSuffix(" %")
        scanner_form.addRow(tr("risk_market_scanner_min_range_pct"), self.market_scanner_min_range_spin)
        self.market_scanner_aggressive_btn = QPushButton(tr("risk_market_scanner_aggressive_btn"))
        self.market_scanner_aggressive_btn.setToolTip(tr("risk_market_scanner_aggressive_hint"))
        self.market_scanner_aggressive_btn.clicked.connect(self._apply_market_scanner_aggressive_preset)
        scanner_form.addRow("", self.market_scanner_aggressive_btn)
        scanner_hint = QLabel(tr("risk_market_scanner_hint"))
        scanner_hint.setStyleSheet("color: #888; font-size: 11px;")
        scanner_hint.setWordWrap(True)
        scanner_form.addRow("", scanner_hint)
        adx_layers_note = QLabel(tr("risk_multi_adx_layers_note"))
        adx_layers_note.setStyleSheet("color: #888; font-size: 11px;")
        adx_layers_note.setWordWrap(True)
        scanner_form.addRow("", adx_layers_note)
        basic_layout.addWidget(scanner_form_w)

        update_group = QGroupBox(tr("risk_update_manifest_group"))
        update_form = QFormLayout()
        self.update_manifest_url_edit = QLineEdit()
        self.update_manifest_url_edit.setPlaceholderText("https://example.com/version.json")
        self.update_manifest_url_edit.setClearButtonEnabled(True)
        self.update_manifest_url_edit.setToolTip(tr("risk_update_manifest_hint"))
        update_form.addRow(tr("risk_update_manifest_url"), self.update_manifest_url_edit)
        update_hint = QLabel(tr("risk_update_manifest_hint"))
        update_hint.setStyleSheet("color: #888; font-size: 11px;")
        update_hint.setWordWrap(True)
        update_form.addRow("", update_hint)
        update_group.setLayout(update_form)
        basic_layout.addWidget(update_group)

        apply_hint_general = QLabel(tr("risk_apply_immediately"))
        apply_hint_general.setStyleSheet("color: #6a9; font-size: 11px; padding: 4px 0;")
        apply_hint_general.setWordWrap(True)
        basic_layout.addWidget(apply_hint_general)
        basic_layout.addStretch(1)
        self._general_sub_stack.addWidget(basic_page)

        ml_retrain_group = QGroupBox(tr("risk_ml_retrain_btn"))
        ml_retrain_layout = QVBoxLayout()
        self.ml_retrain_btn = QPushButton(tr("risk_ml_retrain_btn"))
        self.ml_retrain_btn.clicked.connect(self._on_ml_retrain)
        ml_retrain_layout.addWidget(self.ml_retrain_btn)
        self.trades_report_btn = QPushButton(tr("risk_trades_report_btn"))
        self.trades_report_btn.clicked.connect(self._on_trades_report)
        ml_retrain_layout.addWidget(self.trades_report_btn)
        ml_retrain_hint = QLabel(tr("risk_ml_retrain_hint"))
        ml_retrain_hint.setStyleSheet("color: #888; font-size: 11px;")
        ml_retrain_hint.setWordWrap(True)
        ml_retrain_layout.addWidget(ml_retrain_hint)
        trades_report_hint = QLabel(tr("risk_trades_report_hint"))
        trades_report_hint.setStyleSheet("color: #888; font-size: 11px;")
        trades_report_hint.setWordWrap(True)
        ml_retrain_layout.addWidget(trades_report_hint)
        ml_retrain_group.setLayout(ml_retrain_layout)

        buy_sell_page = QWidget()
        buy_sell_layout = QVBoxLayout(buy_sell_page)
        signal_intro = QLabel(tr("risk_general_subtab_signal_intro"))
        signal_intro.setStyleSheet("color: #9ab; font-size: 11px; padding: 4px 2px;")
        signal_intro.setWordWrap(True)
        buy_sell_layout.addWidget(signal_intro)
        buy_sell_layout.addWidget(ml_retrain_group)
        signal_form_w = QWidget()
        signal_form = QFormLayout(signal_form_w)
        _add_sub_form_section(signal_form, "risk_section_recommendation_filter")
        self.bot_confidence_spin = QSpinBox()
        self.bot_confidence_spin.setRange(30, 100)
        self.bot_confidence_spin.setSuffix(" %")
        signal_form.addRow(tr("risk_bot_confidence"), self.bot_confidence_spin)
        bot_conf_hint = QLabel(tr("risk_bot_confidence_hint"))
        bot_conf_hint.setStyleSheet("color: #888; font-size: 11px;")
        bot_conf_hint.setWordWrap(True)
        signal_form.addRow("", bot_conf_hint)

        _add_sub_form_section(signal_form, "risk_section_composite_ai")
        self.bot_merge_composite_check = QCheckBox()
        self.bot_merge_composite_check.setToolTip(tr("risk_bot_merge_composite_hint"))
        signal_form.addRow(tr("risk_bot_merge_composite"), self.bot_merge_composite_check)
        merge_comp_hint = QLabel(tr("risk_bot_merge_composite_hint"))
        merge_comp_hint.setStyleSheet("color: #888; font-size: 11px;")
        merge_comp_hint.setWordWrap(True)
        signal_form.addRow("", merge_comp_hint)
        self.chart_show_composite_badge_check = QCheckBox()
        self.chart_show_composite_badge_check.setToolTip(tr("risk_chart_show_composite_badge_hint"))
        signal_form.addRow(tr("risk_chart_show_composite_badge"), self.chart_show_composite_badge_check)
        chart_badge_hint = QLabel(tr("risk_chart_show_composite_badge_hint"))
        chart_badge_hint.setStyleSheet("color: #888; font-size: 11px;")
        chart_badge_hint.setWordWrap(True)
        signal_form.addRow("", chart_badge_hint)
        comp_stack_note = QLabel(tr("risk_composite_ml_stack_note"))
        comp_stack_note.setStyleSheet("color: #888; font-size: 11px;")
        comp_stack_note.setWordWrap(True)
        signal_form.addRow("", comp_stack_note)
        composite_thr_group = QGroupBox(tr("risk_composite_thresholds_group"))
        composite_thr_form = QFormLayout()
        self.composite_score_buy_spin = QDoubleSpinBox()
        self.composite_score_buy_spin.setRange(1.0, 60.0)
        self.composite_score_buy_spin.setDecimals(1)
        self.composite_score_buy_spin.setSingleStep(0.5)
        self.composite_score_buy_spin.setToolTip(tr("risk_composite_score_buy_hint"))
        composite_thr_form.addRow(tr("risk_composite_score_buy"), self.composite_score_buy_spin)
        self.composite_score_strong_spin = QDoubleSpinBox()
        self.composite_score_strong_spin.setRange(2.0, 90.0)
        self.composite_score_strong_spin.setDecimals(1)
        self.composite_score_strong_spin.setSingleStep(0.5)
        self.composite_score_strong_spin.setToolTip(tr("risk_composite_score_strong_hint"))
        composite_thr_form.addRow(tr("risk_composite_score_strong"), self.composite_score_strong_spin)
        self.composite_score_mid_spin = QDoubleSpinBox()
        self.composite_score_mid_spin.setRange(1.0, 90.0)
        self.composite_score_mid_spin.setDecimals(1)
        self.composite_score_mid_spin.setSingleStep(0.5)
        self.composite_score_mid_spin.setToolTip(tr("risk_composite_score_mid_hint"))
        composite_thr_form.addRow(tr("risk_composite_score_mid"), self.composite_score_mid_spin)
        self.composite_adx_for_di_spin = QDoubleSpinBox()
        self.composite_adx_for_di_spin.setRange(5.0, 40.0)
        self.composite_adx_for_di_spin.setDecimals(1)
        self.composite_adx_for_di_spin.setSingleStep(0.5)
        self.composite_adx_for_di_spin.setToolTip(tr("risk_composite_adx_for_di_hint"))
        composite_thr_form.addRow(tr("risk_composite_adx_for_di"), self.composite_adx_for_di_spin)
        composite_thr_hint = QLabel(tr("risk_composite_thresholds_hint"))
        composite_thr_hint.setStyleSheet("color: #888; font-size: 11px;")
        composite_thr_hint.setWordWrap(True)
        composite_thr_form.addRow("", composite_thr_hint)
        self.ai_promote_wait_composite_check = QCheckBox()
        self.ai_promote_wait_composite_check.setToolTip(tr("risk_ai_promote_wait_composite_hint"))
        composite_thr_form.addRow(tr("risk_ai_promote_wait_composite"), self.ai_promote_wait_composite_check)
        promote_wait_hint = QLabel(tr("risk_ai_promote_wait_composite_hint"))
        promote_wait_hint.setStyleSheet("color: #888; font-size: 11px;")
        promote_wait_hint.setWordWrap(True)
        composite_thr_form.addRow("", promote_wait_hint)
        self.ai_regime_router_check = QCheckBox()
        self.ai_regime_router_check.setToolTip(tr("risk_ai_regime_router_hint"))
        composite_thr_form.addRow(tr("risk_ai_regime_router"), self.ai_regime_router_check)
        regime_hint = QLabel(tr("risk_ai_regime_router_hint"))
        regime_hint.setStyleSheet("color: #888; font-size: 11px;")
        regime_hint.setWordWrap(True)
        composite_thr_form.addRow("", regime_hint)
        composite_thr_group.setLayout(composite_thr_form)
        signal_form.addRow(composite_thr_group)

        market_readout_group = QGroupBox(tr("risk_market_readout_thresholds_group"))
        market_readout_form = QFormLayout()
        mr_hint = QLabel(tr("risk_market_readout_thresholds_hint"))
        mr_hint.setStyleSheet("color: #888; font-size: 11px;")
        mr_hint.setWordWrap(True)
        market_readout_form.addRow("", mr_hint)
        self.mr_adx_strong_spin = QDoubleSpinBox()
        self.mr_adx_strong_spin.setRange(10.0, 60.0)
        self.mr_adx_strong_spin.setDecimals(1)
        self.mr_adx_strong_spin.setSingleStep(1.0)
        market_readout_form.addRow(tr("risk_market_readout_adx_strong"), self.mr_adx_strong_spin)
        self.mr_rsi_ob_spin = QDoubleSpinBox()
        self.mr_rsi_ob_spin.setRange(50.0, 95.0)
        self.mr_rsi_ob_spin.setDecimals(1)
        self.mr_rsi_ob_spin.setSingleStep(1.0)
        market_readout_form.addRow(tr("risk_market_readout_rsi_ob"), self.mr_rsi_ob_spin)
        self.mr_rsi_os_spin = QDoubleSpinBox()
        self.mr_rsi_os_spin.setRange(5.0, 50.0)
        self.mr_rsi_os_spin.setDecimals(1)
        self.mr_rsi_os_spin.setSingleStep(1.0)
        market_readout_form.addRow(tr("risk_market_readout_rsi_os"), self.mr_rsi_os_spin)
        self.mr_rsi_ctx_hi_spin = QDoubleSpinBox()
        self.mr_rsi_ctx_hi_spin.setRange(50.0, 80.0)
        self.mr_rsi_ctx_hi_spin.setDecimals(1)
        self.mr_rsi_ctx_hi_spin.setSingleStep(1.0)
        market_readout_form.addRow(tr("risk_market_readout_rsi_ctx_high"), self.mr_rsi_ctx_hi_spin)
        self.mr_rsi_ctx_lo_spin = QDoubleSpinBox()
        self.mr_rsi_ctx_lo_spin.setRange(20.0, 50.0)
        self.mr_rsi_ctx_lo_spin.setDecimals(1)
        self.mr_rsi_ctx_lo_spin.setSingleStep(1.0)
        market_readout_form.addRow(tr("risk_market_readout_rsi_ctx_low"), self.mr_rsi_ctx_lo_spin)
        self.mr_st_ob_spin = QDoubleSpinBox()
        self.mr_st_ob_spin.setRange(60.0, 95.0)
        self.mr_st_ob_spin.setDecimals(1)
        self.mr_st_ob_spin.setSingleStep(1.0)
        market_readout_form.addRow(tr("risk_market_readout_stoch_ob"), self.mr_st_ob_spin)
        self.mr_st_os_spin = QDoubleSpinBox()
        self.mr_st_os_spin.setRange(5.0, 40.0)
        self.mr_st_os_spin.setDecimals(1)
        self.mr_st_os_spin.setSingleStep(1.0)
        market_readout_form.addRow(tr("risk_market_readout_stoch_os"), self.mr_st_os_spin)
        self.mr_st_band_lo_spin = QDoubleSpinBox()
        self.mr_st_band_lo_spin.setRange(30.0, 50.0)
        self.mr_st_band_lo_spin.setDecimals(1)
        self.mr_st_band_lo_spin.setSingleStep(1.0)
        market_readout_form.addRow(tr("risk_market_readout_stoch_band_lo"), self.mr_st_band_lo_spin)
        self.mr_st_band_hi_spin = QDoubleSpinBox()
        self.mr_st_band_hi_spin.setRange(50.0, 70.0)
        self.mr_st_band_hi_spin.setDecimals(1)
        self.mr_st_band_hi_spin.setSingleStep(1.0)
        market_readout_form.addRow(tr("risk_market_readout_stoch_band_hi"), self.mr_st_band_hi_spin)
        self.mr_st_mid_lo_spin = QDoubleSpinBox()
        self.mr_st_mid_lo_spin.setRange(30.0, 55.0)
        self.mr_st_mid_lo_spin.setDecimals(1)
        self.mr_st_mid_lo_spin.setSingleStep(1.0)
        market_readout_form.addRow(tr("risk_market_readout_stoch_mid_lo"), self.mr_st_mid_lo_spin)
        self.mr_st_mid_hi_spin = QDoubleSpinBox()
        self.mr_st_mid_hi_spin.setRange(45.0, 85.0)
        self.mr_st_mid_hi_spin.setDecimals(1)
        self.mr_st_mid_hi_spin.setSingleStep(1.0)
        market_readout_form.addRow(tr("risk_market_readout_stoch_mid_hi"), self.mr_st_mid_hi_spin)
        self.mr_st_kd_eps_spin = QDoubleSpinBox()
        self.mr_st_kd_eps_spin.setRange(0.05, 2.0)
        self.mr_st_kd_eps_spin.setDecimals(2)
        self.mr_st_kd_eps_spin.setSingleStep(0.05)
        market_readout_form.addRow(tr("risk_market_readout_stoch_kd_eps"), self.mr_st_kd_eps_spin)
        self.mr_st_k_bull_spin = QDoubleSpinBox()
        self.mr_st_k_bull_spin.setRange(50.0, 75.0)
        self.mr_st_k_bull_spin.setDecimals(1)
        self.mr_st_k_bull_spin.setSingleStep(1.0)
        market_readout_form.addRow(tr("risk_market_readout_stoch_k_bull"), self.mr_st_k_bull_spin)
        self.mr_st_k_bear_spin = QDoubleSpinBox()
        self.mr_st_k_bear_spin.setRange(25.0, 50.0)
        self.mr_st_k_bear_spin.setDecimals(1)
        self.mr_st_k_bear_spin.setSingleStep(1.0)
        market_readout_form.addRow(tr("risk_market_readout_stoch_k_bear"), self.mr_st_k_bear_spin)
        self.mr_atr_hi_spin = QDoubleSpinBox()
        self.mr_atr_hi_spin.setRange(0.1, 5.0)
        self.mr_atr_hi_spin.setDecimals(2)
        self.mr_atr_hi_spin.setSingleStep(0.1)
        market_readout_form.addRow(tr("risk_market_readout_atr_hi_pct"), self.mr_atr_hi_spin)
        self.mr_st_near_spin = QDoubleSpinBox()
        self.mr_st_near_spin.setRange(0.0005, 0.02)
        self.mr_st_near_spin.setDecimals(4)
        self.mr_st_near_spin.setSingleStep(0.0005)
        market_readout_form.addRow(tr("risk_market_readout_st_near_ratio"), self.mr_st_near_spin)
        market_readout_group.setLayout(market_readout_form)
        signal_form.addRow(market_readout_group)

        _add_sub_form_section(signal_form, "risk_section_entry_filters")
        self.bot_buy_bounce_15m_check = QCheckBox()
        self.bot_buy_bounce_15m_check.setToolTip(tr("risk_bot_buy_bounce_15m_hint"))
        signal_form.addRow(tr("risk_bot_buy_bounce_15m"), self.bot_buy_bounce_15m_check)
        self.bot_live_auto_tune_bounce_check = QCheckBox()
        self.bot_live_auto_tune_bounce_check.setToolTip(tr("risk_bot_live_auto_tune_bounce_hint"))
        signal_form.addRow(tr("risk_bot_live_auto_tune_bounce"), self.bot_live_auto_tune_bounce_check)
        self.bot_buy_bounce_rsi_check = QCheckBox()
        self.bot_buy_bounce_rsi_check.setToolTip(tr("risk_bot_buy_bounce_rsi_hint"))
        signal_form.addRow(tr("risk_bot_buy_bounce_rsi"), self.bot_buy_bounce_rsi_check)
        self.bot_buy_bounce_rsi_max_spin = QDoubleSpinBox()
        self.bot_buy_bounce_rsi_max_spin.setRange(20.0, 80.0)
        self.bot_buy_bounce_rsi_max_spin.setDecimals(1)
        self.bot_buy_bounce_rsi_max_spin.setSingleStep(0.5)
        signal_form.addRow(tr("risk_bot_buy_bounce_rsi_max"), self.bot_buy_bounce_rsi_max_spin)
        self.bot_buy_bounce_vwap_check = QCheckBox()
        self.bot_buy_bounce_vwap_check.setToolTip(tr("risk_bot_buy_bounce_vwap_hint"))
        signal_form.addRow(tr("risk_bot_buy_bounce_vwap"), self.bot_buy_bounce_vwap_check)
        # يُحفظ في الكونفيج كمعامل (مثلاً 1.024)؛ العرض للمستخدم كنسبة % فوق VWAP
        self.bot_buy_bounce_vwap_pct_spin = QDoubleSpinBox()
        self.bot_buy_bounce_vwap_pct_spin.setRange(-2.0, 5.0)
        self.bot_buy_bounce_vwap_pct_spin.setDecimals(2)
        self.bot_buy_bounce_vwap_pct_spin.setSingleStep(0.05)
        self.bot_buy_bounce_vwap_pct_spin.setSuffix(" %")
        self.bot_buy_bounce_vwap_pct_spin.setToolTip(tr("risk_bot_buy_bounce_vwap_ratio_hint"))
        signal_form.addRow(tr("risk_bot_buy_bounce_vwap_ratio"), self.bot_buy_bounce_vwap_pct_spin)
        self.bot_buy_bounce_stoch_check = QCheckBox()
        self.bot_buy_bounce_stoch_check.setToolTip(tr("risk_bot_buy_bounce_stoch_hint"))
        signal_form.addRow(tr("risk_bot_buy_bounce_stoch"), self.bot_buy_bounce_stoch_check)
        self.bot_buy_bounce_stoch_max_spin = QDoubleSpinBox()
        self.bot_buy_bounce_stoch_max_spin.setRange(20.0, 95.0)
        self.bot_buy_bounce_stoch_max_spin.setDecimals(1)
        self.bot_buy_bounce_stoch_max_spin.setSingleStep(0.5)
        signal_form.addRow(tr("risk_bot_buy_bounce_stoch_max"), self.bot_buy_bounce_stoch_max_spin)
        self.bot_buy_bounce_adx_check = QCheckBox()
        self.bot_buy_bounce_adx_check.setToolTip(tr("risk_bot_buy_bounce_adx_hint"))
        signal_form.addRow(tr("risk_bot_buy_bounce_adx"), self.bot_buy_bounce_adx_check)
        self.bot_buy_bounce_adx_min_spin = QDoubleSpinBox()
        self.bot_buy_bounce_adx_min_spin.setRange(5.0, 40.0)
        self.bot_buy_bounce_adx_min_spin.setDecimals(1)
        self.bot_buy_bounce_adx_min_spin.setSingleStep(0.5)
        signal_form.addRow(tr("risk_bot_buy_bounce_adx_min"), self.bot_buy_bounce_adx_min_spin)
        self.bot_buy_bounce_macd_check = QCheckBox()
        self.bot_buy_bounce_macd_check.setToolTip(tr("risk_bot_buy_bounce_macd_hint"))
        signal_form.addRow(tr("risk_bot_buy_bounce_macd"), self.bot_buy_bounce_macd_check)
        self.bot_buy_bounce_macd_min_spin = QDoubleSpinBox()
        self.bot_buy_bounce_macd_min_spin.setRange(-0.20, 0.20)
        self.bot_buy_bounce_macd_min_spin.setDecimals(4)
        self.bot_buy_bounce_macd_min_spin.setSingleStep(0.0025)
        signal_form.addRow(tr("risk_bot_buy_bounce_macd_min"), self.bot_buy_bounce_macd_min_spin)
        bounce_15m_hint = QLabel(tr("risk_bot_buy_bounce_15m_hint"))
        bounce_15m_hint.setStyleSheet("color: #888; font-size: 11px;")
        bounce_15m_hint.setWordWrap(True)
        signal_form.addRow("", bounce_15m_hint)
        self.bot_buy_bounce_15m_check.stateChanged.connect(self._sync_bounce_detail_widgets_enabled)

        _add_sub_form_section(signal_form, "risk_section_ev_ml")
        self.bot_ev_gate_enabled_check = QCheckBox()
        self.bot_ev_gate_enabled_check.setToolTip(tr("risk_bot_ev_gate_enabled_hint"))
        signal_form.addRow(tr("risk_bot_ev_gate_enabled"), self.bot_ev_gate_enabled_check)
        ev_gate_hint = QLabel(tr("risk_bot_ev_gate_enabled_hint"))
        ev_gate_hint.setStyleSheet("color: #888; font-size: 11px;")
        ev_gate_hint.setWordWrap(True)
        signal_form.addRow("", ev_gate_hint)
        self.bot_ev_min_pct_spin = QDoubleSpinBox()
        self.bot_ev_min_pct_spin.setRange(-1.0, 5.0)
        self.bot_ev_min_pct_spin.setDecimals(3)
        self.bot_ev_min_pct_spin.setSingleStep(0.01)
        self.bot_ev_min_pct_spin.setSuffix(" %")
        signal_form.addRow(tr("risk_bot_ev_min_pct"), self.bot_ev_min_pct_spin)
        ev_min_hint = QLabel(tr("risk_bot_ev_min_pct_hint"))
        ev_min_hint.setStyleSheet("color: #888; font-size: 11px;")
        ev_min_hint.setWordWrap(True)
        signal_form.addRow("", ev_min_hint)
        ev_regime_group = QGroupBox(tr("risk_bot_ev_min_pct_regime_group"))
        ev_regime_form = QFormLayout()
        self.bot_ev_min_trend_up_spin = QDoubleSpinBox()
        self.bot_ev_min_trend_up_spin.setRange(-1.0, 5.0)
        self.bot_ev_min_trend_up_spin.setDecimals(3)
        self.bot_ev_min_trend_up_spin.setSingleStep(0.01)
        self.bot_ev_min_trend_up_spin.setSuffix(" %")
        ev_regime_form.addRow(tr("risk_bot_ev_min_pct_trend_up"), self.bot_ev_min_trend_up_spin)
        self.bot_ev_min_trend_down_spin = QDoubleSpinBox()
        self.bot_ev_min_trend_down_spin.setRange(-1.0, 5.0)
        self.bot_ev_min_trend_down_spin.setDecimals(3)
        self.bot_ev_min_trend_down_spin.setSingleStep(0.01)
        self.bot_ev_min_trend_down_spin.setSuffix(" %")
        ev_regime_form.addRow(tr("risk_bot_ev_min_pct_trend_down"), self.bot_ev_min_trend_down_spin)
        self.bot_ev_min_range_spin = QDoubleSpinBox()
        self.bot_ev_min_range_spin.setRange(-1.0, 5.0)
        self.bot_ev_min_range_spin.setDecimals(3)
        self.bot_ev_min_range_spin.setSingleStep(0.01)
        self.bot_ev_min_range_spin.setSuffix(" %")
        ev_regime_form.addRow(tr("risk_bot_ev_min_pct_range"), self.bot_ev_min_range_spin)
        self.bot_ev_min_volatile_spin = QDoubleSpinBox()
        self.bot_ev_min_volatile_spin.setRange(-1.0, 5.0)
        self.bot_ev_min_volatile_spin.setDecimals(3)
        self.bot_ev_min_volatile_spin.setSingleStep(0.01)
        self.bot_ev_min_volatile_spin.setSuffix(" %")
        ev_regime_form.addRow(tr("risk_bot_ev_min_pct_volatile"), self.bot_ev_min_volatile_spin)
        ev_regime_group.setLayout(ev_regime_form)
        signal_form.addRow(ev_regime_group)
        wf_group = QGroupBox(tr("risk_ml_wf_group"))
        wf_form = QFormLayout()
        self.ml_wf_cost_spin = QDoubleSpinBox()
        self.ml_wf_cost_spin.setRange(0.0, 2.0)
        self.ml_wf_cost_spin.setDecimals(3)
        self.ml_wf_cost_spin.setSingleStep(0.01)
        self.ml_wf_cost_spin.setSuffix(" %")
        wf_form.addRow(tr("risk_ml_wf_cost_per_trade_pct"), self.ml_wf_cost_spin)
        self.ml_wf_train_min_spin = QSpinBox()
        self.ml_wf_train_min_spin.setRange(20, 1000)
        wf_form.addRow(tr("risk_ml_wf_train_min"), self.ml_wf_train_min_spin)
        self.ml_wf_test_window_spin = QSpinBox()
        self.ml_wf_test_window_spin.setRange(5, 300)
        wf_form.addRow(tr("risk_ml_wf_test_window"), self.ml_wf_test_window_spin)
        wf_group.setLayout(wf_form)
        signal_form.addRow(wf_group)

        buy_sell_layout.addWidget(signal_form_w)

        exit_intro = QLabel(tr("risk_general_subtab_exit_intro"))
        exit_intro.setStyleSheet("color: #9ab; font-size: 11px; padding: 4px 2px;")
        exit_intro.setWordWrap(True)
        buy_sell_layout.addWidget(exit_intro)
        exit_form_w = QWidget()
        exit_form = QFormLayout(exit_form_w)

        _add_sub_form_section(exit_form, "risk_section_auto_sell")
        self.bot_auto_sell_check = QCheckBox()
        self.bot_auto_sell_check.setToolTip(tr("risk_bot_auto_sell_hint"))
        exit_form.addRow(tr("risk_bot_auto_sell"), self.bot_auto_sell_check)
        self.bot_auto_sell_requires_robot_check = QCheckBox()
        self.bot_auto_sell_requires_robot_check.setToolTip(tr("risk_bot_auto_sell_requires_robot_hint"))
        exit_form.addRow(tr("risk_bot_auto_sell_requires_robot"), self.bot_auto_sell_requires_robot_check)
        auto_sell_hint = QLabel(tr("risk_bot_auto_sell_hint"))
        auto_sell_hint.setStyleSheet("color: #888; font-size: 11px;")
        auto_sell_hint.setWordWrap(True)
        exit_form.addRow("", auto_sell_hint)
        self.bot_block_ai_sell_while_losing_check = QCheckBox()
        self.bot_block_ai_sell_while_losing_check.setToolTip(tr("risk_bot_block_ai_sell_while_losing_hint"))
        exit_form.addRow(tr("risk_bot_block_ai_sell_while_losing"), self.bot_block_ai_sell_while_losing_check)
        block_ai_loss_hint = QLabel(tr("risk_bot_block_ai_sell_while_losing_hint"))
        block_ai_loss_hint.setStyleSheet("color: #888; font-size: 11px;")
        block_ai_loss_hint.setWordWrap(True)
        exit_form.addRow("", block_ai_loss_hint)

        _add_sub_form_section(exit_form, "risk_section_indicator_thresholds")
        ind_thr_intro = QLabel(tr("risk_section_indicator_thresholds_hint"))
        ind_thr_intro.setStyleSheet("color: #888; font-size: 11px;")
        ind_thr_intro.setWordWrap(True)
        exit_form.addRow("", ind_thr_intro)
        buy_ptr = QLabel(tr("risk_indicator_thresholds_buy_pointer"))
        buy_ptr.setStyleSheet("color: #7a9; font-size: 11px;")
        buy_ptr.setWordWrap(True)
        exit_form.addRow("", buy_ptr)
        auto_sell_ind_form = QFormLayout()
        self.sell_overbought_rsi_spin = QDoubleSpinBox()
        self.sell_overbought_rsi_spin.setRange(50.0, 95.0)
        self.sell_overbought_rsi_spin.setDecimals(1)
        self.sell_overbought_rsi_spin.setSingleStep(1.0)
        self.sell_overbought_rsi_spin.setToolTip(tr("risk_sell_overbought_rsi_hint"))
        auto_sell_ind_form.addRow(tr("risk_sell_overbought_rsi_min"), self.sell_overbought_rsi_spin)
        self.sell_overbought_min_profit_spin = QDoubleSpinBox()
        self.sell_overbought_min_profit_spin.setRange(0.0, 20.0)
        self.sell_overbought_min_profit_spin.setDecimals(2)
        self.sell_overbought_min_profit_spin.setSingleStep(0.05)
        self.sell_overbought_min_profit_spin.setSuffix(" %")
        self.sell_overbought_min_profit_spin.setToolTip(tr("risk_sell_overbought_min_profit_hint"))
        auto_sell_ind_form.addRow(tr("risk_sell_overbought_min_profit"), self.sell_overbought_min_profit_spin)
        self.sell_peak_rsi_spin = QDoubleSpinBox()
        self.sell_peak_rsi_spin.setMinimum(0.0)
        self.sell_peak_rsi_spin.setMaximum(95.0)
        self.sell_peak_rsi_spin.setDecimals(1)
        self.sell_peak_rsi_spin.setSingleStep(1.0)
        self.sell_peak_rsi_spin.setSpecialValueText(tr("risk_peak_rsi_disabled"))
        self.sell_peak_rsi_spin.setToolTip(tr("risk_sell_peak_rsi_hint"))
        auto_sell_ind_form.addRow(tr("risk_sell_peak_rsi_min"), self.sell_peak_rsi_spin)
        self.sell_peak_min_profit_spin = QDoubleSpinBox()
        self.sell_peak_min_profit_spin.setRange(0.0, 20.0)
        self.sell_peak_min_profit_spin.setDecimals(2)
        self.sell_peak_min_profit_spin.setSingleStep(0.05)
        self.sell_peak_min_profit_spin.setSuffix(" %")
        self.sell_peak_min_profit_spin.setToolTip(tr("risk_sell_peak_min_profit_hint"))
        auto_sell_ind_form.addRow(tr("risk_sell_peak_min_profit"), self.sell_peak_min_profit_spin)
        self.sell_overbought_limit_buy_buffer_spin = QDoubleSpinBox()
        self.sell_overbought_limit_buy_buffer_spin.setMinimum(0.0)
        self.sell_overbought_limit_buy_buffer_spin.setMaximum(25.0)
        self.sell_overbought_limit_buy_buffer_spin.setDecimals(1)
        self.sell_overbought_limit_buy_buffer_spin.setSingleStep(0.5)
        self.sell_overbought_limit_buy_buffer_spin.setSpecialValueText(tr("risk_buffer_disabled"))
        self.sell_overbought_limit_buy_buffer_spin.setToolTip(tr("risk_sell_overbought_limit_buy_buffer_hint"))
        auto_sell_ind_form.addRow(tr("risk_sell_overbought_limit_buy_buffer"), self.sell_overbought_limit_buy_buffer_spin)
        auto_sell_ind_wrap = QWidget()
        auto_sell_ind_wrap.setLayout(auto_sell_ind_form)
        exit_form.addRow(auto_sell_ind_wrap)
        ind_thr_sell_paths = QLabel(tr("risk_indicator_thresholds_sell_path_note"))
        ind_thr_sell_paths.setStyleSheet("color: #c9a227; font-size: 11px;")
        ind_thr_sell_paths.setWordWrap(True)
        exit_form.addRow("", ind_thr_sell_paths)

        _add_sub_form_section(exit_form, "risk_section_sell_barriers")
        self.limit_sell_blocks_signal_check = QCheckBox()
        self.limit_sell_blocks_signal_check.setToolTip(tr("risk_limit_sell_blocks_signal_hint"))
        exit_form.addRow(tr("risk_limit_sell_blocks_signal"), self.limit_sell_blocks_signal_check)
        limit_block_hint = QLabel(tr("risk_limit_sell_blocks_signal_hint"))
        limit_block_hint.setStyleSheet("color: #888; font-size: 11px;")
        limit_block_hint.setWordWrap(True)
        exit_form.addRow("", limit_block_hint)
        self.bot_signal_sell_bypass_tp_barrier_check = QCheckBox()
        self.bot_signal_sell_bypass_tp_barrier_check.setToolTip(
            tr("risk_bot_signal_sell_bypass_tp_barrier_hint")
        )
        exit_form.addRow(
            tr("risk_bot_signal_sell_bypass_tp_barrier"),
            self.bot_signal_sell_bypass_tp_barrier_check,
        )
        bypass_tp_hint = QLabel(tr("risk_bot_signal_sell_bypass_tp_barrier_hint"))
        bypass_tp_hint.setStyleSheet("color: #888; font-size: 11px;")
        bypass_tp_hint.setWordWrap(True)
        exit_form.addRow("", bypass_tp_hint)
        self.bot_trailing_bypass_tp_barrier_check = QCheckBox()
        self.bot_trailing_bypass_tp_barrier_check.setToolTip(
            tr("risk_bot_trailing_bypass_tp_barrier_hint")
        )
        exit_form.addRow(
            tr("risk_bot_trailing_bypass_tp_barrier"),
            self.bot_trailing_bypass_tp_barrier_check,
        )
        trailing_bypass_hint = QLabel(tr("risk_bot_trailing_bypass_tp_barrier_hint"))
        trailing_bypass_hint.setStyleSheet("color: #888; font-size: 11px;")
        trailing_bypass_hint.setWordWrap(True)
        exit_form.addRow("", trailing_bypass_hint)
        self.bot_auto_sl_check = QCheckBox()
        self.bot_auto_sl_check.setToolTip(tr("risk_bot_auto_sl_hint"))
        exit_form.addRow(tr("risk_bot_auto_sl"), self.bot_auto_sl_check)
        auto_sl_hint = QLabel(tr("risk_bot_auto_sl_hint"))
        auto_sl_hint.setStyleSheet("color: #888; font-size: 11px;")
        auto_sl_hint.setWordWrap(True)
        exit_form.addRow("", auto_sl_hint)
        self.bot_apply_execution_filters_check = QCheckBox()
        self.bot_apply_execution_filters_check.setToolTip(tr("risk_bot_apply_execution_filters_hint"))
        exit_form.addRow(tr("risk_bot_apply_execution_filters"), self.bot_apply_execution_filters_check)
        exec_filters_hint = QLabel(tr("risk_bot_apply_execution_filters_hint"))
        exec_filters_hint.setStyleSheet("color: #888; font-size: 11px;")
        exec_filters_hint.setWordWrap(True)
        exit_form.addRow("", exec_filters_hint)

        _add_sub_form_section(exit_form, "risk_section_position_management")
        self.ml_weight_spin = QSpinBox()
        self.ml_weight_spin.setRange(0, 100)
        self.ml_weight_spin.setSuffix(" %")
        exit_form.addRow(tr("risk_ml_weight"), self.ml_weight_spin)
        ml_weight_hint = QLabel(tr("risk_ml_weight_hint"))
        ml_weight_hint.setStyleSheet("color: #888; font-size: 11px;")
        ml_weight_hint.setWordWrap(True)
        exit_form.addRow("", ml_weight_hint)

        # إعدادات التتبّع (Trailing) — نسبة التتبع > 0، وأدنى ربح يمكن تعطيله بـ 0%
        self.trailing_stop_spin = QDoubleSpinBox()
        self.trailing_stop_spin.setRange(0.1, 100.0)
        self.trailing_stop_spin.setSingleStep(0.1)
        self.trailing_stop_spin.setDecimals(1)
        self.trailing_stop_spin.setSuffix(" %")
        self.trailing_stop_spin.setToolTip("0.1 – 100 % (خطوة 0.1)" if get_language() == "ar" else "0.1 – 100 % (step 0.1)")
        exit_form.addRow(tr("risk_trailing_stop"), self.trailing_stop_spin)

        self.trailing_min_profit_spin = QDoubleSpinBox()
        self.trailing_min_profit_spin.setRange(0.0, 100.0)
        self.trailing_min_profit_spin.setSingleStep(0.1)
        self.trailing_min_profit_spin.setDecimals(1)
        self.trailing_min_profit_spin.setSuffix(" %")
        self.trailing_min_profit_spin.setToolTip("0 – 100 % (خطوة 0.1)" if get_language() == "ar" else "0 – 100 % (step 0.1)")
        exit_form.addRow(tr("risk_trailing_min_profit"), self.trailing_min_profit_spin)

        # إعدادات أوامر الأمان (DCA)
        self.dca_count_spin = QSpinBox()
        self.dca_count_spin.setRange(1, 4)
        exit_form.addRow(tr("risk_dca_count"), self.dca_count_spin)
        lbl_dca_count = QLabel(tr("risk_dca_count_hint"))
        lbl_dca_count.setStyleSheet("color: #888; font-size: 11px;")
        lbl_dca_count.setWordWrap(True)
        exit_form.addRow("", lbl_dca_count)

        self.dca_step_spin = QSpinBox()
        self.dca_step_spin.setRange(1, 5)
        self.dca_step_spin.setSuffix(" %")
        exit_form.addRow(tr("risk_dca_step"), self.dca_step_spin)
        lbl_dca_step = QLabel(tr("risk_dca_step_hint"))
        lbl_dca_step.setStyleSheet("color: #888; font-size: 11px;")
        lbl_dca_step.setWordWrap(True)
        exit_form.addRow("", lbl_dca_step)

        self.dca_volume_spin = QDoubleSpinBox()
        self.dca_volume_spin.setRange(0.5, 3.0)
        self.dca_volume_spin.setSingleStep(0.1)
        self.dca_volume_spin.setDecimals(2)
        exit_form.addRow(tr("risk_dca_volume"), self.dca_volume_spin)
        lbl_dca_volume = QLabel(tr("risk_dca_volume_hint"))
        lbl_dca_volume.setStyleSheet("color: #888; font-size: 11px;")
        lbl_dca_volume.setWordWrap(True)
        exit_form.addRow("", lbl_dca_volume)

        buy_sell_layout.addWidget(exit_form_w)
        buy_sell_layout.addStretch(1)
        self._general_sub_stack.addWidget(buy_sell_page)

        general_layout.addWidget(self.general_sub_frame)

        # تنبيهات تيليجرام — آخر قسم في الإعدادات العامة (أسفل القائمة)
        telegram_group = QGroupBox(tr("risk_telegram_group"))
        telegram_form = QFormLayout()
        three_terms_btn = QPushButton(tr("telegram_three_terms_btn"))
        three_terms_btn.setStyleSheet("font-size: 11px; color: #88aacc;")
        three_terms_btn.setToolTip(tr("telegram_three_terms_title"))
        three_terms_btn.clicked.connect(self._show_three_terms_help)
        telegram_form.addRow("", three_terms_btn)
        self.telegram_enable_check = QCheckBox()
        telegram_form.addRow(tr("risk_telegram_enable"), self.telegram_enable_check)
        self.telegram_token_edit = QLineEdit()
        self.telegram_token_edit.setEchoMode(QLineEdit.EchoMode.Normal)
        self.telegram_token_edit.setToolTip(tr("risk_telegram_token_tooltip"))
        self.telegram_token_edit.setReadOnly(False)
        self.telegram_token_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.telegram_token_edit.setMinimumWidth(280)
        telegram_form.addRow(tr("risk_telegram_token"), self.telegram_token_edit)
        self.telegram_bot_username_edit = QLineEdit()
        self.telegram_bot_username_edit.setPlaceholderText(tr("risk_telegram_bot_username_placeholder"))
        self.telegram_bot_username_edit.setToolTip(tr("risk_telegram_bot_username_tooltip"))
        self.telegram_bot_username_edit.setReadOnly(False)
        self.telegram_bot_username_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        telegram_form.addRow(tr("risk_telegram_bot_username"), self.telegram_bot_username_edit)
        self.telegram_chat_id_edit = QLineEdit()
        self.telegram_chat_id_edit.setToolTip(tr("risk_telegram_chat_id_tooltip"))
        self.telegram_chat_id_edit.setReadOnly(False)
        self.telegram_chat_id_edit.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.telegram_chat_id_edit.setMinimumWidth(200)
        chat_id_help_btn = QPushButton(tr("telegram_chat_id_help_btn"))
        chat_id_help_btn.setStyleSheet("font-size: 11px; color: #88aacc;")
        chat_id_help_btn.clicked.connect(self._show_chat_id_help)
        chat_id_row = QHBoxLayout()
        chat_id_row.addWidget(self.telegram_chat_id_edit)
        chat_id_row.addWidget(chat_id_help_btn)
        telegram_form.addRow(tr("risk_telegram_chat_id"), chat_id_row)
        self.telegram_test_btn = QPushButton(tr("telegram_test_btn"))
        self.telegram_test_btn.setToolTip(tr("telegram_test_btn_tooltip"))
        self.telegram_test_btn.clicked.connect(self._send_telegram_test)
        telegram_form.addRow("", self.telegram_test_btn)
        telegram_group.setLayout(telegram_form)
        general_layout.addWidget(telegram_group)

        general_layout.addStretch(1)

        # صفحة إعدادات البوت الخاصة — شروط الشراء/البيع + شرح الفلاتر
        adv_page = QWidget()
        adv_page_layout = QVBoxLayout(adv_page)
        adv_hint = QLabel(tr("risk_advanced_hint"))
        adv_hint.setStyleSheet("color: #6a9; font-size: 11px; padding: 6px;")
        adv_hint.setWordWrap(True)
        adv_page_layout.addWidget(adv_hint)
        adv_sections = QLabel(tr("risk_advanced_sections"))
        adv_sections.setStyleSheet("color: #aaa; font-size: 11px; padding: 4px 6px; font-weight: bold;")
        adv_sections.setWordWrap(True)
        adv_page_layout.addWidget(adv_sections)
        adv_affect = QLabel(tr("risk_advanced_affect"))
        adv_affect.setStyleSheet("color: #8a8; font-size: 10px; padding: 2px 6px;")
        adv_affect.setWordWrap(True)
        adv_page_layout.addWidget(adv_affect)

        buy_cond_group = QGroupBox(tr("risk_buy_conditions_list"))
        buy_cond_layout = QVBoxLayout()
        buy_btn_row = QHBoxLayout()
        self.buy_condition_combo = QComboBox()
        self.buy_condition_combo.setMinimumWidth(200)
        for cid, _ in self._buy_condition_options():
            self.buy_condition_combo.addItem(tr(f"risk_cond_buy_{cid}"), cid)
        buy_btn_row.addWidget(self.buy_condition_combo, 1)
        add_buy_btn = QPushButton(tr("risk_cond_add"))
        add_buy_btn.setMinimumWidth(72)
        add_buy_btn.setToolTip(tr("risk_cond_add"))
        add_buy_btn.clicked.connect(self._on_add_buy_condition)
        buy_btn_row.addWidget(add_buy_btn)
        rem_buy_btn = QPushButton(tr("risk_cond_remove"))
        rem_buy_btn.setMinimumWidth(72)
        rem_buy_btn.setToolTip(tr("risk_cond_remove"))
        rem_buy_btn.clicked.connect(lambda: self._remove_selected(self.buy_conditions_list))
        buy_btn_row.addWidget(rem_buy_btn)
        buy_cond_layout.addLayout(buy_btn_row)
        self.buy_conditions_list = QListWidget()
        self.buy_conditions_list.setMinimumHeight(100)
        self.buy_conditions_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        buy_cond_layout.addWidget(self.buy_conditions_list)
        buy_cond_paths = QLabel(tr("risk_buy_conditions_paths_note"))
        buy_cond_paths.setStyleSheet("color: #c9a227; font-size: 11px;")
        buy_cond_paths.setWordWrap(True)
        buy_cond_layout.addWidget(buy_cond_paths)
        buy_cond_group.setLayout(buy_cond_layout)
        adv_page_layout.addWidget(buy_cond_group)

        sell_cond_group = QGroupBox(tr("risk_sell_conditions_list"))
        sell_cond_group.setToolTip(tr("risk_sell_conditions_list_hint"))
        sell_cond_layout = QVBoxLayout()
        sell_btn_row = QHBoxLayout()
        self.sell_condition_combo = QComboBox()
        self.sell_condition_combo.setMinimumWidth(200)
        for cid, _ in self._sell_condition_options():
            self.sell_condition_combo.addItem(tr(f"risk_cond_sell_{cid}"), cid)
        sell_btn_row.addWidget(self.sell_condition_combo, 1)
        add_sell_btn = QPushButton(tr("risk_cond_add"))
        add_sell_btn.setMinimumWidth(72)
        add_sell_btn.setToolTip(tr("risk_cond_add"))
        add_sell_btn.clicked.connect(self._on_add_sell_condition)
        sell_btn_row.addWidget(add_sell_btn)
        rem_sell_btn = QPushButton(tr("risk_cond_remove"))
        rem_sell_btn.setMinimumWidth(72)
        rem_sell_btn.setToolTip(tr("risk_cond_remove"))
        rem_sell_btn.clicked.connect(lambda: self._remove_selected(self.sell_conditions_list))
        sell_btn_row.addWidget(rem_sell_btn)
        sell_cond_layout.addLayout(sell_btn_row)
        self.sell_conditions_list = QListWidget()
        self.sell_conditions_list.setMinimumHeight(100)
        self.sell_conditions_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        sell_cond_layout.addWidget(self.sell_conditions_list)
        sell_cond_paths = QLabel(tr("risk_sell_conditions_paths_note"))
        sell_cond_paths.setStyleSheet("color: #c9a227; font-size: 11px;")
        sell_cond_paths.setWordWrap(True)
        sell_cond_layout.addWidget(sell_cond_paths)
        sell_cond_hint2 = QLabel(tr("risk_sell_conditions_overbought_moved_hint"))
        sell_cond_hint2.setStyleSheet("color: #888; font-size: 11px;")
        sell_cond_hint2.setWordWrap(True)
        sell_cond_layout.addWidget(sell_cond_hint2)
        sell_cond_group.setLayout(sell_cond_layout)
        adv_page_layout.addWidget(sell_cond_group)

        adv_page_layout.addStretch(1)

        # ترتيب الصفحات: 0=عامة، 1=متقدمة (بوت خاص)، 2=دليل
        self._stack.addWidget(general_page)
        self._stack.addWidget(adv_page)
        self._stack.addWidget(guide_page)
        layout.addWidget(self.main_sections_frame)

        scroll.setWidget(content)
        outer_layout.addWidget(scroll)

        btn_row = QHBoxLayout()
        export_btn = QPushButton(tr("risk_export_config"))
        export_btn.clicked.connect(self._export_config)
        import_btn = QPushButton(tr("risk_import_config"))
        import_btn.clicked.connect(self._import_config)
        btn_row.addWidget(export_btn)
        btn_row.addWidget(import_btn)
        btn_row.addStretch()
        save_btn = QPushButton(tr("risk_save"))
        save_btn.clicked.connect(self._save)
        close_btn = QPushButton(tr("risk_close"))
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(close_btn)
        outer_layout.addLayout(btn_row)

        self._loading_strategy = False
        self._load()
        self._connect_dirty()

    def _set_main_risk_section(self, idx: int) -> None:
        self._stack.setCurrentIndex(idx)
        self.btn_section_general.setChecked(idx == 0)
        self.btn_section_advanced.setChecked(idx == 1)
        self.btn_section_guide.setChecked(idx == 2)

    def _set_general_subtab(self, idx: int) -> None:
        self._general_sub_stack.setCurrentIndex(idx)
        b = self._general_sub_btn_group.button(idx)
        if b is not None:
            b.setChecked(True)

    def _buy_condition_options(self):
        """(id, translation_key) للخيارات المتاحة لشروط الشراء."""
        return [
            ("at_support", "risk_cond_buy_at_support"),
            ("no_buy_at_peak", "risk_cond_buy_no_buy_at_peak"),
            ("below_vwap", "risk_cond_buy_below_vwap"),
            ("no_buy_at_r1", "risk_cond_buy_no_buy_at_r1"),
        ]

    def _sell_condition_options(self):
        """(id, translation_key) للخيارات المتاحة لشروط البيع."""
        return [
            ("sell_at_overbought", "risk_cond_sell_sell_at_overbought"),
            ("take_profit", "risk_cond_sell_take_profit"),
            ("sell_at_peak", "risk_cond_sell_sell_at_peak"),
            ("trailing_stop", "risk_cond_sell_trailing_stop"),
            ("limit_sell", "risk_cond_sell_limit_sell"),
            ("stop_loss", "risk_cond_sell_stop_loss"),
        ]

    def _on_add_buy_condition(self):
        cid = self.buy_condition_combo.currentData()
        if not cid:
            return
        label = tr(f"risk_cond_buy_{cid}")
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, cid)
        self.buy_conditions_list.addItem(item)
        self._set_dirty()

    def _on_add_sell_condition(self):
        cid = self.sell_condition_combo.currentData()
        if not cid:
            return
        label = tr(f"risk_cond_sell_{cid}")
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, cid)
        self.sell_conditions_list.addItem(item)
        self._set_dirty()

    def _remove_selected(self, list_widget: QListWidget):
        row = list_widget.currentRow()
        if row >= 0:
            list_widget.takeItem(row)
            self._set_dirty()

    def _on_strategy_changed(self):
        """عند تغيير الاستراتيجية من القائمة: تطبيق افتراضياتها تلقائياً (ما عدا الخاصة)."""
        if getattr(self, "_loading_strategy", True):
            return
        mode = self.strategy_combo.currentData() or "custom"
        # auto لا يملك preset ثابت — فقط نحفظ الوضع
        if mode == "auto":
            try:
                cfg = load_config()
                cfg["strategy_mode"] = "auto"
                save_config(cfg)
                self.config_saved.emit(cfg)
            except Exception:
                pass
            return
        if mode != "custom":
            self._apply_strategy_preset(silent=True)

    def _apply_strategy_preset(self, silent=False):
        mode = self.strategy_combo.currentData() or "custom"
        if mode == "custom":
            if not silent:
                QMessageBox.information(self, tr("risk_strategy_group"), tr("risk_strategy_apply_preset_hint"))
            return
        if mode == "auto":
            # لا توجد افتراضيات ثابتة لوضع auto — فقط حفظ mode
            try:
                cfg = load_config()
                cfg["strategy_mode"] = "auto"
                save_config(cfg)
                self.config_saved.emit(cfg)
            except Exception:
                pass
            if not silent:
                QMessageBox.information(self, tr("risk_strategy_group"), tr("risk_strategy_auto_no_preset"))
            return
        preset = STRATEGY_PRESETS.get(mode, {})
        if not preset:
            if not silent:
                QMessageBox.information(self, tr("risk_strategy_group"), tr("risk_strategy_apply_preset_hint"))
            return
        cfg = load_config()
        for k, v in preset.items():
            cfg[k] = v
        cfg["strategy_mode"] = mode
        save_config(cfg)
        self._loading_strategy = True
        self._load()
        self._loading_strategy = False
        # إبلاغ اللوحة الرئيسية فوراً ليتحدّث سطر «الاستراتيجية الحالية» دون الحاجة لزر حفظ
        self.config_saved.emit(cfg)
        if not silent:
            QMessageBox.information(self, tr("risk_strategy_group"), tr("risk_strategy_preset_applied"))

    def _on_ml_retrain(self):
        """إعادة تدريب نموذج الذكاء الاصطناعي على نتائج الصفقات."""
        try:
            from ml_model import train_ml_model, build_training_dataset, MIN_TRADES_FOR_TRAINING
        except ImportError:
            QMessageBox.warning(
                self, tr("risk_ml_retrain_btn"),
                tr("risk_ml_retrain_fail") + "\n\npip install scikit-learn joblib"
            )
            return
        try:
            X, _, _, _ = build_training_dataset()
        except Exception as e:
            QMessageBox.warning(self, tr("risk_ml_retrain_btn"), tr("risk_ml_retrain_fail") + f"\n\n{type(e).__name__}: {e}")
            return
        if len(X) < MIN_TRADES_FOR_TRAINING:
            QMessageBox.information(self, tr("risk_ml_retrain_btn"), tr("risk_ml_retrain_need_more"))
            return
        _saved, err_msg, outcome = train_ml_model()
        if outcome == "saved":
            msg = tr("risk_ml_retrain_ok").format(n=len(X))
            if err_msg:
                msg += f"\n\n{err_msg}"
            QMessageBox.information(self, tr("risk_ml_retrain_btn"), msg)
        elif outcome == "not_promoted":
            QMessageBox.information(
                self,
                tr("risk_ml_retrain_btn"),
                tr("risk_ml_retrain_not_promoted_hint") + "\n\n" + (err_msg or ""),
            )
        else:
            QMessageBox.warning(
                self,
                tr("risk_ml_retrain_btn"),
                err_msg or tr("risk_ml_retrain_fail"),
            )

    @staticmethod
    def _apply_gradual_patches(cfg: dict, patches: dict) -> tuple[dict, list[str]]:
        """
        تطبيق التوصيات بشكل تدريجي (خطوة آمنة صغيرة) بدلاً من قفزات كبيرة.
        يُرجع (applied_patches, human_lines).
        """
        if not isinstance(cfg, dict) or not isinstance(patches, dict):
            return {}, []
        step_limits = {
            "bot_confidence_min": 3.0,
            "bot_expected_value_min_pct": 0.02,
            "bot_expected_value_min_pct_volatile": 0.02,
            "bot_buy_bounce_context_rsi_max": 2.0,
            "bot_buy_bounce_vwap_max_ratio": 0.001,
            "bot_buy_bounce_stoch_k_max": 2.0,
            "bot_buy_bounce_adx_min": 1.0,
            "bot_buy_bounce_macd_diff_min": 0.005,
            "composite_score_buy": 1.0,
        }
        applied: dict = {}
        lines: list[str] = []
        for k, target in patches.items():
            cur = cfg.get(k)
            # bool/int/string: تطبيق مباشر (منطقي وليس تدرجي)
            if isinstance(target, bool) or isinstance(target, str) or cur is None:
                if cur != target:
                    applied[k] = target
                    lines.append(f"- {k}: {cur} -> {target}")
                continue
            try:
                cur_f = float(cur)
                tgt_f = float(target)
            except Exception:
                if cur != target:
                    applied[k] = target
                    lines.append(f"- {k}: {cur} -> {target}")
                continue
            lim = float(step_limits.get(k, 0.0) or 0.0)
            if lim > 0:
                delta = tgt_f - cur_f
                if abs(delta) > lim:
                    tgt_f = cur_f + (lim if delta > 0 else -lim)
            # الحفاظ على نوع المفتاح
            if isinstance(cur, int) and not isinstance(cur, bool):
                new_v = int(round(tgt_f))
            else:
                new_v = round(float(tgt_f), 4)
            if new_v != cur:
                applied[k] = new_v
                lines.append(f"- {k}: {cur} -> {new_v}")
        return applied, lines

    def _open_scroll_text_dialog(self, title: str, text: str, *, yes_no: bool = False) -> bool:
        """
        نص طويل مع تمرير بالعجلة فوق المربع (ارتفاع محدود بارتفاع الشاشة).
        yes_no=False: زر موافق فقط. yes_no=True: نعم/لا ويُرجع True عند نعم.
        """
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        scr = QApplication.primaryScreen()
        avail_h = int(scr.availableGeometry().height()) if scr else 720
        avail_w = int(scr.availableGeometry().width()) if scr else 1024
        text_h = min(max(260, int(avail_h * 0.62)), 720)
        dlg.setMinimumWidth(min(560, max(480, int(avail_w * 0.45))))
        dlg.resize(min(720, int(avail_w * 0.55)), min(text_h + 100, int(avail_h * 0.9)))

        layout = QVBoxLayout(dlg)
        te = QPlainTextEdit()
        te.setPlainText(text)
        te.setReadOnly(True)
        te.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        te.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        te.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        te.setMinimumHeight(text_h)
        layout.addWidget(te)
        te.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

        if yes_no:
            dlg._tr_yes = False
            bb = QDialogButtonBox()
            bn_yes = bb.addButton(tr("risk_scroll_yes"), QDialogButtonBox.ButtonRole.YesRole)
            bn_no = bb.addButton(tr("risk_scroll_no"), QDialogButtonBox.ButtonRole.NoRole)

            def _on_yes():
                dlg._tr_yes = True
                dlg.accept()

            bn_yes.clicked.connect(_on_yes)
            bn_no.clicked.connect(dlg.reject)
            layout.addWidget(bb)
        else:
            bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
            bb.accepted.connect(dlg.accept)
            ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
            if ok_btn is not None:
                ok_btn.setText(tr("risk_scroll_ok"))
            layout.addWidget(bb)

        dlg.exec()
        if yes_no:
            return bool(getattr(dlg, "_tr_yes", False))
        return False

    def _on_trades_report(self):
        """تقرير مراجعة شامل + تعارضات الشروط؛ توصيات رقمية وموافقة للتطبيق التلقائي وإعادة تدريب ML."""
        try:
            from ml_model import (
                build_comprehensive_audit_report,
                suggest_comprehensive_audit_patches,
                train_ml_model,
            )
        except Exception as e:
            QMessageBox.warning(
                self,
                tr("risk_trades_report_btn"),
                tr("risk_ml_retrain_fail") + f"\n\n{type(e).__name__}: {e}",
            )
            return
        cfg_now = load_config()
        try:
            report = build_comprehensive_audit_report(cfg_now, window=50) or ""
        except Exception as e:
            QMessageBox.warning(
                self,
                tr("risk_trades_report_btn"),
                tr("risk_ml_retrain_fail") + f"\n\n{type(e).__name__}: {e}",
            )
            return
        if not report.strip():
            report = tr("risk_trades_report_empty")
            self._open_scroll_text_dialog(tr("risk_trades_report_btn"), report, yes_no=False)
            return

        patches, notes = suggest_comprehensive_audit_patches(cfg_now, window=50)
        if patches:
            gradual_patches, gradual_lines = self._apply_gradual_patches(cfg_now, patches)
            pp = gradual_lines if gradual_lines else [f"- {k} = {v}" for k, v in patches.items()]
            report = (
                report
                + "\n\n"
                + tr("risk_trades_report_apply_heading")
                + "\n"
                + "\n".join(pp)
            )
            if notes:
                report += "\n" + "\n".join(notes)
            full_q = report + "\n\n" + tr("risk_trades_report_apply_confirm")
            if self._open_scroll_text_dialog(tr("risk_trades_report_btn"), full_q, yes_no=True):
                cfg = load_config()
                # دمج كل مفاتيح التقرير، مع أن القيم التدريجية تطغى على الهدف الكامل لنفس المفتاح
                # (سابقاً: update(gradual_patches) فقط أهمل بقية مفاتيح patches عند وجود تدريج جزئي).
                to_apply = dict(patches)
                to_apply.update(gradual_patches)
                cfg.update(to_apply)
                save_config(cfg)
                try:
                    self.config_saved.emit(cfg)
                except Exception:
                    pass
                self._loading = True
                try:
                    self._load()
                finally:
                    self._loading = False
                self._dirty = False
                _unused, msg, outcome = train_ml_model()
                follow = (
                    tr("risk_trades_report_applied")
                    + "\n\n"
                    + tr("risk_trades_report_apply_saved_hint")
                    + "\n\n"
                    + (msg or "")
                )
                if outcome == "saved":
                    self._open_scroll_text_dialog(tr("risk_trades_report_btn"), follow, yes_no=False)
                elif outcome == "not_promoted":
                    self._open_scroll_text_dialog(tr("risk_trades_report_btn"), follow, yes_no=False)
                else:
                    self._open_scroll_text_dialog(
                        tr("risk_trades_report_btn"),
                        tr("risk_trades_report_applied") + "\n\n" + (msg or tr("risk_ml_retrain_fail")),
                        yes_no=False,
                    )
                return
        report = report + "\n\n" + tr("risk_trades_report_no_auto_patches")
        self._open_scroll_text_dialog(tr("risk_trades_report_btn"), report, yes_no=False)

    def _restore_defaults_signal_and_exit(self) -> None:
        """إعادة حقول «إشارة ودخول» و«خروج ومراكز» إلى قيم config.DEFAULTS (لا يحفظ حتى تضغط حفظ)."""
        d = DEFAULTS
        self._loading = True
        try:
            self.bot_confidence_spin.setValue(int(d.get("bot_confidence_min", 60)))
            self.bot_buy_bounce_15m_check.setChecked(
                bool(d.get("bot_buy_require_early_bounce_15m", False))
            )
            self.bot_live_auto_tune_bounce_check.setChecked(
                bool(d.get("bot_live_auto_tune_bounce", False))
            )
            self.bot_buy_bounce_rsi_check.setChecked(bool(d.get("bot_buy_bounce_use_rsi", True)))
            self.bot_buy_bounce_rsi_max_spin.setValue(
                float(d.get("bot_buy_bounce_context_rsi_max", 52.0) or 52.0)
            )
            self.bot_buy_bounce_vwap_check.setChecked(bool(d.get("bot_buy_bounce_use_vwap", True)))
            _vwap_r = float(d.get("bot_buy_bounce_vwap_max_ratio", 1.006) or 1.006)
            self.bot_buy_bounce_vwap_pct_spin.setValue((_vwap_r - 1.0) * 100.0)
            self.bot_buy_bounce_stoch_check.setChecked(bool(d.get("bot_buy_bounce_use_stoch", True)))
            self.bot_buy_bounce_stoch_max_spin.setValue(
                float(d.get("bot_buy_bounce_stoch_k_max", 58.0) or 58.0)
            )
            self.bot_buy_bounce_adx_check.setChecked(bool(d.get("bot_buy_bounce_use_adx", False)))
            self.bot_buy_bounce_adx_min_spin.setValue(
                float(d.get("bot_buy_bounce_adx_min", 14.0) or 14.0)
            )
            self.bot_buy_bounce_macd_check.setChecked(bool(d.get("bot_buy_bounce_use_macd", True)))
            self.bot_buy_bounce_macd_min_spin.setValue(
                float(d.get("bot_buy_bounce_macd_diff_min", -0.025) or -0.025)
            )
            self._sync_bounce_detail_widgets_enabled()
            self.bot_ev_gate_enabled_check.setChecked(
                bool(d.get("bot_expected_value_gate_enabled", False))
            )
            self.bot_ev_min_pct_spin.setValue(
                float(d.get("bot_expected_value_min_pct", 0.03) or 0.0)
            )
            self.bot_ev_min_trend_up_spin.setValue(
                float(d.get("bot_expected_value_min_pct_trend_up", 0.01) or 0.0)
            )
            self.bot_ev_min_trend_down_spin.setValue(
                float(d.get("bot_expected_value_min_pct_trend_down", 0.08) or 0.0)
            )
            self.bot_ev_min_range_spin.setValue(
                float(d.get("bot_expected_value_min_pct_range", 0.03) or 0.0)
            )
            self.bot_ev_min_volatile_spin.setValue(
                float(d.get("bot_expected_value_min_pct_volatile", 0.12) or 0.0)
            )
            self.ml_wf_cost_spin.setValue(
                float(d.get("ml_wf_cost_per_trade_pct", 0.08) or 0.0)
            )
            self.ml_wf_train_min_spin.setValue(int(d.get("ml_wf_train_min", 40) or 40))
            self.ml_wf_test_window_spin.setValue(int(d.get("ml_wf_test_window", 10) or 10))
            self.bot_auto_sell_check.setChecked(bool(d.get("bot_auto_sell", False)))
            self.bot_auto_sell_requires_robot_check.setChecked(
                bool(d.get("bot_auto_sell_requires_robot", False))
            )
            self.bot_block_ai_sell_while_losing_check.setChecked(
                bool(d.get("bot_block_ai_sell_while_losing", True))
            )
            self.bot_merge_composite_check.setChecked(bool(d.get("bot_merge_composite", False)))
            self.chart_show_composite_badge_check.setChecked(
                bool(d.get("chart_show_composite_badge", True))
            )
            _buy = float(d.get("composite_score_buy", 12.0) or 12.0)
            _strong = float(d.get("composite_score_strong", 31.0) or 31.0)
            _mid = float(d.get("composite_score_mid", 21.0) or 21.0)
            _adx_di = float(d.get("composite_adx_for_di", 20.0) or 20.0)
            _ct = clamp_composite_thresholds(_buy, _strong, _mid, _adx_di)
            self.composite_score_buy_spin.setValue(round(_ct["buy"], 1))
            self.composite_score_strong_spin.setValue(round(_ct["strong"], 1))
            self.composite_score_mid_spin.setValue(round(_ct["mid"], 1))
            self.composite_adx_for_di_spin.setValue(round(_ct["adx_di"], 1))
            self.mr_adx_strong_spin.setValue(float(d.get("market_readout_adx_strong_min", 30.0) or 30.0))
            self.mr_rsi_ob_spin.setValue(float(d.get("market_readout_rsi_overbought", 70.0) or 70.0))
            self.mr_rsi_os_spin.setValue(float(d.get("market_readout_rsi_oversold", 30.0) or 30.0))
            self.mr_rsi_ctx_hi_spin.setValue(float(d.get("market_readout_rsi_ctx_high", 55.0) or 55.0))
            self.mr_rsi_ctx_lo_spin.setValue(float(d.get("market_readout_rsi_ctx_low", 45.0) or 45.0))
            self.mr_st_ob_spin.setValue(float(d.get("market_readout_stoch_overbought", 74.0) or 74.0))
            self.mr_st_os_spin.setValue(float(d.get("market_readout_stoch_oversold", 26.0) or 26.0))
            self.mr_st_band_lo_spin.setValue(float(d.get("market_readout_stoch_band_lo", 45.0) or 45.0))
            self.mr_st_band_hi_spin.setValue(float(d.get("market_readout_stoch_band_hi", 55.0) or 55.0))
            self.mr_st_mid_lo_spin.setValue(float(d.get("market_readout_stoch_mid_lo", 40.0) or 40.0))
            self.mr_st_mid_hi_spin.setValue(float(d.get("market_readout_stoch_mid_hi", 60.0) or 60.0))
            self.mr_st_kd_eps_spin.setValue(float(d.get("market_readout_stoch_kd_eps", 0.25) or 0.25))
            self.mr_st_k_bull_spin.setValue(float(d.get("market_readout_stoch_k_bull_min", 55.0) or 55.0))
            self.mr_st_k_bear_spin.setValue(float(d.get("market_readout_stoch_k_bear_max", 45.0) or 45.0))
            self.mr_atr_hi_spin.setValue(float(d.get("market_readout_atr_high_vol_pct", 0.8) or 0.8))
            self.mr_st_near_spin.setValue(
                float(d.get("market_readout_supertrend_near_ratio", 0.002) or 0.002)
            )
            self.ai_promote_wait_composite_check.setChecked(
                bool(d.get("ai_promote_wait_from_composite", False))
            )
            self.ai_regime_router_check.setChecked(bool(d.get("ai_use_regime_router", True)))
            self.bot_signal_sell_bypass_tp_barrier_check.setChecked(
                bool(d.get("bot_signal_sell_bypass_tp_barrier", False))
            )
            self.bot_trailing_bypass_tp_barrier_check.setChecked(
                bool(d.get("bot_trailing_bypass_tp_barrier", False))
            )
            self.limit_sell_blocks_signal_check.setChecked(
                bool(d.get("limit_sell_blocks_until_target", False))
            )
            self.bot_auto_sl_check.setChecked(bool(d.get("bot_auto_sl", True)))
            self.bot_apply_execution_filters_check.setChecked(
                bool(d.get("bot_apply_execution_filters", True))
            )
            _ts = float(d.get("trailing_stop_pct", 3.0) or 0.1)
            self.trailing_stop_spin.setValue(round(max(0.1, min(100.0, _ts)), 1))
            _tm = float(d.get("trailing_min_profit_pct", 5.0) or 0.0)
            self.trailing_min_profit_spin.setValue(round(max(0.0, min(100.0, _tm)), 1))
            self.dca_count_spin.setValue(int(d.get("safety_orders_count", 3)))
            self.dca_step_spin.setValue(int(d.get("safety_order_step_pct", 3.0)))
            self.dca_volume_spin.setValue(float(d.get("safety_order_volume_scale", 1.0)))
            self.ml_weight_spin.setValue(int(d.get("ml_weight_pct", 30)))
            _ob_rsi = float(d.get("sell_at_overbought_rsi_min", 72.0) or 72.0)
            self.sell_overbought_rsi_spin.setValue(round(max(50.0, min(95.0, _ob_rsi)), 1))
            _ob_mp = float(d.get("sell_at_overbought_min_profit_pct", 0.35) or 0.0)
            self.sell_overbought_min_profit_spin.setValue(round(max(0.0, min(20.0, _ob_mp)), 2))
            _pk_rsi = float(d.get("sell_at_peak_rsi_min", 0.0) or 0.0)
            self.sell_peak_rsi_spin.setValue(round(max(0.0, min(95.0, _pk_rsi)), 1))
            _pk_mp = d.get("sell_at_peak_min_profit_pct")
            if _pk_mp is None:
                _pk_mp = d.get("sell_at_peak_min_profit")
            if _pk_mp is None:
                _pk_mp = 0.5
            self.sell_peak_min_profit_spin.setValue(round(max(0.0, min(20.0, float(_pk_mp))), 2))
            _ob_buf = float(d.get("sell_at_overbought_limit_buy_rsi_buffer", 5.0) or 0.0)
            self.sell_overbought_limit_buy_buffer_spin.setValue(round(max(0.0, min(25.0, _ob_buf)), 1))
        finally:
            self._loading = False
        self._set_dirty()

    def _load(self):
        cfg = load_config()
        self._loading = True
        self._loading_strategy = True
        strategy_mode = cfg.get("strategy_mode", "custom") or "custom"
        idx = self.strategy_combo.findData(strategy_mode)
        if idx >= 0:
            self.strategy_combo.setCurrentIndex(idx)
        self._loading_strategy = False
        _acp = bool(cfg.get("apply_conditions_to_presets", DEFAULTS.get("apply_conditions_to_presets", True)))
        self.apply_conditions_presets_btn.blockSignals(True)
        self.apply_conditions_presets_btn.setChecked(_acp)
        self.apply_conditions_presets_btn.setText(
            tr("risk_apply_conditions_presets_btn_on") if _acp else tr("risk_apply_conditions_presets_btn_off")
        )
        self.apply_conditions_presets_btn.setStyleSheet(self._risk_apply_presets_btn_stylesheet(_acp))
        self.apply_conditions_presets_btn.blockSignals(False)
        self.bot_follow_suggested_strategy_check.setChecked(
            bool(cfg.get("bot_follow_suggested_strategy", True))
        )
        try:
            _fsec = int(cfg.get("bot_follow_suggested_strategy_sec", 50) or 50)
        except (TypeError, ValueError):
            _fsec = 50
        self.bot_follow_strategy_sec_spin.setValue(max(15, min(300, _fsec)))
        self.amount_spin.setValue(int(cfg.get("amount_usdt", DEFAULTS["amount_usdt"])))
        cur = (cfg.get("display_currency") or DEFAULTS.get("display_currency", "USD")).strip().upper()
        cur_idx = self.display_currency_combo.findData(cur if cur in ("USD", "EUR") else "USD")
        if cur_idx >= 0:
            self.display_currency_combo.setCurrentIndex(cur_idx)
        self.currency_rate_eur_spin.setValue(float(cfg.get("currency_rate_eur") or DEFAULTS.get("currency_rate_eur", 0.92)))
        # Telegram settings
        self.telegram_enable_check.setChecked(bool(cfg.get("telegram_enabled", False)))
        self.telegram_token_edit.setText(str(cfg.get("telegram_bot_token", "")))
        self.telegram_chat_id_edit.setText(str(cfg.get("telegram_chat_id", "")))
        self.daily_loss_spin.setValue(int(cfg.get("daily_loss_limit_usdt", 0)))
        self.max_trades_per_day_spin.setValue(int(cfg.get("max_trades_per_day", 0)))
        try:
            _mopen = int(cfg.get("bot_max_open_trades", DEFAULTS.get("bot_max_open_trades", 1)))
        except (TypeError, ValueError):
            _mopen = 1
        self.bot_max_open_trades_spin.setValue(max(0, min(50, _mopen)))
        self.max_consecutive_losses_spin.setValue(int(cfg.get("bot_max_consecutive_losses", 0)))
        _cbc = get_circuit_breaker_config(cfg)
        self.cb_enabled_check.setChecked(bool(_cbc["enabled"]))
        self.cb_volatility_spin.setValue(float(_cbc["volatility_pct_max"]))
        self.cb_adx_spin.setValue(float(_cbc["adx_min"]))
        self.cb_mtf_bias_spin.setValue(float(_cbc["mtf_bias_floor"]))
        self.cb_mtf_rsi_spin.setValue(float(_cbc["mtf_rsi_threshold"]))
        self.cb_pause_spin.setValue(int(_cbc["pause_minutes"]))
        self.max_trades_per_symbol_spin.setValue(int(cfg.get("max_trades_per_symbol", 0)))
        self.portfolio_max_exposure_spin.setValue(
            int(cfg.get("portfolio_max_exposure_usdt", DEFAULTS.get("portfolio_max_exposure_usdt", 0)) or 0)
        )
        gap_min = cfg.get(
            "bot_same_symbol_buy_min_interval_min",
            DEFAULTS.get("bot_same_symbol_buy_min_interval_min", 1),
        )
        # توافق مع الإعداد القديم بالثواني
        if "bot_same_symbol_buy_min_interval_min" not in cfg and "bot_same_symbol_buy_min_interval_sec" in cfg:
            try:
                gap_min = max(0, int(round(float(cfg.get("bot_same_symbol_buy_min_interval_sec", 0) or 0) / 60.0)))
            except Exception:
                gap_min = DEFAULTS.get("bot_same_symbol_buy_min_interval_min", 1)
        self.bot_same_symbol_buy_min_interval_spin.setValue(int(gap_min or 0))
        self.bot_confidence_spin.setValue(int(cfg.get("bot_confidence_min", DEFAULTS.get("bot_confidence_min", 60))))
        self.market_scanner_pool_size_spin.setValue(
            int(cfg.get("market_scanner_pool_size", DEFAULTS.get("market_scanner_pool_size", 50)) or 50)
        )
        self.market_scanner_min_quote_volume_spin.setValue(
            int(
                float(
                    cfg.get(
                        "market_scanner_min_quote_volume_usdt",
                        DEFAULTS.get("market_scanner_min_quote_volume_usdt", 5_000_000.0),
                    )
                    or 5_000_000.0
                )
            )
        )
        self.market_scanner_min_change_spin.setValue(
            float(
                cfg.get(
                    "market_scanner_min_change_pct",
                    DEFAULTS.get("market_scanner_min_change_pct", 0.3),
                )
                or 0.3
            )
        )
        self.market_scanner_min_range_spin.setValue(
            float(
                cfg.get(
                    "market_scanner_min_range_pct",
                    DEFAULTS.get("market_scanner_min_range_pct", 1.0),
                )
                or 1.0
            )
        )
        trade_horizon = str(cfg.get("bot_trade_horizon", DEFAULTS.get("bot_trade_horizon", "short")) or "short").strip().lower()
        if trade_horizon not in ("short", "swing"):
            trade_horizon = "short"
        idx_h = self.trade_horizon_combo.findData(trade_horizon)
        if idx_h >= 0:
            self.trade_horizon_combo.setCurrentIndex(idx_h)
        self.bot_buy_bounce_15m_check.setChecked(
            bool(cfg.get("bot_buy_require_early_bounce_15m", DEFAULTS.get("bot_buy_require_early_bounce_15m", False)))
        )
        self.bot_live_auto_tune_bounce_check.setChecked(
            bool(cfg.get("bot_live_auto_tune_bounce", DEFAULTS.get("bot_live_auto_tune_bounce", False)))
        )
        self.bot_buy_bounce_rsi_check.setChecked(
            bool(cfg.get("bot_buy_bounce_use_rsi", DEFAULTS.get("bot_buy_bounce_use_rsi", True)))
        )
        self.bot_buy_bounce_rsi_max_spin.setValue(
            float(cfg.get("bot_buy_bounce_context_rsi_max", DEFAULTS.get("bot_buy_bounce_context_rsi_max", 52.0)) or 52.0)
        )
        self.bot_buy_bounce_vwap_check.setChecked(
            bool(cfg.get("bot_buy_bounce_use_vwap", DEFAULTS.get("bot_buy_bounce_use_vwap", True)))
        )
        _vwap_r = float(cfg.get("bot_buy_bounce_vwap_max_ratio", DEFAULTS.get("bot_buy_bounce_vwap_max_ratio", 1.006)) or 1.006)
        self.bot_buy_bounce_vwap_pct_spin.setValue((_vwap_r - 1.0) * 100.0)
        self.bot_buy_bounce_stoch_check.setChecked(
            bool(cfg.get("bot_buy_bounce_use_stoch", DEFAULTS.get("bot_buy_bounce_use_stoch", True)))
        )
        self.bot_buy_bounce_stoch_max_spin.setValue(
            float(cfg.get("bot_buy_bounce_stoch_k_max", DEFAULTS.get("bot_buy_bounce_stoch_k_max", 58.0)) or 58.0)
        )
        self.bot_buy_bounce_adx_check.setChecked(
            bool(cfg.get("bot_buy_bounce_use_adx", DEFAULTS.get("bot_buy_bounce_use_adx", False)))
        )
        self.bot_buy_bounce_adx_min_spin.setValue(
            float(cfg.get("bot_buy_bounce_adx_min", DEFAULTS.get("bot_buy_bounce_adx_min", 14.0)) or 14.0)
        )
        self.bot_buy_bounce_macd_check.setChecked(
            bool(cfg.get("bot_buy_bounce_use_macd", DEFAULTS.get("bot_buy_bounce_use_macd", True)))
        )
        self.bot_buy_bounce_macd_min_spin.setValue(
            float(cfg.get("bot_buy_bounce_macd_diff_min", DEFAULTS.get("bot_buy_bounce_macd_diff_min", -0.025)) or -0.025)
        )
        self._sync_bounce_detail_widgets_enabled()
        self.bot_ev_gate_enabled_check.setChecked(
            bool(cfg.get("bot_expected_value_gate_enabled", DEFAULTS.get("bot_expected_value_gate_enabled", True)))
        )
        self.bot_ev_min_pct_spin.setValue(
            float(cfg.get("bot_expected_value_min_pct", DEFAULTS.get("bot_expected_value_min_pct", 0.03)) or 0.0)
        )
        self.bot_ev_min_trend_up_spin.setValue(
            float(cfg.get("bot_expected_value_min_pct_trend_up", DEFAULTS.get("bot_expected_value_min_pct_trend_up", 0.01)) or 0.0)
        )
        self.bot_ev_min_trend_down_spin.setValue(
            float(cfg.get("bot_expected_value_min_pct_trend_down", DEFAULTS.get("bot_expected_value_min_pct_trend_down", 0.08)) or 0.0)
        )
        self.bot_ev_min_range_spin.setValue(
            float(cfg.get("bot_expected_value_min_pct_range", DEFAULTS.get("bot_expected_value_min_pct_range", 0.03)) or 0.0)
        )
        self.bot_ev_min_volatile_spin.setValue(
            float(cfg.get("bot_expected_value_min_pct_volatile", DEFAULTS.get("bot_expected_value_min_pct_volatile", 0.12)) or 0.0)
        )
        self.ml_wf_cost_spin.setValue(
            float(cfg.get("ml_wf_cost_per_trade_pct", DEFAULTS.get("ml_wf_cost_per_trade_pct", 0.08)) or 0.0)
        )
        self.ml_wf_train_min_spin.setValue(
            int(cfg.get("ml_wf_train_min", DEFAULTS.get("ml_wf_train_min", 40)) or 40)
        )
        self.ml_wf_test_window_spin.setValue(
            int(cfg.get("ml_wf_test_window", DEFAULTS.get("ml_wf_test_window", 10)) or 10)
        )
        self.bot_auto_sell_check.setChecked(bool(cfg.get("bot_auto_sell", DEFAULTS.get("bot_auto_sell", False))))
        self.bot_auto_sell_requires_robot_check.setChecked(
            bool(cfg.get("bot_auto_sell_requires_robot", DEFAULTS.get("bot_auto_sell_requires_robot", False)))
        )
        self.bot_block_ai_sell_while_losing_check.setChecked(
            bool(
                cfg.get(
                    "bot_block_ai_sell_while_losing",
                    DEFAULTS.get("bot_block_ai_sell_while_losing", True),
                )
            )
        )
        self.bot_merge_composite_check.setChecked(
            bool(cfg.get("bot_merge_composite", DEFAULTS.get("bot_merge_composite", False)))
        )
        self.chart_show_composite_badge_check.setChecked(
            bool(cfg.get("chart_show_composite_badge", DEFAULTS.get("chart_show_composite_badge", True)))
        )
        _d = DEFAULTS
        _buy = float(cfg.get("composite_score_buy", _d.get("composite_score_buy", 12.0)) or 12.0)
        _strong = float(cfg.get("composite_score_strong", _d.get("composite_score_strong", 31.0)) or 31.0)
        _mid = float(cfg.get("composite_score_mid", _d.get("composite_score_mid", 21.0)) or 21.0)
        _adx_di = float(cfg.get("composite_adx_for_di", _d.get("composite_adx_for_di", 20.0)) or 20.0)
        _ct = clamp_composite_thresholds(_buy, _strong, _mid, _adx_di)
        self.composite_score_buy_spin.setValue(round(_ct["buy"], 1))
        self.composite_score_strong_spin.setValue(round(_ct["strong"], 1))
        self.composite_score_mid_spin.setValue(round(_ct["mid"], 1))
        self.composite_adx_for_di_spin.setValue(round(_ct["adx_di"], 1))
        self.mr_adx_strong_spin.setValue(
            float(cfg.get("market_readout_adx_strong_min", _d.get("market_readout_adx_strong_min", 30.0)) or 30.0)
        )
        self.mr_rsi_ob_spin.setValue(
            float(cfg.get("market_readout_rsi_overbought", _d.get("market_readout_rsi_overbought", 70.0)) or 70.0)
        )
        self.mr_rsi_os_spin.setValue(
            float(cfg.get("market_readout_rsi_oversold", _d.get("market_readout_rsi_oversold", 30.0)) or 30.0)
        )
        self.mr_rsi_ctx_hi_spin.setValue(
            float(cfg.get("market_readout_rsi_ctx_high", _d.get("market_readout_rsi_ctx_high", 55.0)) or 55.0)
        )
        self.mr_rsi_ctx_lo_spin.setValue(
            float(cfg.get("market_readout_rsi_ctx_low", _d.get("market_readout_rsi_ctx_low", 45.0)) or 45.0)
        )
        self.mr_st_ob_spin.setValue(
            float(cfg.get("market_readout_stoch_overbought", _d.get("market_readout_stoch_overbought", 74.0)) or 74.0)
        )
        self.mr_st_os_spin.setValue(
            float(cfg.get("market_readout_stoch_oversold", _d.get("market_readout_stoch_oversold", 26.0)) or 26.0)
        )
        self.mr_st_band_lo_spin.setValue(
            float(cfg.get("market_readout_stoch_band_lo", _d.get("market_readout_stoch_band_lo", 45.0)) or 45.0)
        )
        self.mr_st_band_hi_spin.setValue(
            float(cfg.get("market_readout_stoch_band_hi", _d.get("market_readout_stoch_band_hi", 55.0)) or 55.0)
        )
        self.mr_st_mid_lo_spin.setValue(
            float(cfg.get("market_readout_stoch_mid_lo", _d.get("market_readout_stoch_mid_lo", 40.0)) or 40.0)
        )
        self.mr_st_mid_hi_spin.setValue(
            float(cfg.get("market_readout_stoch_mid_hi", _d.get("market_readout_stoch_mid_hi", 60.0)) or 60.0)
        )
        self.mr_st_kd_eps_spin.setValue(
            float(cfg.get("market_readout_stoch_kd_eps", _d.get("market_readout_stoch_kd_eps", 0.25)) or 0.25)
        )
        self.mr_st_k_bull_spin.setValue(
            float(cfg.get("market_readout_stoch_k_bull_min", _d.get("market_readout_stoch_k_bull_min", 55.0)) or 55.0)
        )
        self.mr_st_k_bear_spin.setValue(
            float(cfg.get("market_readout_stoch_k_bear_max", _d.get("market_readout_stoch_k_bear_max", 45.0)) or 45.0)
        )
        self.mr_atr_hi_spin.setValue(
            float(cfg.get("market_readout_atr_high_vol_pct", _d.get("market_readout_atr_high_vol_pct", 0.8)) or 0.8)
        )
        self.mr_st_near_spin.setValue(
            float(
                cfg.get(
                    "market_readout_supertrend_near_ratio",
                    _d.get("market_readout_supertrend_near_ratio", 0.002),
                )
                or 0.002
            )
        )
        self.ai_promote_wait_composite_check.setChecked(
            bool(cfg.get("ai_promote_wait_from_composite", DEFAULTS.get("ai_promote_wait_from_composite", False)))
        )
        self.ai_regime_router_check.setChecked(
            bool(cfg.get("ai_use_regime_router", DEFAULTS.get("ai_use_regime_router", True)))
        )
        self.bot_signal_sell_bypass_tp_barrier_check.setChecked(
            bool(
                cfg.get(
                    "bot_signal_sell_bypass_tp_barrier",
                    DEFAULTS.get("bot_signal_sell_bypass_tp_barrier", False),
                )
            )
        )
        self.bot_trailing_bypass_tp_barrier_check.setChecked(
            bool(
                cfg.get(
                    "bot_trailing_bypass_tp_barrier",
                    DEFAULTS.get("bot_trailing_bypass_tp_barrier", False),
                )
            )
        )
        self.limit_sell_blocks_signal_check.setChecked(
            bool(cfg.get("limit_sell_blocks_until_target", DEFAULTS.get("limit_sell_blocks_until_target", False)))
        )
        self.bot_auto_sl_check.setChecked(bool(cfg.get("bot_auto_sl", DEFAULTS.get("bot_auto_sl", True))))
        self.bot_apply_execution_filters_check.setChecked(apply_execution_filters(cfg))
        _ts = float(cfg.get("trailing_stop_pct", DEFAULTS.get("trailing_stop_pct", 3.0)) or 0.1)
        self.trailing_stop_spin.setValue(round(max(0.1, min(100.0, _ts)), 1))
        _tm = float(cfg.get("trailing_min_profit_pct", DEFAULTS.get("trailing_min_profit_pct", 5.0)) or 0.0)
        self.trailing_min_profit_spin.setValue(round(max(0.0, min(100.0, _tm)), 1))
        self.dca_count_spin.setValue(int(cfg.get("safety_orders_count", DEFAULTS.get("safety_orders_count", 3))))
        self.dca_step_spin.setValue(int(cfg.get("safety_order_step_pct", DEFAULTS.get("safety_order_step_pct", 3.0))))
        self.dca_volume_spin.setValue(float(cfg.get("safety_order_volume_scale", DEFAULTS.get("safety_order_volume_scale", 1.0))))
        self.ml_weight_spin.setValue(int(cfg.get("ml_weight_pct", DEFAULTS.get("ml_weight_pct", 30))))
        self.update_manifest_url_edit.setText(str(cfg.get("update_manifest_url") or DEFAULTS.get("update_manifest_url") or ""))
        # قوائم شروط الشراء والبيع
        self.buy_conditions_list.clear()
        for cid in (cfg.get("buy_conditions") or []):
            if isinstance(cid, str) and cid != "after_hs_bear_rebound":
                item = QListWidgetItem(tr(f"risk_cond_buy_{cid}"))
                item.setData(Qt.ItemDataRole.UserRole, cid)
                self.buy_conditions_list.addItem(item)
        self.sell_conditions_list.clear()
        for cid in (cfg.get("sell_conditions") or []):
            if isinstance(cid, str):
                item = QListWidgetItem(tr(f"risk_cond_sell_{cid}"))
                item.setData(Qt.ItemDataRole.UserRole, cid)
                self.sell_conditions_list.addItem(item)
        _ob_rsi = float(
            cfg.get(
                "sell_at_overbought_rsi_min",
                DEFAULTS.get("sell_at_overbought_rsi_min", 72.0),
            )
            or 72.0
        )
        self.sell_overbought_rsi_spin.setValue(round(max(50.0, min(95.0, _ob_rsi)), 1))
        _ob_mp = float(
            cfg.get(
                "sell_at_overbought_min_profit_pct",
                DEFAULTS.get("sell_at_overbought_min_profit_pct", 0.35),
            )
            or 0.0
        )
        self.sell_overbought_min_profit_spin.setValue(round(max(0.0, min(20.0, _ob_mp)), 2))
        _pk_rsi = float(cfg.get("sell_at_peak_rsi_min", DEFAULTS.get("sell_at_peak_rsi_min", 0.0)) or 0.0)
        self.sell_peak_rsi_spin.setValue(round(max(0.0, min(95.0, _pk_rsi)), 1))
        _pk_mp = cfg.get("sell_at_peak_min_profit_pct")
        if _pk_mp is None:
            _pk_mp = cfg.get("sell_at_peak_min_profit")
        if _pk_mp is None:
            _pk_mp = DEFAULTS.get("sell_at_peak_min_profit_pct", 0.5)
        self.sell_peak_min_profit_spin.setValue(round(max(0.0, min(20.0, float(_pk_mp))), 2))
        _ob_buf = float(
            cfg.get(
                "sell_at_overbought_limit_buy_rsi_buffer",
                DEFAULTS.get("sell_at_overbought_limit_buy_rsi_buffer", 5.0),
            )
            or 0.0
        )
        self.sell_overbought_limit_buy_buffer_spin.setValue(round(max(0.0, min(25.0, _ob_buf)), 1))
        self._loading = False

    def _save(self):
        cfg = load_config()
        cfg["amount_usdt"] = self.amount_spin.value()
        cfg["amount_type"] = "value"
        cfg["display_currency"] = self.display_currency_combo.currentData() or "USD"
        cfg["currency_rate_eur"] = round(self.currency_rate_eur_spin.value(), 4)
        cfg["telegram_enabled"] = self.telegram_enable_check.isChecked()
        cfg["telegram_bot_token"] = self.telegram_token_edit.text().strip()
        cfg["telegram_bot_username"] = self.telegram_bot_username_edit.text().strip()
        cfg["telegram_chat_id"] = self.telegram_chat_id_edit.text().strip()
        cfg["daily_loss_limit_usdt"] = self.daily_loss_spin.value()
        cfg["max_trades_per_day"] = self.max_trades_per_day_spin.value()
        cfg["bot_max_open_trades"] = int(self.bot_max_open_trades_spin.value())
        cfg["bot_max_consecutive_losses"] = self.max_consecutive_losses_spin.value()
        cb_en = self.cb_enabled_check.isChecked()
        cb_vol = float(self.cb_volatility_spin.value())
        cb_adx = float(self.cb_adx_spin.value())
        cb_mtf = float(self.cb_mtf_bias_spin.value())
        cb_rsi = float(self.cb_mtf_rsi_spin.value())
        cb_pause = int(self.cb_pause_spin.value())
        cfg["bot_circuit_breaker_enabled"] = cb_en
        cfg["bot_cb_volatility_pct_max"] = cb_vol
        cfg["bot_cb_adx_min"] = cb_adx
        cfg["bot_cb_mtf_bias_floor"] = cb_mtf
        cfg["bot_cb_mtf_rsi_threshold"] = cb_rsi
        cfg["bot_cb_pause_minutes"] = cb_pause
        cfg["circuit_breaker_enabled"] = cb_en
        cfg["circuit_breaker_volatility_pct_max"] = cb_vol
        cfg["circuit_breaker_adx_min"] = cb_adx
        cfg["circuit_breaker_mtf_bias_floor"] = cb_mtf
        cfg["circuit_breaker_mtf_rsi_threshold"] = cb_rsi
        cfg["circuit_breaker_pause_minutes"] = cb_pause
        cfg["max_trades_per_symbol"] = self.max_trades_per_symbol_spin.value()
        cfg["portfolio_max_exposure_usdt"] = int(self.portfolio_max_exposure_spin.value())
        cfg["bot_same_symbol_buy_min_interval_min"] = int(self.bot_same_symbol_buy_min_interval_spin.value())
        cfg.pop("bot_swing_high_lookback", None)
        cfg.pop("bot_min_pullback_from_peak_pct", None)
        cfg["bot_confidence_min"] = self.bot_confidence_spin.value()
        cfg["market_scanner_pool_size"] = int(self.market_scanner_pool_size_spin.value())
        cfg["market_scanner_min_quote_volume_usdt"] = float(self.market_scanner_min_quote_volume_spin.value())
        cfg["market_scanner_min_change_pct"] = float(self.market_scanner_min_change_spin.value())
        cfg["market_scanner_min_range_pct"] = float(self.market_scanner_min_range_spin.value())
        cfg["bot_master_profile"] = "aggressive"
        cfg["bot_trade_horizon"] = self.trade_horizon_combo.currentData() or "short"
        cfg["bot_entry_profile"] = "aggressive"
        cfg.pop("bot_second_layer_buy_min_score", None)
        cfg.pop("bot_buy_dip_gate_above_rsi", None)
        cfg.pop("bot_buy_block_if_stoch_k_gte", None)
        cfg.pop("bot_buy_block_if_stoch_d_gte", None)
        cfg.pop("bot_buy_block_if_cci_gte", None)
        cfg.pop("bot_buy_block_if_above_vwap_pct", None)
        cfg["bot_buy_require_early_bounce_15m"] = self.bot_buy_bounce_15m_check.isChecked()
        cfg["bot_live_auto_tune_bounce"] = self.bot_live_auto_tune_bounce_check.isChecked()
        cfg["bot_buy_bounce_use_rsi"] = self.bot_buy_bounce_rsi_check.isChecked()
        cfg["bot_buy_bounce_context_rsi_max"] = float(self.bot_buy_bounce_rsi_max_spin.value())
        cfg["bot_buy_bounce_use_vwap"] = self.bot_buy_bounce_vwap_check.isChecked()
        cfg["bot_buy_bounce_vwap_max_ratio"] = 1.0 + float(self.bot_buy_bounce_vwap_pct_spin.value()) / 100.0
        cfg["bot_buy_bounce_use_stoch"] = self.bot_buy_bounce_stoch_check.isChecked()
        cfg["bot_buy_bounce_stoch_k_max"] = float(self.bot_buy_bounce_stoch_max_spin.value())
        cfg["bot_buy_bounce_use_adx"] = self.bot_buy_bounce_adx_check.isChecked()
        cfg["bot_buy_bounce_adx_min"] = float(self.bot_buy_bounce_adx_min_spin.value())
        cfg["bot_buy_bounce_use_macd"] = self.bot_buy_bounce_macd_check.isChecked()
        cfg["bot_buy_bounce_macd_diff_min"] = float(self.bot_buy_bounce_macd_min_spin.value())
        cfg["bot_expected_value_gate_enabled"] = self.bot_ev_gate_enabled_check.isChecked()
        cfg["bot_expected_value_min_pct"] = float(self.bot_ev_min_pct_spin.value())
        cfg["bot_expected_value_min_pct_trend_up"] = float(self.bot_ev_min_trend_up_spin.value())
        cfg["bot_expected_value_min_pct_trend_down"] = float(self.bot_ev_min_trend_down_spin.value())
        cfg["bot_expected_value_min_pct_range"] = float(self.bot_ev_min_range_spin.value())
        cfg["bot_expected_value_min_pct_volatile"] = float(self.bot_ev_min_volatile_spin.value())
        cfg["ml_wf_cost_per_trade_pct"] = float(self.ml_wf_cost_spin.value())
        cfg["ml_wf_train_min"] = int(self.ml_wf_train_min_spin.value())
        cfg["ml_wf_test_window"] = int(self.ml_wf_test_window_spin.value())
        cfg["bot_auto_sell"] = self.bot_auto_sell_check.isChecked()
        cfg["bot_auto_sell_requires_robot"] = self.bot_auto_sell_requires_robot_check.isChecked()
        cfg["bot_block_ai_sell_while_losing"] = self.bot_block_ai_sell_while_losing_check.isChecked()
        cfg["bot_merge_composite"] = self.bot_merge_composite_check.isChecked()
        cfg["chart_show_composite_badge"] = self.chart_show_composite_badge_check.isChecked()
        _ct = clamp_composite_thresholds(
            float(self.composite_score_buy_spin.value()),
            float(self.composite_score_strong_spin.value()),
            float(self.composite_score_mid_spin.value()),
            float(self.composite_adx_for_di_spin.value()),
        )
        cfg["composite_score_buy"] = _ct["buy"]
        cfg["composite_score_strong"] = _ct["strong"]
        cfg["composite_score_mid"] = _ct["mid"]
        cfg["composite_adx_for_di"] = _ct["adx_di"]
        cfg["ai_promote_wait_from_composite"] = self.ai_promote_wait_composite_check.isChecked()
        cfg["ai_use_regime_router"] = self.ai_regime_router_check.isChecked()
        self.composite_score_buy_spin.setValue(round(_ct["buy"], 1))
        self.composite_score_strong_spin.setValue(round(_ct["strong"], 1))
        self.composite_score_mid_spin.setValue(round(_ct["mid"], 1))
        self.composite_adx_for_di_spin.setValue(round(_ct["adx_di"], 1))
        cfg["market_readout_adx_strong_min"] = float(self.mr_adx_strong_spin.value())
        cfg["market_readout_rsi_overbought"] = float(self.mr_rsi_ob_spin.value())
        cfg["market_readout_rsi_oversold"] = float(self.mr_rsi_os_spin.value())
        cfg["market_readout_rsi_ctx_high"] = float(self.mr_rsi_ctx_hi_spin.value())
        cfg["market_readout_rsi_ctx_low"] = float(self.mr_rsi_ctx_lo_spin.value())
        cfg["market_readout_stoch_overbought"] = float(self.mr_st_ob_spin.value())
        cfg["market_readout_stoch_oversold"] = float(self.mr_st_os_spin.value())
        cfg["market_readout_stoch_band_lo"] = float(self.mr_st_band_lo_spin.value())
        cfg["market_readout_stoch_band_hi"] = float(self.mr_st_band_hi_spin.value())
        cfg["market_readout_stoch_mid_lo"] = float(self.mr_st_mid_lo_spin.value())
        cfg["market_readout_stoch_mid_hi"] = float(self.mr_st_mid_hi_spin.value())
        cfg["market_readout_stoch_kd_eps"] = float(self.mr_st_kd_eps_spin.value())
        cfg["market_readout_stoch_k_bull_min"] = float(self.mr_st_k_bull_spin.value())
        cfg["market_readout_stoch_k_bear_max"] = float(self.mr_st_k_bear_spin.value())
        cfg["market_readout_atr_high_vol_pct"] = float(self.mr_atr_hi_spin.value())
        cfg["market_readout_supertrend_near_ratio"] = float(self.mr_st_near_spin.value())
        cfg["bot_signal_sell_bypass_tp_barrier"] = (
            self.bot_signal_sell_bypass_tp_barrier_check.isChecked()
        )
        cfg["bot_trailing_bypass_tp_barrier"] = (
            self.bot_trailing_bypass_tp_barrier_check.isChecked()
        )
        cfg["limit_sell_blocks_until_target"] = self.limit_sell_blocks_signal_check.isChecked()
        cfg["bot_auto_sl"] = self.bot_auto_sl_check.isChecked()
        cfg["bot_apply_execution_filters"] = self.bot_apply_execution_filters_check.isChecked()
        cfg.pop("bot_preset_full_buy_filters", None)
        cfg.pop("bot_hs_bear_rebound_enabled", None)
        cfg["trailing_stop_pct"] = float(self.trailing_stop_spin.value())
        cfg["trailing_min_profit_pct"] = float(self.trailing_min_profit_spin.value())
        cfg["safety_orders_count"] = int(self.dca_count_spin.value())
        cfg["safety_order_step_pct"] = float(self.dca_step_spin.value())
        cfg["safety_order_volume_scale"] = float(self.dca_volume_spin.value())
        _buy_out: list[str] = []
        for i in range(self.buy_conditions_list.count()):
            it = self.buy_conditions_list.item(i)
            if not it:
                continue
            cid = it.data(Qt.ItemDataRole.UserRole)
            if isinstance(cid, str) and cid and cid != "after_hs_bear_rebound":
                _buy_out.append(cid)
        cfg["buy_conditions"] = _buy_out
        cfg["sell_conditions"] = [
            self.sell_conditions_list.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.sell_conditions_list.count())
            if self.sell_conditions_list.item(i)
        ]
        cfg["sell_at_overbought_rsi_min"] = float(self.sell_overbought_rsi_spin.value())
        cfg["sell_at_overbought_min_profit_pct"] = float(self.sell_overbought_min_profit_spin.value())
        cfg["sell_at_peak_rsi_min"] = float(self.sell_peak_rsi_spin.value())
        cfg["sell_at_peak_min_profit_pct"] = float(self.sell_peak_min_profit_spin.value())
        cfg["sell_at_overbought_limit_buy_rsi_buffer"] = float(self.sell_overbought_limit_buy_buffer_spin.value())
        cfg["strategy_mode"] = self.strategy_combo.currentData() or "custom"
        cfg["apply_conditions_to_presets"] = self.apply_conditions_presets_btn.isChecked()
        cfg["bot_follow_suggested_strategy"] = self.bot_follow_suggested_strategy_check.isChecked()
        cfg["bot_follow_suggested_strategy_sec"] = int(self.bot_follow_strategy_sec_spin.value())
        cfg["ml_weight_pct"] = self.ml_weight_spin.value()
        cfg["update_manifest_url"] = self.update_manifest_url_edit.text().strip()
        save_config(cfg)
        self._dirty = False
        self.config_saved.emit(cfg)
        log.info("Risk settings saved: amount=%s, daily_limit=%s",
                 cfg["amount_usdt"], cfg["daily_loss_limit_usdt"])
        self.close()

    def _export_config(self):
        """تصدير الإعدادات إلى ملف JSON."""
        path, _ = QFileDialog.getSaveFileName(self, tr("risk_export_config"), "", "JSON (*.json)")
        if not path:
            return
        try:
            cfg = load_config()
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            QMessageBox.information(self, tr("risk_title"), tr("risk_export_done"))
        except Exception as e:
            log.warning("Export config failed: %s", e)
            QMessageBox.warning(self, tr("risk_title"), tr("risk_export_failed").format(err=str(e)))

    def _import_config(self):
        """استيراد الإعدادات من ملف JSON."""
        path, _ = QFileDialog.getOpenFileName(self, tr("risk_import_config"), "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            if not isinstance(cfg, dict):
                raise ValueError("Invalid config format")
            save_config(cfg)
            self._loading = True
            self._load()
            self._loading = False
            self._dirty = False
            QMessageBox.information(self, tr("risk_title"), tr("risk_import_done"))
        except Exception as e:
            log.warning("Import config failed: %s", e)
            QMessageBox.warning(self, tr("risk_title"), tr("risk_import_failed").format(err=str(e)))

    def _connect_dirty(self):
        """ربط كل الحقول بتفعيل علامة «تغييرات غير محفوظة»."""
        for w in (self.amount_spin, self.daily_loss_spin, self.max_trades_per_day_spin, self.bot_max_open_trades_spin, self.max_consecutive_losses_spin, self.cb_volatility_spin, self.cb_adx_spin, self.cb_mtf_bias_spin, self.cb_mtf_rsi_spin, self.cb_pause_spin, self.max_trades_per_symbol_spin, self.portfolio_max_exposure_spin, self.bot_same_symbol_buy_min_interval_spin, self.bot_confidence_spin, self.market_scanner_pool_size_spin, self.market_scanner_min_quote_volume_spin, self.market_scanner_min_change_spin, self.market_scanner_min_range_spin, self.bot_ev_min_pct_spin, self.bot_ev_min_trend_up_spin, self.bot_ev_min_trend_down_spin, self.bot_ev_min_range_spin, self.bot_ev_min_volatile_spin, self.ml_wf_cost_spin, self.ml_wf_train_min_spin, self.ml_wf_test_window_spin,
                  self.trailing_stop_spin, self.trailing_min_profit_spin,
                  self.sell_overbought_rsi_spin,
                  self.sell_overbought_min_profit_spin,
                  self.sell_peak_rsi_spin,
                  self.sell_peak_min_profit_spin,
                  self.sell_overbought_limit_buy_buffer_spin,
                  self.dca_count_spin, self.dca_step_spin, self.dca_volume_spin,
                  self.currency_rate_eur_spin, self.ml_weight_spin,
                  self.composite_score_buy_spin, self.composite_score_strong_spin, self.composite_score_mid_spin, self.composite_adx_for_di_spin,
                  self.mr_adx_strong_spin, self.mr_rsi_ob_spin, self.mr_rsi_os_spin, self.mr_rsi_ctx_hi_spin, self.mr_rsi_ctx_lo_spin,
                  self.mr_st_ob_spin, self.mr_st_os_spin, self.mr_st_band_lo_spin, self.mr_st_band_hi_spin,
                  self.mr_st_mid_lo_spin, self.mr_st_mid_hi_spin, self.mr_st_kd_eps_spin, self.mr_st_k_bull_spin, self.mr_st_k_bear_spin,
                  self.mr_atr_hi_spin, self.mr_st_near_spin):
            if hasattr(w, "valueChanged"):
                w.valueChanged.connect(self._set_dirty)
            elif hasattr(w, "stateChanged"):
                w.stateChanged.connect(self._set_dirty)
        for w in (
            self.cb_enabled_check,
            self.bot_buy_bounce_15m_check,
            self.bot_live_auto_tune_bounce_check,
            self.bot_buy_bounce_rsi_check,
            self.bot_buy_bounce_vwap_check,
            self.bot_buy_bounce_stoch_check,
            self.bot_buy_bounce_adx_check,
            self.bot_buy_bounce_macd_check,
            self.bot_ev_gate_enabled_check,
            self.bot_auto_sell_check,
            self.bot_auto_sell_requires_robot_check,
            self.bot_block_ai_sell_while_losing_check,
            self.bot_merge_composite_check,
            self.chart_show_composite_badge_check,
            self.ai_promote_wait_composite_check,
            self.ai_regime_router_check,
            self.limit_sell_blocks_signal_check,
            self.bot_signal_sell_bypass_tp_barrier_check,
            self.bot_trailing_bypass_tp_barrier_check,
            self.bot_auto_sl_check,
            self.bot_apply_execution_filters_check,
        ):
            if hasattr(w, "stateChanged"):
                w.stateChanged.connect(self._set_dirty)
        # Telegram controls
        self.telegram_enable_check.stateChanged.connect(self._set_dirty)
        self.telegram_token_edit.textChanged.connect(self._set_dirty)
        self.telegram_bot_username_edit.textChanged.connect(self._set_dirty)
        self.telegram_chat_id_edit.textChanged.connect(self._set_dirty)
        self.update_manifest_url_edit.textChanged.connect(self._set_dirty)
        self.strategy_combo.currentIndexChanged.connect(self._set_dirty)
        self.bot_buy_bounce_vwap_pct_spin.valueChanged.connect(self._set_dirty)
        self.bot_buy_bounce_rsi_max_spin.valueChanged.connect(self._set_dirty)
        self.bot_buy_bounce_stoch_max_spin.valueChanged.connect(self._set_dirty)
        self.bot_buy_bounce_adx_min_spin.valueChanged.connect(self._set_dirty)
        self.bot_buy_bounce_macd_min_spin.valueChanged.connect(self._set_dirty)
        self.trade_horizon_combo.currentIndexChanged.connect(self._set_dirty)

    def _show_three_terms_help(self):
        """شرح المصطلحات الثلاثة (Token، معرف البوت، معرّف المحادثة) من رسالة BotFather."""
        QMessageBox.information(
            self,
            tr("telegram_three_terms_title"),
            tr("telegram_three_terms_help"),
        )

    def _show_chat_id_help(self):
        """فتح الرابط وشرح كيفية الحصول على معرّف المحادثة (رقم وليس Bot API)."""
        token = (self.telegram_token_edit.text() or "").strip()
        if get_language() == "ar":
            steps = (
                "معرّف المحادثة = رقم فقط (ليس الرابط).\n\n"
                "⚠ لا تنسخ الرابط. انسخ الرقم من داخل الصفحة فقط.\n\n"
                "الخطوات:\n"
                "1) افتح تيليجرام وأرسل /start للبوت.\n"
                "2) اضغط الزر أدناه لفتح الرابط في المتصفح.\n"
                "3) في الصفحة التي تفتح، ابحث عن: \"chat\":{\"id\":\n"
                "4) الرقم الذي بعدها (مثل 584739201) — انسخ هذا الرقم فقط.\n"
                "5) الصق الرقم في حقل «معرّف المحادثة» (وليس الرابط)."
            )
        else:
            steps = (
                "Chat ID = a number only (not the link).\n\n"
                "⚠ Do not copy the link. Copy only the number from inside the page.\n\n"
                "Steps:\n"
                "1) Open Telegram and send /start to the bot.\n"
                "2) Click the button below to open the link in your browser.\n"
                "3) On the page that opens, search for: \"chat\":{\"id\":\n"
                "4) The number after it (e.g. 584739201) — copy that number only.\n"
                "5) Paste the number in the «Chat ID» field (not the link)."
            )
        if token:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            cb = QApplication.clipboard()
            if cb:
                cb.setText(url)
            try:
                QDesktopServices.openUrl(QUrl(url))
            except Exception:
                pass
            if get_language() == "ar":
                steps += "\n\n✓ تم نسخ الرابط إلى الحافظة. الصقه في المتصفح (Ctrl+V) ثم Enter."
            else:
                steps += "\n\n✓ Link copied to clipboard. Paste in browser (Ctrl+V) then Enter."
        else:
            if get_language() == "ar":
                steps += "\n\n(ضع رمز البوت في الحقل أعلاه ثم اضغط الزر مرة أخرى لفتح الرابط.)"
            else:
                steps += "\n\n(Put the bot token in the field above and press the button again to open the link.)"
        QMessageBox.information(self, tr("telegram_chat_id_help_btn"), steps)

    def _send_telegram_test(self):
        """إرسال رسالة تجريبية إلى تيليجرام باستخدام القيم الحالية في النموذج."""
        token = (self.telegram_token_edit.text() or "").strip()
        chat_id = (self.telegram_chat_id_edit.text() or "").strip()
        if not token or not chat_id:
            if get_language() == "ar":
                QMessageBox.warning(self, tr("telegram_test_btn"), "أدخل رمز البوت ومعرّف المحادثة (رقم Chat ID) أولاً.")
            else:
                QMessageBox.warning(self, tr("telegram_test_btn"), "Enter bot token and Chat ID (number) first.")
            return
        if chat_id.startswith("@"):
            if get_language() == "ar":
                QMessageBox.warning(
                    self, tr("telegram_test_btn"),
                    "معرّف المحادثة يجب أن يكون رقماً وليس @username. استخدم زر «كيف أحصل على معرّف المحادثة؟» وانسخ الرقم من الصفحة (وليس الرابط)."
                )
            else:
                QMessageBox.warning(self, tr("telegram_test_btn"), "Chat ID must be a number, not @username. Use «How do I get Chat ID?» and copy the number from the page (not the link).")
            return
        if "http" in chat_id or "telegram.org" in chat_id or ("/" in chat_id and not chat_id.lstrip("-").isdigit()):
            if get_language() == "ar":
                QMessageBox.warning(
                    self, tr("telegram_test_btn"),
                    "معرّف المحادثة يجب أن يكون رقماً فقط، وليس الرابط.\n\n"
                    "افتح الرابط في المتصفح، ثم من الصفحة ابحث عن \"chat\":{\"id\": وانْسخ الرقم الذي بعده (مثل 584739201) والصقه هنا."
                )
            else:
                QMessageBox.warning(
                    self, tr("telegram_test_btn"),
                    "Chat ID must be the number only, not the link.\n\n"
                    "Open the link in your browser, then on the page search for \"chat\":{\"id\": and copy the number after it (e.g. 584739201) and paste it here."
                )
            return
        ok, reason, err_detail = send_telegram_test_message(token, chat_id)
        detail = f"\n\n{err_detail}" if (err_detail and len(err_detail) < 300) else ""
        if get_language() == "ar":
            if ok:
                QMessageBox.information(self, tr("telegram_test_btn"), "تم إرسال الرسالة التجريبية. تحقق من تيليجرام.")
            elif reason == "no_internet":
                QMessageBox.warning(self, tr("telegram_test_btn"), "لا يوجد اتصال بالإنترنت. تحقق من الاتصال ثم جرّب مرة أخرى.")
            elif reason == "bad_token":
                QMessageBox.warning(self, tr("telegram_test_btn"), "رمز البوت غير صحيح. تحقق من الرمز من BotFather.")
            elif reason == "bad_chat_id":
                msg = "معرّف المحادثة (Chat ID) غير صحيح أو البوت لم يتلقَ رسالة منك بعد.\n\nأرسل /start للبوت في تيليجرام، ثم افتح:\nhttps://api.telegram.org/bot<رمزك>/getUpdates\nوابحث عن \"chat\":{\"id\": ثم انسخ الرقم (قد يكون سالباً للمجموعات)."
                QMessageBox.warning(self, tr("telegram_test_btn"), msg + detail)
            else:
                QMessageBox.warning(self, tr("telegram_test_btn"), "فشل الإرسال." + detail)
        else:
            if ok:
                QMessageBox.information(self, tr("telegram_test_btn"), "Test message sent. Check Telegram.")
            elif reason == "no_internet":
                QMessageBox.warning(self, tr("telegram_test_btn"), "No internet connection. Check your connection and try again.")
            elif reason == "bad_token":
                QMessageBox.warning(self, tr("telegram_test_btn"), "Invalid bot token. Check the token from BotFather.")
            elif reason == "bad_chat_id":
                msg = "Chat ID is wrong or the bot has not received a message from you yet.\n\nSend /start to the bot in Telegram, then open:\nhttps://api.telegram.org/bot<YOUR_TOKEN>/getUpdates\nand copy the number from \"chat\":{\"id\": (can be negative for groups)."
                QMessageBox.warning(self, tr("telegram_test_btn"), msg + detail)
            else:
                QMessageBox.warning(self, tr("telegram_test_btn"), "Send failed." + detail)

    def _apply_ev_wf_preset(self, name: str):
        """EV + Walk-Forward + ثقة البوت + وزن ML + تريلينغ (قيم البوت الافتراضية)."""
        _ = name
        self.bot_ev_min_pct_spin.setValue(0.00)
        self.bot_ev_min_trend_up_spin.setValue(0.00)
        self.bot_ev_min_trend_down_spin.setValue(0.01)
        self.bot_ev_min_range_spin.setValue(0.00)
        self.bot_ev_min_volatile_spin.setValue(0.03)
        self.ml_wf_cost_spin.setValue(0.06)
        self.ml_wf_train_min_spin.setValue(30)
        self.ml_wf_test_window_spin.setValue(12)
        self.bot_confidence_spin.setValue(54)
        self.ml_weight_spin.setValue(35)
        self.trailing_stop_spin.setValue(1.8)
        self.trailing_min_profit_spin.setValue(0.8)
        self.bot_ev_gate_enabled_check.setChecked(True)
        self._set_dirty()

    def _apply_market_scanner_aggressive_preset(self):
        """Preset سريع لماسح السوق: يركز على الحركة والسيولة العالية."""
        self.market_scanner_pool_size_spin.setValue(70)
        self.market_scanner_min_quote_volume_spin.setValue(15_000_000)
        self.market_scanner_min_change_spin.setValue(0.8)
        self.market_scanner_min_range_spin.setValue(1.8)
        self._set_dirty()

    def _apply_master_profile(self, name: str):
        """إعادة تطبيق إعدادات البوت حسب أفق الصفقة (قصير/سوينغ)."""
        _ = name
        h = (self.trade_horizon_combo.currentData() or "short").strip().lower()
        if h not in ("short", "swing"):
            h = "short"
        self._apply_ev_wf_preset("aggressive")
        self.cb_enabled_check.setChecked(True)
        self.cb_mtf_rsi_spin.setValue(45.0)
        if h == "swing":
            self.cb_volatility_spin.setValue(1.9)
            self.cb_adx_spin.setValue(18.0)
            self.cb_mtf_bias_spin.setValue(-0.75)
        else:
            self.cb_volatility_spin.setValue(2.4)
            self.cb_adx_spin.setValue(15.0)
            self.cb_mtf_bias_spin.setValue(-1.10)
        self.bot_auto_sell_check.setChecked(True)
        self.bot_merge_composite_check.setChecked(False)
        if h == "swing":
            self.composite_score_buy_spin.setValue(13.0)
            self.composite_score_strong_spin.setValue(32.0)
            self.composite_score_mid_spin.setValue(22.0)
            self.composite_adx_for_di_spin.setValue(21.0)
            self.bot_confidence_spin.setValue(60)
            self.ml_weight_spin.setValue(34)
        else:
            self.composite_score_buy_spin.setValue(10.0)
            self.composite_score_strong_spin.setValue(27.0)
            self.composite_score_mid_spin.setValue(17.0)
            self.composite_adx_for_di_spin.setValue(18.0)
            self.bot_confidence_spin.setValue(54)
            self.ml_weight_spin.setValue(28)
        self.limit_sell_blocks_signal_check.setChecked(False)
        self.bot_signal_sell_bypass_tp_barrier_check.setChecked(True)
        self.bot_trailing_bypass_tp_barrier_check.setChecked(True)
        self.bot_auto_sl_check.setChecked(True)
        self.bot_block_ai_sell_while_losing_check.setChecked(True)
        self.trailing_stop_spin.setValue(2.2 if h == "swing" else 1.8)
        self.trailing_min_profit_spin.setValue(1.8 if h == "swing" else 0.8)
        self.bot_same_symbol_buy_min_interval_spin.setValue(4 if h == "swing" else 0)
        self._set_dirty()

    def _on_trade_horizon_changed(self):
        if getattr(self, "_loading", False):
            return
        self._apply_master_profile("aggressive")

    def _apply_trade_horizon(self, mode: str):
        """مُستبقٍ للتوافق الخلفي — الإعدادات التفصيلية تُطبَّق الآن عبر _apply_master_profile."""
        _ = mode

    def _sync_bounce_detail_widgets_enabled(self, *_):
        """عند إيقاف فلتر الارتداد: تعطيل الحقول الفرعية (لا تُقرأ من البوت)."""
        on = self.bot_buy_bounce_15m_check.isChecked()
        for w in (
            self.bot_live_auto_tune_bounce_check,
            self.bot_buy_bounce_rsi_check,
            self.bot_buy_bounce_rsi_max_spin,
            self.bot_buy_bounce_vwap_check,
            self.bot_buy_bounce_vwap_pct_spin,
            self.bot_buy_bounce_stoch_check,
            self.bot_buy_bounce_stoch_max_spin,
            self.bot_buy_bounce_adx_check,
            self.bot_buy_bounce_adx_min_spin,
            self.bot_buy_bounce_macd_check,
            self.bot_buy_bounce_macd_min_spin,
        ):
            w.setEnabled(on)

    def _risk_apply_presets_btn_stylesheet(self, on: bool) -> str:
        if on:
            return (
                "QPushButton { background-color: #1a4d2a; color: #d8f5e0; padding: 6px 14px; "
                "border-radius: 6px; font-weight: bold; border: 1px solid #2d8f4a; }"
            )
        return (
            "QPushButton { background-color: #442a2a; color: #f0d0d0; padding: 6px 14px; "
            "border-radius: 6px; font-weight: bold; border: 1px solid #884444; }"
        )

    def _on_apply_conditions_presets_toggled(self, checked: bool):
        self.apply_conditions_presets_btn.setText(
            tr("risk_apply_conditions_presets_btn_on") if checked else tr("risk_apply_conditions_presets_btn_off")
        )
        self.apply_conditions_presets_btn.setStyleSheet(self._risk_apply_presets_btn_stylesheet(checked))
        self._set_dirty()

    def _set_dirty(self, *_):
        if not getattr(self, "_loading", False):
            self._dirty = True

    def closeEvent(self, event: QCloseEvent):
        if not getattr(self, "_dirty", False):
            event.accept()
            return
        msg = QMessageBox(self)
        msg.setWindowTitle(tr("risk_title"))
        msg.setText(tr("risk_unsaved_hint"))
        save_btn = msg.addButton(tr("risk_save"), QMessageBox.ButtonRole.AcceptRole)
        discard_btn = msg.addButton(tr("risk_discard"), QMessageBox.ButtonRole.DestructiveRole)
        msg.addButton(tr("risk_cancel"), QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(save_btn)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == save_btn:
            self._save()
            event.accept()
        elif clicked == discard_btn:
            self._dirty = False
            event.accept()
        else:
            event.ignore()
