# bot_logic.py — منطق قرار البوت فقط (بدون واجهة أو تنفيذ أوامر)
# الشروط التفصيلية (شراء/بيع) ستكون لاحقاً في قائمتين في الإعدادات. هنا فقط التحققات الأساسية.
from __future__ import annotations

import logging
from typing import Optional, Union

from trade_history import count_buy_trades_today, count_consecutive_losses
from composite_signal import get_composite_thresholds
from config import get_circuit_breaker_config

log = logging.getLogger("trading.bot_logic")


def bot_uses_composite(config: dict) -> bool:
    """إن False: لا تُستخدم درجة المركّب في decide (دمج، EV، هيكل هابط، توافق، إلخ)."""
    return isinstance(config, dict) and bool(config.get("bot_merge_composite", False))


def bounce_filter_enabled(config: dict) -> bool:
    """فلتر الارتداد المبكر (فريم الشارت الحالي). إن False: لا يُنفَّذ في decide ولا يُطبَّق الضبط اللحظي لقيمه."""
    return isinstance(config, dict) and bool(config.get("bot_buy_require_early_bounce_15m", False))


def apply_execution_filters(config: dict) -> bool:
    """
    مفتاح واحد (`bot_apply_execution_filters`):
    - True: فلاتر decide المتقدّمة قبل شراء البوت لأي strategy_mode، ودمج/انتظارات المركّب في signal_engine
      (postprocess، زخم ترند مبكر، توسيع RSI حسب المركّب، إلخ).
    - False: مسار خفيف في decide؛ وفي محرّك الإشارة: لا قاعدة «زخم ترند مبكر» ولا طبقة postprocess
      (مطاردة مقاومة، شروط مركّب+شموع، دمج المركّب في التوصية) — تبقى سلسلة القواعد/الاستراتيجية فقط قبل decide.
      ما زال يطبّق: حد الثقة، المراكز، القاطع، التعرّض، الخسارة المتتالية، السعر الحي، قيود البيع في decide.
    """
    if not isinstance(config, dict):
        return True
    return bool(config.get("bot_apply_execution_filters", True))


def apply_private_condition_lists_for_strategy(config: dict) -> bool:
    """
    قوائم تبويب «إعدادات البوت الخاصة» (`buy_conditions` / `sell_conditions`): مع تفعيل فلاتر التنفيذ،
    تُطبَّق دائماً على custom/auto؛ على استراتيجية قالب تخضع لـ `apply_conditions_to_presets`.
    """
    if not apply_execution_filters(config):
        return False
    sm = str(config.get("strategy_mode") or "custom").strip().lower()
    if sm in ("", "custom", "auto"):
        return True
    preset_modes = {"scalping", "bounce", "trend", "dca", "grid", "3commas", "breakout"}
    if sm not in preset_modes:
        return True
    return bool(config.get("apply_conditions_to_presets", True))


def _float(d: dict, key: str, default: float = 0.0) -> float:
    v = d.get(key, default)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def decide(
    recommendation: str,
    confidence: float,
    indicators: dict,
    config: dict,
    *,
    has_position: bool,
    pos: Optional[dict],
    last_price: float,
    daily_pnl: float,
    open_count: int,
    open_symbol_count: int,
    current_symbol: str,
    conf_min: float,
    candle_high: Optional[float] = None,
    at_real_peak: Optional[bool] = None,
    take_profit_barrier: bool = False,
    composite_score: Optional[float] = None,
    open_portfolio_exposure_usdt: float = 0.0,
    planned_buy_notional_usdt: float = 0.0,
    chart_interval: Union[str, None] = None,
) -> tuple[Optional[str], float, Optional[str], None]:
    """
    يحدد هل ينفّذ البوت أمراً مطابقاً لتوصية اللوحة (BUY/SELL) — لا يعكس الاتجاه هنا.
    التوصية نفسها تأتي من قواعد لوحة الذكاء (مزيج منقّح/اتجاه/شموع/مركّب) وليست ضمان ربح.
    شروط الإعدادات (قائمتا الشراء/البيع) فلاتر فقط: عند منع الشرط يُتخطّى التنفيذ.
    عند إيقاف «تطبيق فلاتر التنفيذ المتقدّمة» (bot_apply_execution_filters=False): تُتخطّى فلاتر شراء إضافية (انظر apply_execution_filters).
    الإرجاع: (action, final_confidence, skip_reason|None, None)
    """
    rec = (recommendation or "").strip().upper()
    conf = float(confidence or 0)
    ind = indicators if isinstance(indicators, dict) else {}
    mtf_bias = _float(ind, "mtf_bias", 0.0)  # انحياز متعدد الأطر (1h/4h)
    master_profile = str(config.get("bot_master_profile", "") or "").strip().lower()
    if master_profile != "aggressive":
        master_profile = "aggressive"
    trade_horizon = str(config.get("bot_trade_horizon", "short") or "short").strip().lower()
    if trade_horizon not in ("short", "swing"):
        trade_horizon = "short"

    try:
        from market_status_readout import engine_market_readout_bundle

        _mr_bot = engine_market_readout_bundle(config, trade_horizon=trade_horizon)
    except Exception:
        _mr_bot = {}

    def _mr(key: str, default: float) -> float:
        try:
            return float(_mr_bot.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    def _pval(conservative: float, balanced: float, aggressive: float) -> float:
        return aggressive

    def _h(short_v: float, swing_v: float) -> float:
        return swing_v if trade_horizon == "swing" else short_v

    # تعديل خفيف للثقة حسب انحياز 1h/4h (VWAP/MACD/شموع في `_compute_mtf_frame_bias`).
    # «الاتجاه» في حالة السوق/اللوحة يأتي من منحدر 12 شمعة على إطار الشارت الحالي — قد يختلف عن HTF.
    # إن قرأ الشارت صعوداً لا نطبّق خصم −8 الكامل لمجرد HTF هابط (يُزال التناقض الظاهر للمستخدم).
    chart_trend = str(ind.get("trend", "") or "").strip().upper()
    if apply_execution_filters(config):
        if rec == "BUY":
            if mtf_bias >= 0.6:
                conf += 4.0
            elif mtf_bias <= -0.6 and chart_trend != "UP":
                conf -= 8.0
        elif rec == "SELL":
            if mtf_bias <= -0.6:
                conf += 4.0
            elif mtf_bias >= 0.6 and chart_trend != "DOWN":
                conf -= 8.0

    # دمج المؤشر المركّب (−100…100) مع التوصية — يشمل عند التوفر buy_pressure_score (بدون Fear&Greed)
    if apply_execution_filters(config) and bot_uses_composite(config) and composite_score is not None:
        try:
            cs = max(-100.0, min(100.0, float(composite_score)))
        except (TypeError, ValueError):
            cs = None
        if cs is not None:
            if rec == "BUY":
                if cs <= -14:
                    return (
                        None,
                        conf,
                        "انتظار — المؤشر المركب هابط (بيع) والتوصية شراء",
                        None,
                    )
                if cs >= 14:
                    conf += 6.0
                elif cs <= -5:
                    conf -= 5.0
            elif rec == "SELL":
                if cs >= 14:
                    return (
                        None,
                        conf,
                        "انتظار — المؤشر المركب صاعد (شراء) والتوصية بيع",
                        None,
                    )
                if cs <= -14:
                    conf += 6.0
                elif cs >= 5:
                    conf -= 5.0

    conf = max(0.0, min(100.0, conf))

    # —— 1) التوصية يجب أن تكون BUY أو SELL ——
    if rec not in ("BUY", "SELL"):
        return None, conf, f"Waiting — recommendation={rec} (need BUY/SELL)", None

    # —— 2) حد أدنى للثقة ——
    if conf < conf_min:
        return None, conf, f"Waiting — confidence {conf:.1f}% < min {int(conf_min)}%", None

    # —— 2.05) أطر طويلة + Supertrend هابط، وهبوط هيكلي + مركّب ضعيف — منع شراء البوت (يتطابق مع composite_merge) ——
    if rec == "BUY" and apply_execution_filters(config):
        if bool(config.get("bot_block_buy_st_bear_htf_enabled", True)):
            htf_raw = config.get("bot_st_bear_block_chart_intervals")
            if isinstance(htf_raw, list) and htf_raw:
                htf_set = {str(x).strip().lower() for x in htf_raw if str(x).strip()}
            else:
                htf_set = {"4h", "1d"}
            iv_guard = str(chart_interval or ind.get("chart_interval") or "").strip().lower()
            if iv_guard in htf_set and int(_float(ind, "supertrend_dir", 0)) < 0:
                return (
                    None,
                    conf,
                    "انتظار — Supertrend هابط على إطار طويل (4h/1d): شراء البوت معطّل",
                    None,
                )
        if bot_uses_composite(config) and bool(
            config.get("bot_structural_bear_require_mid_composite", True)
        ):
            tr_d = str(ind.get("trend", "") or "").upper() == "DOWN"
            st_br = int(_float(ind, "supertrend_dir", 0)) <= -1
            if tr_d and st_br and composite_score is not None:
                try:
                    mid_thr = float(get_composite_thresholds()["mid"])
                    if float(composite_score) < mid_thr:
                        return (
                            None,
                            conf,
                            "انتظار — ترند هابط + Supertrend هابط مع درجة المركّب دون العتبة الوسطى",
                            None,
                        )
                except Exception:
                    pass

    # —— 2.1) فلتر القيمة المتوقعة (EV) قبل الشراء ——
    if (
        rec == "BUY"
        and apply_execution_filters(config)
        and bool(config.get("bot_expected_value_gate_enabled", True))
    ):
        try:
            from ml_model import estimate_expected_value_pct

            ev_data = estimate_expected_value_pct(
                indicators=ind,
                market_info={
                    "trend": ind.get("trend"),
                    "volume_strength": ind.get("volume_strength", 1.0),
                    "volatility": ind.get("volatility", 0.0),
                    "volatility_pct": ind.get("volatility_pct", 0.0),
                },
            )
        except Exception:
            ev_data = None
        if isinstance(ev_data, dict):
            try:
                ev_pct = float(ev_data.get("ev_pct", 0.0) or 0.0)
                wr = float(ev_data.get("win_rate", 0.0) or 0.0)
                n_s = int(ev_data.get("samples", 0) or 0)
                regime = str(ev_data.get("regime", "") or "").strip()
                n_same = int(ev_data.get("same_regime_samples", 0) or 0)
            except (TypeError, ValueError):
                ev_pct, wr, n_s, regime, n_same = 0.0, 0.0, 0, "", 0
            cost_rt = max(0.0, float(config.get("ml_wf_cost_per_trade_pct", 0.08) or 0.0))
            ev_net = ev_pct - cost_rt
            ev_min = _float(config, "bot_expected_value_min_pct", 0.03)
            if regime == "trend_up":
                ev_min = _float(config, "bot_expected_value_min_pct_trend_up", ev_min)
            elif regime == "trend_down":
                ev_min = _float(config, "bot_expected_value_min_pct_trend_down", ev_min)
            elif regime == "range":
                ev_min = _float(config, "bot_expected_value_min_pct_range", ev_min)
            elif regime == "volatile":
                ev_min = _float(config, "bot_expected_value_min_pct_volatile", ev_min)
            try:
                _ev_comp_thr = float(get_composite_thresholds()["buy"])
            except (TypeError, ValueError):
                _ev_comp_thr = 10.0
            _ev_comp_ok = (
                bot_uses_composite(config)
                and composite_score is not None
                and float(composite_score) >= _ev_comp_thr
            )
            # EV تاريخي منخفض لكن المركّب الحالي داعم للشراء — لا نقتل الصفقة بمقاييس قديمة
            if n_s >= 20 and ev_net < ev_min and not _ev_comp_ok:
                rg_txt = f" | regime={regime} ({n_same}/{n_s})" if regime else ""
                return (
                    None,
                    conf,
                    f"انتظار — EV صافٍ منخفض ({ev_net:+.3f}% = عائد تاريخي {ev_pct:+.3f}% − تكلفة {cost_rt:.3f}%) < {ev_min:.3f}% | win≈{wr:.1f}% ({n_s}){rg_txt}",
                    None,
                )

    # —— 3) حد عدد المراكز المفتوحة (للشراء) ——
    _mo = config.get("bot_max_open_trades", 1)
    try:
        max_open = int(_mo)
    except (TypeError, ValueError):
        max_open = 1
    max_per_symbol = int(config.get("max_trades_per_symbol", 0) or 0)
    if rec == "BUY":
        # —— 3.0) Circuit Breaker ——
        cb = get_circuit_breaker_config(config)
        if cb["enabled"]:
            vol_pct = _float(ind, "volatility_pct", 0.0)
            adx_here = _float(ind, "adx14", 0.0)
            rsi_here_cb = _float(ind, "rsi", 50.0)
            cb_vol_max = float(cb["volatility_pct_max"])
            cb_adx_min = float(cb["adx_min"])
            cb_mtf_floor = float(cb["mtf_bias_floor"])
            cb_mtf_rsi = float(cb["mtf_rsi_threshold"])
            # تقلب مرتفع + ADX ضعيف = سوق فوضوي (noise regime)
            if vol_pct > 0 and cb_vol_max > 0 and vol_pct >= cb_vol_max and adx_here < cb_adx_min:
                return (
                    None,
                    conf,
                    f"Circuit Breaker — high volatility regime ({vol_pct:.2f}% / ADX {adx_here:.1f})",
                    None,
                )
            # انحياز متعدد الأطر هابط بقوة — لا BUY في اتجاه معاكس
            if mtf_bias <= cb_mtf_floor and rsi_here_cb > cb_mtf_rsi:
                return (
                    None,
                    conf,
                    f"Circuit Breaker — higher timeframe downtrend (MTF {mtf_bias:+.2f}, RSI>{cb_mtf_rsi:.0f})",
                    None,
                )

        if max_open > 0 and open_count >= max_open:
            return None, conf, f"Waiting — max open trades reached ({open_count}/{max_open})", None
        if max_per_symbol > 0 and open_symbol_count >= max_per_symbol:
            return None, conf, f"Waiting — max trades per symbol reached ({open_symbol_count}/{max_per_symbol})", None
        cap_usdt = float(config.get("portfolio_max_exposure_usdt", 0) or 0)
        if cap_usdt > 0:
            planned = max(0.0, float(planned_buy_notional_usdt or 0.0))
            open_exp = max(0.0, float(open_portfolio_exposure_usdt or 0.0))
            if planned > 0 and (open_exp + planned) > cap_usdt + 1e-6:
                return (
                    None,
                    conf,
                    f"Waiting — portfolio exposure cap ({open_exp:.0f}+{planned:.0f}>{cap_usdt:.0f} USDT)",
                    None,
                )
        # —— 3.1) قاطع خسائر متتالية ——
        max_consec_losses = int(config.get("bot_max_consecutive_losses", 0) or 0)
        if max_consec_losses > 0:
            try:
                consec_losses = int(count_consecutive_losses(limit=500))
            except Exception:
                consec_losses = 0
            if consec_losses >= max_consec_losses:
                return (
                    None,
                    conf,
                    f"Stopped — consecutive losses guard ({consec_losses}/{max_consec_losses})",
                    None,
                )

    # —— 4) بيع بدون مركز ——
    if rec == "SELL" and not has_position:
        return None, conf, "Waiting — no open position (SELL skipped)", None

    # —— 4b) بيع بتوصية اللوحة أثناء الخسارة ——
    # مسار وقف الخسارة (_check_stop_loss) مستقل ولا يمر هنا؛ هنا نمنع بيع «إشارة SELL» فقط حتى لا يُغلق المركز بخسارة
    # قبل ضرب SL أو قبل قرار يدوي.
    if (
        rec == "SELL"
        and has_position
        and pos
        and isinstance(pos, dict)
        and bool(config.get("bot_block_ai_sell_while_losing", True))
    ):
        entry = _float(pos, "entry_price", 0)
        if entry > 0 and last_price > 0 and last_price < entry:
            return (
                None,
                conf,
                "انتظار — في الخسارة: بيع التوصية معطّل؛ الخروج بالخسارة عبر وقف الخسارة (أو البيع اليدوي)",
                None,
            )

    # —— 5) البيع التلقائي معطّل: لا يُنفَّذ بيع من «توصية البوت» (حد بيع/تتبع/SL ما زال عبر مسار السعر) ——
    if rec == "SELL" and not config.get("bot_auto_sell", False):
        return (
            None,
            conf,
            "Waiting — auto-sell off (SELL from AI panel is not executed; enable auto-sell in risk settings or use manual sell)",
            None,
        )

    # —— 5.0) حد بيع (لكل صف أو عام) لم يتحقق والسعر فوق الدخول — لا بيع بإشارة SELL قبل الهدف
    if rec == "SELL" and take_profit_barrier:
        return None, conf, "انتظار — حد البيع/هدف الربح لم يتحقق بعد (لا بيع بإشارة قبل الهدف)", None

    # —— 5.1) حدّ البيع (Limit Sell): لا نبيع قبل الوصول للهدف ——
    # افتراضياً: إذا كانت التوصية SELL (إشارة هبوط/بيع) نُنفّذ البيع حتى لو لم يُحقَّق هدف الربح —
    # حتى لا يبقى المركز معلّقاً والسعر ينزل. مسار «الهدف فقط» ما زال متاحاً عبر الإعداد أدناه.
    # البيع عند الوصول للهدف من دون انتظار SELL يبقى في لوحة التداول (_check_limit_sell).
    if rec == "SELL" and pos and isinstance(pos, dict):
        require_limit_before_signal_sell = bool(
            config.get("limit_sell_blocks_until_target", False)
        )
        entry = _float(pos, "entry_price", 0)
        if require_limit_before_signal_sell and entry > 0:
            ls_type = (config.get("limit_sell_type") or "percent").strip().lower()
            ls_val = _float(config, "limit_sell_value", 0)
            ls_price = _float(config, "limit_sell_price", 0)
            target = 0.0
            if ls_type == "price":
                target = ls_price if ls_price > 0 else 0.0
            else:
                if ls_val > 0:
                    target = entry * (1.0 + (ls_val / 100.0))
            if target <= 0:
                log.warning(
                    "limit_sell_blocks_until_target is on but global limit sell target is unset (value=0) — not blocking SELL signal"
                )
            if target > 0 and last_price < target:
                pct = ((last_price / entry) - 1.0) * 100.0
                tgt_pct = ((target / entry) - 1.0) * 100.0
                return None, conf, f"انتظار — حدّ البيع لم يتحقق بعد (الحالي {pct:+.2f}% / الهدف {tgt_pct:+.2f}%)", None

    # —— 6) السعر المباشر ضروري لتنفيذ الأمر (شراء أو بيع) ——
    if not last_price or last_price <= 0:
        return None, conf, "Waiting — live price not available", None

    # —— 7) فلاتر شراء إضافية (قوائم متقدّم، قمم، VWAP، ارتداد، توافق…) — تُتخطّى عندما bot_apply_execution_filters=False ——
    if rec == "BUY" and apply_execution_filters(config):
        aggressive_mode = (master_profile == "aggressive")
        buy_conditions = list(config.get("buy_conditions") or [])
        bearish_patterns = set(ind.get("candle_pattern_bearish") or [])
        bullish_patterns = set(ind.get("candle_pattern_bullish") or [])
        candle_score = _float(ind, "candle_pattern_score", 0)
        bullish_reversal_patterns = {
            "Hammer",
            "DragonflyDoji",
            "BullishEngulfing",
            "PiercingLine",
            "MorningStar",
            "TweezerBottoms",
            "ThreeInsideUp",
            "ThreeOutsideUp",
            "InverseHeadAndShoulders",
            "DoubleBottom",
        }
        strong_bullish_reversal = bool(bullish_patterns & bullish_reversal_patterns)
        rsi_here = _float(ind, "rsi", 50)
        macd_now = _float(ind, "macd", 0)
        signal_now = _float(ind, "signal", 0)
        hist_now = _float(ind, "hist", 0)
        macd_bear_now = (macd_now - signal_now) <= 0 or hist_now < 0
        # منع شراء عند هبوط «هيكلي» (3+ شموع / رأس وكتفين على فاصل ≥15m فقط في المكشوف)
        # (نجمة ساقطة/معلّق كثيراً تُخطَأ على الضوضاء وتمنع الشراء بلا داعٍ).
        bearish_hard_block = {
            "EveningStar",
            "ThreeBlackCrows",
            "HeadAndShoulders",  # يظهر في المؤشرات فقط عند فريم 15m/1h/… وليس على 1m
        }
        bearish_two_candle = {"BearishEngulfing", "DarkCloudCover"}
        # لا نمنع لمجرد اسم نمط هابط؛ نطلب تأكيد هبوطي فعلي (درجة شموع + زخم).
        if (bearish_patterns & bearish_hard_block) and candle_score <= -2 and macd_bear_now:
            # إذا ظهرت مع نمط صاعد قوي ودرجة عامة ليست هابطة بقوة، لا نمنع.
            if not (bullish_patterns and candle_score >= -1 and rsi_here < 64):
                return None, conf, "انتظار — نمط هابط متعدد الشموع مؤكَّد بزخم هابط", None
        if (bearish_patterns & bearish_two_candle) and rsi_here >= _mr("mr_bear_two_candle_rsi", 58.0) and candle_score <= -1 and macd_bear_now:
            return None, conf, "انتظار — ابتلاع/سحابة هابطة عند RSI مرتفع (تم منع الشراء)", None
        # درجة الشموع السلبية قد تُخفي انعكاس قاع حقيقي؛
        # لا نمنع إذا ظهر نموذج انعكاس صاعد قوي قرب الدعم مع RSI منخفض.
        if candle_score <= -3 and not (strong_bullish_reversal and rsi_here <= 51):
            return None, conf, "انتظار — ضغط شموعي هابط قوي (تم منع الشراء)", None

        adx_here = _float(ind, "adx14", 0)
        macd_here = _float(ind, "macd", 0)
        signal_here = _float(ind, "signal", 0)
        macd_diff = macd_here - signal_here
        vwap = _float(ind, "vwap", 0)
        r1 = _float(ind, "pivot_r1", 0)
        support_levels = [_float(ind, k, 0) for k in ("pivot_s1", "pivot_s2", "pivot_s3", "pivot_s4") if _float(ind, k, 0) > 0]
        at_support = any(abs(last_price - s) / s <= 0.008 for s in support_levels if s > 0)
        below_or_near_vwap = bool(vwap > 0 and last_price <= vwap * 1.004)
        near_oversold = bool(rsi_here <= _mr("mr_near_oversold_rsi", 50.0))
        mtf_supportive = bool(mtf_bias >= _h(_pval(0.45, 0.35, 0.20), _pval(0.62, 0.50, 0.35)))
        comp_buy_thr = 10.0
        comp_supportive = False
        if bot_uses_composite(config):
            try:
                comp_buy_thr = float(get_composite_thresholds()["buy"])
            except (TypeError, ValueError):
                comp_buy_thr = 10.0
            if composite_score is not None:
                try:
                    comp_supportive = float(composite_score) >= comp_buy_thr
                except (TypeError, ValueError):
                    comp_supportive = False

        # —— فتح المركز (BUY): قرب قمة محلية في نافذة الشموع — منع صريح قبل باقي الفلاتر
        wh_rec = _float(ind, "window_high_recent", 0.0)
        pct_below_wh = _float(ind, "pct_below_window_high", 999.0)
        tight_top_pct = _pval(0.17, 0.14, 0.12)
        near_local_top = bool(wh_rec > 0 and pct_below_wh >= 0.0 and pct_below_wh < tight_top_pct)

        # منع الشراء في سوق عرضي ضعيف: ADX منخفض + زخم MACD شبه مسطّح + RSI وسط (لا أفضلية واضحة)
        if (
            adx_here > 0
            and adx_here < _mr("mr_adx_chop_max", 16.0)
            and abs(macd_diff) < 0.015
            and _mr("mr_chop_rsi_lo", 47.0) <= rsi_here <= _mr("mr_chop_rsi_hi", 58.0)
        ):
            return None, conf, "انتظار — سوق عرضي ضعيف (ADX منخفض/زخم غير واضح)", None

        _atr14 = _float(ind, "atr14", 0.0)
        if _atr14 > 0 and last_price > 0:
            _atr_pct = (_atr14 / last_price) * 100.0
            if _atr_pct >= _mr("mr_atr_hi_pct", 0.8) and adx_here < _mr("mr_adx_chop_max", 16.0):
                conf = max(0.0, conf - 2.0)
        _st_line = _float(ind, "supertrend", 0.0)
        if _st_line > 0 and last_price > 0:
            _nr = _mr("mr_st_near_ratio", 0.002)
            if abs(last_price - _st_line) / last_price <= _nr and rsi_here >= _mr("mr_chop_rsi_hi", 58.0):
                conf = max(0.0, conf - 1.5)

        # لا دخول BUY أعلى من VWAP إلا إذا يوجد زخم صاعد واضح.
        # (كان الشرط القديم يمنع حتى عند ميل صاعد فعلي، فصار يسبب «VWAP بدون دعم كافٍ» بشكل زائد)
        trend_here = str(ind.get("trend", "") or "").upper()
        st_dir_here = int(_float(ind, "supertrend_dir", 0))
        st_k_here = _float(ind, "stoch_rsi_k", 50.0)
        st_d_here = _float(ind, "stoch_rsi_d", 50.0)
        # —— فلتر ارتداد اختياري: يعمل على فريم الشارت المختار حالياً (1m/5m/15m/1h/4h…)
        if bounce_filter_enabled(config):
            hist_prev_b = _float(ind, "hist_prev", hist_now)
            wl_rec = _float(ind, "window_low_recent", 0.0)
            pawl = _float(ind, "pct_above_window_low", 999.0)
            max_pct_above_low = float(config.get("bot_buy_bounce_max_pct_above_low", 0.5) or 0.5)
            near_local_bottom = bool(wl_rec > 0 and pawl >= 0.0 and pawl <= max_pct_above_low)
            ind_speed = str(config.get("indicator_speed_profile", "balanced") or "balanced").strip().lower()
            if ind_speed == "standard":
                ind_speed = "balanced"
            auto_bounce_tune = bool(config.get("bot_live_auto_tune_bounce", False))
            use_rsi_bounce = bool(config.get("bot_buy_bounce_use_rsi", True))
            rsi_ctx = float(config.get("bot_buy_bounce_context_rsi_max", 48.0) or 48.0)
            use_vwap_bounce = bool(config.get("bot_buy_bounce_use_vwap", True))
            vwap_mx = float(config.get("bot_buy_bounce_vwap_max_ratio", 1.006) or 1.006)
            use_stoch_bounce = bool(config.get("bot_buy_bounce_use_stoch", True))
            stoch_k_mx = float(config.get("bot_buy_bounce_stoch_k_max", 58.0) or 58.0)
            use_macd_bounce = bool(config.get("bot_buy_bounce_use_macd", True))
            macd_diff_min = float(config.get("bot_buy_bounce_macd_diff_min", -0.025) or -0.025)
            use_adx_bounce = bool(config.get("bot_buy_bounce_use_adx", False))
            adx_min_bounce = float(config.get("bot_buy_bounce_adx_min", 14.0) or 14.0)
            if auto_bounce_tune:
                use_rsi_bounce = True
                use_vwap_bounce = True
                use_stoch_bounce = True
                use_macd_bounce = True
                use_adx_bounce = True
                vol_pct = _float(ind, "volatility_pct", 0.0)
                _adx_tf0 = _mr("mr_adx_trend_floor", 18.0)
                rsi_ctx = max(42.0, min(58.0, 46.0 + max(0.0, min(8.0, adx_here - _adx_tf0)) * 0.7))
                vwap_mx = max(1.0010, min(1.0100, 1.0035 + max(0.0, min(2.5, vol_pct)) * 0.0012))
                stoch_k_mx = max(34.0, min(72.0, 46.0 + max(0.0, min(20.0, adx_here - max(10.0, _adx_tf0 - 6.0))) * 1.0))
                macd_diff_min = max(-0.06, min(0.02, -0.028 + max(0.0, min(2.5, vol_pct)) * 0.006))
                adx_min_bounce = max(10.0, min(24.0, 12.0 + max(0.0, min(10.0, vol_pct)) * 0.8))

            vwap_ctx_ok = bool(
                use_vwap_bounce and vwap > 0 and last_price > 0 and last_price <= vwap * vwap_mx
            )
            rsi_ctx_ok = bool(use_rsi_bounce and rsi_here <= rsi_ctx)
            dip_ok = bool(
                near_local_bottom
                or rsi_ctx_ok
                or vwap_ctx_ok
                or at_support
                or strong_bullish_reversal
            )
            prev_c = _float(ind, "prev_close", 0.0)
            price_up_bar = bool(last_price > 0 and prev_c > 0 and last_price > prev_c)
            hist_turning_up = bool(hist_now > hist_prev_b)
            stoch_early_cross = bool(use_stoch_bounce and st_k_here > st_d_here and st_k_here <= stoch_k_mx)
            macd_turn_ok = bool(use_macd_bounce and macd_diff > macd_diff_min)
            turn_ok = bool(
                hist_turning_up
                or stoch_early_cross
                or (price_up_bar and hist_now >= hist_prev_b and macd_turn_ok)
            )
            if use_adx_bounce and adx_here < adx_min_bounce:
                turn_ok = False
            if near_local_top:
                return (
                    None,
                    conf,
                    "انتظار — فلتر الارتداد: قرب قمة محلية (لا شراء)",
                    None,
                )
            if not dip_ok or not turn_ok:
                return (
                    None,
                    conf,
                    "انتظار — فلتر الارتداد المبكر غير متحقق (قاع/زخم)",
                    None,
                )
        cci_here = _float(ind, "cci20", 0.0)
        ema9_here = _float(ind, "ema9", 0.0)
        ema21_here = _float(ind, "ema21", 0.0)
        _adx_tf = _mr("mr_adx_trend_floor", 18.0)
        bullish_momentum_ok = bool(
            (trend_here == "UP")
            and (st_dir_here == 1)
            and (macd_diff > 0)
            and (adx_here >= _adx_tf)
        )
        if vwap > 0 and last_price > vwap * 1.018 and not at_support and mtf_bias < 0.10 and not bullish_momentum_ok:
            return None, conf, "انتظار — السعر أعلى VWAP بدون دعم كافٍ", None

        # لا نطارد القمة: منع شراء عندما يكون السعر متمدداً أعلى VWAP مع RSI مرتفع.
        if rsi_here >= _mr("mr_vwap_chase_rsi", 69.0) and vwap > 0 and last_price > vwap * 1.014 and not at_support:
            return None, conf, "انتظار — شراء قرب قمة ممتدة (RSI مرتفع والسعر بعيد عن VWAP)", None

        # فلتر قمة سريع: عند التشبّع السريع (StochRSI/CCI) لا نشتري حتى لو باقي المؤشرات قوية.
        top_k_th = _mr("mr_fast_top_stoch_k", 97.0)
        top_d_th = _mr("mr_fast_top_stoch_d", 94.0)
        top_cci_th = _pval(140.0, 170.0, 190.0)
        top_vwap_mult = _pval(1.007, 1.010, 1.013)
        fast_top_zone = bool(
            st_k_here >= top_k_th
            and st_d_here >= top_d_th
            and cci_here >= top_cci_th
            and vwap > 0
            and last_price >= vwap * top_vwap_mult
            and not at_support
        )
        if fast_top_zone:
            return None, conf, "انتظار — تشبّع قمة سريع (StochRSI/CCI) يمنع الشراء", None

        # قمة سوينغ قصيرة الأجل (1m): منع افتراضي — سابقاً كان يعتمد على شرط «لا شراء عند القمة» في الإعدادات فقط.
        if at_real_peak is True and rsi_here >= _pval(52.0, 54.0, 56.0):
            if vwap > 0 and last_price > vwap * (1.0 + _h(0.0012, 0.0018)):
                return None, conf, "انتظار — قمة نافذة قصيرة فوق VWAP (تجنب شراء الذروة)", None

        # فتح مركز عند ذروة محلية (أعلى ~40 شمعة): لا نشتري مطاردةً فوق VWAP من دون دعم/تشبع بيع
        if near_local_top and rsi_here >= _pval(48.0, 50.0, 52.0):
            if vwap > 0 and last_price > vwap * 1.0015 and not at_support and not near_oversold:
                return (
                    None,
                    conf,
                    "انتظار — لا فتح مركز: السعر ملاصق لقمة محلية وفوق VWAP (بدون دعم)",
                    None,
                )

        # لا نلتقط سكيناً ساقطة: في اتجاه هابط قوي نطلب ارتداداً حقيقياً قبل BUY.
        rev_rsi_th = _pval(44.0, 47.0, 50.0)
        rev_macd_th = _pval(-0.015, -0.022, -0.030)
        rev_hist_th = _pval(-0.015, -0.022, -0.030)
        deep_mtf_floor = _h(_pval(-0.55, -0.65, -0.75), _pval(-0.42, -0.52, -0.62))
        deep_reversal_ok = bool(
            at_support
            and rsi_here <= rev_rsi_th
            and macd_diff > rev_macd_th
            and hist_now > rev_hist_th
        )
        if strong_bullish_reversal and at_support and rsi_here <= _pval(48.0, 52.0, 54.0):
            deep_reversal_ok = True
        aggressive_rebound_hint = bool(
            aggressive_mode
            and (at_support or below_or_near_vwap)
            and rsi_here <= _mr("mr_bot_aggr_hint_rsi", 54.0)
            and st_k_here >= st_d_here
            and st_k_here <= _mr("mr_aggr_rebound_stoch_max", 58.0)
            and (macd_diff > -0.02 or hist_now > -0.02 or strong_bullish_reversal)
        )
        if mtf_bias <= deep_mtf_floor and st_dir_here < 0 and macd_diff <= 0 and not deep_reversal_ok:
            if aggressive_rebound_hint:
                conf = max(conf, 60.0)
            else:
                return None, conf, "انتظار — هبوط قوي بدون ارتداد مؤكّد", None

        # أولوية شراء القاع: إذا RSI منخفض جداً وقرب دعم مع تحسن MACD نسمح بالدخول مبكراً.
        if rsi_here <= 38 and at_support and macd_diff > -0.01:
            conf = max(conf, 60.0)

        # تشديد الدخول: نطلب توافقاً كافياً من إشارات داعمة (المركّب يُحسب فقط عند تفعيل دمج المركّب)
        _conf_parts = [
            at_support,
            below_or_near_vwap,
            near_oversold,
            mtf_supportive,
        ]
        if bot_uses_composite(config):
            _conf_parts.append(comp_supportive)
        confluence_count = sum(1 for ok in _conf_parts if ok)
        # متابعة ترند كانت تخفّض شرط التوافق إلى 1 فتفتح مراكز متأخرة عند القمة — نلغيها قرب أعلى النافذة
        trend_continuation_ok = bool(
            (trend_here == "UP")
            and (st_dir_here == 1)
            and (macd_diff > 0)
            and (adx_here >= _adx_tf)
            and (ema9_here > 0 and ema21_here > 0 and last_price >= min(ema9_here, ema21_here) * 0.998)
            and not near_local_top
        )
        # إشارة انعكاس سريعة قرب القاع: تسمح بدخول مبكر قبل اكتمال كل الفلاتر الثقيلة.
        fast_bottom_rsi_th = _mr("mr_fast_bottom_rsi_line", 48.0)
        fast_bottom_stoch_th = _mr("mr_fast_bottom_stoch", 34.0)
        fast_bottom_macd_th = _pval(-0.015, -0.022, -0.030)
        fast_bottom_hist_th = _pval(-0.012, -0.018, -0.025)
        fast_bottom_reversal = bool(
            (at_support or below_or_near_vwap)
            and rsi_here <= fast_bottom_rsi_th
            and st_k_here <= fast_bottom_stoch_th
            and st_k_here >= st_d_here
            and (macd_diff > fast_bottom_macd_th or hist_now > fast_bottom_hist_th)
        )
        candle_bottom_reversal = bool(
            strong_bullish_reversal
            and (at_support or below_or_near_vwap)
            and rsi_here <= _pval(50.0, 54.0, 56.0)
            and (macd_diff > -0.03 or hist_now > -0.03)
        )
        fast_bottom_reversal = bool(fast_bottom_reversal or candle_bottom_reversal)
        if fast_bottom_reversal:
            conf = max(conf, 62.0 if aggressive_mode else 58.0)

        if strong_bullish_reversal and at_support:
            conf = max(conf, _pval(62.0, 60.0, 58.0))
        # قصير الأجل: توافق واحد من 5 كافٍ (سابقاً 2 فكان يمنع الشراء كثيراً عند سعر فوق VWAP وRSI ليس منخفضاً)
        base_need = 1
        need_confluence = max(base_need, 2) if trade_horizon == "swing" else base_need
        # في الهجومي: عند ارتداد قاع سريع نسمح بدخول أسرع (إشارة الارتداد + مؤشر مساعد واحد).
        if aggressive_mode and fast_bottom_reversal:
            need_confluence = 1
        # متابعة ترند صاعدة مؤكدة (كان يُحسب سابقاً ولا يُستخدم — فيبقى الشراء محجوباً بلا داعٍ)
        if trend_continuation_ok:
            confluence_count = max(confluence_count, need_confluence)
        if confluence_count < need_confluence:
            flags = [
                ("قرب دعم S1..S4", at_support),
                ("السعر تحت/قرب VWAP", below_or_near_vwap),
                ("RSI منخفض (حسب البروفايل)", near_oversold),
                ("انحياز أطر أعلى داعم", mtf_supportive),
            ]
            if bot_uses_composite(config):
                flags.append((f"المركّب داعم (>={comp_buy_thr:g})", comp_supportive))
            ok_list = [name for name, ok in flags if ok]
            no_list = [name for name, ok in flags if not ok]
            ok_txt = "، ".join(ok_list) if ok_list else "لا شيء"
            no_txt = "، ".join(no_list) if no_list else "لا شيء"
            return (
                None,
                conf,
                f"انتظار — توافق الشراء غير كافٍ ({confluence_count}/{need_confluence}). "
                f"المتحقق: {ok_txt}. غير المتحقق: {no_txt}",
                None,
            )

        # منع التعزيز في الخسارة إلا مع إشارة انعكاس واضحة — يقلّل الشراء في مناطق خاطئة.
        if has_position and isinstance(pos, dict):
            open_entry = _float(pos, "entry_price", 0.0)
            if open_entry > 0 and last_price > 0:
                open_pnl_pct = ((last_price / open_entry) - 1.0) * 100.0
                loser_block_pct = _pval(-0.30, -0.45, -0.60)
                loser_rsi_th = _pval(44.0, 47.0, 50.0)
                loser_stoch_th = _mr("mr_loser_stoch", 34.0)
                loser_macd_th = _pval(-0.015, -0.022, -0.030)
                loser_hist_th = _pval(-0.015, -0.022, -0.030)
                add_to_loser_ok = bool(
                    at_support
                    and rsi_here <= loser_rsi_th
                    and st_k_here <= loser_stoch_th
                    and macd_diff > loser_macd_th
                    and hist_now > loser_hist_th
                )
                if open_pnl_pct <= loser_block_pct and not add_to_loser_ok:
                    return None, conf, "انتظار — المركز الحالي خاسر ولا توجد إشارة انعكاس كافية", None

        # الثقة الأدنى مُطبَّقة مسبقاً بخطوة (2) عبر conf_min؛ لا نفرض أرضية ثابتة أعلى من إعداد المستخدم
        # (كانت 58% تتعارض مع presets مثل scalping عند bot_confidence_min=40).

        if apply_private_condition_lists_for_strategy(config):
            if "no_buy_at_peak" in buy_conditions and candle_high and candle_high > 0 and last_price >= candle_high * 0.998:
                if at_real_peak is True:
                    return None, conf, "انتظار — شرط الشراء: عدم الشراء عند القمة (تم المنع)", None
                # at_real_peak is None: لا نمنع — لم نُثبت قمة سوينغ؛ False = ليس عند قمة السوينغ
            if "no_buy_at_r1" in buy_conditions and r1 > 0 and last_price >= r1 * 0.998:
                if not at_support:
                    return None, conf, "انتظار — شرط الشراء: عدم الشراء عند مقاومة R1 (تم المنع)", None
            vwap_block_mult = 1.005 if aggressive_mode else 1.012
            if "below_vwap" in buy_conditions and vwap > 0 and last_price > vwap * vwap_block_mult:
                rsi = _float(ind, "rsi", 100)
                if not (rsi < 32 or at_support or fast_bottom_reversal or strong_bullish_reversal):
                    return None, conf, "انتظار — شرط الشراء: الشراء تحت VWAP فقط (تم المنع)", None
            if "at_support" in buy_conditions and not at_support:
                return None, conf, "انتظار — شرط الشراء: الشراء عند الدعم فقط (تم المنع)", None
        # فلتر متعدد الأطر: لا شراء عكس اتجاه 1h/4h الهابط بوضوح إلا قرب دعم
        if mtf_bias <= -0.8 and not at_support:
            return None, conf, "انتظار — الاتجاه العام (1h/4h) هابط بوضوح", None

    # —— 8) حد الخسارة اليومية ——
    limit = _float(config, "daily_loss_limit_usdt", 0)
    if limit > 0 and daily_pnl <= -limit:
        return None, conf, "Stopped — daily loss limit reached", None

    # —— 9) حد صفقات الشراء في اليوم ——
    max_per_day = int(config.get("max_trades_per_day", 0) or 0)
    if rec == "BUY" and max_per_day > 0:
        today_count = count_buy_trades_today()
        if today_count >= max_per_day:
            return None, conf, "Stopped — max trades per day reached", None

    return rec, conf, None, None
