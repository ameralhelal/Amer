"""
CryptoWeb Hub — minimal API + static shell (داخل مشروع التداول: trading/web_hub).
تشغيل من مجلد web_hub:
  cd web_hub
  python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.etoro_config import get_etoro_credentials

_BASE = Path(__file__).resolve().parent.parent
_STATIC = _BASE / "static"
_TRADING_ROOT = Path(__file__).resolve().parent.parent.parent
if _TRADING_ROOT.is_dir() and (_TRADING_ROOT / "exchange_etoro.py").is_file():
    _root_s = str(_TRADING_ROOT)
    if _root_s not in sys.path:
        sys.path.insert(0, _root_s)

try:
    from exchange_etoro import EtoroClient
except ImportError:
    EtoroClient = None  # type: ignore[misc, assignment]

# يُعرَض في /api/hub-info ورأس HTTP للصفحة الرئيسية — غيّره عند تحديث الواجهة
HUB_BUILD = "20260410"

_MAIN_PY = Path(__file__).resolve()


@asynccontextmanager
async def _hub_lifespan(app: FastAPI):
    """يطبع على الطرفية أي main.py تُحمَّل فعلياً — مفيد عند وجود أكثر من نسخة/منفذ."""
    print(f"[CryptoWeb Hub] main.py = {_MAIN_PY}", flush=True)
    print(f"[CryptoWeb Hub] static   = {_STATIC.resolve()}", flush=True)
    print("[CryptoWeb Hub] توقّع GET /api/health → version 0.1.1 ومفتاح hub", flush=True)
    yield


app = FastAPI(
    title="CryptoWeb Hub",
    description="Web companion shell; domain and full features to be wired later.",
    version="0.1.1",
    lifespan=_hub_lifespan,
)


class _NoCacheAssetsMiddleware(BaseHTTPMiddleware):
    """يمنع تخزين /assets في المتصفح حتى تظهر تعديلات CSS/JS فور إعادة التحميل."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        p = request.url.path or ""
        if p.startswith("/assets/"):
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            response.headers["Pragma"] = "no-cache"
        return response


app.add_middleware(_NoCacheAssetsMiddleware)

if _STATIC.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_STATIC)), name="assets")


def _hub_diagnostic() -> dict:
    """مسار الملفات الثابتة وفحص سريع لمحتوى index.html."""
    idx = _STATIC / "index.html"
    out: dict = {
        "main_py_path": str(_MAIN_PY),
        "hub_build": HUB_BUILD,
        "static_dir": str(_STATIC.resolve()),
        "index_html_path": str(idx.resolve()) if idx.is_file() else None,
        "index_exists": idx.is_file(),
    }
    if idx.is_file():
        st = idx.stat()
        out["index_size_bytes"] = st.st_size
        out["index_mtime_unix"] = int(st.st_mtime)
        try:
            raw = idx.read_text(encoding="utf-8", errors="replace")
            out["index_has_hub_static_marker"] = f"hub-static-v: {HUB_BUILD}" in raw
            out["index_has_etoro_enabled"] = '<option value="etoro" disabled>' not in raw
        except OSError:
            out["index_read_error"] = True
    return out


@app.get("/api/health")
def health():
    body: dict = {"ok": True, "service": "cryptoweb-hub", "version": "0.1.1"}
    body["hub"] = _hub_diagnostic()
    return JSONResponse(body)


@app.get("/api/hub-info")
@app.get("/hub-info")
def hub_info():
    """
    يحدد من أي مجلد يُقرأ `static/index.html` فعلياً.
    نفس البيانات مضمّنة أيضاً في GET /api/health تحت المفتاح `hub`.
    """
    return JSONResponse({"ok": True, **_hub_diagnostic()})


def _rsi_wilder(closes: list[float], period: int = 14) -> float | None:
    """RSI (Wilder) من أسعار الإغلاق."""
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gains.append(max(ch, 0.0))
        losses.append(max(-ch, 0.0))
    if len(gains) < period:
        return None
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = (avg_g * (period - 1) + gains[i]) / period
        avg_l = (avg_l * (period - 1) + losses[i]) / period
    if avg_l <= 0:
        return 100.0 if avg_g > 0 else 50.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))


def _sma_last(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    return sum(vals[-n:]) / n


def _vwap_typical(h: list[float], low: list[float], c: list[float], vol: list[float]) -> float | None:
    if not c or len(c) != len(vol) or len(h) != len(c):
        return None
    num = 0.0
    den = 0.0
    for i in range(len(c)):
        tp = (h[i] + low[i] + c[i]) / 3.0
        vi = vol[i]
        num += tp * vi
        den += vi
    return num / den if den > 0 else None


def _atr_simple(h: list[float], low: list[float], c: list[float], period: int = 14) -> float | None:
    if len(c) < period + 1 or len(h) != len(c):
        return None
    trs: list[float] = []
    for i in range(1, len(c)):
        tr = max(h[i] - low[i], abs(h[i] - c[i - 1]), abs(low[i] - c[i - 1]))
        trs.append(tr)
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def _fp_hub(x: float | None, nd: int = 6) -> str:
    try:
        v = float(x or 0)
    except (TypeError, ValueError):
        return "—"
    if abs(v) < 1e-12:
        return "—"
    if abs(v) >= 1000:
        return f"{v:.2f}"
    if abs(v) >= 1:
        return f"{v:.4f}"
    return f"{v:.{nd}g}"


def _hub_indicator_lines_ar(sym: str, iv: str, ind: dict, candles_n: int, ticker_price: float) -> list[str]:
    """نص عربي مكثّف يقارب صفوف IndicatorsPanel."""
    if not ind:
        return []
    macd = float(ind.get("macd", 0) or 0)
    sig = float(ind.get("signal", 0) or 0)
    hist = float(ind.get("hist", 0) or 0)
    rsi = float(ind.get("rsi", 50) or 50)
    upper = float(ind.get("bb_upper", 0) or 0)
    lower = float(ind.get("bb_lower", 0) or 0)
    bw = float(ind.get("bb_width", 0) or 0)
    vwap = float(ind.get("vwap", 0) or 0)
    atr = float(ind.get("atr14", 0) or 0)
    adx = float(ind.get("adx14", 0) or 0)
    pdi = float(ind.get("plus_di14", 0) or 0)
    mdi = float(ind.get("minus_di14", 0) or 0)
    st_k = float(ind.get("stoch_rsi_k", 0) or 0)
    st_d = float(ind.get("stoch_rsi_d", 0) or 0)
    obv = float(ind.get("obv", 0) or 0)
    cci = float(ind.get("cci20", 0) or 0)
    ten = float(ind.get("ichimoku_tenkan", 0) or 0)
    kij = float(ind.get("ichimoku_kijun", 0) or 0)
    st_val = float(ind.get("supertrend", 0) or 0)
    st_dir = int(ind.get("supertrend_dir", 0) or 0)
    mfi = float(ind.get("mfi", 0) or 0)
    willr = float(ind.get("willr", 0) or 0)
    ema9 = float(ind.get("ema9", 0) or 0)
    ema21 = float(ind.get("ema21", 0) or 0)
    ema50 = float(ind.get("ema50", 0) or 0)
    ema200 = float(ind.get("ema200", 0) or 0)
    pv = float(ind.get("pivot", 0) or 0)
    r1 = float(ind.get("pivot_r1", 0) or 0)
    r2 = float(ind.get("pivot_r2", 0) or 0)
    s1 = float(ind.get("pivot_s1", 0) or 0)
    s2 = float(ind.get("pivot_s2", 0) or 0)
    st_txt = "صاعد ↑" if st_dir == 1 else ("هابط ↓" if st_dir == -1 else "—")
    prof = str(ind.get("indicator_speed_profile", "") or "")
    lines = [
        f"{sym} · إطار {iv} · شموع محمّلة: {candles_n} · ملف المؤشرات: {prof or 'balanced'}",
        f"MACD: {macd:.5f} | إشارة: {sig:.5f} | هيستو: {hist:.5f}",
        f"RSI: {rsi:.2f} | Bollinger: علوي={_fp_hub(upper)} سفلي={_fp_hub(lower)} عرض={bw:.4f}",
        (
            f"VWAP={_fp_hub(vwap)} | ATR14={_fp_hub(atr)} | ADX14={adx:.1f} (+DI={pdi:.1f}/-DI={mdi:.1f}) "
            f"StochRSI K/D={st_k:.1f}/{st_d:.1f} | CCI20={cci:.1f} | OBV={obv:.0f} | إيشيموكو T/K={_fp_hub(ten)}/{_fp_hub(kij)}"
        ),
        (
            f"Supertrend: {_fp_hub(st_val)} ({st_txt}) | MFI: {mfi:.1f} | Williams %R: {willr:.1f} "
            f"EMA9={_fp_hub(ema9)} EMA21={_fp_hub(ema21)} EMA50={_fp_hub(ema50)} EMA200={_fp_hub(ema200)}"
        ),
        f"Pivot: {_fp_hub(pv)} | R1={_fp_hub(r1)} R2={_fp_hub(r2)} | S1={_fp_hub(s1)} S2={_fp_hub(s2)}",
        f"سعر إغلاق آخر شمعة: {_fp_hub(float(ind.get('close', 0) or 0))} · ticker 24h: {ticker_price}",
    ]
    fg = ind.get("fear_greed_index")
    if fg is not None:
        fgc = str(ind.get("fear_greed_classification") or "").strip()
        tail = f" ({fgc})" if fgc else ""
        lines.append(f"Fear & Greed: {int(fg)}{tail}")
    bps = ind.get("buy_pressure_score")
    if bps is not None:
        lines.append(f"Buy pressure score: {float(bps):.1f}")
    csum = (ind.get("candle_pattern_summary") or "").strip()
    if csum and csum != "—":
        lines.append(f"أنماط الشموع: {csum}")
    lines.append("")
    lines.append("— نفس منطق الحساب: websocket_manager.FrameStream (تطبيق سطح المكتب).")
    return lines


@app.get("/api/snapshot")
def api_snapshot(symbol: str = "BTCUSDT", interval: str = "15m"):
    """
    لحالة السوق ولوحة التوصية: ticker 24h + شموع Binance؛ المؤشرات عبر FrameStream كالتطبيق.
    """
    sym = str(symbol or "BTCUSDT").strip().upper().replace("/", "")
    iv = str(interval or "15m").strip()
    if iv not in ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"):
        iv = "15m"
    out: dict = {"ok": False, "symbol": sym, "interval": iv}
    try:
        r24 = requests.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            params={"symbol": sym},
            timeout=12,
        )
        r24.raise_for_status()
        d24 = r24.json()
        if not isinstance(d24, dict):
            raise ValueError("24hr not dict")
        price = float(d24["lastPrice"])
        change_pct = float(d24["priceChangePercent"])
        high_24 = float(d24["highPrice"])
        low_24 = float(d24["lowPrice"])
        open_24 = float(d24["openPrice"])
        qvol = float(d24["quoteVolume"])
    except Exception as e:
        out["error"] = str(e)
        return JSONResponse(out, status_code=200)

    range_pct = ((high_24 - low_24) / low_24 * 100.0) if low_24 > 0 else 0.0

    raw_k: list = []
    try:
        rk = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": sym, "interval": iv, "limit": 500},
            timeout=16,
        )
        rk.raise_for_status()
        raw_k = rk.json()
        if not isinstance(raw_k, list):
            raw_k = []
    except Exception:
        raw_k = []

    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    vols: list[float] = []
    for row in raw_k:
        try:
            highs.append(float(row[2]))
            lows.append(float(row[3]))
            closes.append(float(row[4]))
            vols.append(float(row[5]))
        except (IndexError, TypeError, ValueError):
            continue

    ind_full: dict | None = None
    mkt_full: dict | None = None
    framestream_error: str | None = None
    try:
        from app.framestream_snapshot import indicators_and_market_from_klines

        ind_full, mkt_full = indicators_and_market_from_klines(sym, iv, raw_k)
    except Exception as e:
        framestream_error = str(e)
        ind_full, mkt_full = None, None

    if ind_full:
        rsi_v = ind_full.get("rsi")
        sma20_v = ind_full.get("ma20")
        vwap_v = ind_full.get("vwap")
        atr14_v = ind_full.get("atr14")
        last_c = float(ind_full.get("close") or price)
        candles_used = len(raw_k)
        rsi = float(rsi_v) if rsi_v is not None else None
        sma20 = float(sma20_v) if sma20_v is not None else None
        vwap = float(vwap_v) if vwap_v is not None else None
        atr14 = float(atr14_v) if atr14_v is not None else None
    else:
        rsi = _rsi_wilder(closes, 14) if closes else None
        sma20 = _sma_last(closes, 20) if closes else None
        last_c = closes[-1] if closes else price
        vwap = _vwap_typical(highs, lows, closes, vols) if closes and vols else None
        atr14 = _atr_simple(highs, lows, closes, 14) if closes else None
        candles_used = len(closes)

    suggestion = "انتظار — لا إشارة واضحة"
    confidence = 48
    strategy_ar = f"إطار {iv} · احتياطي (بدون FrameStream كامل)"
    rec_detail_ar = ""

    if ind_full:
        try:
            from ai_panel import AIPanel

            rec_code, conf_ai = AIPanel.get_recommendation(ind_full, mkt_full or {})
            rec_code = str(rec_code or "WAIT").strip().upper()
            smap = {"BUY": "شراء", "SELL": "بيع", "WAIT": "انتظار"}
            suggestion = smap.get(rec_code, "انتظار")
            confidence = int(max(30, min(95, round(float(conf_ai)))))
        except Exception:
            suggestion = "انتظار — تعذر تحميل AIPanel (تحقق من PyQt6 في بيئة الخادم)"
            confidence = 45
        try:
            from recommendation_analysis_text import suggest_strategy_from_market
            from translations import tr

            sk, rk = suggest_strategy_from_market(ind_full, iv)
            strategy_ar = f"{tr('risk_strategy_' + sk)} — {tr(rk)}"
        except Exception:
            strategy_ar = f"إطار {iv} — نفس مؤشرات FrameStream"
        try:
            from recommendation_analysis_text import build_recommendation_analysis_result

            ra = build_recommendation_analysis_result(price, ind_full, mkt_full or {}, iv)
            if ra:
                rec_detail_ar = ra.text
        except Exception:
            rec_detail_ar = ""
    elif rsi is not None and sma20 is not None:
        strategy_ar = f"إطار {iv} · RSI + SMA20 + VWAP/ATR (احتياطي)"
        if rsi < 32 and change_pct < 0.5:
            suggestion = "إيجابي محافظ (ارتداد محتمل)"
            confidence = min(52 + int(32 - rsi), 82)
        elif rsi > 68 and change_pct > 0:
            suggestion = "حذر — تشبع صعودي"
            confidence = min(52 + int(rsi - 68), 82)
        elif last_c > sma20 and change_pct > 0:
            suggestion = "ميل صعودي قصير المدى"
            confidence = 58
        elif last_c < sma20 and change_pct < 0:
            suggestion = "ميل هبوطي قصير المدى"
            confidence = 58
        else:
            suggestion = "محايد — راقب التأكيد"
            confidence = 50

    cb_badge = "OFF"
    if range_pct > 10:
        cb_badge = "تقلب"
    elif range_pct > 6 and abs(change_pct) < 0.25:
        cb_badge = "حذر"

    if qvol >= 1_000_000_000:
        liq_short = f"{qvol / 1_000_000_000:.2f}B"
    elif qvol >= 1_000_000:
        liq_short = f"{qvol / 1_000_000:.1f}M"
    else:
        liq_short = f"{qvol:.0f}"

    if ind_full:
        ind_lines = _hub_indicator_lines_ar(sym, iv, ind_full, candles_used, price)
    else:
        ind_lines = [
            f"الرمز {sym} · إطار الشموع {iv} · عدد الشموع: {candles_used}",
            f"آخر إغلاق (شمعة): {last_c}",
        ]
        if framestream_error:
            ind_lines.append(f"تعذر FrameStream: {framestream_error[:200]}")
        if rsi is not None:
            ind_lines.append(f"RSI(14) احتياطي: {rsi:.2f}")
        if sma20 is not None:
            ind_lines.append(f"SMA(20) احتياطي: {sma20:.8g}")
        if vwap is not None:
            ind_lines.append(f"VWAP احتياطي: {vwap:.8g}")
        if atr14 is not None:
            ind_lines.append(f"ATR(14) احتياطي: {atr14:.8g}")
        ind_lines.append(f"سعر لحظي (ticker 24h): {price}")

    mkt_lines = [
        "ملخص 24 ساعة (Binance ticker):",
        f"أعلى {high_24} · أدنى {low_24} · افتتاح {open_24}",
        f"التغيّر: {change_pct:.2f}% · حجم تداول (USDT) ≈ {liq_short}",
        f"مدى اليوم تقريباً: {range_pct:.2f}%",
    ]
    if mkt_full:
        mkt_lines.append("")
        mkt_lines.append("ملخص إطار الشموع (نفس compute_market_info):")
        mkt_lines.append(
            f"اتجاه: {mkt_full.get('trend', '—')} · قوة حجم: {float(mkt_full.get('volume_strength', 0) or 0):.3f} "
            f"· تقلب σ: {float(mkt_full.get('volatility', 0) or 0):.6g} · تقلب %: {float(mkt_full.get('volatility_pct', 0) or 0):.3f}%"
        )
    mkt_lines.extend(["", "التنفيذ والمراكز: من تطبيق سطح المكتب أو الوسيط."])

    market_indicators_html_ar = ""
    if ind_full:
        try:
            from market_status_readout import build_market_indicators_readout_html

            _mh = build_market_indicators_readout_html(ind_full, mkt_full or {}, guard_line_html="")
            if _mh:
                market_indicators_html_ar = _mh
        except Exception:
            market_indicators_html_ar = ""

    body: dict = {
        "ok": True,
        "price": price,
        "change_pct": change_pct,
        "high_24h": high_24,
        "low_24h": low_24,
        "open_24h": open_24,
        "quote_volume_usdt": qvol,
        "quote_volume_short": liq_short,
        "range_pct_24h": round(range_pct, 2),
        "rsi_14": round(float(rsi), 2) if rsi is not None else None,
        "sma_20": round(float(sma20), 8) if sma20 is not None else None,
        "vwap": round(float(vwap), 8) if vwap is not None else None,
        "atr_14": round(float(atr14), 8) if atr14 is not None else None,
        "candles_used": candles_used,
        "suggestion_ar": suggestion,
        "confidence": int(min(95, max(30, confidence))),
        "strategy_ar": strategy_ar,
        "cb_badge": cb_badge,
        "indicators_text_ar": "\n".join(ind_lines),
        "market_text_ar": "\n".join(mkt_lines),
        "indicators_engine": "framestream" if ind_full else "fallback",
        "recommendation_detail_ar": rec_detail_ar,
        "market_indicators_html_ar": market_indicators_html_ar,
    }
    if ind_full is not None:
        body["indicators"] = ind_full
    if mkt_full is not None:
        body["market_info"] = mkt_full
    out.update(body)
    return JSONResponse(out)


@app.get("/api/quote")
def api_quote(symbol: str = "BTCUSDT"):
    """
    آخر سعر ونسبة تغيّر 24h من Binance (لتحديث رأس الواجهة بين طلبات الشموع).
    """
    sym = str(symbol or "BTCUSDT").strip().upper().replace("/", "")
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            params={"symbol": sym},
            timeout=12,
        )
        r.raise_for_status()
        d = r.json()
        if not isinstance(d, dict):
            return JSONResponse({"ok": False, "symbol": sym, "error": "bad response"}, status_code=200)
        return JSONResponse(
            {
                "ok": True,
                "symbol": str(d.get("symbol") or sym),
                "price": float(d["lastPrice"]),
                "change_pct": float(d["priceChangePercent"]),
            }
        )
    except Exception as e:
        return JSONResponse({"ok": False, "symbol": sym, "error": str(e)}, status_code=200)


@app.get("/api/binance-check")
def binance_check():
    """
    فحص من الخادم (وليس من المتصفح) هل يصل Python إلى api.binance.com.
    يُستخدم من واجهة «إعدادات» عندما يفشل /api/klines.
    """
    try:
        r = requests.get("https://api.binance.com/api/v3/ping", timeout=12)
        r.raise_for_status()
        return JSONResponse({"ok": True, "http_status": r.status_code})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=200)


@app.get("/api/klines")
def api_klines(symbol: str = "BTCUSDT", interval: str = "15m", limit: int = 300):
    """
    وكيل شموع Binance للمتصفح (يتفادى CORS). نفس شكل /api/v3/klines مبسّط.
    """
    sym = str(symbol or "BTCUSDT").strip().upper().replace("/", "")
    iv = str(interval or "15m").strip()
    if iv not in ("1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"):
        iv = "15m"
    try:
        lim = max(50, min(1000, int(limit)))
    except (TypeError, ValueError):
        lim = 300
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": sym, "interval": iv, "limit": lim},
            timeout=15,
        )
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        return JSONResponse({"error": str(e), "symbol": sym}, status_code=502)
    out = []
    for row in raw if isinstance(raw, list) else []:
        try:
            out.append(
                {
                    "t": int(row[0]) // 1000,
                    "o": float(row[1]),
                    "h": float(row[2]),
                    "l": float(row[3]),
                    "c": float(row[4]),
                    "v": float(row[5]),
                }
            )
        except (IndexError, TypeError, ValueError):
            continue
    return JSONResponse({"symbol": sym, "interval": iv, "candles": out})


def _etoro_client_or_response():
    """(client, None) أو (None, JSONResponse خطأ)."""
    if EtoroClient is None:
        return None, JSONResponse(
            {"ok": False, "error": "تعذر تحميل exchange_etoro (مسار المشروع)."},
            status_code=503,
        )
    u, k, demo = get_etoro_credentials()
    if not u or not k:
        hint = (
            "المفاتيح غير موجودة على الخادم. خيارات: (1) متغيرات البيئة ETORO_USER_KEY و ETORO_API_KEY "
            "(2) ملف %APPDATA%\\CryptoTrading\\api_settings.json بنفس مفاتيح eToro كما في تطبيق سطح المكتب "
            "— إن كان الملف مشفّراً استخدم المتغيرات فقط."
        )
        return None, JSONResponse(
            {"ok": False, "configured": False, "hint": hint},
            status_code=200,
        )
    try:
        return EtoroClient(u, k, demo=demo), None
    except Exception as e:
        return None, JSONResponse({"ok": False, "error": str(e)}, status_code=200)


@app.get("/api/etoro/status")
def etoro_status():
    """
    التحقق من اتصال eToro باستخدام المفاتيح على الخادم فقط (لا تُرسل للمتصفح).
    """
    client, err = _etoro_client_or_response()
    if client is None:
        return err
    try:
        bd = client.get_usdt_balance_breakdown()
        bal = bd.available
        pos = client.get_positions()
        n = len(pos) if isinstance(pos, list) else 0
        return JSONResponse(
            {
                "ok": True,
                "configured": True,
                "demo": client.demo,
                "balance_usdt": bal,
                "etoro_credits_usd": bd.credits,
                "etoro_pending_reserved_usd": bd.pending_total_applied,
                "etoro_pending_orders_for_open_usd": bd.pending_orders_for_open,
                "etoro_pending_orders_list_usd": bd.pending_orders_list,
                "etoro_ignored_stale_orders_list": bd.ignored_stale_orders_list,
                "open_positions": n,
            }
        )
    except Exception as e:
        return JSONResponse(
            {
                "ok": False,
                "configured": True,
                "demo": client.demo,
                "error": str(e),
            },
            status_code=200,
        )


@app.get("/api/etoro/price")
def etoro_price(symbol: str = "BTCUSDT"):
    """آخر سعر للأداة عبر واجهة eToro (يتطلب مفاتيح الخادم)."""
    client, err = _etoro_client_or_response()
    if client is None:
        return err
    sym = str(symbol or "BTCUSDT").strip().upper()
    try:
        px = client.get_last_price(sym)
        if px and px > 0:
            return JSONResponse({"ok": True, "symbol": sym, "price": px, "demo": client.demo})
        return JSONResponse(
            {"ok": False, "symbol": sym, "error": "لا سعر متاح لهذا الرمز على eToro"},
            status_code=200,
        )
    except Exception as e:
        return JSONResponse({"ok": False, "symbol": sym, "error": str(e)}, status_code=200)


@app.websocket("/ws/echo")
async def ws_echo(websocket: WebSocket):
    """Placeholder WebSocket — replace with market stream proxy later."""
    await websocket.accept()
    try:
        while True:
            msg = await websocket.receive_text()
            await websocket.send_text(f"echo: {msg}")
    except WebSocketDisconnect:
        pass


@app.get("/")
async def root_page():
    index = _STATIC / "index.html"
    if index.is_file():
        return FileResponse(
            index,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "X-Hub-Build": HUB_BUILD,
                "X-Hub-Static-Dir": str(_STATIC.resolve()),
            },
        )
    return JSONResponse(
        {"message": "ضع static/index.html بجانب app/ داخل مجلد web_hub"},
        status_code=404,
    )
