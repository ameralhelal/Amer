from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QFrame
from PyQt6.QtCore import Qt, pyqtSignal
from format_utils import format_price


class ClickableIndicatorRow(QFrame):
    """صف مؤشر قابل للنقر — عند النقر يُصدِر إشارة بمفتاح المؤشر."""
    clicked = pyqtSignal(str)

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self._key = key
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("ClickableIndicatorRow")
        self.setStyleSheet("""
            #ClickableIndicatorRow {
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 4px;
                padding: 2px 4px;
            }
            #ClickableIndicatorRow:hover {
                background-color: #2a2e38;
                border-color: #0d7dd6;
            }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        self._label = QLabel()
        self._label.setStyleSheet("color: #cccccc;")
        layout.addWidget(self._label)

    def setText(self, text: str):
        self._label.setText(text)

    def setStyleSheetForLabel(self, style: str):
        self._label.setStyleSheet(style)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._key)
        super().mousePressEvent(event)


class IndicatorsPanel(QWidget):
    indicator_clicked = pyqtSignal(str)  # عند النقر على مؤشر نفتح شارت المؤشر

    def __init__(self):
        super().__init__()

        self.current_symbol = "BTCUSDT"

        layout = QVBoxLayout()
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        self.label_title = QLabel("لوحة المؤشرات — انقر على مؤشر لفتح شارته")
        self.label_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label_title.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #ffffff;"
        )

        self.row_macd = ClickableIndicatorRow("macd")
        self.row_macd.setText("MACD: -")
        self.row_macd.clicked.connect(self.indicator_clicked.emit)

        self.row_rsi = ClickableIndicatorRow("rsi")
        self.row_rsi.setText("RSI: -")
        self.row_rsi.clicked.connect(self.indicator_clicked.emit)

        self.row_bb = ClickableIndicatorRow("bb")
        self.row_bb.setText("Bollinger Bands: -")
        self.row_bb.clicked.connect(self.indicator_clicked.emit)

        self.row_pro = ClickableIndicatorRow("vwap")
        self.row_pro.setText("Pro Indicators (VWAP/ATR/ADX/...): -")
        self.row_pro.clicked.connect(self.indicator_clicked.emit)

        self.row_extra = ClickableIndicatorRow("supertrend")
        self.row_extra.setText("Supertrend / MFI / Williams %R / EMAs: -")
        self.row_extra.clicked.connect(self.indicator_clicked.emit)

        self.row_levels = ClickableIndicatorRow("pivot")
        self.row_levels.setText("Levels (Pivot): -")
        self.row_levels.clicked.connect(self.indicator_clicked.emit)

        layout.addWidget(self.label_title)
        layout.addWidget(self.row_macd)
        layout.addWidget(self.row_rsi)
        layout.addWidget(self.row_bb)
        layout.addWidget(self.row_pro)
        layout.addWidget(self.row_extra)
        layout.addWidget(self.row_levels)

        layout.addStretch()
        self.setLayout(layout)

    # ----------------------------------------------------
    # استقبال المؤشرات من TradingPanel (نظام الفريمات)
    # ----------------------------------------------------
    def update_indicators(self, *args):
        # يدعم: update_indicators(indicators) أو update_indicators(interval, indicators)
        if len(args) == 1:
            _, indicators = "1m", args[0]
        else:
            _, indicators = args[0], args[1]

        try:
            # MACD
            macd = float(indicators.get("macd", 0.0))
            signal = float(indicators.get("signal", 0.0))
            hist = float(indicators.get("hist", 0.0))

            self.row_macd.setText(
                f"{self.current_symbol} — MACD: {macd:.5f} | Signal: {signal:.5f} | Hist: {hist:.5f}"
            )
            if hist > 0:
                self.row_macd.setStyleSheetForLabel("color: #00cc66;")
            elif hist < 0:
                self.row_macd.setStyleSheetForLabel("color: #ff5555;")
            else:
                self.row_macd.setStyleSheetForLabel("color: #cccccc;")

            rsi = float(indicators.get("rsi", 50.0))
            self.row_rsi.setText(f"{self.current_symbol} — RSI: {rsi:.2f}")
            if rsi > 70:
                self.row_rsi.setStyleSheetForLabel("color: #ff5555; font-weight: bold;")
            elif rsi < 30:
                self.row_rsi.setStyleSheetForLabel("color: #3399ff; font-weight: bold;")
            else:
                self.row_rsi.setStyleSheetForLabel("color: #00cc66;")

            upper = float(indicators.get("bb_upper", 0.0))
            lower = float(indicators.get("bb_lower", 0.0))
            width = float(indicators.get("bb_width", 0.0))
            self.row_bb.setText(
                f"{self.current_symbol} — Bollinger: Upper={upper:.4f}, Lower={lower:.4f}, Width={width:.4f}"
            )

            vwap = float(indicators.get("vwap", 0.0))
            atr = float(indicators.get("atr14", 0.0))
            adx = float(indicators.get("adx14", 0.0))
            pdi = float(indicators.get("plus_di14", 0.0))
            mdi = float(indicators.get("minus_di14", 0.0))
            st_k = float(indicators.get("stoch_rsi_k", 0.0))
            st_d = float(indicators.get("stoch_rsi_d", 0.0))
            obv = float(indicators.get("obv", 0.0))
            cci = float(indicators.get("cci20", 0.0))
            tenkan = float(indicators.get("ichimoku_tenkan", 0.0))
            kijun = float(indicators.get("ichimoku_kijun", 0.0))
            self.row_pro.setText(
                f"{self.current_symbol} — VWAP={format_price(vwap)} | ATR14={format_price(atr)} | ADX14={adx:.1f} (+DI={pdi:.1f}/-DI={mdi:.1f}) "
                f"StochRSI K/D={st_k:.1f}/{st_d:.1f} | CCI20={cci:.1f} | OBV={obv:.0f} | Ichimoku T/K={format_price(tenkan)}/{format_price(kijun)}"
            )

            st_val = float(indicators.get("supertrend", 0.0))
            st_dir = int(indicators.get("supertrend_dir", 0))
            close_px = float(indicators.get("close", 0.0) or 0.0)
            near_st = (
                close_px > 0
                and st_val > 0
                and st_dir != 0
                and abs(close_px - st_val) / close_px <= 0.002
            )
            mfi = float(indicators.get("mfi", 0.0))
            willr = float(indicators.get("willr", 0.0))
            ema9 = float(indicators.get("ema9", 0.0))
            ema21 = float(indicators.get("ema21", 0.0))
            ema50 = float(indicators.get("ema50", 0.0))
            ema200 = float(indicators.get("ema200", 0.0))
            st_txt = "صاعد ↑" if st_dir == 1 else ("هابط ↓" if st_dir == -1 else "—")
            self.row_extra.setText(
                f"{self.current_symbol} — Supertrend: {format_price(st_val)} ({st_txt}) | MFI: {mfi:.1f} | Williams %R: {willr:.1f} "
                f"EMA9={format_price(ema9)} | EMA21={format_price(ema21)} | EMA50={format_price(ema50)} | EMA200={format_price(ema200)}"
            )
            if st_dir == 1:
                self.row_extra.setStyleSheetForLabel("color: #ffaa44;" if near_st else "color: #00cc66;")
            elif st_dir == -1:
                self.row_extra.setStyleSheetForLabel("color: #ffaa44;" if near_st else "color: #ff5555;")
            else:
                self.row_extra.setStyleSheetForLabel("color: #cccccc;")

            pv = float(indicators.get("pivot", 0.0))
            r1 = float(indicators.get("pivot_r1", 0.0))
            r2 = float(indicators.get("pivot_r2", 0.0))
            s1 = float(indicators.get("pivot_s1", 0.0))
            s2 = float(indicators.get("pivot_s2", 0.0))
            self.row_levels.setText(
                f"{self.current_symbol} — Pivot={format_price(pv)} | R1={format_price(r1)} R2={format_price(r2)} | S1={format_price(s1)} S2={format_price(s2)}"
            )

        except Exception as e:
            print("IndicatorsPanel update error:", e)

    # ----------------------------------------------------
    # تغيير العملة
    # ----------------------------------------------------
    def change_symbol(self, symbol: str):
        self.current_symbol = symbol
        self.row_macd.setText(f"{symbol} — MACD: -")
        self.row_rsi.setText(f"{symbol} — RSI: -")
        self.row_bb.setText(f"{symbol} — Bollinger Bands: -")
        self.row_pro.setText(f"{symbol} — Pro Indicators: -")
        self.row_extra.setText(f"{symbol} — Supertrend / MFI / Williams %R / EMAs: -")
        self.row_levels.setText(f"{symbol} — Levels: -")
        print(f"IndicatorsPanel switched to symbol: {symbol}")
