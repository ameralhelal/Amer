# mode_toggle.py
# Toggle بين REAL MODE و TESTNET MODE (Simulation)

from PyQt6.QtWidgets import QWidget, QPushButton, QHBoxLayout
from PyQt6.QtCore import pyqtSignal


class ModeToggle(QWidget):
    """
    زرّين للتبديل بين:
    - REAL MODE (أخضر)
    - TESTNET MODE (أحمر)

    الإشارة:
        True  = REAL
        False = TESTNET
    """

    mode_changed = pyqtSignal(bool)

    def __init__(self):
        super().__init__()

        self.setObjectName("ModeToggle")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # زر حقيقي — تداول حقيقي (Mainnet، أموال حقيقية)
        self.real_btn = QPushButton("حقيقي")
        self.real_btn.setObjectName("RealButton")
        self.real_btn.setCheckable(True)
        self.real_btn.setFixedHeight(24)
        self.real_btn.setToolTip("تداول حقيقي: يستخدم مفاتيح Mainnet من إعدادات API (binance.com)")

        # زر وهمي — تداول وهمي (أموال تجريبية)
        self.test_btn = QPushButton("وهمي")
        self.test_btn.setObjectName("TestnetButton")
        self.test_btn.setCheckable(True)
        self.test_btn.setFixedHeight(24)
        self.test_btn.setToolTip("تداول وهمي: يستخدم مفاتيح Testnet من إعدادات API (testnet.binance.vision)")

        layout.addWidget(self.real_btn, 1)
        layout.addWidget(self.test_btn, 1)

        # الوضع الافتراضي = TESTNET
        self.real_mode = False
        self.test_btn.setChecked(True)

        # الإشارات
        self.real_btn.clicked.connect(self.set_real_mode)
        self.test_btn.clicked.connect(self.set_test_mode)

    # ============================================================
    #   FUNCTIONS
    # ============================================================
    def set_real_mode(self):
        self.real_mode = True
        self.real_btn.setChecked(True)
        self.test_btn.setChecked(False)
        self.mode_changed.emit(True)

    def set_test_mode(self):
        self.real_mode = False
        self.real_btn.setChecked(False)
        self.test_btn.setChecked(True)
        self.mode_changed.emit(False)

    def is_real_mode(self) -> bool:
        return self.real_mode
