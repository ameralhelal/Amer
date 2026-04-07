# جسر بدون واجهة: حساب أسعار مثلثات التوصية على الشارت + الاستراتيجية المقترحة
# (كان سابقاً لوحة «ملخص الذكاء»؛ أُزيلت من المركز ويبقى المنطق للبوت والشارت)
from PyQt6.QtCore import QObject, pyqtSignal

from recommendation_analysis_text import build_recommendation_analysis_result


class ChartRecommendationBridge(QObject):
    recommendation_prices_updated = pyqtSignal(object, object)
    suggested_strategy_updated = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.last_price = 0.0
        self._last_official_rec = None
        self._last_market_info: dict = {}
        self._last_rec_buy_price = None
        self._last_rec_sell_price = None
        self._last_interval = "1m"

    def set_official_recommendation(self, rec: str):
        if rec and str(rec).strip().upper() in ("BUY", "SELL", "WAIT"):
            self._last_official_rec = str(rec).strip().upper()

    def set_market_info_snapshot(self, info: dict):
        self._last_market_info = dict(info) if isinstance(info, dict) else {}

    def update_price(self, *args):
        if len(args) == 1:
            _, price = "1m", args[0]
        else:
            _, price = args[0], args[1]
        self.last_price = float(price) if price is not None else 0.0
        if self._last_rec_buy_price is not None or self._last_rec_sell_price is not None:
            self.recommendation_prices_updated.emit(self._last_rec_buy_price, self._last_rec_sell_price)

    def update_indicators(self, interval, indicators):
        self._last_interval = str(interval or "1m")
        self.update_recommendation(self.last_price, indicators)

    def update_recommendation(self, price, indicators):
        if not indicators:
            self.suggested_strategy_updated.emit("")
            return
        interval = self._last_interval
        res = build_recommendation_analysis_result(
            float(price or 0),
            indicators,
            self._last_market_info,
            interval,
        )
        if res is None:
            self.suggested_strategy_updated.emit("")
            return
        self.suggested_strategy_updated.emit(res.suggested_key)

        rec = res.rec_ar
        close = res.close
        s1 = res.s1
        r1 = res.r1
        atr = res.atr
        rec_buy_price = None
        rec_sell_price = None
        if close > 0:
            if rec in ("شراء", "شراء قوي"):
                rec_buy_price = s1 if s1 > 0 else (close - atr * 0.5 if atr > 0 else close)
                rec_sell_price = r1 if r1 > 0 else (close + atr * 1.2 if atr > 0 else close * 1.02)
            elif rec in ("بيع", "بيع قوي"):
                rec_sell_price = r1 if r1 > 0 else close
                if s1 > 0 and s1 < close:
                    rec_buy_price = s1
            else:
                if atr > 0:
                    rec_buy_price = close - atr * 0.3
                    rec_sell_price = close + atr * 0.8
                else:
                    rec_buy_price = close * 0.998
                    rec_sell_price = close * 1.002
        self._last_rec_buy_price = rec_buy_price
        self._last_rec_sell_price = rec_sell_price
        self.recommendation_prices_updated.emit(rec_buy_price, rec_sell_price)
