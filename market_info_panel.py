from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt


class MarketInfoPanel(QWidget):
    def __init__(self):
        super().__init__()

        self.current_symbol = "BTCUSDT"   # ← إضافة مهمة

        layout = QVBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        self.label_title = QLabel("معلومات السوق")
        self.label_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_title.setStyleSheet("font-size: 18px; font-weight: bold; color: #ffffff;")

        self.label_trend = QLabel("الاتجاه: -")
        self.label_volume = QLabel("قوة الحجم: -")
        self.label_volatility = QLabel("التقلب: -")

        self.label_trend.setStyleSheet("color: #cccccc;")
        self.label_volume.setStyleSheet("color: #cccccc;")
        self.label_volatility.setStyleSheet("color: #cccccc;")

        layout.addWidget(self.label_title)
        layout.addWidget(self.label_trend)
        layout.addWidget(self.label_volume)
        layout.addWidget(self.label_volatility)
        layout.addStretch()

        self.setLayout(layout)

    # ----------------------------------------------------
    # تحديث بيانات السوق القادمة من WebSocketManager
    # ----------------------------------------------------
    def update_market_info(self, *args):
        # يدعم: update_market_info(info) أو update_market_info(interval, info)
        if len(args) == 1:
            interval, info = "1m", args[0]
        else:
            interval, info = args[0], args[1]

        if interval != "1m":
            return

        try:
            trend = info.get("trend", "-")
            volume = float(info.get("volume_strength", 0.0))
            volatility_pct = float(info.get("volatility_pct", 0.0))

            self.label_trend.setText(f"{self.current_symbol} — الاتجاه: {trend}")
            if trend == "UP":
                self.label_trend.setStyleSheet("color: #00cc66; font-weight: bold;")
            elif trend == "DOWN":
                self.label_trend.setStyleSheet("color: #ff5555; font-weight: bold;")
            else:
                self.label_trend.setStyleSheet("color: #cccccc;")

            self.label_volume.setText(f"{self.current_symbol} — Volume Strength: {volume:.2f}")
            if volume >= 1.2:
                self.label_volume.setStyleSheet("color: #00cc66;")
            elif volume <= 0.8 and volume != 0.0:
                self.label_volume.setStyleSheet("color: #ffcc66;")
            else:
                self.label_volume.setStyleSheet("color: #cccccc;")

            self.label_volatility.setText(f"{self.current_symbol} — التقلب: {volatility_pct:.2f}%")
            if volatility_pct >= 0.8:
                self.label_volatility.setStyleSheet("color: #ff5555;")
            elif volatility_pct >= 0.3:
                self.label_volatility.setStyleSheet("color: #ffcc66;")
            else:
                self.label_volatility.setStyleSheet("color: #00cc66;")
        except Exception as e:
            print("MarketInfoPanel update_market_info error:", e)

    # ----------------------------------------------------
    # دالة تغيير العملة عند اختيار المستخدم لرمز جديد
    # ----------------------------------------------------
    def change_symbol(self, symbol: str):
        self.current_symbol = symbol

        # تحديث النصوص مباشرة
        self.label_trend.setText(f"{symbol} — الاتجاه: -")
        self.label_volume.setText(f"{symbol} — قوة الحجم: -")
        self.label_volatility.setText(f"{symbol} — التقلب: -")

        print(f"MarketInfoPanel switched to symbol: {symbol}")
