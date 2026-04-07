# دراسة مرشحي السوق لقائمة «المتوقعة»: مجال سيولة (حجم 24h) + تقدير صعود أمامي من هيكل السعر والمؤشرات
from __future__ import annotations

import logging
from typing import Any

import requests

from ai_panel import AIPanel
from binance_chart_aliases import binance_kline_stream_symbol
from composite_signal import compute_composite_signal
from translations import get_language
from websocket_manager import FrameStream

log = logging.getLogger("trading.expected_scan")

_EXCLUDE = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")
# أزواج سيولة ضخمة عملياً مسطّحة — لا تصلح لدراسة «حركة متوقعة»
_EXCLUDE_STABLE_USDT = frozenset(
    {"USDCUSDT", "FDUSDUSDT", "USDEUSDT", "USD1USDT", "TUSDUSDT", "DAIUSDT", "USDPUSDT"}
)


def _fx(x: Any, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def compute_expected_upside_pct(
    ind: dict,
    info: dict | None,
    *,
    composite_score: float,
    recommendation: str,
    chg_24h: float | None = None,
) -> float:
    """
    تقدير تعليمي لصعود محتمل % (نحو ~24h القادمة كأفق ذهني، وليس تنبؤاً مضموناً).
    يعتمد على: بعد السعر عن مقاومات pivot/BB/قمة نافذة، فيبوناتشي 61.8% على نفس النطاق،
    المؤشر المركّب، RSI، MACD، أنماط الشموع، واتجاه السوق في market_info.
    """
    close = _fx(ind.get("close"))
    if close <= 0:
        return 0.0
    info = info if isinstance(info, dict) else {}
    rec_u = str(recommendation or "WAIT").upper()
    comp = max(-100.0, min(100.0, _fx(composite_score)))

    above: list[float] = []
    for k in ("pivot_r1", "pivot_r2", "pivot_r3"):
        v = _fx(ind.get(k))
        if v > close * 1.00015:
            above.append(v)
    bu = _fx(ind.get("bb_upper"))
    if bu > close * 1.00015:
        above.append(bu)
    wh = _fx(ind.get("window_high_recent"))
    if wh > close * 1.00015:
        above.append(wh)
    nearest_r = min(above) if above else close * 1.022
    pct_resist = max(0.0, (nearest_r - close) / close * 100.0)

    lo = _fx(ind.get("window_low_recent"))
    hi = _fx(ind.get("window_high_recent"))
    pct_fib = 0.0
    if hi > lo > 0:
        rng = hi - lo
        f618 = hi - 0.618 * rng
        if close <= f618:
            pct_fib = max(0.0, (f618 - close) / close * 100.0) * 0.55 + max(0.0, (hi - close) / close * 100.0) * 0.12
        elif close < hi:
            pct_fib = max(0.0, (hi - close) / close * 100.0) * 0.42
        else:
            pct_fib = 0.0

    comp_adj = max(0.0, min(1.0, (comp + 25.0) / 125.0))
    rsi = _fx(ind.get("rsi"), 50.0)
    rsi_bounce = max(0.0, (42.0 - min(rsi, 42.0)) / 42.0)
    hist = _fx(ind.get("hist"))
    macd_boost = 0.55 + max(-0.35, min(0.35, hist * 3.0))
    pat = int(ind.get("candle_pattern_score") or 0)
    pat_boost = 0.52 + max(-0.22, min(0.22, pat / 55.0))
    trend_mul = 1.0 if str(info.get("trend") or "").upper() == "UP" else 0.38

    structural = min(pct_resist, 18.0) * 0.48 + min(pct_fib, 14.0) * 0.36
    momentum = (
        comp_adj * 3.8 + rsi_bounce * 3.2 + (macd_boost - 0.55) * 2.4 + (pat_boost - 0.52) * 2.0
    ) * trend_mul
    raw = structural + momentum

    if rec_u == "SELL":
        raw *= 0.32
    elif rec_u == "BUY":
        raw *= 1.07
    if comp < -28.0:
        raw *= 0.48
    elif comp > 22.0:
        raw *= 1.06

    # عملات ارتفعت كثيراً في 24h غالباً تظهر أعلى القائمة لأن الزخم/الاتجاه يرفعان raw —
    # نخفّض التقدير «لأمام» لتقليل مطاردة من صعد بالفعل (ليس تنبؤاً بالانعكاس).
    if chg_24h is not None:
        try:
            chg = max(-35.0, min(90.0, float(chg_24h)))
        except (TypeError, ValueError):
            chg = 0.0
        if chg > 4.0:
            raw *= max(0.38, 1.0 - (chg - 4.0) * 0.019)

    return max(0.0, min(22.0, round(raw, 1)))


def fetch_expected_study_pool(cfg: dict) -> list[dict[str, Any]]:
    """
    مرشحون للتحليل: أزواج سيولة عالية (حجم تداول USDT في آخر 24h من تذكرة Binance) —
    لاختيار أسواق نشطة فقط، وليس «من صعد بالفعل».
    الترتيب النهائي للعرض يُحدَّد لاحقاً حسب تقدير الصعود المتوقع من الشموع والمؤشرات.
    يُستبعد اختيارياً من ارتفعت 24h أكثر من عتبة (تقليل مطاردة الضخة).
    """
    try:
        pool_size = int(cfg.get("market_scanner_pool_size", 50) or 50)
    except (TypeError, ValueError):
        pool_size = 50
    pool_size = max(10, min(200, pool_size))
    try:
        min_qv = float(cfg.get("expected_study_min_quote_volume_usdt", 10_000_000.0) or 10_000_000.0)
    except (TypeError, ValueError):
        min_qv = 10_000_000.0
    try:
        max_gain = float(cfg.get("expected_study_exclude_24h_gain_above_pct", 28.0) or 28.0)
    except (TypeError, ValueError):
        max_gain = 28.0

    ranked: list[tuple[float, dict[str, Any]]] = []
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=12)
        r.raise_for_status()
        arr = r.json()
        if not isinstance(arr, list):
            return []
        for it in arr:
            try:
                sym = str(it.get("symbol") or "").strip().upper()
                if not sym.endswith("USDT"):
                    continue
                if any(sym.endswith(x) for x in _EXCLUDE):
                    continue
                if sym in _EXCLUDE_STABLE_USDT:
                    continue
                last_p = float(it.get("lastPrice") or 0.0)
                high_p = float(it.get("highPrice") or 0.0)
                low_p = float(it.get("lowPrice") or 0.0)
                chg = float(it.get("priceChangePercent") or 0.0)
                qv = float(it.get("quoteVolume") or 0.0)
                if last_p <= 0 or qv <= 0:
                    continue
                if qv < min_qv:
                    continue
                if max_gain > 0 and chg > max_gain:
                    continue
                day_range_pct = ((high_p - low_p) / last_p * 100.0) if high_p > 0 and low_p > 0 else 0.0
                ranked.append(
                    (
                        qv,
                        {
                            "symbol": sym,
                            "chg_pct": chg,
                            "quote_volume_usdt": qv,
                            "range_pct": day_range_pct,
                            "activity": qv,
                        },
                    )
                )
            except Exception:
                continue
    except Exception as e:
        log.warning("expected study pool fetch failed: %s", e)
        return []

    def _study_pool_rank_key(item: tuple[float, dict[str, Any]]) -> float:
        qv, row = item
        chg = max(-35.0, min(90.0, _fx(row.get("chg_pct"))))
        # سيولة عالية لكن مع تثبيط تدريجي لـ +24h الكبير — يدخل مرشحون نشطون أقل «امتداداً يومياً» لأعلى N للتحليل
        damp = 1.0 / (1.0 + max(0.0, chg - 1.5) * 0.026)
        return float(qv) * damp

    ranked.sort(key=_study_pool_rank_key, reverse=True)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for _, row in ranked:
        s = row["symbol"]
        if s in seen:
            continue
        seen.add(s)
        out.append(row)
        if len(out) >= pool_size:
            break
    return out


def fetch_momentum_pool(cfg: dict) -> list[dict[str, Any]]:
    """
    يبني ترتيباً من تذاكر Binance 24h بنفس منطق ماسح السوق في لوحة التداول.
    يُرجع قوائم من dict: symbol, chg_pct, quote_volume_usdt, range_pct, activity
    """
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

    ranked: list[tuple[float, dict[str, Any]]] = []
    try:
        r = requests.get("https://api.binance.com/api/v3/ticker/24hr", timeout=12)
        r.raise_for_status()
        arr = r.json()
        if not isinstance(arr, list):
            return []
        for it in arr:
            try:
                sym = str(it.get("symbol") or "").strip().upper()
                if not sym.endswith("USDT"):
                    continue
                if any(sym.endswith(x) for x in _EXCLUDE):
                    continue
                last_p = float(it.get("lastPrice") or 0.0)
                high_p = float(it.get("highPrice") or 0.0)
                low_p = float(it.get("lowPrice") or 0.0)
                chg = float(it.get("priceChangePercent") or 0.0)
                qv = float(it.get("quoteVolume") or 0.0)
                if last_p <= 0 or qv <= 0:
                    continue
                if chg < min_chg:
                    continue
                day_range_pct = ((high_p - low_p) / last_p * 100.0) if high_p > 0 and low_p > 0 else 0.0
                if day_range_pct < min_rng:
                    continue
                if qv < min_qv:
                    continue
                activity = (qv / 1_000_000.0) + (chg * 8.0) + (day_range_pct * 5.0)
                ranked.append(
                    (
                        activity,
                        {
                            "symbol": sym,
                            "chg_pct": chg,
                            "quote_volume_usdt": qv,
                            "range_pct": day_range_pct,
                            "activity": activity,
                        },
                    )
                )
            except Exception:
                continue
    except Exception as e:
        log.warning("expected scan 24h fetch failed: %s", e)
        return []

    ranked.sort(key=lambda t: t[0], reverse=True)
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for _, row in ranked:
        s = row["symbol"]
        if s in seen:
            continue
        seen.add(s)
        out.append(row)
        if len(out) >= pool_size:
            break
    return out


def analyze_symbol(
    symbol: str,
    interval: str,
    cfg: dict,
    *,
    chg_24h: float | None = None,
    quote_volume_usdt: float | None = None,
) -> dict[str, Any] | None:
    """يحمّل الشموع ويحسب التوصية والمركّب لرمز واحد."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    if interval not in ("1m", "5m", "15m", "1h", "4h", "1d"):
        interval = "4h"
    try:
        stream_sym = binance_kline_stream_symbol(sym)
        fs = FrameStream(symbol=stream_sym, interval=interval)
        bucket: dict[str, Any] = {"ind": None, "info": None}
        fs.on_indicators = lambda d, b=bucket: b.__setitem__("ind", d)
        fs.on_market_info = lambda i, b=bucket: b.__setitem__("info", i)
        fs.load_historical(lightweight=True)
        ind = bucket.get("ind") if isinstance(bucket.get("ind"), dict) else None
        info = bucket.get("info") if isinstance(bucket.get("info"), dict) else {}
        if not ind:
            return None
        lang_ar = get_language() == "ar"
        rec, _conf = AIPanel.get_recommendation(ind, info, cfg)
        rec_u = str(rec or "").upper()
        try:
            comp = compute_composite_signal(ind, info, lang_ar=lang_ar)
            comp_sc = float(comp.get("score", 0.0) or 0.0)
        except Exception:
            comp_sc = 0.0
        qv_out: float | None
        try:
            qv_out = float(quote_volume_usdt) if quote_volume_usdt is not None else None
        except (TypeError, ValueError):
            qv_out = None
        chg_f: float | None
        try:
            chg_f = float(chg_24h) if chg_24h is not None else None
        except (TypeError, ValueError):
            chg_f = None
        exp_pct = compute_expected_upside_pct(
            ind,
            info,
            composite_score=comp_sc,
            recommendation=rec_u,
            chg_24h=chg_f,
        )
        return {
            "symbol": sym,
            "quote_volume_usdt": qv_out,
            "expected_upside_pct": float(exp_pct),
            "chart_interval": interval,
            "chg_24h_pct": chg_f,
        }
    except Exception as e:
        log.debug("expected scan analyze %s failed: %s", sym, e)
        return None
