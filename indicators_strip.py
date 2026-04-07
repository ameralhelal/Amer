# indicators_strip.py — شريط المؤشرات في الأسفل: ملخص الذكاء + كل مؤشر كقسم قابل للنقر
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QScrollArea, QFrame,
)
from PyQt6.QtCore import Qt, pyqtSignal


class ClickableIndicatorCard(QFrame):
    """بطاقة مؤشر قابلة للنقر — عند النقر تُصدِر إشارة بالمفتاح."""
    clicked = pyqtSignal(str)

    def __init__(self, key: str, title_ar: str, parent=None):
        super().__init__(parent)
        self.setObjectName("IndicatorCard")
        self._key = key
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("""
            #IndicatorCard {
                background-color: #252a32;
                border: 1px solid #353b45;
                border-radius: 8px;
                padding: 8px 12px;
                min-width: 72px;
            }
            #IndicatorCard:hover {
                background-color: #2d333d;
                border-color: #0d7dd6;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(2)
        self._label = QLabel(title_ar)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet("color: #e0e0e0; font-size: 12px; font-weight: bold;")
        self._label.setWordWrap(True)
        layout.addWidget(self._label)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._key)
        super().mousePressEvent(event)


class IndicatorsStrip(QWidget):
    """شريط أقسام المؤشرات في الأسفل: ملخص الذكاء + كل مؤشر. النقر يفتح نافذة التفاصيل."""
    section_clicked = pyqtSignal(str)  # "ai_summary" | "rsi" | "macd" | ...

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(72)
        self.setStyleSheet("background-color: #1a1d23; border-top: 1px solid #2a2e36;")

        # عناوين الأقسام: مفتاح -> عنوان عربي
        self._sections = [
            ("ai_summary", "ملخص الذكاء"),
            ("rsi", "RSI"),
            ("macd", "MACD"),
            ("bb", "بولينجر"),
            ("vwap", "VWAP"),
            ("adx", "ADX"),
            ("stoch_rsi", "Stoch RSI"),
            ("atr", "ATR"),
            ("cci", "CCI"),
            ("supertrend", "Supertrend"),
            ("mfi", "MFI"),
            ("willr", "Williams %R"),
            ("pivot", "Pivot"),
            ("ema", "EMAs"),
            ("ichimoku", "Ichimoku"),
        ]

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        scroll.setFixedHeight(70)

        strip_content = QWidget()
        strip_layout = QHBoxLayout(strip_content)
        strip_layout.setContentsMargins(8, 6, 8, 6)
        strip_layout.setSpacing(8)

        title_label = QLabel("المؤشرات:")
        title_label.setStyleSheet("color: #888; font-size: 12px; font-weight: bold;")
        strip_layout.addWidget(title_label)

        for key, title_ar in self._sections:
            card = ClickableIndicatorCard(key, title_ar)
            card.clicked.connect(self.section_clicked.emit)
            strip_layout.addWidget(card)

        strip_layout.addStretch(1)
        scroll.setWidget(strip_content)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)
