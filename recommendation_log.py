# recommendation_log.py — تسجيل توصيات الذكاء الاصطناعي ونتائج الصفقات (لتحسين المعايير لاحقاً)
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime

log = logging.getLogger("trading.recommendation_log")
_AUTO_RETRAIN_LOCK = threading.Lock()
_AUTO_RETRAIN_RUNNING = False


def _data_path():
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
    folder = os.path.join(base, "CryptoTrading")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "ai_training_data.json")


def _load_data():
    path = _data_path()
    if not os.path.isfile(path):
        return {"outcomes": [], "recommendations": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {"outcomes": [], "recommendations": []}
        data.setdefault("outcomes", [])
        data.setdefault("recommendations", [])
        return data
    except Exception as e:
        log.warning("Could not load AI training data: %s", e)
        return {"outcomes": [], "recommendations": []}


def _save_data(data: dict):
    path = _data_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.warning("Could not save AI training data: %s", e)


def log_recommendation(symbol: str, recommendation: str, confidence: float, price: float, executed: bool = False):
    """
    تسجيل كل توصية صادرة عن الذكاء (BUY/SELL/WAIT).
    يُستخدم لتحليل تكرار التوصيات ونسبة التنفيذ لاحقاً.
    """
    if recommendation not in ("BUY", "SELL", "WAIT"):
        return
    data = _load_data()
    recs = data["recommendations"]
    recs.append({
        "time": datetime.utcnow().isoformat() + "Z",
        "symbol": str(symbol).upper(),
        "recommendation": recommendation,
        "confidence": round(float(confidence), 2),
        "price": float(price),
        "executed": bool(executed),
    })
    if len(recs) > 2000:
        data["recommendations"] = recs[-1500:]
    else:
        data["recommendations"] = recs
    _save_data(data)


def _serialize_indicators(indicators: dict, market_info: dict) -> tuple[dict, dict]:
    """تحويل المؤشرات ومعلومات السوق إلى dict قابلة للحفظ (أرقام ونصوص فقط)."""
    ind = {}
    if isinstance(indicators, dict):
        for k, v in indicators.items():
            try:
                if isinstance(v, (int, float)):
                    ind[k] = round(float(v), 6)
                elif isinstance(v, str):
                    ind[k] = v
            except (TypeError, ValueError):
                pass
    info = {}
    if isinstance(market_info, dict):
        for k, v in market_info.items():
            try:
                if isinstance(v, (int, float)):
                    info[k] = round(float(v), 6)
                elif isinstance(v, str):
                    info[k] = v
            except (TypeError, ValueError):
                pass
    return ind, info


def record_bot_buy(symbol: str, entry_price: float, quantity: float, confidence: float, indicators: dict = None, market_info: dict = None):
    """
    تسجيل تنفيذ شراء من البوت مع لقطة المؤشرات عند الدخول (للتعلّم لاحقاً).
    """
    ind, info = _serialize_indicators(indicators or {}, market_info or {})
    # حفظ لقطة إعدادات البروفايل/الحساسية عند الدخول لاستخدامها في تقارير التعلم اللاحقة.
    try:
        from config import load_config, get_circuit_breaker_config

        _cfg = load_config()
        _cbc = get_circuit_breaker_config(_cfg)
    except Exception:
        _cfg = {}
        try:
            from config import get_circuit_breaker_config

            _cbc = get_circuit_breaker_config(_cfg)
        except Exception:
            _cbc = {
                "enabled": True,
                "volatility_pct_max": 1.8,
                "adx_min": 18.0,
                "mtf_bias_floor": -0.9,
                "pause_minutes": 20,
                "mtf_rsi_threshold": 45.0,
            }
    # نسخ البروفايل داخل لقطة المؤشرات أيضاً حتى التقارير/الصفقات القديمة نسبياً تظهر حساسية/بروفايل واضحين.
    try:
        ind["bot_master_profile"] = str(_cfg.get("bot_master_profile", "aggressive") or "aggressive")
        ind["bot_entry_profile"] = str(_cfg.get("bot_entry_profile", "aggressive") or "aggressive")
        ind["indicator_speed_profile"] = str(_cfg.get("indicator_speed_profile", "balanced") or "balanced")
        ind["bot_trade_horizon"] = str(_cfg.get("bot_trade_horizon", "short") or "short")
    except Exception:
        pass
    try:
        from signal_engine.regime import detect_regime_from_snapshots

        _entry_regime = detect_regime_from_snapshots(ind, info, _cfg)
    except Exception:
        _entry_regime = "neutral"
    risk_snapshot = {
        "strategy_mode": str(_cfg.get("strategy_mode", "custom") or "custom"),
        "bot_confidence_min": float(_cfg.get("bot_confidence_min", 60) or 60),
        "bot_expected_value_gate_enabled": bool(_cfg.get("bot_expected_value_gate_enabled", True)),
        "bot_expected_value_min_pct": float(_cfg.get("bot_expected_value_min_pct", 0.03) or 0.03),
        "bot_expected_value_min_pct_trend_up": float(_cfg.get("bot_expected_value_min_pct_trend_up", 0.01) or 0.01),
        "bot_expected_value_min_pct_trend_down": float(_cfg.get("bot_expected_value_min_pct_trend_down", 0.08) or 0.08),
        "bot_expected_value_min_pct_range": float(_cfg.get("bot_expected_value_min_pct_range", 0.03) or 0.03),
        "bot_expected_value_min_pct_volatile": float(_cfg.get("bot_expected_value_min_pct_volatile", 0.12) or 0.12),
        "bot_circuit_breaker_enabled": bool(_cbc["enabled"]),
        "bot_cb_volatility_pct_max": float(_cbc["volatility_pct_max"]),
        "bot_cb_adx_min": float(_cbc["adx_min"]),
        "bot_cb_mtf_bias_floor": float(_cbc["mtf_bias_floor"]),
        "bot_cb_pause_minutes": int(_cbc["pause_minutes"]),
        "bot_cb_mtf_rsi_threshold": float(_cbc["mtf_rsi_threshold"]),
        "circuit_breaker_enabled": bool(_cbc["enabled"]),
        "circuit_breaker_volatility_pct_max": float(_cbc["volatility_pct_max"]),
        "circuit_breaker_adx_min": float(_cbc["adx_min"]),
        "circuit_breaker_mtf_bias_floor": float(_cbc["mtf_bias_floor"]),
        "circuit_breaker_pause_minutes": int(_cbc["pause_minutes"]),
        "circuit_breaker_mtf_rsi_threshold": float(_cbc["mtf_rsi_threshold"]),
        "bot_merge_composite": bool(_cfg.get("bot_merge_composite", False)),
        "composite_score_buy": float(_cfg.get("composite_score_buy", 12.0) or 12.0),
        "composite_score_strong": float(_cfg.get("composite_score_strong", 31.0) or 31.0),
        "composite_score_mid": float(_cfg.get("composite_score_mid", 21.0) or 21.0),
        "composite_adx_for_di": float(_cfg.get("composite_adx_for_di", 20.0) or 20.0),
        "bot_buy_require_early_bounce_15m": bool(_cfg.get("bot_buy_require_early_bounce_15m", False)),
        "bot_buy_bounce_use_rsi": bool(_cfg.get("bot_buy_bounce_use_rsi", True)),
        "bot_buy_bounce_context_rsi_max": float(_cfg.get("bot_buy_bounce_context_rsi_max", 52.0) or 52.0),
        "bot_buy_bounce_use_vwap": bool(_cfg.get("bot_buy_bounce_use_vwap", True)),
        "bot_buy_bounce_vwap_max_ratio": float(_cfg.get("bot_buy_bounce_vwap_max_ratio", 1.006) or 1.006),
        "bot_buy_bounce_use_stoch": bool(_cfg.get("bot_buy_bounce_use_stoch", True)),
        "bot_buy_bounce_stoch_k_max": float(_cfg.get("bot_buy_bounce_stoch_k_max", 58.0) or 58.0),
        "bot_buy_bounce_use_adx": bool(_cfg.get("bot_buy_bounce_use_adx", False)),
        "bot_buy_bounce_adx_min": float(_cfg.get("bot_buy_bounce_adx_min", 14.0) or 14.0),
        "bot_buy_bounce_use_macd": bool(_cfg.get("bot_buy_bounce_use_macd", True)),
        "bot_buy_bounce_macd_diff_min": float(_cfg.get("bot_buy_bounce_macd_diff_min", -0.025) or -0.025),
        "max_trades_per_day": int(_cfg.get("max_trades_per_day", 0) or 0),
        "max_trades_per_symbol": int(_cfg.get("max_trades_per_symbol", 0) or 0),
        "portfolio_max_exposure_usdt": float(_cfg.get("portfolio_max_exposure_usdt", 0) or 0),
        "bot_same_symbol_buy_min_interval_min": int(_cfg.get("bot_same_symbol_buy_min_interval_min", 1) or 1),
    }
    data = _load_data()
    data["outcomes"].append({
        "symbol": str(symbol).upper(),
        "side": "BUY",
        "entry_price": float(entry_price),
        "entry_time": datetime.utcnow().isoformat() + "Z",
        "quantity": float(quantity),
        "confidence": round(float(confidence), 2),
        "bot_master_profile": str(_cfg.get("bot_master_profile", "aggressive") or "aggressive"),
        "indicator_speed_profile": str(_cfg.get("indicator_speed_profile", "balanced") or "balanced"),
        "bot_entry_profile": str(_cfg.get("bot_entry_profile", "aggressive") or "aggressive"),
        "bot_trade_horizon": str(_cfg.get("bot_trade_horizon", "short") or "short"),
        "market_regime": str(_entry_regime),
        "risk_snapshot": risk_snapshot,
        "indicators": ind,
        "market_info": info,
        "exit_price": None,
        "exit_time": None,
        "pnl": None,
    })
    if len(data["outcomes"]) > 1000:
        data["outcomes"] = data["outcomes"][-800:]
    _save_data(data)


def record_bot_sell_outcome(
    symbol: str,
    exit_price: float,
    pnl: float,
    quantity_sold: float | None = None,
):
    """
    عند إغلاق بيع (من البوت): ربط النتيجة بصفقات الشراء المفتوحة لنفس الرمز.
    - صف شراء واحد مفتوح: يُسجَّل كامل الربح/الخسارة عليه.
    - أكثر من صف شراء مفتوح (شراء متتالي ثم بيع مجمّع): يُوزَّع الربح/الخسارة
      بما يتناسب مع كمية كل صف — حتى لا يبقى شراء «مفتوحاً» في بيانات التدريب
      بينما أُغلق فعلياً على المنصة.
    quantity_sold: اختياري لتوافق الاستدعاء؛ التوزيع يعتمد على كميات الشراء المخزّنة.
    """
    symbol = str(symbol).upper()
    data = _load_data()
    outcomes = data["outcomes"]
    open_idx = [
        i
        for i, o in enumerate(outcomes)
        if isinstance(o, dict)
        and o.get("symbol") == symbol
        and o.get("side") == "BUY"
        and o.get("exit_price") is None
    ]
    if not open_idx:
        log.debug("No open BUY outcome found for symbol %s to attach sell PnL", symbol)
        return

    ex = float(exit_price)
    total_pnl = float(pnl)
    now = datetime.utcnow().isoformat() + "Z"

    def _finalize() -> None:
        _save_data(data)
        try:
            from ml_model import try_auto_retrain

            try_auto_retrain()
        except Exception:
            pass

    if len(open_idx) == 1:
        o = outcomes[open_idx[0]]
        o["exit_price"] = round(ex, 8)
        o["exit_time"] = now
        o["pnl"] = round(total_pnl, 4)
        _finalize()
        return

    total_q = 0.0
    for i in open_idx:
        try:
            total_q += max(0.0, float(outcomes[i].get("quantity", 0) or 0))
        except (TypeError, ValueError):
            pass
    if total_q <= 1e-12:
        log.debug("Open BUY outcomes for %s have no quantity; skip PnL split", symbol)
        return

    for i in open_idx:
        o = outcomes[i]
        try:
            qo = max(0.0, float(o.get("quantity", 0) or 0))
        except (TypeError, ValueError):
            qo = 0.0
        share = total_pnl * (qo / total_q) if qo > 0 else 0.0
        o["exit_price"] = round(ex, 8)
        o["exit_time"] = now
        o["pnl"] = round(share, 4)
    _finalize()


def load_outcomes(limit: int = 500) -> list[dict]:
    """
    تحميل سجل نتائج الصفقات المنفذة من البوت (شراء + بيع مع ربح/خسارة).
    للاستخدام لاحقاً: حساب دقة التوصيات، متوسط الربح حسب نطاق الثقة، إلخ.
    """
    data = _load_data()
    outcomes = data.get("outcomes", [])
    closed = [o for o in outcomes if o.get("pnl") is not None]
    return closed[-limit:] if limit else closed


def load_recommendations(limit: int = 500) -> list[dict]:
    """تحميل آخر التوصيات المسجلة (لتحليل توزيع الثقة وعدد التنفيذ)."""
    data = _load_data()
    recs = data.get("recommendations", [])
    return recs[-limit:] if limit else recs


def get_training_stats() -> dict:
    """
    إحصائيات بسيطة من البيانات المسجلة (لعرضها لاحقاً في الإعدادات أو لوحة التحكم).
    """
    closed = load_outcomes(limit=200)
    if not closed:
        return {"count": 0, "total_pnl": 0.0, "win_rate": 0.0, "avg_confidence_win": 0.0, "avg_confidence_loss": 0.0}
    total_pnl = sum(float(o.get("pnl", 0)) for o in closed)
    wins = [o for o in closed if float(o.get("pnl", 0)) > 0]
    losses = [o for o in closed if float(o.get("pnl", 0)) < 0]
    win_rate = (len(wins) / len(closed)) * 100 if closed else 0
    avg_conf_win = sum(float(o.get("confidence", 0)) for o in wins) / len(wins) if wins else 0
    avg_conf_loss = sum(float(o.get("confidence", 0)) for o in losses) / len(losses) if losses else 0
    return {
        "count": len(closed),
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 1),
        "avg_confidence_win": round(avg_conf_win, 1),
        "avg_confidence_loss": round(avg_conf_loss, 1),
    }


# أقل عدد صفقات مغلقة قبل تطبيق التعلّم التلقائي
MIN_TRADES_FOR_LEARNING = 10
# أقصى تغيير في حد الثقة في كل خطوة (حتى لا يقفز الإعداد دفعة واحدة)
LEARNING_STEP = 2


def suggest_confidence_from_outcomes(current_min: int, stats: dict) -> int:
    """
    اقتراح حد ثقة جديد من نتائج الصفقات (تعلّم بسيط).
    - إذا نسبة الربح منخفضة (< 40%): نرفع الحد (أقل تنفيذ، أوضح إشارات).
    - إذا نسبة الربح جيدة (> 58%): نخفّض الحد قليلاً (مزيد من الفرص).
    """
    count = stats.get("count", 0)
    win_rate = stats.get("win_rate", 0.0)
    if count < MIN_TRADES_FOR_LEARNING:
        return current_min
    current = max(30, min(95, int(current_min)))
    if win_rate < 40.0:
        return min(95, current + LEARNING_STEP)
    if win_rate > 58.0:
        return max(30, current - LEARNING_STEP)
    return current


def suggest_score_threshold(stats: dict) -> int | None:
    """
    اقتراح عتبة النقاط (ai_score_min) من نتائج الصفقات: إن كانت الخسائر كثيرة نرفع العتبة.
    """
    count = stats.get("count", 0)
    win_rate = stats.get("win_rate", 0.0)
    if count < MIN_TRADES_FOR_LEARNING:
        return None
    if win_rate < 38.0:
        return 5
    if win_rate < 45.0:
        return 4
    if win_rate > 60.0:
        return 3
    return None


def apply_learning_step() -> bool:
    """
    خطوة تعلّم واحدة: تحديث حد الثقة وعتبة النقاط، وإعادة تدريب النموذج إن أمكن.
    تُستدعى بعد كل إغلاق صفقة (بيع). تُرجع True إذا تم تغيير أي إعداد.
    """
    try:
        from config import load_config, save_config, DEFAULTS
    except ImportError:
        return False
    stats = get_training_stats()
    count = stats.get("count", 0)
    if count < MIN_TRADES_FOR_LEARNING:
        return False
    cfg = load_config()
    changed = False

    current_conf = int(cfg.get("bot_confidence_min", DEFAULTS.get("bot_confidence_min", 60)))
    suggested_conf = suggest_confidence_from_outcomes(current_conf, stats)
    if suggested_conf != current_conf:
        cfg["bot_confidence_min"] = suggested_conf
        changed = True
        log.info("Learning: bot_confidence_min %s -> %s (win_rate=%.1f%%, trades=%s)", current_conf, suggested_conf, stats.get("win_rate", 0), count)

    suggested_score = suggest_score_threshold(stats)
    if suggested_score is not None:
        current_score = int(cfg.get("ai_score_min", DEFAULTS.get("ai_score_min", 4)))
        if suggested_score != current_score:
            cfg["ai_score_min"] = suggested_score
            changed = True
            log.info("Learning: ai_score_min %s -> %s", current_score, suggested_score)

    if changed:
        save_config(cfg)

    if count >= 25:
        _trigger_auto_retrain_async()

    return changed


def _trigger_auto_retrain_async() -> None:
    """
    تشغيل إعادة التدريب التلقائية بخيط خلفي لتجنب تجميد واجهة PyQt.
    """
    global _AUTO_RETRAIN_RUNNING
    with _AUTO_RETRAIN_LOCK:
        if _AUTO_RETRAIN_RUNNING:
            return
        _AUTO_RETRAIN_RUNNING = True

    def _job():
        global _AUTO_RETRAIN_RUNNING
        try:
            from ml_model import try_auto_retrain
            try_auto_retrain()
        except Exception as e:
            log.debug("ML async auto-retrain skipped: %s", e)
        finally:
            with _AUTO_RETRAIN_LOCK:
                _AUTO_RETRAIN_RUNNING = False

    th = threading.Thread(target=_job, name="ml-auto-retrain", daemon=True)
    th.start()
