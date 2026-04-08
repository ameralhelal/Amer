import json
import logging
import os
import threading
from html import escape as html_escape
import time
import requests
from collections import deque
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QGroupBox, QLabel, QMessageBox, QComboBox,
    QMenu, QApplication, QDialog, QListWidget, QListWidgetItem, QDialogButtonBox,
    QSizePolicy, QFrame, QScrollArea,
)
from PyQt6.QtCore import Qt, pyqtSignal, pyqtSlot, QTimer, QObject, QThread
from PyQt6.QtGui import QColor, QFont
from api_settings_window import open_api_settings_window, get_decrypted_credentials, request_unlock_or_set_password, load_etoro_settings
from risk_settings_window import RiskSettingsWindow
from quick_settings_dialogs import (
    AmountDialog,
    LeverageDialog,
    TPDialog,
    SLDialog,
    LimitBuyDialog,
    MaxTradesDialog,
    MarketTypeDialog,
)
from trade_history import (
    record as record_trade,
    patch_last_sell_pnl,
    get_last_buy_price_for_symbol,
    get_last_buy_info_for_symbol,
    append_sell_forced,
    get_last_closed_trade_pnl,
    suspect_placeholder_entry_price,
    count_consecutive_losses,
    backfill_missing_buys_from_etoro_positions,
)
from recommendation_log import (
    log_recommendation,
    record_bot_buy,
    record_bot_sell_outcome,
    apply_learning_step,
)
from config import (
    load_config,
    load_config_cached,
    save_config,
    get_circuit_breaker_config,
    invalidate_config_disk_cache,
)
from format_utils import format_price
from translations import tr, tr_en, get_language
from ai_panel import AIPanel
from theme_loader import apply_theme
from telegram_notifier import send_trade_notification
from exchange_binance import SpotClient as BinanceSpotClient, FuturesClient as BinanceFuturesClient
from exchange_bitget import SpotClient as BitgetSpotClient, FuturesClient as BitgetFuturesClient
from exchange_etoro import (
    EtoroBalanceBreakdown,
    SpotClient as EtoroSpotClient,
    FuturesClient as EtoroFuturesClient,
    _etoro_pos_entry_units,
    etoro_extract_position_id,
    etoro_extract_open_order_id,
    etoro_row_from_pnl_item,
    etoro_row_from_pnl_item_minimal,
)
from candlestick_patterns import pattern_to_ar
from market_status_readout import market_readout_thresholds
from composite_signal import compute_composite_signal
from ui_palette import (
    TOP_PANEL_BG,
    TOP_PANEL_BORDER,
    TOP_PANEL_RADIUS,
    TOP_PANEL_TITLE,
    TOP_PANEL_PAD,
    TOP_INNER_BG,
    TOP_INNER_BORDER,
    TOP_TEXT_PRIMARY,
    TOP_TEXT_MUTED,
    TOP_TEXT_SECONDARY,
    UI_GREEN,
    UI_RED,
    UI_RED_DARK,
    UI_RED_DEEP,
    UI_AMBER,
    UI_VIOLET,
    UI_INFO,
)
from searchable_combobox import SearchableComboBox
from bot_logic import (
    decide as bot_decide,
    apply_execution_filters,
    apply_private_condition_lists_for_strategy,
)
from websocket_manager import WebSocketManager, FrameStream
from binance_chart_aliases import binance_kline_stream_symbol, binance_spot_pair_symbol
from local_day_change import (
    get_open_at_local_midnight,
    invalidate_symbol as local_day_invalidate_symbol,
    local_today_iso,
)
from mode_toggle import ModeToggle
from execution_report import append_execution_report, sanitize_for_execution_json
from execution import ClosePositionWorker, EtoroResolvePositionWorker, OrderWorker
from open_positions import ROW_PRICE_SYMBOL_ROLE

log = logging.getLogger("trading.panel")

# مدة تجاهل مركز/طلب من eToro بعد الإغلاق (ثوانٍ) — API قد يُعيده لساعات؛ 90s كانت قصيرة جداً بعد إعادة التشغيل
_ETORO_RECENT_CLOSE_FILTER_SEC = 7 * 86400.0


def _compute_mtf_frame_bias(ind: dict | None) -> float:
    """انحياز إطار 1h/4h — نفس منطق mtf_bias في البوت (VWAP + MACD + شموع)."""
    if not isinstance(ind, dict):
        return 0.0
    b = 0.0
    try:
        close = float(ind.get("close", 0) or 0)
        vwap = float(ind.get("vwap", 0) or 0)
        if close > 0 and vwap > 0:
            b += 0.5 if close >= vwap else -0.5
    except (TypeError, ValueError):
        pass
    try:
        macd = float(ind.get("macd", 0) or 0)
        sig = float(ind.get("signal", 0) or 0)
        b += 0.7 if macd >= sig else -0.7
    except (TypeError, ValueError):
        pass
    try:
        cscore = float(ind.get("candle_pattern_score", 0) or 0)
        b += max(-0.5, min(0.5, cscore * 0.12))
    except (TypeError, ValueError):
        pass
    return b


def _exchange_list_meaningful_open_count(pl: list) -> int:
    """
    عدّ المراكز المفتوحة «الحقيقية» من قائمة تعيدها المنصة.
    تجنّب احتساب عناصر eToro من طلبات/سجلات فيها positionId لكن بلا وحدات (يظهر len=8 و ui=0).
    """
    if not isinstance(pl, list) or not pl:
        return 0
    seen = set()
    n = 0
    for item in pl:
        if not isinstance(item, dict):
            continue
        qty = None
        try:
            if "quantity" in item and item.get("quantity") is not None:
                qty = abs(float(item.get("quantity") or 0))
        except (TypeError, ValueError):
            qty = None
        if qty is None or qty <= 1e-12:
            try:
                _, u = _etoro_pos_entry_units(item)
                qty = float(u) if u > 1e-12 else 0.0
            except (TypeError, ValueError):
                qty = 0.0
        if qty <= 1e-12:
            continue
        pid = etoro_extract_position_id(item)
        sym = str(item.get("symbol") or item.get("Symbol") or "").strip().upper()
        key = ("id", int(pid)) if pid is not None else ("sym_qty", sym, round(qty, 8))
        if key in seen:
            continue
        seen.add(key)
        n += 1
    return n


def _bot_max_open_trades_cap(cfg: dict) -> int | None:
    """حد الصفقات المفتوحة للبوت. None = معطّل (القيمة ≤ 0 في الإعدادات)."""
    v = cfg.get("bot_max_open_trades", 1)
    try:
        raw = int(v)
    except (TypeError, ValueError):
        raw = 1
    if raw <= 0:
        return None
    return raw


def _format_max_trades_quick_label(cfg: dict) -> str:
    cap = _bot_max_open_trades_cap(cfg)
    suffix = "∞" if cap is None else str(cap)
    return f"{tr('quick_max_trades_short')} {suffix}"


def _config_use_futures(cfg: dict) -> bool:
    """مسار العقود (Futures) مقابل السبوت (فوري). الإعداد market_type يتقدّم على الرافعة."""
    mt = (cfg.get("market_type") or "auto").strip().lower()
    lev = max(1, int(cfg.get("leverage", 1) or 1))
    if mt in ("spot", "cash"):
        return False
    if mt in ("futures", "future", "perp", "cfd"):
        return True
    return lev > 1


def _balance_bar_network_fetch(
    exchange: str,
    api_key: str,
    api_secret: str,
    testnet: bool,
    use_futures: bool,
) -> tuple[float, EtoroBalanceBreakdown | None]:
    """جلب رصيد شريط الحالة من المنصة (شبكة). يُستدعى من الخيط الرئيسي أو من خيط خلفي عند ↻."""
    etoro_bd: EtoroBalanceBreakdown | None = None
    balance = 0.0
    if exchange == "etoro":
        client = EtoroSpotClient(api_key, api_secret, testnet=testnet)
        etoro_bd = client.get_usdt_balance_breakdown()
        balance = float(etoro_bd.available)
    elif exchange == "binance":
        bal_spot = 0.0
        bal_futures = 0.0
        try:
            spot_client = BinanceSpotClient(api_key, api_secret, testnet=testnet)
            bal_spot = spot_client.get_usdt_balance()
        except Exception:
            pass
        try:
            futures_client = BinanceFuturesClient(api_key, api_secret, testnet=testnet)
            bal_futures = futures_client.get_usdt_balance()
        except Exception:
            pass
        if use_futures:
            balance = bal_futures
        else:
            if bal_spot >= 1.0 or bal_futures <= 0:
                balance = bal_spot
            else:
                balance = bal_futures
    else:
        if use_futures:
            client = BitgetFuturesClient(api_key, api_secret, testnet=testnet)
        else:
            client = BitgetSpotClient(api_key, api_secret, testnet=testnet)
        balance = client.get_usdt_balance()
    return balance, etoro_bd


class _BalanceBarFetchWorker(QObject):
    finished = pyqtSignal(float, bool, object)  # balance, testnet, etoro_bd | None
    failed = pyqtSignal(str)

    def __init__(
        self,
        *,
        exchange: str,
        api_key: str,
        api_secret: str,
        testnet: bool,
        use_futures: bool,
    ):
        super().__init__()
        self._exchange = exchange
        self._api_key = api_key
        self._api_secret = api_secret
        self._testnet = testnet
        self._use_futures = use_futures

    @pyqtSlot()
    def run_fetch(self) -> None:
        try:
            bal, bd = _balance_bar_network_fetch(
                self._exchange,
                self._api_key,
                self._api_secret,
                self._testnet,
                self._use_futures,
            )
            self.finished.emit(float(bal), self._testnet, bd)
        except Exception as e:
            log.warning("Balance bar network fetch failed: %s", e)
            self.failed.emit(str(e))


def _etoro_positions_cache_path() -> str:
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
    folder = os.path.join(base, "CryptoTrading")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "etoro_open_positions_cache.json")


def _etoro_positions_cache_backup_path() -> str:
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
    folder = os.path.join(base, "CryptoTrading")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "etoro_open_positions_cache.bak.json")


def _clear_etoro_positions_cache() -> None:
    """حذف كاش مراكز eToro لتفادي إظهار صفقات قديمة بعد إعادة التشغيل."""
    try:
        for p in (_etoro_positions_cache_path(), _etoro_positions_cache_backup_path()):
            if os.path.isfile(p):
                os.remove(p)
    except OSError:
        pass


def _etoro_recent_closed_ids_path() -> str:
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
    folder = os.path.join(base, "CryptoTrading")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "etoro_recent_closed_position_ids.json")


def _parse_etoro_expiry_map(d) -> dict[int, float]:
    out: dict[int, float] = {}
    if not isinstance(d, dict):
        return out
    now = time.time()
    for k, v in d.items():
        try:
            ki = int(k)
            fv = float(v)
            if ki > 0 and fv > now:
                out[ki] = fv
        except (TypeError, ValueError):
            pass
    return out


def _parse_etoro_mode_block(sub) -> tuple[dict[int, float], dict[int, float]]:
    """(position_ids→expiry, order_ids→expiry). الملف القديم: قاموس مسطّح = مراكز فقط."""
    if not isinstance(sub, dict):
        return {}, {}
    if "p" in sub or "o" in sub:
        return _parse_etoro_expiry_map(sub.get("p")), _parse_etoro_expiry_map(sub.get("o"))
    return _parse_etoro_expiry_map(sub), {}


def _read_all_etoro_closed_maps() -> dict[str, tuple[dict[int, float], dict[int, float]]]:
    p = _etoro_recent_closed_ids_path()
    if not os.path.isfile(p):
        return {"testnet": ({}, {}), "live": ({}, {})}
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return {"testnet": ({}, {}), "live": ({}, {})}
        return {
            "testnet": _parse_etoro_mode_block(raw.get("testnet")),
            "live": _parse_etoro_mode_block(raw.get("live")),
        }
    except Exception:
        return {"testnet": ({}, {}), "live": ({}, {})}


def _write_all_etoro_closed_maps(
    maps: dict[str, tuple[dict[int, float], dict[int, float]]],
) -> None:
    try:
        p = _etoro_recent_closed_ids_path()
        now = time.time()
        serial: dict = {}
        for mode in ("testnet", "live"):
            pdict, odict = maps.get(mode) or ({}, {})
            serial[mode] = {
                "p": {str(k): float(v) for k, v in pdict.items() if int(k) > 0 and float(v) > now},
                "o": {str(k): float(v) for k, v in odict.items() if int(k) > 0 and float(v) > now},
            }
        with open(p, "w", encoding="utf-8") as f:
            json.dump(serial, f, indent=2, ensure_ascii=False)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
    except OSError:
        pass


class ClickableLabel(QLabel):
    """Label قابلة للنقر — نستخدمها لعرض تفاصيل قرار البوت عند الضغط على نص الحالة."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        try:
            if event.button() == Qt.MouseButton.LeftButton:
                self.clicked.emit()
        except Exception:
            pass
        super().mousePressEvent(event)


class SymbolLoadThread(QThread):
    symbols_ready = pyqtSignal(list)

    def run(self):
        try:
            from symbol_fetcher import SymbolFetcher
            from config import load_config

            fetcher = SymbolFetcher()
            symbols = fetcher.get_all_symbols() or []
            cfg = load_config()
            if (cfg.get("exchange") or "").lower() == "etoro":
                from etoro_symbols import filter_symbols_for_etoro_trading

                fav = {str(x).strip().upper() for x in (cfg.get("favorite_symbols") or []) if str(x).strip()}
                last = {str(cfg.get("last_symbol") or "").strip().upper()}
                last.discard("")
                symbols = filter_symbols_for_etoro_trading(symbols, favorites=fav, extra_keep=last)
                log.info(
                    "eToro: symbol list filtered to %s USDT pairs (Binance full list → eToro allowlist + favorites)",
                    len(symbols),
                )
            self.symbols_ready.emit(symbols)
        except Exception as e:
            log.warning("SymbolLoadThread error: %s", e)
            self.symbols_ready.emit(["BTCUSDT", "ETHUSDT"])


def _rank_usdt_pool_from_ticker_24h(
    arr: object,
    pool_size: int,
    min_qv: float,
    min_chg: float,
    min_rng: float,
) -> list[str]:
    """ترتيب أزواج USDT للماسح — يُستدعى من خيط خلفي (طلب ثقيل لا يُشغّل على الواجهة)."""
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(arr, list):
        return out
    ranked: list[tuple[float, str]] = []
    for it in arr:
        try:
            sym = str(it.get("symbol") or "").strip().upper()
            if not sym.endswith("USDT"):
                continue
            if any(sym.endswith(x) for x in ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")):
                continue
            last_p = float(it.get("lastPrice") or 0.0)
            high_p = float(it.get("highPrice") or 0.0)
            low_p = float(it.get("lowPrice") or 0.0)
            chg = float(it.get("priceChangePercent") or 0.0)
            qv = float(it.get("quoteVolume") or 0.0)
            if last_p <= 0 or qv <= 0:
                continue
            day_range_pct = ((high_p - low_p) / last_p * 100.0) if high_p > 0 and low_p > 0 else 0.0
            if chg < min_chg:
                continue
            if day_range_pct < min_rng:
                continue
            if qv < min_qv:
                continue
            activity = (qv / 1_000_000.0) + (chg * 8.0) + (day_range_pct * 5.0)
            ranked.append((activity, sym))
        except Exception:
            continue
    ranked.sort(key=lambda t: t[0], reverse=True)
    for _, sym in ranked:
        if sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
        if len(out) >= pool_size:
            break
    return out


class ScannerPoolLoaderThread(QThread):
    """جلب ticker/24hr من Binance خارج خيط الواجهة — كان يجمّد النافذة عند بدء الماسح."""

    pool_ready = pyqtSignal(list)

    def __init__(
        self,
        pool_size: int,
        min_qv: float,
        min_chg: float,
        min_rng: float,
        parent=None,
    ):
        super().__init__(parent)
        self._pool_size = pool_size
        self._min_qv = min_qv
        self._min_chg = min_chg
        self._min_rng = min_rng

    def run(self):
        try:
            url = "https://api.binance.com/api/v3/ticker/24hr"
            r = requests.get(url, timeout=8)
            r.raise_for_status()
            arr = r.json()
            syms = _rank_usdt_pool_from_ticker_24h(
                arr, self._pool_size, self._min_qv, self._min_chg, self._min_rng
            )
            try:
                from config import load_config

                cfg = load_config()
                if (cfg.get("exchange") or "").lower() == "etoro":
                    from etoro_symbols import filter_symbols_for_etoro_trading

                    fav = {str(x).strip().upper() for x in (cfg.get("favorite_symbols") or []) if str(x).strip()}
                    last = {str(cfg.get("last_symbol") or "").strip().upper()}
                    last.discard("")
                    syms = filter_symbols_for_etoro_trading(syms, favorites=fav, extra_keep=last)
            except Exception as e:
                log.debug("ScannerPoolLoaderThread eToro filter: %s", e)
            self.pool_ready.emit(syms)
        except Exception as e:
            log.debug("ScannerPoolLoaderThread: %s", e)
            self.pool_ready.emit([])


class MarketScannerThread(QThread):
    scan_ready = pyqtSignal(list)
    scan_error = pyqtSignal(str)

    def __init__(self, symbols: list[str], interval: str, parent=None):
        super().__init__(parent)
        self._symbols = [str(s or "").strip().upper() for s in (symbols or []) if str(s or "").strip()]
        self._interval = str(interval or "1m")
        if self._interval not in ("1m", "5m", "15m", "1h", "4h", "1d"):
            self._interval = "1m"

    def run(self):
        try:
            cfg = load_config()
            rows: list[dict] = []
            seen: set[str] = set()
            for sym in self._symbols:
                if sym in seen:
                    continue
                seen.add(sym)
                try:
                    stream_sym = binance_kline_stream_symbol(sym)
                    fs = FrameStream(symbol=stream_sym, interval=self._interval)
                    bucket = {"ind": None, "info": None}
                    fs.on_indicators = lambda d, b=bucket: b.__setitem__("ind", d)
                    fs.on_market_info = lambda i, b=bucket: b.__setitem__("info", i)
                    fs.load_historical(lightweight=True)
                    ind = bucket.get("ind") if isinstance(bucket.get("ind"), dict) else None
                    info = bucket.get("info") if isinstance(bucket.get("info"), dict) else {}
                    if not ind:
                        continue
                    rec, conf = AIPanel.get_recommendation(ind, info, cfg)
                    score = float(conf or 0.0)
                    rec_u = str(rec or "").upper()
                    if rec_u == "BUY":
                        score += 15.0
                    elif rec_u == "SELL":
                        score -= 25.0
                    try:
                        vs = float(info.get("volume_strength", 1.0) or 1.0)
                        score += max(-5.0, min(10.0, (vs - 1.0) * 8.0))
                    except Exception:
                        pass
                    rows.append(
                        {
                            "symbol": sym,
                            "recommendation": rec_u or "WAIT",
                            "confidence": float(conf or 0.0),
                            "score": float(score),
                        }
                    )
                except Exception:
                    continue
            rows.sort(key=lambda r: (float(r.get("score", 0.0)), float(r.get("confidence", 0.0))), reverse=True)
            self.scan_ready.emit(rows[:10])
        except Exception as e:
            self.scan_error.emit(str(e))


def _balance_bar_line(amount: float, *, ar: bool) -> str:
    """سطر الرصيد بجانب السعر. عزل اتجاه يسار→يمين للأرقام وUSDT يمنع قصّ/خلط النص في واجهة RTL."""
    num = f"{amount:,.2f} USDT"
    if ar:
        return f"الرصيد: \u2066{num}\u2069"
    return f"Balance: \u2066{num}\u2069"


class TradingPanel(QWidget):

    price_updated = pyqtSignal(float)
    candle_updated = pyqtSignal(str, list)  # (interval, قائمة شموع)
    indicators_updated = pyqtSignal(str, dict)  # (interval, indicators) — إطار الشارت المختار وليس فرض 1m
    market_info_updated = pyqtSignal(dict)
    composite_signal_updated = pyqtSignal(dict)  # للشارت: شارة المؤشر المركّب

    new_position = pyqtSignal(str, float, float, object, object)  # symbol, price, qty, position_id, etoro_open_order_id
    close_all_positions = pyqtSignal()
    risk_settings_saved = pyqtSignal(dict)
    symbol_changed = pyqtSignal(str)
    open_ai_requested = pyqtSignal()
    show_history_tab_requested = pyqtSignal()
    history_refresh_requested = pyqtSignal()  # تحديث سجل الصفقات بدون تبديل التبويب
    status_bar_message = pyqtSignal(str)  # لتحديث شريط الحالة (اتصال، البوت، الرمز)
    balance_updated = pyqtSignal(str)  # نص الرصيد للعرض في شريط الحالة السفلي

    # eToro: بعد 814 / «لا مركز» يمنع تكرار استدعاء SL على نفس الصف كل تيك
    _ETORO_SL_SUPPRESS_GHOST_SEC = 8 * 3600
    _ETORO_SL_SUPPRESS_RETRY_EXHAUSTED_SEC = 45 * 60

    # إشارات داخلية لاستقبال البيانات من خيط WebSocket وتشغيل التحديث على الـ main thread (سريع وآمن)
    _ws_price = pyqtSignal(str, object)
    _ws_candles = pyqtSignal(str, object)
    _ws_indicators = pyqtSignal(str, object)
    _ws_market_info = pyqtSignal(str, object)
    _local_day_ref_ready = pyqtSignal(str, float)  # رمز، مرجع اليوم أو ‎-1 عند الفشل

    def __init__(self):
        super().__init__()

        self.current_symbol = "BTCUSDT"  # يُستبدل من last_symbol بعد load_config()
        self._real_mode = False  # False = TESTNET, True = REAL
        self._daily_pnl = 0.0  # يُحدَّث من MainWindow لفحص حد الخسارة
        self._order_in_progress = False  # منع تنفيذ أمرين في وقت واحد
        self._order_thread = None
        self._order_worker = None
        self._pending_order_last_price = 0.0
        self._last_price_update_time = 0.0  # لمؤشر الاتصال
        self._last_candle_open_ts = None  # لتخفيف تحديث الشموع داخل نفس الشمعة
        self._last_candle_ui_emit_ts = 0.0  # لتخفيف إعادة رسم الشارت/الملخص
        self._candle_ui_emit_min_interval_sec = 0.20  # لا نرسل نفس الشمعة كثيراً
        self._last_indicators_ui_emit_ts = 0.0  # لتخفيف تحديث بقية اللوحات مع المؤشرات
        self._indicators_ui_emit_min_interval_sec = 0.50  # لا نحدّث واجهة المؤشرات أكثر من مرتين/ثانية
        self._chart_interval = "1m"  # إطار الشموع المعروض (1m, 5m, 15m, 1h, 4h, 1d)
        self._last_price = 0.0  # آخر سعر للربوت الآلي (ويُستخدم لتلوين صعود/هبوط السعر)
        self._local_day_ref: float | None = None
        self._local_day_anchor_local_date: str = ""
        self._local_day_fetching = False
        self._local_day_fetch_fail_date: str = ""
        self._bot_enabled = False
        self._bot_cooldown_until = 0.0
        # eToro: فشل place_order لأن الأداة غير موجودة — لا نعيد محاولة شراء البوت لنفس الرمز حتى يغيّر المستخدم الزوج
        self._bot_etoro_unlisted_symbol: str | None = None
        self._last_etoro_unlist_warn_sym: str | None = None
        self._last_etoro_unlist_warn_ts: float = 0.0
        self._follow_strategy_timer = QTimer(self)
        self._follow_strategy_timer.setSingleShot(True)
        self._follow_strategy_timer.timeout.connect(self._apply_followed_suggested_strategy)
        self._last_suggested_key_for_follow = ""
        self._pending_follow_strategy_key = ""
        _cfg = load_config()
        self._BOT_CONFIDENCE_MIN = float(_cfg.get("bot_confidence_min", 60))
        self._BOT_COOLDOWN_SEC = 90
        self._BOT_MAX_OPEN_TRADES = int(_cfg.get("bot_max_open_trades", 1))
        self._chart_interval = str(_cfg.get("chart_interval", "1m") or "1m")
        if self._chart_interval not in ("1m", "5m", "15m", "1h", "4h", "1d"):
            self._chart_interval = "1m"
        self.current_symbol = _cfg.get("last_symbol", _cfg.get("default_symbol", "BTCUSDT")) or "BTCUSDT"
        self._positions_panel = None
        self._last_indicators = None
        self._last_market_info = None
        self._last_bought_support = None  # مستوى الدعم الذي اشترينا منه (لا نشتري مرة ثانية من نفس الدعم)
        self._last_realized_pnl = None
        self._close_pending_position_id = None  # eToro: إغلاق مركز محدد بـ positionId
        self._bot_last_reason_time = 0.0
        self._last_pivot_alert_time = 0.0  # تخفيف تنبيهات قرب S1/S2/R1
        self._trailing_diag_last_ts = 0.0
        self._trailing_diag_last_msg = ""
        self._position_peak_price = None  # أعلى سعر منذ فتح المركز — للبيع عند النزول (trailing)
        self._bot_last_buy_ts_by_symbol: dict[str, float] = {}
        # آخر توصية من لوحة AI كما تُعرض للمستخدم (قبل فلاتر البوت) — يمنع خلط «شراء حد» مع «بيع» على الشاشة
        self._last_panel_recommendation: str = ""
        self._last_panel_confidence: float = 0.0
        self._last_bot_decide_result: tuple | None = None  # لقطة bot_decide للعرض وللمسار التنفيذي
        self._snapshot_last_price: float = 0.0
        self._limit_buy_pct_runtime_anchor: float | None = None  # مرجع نسبة حد الشراء إن لم يُحفَظ في الإعدادات
        self._limit_buy_blocked_log_ts: float = 0.0  # تخفيف سجل «حد الشراء» عند المنع أو عدم تطابق السعر
        self._limit_buy_mismatch_warn_ts: float = 0.0
        self._etoro_buy_grace_until = 0.0  # منع مسح الجدول عندما API eToro يُرجع فارغاً بعد الشراء
        self._etoro_min_hold_until = 0.0  # لا نفعّل SL / حد البيع قبل هذا الوقت (تفادي بيع بسبب سعر/مزامنة)
        # قفل مؤقت لكل رمز بعد شراء eToro لتفادي شراء مكرر أثناء تأخر مزامنة position_id
        self._etoro_pending_symbol_until: dict[str, float] = {}
        # شراء قيد التنفيذ (خيط الأمر) — يُحسب ضمن bot_max_open_trades قبل ظهور الصف في الجدول
        self._pending_buy_order_count: int = 0
        # بعد إغلاق مركز eToro: المنصة قد تُرجعه في /pnl لثوانٍ/دقائق — نتجاهل position_id مؤقتاً في المزامنة
        self._etoro_recent_closed_position_ids: dict[int, float] = {}
        self._etoro_recent_closed_order_ids: dict[int, float] = {}
        self._last_exchange_value: str | None = None
        self._consec_losses_cache_time = 0.0
        self._consec_losses_cache_value = 0
        self._cb_pause_until = 0.0
        self._scanner_thread = None
        self._scanner_top10: list[dict] = []
        self._scanner_loading = False
        self._scanner_show_on_ready = False
        self._cached_usdt_balance: float | None = None  # آخر رصيد USDT جُلب لحساب التعرّض عند المبلغ كنسبة
        # تفاصيل آخر قرار للبوت (تُعرض عند النقر على نص حالة البوت في الأعلى)
        self._last_bot_decision_details: str = ""
        self._last_composite_rebound_guard: bool = False
        self._mtf_close_history_1h: deque[float] = deque(maxlen=36)
        self._mtf_close_history_4h: deque[float] = deque(maxlen=36)
        self._last_indicators_1h: dict | None = None
        self._last_indicators_4h: dict | None = None
        self._mtf_spark_last_t_open_1h: int = 0
        self._mtf_spark_last_t_open_4h: int = 0
        # عناصر إجراءات سريعة (لجعلها responsive)
        self._quick_group = None
        self._ai_group = None
        self._quick_layout = None
        self._quick_last_scale_key = None

        main_layout = QVBoxLayout()
        main_layout.setSpacing(12)
        self.setMinimumHeight(280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.MinimumExpanding)
        self.setStyleSheet(self.dark_style())

        # ============================================================
        # WebSocket Manager — الربط عبر إشارات لضمان السرعة وعدم تجميد الواجهة
        # ============================================================
        _q = Qt.ConnectionType.QueuedConnection
        self._ws_price.connect(self.update_price, _q)
        self._ws_candles.connect(self.update_candle, _q)
        self._ws_indicators.connect(self.update_indicators, _q)
        self._ws_market_info.connect(self.update_market_info, _q)
        self._local_day_ref_ready.connect(self._on_local_day_ref_ready, _q)

        self.ws = WebSocketManager(symbol=binance_kline_stream_symbol(self.current_symbol))
        self._apply_chart_interval()
        self.ws.start()
        QTimer.singleShot(1200, self._schedule_local_day_anchor_fetch)

        # إعادة رسم لوحة التوصية من آخر المؤشرات المحفوظة حتى لا يبدو التحليل «مجمّداً» إذا فات إشعار WebSocket
        self._recommendation_ui_tick_timer = QTimer(self)
        self._recommendation_ui_tick_timer.setInterval(5_000)
        self._recommendation_ui_tick_timer.timeout.connect(self._tick_recommendation_panel_ui)
        self._recommendation_ui_tick_timer.start()

        # ============================================================
        # الصف العلوي: وضع التداول، إجراءات سريعة، إعدادات، حالة السوق، لوحة AI، AI Panel
        # ============================================================
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        top_row.setContentsMargins(4, 4, 4, 4)
        _top_align = Qt.AlignmentFlag.AlignTop

        # خط موحّد يتناسب مع توسع الأقسام
        _bar_font = "font-family: Segoe UI, Arial; font-size: 10px;"
        # ------------------------------------------------------------
        # وضع التداول نُقل إلى قائمة الإعدادات (لتوسيع مساحة حالة السوق)
        # ------------------------------------------------------------
        self.mode_toggle = ModeToggle()  # افتراضي = TESTNET
        self.mode_toggle.setFixedHeight(26)
        self.mode_toggle.mode_changed.connect(self.on_mode_changed)
        self.toggle_button = QPushButton("OFF")
        self.toggle_button.setCheckable(True)
        self.toggle_button.setToolTip(tr("trading_tooltip_robot"))
        self.toggle_button.setFixedHeight(26)
        self.toggle_button.toggled.connect(self.toggle_trade_mode)
        # اجعل الوضع الوهمي مفعل دائماً عند التشغيل
        try:
            self.mode_toggle.set_test_mode()
        except Exception:
            pass
        # الروبوت يبدأ دائماً مغلق
        self.toggle_button.setChecked(False)

        # ------------------------------------------------------------
        # 2) إجراءات سريعة — يتوسّع، خط متناسب
        # ------------------------------------------------------------
        # تنسيق موحّد لأزرار إجراءات سريعة: ارتفاع واحد فقط
        _btn_h = 22
        quick_group = QGroupBox(tr("trading_quick_actions"))
        quick_group.setObjectName("QuickActionsGroup")
        # عرض «إجراءات سريعة» مستقل — لا يُضبَط مع لوحة التوصية في _sync_side_column_widths()
        quick_group.setMinimumWidth(190)
        quick_group.setMaximumWidth(360)
        quick_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._quick_group = quick_group
        # ستايل الصندوق الكامل يُطبَّق في _apply_quick_actions_responsive() (موحّد مع حالة السوق)
        quick_layout = QVBoxLayout()
        self._quick_layout = quick_layout
        quick_layout.setSpacing(8)
        quick_layout.setContentsMargins(6, 8, 6, 8)
        cfg = load_config()
        # أول خيار من الأعلى: اختيار المنصة (قائمة منسدلة)
        exchange_row = QHBoxLayout()
        exchange_row.setSpacing(6)
        exchange_label = QLabel(tr("quick_exchange"))
        exchange_label.setStyleSheet(f"font-size: 10px; color: {TOP_TEXT_MUTED};")
        self.exchange_combo = QComboBox()
        self.exchange_combo.addItem(tr("exchange_binance"), "binance")
        self.exchange_combo.addItem(tr("exchange_bitget"), "bitget")
        self.exchange_combo.addItem(tr("exchange_etoro"), "etoro")
        self.exchange_combo.setFixedHeight(_btn_h)
        self.exchange_combo.setToolTip(tr("quick_tooltip_exchange"))
        self.exchange_combo.setStyleSheet(
            f"background-color: {TOP_INNER_BG}; color: {TOP_TEXT_PRIMARY}; border: 1px solid {TOP_INNER_BORDER};"
            "border-radius: 8px; padding: 2px 6px; min-height: %dpx;" % _btn_h
        )
        saved_exchange = (cfg.get("exchange") or "binance").lower()
        idx = self.exchange_combo.findData(saved_exchange)
        if idx >= 0:
            self.exchange_combo.setCurrentIndex(idx)
        else:
            self.exchange_combo.setCurrentIndex(0)
        # حفظ المنصة الحالية لاستخدامها عند محاولة التغيير مع كلمة مرور
        cur_data = self.exchange_combo.currentData()
        self._last_exchange_value = str(cur_data).lower() if cur_data else "binance"
        self.exchange_combo.currentIndexChanged.connect(self._on_exchange_changed)
        if self._last_exchange_value == "etoro":
            QTimer.singleShot(
                2000,
                lambda: self._warn_if_etoro_symbol_not_in_allowlist(str(self.current_symbol or "")),
            )
        exchange_row.addWidget(exchange_label)
        exchange_row.addWidget(self.exchange_combo, 1)
        quick_layout.addLayout(exchange_row)
        # اختيار العملة (تحت المنصة) — استعادة آخر رمز من الجلسة السابقة
        default_sym = self.current_symbol
        default_symbols = [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
            "DOGEUSDT", "ADAUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
        ]
        if default_sym and default_sym not in default_symbols:
            default_symbols = [default_sym] + default_symbols
        self.symbol_combo = SearchableComboBox()
        self.symbol_combo.setMinimumWidth(85)
        self.symbol_combo.setFixedHeight(_btn_h)
        self.symbol_combo.set_items(self._symbols_with_favorites(default_symbols))
        self.symbol_combo.blockSignals(True)
        self.symbol_combo.setCurrentText(default_sym)
        self.symbol_combo.blockSignals(False)
        self.symbol_combo.symbolConfirmed.connect(self._on_symbol_combo_changed)
        self._symbol_load_thread = SymbolLoadThread()
        self._symbol_load_thread.symbols_ready.connect(self._on_symbols_loaded)
        self._symbol_load_thread.start()
        self.btn_symbol_more = QPushButton("…")
        self.btn_symbol_more.setFixedSize(_btn_h, _btn_h)
        self.btn_symbol_more.setToolTip(tr("main_tooltip_symbol"))
        self.btn_symbol_more.clicked.connect(self._select_symbol)
        self.btn_symbol_more.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_expected_symbols = QPushButton(tr("expected_symbols_btn"))
        self.btn_expected_symbols.setFixedHeight(_btn_h)
        self.btn_expected_symbols.setToolTip(tr("expected_symbols_tooltip"))
        self.btn_expected_symbols.clicked.connect(self._open_expected_symbols_dialog)
        self.btn_expected_symbols.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_expected_symbols.setStyleSheet(
            f"background-color: {TOP_INNER_BG}; color: {TOP_TEXT_PRIMARY}; border: 1px solid {TOP_INNER_BORDER};"
            f"border-radius: 8px; padding: 2px 8px; min-height: {_btn_h}px; font-size: 10px;"
        )
        symbol_row = QHBoxLayout()
        symbol_row.setSpacing(6)
        symbol_row.addWidget(self.symbol_combo)
        symbol_row.addWidget(self.btn_symbol_more)
        symbol_row.addWidget(self.btn_expected_symbols)
        quick_layout.addLayout(symbol_row)
        self.amount_btn = QPushButton(self._format_amount_text(cfg))
        self.leverage_btn = QPushButton(f"{tr('main_leverage')}: {cfg.get('leverage', 10)}x")
        self.market_type_btn = QPushButton(self._format_market_type_text(cfg))
        # زر عدد الصفقات القصوى المفتوحة في نفس الوقت للروبوت
        self.max_trades_btn = QPushButton(_format_max_trades_quick_label(cfg))
        self.tp_btn = QPushButton(self._format_tp_text(cfg))
        self.limit_buy_btn = QPushButton(self._format_limit_buy_text(cfg))
        self.limit_sell_btn = QPushButton(self._format_limit_sell_text(cfg))
        self.sl_btn = QPushButton(self._format_sl_text(cfg))
        self.auto_sell_btn = QPushButton(tr("quick_auto_sell_off"))
        self.auto_sell_btn.setCheckable(True)
        self.auto_sell_btn.setChecked(bool(cfg.get("bot_auto_sell", False)))
        self.auto_sell_btn.setText(tr("quick_auto_sell_on") if self.auto_sell_btn.isChecked() else tr("quick_auto_sell_off"))
        for b in (
            self.amount_btn,
            self.leverage_btn,
            self.market_type_btn,
            self.max_trades_btn,
            self.tp_btn,
            self.limit_buy_btn,
            self.limit_sell_btn,
            self.sl_btn,
            self.auto_sell_btn,
        ):
            b.setFixedHeight(_btn_h)
            b.setMinimumWidth(120)  # جعل المستطيلات أعرض قليلاً
            # خط صريح بالنقاط — لا ننسخ b.font() فقد يكون pointSize=-1 مع font-size بالبكسل في الثيم فيُطلق Qt تحذيراً
            f = QFont()
            f.setPointSize(13)
            b.setFont(f)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self.amount_btn.setToolTip(tr("quick_tooltip_amount"))
        self.leverage_btn.setToolTip(tr("quick_tooltip_leverage"))
        self.market_type_btn.setToolTip(tr("quick_tooltip_market_type"))
        self.max_trades_btn.setToolTip(tr("quick_tooltip_max_trades"))
        self.tp_btn.setToolTip(tr("quick_tooltip_tp"))
        self.limit_buy_btn.setToolTip(tr("quick_tooltip_limit_buy") + "\n" + tr("quick_limit_hint_cancel"))
        self.limit_sell_btn.setToolTip(tr("quick_tooltip_limit_sell") + "\n" + tr("quick_limit_hint_cancel"))
        self.sl_btn.setToolTip(tr("quick_tooltip_sl"))
        self.auto_sell_btn.setToolTip(tr("quick_tooltip_auto_sell"))
        quick_layout.addWidget(self.amount_btn)
        quick_layout.addWidget(self.leverage_btn)
        quick_layout.addWidget(self.market_type_btn)
        quick_layout.addWidget(self.max_trades_btn)
        quick_layout.addWidget(self.tp_btn)
        quick_layout.addWidget(self.limit_buy_btn)
        quick_layout.addWidget(self.limit_sell_btn)
        quick_layout.addWidget(self.sl_btn)
        quick_layout.addWidget(self.auto_sell_btn)
        self.interval_combo = QComboBox()
        self.interval_combo.addItems(["1m", "5m", "15m", "1h", "4h", "1d"])
        self.interval_combo.blockSignals(True)
        self.interval_combo.setCurrentText(self._chart_interval)
        self.interval_combo.blockSignals(False)
        self.interval_combo.setFixedHeight(_btn_h)
        self.interval_combo.setToolTip(tr("trading_tooltip_interval"))
        # نترك ستايل الكومبو يعتمد على الثيم العام مع ارتفاع موحّد
        quick_layout.addWidget(self.interval_combo)
        self.buy_button = QPushButton("BUY")
        self.buy_button.setObjectName("QuickBuyButton")
        self.sell_button = QPushButton(tr("trading_close_all"))
        self.sell_button.setObjectName("QuickSellButton")
        buy_sell_row = QHBoxLayout()
        buy_sell_row.setSpacing(6)
        buy_sell_row.addWidget(self.buy_button)
        buy_sell_row.addWidget(self.sell_button)
        quick_layout.addLayout(buy_sell_row)
        self.risk_button = QPushButton(tr("trading_risk_settings"))
        self.buy_button.setToolTip(tr("trading_tooltip_buy"))
        self.sell_button.setToolTip(tr("trading_tooltip_close"))
        self.risk_button.setToolTip(tr("trading_tooltip_risk"))
        for b in (self.buy_button, self.sell_button, self.risk_button):
            b.setFixedHeight(_btn_h)
            f = QFont()
            f.setPointSize(13)
            b.setFont(f)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        quick_layout.addWidget(self.risk_button)
        quick_layout.addStretch(1)
        # بدون QScrollArea — ارتفاع اللوحة العلوية محدود في MainWindow فيُقصّ المحتوى بشريط تمرير ويختفي جزء من الأزرار
        quick_group.setLayout(quick_layout)
        try:
            quick_group.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        except Exception:
            pass
        # تطبيق القياسات responsive لأول مرة
        self._apply_quick_actions_responsive()

        # ------------------------------------------------------------
        # 3) حالة السوق — عمودي: ميكرو ثم المؤشرات ثم المركّب (بدون أعمدة أفقية)
        # ------------------------------------------------------------
        price_group = QGroupBox(tr("trading_market_status"))
        price_group.setMinimumWidth(100)
        price_group.setMinimumHeight(200)
        price_group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        # لا نستخدم QGroupBox QLabel العام — كان يضغط الخط ويلغي ألوان السعر والمركّب
        _market_status_style = (
            f"QGroupBox {{ {_bar_font} background-color: {TOP_PANEL_BG}; border: 1px solid {TOP_PANEL_BORDER}; border-radius: {TOP_PANEL_RADIUS}; "
            f"padding: {TOP_PANEL_PAD}; }} "
            f"QGroupBox::title {{ color: {TOP_PANEL_TITLE}; subcontrol-origin: margin; left: 12px; padding: 0 8px; font-weight: bold; }} "
        )
        price_group.setStyleSheet(_market_status_style)
        price_layout = QVBoxLayout()
        price_layout.setSpacing(4)
        price_layout.setContentsMargins(4, 2, 4, 4)
        # صف ثابت الارتفاع — لا يتحرك عند طول/قصر نص البوت أو الاتصال
        _status_bar_frame = QFrame()
        _status_bar_frame.setFixedHeight(30)
        _status_bar_frame.setStyleSheet("QFrame { background: transparent; border: none; }")
        top_status_row = QHBoxLayout(_status_bar_frame)
        top_status_row.setContentsMargins(0, 0, 0, 0)
        top_status_row.setSpacing(6)
        self.connection_label = QLabel(tr("trading_connection_lost"))
        self.connection_label.setObjectName("ConnectionStatus")
        self.connection_label.setFixedHeight(22)
        self.connection_label.setStyleSheet(f"color: {UI_RED}; font-weight: bold;")
        self.connection_label.setWordWrap(False)
        top_status_row.addWidget(self.connection_label, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        top_status_row.addStretch(1)
        self.bot_status_label = ClickableLabel("")
        self.bot_status_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.bot_status_label.setFixedHeight(22)
        self.bot_status_label.setWordWrap(False)
        self.bot_status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # حالة البوت والمعلومات — على اليمين بمحاذاة الرصيد
        self.bot_status_label.setStyleSheet("color: #ffffff; font-size: 11px; font-weight: bold;")
        # حالة افتراضية عند فتح البرنامج: البوت متوقف
        if get_language() == "ar":
            self.bot_status_label.setText("البوت متوقف — شغِّل البوت من زر ON/OFF")
        else:
            self.bot_status_label.setText("Bot is OFF — enable it from the ON/OFF button")
        top_status_row.addWidget(self.bot_status_label, 1, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        price_layout.addWidget(_status_bar_frame)
        # عند النقر على نص حالة البوت في الأعلى، نعرض شرحاً تفصيلياً لآخر قرار
        try:
            self.bot_status_label.clicked.connect(self._show_bot_decision_details)
        except Exception:
            pass
        # الرصيد + التحديث أقصى اليسار؛ السعر في المنتصف (يُعرضان تحت صندوق ADX/الملخص)
        self.balance_frame = QFrame()
        self.balance_frame.setObjectName("BalanceFrame")
        self.balance_frame.setStyleSheet(
            f"#BalanceFrame {{ "
            f"background-color: {TOP_INNER_BG}; border: 1px solid {TOP_PANEL_BORDER}; border-radius: 8px; "
            f"padding: 5px 10px; min-height: 20px; "
            f"}}"
        )
        # عرض مرن بدون زيادة ارتفاع الصف: سطر واحد (بدون لفّ يوسّع الصندوق عمودياً)
        self.balance_frame.setMinimumWidth(96)
        self.balance_frame.setMaximumHeight(38)
        self.balance_frame.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        self.balance_frame.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        _bal_layout = QHBoxLayout(self.balance_frame)
        _bal_layout.setContentsMargins(4, 2, 4, 2)
        _bal_layout.setSpacing(4)
        self.balance_label = QLabel(tr("status_balance_none") if get_language() == "ar" else "Balance: —")
        self.balance_label.setStyleSheet(
            f"font-weight: bold; color: {TOP_TEXT_SECONDARY}; font-size: 11px;"
            "; qproperty-alignment: AlignCenter;"
        )
        self.balance_label.setWordWrap(False)
        # طلب المستخدم: توسيط نص الرصيد داخل #BalanceFrame؛ stretches يمين/يسار أوضح من QLabel وحدها بـ Expanding
        self.balance_label.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.balance_label.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self.balance_label.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        _bal_layout.addStretch(1)
        _bal_layout.addWidget(self.balance_label, 0, Qt.AlignmentFlag.AlignCenter)
        _bal_layout.addStretch(1)
        self.balance_refresh_btn = QPushButton("↻")
        self.balance_refresh_btn.setFixedSize(22, 22)
        self.balance_refresh_btn.setToolTip(tr("balance_refresh_tooltip"))
        self.balance_refresh_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.balance_refresh_btn.setStyleSheet(
            f"background-color: {TOP_INNER_BG}; border: 1px solid {TOP_PANEL_BORDER}; border-radius: 6px; "
            f"font-size: 12px; color: {TOP_TEXT_MUTED}; padding: 0; min-width: 22px; min-height: 22px;"
        )
        self.balance_refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.balance_refresh_btn.clicked.connect(self._on_balance_refresh_clicked)
        self._balance_refresh_thread: QThread | None = None
        self._balance_refresh_worker: _BalanceBarFetchWorker | None = None
        self._balance_refresh_prev_text: str = ""
        self.price_label = QLabel(tr("trading_price") + ": 0.00 $")
        self.price_label.setObjectName("TradingPriceDisplay")
        # عرض مرن، سطر واحد فقط — لا لفّ (اللفّ كان يرفع ارتفاع الصف)
        self.price_label.setMinimumWidth(72)
        self.price_label.setWordWrap(False)
        self.price_label.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self._set_price_label_style_neutral()
        self.price_label.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        self.price_day_pct_label = QLabel("—")
        self.price_day_pct_label.setObjectName("TradingDayPctDisplay")
        self.price_day_pct_label.setMinimumWidth(52)
        self.price_day_pct_label.setAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
        self.price_day_pct_label.setToolTip(tr("trading_day_pct_tooltip"))
        self.price_day_pct_label.setStyleSheet(
            f"color: {TOP_TEXT_MUTED}; background: transparent; border: none; "
            "font-weight: bold; font-size: 13px; padding-left: 2px;"
        )
        self.price_day_pct_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        _price_center = QWidget()
        self._price_center_widget = _price_center
        _price_center.setMinimumWidth(120)
        _price_center.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        _price_center.setStyleSheet("background: transparent; border: none;")
        _price_center.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        _price_center_lt = QHBoxLayout(_price_center)
        _price_center_lt.setContentsMargins(0, 0, 0, 0)
        _price_center_lt.setSpacing(2)
        _price_center_lt.addWidget(self.price_label, 0, Qt.AlignmentFlag.AlignVCenter)
        _price_center_lt.addWidget(self.price_day_pct_label, 0, Qt.AlignmentFlag.AlignVCenter)

        _price_row_frame = QFrame()
        _price_row_frame.setMinimumHeight(40)
        _price_row_frame.setMaximumHeight(56)
        _price_row_frame.setStyleSheet("QFrame { background: transparent; border: none; }")
        # LTR ثابت: في واجهة عربية RTL كان أول عنصر (الرصيد) يُرسَم أقصى اليمين ويبدو المنتصف فارغاً
        _price_row_frame.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        price_balance_row = QHBoxLayout(_price_row_frame)
        price_balance_row.setSpacing(8)
        price_balance_row.setContentsMargins(0, 0, 0, 0)
        _bal_cluster = QWidget()
        _bal_cluster.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        _bal_cluster.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
        _bal_cluster_lt = QHBoxLayout(_bal_cluster)
        _bal_cluster_lt.setContentsMargins(0, 0, 0, 0)
        _bal_cluster_lt.setSpacing(6)
        _bal_cluster_lt.addWidget(self.balance_frame, 0)
        _bal_cluster_lt.addWidget(self.balance_refresh_btn, 0)
        price_balance_row.addWidget(_bal_cluster, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        price_balance_row.addStretch(1)
        price_balance_row.addWidget(_price_center, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        price_balance_row.addStretch(1)
        # صف الرصيد/السعر يُضاف لاحقاً بعد صندوق ADX (يُبقى الملخص أعلى)

        # سطر مخصّص لقرار اللوحة؛ عدد الصفقات وقوة الحجم والتقلب في لوحة التوصية
        self.market_micro_frame = QFrame()
        self.market_micro_frame.setObjectName("MarketMicroFrame")
        self.market_micro_frame.setStyleSheet(
            f"#MarketMicroFrame {{ background-color: {TOP_INNER_BG}; border: 1px solid {TOP_PANEL_BORDER}; border-radius: 8px; }}"
        )
        # قرار اللوحة + المؤشرات: أوزان 16:65 (حصة القرار ≈ 16/81 من المساحة المرنة بينهما)
        self.market_micro_frame.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _micro_lt = QVBoxLayout(self.market_micro_frame)
        _micro_lt.setContentsMargins(10, 10, 10, 10)
        _micro_lt.setSpacing(6)
        self.market_swing_peak_label = QLabel("—")
        self.market_swing_peak_label.setObjectName("MarketSwingPeakInfo")
        self.market_swing_peak_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop
        )
        self.market_swing_peak_label.setWordWrap(True)
        self.market_swing_peak_label.setStyleSheet(
            f"color: {TOP_TEXT_SECONDARY}; font-size: 11px; line-height: 1.5;"
        )
        self.market_swing_peak_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        _micro_lt.addWidget(self.market_swing_peak_label, 1)

        self.market_indicators_label = QLabel("—")
        self.market_indicators_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.market_indicators_label.setWordWrap(True)
        self.market_indicators_label.setTextFormat(Qt.TextFormat.RichText)
        self.market_indicators_label.setStyleSheet(
            f"color: {TOP_TEXT_MUTED}; font-size: 11px; background: transparent;"
        )
        # يمتد أفقياً بجانب عمود الصفقات؛ الارتفاع حسب المحتوى
        self.market_indicators_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._indicators_scroll = QScrollArea()
        self._indicators_scroll.setObjectName("MarketIndicatorsScroll")
        self._indicators_scroll.setMinimumHeight(48)
        self._indicators_scroll.setWidgetResizable(True)
        self._indicators_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._indicators_scroll.setFrameShape(QFrame.Shape.NoFrame)
        # أقل جشعاً عمودياً من قرار اللوحة — يبقى للتمرير لكن لا يأكل معظم العمود
        self._indicators_scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self._indicators_scroll.setStyleSheet(
            f"QScrollArea#MarketIndicatorsScroll {{ "
            f"background-color: {TOP_INNER_BG}; border: 1px solid {TOP_PANEL_BORDER}; border-radius: 8px; "
            f"}}"
        )
        # حاوية داخل التمرير: عمود واحد فقط (بدون تقسيم يمين/يسار)
        self._indicators_scroll_content = QWidget()
        _isc_layout = QVBoxLayout(self._indicators_scroll_content)
        _isc_layout.setContentsMargins(8, 2, 8, 8)
        _isc_layout.setSpacing(4)
        _isc_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        self.market_info_trend_label = QLabel("ADX (قوة الاتجاه): —")
        self.market_info_trend_label.setStyleSheet(f"color: {TOP_TEXT_SECONDARY}; font-size: 11px;")
        self.market_info_trend_label.setWordWrap(True)
        self.market_info_trend_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self.market_indicators_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop
        )
        _isc_layout.addWidget(self.market_info_trend_label, 0)
        _isc_layout.addWidget(self.market_indicators_label, 1)
        self._indicators_scroll.setWidget(self._indicators_scroll_content)

        self.composite_frame = QFrame()
        self.composite_frame.setObjectName("CompositeSignalFrame")
        self.composite_frame.setFixedHeight(38)
        self.composite_frame.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.composite_frame.setStyleSheet(
            f"#CompositeSignalFrame {{ "
            f"background-color: {TOP_INNER_BG}; "
            f"border: 1px solid {TOP_PANEL_BORDER}; "
            f"border-radius: 8px; "
            f"border-left: 3px solid {UI_INFO}; "
            f"}}"
        )
        self.composite_frame.setToolTip(tr("composite_bar_tooltip"))
        # سطر واحد: عنوان | النص الرئيسي (يمتد) | الدرجة
        _cpl = QHBoxLayout(self.composite_frame)
        _cpl.setContentsMargins(10, 4, 10, 4)
        _cpl.setSpacing(8)
        _ctitle = QLabel(
            "المؤشر المركّب:" if get_language() == "ar" else "Composite:"
        )
        _ctitle.setStyleSheet(f"color: {TOP_PANEL_TITLE}; font-size: 10px; font-weight: bold;")
        _ctitle.setWordWrap(False)
        _ctitle.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        _cpl.addWidget(_ctitle, 0, Qt.AlignmentFlag.AlignVCenter)
        self.composite_main_label = QLabel("—")
        self.composite_main_label.setWordWrap(False)
        self.composite_main_label.setAlignment(
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignLeading
        )
        self.composite_main_label.setStyleSheet(
            f"color: {TOP_TEXT_PRIMARY}; font-size: 12px; font-weight: bold;"
        )
        self.composite_main_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        _cpl.addWidget(self.composite_main_label, 1, Qt.AlignmentFlag.AlignVCenter)
        self.composite_score_label = QLabel("")
        self.composite_score_label.setWordWrap(False)
        self.composite_score_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        self.composite_score_label.setStyleSheet(f"color: {TOP_TEXT_MUTED}; font-size: 10px; font-weight: bold;")
        self.composite_score_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        _cpl.addWidget(self.composite_score_label, 0, Qt.AlignmentFlag.AlignVCenter)

        # حالة السوق: قرار اللوحة + التمرير — خفض حصة القرار ثلثاً إضافياً عن 8:19 (≈16:65)
        price_layout.addWidget(self.market_micro_frame, 16)
        price_layout.addWidget(_price_row_frame, 0)
        price_layout.addWidget(self._indicators_scroll, 65)
        price_layout.addWidget(self.composite_frame, 0)

        self._connection_timer = QTimer(self)
        self._connection_timer.timeout.connect(self._update_connection_status)
        self._connection_timer.start(2000)
        # مؤقت للتحديثات الثقيلة (مراكز، وقف خسارة، تتبع) بوتيرة أقل لتخفيف الحمل على الواجهة.
        self._heavy_price_timer = QTimer(self)
        self._heavy_price_timer.timeout.connect(self._run_heavy_price_update)
        self._heavy_price_timer_last_ms = None
        self._heavy_price_timer.start(350)
        price_group.setLayout(price_layout)

        # ------------------------------------------------------------
        # 4) AI Panel — التوصية + التحليل (يتضمن الثقة) + الاستراتيجية ووضع الصفقة (منقولان من حالة السوق)
        # ------------------------------------------------------------
        ai_group = QGroupBox(tr("ai_panel_title"))
        # اسم فريد + محدّد في الستايل — وإلا قاعدة QGroupBox تُطبَّق على كل الصناديق الداخلية (صفوف الاستراتيجية…)
        # فيضاعف الـ padding ويضيّق مساحة النص فيُقصّ ويبدو تداخلاً مع الصف التالي.
        ai_group.setObjectName("AIPanelGroup")
        # min/max يُحدَّثان في _sync_side_column_widths() — عرض كافٍ لعناوين عربية بسطر واحد
        ai_group.setMinimumWidth(195)
        ai_group.setMaximumWidth(320)
        ai_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        try:
            ai_group.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        except Exception:
            pass
        _ai_group_style = (
            f"#AIPanelGroup {{ {_bar_font} background-color: {TOP_PANEL_BG}; border: 1px solid {TOP_PANEL_BORDER}; "
            f"border-radius: {TOP_PANEL_RADIUS}; padding: {TOP_PANEL_PAD}; }} "
            f"#AIPanelGroup::title {{ color: {TOP_PANEL_TITLE}; subcontrol-origin: margin; left: 12px; padding: 0 8px; font-weight: bold; }} "
        )
        ai_group.setStyleSheet(_ai_group_style)
        # LTR للمجموعة كاملة حتى لا تُمركز الصفوف أفقياً تحت اتجاه التطبيق RTL
        ai_group.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        # رأس لوحة التوصية خارج QScrollArea حتى لا يُقصّ صف ↻/⚙ أو يُحجب بالتمرير/محاذاة العنوان
        self._ai_panel_header = QWidget()
        ai_header_lt = QVBoxLayout(self._ai_panel_header)
        ai_header_lt.setContentsMargins(8, 10, 8, 0)
        ai_header_lt.setSpacing(6)
        ai_body_layout = QVBoxLayout()
        ai_body_layout.setSpacing(2)
        ai_body_layout.setContentsMargins(4, 0, 4, 6)
        # صف علوي: OFF يسار ← مسافة ← الإعدادات يمين
        self.robot_btn = QPushButton("OFF")
        self.robot_btn.setCheckable(True)
        self.robot_btn.setFixedSize(42, 17)
        self.robot_btn.setToolTip(tr("trading_tooltip_robot"))
        self.robot_btn.setStyleSheet(
            f"background-color: {UI_RED}; color: white; border: none; border-radius: 3px; "
            "font-size: 8px; font-weight: bold; padding: 0 3px;"
        )
        self.indicator_profile_btn = QPushButton(tr("trading_indicator_profile_btn_mid"))
        self.indicator_profile_btn.setCheckable(True)
        self.indicator_profile_btn.setFixedSize(52, 17)
        self.indicator_profile_btn.setToolTip(tr("trading_indicator_profile_tooltip"))
        self.ai_panel_refresh_btn = QPushButton("↻")
        self.ai_panel_refresh_btn.setFixedSize(36, 28)
        self.ai_panel_refresh_btn.setToolTip(tr("ai_panel_refresh_tooltip"))
        self.ai_panel_refresh_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.ai_panel_refresh_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn = QPushButton("⚙")
        self.settings_btn.setFixedSize(36, 28)
        self.settings_btn.setToolTip(tr("settings_tooltip"))
        self.settings_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        ai_top_row = QHBoxLayout()
        ai_top_row.setContentsMargins(0, 0, 0, 0)
        ai_top_row.setSpacing(6)
        ai_top_row.addWidget(self.robot_btn, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        ai_top_row.addWidget(self.indicator_profile_btn, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        ai_top_row.addStretch(1)
        ai_top_row.addWidget(self.ai_panel_refresh_btn, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        ai_top_row.addWidget(self.settings_btn, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        ai_header_lt.addLayout(ai_top_row)
        # وهمي / L / CB — تحت OFF وLOW (المؤشر) و⚙؛ مُزال من صف «حالة السوق» لإفساح نص حالة البوت
        self.mode_badge = QLabel(tr("settings_trading_testnet") if not getattr(self, "_real_mode", False) else tr("settings_trading_live"))
        self.mode_badge.setObjectName("ModeBadge")
        self.mode_badge.setStyleSheet(
            "padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; "
            "background-color: #d4a017; color: #1a1a1a; border: 1px solid #b8860b;"
        )
        self.mode_badge.setToolTip("تداول وهمي — أموال تجريبية" if get_language() == "ar" else "Testnet — simulated funds")
        self.mode_badge.setFixedHeight(22)
        self.consecutive_losses_badge = QLabel("L 0/0")
        self.consecutive_losses_badge.setObjectName("ConsecutiveLossesBadge")
        self.consecutive_losses_badge.setFixedHeight(22)
        self.consecutive_losses_badge.setStyleSheet(
            "padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; "
            "background-color: #3f3f46; color: #e5e7eb; border: 1px solid #5b5b66;"
        )
        self.consecutive_losses_badge.setToolTip(
            "الخسائر المتتالية الحالية / حد القاطع (0=معطّل)"
            if get_language() == "ar"
            else "Current consecutive losses / guard limit (0=off)"
        )
        self.cb_state_badge = QLabel("CB OFF")
        self.cb_state_badge.setObjectName("CircuitBreakerBadge")
        self.cb_state_badge.setFixedHeight(22)
        self.cb_state_badge.setStyleSheet(
            "padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; "
            "background-color: #334155; color: #cbd5e1; border: 1px solid #475569;"
        )
        self.cb_state_badge.setToolTip(
            "حالة Circuit Breaker (إيقاف/مفعّل مع وقت متبقٍ)"
            if get_language() == "ar"
            else "Circuit breaker state (off/active with remaining time)"
        )
        ai_badges_row = QHBoxLayout()
        ai_badges_row.setContentsMargins(0, 0, 0, 0)
        ai_badges_row.setSpacing(6)
        ai_badges_row.addWidget(self.mode_badge, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        ai_badges_row.addWidget(self.consecutive_losses_badge, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        ai_badges_row.addWidget(self.cb_state_badge, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        ai_badges_row.addStretch(1)
        ai_header_lt.addLayout(ai_badges_row)
        self._update_mode_badge()
        self._update_consecutive_losses_badge()
        self._update_cb_badge()
        # حاوية علوية: الاستراتيجية الحالية + وضع الصفقة + عدد الصفقات (بدل دائرة التوقّع)
        self._ai_ring_block = QWidget()
        self._ai_ring_block.setObjectName("AIRingBlock")
        self._ai_ring_block.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._ai_ring_block.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        _arb_lt = QVBoxLayout(self._ai_ring_block)
        _arb_lt.setContentsMargins(0, 0, 0, 0)
        _arb_lt.setSpacing(2)
        _ai_row_ss = (
            f"QFrame#AIRecoRow {{ background: transparent; border: none; "
            f"border-bottom: 1px solid {TOP_INNER_BORDER}; border-radius: 0; padding: 0px; }}"
        )

        def _ai_row() -> QFrame:
            r = QFrame()
            r.setObjectName("AIRecoRow")
            r.setStyleSheet(_ai_row_ss)
            r.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            r.setMinimumHeight(22)
            r.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            return r
        # لون عناوين اللوحة = نفس لون خلفية زر HI تقريباً
        _title_style = f"color: {UI_INFO}; font-size: 10px; font-weight: bold;"
        _value_style = f"color: {TOP_TEXT_PRIMARY}; font-weight: bold; font-size: 11px;"
        # عناوين الصفوف: سطر واحد (WordWrap مع عرض ضيق كان يكسّر العربية إلى سطرين)
        _ai_key_max_w = 168

        def _ai_key_lbl(top: bool = False, key_width: int | None = None) -> QLabel:
            mw = int(key_width) if key_width is not None else _ai_key_max_w
            w = QLabel()
            w.setStyleSheet(_title_style)
            w.setWordWrap(False)
            w.setMaximumWidth(mw)
            w.setMinimumWidth(1)
            w.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            if top:
                w.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            else:
                w.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            return w

        def _ai_row_value_policy(lbl: QLabel, *, min_w: int = 40) -> None:
            lbl.setMinimumWidth(int(min_w))
            lbl.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred)

        # صفوف مضغوطة: QFrame + خط سفلي فقط — بدون صندوق QGroupBox عريض لكل سطر
        self.strategy_row_frame = _ai_row()
        self.strategy_row_frame.setMinimumHeight(24)
        _str_lt = QHBoxLayout()
        _str_lt.setContentsMargins(4, 1, 4, 1)
        _str_lt.setSpacing(4)
        _str_key = QLabel(str(tr("risk_strategy_current")).rstrip(" :"))
        _str_key.setStyleSheet(_title_style)
        _str_key.setWordWrap(False)
        _str_key.setMaximumWidth(_ai_key_max_w)
        _str_key.setMinimumWidth(1)
        _str_key.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        _str_key.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.strategy_display_label = QLabel("")
        self.strategy_display_label.setWordWrap(False)
        self.strategy_display_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.strategy_display_label.setStyleSheet(
            f"color: {TOP_TEXT_PRIMARY}; font-size: 12px; font-weight: 500;"
        )
        self.strategy_display_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        _str_lt.addWidget(_str_key, 0)
        _str_lt.addWidget(self.strategy_display_label, 1)
        self.strategy_row_frame.setLayout(_str_lt)
        _arb_lt.addWidget(self.strategy_row_frame)

        self.trade_mode_row_frame = _ai_row()
        _tm_lt = QHBoxLayout()
        _tm_lt.setContentsMargins(4, 1, 4, 1)
        _tm_lt.setSpacing(4)
        self.ai_trade_mode_key_label = QLabel("وضع الصفقة" if get_language() == "ar" else "Trade mode")
        self.ai_trade_mode_key_label.setStyleSheet(_title_style)
        self.ai_trade_mode_key_label.setWordWrap(False)
        self.ai_trade_mode_key_label.setMaximumWidth(_ai_key_max_w)
        self.ai_trade_mode_key_label.setMinimumWidth(1)
        self.ai_trade_mode_key_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.ai_trade_mode_key_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.market_trade_mode_label = QLabel("—")
        self.market_trade_mode_label.setObjectName("MarketTradeModeLabel")
        self.market_trade_mode_label.setWordWrap(False)
        self.market_trade_mode_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.market_trade_mode_label.setTextFormat(Qt.TextFormat.PlainText)
        self.market_trade_mode_label.setStyleSheet(f"color: {TOP_TEXT_SECONDARY}; font-size: 11px;")
        _ai_row_value_policy(self.market_trade_mode_label)
        _tm_lt.addWidget(self.ai_trade_mode_key_label, 0)
        _tm_lt.addWidget(
            self.market_trade_mode_label,
            1,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self.trade_mode_row_frame.setLayout(_tm_lt)
        _arb_lt.addWidget(self.trade_mode_row_frame)

        self.suggested_symbol_frame = _ai_row()
        _ss_lt = QHBoxLayout()
        _ss_lt.setContentsMargins(4, 1, 4, 1)
        _ss_lt.setSpacing(4)
        self.ai_suggested_symbol_key_label = QLabel(tr("ai_suggested_symbol_title"))
        self.ai_suggested_symbol_key_label.setStyleSheet(_title_style)
        self.ai_suggested_symbol_key_label.setWordWrap(False)
        self.ai_suggested_symbol_key_label.setMaximumWidth(_ai_key_max_w)
        self.ai_suggested_symbol_key_label.setMinimumWidth(1)
        self.ai_suggested_symbol_key_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.ai_suggested_symbol_key_label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.ai_suggested_symbol_btn = ClickableLabel(tr("ai_suggested_symbol_scan"))
        self.ai_suggested_symbol_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ai_suggested_symbol_btn.setToolTip(tr("ai_suggested_symbol_tooltip"))
        self.ai_suggested_symbol_btn.setWordWrap(False)
        self.ai_suggested_symbol_btn.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.ai_suggested_symbol_btn.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        self.ai_suggested_symbol_btn.setStyleSheet(
            f"color: {TOP_TEXT_PRIMARY}; font-size: 11px; padding: 0px;"
        )
        self.ai_suggested_symbol_btn.setMinimumWidth(44)
        self.ai_suggested_symbol_btn.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred)
        self.ai_suggested_symbol_btn.clicked.connect(self._on_click_suggested_symbol)
        _ss_lt.addWidget(self.ai_suggested_symbol_key_label, 0)
        _ss_lt.addWidget(
            self.ai_suggested_symbol_btn,
            1,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self.suggested_symbol_frame.setLayout(_ss_lt)
        _arb_lt.addWidget(self.suggested_symbol_frame)

        self.open_positions_count_frame = _ai_row()
        _opc_lt = QHBoxLayout()
        _opc_lt.setContentsMargins(4, 1, 4, 1)
        _opc_lt.setSpacing(4)
        _opc_title = _ai_key_lbl()
        _opc_title.setText(tr("open_pos_count_label"))
        _opc_lt.addWidget(_opc_title)
        self.ai_open_positions_value_label = QLabel("0")
        self.ai_open_positions_value_label.setObjectName("AIOpenPositionsValue")
        self.ai_open_positions_value_label.setStyleSheet(_value_style)
        self.ai_open_positions_value_label.setWordWrap(False)
        self.ai_open_positions_value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        _ai_row_value_policy(self.ai_open_positions_value_label, min_w=36)
        _opc_lt.addWidget(
            self.ai_open_positions_value_label,
            1,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self.open_positions_count_frame.setLayout(_opc_lt)
        _arb_lt.addWidget(self.open_positions_count_frame)
        ai_body_layout.addWidget(self._ai_ring_block)

        self.volume_strength_frame = _ai_row()
        _vol_lt = QHBoxLayout()
        _vol_lt.setContentsMargins(4, 1, 4, 1)
        _vol_lt.setSpacing(4)
        _vol_title = _ai_key_lbl()
        _vol_title.setText(tr("market_info_volume"))
        _vol_lt.addWidget(_vol_title)
        self.ai_volume_strength_value_label = QLabel("—")
        self.ai_volume_strength_value_label.setObjectName("AIVolumeStrengthValue")
        self.ai_volume_strength_value_label.setStyleSheet(
            f"color: {TOP_TEXT_SECONDARY}; font-size: 11px;"
        )
        self.ai_volume_strength_value_label.setWordWrap(False)
        self.ai_volume_strength_value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        _ai_row_value_policy(self.ai_volume_strength_value_label)
        _vol_lt.addWidget(
            self.ai_volume_strength_value_label,
            1,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self.volume_strength_frame.setLayout(_vol_lt)
        ai_body_layout.addWidget(self.volume_strength_frame)

        self.volatility_frame = _ai_row()
        _vlt_lt = QHBoxLayout()
        _vlt_lt.setContentsMargins(4, 1, 4, 1)
        _vlt_lt.setSpacing(4)
        _vlt_title = _ai_key_lbl()
        _vlt_title.setText(tr("market_info_volatility"))
        _vlt_lt.addWidget(_vlt_title)
        self.ai_volatility_value_label = QLabel("—")
        self.ai_volatility_value_label.setObjectName("AIVolatilityValue")
        self.ai_volatility_value_label.setStyleSheet(
            f"color: {TOP_TEXT_SECONDARY}; font-size: 11px;"
        )
        self.ai_volatility_value_label.setWordWrap(False)
        self.ai_volatility_value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        _ai_row_value_policy(self.ai_volatility_value_label)
        _vlt_lt.addWidget(
            self.ai_volatility_value_label,
            1,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self.volatility_frame.setLayout(_vlt_lt)
        ai_body_layout.addWidget(self.volatility_frame)

        self.buy_pressure_frame = _ai_row()
        _bp_lt = QHBoxLayout()
        _bp_lt.setContentsMargins(4, 1, 4, 1)
        _bp_lt.setSpacing(4)
        _bp_title = _ai_key_lbl()
        _bp_title.setText(tr("indicator_buy_pressure"))
        _bp_lt.addWidget(_bp_title)
        self.ai_buy_pressure_value_label = QLabel("—")
        self.ai_buy_pressure_value_label.setObjectName("AIBuyPressureValue")
        self.ai_buy_pressure_value_label.setStyleSheet(
            f"color: {TOP_TEXT_SECONDARY}; font-size: 11px;"
        )
        self.ai_buy_pressure_value_label.setWordWrap(False)
        self.ai_buy_pressure_value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        _ai_row_value_policy(self.ai_buy_pressure_value_label)
        _bp_lt.addWidget(
            self.ai_buy_pressure_value_label,
            1,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self.buy_pressure_frame.setLayout(_bp_lt)
        ai_body_layout.addWidget(self.buy_pressure_frame)

        self.fear_greed_frame = _ai_row()
        _fg_lt = QHBoxLayout()
        _fg_lt.setContentsMargins(4, 1, 4, 1)
        _fg_lt.setSpacing(4)
        _fg_title = _ai_key_lbl()
        _fg_title.setText(tr("indicator_fear_greed"))
        _fg_lt.addWidget(_fg_title)
        self.ai_fear_greed_value_label = QLabel("—")
        self.ai_fear_greed_value_label.setObjectName("AIFearGreedValue")
        self.ai_fear_greed_value_label.setStyleSheet(
            f"color: {TOP_TEXT_SECONDARY}; font-size: 11px;"
        )
        self.ai_fear_greed_value_label.setWordWrap(False)
        self.ai_fear_greed_value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        _ai_row_value_policy(self.ai_fear_greed_value_label)
        _fg_lt.addWidget(
            self.ai_fear_greed_value_label,
            1,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self.fear_greed_frame.setLayout(_fg_lt)
        ai_body_layout.addWidget(self.fear_greed_frame)

        self.last_trade_frame = _ai_row()
        _lt = QHBoxLayout()
        _lt.setContentsMargins(4, 1, 4, 1)
        _lt.setSpacing(4)
        _last_trade_title = _ai_key_lbl()
        _last_trade_title.setText(tr("trading_last_trade_title"))
        _lt.addWidget(_last_trade_title)
        self.last_trade_label = QLabel("—")
        self.last_trade_label.setObjectName("LastTradeValue")
        self.last_trade_label.setStyleSheet(_value_style)
        self.last_trade_label.setWordWrap(False)
        self.last_trade_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        _ai_row_value_policy(self.last_trade_label)
        _lt.addWidget(
            self.last_trade_label,
            1,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self.last_trade_pnl_label = None
        self.last_trade_frame.setLayout(_lt)
        ai_body_layout.addWidget(self.last_trade_frame)
        # التوصية مخفية من لوحة التوصية (حسب طلب المستخدم) — تبقى داخلياً لنص التحليل فقط.
        self.recommendation_frame = None
        self.ai_recommend_label = None
        # لا نعرض «الاستراتيجية المقترحة» في لوحة التوصية (طلب المستخدم)؛ يبقى اتباع الاقتراح في الخلفية إن كان مفعّلاً.
        self.strategy_suggestion_label = None
        # التحليل — سطر واحد (اتجاه + نسبة الثقة + لقطة مؤشرات)؛ التلميح يكرر النص مع وقت التحديث
        self.analysis_frame = _ai_row()
        self.analysis_frame.setMinimumHeight(24)
        _ana_lt = QHBoxLayout()
        _ana_lt.setContentsMargins(4, 1, 4, 1)
        _ana_lt.setSpacing(4)
        _ana_title = _ai_key_lbl()
        _ana_title.setText(tr("ai_analysis"))
        _ana_lt.addWidget(_ana_title)
        self.ai_analysis_label = QLabel("—")
        self.ai_analysis_label.setObjectName("AIAnalysisValue")
        self.ai_analysis_label.setStyleSheet(_value_style)
        self.ai_analysis_label.setWordWrap(True)
        self.ai_analysis_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        _ai_row_value_policy(self.ai_analysis_label, min_w=44)
        try:
            self.ai_analysis_label.setAttribute(Qt.WidgetAttribute.WA_TextNoClip, True)
        except Exception:
            pass
        _ana_lt.addWidget(
            self.ai_analysis_label,
            1,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop,
        )
        self.analysis_frame.setLayout(_ana_lt)
        ai_body_layout.addWidget(self.analysis_frame)
        self.bot_conf_min_frame = _ai_row()
        _bcm_lt = QHBoxLayout()
        _bcm_lt.setContentsMargins(4, 1, 4, 1)
        _bcm_lt.setSpacing(4)
        self.ai_bot_conf_min_title = _ai_key_lbl()
        self.ai_bot_conf_min_title.setText(tr("ai_bot_exec_confidence_row"))
        _bcm_lt.addWidget(self.ai_bot_conf_min_title)
        self.ai_bot_conf_min_value_label = QLabel("—")
        self.ai_bot_conf_min_value_label.setObjectName("AIBotConfMinValue")
        self.ai_bot_conf_min_value_label.setStyleSheet(
            f"color: {TOP_TEXT_SECONDARY}; font-size: 11px; font-weight: bold;"
        )
        self.ai_bot_conf_min_value_label.setWordWrap(False)
        self.ai_bot_conf_min_value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.ai_bot_conf_min_value_label.setToolTip(tr("ai_bot_exec_confidence_tooltip"))
        _ai_row_value_policy(self.ai_bot_conf_min_value_label, min_w=36)
        _bcm_lt.addWidget(
            self.ai_bot_conf_min_value_label,
            1,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        self.bot_conf_min_frame.setLayout(_bcm_lt)
        ai_body_layout.addWidget(self.bot_conf_min_frame)
        self._refresh_bot_exec_confidence_display("WAIT", 0.0, None, None)
        ai_body_layout.addStretch(1)
        self._ai_panel_scroll = QScrollArea()
        self._ai_panel_scroll.setObjectName("AIPanelScroll")
        self._ai_panel_scroll.setWidgetResizable(True)
        self._ai_panel_scroll.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self._ai_panel_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._ai_panel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._ai_panel_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._ai_panel_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._ai_panel_scroll.setStyleSheet(
            f"QScrollArea#AIPanelScroll {{ background-color: {TOP_PANEL_BG}; border: none; }}"
        )
        _ai_scroll_inner = QWidget()
        _ai_scroll_inner.setObjectName("AIPanelScrollInner")
        _ai_scroll_inner.setLayout(ai_body_layout)
        _ai_scroll_inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        _ai_scroll_inner.setStyleSheet(
            f"QWidget#AIPanelScrollInner {{ background-color: {TOP_PANEL_BG}; }}"
        )
        self._ai_panel_scroll.setWidget(_ai_scroll_inner)
        try:
            self._ai_panel_scroll.viewport().setStyleSheet(f"background-color: {TOP_PANEL_BG};")
        except Exception:
            pass
        _ai_gb_layout = QVBoxLayout()
        _ai_gb_layout.setContentsMargins(0, 0, 0, 0)
        _ai_gb_layout.setSpacing(0)
        _ai_gb_layout.addWidget(self._ai_panel_header, 0)
        _ai_gb_layout.addWidget(self._ai_panel_scroll, 1)
        ai_group.setLayout(_ai_gb_layout)
        self._ai_group = ai_group

        quick_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        ai_group.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        top_row.addWidget(quick_group, 0, _top_align)
        # بدون AlignTop حتى يملأ عمود «حالة السوق» كامل ارتفاع الصف وتمتد منطقة المؤشرات
        top_row.addWidget(price_group, 1)
        # بدون AlignTop حتى يمتد عمود «لوحة التوصية» لنفس ارتفاع الصف (مثل حالة السوق)
        top_row.addWidget(ai_group, 0)
        # mode_group أُزيل لزيادة مساحة حالة السوق

        main_layout.addLayout(top_row)
        self.setLayout(main_layout)
        QTimer.singleShot(0, self._sync_side_column_widths)
        _cfg_init = load_config()
        self._update_strategy_display(_cfg_init)
        self._update_market_trade_mode_label(_cfg_init)
        self._refresh_last_trade_display()
        try:
            self.history_refresh_requested.connect(self._refresh_last_trade_display)
        except Exception:
            pass
        # الرصيد الأولي + جلب المراكز المفتوحة من المنصة حسب الوضع الحالي
        QTimer.singleShot(2000, lambda: self._emit_balance_for_status_bar(testnet=(not self.is_real_mode())))
        QTimer.singleShot(3500, self._sync_open_positions_from_exchange)
        # مزامنة دورية لمراكز eToro (تحميل معرّف كل مركز + تحديث الجدول)
        self._etoro_positions_timer = QTimer(self)
        # أقل تكراراً — الاستجابة الفارغة من API شائعة؛ التكرار العالي يمسح الجدول بسرعة فيبدو «بيعاً وهمياً»
        self._etoro_positions_timer.setInterval(30000)

        def _periodic_etoro_positions_sync():
            try:
                c = load_config()
                if (c.get("exchange") or "").lower() != "etoro":
                    return
                # مزامنة لكل أوضاع eToro (رافعة 1 أو أعلى). سابقاً كانت تُفعَّل فقط عند الرافعة > 1
                # فيبدو أن المشاكل (تحذيرات get_positions، تعارض مع الصف المحلي) «خاصة بالرافعة» فقط.
                self._sync_open_positions_from_exchange()
            except Exception:
                pass

        self._etoro_positions_timer.timeout.connect(_periodic_etoro_positions_sync)
        self._etoro_positions_timer.start()

        # ============================================================
        # ستايل بقية الأزرار (موحّد وخفيف بدون سيلكتور QSS)
        # ============================================================
        default_button_style = (
            f"background-color: {TOP_INNER_BG};"
            f"color: {TOP_TEXT_PRIMARY};"
            f"border: 1px solid {TOP_INNER_BORDER};"
            "border-radius: 6px;"
            "padding: 6px 10px;"
        )
        self.btn_symbol_more.setStyleSheet(default_button_style)
        self.settings_btn.setStyleSheet(default_button_style)
        self.ai_panel_refresh_btn.setStyleSheet(default_button_style)
        self._refresh_indicator_profile_button_style()

        # ============================================================
        # تفعيل الأزرار (يجب أن تكون في __init__ وليس داخل _show_toast)
        # ============================================================
        self.buy_button.clicked.connect(self.buy_action)
        self.sell_button.clicked.connect(self.close_all_action)
        self.ai_panel_refresh_btn.clicked.connect(self._on_ai_panel_refresh_clicked)
        self.settings_btn.clicked.connect(self._show_settings_menu)
        self.risk_button.clicked.connect(self.risk_settings_action)
        self.robot_btn.toggled.connect(lambda state: self.toggle_button.setChecked(state))
        self.indicator_profile_btn.clicked.connect(self._toggle_indicator_profile)
        self.interval_combo.currentTextChanged.connect(self._on_interval_changed)
        self.amount_btn.clicked.connect(self._open_amount_dialog)
        self.leverage_btn.clicked.connect(self._open_leverage_dialog)
        self.market_type_btn.clicked.connect(self._open_market_type_dialog)
        self.max_trades_btn.clicked.connect(self._open_max_trades_dialog)
        self.tp_btn.clicked.connect(self._open_tp_dialog)
        self.limit_buy_btn.clicked.connect(self._open_limit_buy_dialog)
        self.limit_sell_btn.clicked.connect(self._open_limit_sell_dialog)
        self.sl_btn.clicked.connect(self._open_sl_dialog)
        self.auto_sell_btn.toggled.connect(self._on_auto_sell_toggled)

    # ------------------------------------------------------------
    # رسائل منبثقة auto-close (تُغلق بعد 5 ثوانٍ تلقائياً)
    # ------------------------------------------------------------
    def _get_indicator_profile(self) -> str:
        try:
            cfg = load_config()
            p = str(cfg.get("indicator_speed_profile", "balanced") or "balanced").strip().lower()
        except Exception:
            p = "balanced"
        if p == "standard":
            p = "balanced"
        if p not in ("conservative", "balanced", "fast"):
            p = "balanced"
        return p

    def _refresh_indicator_profile_button_style(self) -> None:
        if not hasattr(self, "indicator_profile_btn") or self.indicator_profile_btn is None:
            return
        p = self._get_indicator_profile()
        self.indicator_profile_btn.blockSignals(True)
        self.indicator_profile_btn.setChecked(p == "fast")
        if p == "fast":
            self.indicator_profile_btn.setText(tr("trading_indicator_profile_btn_fast"))
            self.indicator_profile_btn.setStyleSheet(
                f"background-color: {UI_INFO}; color: #0b1220; border: none; border-radius: 3px; "
                "font-size: 8px; font-weight: bold; padding: 0 3px;"
            )
            self.indicator_profile_btn.setToolTip(tr("trading_indicator_profile_tooltip_fast"))
        elif p == "conservative":
            self.indicator_profile_btn.setText(tr("trading_indicator_profile_btn_slow"))
            self.indicator_profile_btn.setStyleSheet(
                f"background-color: {TOP_PANEL_BORDER}; color: {TOP_TEXT_PRIMARY}; border: none; border-radius: 3px; "
                "font-size: 8px; font-weight: bold; padding: 0 3px;"
            )
            self.indicator_profile_btn.setToolTip(tr("trading_indicator_profile_tooltip_conservative"))
        else:
            self.indicator_profile_btn.setText(tr("trading_indicator_profile_btn_mid"))
            self.indicator_profile_btn.setStyleSheet(
                f"background-color: {TOP_PANEL_BORDER}; color: {TOP_TEXT_PRIMARY}; border: none; border-radius: 3px; "
                "font-size: 8px; font-weight: bold; padding: 0 3px;"
            )
            self.indicator_profile_btn.setToolTip(tr("trading_indicator_profile_tooltip_balanced"))
        self.indicator_profile_btn.blockSignals(False)

    def _toggle_indicator_profile(self) -> None:
        try:
            cfg = load_config()
            cur = str(cfg.get("indicator_speed_profile", "balanced") or "balanced").strip().lower()
            if cur == "standard":
                cur = "balanced"
            order = ["conservative", "balanced", "fast"]
            if cur not in order:
                cur = "balanced"
            nxt = order[(order.index(cur) + 1) % len(order)]
            cfg["indicator_speed_profile"] = nxt
            save_config(cfg)
            self._refresh_indicator_profile_button_style()
            msg_key = (
                "trading_indicator_profile_changed_fast"
                if nxt == "fast"
                else "trading_indicator_profile_changed_conservative"
                if nxt == "conservative"
                else "trading_indicator_profile_changed_balanced"
            )
            self.status_bar_message.emit(tr(msg_key))
            ws = getattr(self, "ws", None)
            fn = getattr(ws, "refresh_indicators_all_frames", None) if ws is not None else None
            if callable(fn):
                fn()
        except Exception:
            pass

    def _show_toast(self, icon: QMessageBox.Icon, title: str, text: str, msec: int = 5000):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setIcon(icon)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setModal(False)
        box.show()
        QTimer.singleShot(msec, box.close)

    def _warn_if_etoro_symbol_not_in_allowlist(self, sym: str) -> None:
        """عند منصة eToro: تنبيه إذا كان زوج الشارت (من Binance) خارج القائمة المعروفة لدى التطبيق."""
        cfg = load_config()
        if (cfg.get("exchange") or "").lower() != "etoro":
            self._bot_etoro_unlisted_symbol = None
            return
        s = str(sym or "").strip().upper()
        if not s:
            return
        from etoro_symbols import symbol_passes_etoro_allowlist

        fav_raw = cfg.get("favorite_symbols") or []
        fav = {str(x or "").strip().upper() for x in fav_raw if str(x or "").strip()}
        if symbol_passes_etoro_allowlist(s, favorites=fav):
            self._bot_etoro_unlisted_symbol = None
            self._last_etoro_unlist_warn_sym = None
            self._last_etoro_unlist_warn_ts = 0.0
            return
        self._bot_etoro_unlisted_symbol = s
        now = time.time()
        if (
            self._last_etoro_unlist_warn_sym == s
            and (now - float(self._last_etoro_unlist_warn_ts or 0.0)) < 6.0
        ):
            return
        self._last_etoro_unlist_warn_sym = s
        self._last_etoro_unlist_warn_ts = now
        try:
            self._show_toast(
                QMessageBox.Icon.Warning,
                tr("etoro_unlisted_symbol_warn_title"),
                tr("etoro_unlisted_symbol_warn_body").format(symbol=s),
                msec=7500,
            )
        except Exception:
            pass

    def _on_symbol_combo_changed(self, text: str):
        if not text:
            return
        sym = str(text).strip().upper()
        if sym == (getattr(self, "current_symbol", None) or "").strip().upper():
            return
        self.current_symbol = sym
        self.change_symbol(sym)
        self.symbol_changed.emit(sym)

    def _on_exchange_changed(self, index: int):
        """تغيير المنصة (Binance / Bitget) مع طلب كلمة مرور أمان قبل الحفظ."""
        new_data = self.exchange_combo.currentData()
        if not new_data:
            return
        new_exch = str(new_data).lower()
        prev_exch = (self._last_exchange_value or "").lower()
        # إذا لم يتغير شيء، لا نفعل شيئاً
        if new_exch == prev_exch:
            return
        # طلب كلمة مرور إعدادات API كطبقة أمان قبل تغيير المنصة
        res = request_unlock_or_set_password(self)
        if res is None:
            # لم تُدخَل كلمة مرور صحيحة — نرجع للمنصة السابقة
            target = prev_exch or "binance"
            self.exchange_combo.blockSignals(True)
            idx = self.exchange_combo.findData(target)
            if idx >= 0:
                self.exchange_combo.setCurrentIndex(idx)
            self.exchange_combo.blockSignals(False)
            try:
                self._show_toast("تم إلغاء تغيير المنصة (لم يتم إدخال كلمة المرور).")
            except Exception:
                pass
            return
        # كلمة المرور صحيحة — نحفظ المنصة الجديدة في الإعدادات
        cfg = load_config()
        cfg["exchange"] = new_exch
        save_config(cfg)
        self._last_exchange_value = new_exch
        self._bot_etoro_unlisted_symbol = None
        self._last_etoro_unlist_warn_sym = None
        self._last_etoro_unlist_warn_ts = 0.0
        self._reload_symbol_list_for_exchange_change()
        if new_exch == "etoro":
            QTimer.singleShot(
                150,
                lambda: self._warn_if_etoro_symbol_not_in_allowlist(str(self.current_symbol or "")),
            )

    def _reload_symbol_list_for_exchange_change(self) -> None:
        """بعد تبديل Binance ↔ eToro: إعادة بناء قائمة الرموز (تصفية eToro)."""
        old = getattr(self, "_symbol_load_thread", None)
        if old is not None and old.isRunning():
            return
        t = SymbolLoadThread()
        t.symbols_ready.connect(self._on_symbols_loaded)
        t.start()
        self._symbol_load_thread = t

    def _on_symbols_loaded(self, symbols: list):
        if not symbols:
            return
        current = self.symbol_combo.currentText()
        ordered_symbols = self._symbols_with_favorites(symbols)
        self.symbol_combo.blockSignals(True)
        self.symbol_combo.set_items(ordered_symbols)
        if current and self.symbol_combo.findText(current) >= 0:
            self.symbol_combo.setCurrentText(current)
        else:
            self.symbol_combo.setCurrentText("BTCUSDT")
        self.symbol_combo.blockSignals(False)
        # تأجيل الماسح عدة ثوانٍ: كان يبدأ فوراً مع تحميل الرموز فينافس الشارت/WebSocket على CPU فيُحسّ بالتجمّد.
        QTimer.singleShot(2800, lambda: self._start_market_scanner(show_popup_on_finish=False))

    def _symbols_with_favorites(self, symbols: list[str]) -> list[str]:
        """إرجاع القائمة مع وضع الرموز المفضلة أولاً."""
        clean = [str(s or "").strip().upper() for s in (symbols or []) if str(s or "").strip()]
        cfg = load_config()
        fav_raw = cfg.get("favorite_symbols") or []
        fav = [str(s or "").strip().upper() for s in fav_raw if str(s or "").strip()]
        fav_set = set(fav)
        head = [s for s in fav if s in clean]
        tail = [s for s in clean if s not in fav_set]
        return head + tail

    def _read_scanner_pool_params(self) -> tuple[int, float, float, float]:
        cfg = load_config()
        try:
            pool_size = int(cfg.get("market_scanner_pool_size", 50) or 50)
        except (TypeError, ValueError):
            pool_size = 50
        pool_size = max(10, min(200, pool_size))
        try:
            min_qv = float(cfg.get("market_scanner_min_quote_volume_usdt", 5_000_000.0) or 5_000_000.0)
        except (TypeError, ValueError):
            min_qv = 5_000_000.0
        try:
            min_chg = float(cfg.get("market_scanner_min_change_pct", 0.3) or 0.3)
        except (TypeError, ValueError):
            min_chg = 0.3
        try:
            min_rng = float(cfg.get("market_scanner_min_range_pct", 1.0) or 1.0)
        except (TypeError, ValueError):
            min_rng = 1.0
        return pool_size, min_qv, min_chg, min_rng

    def _finalize_scanner_pool(self, api_syms: list[str], pool_size: int) -> list[str]:
        """بعد جلب ترتيب المنصة في خيط خلفي: ملء من القائمة أو من الـ combo."""
        out = [str(s or "").strip().upper() for s in (api_syms or []) if str(s or "").strip()]
        seen = set(out)
        if not out:
            combo = getattr(self, "symbol_combo", None)
            if combo is not None:
                for i in range(combo.count()):
                    sym = str(combo.itemText(i) or "").strip().upper()
                    if not sym.endswith("USDT") or sym in seen:
                        continue
                    seen.add(sym)
                    out.append(sym)
                    if len(out) >= pool_size:
                        break
        cur = str(getattr(self, "current_symbol", "") or "").strip().upper()
        if cur and cur.endswith("USDT") and cur not in seen:
            out.insert(0, cur)
        return out[:pool_size]

    def _start_market_scanner(self, show_popup_on_finish: bool = False):
        if self._scanner_loading:
            self._scanner_show_on_ready = self._scanner_show_on_ready or bool(show_popup_on_finish)
            return
        self._scanner_loading = True
        self._scanner_show_on_ready = bool(show_popup_on_finish)
        if hasattr(self, "ai_suggested_symbol_btn"):
            self.ai_suggested_symbol_btn.setEnabled(False)
            self.ai_suggested_symbol_btn.setText(tr("ai_suggested_symbol_loading"))
        pool_size, min_qv, min_chg, min_rng = self._read_scanner_pool_params()
        self._scanner_pool_thread = ScannerPoolLoaderThread(
            pool_size, min_qv, min_chg, min_rng, parent=self
        )
        self._scanner_pool_thread.pool_ready.connect(self._on_scanner_pool_ready)
        self._scanner_pool_thread.finished.connect(self._on_scanner_pool_thread_finished)
        self._scanner_pool_thread.start()

    def _on_scanner_pool_thread_finished(self):
        self._scanner_pool_thread = None

    def _on_scanner_pool_ready(self, api_syms: object):
        if not self._scanner_loading:
            return
        show_popup = self._scanner_show_on_ready
        pool_size, _, _, _ = self._read_scanner_pool_params()
        syms = list(api_syms) if isinstance(api_syms, list) else []
        symbols = self._finalize_scanner_pool(syms, pool_size)
        if not symbols:
            self._scanner_loading = False
            self._scanner_show_on_ready = False
            self._scanner_top10 = []
            if hasattr(self, "ai_suggested_symbol_btn"):
                self.ai_suggested_symbol_btn.setText("—")
                self.ai_suggested_symbol_btn.setEnabled(True)
            if show_popup:
                QMessageBox.information(self, tr("ai_suggested_symbol_title"), tr("ai_suggested_symbol_empty"))
            return
        self._scanner_thread = MarketScannerThread(symbols=symbols, interval=self._chart_interval, parent=self)
        self._scanner_thread.scan_ready.connect(self._on_market_scan_ready)
        self._scanner_thread.scan_error.connect(self._on_market_scan_error)
        self._scanner_thread.finished.connect(self._on_market_scan_finished)
        self._scanner_thread.start()

    def _on_market_scan_ready(self, rows: list):
        self._scanner_top10 = rows if isinstance(rows, list) else []
        top_symbol = "—"
        if self._scanner_top10:
            top_symbol = str(self._scanner_top10[0].get("symbol") or "—")
        if hasattr(self, "ai_suggested_symbol_btn"):
            self.ai_suggested_symbol_btn.setText(top_symbol)
            self.ai_suggested_symbol_btn.setEnabled(True)
        if self._scanner_show_on_ready:
            self._show_top10_suggestions()

    def _on_market_scan_error(self, _msg: str):
        self._scanner_top10 = []
        if hasattr(self, "ai_suggested_symbol_btn"):
            self.ai_suggested_symbol_btn.setEnabled(True)
            self.ai_suggested_symbol_btn.setText("—")
        if self._scanner_show_on_ready:
            QMessageBox.information(self, tr("ai_suggested_symbol_title"), tr("ai_suggested_symbol_failed"))

    def _on_market_scan_finished(self):
        self._scanner_loading = False
        self._scanner_show_on_ready = False
        th = getattr(self, "_scanner_thread", None)
        if th is not None:
            try:
                th.deleteLater()
            except Exception:
                pass
        self._scanner_thread = None

    def _show_top10_suggestions(self):
        rows = self._scanner_top10 if isinstance(self._scanner_top10, list) else []
        if not rows:
            QMessageBox.information(self, tr("ai_suggested_symbol_title"), tr("ai_suggested_symbol_empty"))
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("ai_suggested_symbol_top10_title"))
        dlg.setMinimumWidth(380)
        v = QVBoxLayout(dlg)
        lst = QListWidget(dlg)
        for i, row in enumerate(rows[:10], 1):
            sym = str(row.get("symbol") or "—")
            rec = str(row.get("recommendation") or "WAIT")
            conf = float(row.get("confidence") or 0.0)
            it = QListWidgetItem(f"{i}) {sym}  |  {rec}  |  {conf:.1f}%")
            it.setData(Qt.ItemDataRole.UserRole, sym)
            lst.addItem(it)
        if lst.count() > 0:
            lst.setCurrentRow(0)
        v.addWidget(lst)
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel, parent=dlg)
        pick_btn = btns.addButton(tr("ai_suggested_symbol_pick_btn"), QDialogButtonBox.ButtonRole.AcceptRole)
        fav_btn = btns.addButton(tr("ai_suggested_symbol_add_favorite_btn"), QDialogButtonBox.ButtonRole.ActionRole)
        v.addWidget(btns)

        def _pick_current():
            it = lst.currentItem()
            if it is None:
                return
            self._pick_suggested_symbol(str(it.data(Qt.ItemDataRole.UserRole) or ""))
            dlg.accept()

        def _add_favorite():
            it = lst.currentItem()
            if it is None:
                return
            self._add_suggested_symbol_to_favorites(str(it.data(Qt.ItemDataRole.UserRole) or ""))

        pick_btn.clicked.connect(_pick_current)
        fav_btn.clicked.connect(_add_favorite)
        btns.rejected.connect(dlg.reject)
        dlg.exec()

    def _pick_suggested_symbol(self, symbol: str):
        sym = str(symbol or "").strip().upper()
        if not sym:
            return
        if hasattr(self, "symbol_combo") and self.symbol_combo.findText(sym) >= 0:
            self.symbol_combo.setCurrentText(sym)
        self.change_symbol(sym)

    def _add_suggested_symbol_to_favorites(self, symbol: str):
        sym = str(symbol or "").strip().upper()
        if not sym:
            return
        cfg = load_config()
        fav_raw = cfg.get("favorite_symbols") or []
        fav = [str(s or "").strip().upper() for s in fav_raw if str(s or "").strip()]
        if sym in fav:
            self.status_bar_message.emit(tr("ai_suggested_symbol_already_favorite"))
            try:
                self._show_toast(
                    QMessageBox.Icon.Information,
                    tr("ai_suggested_symbol_title"),
                    tr("ai_suggested_symbol_already_favorite"),
                    msec=3500,
                )
            except Exception:
                pass
            return
        fav.insert(0, sym)
        cfg["favorite_symbols"] = fav[:50]
        save_config(cfg)
        try:
            symbols_now = [str(self.symbol_combo.itemText(i) or "").strip().upper() for i in range(self.symbol_combo.count())]
            ordered = self._symbols_with_favorites(symbols_now)
            cur = self.symbol_combo.currentText()
            self.symbol_combo.blockSignals(True)
            self.symbol_combo.set_items(ordered)
            if cur and self.symbol_combo.findText(cur) >= 0:
                self.symbol_combo.setCurrentText(cur)
            self.symbol_combo.blockSignals(False)
        except Exception:
            pass
        self.status_bar_message.emit(tr("ai_suggested_symbol_added_favorite").format(symbol=sym))
        try:
            self._show_toast(
                QMessageBox.Icon.Information,
                tr("ai_suggested_symbol_title"),
                tr("ai_suggested_symbol_added_favorite").format(symbol=sym),
                msec=3500,
            )
        except Exception:
            pass

    def _on_click_suggested_symbol(self):
        if self._scanner_loading:
            return
        self._start_market_scanner(show_popup_on_finish=True)

    def _select_symbol(self):
        from symbol_selector import SymbolSelector
        dialog = SymbolSelector()
        if dialog.exec() and dialog.selected_symbol:
            new_symbol = dialog.selected_symbol
            if self.symbol_combo.findText(new_symbol) < 0:
                self.symbol_combo.add_items([new_symbol])
            self.symbol_combo.setCurrentText(new_symbol)
            self._on_symbol_combo_changed(new_symbol)

    def _open_expected_symbols_dialog(self):
        from expected_symbols_dialog import ExpectedSymbolsDialog

        d = ExpectedSymbolsDialog(self)
        d.symbol_chosen.connect(self._apply_symbol_from_expected_list)
        d.favorites_changed.connect(self._reorder_symbol_combo_after_favorites_update)
        d.exec()

    def _reorder_symbol_combo_after_favorites_update(self):
        """بعد تعديل المفضلة من نافذة «المتوقعة» — إعادة ترتيب القائمة المنسدلة فقط."""
        try:
            combo = getattr(self, "symbol_combo", None)
            if combo is None:
                return
            symbols_now = [str(combo.itemText(i) or "").strip().upper() for i in range(combo.count())]
            ordered = self._symbols_with_favorites(symbols_now)
            cur = combo.currentText()
            combo.blockSignals(True)
            combo.set_items(ordered)
            if cur and combo.findText(cur) >= 0:
                combo.setCurrentText(cur)
            combo.blockSignals(False)
        except Exception:
            pass

    def _apply_symbol_from_expected_list(self, sym: str):
        sym = str(sym or "").strip().upper()
        if not sym:
            return
        if self.symbol_combo.findText(sym) < 0:
            self.symbol_combo.add_items([sym])
        self.symbol_combo.setCurrentText(sym)
        self._on_symbol_combo_changed(sym)

    def _format_amount_text(self, cfg: dict) -> str:
        at = cfg.get("amount_type", "value")
        if at == "percent":
            pct = cfg.get("amount_percent", 10.0)
            return f"{tr('main_amount')} {pct}%"
        return f"{tr('main_amount')} {cfg.get('amount_usdt', 50)}"

    def _format_market_type_text(self, cfg: dict) -> str:
        mt = (cfg.get("market_type") or "auto").strip().lower()
        if mt == "spot":
            return f"{tr('main_market_type')} {tr('market_type_spot_short')}"
        if mt == "futures":
            return f"{tr('main_market_type')} {tr('market_type_futures_short')}"
        return f"{tr('main_market_type')} {tr('market_type_auto_short')}"

    def _format_tp_text(self, cfg: dict) -> str:
        # زر التتبع — نص بسيط (يظهر كـ \"التتبع\" بالعربية)
        return tr("quick_trailing_title")

    def _format_sl_text(self, cfg: dict) -> str:
        # زر وقف الخسارة — نص عربي/إنجليزي من قاموس الترجمة
        return tr("quick_sl_title")

    def _format_limit_buy_text(self, cfg: dict) -> str:
        """عرض حد الشراء على الزر: النسبة أو السعر أو «معطّل»."""
        title = tr("quick_limit_buy_title")
        typ = (cfg.get("limit_buy_type") or "percent").strip() or "percent"
        if typ == "percent":
            v = float(cfg.get("limit_buy_value", -2.0) or -2.0)
            if v == 0:
                return f"{title} —"
            return f"{title} {v:+.1f}%"
        p = float(cfg.get("limit_buy_price", 0) or 0)
        if p <= 0:
            return f"{title} —"
        return f"{title} {format_price(p)} $"

    def _format_limit_sell_text(self, cfg: dict) -> str:
        """عرض حد البيع على الزر: النسبة أو السعر أو «معطّل»."""
        title = tr("quick_limit_sell_title")
        typ = (cfg.get("limit_sell_type") or "percent").strip() or "percent"
        if typ == "percent":
            v = float(cfg.get("limit_sell_value", 0.0) or 0.0)
            if v <= 0:
                return f"{title} —"
            return f"{title} +{v:.1f}%"
        p = float(cfg.get("limit_sell_price", 0) or 0)
        if p <= 0:
            return f"{title} —"
        return f"{title} {format_price(p)} $"

    def _refresh_quick_buttons(self, cfg: dict = None):
        """تحديث نصوص أزرار المبلغ، الرافعة، هدف الربح، وقف الخسارة، والبيع التلقائي."""
        if cfg is None:
            cfg = load_config()
        self.amount_btn.setText(self._format_amount_text(cfg))
        self.leverage_btn.setText(f"{tr('main_leverage')} {cfg.get('leverage', 10)}x")
        if hasattr(self, "market_type_btn") and self.market_type_btn:
            self.market_type_btn.setText(self._format_market_type_text(cfg))
        self._update_market_trade_mode_label(cfg)
        self.max_trades_btn.setText(_format_max_trades_quick_label(cfg))
        self.tp_btn.setText(self._format_tp_text(cfg))
        if hasattr(self, "limit_buy_btn") and self.limit_buy_btn:
            self.limit_buy_btn.setText(self._format_limit_buy_text(cfg))
        if hasattr(self, "limit_sell_btn") and self.limit_sell_btn:
            self.limit_sell_btn.setText(self._format_limit_sell_text(cfg))
        self.sl_btn.setText(self._format_sl_text(cfg))
        if hasattr(self, "auto_sell_btn") and self.auto_sell_btn:
            on = bool(cfg.get("bot_auto_sell", False))
            self.auto_sell_btn.blockSignals(True)
            self.auto_sell_btn.setChecked(on)
            self.auto_sell_btn.setText(tr("quick_auto_sell_on") if on else tr("quick_auto_sell_off"))
            self.auto_sell_btn.blockSignals(False)

    def _update_market_trade_mode_label(self, cfg: dict = None) -> None:
        if not hasattr(self, "market_trade_mode_label") or self.market_trade_mode_label is None:
            return
        key_lbl = getattr(self, "ai_trade_mode_key_label", None)
        if cfg is None:
            cfg = load_config()
        horizon = str(cfg.get("bot_trade_horizon", "short") or "short").strip().lower()
        if horizon not in ("short", "swing"):
            horizon = "short"
        if horizon == "short":
            horizon_txt = tr("risk_trade_horizon_short")
        else:
            horizon_txt = tr("risk_trade_horizon_swing")
        mode_prefix = tr("risk_trade_horizon_label").rstrip(":")
        mode_fg = "#ffd9b3"
        if key_lbl is not None:
            key_lbl.setText(mode_prefix)
        self.market_trade_mode_label.setText(horizon_txt)
        self.market_trade_mode_label.setStyleSheet(
            f"color: {mode_fg}; font-size: 11px; font-weight: bold;"
        )

    def _on_auto_sell_toggled(self, checked: bool):
        """تفعيل/إلغاء البيع التلقائي من زر إجراءات سريعة."""
        cfg = load_config()
        cfg["bot_auto_sell"] = checked
        save_config(cfg)
        self._refresh_quick_buttons(cfg)

    def _open_amount_dialog(self):
        try:
            d = AmountDialog(self)
            d.config_saved.connect(self._refresh_quick_buttons)
            if d.exec():
                self._refresh_quick_buttons(load_config())
        except Exception as e:
            log.exception("Amount dialog failed")
            QMessageBox.critical(self, tr("trading_robot_title"), str(e))

    def _open_leverage_dialog(self):
        try:
            d = LeverageDialog(self)
            d.config_saved.connect(self._refresh_quick_buttons)
            if d.exec():
                self._refresh_quick_buttons(load_config())
        except Exception as e:
            log.exception("Leverage dialog failed")
            QMessageBox.critical(self, tr("trading_robot_title"), str(e))

    def _open_market_type_dialog(self):
        try:
            d = MarketTypeDialog(self)
            d.config_saved.connect(self._refresh_quick_buttons)
            if d.exec():
                self._refresh_quick_buttons(load_config())
                QTimer.singleShot(400, self._sync_open_positions_from_exchange)
        except Exception as e:
            log.exception("Market type dialog failed")
            QMessageBox.critical(self, tr("trading_robot_title"), str(e))

    def _open_max_trades_dialog(self):
        try:
            d = MaxTradesDialog(self)
            d.config_saved.connect(self._refresh_quick_buttons)
            if d.exec():
                cfg = load_config()
                self._BOT_MAX_OPEN_TRADES = int(cfg.get("bot_max_open_trades", 1))
                self._refresh_quick_buttons(cfg)
        except Exception as e:
            log.exception("Max trades dialog failed")
            QMessageBox.critical(self, tr("trading_robot_title"), str(e))

    def _open_tp_dialog(self):
        try:
            d = TPDialog(self)
            d.config_saved.connect(self._refresh_quick_buttons)
            if d.exec():
                self._refresh_quick_buttons(load_config())
        except Exception as e:
            log.exception("TP dialog failed")
            QMessageBox.critical(self, tr("trading_robot_title"), str(e))

    def _open_limit_buy_dialog(self):
        try:
            ref = float(getattr(self, "_last_price", 0) or 0)
            d = LimitBuyDialog(self, ref_price=ref)
            d.config_saved.connect(self._refresh_quick_buttons)
            if d.exec():
                self._limit_buy_pct_runtime_anchor = None
                self._refresh_quick_buttons(load_config())
        except Exception as e:
            log.exception("Limit buy dialog failed")
            QMessageBox.critical(self, tr("trading_robot_title"), str(e))

    def _open_limit_sell_dialog(self):
        try:
            from quick_settings_dialogs import LimitSellDialog
            d = LimitSellDialog(self)
            d.config_saved.connect(self._refresh_quick_buttons)
            if d.exec():
                self._refresh_quick_buttons(load_config())
        except Exception as e:
            log.exception("Limit sell dialog failed")
            QMessageBox.critical(self, tr("trading_robot_title"), str(e))

    def _open_sl_dialog(self):
        try:
            d = SLDialog(self)
            d.config_saved.connect(self._refresh_quick_buttons)
            if d.exec():
                self._refresh_quick_buttons(load_config())
        except Exception as e:
            log.exception("SL dialog failed")
            QMessageBox.critical(self, tr("trading_robot_title"), str(e))

    def _update_strategy_display(self, cfg: dict, show_change_message: bool = False):
        """تحديث سطر «الاستراتيجية الحالية» في حالة السوق عند فتح البرنامج أو بعد حفظ إعدادات المخاطر."""
        mode = (cfg or {}).get("strategy_mode", "custom") or "custom"
        if mode == "auto":
            ind = self._last_indicators if isinstance(getattr(self, "_last_indicators", None), dict) else {}
            pending = not ind or float(ind.get("close", 0) or 0) <= 0
            if pending:
                name = tr("risk_strategy_auto_prefix") + " — " + tr("risk_strategy_auto_pending_data")
                disp_en = tr_en("risk_strategy_auto_prefix") + " — " + tr_en("risk_strategy_auto_pending_data")
            else:
                name = tr("risk_strategy_auto")
                disp_en = tr_en("risk_strategy_auto")
            tip = name
        else:
            sk = {
                "custom": "risk_strategy_custom",
                "scalping": "risk_strategy_scalping",
                "bounce": "risk_strategy_bounce",
                "trend": "risk_strategy_trend",
                "dca": "risk_strategy_dca",
                "grid": "risk_strategy_grid",
                "3commas": "risk_strategy_3commas",
                "breakout": "risk_strategy_breakout",
            }.get(mode, "risk_strategy_custom")
            name = tr(sk)
            disp_en = tr_en(sk)
            tip = name
        if hasattr(self, "strategy_display_label") and self.strategy_display_label:
            # القيمة بالإنجليزي فقط في اللوحة؛ التلميح والشريط يبقيان بلغة الواجهة
            self.strategy_display_label.setText(("\u200e" + str(disp_en)) if disp_en else "")
            self.strategy_display_label.setToolTip(tip)
        if show_change_message and hasattr(self, "status_bar_message"):
            self.status_bar_message.emit(tr("risk_strategy_changed_to").format(name=name))

    def _refresh_last_trade_display(self):
        """تحميل آخر صفقة مغلقة من السجل وعرضها في لوحة التوصية."""
        if not hasattr(self, "last_trade_label") or not self.last_trade_label:
            return
        try:
            pnl = get_last_closed_trade_pnl()
            # يُزامن مع _update_open_position_display: إن بقي None يُمسح السطر كل 500ms رغم وجود سجل في الملف.
            self._last_realized_pnl = float(pnl) if pnl is not None else None
            if pnl is not None:
                self.last_trade_label.setText(f"{pnl:+.2f}")
                self.last_trade_label.setStyleSheet(
                    f"color: {UI_GREEN}; font-weight: bold; font-size: 12px;" if pnl >= 0 else f"color: {UI_RED}; font-weight: bold; font-size: 12px;"
                )
            else:
                self.last_trade_label.setText("—")
                self.last_trade_label.setStyleSheet("color: #aaa; font-weight: bold; font-size: 12px;")
        except Exception:
            self._last_realized_pnl = None
            self.last_trade_label.setText("—")
            self.last_trade_label.setStyleSheet("color: #aaa; font-weight: bold; font-size: 12px;")

    def update_risk_display(self, cfg: dict):
        """تحديث نصوص المبلغ والرافعة وTP/SL وحد ثقة البوت بعد حفظ إعدادات المخاطر."""
        self._BOT_CONFIDENCE_MIN = float(cfg.get("bot_confidence_min", 60))
        try:
            self._BOT_MAX_OPEN_TRADES = int(cfg.get("bot_max_open_trades", 1))
        except (TypeError, ValueError):
            self._BOT_MAX_OPEN_TRADES = 1
        self._refresh_quick_buttons(cfg)
        self._update_strategy_display(cfg)
        self._refresh_bot_exec_confidence_display(
            str(getattr(self, "_last_panel_recommendation", "WAIT") or "WAIT"),
            float(getattr(self, "_last_panel_confidence", 0) or 0),
            None,
            None,
        )

    def _on_ai_panel_refresh_clicked(self) -> None:
        """إعادة قراءة الإعدادات من القرص وتحديث التوصية/حالة السوق دون انتظار المؤقّت."""
        try:
            invalidate_config_disk_cache()
            cfg = load_config()
            self.update_risk_display(cfg)
            self._refresh_indicator_profile_button_style()
            self._update_mode_badge()
            self._update_consecutive_losses_badge()
            self._update_cb_badge()
            ind = getattr(self, "_last_indicators", None)
            if isinstance(ind, dict) and ind:
                self._sync_top_recommendation_panel(ind, cfg)
                self._update_market_indicators_display()
            self._update_market_info_display()
            try:
                self.status_bar_message.emit(tr("ai_panel_refresh_done"))
            except Exception:
                pass
        except Exception:
            log.warning("ai panel refresh failed", exc_info=True)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_side_column_widths()
        self._apply_quick_actions_responsive()

    def _sync_side_column_widths(self) -> None:
        """إجراءات سريعة: عرض واسع للأزرار. لوحة التوصية: عرض منفصل حتى لا تُلفّ العناوين العربية."""
        w = int(self.width() or 0)
        w_eff = max(640, w)
        q_min = max(185, min(245, int(round(w_eff * 0.195))))
        q_max = max(260, min(480, int(round(w_eff * 0.36))))
        if q_max < q_min + 50:
            q_max = q_min + 50
        qg = getattr(self, "_quick_group", None)
        if qg is not None:
            qg.setMinimumWidth(q_min)
            qg.setMaximumWidth(q_max)
        ai_min = max(195, min(248, int(round(w_eff * 0.192))))
        ai_max = max(255, min(360, int(round(w_eff * 0.33))))
        if ai_max < ai_min + 50:
            ai_max = ai_min + 50
        ag = getattr(self, "_ai_group", None)
        if ag is not None:
            ag.setMinimumWidth(ai_min)
            ag.setMaximumWidth(ai_max)

    def _apply_quick_actions_responsive(self):
        """تصغير/تكبير قسم إجراءات سريعة (عرض + خط + ارتفاع) حسب حجم النافذة."""
        qg = getattr(self, "_quick_group", None)
        ql = getattr(self, "_quick_layout", None)
        if not qg or not ql:
            return

        # مفتاح بسيط لمنع إعادة تطبيق الستايل بشكل مبالغ فيه
        w = max(650, int(self.width() or 0))
        scale = max(0.80, min(1.05, w / 1024.0))
        # إعادة حجم الخط كما كان داخل "إجراءات سريعة"
        font_px = int(round(10 * scale))
        btn_h = int(round(22 * scale))
        btn_h = max(18, min(26, btn_h))
        key = (font_px, btn_h, int(round(scale * 100)))
        if getattr(self, "_quick_last_scale_key", None) == key:
            return
        self._quick_last_scale_key = key

        ql.setSpacing(8)
        ql.setContentsMargins(6, 8, 6, 8)

        pad_h = max(5, int(round(7 * scale)))
        font_px = max(9, font_px)
        bar_font = f"font-family: Segoe UI, Arial; font-size: {font_px}px;"
        # خط وإطار الصندوق الخارجي كـ #AIPanelGroup (ثابت 10px + TOP_PANEL_PAD)
        _quick_panel_font = "font-family: Segoe UI, Arial; font-size: 10px;"
        quick_btn_style = (
            f"background-color: {TOP_INNER_BG}; color: {TOP_TEXT_PRIMARY}; border: 1px solid {TOP_INNER_BORDER};"
            # نفس زوايا صفوف لوحة التوصية (8px)
            f"border-radius: 8px; padding: 0px {pad_h}px; font-weight: normal; min-height: {btn_h}px;"
            f"font-size: {font_px}px;"
        )
        quick_btn_style_hover = ""
        # #QuickActionsGroup فقط — لا تُطبَّق قواعد الصندوق على أي QGroupBox داخلي مستقبلاً
        qg.setStyleSheet(
            f"#QuickActionsGroup {{ {_quick_panel_font} background-color: {TOP_PANEL_BG}; border: 1px solid {TOP_PANEL_BORDER}; "
            f"border-radius: {TOP_PANEL_RADIUS}; padding: {TOP_PANEL_PAD}; }} "
            f"#QuickActionsGroup::title {{ color: {TOP_PANEL_TITLE}; subcontrol-origin: margin; left: 12px; padding: 0 8px; font-weight: bold; }} "
            f"#QuickActionsGroup QComboBox {{ {bar_font} background-color: {TOP_INNER_BG}; color: {TOP_TEXT_PRIMARY}; "
            f"border: 1px solid {TOP_INNER_BORDER}; border-radius: 8px; }} "
            f"#QuickActionsGroup QLabel {{ {bar_font} color: {TOP_TEXT_MUTED}; }} "
            f"#QuickActionsGroup QPushButton {{ font-size: {font_px}px; padding-top: 0px; padding-bottom: 0px; }} "
            f"#QuickActionsGroup QPushButton:hover {{ background-color: {TOP_PANEL_BORDER}; border-color: {TOP_INNER_BORDER}; }} "
            f"#QuickActionsGroup QPushButton:pressed {{ background-color: {TOP_PANEL_BG}; border-color: {TOP_INNER_BORDER}; }} "
            "#QuickActionsGroup QPushButton#QuickBuyButton { "
            f"  background-color: {UI_GREEN}; color: white; border: 1px solid #4ade80; "
            f"  border-radius: 8px; padding: 0px {pad_h}px; font-weight: bold; min-height: {btn_h}px; "
            "} "
            f"#QuickActionsGroup QPushButton#QuickBuyButton:hover {{ background-color: #16a34a; border-color: {UI_GREEN}; }} "
            "#QuickActionsGroup QPushButton#QuickBuyButton:pressed { background-color: #15803d; } "
            "#QuickActionsGroup QPushButton#QuickSellButton { "
            f"  background-color: {UI_RED_DARK}; color: white; border: 1px solid {UI_RED_DEEP}; "
            f"  border-radius: 8px; padding: 0px {pad_h}px; font-weight: bold; min-height: {btn_h}px; "
            "} "
            f"#QuickActionsGroup QPushButton#QuickSellButton:hover {{ background-color: {UI_RED_DEEP}; border-color: #991b1b; }} "
            "#QuickActionsGroup QPushButton#QuickSellButton:pressed { background-color: #991b1b; border-color: #7f1d1d; } "
        )

        # أزرار رمادية
        for b in (
            getattr(self, "amount_btn", None),
            getattr(self, "leverage_btn", None),
            getattr(self, "market_type_btn", None),
            getattr(self, "max_trades_btn", None),
            getattr(self, "tp_btn", None),
            getattr(self, "limit_buy_btn", None),
            getattr(self, "limit_sell_btn", None),
            getattr(self, "sl_btn", None),
            getattr(self, "auto_sell_btn", None),
            getattr(self, "risk_button", None),
        ):
            if b:
                b.setFixedHeight(btn_h)
                b.setStyleSheet(quick_btn_style + quick_btn_style_hover)

        # Combos
        for cb in (getattr(self, "exchange_combo", None), getattr(self, "interval_combo", None), getattr(self, "symbol_combo", None)):
            if cb:
                cb.setFixedHeight(btn_h)
                cb.setStyleSheet(
                    f"background-color: {TOP_INNER_BG}; color: {TOP_TEXT_PRIMARY}; border: 1px solid {TOP_INNER_BORDER};"
                    f"border-radius: 8px; padding: 0px {max(4, pad_h - 1)}px; min-height: {btn_h}px;"
                    f"{bar_font}"
                )

        more_btn = getattr(self, "btn_symbol_more", None)
        if more_btn:
            more_btn.setFixedSize(btn_h, btn_h)
            more_btn.setStyleSheet(quick_btn_style + quick_btn_style_hover)
        exp_btn = getattr(self, "btn_expected_symbols", None)
        if exp_btn:
            exp_btn.setFixedHeight(btn_h)
            exp_btn.setStyleSheet(quick_btn_style + quick_btn_style_hover)

    # ============================================================
    # تغيير العملة
    # ============================================================
    def _apply_chart_interval(self):
        """ربط إطار الشموع المختار بالواجهة. مؤشرات 1m و 1h و 4h تُستقبل دائماً للبوت والتحليل (سكالبينغ + اتجاه)."""
        if not hasattr(self, "ws") or self.ws is None:
            return
        iv = self._chart_interval
        self.ws.set_callbacks(
            iv,
            on_price=lambda p: self._ws_price.emit(iv, p),
            on_candles=lambda c: self._ws_candles.emit(iv, c),
            on_indicators=lambda i: self._ws_indicators.emit(iv, i),
            on_market_info=lambda m: self._ws_market_info.emit(iv, m),
        )
        frames = getattr(self.ws, "frames", {})
        # 1m: قرارات فورية وسكالبينغ — 1h/4h: اتجاه واتساق (تُخزَّن للاستخدام لاحقاً أو عرض متعدد الأطر)
        for always_iv in ("1m", "1h", "4h"):
            if always_iv != iv and frames.get(always_iv) is not None:
                frames[always_iv].on_indicators = lambda i, _iv=always_iv: self._ws_indicators.emit(_iv, i)
        log.info("Chart interval: %s", iv)

    def _on_interval_changed(self, text: str):
        if text and text != self._chart_interval and text in ("1m", "5m", "15m", "1h", "4h", "1d"):
            self._chart_interval = text
            self._apply_chart_interval()
            self._start_market_scanner(show_popup_on_finish=False)

    def change_symbol(self, symbol: str):
        sym_u = str(symbol or "").strip().upper()
        ul = getattr(self, "_bot_etoro_unlisted_symbol", None)
        if ul and sym_u != ul:
            self._bot_etoro_unlisted_symbol = None
        self.current_symbol = symbol
        # جدول المراكز يحقن سعر الشارت في _price_by_symbol بمفتاح current_symbol — يجب أن يطابق رمز الشارت هنا دائماً
        pp = getattr(self, "_positions_panel", None)
        if pp is not None:
            try:
                pp.current_symbol = str(symbol or "").strip().upper() or pp.current_symbol
            except Exception:
                pass
        # إعادة تعيين آخر سعر حتى لا يُقارن سعر الرمز السابق عند أول تيك للرمز الجديد
        self._last_price = 0.0
        local_day_invalidate_symbol(symbol)
        self._mtf_close_history_1h.clear()
        self._mtf_close_history_4h.clear()
        self._last_indicators_1h = None
        self._last_indicators_4h = None
        self._mtf_spark_last_t_open_1h = 0
        self._mtf_spark_last_t_open_4h = 0
        try:
            self._update_mtf_htf_readout()
        except Exception:
            pass
        self._local_day_ref = None
        self._local_day_anchor_local_date = ""
        self._local_day_fetching = False
        self._local_day_fetch_fail_date = ""
        if hasattr(self, "price_label") and self.price_label:
            self._set_price_label_style_neutral()
        if hasattr(self, "price_day_pct_label") and self.price_day_pct_label:
            self.price_day_pct_label.setText("—")
            self.price_day_pct_label.setStyleSheet(self._day_pct_label_stylesheet(TOP_TEXT_MUTED))
        if hasattr(self, "symbol_combo") and self.symbol_combo.currentText() != symbol:
            self.symbol_combo.blockSignals(True)
            if self.symbol_combo.findText(symbol) >= 0:
                self.symbol_combo.setCurrentText(symbol)
            self.symbol_combo.blockSignals(False)
        # إيقاف WebSocket الحالي بأمان قبل إنشاء اتصال جديد
        if hasattr(self, "ws") and self.ws is not None:
            stop_fn = getattr(self.ws, "stop", None)
            if callable(stop_fn):
                try:
                    stop_fn()
                except Exception as e:
                    log.debug("ws.stop() error (ignored): %s", e)

        stream_sym = binance_kline_stream_symbol(symbol)
        self.ws = WebSocketManager(symbol=stream_sym)
        self._apply_chart_interval()
        self.ws.start()

        if stream_sym != symbol.strip().lower().replace(" ", ""):
            log.info("Switched to symbol: %s (شارت Binance: %s)", symbol, stream_sym.upper())
        else:
            log.info("Switched to symbol: %s", symbol)
        self._warn_if_etoro_symbol_not_in_allowlist(sym_u)
        if hasattr(self, "_last_price"):
            self._update_open_position_display(self._last_price)
        self._emit_status_message()
        QTimer.singleShot(400, self._schedule_local_day_anchor_fetch)

    def set_positions_panel(self, panel):
        """ربط لوحة المراكز المفتوحة لعرض الصفقة في حالة السوق."""
        self._positions_panel = panel
        if panel is not None:
            try:
                panel.current_symbol = str(getattr(self, "current_symbol", None) or "").strip().upper() or getattr(
                    panel, "current_symbol", "BTCUSDT"
                )
            except Exception:
                pass
        if panel is not None and hasattr(panel, "close_row_requested"):
            try:
                panel.close_row_requested.disconnect()
            except TypeError:
                pass
            panel.close_row_requested.connect(self.close_single_row_action)
        self._hydrate_etoro_recent_closed_from_disk()
        QTimer.singleShot(400, self._restore_etoro_positions_from_cache)
        self._refresh_open_positions_count_label()

    def _count_open_position_rows(self) -> int:
        """عدد صفوف المراكز ذات كمية > 0 (عرض خام — للتوافق)."""
        if self._positions_panel is None:
            return 0
        n = 0
        for row in range(self._positions_panel.table.rowCount()):
            try:
                qty_item = self._positions_panel.table.item(row, 2)
                qty = float(qty_item.text() or 0) if qty_item else 0.0
            except (ValueError, TypeError, AttributeError):
                qty = 0.0
            if qty > 0:
                n += 1
        return n

    def _logical_open_count_for_bot(self) -> int:
        """عدد مراكز منطقي + أي شراء قيد التنفيذ (حد الصفقات / البوت)."""
        base = 0
        if self._positions_panel is not None:
            try:
                base = int(self._positions_panel.logical_open_row_count())
            except Exception:
                base = self._count_open_position_rows()
        pend = max(0, int(getattr(self, "_pending_buy_order_count", 0) or 0))
        return base + pend

    def _parse_table_price_cell(self, text: str) -> float | None:
        """تحويل نص عمود سعر من الجدول إلى float (يتجاهل الفواصل)."""
        try:
            t = (text or "").strip().replace(",", "")
            if not t or t in ("-", "—"):
                return None
            return float(t)
        except (TypeError, ValueError):
            return None

    def _sum_open_positions_notional_usdt(self) -> float:
        """مجموع (كمية × سعر لحظي) لكل صف مركز مفتوح — لتقدير التعرّض."""
        if self._positions_panel is None:
            return 0.0
        total = 0.0
        tbl = self._positions_panel.table
        for row in range(tbl.rowCount()):
            try:
                qty_item = tbl.item(row, 2)
                cur_item = tbl.item(row, 4)
                entry_item = tbl.item(row, 1)
                qty = float(qty_item.text() or 0) if qty_item else 0.0
                if qty <= 0:
                    continue
                px = self._parse_table_price_cell(cur_item.text() if cur_item else "")
                if px is None or px <= 0:
                    ep = self._parse_table_price_cell(entry_item.text() if entry_item else "")
                    px = ep if ep is not None and ep > 0 else None
                if px is not None and px > 0:
                    total += qty * px
            except (TypeError, ValueError, AttributeError):
                continue
        return total

    def _planned_buy_notional_usdt(self, cfg: dict, last_price: float) -> float:
        """نوشنال صفقة شراء قادمة (USDT) — يطابق منطق _execute_real_order للكمية."""
        if last_price <= 0:
            return 0.0
        leverage = max(1, int(cfg.get("leverage", 1) or 1))
        use_futures = _config_use_futures(cfg)
        amt_type = (cfg.get("amount_type") or "value").strip().lower()
        margin = 0.0
        if amt_type == "percent":
            bal = getattr(self, "_cached_usdt_balance", None)
            if bal is None or float(bal) <= 0:
                return 0.0
            pct = float(cfg.get("amount_percent", 10.0) or 10.0)
            margin = float(bal) * (pct / 100.0)
        else:
            margin = float(cfg.get("amount_usdt", 50) or 50)
        if margin <= 0:
            return 0.0
        if use_futures:
            return float(margin) * float(leverage)
        return float(margin)

    def _portfolio_exposure_allows_buy(self, cfg: dict, last_price: float) -> tuple[bool, str]:
        """True إذا لم يُضبط سقف التعرّض أو لم يُتجاوَز."""
        cap = float(cfg.get("portfolio_max_exposure_usdt", 0) or 0)
        if cap <= 0:
            return True, ""
        open_n = self._sum_open_positions_notional_usdt()
        planned = self._planned_buy_notional_usdt(cfg, last_price)
        if planned <= 0:
            return True, ""
        if open_n + planned > cap + 1e-6:
            return (
                False,
                f"Waiting — portfolio exposure cap ({open_n:.0f}+{planned:.0f}>{cap:.0f} USDT)",
            )
        return True, ""

    def _count_open_rows_matching_symbol(self, symbol: str) -> int:
        """عدد المراكز المنطقية للرمز (دمج تكرار position_id/order_id) — max_trades_per_symbol."""
        su = str(symbol or "").strip().upper()
        if not su or self._positions_panel is None:
            return 0
        try:
            return int(self._positions_panel.logical_rows_for_symbol_count(su))
        except Exception:
            return 0

    def _refresh_open_positions_count_label(self) -> None:
        """عدد الصفقات ذات كمية > 0 في جدول المراكز — يُعرض في لوحة التوصية."""
        if not hasattr(self, "ai_open_positions_value_label") or self.ai_open_positions_value_label is None:
            return
        try:
            count = (
                self._positions_panel.logical_open_row_count()
                if self._positions_panel is not None
                else 0
            )
        except Exception:
            count = self._count_open_position_rows()
        self.ai_open_positions_value_label.setText(str(int(count)))
        if count > 0:
            self.ai_open_positions_value_label.setStyleSheet(
                f"color: {TOP_TEXT_PRIMARY}; font-weight: bold; font-size: 11px;"
            )
        else:
            self.ai_open_positions_value_label.setStyleSheet(
                f"color: {TOP_TEXT_SECONDARY}; font-weight: bold; font-size: 11px;"
            )

    def _sync_open_positions_from_exchange(self):
        """جلب المراكز المفتوحة من المنصة (حسب الوضع الحالي حقيقي/وهمي) وعرضها.
        Binance/Bitget: مسار العقود فقط (Futures). eToro: دائماً — نفس /pnl."""
        if self._positions_panel is None:
            return
        testnet = not self.is_real_mode()
        cfg = load_config()
        use_futures = _config_use_futures(cfg)
        exchange = (cfg.get("exchange") or "binance").lower()

        if not use_futures and exchange != "etoro":
            msg = (
                "تحديث المراكز من المنصة يتطلب وضع العقود (Futures). "
                "اضغط «السوق» واختر «عقود»، أو اجعل النوع «تلقائي» مع رافعة > 1."
            )
            self.status_bar_message.emit(msg)
            return

        self.status_bar_message.emit("جاري تحديث المراكز من المنصة…")
        try:
            # Binance Futures sync
            if exchange == "binance":
                api_key, api_secret = get_decrypted_credentials(self, testnet=testnet)
                if not (api_key and api_secret):
                    self.status_bar_message.emit("إعدادات API غير مكتملة — أضِ مفاتيح المنصة من الإعدادات.")
                    return
                client = BinanceFuturesClient(api_key, api_secret, testnet=testnet)
                positions = client.get_open_positions()
                # لا تفرّغ الجدول إذا رجعت المنصة قائمة فارغة بشكل مؤقت (مشكلة اتصال/صلاحيات)
                if positions is not None and len(positions) > 0:
                    self._positions_panel.set_positions_from_exchange(positions)
                    self.status_bar_message.emit(f"تم تحديث المراكز — {len(positions)} مركز من Binance.")
                else:
                    self.status_bar_message.emit("لا توجد مراكز مفتوحة على Binance (أو فشل الاتصال).")
            # eToro sync (Demo/Real حسب وضع التداول)
            elif exchange == "etoro":
                et_user, et_key = load_etoro_settings()
                if not (et_user and et_key):
                    self.status_bar_message.emit("مفاتيح eToro غير مكتملة — أضِها من إعدادات API.")
                    return
                client = EtoroFuturesClient(et_user, et_key, testnet=testnet)
                raw = client.get_positions()
                self._etoro_prune_recent_closed_position_ids()
                _now_skip = time.time()
                positions = []
                for item in raw or []:
                    try:
                        row = etoro_row_from_pnl_item(item) or etoro_row_from_pnl_item_minimal(item)
                        if not row:
                            continue
                        try:
                            rp = row.get("position_id")
                            if rp is not None:
                                rpi = int(rp)
                                if rpi > 0:
                                    exp = float(
                                        (getattr(self, "_etoro_recent_closed_position_ids", None) or {}).get(
                                            rpi, 0.0
                                        )
                                    )
                                    if exp > _now_skip:
                                        continue
                        except (TypeError, ValueError):
                            pass
                        oids_blk = getattr(self, "_etoro_recent_closed_order_ids", None) or {}
                        oid_chk = row.get("order_id")
                        if oid_chk is None and isinstance(item, dict):
                            oid_chk = etoro_extract_open_order_id(item)
                        if oid_chk is not None:
                            try:
                                oi = int(oid_chk)
                                if oi > 0 and float(oids_blk.get(oi, 0) or 0) > _now_skip:
                                    continue
                            except (TypeError, ValueError):
                                pass
                        positions.append(row)
                    except Exception:
                        continue
                rows_now = self._positions_panel.table.rowCount() if self._positions_panel else 0
                if positions is not None and len(positions) > 0:
                    # eToro غالباً يُرجع مركزاً واحداً قبل أن يظهر الثاني — الاستبدال الكامل كان يحذف الصف الجديد
                    # أي صف محلي: ندمج دائماً حتى لا يُضاف مركز API كصف ثانٍ بجانب صف «بعد الشراء» لنفس الصفقة.
                    use_merge = rows_now > 0 or rows_now > len(positions) or (
                        hasattr(self._positions_panel, "has_pending_etoro_open_rows")
                        and self._positions_panel.has_pending_etoro_open_rows()
                    )
                    if use_merge and hasattr(self._positions_panel, "merge_etoro_positions_from_exchange"):
                        self._positions_panel.merge_etoro_positions_from_exchange(positions)
                    else:
                        self._positions_panel.set_positions_from_exchange(positions)
                    rows_after = self._positions_panel.table.rowCount() if self._positions_panel else 0
                    log.info(
                        "eToro sync: api_positions=%s rows_before=%s rows_after=%s mode=%s",
                        len(positions),
                        rows_now,
                        rows_after,
                        "merge" if use_merge else "replace",
                    )
                    # لا تستبدل كاش التشغيل ببيانات أقل أثناء الاستجابة المؤقتة الناقصة.
                    if rows_now <= len(positions):
                        for p in (_etoro_positions_cache_path(), _etoro_positions_cache_backup_path()):
                            try:
                                with open(p, "w", encoding="utf-8") as cf:
                                    json.dump(positions, cf, indent=2, ensure_ascii=False)
                            except OSError:
                                pass
                    self.status_bar_message.emit(f"تم تحديث المراكز — {len(positions)} مركز من eToro.")
                    try:
                        n_bf = backfill_missing_buys_from_etoro_positions(
                            positions,
                            mode="testnet" if testnet else "live",
                            to_hist_symbol=self._trade_history_symbol,
                        )
                        if n_bf > 0:
                            self.history_refresh_requested.emit()
                            log.info("eToro sync: trade history backfill changes=%s", n_bf)
                    except Exception as e:
                        log.debug("eToro trade history backfill: %s", e)
                else:
                    # eToro غالباً يُرجع قوائم فارغة لثوانٍ/دقائق (خصوصاً مع CFD/الرافعة).
                    # لا نُفرّغ الجدول أبداً عند استجابة فارغة — وإلا يختفي الصف ولا يُحسب الربح ويفشل البوت (لا مركز).
                    has_rows = bool(
                        self._positions_panel
                        and self._positions_panel.table.rowCount() > 0
                    )
                    if has_rows:
                        kept_rows = self._positions_panel.table.rowCount() if self._positions_panel else 0
                        log.info(
                            "eToro sync: api_positions=0 kept_local_rows=%s (protect from transient empty response)",
                            kept_rows,
                        )
                        self.status_bar_message.emit(
                            "مزامنة eToro: API بدون مراكز — الإبقاء على الصفوف المحلية (تحقق Demo/Live والمفاتيح؛ أو حدّث لاحقاً)."
                        )
                        if hasattr(self, "_last_price") and self._last_price:
                            self._update_open_position_display(self._last_price)
                        return
                    if raw and len(raw) > 0:
                        self.status_bar_message.emit(
                            "eToro أعاد بيانات مراكز لكن التطبيق لم يتعرّف على الصيغة. تحقق من المفتاح والحساب (Demo/Real)."
                        )
                    else:
                        self.status_bar_message.emit(
                            "لا مراكز من eToro (أو فشل الاتصال). تحقق من المفتاح والحساب Demo/Real."
                        )
            else:
                self.status_bar_message.emit("تحديث المراكز غير مدعوم لهذه المنصة.")
                return

            if hasattr(self, "_last_price") and self._last_price:
                self._update_open_position_display(self._last_price)
        except Exception as e:
            log.warning("Sync open positions failed: %s", e)
            self.status_bar_message.emit(f"فشل تحديث المراكز: {e}")

    def _etoro_prune_recent_closed_position_ids(self) -> None:
        now = time.time()
        d = getattr(self, "_etoro_recent_closed_position_ids", None) or {}
        for k in [x for x, exp in d.items() if float(exp or 0) <= now]:
            d.pop(k, None)
        o = getattr(self, "_etoro_recent_closed_order_ids", None) or {}
        for k in [x for x, exp in o.items() if float(exp or 0) <= now]:
            o.pop(k, None)

    def _persist_etoro_closed_filters(self) -> None:
        try:
            self._etoro_prune_recent_closed_position_ids()
            maps = _read_all_etoro_closed_maps()
            mk = "testnet" if not self.is_real_mode() else "live"
            maps[mk] = (
                dict(getattr(self, "_etoro_recent_closed_position_ids", {}) or {}),
                dict(getattr(self, "_etoro_recent_closed_order_ids", {}) or {}),
            )
            _write_all_etoro_closed_maps(maps)
        except Exception:
            pass

    def _etoro_mark_recent_closed_position(self, position_id: int | None) -> None:
        """يمنع إعادة إظهار مركز أُغلق للتو عندما تتأخر eToro عن تحديث قائمة المراكز.
        يُحفظ على القرص لأن إعادة تشغيل التطبيق كانت تُصفّر الذاكرة فيُعاد المركز من /pnl."""
        try:
            pid = int(position_id) if position_id is not None else 0
        except (TypeError, ValueError):
            pid = 0
        if pid <= 0:
            return
        if not hasattr(self, "_etoro_recent_closed_position_ids"):
            self._etoro_recent_closed_position_ids = {}
        self._etoro_recent_closed_position_ids[pid] = time.time() + _ETORO_RECENT_CLOSE_FILTER_SEC
        self._persist_etoro_closed_filters()

    def _etoro_mark_recent_closed_order(self, order_id: int | None) -> None:
        try:
            oid = int(order_id) if order_id is not None else 0
        except (TypeError, ValueError):
            oid = 0
        if oid <= 0:
            return
        if not hasattr(self, "_etoro_recent_closed_order_ids"):
            self._etoro_recent_closed_order_ids = {}
        self._etoro_recent_closed_order_ids[oid] = time.time() + _ETORO_RECENT_CLOSE_FILTER_SEC
        self._persist_etoro_closed_filters()

    def _etoro_trade_recently_closed_blocks_sl(self, trade: dict | None) -> bool:
        """مركز أُغلق للتو (مُسجّل في التصفية) — لا نُعيد تشغيل وقف الخسارة لنفس position_id."""
        if not trade or (load_config().get("exchange") or "").lower() != "etoro":
            return False
        try:
            pid = trade.get("position_id")
            if pid is None:
                return False
            pi = int(pid)
            if pi <= 0:
                return False
            exp = float((getattr(self, "_etoro_recent_closed_position_ids", None) or {}).get(pi, 0))
            return exp > time.time()
        except Exception:
            return False

    @staticmethod
    def _etoro_close_msg_implies_no_exchange_position(msg: str) -> bool:
        """خطأ إغلاق يعني غالباً لا مركز قابل للإغلاق (814، داخلي فقط، إلخ)."""
        if not msg:
            return False
        ml = msg.lower()
        if "814" in msg or "errorcode=814" in ml:
            return True
        if "internal only" in ml:
            return True
        if "لم يُنشئ مركز" in msg:
            return True
        if "did not create" in ml and "position" in ml:
            return True
        return False

    def _etoro_sl_suppress_until_map(self) -> dict[str, float]:
        if not hasattr(self, "_etoro_sl_suppress_until"):
            self._etoro_sl_suppress_until = {}
        return self._etoro_sl_suppress_until

    @staticmethod
    def _etoro_sl_suppress_key(row: int, symbol_u: str) -> str:
        return f"{int(row)}::{(symbol_u or '').strip().upper()}"

    def _etoro_register_sl_suppress_row(self, row: int, symbol_u: str, seconds: float, note: str = "") -> None:
        m = self._etoro_sl_suppress_until_map()
        k = self._etoro_sl_suppress_key(row, symbol_u)
        m[k] = time.time() + max(30.0, float(seconds))
        if note:
            log.info("eToro: كبت وقف الخسارة مؤقتاً للصف %s — %s", k, note)
        else:
            log.info("eToro: كبت وقف الخسارة مؤقتاً للصف %s", k)

    def _etoro_trade_sl_suppressed(self, trade: dict | None) -> bool:
        if not trade or (load_config().get("exchange") or "").lower() != "etoro":
            return False
        try:
            r = trade.get("row")
            if r is None:
                return False
            sym = (trade.get("symbol") or "").strip().upper()
            k = self._etoro_sl_suppress_key(int(r), sym)
            until = float(self._etoro_sl_suppress_until_map().get(k, 0) or 0)
            return until > time.time()
        except (TypeError, ValueError):
            return False

    def _hydrate_etoro_recent_closed_from_disk(self) -> None:
        try:
            if (load_config().get("exchange") or "").lower() != "etoro":
                return
            maps = _read_all_etoro_closed_maps()
            key = "testnet" if not self.is_real_mode() else "live"
            p0, o0 = maps.get(key) or ({}, {})
            self._etoro_recent_closed_position_ids = dict(p0)
            self._etoro_recent_closed_order_ids = dict(o0)
            self._etoro_prune_recent_closed_position_ids()
        except Exception:
            pass

    def _restore_etoro_positions_from_cache(self):
        """استعادة مراكز eToro من الكاش عند بدء التشغيل ثم التحقق من API.

        الهدف: عدم اختفاء المراكز مباشرة بعد إعادة فتح البرنامج عندما تتأخر API
        أو تُرجع قائمة فارغة مؤقتاً.
        """
        try:
            if self._positions_panel is None:
                return
            cfg = load_config()
            if (cfg.get("exchange") or "").lower() != "etoro":
                return
            if self._positions_panel.table.rowCount() > 0:
                return
            cached = None
            cache_source = None
            for p in (_etoro_positions_cache_path(), _etoro_positions_cache_backup_path()):
                if not os.path.isfile(p):
                    continue
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, list) and len(data) > 0:
                        cached = data
                        cache_source = p
                        break
                except Exception as e:
                    log.debug("eToro cache restore failed (%s): %s", p, e)
            if isinstance(cached, list) and len(cached) > 0:
                self._positions_panel.set_positions_from_exchange(cached)
                log.info(
                    "eToro restore: restored_from_cache rows=%s source=%s",
                    len(cached),
                    cache_source or "unknown",
                )
                self.status_bar_message.emit(
                    "تمت استعادة المراكز من الكاش المحلي مؤقتاً — جاري التحقق من eToro…"
                )
            QTimer.singleShot(400, self._sync_open_positions_from_exchange)
            QTimer.singleShot(2200, self._sync_open_positions_from_exchange)
            QTimer.singleShot(6000, self._sync_open_positions_from_exchange)
        except Exception as e:
            log.debug("eToro startup sync schedule: %s", e)

    def _persist_etoro_positions_cache_from_table(self) -> None:
        """حفظ لقطة المراكز المفتوحة من جدول الواجهة إلى كاش eToro قبل الإغلاق."""
        try:
            cfg = load_config()
            if (cfg.get("exchange") or "").lower() != "etoro":
                return
            if self._positions_panel is None:
                return
            tbl = self._positions_panel.table
            rows: list[dict] = []
            for r in range(tbl.rowCount()):
                it_sym = tbl.item(r, 0)
                it_ep = tbl.item(r, 1)
                it_qty = tbl.item(r, 2)
                if it_sym is None or it_ep is None or it_qty is None:
                    continue
                sym = str(it_sym.text() or "").strip().upper()
                try:
                    ep = float(it_ep.text() or 0.0)
                    qty = float(it_qty.text() or 0.0)
                except (TypeError, ValueError):
                    continue
                if not sym or ep <= 0 or qty <= 0:
                    continue
                row = {"symbol": sym, "entry_price": float(ep), "quantity": float(qty)}
                try:
                    pid_v = it_sym.data(Qt.ItemDataRole.UserRole)
                    if pid_v is not None:
                        pid_i = int(pid_v)
                        if pid_i > 0:
                            row["position_id"] = pid_i
                except (TypeError, ValueError):
                    pass
                try:
                    oid_v = it_sym.data(int(Qt.ItemDataRole.UserRole) + 10)
                    if oid_v is not None:
                        oid_i = int(oid_v)
                        if oid_i > 0:
                            row["order_id"] = oid_i
                except (TypeError, ValueError):
                    pass
                try:
                    px_sym = it_sym.data(ROW_PRICE_SYMBOL_ROLE)
                    if px_sym:
                        row["price_symbol"] = str(px_sym).strip().upper()
                except Exception:
                    pass
                rows.append(row)
            if not rows:
                _clear_etoro_positions_cache()
                return
            for p in (_etoro_positions_cache_path(), _etoro_positions_cache_backup_path()):
                try:
                    with open(p, "w", encoding="utf-8") as cf:
                        json.dump(rows, cf, indent=2, ensure_ascii=False)
                except OSError:
                    pass
        except Exception as e:
            log.debug("persist eToro cache on shutdown failed: %s", e)

    def _update_open_position_display(self, current_price: float):
        """تحديث ملصق آخر صفقة عند عدم وجود مراكز. جدول المراكز والتغير يُحدَّثان في تبويب المركز عبر price_updated."""
        _ = current_price
        count = 0
        if self._positions_panel is not None:
            for row in range(self._positions_panel.table.rowCount()):
                qty_item = self._positions_panel.table.item(row, 2)
                try:
                    qty = float(qty_item.text() or 0) if qty_item else 0.0
                except (ValueError, TypeError):
                    qty = 0.0
                if qty > 0:
                    count += 1
        if self._positions_panel is None or count == 0:
            if hasattr(self, "last_trade_label") and self.last_trade_label:
                if self._last_realized_pnl is None:
                    self.last_trade_label.setText("—")
                    self.last_trade_label.setStyleSheet("color: #aaa; font-weight: bold; font-size: 12px;")
                else:
                    pnl = float(self._last_realized_pnl)
                    self.last_trade_label.setText(f"{pnl:+.2f}")
                    self.last_trade_label.setStyleSheet(
                        f"color: {UI_GREEN}; font-weight: bold; font-size: 12px;" if pnl >= 0 else f"color: {UI_RED}; font-weight: bold; font-size: 12px;"
                    )

    # ============================================================
    # عرض السعر — لون أخضر صعود / أحمر هبوط
    # ============================================================
    @staticmethod
    def _price_label_stylesheet(text_color: str, border_color: str) -> str:
        """
        ستايل السعر — يُطبَّق على QLabel وحده عبر setStyleSheet.
        بدون محدّد #objectName لأن بعض إصدارات Qt تُسجّل «Could not parse stylesheet» معه.
        حدود منفصلة بدل اختصار border: 1px solid لتفادي أخطاء التحليل.
        """
        tc = (text_color or TOP_TEXT_PRIMARY).strip()
        bc = (border_color or TOP_PANEL_BORDER).strip()
        bg = (TOP_INNER_BG or "#1a1f28").strip()
        return (
            f"color: {tc}; "
            f"background-color: {bg}; "
            f"border-style: solid; border-width: 1px; border-color: {bc}; "
            "border-radius: 8px; "
            "padding: 5px 12px; "
            "font-weight: bold; "
            "font-size: 16px;"
        )

    def _set_price_label_style_neutral(self) -> None:
        if hasattr(self, "price_label") and self.price_label:
            self.price_label.setStyleSheet(
                self._price_label_stylesheet(TOP_TEXT_PRIMARY, TOP_PANEL_BORDER)
            )

    def _set_price_label_style_up(self) -> None:
        if hasattr(self, "price_label") and self.price_label:
            self.price_label.setStyleSheet(self._price_label_stylesheet(UI_GREEN, "#15803d"))

    def _set_price_label_style_down(self) -> None:
        if hasattr(self, "price_label") and self.price_label:
            self.price_label.setStyleSheet(self._price_label_stylesheet(UI_RED, "#b91c1c"))

    @staticmethod
    def _day_pct_label_stylesheet(text_color: str) -> str:
        tc = (text_color or TOP_TEXT_MUTED).strip()
        return (
            f"color: {tc}; background: transparent; border: none; "
            "font-weight: bold; font-size: 13px; padding-left: 2px;"
        )

    def _schedule_local_day_anchor_fetch(self) -> None:
        sym = str(getattr(self, "current_symbol", "") or "").strip().upper()
        if not sym or sym.startswith("ETORO_"):
            if hasattr(self, "price_day_pct_label") and self.price_day_pct_label:
                self.price_day_pct_label.setText("—")
                self.price_day_pct_label.setStyleSheet(self._day_pct_label_stylesheet(TOP_TEXT_MUTED))
            return
        if self._local_day_fetching:
            return
        self._local_day_fetching = True

        def work():
            ref = get_open_at_local_midnight(sym)
            val = float(ref) if ref is not None and ref > 0 else -1.0
            self._local_day_ref_ready.emit(sym, val)

        threading.Thread(target=work, daemon=True).start()

    def _on_local_day_ref_ready(self, sym: str, ref_or_neg: float) -> None:
        self._local_day_fetching = False
        cur = str(getattr(self, "current_symbol", "") or "").strip().upper()
        if sym != cur:
            return
        if ref_or_neg <= 0:
            self._local_day_ref = None
            self._local_day_anchor_local_date = ""
            self._local_day_fetch_fail_date = local_today_iso()
        else:
            self._local_day_ref = ref_or_neg
            self._local_day_anchor_local_date = local_today_iso()
            self._local_day_fetch_fail_date = ""
        self._update_day_pct_label()

    def _check_local_day_midnight_rollover(self) -> None:
        sym = str(getattr(self, "current_symbol", "") or "").strip().upper()
        if not sym or sym.startswith("ETORO_"):
            return
        today = local_today_iso()
        anchor = getattr(self, "_local_day_anchor_local_date", "") or ""
        if anchor and anchor != today:
            local_day_invalidate_symbol(sym)
            self._local_day_ref = None
            self._local_day_anchor_local_date = ""
            self._local_day_fetching = False
            self._local_day_fetch_fail_date = ""
            self._schedule_local_day_anchor_fetch()
            return
        if (
            getattr(self, "_local_day_ref", None) is None
            and not self._local_day_fetching
            and getattr(self, "_local_day_fetch_fail_date", "") != today
        ):
            self._schedule_local_day_anchor_fetch()

    def _update_day_pct_label(self) -> None:
        if not hasattr(self, "price_day_pct_label") or not self.price_day_pct_label:
            return
        ref = getattr(self, "_local_day_ref", None)
        pr = float(getattr(self, "_last_price", 0) or 0)
        if ref is None or ref <= 0 or pr <= 0:
            self.price_day_pct_label.setText("—")
            self.price_day_pct_label.setStyleSheet(self._day_pct_label_stylesheet(TOP_TEXT_MUTED))
            return
        pct = (pr - ref) / ref * 100.0
        sign = "+" if pct >= 0 else ""
        self.price_day_pct_label.setText(f"{sign}{pct:.2f}%")
        if pct > 1e-12:
            self.price_day_pct_label.setStyleSheet(self._day_pct_label_stylesheet(UI_GREEN))
        elif pct < -1e-12:
            self.price_day_pct_label.setStyleSheet(self._day_pct_label_stylesheet(UI_RED))
        else:
            self.price_day_pct_label.setStyleSheet(self._day_pct_label_stylesheet(TOP_TEXT_MUTED))

    # ============================================================
    # إشارات الربط — متوافقة مع WebSocketManager (فريمات)
    # ============================================================
    def update_price(self, interval, new_price):
        if interval != self._chart_interval:
            return
        self._check_local_day_midnight_rollover()
        self._last_price_update_time = time.time()
        new_f = float(new_price)
        prev = float(getattr(self, "_last_price", 0) or 0)
        self._last_price = new_f
        # تحديث عرض السعر فقط (خفيف جداً) — لا تشغيل منطق ثقيل هنا لئلا تتجمّد الشاشة عند الضغط على الأزرار
        if hasattr(self, "price_label") and self.price_label:
            self.price_label.setText(f"{tr('trading_price')}: {format_price(new_price)} $")
            if prev > 0:
                tol = max(abs(prev) * 1e-9, 1e-12)
                if new_f > prev + tol:
                    self._set_price_label_style_up()
                elif new_f < prev - tol:
                    self._set_price_label_style_down()
                else:
                    self._set_price_label_style_neutral()
            else:
                self._set_price_label_style_neutral()
        self._update_day_pct_label()

    def _sell_condition_enabled(self, cfg: dict, condition_id: str) -> bool:
        """إن كانت `sell_conditions` فارغة يُسمح بكل المسارات؛ وإلا يُنفَّذ فقط ما ورد في القائمة.
        عند إيقاف «تطبيق فلاتر التنفيذ المتقدّمة» لا تُفلتر مسارات البيع بقائمة الشروط
        (نفس منطق apply_execution_filters في bot_logic).
        عند استراتيجية قالب وإيقاف «قوائم إعدادات البوت الخاصة على القوالب» تُتخطّى القائمة."""
        if not apply_execution_filters(cfg):
            return True
        if not apply_private_condition_lists_for_strategy(cfg):
            return True
        conds = cfg.get("sell_conditions")
        if not isinstance(conds, list) or len(conds) == 0:
            return True
        return condition_id in conds

    def _candle_peak_high_flags(self, cfg: dict) -> tuple[float | None, bool | None]:
        """أعلى شمعة الإطار 1m الحالية وما إذا كانت عند قمة سوينغ (بيع عند الذروة / decide)."""
        candle_high = None
        at_real_peak = None
        try:
            f = self.ws.frames.get("1m") if self.ws else None
            if f and getattr(f, "candles", None) and len(f.candles) >= 1:
                candle_high = float(f.candles[-1][1])
                lookback = int(cfg.get("sell_at_peak_swing_lookback", 15) or 15)
                lookback = max(2, min(lookback, len(f.candles)))
                recent_highs = [float(f.candles[i][1]) for i in range(-lookback, 0)]
                swing_high = max(recent_highs) if recent_highs else candle_high
                at_real_peak = candle_high >= swing_high * 0.998
        except (TypeError, IndexError, ValueError, AttributeError):
            pass
        return candle_high, at_real_peak

    def _sync_heavy_price_timer_interval(self) -> None:
        """يزيد الفاصل قليلاً مع كثرة صفوف المراكز لتخفيف الحمل؛ SL/التتبع يبقيان على نفس المسار."""
        rows = 0
        pp = getattr(self, "_positions_panel", None)
        if pp is not None and getattr(pp, "table", None) is not None:
            try:
                rows = int(pp.table.rowCount())
            except Exception:
                rows = 0
        if rows >= 50:
            ms = 650
        elif rows >= 25:
            ms = 500
        else:
            ms = 350
        if getattr(self, "_heavy_price_timer_last_ms", None) != ms:
            self._heavy_price_timer_last_ms = ms
            self._heavy_price_timer.setInterval(ms)

    def _run_heavy_price_update(self):
        """يُستدعى من مؤقت (أساس 350 ms، يزيد تلقائياً مع كثرة الصفوف) — مراكز + وقف خسارة + تتبع."""
        self._sync_heavy_price_timer_interval()
        self._refresh_open_positions_count_label()
        self._check_local_day_midnight_rollover()
        price = getattr(self, "_last_price", 0) or 0
        pp = getattr(self, "_positions_panel", None)
        if pp is not None:
            try:
                pp.current_symbol = str(getattr(self, "current_symbol", None) or "").strip().upper() or pp.current_symbol
            except Exception:
                pass
        # بدون سعر شارت بعد (أول تيك أو بعد تغيير الرمز): لا نُصدِر price_updated لكن نستطلع Binance
        # للصفوف حتى لا تبقى أعمدة السعر/الربح «—» إلى أن يصل التيك.
        if price <= 0:
            _rc = 0
            if pp is not None:
                try:
                    _rc = int(pp.table.rowCount()) if getattr(pp, "table", None) is not None else 0
                except Exception:
                    _rc = 0
            if _rc > 0:
                now = time.time()
                last_h = float(getattr(self, "_last_positions_poll_help_ts", 0.0) or 0.0)
                if now - last_h >= 2.0:
                    self._last_positions_poll_help_ts = now
                    try:
                        pp._schedule_price_poll_for_open_rows()
                    except Exception:
                        pass
            return
        # نفحص دائماً بوتيرة المؤقت حتى لا يبقى منطق SL/Trailing/limit معلّقاً
        # عند تغيّر بسيط جداً في السعر أو عند اختلافات توقيت تحديث السعر بين الإشارات.
        self.price_updated.emit(price)
        self._update_open_position_display(float(price))
        self._check_stop_loss(float(price))
        self._check_limit_sell(float(price))
        self._check_trailing_stop(float(price))
        self._check_sell_at_peak(float(price))
        self._check_sell_at_overbought(float(price))
        self._check_limit_buy(float(price))

    def _check_stop_loss(self, last_price: float):
        """إذا كان لدينا مركز والسعر وصل لمستوى حد الخسارة (SL) المُعد نبيع لوقف الخسارة."""
        cfg = load_config()
        if not cfg.get("bot_auto_sl", True):
            return
        # وقف الخسارة حماية مستقلة: لا يُحجب بقائمة sell_conditions
        # ويعمل حتى لو زر الروبوت OFF طالما bot_auto_sl مفعّل.
        if self._execution_busy_for_orders():
            return
        # وقف الخسارة له أولوية قصوى: لا نمنعه بـ cooldown.
        # مهم: لا نؤخر وقف الخسارة بمهلة eToro؛ هو مسار حماية خسارة يجب أن يبقى فعّالاً فوراً.
        if not self._positions_panel:
            return
        sl_type = cfg.get("sl_type", "percent") or "percent"
        sl_val = float(cfg.get("sl_value", -1.0) or -1.0)
        row_sl = self._positions_panel.find_row_stop_loss_hit(last_price, self.current_symbol)
        if row_sl is not None:
            if self._etoro_trade_sl_suppressed(row_sl):
                return
            if self._etoro_trade_recently_closed_blocks_sl(row_sl):
                return
            log.info("Bot: Per-row stop loss triggered")
            self._bot_cooldown_until = time.time() + self._BOT_COOLDOWN_SEC
            self._pending_order_confidence = 80.0
            self._pending_indicators = self._last_indicators or {}
            self._pending_market_info = self._last_market_info or {}
            self.close_single_row_action(row_sl, order_reason="وقف خسارة (صف)")
            return
        # حماية إضافية: فحص كل الصفوف المفتوحة وليس فقط رمز الشارت الحالي.
        if hasattr(self._positions_panel, "find_any_row_stop_loss_hit"):
            row_sl_any = self._positions_panel.find_any_row_stop_loss_hit(
                sl_type,
                sl_val,
                chart_symbol=self.current_symbol,
                chart_price=float(last_price),
            )
            if row_sl_any is not None:
                if self._etoro_trade_sl_suppressed(row_sl_any):
                    return
                if self._etoro_trade_recently_closed_blocks_sl(row_sl_any):
                    return
                log.info("Bot: Stop loss triggered for row (per-row or global SL)")
                self._bot_cooldown_until = time.time() + self._BOT_COOLDOWN_SEC
                self._pending_order_confidence = 80.0
                self._pending_indicators = self._last_indicators or {}
                self._pending_market_info = self._last_market_info or {}
                self.close_single_row_action(row_sl_any, order_reason="وقف خسارة (صف)")
                return
        # لا نستخدم get_position_for_symbol + _execute_real_order(SELL) هنا: ذلك يبيع كمية مجمّعة
        # لكل صفوف الرمز ويغلق أكثر من صفقة دفعة واحدة. الإغلاق يبقى عبر close_single_row_action فقط.

    def _take_profit_barrier_active(self, last_price: float) -> bool:
        """
        يمنع البيع المبكر (تتبع أو إشارة SELL) فقط عندما يكون للصف **هدف بيع مضبوط في الجدول**
        والسعر فوق الدخول ولم يصل للهدف بعد.
        لا نستخدم هنا «حد البيع العام» من الإعدادات: ذلك يُنفَّذ تلقائياً عند وصول السعر فقط
        (`_check_limit_sell`)، وفرضه كحاجز كان يمنع بيع الإشارة بسبب قيمة افتراضية في الملف دون اختيار صريح.
        لربط توصية SELL بهدف حد البيع العام استخدم إعداد «انتظار هدف الربح حتى مع توصية بيع» في المخاطر.
        في الخسارة تحت الدخول لا نمنع الخروج.
        """
        if last_price <= 0 or not self._positions_panel:
            return False
        try:
            if self._positions_panel.take_profit_still_pending_for_chart(
                last_price, self.current_symbol
            ):
                return True
        except Exception:
            pass
        return False

    def _auto_profit_sell_allowed(self, cfg: dict, *, priority_path: bool = False) -> bool:
        """مسارات جني الربح التلقائية (حد بيع، تتبع، ذروة، RSI). وقف الخسارة يبقى مستقلاً عبر bot_auto_sl."""
        if not bool(cfg.get("bot_auto_sell", False)):
            return False
        # المسارات ذات الأولوية (حد البيع + التتبع) لا تُحجب بخيار ربط البيع بزر الروبوت.
        if priority_path:
            return True
        if bool(cfg.get("bot_auto_sell_requires_robot", False)) and not self._bot_enabled:
            return False
        return True

    def _limit_orders_bind_and_price(self, cfg: dict, chart_price: float) -> tuple[str, float]:
        """
        رمز التنفيذ لحد الشراء/البيع والسعر المستخدم للمقارنة.
        إن وُجد limit_orders_bind_symbol يُستخدم سعر ذلك الزوج (جلب لحظي إن لم يكن هو شارت الشاشة).
        """
        bind = str(cfg.get("limit_orders_bind_symbol") or "").strip().upper()
        cur = str(getattr(self, "current_symbol", "") or "").strip().upper()
        if bind:
            if bind == cur:
                return bind, float(chart_price or 0)
            return bind, float(self._price_for_closed_symbol(bind) or 0)
        return cur, float(chart_price or 0)

    def _check_limit_sell(self, last_price: float):
        """بيع تلقائي عند وصول السعر إلى حد البيع (سعر ثابت أو نسبة مئوية من سعر الدخول).

        ملاحظة: هذا المسار له أولوية تنفيذية ولا يُحجب بقائمة sell_conditions.
        """
        cfg = load_config()
        if not self._auto_profit_sell_allowed(cfg, priority_path=True):
            return
        # افتراضياً: يكفي «البيع التلقائي» دون زر الروبوت. إن فُعّل «ربط البيع التلقائي بالروبوت» في المخاطر يُشترط تشغيل الروبوت.
        if self._execution_busy_for_orders():
            return
        if time.time() < self._bot_cooldown_until:
            return
        try:
            if (cfg.get("exchange") or "").lower() == "etoro":
                if time.time() < float(getattr(self, "_etoro_min_hold_until", 0) or 0):
                    return
        except Exception:
            pass
        if not self._positions_panel:
            return
        trade_sym, cmp_px = self._limit_orders_bind_and_price(cfg, last_price)
        bind_on = bool(str(cfg.get("limit_orders_bind_symbol") or "").strip())
        if bind_on and cmp_px <= 0:
            return
        row_tp = self._positions_panel.find_row_take_profit_hit(cmp_px, trade_sym)
        if row_tp is not None:
            log.info("Bot: Per-row take profit (limit) triggered")
            self._bot_cooldown_until = time.time() + self._BOT_COOLDOWN_SEC
            self._pending_order_confidence = 80.0
            self._pending_indicators = self._last_indicators or {}
            self._pending_market_info = self._last_market_info or {}
            if hasattr(self, "bot_status_label") and self.bot_status_label:
                self.bot_status_label.setText("حد البيع (صف)…")
            self.close_single_row_action(row_tp, order_reason="حد البيع (صف)")
            return
        pos = self._positions_panel.get_position_for_symbol(trade_sym)
        if not pos or float(pos.get("quantity", 0) or 0) <= 0:
            return
        entry = float(pos.get("entry_price", 0) or 0)
        if entry <= 0 or cmp_px <= 0:
            return
        if suspect_placeholder_entry_price(entry, cmp_px):
            log.warning(
                "Bot: حد البيع متخطى — دخول مشبوه entry=%.6f مقابل سعر %.6f (غالباً مزامنة eToro ناقصة)",
                entry,
                cmp_px,
            )
            return
        sell_type = (cfg.get("limit_sell_type") or "percent").strip() or "percent"
        sell_val = float(cfg.get("limit_sell_value", 0.0) or 0.0)
        if sell_type == "percent":
            if sell_val <= 0:
                return
            target = entry * (1.0 + sell_val / 100.0)
        else:
            target = float(cfg.get("limit_sell_price", sell_val) or sell_val)
            if target <= 0:
                return
        if cmp_px < target:
            return
        log.info(
            "Bot: Limit sell hit — sym=%s price %.2f >= target %.2f (entry %.2f), executing SELL",
            trade_sym,
            cmp_px,
            target,
            entry,
        )
        self._bot_cooldown_until = time.time() + self._BOT_COOLDOWN_SEC
        self._order_in_progress = True
        self._set_trade_buttons_enabled(False)
        self._pending_order_confidence = 80.0
        self._pending_indicators = self._last_indicators or {}
        self._pending_market_info = self._last_market_info or {}
        if hasattr(self, "bot_status_label") and self.bot_status_label:
            self.bot_status_label.setText("حد البيع…")
        self._pending_order_reason = "حد البيع"
        self._execute_real_order(
            "SELL",
            cmp_px,
            cfg,
            testnet=(not self._real_mode),
            symbol_override=(trade_sym if bind_on else None),
        )

    def _check_trailing_stop(self, last_price: float):
        """وقف خسارة متحرك: يحدّث القمة منذ الدخول؛ يبيع إذا نزل السعر من القمة بنسبة التتبع
        وبعد تحقيق أدنى ربح (من إعدادات المخاطر).

        ملاحظة: هذا المسار له أولوية تنفيذية ولا يُحجب بقائمة sell_conditions
        أو بحاجز هدف الصف.
        """
        cfg = load_config()
        # التتبع مستقل عن bot_auto_sell (الافتراضي False) — يُتحكم به بـ bot_trailing_enabled
        if not bool(cfg.get("bot_trailing_enabled", True)):
            self._set_trailing_diag("التتبع محجوب: معطّل في الإعدادات (bot_trailing_enabled)")
            return
        if bool(cfg.get("bot_auto_sell_requires_robot", False)) and not self._bot_enabled:
            self._set_trailing_diag("التتبع محجوب: يُشترط تشغيل زر الروبوت (المخاطر)")
            return
        if self._execution_busy_for_orders():
            self._set_trailing_diag("التتبع محجوب: يوجد أمر قيد التنفيذ")
            return
        if time.time() < self._bot_cooldown_until:
            self._set_trailing_diag("التتبع محجوب: فترة تهدئة")
            return
        trail_pct = float(cfg.get("trailing_stop_pct", 0) or 0)
        if trail_pct <= 0:
            self._set_trailing_diag("التتبع محجوب: نسبة التتبع = 0")
            return
        min_profit = float(cfg.get("trailing_min_profit_pct", 0) or 0)
        try:
            if (cfg.get("exchange") or "").lower() == "etoro":
                if time.time() < float(getattr(self, "_etoro_min_hold_until", 0) or 0):
                    return
        except Exception:
            pass
        if not self._positions_panel:
            self._set_trailing_diag("التتبع محجوب: لا يوجد جدول مراكز")
            return
        pos = self._positions_panel.get_position_for_symbol(self.current_symbol)
        if not pos or float(pos.get("quantity", 0) or 0) <= 0:
            try:
                n_open = int(self._positions_panel.logical_open_row_count())
            except Exception:
                n_open = 0
            if n_open > 0:
                self._set_trailing_diag(
                    "التتبع محجوب: الشارت لا يطابق رمز المركز — اختر نفس رمز الصف في الشارت",
                    min_interval_sec=5.0,
                )
            else:
                self._set_trailing_diag("التتبع محجوب: لا يوجد مركز مفتوح")
            return
        entry = float(pos.get("entry_price", 0) or 0)
        if entry <= 0 or last_price <= 0:
            return
        # تحديث أعلى سعر أثناء بقاء المركز (للتتبع وليس فقط عند تنفيذ الشراء)
        self._position_peak_price = max(float(self._position_peak_price or last_price), last_price)
        peak = float(self._position_peak_price)
        pnl_pct = (last_price / entry - 1.0) * 100.0
        if pnl_pct < min_profit:
            self._set_trailing_diag(
                f"التتبع مسلّح بعد +{min_profit:.2f}% (الآن {pnl_pct:+.2f}%)",
                min_interval_sec=3.5,
            )
            return
        trail_price = peak * (1.0 - trail_pct / 100.0)
        if last_price > trail_price:
            self._set_trailing_diag(
                f"التتبع نشط: قمة {peak:.4f} | خط بيع {trail_price:.4f}",
                min_interval_sec=3.5,
            )
            return
        log.info(
            "Bot: Trailing stop hit — price %.6f <= trail %.6f (peak %.6f, entry %.6f, trail %%=%.2f)",
            last_price,
            trail_price,
            peak,
            entry,
            trail_pct,
        )
        self._bot_cooldown_until = time.time() + self._BOT_COOLDOWN_SEC
        self._order_in_progress = True
        self._set_trade_buttons_enabled(False)
        self._pending_order_confidence = 80.0
        self._pending_indicators = self._last_indicators or {}
        self._pending_market_info = self._last_market_info or {}
        self._pending_order_reason = "التتبع"
        self._execute_real_order("SELL", last_price, cfg, testnet=(not self._real_mode))

    def _check_sell_at_peak(self, last_price: float):
        """بيع عند ذروة شمعة 1m (قمة سوينغ) أو عند RSI تشبع إن ضُبط — يتطلب `sell_at_peak` في القائمة عندما القائمة غير فارغة."""
        cfg = load_config()
        if not self._auto_profit_sell_allowed(cfg):
            return
        if not self._sell_condition_enabled(cfg, "sell_at_peak"):
            return
        if self._execution_busy_for_orders():
            return
        if time.time() < self._bot_cooldown_until:
            return
        try:
            if (cfg.get("exchange") or "").lower() == "etoro":
                if time.time() < float(getattr(self, "_etoro_min_hold_until", 0) or 0):
                    return
        except Exception:
            pass
        if not self._positions_panel:
            return
        pos = self._positions_panel.get_position_for_symbol(self.current_symbol)
        if not pos or float(pos.get("quantity", 0) or 0) <= 0:
            return
        entry = float(pos.get("entry_price", 0) or 0)
        if entry <= 0 or last_price <= 0:
            return
        if suspect_placeholder_entry_price(entry, last_price):
            return
        if self._take_profit_barrier_active(last_price):
            if not bool(cfg.get("bot_signal_sell_bypass_tp_barrier", False)):
                return
        min_profit = float(
            cfg.get("sell_at_peak_min_profit_pct")
            or cfg.get("sell_at_peak_min_profit")
            or 0.5
        )
        pnl_pct = (last_price / entry - 1.0) * 100.0
        if pnl_pct < min_profit:
            return
        _, at_real_peak = self._candle_peak_high_flags(cfg)
        rsi_min = float(cfg.get("sell_at_peak_rsi_min", 0) or 0)
        rsi = float((self._last_indicators or {}).get("rsi", 0) or 0)
        peak_ok = bool(at_real_peak)
        rsi_ok = rsi_min > 0 and rsi >= rsi_min
        if not peak_ok and not rsi_ok:
            return
        log.info(
            "Bot: Sell at peak — pnl %%=%.2f peak_ok=%s rsi=%.1f rsi_ok=%s",
            pnl_pct,
            peak_ok,
            rsi,
            rsi_ok,
        )
        self._bot_cooldown_until = time.time() + self._BOT_COOLDOWN_SEC
        self._order_in_progress = True
        self._set_trade_buttons_enabled(False)
        self._pending_order_confidence = 80.0
        self._pending_indicators = self._last_indicators or {}
        self._pending_market_info = self._last_market_info or {}
        if hasattr(self, "bot_status_label") and self.bot_status_label:
            self.bot_status_label.setText("بيع عند الذروة…")
        self._pending_order_reason = "البيع عند الذروة"
        self._execute_real_order("SELL", last_price, cfg, testnet=(not self._real_mode))

    def _check_sell_at_overbought(self, last_price: float):
        """بيع عند RSI فوق عتبة تشبع — يتطلب `sell_at_overbought` في القائمة عندما القائمة غير فارغة."""
        cfg = load_config()
        if not self._auto_profit_sell_allowed(cfg):
            return
        if not self._sell_condition_enabled(cfg, "sell_at_overbought"):
            return
        if self._execution_busy_for_orders():
            return
        if time.time() < self._bot_cooldown_until:
            return
        try:
            if (cfg.get("exchange") or "").lower() == "etoro":
                if time.time() < float(getattr(self, "_etoro_min_hold_until", 0) or 0):
                    return
        except Exception:
            pass
        if not self._positions_panel:
            return
        pos = self._positions_panel.get_position_for_symbol(self.current_symbol)
        if not pos or float(pos.get("quantity", 0) or 0) <= 0:
            return
        entry = float(pos.get("entry_price", 0) or 0)
        if entry <= 0 or last_price <= 0:
            return
        if suspect_placeholder_entry_price(entry, last_price):
            return
        if self._take_profit_barrier_active(last_price):
            if not bool(cfg.get("bot_signal_sell_bypass_tp_barrier", False)):
                return
        rsi_th = float(cfg.get("sell_at_overbought_rsi_min", 72) or 72)
        if rsi_th <= 0:
            return
        rsi = float((self._last_indicators or {}).get("rsi", 0) or 0)
        if rsi < rsi_th:
            return
        min_profit = float(cfg.get("sell_at_overbought_min_profit_pct", 0) or 0)
        pnl_pct = (last_price / entry - 1.0) * 100.0
        if pnl_pct < min_profit:
            return
        log.info("Bot: Sell at overbought — rsi=%.1f >= %.1f pnl %%=%.2f", rsi, rsi_th, pnl_pct)
        self._bot_cooldown_until = time.time() + self._BOT_COOLDOWN_SEC
        self._order_in_progress = True
        self._set_trade_buttons_enabled(False)
        self._pending_order_confidence = 80.0
        self._pending_indicators = self._last_indicators or {}
        self._pending_market_info = self._last_market_info or {}
        if hasattr(self, "bot_status_label") and self.bot_status_label:
            self.bot_status_label.setText("تشبع صاعد…")
        self._pending_order_reason = "تشبع صاعد (RSI)"
        self._execute_real_order("SELL", last_price, cfg, testnet=(not self._real_mode))

    def _check_limit_buy(self, last_price: float):
        """شراء تلقائي عند لمس حد الشراء (سعر ثابت، أو نسبة تحت مرجع محفوظ / أول لقطة سعر). يتطلب تشغيل الروبوت."""
        if not self._bot_enabled:
            return
        if self._execution_busy_for_orders():
            return
        if time.time() < self._bot_cooldown_until:
            return
        cfg = load_config()
        trade_sym, cmp_px = self._limit_orders_bind_and_price(cfg, last_price)
        bind_on = bool(str(cfg.get("limit_orders_bind_symbol") or "").strip())
        if bind_on and cmp_px <= 0:
            return
        try:
            _ob_buf = float(cfg.get("sell_at_overbought_limit_buy_rsi_buffer", 5) or 0)
        except (TypeError, ValueError):
            _ob_buf = 5.0
        if _ob_buf > 0 and self._sell_condition_enabled(cfg, "sell_at_overbought"):
            _rsi_th = float(cfg.get("sell_at_overbought_rsi_min", 72) or 72)
            if _rsi_th > 0:
                _rsi_now = float((self._last_indicators or {}).get("rsi", 0) or 0)
                if _rsi_now >= _rsi_th - _ob_buf:
                    return
        typ = (cfg.get("limit_buy_type") or "percent").strip().lower()
        buy_val = float(cfg.get("limit_buy_value", 0.0) or 0.0)
        if typ == "percent":
            if buy_val >= 0:
                self._limit_buy_pct_runtime_anchor = None
                return
        else:
            px = float(cfg.get("limit_buy_price", 0) or cfg.get("limit_buy_value", 0) or 0)
            if px <= 0:
                return
        limit = float(cfg.get("daily_loss_limit_usdt", 0) or 0)
        if limit > 0 and self._daily_pnl <= -limit:
            return
        if not self._positions_panel:
            return
        open_count = self._logical_open_count_for_bot()
        cap_m = _bot_max_open_trades_cap(cfg)
        if cap_m is not None and open_count >= cap_m:
            return
        max_per_sym = int(cfg.get("max_trades_per_symbol", 0) or 0)
        sym_n = self._count_open_rows_matching_symbol(trade_sym)
        if max_per_sym > 0 and sym_n >= max_per_sym:
            return
        ok_exp, _ = self._portfolio_exposure_allows_buy(cfg, float(cmp_px or 0.0))
        if not ok_exp:
            return
        # حد الشراء لا يقرأ توصية البيع/الشراء — فيُنفَّذ شراء من السعر فقط بينما اللوحة قد تعرض SELL
        rec_u = (getattr(self, "_last_panel_recommendation", "") or "").strip().upper()
        conf_min = float(cfg.get("bot_confidence_min", 60) or 60)
        conf_last = float(getattr(self, "_last_panel_confidence", 0) or 0)
        if rec_u == "SELL" and conf_last >= conf_min:
            log.info(
                "Bot: limit buy skipped — panel shows SELL at %.1f%% (min %.0f%%); not mixing with auto buy",
                conf_last,
                conf_min,
            )
            return
        sym_u = str(trade_sym or "").upper()
        pos = self._positions_panel.get_position_for_symbol(trade_sym)
        has_position = pos is not None and float(pos.get("quantity", 0) or 0) > 0
        exchange = (cfg.get("exchange") or "").lower()
        use_futures = _config_use_futures(cfg)
        if exchange == "etoro" and use_futures and not has_position:
            pend_until = float(getattr(self, "_etoro_pending_symbol_until", {}).get(sym_u, 0.0) or 0.0)
            if pend_until > time.time():
                return
        target = 0.0
        if typ == "price":
            target = float(cfg.get("limit_buy_price", 0) or cfg.get("limit_buy_value", 0) or 0)
        else:
            anchor_cfg = float(cfg.get("limit_buy_anchor_price", 0) or 0)
            if anchor_cfg > 0:
                anchor = anchor_cfg
            else:
                if self._limit_buy_pct_runtime_anchor is None or float(self._limit_buy_pct_runtime_anchor) <= 0:
                    if cmp_px > 0:
                        self._limit_buy_pct_runtime_anchor = float(cmp_px)
                    return
                anchor = float(self._limit_buy_pct_runtime_anchor)
            target = anchor * (1.0 + buy_val / 100.0)
        if target <= 0 or cmp_px <= 0:
            return
        if cmp_px > target:
            return
        # سعر المقارنة أدنى بكثير من هدف حد الشراء → غالباً رمز/تغذية قديمة أو هدف من زوج آخر (مثال: 0.001 مقابل 0.87)
        if target >= 0.05 and cmp_px < target * 0.02:
            _mw = time.time()
            if _mw - getattr(self, "_limit_buy_mismatch_warn_ts", 0) >= 90.0:
                self._limit_buy_mismatch_warn_ts = _mw
                log.warning(
                    "Bot: limit buy ignored — compare_price %.6f is far below target %.6f (check symbol vs limit_buy_price)",
                    cmp_px,
                    target,
                )
            return
        blocked, _wsec = self._same_symbol_buy_interval_should_block(cfg)
        if blocked:
            _lb = time.time()
            if _lb - getattr(self, "_limit_buy_blocked_log_ts", 0) >= 45.0:
                self._limit_buy_blocked_log_ts = _lb
                log.info(
                    "Bot: Limit buy conditions met — price %.6f <= target %.6f (type=%s); skipped same-symbol interval (%ds left)",
                    cmp_px,
                    target,
                    typ,
                    _wsec,
                )
            return
        log.info(
            "Bot: Limit buy hit — sym=%s price %.6f <= target %.6f (type=%s)",
            trade_sym,
            cmp_px,
            target,
            typ,
        )
        if self._real_mode and not cfg.get("first_real_order_done", False):
            mb = QMessageBox(self)
            mb.setWindowTitle(tr("trading_first_real_title"))
            mb.setText(tr("trading_first_real_text"))
            mb.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            mb.setDefaultButton(QMessageBox.StandardButton.No)
            if mb.exec() != QMessageBox.StandardButton.Yes:
                return
            cfg["first_real_order_done"] = True
            save_config(cfg)
        self._bot_cooldown_until = time.time() + self._BOT_COOLDOWN_SEC
        self._order_in_progress = True
        self._set_trade_buttons_enabled(False)
        self._pending_order_confidence = 75.0
        self._pending_indicators = self._last_indicators or {}
        self._pending_market_info = self._last_market_info or {}
        if hasattr(self, "bot_status_label") and self.bot_status_label:
            self.bot_status_label.setText("حد الشراء…")
        self._pending_order_reason = "حد الشراء"
        self._stamp_bot_buy_commit_ts(cfg)
        self._execute_real_order(
            "BUY",
            cmp_px,
            cfg,
            testnet=(not self._real_mode),
            symbol_override=(trade_sym if bind_on else None),
        )

    def update_candle(self, interval, candles):
        if interval == self._chart_interval:
            self._last_price_update_time = time.time()  # أي بيانات شموع = اتصال فعّال
            # تحديث لحظي: ارسال مباشرة لتجنب أي تأخير في واجهة الشارت
            self.candle_updated.emit(interval, candles)

    def _sync_top_recommendation_panel(self, indicators: dict, cfg: dict | None = None) -> None:
        """يحدّث صناديق لوحة التوصية في الشريط العلوي من نفس دفعة المؤشرات (احتياط بجانب إشارة AIPanel)."""
        if not isinstance(indicators, dict) or not indicators:
            return
        try:
            mi = self._last_market_info if isinstance(getattr(self, "_last_market_info", None), dict) else {}
            c = cfg if isinstance(cfg, dict) else load_config()
            rec, conf = AIPanel.get_recommendation(indicators, mi, c)
            self.update_ai_panel_display(rec, conf, indicators, mi)
        except Exception as e:
            log.warning("sync top recommendation panel failed: %s", e, exc_info=True)
            try:
                self.update_ai_panel_display("WAIT", 0.0, indicators, {})
            except Exception:
                pass

    def _tick_recommendation_panel_ui(self) -> None:
        """نبض دوري: يعيد حساب التوصية وقوة الحجم من الكاش حتى يتحرّك العرض مع السوق."""
        ind = getattr(self, "_last_indicators", None)
        if not isinstance(ind, dict) or not ind:
            return
        try:
            self._sync_top_recommendation_panel(ind)
            self._update_market_info_display()
        except Exception:
            log.warning("recommendation panel tick failed", exc_info=True)

    def update_indicators(self, interval, indicators):
        if not isinstance(indicators, dict):
            return
        # 1h / 4h: حفظ لاتجاه متعدد الأطر — وإذا كانا هما إطار الشارت يجب أيضاً ملء `_last_indicators`
        # (وإلا يبقى المؤشر المركّب وحالة السوق بلا بيانات لأنها تعتمد على `_last_indicators` فقط).
        # عندما يكون إطار الشارت غير 1h/4h لا نحدّث لوحة المؤشرات من هذه الحزمة — لتفادي إفراغ المركّب قبل وصول إطار الشارت.
        if interval in ("1h", "4h"):
            setattr(self, f"_last_indicators_{interval}", indicators)
            try:
                hist = self._mtf_close_history_1h if interval == "1h" else self._mtf_close_history_4h
                if len(hist) == 0:
                    tail = indicators.get("closed_closes_tail")
                    if isinstance(tail, list):
                        for x in tail:
                            try:
                                v = float(x)
                                if v > 0:
                                    hist.append(v)
                            except (TypeError, ValueError):
                                pass
                t_open = int(indicators.get("last_candle_open_ms") or 0)
                prev_t_attr = f"_mtf_spark_last_t_open_{interval}"
                prev_t = int(getattr(self, prev_t_attr, 0) or 0)
                # إغلاق الشمعة المفتوحة يعطي نفس «last» لـ 1h و4h في التيك — نلحق فقط عند دورة شمعة جديدة بإغلاق الشمعة السابقة
                if t_open > 0 and prev_t > 0 and t_open != prev_t:
                    pc = float(indicators.get("prev_close", 0) or 0)
                    if pc > 0 and (len(hist) == 0 or abs(hist[-1] - pc) > 1e-12):
                        hist.append(pc)
                if t_open > 0:
                    setattr(self, prev_t_attr, t_open)
            except (TypeError, ValueError):
                pass
            try:
                self._update_mtf_htf_readout()
            except Exception:
                pass
        if interval == self._chart_interval:
            # نسخة موحّدة للمحرّك والبوت: إطار الشارت داخل المؤشرات (بدونها فلاتر 15m/4h وHTF تخطئ)
            ind_out = dict(indicators)
            ind_out["chart_interval"] = str(interval or self._chart_interval or "1m")
            self._last_indicators = ind_out
            self._update_market_indicators_display()
            try:
                self._sync_top_recommendation_panel(ind_out)
            except Exception:
                log.exception("sync top recommendation before indicators_updated emit")
            try:
                self.indicators_updated.emit(interval, ind_out)
            except Exception:
                log.exception("indicators_updated: فشل أحد مستقبلات الإشارة (اللوحة العلوية حُدّثت بالفعل)")

    def update_market_info(self, interval, info):
        if interval == self._chart_interval:
            self._last_market_info = info if isinstance(info, dict) else None
            self.market_info_updated.emit(info)
            self._update_market_indicators_display()
            self._update_market_info_display()
            if isinstance(self._last_indicators, dict) and self._last_indicators:
                self._sync_top_recommendation_panel(self._last_indicators)

    def _clear_composite_ui(self) -> None:
        """إفراغ المؤشر المركّب وشارة الشارت."""
        if hasattr(self, "composite_main_label") and self.composite_main_label:
            self.composite_main_label.setText("—")
            self.composite_main_label.setToolTip("")
            self.composite_main_label.setStyleSheet(
                f"color: {TOP_TEXT_MUTED}; font-size: 12px; font-weight: bold;"
            )
        if hasattr(self, "composite_score_label") and self.composite_score_label:
            self.composite_score_label.setText("")
            self.composite_score_label.setStyleSheet(
                f"color: {TOP_TEXT_MUTED}; font-size: 10px; font-weight: bold;"
            )
        try:
            self.composite_signal_updated.emit({"clear": True})
        except Exception:
            pass

    def _apply_market_swing_peak_label_accent(self, accent: str) -> None:
        """ألوان قرار اللوحة: شراء أخضر، بيع أحمر، منع شراء برتقالي، منع بيع بنفسجي."""
        lbl = getattr(self, "market_swing_peak_label", None)
        if not lbl:
            return
        if accent == "buy":
            col = UI_GREEN
        elif accent == "sell":
            col = UI_RED
        elif accent == "block_buy":
            col = UI_AMBER
        elif accent == "block_sell":
            col = UI_VIOLET
        else:
            col = TOP_TEXT_SECONDARY
        lbl.setStyleSheet(f"color: {col}; font-size: 11px; line-height: 1.5;")

    def _update_market_swing_peak_label(self) -> None:
        """عرض خلاصة قرار السوق فقط بدون تفاصيل OHLC."""
        if not hasattr(self, "market_swing_peak_label") or self.market_swing_peak_label is None:
            return
        ind = self._last_indicators or {}
        if not ind:
            self.market_swing_peak_label.setText("—")
            self._apply_market_swing_peak_label_accent("neutral")
            return
        try:
            line = self._get_market_decision_line(ind, self._last_market_info or {})
            self.market_swing_peak_label.setText(line)
            self._apply_market_swing_peak_label_accent(
                AIPanel.decision_accent_for_market_status(ind, self._last_market_info or {})
            )
        except Exception:
            self.market_swing_peak_label.setText(tr("market_status_peak_waiting"))
            self._apply_market_swing_peak_label_accent("neutral")

    def _update_mtf_htf_readout(self) -> None:
        """كان يعرض 1h/4h في «حالة السوق» — أُزيل للواجهة؛ التحديث الخلفي لـ mtf_bias في البوت يبقى كما هو."""
        return

    def _apply_composite_signal_ui(self, ind: dict, info: dict) -> None:
        """تحديث صندوق المؤشر المركّب وإرسال الشارة للشارت."""
        if not hasattr(self, "composite_main_label") or not self.composite_main_label:
            return
        ar = get_language() == "ar"
        try:
            comp = compute_composite_signal(ind, info, lang_ar=ar)
        except Exception:
            self._clear_composite_ui()
            return
        self._last_composite_rebound_guard = bool(comp.get("rebound_guard", False))
        label = comp["label_ar"] if ar else comp["label_en"]
        lvl = comp.get("level") or "neutral"
        if lvl in ("strong_buy", "buy"):
            col = UI_GREEN
        elif lvl in ("strong_sell", "sell"):
            col = UI_RED
        else:
            col = "#cccccc"
        self.composite_main_label.setText(label)
        _top_expl = tr("composite_top_panel_tooltip")
        self.composite_main_label.setToolTip(f"{label}\n\n{_top_expl}")
        self.composite_main_label.setStyleSheet(
            f"color: {col}; font-size: 12px; font-weight: bold;"
        )
        if hasattr(self, "composite_score_label") and self.composite_score_label:
            self.composite_score_label.setText(
                (f"الدرجة: {comp['score']:+.1f}" if ar else f"Score: {comp['score']:+.1f}")
            )
            self.composite_score_label.setToolTip(
                f"{'مقياس الدرجة: −100 … +100' if ar else 'Score scale: −100 … +100'}\n\n{_top_expl}"
            )
            self.composite_score_label.setStyleSheet(
                f"color: {TOP_TEXT_MUTED}; font-size: 10px; font-weight: bold;"
            )
        try:
            try:
                cfg = load_config_cached()
                show_chart_badge = bool(cfg.get("chart_show_composite_badge", True))
            except Exception:
                show_chart_badge = True
            if show_chart_badge:
                self.composite_signal_updated.emit(
                    {
                        "clear": False,
                        "short_ar": comp.get("short_ar", ""),
                        "short_en": comp.get("short_en", ""),
                        "bg": comp.get("badge_bg", "#333333"),
                        "fg": comp.get("badge_fg", "#ffffff"),
                    }
                )
            else:
                self.composite_signal_updated.emit({"clear": True})
        except Exception:
            pass

    def _update_market_indicators_display(self):
        """عرض أهم مؤشرات السكالبينغ في قسم حالة السوق (ADX/VWAP/ATR/StochRSI)."""
        if not hasattr(self, "market_indicators_label") or self.market_indicators_label is None:
            return
        ind = self._last_indicators or {}
        info = self._last_market_info or {}
        if not ind:
            self.market_indicators_label.setText("—")
            self.market_indicators_label.setStyleSheet(
                f"color: {TOP_TEXT_MUTED}; font-size: 11px;"
            )
            if hasattr(self, "market_swing_peak_label") and self.market_swing_peak_label:
                self.market_swing_peak_label.setText("—")
                self._apply_market_swing_peak_label_accent("neutral")
            self._clear_composite_ui()
            self._maybe_refresh_auto_strategy_line()
            self._update_mtf_htf_readout()
            return
        try:
            price = float(ind.get("close", 0) or 0)
            vwap = float(ind.get("vwap", 0) or 0)
            atr = float(ind.get("atr14", 0) or 0)
            adx = float(ind.get("adx14", 0) or 0)
            pdi = float(ind.get("plus_di14", 0) or 0)
            mdi = float(ind.get("minus_di14", 0) or 0)
            st_k = float(ind.get("stoch_rsi_k", 0) or 0)
            st_d = float(ind.get("stoch_rsi_d", 0) or 0)
        except (TypeError, ValueError):
            if hasattr(self, "market_swing_peak_label") and self.market_swing_peak_label:
                self.market_swing_peak_label.setText("—")
                self._apply_market_swing_peak_label_accent("neutral")
            self._clear_composite_ui()
            self._maybe_refresh_auto_strategy_line()
            return

        trend = (info.get("trend") or "").upper()
        try:
            st_dir_ctx = int(ind.get("supertrend_dir", 0) or 0)
        except (TypeError, ValueError):
            st_dir_ctx = 0
        # سياق شراء/بيع للعرض: يدمج اتجاه الحجم/التدفق مع Supertrend حتى لا يتعارض النص مع الهبوط أو الصعود الواضح
        bearish_ctx = (trend == "DOWN") or (st_dir_ctx == -1)
        bullish_ctx = (trend == "UP") or (st_dir_ctx == 1)
        vwap_side = "↑" if (price and vwap and price >= vwap) else ("↓" if (price and vwap and price < vwap) else "—")
        di_side = "↑" if pdi > mdi else ("↓" if mdi > pdi else "—")
        try:
            _cfg_mi = load_config_cached()
        except Exception:
            _cfg_mi = {}
        adx_min = float(_cfg_mi.get("scalp_adx_min", 25) or 25)
        th = market_readout_thresholds(_cfg_mi)
        adx_strong = th["market_readout_adx_strong_min"]
        rsi_ob = th["market_readout_rsi_overbought"]
        rsi_os = th["market_readout_rsi_oversold"]
        rsi_hi = th["market_readout_rsi_ctx_high"]
        rsi_lo = th["market_readout_rsi_ctx_low"]
        st_ob = th["market_readout_stoch_overbought"]
        st_os = th["market_readout_stoch_oversold"]
        st_band_lo = th["market_readout_stoch_band_lo"]
        st_band_hi = th["market_readout_stoch_band_hi"]
        st_mid_lo = th["market_readout_stoch_mid_lo"]
        st_mid_hi = th["market_readout_stoch_mid_hi"]
        st_kd_eps = th["market_readout_stoch_kd_eps"]
        st_k_bull = th["market_readout_stoch_k_bull_min"]
        st_k_bear = th["market_readout_stoch_k_bear_max"]
        atr_hi_pct = th["market_readout_atr_high_vol_pct"]
        st_near_ratio = th["market_readout_supertrend_near_ratio"]
        rsi_mid = (rsi_hi + rsi_lo) / 2.0
        rsi_mid_s = f"{rsi_mid:.0f}" if abs(rsi_mid - round(rsi_mid)) < 0.01 else f"{rsi_mid:.1f}"
        adx_ok = adx >= adx_min

        # ألوان رقمية لكل مؤشر على حدة (مثل الربح/الخسارة)
        def color_span(value_str: str, color: str) -> str:
            return f'<span style="color:{color}; font-weight:bold;">{value_str}</span>'

        # ADX: قوة الاتجاه + ترجمة مباشرة
        adx_txt = color_span(f"{adx:.1f}", UI_GREEN) if adx_ok else color_span(f"{adx:.1f}", "#aaaaaa")
        if adx >= adx_strong:
            adx_forecast = color_span("اتجاه قوي (احتمال استمرار الحركة)", UI_GREEN)
        elif adx >= adx_min:
            adx_forecast = color_span("اتجاه متوسط (قابل للاستمرار)", UI_AMBER)
        elif adx > 0:
            adx_forecast = color_span("اتجاه ضعيف (تذبذب/اختراقات كاذبة)", "#aaaaaa")
        else:
            adx_forecast = color_span("غير متاح", "#aaaaaa")

        # +DI/-DI: إذا +DI>−DI (قوة شراء) = أخضر، العكس = أحمر
        pdi_color = UI_GREEN if pdi > mdi else (UI_RED if pdi < mdi else "#aaaaaa")
        mdi_color = UI_RED if mdi > pdi else (UI_GREEN if mdi < pdi else "#aaaaaa")
        pdi_txt = color_span(f"{pdi:.1f}", pdi_color)
        mdi_txt = color_span(f"{mdi:.1f}", mdi_color)
        if pdi > mdi:
            di_forecast = color_span("ضغط شرائي (ميل صعود)", UI_GREEN)
        elif mdi > pdi:
            di_forecast = color_span("ضغط بيعي (ميل هبوط)", UI_RED)
        else:
            di_forecast = color_span("توازن (محايد)", "#aaaaaa")

        # VWAP: السعر فوق VWAP = أخضر، تحته = أحمر
        if price and vwap:
            vwap_color = UI_GREEN if price >= vwap else UI_RED
        else:
            vwap_color = "#aaaaaa"
        vwap_txt = color_span(format_price(vwap), vwap_color)
        if price and vwap:
            if price >= vwap and bearish_ctx:
                vwap_forecast = color_span("فوق VWAP وسط ترند هابط — غالباً ارتداد/اختبار وليس انعكاساً تلقائياً", UI_AMBER)
            elif price < vwap and bullish_ctx:
                vwap_forecast = color_span("تحت VWAP وسط ترند صاعد — غالباً تصحيح/اختبار دعم", UI_AMBER)
            elif price >= vwap:
                vwap_forecast = color_span("فوق VWAP — سيولة/ضغط صاعد نسبي", UI_GREEN)
            else:
                vwap_forecast = color_span("تحت VWAP — ضغط هابط نسبي", UI_RED)
        else:
            vwap_forecast = color_span("غير متاح", "#aaaaaa")

        # StochRSI: تشبع قوي — عتبات قابلة للضبط (عرض فقط؛ market_readout_*)
        _st_eps = st_kd_eps
        _near = abs(st_k - st_d) < _st_eps
        if st_d > st_k and st_k >= st_ob:
            st_color = UI_RED
        elif st_k > st_d and st_k <= st_os:
            st_color = UI_GREEN
        elif _near or (st_band_lo <= st_k <= st_band_hi and st_band_lo <= st_d <= st_band_hi):
            st_color = "#aaaaaa"
        elif st_k > st_d:
            st_color = UI_AMBER
        else:
            st_color = UI_AMBER
        st_txt = color_span(f"{st_k:.1f}/{st_d:.1f}", st_color)
        # توقع: تشبع فقط = أحمر/أخضر قوي؛ تقاطع K/D في النطاق الوسطي = محايد (تذبذب)
        if _near:
            st_forecast_label = "محايد (K≈D)"
            st_forecast_color = "#aaaaaa"
        elif st_d > st_k and st_k >= st_ob:
            st_forecast_label = "تصحيح/هبوط محتمل (تشبع شراء)"
            st_forecast_color = UI_RED
        elif st_k > st_d and st_k <= st_os:
            st_forecast_label = "صعود محتمل (تشبع بيع)"
            st_forecast_color = UI_GREEN
        elif st_mid_lo <= st_k <= st_mid_hi and st_mid_lo <= st_d <= st_mid_hi:
            st_forecast_label = "تذبذب (منطقة وسطى)"
            st_forecast_color = "#aaaaaa"
        elif st_k > st_d and st_k > st_k_bull:
            if bearish_ctx:
                st_forecast_label = "K>D وسط ترند هابط — تصحيح محتمل لا يعني انعكاساً صاعداً"
                st_forecast_color = UI_AMBER
            else:
                st_forecast_label = "ميل صاعد (K فوق D)"
                st_forecast_color = UI_GREEN
        elif st_d > st_k and st_k < st_k_bear:
            if bullish_ctx:
                st_forecast_label = "D>K وسط ترند صاعد — تجميع محتمل لا يعني انعكاساً هابطاً"
                st_forecast_color = UI_AMBER
            else:
                st_forecast_label = "ميل هابط (D فوق K)"
                st_forecast_color = UI_RED
        elif st_k > st_d:
            st_forecast_label = "ضغط صعودي خفيف"
            st_forecast_color = UI_AMBER
        else:
            st_forecast_label = "ضغط هبوطي خفيف"
            st_forecast_color = UI_AMBER
        st_forecast_txt = color_span(st_forecast_label, st_forecast_color)

        # RSI للعرض في حالة السوق: لون حسب التشبع (عتبات market_readout_rsi_*)
        rsi = float(ind.get("rsi", 50) or 50)
        if rsi > rsi_ob:
            rsi_color = UI_RED
        elif rsi < rsi_os:
            rsi_color = UI_INFO
        else:
            rsi_color = "#aaaaaa"
        rsi_txt = color_span(f"{rsi:.1f}", rsi_color)
        if rsi >= rsi_ob:
            if bearish_ctx:
                rsi_forecast = color_span("تشبع شراء وسط ترند هابط — تصحيح محتمل (الترند الأعمق هابط)", UI_AMBER)
            else:
                rsi_forecast = color_span("تشبع شراء (تصحيح/هبوط محتمل)", UI_RED)
        elif rsi <= rsi_os:
            if bullish_ctx:
                rsi_forecast = color_span("تشبع بيع وسط ترند صاعد — ارتداد محتمل (الترند الأعمق صاعد)", UI_AMBER)
            else:
                rsi_forecast = color_span("تشبع بيع (ارتداد/صعود محتمل)", UI_INFO)
        elif bearish_ctx and rsi >= rsi_hi:
            rsi_forecast = color_span("RSI فوق الوسط وسط ترند هابط — ليس بالضرورة صعوداً جديداً", UI_AMBER)
        elif bullish_ctx and rsi <= rsi_lo:
            rsi_forecast = color_span("RSI تحت الوسط وسط ترند صاعد — تجميع/تصحيح محتمل", UI_AMBER)
        elif bullish_ctx and rsi >= rsi_hi:
            rsi_forecast = color_span("فوق الوسط يتماشى مع ترند صاعد", UI_GREEN)
        elif bearish_ctx and rsi <= rsi_lo:
            rsi_forecast = color_span(
                f"RSI تحت {rsi_mid_s} — زخم ضعيف يتوافق مع ترند هابط (ليست إشارة شراء؛ التشبع البيعي الشديد غالباً دون {rsi_os:.0f})",
                UI_RED,
            )
        elif rsi >= rsi_hi:
            rsi_forecast = color_span("ميل صاعد نسبي (بلا ترند عام واضح)", UI_GREEN)
        elif rsi <= rsi_lo:
            rsi_forecast = color_span("ميل هابط نسبي (بلا ترند عام واضح)", UI_RED)
        else:
            rsi_forecast = color_span("محايد", "#aaaaaa")
        # دمج RSI مع الاتجاه: تشبع شراء/بيع يُوضّح في تسمية الترند
        if trend == "UP" and rsi > rsi_ob:
            trend_label = "صاعد — تشبع شراء"
            trend_color = UI_AMBER
        elif trend == "DOWN" and rsi < rsi_os:
            trend_label = "هابط — تشبع بيع"
            trend_color = UI_INFO
        elif trend == "UP":
            trend_label = "صاعد"
            trend_color = UI_GREEN
        elif trend == "DOWN":
            trend_label = "هابط"
            trend_color = UI_RED
        else:
            trend_label = "محايد"
            trend_color = "#aaaaaa"
        trend_txt = color_span(trend_label, trend_color)
        self._update_market_trade_mode_label(_cfg_mi)
        guard_on = bool(getattr(self, "_last_composite_rebound_guard", False))
        guard_line = ""
        if guard_on:
            guard_line = (
                color_span("Rebound guard: ON (خفض تشديد بيع القاع)", UI_AMBER)
                if get_language() == "ar"
                else color_span("Rebound guard: ON (downgraded bottom strong-sell)", UI_AMBER)
            )

        line1 = f"ADX: {adx_txt} (الحد {adx_min:.0f}+ {'✓' if adx_ok else '—'}) | {adx_forecast}"
        line2 = f"+DI/-DI: {pdi_txt}/{mdi_txt} {di_side} | {di_forecast}"
        line2b = f"VWAP: {vwap_txt} ({vwap_side}) | {vwap_forecast}"
        _atr_pct_hi = atr > 0 and price > 0 and (atr / max(price, 1e-9)) * 100.0 >= atr_hi_pct
        line2c = f"ATR14: {format_price(atr)} | {color_span('تذبذب أعلى' if _atr_pct_hi else 'تذبذب طبيعي/منخفض', UI_AMBER if _atr_pct_hi else '#aaaaaa')}"
        line3 = f"StochRSI K/D: {st_txt} | التوقّع اللحظي: {st_forecast_txt} | الاتجاه العام: {trend_txt}"
        # مؤشر قوي: Supertrend (اتجاه واضح + مستوى ديناميكي)
        st_val = float(ind.get("supertrend", 0) or 0)
        st_dir = int(ind.get("supertrend_dir", 0) or 0)
        line_rsi = f"RSI: {rsi_txt} | {rsi_forecast}"
        # أنماط الشموع (بأسماء عربية) + سهم الاتجاه المتوقع تحت RSI في حالة السوق
        candle_score = float(ind.get("candle_pattern_score", 0) or 0)
        bull_list = list(ind.get("candle_pattern_bullish") or [])
        bear_list = list(ind.get("candle_pattern_bearish") or [])
        candle_summary_raw = str(ind.get("candle_pattern_summary") or "").strip()
        _candle_summary_lines = [x.strip() for x in candle_summary_raw.splitlines() if x.strip()]
        _mtf_rows_raw = ind.get("candle_pattern_mtf_rows")
        _mtf_rows = _mtf_rows_raw if isinstance(_mtf_rows_raw, list) else []
        use_mtf_colored_rows = len(_mtf_rows) > 0
        use_mtf_candle_lines = use_mtf_colored_rows or len(_candle_summary_lines) > 1

        def _fmt_pat(lst: list) -> str:
            names = []
            for x in lst[:3]:
                sx = str(x)
                names.append(pattern_to_ar(sx))
            return "، ".join(names)

        if candle_score > 0 and bull_list:
            c_color = UI_GREEN
            c_arrow = "⬆️"
            c_names = _fmt_pat(bull_list)
        elif candle_score < 0 and bear_list:
            c_color = UI_RED
            c_arrow = "⬇️"
            c_names = _fmt_pat(bear_list)
        elif candle_score > 0:
            c_color = UI_GREEN
            c_arrow = "⬆️"
            c_names = "إشارة صعودية"
        elif candle_score < 0:
            c_color = UI_RED
            c_arrow = "⬇️"
            c_names = "إشارة هبوطية"
        else:
            c_color = "#aaaaaa"
            c_arrow = "↔️"
            c_names = "محايد"
        # لا يكفي العدد الخام للأنماط: أثناء ترند هابط تُعرض الأنماط الصاعدة كتصحيح محتمل لا كـ«صعود أخضر» (والمعكس في الصعود)
        _ar_ui = get_language() == "ar"
        if not use_mtf_candle_lines:
            if bearish_ctx and (candle_score > 0 or bool(bull_list)):
                c_color = UI_AMBER
                c_arrow = "↔️"
                if _ar_ui:
                    c_suffix = " — ترند عام هابط: الأنماط الصاعدة غالباً ارتداد/بناء مؤقت"
                else:
                    c_suffix = " — downtrend: bullish patterns often mean bounce only"
                if len(str(c_names)) > 52:
                    c_names = str(c_names)[:51].rstrip("، ") + "…"
                c_names = f"{c_names}{c_suffix}"
            elif bullish_ctx and (candle_score < 0 or bool(bear_list)):
                c_color = UI_AMBER
                c_arrow = "↔️"
                if _ar_ui:
                    c_suffix = " — ترند عام صاعد: الأنماط الهابطة غالباً تصحيح مؤقت"
                else:
                    c_suffix = " — uptrend: bearish patterns often mean pullback only"
                if len(str(c_names)) > 52:
                    c_names = str(c_names)[:51].rstrip("، ") + "…"
                c_names = f"{c_names}{c_suffix}"
        if use_mtf_candle_lines:
            if use_mtf_colored_rows:
                _tone_colors = {
                    "bull": UI_GREEN,
                    "bear": UI_RED,
                    "neutral": "#aaaaaa",
                    "mixed": UI_AMBER,
                }
                _mtf_parts: list[str] = []
                for _row in _mtf_rows:
                    if not isinstance(_row, dict):
                        continue
                    _tone = str(_row.get("tone") or "neutral").strip().lower()
                    _col = _tone_colors.get(_tone, "#aaaaaa")
                    _mtf_parts.append(color_span(html_escape(str(_row.get("text") or "")), _col))
                _mtf_body = "<br>".join(_mtf_parts)
            else:
                _mtf_body = "<br>".join(html_escape(s) for s in _candle_summary_lines)
            _mtf_hdr = f"{color_span('الشموع', c_color)} {color_span(c_arrow, c_color)}"
            line_candle = f"{_mtf_hdr}<br>{_mtf_body}"
            if bearish_ctx and (candle_score > 0 or bool(bull_list)):
                _ctx = (
                    "ترند عام هابط: الصعود في الأسطر أعلاه غالباً ارتداد/بناء مؤقت"
                    if _ar_ui
                    else "Downtrend: bullish lines above often mean bounce only"
                )
                line_candle += f"<br>{color_span(html_escape(_ctx), UI_AMBER)}"
            elif bullish_ctx and (candle_score < 0 or bool(bear_list)):
                _ctx = (
                    "ترند عام صاعد: الهبوط في الأسطر أعلاه غالباً تصحيح مؤقت"
                    if _ar_ui
                    else "Uptrend: bearish lines above often mean pullback only"
                )
                line_candle += f"<br>{color_span(html_escape(_ctx), UI_AMBER)}"
        else:
            line_candle = f"الشموع: {color_span(c_names, c_color)} {color_span(c_arrow, c_color)}"
        # Supertrend: لا نربط الإظهار بـ st_val فقط (عملات رخيصة جداً)؛ نخفّف «صعود/هبوط محتمل» إلى وصف اتجاه
        if st_dir != 0:
            st_label = "صاعد ↑" if st_dir == 1 else "هابط ↓"
            _px = float(price or 0)
            _st = float(st_val or 0)
            _near_st = (
                _px > 0
                and _st > 0
                and abs(_px - _st) / _px <= st_near_ratio
            )
            if _near_st:
                st_color = UI_AMBER
                st_forecast = color_span("قرب خط Supertrend — تذبذب أو انعكاس محتمل", UI_AMBER)
            else:
                st_color = UI_GREEN if st_dir == 1 else UI_RED
                st_forecast = color_span(
                    "اتجاه صاعد (الخط كدعم تحت السعر)" if st_dir == 1 else "اتجاه هابط (الخط كمقاومة فوق السعر)",
                    st_color,
                )
            line4 = f"Supertrend: {color_span(format_price(st_val), st_color)} ({color_span(st_label, st_color)}) | {st_forecast}"
            html = (
                f"<div style='font-size:11px; color:{TOP_TEXT_MUTED}; line-height:1.45; text-align:right;'>"
                f"{guard_line + '<br>' if guard_line else ''}{line1}<br>{line2}<br>{line2b}<br>{line2c}<br>{line3}<br>{line4}<br>{line_rsi}<br>{line_candle}</div>"
            )
        else:
            html = (
                f"<div style='font-size:11px; color:{TOP_TEXT_MUTED}; line-height:1.45; text-align:right;'>"
                f"{guard_line + '<br>' if guard_line else ''}{line1}<br>{line2}<br>{line2b}<br>{line2c}<br>{line3}<br>{line_rsi}<br>{line_candle}</div>"
            )
        self.market_indicators_label.setText(html)
        self.market_indicators_label.setStyleSheet("font-size: 11px; background: transparent;")
        self._update_market_swing_peak_label()
        self._update_mtf_htf_readout()
        self._apply_composite_signal_ui(ind, info)
        # تنبيه عند اقتراب السعر من S1 أو S2 أو R1 (مرة كل 60 ثانية كحد أقصى)
        if price and (time.time() - getattr(self, "_last_pivot_alert_time", 0)) >= 60:
            s1 = float(ind.get("pivot_s1", 0) or 0)
            s2 = float(ind.get("pivot_s2", 0) or 0)
            r1 = float(ind.get("pivot_r1", 0) or 0)
            tol = 0.005
            near = []
            if s1 and abs(price - s1) / s1 <= tol:
                near.append("S1")
            if s2 and abs(price - s2) / s2 <= tol:
                near.append("S2")
            if r1 and abs(price - r1) / r1 <= tol:
                near.append("R1")
            if near:
                self._last_pivot_alert_time = time.time()
                self.status_bar_message.emit(tr("trading_near_level").format(levels=", ".join(near)))
        self._maybe_refresh_auto_strategy_line()

    def _maybe_refresh_auto_strategy_line(self) -> None:
        """عند وضع auto يُحدَّث سطر الاستراتيجية مع كل تحديث للمؤشرات (نوع المسار الفعلي)."""
        try:
            cfg = load_config()
            if str(cfg.get("strategy_mode") or "").lower() == "auto":
                self._update_strategy_display(cfg)
        except Exception:
            pass

    def _update_market_info_display(self):
        """تحديث ADX في حالة السوق؛ قوة الحجم والتقلب في لوحة التوصية."""
        info = self._last_market_info or {}
        ind = self._last_indicators or {}
        _adx_lbl = getattr(self, "market_info_trend_label", None)
        try:
            # مؤشر ADX (قوة الاتجاه): < 20 ضعيف، 20–40 قوي، > 40 قوي جداً
            adx = float(ind.get("adx14", 0) or 0)
            if _adx_lbl is not None and adx > 0:
                if adx >= 40:
                    adx_text = f"{adx:.1f} (قوي جداً)"
                    style = f"color: {UI_GREEN}; font-weight: bold; font-size: 11px;"
                elif adx >= 25:
                    adx_text = f"{adx:.1f} (قوي)"
                    style = f"color: {UI_GREEN}; font-weight: bold; font-size: 11px;"
                elif adx >= 20:
                    adx_text = f"{adx:.1f} (متوسط)"
                    style = f"color: {UI_AMBER}; font-size: 11px;"
                else:
                    adx_text = f"{adx:.1f} (ضعيف)"
                    style = "color: #aaaaaa; font-size: 11px;"
                _adx_lbl.setText(f"ADX (قوة الاتجاه): {adx_text}")
                _adx_lbl.setStyleSheet(style)
            elif _adx_lbl is not None:
                _adx_lbl.setText("ADX (قوة الاتجاه): —")
                _adx_lbl.setStyleSheet("color: #ccc; font-size: 11px;")

            vol = float(info.get("volume_strength", 0) or 0)
            if hasattr(self, "ai_volume_strength_value_label") and self.ai_volume_strength_value_label is not None:
                self.ai_volume_strength_value_label.setText(f"{vol:.2f}")
                self.ai_volume_strength_value_label.setStyleSheet(
                    f"color: {UI_GREEN}; font-weight: bold; font-size: 11px;" if vol >= 1.2 else
                    f"color: {UI_AMBER}; font-weight: bold; font-size: 11px;" if vol <= 0.8 and vol != 0 else
                    "color: #cccccc; font-weight: bold; font-size: 11px;"
                )

            volatility_pct = float(info.get("volatility_pct", 0) or 0)
            # عرض التقلب كنسبة مئوية (أوضح)، اللون: <0.3% منخفض، 0.3–0.8% متوسط، ≥0.8% مرتفع
            if hasattr(self, "ai_volatility_value_label") and self.ai_volatility_value_label is not None:
                self.ai_volatility_value_label.setText(f"{volatility_pct:.2f}%")
                self.ai_volatility_value_label.setStyleSheet(
                    f"color: {UI_RED}; font-weight: bold; font-size: 11px;" if volatility_pct >= 0.8 else
                    f"color: {UI_AMBER}; font-weight: bold; font-size: 11px;" if volatility_pct >= 0.3 else
                    f"color: {UI_GREEN}; font-weight: bold; font-size: 11px;"
                )

            _raw_bp = ind.get("buy_pressure_score")
            if hasattr(self, "ai_buy_pressure_value_label") and self.ai_buy_pressure_value_label is not None:
                if _raw_bp is not None:
                    try:
                        bp = float(_raw_bp)
                    except (TypeError, ValueError):
                        bp = None
                    if bp is not None:
                        self.ai_buy_pressure_value_label.setText(f"{bp:.1f}")
                        self.ai_buy_pressure_value_label.setStyleSheet(
                            f"color: {UI_GREEN}; font-weight: bold; font-size: 11px;" if bp >= 60 else
                            f"color: {UI_AMBER}; font-weight: bold; font-size: 11px;" if bp <= 40 else
                            "color: #cccccc; font-weight: bold; font-size: 11px;"
                        )
                    else:
                        self.ai_buy_pressure_value_label.setText("—")
                        self.ai_buy_pressure_value_label.setStyleSheet(
                            f"color: {TOP_TEXT_SECONDARY}; font-size: 11px;"
                        )
                else:
                    self.ai_buy_pressure_value_label.setText("—")
                    self.ai_buy_pressure_value_label.setStyleSheet(
                        f"color: {TOP_TEXT_SECONDARY}; font-size: 11px;"
                    )

            fgi = ind.get("fear_greed_index")
            fgc = str(ind.get("fear_greed_classification", "") or "").strip()
            if hasattr(self, "ai_fear_greed_value_label") and self.ai_fear_greed_value_label is not None:
                if fgi is not None:
                    try:
                        iv = int(fgi)
                    except (TypeError, ValueError):
                        iv = None
                    if iv is not None:
                        tail = f" ({fgc})" if fgc else ""
                        self.ai_fear_greed_value_label.setText(f"{iv}{tail}")
                        if iv <= 25:
                            _fg_style = f"color: {UI_RED}; font-weight: bold; font-size: 11px;"
                        elif iv <= 45:
                            _fg_style = f"color: {UI_AMBER}; font-weight: bold; font-size: 11px;"
                        elif iv >= 75:
                            _fg_style = f"color: {UI_GREEN}; font-weight: bold; font-size: 11px;"
                        else:
                            _fg_style = "color: #cccccc; font-weight: bold; font-size: 11px;"
                        self.ai_fear_greed_value_label.setStyleSheet(_fg_style)
                    else:
                        self.ai_fear_greed_value_label.setText("—")
                        self.ai_fear_greed_value_label.setStyleSheet(
                            f"color: {TOP_TEXT_SECONDARY}; font-size: 11px;"
                        )
                else:
                    self.ai_fear_greed_value_label.setText("—")
                    self.ai_fear_greed_value_label.setStyleSheet(
                        f"color: {TOP_TEXT_SECONDARY}; font-size: 11px;"
                    )
        except (TypeError, ValueError):
            if _adx_lbl is not None:
                _adx_lbl.setText("ADX (قوة الاتجاه): —")
            if hasattr(self, "ai_volume_strength_value_label") and self.ai_volume_strength_value_label is not None:
                self.ai_volume_strength_value_label.setText("—")
                self.ai_volume_strength_value_label.setStyleSheet(
                    f"color: {TOP_TEXT_SECONDARY}; font-size: 11px;"
                )
            if hasattr(self, "ai_volatility_value_label") and self.ai_volatility_value_label is not None:
                self.ai_volatility_value_label.setText("—")
                self.ai_volatility_value_label.setStyleSheet(
                    f"color: {TOP_TEXT_SECONDARY}; font-size: 11px;"
                )
            if hasattr(self, "ai_buy_pressure_value_label") and self.ai_buy_pressure_value_label is not None:
                self.ai_buy_pressure_value_label.setText("—")
                self.ai_buy_pressure_value_label.setStyleSheet(
                    f"color: {TOP_TEXT_SECONDARY}; font-size: 11px;"
                )
            if hasattr(self, "ai_fear_greed_value_label") and self.ai_fear_greed_value_label is not None:
                self.ai_fear_greed_value_label.setText("—")
                self.ai_fear_greed_value_label.setStyleSheet(
                    f"color: {TOP_TEXT_SECONDARY}; font-size: 11px;"
                )

    # ============================================================
    # Quick Actions — الربط مع OpenPositionsPanel
    # ============================================================
    def set_daily_pnl(self, pnl: float):
        """يُستدعى من MainWindow لتحديث إجمالي PnL (لحد الخسارة اليومية)."""
        self._daily_pnl = float(pnl)

    def _update_connection_status(self):
        """تحديث مؤشر الاتصال (أخضر = بيانات حديثة، أحمر = انقطع) وسطر الاستراتيجية الحالية من الإعدادات."""
        if not hasattr(self, "connection_label"):
            return
        elapsed = time.time() - self._last_price_update_time if self._last_price_update_time else 999
        if elapsed < 30:
            self.connection_label.setText(tr("trading_connection_ok"))
            self.connection_label.setStyleSheet(f"color: {UI_GREEN}; font-weight: bold;")
        else:
            self.connection_label.setText(tr("trading_connection_lost"))
            self.connection_label.setStyleSheet(f"color: {UI_RED}; font-weight: bold;")
        self._emit_status_message()
        # إبقاء سطر «الاستراتيجية الحالية» معبّأً دائماً من الإعدادات المحفوظة (لا يُستبدل بحالة البوت أو الاقتراح)
        try:
            self._update_strategy_display(load_config())
        except Exception:
            pass

    def _emit_status_message(self):
        """إرسال نص محدث لشريط الحالة (الرمز، الاتصال، حالة البوت)."""
        self._update_consecutive_losses_badge()
        self._update_cb_badge()
        conn = tr("trading_connection_ok") if (time.time() - (self._last_price_update_time or 0)) < 30 else tr("trading_connection_lost")
        bot = tr("trading_robot_on") if self._bot_enabled else tr("trading_robot_off")
        msg = f"{tr('main_status_symbol')}: {self.current_symbol} | {conn} | {bot}"
        self.status_bar_message.emit(msg)

    def _get_consecutive_losses(self) -> int:
        """عدد الخسائر المتتالية من سجل التداول الفعلي (trade_history)."""
        now = time.time()
        if now - float(getattr(self, "_consec_losses_cache_time", 0.0) or 0.0) < 10.0:
            return int(getattr(self, "_consec_losses_cache_value", 0) or 0)
        try:
            losses = int(count_consecutive_losses(limit=500))
        except Exception:
            losses = 0
        self._consec_losses_cache_time = now
        self._consec_losses_cache_value = int(losses)
        return int(losses)

    def _update_consecutive_losses_badge(self) -> None:
        if not hasattr(self, "consecutive_losses_badge") or self.consecutive_losses_badge is None:
            return
        cfg = load_config()
        limit = int(cfg.get("bot_max_consecutive_losses", 0) or 0)
        losses = self._get_consecutive_losses()
        self.consecutive_losses_badge.setText(f"L {losses}/{limit}")
        if limit > 0 and losses >= limit:
            self.consecutive_losses_badge.setStyleSheet(
                "padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; "
                "background-color: #7f1d1d; color: #fecaca; border: 1px solid #b91c1c;"
            )
        elif limit > 0 and losses >= max(1, limit - 1):
            self.consecutive_losses_badge.setStyleSheet(
                "padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; "
                "background-color: #78350f; color: #fde68a; border: 1px solid #d97706;"
            )
        else:
            self.consecutive_losses_badge.setStyleSheet(
                "padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; "
                "background-color: #3f3f46; color: #e5e7eb; border: 1px solid #5b5b66;"
            )

    def _update_cb_badge(self) -> None:
        if not hasattr(self, "cb_state_badge") or self.cb_state_badge is None:
            return
        cfg = load_config()
        enabled = bool(get_circuit_breaker_config(cfg)["enabled"])
        now = time.time()
        active_until = float(getattr(self, "_cb_pause_until", 0.0) or 0.0)
        if not enabled:
            self.cb_state_badge.setText("CB OFF")
            self.cb_state_badge.setStyleSheet(
                "padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; "
                "background-color: #334155; color: #cbd5e1; border: 1px solid #475569;"
            )
            return
        if active_until > now:
            remain_sec = int(active_until - now)
            mm = remain_sec // 60
            ss = remain_sec % 60
            self.cb_state_badge.setText(f"CB {mm:02d}:{ss:02d}")
            self.cb_state_badge.setStyleSheet(
                "padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; "
                "background-color: #7f1d1d; color: #fecaca; border: 1px solid #b91c1c;"
            )
        else:
            self.cb_state_badge.setText("CB READY")
            self.cb_state_badge.setStyleSheet(
                "padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; "
                "background-color: #14532d; color: #bbf7d0; border: 1px solid #16a34a;"
            )

    def _set_trade_buttons_enabled(self, enabled: bool):
        """تفعيل/تعطيل أزرار الشراء والبيع (منع نقرات مزدوجة وتجربة مستخدم أوضح)."""
        self.buy_button.setEnabled(enabled)
        self.sell_button.setEnabled(enabled)

    def buy_action(self):
        if self._execution_busy_for_orders():
            QMessageBox.information(
                self,
                tr("trading_robot_title"),
                "يُنفَّذ أمر سابق. انتظر انتهاء التنفيذ ثم جرّب مرة أخرى." if get_language() == "ar" else "An order is already in progress. Wait for it to finish, then try again.",
            )
            return
        cfg = load_config()
        limit = float(cfg.get("daily_loss_limit_usdt", 0) or 0)
        if limit > 0 and self._daily_pnl <= -limit:
            QMessageBox.warning(
                self,
                "حد الخسارة اليومية",
                f"تم الوصول إلى حد الخسارة اليومية ({limit} USDT). إيقاف فتح صفقات جديدة.",
            )
            return
        last_price = self.ws.frames["1m"].last_price
        if not last_price:
            QMessageBox.warning(self, "السعر", "لا يوجد سعر حالياً. انتظر تحديث البيانات.")
            return
        blocked, _wait_sec = self._same_symbol_buy_interval_should_block(cfg)
        if blocked:
            wait_left_min = max(1, int((_wait_sec + 59) // 60))
            QMessageBox.information(
                self,
                tr("trading_robot_title"),
                tr("bot_wait_same_symbol_buy_interval").format(m=wait_left_min),
            )
            return
        self._order_in_progress = True
        self._set_trade_buttons_enabled(False)
        self._pending_order_reason = "شراء يدوي"
        self._pending_indicators = dict(self._last_indicators or {})
        self._pending_market_info = dict(self._last_market_info or {})
        self._stamp_bot_buy_commit_ts(cfg)
        if self.is_real_mode():
            self._execute_real_order("BUY", last_price, cfg, testnet=False)
        else:
            # TESTNET: أوامر حقيقية على testnet.binance
            self._execute_real_order("BUY", last_price, cfg, testnet=True)

    def close_single_row_action(self, trade: dict, *, order_reason: str | None = None):
        """إغلاق مركز واحد — من زر ✕ بجانب الصف في «المراكز المفتوحة» أو من حد بيع/وقف لكل صف."""
        if not isinstance(trade, dict):
            return
        if self._execution_busy_for_orders():
            QMessageBox.information(
                self,
                tr("trading_robot_title"),
                "يُنفَّذ أمر سابق. انتظر انتهاء التنفيذ ثم جرّب مرة أخرى." if get_language() == "ar" else "An order is already in progress. Wait for it to finish, then try again.",
            )
            return
        cfg = load_config()
        limit = float(cfg.get("daily_loss_limit_usdt", 0) or 0)
        if limit > 0 and self._daily_pnl <= -limit:
            QMessageBox.warning(
                self,
                "حد الخسارة اليومية",
                f"تم الوصول إلى حد الخسارة اليومية ({limit} USDT). إيقاف فتح صفقات جديدة.",
            )
            return
        use_futures = _config_use_futures(cfg)
        selected_trade = trade
        selected_symbol = (trade.get("symbol") or "").strip().upper()
        if not selected_symbol or float(trade.get("quantity") or 0) <= 0:
            self._notify_error_popup(title="تنبيه", text="بيانات الصف غير صالحة.")
            return
        # كان last_price يُؤخذ من شموع الشارت فقط → بيع STOUSDT يُسجَّل بسعر زوج الشارت (مثل DOGE).
        last_price = float(self._close_ref_price_for_symbol(selected_symbol) or 0)
        if last_price <= 0:
            try:
                lp_ws = float(self.ws.frames["1m"].last_price or 0)
            except Exception:
                lp_ws = 0.0
            cur = (getattr(self, "current_symbol", None) or "").strip().upper()
            if selected_symbol == cur and lp_ws > 0:
                last_price = lp_ws
        if last_price <= 0:
            QMessageBox.warning(
                self,
                "السعر",
                "لا يوجد سعر مرجعي لرمز هذا الصف. اعرض نفس الرمز في الشارت أو انتظر التحديث."
                if get_language() == "ar"
                else "No reference price for this row. Show the same symbol on the chart or wait for data.",
            )
            return

        self._order_in_progress = True
        self._set_trade_buttons_enabled(False)
        testnet = not self.is_real_mode()
        cfg_ex = load_config()
        exchange = (cfg_ex.get("exchange") or "binance").lower()
        if exchange == "etoro":
            api_key, api_secret = load_etoro_settings()
        else:
            api_key, api_secret = get_decrypted_credentials(self, testnet=testnet)
        if not (api_key and api_secret):
            self._reenable_trade_buttons()
            QMessageBox.warning(self, "API Settings", "يرجى إدخال المفاتيح من إعدادات API (أو مفاتيح eToro عند اختيار eToro).")
            return
        if use_futures:
            try:
                if exchange == "etoro":
                    client = EtoroFuturesClient(api_key, api_secret, testnet=testnet)
                    td = dict(trade)
                    sym = (td.get("symbol") or "").strip().upper()
                    qty = float(td.get("quantity") or 0)
                    ent = float(td.get("entry_price") or 0)
                    lp_chk = float(getattr(self, "_last_price", 0) or 0)
                    if (
                        ent > 0
                        and lp_chk > 0
                        and suspect_placeholder_entry_price(ent, lp_chk)
                    ):
                        bp, bq = get_last_buy_info_for_symbol(self._history_symbol(sym))
                        if bp and float(bp) > 0:
                            log.warning(
                                "eToro إغلاق صف: دخول مشبوه %.6f — استخدام آخر شراء %.6f",
                                ent,
                                float(bp),
                            )
                            ent = float(bp)
                        if bq is not None and float(bq) > 0 and qty > float(bq) * 10:
                            qty = float(bq)
                    if qty <= 0 or not sym:
                        self._reenable_trade_buttons()
                        QMessageBox.warning(
                            self,
                            "eToro",
                            "بيانات الصف غير صالحة. حدّث المراكز من المنصة.",
                        )
                        return
                    pid_ui = None
                    try:
                        if td.get("position_id") is not None and str(td.get("position_id")).strip() != "":
                            pid_ui = int(float(str(td.get("position_id")).strip()))
                            if pid_ui <= 0:
                                pid_ui = None
                    except (TypeError, ValueError):
                        pid_ui = None
                    oid_ui = None
                    try:
                        x = td.get("etoro_open_order_id")
                        if x is not None:
                            oid_ui = int(x)
                            if oid_ui <= 0:
                                oid_ui = None
                    except (TypeError, ValueError):
                        oid_ui = None
                    self._pending_order_reason = order_reason or "بيع يدوي (eToro)"
                    self._close_position_snapshot = (sym, qty, ent)
                    self._close_pending_position_id = (
                        int(pid_ui) if pid_ui is not None and int(pid_ui) > 0 else None
                    )
                    self._close_pending_row_index = td.get("row")  # لاحقاً: إزالة هذا الصف فقط إن فشل الإغلاق
                    self._close_pending_last_price = float(
                        self._close_ref_price_for_symbol(sym) or 0
                    )
                    self._close_position_snapshots = None
                    self._close_worker = ClosePositionWorker(
                        client,
                        symbol=sym,
                        close_all=False,
                        etoro_close_spec={
                            "symbol": sym,
                            "entry_price": ent,
                            "quantity": qty,
                            "position_id": pid_ui,
                            "etoro_open_order_id": oid_ui,
                        },
                    )
                    self._close_thread = QThread()
                    self._close_worker.moveToThread(self._close_thread)
                    self._close_thread.started.connect(self._close_worker.run)
                    self._close_worker.finished.connect(
                        self._on_close_position_finished,
                        Qt.ConnectionType.QueuedConnection,
                    )
                    self._close_thread.start()
                else:
                    if exchange == "bitget":
                        client = BitgetFuturesClient(api_key, api_secret, testnet=testnet)
                    else:
                        client = BinanceFuturesClient(api_key, api_secret, testnet=testnet)
                    if not selected_symbol:
                        self._reenable_trade_buttons()
                        return
                    self._pending_order_reason = order_reason or "بيع يدوي (فيوتشر)"
                    qty = float(trade.get("quantity") or 0)
                    avg_entry = float(trade.get("entry_price") or 0)
                    self._close_position_snapshot = (selected_symbol, qty, avg_entry) if qty > 0 else None
                    self._close_pending_position_id = None
                    self._close_pending_last_price = float(
                        self._close_ref_price_for_symbol(selected_symbol) or 0
                    )
                    self._close_position_snapshots = None
                    self._close_worker = ClosePositionWorker(client, symbol=selected_symbol, close_all=False)
                    self._close_thread = QThread()
                    self._close_worker.moveToThread(self._close_thread)
                    self._close_thread.started.connect(self._close_worker.run)
                    self._close_worker.finished.connect(
                        self._on_close_position_finished,
                        Qt.ConnectionType.QueuedConnection,
                    )
                    self._close_thread.start()
            except Exception as e:
                self._reenable_trade_buttons()
                log.exception("Futures client init failed")
                QMessageBox.critical(self, "خطأ", str(e))
            return
        self._pending_order_reason = order_reason or "بيع يدوي (صف واحد)"
        sym = selected_trade.get("symbol")
        qty = float(selected_trade.get("quantity") or 0)
        entry = float(selected_trade.get("entry_price") or 0)
        if not sym or qty <= 0:
            self._reenable_trade_buttons()
            return
        self._pending_indicators = dict(self._last_indicators or {})
        self._pending_market_info = dict(self._last_market_info or {})
        self._execute_real_order(
            "SELL",
            last_price,
            cfg,
            testnet=testnet,
            symbol_override=str(sym),
            quantity_override=qty,
            avg_entry_override=entry if entry > 0 else None,
        )

    def _on_close_position_finished(self, ok: bool, msg: str, symbol: str, close_all: bool):
        self._stop_order_timeout_timer()
        if getattr(self, "_close_thread", None):
            self._close_thread.quit()
            self._close_thread.wait(3000)
            self._close_thread = None
        self._close_worker = None
        # eToro: تسجيل position_id كمُغلق قبل إعادة تفعيل الواجهة — وإلا يُعاد وقف الخسارة لنفس الصف في نفس الثانية.
        if ok and not close_all and (load_config().get("exchange") or "").lower() == "etoro":
            try:
                ep = getattr(self, "_close_pending_position_id", None)
                if ep is not None and int(ep) > 0:
                    self._etoro_mark_recent_closed_position(int(ep))
            except Exception:
                pass
        self._reenable_trade_buttons()

        mode = "testnet" if not getattr(self, "_real_mode", False) else "live"
        reason_ca = getattr(self, "_pending_order_reason", "") or "بيع يدوي"
        n_logged = 0
        total_pnl = 0.0
        close_all_snap_nonempty = False

        if close_all:
            snapshots = list(getattr(self, "_close_position_snapshots", None) or [])
            close_all_snap_nonempty = len(snapshots) > 0
            if snapshots:
                total_pnl, n_logged = self._write_close_all_to_history(
                    snapshots, mode, reason_ca, ok
                )
                self._close_position_snapshots = []
                try:
                    self.history_refresh_requested.emit()
                except Exception:
                    pass
                try:
                    from PyQt6.QtWidgets import QApplication

                    QApplication.processEvents()
                except Exception:
                    pass
                ar = get_language() == "ar"
                if n_logged > 0 or len(snapshots) > 0:
                    self._show_toast(
                        QMessageBox.Icon.Information,
                        "سجل الصفقات" if ar else "Trade history",
                        (
                            f"سُجّل {len(snapshots)} إغلاق في السجل. PnL تقريبي: {total_pnl:+.2f} USDT"
                            + ("" if ok else "\n(ظهر خطأ من المنصة — راجع المراكز.)")
                            if ar
                            else f"Logged {len(snapshots)} close(s). Approx. PnL: {total_pnl:+.2f} USDT"
                            + ("" if ok else "\n(Platform reported an error — verify positions.)")
                        ),
                        msec=10000,
                    )
            elif ok:
                log.warning("close_all ok but snapshots empty")
                self._show_toast(
                    QMessageBox.Icon.Warning,
                    "سجل الصفقات",
                    "لا توجد لقطات مراكز — حدّث المراكز من المنصة ثم أعد «إغلاق الكل»."
                    if get_language() == "ar"
                    else "No position snapshot — refresh from exchange then try close-all again.",
                    msec=9000,
                )

        if not ok:
            sym_disp_fail = self._display_symbol((symbol or "").strip().upper())
            log.warning(
                "[إغلاق/بيع] فشل — symbol=%s close_all=%s reason=%s | %s",
                sym_disp_fail,
                close_all,
                (reason_ca or "")[:80],
                (msg or "").strip()[:600],
            )
            m = (msg or "").strip()
            row_idx_fail = getattr(self, "_close_pending_row_index", None)
            is_etoro = (load_config().get("exchange") or "").lower() == "etoro"
            if not hasattr(self, "_etoro_close_retry_counts"):
                self._etoro_close_retry_counts = {}
            # 814 / internal only: إعادة المحاولة لا تفيد — نكبت SL فوراً ونخرج (يمنع حلقة كل تيك)
            if (
                is_etoro
                and row_idx_fail is not None
                and ("وقف خسارة" in (reason_ca or ""))
                and self._etoro_close_msg_implies_no_exchange_position(m)
            ):
                self._etoro_register_sl_suppress_row(
                    int(row_idx_fail),
                    (symbol or "").strip().upper(),
                    float(self._ETORO_SL_SUPPRESS_GHOST_SEC),
                    "خطأ منصة (814/لا مركز قابل للإغلاق) — أزل الصف يدوياً أو حدّث المراكز",
                )
                self._on_exchange_reports_no_position(symbol)
                return
            if "لا يوجد مركز" in m or "no open position" in m.lower() or "لا توجد صفقة" in m:
                # eToro + وقف خسارة: API قد تتأخر لحظياً في إرجاع positionId.
                # أعد مزامنة سريعة ثم أعد محاولة إغلاق نفس الصف (مرتين كحد أقصى).
                if is_etoro and ("وقف خسارة" in (reason_ca or "")) and row_idx_fail is not None:
                    key = f"{int(row_idx_fail)}::{(symbol or '').strip().upper()}"
                    tries = int((self._etoro_close_retry_counts or {}).get(key, 0) or 0)
                    if tries < 2:
                        self._etoro_close_retry_counts[key] = tries + 1
                        log.info(
                            "وقف خسارة eToro: إعادة محاولة بعد مزامنة (%s/2)",
                            tries + 1,
                        )
                        QTimer.singleShot(350, self._sync_open_positions_from_exchange)
                        def _retry_close_row():
                            try:
                                if self._positions_panel is None:
                                    return
                                r = int(row_idx_fail)
                                trd = self._positions_panel.get_close_trade_dict(r)
                                if trd:
                                    self.close_single_row_action(trd, order_reason=reason_ca or "وقف خسارة (إعادة)")
                            except Exception:
                                pass
                        QTimer.singleShot(1500, _retry_close_row)
                        return
                    self._etoro_register_sl_suppress_row(
                        int(row_idx_fail),
                        (symbol or "").strip().upper(),
                        float(self._ETORO_SL_SUPPRESS_RETRY_EXHAUSTED_SEC),
                        "نفدت إعادات الإغلاق مع «لا مركز» — راجع الصف أو المزامنة",
                    )
                self._on_exchange_reports_no_position(symbol)
            elif ("لا معرّف مركز" in m or "المنصة لم تُرجع مراكز" in m) and not close_all:
                # لا نحذف الصف محلياً عند غموض المعرّف؛ نُبقيه ونزامن.
                if self._positions_panel and symbol:
                    QTimer.singleShot(350, self._sync_open_positions_from_exchange)
                log.info(
                    "إغلاق صف: تعذر تحديد معرّف المركز — مزامنة مجدولة (symbol=%s)",
                    (symbol or "").strip().upper(),
                )
            elif not (close_all and close_all_snap_nonempty):
                QMessageBox.warning(
                    self,
                    tr("trading_robot_title"),
                    self._localize_exchange_error(msg) if msg else tr("trading_sell_failed"),
                )
            return

        reason = reason_ca
        if ok:
            try:
                self._etoro_close_retry_counts = {}
            except Exception:
                pass
            if close_all:
                self.close_all_positions.emit()
                self.set_daily_pnl(0.0)
                if (load_config().get("exchange") or "").lower() == "etoro":
                    _clear_etoro_positions_cache()
                try:
                    cfg_ca = load_config()
                    if (cfg_ca.get("exchange") or "").lower() == "etoro" and _config_use_futures(
                        cfg_ca
                    ):
                        # إغلاق الكل عبر الخيط لا يمرّ بـ _on_order_finished(SELL) — امسح قفل «انتظار تأكيد مركز»
                        self._etoro_pending_symbol_until.clear()
                except Exception:
                    pass
            else:
                sym_u = (symbol or "").strip().upper()
                pid_done = getattr(self, "_close_pending_position_id", None)
                row_idx_done = getattr(self, "_close_pending_row_index", None)
                self._close_pending_position_id = None
                self._close_pending_row_index = None  # تنظيف بعد إغلاق صف واحد
                snap0 = getattr(self, "_close_position_snapshot", None)
                sell_sym = sym_u or (
                    str(snap0[0]).strip().upper() if snap0 and snap0[0] else ""
                )
                sell_qty = float(snap0[1]) if snap0 and len(snap0) > 1 else 0.0
                sell_ae = float(snap0[2]) if snap0 and len(snap0) > 2 else 0.0
                fifo_lots_snapshot: list[tuple[float, float]] = []
                if self._positions_panel and sell_sym:
                    try:
                        fifo_lots_snapshot = self._positions_panel.get_fifo_lots_for_symbol(
                            sell_sym
                        )
                    except Exception:
                        fifo_lots_snapshot = []
                # قبل حذف الصف: أكمِل الكمية/الدخول من الجدول أو من سجل آخر شراء (مهم لـ eToro)
                if (sell_qty <= 0 or sell_ae <= 0) and self._positions_panel and sell_sym:
                    p2 = self._positions_panel.get_position_for_symbol(sell_sym)
                    if p2:
                        if sell_qty <= 0:
                            sell_qty = float(p2.get("quantity") or 0)
                        if sell_ae <= 0:
                            sell_ae = float(p2.get("entry_price") or 0)
                if (sell_qty <= 0 or sell_ae <= 0) and sell_sym:
                    bp, bq = get_last_buy_info_for_symbol(sell_sym)
                    if sell_ae <= 0 and bp is not None and float(bp) > 0:
                        sell_ae = float(bp)
                    if sell_qty <= 0 and bq is not None and float(bq) > 0:
                        sell_qty = float(bq)
                snapshot = (sell_sym, sell_qty, sell_ae)
                # eToro: سجّل position_id قبل حذف الصف — مزامنة 30s قد تعيد المركز من API بعد الإغلاق
                pid_for_mark = pid_done
                oid_for_mark = None
                try:
                    if self._positions_panel and sell_sym:
                        pm = self._positions_panel.get_position_for_symbol(sell_sym)
                        if pm:
                            if (pid_for_mark is None or int(pid_for_mark or 0) <= 0) and pm.get(
                                "position_id"
                            ):
                                pid_for_mark = int(pm["position_id"])
                            if pm.get("etoro_open_order_id") is not None:
                                oid_for_mark = int(pm["etoro_open_order_id"])
                except (TypeError, ValueError):
                    pass
                try:
                    cfg_et_mark = load_config()
                    if (cfg_et_mark.get("exchange") or "").lower() == "etoro":
                        self._etoro_mark_recent_closed_position(pid_for_mark)
                        self._etoro_mark_recent_closed_order(oid_for_mark)
                except Exception:
                    pass
                rem_sym = (sym_u or sell_sym or "").strip().upper()
                # إزالة الصف: قد يُعرف بـ position_id أو مؤشر الصف حتى لو كان رمز الاستدعاء فارغاً
                _can_remove_row = self._positions_panel and (
                    bool(rem_sym) or pid_done is not None or row_idx_done is not None
                )
                # إزالة الصف الذي نُقر عليه فقط (بالمؤشر) لتفادي إزالة صف آخر عند وجود مراكز متعددة لنفس الرمز
                if _can_remove_row:
                    if pid_done is not None:
                        if not self._positions_panel.remove_position_by_position_id(int(pid_done)):
                            self._positions_panel.remove_positions_for_symbol(rem_sym)
                    elif row_idx_done is not None and hasattr(self._positions_panel, "remove_row_at"):
                        try:
                            self._positions_panel.remove_row_at(int(row_idx_done))
                        except (TypeError, ValueError):
                            if snapshot and float(snapshot[1] or 0) > 0 and float(snapshot[2] or 0) > 0 and hasattr(self._positions_panel, "remove_trade"):
                                self._positions_panel.remove_trade(
                                    str(snapshot[0] or rem_sym).strip().upper(),
                                    float(snapshot[2]),
                                    float(snapshot[1]),
                                )
                            else:
                                self._positions_panel.remove_positions_for_symbol(rem_sym)
                    elif (
                        snapshot
                        and float(snapshot[1] or 0) > 0
                        and float(snapshot[2] or 0) > 0
                        and hasattr(self._positions_panel, "remove_trade")
                    ):
                        self._positions_panel.remove_trade(
                            str(snapshot[0] or rem_sym).strip().upper(),
                            float(snapshot[2]),
                            float(snapshot[1]),
                        )
                    else:
                        self._positions_panel.remove_positions_for_symbol(rem_sym)
                # eToro: إغلاق واحد على المنصة = غالباً لا يبقى مركز للرمز — امسح أي صفوف متبقية
                # (مكررات واجهة، أو إزالة بـ position_id أزالت صفاً واحداً فقط، أو كمية مجمّعة لم تطابق remove_trade).
                try:
                    cfg_rm = load_config()
                    if (
                        (cfg_rm.get("exchange") or "").lower() == "etoro"
                        and rem_sym
                        and self._positions_panel
                    ):
                        self._positions_panel.remove_positions_for_symbol(rem_sym)
                except Exception:
                    pass
                try:
                    cfg_ep = load_config()
                    if (cfg_ep.get("exchange") or "").lower() == "etoro" and _config_use_futures(
                        cfg_ep
                    ):
                        # إغلاق صف عبر CloseWorker لا يمرّ بـ _on_order_finished(SELL) — وإلا يبقى البوت في
                        # «eToro position confirmation» رغم عدم وجود مركز حتى تنتهي مهلة الشراء السابقة.
                        for _k in (
                            str(rem_sym or "").strip().upper(),
                            str(sell_sym or "").strip().upper(),
                            str(sym_u or "").strip().upper(),
                            str(getattr(self, "current_symbol", None) or "").strip().upper(),
                        ):
                            if _k:
                                self._etoro_pending_symbol_until.pop(_k, None)
                except Exception:
                    pass
                # تحديث جدول المراكز في «حالة السوق» فوراً — قبل جلب الأسعار/السجل (قد يستغرق ثوانٍ)
                self._update_open_position_display(getattr(self, "_last_price", 0) or 0)
                try:
                    if (load_config().get("exchange") or "").lower() == "etoro":
                        QTimer.singleShot(500, self._sync_open_positions_from_exchange)
                except Exception:
                    pass
                # إغلاق آخر مركز عبر خيط الإغلاق: كان الكاش يبقى فيُعاد الموقف بعد إعادة تشغيل البرنامج
                try:
                    if (load_config().get("exchange") or "").lower() == "etoro":
                        if self._count_open_position_rows() == 0:
                            _clear_etoro_positions_cache()
                except Exception:
                    pass
                sell_u = (sell_sym or "").strip().upper()
                cur_u = (getattr(self, "current_symbol", None) or "").strip().upper()
                px = float(getattr(self, "_close_pending_last_price", 0) or 0)
                # كان يُخزَّن سعر الشارت لرمز آخر → يظهر بيع PAXG بسعر BTC
                if sell_u and cur_u and sell_u != cur_u:
                    px = 0.0
                if px <= 0:
                    px = float(self._close_ref_price_for_symbol(sell_sym) or 0)
                if px <= 0:
                    px = float(self._price_for_closed_symbol(self._display_symbol(sell_sym)) or 0)
                # eToro: جلب سعر مباشر إن بقي 0 (بعد إصلاح ETORO_* في get_instrument_id)
                if px <= 0 and sell_sym:
                    try:
                        cfg_px = load_config()
                        if (cfg_px.get("exchange") or "").lower() == "etoro":
                            u, k = load_etoro_settings()
                            if u and k:
                                c = EtoroFuturesClient(u, k, testnet=not self.is_real_mode())
                                for cand in (sell_sym, self._display_symbol(sell_sym)):
                                    cs = str(cand or "").strip()
                                    if not cs:
                                        continue
                                    px = float(c.get_last_price(cs) or 0)
                                    if px > 0:
                                        break
                    except Exception:
                        pass
                # إن بقي سعر شاذ مقارنة بالدخول — لا تسجّل ربحاً وهمياً
                try:
                    ae_chk = float(sell_ae or 0)
                    if px > 0 and ae_chk > 0 and max(px / ae_chk, ae_chk / px) > 8.0:
                        log.warning(
                            "سجل إغلاق: سعر بيع مشبوه لرمز %s (px=%.4f دخول=%.4f) — تجاهل لتفادي ربح خاطئ",
                            sell_u,
                            px,
                            ae_chk,
                        )
                        px = 0.0
                except Exception:
                    pass
                # لا نستخدم سعر الدخول كسعر بيع — يُظهر ربحاً 0 دائماً عندما يفشل جلب السوق.
                pnl = None
                if sell_sym and sell_qty > 0:
                    sym = self._history_symbol(sell_sym)
                    qty = sell_qty
                    ae = float(sell_ae or 0)
                    if px > 0 and suspect_placeholder_entry_price(ae, px):
                        bp, bq = get_last_buy_info_for_symbol(sym)
                        if bp and float(bp) > 0:
                            log.warning(
                                "سجل البيع: دخول من المركز مشبوه %.6f — استخدام آخر شراء %.6f",
                                ae,
                                float(bp),
                            )
                            ae = float(bp)
                        if bq is not None and float(bq) > 0 and qty > float(bq) * 10:
                            qty = float(bq)
                    used_multi_fifo = False
                    if (
                        px > 0
                        and qty > 0
                        and fifo_lots_snapshot
                        and len(fifo_lots_snapshot) > 1
                    ):
                        parts = self._allocate_fifo_sell_parts(fifo_lots_snapshot, float(qty))
                        if len(parts) > 1:
                            total_pnl = 0.0
                            for ep_leg, qq in parts:
                                pt = record_trade(
                                    sym,
                                    "SELL",
                                    float(px),
                                    float(qq),
                                    mode=mode,
                                    reason=reason,
                                    avg_buy_price=float(ep_leg),
                                    use_fifo=False,
                                )
                                if pt is not None:
                                    total_pnl += float(pt)
                            pnl = round(total_pnl, 4)
                            used_multi_fifo = True
                    if not used_multi_fifo:
                        if ae > 0 and px > 0:
                            _ok_h, _, pnl = append_sell_forced(
                                sym, qty, ae, float(px), mode, reason
                            )
                            if not _ok_h:
                                pnl = record_trade(
                                    sym,
                                    "SELL",
                                    float(px),
                                    qty,
                                    mode=mode,
                                    reason=reason,
                                    avg_buy_price=ae,
                                    use_fifo=False,
                                )
                        else:
                            pnl = record_trade(
                                sym,
                                "SELL",
                                float(px or 0),
                                qty,
                                mode=mode,
                                reason=reason,
                                avg_buy_price=None,
                                use_fifo=True,
                            )
                    if pnl is None and ae > 0 and px > 0 and qty > 0:
                        pnl = round((float(px) - ae) * qty, 4)
                        patch_last_sell_pnl(
                            sym,
                            pnl,
                            avg_buy_price=ae,
                            exit_price=float(px),
                            quantity=qty,
                        )
                elif sell_sym:
                    # إغلاق نجح لكن الكمية غير معروفة في الجدول — جرّب كمية آخر شراء من السجل
                    _bp, _bq = get_last_buy_info_for_symbol(self._history_symbol(sell_sym))
                    q_hist = float(_bq) if _bq is not None and float(_bq) > 0 else 0.0
                    if px > 0 and q_hist > 0:
                        pnl = record_trade(
                            self._history_symbol(sell_sym),
                            "SELL",
                            px,
                            q_hist,
                            mode=mode,
                            reason=reason + " — كمية من السجل",
                            avg_buy_price=None,
                            use_fifo=True,
                        )
                try:
                    self.history_refresh_requested.emit()
                except Exception:
                    pass
                if pnl is not None:
                    self._last_realized_pnl = float(pnl)
                if hasattr(self, "last_trade_label") and self.last_trade_label:
                    if pnl is not None:
                        pnl_f = float(pnl)
                        self.last_trade_label.setText(f"{pnl_f:+.2f}")
                        self.last_trade_label.setStyleSheet(
                            f"color: {UI_GREEN}; font-weight: bold; font-size: 12px;" if pnl_f >= 0 else f"color: {UI_RED}; font-weight: bold; font-size: 12px;"
                        )
                    else:
                        self.last_trade_label.setText("—")
                        self.last_trade_label.setStyleSheet("color: #aaa; font-weight: bold; font-size: 12px;")
                # إشعار مثل الشراء + نافذة صغيرة لا تُغلق بسرعة عن أنظار المستخدم
                if sell_sym:
                    self._notify_trade_popup(
                        side="SELL",
                        symbol=sell_sym,
                        price=float(px or 0),
                        qty=float(sell_qty or 0),
                        mode=mode,
                        reason=reason
                        + ("" if sell_qty > 0 else " — راجع سجل الصفقات إن لم تُحسب الكمية"),
                        pnl=pnl,
                    )
                    ar_toast = get_language() == "ar"
                    pnl_s = f"{float(pnl):+.2f} USDT" if pnl is not None else "—"
                    sym_disp = self._display_symbol(sell_sym)
                    self._show_toast(
                        QMessageBox.Icon.Information,
                        "تم البيع" if ar_toast else "Sold",
                        f"{sym_disp} @ {format_price(px)} | PnL: {pnl_s}",
                        msec=12000,
                    )
                    self._flash_bot_status(
                        f"تم بيع {sym_disp} | ربح/خسارة: {pnl_s}",
                        seconds=14,
                    )
                    try:
                        send_trade_notification(
                            side="SELL",
                            symbol=sell_sym,
                            price=float(px or 0),
                            qty=float(sell_qty or 0),
                            mode=mode,
                            pnl=pnl,
                            is_bot=bool(
                                reason
                                and any(
                                    x in (reason or "")
                                    for x in (
                                        "حد البيع",
                                        "وقف الخسارة",
                                        "ذروة",
                                        "التتبع",
                                        "توصية البوت",
                                        "البوت",
                                    )
                                )
                            ),
                            confidence=None,
                        )
                    except Exception:
                        log.warning("Telegram notify after close failed", exc_info=True)
                    if reason and any(
                        x in (reason or "")
                        for x in ("حد البيع", "وقف الخسارة", "ذروة", "التتبع", "توصية البوت")
                    ):
                        record_bot_sell_outcome(
                            sell_sym,
                            float(px or 0),
                            float(pnl) if pnl is not None else 0.0,
                            quantity_sold=float(sell_qty or 0.0),
                        )
            self._update_open_position_display(getattr(self, "_last_price", 0) or 0)
            # مثل _on_order_finished: إغلاق من جدول المراكز (CloseWorker) كان لا يحدّث شريط الرصيد
            QTimer.singleShot(800, lambda: self._emit_balance_for_status_bar(testnet=(not self.is_real_mode())))
            self.status_bar_message.emit(tr("trading_sell_done"))

    def _reenable_trade_buttons(self):
        self._order_in_progress = False
        self._set_trade_buttons_enabled(True)

    def _stop_order_timeout_timer(self) -> None:
        """إيقاف مؤقت مهلة OrderWorker — يُستدعى أيضاً عند الإغلاق بـ CloseWorker حتى لا يبقى مؤقت قديم يفعّل _reenable."""
        try:
            t = getattr(self, "_order_timeout_timer", None)
            if t is not None:
                t.stop()
        except Exception:
            pass

    def _close_ref_price_for_symbol(self, symbol: str) -> float:
        """سعر مرجعي لإغلاق/سجل: سعر الشارت فقط إن طابق الرمز، وإلا جلب حسب الرمز (بدون خلط أصول)."""
        sym = (symbol or "").strip().upper()
        if not sym:
            return 0.0
        cur = (getattr(self, "current_symbol", None) or "").strip().upper()
        lp = float(getattr(self, "_last_price", 0) or 0)
        if sym == cur and lp > 0:
            return lp
        return float(self._price_for_closed_symbol(sym) or 0)

    def _price_for_closed_symbol(self, symbol: str) -> float:
        """
        سعر السوق التقريبي لرمز المركز المُغلق — لحساب الربح/الخسارة في السجل.
        (سعر الشارت الحالي يخص current_symbol فقط؛ استخدامه لكل الرموز يُظهر ربحاً خاطئاً.)
        """
        sym = (symbol or "").strip().upper()
        if not sym:
            return 0.0
        cur = (getattr(self, "current_symbol", None) or "").strip().upper()
        lp = float(getattr(self, "_last_price", 0) or 0)
        if sym == cur and lp > 0:
            return lp
        cfg = load_config()
        ex = (cfg.get("exchange") or "binance").lower()
        testnet = not self.is_real_mode()
        try:
            if ex == "etoro":
                u, k = load_etoro_settings()
                if u and k:
                    c = EtoroFuturesClient(u, k, testnet=testnet)
                    px = float(c.get_last_price(sym) or 0)
                    if px > 0:
                        return px
            import requests

            url = (
                "https://testnet.binancefuture.com/fapi/v1/ticker/price"
                if testnet
                else "https://fapi.binance.com/fapi/v1/ticker/price"
            )
            r = requests.get(url, params={"symbol": sym}, timeout=8)
            if r.ok:
                p = float((r.json() or {}).get("price") or 0)
                if p > 0:
                    return p
        except Exception as e:
            log.debug("_price_for_closed_symbol(%s): %s", sym, e)
        # لا تُرجع سعر الشارت لرمز مختلف — كان يسجّل بيع PAXG بسعر BTC (ربح خيالي)
        try:
            spot_base = (
                "https://testnet.binance.vision/api/v3/ticker/price"
                if testnet
                else "https://api.binance.com/api/v3/ticker/price"
            )
            r2 = requests.get(spot_base, params={"symbol": sym}, timeout=8)
            if r2.ok:
                p2 = float((r2.json() or {}).get("price") or 0)
                if p2 > 0:
                    return p2
        except Exception as e:
            log.debug("_price_for_closed_symbol spot %s: %s", sym, e)
        return 0.0

    def _enrich_snapshots_with_mark_price(self, raw_snaps: list, client, exchange: str) -> list:
        out = []
        for row in raw_snaps or []:
            if len(row) < 3:
                continue
            s, q, e = str(row[0]).strip().upper(), float(row[1]), float(row[2])
            if q <= 0:
                continue
            mk = float(self._price_for_closed_symbol(s) or 0)
            if mk <= 0 and client is not None:
                try:
                    if (exchange or "").lower() == "etoro":
                        mk = float(client.get_last_price(s) or 0)
                except Exception:
                    pass
            out.append((s, q, e, mk))
        return out

    def _write_close_all_to_history(
        self, snapshots: list, mode: str, reason: str, platform_ok: bool
    ) -> tuple[float, int]:
        """كتابة إغلاق الكل في trade_history.json — يُستدعى حتى لو أعادت المنصة ok=False."""
        total, n_ok = 0.0, 0
        rsn = (reason or "إغلاق الكل").strip()
        if not platform_ok:
            rsn += " | راجع المنصة"
        for row in snapshots or []:
            if len(row) >= 4:
                sym, qty, ae, mk = row[0], float(row[1]), float(row[2]), float(row[3])
            else:
                sym, qty, ae = row[0], float(row[1]), float(row[2])
                mk = 0.0
            sym = str(sym or "").strip().upper()
            sym_hist = self._history_symbol(sym)
            if qty <= 0 or not sym:
                continue
            sell_px = float(mk) if mk > 0 else 0.0
            if sell_px <= 0:
                sell_px = float(self._price_for_closed_symbol(sym) or self._price_for_closed_symbol(sym_hist) or 0)
            if sell_px <= 0:
                sell_px = float(getattr(self, "_last_price", 0) or 0)
            if sell_px <= 0:
                sell_px = float(ae if ae > 0 else 1.0)
            ok_w, _path, pnl = append_sell_forced(sym_hist, qty, ae, sell_px, mode, rsn)
            if ok_w:
                n_ok += 1
                if pnl is not None:
                    total += float(pnl)
        return total, n_ok

    def _snapshot_positions_for_close_all(self, client, exchange: str) -> list:
        """
        لقطات (رمز، كمية، سعر دخول) قبل إغلاق الكل لتسجيل PnL.
        أولاً من جدول المراكز؛ إن كان فارغاً أو بلا دخول — من API المنصة.
        """
        out: list[tuple[str, float, float]] = []
        try:
            if self._positions_panel and hasattr(self._positions_panel, "table"):
                tbl = self._positions_panel.table
                for row in range(tbl.rowCount()):
                    sym_item = tbl.item(row, 0)
                    entry_item = tbl.item(row, 1)
                    qty_item = tbl.item(row, 2)
                    if not sym_item or not qty_item:
                        continue
                    sym = (sym_item.text() or "").strip().upper()
                    if not sym:
                        continue
                    try:
                        qty = float(str(qty_item.text() or "0").replace(",", ""))
                    except (ValueError, TypeError):
                        continue
                    if qty <= 0:
                        continue
                    avg_entry = 0.0
                    if entry_item and entry_item.text():
                        try:
                            avg_entry = float(str(entry_item.text()).replace(",", ""))
                        except (ValueError, TypeError):
                            pass
                    if avg_entry <= 0:
                        val_item = tbl.item(row, 3)
                        if val_item and val_item.text():
                            try:
                                vt = (
                                    (val_item.text() or "")
                                    .replace(",", "")
                                    .replace("USDT", "")
                                    .replace("$", "")
                                    .strip()
                                )
                                val = float(vt)
                                if val > 0:
                                    avg_entry = val / qty
                            except (ValueError, TypeError):
                                pass
                    if avg_entry > 0:
                        out.append((sym, qty, avg_entry))
        except Exception as e:
            log.warning("_snapshot_positions_for_close_all UI: %s", e)
        if out:
            return out
        if client is None:
            return out
        ex = (exchange or "").lower()
        try:
            if ex == "etoro":
                for item in client.get_positions() or []:
                    r = etoro_row_from_pnl_item(item)
                    if not r:
                        continue
                    q = float(r.get("quantity") or 0)
                    e = float(r.get("entry_price") or 0)
                    sym = (r.get("symbol") or "").strip().upper()
                    if sym and q > 0 and e > 0:
                        out.append((sym, q, e))
            else:
                gop = getattr(client, "get_open_positions", None)
                if callable(gop):
                    for p in gop() or []:
                        if not isinstance(p, dict):
                            continue
                        sym = (p.get("symbol") or "").strip().upper()
                        q = float(p.get("quantity") or 0)
                        e = float(p.get("entry_price") or 0)
                        if sym and q > 0 and e > 0:
                            out.append((sym, q, e))
        except Exception as e:
            log.warning("_snapshot_positions_for_close_all API: %s", e)
        return out

    def _exit_price_after_close_all(self, symbol: str, last_price: float) -> float:
        px = self._price_for_closed_symbol(symbol) or float(last_price or 0)
        if px > 0:
            return px
        try:
            cfg = load_config()
            if (cfg.get("exchange") or "").lower() == "etoro":
                u, k = load_etoro_settings()
                if u and k:
                    c = EtoroFuturesClient(u, k, testnet=not self.is_real_mode())
                    t = float(c.get_last_price(symbol) or 0)
                    if t > 0:
                        return t
        except Exception as e:
            log.debug("_exit_price_after_close_all: %s", e)
        return float(last_price or 0)

    def _on_exchange_reports_no_position(self, symbol: str = None):
        """المنصة تُبلغ أنه لا يوجد مركز.

        مهم: هذه الرسالة قد تكون "خاطئة/مؤقتة" بسبب تأخر/مشكلة API، لذلك لا نحذف من الواجهة مباشرة
        إلا بعد محاولة تحقق سريع من المنصة (خصوصاً للفيوتشر).
        """
        sym = (symbol or "").strip().upper() or None
        removed_ui = False
        try:
            cfg = load_config()
            use_futures = _config_use_futures(cfg)
            exchange = (cfg.get("exchange") or "binance").lower()
            testnet = not self.is_real_mode()

            # تحقق سريع من المنصة قبل حذف الصف من الواجهة (فيوتشر فقط)
            if sym and use_futures:
                api_key = api_secret = None
                if exchange == "etoro":
                    api_key, api_secret = load_etoro_settings()
                else:
                    api_key, api_secret = get_decrypted_credentials(self, testnet=testnet)
                if api_key and api_secret:
                    client = None
                    if exchange == "etoro":
                        client = EtoroFuturesClient(api_key, api_secret, testnet=testnet)
                        # إن توفر تحقق مباشر نستخدمه
                        has_pos = getattr(client, "has_position_for_symbol", None)
                        if callable(has_pos):
                            if has_pos(sym):
                                # المركز موجود فعلاً — لا نحذف من الواجهة
                                if hasattr(self, "bot_status_label") and self.bot_status_label:
                                    self.bot_status_label.setText("تنبيه: المنصة قالت لا يوجد مركز لكن التحقق وجد مركزاً — تم الإبقاء على الصفقة")
                                QTimer.singleShot(500, self._sync_open_positions_from_exchange)
                                return
                    elif exchange == "bitget":
                        client = BitgetFuturesClient(api_key, api_secret, testnet=testnet)
                    else:
                        client = BinanceFuturesClient(api_key, api_secret, testnet=testnet)

                    # Binance/Bitget: تحقق عبر get_open_positions إن توفرت
                    get_open = getattr(client, "get_open_positions", None)
                    if callable(get_open):
                        positions = get_open() or []
                        for p in positions:
                            try:
                                ps = str(p.get("symbol") or "").strip().upper()
                            except Exception:
                                ps = ""
                            if ps == sym:
                                if hasattr(self, "bot_status_label") and self.bot_status_label:
                                    self.bot_status_label.setText("تنبيه: تم العثور على مركز مفتوح بعد التحقق — لم نحذف الصفقة من الواجهة")
                                QTimer.singleShot(500, self._sync_open_positions_from_exchange)
                                return
        except Exception as e:
            log.debug("No-position verify failed (ignored): %s", e)

        # إذا لم نستطع التحقق أو لم نجد مركزاً: لا نحذف فوراً من الواجهة.
        # بعض المنصات تُرجع "no position" بشكل مؤقت/متذبذب؛ الحذف الفوري كان يُخفي صفقات مفتوحة فعلياً.
        # نعتمد مزامنة لاحقة لتأكيد الحالة بدلاً من الإزالة المباشرة.
        removed_ui = False
        if hasattr(self, "bot_status_label") and self.bot_status_label:
            self.bot_status_label.setText(
                "لا يوجد مركز حسب المنصة حالياً — لم نحذف الصفوف تلقائياً، سيتم التحقق بالمزامنة."
            )
        QTimer.singleShot(500, self._sync_open_positions_from_exchange)

    def _execute_real_order(
        self,
        side: str,
        last_price: float,
        cfg: dict,
        *,
        testnet: bool = False,
        symbol_override: str = None,
        quantity_override: float | None = None,
        avg_entry_override: float | None = None,
    ):
        """تنفيذ أمر حقيقي في خيط منفصل. testnet=True = مفتاح Testnet (وهمي)، False = مفتاح Mainnet (حقيقي).
        إذا كانت الرافعة > 1 يُستخدم الفيوتشر (Futures) وتُطبَّق الرافعة على حجم المركز وحساب الربح/الخسارة.
        symbol_override: عند البيع يدوياً لصفقة محددة يُمرَّر رمز الصفقة المحددة."""
        order_symbol = symbol_override or self.current_symbol
        exchange = (cfg.get("exchange") or "binance").lower()
        if exchange == "binance":
            order_symbol = binance_spot_pair_symbol(order_symbol)
        lp = float(last_price or 0)
        if lp <= 0:
            lp = float(getattr(self, "_last_price", 0) or 0)
        if lp <= 0 and order_symbol:
            lp = float(self._price_for_closed_symbol(order_symbol) or 0)
        if lp <= 0:
            self._reenable_trade_buttons()
            self._notify_error_popup(
                title="السعر",
                text="السعر اللحظي غير متاح — انتظر تحديث الشارت ثم أعد المحاولة (مهم لحساب الربح عند البيع).",
            )
            return
        last_price = lp
        use_futures = _config_use_futures(cfg)
        leverage = max(1, int(cfg.get("leverage", 1) or 1))
        if exchange == "etoro":
            api_key, api_secret = load_etoro_settings()
            if not (api_key and api_secret):
                self._reenable_trade_buttons()
                self._notify_error_popup(
                    title="فشل تنفيذ الأمر",
                    text="يرجى إدخال User Key و API Key من إعدادات API (قسم eToro) قبل التداول.",
                )
                return
        else:
            api_key, api_secret = get_decrypted_credentials(self, testnet=testnet)
            if not (api_key and api_secret):
                self._reenable_trade_buttons()
                self._notify_error_popup(
                    title="فشل تنفيذ الأمر",
                    text="يرجى إدخال API Key و Secret من زر API Settings قبل التداول.",
                )
                return
        try:
            if exchange == "etoro":
                if use_futures:
                    client = EtoroFuturesClient(api_key, api_secret, testnet=testnet)
                    ok_lev, msg_lev = client.set_leverage(order_symbol, leverage)
                    if not ok_lev:
                        self._reenable_trade_buttons()
                        self._notify_error_popup(title="الرافعة", text=msg_lev or "تعذّر ضبط الرافعة.")
                        return
                else:
                    client = EtoroSpotClient(api_key, api_secret, testnet=testnet)
            elif use_futures:
                if exchange == "bitget":
                    client = BitgetFuturesClient(api_key, api_secret, testnet=testnet)
                else:
                    client = BinanceFuturesClient(api_key, api_secret, testnet=testnet)
                ok_lev, msg_lev = client.set_leverage(order_symbol, leverage)
                if not ok_lev:
                    self._reenable_trade_buttons()
                    self._notify_error_popup(title="الرافعة", text=msg_lev or "تعذّر ضبط الرافعة على المنصة.")
                    return
            else:
                if exchange == "bitget":
                    client = BitgetSpotClient(api_key, api_secret, testnet=testnet)
                else:
                    client = BinanceSpotClient(api_key, api_secret, testnet=testnet)
        except Exception as e:
            self._reenable_trade_buttons()
            log.exception("Client init failed")
            self._notify_error_popup(title="خطأ", text=str(e))
            return
        # eToro: واجهة /pnl قد ترجع قوائم فارغة — لا نمنع البيع إن كان في الجدول كمية أو position_id أو order_id
        etoro_close_pid: int | None = None
        etoro_close_oid: int | None = None
        etoro_panel_pos = None
        if side == "SELL" and exchange == "etoro" and use_futures and self._positions_panel:
            try:
                etoro_panel_pos = self._positions_panel.get_position_for_symbol(order_symbol)
            except Exception:
                etoro_panel_pos = None
            if etoro_panel_pos:
                try:
                    pv = etoro_panel_pos.get("position_id")
                    if pv is not None and int(pv) > 0:
                        etoro_close_pid = int(pv)
                except (TypeError, ValueError):
                    etoro_close_pid = None
                try:
                    ov = etoro_panel_pos.get("etoro_open_order_id")
                    if ov is not None and int(ov) > 0:
                        etoro_close_oid = int(ov)
                except (TypeError, ValueError):
                    etoro_close_oid = None
        if side == "SELL" and exchange == "etoro" and use_futures and self._positions_panel:
            # fallback: إن فشل التجميع، خذ أي معرّف من صفوف الرمز في الجدول
            if etoro_close_pid is None and etoro_close_oid is None and hasattr(self._positions_panel, "get_any_position_hint_for_symbol"):
                try:
                    hint = self._positions_panel.get_any_position_hint_for_symbol(order_symbol) or {}
                    hvp = hint.get("position_id")
                    hvo = hint.get("etoro_open_order_id")
                    if hvp is not None and int(hvp) > 0:
                        etoro_close_pid = int(hvp)
                    if hvo is not None and int(hvo) > 0:
                        etoro_close_oid = int(hvo)
                except Exception:
                    pass

        if side == "SELL" and exchange == "etoro" and use_futures and hasattr(client, "has_position_for_symbol"):
            panel_has_qty = bool(
                etoro_panel_pos and float(etoro_panel_pos.get("quantity") or 0) > 0
            )
            order_symbol_disp = self._display_symbol(order_symbol)
            log.info(
                "[eToro SELL prep] symbol=%s panel_has_qty=%s pid=%s oid=%s",
                order_symbol_disp,
                panel_has_qty,
                etoro_close_pid,
                etoro_close_oid,
            )
            if (
                not panel_has_qty
                and etoro_close_pid is None
                and etoro_close_oid is None
                and not client.has_position_for_symbol(order_symbol)
            ):
                self._on_exchange_reports_no_position(order_symbol)
                self._reenable_trade_buttons()
                return

        # بيع eToro (بوت/زر بيع عام): استخدم نفس مسار الإغلاق اليدوي للصف (ClosePositionWorker)
        # حتى يكون سلوك البيع وتسجيل الربح/الخسارة موحداً.
        if side == "SELL" and exchange == "etoro" and use_futures:
            close_qty = 0.0
            close_entry = 0.0
            try:
                if etoro_panel_pos:
                    close_qty = float(etoro_panel_pos.get("quantity") or 0)
                    close_entry = float(etoro_panel_pos.get("entry_price") or 0)
            except Exception:
                close_qty = 0.0
                close_entry = 0.0
            lp_row = float(last_price or 0)
            if (
                close_entry > 0
                and lp_row > 0
                and suspect_placeholder_entry_price(close_entry, lp_row)
            ):
                hs = self._history_symbol(order_symbol)
                bp, bq = get_last_buy_info_for_symbol(hs)
                if bp and float(bp) > 0:
                    log.warning(
                        "eToro بيع: تصحيح دخول من لوحة %.6f → آخر شراء %.6f",
                        close_entry,
                        float(bp),
                    )
                    close_entry = float(bp)
                if bq is not None and float(bq) > 0 and close_qty > float(bq) * 10:
                    close_qty = float(bq)
            if close_qty > 0:
                self._close_position_snapshot = (order_symbol, close_qty, close_entry)
                self._close_pending_position_id = etoro_close_pid
                self._close_pending_row_index = None
                self._close_pending_last_price = float(
                    self._close_ref_price_for_symbol(order_symbol) or 0
                )
                self._close_position_snapshots = None
                self._close_worker = ClosePositionWorker(
                    client,
                    symbol=order_symbol,
                    close_all=False,
                    etoro_close_spec={
                        "symbol": order_symbol,
                        "entry_price": close_entry,
                        "quantity": close_qty,
                        "position_id": etoro_close_pid,
                        "etoro_open_order_id": etoro_close_oid,
                    },
                )
                self._close_thread = QThread()
                self._close_worker.moveToThread(self._close_thread)
                self._close_thread.started.connect(self._close_worker.run)
                self._close_worker.finished.connect(
                    self._on_close_position_finished,
                    Qt.ConnectionType.QueuedConnection,
                )
                self._stop_order_timeout_timer()
                self._close_thread.start()
                return
        amount_type = cfg.get("amount_type", "value")
        if amount_type == "percent":
            balance = client.get_usdt_balance()
            pct = float(cfg.get("amount_percent", 10.0) or 10.0)
            amount_usdt = balance * (pct / 100.0)
            if balance <= 0:
                self._reenable_trade_buttons()
                hint = ""
                if testnet and use_futures:
                    hint = (
                        "\n\nتلميح: مفاتيحك من Spot Test Network (testnet.binance.vision). "
                        "عند الرافعة أكبر من 1x يُستخدم العقود (Futures) وشبكة أخرى (testnet.binancefuture.com). "
                        "جرّب ضبط الرافعة على 1x لاستخدام السبوت مع نفس المفاتيح، أو أنشئ مفتاحاً من Futures Testnet."
                    )
                self._notify_error_popup(
                    title="المبلغ",
                    text="لا يمكن استخدام نسبة من الرصيد: الرصيد غير متوفر أو صفر." + hint,
                )
                return
        else:
            amount_usdt = float(cfg.get("amount_usdt", 50) or 50)
        min_amount = 1.0 if testnet else 5.0  # في Testnet نسمح بمبلغ أقل للتجربة
        if amount_usdt < min_amount:
            self._reenable_trade_buttons()
            self._notify_error_popup(title="المبلغ", text=f"الحد الأدنى للمبلغ {min_amount:.0f} USDT.")
            return
        # شراء: احترام bot_max_open_trades و max_trades_per_symbol مرة أخرى هنا لأن الجدول قد يتأخر عن المنصة
        # أو يكون أمر سابق قد نُفّذ بين حساب البوت وفتح الخيط.
        if side == "BUY":
            max_open_cap = _bot_max_open_trades_cap(cfg)
            max_per_sym = int(cfg.get("max_trades_per_symbol", 0) or 0)
            n_ui = self._logical_open_count_for_bot()
            n_eff = n_ui
            get_pos = getattr(client, "get_positions", None)
            if not callable(get_pos):
                get_pos = getattr(client, "get_open_positions", None)
            if callable(get_pos):
                try:
                    pl = get_pos()
                    if isinstance(pl, list):
                        n_ex = _exchange_list_meaningful_open_count(pl)
                        if n_ex > n_eff:
                            n_eff = n_ex
                except Exception as e:
                    log.debug("BUY guard: exchange position count failed: %s", e)
            if max_open_cap is not None and n_eff >= max_open_cap:
                max_open = max_open_cap
                sync_hint = ""
                if n_eff > n_ui:
                    sync_hint = (
                        " — exchange shows %s open vs %s in table (sync or close on exchange)"
                        % (n_eff, n_ui)
                    )
                log.warning(
                    "BUY blocked: open positions effective=%s (ui=%s) >= bot_max_open_trades=%s%s",
                    n_eff,
                    n_ui,
                    max_open,
                    sync_hint,
                )
                if hasattr(self, "bot_status_label") and self.bot_status_label:
                    hint_ar = (
                        f" — المنصة: {n_eff}، الجدول: {n_ui} (حدّث أو أغلق من المنصة)"
                        if n_eff > n_ui
                        else ""
                    )
                    self.bot_status_label.setText(
                        f"انتظار — الحد الأقصى للصفقات ({n_eff}/{max_open}){hint_ar}"
                    )
                self._reenable_trade_buttons()
                skip_extra = (
                    f" — exchange count {n_eff} vs table {n_ui}; refresh positions or close on exchange"
                    if n_eff > n_ui
                    else ""
                )
                self._bot_skip(
                    f"Waiting — max open trades reached ({n_eff}/{max_open}){skip_extra}"
                )
                return
            sym_n = self._count_open_rows_matching_symbol(order_symbol)
            if max_per_sym > 0:
                if sym_n >= max_per_sym:
                    log.warning(
                        "BUY blocked: rows for symbol=%s count=%s >= max_trades_per_symbol=%s",
                        order_symbol,
                        sym_n,
                        max_per_sym,
                    )
                    if hasattr(self, "bot_status_label") and self.bot_status_label:
                        self.bot_status_label.setText(
                            f"انتظار — حد الصفقات للرمز ({sym_n}/{max_per_sym})"
                        )
                    self._reenable_trade_buttons()
                    self._bot_skip(
                        f"Waiting — max trades per symbol reached ({sym_n}/{max_per_sym})"
                    )
                    return
            ok_exp, msg_exp = self._portfolio_exposure_allows_buy(cfg, float(last_price or 0.0))
            if not ok_exp:
                if hasattr(self, "bot_status_label") and self.bot_status_label:
                    self.bot_status_label.setText(msg_exp[:80])
                self._reenable_trade_buttons()
                self._bot_skip(msg_exp)
                return
            # eToro فيوتشرز: قفل قبل بدء الخيط — يمنع شراءً ثانياً حتى تهدأ المنصة/API (غالباً positions=0 لثوانٍ).
            # المدة: max(18s، فاصل نفس الرمز) أو 45s إذا الفاصل معطّل — حتى لا يُلغى المعنى بمسح مبكر للقفل.
            if exchange == "etoro" and use_futures:
                try:
                    sk = str(self.current_symbol or order_symbol or "").strip().upper()
                    if sk:
                        gap_sec = float(self._same_symbol_buy_interval_sec(cfg) or 0.0)
                        lock_sec = max(18.0, gap_sec if gap_sec > 0 else 45.0)
                        until = time.time() + lock_sec
                        prev = float(self._etoro_pending_symbol_until.get(sk, 0.0) or 0.0)
                        self._etoro_pending_symbol_until[sk] = max(prev, until)
                except Exception:
                    pass
        # الكمية:
        # - عند SELL: نبيع كمية المركز المفتوح فعلياً (حتى لا يحدث بيع جزئي/PNL غير مفهوم)
        # - عند BUY: نحسبها من المبلغ والسعر (ومع الرافعة إن كانت Futures)
        quantity = 0.0
        if quantity_override is not None:
            # عند بيع صفقة محددة: احترم الكمية المحددة ولا تستبدلها بالكمية المجمّعة للرمز
            try:
                quantity = float(quantity_override)
            except Exception:
                quantity = 0.0
        elif str(side).upper() == "SELL" and self._positions_panel:
            # عند بيع "الرمز" (بدون تحديد صفقة): نبيع كمية المركز المفتوح فعلياً
            try:
                pos = self._positions_panel.get_position_for_symbol(order_symbol)
                if pos and float(pos.get("quantity", 0) or 0) > 0:
                    quantity = float(pos["quantity"])
            except Exception:
                quantity = 0.0
        elif str(side).upper() == "SELL":
            quantity = 0.0
        if quantity <= 0:
            # خطأ خطير كان يحدث هنا: عند SELL من دون مركز في الجدول كان الكود يحسب الكمية كشراء (مبلغ/سعر)
            # فيُنفَّذ «بيع» بكمية كاملة ويُسجَّل في السجل رغم عدم وجود صفقة مفتوحة.
            if str(side).upper() == "SELL":
                log.warning(
                    "SELL blocked: no open quantity for symbol=%s (panel missing row or qty=0, override=%s)",
                    order_symbol,
                    quantity_override,
                )
                self._reenable_trade_buttons()
                try:
                    self._bot_skip("SELL blocked — no open position quantity")
                except Exception:
                    pass
                self._notify_error_popup(
                    title=tr("trading_sell_no_position_title"),
                    text=tr("trading_sell_no_position_body"),
                    msec=10000,
                )
                return
            if use_futures:
                quantity = round((amount_usdt * leverage) / last_price, 8)
            else:
                quantity = round(amount_usdt / last_price, 8)
        if quantity <= 0:
            self._reenable_trade_buttons()
            self._notify_error_popup(title="الكمية", text="الكمية المحسوبة غير صالحة.")
            return
        self._pending_order_last_price = last_price
        self._pending_order_side = side
        self._pending_order_quantity = quantity
        self._pending_order_testnet = testnet
        self._pending_order_symbol = order_symbol
        self._pending_order_avg_entry_override = avg_entry_override
        self._pending_order_started_at = time.perf_counter()
        self._pending_order_requested_price = float(last_price or 0)
        self._order_thread = QThread()
        reason = getattr(self, "_pending_order_reason", "") or ""
        self._order_worker = OrderWorker(
            client,
            order_symbol,
            side,
            quantity,
            max_retries=2,
            price=last_price,
            testnet=testnet,
            reason=reason,
            etoro_position_id=etoro_close_pid
            if side == "SELL" and exchange == "etoro" and use_futures
            else None,
            etoro_open_order_id=etoro_close_oid
            if side == "SELL" and exchange == "etoro" and use_futures
            else None,
            etoro_instrument_id=(
                int(order_symbol.split("_", 1)[1])
                if side == "SELL"
                and exchange == "etoro"
                and use_futures
                and isinstance(order_symbol, str)
                and order_symbol.startswith("ETORO_")
                else None
            ),
        )
        self._order_worker._thread = self._order_thread  # ربط الخيط بهذا العامل فقط — لا نوقف خيط أمر آخر
        self._order_worker.moveToThread(self._order_thread)
        self._order_thread.started.connect(self._order_worker.run)
        self._order_worker.finished.connect(
            self._on_real_order_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        if side == "BUY":
            try:
                self._pending_buy_order_count = max(
                    0, int(getattr(self, "_pending_buy_order_count", 0) or 0) + 1
                )
            except Exception:
                self._pending_buy_order_count = 1
        self._order_thread.start()

        # حماية من التعليق: إذا لم تصل نتيجة الأمر خلال 25 ثانية نعرض خطأ ونفك التعليق
        try:
            if getattr(self, "_order_timeout_timer", None) is None:
                self._order_timeout_timer = QTimer(self)
                self._order_timeout_timer.setSingleShot(True)
                def _on_timeout():
                    if not getattr(self, "_order_in_progress", False):
                        return
                    w = getattr(self, "_order_worker", None)
                    th = getattr(self, "_order_thread", None)
                    # لا نترك الخيط «قيد التشغيل» وإلا يبقى _execution_busy_for_orders() = True
                    # فيظهر البوت دائماً «انتظار — جاري تنفيذ أمر» حتى بعد انتهاء المهلة.
                    try:
                        if w is not None:
                            w.finished.disconnect()
                    except (TypeError, RuntimeError):
                        pass
                    try:
                        if th is not None:
                            th.quit()
                            th.wait(3000)
                    except Exception:
                        pass
                    self._order_thread = None
                    self._order_worker = None
                    if str(side).upper() == "BUY":
                        try:
                            self._pending_buy_order_count = max(
                                0,
                                int(getattr(self, "_pending_buy_order_count", 0) or 0) - 1,
                            )
                        except Exception:
                            self._pending_buy_order_count = 0
                    self._stop_order_timeout_timer()
                    self._reenable_trade_buttons()
                    self._notify_error_popup(
                        title="انتهت المهلة",
                        text=f"لم يصل رد من المنصة على أمر {side} للرمز {order_symbol}. قد يكون الاتصال بطيئاً أو فشل الطلب.\n\nتحقق من سجل الأوامر/المنصة.",
                        msec=15000,
                    )
                self._order_timeout_timer.timeout.connect(_on_timeout)
            self._stop_order_timeout_timer()
            self._order_timeout_timer.start(25000)
        except Exception:
            pass

    def _start_etoro_resolve_position_async(
        self, order_id: int, symbol: str, entry: float, qty: float, testnet: bool
    ):
        """جلب positionID من orderID في خيط منفصل حتى لا يتجمّد زر الشراء."""
        try:
            oid = int(order_id)
        except (TypeError, ValueError):
            return
        if oid <= 0:
            return
        api_key, api_secret = load_etoro_settings()
        if not (api_key and api_secret):
            return
        prev = getattr(self, "_etoro_resolve_worker", None)
        if prev is not None and hasattr(prev, "cancel"):
            try:
                prev.cancel()
            except Exception:
                pass
        th = QThread()
        w = EtoroResolvePositionWorker(api_key, api_secret, testnet, oid)
        w.moveToThread(th)
        self._etoro_resolve_worker = w

        def _on_pid(pid: int, _w=w, _th=th, _oid=oid):
            if getattr(self, "_etoro_resolve_worker", None) is not _w:
                try:
                    _th.quit()
                except Exception:
                    pass
                return
            try:
                self._apply_etoro_resolved_position_id(
                    symbol, float(entry), float(qty), int(pid or 0), testnet, _oid
                )
            except Exception as e:
                log.exception("eToro resolve position callback: %s", e)
            try:
                _th.quit()
            except Exception:
                pass

        th.started.connect(w.run)
        w.finished.connect(_on_pid, Qt.ConnectionType.QueuedConnection)
        th.finished.connect(th.deleteLater)
        w.finished.connect(w.deleteLater)
        self._etoro_resolve_thread = th
        th.start()

    def _apply_etoro_resolved_position_id(
        self,
        symbol: str,
        entry: float,
        qty: float,
        position_id: int,
        testnet: bool,
        etoro_open_order_id: int | None = None,
    ):
        if position_id > 0 and self._positions_panel and hasattr(
            self._positions_panel, "apply_position_id_after_buy"
        ):
            if self._positions_panel.apply_position_id_after_buy(
                symbol, entry, qty, position_id, etoro_open_order_id=etoro_open_order_id
            ):
                log.info("eToro: رُبط position_id=%s بالصف %s (خلفية)", position_id, symbol)
                return
        if position_id <= 0:
            cfg = load_config()
            if (cfg.get("exchange") or "").lower() == "etoro" and _config_use_futures(cfg):
                QTimer.singleShot(800, self._sync_open_positions_from_exchange)

    def _build_execution_context_for_report(
        self, *, reason: str, side: str, include_last_bot_ui_text: bool = False
    ) -> dict | None:
        """لقطة مؤشرات/سياق عند التنفيذ — تُحفظ مع تقرير التنفيذ وتُعرض من سجل الأخطاء."""
        ind = getattr(self, "_pending_indicators", None)
        info = getattr(self, "_pending_market_info", None)
        if not isinstance(ind, dict):
            ind = {}
        if not isinstance(info, dict):
            info = {}
        conf = getattr(self, "_pending_order_confidence", None)
        if not ind and not info and conf is None:
            return None
        comp_score = None
        comp_label = None
        ctx_comp_reasons = None
        try:
            from composite_signal import compute_composite_signal

            comp = compute_composite_signal(
                ind,
                info,
                lang_ar=(get_language() == "ar"),
            )
            if isinstance(comp, dict):
                comp_score = float(comp.get("score", 0.0) or 0.0)
                if get_language() == "ar":
                    comp_label = str(comp.get("label_ar", "") or "").strip() or None
                    comp_reasons = comp.get("reasons_ar")
                else:
                    comp_label = str(comp.get("label_en", "") or "").strip() or None
                    comp_reasons = comp.get("reasons_en")
                if isinstance(comp_reasons, (list, tuple)):
                    ctx_comp_reasons = [str(x) for x in comp_reasons[:12] if x]
        except Exception:
            pass
        ctx = {
            "symbol": str(getattr(self, "current_symbol", "") or "").upper(),
            "chart_interval": str(getattr(self, "_chart_interval", "") or ""),
            "side": str(side or "").upper(),
            "reason": str(reason or "").strip(),
            "confidence_at_execute_pct": float(conf) if conf is not None else None,
            "composite_score": comp_score,
            "composite_label": comp_label,
            "composite_reason_lines": ctx_comp_reasons,
            "indicators": ind,
            "market_info": info,
        }
        # نص حالة البوت في الواجهة يعكس غالباً آخر تخطٍ/انتظار — نُرفقه فقط عند فشل التنفيذ لمساعدة التشخيص
        if include_last_bot_ui_text:
            details = (getattr(self, "_last_bot_decision_details", None) or "").strip()
            if details:
                ctx["last_bot_ui_details_snapshot"] = details[:12000]
        return sanitize_for_execution_json(ctx)

    def _append_execution_report(
        self,
        *,
        ok: bool,
        msg: str,
        side: str,
        symbol: str,
        requested_price: float,
        executed_price: float,
        quantity: float,
        testnet: bool,
        reason: str,
        latency_ms: float | None,
        pnl: float | None = None,
        execution_context: dict | None = None,
    ) -> None:
        req = float(requested_price or 0)
        exe = float(executed_price or 0)
        slip_abs = None
        slip_pct = None
        if req > 0 and exe > 0:
            if str(side).upper() == "BUY":
                slip_abs = exe - req
            else:
                slip_abs = req - exe
            slip_pct = (slip_abs / req) * 100.0
        payload = {
            "symbol": str(symbol or "").upper(),
            "side": str(side or "").upper(),
            "mode": "testnet" if bool(testnet) else "live",
            "ok": bool(ok),
            "reason": str(reason or "").strip(),
            "message": str(msg or "").strip(),
            "requested_price": round(req, 10) if req > 0 else None,
            "executed_price": round(exe, 10) if exe > 0 else None,
            "quantity": float(quantity or 0),
            "latency_ms": round(float(latency_ms), 1) if latency_ms is not None else None,
            "slippage_abs": round(float(slip_abs), 10) if slip_abs is not None else None,
            "slippage_pct": round(float(slip_pct), 6) if slip_pct is not None else None,
            "pnl": round(float(pnl), 4) if pnl is not None else None,
        }
        if isinstance(execution_context, dict) and execution_context:
            payload["execution_context"] = execution_context
        ok_rep, info = append_execution_report(payload)
        if not ok_rep:
            log.warning("Execution report write failed: %s", info)

    @staticmethod
    def _etoro_instrument_missing_message(msg: str) -> bool:
        """فشل قبل إرسال الطلب — لا أداة مطابقة في بحث eToro."""
        s = (msg or "").strip()
        if "لم يتم العثور على الأداة" in s:
            return True
        low = s.lower()
        return "instrument not found" in low or "no instrument" in low

    def _on_real_order_finished(
        self, ok: bool, msg: str, side: str, sold_symbol: str, price: float, qty: float,
        testnet: bool, reason: str, position_id=None, etoro_open_order_id=None,
    ):
        worker = self.sender()
        # بعد انتهاء المهلة نفصل finished ونصفّر العامل — قد تصل إشارة متأخرة؛ لا نكرر السجل/الخصم
        if worker is None or getattr(self, "_order_worker", None) is not worker:
            return
        thread = getattr(worker, "_thread", None) if worker else None
        if thread is not None:
            thread.quit()
            thread.wait(3000)
        self._order_thread = None
        self._order_worker = None
        self._stop_order_timeout_timer()
        if str(side or "").upper() == "BUY":
            try:
                self._pending_buy_order_count = max(
                    0, int(getattr(self, "_pending_buy_order_count", 0) or 0) - 1
                )
            except Exception:
                self._pending_buy_order_count = 0
        self._reenable_trade_buttons()
        sold_symbol_disp = self._display_symbol(sold_symbol or self.current_symbol)
        latency_ms = None
        try:
            started = float(getattr(self, "_pending_order_started_at", 0.0) or 0.0)
            if started > 0:
                latency_ms = max(0.0, (time.perf_counter() - started) * 1000.0)
        except Exception:
            latency_ms = None
        requested_price = float(
            getattr(self, "_pending_order_requested_price", 0.0)
            or getattr(self, "_pending_order_last_price", 0.0)
            or 0.0
        )
        if ok:
            sell_snap_entry = None
            sell_snap_qty = None
            if side == "SELL" and self._positions_panel:
                try:
                    _sp = self._positions_panel.get_position_for_symbol(sold_symbol)
                    if _sp:
                        sell_snap_entry = float(_sp.get("entry_price", 0) or 0) or None
                        sell_snap_qty = float(_sp.get("quantity", 0) or 0) or None
                        if sell_snap_entry is not None and sell_snap_entry <= 0:
                            sell_snap_entry = None
                        if sell_snap_qty is not None and sell_snap_qty <= 0:
                            sell_snap_qty = None
                except Exception:
                    pass
            fifo_lots_snapshot: list[tuple[float, float]] = []
            if side == "SELL" and self._positions_panel:
                try:
                    fifo_lots_snapshot = self._positions_panel.get_fifo_lots_for_symbol(
                        sold_symbol
                    )
                except Exception:
                    fifo_lots_snapshot = []
            avg_entry = None
            try:
                if side == "SELL" and getattr(self, "_pending_order_avg_entry_override", None) is not None:
                    avg_entry = float(getattr(self, "_pending_order_avg_entry_override"))
            except Exception:
                avg_entry = None
            if side == "SELL" and self._positions_panel:
                pid_mark_order = None
                oid_mark_order = None
                try:
                    cfg_sell = load_config()
                    _etoro_sell = (cfg_sell.get("exchange") or "").lower() == "etoro"
                except Exception:
                    _etoro_sell = False
                if _etoro_sell:
                    try:
                        if position_id is not None and int(position_id) > 0:
                            pid_mark_order = int(position_id)
                    except (TypeError, ValueError):
                        pass
                    try:
                        po0 = self._positions_panel.get_position_for_symbol(sold_symbol)
                    except Exception:
                        po0 = None
                    if (pid_mark_order is None or pid_mark_order <= 0) and po0 and po0.get(
                        "position_id"
                    ):
                        try:
                            pid_mark_order = int(po0["position_id"])
                        except (TypeError, ValueError):
                            pass
                    if po0 and po0.get("etoro_open_order_id") is not None:
                        try:
                            oid_mark_order = int(po0["etoro_open_order_id"])
                        except (TypeError, ValueError):
                            pass
                try:
                    if avg_entry is None:
                        pos = self._positions_panel.get_position_for_symbol(sold_symbol)
                        if pos:
                            avg_entry = float(pos.get("entry_price", 0) or 0)
                except Exception:
                    avg_entry = None
                # لا نمرر 0 كمتوسط دخول لأنه ينتج pnl=None إذا فشل FIFO
                try:
                    if avg_entry is not None and float(avg_entry) <= 0:
                        avg_entry = None
                except Exception:
                    avg_entry = None
                # إذا كان البيع لصفقة محددة (avg_entry_override موجود) احذف صفاً واحداً فقط
                if getattr(self, "_pending_order_avg_entry_override", None) is not None:
                    try:
                        ep = float(getattr(self, "_pending_order_avg_entry_override") or 0)
                        self._positions_panel.remove_trade(sold_symbol, ep, float(qty or 0))
                    except Exception:
                        pass
                else:
                    self._positions_panel.remove_positions_for_symbol(sold_symbol)
                if _etoro_sell:
                    try:
                        self._etoro_mark_recent_closed_position(pid_mark_order)
                        self._etoro_mark_recent_closed_order(oid_mark_order)
                    except Exception:
                        pass
                # نفس لوحة المراكز المصغّرة: السجل يُحدَّث هنا لكن العرض كان يعتمد على مؤقت 500ms
                self._update_open_position_display(getattr(self, "_last_price", 0) or 0)
                try:
                    if _etoro_sell and self._count_open_position_rows() == 0:
                        _clear_etoro_positions_cache()
                except Exception:
                    pass
            price = float(price or 0)
            if side == "SELL" and price <= 0:
                price = float(getattr(self, "_pending_order_last_price", 0) or 0)
            if side == "SELL" and price <= 0:
                price = float(self._price_for_closed_symbol(sold_symbol) or 0)
            if side == "SELL" and price <= 0 and (sold_symbol or "").strip().upper() == (
                getattr(self, "current_symbol", None) or ""
            ).strip().upper():
                price = float(getattr(self, "_last_price", 0) or 0)
            qty = float(qty or 0)
            if side == "SELL" and qty <= 0 and sell_snap_qty and sell_snap_qty > 0:
                qty = sell_snap_qty
            mode = "testnet" if testnet else "live"
            reason = (reason or "").strip()
            # ضمان: عند بيع صفقة محددة نستخدم سعر دخولها لحساب الربح دائماً
            if side == "SELL" and avg_entry is None and getattr(self, "_pending_order_avg_entry_override", None) is not None:
                try:
                    avg_entry = float(getattr(self, "_pending_order_avg_entry_override"))
                except Exception:
                    avg_entry = None
            if side == "SELL" and (avg_entry is None or float(avg_entry or 0) <= 0) and sell_snap_entry:
                avg_entry = float(sell_snap_entry)
            if side == "SELL" and (avg_entry is None or float(avg_entry or 0) <= 0):
                _fb = get_last_buy_price_for_symbol(sold_symbol)
                if _fb and _fb > 0:
                    avg_entry = float(_fb)
            hist_sym = self._history_symbol(sold_symbol)
            pnl = None
            side_u = str(side).upper()
            override_avg = getattr(self, "_pending_order_avg_entry_override", None)
            used_multi_fifo = False
            if (
                side_u == "SELL"
                and override_avg is None
                and fifo_lots_snapshot
                and len(fifo_lots_snapshot) > 1
                and float(qty or 0) > 0
            ):
                parts = self._allocate_fifo_sell_parts(fifo_lots_snapshot, float(qty))
                if len(parts) > 1:
                    total_pnl = 0.0
                    for ep_leg, qq in parts:
                        pt = record_trade(
                            hist_sym,
                            "SELL",
                            price,
                            float(qq),
                            mode=mode,
                            reason=reason,
                            avg_buy_price=float(ep_leg),
                            use_fifo=False,
                        )
                        if pt is not None:
                            total_pnl += float(pt)
                    pnl = round(total_pnl, 4)
                    used_multi_fifo = True
                elif len(parts) == 1:
                    ep0, qq0 = parts[0]
                    pnl = record_trade(
                        hist_sym,
                        "SELL",
                        price,
                        float(qq0),
                        mode=mode,
                        reason=reason,
                        avg_buy_price=float(ep0),
                        use_fifo=False,
                    )
                    used_multi_fifo = True
            et_pid = None
            et_oid = None
            if side_u == "BUY":
                try:
                    if position_id is not None and int(position_id) > 0:
                        et_pid = int(position_id)
                except (TypeError, ValueError):
                    et_pid = None
                try:
                    if etoro_open_order_id is not None and int(etoro_open_order_id) > 0:
                        et_oid = int(etoro_open_order_id)
                except (TypeError, ValueError):
                    et_oid = None
            if not used_multi_fifo:
                pnl = record_trade(
                    sold_symbol,
                    side,
                    price,
                    qty,
                    mode=mode,
                    reason=reason,
                    avg_buy_price=avg_entry,
                    use_fifo=False
                    if (
                        side_u == "SELL"
                        and avg_entry is not None
                        and float(avg_entry or 0) > 0
                    )
                    else True,
                    etoro_position_id=et_pid,
                    etoro_open_order_id=et_oid,
                )
            if side == "SELL" and pnl is None and price > 0 and qty > 0:
                _ae = None
                try:
                    if avg_entry is not None and float(avg_entry) > 0:
                        _ae = float(avg_entry)
                except (TypeError, ValueError):
                    pass
                if _ae is None and sell_snap_entry and sell_snap_entry > 0:
                    _ae = sell_snap_entry
                if _ae is None:
                    _ae2 = get_last_buy_price_for_symbol(sold_symbol)
                    if _ae2 and _ae2 > 0:
                        _ae = float(_ae2)
                if _ae and _ae > 0:
                    pnl = round((price - _ae) * qty, 4)
                    patch_last_sell_pnl(
                        sold_symbol,
                        pnl,
                        avg_buy_price=_ae,
                        exit_price=price,
                        quantity=qty,
                    )
            # كمية البيع من المنصة قد تكون 0 في الواجهة — استخدم آخر شراء من السجل
            if side == "SELL" and pnl is None and price > 0:
                _bp, _bq = get_last_buy_info_for_symbol(sold_symbol)
                if _bp is not None and float(_bp) > 0:
                    qx = float(qty) if qty and float(qty) > 0 else 0.0
                    if qx <= 0 and _bq is not None and float(_bq) > 0:
                        qx = float(_bq)
                    if qx > 0:
                        pnl = round((float(price) - float(_bp)) * qx, 4)
                        patch_last_sell_pnl(
                            sold_symbol,
                            pnl,
                            avg_buy_price=float(_bp),
                            exit_price=float(price),
                            quantity=qx,
                        )
            try:
                self.history_refresh_requested.emit()
            except Exception:
                pass
            try:
                exec_ctx = self._build_execution_context_for_report(
                    reason=reason, side=side, include_last_bot_ui_text=False
                )
                self._append_execution_report(
                    ok=True,
                    msg=msg,
                    side=side,
                    symbol=sold_symbol or self.current_symbol,
                    requested_price=requested_price,
                    executed_price=price,
                    quantity=qty,
                    testnet=testnet,
                    reason=reason,
                    latency_ms=latency_ms,
                    pnl=float(pnl) if pnl is not None else None,
                    execution_context=exec_ctx,
                )
            except Exception:
                log.warning("Execution report emit failed", exc_info=True)
            if hasattr(self, "last_trade_label") and self.last_trade_label:
                if side == "SELL" and pnl is not None:
                    self._last_realized_pnl = float(pnl)
                    pnl_f = self._last_realized_pnl
                    self.last_trade_label.setText(f"{pnl_f:+.2f}")
                    self.last_trade_label.setStyleSheet(
                        f"color: {UI_GREEN}; font-weight: bold; font-size: 12px;" if pnl_f >= 0 else f"color: {UI_RED}; font-weight: bold; font-size: 12px;"
                    )
                elif str(side).upper() == "BUY":
                    # شراء جديد لا يعني «لا آخر صفقة» — أبقِ آخر بيع مغلق من السجل كما بعد إعادة التشغيل.
                    self._refresh_last_trade_display()
                else:
                    self.last_trade_label.setText("—")
                    self.last_trade_label.setStyleSheet("color: #aaa; font-weight: bold; font-size: 12px;")
            self._notify_trade_popup(
                side=side,
                symbol=sold_symbol_disp,
                price=price,
                qty=qty,
                mode=mode,
                reason=reason,
                pnl=pnl,
            )

            # رسالة واضحة في سطر حالة البوت (تظهر دائماً حتى لو لم تنتبه للنافذة)
            try:
                side_ar_short = "شراء" if side == "BUY" else "بيع"
                pnl_txt = ""
                if side == "SELL":
                    if pnl is None:
                        pnl_txt = " | ربح/خسارة: —"
                    else:
                        pnl_txt = f" | ربح/خسارة: {float(pnl):+.2f} USDT"
                r_txt = f" | السبب: {reason}" if reason else ""
                status = f"تم {side_ar_short} {sold_symbol_disp} بنجاح{pnl_txt}{r_txt}"
                self._flash_bot_status(status, seconds=12)
            except Exception:
                pass
            conf = getattr(self, "_pending_order_confidence", None)
            ind = getattr(self, "_pending_indicators", None) or {}
            info = getattr(self, "_pending_market_info", None) or {}
            # تسجيل أداء البوت للتعلّم
            if conf is not None:
                if side == "BUY":
                    record_bot_buy(self.current_symbol, price, qty, conf, indicators=ind, market_info=info)
                    try:
                        sym_b = str(self.current_symbol or sold_symbol or "").strip().upper()
                        if sym_b:
                            self._bot_last_buy_ts_by_symbol[sym_b] = time.time()
                    except Exception:
                        pass
                elif side == "SELL" and pnl is not None:
                    record_bot_sell_outcome(
                        self.current_symbol, price, pnl, quantity_sold=qty
                    )
                    if apply_learning_step():
                        cfg = load_config()
                        self._BOT_CONFIDENCE_MIN = float(cfg.get("bot_confidence_min", 60))
                self._pending_order_confidence = None
            # إشعار تيليجرام بالصفقة (شراء أو بيع)
            try:
                send_trade_notification(
                    side=side,
                    symbol=sold_symbol_disp,
                    price=price,
                    qty=qty,
                    mode=mode,
                    pnl=pnl,
                    is_bot=(conf is not None),
                    confidence=conf,
                )
            except Exception:
                log.warning("Failed to schedule Telegram trade notification", exc_info=True)
            # تحديث الرصيد بعد الأمر حسب الوضع الحالي (حقيقي/وهمي)
            QTimer.singleShot(800, lambda: self._emit_balance_for_status_bar(testnet=(not self.is_real_mode())))
            if side == "BUY":
                self._position_peak_price = max(self._position_peak_price or price, price)
                try:
                    cfg_gr = load_config()
                    if (cfg_gr.get("exchange") or "").lower() == "etoro":
                        # أي شراء eToro: مهلة طويلة — API /pnl غالباً فارغ بعد الثواني الأولى
                        self._etoro_buy_grace_until = time.time() + 10.0
                        # لا نفعّل وقف خسارة / حد بيع تلقائي من سعر الشارت قبل استقرار المركز (10 ثوانٍ)
                        self._etoro_min_hold_until = time.time() + 10.0
                        try:
                            sym_key = str(self.current_symbol or sold_symbol or "").upper()
                            if sym_key:
                                # لا نُقصّر القفل الضارب في _execute_real_order (18–45s+) إلى 10s فقط
                                _grace = time.time() + 10.0
                                prev_u = float(
                                    self._etoro_pending_symbol_until.get(sym_key, 0.0) or 0.0
                                )
                                self._etoro_pending_symbol_until[sym_key] = max(prev_u, _grace)
                        except Exception:
                            pass
                except Exception:
                    pass
                try:
                    pid_emit = None
                    if position_id is not None:
                        pid_emit = int(position_id)
                except (TypeError, ValueError):
                    pid_emit = None
                try:
                    oid_emit = None
                    if etoro_open_order_id is not None:
                        oid_emit = int(etoro_open_order_id)
                except (TypeError, ValueError):
                    oid_emit = None
                try:
                    self.new_position.emit(
                        self.current_symbol, float(price), float(qty), pid_emit, oid_emit
                    )
                except Exception as e:
                    log.exception("تحديث جدول المراكز بعد الشراء: %s", e)
                cfg = load_config()
                if (cfg.get("exchange") or "").lower() == "etoro" and _config_use_futures(cfg):
                    oid = None
                    try:
                        if etoro_open_order_id is not None:
                            oid = int(etoro_open_order_id)
                    except (TypeError, ValueError):
                        oid = None
                    if oid and oid > 0 and not position_id:
                        self._start_etoro_resolve_position_async(
                            oid, self.current_symbol, float(price), float(qty), testnet
                        )
                    elif not position_id:
                        # مزامنات متدرجة — eToro يتأخر أحياناً في إظهار الصفقة الثانية في /pnl
                        QTimer.singleShot(1500, self._sync_open_positions_from_exchange)
                        QTimer.singleShot(4500, self._sync_open_positions_from_exchange)
                        QTimer.singleShot(12000, self._sync_open_positions_from_exchange)
            elif side == "SELL":
                self._position_peak_price = None
                self._last_bought_support = None  # بعد البيع نسمح بالشراء من أي دعم مرة أخرى لاحقاً
                # لا نحذف صفاً بالاعتماد على current_symbol هنا لأن الأمر قد يكون لرمز مختلف
                # (نحذف المراكز الخاصة بالرمز المبيع أعلاه عبر remove_positions_for_symbol).
                try:
                    cfg_s = load_config()
                    if (cfg_s.get("exchange") or "").lower() == "etoro" and _config_use_futures(
                        cfg_s
                    ):
                        for _k in (
                            str(sold_symbol or "").strip().upper(),
                            str(self.current_symbol or "").strip().upper(),
                        ):
                            if _k:
                                self._etoro_pending_symbol_until.pop(_k, None)
                except Exception:
                    pass
            self._pending_order_avg_entry_override = None
        else:
            _was_bot_order_attempt = getattr(self, "_pending_order_confidence", None) is not None
            if _was_bot_order_attempt:
                self._pending_order_confidence = None
            # فشل شراء: أزل طابع الفاصل لنفس الرمز حتى لا يُمنع إعادة المحاولة دون داعٍ
            if side == "BUY":
                try:
                    sym_fail = str(sold_symbol or self.current_symbol or "").strip().upper()
                    if sym_fail and hasattr(self, "_bot_last_buy_ts_by_symbol") and self._bot_last_buy_ts_by_symbol:
                        self._bot_last_buy_ts_by_symbol.pop(sym_fail, None)
                except Exception:
                    pass
            # اجعل الفشل واضحاً في INFO + إشعار منبثق
            log.info("Order failed: %s", msg)
            log.warning("Order failed: %s", msg)
            m = (msg or "").strip()
            _etoro_no_instr = self._etoro_instrument_missing_message(m)
            # لم يُرسل طلب للمنصة — لا داعي لفترة تهدئة البوت (كانت تُضبط قبل التنفيذ)
            if _etoro_no_instr:
                self._bot_cooldown_until = 0.0
                try:
                    sym_ul = str(sold_symbol or self.current_symbol or "").strip().upper()
                    if sym_ul:
                        self._bot_etoro_unlisted_symbol = sym_ul
                except Exception:
                    pass
                log.info(
                    "eToro: instrument not found for %s — bot BUY skipped until you change symbol (no order was sent)",
                    str(sold_symbol or self.current_symbol or "").strip().upper() or "?",
                )
            # إن فشل شراء eToro لا نُبقي قفل الرمز
            try:
                cfg_fail = load_config()
                if side == "BUY" and (cfg_fail.get("exchange") or "").lower() == "etoro":
                    sym_key = str(sold_symbol or self.current_symbol or "").upper()
                    if sym_key:
                        self._etoro_pending_symbol_until.pop(sym_key, None)
            except Exception:
                pass
            if "لا يوجد مركز" in m or "no open position" in m.lower() or "لا توجد صفقة" in m:
                self._on_exchange_reports_no_position(sold_symbol or self.current_symbol)
                # إشعار واضح حتى لا يبدو أن الصفقة "اختفت" أو الأمر "تعليق"
                try:
                    self._notify_error_popup(
                        title="فشل التنفيذ — لا يوجد مركز",
                        text=f"لم يتم تنفيذ الأمر لأن المنصة تقول أنه لا يوجد مركز مفتوح للرمز: {sold_symbol_disp}\n\nتفاصيل المنصة:\n{m}",
                    )
                except Exception:
                    pass
            elif _etoro_no_instr:
                # ليس «خطأ منصة» — الزوج غير مدرج على eToro؛ لا نافذة حرجة ولا منبثق عند البوت (يُكفي السجل + سطر حالة البوت)
                if not _was_bot_order_attempt:
                    try:
                        self._notify_error_popup(
                            title=tr("etoro_pair_not_listed_title"),
                            text=tr("etoro_pair_not_listed_body").format(
                                symbol=self._display_symbol(sold_symbol or self.current_symbol)
                            ),
                            msec=9000,
                        )
                    except Exception:
                        pass
            else:
                friendly = self._localize_exchange_error(msg)
                try:
                    self._notify_error_popup(
                        title="فشل تنفيذ الأمر",
                        text=str(friendly or msg or "Order failed"),
                    )
                except Exception:
                    pass
                QMessageBox.critical(self, "تنبيه — خطأ في الأمر", friendly)
            self._pending_order_avg_entry_override = None
            try:
                exec_ctx = self._build_execution_context_for_report(
                    reason=(reason or "").strip(),
                    side=side,
                    include_last_bot_ui_text=True,
                )
                self._append_execution_report(
                    ok=False,
                    msg=msg,
                    side=side,
                    symbol=sold_symbol or self.current_symbol,
                    requested_price=requested_price,
                    executed_price=float(price or 0),
                    quantity=float(qty or 0),
                    testnet=testnet,
                    reason=(reason or "").strip(),
                    latency_ms=latency_ms,
                    pnl=None,
                    execution_context=exec_ctx,
                )
            except Exception:
                log.warning("Execution report emit failed", exc_info=True)

    def _notify_trade_popup(self, *, side: str, symbol: str, price: float, qty: float, mode: str, reason: str, pnl: float | None):
        """إشعار منبثق + صوت عند الشراء/البيع (بوت أو يدوي)."""
        # صوت واضح على ويندوز (أفضل من QApplication.beep التي قد تكون صامتة)
        try:
            import winsound  # type: ignore
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            try:
                QApplication.beep()
            except Exception:
                pass
        side_u = (side or "").strip().upper()
        symbol_disp = self._display_symbol(symbol)
        side_ar = tr("trading_side_buy") if side_u == "BUY" else tr("trading_side_sell")
        value_usdt = float(price or 0) * float(qty or 0)
        txt = (
            f"تم تنفيذ {side_ar} بنجاح.\n\n"
            f"نوع الصفقة: {side_ar}\n"
            f"العملة/الرمز: {symbol_disp}\n"
            f"السعر: {format_price(price)}\n"
            f"الكمية: {float(qty):.6f}\n"
            f"قيمة الصفقة: {value_usdt:.2f} USDT\n"
            + (f"السبب: {reason}\n" if reason else "")
            + f"الوضع: {'وهمي (Testnet)' if mode == 'testnet' else 'حقيقي'}\n\n"
            "تم تسجيل الصفقة في «سجل الصفقات»."
        )
        if pnl is not None and side_u == "SELL":
            txt += "\n" + tr("trading_msg_pnl").format(pnl=pnl)
        title = f"تم {side_ar} — {symbol_disp}"
        _msg = QMessageBox(self)
        _msg.setWindowTitle(title)
        _msg.setText(txt)
        _msg.setIcon(QMessageBox.Icon.Information)
        _msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        _msg.setModal(False)
        try:
            # اجعلها تظهر فوق الواجهة حتى لا "تضيع" خلف النوافذ
            _msg.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        except Exception:
            pass
        _msg.show()
        try:
            _msg.raise_()
            _msg.activateWindow()
        except Exception:
            pass
        close_ms = 22000 if side_u == "SELL" else 12000
        QTimer.singleShot(close_ms, _msg.close)

    def _display_symbol(self, symbol: str) -> str:
        """تحويل رمز eToro التقني (ETORO_100000) إلى رمز العرض الحالي للمستخدم."""
        s = str(symbol or "").strip().upper()
        if not s:
            return s
        if s.startswith("ETORO_"):
            cur = str(getattr(self, "current_symbol", "") or "").strip().upper()
            if cur and not cur.startswith("ETORO_"):
                return cur
        return s

    @staticmethod
    def _allocate_fifo_sell_parts(
        lots: list[tuple[float, float]], sell_qty: float
    ) -> list[tuple[float, float]]:
        """توزيع كمية البيع على اللوتات بترتيب FIFO (جزئي مسموح على آخر لوت مستهلك)."""
        rem = float(sell_qty)
        out: list[tuple[float, float]] = []
        for ep, q in lots:
            if rem <= 1e-12:
                break
            try:
                qq = float(q)
                epp = float(ep)
            except (TypeError, ValueError):
                continue
            if qq <= 0:
                continue
            take = min(rem, qq)
            if take > 1e-12:
                out.append((epp, take))
                rem -= take
        return out

    def _history_symbol(self, symbol: str) -> str:
        """رمز موحّد لسجل الصفقات — يطابق صفقة الشراء (مثل BTCUSDT) وليس ETORO_*."""
        h = self._display_symbol(symbol)
        return h if h else str(symbol or "").strip()

    def _trade_history_symbol(self, raw_symbol: str) -> str:
        """رمز السجل بعد التوحيد (للمزامنة مع trade_history.json)."""
        from trade_history import _norm_hist_symbol

        return _norm_hist_symbol(self._history_symbol(raw_symbol or ""))

    def _notify_error_popup(self, *, title: str, text: str, msec: int = 12000):
        """إشعار منبثق + صوت عند فشل الأمر (حتى لو السجل على INFO فقط)."""
        try:
            import winsound  # type: ignore
            winsound.MessageBeep(winsound.MB_ICONHAND)
        except Exception:
            try:
                QApplication.beep()
            except Exception:
                pass
        box = QMessageBox(self)
        box.setWindowTitle(title or "خطأ")
        box.setText(text or "")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.setModal(False)
        try:
            box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        except Exception:
            pass
        box.show()
        try:
            box.raise_()
            box.activateWindow()
        except Exception:
            pass
        QTimer.singleShot(max(1000, int(msec)), box.close)

    def _restore_bot_status_label_idle(self) -> None:
        """إعادة سطر حالة البوت بعد انتهاء رسالة مؤقتة (وميض) — نفس نص تفعيل/إيقاف الزر."""
        if not hasattr(self, "bot_status_label") or not self.bot_status_label:
            return
        if self._execution_busy_for_orders():
            return
        try:
            if self._bot_enabled:
                if get_language() == "ar":
                    self.bot_status_label.setText("البوت يعمل — بانتظار إشارة مناسبة")
                else:
                    self.bot_status_label.setText("Bot ON — waiting for a signal")
            else:
                if get_language() == "ar":
                    self.bot_status_label.setText("البوت متوقف — شغِّل البوت من زر ON/OFF")
                else:
                    self.bot_status_label.setText("Bot is OFF — enable it from the ON/OFF button")
        except Exception:
            pass

    def _on_bot_status_flash_restore(self) -> None:
        """بعد انتهاء مؤقت الوميض: تحديث شريط الحالة وإعادة نص البوت الافتراضي."""
        self._emit_status_message()
        self._restore_bot_status_label_idle()

    def _flash_bot_status(self, text: str, *, seconds: int = 10):
        """إظهار رسالة مؤقتة في سطر حالة البوت ثم الرجوع للوضع الطبيعي."""
        if not hasattr(self, "bot_status_label") or not self.bot_status_label:
            return
        try:
            self.bot_status_label.setText(str(text or ""))
        except Exception:
            return
        try:
            if getattr(self, "_bot_status_restore_timer", None) is None:
                self._bot_status_restore_timer = QTimer(self)
                self._bot_status_restore_timer.setSingleShot(True)
                self._bot_status_restore_timer.timeout.connect(self._on_bot_status_flash_restore)
            self._bot_status_restore_timer.stop()
            self._bot_status_restore_timer.start(max(1000, int(seconds) * 1000))
        except Exception:
            pass

    def _set_trailing_diag(self, text: str, *, min_interval_sec: float = 2.0):
        """تشخيص التتبع في السجل فقط — لا يُعرَض على سطر حالة البوت (التنفيذ يبقى صامتاً في الواجهة)."""
        msg = str(text or "").strip()
        if not msg:
            return
        now = time.time()
        last_msg = str(getattr(self, "_trailing_diag_last_msg", "") or "")
        last_ts = float(getattr(self, "_trailing_diag_last_ts", 0.0) or 0.0)
        if msg == last_msg and (now - last_ts) < float(min_interval_sec):
            return
        self._trailing_diag_last_msg = msg
        self._trailing_diag_last_ts = now
        log.debug("%s", msg)

    def _localize_exchange_error(self, msg: str) -> str:
        """
        ترجمة مختصرة لبعض أخطاء المنصة الشائعة (مع إظهار النص الأصلي أيضاً).
        """
        try:
            text = str(msg or "").strip()
        except Exception:
            text = str(msg)
        lower = text.lower()
        ar_expl = None
        if "etoro" in lower and ("403" in text or "insufficientpermissions" in lower) and "permission" in lower:
            ar_expl = "صلاحيات مفتاح eToro غير كافية. أضف صلاحيات Read و Trade في eToro: الإعدادات > Trading > API Key Management، ثم احفظ المفتاح من جديد."
        elif (
            "insufficient balance" in lower
            or "insufficient funds" in lower
            or "not enough balance" in lower
            or "balance is insufficient" in lower
            or "margin is insufficient" in lower
            or "insufficient margin" in lower
            or "-2010" in text  # Binance: Account has insufficient balance
        ):
            ar_expl = "لا يوجد رصيد كافٍ في الحساب (أو هامش كافٍ) لتنفيذ هذا الأمر — المبلغ/الكمية أكبر من المتاح."
        elif "min_notional" in lower:
            ar_expl = "قيمة الصفقة أصغر من الحد الأدنى المسموح به لهذه العملة على المنصة (MIN_NOTIONAL). جرّب زيادة المبلغ أو الكمية."
        elif "lot_size" in lower:
            ar_expl = "الكمية لا تطابق متطلبات حجم العقد (LOT_SIZE) لهذه العملة. عدّل عدد الأرقام العشرية أو غيّر الكمية قليلاً."

        if ar_expl:
            if get_language() == "ar":
                return f"{ar_expl}\n\nتفاصيل المنصة:\n{text}"
            return f"{text}\n\nHint: {ar_expl}"
        return text

    def close_all_action(self):
        """إغلاق كل المراكز. هذا هو المسار الوحيد لبيع/إغلاق الكل (مع تأكيد)."""
        if self._execution_busy_for_orders():
            QMessageBox.information(
                self,
                tr("trading_robot_title"),
                "يُنفَّذ أمر سابق. انتظر انتهاء التنفيذ ثم جرّب مرة أخرى." if get_language() == "ar" else "An order is already in progress. Wait for it to finish, then try again.",
            )
            return

        mb = QMessageBox(self)
        mb.setWindowTitle("تأكيد" if get_language() == "ar" else "Confirm")
        mb.setText(
            (
                "هل تريد بيع/إغلاق كل المراكز الظاهرة في الجدول على المنصة دفعة واحدة؟"
                if get_language() == "ar"
                else "Sell/close ALL positions shown in the table on the exchange, in one batch?"
            )
        )
        mb.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        mb.setDefaultButton(QMessageBox.StandardButton.No)
        if mb.exec() != QMessageBox.StandardButton.Yes:
            return

        cfg = load_config()
        use_futures = _config_use_futures(cfg)

        # Spot: بيع فعلي على المنصة لكل صف في جدول المراكز (Binance / eToro)؛ Bitget غير مفعّل.
        if not use_futures:
            testnet = not self.is_real_mode()
            exchange = (cfg.get("exchange") or "binance").lower()
            if exchange == "bitget":
                self._notify_error_popup(
                    title="Bitget",
                    text=(
                        "إغلاق الكل على Spot غير مفعّل لـ Bitget في هذا الإصدار."
                        if get_language() == "ar"
                        else "Close-all on Bitget Spot is not enabled in this build."
                    ),
                )
                return
            if exchange == "etoro":
                api_key, api_secret = load_etoro_settings()
            else:
                api_key, api_secret = get_decrypted_credentials(self, testnet=testnet)
            if not (api_key and api_secret):
                self._notify_error_popup(
                    title="API Settings",
                    text="يرجى إدخال المفاتيح من إعدادات API (أو مفاتيح eToro عند اختيار eToro).",
                )
                return
            try:
                if exchange == "etoro":
                    client = EtoroSpotClient(api_key, api_secret, testnet=testnet)
                else:
                    client = BinanceSpotClient(api_key, api_secret, testnet=testnet)
            except Exception as e:
                log.exception("Spot client init failed (close all)")
                self._notify_error_popup(title="خطأ", text=str(e))
                return

            self._pending_order_reason = (
                "إغلاق الكل (Spot)" if get_language() == "ar" else "Close all (Spot)"
            )
            raw_sn = self._snapshot_positions_for_close_all(client, exchange)
            self._close_position_snapshots = self._enrich_snapshots_with_mark_price(
                raw_sn, client, exchange
            )
            if not self._close_position_snapshots:
                QMessageBox.warning(
                    self,
                    tr("trading_robot_title"),
                    (
                        "لا توجد مراكز في الجدول لبيعها. اضغط تحديث المراكز من المنصة ثم أعد المحاولة."
                        if get_language() == "ar"
                        else "No positions in the table to sell. Refresh positions from the exchange, then try again."
                    ),
                )
                return

            self._order_in_progress = True
            self._set_trade_buttons_enabled(False)
            self._close_pending_position_id = None
            self._close_worker = ClosePositionWorker(
                client,
                spot_close_rows=list(self._close_position_snapshots),
            )
            self._close_thread = QThread()
            self._close_worker.moveToThread(self._close_thread)
            self._close_thread.started.connect(self._close_worker.run)
            self._close_worker.finished.connect(
                self._on_close_position_finished,
                Qt.ConnectionType.QueuedConnection,
            )
            self._close_thread.start()
            return

        # Futures: إغلاق حقيقي لكل المراكز عبر العميل
        testnet = not self.is_real_mode()
        exchange = (cfg.get("exchange") or "binance").lower()
        if exchange == "etoro":
            api_key, api_secret = load_etoro_settings()
        else:
            api_key, api_secret = get_decrypted_credentials(self, testnet=testnet)
        if not (api_key and api_secret):
            self._notify_error_popup(
                title="API Settings",
                text="يرجى إدخال المفاتيح من إعدادات API (أو مفاتيح eToro عند اختيار eToro).",
            )
            return

        try:
            if exchange == "etoro":
                client = EtoroFuturesClient(api_key, api_secret, testnet=testnet)
            elif exchange == "bitget":
                client = BitgetFuturesClient(api_key, api_secret, testnet=testnet)
            else:
                client = BinanceFuturesClient(api_key, api_secret, testnet=testnet)
        except Exception as e:
            log.exception("Futures client init failed")
            self._notify_error_popup(title="خطأ", text=str(e))
            return

        self._pending_order_reason = (
            "إغلاق كل المراكز" if get_language() == "ar" else "Close all positions"
        )
        raw_sn = self._snapshot_positions_for_close_all(client, exchange)
        self._close_position_snapshots = self._enrich_snapshots_with_mark_price(raw_sn, client, exchange)

        self._order_in_progress = True
        self._set_trade_buttons_enabled(False)
        self._close_pending_position_id = None
        self._close_worker = ClosePositionWorker(client, close_all=True)
        self._close_thread = QThread()
        self._close_worker.moveToThread(self._close_thread)
        self._close_thread.started.connect(self._close_worker.run)
        self._close_worker.finished.connect(
            self._on_close_position_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        self._close_thread.start()

    def _open_history_tab(self):
        """فتح تبويب سجل الصفقات في القسم السفلي."""
        self.show_history_tab_requested.emit()

    # ============================================================
    # Settings
    # ============================================================
    def _show_balance(self):
        """عرض رصيد USDT من المنصة حسب الوضع الحالي (وهمي/حقيقي) ونوع الحساب (سبوت/عقود)."""
        testnet = not self.is_real_mode()
        cfg = load_config()
        exchange = (cfg.get("exchange") or "binance").lower()
        if exchange == "etoro":
            api_key, api_secret = load_etoro_settings()
            if not (api_key and api_secret):
                QMessageBox.warning(
                    self,
                    "عرض الرصيد",
                    "أدخل مفاتيح eToro (User Key و API Key) من إعدادات API.",
                )
                return
        else:
            api_key, api_secret = get_decrypted_credentials(self, testnet=testnet)
            if not (api_key and api_secret):
                QMessageBox.warning(
                    self,
                    "عرض الرصيد",
                    "لا توجد مفاتيح API. افتح «إعدادات API» وأدخل المفتاح والسر في قسم "
                    + ("«تداول وهمي (Testnet)»." if testnet else "«تداول حقيقي (Mainnet)»."),
                )
                return
        use_futures = _config_use_futures(cfg)
        etoro_bd: EtoroBalanceBreakdown | None = None
        try:
            if exchange == "etoro":
                client = EtoroSpotClient(api_key, api_secret, testnet=testnet)
            elif use_futures:
                if exchange == "bitget":
                    client = BitgetFuturesClient(api_key, api_secret, testnet=testnet)
                else:
                    client = BinanceFuturesClient(api_key, api_secret, testnet=testnet)
            else:
                if exchange == "bitget":
                    client = BitgetSpotClient(api_key, api_secret, testnet=testnet)
                else:
                    client = BinanceSpotClient(api_key, api_secret, testnet=testnet)
            if exchange == "etoro":
                etoro_bd = client.get_usdt_balance_breakdown()
                balance = etoro_bd.available
            else:
                balance = client.get_usdt_balance()
            extra = ""
            if balance <= 0 and not use_futures and exchange == "binance" and hasattr(client, "get_account_balances"):
                all_bal, err = client.get_account_balances()
                if err:
                    extra = f"\n\nخطأ من المنصة: {err}"
                elif all_bal:
                    parts = [f"{a}: {v:,.4f}" for a, v in sorted(all_bal.items(), key=lambda x: -x[1])[:8]]
                    extra = f"\n\nأرصدتك على Testnet: {', '.join(parts)}\n\nالتطبيق يحتاج USDT. إن لم يكن لديك USDT، غيّر «المبلغ» إلى «قيمة ثابتة» (مثلاً 50 USDT) وجرّب الشراء؛ إن رفضت المنصة فالحساب لا يملك رصيداً كافياً."
                else:
                    extra = "\n\nالحساب فارغ أو المنصة لم تُعطِ رصيداً. غيّر «المبلغ» إلى «قيمة ثابتة» (مثلاً 50 USDT) للتجربة."
        except Exception as e:
            log.exception("Balance fetch failed")
            QMessageBox.warning(
                self,
                "عرض الرصيد",
                f"تعذّر جلب الرصيد:\n{str(e)}\n\nتأكد من المفاتيح والوضع (وهمي/حقيقي) وزر «السوق» (فوري/عقود).",
            )
            return
        mode_ar = "وهمي (Testnet)" if testnet else "حقيقي (Mainnet)"
        type_ar = "عقود (Futures)" if use_futures else "سبوت (Spot)"
        tip = "\n\n(الرصيد المعروض = USDT السائل فقط. إذا لديك مراكز مفتوحة (شراء لم يُبَع)، قيمتها ليست هنا؛ عند البيع يتحوّل الربح إلى رصيد.)"
        etoro_block = ""
        if exchange == "etoro" and etoro_bd is not None:
            bd = etoro_bd
            etoro_block = (
                f"\n\n— eToro —\n"
                f"ائتمان/رصيد المنصة: {bd.credits:,.2f} USD\n"
                f"محجوز بالطلبات (يُطرح لحساب المتاح): {bd.pending_total_applied:,.2f} USD\n"
                f"  • ordersForOpen: {bd.pending_orders_for_open:,.2f}\n"
                f"  • قائمة orders (حالة مفتوحة فقط): {bd.pending_orders_list:,.2f}\n"
                f"متاح للشراء (نسبة/قيمة): {bd.available:,.2f} USD"
            )
            if bd.ignored_stale_orders_list:
                etoro_block += (
                    "\n\nملاحظة: وُجد مجموع كبير في قائمة orders مع طلبات قليلة في ordersForOpen؛ "
                    "عُومِلت كبيانات قديمة ولم يُطرح ذلك المجموع (لتفادي ظهور رصيد متاح زائف منخفض)."
                )
            tip = ""
        QMessageBox.information(
            self,
            "رصيد USDT",
            f"الوضع: {mode_ar}\nنوع الحساب: {type_ar}\n\nرصيد USDT المتاح: {balance:,.2f}{etoro_block}{tip}{extra}",
        )
        self._emit_balance_for_status_bar(balance, testnet, etoro_breakdown=etoro_bd)

    def _emit_balance_for_status_bar(
        self, balance=None, testnet=None, etoro_breakdown: EtoroBalanceBreakdown | None = None
    ):
        """تحديث نص الرصيد في مستطيل الرصيد (جنب السعر) — مربوط دائماً بوضع التداول: حقيقي=mainnet، وهمي=testnet."""
        def _set_text(text):
            if hasattr(self, "balance_label") and self.balance_label:
                self.balance_label.setText(text)
            if hasattr(self, "balance_updated"):
                self.balance_updated.emit(text)

        def _etoro_balance_tooltip(bd: EtoroBalanceBreakdown) -> str:
            lines = [
                f"متاح للشراء: {bd.available:,.2f} USD",
                f"ائتمان: {bd.credits:,.2f} USD",
                f"  ordersForOpen: {bd.pending_orders_for_open:,.2f} | orders: {bd.pending_orders_list:,.2f}",
            ]
            if bd.ignored_stale_orders_list:
                lines.append("استُبعد جزء كبير من قائمة orders (احتمال بيانات قديمة).")
            return "\n".join(lines)

        if balance is not None and testnet is not None:
            try:
                self._cached_usdt_balance = float(balance)
            except (TypeError, ValueError):
                pass
            text = _balance_bar_line(float(balance), ar=(get_language() == "ar"))
            _set_text(text)
            if etoro_breakdown is not None and hasattr(self, "balance_label") and self.balance_label:
                self.balance_label.setToolTip(_etoro_balance_tooltip(etoro_breakdown))
            elif hasattr(self, "balance_label") and self.balance_label:
                self.balance_label.setToolTip("")
            return
        if getattr(self, "balance_label", None) is None:
            return
        # الرصيد المعروض مربوط دائماً بوضع التداول: حقيقي = mainnet فقط، وهمي = testnet فقط
        use_real = self.is_real_mode()
        testnet = testnet if testnet is not None else (not use_real)
        if use_real:
            testnet = False  # إجبار mainnet عند الوضع الحقيقي
        cfg = load_config()
        exchange = (cfg.get("exchange") or "binance").lower()
        if exchange == "etoro":
            api_key, api_secret = load_etoro_settings()
        else:
            api_key, api_secret = get_decrypted_credentials(self, testnet=testnet)
        if not (api_key and api_secret):
            _set_text(tr("status_balance_none"))
            return
        use_futures = _config_use_futures(cfg)
        log.debug("Balance fetch: %s", "mainnet (حقيقي)" if not testnet else "testnet (وهمي)")
        try:
            balance, etoro_bd = _balance_bar_network_fetch(
                exchange, api_key, api_secret, testnet, use_futures
            )
            try:
                self._cached_usdt_balance = float(balance)
            except (TypeError, ValueError):
                pass
            if etoro_bd is not None:
                bd = etoro_bd
                try:
                    self._cached_usdt_balance = float(bd.available)
                except (TypeError, ValueError):
                    pass
                text = _balance_bar_line(float(bd.available), ar=(get_language() == "ar"))
            else:
                text = _balance_bar_line(float(balance), ar=(get_language() == "ar"))
            _set_text(text)
            if etoro_bd is not None and hasattr(self, "balance_label") and self.balance_label:
                self.balance_label.setToolTip(_etoro_balance_tooltip(etoro_bd))
            elif hasattr(self, "balance_label") and self.balance_label:
                self.balance_label.setToolTip("")
        except Exception:
            _none = tr("status_balance_none")
            hint_ar = " — تحقق من المفاتيح/الاتصال"
            hint_en = " — check keys/connection"
            if not testnet and get_language() == "ar":
                _set_text(_none + hint_ar)
            elif not testnet:
                _set_text(_none + hint_en)
            else:
                _set_text(_none)

    def _on_balance_refresh_clicked(self) -> None:
        thr = getattr(self, "_balance_refresh_thread", None)
        if thr is not None and thr.isRunning():
            return
        use_real = self.is_real_mode()
        testnet = False if use_real else True
        cfg = load_config()
        exchange = (cfg.get("exchange") or "binance").lower()
        if exchange == "etoro":
            ak, sec = load_etoro_settings()
        else:
            ak, sec = get_decrypted_credentials(self, testnet=testnet)
        if not (ak and sec):
            try:
                self.status_bar_message.emit(tr("balance_refresh_no_keys"))
            except Exception:
                pass
            self._emit_balance_for_status_bar(testnet=testnet)
            return
        use_futures = _config_use_futures(cfg)
        self._balance_refresh_prev_text = self.balance_label.text() if self.balance_label else ""
        self.balance_refresh_btn.setEnabled(False)
        if self.balance_label:
            self.balance_label.setText(tr("balance_refresh_busy"))
        worker = _BalanceBarFetchWorker(
            exchange=exchange,
            api_key=ak,
            api_secret=sec,
            testnet=testnet,
            use_futures=use_futures,
        )
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run_fetch)
        worker.finished.connect(self._on_balance_bar_fetch_finished)
        worker.failed.connect(self._on_balance_bar_fetch_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.failed.connect(worker.deleteLater)
        thread.finished.connect(self._balance_refresh_thread_cleanup)
        thread.finished.connect(thread.deleteLater)
        self._balance_refresh_thread = thread
        self._balance_refresh_worker = worker
        thread.start()

    def _balance_refresh_thread_cleanup(self) -> None:
        self._balance_refresh_thread = None
        self._balance_refresh_worker = None
        if getattr(self, "balance_refresh_btn", None):
            self.balance_refresh_btn.setEnabled(True)

    def _on_balance_bar_fetch_finished(self, balance: float, testnet: bool, etoro_bd: object) -> None:
        bd = etoro_bd if isinstance(etoro_bd, EtoroBalanceBreakdown) else None
        self._emit_balance_for_status_bar(float(balance), testnet, etoro_breakdown=bd)

    def _on_balance_bar_fetch_failed(self, err: str) -> None:
        if self.balance_label:
            prev = getattr(self, "_balance_refresh_prev_text", "") or ""
            self.balance_label.setText(prev if prev else tr("status_balance_none"))
        try:
            self.status_bar_message.emit(f"{tr('balance_refresh_failed')}: {err[:200]}")
        except Exception:
            pass

    def api_settings_action(self):
        open_api_settings_window(self)

    def risk_settings_action(self):
        win = RiskSettingsWindow(self)
        win.config_saved.connect(self.risk_settings_saved.emit)
        win.config_saved.connect(lambda c: self._update_strategy_display(c, show_change_message=True))
        win.exec()

    def history_action(self):
        self.show_history_tab_requested.emit()

    def refresh_action(self):
        log.info("Refresh requested")

    def _check_for_updates_action(self):
        """التحقق من إصدار أحدث (رابط manifest في update_manifest_url)."""
        from app_update_ui import start_update_check

        start_update_check(self)

    def _show_settings_menu(self):
        """فتح قائمة الإعدادات (⚙ بجانب لوحة التوصية): لغة، ثيم، API، التحقق من التحديث، باك‌تيست، إلخ."""
        menu = QMenu(self)
        menu.setToolTipsVisible(True)

        # اللغة — عربي / English
        lang_menu = menu.addMenu(tr("settings_menu_lang"))
        act_ar = lang_menu.addAction(tr("risk_lang_ar"))
        act_ar.triggered.connect(lambda: self._set_language("ar"))
        act_en = lang_menu.addAction(tr("risk_lang_en"))
        act_en.triggered.connect(lambda: self._set_language("en"))

        # لون الخلفية — فاتح / قاتم
        theme_menu = menu.addMenu(tr("settings_menu_theme"))
        act_light = theme_menu.addAction(tr("settings_theme_light"))
        act_light.triggered.connect(lambda: self._set_theme("light"))
        act_dark = theme_menu.addAction(tr("settings_theme_dark"))
        act_dark.triggered.connect(lambda: self._set_theme("dark"))

        menu.addSeparator()
        # التداول: وضع وهمي/حقيقي + الروبوت
        trading_menu = menu.addMenu(tr("settings_menu_trading"))
        mode_menu = trading_menu.addMenu(tr("settings_trading_mode"))
        act_test = mode_menu.addAction(tr("settings_trading_testnet"))
        act_test.setCheckable(True)
        act_live = mode_menu.addAction(tr("settings_trading_live"))
        act_live.setCheckable(True)
        # تفعيل الوضع الحالي
        is_real = bool(self._real_mode)
        act_live.setChecked(is_real)
        act_test.setChecked(not is_real)
        act_test.triggered.connect(lambda: self.mode_toggle.set_test_mode())
        act_live.triggered.connect(lambda: self.mode_toggle.set_real_mode())

        act_robot = trading_menu.addAction(tr("settings_robot_toggle"))
        act_robot.setCheckable(True)
        act_robot.setChecked(bool(self._bot_enabled))
        act_robot.triggered.connect(lambda checked: self.toggle_button.setChecked(bool(checked)))

        menu.addAction(tr("trading_api_settings"), self.api_settings_action)
        act_updates = menu.addAction(tr("rec_check_update_btn"))
        act_updates.setToolTip(tr("rec_check_update_tooltip"))
        act_updates.triggered.connect(self._check_for_updates_action)
        menu.addSeparator()
        menu.addAction(tr("menu_backtest"), self._open_backtest_dialog)

        menu.exec(self.settings_btn.mapToGlobal(self.settings_btn.rect().bottomLeft()))

    def _open_backtest_dialog(self):
        """فتح نافذة اختبار الاستراتيجية على البيانات التاريخية."""
        try:
            from backtest_dialog import BacktestDialog
            dlg = BacktestDialog(self.window())
            dlg.exec()
        except Exception as e:
            log.exception("Backtest dialog failed: %s", e)
            QMessageBox.warning(self, tr("menu_backtest"), str(e))

    def _set_language(self, lang: str):
        cfg = load_config()
        cfg["language"] = lang
        save_config(cfg)
        QMessageBox.information(
            self,
            tr("risk_language"),
            tr("risk_restart_hint"),
        )

    def _set_theme(self, theme: str):
        cfg = load_config()
        cfg["theme"] = theme
        save_config(cfg)
        apply_theme(theme)

    # ============================================================
    # Toggle ON/OFF
    # ============================================================
    def toggle_trade_mode(self, state):
        if state:
            self._bot_enabled = True
            self.toggle_button.setText("ON")
            self.toggle_button.setStyleSheet(f"background-color: {UI_GREEN}; color: white;")
            if hasattr(self, "robot_btn") and self.robot_btn:
                self.robot_btn.setChecked(True)
                self.robot_btn.setText("ON")
                self.robot_btn.setStyleSheet(
                    f"background-color: {UI_GREEN}; color: white; border: none; border-radius: 3px; "
                    "font-size: 8px; font-weight: bold; padding: 0 3px;"
                )
            # تحديث نص الحالة فوق السعر عند تفعيل البوت
            if hasattr(self, "bot_status_label") and self.bot_status_label:
                if get_language() == "ar":
                    self.bot_status_label.setText("البوت يعمل — بانتظار إشارة مناسبة")
                else:
                    self.bot_status_label.setText("Bot ON — waiting for a signal")
            log.info("Bot enabled (%s)", "LIVE" if self._real_mode else "TESTNET")
            self.status_bar_message.emit(tr("trading_bot_enabled_notify"))
            self._emit_status_message()
        else:
            self._bot_enabled = False
            self.toggle_button.setText("OFF")
            self.toggle_button.setStyleSheet(f"background-color: {UI_RED}; color: white;")
            if hasattr(self, "robot_btn") and self.robot_btn:
                self.robot_btn.setChecked(False)
                self.robot_btn.setText("OFF")
                self.robot_btn.setStyleSheet(
                    f"background-color: {UI_RED}; color: white; border: none; border-radius: 3px; "
                    "font-size: 8px; font-weight: bold; padding: 0 3px;"
                )
            # تحديث نص الحالة فوق السعر عند إيقاف البوت
            if hasattr(self, "bot_status_label") and self.bot_status_label:
                if get_language() == "ar":
                    self.bot_status_label.setText("البوت متوقف — شغِّل البوت من زر ON/OFF")
                else:
                    self.bot_status_label.setText("Bot is OFF — enable it from the ON/OFF button")
            log.info("Bot disabled")
            self.status_bar_message.emit(tr("trading_bot_disabled_notify"))
            self._emit_status_message()

    def _eval_bot_decide_snapshot(
        self,
        recommendation: str,
        confidence: float,
        indicators=None,
        market_info=None,
    ) -> tuple:
        """نفس مدخلات bot_decide كما في مسار التنفيذ — لقطة ثقة/قرار دون إرسال أمر."""
        cfg = load_config()
        try:
            self._BOT_CONFIDENCE_MIN = float(cfg.get("bot_confidence_min", 60))
        except (TypeError, ValueError):
            self._BOT_CONFIDENCE_MIN = 60.0
        try:
            self._BOT_MAX_OPEN_TRADES = int(cfg.get("bot_max_open_trades", 1))
        except (TypeError, ValueError):
            self._BOT_MAX_OPEN_TRADES = 1
        has_position = False
        pos = None
        if self._positions_panel:
            pos = self._positions_panel.get_position_for_symbol(self.current_symbol)
            has_position = pos is not None and float(pos.get("quantity", 0) or 0) > 0
        try:
            iv = str(getattr(self, "_chart_interval", "1m") or "1m")
            f = self.ws.frames.get(iv) if self.ws else None
            last_price = float(f.last_price) if f and getattr(f, "last_price", None) else None
            if (not last_price or last_price <= 0) and self.ws:
                f1 = self.ws.frames.get("1m")
                last_price = float(f1.last_price) if f1 and getattr(f1, "last_price", None) else last_price
        except (TypeError, AttributeError, ValueError):
            last_price = None
        if not last_price and self._last_price > 0:
            last_price = self._last_price
        self._snapshot_last_price = float(last_price or 0.0)
        candle_high, at_real_peak = self._candle_peak_high_flags(cfg)
        open_count = self._logical_open_count_for_bot()
        open_symbol_count = self._count_open_rows_matching_symbol(self.current_symbol)
        if isinstance(indicators, dict) and indicators.get("close") is not None:
            self._last_indicators = dict(indicators)
        elif not isinstance(getattr(self, "_last_indicators", None), dict) or not self._last_indicators:
            if isinstance(indicators, dict):
                self._last_indicators = indicators
        iv_bot = str(getattr(self, "_chart_interval", "1m") or "1m")
        if isinstance(self._last_indicators, dict):
            self._last_indicators.setdefault("chart_interval", iv_bot)
        try:
            b1h = _compute_mtf_frame_bias(getattr(self, "_last_indicators_1h", None))
            b4h = _compute_mtf_frame_bias(getattr(self, "_last_indicators_4h", None))
            mtf_bias = b1h * 0.4 + b4h * 0.6
            if isinstance(self._last_indicators, dict):
                self._last_indicators["mtf_bias"] = float(mtf_bias)
        except Exception:
            pass
        try:
            info_ai = market_info if isinstance(market_info, dict) else (getattr(self, "_last_market_info", None) or {})
            if isinstance(self._last_indicators, dict) and isinstance(info_ai, dict):
                self._last_indicators["trend"] = str(info_ai.get("trend", "") or "")
                self._last_indicators["volume_strength"] = float(info_ai.get("volume_strength", 0) or 0)
                self._last_indicators["volatility"] = float(info_ai.get("volatility", 0) or 0)
                self._last_indicators["volatility_pct"] = float(info_ai.get("volatility_pct", 0) or 0)
        except Exception:
            pass
        comp_score = None
        try:
            info_ai = market_info if isinstance(market_info, dict) else (getattr(self, "_last_market_info", None) or {})
            if not isinstance(info_ai, dict):
                info_ai = {}
            comp = compute_composite_signal(
                self._last_indicators or {},
                info_ai,
                lang_ar=(get_language() == "ar"),
            )
            comp_score = float(comp.get("score", 0.0) or 0.0)
        except Exception:
            pass
        tp_barrier = self._take_profit_barrier_active(last_price or 0.0)
        if bool(cfg.get("bot_signal_sell_bypass_tp_barrier", False)):
            tp_barrier = False
        open_exp = self._sum_open_positions_notional_usdt()
        planned_n = self._planned_buy_notional_usdt(cfg, float(last_price or 0.0))
        action, final_confidence, skip_reason, _x = bot_decide(
            recommendation,
            confidence,
            self._last_indicators or {},
            cfg,
            has_position=has_position,
            pos=pos,
            last_price=last_price or 0.0,
            daily_pnl=getattr(self, "_daily_pnl", 0.0),
            open_count=open_count,
            open_symbol_count=open_symbol_count,
            current_symbol=self.current_symbol,
            conf_min=self._BOT_CONFIDENCE_MIN,
            candle_high=candle_high,
            at_real_peak=at_real_peak,
            take_profit_barrier=tp_barrier,
            composite_score=comp_score,
            open_portfolio_exposure_usdt=open_exp,
            planned_buy_notional_usdt=planned_n,
            chart_interval=getattr(self, "_chart_interval", None),
        )
        return action, final_confidence, skip_reason, _x, comp_score, cfg

    def _refresh_bot_exec_confidence_display(
        self,
        recommendation: str,
        confidence: float,
        indicators=None,
        market_info=None,
    ) -> None:
        """تحديث تسمية «ثقة تنفيذ البوت (حالية)» و`_last_bot_decide_result`."""
        lbl = getattr(self, "ai_bot_conf_min_value_label", None)
        try:
            t = self._eval_bot_decide_snapshot(recommendation, confidence, indicators, market_info)
            self._last_bot_decide_result = t
            _fc = float(t[1])
            if lbl is not None:
                lbl.setText(f"{_fc:.1f}%")
        except Exception as e:
            log.debug("Bot exec confidence snapshot failed: %s", e)
            self._last_bot_decide_result = None
            if lbl is not None:
                lbl.setText("—")

    def update_ai_panel_display(self, recommendation: str, confidence: float, _indicators=None, _market_info=None):
        """تحديث صندوق AI Panel في الشريط العلوي بالتوصية ولونها؛ الثقة تُعرض مرة واحدة داخل سطر التحليل."""
        rec = (recommendation or "").strip().upper()
        self._last_panel_recommendation = rec
        self._last_panel_confidence = float(confidence or 0)
        try:
            if isinstance(_indicators, dict):
                self._last_market_decision_line = AIPanel.decision_explain_line_for_market_status(
                    _indicators,
                    _market_info if isinstance(_market_info, dict) else (self._last_market_info or {}),
                )
                self._last_market_decision_ts = float(time.time())
        except Exception:
            pass
        if rec == "BUY":
            rec_ar = tr("ai_buy")
            color = f"color: {UI_GREEN}; font-weight: bold; font-size: 12px;"
        elif rec == "SELL":
            rec_ar = tr("ai_sell")
            color = f"color: {UI_RED_DARK}; font-weight: bold; font-size: 12px;"
        else:
            rec_ar = tr("ai_wait") if rec == "WAIT" else (rec or "—")
            color = f"color: {TOP_TEXT_MUTED}; font-weight: bold; font-size: 12px;"
        if hasattr(self, "ai_recommend_label") and self.ai_recommend_label:
            self.ai_recommend_label.setText(rec_ar)
            self.ai_recommend_label.setStyleSheet(color)
        # سطر التحليل: عرض الرقم فقط (الثقة %) — الشرح الطويل يبقى في التلميح وحالة السوق
        _ana_txt = f"{confidence:.1f}%"
        _full = ""
        if isinstance(_indicators, dict):
            _full = str(getattr(self, "_last_market_decision_line", "") or "").strip()
        self.ai_analysis_label.setText(_ana_txt)
        _ts = time.strftime("%H:%M:%S", time.localtime())
        _tip_detail = _full if _full else f"{rec_ar} — {confidence:.1f}%"
        _tip = (
            f"{_tip_detail}\nآخر تحديث عرض: {_ts}\n\n{tr('ai_analysis_engine_tooltip')}"
            if get_language() == "ar"
            else f"{_tip_detail}\nDisplay refresh: {_ts}\n\n{tr('ai_analysis_engine_tooltip')}"
        )
        self.ai_analysis_label.setToolTip(_tip)
        self.ai_analysis_label.setStyleSheet(color)
        self._refresh_bot_exec_confidence_display(recommendation, confidence, _indicators, _market_info)

    def _get_market_decision_line(self, ind: dict, info: dict) -> str:
        """نص سبب القرار في حالة السوق — يفضّل النص المأخوذ من نفس تحديث لوحة التوصية لتفادي أي عدم تزامن."""
        txt = str(getattr(self, "_last_market_decision_line", "") or "").strip()
        if txt:
            ts = float(getattr(self, "_last_market_decision_ts", 0.0) or 0.0)
            if ts > 0:
                hhmmss = time.strftime("%H:%M:%S", time.localtime(ts))
                return f"{txt} — {tr('market_status_decision_updated_at').format(t=hhmmss)}"
            return txt
        txt = AIPanel.decision_explain_line_for_market_status(ind, info)
        now = time.strftime("%H:%M:%S", time.localtime())
        return f"{txt} — {tr('market_status_decision_updated_at').format(t=now)}"
    def set_suggested_strategy(self, strategy_key: str):
        """تحديث مربع الاستراتيجية المقترحة إن وُجد؛ وإلا يبقى اتباع الاقتراح في الخلفية فقط."""
        lbl = getattr(self, "strategy_suggestion_label", None)
        if not (strategy_key and str(strategy_key).strip()):
            if lbl:
                lbl.setText("—")
            self._follow_strategy_timer.stop()
            self._last_suggested_key_for_follow = ""
            self._pending_follow_strategy_key = ""
            return
        if lbl:
            try:
                name = tr("risk_strategy_" + str(strategy_key).strip())
            except Exception:
                name = strategy_key or "—"
            lbl.setText(name or "—")
            lbl.setStyleSheet("color: #d0d4d8; font-weight: bold; font-size: 11px;")

        key = str(strategy_key).strip().lower()
        try:
            cfg0 = load_config()
            if not bool(cfg0.get("bot_follow_suggested_strategy", True)):
                return
        except Exception:
            return
        if key in ("custom",):
            self._follow_strategy_timer.stop()
            self._last_suggested_key_for_follow = key
            return
        prev = getattr(self, "_last_suggested_key_for_follow", "")
        if key == prev:
            return
        self._last_suggested_key_for_follow = key
        try:
            if (cfg0.get("strategy_mode") or "").strip().lower() == key:
                return
        except Exception:
            pass
        self._pending_follow_strategy_key = key
        try:
            sec = int(cfg0.get("bot_follow_suggested_strategy_sec", 50) or 50)
        except (TypeError, ValueError):
            sec = 50
        sec = max(15, min(300, sec))
        self._follow_strategy_timer.stop()
        self._follow_strategy_timer.start(sec * 1000)

    def _apply_followed_suggested_strategy(self) -> None:
        """تم تعطيل الحفظ التلقائي للاستراتيجية المقترحة حمايةً لإعدادات المخاطر."""
        key = (getattr(self, "_pending_follow_strategy_key", None) or "").strip().lower()
        self._pending_follow_strategy_key = ""
        if not key or key in ("custom",):
            return
        try:
            cfg = load_config()
            if not bool(cfg.get("bot_follow_suggested_strategy", True)):
                return
            # لا نغيّر strategy_mode ولا أي إعداد مخاطرة تلقائياً.
            log.info("Follow suggested strategy: auto-apply disabled (suggested=%s)", key)
        except Exception:
            log.exception("Follow suggested strategy apply failed")

    def _bot_wait_indicator_snapshot(
        self,
        *,
        skip_reason: str,
        composite_score: float | None = None,
        raw_rec: str | None = None,
    ) -> str:
        """سطر أو سطران موجزان: أرقام المؤشرات الحالية لتفسير الانتظار (بدون قائمة طويلة)."""
        ind = self._last_indicators if isinstance(getattr(self, "_last_indicators", None), dict) else {}
        if not ind:
            return ""
        lang_ar = get_language() == "ar"
        try:
            rsi = float(ind.get("rsi") or 0.0)
            macd = float(ind.get("macd") or 0.0)
            sig = float(ind.get("signal") or 0.0)
            hist = float(ind.get("hist") or 0.0)
            close = float(ind.get("close") or self._last_price or 0.0)
            vwap = float(ind.get("vwap") or 0.0)
            sk = float(ind.get("stoch_rsi_k") or 0.0)
            sd = float(ind.get("stoch_rsi_d") or 0.0)
            std = int(ind.get("supertrend_dir") or 0)
            cscore = float(ind.get("candle_pattern_score") or 0.0)
            mdf = macd - sig
        except (TypeError, ValueError):
            return ""
        try:
            _cfg_snap = load_config_cached()
        except Exception:
            _cfg_snap = {}
        merge_on = bool((_cfg_snap or {}).get("bot_merge_composite", False))
        sr = (skip_reason or "").strip()
        ru = sr.lower()
        # سطر يربط نص السبب بعائلة المؤشرات (رسائل bot_logic غالباً عربية حتى لو واجهة EN)
        hint = ""
        if "فلتر الارتداد" in sr or "الارتداد المبكر" in sr:
            hint = (
                "الفلتر يفحص: قرب قاع 40 شمعة، RSI، VWAP، دعم، هستوجرام، StochRSI."
                if lang_ar
                else "Filter uses: 40-candle low, RSI, VWAP, support, histogram, StochRSI."
            )
        elif "هبوط قوي بدون ارتداد" in sr or (
            "ارتداد مؤكد" in sr and "فلتر الارتداد" not in sr and "الارتداد المبكر" not in sr
        ):
            hint = (
                "حارس ترند هابط في فلاتر التنفيذ — ليس «فلتر الارتداد المبكر» من إعدادات المخاطر."
                if lang_ar
                else "Bear-trend guard in execution filters — not the Risk «Early bounce» toggle."
            )
        elif "vwap" in ru or "VWAP" in sr:
            hint = (
                "الشرط مرتبط أساساً بموقع السعر مقابل VWAP والدعم."
                if lang_ar
                else "Tied mainly to price vs VWAP and support."
            )
        elif "قمة" in sr or "ذروة" in sr or "stoch" in ru or "ستوك" in sr or "peak" in ru or "top zone" in ru:
            hint = (
                "الشرط مرتبط بقمة/ذروة محلية أو تشبع (RSI، StochRSI، نافذة الشموع)."
                if lang_ar
                else "Tied to local top / overbought (RSI, StochRSI, candle window)."
            )
        elif "macd" in ru or "هستو" in sr or "hist" in ru:
            hint = (
                "الشرط مرتبط بزخم MACD/الهستوجرام."
                if lang_ar
                else "Tied to MACD / histogram momentum."
            )
        elif "شموع" in sr or "نمط" in sr or "candle" in ru or "pattern" in ru:
            hint = (
                "الشرط مرتبط بدرجة/أنماط الشموع."
                if lang_ar
                else "Tied to candle pattern score."
            )
        elif "مركب" in sr or "مركّب" in sr or "composite" in ru:
            hint = (
                (
                    "الشرط مرتبط بدمج المركّب في قرار التنفيذ — عطّل «دمج المؤشر المركّب» في المخاطر إن أردت إزالته."
                    if merge_on
                    else "نص قديم أو إعداد لم يُحفَظ؛ دمج المركّب معطّل حالياً — احفظ المخاطر وأعد تشغيل التحديث."
                )
                if lang_ar
                else (
                    "Tied to composite merge in execution — turn off «Merge composite» in Risk if unwanted."
                    if merge_on
                    else "Stale message or unsaved config; composite merge is off — save Risk settings and retry."
                )
            )
        elif ("ثقة" in sr and "تنفيذ" in sr) or ("confidence" in ru and "min" in ru):
            hint = (
                "حد الثقة يدمج عدة مدخلات؛ الأرقام أدناه لقطة لحظية من المؤشرات."
                if lang_ar
                else "Min confidence blends several inputs; numbers below are a live snapshot."
            )

        parts: list[str] = []
        if lang_ar:
            if rsi >= 68:
                parts.append(f"RSI={rsi:.1f} (تشبع شراء)")
            elif rsi <= 35:
                parts.append(f"RSI={rsi:.1f} (تشبع بيع)")
            else:
                parts.append(f"RSI={rsi:.1f}")
            if mdf > 0 and hist >= 0:
                parts.append("MACD صاعد")
            elif mdf < 0 and hist <= 0:
                parts.append("MACD هابط")
            else:
                parts.append("MACD مختلط")
            if close > 0 and vwap > 0:
                parts.append("فوق VWAP" if close >= vwap else "تحت VWAP")
            if sk or sd:
                kd = "K>D" if sk > sd else ("D>K" if sd > sk else "K≈D")
                parts.append(f"Stoch {sk:.0f}/{sd:.0f} ({kd})")
            if std == 1:
                parts.append("ST↑")
            elif std == -1:
                parts.append("ST↓")
            parts.append(f"شموع{cscore:+.1f}")
            if merge_on and composite_score is not None:
                parts.append(f"مركّب{composite_score:+.1f}")
            rec = (raw_rec or "").strip().upper()
            if rec:
                parts.append(f"لوحة:{rec}")
            snap = "لقطة مؤشرات: " + " | ".join(parts)
        else:
            ob = " (overbought)" if rsi >= 68 else (" (oversold)" if rsi <= 35 else "")
            parts.append(f"RSI={rsi:.1f}{ob}")
            if mdf > 0 and hist >= 0:
                parts.append("MACD bull")
            elif mdf < 0 and hist <= 0:
                parts.append("MACD bear")
            else:
                parts.append("MACD mixed")
            if close > 0 and vwap > 0:
                parts.append("above VWAP" if close >= vwap else "below VWAP")
            if sk or sd:
                kd = "K>D" if sk > sd else ("D>K" if sd > sk else "K≈D")
                parts.append(f"Stoch {sk:.0f}/{sd:.0f} ({kd})")
            if std == 1:
                parts.append("ST up")
            elif std == -1:
                parts.append("ST down")
            parts.append(f"candles{cscore:+.1f}")
            if merge_on and composite_score is not None:
                parts.append(f"comp{composite_score:+.1f}")
            if raw_rec:
                parts.append(f"panel:{str(raw_rec).strip().upper()}")
            snap = "Indicators: " + " | ".join(parts)

        out = []
        if hint:
            out.append(hint)
        out.append(snap)
        return "\n".join(out)

    def _bot_skip(
        self,
        reason: str,
        level: str = "info",
        *,
        raw_rec: str | None = None,
        raw_conf: float | None = None,
        exec_conf: float | None = None,
        conf_min: float | None = None,
        indicator_snapshot: bool = False,
        composite_score: float | None = None,
    ):
        """تسجيل سبب عدم التنفيذ بدون إزعاج (مرة كل 30 ثانية)."""
        now = time.time()
        # عرض السبب بالعربية في الواجهة (مع بقاء الـ log كما هو)
        ui_reason = str(reason or "")
        try:
            if ui_reason.startswith("Waiting —"):
                ui_reason = ui_reason.replace("Waiting —", "انتظار —", 1)
            if "cooldown active" in ui_reason:
                ui_reason = ui_reason.replace("cooldown active", "فترة تهدئة")
            if "order in progress" in ui_reason:
                ui_reason = ui_reason.replace("order in progress", "جاري تنفيذ أمر")
            if "recommendation=" in ui_reason:
                ui_reason = ui_reason.replace("recommendation=", "التوصية=")
                ui_reason = ui_reason.replace("(need BUY/SELL)", "(نحتاج شراء/بيع)")
                ui_reason = ui_reason.replace("WAIT", "انتظار")
                ui_reason = ui_reason.replace("BUY", "شراء")
                ui_reason = ui_reason.replace("SELL", "بيع")
            if "confidence" in ui_reason and "< min" in ui_reason:
                ui_reason = ui_reason.replace("confidence", "الثقة")
                ui_reason = ui_reason.replace("< min", "< الحد الأدنى")
            if "max open trades reached" in ui_reason:
                ui_reason = ui_reason.replace("max open trades reached", "تم الوصول للحد الأقصى للصفقات")
            if "max trades per symbol reached" in ui_reason:
                ui_reason = ui_reason.replace(
                    "max trades per symbol reached", "تم الوصول لحد الصفقات لهذا الرمز"
                )
            if "no open position" in ui_reason:
                ui_reason = ui_reason.replace("no open position (SELL skipped)", "لا توجد صفقة مفتوحة (تخطي البيع)")
            if "live price not available" in ui_reason:
                ui_reason = ui_reason.replace("live price not available", "السعر المباشر غير متاح")
            if "price at candle high" in ui_reason:
                ui_reason = ui_reason.replace("price at candle high (buy on pullback, not at top)", "السعر عند قمة الشمعة (انتظر تصحيحاً بسيطاً)")
                ui_reason = ui_reason.replace("price at candle high (no buy at peak)", "السعر عند قمة الشمعة (لا تشتري في القمة)")
            if "price above VWAP" in ui_reason:
                ui_reason = ui_reason.replace("price above VWAP (buy on dips only)", "السعر أعلى من VWAP (شراء عند النزول فقط)")
            if "price at resistance R1" in ui_reason:
                ui_reason = ui_reason.replace("price at resistance R1 (buy on pullback or breakout)", "السعر عند مقاومة R1 (انتظر تصحيحاً أو اختراقاً)")
            if ui_reason == "Buying — bounce from support":
                ui_reason = "شراء — ارتداد من الدعم"
            if ui_reason == "Executing…":
                ui_reason = "تنفيذ…"
            if "auto-sell off" in ui_reason and "SELL from AI" in ui_reason:
                ui_reason = (
                    "انتظار — البيع التلقائي معطّل: توصية بيع من اللوحة لا تُنفَّذ. "
                    "فعّل «البيع التلقائي» في المخاطر أو استخدم زر البيع. "
                    "(حد البيع عند السعر/وقف الخسارة يبقى من مسار التحديث كل 500ms إن كان مفعّلاً.)"
                )
            if ui_reason == "حد الخسارة…":
                ui_reason = "حد الخسارة…"
            if ui_reason == "بيع عند الذروة…":
                ui_reason = "بيع عند الذروة…"
            if ui_reason == "بيع عند النزول…":
                ui_reason = "بيع عند النزول…"
            if ui_reason == "Stopped — daily loss limit reached":
                ui_reason = "توقف — تم الوصول لحد الخسارة اليومية"
            if ui_reason == "Stopped — max trades per day reached":
                ui_reason = "توقف — تم الوصول لحد الصفقات اليومي"
        except Exception:
            ui_reason = str(reason or "")
        # سطر موحّد واضح يزيل التناقض الظاهري بين لوحة التوصية وقرار التنفيذ.
        # نستخدمه خصوصاً عند منع التنفيذ بسبب الثقة.
        try:
            if (
                isinstance(raw_conf, (int, float))
                and isinstance(exec_conf, (int, float))
                and isinstance(conf_min, (int, float))
                and raw_rec
                and ("confidence" in str(reason or "").lower())
                and ("< min" in str(reason or "").lower())
            ):
                rec_ar = "شراء" if str(raw_rec).upper() == "BUY" else ("بيع" if str(raw_rec).upper() == "SELL" else str(raw_rec))
                ui_reason = (
                    f"انتظار — ثقة التنفيذ {float(exec_conf):.1f}% أقل من الحد {float(conf_min):.0f}% "
                    f"(التوصية: {rec_ar} {float(raw_conf):.1f}%)"
                )
        except Exception:
            pass
        details_text = (ui_reason or "").strip()
        if indicator_snapshot:
            try:
                snap = self._bot_wait_indicator_snapshot(
                    skip_reason=str(reason or ""),
                    composite_score=composite_score,
                    raw_rec=raw_rec,
                )
                if snap:
                    details_text = f"{details_text}\n\n{snap}".strip()
            except Exception:
                pass
        if hasattr(self, "bot_status_label") and self.bot_status_label:
            self.bot_status_label.setText(ui_reason)
        try:
            self._last_bot_decision_details = details_text
        except Exception:
            self._last_bot_decision_details = str(reason or "").strip()
        # اللوحة على WAIT: سلوك عادي — لا نملأ السجل بـ INFO كل بضع ثوانٍ
        rs = str(reason or "")
        if "recommendation=WAIT" in rs and "need BUY/SELL" in rs:
            return
        # التهدئة للسجل فقط — لا تمنع تحديث التفاصيل أعلاه
        if now - (self._bot_last_reason_time or 0) < 30:
            return
        self._bot_last_reason_time = now
        if level == "warning":
            log.warning("Bot: %s", reason)
        elif level == "debug":
            log.debug("Bot: %s", reason)
        else:
            log.info("Bot: %s", reason)

    def _show_bot_decision_details(self):
        """عرض نافذة صغيرة تشرح آخر سبب/قرار للبوت عند النقر على نص الحالة."""
        try:
            text = (self._last_bot_decision_details or "").strip()
        except Exception:
            text = ""
        if not text:
            text = tr("trading_bot_no_details")
        title = tr("trading_bot_details_title")
        try:
            QMessageBox.information(self, title, text)
        except Exception:
            # في حال استدعاء قبل اكتمال إنشاء الواجهة
            log.info("Bot details: %s", text)

    def _execution_busy_for_orders(self) -> bool:
        """يمنع تداخل أوامر حقيقية: خيط OrderWorker أو خيط إغلاق مركز.
        خيط حلّ position_id لـ eToro بعد شراء ناجح يعمل في الخلفية فقط — لا يُعتبر «أمراً قيد التنفيذ»
        وإلا يبقى البوت والـ SL والأزرار معلّقة دقائق رغم انتهاء الأمر على المنصة."""
        th_ord = getattr(self, "_order_thread", None)
        if th_ord is not None:
            try:
                if not th_ord.isRunning():
                    self._order_thread = None
                    self._order_worker = None
                else:
                    return True
            except Exception:
                return True
        ct = getattr(self, "_close_thread", None)
        if ct is not None:
            try:
                if not ct.isRunning():
                    self._close_thread = None
                    self._close_worker = None
                else:
                    return True
            except Exception:
                return True
        # تنظيف مراجع خيط eToro عند انتهائه — دون حجب تنفيذ أوامر جديدة أثناء تشغيله
        rt = getattr(self, "_etoro_resolve_thread", None)
        if rt is not None:
            try:
                if not rt.isRunning():
                    self._etoro_resolve_thread = None
                    self._etoro_resolve_worker = None
            except Exception:
                pass
        # العلم أولاً كان يُفحص قبل تنظيف الخيوط؛ إن بقي True دون أي خيط نشط يبقى «أمر سابق» وهمياً
        if getattr(self, "_order_in_progress", False):
            any_running = False
            for attr in ("_order_thread", "_close_thread"):
                t = getattr(self, attr, None)
                if t is None:
                    continue
                try:
                    if t.isRunning():
                        any_running = True
                        break
                except Exception:
                    any_running = True
                    break
            if not any_running:
                log.warning(
                    "Self-heal: _order_in_progress with no running order/close thread — re-enabling UI"
                )
                self._reenable_trade_buttons()
        if getattr(self, "_order_in_progress", False):
            return True
        return False

    def _same_symbol_buy_interval_sec(self, cfg: dict) -> int:
        """الفاصل بالثواني بين شراءين لنفس الرمز (0 = معطّل)."""
        try:
            min_gap_min = int(
                cfg.get(
                    "bot_same_symbol_buy_min_interval_min",
                    cfg.get("bot_same_symbol_buy_min_interval_sec", 60) / 60.0,
                )
                or 0
            )
        except Exception:
            min_gap_min = 0
        return max(0, int(min_gap_min * 60))

    def _same_symbol_buy_interval_should_block(self, cfg: dict) -> tuple[bool, int]:
        """(يمنع، ثوانٍ متبقية) — يعتمد على آخر طابع شراء لنفس الرمز وليس على ظهور المركز في الجدول."""
        min_gap_sec = self._same_symbol_buy_interval_sec(cfg)
        if min_gap_sec <= 0:
            return False, 0
        sym_k = str(self.current_symbol or "").strip().upper()
        if not sym_k:
            return False, 0
        d = getattr(self, "_bot_last_buy_ts_by_symbol", None) or {}
        last_buy_ts = float(d.get(sym_k, 0.0) or 0.0)
        if last_buy_ts <= 0:
            return False, 0
        elapsed = time.time() - last_buy_ts
        if elapsed < float(min_gap_sec):
            return True, int(max(1, round(float(min_gap_sec) - elapsed)))
        return False, 0

    def _stamp_bot_buy_commit_ts(self, cfg: dict) -> None:
        """يُستدعى عند بدء تنفيذ شراء بعد تجاوز الفلاتر — يمنع شراءاً ثانياً قبل انتهاء الفاصل حتى لو لم يُحدَّث الجدول بعد."""
        if self._same_symbol_buy_interval_sec(cfg) <= 0:
            return
        sym_k = str(self.current_symbol or "").strip().upper()
        if not sym_k:
            return
        if not hasattr(self, "_bot_last_buy_ts_by_symbol") or self._bot_last_buy_ts_by_symbol is None:
            self._bot_last_buy_ts_by_symbol = {}
        self._bot_last_buy_ts_by_symbol[sym_k] = time.time()

    def on_ai_recommendation(self, recommendation: str, confidence: float, indicators=None, market_info=None):
        """عند توصية AI: إن كان الربوت مفعّلاً ووضع Testnet، تنفيذ شراء/بيع تلقائي.
        إذا كان هناك مركز مفتوح ووصل السعر إلى هدف الربح (TP) يُنفَّذ بيع تلقائي حتى لو التوصية WAIT."""
        self._last_panel_recommendation = (recommendation or "").strip().upper()
        self._last_panel_confidence = float(confidence or 0)
        self._refresh_bot_exec_confidence_display(recommendation, confidence, indicators, market_info)
        if not self._bot_enabled:
            return
        if time.time() < self._bot_cooldown_until:
            # إذا كان الأمر لا يزال قيد التنفيذ لا نعرض «فترة تهدئة» حتى لا يتوهم المستخدم تعارضاً
            if self._execution_busy_for_orders():
                if hasattr(self, "bot_status_label") and self.bot_status_label:
                    self.bot_status_label.setText("تنفيذ…")
                return
            self._bot_skip("Waiting — cooldown active")
            return
        if self._execution_busy_for_orders():
            self._bot_skip("Waiting — order in progress")
            return
        has_position = False
        pos = None
        sym_u = str(self.current_symbol or "").upper()
        if self._positions_panel:
            pos = self._positions_panel.get_position_for_symbol(self.current_symbol)
            has_position = pos is not None and float(pos.get("quantity", 0) or 0) > 0
        # لا نُلغِ قفل eToro هنا لمجرد ظهور صف في الجدول — كان يُصفّر الحماية قبل ثوانٍ
        # فيُسمح بشراء ثانٍ لنفس الرمز بينما المنصة/API ما زالت غير متزامنة (positions=0 في السجل).
        # يُمسح القفل عند نجاح البيع أو انتهاء المهلة أو التنظيف في مسار الأمر.
        cfg = load_config()
        # حد الثقة وعدد المراكز: يجب أن يطابقا الملف في كل تقييم — كان يُستخدم كاش الواجهة فيُنفَّذ البوت بعتبة قديمة إذا حُفظت الإعدادات دون مسار update_risk_display أو بعد تعلّم آلي يعدّل الملف.
        try:
            self._BOT_CONFIDENCE_MIN = float(cfg.get("bot_confidence_min", 60))
        except (TypeError, ValueError):
            self._BOT_CONFIDENCE_MIN = 60.0
        try:
            self._BOT_MAX_OPEN_TRADES = int(cfg.get("bot_max_open_trades", 1))
        except (TypeError, ValueError):
            self._BOT_MAX_OPEN_TRADES = 1
        exchange = (cfg.get("exchange") or "").lower()
        use_futures = _config_use_futures(cfg)
        # قفل مؤقت بعد شراء eToro: يمنع شراء/إشارة جديدة لنفس الرمز حتى تأكيد المركز من المنصة
        if exchange == "etoro" and use_futures and not has_position:
            pend_until = float(getattr(self, "_etoro_pending_symbol_until", {}).get(sym_u, 0.0) or 0.0)
            now = time.time()
            if pend_until > now:
                self._bot_skip("Waiting — eToro position confirmation in progress")
                return
            if pend_until > 0 and pend_until <= now:
                # انتهت المهلة: تنظيف القفل
                try:
                    self._etoro_pending_symbol_until.pop(sym_u, None)
                except Exception:
                    pass
        t = getattr(self, "_last_bot_decide_result", None)
        if not isinstance(t, tuple) or len(t) < 6:
            log.warning("Bot: decide snapshot missing — skip execution path")
            return
        action, final_confidence, skip_reason, _, comp_score, cfg = t
        last_price = float(getattr(self, "_snapshot_last_price", 0) or 0) or float(self._last_price or 0)
        if skip_reason:
            if skip_reason.startswith("Circuit Breaker"):
                try:
                    pause_min = int(get_circuit_breaker_config(cfg)["pause_minutes"])
                    cb_until = time.time() + max(60, pause_min * 60)
                    self._cb_pause_until = max(float(getattr(self, "_cb_pause_until", 0.0) or 0.0), cb_until)
                    self._bot_cooldown_until = max(
                        float(getattr(self, "_bot_cooldown_until", 0.0) or 0.0),
                        cb_until,
                    )
                except Exception:
                    pass
            self._bot_skip(
                skip_reason,
                raw_rec=(recommendation or "").strip().upper(),
                raw_conf=float(confidence or 0.0),
                exec_conf=float(final_confidence or 0.0),
                conf_min=float(self._BOT_CONFIDENCE_MIN),
                indicator_snapshot=True,
                composite_score=comp_score,
            )
            return
        if action is None:
            log.warning(
                "Bot: decide returned no action without skip_reason (recommendation=%s) — check bot_logic",
                (recommendation or "").strip().upper(),
            )
            return
        recommendation = action
        confidence = final_confidence
        if recommendation == "BUY":
            blocked, _wait_sec = self._same_symbol_buy_interval_should_block(cfg)
            if blocked:
                wait_left_min = max(1, int((_wait_sec + 59) // 60))
                self._bot_skip(
                    tr("bot_wait_same_symbol_buy_interval").format(m=wait_left_min)
                )
                return
            if (cfg.get("exchange") or "").lower() == "etoro" and _config_use_futures(cfg):
                ul = getattr(self, "_bot_etoro_unlisted_symbol", None)
                cur = str(self.current_symbol or "").strip().upper()
                if ul and cur and cur == ul:
                    self._bot_skip(tr("bot_wait_etoro_symbol_not_listed"), level="debug")
                    return
        # بيع من توصية اللوحة كان يتجاوز _etoro_min_hold_until (موجود فقط في مسارات السعر) → شراء+بيع بنفس اللحظة
        if recommendation == "SELL" and (cfg.get("exchange") or "").lower() == "etoro" and _config_use_futures(cfg):
            try:
                if time.time() < float(getattr(self, "_etoro_min_hold_until", 0) or 0):
                    self._bot_skip(
                        "Waiting — eToro post-buy hold (avoid instant sell after buy)"
                    )
                    return
            except Exception:
                pass
        if recommendation == "SELL" and self._positions_panel:
            _pos_now = self._positions_panel.get_position_for_symbol(self.current_symbol)
            if not _pos_now or float(_pos_now.get("quantity", 0) or 0) <= 0:
                log.warning(
                    "Bot: SELL skipped before execute — no row/qty in positions table for %s",
                    self.current_symbol,
                )
                self._bot_skip("Waiting — no open position quantity (SELL skipped)")
                return
        log.info("Bot: All filters passed for %s (confidence %.1f%%), proceeding to execute", action, final_confidence)
        # تأكيد قبل أول صفقة حقيقية (LIVE)
        if self._real_mode and not cfg.get("first_real_order_done", False):
            mb = QMessageBox(self)
            mb.setWindowTitle(tr("trading_first_real_title"))
            mb.setText(tr("trading_first_real_text"))
            mb.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            mb.setDefaultButton(QMessageBox.StandardButton.No)
            if mb.exec() != QMessageBox.StandardButton.Yes:
                return
            cfg["first_real_order_done"] = True
            save_config(cfg)
        self._bot_cooldown_until = time.time() + self._BOT_COOLDOWN_SEC
        self._order_in_progress = True
        self._set_trade_buttons_enabled(False)
        self._pending_order_confidence = confidence
        self._pending_indicators = indicators if isinstance(indicators, dict) else {}
        self._pending_market_info = market_info if isinstance(market_info, dict) else {}
        log_recommendation(self.current_symbol, recommendation, confidence, last_price, executed=True)
        if hasattr(self, "bot_status_label") and self.bot_status_label:
            self.bot_status_label.setText("Executing…")
        log.info(
            "Bot executing %s (confidence %.1f%%) — source=AI panel recommendation (same as displayed)",
            recommendation,
            confidence,
        )
        self._pending_order_reason = "توصية البوت - شراء" if recommendation == "BUY" else "توصية البوت - بيع"
        if recommendation == "BUY":
            self._stamp_bot_buy_commit_ts(cfg)
        self._execute_real_order(recommendation, last_price or self._last_price or 0, cfg, testnet=(not self._real_mode))

    # ============================================================
    # REAL / TESTNET MODE
    # ============================================================
    def on_mode_changed(self, is_real: bool):
        """
        True  = REAL — أوامر على المنصة الرئيسية (mainnet).
        False = TESTNET — أوامر على testnet.binance (أموال تجريبية).
        """
        self._real_mode = is_real
        self._update_mode_badge()
        # إظهار الرصيد حسب الوضع فوراً: عند الحقيقي نحدّث بعد جلب الرصيد من mainnet
        if hasattr(self, "balance_label") and self.balance_label:
            self.balance_label.setText(tr("status_balance_none"))
        if is_real:
            # عند التحويل إلى حقيقي: تحقق من وجود مفاتيح Mainnet
            api_key, api_secret = get_decrypted_credentials(self, testnet=False)
            if not (api_key and api_secret):
                QMessageBox.warning(self, tr("main_api_msg_title"), "لم يتم فتح مفاتيح التداول الحقيقي. الرجوع للوضع الوهمي.")
                self._real_mode = False
                self._update_mode_badge()
                try:
                    self.mode_toggle.set_test_mode()
                except Exception:
                    pass
                self._emit_balance_for_status_bar(testnet=True)
                QTimer.singleShot(400, self._sync_open_positions_from_exchange)
                return
            log.info("Mode: Live — mainnet orders (unlocked)")
            self._emit_balance_for_status_bar(testnet=False)
            QTimer.singleShot(800, lambda: self._emit_balance_for_status_bar(testnet=False))
            QTimer.singleShot(600, self._sync_open_positions_from_exchange)
        else:
            log.info("Mode: Testnet — testnet orders")
            self._emit_balance_for_status_bar(testnet=True)
            QTimer.singleShot(800, lambda: self._emit_balance_for_status_bar(testnet=True))
            QTimer.singleShot(600, self._sync_open_positions_from_exchange)

    def _update_mode_badge(self):
        """تحديث شارة وضع التداول (وهمي/حقيقي) في لوحة التوصية."""
        if not hasattr(self, "mode_badge") or not self.mode_badge:
            return
        if self._real_mode:
            self.mode_badge.setText(tr("settings_trading_live"))
            self.mode_badge.setStyleSheet(
                "padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; "
                "background-color: #c0392b; color: #fff; border: 1px solid #a93226;"
            )
            self.mode_badge.setToolTip("تداول حقيقي — أموال حقيقية" if get_language() == "ar" else "Live trading — real funds")
        else:
            self.mode_badge.setText(tr("settings_trading_testnet"))
            self.mode_badge.setStyleSheet(
                "padding: 2px 8px; border-radius: 4px; font-size: 10px; font-weight: bold; "
                "background-color: #d4a017; color: #1a1a1a; border: 1px solid #b8860b;"
            )
            self.mode_badge.setToolTip("تداول وهمي — أموال تجريبية" if get_language() == "ar" else "Testnet — simulated funds")

    def shutdown_background(self) -> None:
        """عند إغلاق النافذة الرئيسية: إيقاف المؤقتات وWebSocket وانتظار خيوط Qt قصيرة حتى يعود التحكم للطرفية."""
        try:
            self._persist_etoro_positions_cache_from_table()
        except Exception:
            pass
        try:
            for name in (
                "_connection_timer",
                "_heavy_price_timer",
                "_etoro_positions_timer",
                "_order_timeout_timer",
                "_bot_status_restore_timer",
            ):
                t = getattr(self, name, None)
                if t is not None:
                    t.stop()
        except Exception:
            pass
        try:
            if getattr(self, "ws", None) is not None:
                self.ws.stop()
        except Exception:
            pass
        try:
            w = getattr(self, "_etoro_resolve_worker", None)
            if w is not None and hasattr(w, "cancel"):
                w.cancel()
        except Exception:
            pass
        try:
            th = getattr(self, "_symbol_load_thread", None)
            if th is not None and th.isRunning():
                th.requestInterruption()
                th.wait(2000)
        except Exception:
            pass
        for attr in ("_order_thread", "_close_thread", "_etoro_resolve_thread"):
            try:
                th = getattr(self, attr, None)
                if th is not None and th.isRunning():
                    th.quit()
                    th.wait(2000)
            except Exception:
                pass

    def is_real_mode(self) -> bool:
        return self._real_mode

    # ============================================================
    # Dark Style
    # ============================================================
    def dark_style(self):
        return f"""
        QWidget {{
            background-color: {TOP_PANEL_BG};
            color: {TOP_TEXT_PRIMARY};
            font-size: 13px;
        }}

        QGroupBox {{
            border: 1px solid {TOP_PANEL_BORDER};
            border-radius: 10px;
            margin-top: 12px;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top center;
            padding: 0 6px;
            color: {TOP_PANEL_TITLE};
            font-size: 11px;
        }}
        QLabel {{
            color: {TOP_TEXT_PRIMARY};
        }}
        /* التوصية والتحليل وآخر صفقة: لون افتراضي محايد؛ يُستبدل ديناميكياً بأخضر/أحمر في update_ai_panel_display */
        #LastTradeValue, #AIRecommendValue, #AIAnalysisValue {{
            color: #b0b8c0;
            font-weight: bold;
            font-size: 11px;
        }}
        """
