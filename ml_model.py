# ml_model.py — نموذج تعلّم آلي للتنبؤ باحتمال نجاح الصفقة (لدمجه مع التوصية القائمة على القواعد)
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

log = logging.getLogger("trading.ml")

# أقل عدد صفقات مغلقة لتدريب النموذج
MIN_TRADES_FOR_TRAINING = 25

# أسماء الميزات المستخدمة في التدريب والتنبؤ
FEATURE_KEYS = [
    "rsi_norm", "macd_norm", "volume_strength", "volatility_norm", "trend_up",
    "adx_norm", "vwap_above", "atr_pct", "stoch_rsi_k_norm",
    "dist_pivot_norm", "dist_s1_norm", "dist_r1_norm", "candle_score_norm",
    # انحياز أطر متعددة، تقلب %، اتجاه هابط، ضغط هابط من RSI — يساعد على تمييز بيئات الهبوط
    "mtf_bias_norm", "volatility_pct_norm", "trend_down", "rsi_bearish_pressure",
    # بروفايل البوت + نظام السوق (متوافق مع signal_engine.regime عند التسجيل)
    "profile_master_ord",
    "profile_speed_ord",
    "profile_entry_ord",
    "profile_horizon_ord",
    "regime_trend_up",
    "regime_trend_down",
    "regime_range",
    "regime_neutral",
]


def _encode_master_profile(s: str | None) -> float:
    # بروفايل رئيسي واحد في التطبيق: هجومي
    return 1.0


def _encode_speed_profile(s: str | None) -> float:
    m = (s or "balanced").strip().lower()
    if m in ("conservative", "slow"):
        return 0.0
    if m in ("fast", "aggressive"):
        return 1.0
    if m == "standard":
        return 0.5
    return 0.5


def _encode_entry_profile(s: str | None) -> float:
    _ = s
    return 1.0


def _encode_trade_horizon(s: str | None) -> float:
    return 1.0 if (s or "short").strip().lower() == "swing" else 0.0


def _regime_str_to_one_hot(regime: str | None) -> list[float]:
    r = (regime or "neutral").strip().lower()
    return [
        1.0 if r == "trend_up" else 0.0,
        1.0 if r == "trend_down" else 0.0,
        1.0 if r == "range" else 0.0,
        1.0 if r == "neutral" else 0.0,
    ]


def _resolve_market_regime_str(
    indicators: dict,
    market_info: dict,
    cfg: dict | None,
    stored: str | None,
) -> str:
    if stored and str(stored).strip():
        return str(stored).strip().lower()
    try:
        from signal_engine.regime import detect_regime_from_snapshots

        return detect_regime_from_snapshots(indicators, market_info, cfg)
    except Exception:
        return "neutral"


def _extract_profile_tail(
    indicators: dict,
    market_info: dict,
    cfg: dict | None,
    *,
    outcome_meta: dict | None = None,
) -> list[float]:
    if outcome_meta:
        mp = outcome_meta.get("bot_master_profile")
        sp = outcome_meta.get("indicator_speed_profile")
        ep = outcome_meta.get("bot_entry_profile")
        hz = outcome_meta.get("bot_trade_horizon")
        mr = outcome_meta.get("market_regime")
    else:
        c = cfg or {}
        mp = c.get("bot_master_profile")
        sp = c.get("indicator_speed_profile")
        ep = c.get("bot_entry_profile")
        hz = c.get("bot_trade_horizon")
        mr = None
    reg = _resolve_market_regime_str(indicators, market_info, cfg, mr)
    return [
        _encode_master_profile(str(mp) if mp is not None else None),
        _encode_speed_profile(str(sp) if sp is not None else None),
        _encode_entry_profile(str(ep) if ep is not None else None),
        _encode_trade_horizon(str(hz) if hz is not None else None),
        *_regime_str_to_one_hot(reg),
    ]


def _model_path():
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
    folder = os.path.join(base, "CryptoTrading")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "ml_model.pkl")


def _meta_path():
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
    folder = os.path.join(base, "CryptoTrading")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "ml_model_meta.json")


def _registry_path():
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
    folder = os.path.join(base, "CryptoTrading")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "ml_model_registry.json")


# عدد الصفقات المغلقة الجديدة قبل إعادة التدريب التلقائي
AUTO_RETRAIN_EVERY_N_OUTCOMES = 25


def _extract_features(
    indicators: dict,
    market_info: dict,
    *,
    cfg: dict | None = None,
    outcome_meta: dict | None = None,
) -> list[float]:
    """
    استخراج متجه ميزات مطبّعة (لتستقر تدريب النموذج).
    RSI و StochRSI في [0,1]، ATR كنسبة من السعر، إلخ.
    مع ذيل بروفايل/أفق/نظام سوق (من outcome عند التدريب أو من cfg عند التنبؤ المباشر).
    """
    ind = indicators or {}
    info = market_info or {}
    close = float(ind.get("close") or 0)
    rsi = float(ind.get("rsi", 50))
    rsi_norm = rsi / 100.0 if 0 <= rsi <= 100 else 0.5
    macd = float(ind.get("macd", 0))
    signal = float(ind.get("signal", 0))
    macd_diff = macd - signal
    macd_norm = max(-1.0, min(1.0, macd_diff * 10.0))
    vol = max(0.0, min(3.0, float(info.get("volume_strength", 1))))
    volatility = float(info.get("volatility", 0))
    volatility_norm = min(5.0, (volatility / (close + 1e-9)) * 100.0) if close > 0 else 0.0
    trend = (info.get("trend") or "").upper()
    trend_up = 1.0 if trend == "UP" else 0.0
    adx = float(ind.get("adx14", 0))
    adx_norm = min(1.0, adx / 60.0)
    vwap = float(ind.get("vwap", 0) or 0)
    vwap_above = 1.0 if (close > 0 and vwap > 0 and close >= vwap) else 0.0
    atr = float(ind.get("atr14", 0) or 0)
    atr_pct = min(10.0, (atr / (close + 1e-9)) * 100.0) if close > 0 else 0.0
    st_k = float(ind.get("stoch_rsi_k", 50) or 50)
    stoch_rsi_k_norm = st_k / 100.0 if 0 <= st_k <= 100 else 0.5
    # بُعد السعر من المحور و S1 و R1 (نسبة من السعر، محدود بين -1 و 1)
    pivot = float(ind.get("pivot", 0) or 0)
    s1 = float(ind.get("pivot_s1", 0) or 0)
    r1 = float(ind.get("pivot_r1", 0) or 0)
    if close > 0:
        dist_pivot = (close - pivot) / close if pivot > 0 else 0.0
        dist_s1 = (close - s1) / close if s1 > 0 else 0.0
        dist_r1 = (close - r1) / close if r1 > 0 else 0.0
    else:
        dist_pivot = dist_s1 = dist_r1 = 0.0
    dist_pivot_norm = max(-1.0, min(1.0, dist_pivot * 20.0))
    dist_s1_norm = max(-1.0, min(1.0, dist_s1 * 20.0))
    dist_r1_norm = max(-1.0, min(1.0, dist_r1 * 20.0))
    # قوة نمط الشموع [-1, 1] من candle_pattern_score إن وُجد
    candle_score = float(ind.get("candle_pattern_score", 0) or 0)
    candle_score_norm = max(-1.0, min(1.0, candle_score / 5.0))
    mtf_bias = float(ind.get("mtf_bias", 0) or 0)
    mtf_bias_norm = max(-1.0, min(1.0, mtf_bias / 2.0))
    vol_pct = float(ind.get("volatility_pct", 0) or info.get("volatility_pct", 0) or 0)
    volatility_pct_norm = min(1.0, max(0.0, vol_pct / 2.5))
    trend_down = 1.0 if (info.get("trend") or "").upper() == "DOWN" else 0.0
    if rsi < 50.0:
        rsi_bearish_pressure = (50.0 - rsi) / 50.0
    else:
        rsi_bearish_pressure = 0.0
    base = [
        rsi_norm,
        macd_norm,
        vol,
        volatility_norm,
        trend_up,
        adx_norm,
        vwap_above,
        atr_pct,
        stoch_rsi_k_norm,
        dist_pivot_norm,
        dist_s1_norm,
        dist_r1_norm,
        candle_score_norm,
        mtf_bias_norm,
        volatility_pct_norm,
        trend_down,
        rsi_bearish_pressure,
    ]
    tail = _extract_profile_tail(ind, info, cfg, outcome_meta=outcome_meta)
    return base + tail


def _classify_regime(indicators: dict, market_info: dict) -> str:
    """
    تصنيف مبسّط لحالة السوق لتجميع الصفقات المشابهة:
    trend_up / trend_down / range / volatile
    """
    ind = indicators or {}
    info = market_info or {}
    adx = float(ind.get("adx14", 0) or 0)
    mtf = float(ind.get("mtf_bias", 0) or 0)
    vol_pct = float(ind.get("volatility_pct", 0) or info.get("volatility_pct", 0) or 0)
    trend = str(info.get("trend", "") or "").upper()
    if vol_pct >= 1.3 and adx < 18:
        return "volatile"
    if adx >= 22:
        if mtf >= 0.35 or trend == "UP":
            return "trend_up"
        if mtf <= -0.35 or trend == "DOWN":
            return "trend_down"
    return "range"


def _parse_iso_time(v: str) -> datetime | None:
    s = str(v or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def build_training_dataset(limit: int = 1200) -> tuple[list[list[float]], list[int], list[datetime], list[float]]:
    """
    بناء مجموعة تدريب من الصفقات المغلقة: ميزات عند الدخول + التسمية (1=ربح، 0=خسارة).
    يُرجع (X, y, t, row_weight) — row_weight يضخّم صفوف trend_down عند تفعيل الإعداد.
    """
    from recommendation_log import load_outcomes

    try:
        from config import load_config

        _cfg_ds = load_config()
    except Exception:
        _cfg_ds = {}
    boost_on = bool(_cfg_ds.get("ml_train_trend_down_boost_enabled", True))
    td_mult = max(1.0, float(_cfg_ds.get("ml_train_trend_down_weight_mult", 1.35) or 1.35))

    closed = load_outcomes(limit=limit)
    X, y, t, w_row = [], [], [], []
    for o in closed:
        ind = o.get("indicators") or o.get("entry_indicators") or {}
        info = o.get("market_info") or o.get("entry_market_info") or {}
        if not ind and not info:
            continue
        pnl = float(o.get("pnl", 0))
        try:
            row = _extract_features(ind, info, outcome_meta=o)
            ts = _parse_iso_time(o.get("exit_time") or o.get("entry_time") or "")
            if ts is None:
                continue
            reg = _resolve_market_regime_str(ind, info, None, o.get("market_regime"))
            rw = td_mult if (boost_on and reg == "trend_down") else 1.0
            X.append(row)
            y.append(1 if pnl > 0 else 0)
            t.append(ts)
            w_row.append(rw)
        except (TypeError, ValueError):
            continue
    return X, y, t, w_row


def _build_outcome_rows(limit: int = 1200) -> list[tuple[datetime, list[float], int, float]]:
    """
    صفوف تدريب كاملة: (الوقت، الميزات، label، pnl_pct).
    pnl_pct = (pnl / (entry_price*qty)) * 100
    """
    from recommendation_log import load_outcomes

    closed = load_outcomes(limit=limit)
    rows: list[tuple[datetime, list[float], int, float]] = []
    for o in closed:
        try:
            ind = o.get("indicators") or o.get("entry_indicators") or {}
            info = o.get("market_info") or o.get("entry_market_info") or {}
            if not ind and not info:
                continue
            ts = _parse_iso_time(o.get("exit_time") or o.get("entry_time") or "")
            if ts is None:
                continue
            row = _extract_features(ind, info, outcome_meta=o)
            pnl = float(o.get("pnl", 0.0) or 0.0)
            ep = float(o.get("entry_price", 0.0) or 0.0)
            q = float(o.get("quantity", 0.0) or 0.0)
            notional = ep * q
            if notional <= 1e-9:
                continue
            pnl_pct = (pnl / notional) * 100.0
            y = 1 if pnl > 0 else 0
            rows.append((ts, row, y, pnl_pct))
        except Exception:
            continue
    rows.sort(key=lambda z: z[0])
    return rows


def _quality_check(y: list[int]) -> tuple[bool, str]:
    n = len(y)
    if n < MIN_TRADES_FOR_TRAINING:
        return False, f"عدد الصفقات المغلقة غير كافٍ ({n}). تحتاج {MIN_TRADES_FOR_TRAINING} صفقة على الأقل."
    pos = sum(1 for v in y if int(v) == 1)
    neg = n - pos
    if pos < 5 or neg < 5:
        return False, f"البيانات غير متوازنة للتدريب (wins={pos}, losses={neg})."
    return True, ""


def _time_split(
    X: list[list[float]],
    y: list[int],
    t: list[datetime],
    w_row: list[float],
    *,
    train_ratio: float = 0.8,
) -> tuple[list[list[float]], list[int], list[list[float]], list[int], list[float], list[float]]:
    rows = sorted(zip(t, X, y, w_row), key=lambda z: z[0])
    split = max(1, min(len(rows) - 1, int(len(rows) * train_ratio)))
    train_rows = rows[:split]
    test_rows = rows[split:]
    X_tr = [r[1] for r in train_rows]
    y_tr = [r[2] for r in train_rows]
    w_tr = [r[3] for r in train_rows]
    X_te = [r[1] for r in test_rows]
    y_te = [r[2] for r in test_rows]
    w_te = [r[3] for r in test_rows]
    return X_tr, y_tr, X_te, y_te, w_tr, w_te


def _recency_train_weights(n_train: int, cfg: dict) -> list[float] | None:
    """أوزان عيّنات التدريب: الأحدث زمنياً (آخر صف في نافذة التدريب) أوزان أعلى."""
    if not bool(cfg.get("ml_train_recency_enabled", True)):
        return None
    if n_train <= 0:
        return None
    w_min = float(cfg.get("ml_train_recency_min_weight", 0.35) or 0.35)
    w_max = float(cfg.get("ml_train_recency_max_weight", 1.0) or 1.0)
    w_min = max(0.05, min(w_min, w_max))
    if n_train == 1:
        return [w_max]
    return [w_min + (w_max - w_min) * (i / (n_train - 1)) for i in range(n_train)]


def _evaluate_model(model, X_test: list[list[float]], y_test: list[int]) -> dict:
    from sklearn.metrics import accuracy_score, f1_score

    if not X_test:
        return {"accuracy": 0.0, "f1": 0.0, "score": 0.0, "test_size": 0}
    pred = model.predict(X_test)
    acc = float(accuracy_score(y_test, pred))
    try:
        f1 = float(f1_score(y_test, pred, zero_division=0))
    except Exception:
        f1 = 0.0
    score = (acc * 0.6) + (f1 * 0.4)
    return {
        "accuracy": round(acc, 4),
        "f1": round(f1, 4),
        "score": round(score, 4),
        "test_size": len(y_test),
    }


def _evaluate_walk_forward(
    rows: list[tuple[datetime, list[float], int, float]],
    *,
    cost_per_trade_pct: float = 0.08,
    train_min: int = 40,
    test_window: int = 10,
) -> dict:
    """
    Walk-forward: درّب على الماضي ثم اختبر على نافذة لاحقة بشكل متتابع.
    يحتسب جودة التصنيف + EV% بعد خصم تكلفة تقديرية لكل صفقة.
    """
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.metrics import accuracy_score, f1_score

    if len(rows) < (train_min + test_window):
        return {
            "folds": 0,
            "test_size": 0,
            "accuracy": 0.0,
            "f1": 0.0,
            "avg_net_ev_pct": 0.0,
            "cost_per_trade_pct": float(cost_per_trade_pct),
        }

    all_pred: list[int] = []
    all_true: list[int] = []
    net_pnl_pcts: list[float] = []
    folds = 0
    i = train_min
    n = len(rows)
    while i < n:
        end = min(n, i + test_window)
        train_rows = rows[:i]
        test_rows = rows[i:end]
        if len(train_rows) < train_min or not test_rows:
            break
        X_tr = [r[1] for r in train_rows]
        y_tr = [r[2] for r in train_rows]
        X_te = [r[1] for r in test_rows]
        y_te = [r[2] for r in test_rows]
        pnl_te = [r[3] for r in test_rows]

        model = RandomForestClassifier(n_estimators=50, max_depth=6, random_state=42)
        model.fit(X_tr, y_tr)
        pred = list(model.predict(X_te))
        all_pred.extend(int(v) for v in pred)
        all_true.extend(int(v) for v in y_te)
        for p_hat, pnl_pct in zip(pred, pnl_te):
            # نحاكي سياسة BUY-only: ندخل فقط إذا التوقع = ربح
            if int(p_hat) == 1:
                net_pnl_pcts.append(float(pnl_pct) - float(cost_per_trade_pct))
        folds += 1
        i = end

    if not all_true:
        return {
            "folds": folds,
            "test_size": 0,
            "accuracy": 0.0,
            "f1": 0.0,
            "avg_net_ev_pct": 0.0,
            "cost_per_trade_pct": float(cost_per_trade_pct),
        }

    acc = float(accuracy_score(all_true, all_pred))
    try:
        f1 = float(f1_score(all_true, all_pred, zero_division=0))
    except Exception:
        f1 = 0.0
    avg_net_ev = float(sum(net_pnl_pcts) / len(net_pnl_pcts)) if net_pnl_pcts else 0.0
    return {
        "folds": int(folds),
        "test_size": int(len(all_true)),
        "accuracy": round(acc, 4),
        "f1": round(f1, 4),
        "avg_net_ev_pct": round(avg_net_ev, 4),
        "cost_per_trade_pct": round(float(cost_per_trade_pct), 4),
    }


def _safe_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _window_metrics(items: list[dict]) -> dict:
    n = len(items)
    if n <= 0:
        return {"n": 0, "pnl_sum": 0.0, "win_rate": 0.0, "avg_pnl": 0.0}
    pnls = [_safe_float(x.get("pnl", 0.0), 0.0) for x in items]
    wins = sum(1 for p in pnls if p > 0)
    pnl_sum = sum(pnls)
    return {
        "n": n,
        "pnl_sum": float(pnl_sum),
        "win_rate": float((wins / n) * 100.0),
        "avg_pnl": float(pnl_sum / n),
    }


def _outcome_profile_labels(o: dict) -> tuple[str, str]:
    """بروفايل رئيسي + حساسية: من الحقول العليا أو من لقطة المؤشرات عند الدخول."""
    ind = o.get("indicators") if isinstance(o.get("indicators"), dict) else {}
    prof = o.get("bot_master_profile")
    if prof is None or str(prof).strip() == "":
        prof = ind.get("bot_master_profile")
    sens = o.get("indicator_speed_profile")
    if sens is None or str(sens).strip() == "":
        sens = ind.get("indicator_speed_profile")
    prof_s = str(prof or "").strip().lower() or "غير مسجّل"
    sens_s = str(sens or "").strip().lower() or "غير مسجّل"
    return prof_s, sens_s


def _profile_sensitivity_report(items: list[dict]) -> list[str]:
    from collections import defaultdict

    agg: dict[tuple[str, str], dict] = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
    for o in items:
        prof, sens = _outcome_profile_labels(o)
        k = (prof, sens)
        pnl = _safe_float(o.get("pnl", 0.0), 0.0)
        agg[k]["n"] += 1
        agg[k]["pnl"] += pnl
        if pnl > 0:
            agg[k]["wins"] += 1
    rows: list[tuple[int, float, str]] = []
    for (prof, sens), v in agg.items():
        n = int(v["n"] or 0)
        if n <= 0:
            continue
        wr = (float(v["wins"]) / n) * 100.0
        pnl = float(v["pnl"])
        rows.append(
            (
                n,
                pnl,
                f"- {prof}/{sens}: صفقات={n} | win={wr:.1f}% | pnl={pnl:+.2f}",
            )
        )
    rows.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [r[2] for r in rows[:8]]


def _risk_setting_impact_report(items: list[dict]) -> list[str]:
    """
    تحليل تأثير إعدادات المخاطر المسجلة مع كل صفقة.
    يعرض أفضل/أسوأ قيمة لكل إعداد عند وجود أكثر من قيمة في آخر نافذة.
    """
    from collections import defaultdict

    keys = [
        "strategy_mode",
        "bot_confidence_min",
        "bot_expected_value_gate_enabled",
        "bot_expected_value_min_pct",
        "bot_expected_value_min_pct_trend_up",
        "bot_expected_value_min_pct_trend_down",
        "bot_expected_value_min_pct_range",
        "bot_expected_value_min_pct_volatile",
        "bot_circuit_breaker_enabled",
        "bot_cb_volatility_pct_max",
        "bot_cb_adx_min",
        "bot_cb_mtf_bias_floor",
        "bot_cb_pause_minutes",
        "bot_cb_mtf_rsi_threshold",
        "circuit_breaker_enabled",
        "circuit_breaker_volatility_pct_max",
        "circuit_breaker_adx_min",
        "circuit_breaker_mtf_bias_floor",
        "circuit_breaker_mtf_rsi_threshold",
        "bot_merge_composite",
        "composite_score_buy",
        "composite_score_strong",
        "composite_score_mid",
        "composite_adx_for_di",
        "bot_buy_require_early_bounce_15m",
        "bot_buy_bounce_use_rsi",
        "bot_buy_bounce_context_rsi_max",
        "bot_buy_bounce_use_vwap",
        "bot_buy_bounce_vwap_max_ratio",
        "bot_buy_bounce_use_stoch",
        "bot_buy_bounce_stoch_k_max",
        "bot_buy_bounce_use_adx",
        "bot_buy_bounce_adx_min",
        "bot_buy_bounce_use_macd",
        "bot_buy_bounce_macd_diff_min",
    ]
    out: list[str] = []
    for k in keys:
        bucket = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
        for o in items:
            snap = o.get("risk_snapshot") if isinstance(o.get("risk_snapshot"), dict) else {}
            if not snap or k not in snap:
                continue
            v = snap.get(k)
            vk = str(v)
            pnl = _safe_float(o.get("pnl", 0.0), 0.0)
            bucket[vk]["n"] += 1
            bucket[vk]["pnl"] += pnl
            if pnl > 0:
                bucket[vk]["wins"] += 1
        if len(bucket) < 2:
            continue
        ranked: list[tuple[float, int, str]] = []
        for vk, m in bucket.items():
            n = int(m["n"] or 0)
            if n < 5:
                continue
            wr = (float(m["wins"]) / n) * 100.0
            pnl = float(m["pnl"])
            score = pnl + (wr * 0.15)
            ranked.append((score, n, f"{vk} | n={n} | win={wr:.1f}% | pnl={pnl:+.2f}"))
        if len(ranked) < 2:
            continue
        ranked.sort(key=lambda x: x[0], reverse=True)
        best = ranked[0][2]
        worst = ranked[-1][2]
        out.append(f"- {k}: الأفضل [{best}] | الأسوأ [{worst}]")
    return out[:22]


def _risk_setting_best_value_suggestions(items: list[dict]) -> tuple[dict, list[str]]:
    """
    استخراج اقتراحات قيم من إعدادات المخاطر نفسها (من النتائج الفعلية لآخر النافذة).
    نأخذ فقط الإعدادات التي لديها تنوع كافٍ وفرق واضح.
    """
    from collections import defaultdict

    candidate_keys = [
        "bot_confidence_min",
        "bot_expected_value_min_pct",
        "bot_buy_bounce_context_rsi_max",
        "bot_buy_bounce_vwap_max_ratio",
        "bot_buy_bounce_stoch_k_max",
        "bot_buy_bounce_adx_min",
        "bot_buy_bounce_macd_diff_min",
    ]
    patches: dict = {}
    notes: list[str] = []
    for k in candidate_keys:
        bucket = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
        for o in items:
            snap = o.get("risk_snapshot") if isinstance(o.get("risk_snapshot"), dict) else {}
            if not snap or k not in snap:
                continue
            v = snap.get(k)
            try:
                vk = float(v)
            except Exception:
                continue
            pnl = _safe_float(o.get("pnl", 0.0), 0.0)
            bucket[vk]["n"] += 1
            bucket[vk]["pnl"] += pnl
            if pnl > 0:
                bucket[vk]["wins"] += 1
        if len(bucket) < 2:
            continue
        ranked: list[tuple[float, float, int]] = []  # score, value, n
        for val, m in bucket.items():
            n = int(m["n"] or 0)
            if n < 6:
                continue
            wr = (float(m["wins"]) / n) * 100.0
            pnl = float(m["pnl"])
            score = pnl + (wr * 0.15)
            ranked.append((score, float(val), n))
        if len(ranked) < 2:
            continue
        ranked.sort(key=lambda x: x[0], reverse=True)
        best_score, best_val, _best_n = ranked[0]
        worst_score, worst_val, _worst_n = ranked[-1]
        # تجاهل الفروقات الضعيفة
        if (best_score - worst_score) < 2.0:
            continue
        # اقتراح آمن: تحريك محدود نحو الأفضل
        if k == "bot_confidence_min":
            patches[k] = int(max(30, min(95, round(best_val))))
        elif k == "bot_expected_value_min_pct":
            patches[k] = float(max(-1.0, min(5.0, round(best_val, 3))))
        elif k == "bot_buy_bounce_context_rsi_max":
            patches[k] = float(max(20.0, min(80.0, round(best_val, 1))))
        elif k == "bot_buy_bounce_vwap_max_ratio":
            patches[k] = float(max(0.98, min(1.05, round(best_val, 4))))
        elif k == "bot_buy_bounce_stoch_k_max":
            patches[k] = float(max(20.0, min(95.0, round(best_val, 1))))
        elif k == "bot_buy_bounce_adx_min":
            patches[k] = float(max(5.0, min(40.0, round(best_val, 1))))
        elif k == "bot_buy_bounce_macd_diff_min":
            patches[k] = float(max(-0.20, min(0.20, round(best_val, 4))))
        notes.append(f"- من سجل الإعدادات: {k} الأفضل كان قرب {best_val:g} (أسوأ قيمة ملحوظة {worst_val:g}).")
    return patches, notes


def _loss_pattern_hints(items: list[dict]) -> list[str]:
    """
    تلميحات تفصيلية من صفقات الخسارة — نفس تعريفات النِسَب في سطر «ملخص المؤشرات» بالتقرير.
    عتبات متوسطة (~28–35%) حتى تظهر اقتراحات لكل محور ظاهر في الملخص وليس VWAP فقط.
    """
    losses = [o for o in items if _safe_float(o.get("pnl", 0.0), 0.0) < 0]
    if not losses:
        return []
    n = len(losses)
    c_down = 0
    c_vwap_below = 0
    c_hot_rsi = 0
    c_high_vol = 0
    c_strong_down = 0
    c_adx_weak = 0
    c_macd_neg = 0
    c_hist_neg = 0
    c_stoch_hot = 0
    for o in losses:
        ind = o.get("indicators") if isinstance(o.get("indicators"), dict) else {}
        info = o.get("market_info") if isinstance(o.get("market_info"), dict) else {}
        trend = str(info.get("trend", "") or "").upper()
        close = _safe_float(ind.get("close", 0.0), 0.0)
        vwap = _safe_float(ind.get("vwap", 0.0), 0.0)
        rsi = _safe_float(ind.get("rsi", 50.0), 50.0)
        adx = _safe_float(ind.get("adx14", 0.0), 0.0)
        macd = _safe_float(ind.get("macd", 0.0), 0.0)
        signal = _safe_float(ind.get("signal", 0.0), 0.0)
        hist = _safe_float(ind.get("hist", 0.0), 0.0)
        st_k = _safe_float(ind.get("stoch_rsi_k", 50.0), 50.0)
        vol_pct = _safe_float(ind.get("volatility_pct", info.get("volatility_pct", 0.0)), 0.0)
        if trend == "DOWN":
            c_down += 1
        if close > 0 and vwap > 0 and close < vwap:
            c_vwap_below += 1
        if rsi >= 65:
            c_hot_rsi += 1
        if vol_pct >= 1.2:
            c_high_vol += 1
        if trend == "DOWN" and adx >= 22:
            c_strong_down += 1
        if adx > 0 and adx < 16:
            c_adx_weak += 1
        if (macd - signal) < 0:
            c_macd_neg += 1
        if hist < 0:
            c_hist_neg += 1
        if st_k >= 80:
            c_stoch_hot += 1
    hints: list[str] = []

    def _ratio(c: int) -> float:
        return (c / max(1, n)) * 100.0

    r_down = _ratio(c_down)
    r_sd = _ratio(c_strong_down)
    r_vwap = _ratio(c_vwap_below)
    r_rsi = _ratio(c_hot_rsi)
    r_vol = _ratio(c_high_vol)
    r_adx_w = _ratio(c_adx_weak)
    r_macd = _ratio(c_macd_neg)
    r_hist = _ratio(c_hist_neg)
    r_stoch = _ratio(c_stoch_hot)

    if r_down >= 55:
        hints.append("- أغلب الخسائر حدثت أثناء اتجاه هابط: قلّل الشراء العكسي وارفع حد الثقة في الهبوط.")
    if r_sd >= 40:
        hints.append("- نسبة ملحوظة في (DOWN + ADX قوي): فعّل/شدد مرشح منع BUY ضد الترند.")
    if r_vwap >= 55:
        hints.append("- كثير من الخسائر كانت والسعر أسفل VWAP: أضف شرط تأكيد قبل الدخول (مثلاً إغلاق فوق VWAP أو فلتر ارتداد VWAP).")
    if r_rsi >= 35:
        hints.append("- يوجد شراء عند RSI مرتفع نسبيًا (≥65): شدّد سقف RSI في فلتر الارتداد أو ارفع حد الثقة.")
    if r_vol >= 40:
        hints.append("- الخسائر تزداد مع التقلب العالي: خفّض الحجم أو ارفع عتبات الدخول وحد EV في الوضع المتقلب.")
    if r_adx_w >= 28:
        hints.append(
            "- نسبة واضحة من الخسائر مع ADX ضعيف (<16): فضّل تجنب التداول في المدى الضيق — فعّل ADX في فلتر الارتداد أو ارفع نقاط الطبقة الثانية."
        )
    if max(r_macd, r_hist) >= 28:
        hints.append(
            "- زخم سالب (MACD تحت الإشارة أو Histogram سالب) في كثير من الخسائر: شدّد شرط MACD في الارتداد أو انتظر انعكاسًا أو ارفع عتبة المركّب."
        )
    if r_stoch >= 25:
        hints.append(
            "- StochRSI مرتفع (≥80) في خسائر ملحوظة: اخفض سقف Stoch K في إعدادات الارتداد لتقليل شراء التشبع الزائف."
        )
    return hints


def _recent_degradation_analysis(*, window: int = 50) -> dict | None:
    from recommendation_log import load_outcomes

    all_closed = load_outcomes(limit=max(220, window * 4))
    if len(all_closed) < (window * 2):
        return None
    recent = all_closed[-window:]
    prev = all_closed[-(window * 2):-window]
    m_r = _window_metrics(recent)
    m_p = _window_metrics(prev)
    pnl_delta = float(m_r["pnl_sum"] - m_p["pnl_sum"])
    wr_delta = float(m_r["win_rate"] - m_p["win_rate"])
    losses = [o for o in recent if _safe_float(o.get("pnl", 0.0), 0.0) < 0]
    n_losses = len(losses)
    c_down = c_vwap_below = c_hot_rsi = c_high_vol = c_strong_down = 0
    c_adx_weak = c_macd_neg = c_hist_neg = c_stoch_hot = 0
    for o in losses:
        ind = o.get("indicators") if isinstance(o.get("indicators"), dict) else {}
        info = o.get("market_info") if isinstance(o.get("market_info"), dict) else {}
        trend = str(info.get("trend", "") or "").upper()
        close = _safe_float(ind.get("close", 0.0), 0.0)
        vwap = _safe_float(ind.get("vwap", 0.0), 0.0)
        rsi = _safe_float(ind.get("rsi", 50.0), 50.0)
        adx = _safe_float(ind.get("adx14", 0.0), 0.0)
        macd = _safe_float(ind.get("macd", 0.0), 0.0)
        signal = _safe_float(ind.get("signal", 0.0), 0.0)
        hist = _safe_float(ind.get("hist", 0.0), 0.0)
        st_k = _safe_float(ind.get("stoch_rsi_k", 50.0), 50.0)
        vol_pct = _safe_float(ind.get("volatility_pct", info.get("volatility_pct", 0.0)), 0.0)
        if trend == "DOWN":
            c_down += 1
        if close > 0 and vwap > 0 and close < vwap:
            c_vwap_below += 1
        if rsi >= 65:
            c_hot_rsi += 1
        if vol_pct >= 1.2:
            c_high_vol += 1
        if trend == "DOWN" and adx >= 22:
            c_strong_down += 1
        if adx > 0 and adx < 16:
            c_adx_weak += 1
        if (macd - signal) < 0:
            c_macd_neg += 1
        if hist < 0:
            c_hist_neg += 1
        if st_k >= 80:
            c_stoch_hot += 1

    def _ratio(c: int) -> float:
        return (c / max(1, n_losses)) * 100.0

    degraded = (pnl_delta < -0.5) or (wr_delta <= -5.0)
    return {
        "degraded": bool(degraded),
        "recent": m_r,
        "prev": m_p,
        "pnl_delta": pnl_delta,
        "wr_delta": wr_delta,
        "recent_items": recent,
        "n_losses": n_losses,
        "ratios": {
            "down": _ratio(c_down),
            "strong_down": _ratio(c_strong_down),
            "vwap_below": _ratio(c_vwap_below),
            "hot_rsi": _ratio(c_hot_rsi),
            "high_vol": _ratio(c_high_vol),
            "adx_weak": _ratio(c_adx_weak),
            "macd_neg": _ratio(c_macd_neg),
            "hist_neg": _ratio(c_hist_neg),
            "stoch_hot": _ratio(c_stoch_hot),
        },
    }


def build_recent_50_diagnostic_report(*, window: int = 50) -> str:
    """تقرير تشخيصي: آخر 50 صفقة مقابل الـ50 السابقة + تحليل بروفايل/حساسية."""
    a = _recent_degradation_analysis(window=window)
    if not a:
        return ""
    if not bool(a.get("degraded", False)):
        return ""
    m_r = a["recent"]
    m_p = a["prev"]
    pnl_delta = float(a["pnl_delta"])
    wr_delta = float(a["wr_delta"])
    recent = a["recent_items"]
    lines: list[str] = []
    lines.append("— تقرير آخر 50 صفقة (مقارنة بالـ50 السابقة) —")
    lines.append(
        f"الحالية: pnl={m_r['pnl_sum']:+.2f} | win={m_r['win_rate']:.1f}% | avg={m_r['avg_pnl']:+.2f}"
    )
    lines.append(
        f"السابقة: pnl={m_p['pnl_sum']:+.2f} | win={m_p['win_rate']:.1f}% | avg={m_p['avg_pnl']:+.2f}"
    )
    lines.append(f"الفارق: pnl={pnl_delta:+.2f} | win={wr_delta:+.1f}%")
    ratios = a.get("ratios", {}) if isinstance(a.get("ratios"), dict) else {}
    if ratios:
        lines.append(
            "ملخص المؤشرات في الصفقات الخاسرة (آخر 50): "
            f"VWAP-={float(ratios.get('vwap_below', 0.0)):.1f}% | "
            f"RSI hot={float(ratios.get('hot_rsi', 0.0)):.1f}% | "
            f"ADX weak={float(ratios.get('adx_weak', 0.0)):.1f}% | "
            f"MACD-={float(ratios.get('macd_neg', 0.0)):.1f}% | "
            f"Hist-={float(ratios.get('hist_neg', 0.0)):.1f}% | "
            f"Stoch hot={float(ratios.get('stoch_hot', 0.0)):.1f}%"
        )
    ps = _profile_sensitivity_report(recent)
    if ps:
        lines.append("تفصيل حسب البروفايل/الحساسية (آخر 50):")
        lines.extend(ps)
    rs = _risk_setting_impact_report(recent)
    if rs:
        lines.append("تأثير إعدادات المخاطر (من الصفقات المسجلة):")
        lines.extend(rs)
    hints = _loss_pattern_hints(recent)
    if hints:
        lines.append("الخلل المحتمل والتعديل المقترح (لكل محور يتجاوز عتبة في الخسائر):")
        lines.extend(hints[:14])
    else:
        lines.append("الخلل المحتمل غير واضح من الميزات المسجلة؛ راجع توقيت الدخول والخروج وحدود المخاطر.")
    return "\n".join(lines)


# مفاتيح تُعدّ «فلتر الارتداد» — عند تعديلها في المراجعة الشاملة نفعّل الضبط اللحظي إن كان معطّلاً
_BOUNCE_RELATED_PATCH_KEYS = frozenset(
    {
        "bot_buy_bounce_use_rsi",
        "bot_buy_bounce_context_rsi_max",
        "bot_buy_bounce_use_vwap",
        "bot_buy_bounce_vwap_max_ratio",
        "bot_buy_bounce_use_stoch",
        "bot_buy_bounce_stoch_k_max",
        "bot_buy_bounce_use_adx",
        "bot_buy_bounce_adx_min",
        "bot_buy_bounce_use_macd",
        "bot_buy_bounce_macd_diff_min",
    }
)


def _patches_from_trade_analysis(
    a: dict,
    cfg: dict,
    *,
    bounce_tuning_allowed: bool,
) -> tuple[dict, list[str]]:
    """اقتراحات رقمية من نافذة الصفقات + نِسَب الخسائر (يُفصَّل bounce حسب bounce_tuning_allowed)."""
    ratios = a.get("ratios", {}) if isinstance(a.get("ratios"), dict) else {}
    patches: dict = {}
    notes: list[str] = []
    # ثقة البوت
    if float(a.get("wr_delta", 0.0)) <= -5.0 or float(a.get("pnl_delta", 0.0)) < -1.0:
        patches["bot_confidence_min"] = 65
        notes.append("- رفع حد الثقة إلى 65 لتقليل الإشارات الضعيفة.")
    # مرشحات بيئة هابطة
    if float(ratios.get("strong_down", 0.0)) >= 40.0:
        patches["ai_use_regime_router"] = True
        notes.append("- تفعيل موجّه النظام (Regime Router) بسبب خسائر في اتجاه هابط قوي.")
        ev_td = float(cfg.get("bot_expected_value_min_pct_trend_down", 0.08) or 0.08)
        if ev_td < 0.12:
            patches["bot_expected_value_min_pct_trend_down"] = round(min(0.2, ev_td + 0.02), 3)
            notes.append("- رفع حد EV قليلاً في ترند هابط (من نمط الخسائر).")
    if float(ratios.get("vwap_below", 0.0)) >= 55.0:
        if bounce_tuning_allowed:
            patches["bot_buy_bounce_use_vwap"] = True
            patches["bot_buy_bounce_vwap_max_ratio"] = 1.001
            notes.append("- تشديد شرط VWAP داخل فلتر الارتداد (السماح فقط قرب/تحت VWAP بشكل أدق).")
        else:
            notes.append("- (يُفضّل تفعيل الضبط اللحظي للارتداد أو المراجعة الشاملة لتطبيق VWAP.)")
    if float(ratios.get("high_vol", 0.0)) >= 40.0:
        patches["bot_expected_value_min_pct_volatile"] = 0.10
        notes.append("- رفع حد EV في السوق المتقلب إلى 0.10%.")
    if float(ratios.get("hot_rsi", 0.0)) >= 35.0:
        patches["bot_confidence_min"] = max(int(patches.get("bot_confidence_min", 60)), 67)
        if bounce_tuning_allowed:
            patches["bot_buy_bounce_use_rsi"] = True
            patches["bot_buy_bounce_context_rsi_max"] = 48.0
            notes.append("- رفع إضافي للثقة (67) + تشديد سياق RSI لتقليل دخولات RSI الساخنة.")
        else:
            notes.append("- رفع الثقة إلى 67 (ضبط RSI Bounce يحتاج الضبط اللحظي أو مراجعة شاملة).")
    if float(ratios.get("stoch_hot", 0.0)) >= 28.0:
        if bounce_tuning_allowed:
            patches["bot_buy_bounce_use_stoch"] = True
            patches["bot_buy_bounce_stoch_k_max"] = 52.0
            notes.append("- تشديد سقف StochRSI K إلى 52 لمنع الشراء عند تشبع مرتفع.")
    if float(ratios.get("adx_weak", 0.0)) >= 28.0:
        if bounce_tuning_allowed:
            patches["bot_buy_bounce_use_adx"] = True
            patches["bot_buy_bounce_adx_min"] = 16.0
            notes.append("- تشديد ADX داخل فلتر الارتداد لأن الخسائر تكثر مع ADX ضعيف.")
        else:
            notes.append("- (يُفضّل تفعيل الضبط اللحظي للارتداد لتشديد ADX عند ضعف الاتجاه.)")
    if float(ratios.get("macd_neg", 0.0)) >= 30.0 or float(ratios.get("hist_neg", 0.0)) >= 30.0:
        if bounce_tuning_allowed:
            patches["bot_buy_bounce_use_macd"] = True
            patches["bot_buy_bounce_macd_diff_min"] = -0.015
        merge_on = bool(cfg.get("bot_merge_composite", False))
        if merge_on:
            patches["composite_score_buy"] = 14.0
        patches["bot_confidence_min"] = max(int(patches.get("bot_confidence_min", 60)), 68)
        if bounce_tuning_allowed:
            if merge_on:
                notes.append("- تشديد عتبة المركّب والحد الأدنى للثقة عند بيئة MACD/Histogram سلبية.")
            else:
                notes.append(
                    "- رفع الحد الأدنى للثقة عند بيئة MACD/Histogram سلبية؛ "
                    "«دمج المركّب» معطّل فلم يُقترح تعديل composite_score_buy."
                )
        else:
            if merge_on:
                notes.append("- تشديد المركّب والثقة (MACD Bounce يحتاج الضبط اللحظي أو مراجعة شاملة).")
            else:
                notes.append(
                    "- رفع الثقة؛ MACD Bounce يحتاج الضبط اللحظي. «دمج المركّب» معطّل فلا تعديل لعتبة المركّب."
                )
    recent_items = a.get("recent_items", []) if isinstance(a.get("recent_items"), list) else []
    rs_patches, rs_notes = _risk_setting_best_value_suggestions(recent_items)
    for k, v in rs_patches.items():
        if k not in patches:
            patches[k] = v
    notes.extend(rs_notes[:8])
    return patches, notes


def suggest_config_adjustments_from_recent(*, window: int = 50) -> tuple[dict, list[str]]:
    """
    اقتراح تعديلات إعدادات (بدون حفظ) عند تدهور آخر 50 صفقة.
    يُرجع (patches, notes).
    """
    a = _recent_degradation_analysis(window=window)
    if not a or not bool(a.get("degraded", False)):
        return {}, []
    try:
        from config import load_config as _load_cfg

        _cfg_now = _load_cfg() if callable(_load_cfg) else {}
    except Exception:
        _cfg_now = {}
    bounce_tuning_allowed = bool((_cfg_now or {}).get("bot_live_auto_tune_bounce", False))
    return _patches_from_trade_analysis(a, _cfg_now or {}, bounce_tuning_allowed=bounce_tuning_allowed)


def _audit_config_conflicts(cfg: dict) -> list[str]:
    """تنبيهات تعارض / شروط قد تعيق — نص عربي للتقرير."""
    if not isinstance(cfg, dict):
        return ["• إعدادات غير صالحة."]
    lines: list[str] = []
    if bool(cfg.get("bot_merge_composite", False)) and bool(cfg.get("ai_promote_wait_from_composite", False)):
        lines.append(
            "⚠ تعارض: «دمج المركّب مع البوت» + «ترقية انتظار من المركّب» قد يتصادمان مع فلاتر RSI/الشموع/القاع."
        )
    try:
        from bot_logic import apply_execution_filters as _apply_exec_filters
    except ImportError:
        _apply_exec_filters = None  # type: ignore[misc, assignment]
    if _apply_exec_filters is not None and not _apply_exec_filters(cfg):
        lines.append(
            "ℹ فلاتر المخاطر المتقدّمة قبل تنفيذ البوت معطّلة — مسار تنفيذ خفيف لجميع أوضاع الاستراتيجية (قوائم الشروط المتقدّمة وطبقة decide الثقيلة لا تُطبَّق قبل الشراء)."
        )
    if bool(cfg.get("bot_merge_composite", False)):
        ep = str(cfg.get("bot_entry_profile", "") or "").strip().lower()
        spd = str(cfg.get("indicator_speed_profile", "") or "").strip().lower()
        fast_stack = ep == "aggressive" or spd == "fast"
        if fast_stack:
            lines.append(
                "⚠ دمج المركّب مفعّل في البوت مع دخول سريع الاستجابة أو حساسية مؤشرات سريعة — قد تتضاعف الإشارات؛ راقب التكرار والثقة."
            )
        else:
            lines.append(
                "⚠ دمج المركّب مفعّل في البوت — يُقيَّم المركّب عند التنفيذ إضافةً لمسار التوصية؛ راقب التكرار والثقة."
            )
    try:
        cmin = int(cfg.get("bot_confidence_min", 60) or 60)
        ai_min = int(cfg.get("ai_score_min", 4) or 4)
    except (TypeError, ValueError):
        cmin, ai_min = 60, 4
    if cmin >= 78 and ai_min <= 3:
        lines.append("⚠ ثقة بوت عالية جداً مع عتبة ذكاء منخفضة — قد يمرّ شراء ضعيف أو يُحجب الكثير حسب اللوحة.")
    if bool(cfg.get("bot_expected_value_gate_enabled", True)):
        try:
            ev = float(cfg.get("bot_expected_value_min_pct", 0.03) or 0.03)
        except (TypeError, ValueError):
            ev = 0.03
        if ev > 0.25:
            lines.append("⚠ حد EV العام مرتفع جداً — قد يمنع معظم عمليات الشراء رغم إشارات اللوحة.")
    if not bool(cfg.get("bot_buy_require_early_bounce_15m", False)):
        if (
            str(cfg.get("bot_master_profile", "") or "").strip().lower() == "aggressive"
            and str(cfg.get("indicator_speed_profile", "") or "").strip().lower() == "fast"
        ):
            lines.append(
                "⚠ حساسية سريعة دون شرط ارتداد 15m — خطر شراء في منتصف هبوط؛ فكّر بتفعيل فلتر القاع."
            )
    try:
        buy = float(cfg.get("composite_score_buy", 12) or 12)
        strong = float(cfg.get("composite_score_strong", 31) or 31)
        mid = float(cfg.get("composite_score_mid", 21) or 21)
    except (TypeError, ValueError):
        buy, strong, mid = 12.0, 31.0, 21.0
    if buy >= strong or mid <= buy or mid >= strong:
        lines.append("⚠ عتبات المركّب (buy/mid/strong) غير متسلسلة — التشغيل يضبطها تلقائياً لكن الأرقام في الملف قد تربكك.")
    if bool(cfg.get("limit_sell_blocks_until_target", False)) and not bool(cfg.get("bot_auto_sell", False)):
        lines.append("⚠ حد البيع يحجب SELL حتى الهدف مع بيع تلقائي معطّل — قد يصعب الخروج اليدوي من إشارة البوت.")
    mxd = int(cfg.get("max_trades_per_day", 0) or 0)
    mopen = int(cfg.get("bot_max_open_trades", 1) or 1)
    if mxd == 1 and mopen > 2:
        lines.append("⚠ صفقة واحدة يومياً مع السماح بعدة مراكز مفتوحة — قد لا يتطابق مع توقعاتك للتعرّض.")
    bc = cfg.get("buy_conditions")
    sc = cfg.get("sell_conditions")
    if isinstance(sc, list) and len(sc) > 8 and isinstance(bc, list) and len(bc) <= 1:
        lines.append("⚠ شروط بيع كثيرة جداً مقابل شروط شراء قليلة — قد يحدّ الخروج أو يبطئ التدوير.")
    try:
        from config import get_circuit_breaker_config

        _cbc = get_circuit_breaker_config(cfg)
    except Exception:
        _cbc = {
            "enabled": bool(cfg.get("bot_circuit_breaker_enabled", True)),
            "volatility_pct_max": float(cfg.get("bot_cb_volatility_pct_max", 1.8) or 1.8),
            "adx_min": float(cfg.get("bot_cb_adx_min", 18) or 18),
        }
    if bool(_cbc.get("enabled", True)):
        try:
            cbv = float(_cbc.get("volatility_pct_max", 1.8) or 1.8)
            cb_adx = float(_cbc.get("adx_min", 18) or 18)
        except (TypeError, ValueError):
            cbv, cb_adx = 1.8, 18.0
        if cbv < 0.9 and cb_adx > 25:
            lines.append("⚠ قاطع: تقلب مسموح ضيق جداً مع ADX مطلوب عالٍ — قد يمنع الشراء في أغلب الجلسات.")
    return lines


def _conflict_resolution_patches(cfg: dict) -> tuple[dict, list[str]]:
    """تعديلات آمنة لإزالة تعارضات واضحة (قبل دمج اقتراحات الصفقات)."""
    patches: dict = {}
    notes: list[str] = []
    if not isinstance(cfg, dict):
        return patches, notes
    if bool(cfg.get("bot_merge_composite", False)) and bool(cfg.get("ai_promote_wait_from_composite", False)):
        patches["ai_promote_wait_from_composite"] = False
        notes.append("- إيقاف «ترقية انتظار من المركّب» تلقائياً لأنها تتعارض مع دمج المركّب في البوت.")
    # ضبط clamp للمركّب فقط عند تفعيل الدمج — وإلا تظهر توصيات composite_score_* بلا أثر على البوت
    if bool(cfg.get("bot_merge_composite", False)):
        try:
            from composite_signal import clamp_composite_thresholds

            buy = float(cfg.get("composite_score_buy", 12) or 12)
            strong = float(cfg.get("composite_score_strong", 31) or 31)
            mid = float(cfg.get("composite_score_mid", 21) or 21)
            adx_di = float(cfg.get("composite_adx_for_di", 20) or 20)
            cl = clamp_composite_thresholds(buy, strong, mid, adx_di)
            if (
                abs(cl["buy"] - buy) > 0.01
                or abs(cl["strong"] - strong) > 0.01
                or abs(cl["mid"] - mid) > 0.01
                or abs(cl["adx_di"] - adx_di) > 0.01
            ):
                patches["composite_score_buy"] = cl["buy"]
                patches["composite_score_strong"] = cl["strong"]
                patches["composite_score_mid"] = cl["mid"]
                patches["composite_adx_for_di"] = cl["adx_di"]
                notes.append("- ضبط عتبات المركّب (buy/mid/strong/adx) إلى تسلسل صالح كما في التشغيل الفعلي.")
        except Exception:
            pass
    return patches, notes


def _config_audit_snapshot_lines(cfg: dict) -> list[str]:
    """لقطة شروط الشراء/المرشحات — المجموعات التي يعتمدها البوت صراحة."""
    if not isinstance(cfg, dict):
        return []
    view = dict(cfg)
    try:
        from config import get_circuit_breaker_config

        cbc = get_circuit_breaker_config(view)
        view["circuit_breaker_enabled"] = cbc["enabled"]
        view["circuit_breaker_volatility_pct_max"] = cbc["volatility_pct_max"]
        view["circuit_breaker_adx_min"] = cbc["adx_min"]
        view["circuit_breaker_mtf_bias_floor"] = cbc["mtf_bias_floor"]
        view["circuit_breaker_pause_minutes"] = cbc["pause_minutes"]
        view["circuit_breaker_mtf_rsi_threshold"] = cbc["mtf_rsi_threshold"]
    except Exception:
        pass

    def _line(label: str, key: str) -> str:
        return f"  • {label} [{key}]: {view.get(key)}"

    sections: list[tuple[str, list[tuple[str, str]]]] = [
        (
            "── قاطع الدوائر: تقلب / ADX / انحياز MTF ──",
            [
                ("قاطع مفعّل", "circuit_breaker_enabled"),
                ("أقصى تقلب % يُفعّل الحظر (مع ADX أدنى من الحد)", "circuit_breaker_volatility_pct_max"),
                ("أدنى ADX مع التقلب (أقل منه + تقلب عالٍ = حظر)", "circuit_breaker_adx_min"),
                ("أدنى انحياز MTF مسموح (أقل = حظر شراء)", "circuit_breaker_mtf_bias_floor"),
                ("عتبة RSI مع انحياز MTF سيّئ (أعلى = حظر)", "circuit_breaker_mtf_rsi_threshold"),
                ("دقائق تهدئة بعد القاطع", "circuit_breaker_pause_minutes"),
            ],
        ),
        (
            "── ثقة البوت ──",
            [
                ("حد أدنى لثقة التوصية %", "bot_confidence_min"),
            ],
        ),
        (
            "── عتبات EV (حسب نظام السوق في البوت) ──",
            [
                ("بوابة EV مفعّلة", "bot_expected_value_gate_enabled"),
                ("EV عام %", "bot_expected_value_min_pct"),
                ("EV ترند صاعد %", "bot_expected_value_min_pct_trend_up"),
                ("EV ترند هابط %", "bot_expected_value_min_pct_trend_down"),
                ("EV نطاق %", "bot_expected_value_min_pct_range"),
                ("EV متقلب %", "bot_expected_value_min_pct_volatile"),
            ],
        ),
        (
            "── دمج المؤشر المركّب في قرار البوت ──",
            [
                ("دمج المركّب مع التوصية", "bot_merge_composite"),
                ("عتبة شراء مركّب (buy)", "composite_score_buy"),
                ("عتبة منتصف (mid)", "composite_score_mid"),
                ("عتبة قوية (strong)", "composite_score_strong"),
                ("ADX لمسار DI في المركّب", "composite_adx_for_di"),
            ],
        ),
        (
            "── سياق عام (لوحة + استراتيجية) ──",
            [
                ("بروفايل رئيسي", "bot_master_profile"),
                ("تطبيق فلاتر المخاطر المتقدّمة قبل التنفيذ", "bot_apply_execution_filters"),
                ("قوائم البوت الخاصة على قوالب الاستراتيجية", "apply_conditions_to_presets"),
                ("حساسية المؤشرات", "indicator_speed_profile"),
                ("أفق الصفقة", "bot_trade_horizon"),
                ("موجّه النظام في الذكاء", "ai_use_regime_router"),
                ("ذكاء اللوحة score≥", "ai_score_min"),
                ("سكالب: أدنى ADX", "scalp_adx_min"),
                ("ارتداد 15m إلزامي", "bot_buy_require_early_bounce_15m"),
                ("ضبط لحظي لقيم الارتداد", "bot_live_auto_tune_bounce"),
            ],
        ),
    ]
    lines: list[str] = []
    for header, pairs in sections:
        lines.append(header)
        for label, key in pairs:
            lines.append(_line(label, key))
    return lines


def build_comprehensive_audit_report(cfg: dict | None = None, *, window: int = 50) -> str:
    """
    تقرير شامل: مقارنة الصفقات (إن وُجدت)، مؤشرات الخسائر، تعارضات الإعدادات، لقطة شروط.
    """
    try:
        from config import load_config

        cfg = cfg if isinstance(cfg, dict) else load_config()
    except Exception:
        cfg = cfg if isinstance(cfg, dict) else {}
    parts: list[str] = [
        "══ مراجعة شاملة — صفقات + مؤشرات + شروط + تعارضات ══",
        "",
    ]
    a = _recent_degradation_analysis(window=window)
    if not a:
        parts.append(
            f"── أ) الصفقات: أقل من {window * 2} صفقة مغلقة — مقارنة النافذتين غير متوفرة؛ تُراجع التعارضات واللقطة فقط."
        )
    else:
        m_r = a["recent"]
        m_p = a["prev"]
        pnl_delta = float(a["pnl_delta"])
        wr_delta = float(a["wr_delta"])
        parts.append(f"── أ) آخر {window} صفقة مقابل الـ{window} السابقة ──")
        parts.append(
            f"الحالية: pnl={m_r['pnl_sum']:+.2f} | win={m_r['win_rate']:.1f}% | avg={m_r['avg_pnl']:+.2f}"
        )
        parts.append(
            f"السابقة: pnl={m_p['pnl_sum']:+.2f} | win={m_p['win_rate']:.1f}% | avg={m_p['avg_pnl']:+.2f}"
        )
        parts.append(f"الفارق: pnl={pnl_delta:+.2f} | win={wr_delta:+.1f}%")
        if bool(a.get("degraded", False)):
            parts.append("حالة: تدهور ملحوظ مقارنة بالنافذة السابقة.")
        else:
            parts.append("حالة: لا يستوفي معيار «تدهور التقرير القديم» لكن البيانات تُراجع أدناه.")
        ratios = a.get("ratios", {}) if isinstance(a.get("ratios"), dict) else {}
        if ratios:
            parts.append(
                "مؤشرات داخل صفقات خاسرة (النافذة الأخيرة): "
                f"DOWN={float(ratios.get('down', 0.0)):.1f}% | "
                f"DOWN+ADXقوي={float(ratios.get('strong_down', 0.0)):.1f}% | "
                f"تحت VWAP={float(ratios.get('vwap_below', 0.0)):.1f}% | "
                f"RSIساخن={float(ratios.get('hot_rsi', 0.0)):.1f}% | "
                f"تقلب عالٍ={float(ratios.get('high_vol', 0.0)):.1f}% | "
                f"ADXضعيف={float(ratios.get('adx_weak', 0.0)):.1f}% | "
                f"MACD-={float(ratios.get('macd_neg', 0.0)):.1f}% | "
                f"Hist-={float(ratios.get('hist_neg', 0.0)):.1f}% | "
                f"Stochساخن={float(ratios.get('stoch_hot', 0.0)):.1f}%"
            )
        recent = a.get("recent_items", [])
        if isinstance(recent, list) and recent:
            ps = _profile_sensitivity_report(recent)
            if ps:
                parts.append("حسب البروفايل/الحساسية:")
                parts.extend(ps)
            hints = _loss_pattern_hints(recent)
            if hints:
                parts.append("اقتراحات منطقية حسب المؤشرات:")
                parts.extend(hints[:16])
            rs = _risk_setting_impact_report(recent)
            if rs:
                parts.append("تأثير قيم إعدادات مسجّلة مع الصفقات:")
                parts.extend(rs[:20])
    parts.append("")
    parts.append("── ب) تعارضات أو شروط قد تعيق ──")
    conf = _audit_config_conflicts(cfg)
    if conf:
        parts.extend(conf)
    else:
        parts.append("• لا تعارضات بارزة بين المفاتيح المفحوصة.")
    parts.append("")
    parts.append("── ج) لقطة شروط حالية (مرجع سريع) ──")
    parts.extend(_config_audit_snapshot_lines(cfg))
    return "\n".join(parts)


def suggest_comprehensive_audit_patches(cfg: dict, *, window: int = 50) -> tuple[dict, list[str]]:
    """
    دمج: إصلاح تعارضات + اقتراحات من نِسَب الخسائر (دون اشتراط «تدهور») +
    تفعيل ضبط الارتداد اللحظي عند الحاجة + ارتداد 15m عند VWAP.
    """
    patches: dict = {}
    notes: list[str] = []
    cr, cn = _conflict_resolution_patches(cfg)
    patches.update(cr)
    notes.extend(cn)
    a = _recent_degradation_analysis(window=window)
    if a:
        tr, tn = _patches_from_trade_analysis(a, cfg, bounce_tuning_allowed=True)
        for k, v in tr.items():
            patches[k] = v
        notes.extend(tn)
        ratios = a.get("ratios", {}) if isinstance(a.get("ratios"), dict) else {}
        if float(ratios.get("vwap_below", 0.0)) >= 50.0 and not bool(cfg.get("bot_buy_require_early_bounce_15m", False)):
            patches["bot_buy_require_early_bounce_15m"] = True
            notes.append("- تفعيل شرط ارتداد 15m (قاع/زخم مبكر) بسبب نسبة خسائر تحت VWAP.")
    if any(k in patches for k in _BOUNCE_RELATED_PATCH_KEYS):
        if not bool(cfg.get("bot_live_auto_tune_bounce", False)):
            patches["bot_live_auto_tune_bounce"] = True
            notes.append("- تفعيل «الضبط اللحظي لشروط الارتداد» حتى تُطبَّق عتبات RSI/VWAP/Stoch/ADX/MACD المقترحة.")
    return patches, notes


def _load_registry() -> dict:
    import json

    p = _registry_path()
    if not os.path.isfile(p):
        return {"best_score": 0.0, "versions": []}
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            return {"best_score": 0.0, "versions": []}
        d.setdefault("best_score", 0.0)
        d.setdefault("versions", [])
        return d
    except Exception:
        return {"best_score": 0.0, "versions": []}


def _save_registry(reg: dict) -> None:
    import json

    p = _registry_path()
    try:
        versions = reg.get("versions", [])
        if isinstance(versions, list) and len(versions) > 200:
            reg["versions"] = versions[-200:]
        with open(p, "w", encoding="utf-8") as f:
            json.dump(reg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.debug("Could not save ML registry: %s", e)


def _ml_train_user_report_promoted(
    *,
    sw_tr,
    wn: int,
    overall_wr: float,
    recent_wr: float,
    metrics: dict,
    new_score: float,
    wf_metrics: dict,
) -> str:
    """نص إحصاءات التدريب للمستخدم — عربي/إنجليزي حسب إعدادات الواجهة."""
    n_feats = len(FEATURE_KEYS)
    recency_on = sw_tr is not None
    acc = float(metrics.get("accuracy", 0.0) or 0.0)
    f1 = float(metrics.get("f1", 0.0) or 0.0)
    wf_acc = float(wf_metrics.get("accuracy", 0.0) or 0.0)
    wf_f1 = float(wf_metrics.get("f1", 0.0) or 0.0)
    ev_net = float(wf_metrics.get("avg_net_ev_pct", 0.0) or 0.0)
    cost = float(wf_metrics.get("cost_per_trade_pct", 0.0) or 0.0)
    try:
        from translations import tr

        recency = tr("ml_train_recency_on") if recency_on else tr("ml_train_recency_off")
        line1 = tr("ml_train_report_promoted").format(
            n_feats=n_feats,
            recency=recency,
            all_wr=overall_wr * 100,
            wn=wn,
            recent_wr=recent_wr * 100,
            acc=acc * 100,
            f1=f1 * 100,
            score=new_score,
        )
        line2 = tr("ml_train_report_wf").format(
            wf_acc=wf_acc * 100,
            wf_f1=wf_f1 * 100,
            ev_net=ev_net,
            cost=cost,
        )
        return line1 + line2
    except Exception:
        rt = "ON" if recency_on else "OFF"
        return (
            f"features={n_feats} recency={rt} | "
            f"win% all={overall_wr*100:.1f}% last{wn}={recent_wr*100:.1f}% | "
            f"holdout acc={acc*100:.1f}% f1={f1*100:.1f}% score={new_score:.3f}\n"
            f"WF acc={wf_acc*100:.1f}% f1={wf_f1*100:.1f}% ev_net={ev_net:+.3f}% (cost {cost:.3f}%)"
        )


def _ml_train_user_report_not_promoted(
    *,
    sw_tr,
    wn: int,
    overall_wr: float,
    recent_wr: float,
    new_score: float,
    best_score: float,
) -> str:
    n_feats = len(FEATURE_KEYS)
    recency_on = sw_tr is not None
    try:
        from translations import tr

        recency = tr("ml_train_recency_on") if recency_on else tr("ml_train_recency_off")
        summary = tr("ml_train_report_holdout_only").format(
            n_feats=n_feats,
            recency=recency,
            all_wr=overall_wr * 100,
            wn=wn,
            recent_wr=recent_wr * 100,
            score=new_score,
        )
        return summary + tr("ml_train_not_promoted_detail").format(
            new_score=new_score,
            best_score=best_score,
        )
    except Exception:
        rt = "ON" if recency_on else "OFF"
        summary = (
            f"features={n_feats} recency={rt} | "
            f"win% all={overall_wr*100:.1f}% last{wn}={recent_wr*100:.1f}% | "
            f"holdout score={new_score:.3f}\n"
        )
        return (
            summary
            + f"Training finished but not promoted: score={new_score:.4f} < best={best_score:.4f}.\n"
        )


def train_ml_model() -> tuple[bool, str, str]:
    """
    تدريب نموذج غابة عشوائية على نتائج الصفقات وحفظه على القرص.
    يُرجع (saved, message, outcome):
    - outcome == \"saved\": حُفظ النموذج الجديد (الأفضل أو أول مرة).
    - outcome == \"not_promoted\": التدريب اكتمل لكن النموذج لم يُعتمد (أقل من الأفضل الحالي).
    - outcome == \"failed\": فشل قبل/أثناء التدريب.
    """
    try:
        import joblib
        from sklearn.ensemble import RandomForestClassifier
    except ImportError as e:
        log.warning("sklearn or joblib not installed: %s", e)
        return (
            False,
            "لم يتم العثور على scikit-learn أو joblib. شغّل من نفس بيئة البايثون: pip install scikit-learn joblib",
            "failed",
        )

    try:
        X, y, t, w_row = build_training_dataset()
    except Exception as e:
        log.warning("build_training_dataset failed: %s", e)
        return False, f"خطأ في تحميل البيانات: {type(e).__name__}: {e}", "failed"

    ok_q, msg_q = _quality_check(y)
    if not ok_q:
        log.info("ML quality check failed: %s", msg_q)
        return False, msg_q, "failed"

    try:
        try:
            from config import load_config

            cfg = load_config()
        except Exception:
            cfg = {}
        rows_sorted = sorted(zip(t, X, y, w_row), key=lambda z: z[0])
        n_all = len(rows_sorted)
        overall_wr = sum(1 for r in rows_sorted if r[2] == 1) / n_all if n_all else 0.0
        wn = min(20, n_all)
        recent_wr = sum(1 for r in rows_sorted[-wn:] if r[2] == 1) / wn if wn else 0.0
        n_td = sum(1 for r in rows_sorted if r[3] > 1.0001)

        X_tr, y_tr, X_te, y_te, w_tr, _w_te = _time_split(X, y, t, w_row, train_ratio=0.8)
        if len(X_tr) < 10 or len(X_te) < 5:
            return False, "البيانات بعد التقسيم الزمني غير كافية للتقييم.", "failed"
        sw_tr = _recency_train_weights(len(X_tr), cfg)
        base_w = list(w_tr)
        if sw_tr is not None:
            fit_w = [sw_tr[i] * base_w[i] for i in range(len(X_tr))]
        elif any(w > 1.0001 for w in base_w):
            fit_w = base_w
        else:
            fit_w = None
        model = RandomForestClassifier(n_estimators=50, max_depth=6, random_state=42)
        if fit_w is not None:
            model.fit(X_tr, y_tr, sample_weight=fit_w)
        else:
            model.fit(X_tr, y_tr)
        metrics = _evaluate_model(model, X_te, y_te)
        wf_rows = _build_outcome_rows(limit=1200)
        wf_cost = float(cfg.get("ml_wf_cost_per_trade_pct", 0.08) or 0.08)
        wf_train_min = int(cfg.get("ml_wf_train_min", 40) or 40)
        wf_test_win = int(cfg.get("ml_wf_test_window", 10) or 10)
        wf_metrics = _evaluate_walk_forward(
            wf_rows,
            cost_per_trade_pct=wf_cost,
            train_min=wf_train_min,
            test_window=wf_test_win,
        )

        reg = _load_registry()
        best_score = float(reg.get("best_score", 0.0) or 0.0)
        new_score = float(metrics.get("score", 0.0) or 0.0)
        has_current_model = os.path.isfile(_model_path())
        always_promote = bool(cfg.get("ml_always_promote_latest", True))
        if always_promote:
            should_promote = True
        else:
            should_promote = (not has_current_model) or (new_score >= best_score + 0.005)

        reg_versions = reg.get("versions", [])
        if not isinstance(reg_versions, list):
            reg_versions = []
        version = len(reg_versions) + 1
        reg_versions.append(
            {
                "version": version,
                "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "feature_dim": len(FEATURE_KEYS),
                "train_recency_weighted": sw_tr is not None,
                "train_trend_down_boost_rows": int(n_td),
                "train_trend_down_mult": (
                    float(max(1.0, float(cfg.get("ml_train_trend_down_weight_mult", 1.35) or 1.35)))
                    if bool(cfg.get("ml_train_trend_down_boost_enabled", True))
                    else 1.0
                ),
                "dataset_win_rate": round(overall_wr, 4),
                "recent_window_win_rate": round(recent_wr, 4),
                "recent_window_n": int(wn),
                "train_size": len(X_tr),
                "test_size": metrics.get("test_size", 0),
                "accuracy": metrics.get("accuracy", 0.0),
                "f1": metrics.get("f1", 0.0),
                "score": new_score,
                "walk_forward": wf_metrics,
                "promoted": bool(should_promote),
                "always_promote_latest": bool(always_promote),
            }
        )
        reg["versions"] = reg_versions

        if should_promote:
            path = _model_path()
            joblib.dump(model, path)
            reg["best_score"] = max(best_score, new_score)
            _save_trained_count(len(X))
            _save_registry(reg)
            log.info(
                "ML model trained/promoted (train=%s, test=%s, score=%.4f, best_hist=%.4f, wf_ev=%.4f%%, feats=%s recency=%s trend_down_boost_rows=%s always_latest=%s)",
                len(X_tr),
                len(X_te),
                new_score,
                float(reg.get("best_score", 0.0) or 0.0),
                float(wf_metrics.get("avg_net_ev_pct", 0.0) or 0.0),
                len(FEATURE_KEYS),
                sw_tr is not None,
                n_td,
                always_promote,
            )
            diag = build_recent_50_diagnostic_report(window=50)
            msg = _ml_train_user_report_promoted(
                sw_tr=sw_tr,
                wn=wn,
                overall_wr=overall_wr,
                recent_wr=recent_wr,
                metrics=metrics,
                new_score=new_score,
                wf_metrics=wf_metrics,
            )
            if diag:
                msg += "\n\n" + diag
            return True, msg, "saved"

        _save_registry(reg)
        log.info(
            "ML model trained but not promoted (score=%.4f < best=%.4f)",
            new_score,
            best_score,
        )
        diag = build_recent_50_diagnostic_report(window=50)
        msg_np = _ml_train_user_report_not_promoted(
            sw_tr=sw_tr,
            wn=wn,
            overall_wr=overall_wr,
            recent_wr=recent_wr,
            new_score=new_score,
            best_score=best_score,
        )
        if diag:
            msg_np += "\n\n" + diag
        return (
            False,
            msg_np,
            "not_promoted",
        )
    except Exception as e:
        log.warning("ML training failed: %s", e)
        return False, f"{type(e).__name__}: {e}", "failed"


def _save_trained_count(count: int):
    import json
    path = _meta_path()
    try:
        prev = {}
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                prev = json.load(f) or {}
        if not isinstance(prev, dict):
            prev = {}
        prev["last_trained_count"] = int(count)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(prev, f)
    except Exception as e:
        log.debug("Could not save ML meta: %s", e)


def _load_trained_count() -> int:
    import json
    path = _meta_path()
    if not os.path.isfile(path):
        return 0
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("last_trained_count", 0))
    except Exception:
        return 0


def try_auto_retrain() -> bool:
    """
    إعادة تدريب تلقائية إذا زاد عدد الصفقات المغلقة عن آخر تدريب بـ AUTO_RETRAIN_EVERY_N_OUTCOMES.
    تُستدعى بعد تسجيل إغلاق صفقة. تُرجع True إذا تم التدريب.
    """
    from recommendation_log import load_outcomes
    closed = load_outcomes(limit=2000)
    count = len(closed)
    if count < MIN_TRADES_FOR_TRAINING:
        return False
    last = _load_trained_count()
    if count - last < AUTO_RETRAIN_EVERY_N_OUTCOMES and last > 0:
        return False
    saved, _, outcome = train_ml_model()
    return outcome == "saved"


def load_ml_model():
    """تحميل النموذج من القرص. يُرجع النموذج أو None."""
    try:
        import joblib
    except ImportError:
        return None
    path = _model_path()
    if not os.path.isfile(path):
        return None
    try:
        return joblib.load(path)
    except Exception as e:
        log.warning("Could not load ML model: %s", e)
        return None


def predict_win_probability(indicators: dict, market_info: dict) -> float | None:
    """
    التنبؤ باحتمال نجاح الصفقة (0..1) من المؤشرات الحالية.
    يُرجع None إذا لم يكن النموذج متوفراً أو فشل التنبؤ أو النموذج قديم (عدد ميزات مختلف).
    """
    model = load_ml_model()
    if model is None:
        return None
    try:
        try:
            from config import load_config

            _cfg = load_config()
        except Exception:
            _cfg = {}
        row = _extract_features(indicators or {}, market_info or {}, cfg=_cfg)
        n_features = len(row)
        if hasattr(model, "n_features_in_") and model.n_features_in_ != n_features:
            log.debug("ML model has %s features, current extraction has %s; retrain needed.", getattr(model, "n_features_in_", None), n_features)
            return None
        proba = model.predict_proba([row])[0]
        if hasattr(proba, "__len__") and len(proba) >= 2:
            return float(proba[1])
        return float(proba[0]) if proba.shape[0] == 1 else None
    except Exception as e:
        log.debug("ML predict failed: %s", e)
        return None


def estimate_expected_value_pct(
    indicators: dict,
    market_info: dict,
    *,
    limit: int = 1200,
    min_samples: int = 20,
    top_k: int = 80,
) -> dict | None:
    """
    تقدير «القيمة المتوقعة %» من صفقات تاريخية مشابهة:
    EV% = (win_rate * avg_win_pct) - ((1-win_rate) * avg_loss_pct_abs)
    يرجع dict: ev_pct, win_rate, samples أو None إذا البيانات غير كافية.
    """
    from recommendation_log import load_outcomes

    try:
        try:
            from config import load_config

            _cfg_ev = load_config()
        except Exception:
            _cfg_ev = {}
        target = _extract_features(indicators or {}, market_info or {}, cfg=_cfg_ev)
    except Exception:
        return None
    closed = load_outcomes(limit=limit)
    if not closed:
        return None

    target_regime = _classify_regime(indicators or {}, market_info or {})
    rows: list[tuple[float, float, str]] = []  # (distance, pnl_pct, regime)
    for o in closed:
        try:
            ind = o.get("indicators") or o.get("entry_indicators") or {}
            info = o.get("market_info") or o.get("entry_market_info") or {}
            if not isinstance(ind, dict) or not isinstance(info, dict):
                continue
            feat = _extract_features(ind, info, outcome_meta=o)
            if len(feat) != len(target):
                continue
            pnl = float(o.get("pnl", 0.0) or 0.0)
            ep = float(o.get("entry_price", 0.0) or 0.0)
            q = float(o.get("quantity", 0.0) or 0.0)
            notional = ep * q
            if notional <= 1e-9:
                continue
            pnl_pct = (pnl / notional) * 100.0
            dist = 0.0
            for a, b in zip(feat, target):
                d = float(a) - float(b)
                dist += d * d
            rows.append((dist ** 0.5, pnl_pct, _classify_regime(ind, info)))
        except Exception:
            continue

    if len(rows) < min_samples:
        return None

    same_regime = [r for r in rows if r[2] == target_regime]
    picked_target_n = max(min_samples, min(top_k, len(rows)))
    if len(same_regime) >= min_samples:
        same_regime.sort(key=lambda x: x[0])
        picked = same_regime[: min(picked_target_n, len(same_regime))]
    else:
        # fallback: نستخدم كل الحالات لكن مع عقوبة مسافة للحالات غير المطابقة
        blended: list[tuple[float, float, str]] = []
        for d, p, rg in rows:
            penalty = 0.0 if rg == target_regime else 0.45
            blended.append((d + penalty, p, rg))
        blended.sort(key=lambda x: x[0])
        picked = blended[:picked_target_n]

    wins = [p for _, p, _ in picked if p > 0]
    losses = [p for _, p, _ in picked if p < 0]
    n = len(picked)
    same_n = sum(1 for _, _, rg in picked if rg == target_regime)
    win_rate = (len(wins) / n) if n else 0.0
    avg_win = (sum(wins) / len(wins)) if wins else 0.0
    avg_loss_abs = (abs(sum(losses) / len(losses))) if losses else 0.0
    ev_pct = (win_rate * avg_win) - ((1.0 - win_rate) * avg_loss_abs)
    return {
        "ev_pct": float(ev_pct),
        "win_rate": float(win_rate * 100.0),
        "samples": int(n),
        "regime": target_regime,
        "same_regime_samples": int(same_n),
        "avg_win_pct": float(avg_win),
        "avg_loss_pct_abs": float(avg_loss_abs),
    }
