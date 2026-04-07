# backtest_engine.py — اختبار الاستراتيجية على بيانات تاريخية (مثل TradingView / MetaTrader)
import logging
import requests
from typing import List, Dict, Any, Optional, Tuple

from ai_panel import AIPanel
from binance_chart_aliases import binance_spot_pair_symbol

log = logging.getLogger("trading.backtest")


def _apply_trade_usd_fees(trades: List[Dict], notional_usd: float, fee_roundtrip_pct: float) -> None:
    """يُضيف لكل صفقة: pnl_usd_gross, fee_usd, pnl_usd (صافٍ بعد الرسوم)."""
    fee_rt = max(0.0, float(fee_roundtrip_pct))
    n = max(0.0, float(notional_usd))
    for t in trades:
        pct = float(t.get("pnl_pct") or 0.0)
        gross = n * pct / 100.0
        fee = n * (fee_rt / 100.0)
        t["pnl_usd_gross"] = round(gross, 4)
        t["fee_usd"] = round(fee, 4)
        t["pnl_usd"] = round(gross - fee, 4)


def _equity_and_drawdown(initial: float, net_pnls: List[float]) -> Tuple[List[float], float]:
    """منحنى الرصيد بعد كل صفقة + أقصى تراجع نسبي %."""
    curve = [float(initial)]
    eq = float(initial)
    for x in net_pnls:
        eq += x
        curve.append(eq)
    peak = curve[0] if curve else initial
    max_dd = 0.0
    for e in curve:
        if e > peak:
            peak = e
        if peak > 1e-12:
            dd = (peak - e) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return curve, round(max_dd, 2)


def _build_extended_summary(
    trades: List[Dict],
    *,
    initial_capital_usd: float,
    notional_usd: float,
    fee_roundtrip_pct: float,
    symbol: str,
    interval: str,
    candles_count: int,
) -> Dict[str, Any]:
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]
    net_pnls = [float(t.get("pnl_usd", 0.0)) for t in trades]
    equity_curve, max_dd_pct = _equity_and_drawdown(initial_capital_usd, net_pnls)
    final_equity = equity_curve[-1] if equity_curve else initial_capital_usd
    total_pnl_usd = round(sum(net_pnls), 2)

    sum_win_usd = sum(float(t.get("pnl_usd", 0)) for t in wins)
    sum_loss_usd = sum(float(t.get("pnl_usd", 0)) for t in losses)
    profit_factor = None
    if sum_loss_usd < 0:
        profit_factor = round(sum_win_usd / abs(sum_loss_usd), 3)

    avg_win_pct = round(sum(t["pnl_pct"] for t in wins) / len(wins), 3) if wins else 0.0
    avg_loss_pct = round(sum(t["pnl_pct"] for t in losses) / len(losses), 3) if losses else 0.0
    avg_win_usd = round(sum_win_usd / len(wins), 2) if wins else 0.0
    avg_loss_usd = round(sum_loss_usd / len(losses), 2) if losses else 0.0

    rr_pct = None
    if losses and avg_loss_pct != 0:
        rr_pct = round(avg_win_pct / abs(avg_loss_pct), 3)

    exit_reasons: Dict[str, int] = {}
    for t in trades:
        r = str(t.get("exit_reason") or "?")
        exit_reasons[r] = exit_reasons.get(r, 0) + 1

    # مجموع نسب الصفقات ≠ عائد رأس المال (الرسوم ثابتة لكل صفقة بالدولار)
    sum_trade_pnl_pct = sum(float(t["pnl_pct"]) for t in trades)
    avg_trade_pnl_pct = (
        round(sum_trade_pnl_pct / len(trades), 4) if trades else 0.0
    )
    total_return_on_capital_pct = (
        round((final_equity - initial_capital_usd) / initial_capital_usd * 100.0, 2)
        if initial_capital_usd > 1e-12
        else 0.0
    )
    return {
        "total_trades": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate_pct": round(100.0 * len(wins) / len(trades), 1) if trades else 0,
        # عائد على رأس المال — يطابق الصافي $ والرصيد النهائي
        "total_pnl_pct": total_return_on_capital_pct,
        "sum_trade_pnl_pct": round(sum_trade_pnl_pct, 2),
        "avg_pnl_pct": avg_trade_pnl_pct,
        "candles_count": candles_count,
        "symbol": symbol,
        "interval": interval,
        "notional_usd": round(notional_usd, 2),
        "initial_capital_usd": round(initial_capital_usd, 2),
        "fee_roundtrip_pct": round(fee_roundtrip_pct, 4),
        "total_pnl_usd": total_pnl_usd,
        "final_equity_usd": round(final_equity, 2),
        "equity_curve": equity_curve,
        "max_drawdown_pct": max_dd_pct,
        "avg_win_pct": avg_win_pct,
        "avg_loss_pct": avg_loss_pct,
        "avg_win_usd": avg_win_usd,
        "avg_loss_usd": avg_loss_usd,
        "rr_ratio_pct": rr_pct,
        "profit_factor": profit_factor,
        "exit_reasons": exit_reasons,
    }

# Binance: أقصى 1000 شمعة لكل طلب — نستخدم طلبات متعددة (startTime/endTime) لأكثر من 1000
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
MAX_PER_REQUEST = 1000


def _parse_candle(c) -> Tuple:
    return (float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5]))


def fetch_historical_candles(symbol: str, interval: str, limit: int = 500) -> List[Tuple]:
    """جلب شموع تاريخية من Binance. يدعم أكثر من 1000 شمعة بطلبات متعددة. كل عنصر: (open, high, low, close, volume)."""
    symbol = binance_spot_pair_symbol(symbol).upper()
    all_candles: List[Tuple] = []
    end_time = None  # نبدأ من الأحدث ثم نرجع للخلف
    try:
        while len(all_candles) < limit:
            fetch_count = min(MAX_PER_REQUEST, limit - len(all_candles))
            params = {
                "symbol": symbol,
                "interval": interval,
                "limit": fetch_count,
            }
            if end_time is not None:
                params["endTime"] = end_time
            r = requests.get(BINANCE_KLINES_URL, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            batch = [_parse_candle(c) for c in data]
            # Binance يرجع الأقدم أولاً عند استخدام endTime — نضيفهم في البداية
            all_candles = batch + all_candles
            if len(data) < fetch_count:
                break
            # أول طابع زمني في الدفعة السابقة = endTime للطلب التالي (نطلب الأقدم)
            first_ts = int(data[0][0])
            end_time = first_ts - 1
            if end_time <= 0:
                break
        # أخذ العدد المطلوب فقط (الأحدث «limit» شمعة)
        if len(all_candles) > limit:
            all_candles = all_candles[-limit:]
        return all_candles
    except requests.HTTPError as e:
        resp = getattr(e, "response", None)
        if resp is not None and resp.status_code == 400:
            log.warning(
                "fetch_historical_candles: Binance rejected symbol %r (400). "
                "Use a valid USDT spot pair; typo fixes (e.g. AMPP→AMP) are applied when known.",
                symbol,
            )
            return []
        log.exception("fetch_historical_candles failed: %s", e)
        return []
    except Exception as e:
        log.exception("fetch_historical_candles failed: %s", e)
        return []


def run_backtest(
    candles: List[Tuple],
    config: Dict[str, Any],
    symbol: str = "BTCUSDT",
    interval: str = "1m",
    progress_callback: Optional[callable] = None,
    *,
    notional_usd: float = 1000.0,
    initial_capital_usd: float = 10000.0,
    fee_roundtrip_pct: float = 0.2,
) -> Tuple[List[Dict], Dict[str, Any]]:
    """
    تشغيل محاكاة التداول على الشموع التاريخية.
    candles: قائمة (open, high, low, close, volume)
    config: إعدادات من load_config() (tp_type, tp_value, sl_type, sl_value, bot_confidence_min, إلخ)
    notional_usd: حجم كل صفقة بالدولار (لعرض الربح/الخسارة بالدولار والرسوم).
    initial_capital_usd: رصيد بداية المحاكاة لمنحنى الرصيد وأقصى تراجع.
    fee_roundtrip_pct: رسوم ذهاب وإياب كنسبة من حجم الصفقة (مثلاً 0.2 = 0.2٪ لكل صفقة مغلقة).
    يُرجع: (قائمة صفقات، ملخص إحصائي)
    """
    from websocket_manager import FrameStream

    if len(candles) < 60:
        return [], {"error": "شموع غير كافية (أدنى 60)"}

    notional_usd = max(1.0, float(notional_usd))
    initial_capital_usd = max(1.0, float(initial_capital_usd))
    fee_roundtrip_pct = max(0.0, float(fee_roundtrip_pct))

    trades = []
    position = None  # {"entry_idx", "entry_price", "entry_close", "tp", "sl", "trailing_high"}
    stream = FrameStream(symbol.lower(), interval)
    ind_result = [None]
    info_result = [None]

    def capture_ind(x):
        ind_result[0] = x

    def capture_info(x):
        info_result[0] = x

    stream.on_indicators = capture_ind
    stream.on_market_info = capture_info

    tp_type = config.get("tp_type", "percent")
    tp_value = float(config.get("tp_value", 2.0))
    sl_type = config.get("sl_type", "percent")
    sl_value = float(config.get("sl_value", -1.0))
    confidence_min = int(config.get("bot_confidence_min", 60))
    trailing_stop_pct = float(config.get("trailing_stop_pct", 3.0) or 0)
    trailing_min_profit_pct = float(config.get("trailing_min_profit_pct", 0) or 0)

    n = len(candles)
    for i in range(60, n):
        if progress_callback and (i % 50 == 0 or i == n - 1):
            progress_callback(int(100 * (i - 60) / max(1, n - 60)))

        window = candles[: i + 1]
        stream.candles = [tuple(c) for c in window]
        ind_result[0] = None
        info_result[0] = None
        stream.compute_market_info()
        stream.compute_indicators()
        ind, info = ind_result[0], info_result[0]
        if not ind or not info:
            continue

        close = float(candles[i][3])
        high = float(candles[i][1])
        low = float(candles[i][2])

        # التحقق من TP/SL على الشمعة الحالية إذا كان هناك مركز مفتوح
        if position is not None:
            entry = position["entry_price"]
            tp = position["tp"]
            sl = position["sl"]
            exit_price = None
            exit_reason = None

            if tp is not None and high >= tp:
                exit_price = tp
                exit_reason = "TP"
            elif sl is not None and low <= sl:
                exit_price = sl
                exit_reason = "SL"
            elif trailing_stop_pct and position.get("trailing_high") is not None:
                th = position["trailing_high"]
                if th > 0:
                    pnl_pct_cur = (th - entry) / entry * 100.0
                    if pnl_pct_cur >= trailing_min_profit_pct:
                        trail_price = th * (1 - trailing_stop_pct / 100.0)
                        if low <= trail_price:
                            exit_price = trail_price
                            exit_reason = "Trailing"

            if exit_price is not None:
                pnl_pct = (exit_price - entry) / entry * 100.0
                trades.append({
                    "entry_idx": position["entry_idx"],
                    "exit_idx": i,
                    "entry_price": entry,
                    "exit_price": exit_price,
                    "pnl_pct": round(pnl_pct, 4),
                    "exit_reason": exit_reason,
                })
                position = None
                continue

            position["trailing_high"] = max(position.get("trailing_high") or high, high)

        if position is not None:
            continue
        if len([t for t in trades if t.get("exit_idx") == i]) > 0:
            continue

        rec, confidence = AIPanel.get_recommendation(ind, info, config)
        if confidence < confidence_min:
            continue
        if rec != "BUY":
            continue

        entry_price = close
        if tp_type == "percent":
            tp = entry_price * (1 + tp_value / 100.0)
        else:
            tp = float(tp_value) if tp_value > 0 else None
        if sl_type == "percent":
            sl = entry_price * (1 + sl_value / 100.0) if sl_value < 0 else None
        else:
            sl = float(sl_value) if sl_value > 0 else None

        position = {
            "entry_idx": i,
            "entry_price": entry_price,
            "entry_close": close,
            "tp": tp,
            "sl": sl,
            "trailing_high": high,
        }

    if position is not None:
        exit_price = float(candles[-1][3])
        entry = position["entry_price"]
        pnl_pct = (exit_price - entry) / entry * 100.0
        trades.append({
            "entry_idx": position["entry_idx"],
            "exit_idx": n - 1,
            "entry_price": entry,
            "exit_price": exit_price,
            "pnl_pct": round(pnl_pct, 4),
            "exit_reason": "End",
        })

    _apply_trade_usd_fees(trades, notional_usd, fee_roundtrip_pct)
    summary = _build_extended_summary(
        trades,
        initial_capital_usd=initial_capital_usd,
        notional_usd=notional_usd,
        fee_roundtrip_pct=fee_roundtrip_pct,
        symbol=symbol,
        interval=interval,
        candles_count=n,
    )
    return trades, summary
