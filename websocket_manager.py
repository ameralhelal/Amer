import json
import logging
import threading
import time
import numpy as np
import requests
from websocket import WebSocketApp
import pandas as pd

from candlestick_patterns import (
    build_multi_timeframe_pattern_report,
    detect_all as detect_candlestick_patterns,
)
from fear_greed import get_crypto_fear_greed_index

log = logging.getLogger("trading.ws")


# ============================================================
#   STREAM لكل فريم
# ============================================================
class FrameStream:
    def __init__(self, symbol, interval):
        self.symbol = symbol.lower()
        self.interval = interval

        self.ws_kline = None
        self.ws_ticker = None

        self.candles = []      # [(o,h,l,c,v,t_ms), ...] — t_ms = وقت فتح الشمعة (Binance ms)
        self.last_price = 0.0
        self._ws_manager = None  # WebSocketManager — لقراءة شموع فريمات أخرى (أنماط متعددة)

        # Callbacks (يتم ضبطها من WebSocketManager)
        # ملاحظة: هذه ترسل للواجهة بدون interval
        self.on_price = None              # (price)
        self.on_candles = None            # (candles)
        self.on_indicators = None         # (indicators)
        self.on_market_info = None        # (info)

        self.running = False
        self._last_indicator_run_time = 0.0  # لتحديث التحليل أثناء الشمعة (كل ~5 ثوانٍ — لحظي)
        self._indicators_inflight = False  # منع تكدّس خيوط حساب المؤشرات عند ضغط الرسائل
        self._last_candles_emit_time = 0.0  # تقليل ضغط إعادة رسم الشارت أثناء الشمعة المفتوحة

        # لا نحمّل التاريخ هنا حتى لا نجمّد الواجهة — التحميل يتم في خيط run_kline

    # --------------------------------------------------------
    # تحميل الشموع التاريخية (يُستدعى من خيط الخلفية)
    # --------------------------------------------------------
    def load_historical(self, lightweight: bool = False):
        """lightweight=True: للماسح/تحليل الدفعي فقط — بدون أنماط شموع ولا Fear&Greed (لا يُستخدم لشارتك الرئيسي)."""
        try:
            url = "https://api.binance.com/api/v3/klines"
            params = {
                "symbol": self.symbol.upper(),
                "interval": self.interval,
                "limit": 500
            }
            data = requests.get(url, params=params, timeout=15).json()

            self.candles = [
                (
                    float(c[1]),  # open
                    float(c[2]),  # high
                    float(c[3]),  # low
                    float(c[4]),  # close
                    float(c[5]),  # volume
                    int(c[0]),   # open time (ms) — للمحور الزمني والتلميح
                )
                for c in data
            ]
            if self.candles:
                self.last_price = float(self.candles[-1][3])

            if self.on_candles:
                self.on_candles(self.candles)

            self.compute_market_info()
            self.compute_indicators(lightweight=lightweight)

            log.info("Loaded %d candles for %s %s", len(self.candles), self.symbol, self.interval)

        except Exception:
            log.exception("Historical load error")

    # --------------------------------------------------------
    # تشغيل
    # --------------------------------------------------------
    def start(self):
        if self.running:
            return
        self.running = True

        threading.Thread(target=self.run_kline, daemon=True).start()
        threading.Thread(target=self.run_ticker, daemon=True).start()

    def stop(self):
        self.running = False
        try:
            if self.ws_kline:
                self.ws_kline.close()
            if self.ws_ticker:
                self.ws_ticker.close()
        except Exception:
            pass

    # --------------------------------------------------------
    # WebSocket للكلاين (مع إعادة اتصال تلقائي)
    # --------------------------------------------------------
    def run_kline(self):
        # تحميل الشموع التاريخية في الخلفية حتى تظهر النافذة فوراً
        self.load_historical()

        url = f"wss://stream.binance.com:9443/ws/{self.symbol}@kline_{self.interval}"
        wait = 1.0
        max_wait = 60.0

        def on_message(ws, message):
            data = json.loads(message)
            k = data["k"]
            candle = (
                float(k["o"]),
                float(k["h"]),
                float(k["l"]),
                float(k["c"]),
                float(k["v"]),
                int(k["t"]),
            )
            # وقت فتح الشمعة (ms) — لمقارنة التحديث مع آخر صف؛ شمعة جديدة = إلحاق لا استبدال
            t_open = int(k["t"])
            if k["x"]:
                if self.candles and self.candles[-1][5] == t_open:
                    self.candles[-1] = candle  # إغلاق نفس الشمعة التي كانت تُحدَّث بـ x=false
                else:
                    self.candles.append(candle)
                    if len(self.candles) > 1000:
                        self.candles.pop(0)
                if self.on_candles:
                    self.on_candles(self.candles)
                # حساب المؤشرات في خيط منفصل مع حارس يمنع تكدّس خيوط متوازية.
                if not self._indicators_inflight:
                    self._indicators_inflight = True
                    def _compute():
                        try:
                            self._last_indicator_run_time = time.time()
                            self.compute_market_info()
                            self.compute_indicators()
                        finally:
                            self._indicators_inflight = False
                    threading.Thread(target=_compute, daemon=True).start()
            else:
                # تحديث الشمعة الحالية (غير المغلقة) لعرض فوري وتقليل التأخير
                if not self.candles:
                    self.candles.append(candle)
                    if len(self.candles) > 1000:
                        self.candles.pop(0)
                elif self.candles[-1][5] == t_open:
                    self.candles[-1] = candle
                else:
                    # بدء فترة جديدة: لا نستبدل الشمعة المغلقة السابقة
                    self.candles.append(candle)
                    if len(self.candles) > 1000:
                        self.candles.pop(0)
                self.last_price = float(candle[3])
                if self.on_price:
                    self.on_price(self.last_price)
                # throttle: أثناء الشمعة غير المغلقة نرسل الشموع بوتيرة مضبوطة لتخفيف ضغط الواجهة.
                now = time.time()
                # أسرع قليلاً من قبل لتجنب الإحساس بالتجمّد في الشارت
                if self.on_candles and (now - self._last_candles_emit_time >= 0.18):
                    self._last_candles_emit_time = now
                    self.on_candles(self.candles)
                # تحديث المؤشرات أثناء الشمعة بفاصل أقصر (~0.8s). الحد ≥20 يوافق compute_indicators (كان 60 فيجمّد لوحة التوصية دقائق/ساعات).
                if (
                    now - self._last_indicator_run_time >= 0.8
                    and len(self.candles) >= 20
                    and not self._indicators_inflight
                ):
                    self._last_indicator_run_time = now
                    def _compute_during_candle():
                        try:
                            self._indicators_inflight = True
                            self.compute_market_info()
                            self.compute_indicators()
                        finally:
                            self._indicators_inflight = False
                    threading.Thread(target=_compute_during_candle, daemon=True).start()

        # ping كل 20 ثانية للحفاظ على الاتصال؛ انتهاء صلاحية 10 ثوانٍ إذا لم يُرد pong
        ping_interval, ping_timeout = 20, 10
        while self.running:
            try:
                self.ws_kline = WebSocketApp(url, on_message=on_message)
                connected_at = time.time()
                self.ws_kline.run_forever(ping_interval=ping_interval, ping_timeout=ping_timeout)
                # إذا بقي الاتصال فعّالاً أكثر من دقيقة ثم انقطع، نعيد wait لبداية سريعة
                if time.time() - connected_at > 60:
                    wait = 1.0
            except Exception as e:
                log.warning("Kline WS error %s, reconnect in %.0fs", e, wait)
            if not self.running:
                break
            time.sleep(wait)
            wait = min(max_wait, wait * 2)

    # --------------------------------------------------------
    # WebSocket للسعر (مع إعادة اتصال تلقائي)
    # --------------------------------------------------------
    def run_ticker(self):
        # @trade = سعر آخر صفقة — يطابق ما تعرضه Binance سبوت عادةً (Last Price).
        # miniTicker كان يعطي قيمة قد تختلف قليلاً عن «آخر صفقة» في الواجهة.
        url = f"wss://stream.binance.com:9443/ws/{self.symbol}@trade"
        wait = 1.0
        max_wait = 60.0

        def on_message(ws, message):
            data = json.loads(message)
            if isinstance(data, dict) and isinstance(data.get("data"), dict):
                data = data["data"]
            if not isinstance(data, dict) or "p" not in data:
                return
            price = float(data["p"])
            self.last_price = price
            if self.on_price:
                self.on_price(price)

        ping_interval, ping_timeout = 20, 10
        while self.running:
            try:
                self.ws_ticker = WebSocketApp(url, on_message=on_message)
                connected_at = time.time()
                self.ws_ticker.run_forever(ping_interval=ping_interval, ping_timeout=ping_timeout)
                if time.time() - connected_at > 60:
                    wait = 1.0
            except Exception as e:
                log.warning("Ticker WS error %s, reconnect in %.0fs", e, wait)
            if not self.running:
                break
            time.sleep(wait)
            wait = min(max_wait, wait * 2)

    # --------------------------------------------------------
    # حساب Trend / Volume / Volatility
    # --------------------------------------------------------
    def compute_market_info(self):
        n_trend = 12
        if len(self.candles) < n_trend:
            return

        closes = np.array([c[3] for c in self.candles])
        volumes = np.array([c[4] for c in self.candles])
        # فترة أقصر (12) لرد أسرع على انعكاس الاتجاه
        x = np.arange(n_trend)
        y = np.asarray(closes[-n_trend:], dtype=float)
        slope = float(np.polyfit(x, y, 1)[0])
        # المنحدر الخطي على 12 إغلاقاً يوزّن كل الشموع بالتساوي؛ ارتداد قوي في آخر النصف مع
        # هبوط في أول النافذة يعطي منحدراً سالباً رغم أن العين ترى «صعوداً» حديثاً.
        # ندمج: صعود إذا المنحدر > 0، أو إذا متوسط آخر 6 أغلاق أعلى بوضوح من أول 6.
        half1 = float(np.mean(y[:6]))
        half2 = float(np.mean(y[6:]))
        if slope > 0:
            trend = "UP"
        elif half2 > half1 * 1.0005:
            trend = "UP"
        else:
            trend = "DOWN"

        vol_strength = volumes[-1] / (np.mean(volumes[-20:]) + 1e-9)
        volatility = np.std(closes[-n_trend:])
        last_close = float(closes[-1]) if len(closes) else 1.0
        # تقلب كنسبة مئوية من السعر (أوضح للمقارنة بين العملات)
        volatility_pct = (volatility / (last_close + 1e-9)) * 100.0

        info = {
            "trend": trend,
            "trend_slope_per_bar": slope,
            "trend_mean_first6": half1,
            "trend_mean_last6": half2,
            "volume_strength": float(vol_strength),
            "volatility": float(volatility),
            "volatility_pct": float(volatility_pct),
        }

        if self.on_market_info:
            self.on_market_info(info)

    # --------------------------------------------------------
    # حساب MACD / RSI / BB / MA + مؤشرات احترافية إضافية
    # --------------------------------------------------------
    def _emit_indicators(self, indicators: dict):
        """إرسال المؤشرات للواجهة (من أي خيط)."""
        if self.on_indicators:
            try:
                self.on_indicators(indicators)
            except Exception as e:
                log.debug("on_indicators callback error: %s", e)

    def compute_indicators(self, lightweight: bool = False):
        n = len(self.candles)
        if n < 20:
            return
        highs = np.array([c[1] for c in self.candles], dtype=float)
        lows = np.array([c[2] for c in self.candles], dtype=float)
        closes = np.array([c[3] for c in self.candles], dtype=float)
        volumes = np.array([c[4] for c in self.candles], dtype=float)

        ma20 = np.mean(closes[-20:])
        ma50 = np.mean(closes[-50:])

        std20 = np.std(closes[-20:])
        upper = ma20 + 2 * std20
        lower = ma20 - 2 * std20
        bandwidth = (upper - lower) / ma20

        # بروفايل الحساسية: fast (تفاعلي) أو standard (أهدأ وأكثر ثباتاً)
        try:
            from config import load_config_cached
            _cfg_prof = load_config_cached()
            _ind_prof = str(_cfg_prof.get("indicator_speed_profile", "balanced") or "balanced").strip().lower()
        except Exception:
            _ind_prof = "balanced"
        if _ind_prof == "standard":
            _ind_prof = "balanced"
        if _ind_prof not in ("conservative", "balanced", "fast"):
            _ind_prof = "balanced"
        if _ind_prof == "fast":
            _rsi_per = 5
            _macd_fast, _macd_slow, _macd_sig = 6, 13, 5
            _stoch_rsi_per = 5
            _stoch_len = 5
            _atr_per = 5
            _adx_per = 10
            _stoch_k_smooth = 2
            _stoch_d_smooth = 2
            _cci_per = 14
            _willr_per = 10
            _mfi_per = 10
            _ichi_tenkan_per, _ichi_kijun_per, _ichi_span_b_per = 7, 22, 44
            _st_per, _st_mult = 5, 1.6
        elif _ind_prof == "conservative":
            _rsi_per = 18
            _macd_fast, _macd_slow, _macd_sig = 15, 30, 11
            _stoch_rsi_per = 18
            _stoch_len = 18
            _atr_per = 10
            _adx_per = 18
            _stoch_k_smooth = 4
            _stoch_d_smooth = 4
            _cci_per = 30
            _willr_per = 20
            _mfi_per = 20
            _ichi_tenkan_per, _ichi_kijun_per, _ichi_span_b_per = 12, 34, 68
            _st_per, _st_mult = 10, 2.4
        else:
            # قياسي أقرب لـ TradingView الافتراضي
            _rsi_per = 14
            _macd_fast, _macd_slow, _macd_sig = 12, 26, 9
            _stoch_rsi_per = 14
            _stoch_len = 14
            _atr_per = 7
            _adx_per = 14
            _stoch_k_smooth = 3
            _stoch_d_smooth = 3
            _cci_per = 20
            _willr_per = 14
            _mfi_per = 14
            _ichi_tenkan_per, _ichi_kijun_per, _ichi_span_b_per = 9, 26, 52
            _st_per, _st_mult = 7, 2.0

        # RSI بطريقة Wilder (RMA) — أدق من المتوسط البسيط وآمن من التحيز اللحظي
        try:
            delta = pd.Series(closes).diff()
            gain = delta.clip(lower=0.0)
            loss = (-delta).clip(lower=0.0)
            avg_gain = gain.ewm(alpha=1 / _rsi_per, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1 / _rsi_per, adjust=False).mean() + 1e-9
            rs = avg_gain / avg_loss
            rsi = float((100.0 - (100.0 / (1.0 + rs))).iloc[-1])
            if not np.isfinite(rsi):
                rsi = 50.0
        except Exception:
            rsi = 50.0

        s = pd.Series(closes)
        ema_fast = s.ewm(span=_macd_fast, adjust=False).mean()
        ema_slow = s.ewm(span=_macd_slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=_macd_sig, adjust=False).mean()
        histogram = macd_line - signal_line

        # -----------------------------
        # VWAP (Volume Weighted Average Price)
        # -----------------------------
        try:
            typical = (highs + lows + closes) / 3.0
            cum_vol = np.cumsum(volumes)
            cum_tpv = np.cumsum(typical * volumes)
            vwap = float(cum_tpv[-1] / (cum_vol[-1] + 1e-9))
        except Exception:
            vwap = 0.0

        # -----------------------------
        # ATR — مربوط ببروفايل الحساسية (FAST/STD)
        # -----------------------------
        atr = 0.0
        try:
            prev_close = np.roll(closes, 1)
            prev_close[0] = closes[0]
            tr1 = highs - lows
            tr2 = np.abs(highs - prev_close)
            tr3 = np.abs(lows - prev_close)
            tr = np.maximum(tr1, np.maximum(tr2, tr3))
            atr = float(pd.Series(tr).ewm(alpha=1/_atr_per, adjust=False).mean().iloc[-1])
        except Exception:
            atr = 0.0

        # -----------------------------
        # ADX (+DI/-DI) — مربوط ببروفايل الحساسية (FAST/STD)
        # -----------------------------
        adx = 0.0
        plus_di = 0.0
        minus_di = 0.0
        try:
            _adx_per = max(2, int(_adx_per))
            up_move = highs[1:] - highs[:-1]
            down_move = lows[:-1] - lows[1:]
            plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
            minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
            prev_close2 = closes[:-1]
            tr1 = highs[1:] - lows[1:]
            tr2 = np.abs(highs[1:] - prev_close2)
            tr3 = np.abs(lows[1:] - prev_close2)
            tr = np.maximum(tr1, np.maximum(tr2, tr3))
            _alpha = 1.0 / float(_adx_per)
            tr_s = pd.Series(tr).ewm(alpha=_alpha, adjust=False).mean()
            plus_s = pd.Series(plus_dm).ewm(alpha=_alpha, adjust=False).mean()
            minus_s = pd.Series(minus_dm).ewm(alpha=_alpha, adjust=False).mean()
            plus_di_series = 100 * (plus_s / (tr_s + 1e-9))
            minus_di_series = 100 * (minus_s / (tr_s + 1e-9))
            dx = (100 * (np.abs(plus_di_series - minus_di_series) / (plus_di_series + minus_di_series + 1e-9))).fillna(0)
            adx = float(pd.Series(dx).ewm(alpha=_alpha, adjust=False).mean().iloc[-1])
            plus_di = float(plus_di_series.iloc[-1])
            minus_di = float(minus_di_series.iloc[-1])
        except Exception:
            adx, plus_di, minus_di = 0.0, 0.0, 0.0

        # -----------------------------
        # Stochastic RSI — profile dependent + smoothing (K=3, D=3)
        # -----------------------------
        stoch_rsi_k = 0.0
        stoch_rsi_d = 0.0
        try:
            delta2 = pd.Series(closes).diff()
            gain2 = delta2.clip(lower=0)
            loss2 = (-delta2).clip(lower=0)
            avg_gain2 = gain2.ewm(alpha=1/_stoch_rsi_per, adjust=False).mean()
            avg_loss2 = loss2.ewm(alpha=1/_stoch_rsi_per, adjust=False).mean() + 1e-9
            rs2 = avg_gain2 / avg_loss2
            rsi_series = 100 - (100 / (1 + rs2))
            rsi_min = rsi_series.rolling(_stoch_len).min()
            rsi_max = rsi_series.rolling(_stoch_len).max()
            stoch_raw = ((rsi_series - rsi_min) / (rsi_max - rsi_min + 1e-9)) * 100.0
            k_line = stoch_raw.rolling(_stoch_k_smooth).mean()
            d_line = k_line.rolling(_stoch_d_smooth).mean()
            stoch_rsi_k = float(k_line.iloc[-1])
            stoch_rsi_d = float(d_line.iloc[-1])
            if not np.isfinite(stoch_rsi_k):
                stoch_rsi_k = 50.0
            if not np.isfinite(stoch_rsi_d):
                stoch_rsi_d = 50.0
            stoch_rsi_k = float(max(0.0, min(100.0, stoch_rsi_k)))
            stoch_rsi_d = float(max(0.0, min(100.0, stoch_rsi_d)))
        except Exception:
            # 0/0 يُفسَّر في الواجهة كـ D>K دائماً (أحمر)؛ المحايد 50/50
            stoch_rsi_k, stoch_rsi_d = 50.0, 50.0

        # -----------------------------
        # OBV (On-Balance Volume)
        # -----------------------------
        obv = 0.0
        try:
            direction = np.sign(np.diff(closes))
            obv_series = np.zeros(len(closes))
            for i in range(1, len(closes)):
                if direction[i-1] > 0:
                    obv_series[i] = obv_series[i-1] + volumes[i]
                elif direction[i-1] < 0:
                    obv_series[i] = obv_series[i-1] - volumes[i]
                else:
                    obv_series[i] = obv_series[i-1]
            obv = float(obv_series[-1])
        except Exception:
            obv = 0.0

        # -----------------------------
        # Ichimoku (Tenkan/Kijun/Senkou A/B) - current values
        # -----------------------------
        tenkan = 0.0
        kijun = 0.0
        senkou_a = 0.0
        senkou_b = 0.0
        try:
            if len(closes) >= _ichi_span_b_per:
                tenkan = float((np.max(highs[-_ichi_tenkan_per:]) + np.min(lows[-_ichi_tenkan_per:])) / 2.0)
                kijun = float((np.max(highs[-_ichi_kijun_per:]) + np.min(lows[-_ichi_kijun_per:])) / 2.0)
                senkou_a = float((tenkan + kijun) / 2.0)
                senkou_b = float((np.max(highs[-_ichi_span_b_per:]) + np.min(lows[-_ichi_span_b_per:])) / 2.0)
        except Exception:
            tenkan, kijun, senkou_a, senkou_b = 0.0, 0.0, 0.0, 0.0

        # -----------------------------
        # Pivot Points (Classic)
        #
        # ملاحظة مهمة:
        # حساب Pivot من "الشمعة السابقة" على فريم 1m/5m يجعل مستويات S/R قريبة جداً (غير مفيدة بصرياً).
        # لذلك نحسبها من نطاق أقرب لـ "يوم تداول" حسب الفريم (High/Low) مع إغلاق آخر شمعة مكتملة.
        # -----------------------------
        pivot = r1 = r2 = r3 = r4 = s1 = s2 = s3 = s4 = 0.0
        try:
            if len(closes) >= 2:
                # عدد شموع تقريبي يغطي 24 ساعة حسب الفريم الحالي
                _per_day = {
                    "1m": 1440,
                    "5m": 288,
                    "15m": 96,
                    "1h": 24,
                    "4h": 6,
                    "1d": 2,  # يومي: نستخدم آخر يومين لضمان وجود شمعة مكتملة
                }.get(getattr(self, "interval", "1m"), 1440)
                win = int(max(2, min(len(closes), _per_day)))
                ph = float(np.max(highs[-win:]))
                pl = float(np.min(lows[-win:]))
                # إغلاق آخر شمعة مكتملة (تجنب الشمعة الحالية غير المكتملة)
                pc = float(closes[-2])
                pivot = (ph + pl + pc) / 3.0
                r1 = 2 * pivot - pl
                s1 = 2 * pivot - ph
                r2 = pivot + (ph - pl)
                s2 = pivot - (ph - pl)
                r3 = pivot + 2 * (ph - pl)
                s3 = pivot - 2 * (ph - pl)
                r4 = pivot + 3 * (ph - pl)
                s4 = pivot - 3 * (ph - pl)
        except Exception:
            pivot = r1 = r2 = r3 = r4 = s1 = s2 = s3 = s4 = 0.0

        # -----------------------------
        # CCI (Commodity Channel Index) — مربوط ببروفايل الحساسية
        # -----------------------------
        cci = 0.0
        try:
            tp = (highs + lows + closes) / 3.0
            tp_s = pd.Series(tp)
            sma_tp = tp_s.rolling(_cci_per).mean()
            mad = (tp_s - sma_tp).abs().rolling(_cci_per).mean()
            cci = float(((tp_s - sma_tp) / (0.015 * (mad + 1e-9))).iloc[-1])
        except Exception:
            cci = 0.0

        # -----------------------------
        # EMAs (9, 21, 50, 200) — للمرجع والاتجاه
        # -----------------------------
        ema9 = ema21 = ema50 = ema200 = 0.0
        try:
            s = pd.Series(closes)
            if len(closes) >= 9:
                ema9 = float(s.ewm(span=9, adjust=False).mean().iloc[-1])
            if len(closes) >= 21:
                ema21 = float(s.ewm(span=21, adjust=False).mean().iloc[-1])
            if len(closes) >= 50:
                ema50 = float(s.ewm(span=50, adjust=False).mean().iloc[-1])
            if len(closes) >= 200:
                ema200 = float(s.ewm(span=200, adjust=False).mean().iloc[-1])
        except Exception:
            pass

        # -----------------------------
        # Williams %R — مربوط ببروفايل الحساسية
        # -----------------------------
        willr = 0.0
        try:
            if len(closes) >= _willr_per:
                hh = np.max(highs[-_willr_per:])
                ll = np.min(lows[-_willr_per:])
                rng = hh - ll
                if rng > 1e-9:
                    willr = -100.0 * (hh - closes[-1]) / rng
                else:
                    willr = -50.0
        except Exception:
            willr = 0.0

        # -----------------------------
        # MFI (Money Flow Index) — مربوط ببروفايل الحساسية
        # -----------------------------
        mfi = 0.0
        try:
            if len(closes) >= _mfi_per + 1:
                typical = (highs + lows + closes) / 3.0
                raw_mf = typical * volumes
                pos_mf = 0.0
                neg_mf = 0.0
                for i in range(-_mfi_per, 0):
                    if typical[i] > typical[i - 1]:
                        pos_mf += raw_mf[i]
                    elif typical[i] < typical[i - 1]:
                        neg_mf += raw_mf[i]
                if neg_mf > 1e-9:
                    mfi = 100.0 - (100.0 / (1.0 + pos_mf / neg_mf))
                else:
                    mfi = 100.0 if pos_mf > 0 else 50.0
        except Exception:
            mfi = 0.0

        # -----------------------------
        # Supertrend — مربوط ببروفايل الحساسية (FAST/STD)
        # لا نستخدم == بين floats لتحديد النطاق السابق (كان يسبب بقاء الهبوط دائماً تقريباً).
        # -----------------------------
        supertrend_val = 0.0
        supertrend_dir = 0  # 1 = صاعد، -1 = هابط
        try:
            _st_per = max(2, int(_st_per))
            _st_mult = max(0.5, float(_st_mult))
            if len(closes) >= _st_per + 1:
                hl2 = (highs + lows) / 2.0
                tr_st = np.maximum(
                    highs - lows,
                    np.maximum(
                        np.abs(highs - np.roll(closes, 1)),
                        np.abs(lows - np.roll(closes, 1)),
                    ),
                )
                tr_st[0] = highs[0] - lows[0]
                atr_st = pd.Series(tr_st).ewm(alpha=1.0 / float(_st_per), adjust=False).mean()
                basic_upper = hl2 + _st_mult * atr_st.values
                basic_lower = hl2 - _st_mult * atr_st.values
                n = len(closes)
                final_upper = np.zeros(n)
                final_lower = np.zeros(n)
                supertrend_arr = np.zeros(n)
                final_upper[0] = basic_upper[0]
                final_lower[0] = basic_lower[0]
                supertrend_arr[0] = basic_upper[0]
                trend = -1
                prev_on_upper = True
                for i in range(1, n):
                    if basic_upper[i] < final_upper[i - 1] or closes[i - 1] > final_upper[i - 1]:
                        final_upper[i] = basic_upper[i]
                    else:
                        final_upper[i] = final_upper[i - 1]
                    if basic_lower[i] > final_lower[i - 1] or closes[i - 1] < final_lower[i - 1]:
                        final_lower[i] = basic_lower[i]
                    else:
                        final_lower[i] = final_lower[i - 1]
                    if prev_on_upper:
                        if closes[i] <= final_upper[i]:
                            supertrend_arr[i] = final_upper[i]
                            trend = -1
                            prev_on_upper = True
                        else:
                            supertrend_arr[i] = final_lower[i]
                            trend = 1
                            prev_on_upper = False
                    else:
                        if closes[i] >= final_lower[i]:
                            supertrend_arr[i] = final_lower[i]
                            trend = 1
                            prev_on_upper = False
                        else:
                            supertrend_arr[i] = final_upper[i]
                            trend = -1
                            prev_on_upper = True
                supertrend_val = float(supertrend_arr[-1])
                supertrend_dir = int(trend)
        except Exception:
            pass

        prev_close = float(closes[-2]) if len(closes) >= 2 else 0.0
        prev_low = float(lows[-2]) if len(lows) >= 2 else 0.0
        prev_high = float(highs[-2]) if len(highs) >= 2 else 0.0

        last_candle_open = float(self.candles[-1][0]) if self.candles else 0.0
        last_candle_high = float(highs[-1])
        last_candle_low = float(lows[-1])
        last_candle_close = float(closes[-1])
        candle_body_pct = 0.0
        candle_body_bullish = 0.0
        try:
            if abs(last_candle_open) > 1e-12:
                candle_body_pct = (last_candle_close - last_candle_open) / last_candle_open * 100.0
            candle_body_bullish = 1.0 if last_candle_close >= last_candle_open else 0.0
        except Exception:
            pass

        _hist_last = float(histogram.iloc[-1])
        try:
            _hist_prev = float(histogram.iloc[-2]) if len(histogram) >= 2 else _hist_last
        except (TypeError, IndexError, ValueError):
            _hist_prev = _hist_last

        # أعلى سعر في آخر N شمعة + المسافة تحته بالـ% — لفلتر «فتح المركز» (تجنب الشراء عند ذروة محلية)
        _win_high_lb = min(40, len(highs))
        try:
            _wh_recent = float(np.max(highs[-_win_high_lb:])) if _win_high_lb >= 1 else float(highs[-1])
            _cl = float(closes[-1])
            _pct_below_wh = ((_wh_recent - _cl) / _wh_recent * 100.0) if _wh_recent > 1e-12 else 100.0
        except Exception:
            _wh_recent = float(highs[-1]) if len(highs) else 0.0
            _pct_below_wh = 100.0

        _win_low_lb = min(40, len(lows))
        try:
            _wl_recent = float(np.min(lows[-_win_low_lb:])) if _win_low_lb >= 1 else float(lows[-1])
            _cl_wl = float(closes[-1])
            _pct_above_wl = ((_cl_wl - _wl_recent) / _wl_recent * 100.0) if _wl_recent > 1e-12 else 100.0
        except Exception:
            _wl_recent = float(lows[-1]) if len(lows) else 0.0
            _pct_above_wl = 100.0

        _t_open_ms = int(self.candles[-1][5]) if self.candles else 0
        try:
            if n > 2:
                _sl = max(0, min(24, n - 1))
                closed_closes_tail = [float(x) for x in closes[-(_sl + 1) : -1]]
            else:
                closed_closes_tail = []
        except Exception:
            closed_closes_tail = []
        indicators = {
            "close": float(closes[-1]),
            "last_candle_open_ms": _t_open_ms,
            "closed_closes_tail": closed_closes_tail,
            "prev_close": prev_close,
            "prev_low": prev_low,
            "prev_high": prev_high,
            "ma20": float(ma20),
            "ma50": float(ma50),
            "bb_upper": float(upper),
            "bb_middle": float(ma20),
            "bb_lower": float(lower),
            "bb_width": float(bandwidth),
            "rsi": float(rsi),
            "macd": float(macd_line.iloc[-1]),
            "signal": float(signal_line.iloc[-1]),
            "hist": _hist_last,
            "hist_prev": _hist_prev,
            "window_high_recent": float(_wh_recent),
            "pct_below_window_high": float(_pct_below_wh),
            "window_low_recent": float(_wl_recent),
            "pct_above_window_low": float(_pct_above_wl),
            # Professional indicators
            "vwap": float(vwap),
            "atr14": float(atr),
            "adx14": float(adx),
            "plus_di14": float(plus_di),
            "minus_di14": float(minus_di),
            "stoch_rsi_k": float(stoch_rsi_k),
            "stoch_rsi_d": float(stoch_rsi_d),
            "obv": float(obv),
            "ichimoku_tenkan": float(tenkan),
            "ichimoku_kijun": float(kijun),
            "ichimoku_senkou_a": float(senkou_a),
            "ichimoku_senkou_b": float(senkou_b),
            "pivot": float(pivot),
            "pivot_r1": float(r1),
            "pivot_r2": float(r2),
            "pivot_s1": float(s1),
            "pivot_s2": float(s2),
            "pivot_r3": float(r3),
            "pivot_s3": float(s3),
            "pivot_r4": float(r4),
            "pivot_s4": float(s4),
            "cci20": float(cci),
            "volume_strength": float(volumes[-1] / (np.mean(volumes[-20:]) + 1e-9)),
            # مؤشرات مضافة: EMAs، Williams %R، MFI، Supertrend
            "ema9": float(ema9),
            "ema21": float(ema21),
            "ema50": float(ema50),
            "ema200": float(ema200),
            "willr": float(willr),
            "mfi": float(mfi),
            "supertrend": float(supertrend_val),
            "supertrend_dir": int(supertrend_dir),
            "last_candle_open": float(last_candle_open),
            "last_candle_high": float(last_candle_high),
            "last_candle_low": float(last_candle_low),
            "last_candle_close": float(last_candle_close),
            "candle_body_pct": float(candle_body_pct),
            "candle_body_bullish": float(candle_body_bullish),
        }

        # أنماط الشموع اليابانية (دوجي، مطرقة، ابتلاع، هارامي، نجمة الصباح/المساء، إلخ)
        indicators["chart_interval"] = str(self.interval)
        if lightweight:
            indicators["candle_pattern_score"] = 0
            indicators["candle_pattern_bullish"] = []
            indicators["candle_pattern_bearish"] = []
            indicators["candle_pattern_neutral"] = []
            indicators["candle_pattern_summary"] = ""
            indicators["candle_pattern_mtf_rows"] = []
            indicators["indicator_speed_profile"] = str(_ind_prof)
        else:
            try:
                _mgr = getattr(self, "_ws_manager", None)
                if _mgr is not None and getattr(_mgr, "frames", None):
                    pat = build_multi_timeframe_pattern_report(
                        self.candles,
                        str(self.interval),
                        candles_4h=_mgr.frames["4h"].candles,
                        candles_1h=_mgr.frames["1h"].candles,
                        candles_15m=_mgr.frames["15m"].candles,
                        sensitivity_profile=_ind_prof,
                    )
                else:
                    pat = detect_candlestick_patterns(
                        self.candles,
                        interval=self.interval,
                        sensitivity_profile=_ind_prof,
                    )
                indicators["candle_pattern_score"] = int(pat.get("score", 0))
                indicators["candle_pattern_bullish"] = list(pat.get("bullish", []))
                indicators["candle_pattern_bearish"] = list(pat.get("bearish", []))
                indicators["candle_pattern_neutral"] = list(pat.get("neutral", []))
                indicators["candle_pattern_summary"] = str(pat.get("summary", ""))
                _mr = pat.get("candle_pattern_mtf_rows")
                indicators["candle_pattern_mtf_rows"] = list(_mr) if isinstance(_mr, list) else []
                indicators["indicator_speed_profile"] = str(_ind_prof)
            except Exception as e:
                log.debug("Candlestick pattern detection failed: %s", e)
                indicators["candle_pattern_score"] = 0
                indicators["candle_pattern_bullish"] = []
                indicators["candle_pattern_bearish"] = []
                indicators["candle_pattern_neutral"] = []
                indicators["candle_pattern_summary"] = ""
                indicators["candle_pattern_mtf_rows"] = []

        if not lightweight:
            try:
                fg = get_crypto_fear_greed_index()
                if fg is not None:
                    indicators["fear_greed_index"] = int(fg["value"])
                    indicators["fear_greed_classification"] = str(fg.get("classification", ""))
            except Exception:
                pass
        try:
            _mfi = float(indicators.get("mfi") or 50.0)
            _vs = float(indicators.get("volume_strength") or 1.0)
            _vpart = max(0.0, min(100.0, 50.0 + (_vs - 1.0) * 35.0))
            indicators["buy_pressure_score"] = round(0.55 * _mfi + 0.45 * _vpart, 1)
        except Exception:
            pass

        self._emit_indicators(indicators)


# ============================================================
#   MANAGER يدير كل الفريمات
# ============================================================
class WebSocketManager:
    def __init__(self, symbol: str):
        self.symbol = symbol.lower()
        self._active_frame: FrameStream | None = None
        self._user_on_price = None
        self._last_price_push_time: float = 0.0
        self._poll_running: bool = False
        self._price_poll_thread: threading.Thread | None = None

        # فريمات متعددة (جاهزة للمستقبل)
        self.frames = {
            "1m": FrameStream(self.symbol, "1m"),
            "5m": FrameStream(self.symbol, "5m"),
            "15m": FrameStream(self.symbol, "15m"),
            "1h": FrameStream(self.symbol, "1h"),
            "4h": FrameStream(self.symbol, "4h"),
            "1d": FrameStream(self.symbol, "1d"),
        }
        for _fs in self.frames.values():
            _fs._ws_manager = self

    # --------------------------------------------------------
    # start / stop
    # --------------------------------------------------------
    def start(self):
        for f in self.frames.values():
            f.start()

    def stop(self):
        self._poll_running = False
        for f in self.frames.values():
            f.stop()

    def refresh_indicators_all_frames(self) -> None:
        """إعادة حساب المؤشرات وحالة السوق لكل الفريمات بعد تغيير بروفايل الحساسية (منخفض/متوسط/عالي) في الإعدادات."""
        def _run():
            for f in list(self.frames.values()):
                try:
                    if len(getattr(f, "candles", []) or []) < 20:
                        continue
                    f.compute_market_info()
                    f.compute_indicators()
                except Exception as exc:
                    log.debug("refresh_indicators_all_frames %s: %s", getattr(f, "interval", "?"), exc)

        threading.Thread(target=_run, daemon=True).start()

    def _start_binance_spot_price_poll(self) -> None:
        """عند هدوء @trade يُستطلع نفس سعر REST الرسمي (ticker/price) كما في موقع Binance."""
        if self._price_poll_thread is not None and self._price_poll_thread.is_alive():
            return
        self._poll_running = True

        def _loop():
            first = True
            while self._poll_running:
                time.sleep(1.5 if first else 4.0)
                first = False
                if not self._poll_running:
                    break
                if time.time() - self._last_price_push_time < 3.0:
                    continue
                wf = self._active_frame
                cb = self._user_on_price
                if wf is None or cb is None:
                    continue
                try:
                    r = requests.get(
                        "https://api.binance.com/api/v3/ticker/price",
                        params={"symbol": self.symbol.upper()},
                        timeout=6,
                    )
                    r.raise_for_status()
                    p = float(r.json().get("price") or 0)
                    if p > 0:
                        wf.last_price = p
                        self._last_price_push_time = time.time()
                        cb(p)
                except Exception as e:
                    log.debug("Binance spot price poll: %s", e)

        self._price_poll_thread = threading.Thread(target=_loop, daemon=True)
        self._price_poll_thread.start()

    # --------------------------------------------------------
    # set_callbacks (ربط فريم معيّن بالواجهة: 1m, 5m, 15m, 1h, 4h, 1d)
    # --------------------------------------------------------
    def set_callbacks(self, interval: str, on_price, on_candles, on_indicators, on_market_info):
        prev = getattr(self, "_active_frame", None)
        f = self.frames.get(interval)
        if not f:
            interval = "1m"
            f = self.frames["1m"]
        if prev is not None and prev is not f:
            prev.on_price = None
            prev.on_candles = None
            prev.on_indicators = None
            prev.on_market_info = None
        self._active_frame = f
        self._user_on_price = on_price

        def _wrapped_price(p: float) -> None:
            self._last_price_push_time = time.time()
            on_price(p)

        f.on_price = _wrapped_price
        f.on_candles = on_candles
        f.on_indicators = on_indicators
        f.on_market_info = on_market_info
        self._start_binance_spot_price_poll()
