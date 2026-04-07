# config.py — إعدادات عامة للتطبيق (رمز افتراضي، مبلغ، رافعة، حد الخسارة اليومية)
import copy
import json
import logging
import os

log = logging.getLogger("trading.config")

# تخزين مؤقت لنتيجة load_config طالما لم يتغيّر الملف على القرص — يقلّل تجمّد الواجهة عند تحديثات متكررة.
_config_disk_cache: tuple[float | None, dict] | None = None


def _config_file_mtime() -> float | None:
    path = _config_path()
    try:
        return os.path.getmtime(path) if os.path.isfile(path) else None
    except OSError:
        return None


def invalidate_config_disk_cache() -> None:
    global _config_disk_cache
    _config_disk_cache = None


def load_config_cached() -> dict:
    """
    نفس منطق load_config مع إعادة القراءة من القرص فقط عند تغيّر الملف (mtime).
    يُرجع نسخة سطحية للاستخدام القرائي؛ لا تُعدَّل ثم تُعتمد كمصدر وحيد دون save_config.
    """
    global _config_disk_cache
    mtime = _config_file_mtime()
    if _config_disk_cache is not None and _config_disk_cache[0] == mtime:
        return dict(_config_disk_cache[1])
    fresh = load_config()
    _config_disk_cache = (mtime, fresh)
    return dict(fresh)


def _config_path():
    base = os.environ.get("APPDATA") or os.environ.get("HOME") or "."
    folder = os.path.join(base, "CryptoTrading")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "config.json")


DEFAULTS = {
    "exchange": "binance",        # منصة التداول: binance، bitget (لاحقاً)
    "default_symbol": "BTCUSDT",
    # قائمة مرجعية منفصلة عن favorite_symbols — عملات تضعها أنت للمراقبة (لا تغيّر المفضلة)
    "expected_symbols": [],
    # دراسة السوق من نافذة «المتوقعة»: مرشحون بحجم تداول 24h (سيولة) وليس بصعود أمس؛ التحليل على 4h أو 1d
    "expected_study_indicator_count": 18,
    "expected_study_chart_interval": "4h",  # 4h | 1d مفضّل لرؤية «أيام» وليس دقائق
    "expected_study_merge_count": 5,
    "expected_study_min_quote_volume_usdt": 10_000_000.0,  # أقل سيولة يومية (USDT) لدخول مجال الدراسة
    # استبعاد الرموز التي صعدت أمس أكثر من هذا % (0 = تعطيل الاستبعاد — قد يعيد ضخ أمس للقائمة)
    "expected_study_exclude_24h_gain_above_pct": 28.0,
    "amount_usdt": 50,
    "amount_type": "value",       # "value" = USDT ثابت، "percent" = نسبة من الرصيد
    "amount_percent": 10.0,      # عند amount_type=percent
    "leverage": 10,               # 1..125
    # سبوت (فوري) مقابل عقود (Futures): auto = حسب الرافعة (>1 فيوتشر)، spot/futures = إجبار النوع
    "market_type": "auto",       # "auto" | "spot" | "futures"
    "bot_max_open_trades": 1,     # أقصى صفقات مفتوحة معاً؛ ضع 0 لتعطيل الحد (بدون قيد)
    "portfolio_max_exposure_usdt": 0,  # سقف إجمالي لتعرّض المراكز + صفقة الشراء القادمة بالـ USDT (0 = معطّل)
    "max_trades_per_day": 0,      # حد أقصى لعدد صفقات الشراء في اليوم (0 = معطّل)
    "max_trades_per_symbol": 0,   # حد أقصى لعدد الصفقات المفتوحة لنفس الرمز (0 = معطّل)
    "bot_same_symbol_buy_min_interval_min": 5,  # أقل فاصل (دقيقة) بين شراءين تلقائيين لنفس الرمز (1 كانت تسمح بتكديس قريب جداً)
    # شراء 15m فقط: يشترط «قاع محتمل» + انقلاب زخم مبكر (يعطّل افتراضياً — يُفعّل من المخاطر)
    "bot_buy_require_early_bounce_15m": False,
    "bot_buy_bounce_max_pct_above_low": 0.5,  # أقصى % فوق أدنى آخر 40 شمعة يُعدّ «قريباً من القاع»
    "bot_buy_bounce_use_rsi": True,  # تضمين شرط RSI ضمن سياق القاع
    "bot_buy_bounce_context_rsi_max": 52.0,  # RSI ≤ هذا يُعدّ سياق هبوط/قاع (أو استخدم VWAP/دعم/نمط)
    "bot_buy_bounce_use_vwap": True,  # تضمين شرط VWAP ضمن فلتر Bounce 15m
    "bot_buy_bounce_vwap_max_ratio": 1.006,  # السعر ≤ VWAP×هذا؛ في الواجهة تُعرض كنسبة % = (هذا−1)×100
    "bot_buy_bounce_use_stoch": True,  # تضمين شرط StochRSI ضمن انعكاس الزخم
    "bot_buy_bounce_stoch_k_max": 58.0,  # StochRSI: K>D مع K≤هذا = انعكاس مبكر من النصف السفلي
    "bot_buy_bounce_use_adx": False,  # إن فُعّل: يشترط ADX ≥ الحد الأدنى قبل السماح بشراء الارتداد
    "bot_buy_bounce_adx_min": 14.0,
    "bot_buy_bounce_use_macd": True,  # تضمين شرط MACD diff ضمن انعكاس الزخم
    "bot_buy_bounce_macd_diff_min": -0.025,  # الحد الأدنى لفرق MACD-Signal في إشارة الانعكاس
    "bot_live_auto_tune_bounce": False,  # ضبط لحظي تلقائي لقيم Bounce (في كل الأوضاع عند التفعيل)
    "tp_type": "percent",         # "price" أو "percent"
    "tp_value": 2.0,              # نسبة مئوية أو سعر حسب tp_type
    "limit_buy_type": "percent", # "price" أو "percent" مثل وقف الخسارة
    "limit_buy_value": -2.0,     # نسبة مئوية (تحت السعر الحالي، مثل -2) أو سعر حسب النوع
    "limit_buy_price": 0.0,      # يُضبط من النقر على التوصية الأخضر (عند النوع سعر)
    "limit_buy_anchor_price": 0.0,  # مرجع النسبة المئوية (يُحفظ عند حفظ حد الشراء بلحظة سعر معروفة)
    "limit_sell_type": "percent", # "price" أو "percent" مثل حد الشراء
    "limit_sell_value": 0.0,      # 0 = معطّل حتى تضبط من زر حد البيع؛ البيع بإشارة البوت لا يُحجب بنسبة افتراضية
    "limit_sell_price": 0.0,      # سعر ثابت للبيع إن تم اختياره من الإعداد السريع
    # إن وُجد (مثل BTCUSDT): يُقارن حد الشراء/البيع بسعر هذا الزوج ويُنفَّذ الشراء/البيع عليه؛ فارغ = رمز الشارت
    "limit_orders_bind_symbol": "",
    "trailing_stop_pct": 3.0,     # بيع عند نزول السعر من القمة بهذه النسبة (وقف خسارة متحرك)
    "trailing_min_profit_pct": 5.0,  # تفعيل البيع عند النزول فقط إذا الربح الحالي >= هذه النسبة
    "sl_type": "percent",         # "price" أو "percent"
    "sl_value": -1.0,             # نسبة مئوية أو سعر حسب sl_type
    "daily_loss_limit_usdt": 0,  # 0 = معطّل
    # قاطع خسائر متتالية للشراء: إذا خسر البوت N صفقات مغلقة متتالية يمنع BUY مؤقتاً (0 = معطّل)
    "bot_max_consecutive_losses": 0,
    "bot_circuit_breaker_enabled": True,
    "bot_cb_volatility_pct_max": 1.8,   # مع ADX < bot_cb_adx_min: يمنع BUY (ليس التقلب وحده)
    "bot_cb_adx_min": 18.0,             # إذا ADX أقل من هذا مع تقلب ≥ الحد أعلاه → قاطع
    "bot_cb_mtf_bias_floor": -0.9,      # إذا انحياز 1h/4h أقل من هذا يمنع BUY
    "bot_cb_pause_minutes": 20,         # مدة التهدئة عند تفعيل القاطع
    "bot_cb_mtf_rsi_threshold": 45.0,   # مع انحياز MTF ≤ الأرضية: يمنع BUY إذا RSI أعلى من هذا
    # مفاتيح القاطع الرسمية (يُفضَّلها get_circuit_breaker_config؛ تُنسخ مع bot_cb_* عند الحفظ)
    "circuit_breaker_enabled": True,
    "circuit_breaker_volatility_pct_max": 1.8,
    "circuit_breaker_adx_min": 18.0,
    "circuit_breaker_mtf_bias_floor": -0.9,
    "circuit_breaker_pause_minutes": 20,
    "circuit_breaker_mtf_rsi_threshold": 45.0,
    "bot_confidence_min": 60,    # حد أدنى لثقة التوصية (%) 30–100 حتى ينفّذ البوت
    "bot_master_profile": "aggressive",  # ثابت: هجومي فقط (أُزيل محافظ/متوازن)
    "bot_trade_horizon": "short",  # نمط الصفقة: short | swing
    "bot_entry_profile": "aggressive",  # ثابت — واجهة الإعدادات لا تعرضه
    # ماسح السوق لسطر «العملة المقترحة» في لوحة التوصية
    "market_scanner_pool_size": 50,  # عدد العملات المرشحة أولياً من المنصة قبل تصفية أفضل 10
    "market_scanner_min_quote_volume_usdt": 5_000_000.0,  # أقل سيولة (Quote Volume) خلال 24h
    "market_scanner_min_change_pct": 0.3,  # أقل نسبة صعود 24h لتجنب السوق الجامد
    "market_scanner_min_range_pct": 1.0,  # أقل تذبذب يومي ((high-low)/last*100) لتجنب الحركة الضعيفة
    "bot_expected_value_gate_enabled": False,  # فلتر EV: عند True قد يمنع معظم عمليات الشراء رغم توصية اللوحة؛ يبقى قابلاً للتفعيل من المخاطر
    "bot_expected_value_min_pct": 0.03,  # الحد الأدنى للعائد المتوقع (%) للسماح بـ BUY
    "bot_expected_value_min_pct_trend_up": 0.01,
    "bot_expected_value_min_pct_trend_down": 0.08,
    "bot_expected_value_min_pct_range": 0.03,
    "bot_expected_value_min_pct_volatile": 0.12,
    "ml_wf_cost_per_trade_pct": 0.08,  # تكلفة ذهاب وإياب تقديرية %: walk-forward + خصم من EV في البوت (موحّد)
    "ml_wf_train_min": 40,  # أقل حجم تدريب قبل أول نافذة اختبار في walk-forward
    "ml_wf_test_window": 10,  # حجم نافذة الاختبار لكل fold في walk-forward
    "ml_train_recency_enabled": True,  # أوزان زمنية: الصفقات الأحدث أثقل في تدريب الغابة
    "ml_train_recency_min_weight": 0.35,  # وزن أقدم صفقة في نافذة التدريب (0.05–1)
    "ml_train_recency_max_weight": 1.0,  # وزن أحدث صفقة في نافذة التدريب
    # True = حفظ آخر تدريب ناجح دائماً (يتكيّف مع الخسائر الأخيرة). False = الاعتماد فقط إن تفوّق على الأفضل المحفوظ
    "ml_always_promote_latest": True,
    # عيّنات دخولها trend_down (نظام المحرّك): رفع وزنها في الغابة لتحسين تعلّم شراء الترند الهابط
    "ml_train_trend_down_boost_enabled": True,
    "ml_train_trend_down_weight_mult": 1.35,  # مضاعف ≥1 لكل صفقة market_regime=trend_down
    "bot_merge_composite": False,  # دمج المؤشر المركّب مع التوصية — اختياري من المخاطر (الافتراضي: قرار البوت = التوصية والفلاتر السابقة)
    # منع شراء البوت عند Supertrend هابط على أطر طويلة (افتراضي: 4h و1d) — يتطابق مع دمج المركّب
    "bot_block_buy_st_bear_htf_enabled": True,
    "bot_st_bear_block_chart_intervals": ["4h", "1d"],
    # عند ترند هابط + Supertrend هابط: لا يبقى «شراء» من اللوحة إلا إذا درجة المركّب ≥ العتبة الوسطى (وليس فقط عتبة الشراء)
    "bot_structural_bear_require_mid_composite": True,
    # إظهار نص المركّب (مثل «شراء قوي») في زاوية الشارت — معطّل يُخفي الشارة فقط؛ الصف العلوي والمحرّك لا يتغيران
    "chart_show_composite_badge": True,
    # عتبات المؤشر المركّب (درجة −100…100 + ADX لـ DI) — يُضبط تلقائياً عند التعارض (انظر composite_signal.clamp_composite_thresholds)
    # افتراضي قريب من «اتباع الاتجاه» مع تشديد بسيط: شراء/قوي أعلى قليلاً، ADX أعلى لاعتماد +DI/−DI عند ترند أوضح
    "composite_score_buy": 12.0,
    "composite_score_strong": 31.0,
    "composite_score_mid": 21.0,
    "composite_adx_for_di": 20.0,
    # لوحة الذكاء: إن True يحوّل المركّب «انتظار» إلى شراء/بيع عند درجة عالية (سلوك قديم؛ يسبب تضارباً مع قواعد RSI/الشموع)
    "ai_promote_wait_from_composite": False,
    # توجيه مسارات لوحة الذكاء حسب نظام السوق (signal_engine) — False = ترتيب قواعد موحّد قديم
    "ai_use_regime_router": True,
    # إن True: توصية SELL من البوت لا تُمنع بحاجز «هدف الربح/حد البيع» (لا يزال حد البيع التلقائي عند وصول السعر للهدف)
    "bot_signal_sell_bypass_tp_barrier": False,
    # إن True: التتبع (Trailing Stop) يتجاهل حاجز «هدف الصف/حد البيع» ويستطيع البيع قبل تحقق الهدف
    "bot_trailing_bypass_tp_barrier": False,
    "bot_auto_sell": False,   # False = البوت يشتري تلقائياً فقط، ولا يبيع إلا عند أمرك (زر البيع)
    # التتبع (Trailing) مستقل عن bot_auto_sell — يمكن تشغيله مع إيقاف البيع التلقائي العام
    "bot_trailing_enabled": True,
    # إن True: حد البيع/التتبع/الذروة/تشبع RSI لا يعمل إلا عند تشغيل زر الروبوت (إيقاف الروبوت = إيقاف هذه المسارات)
    "bot_auto_sell_requires_robot": False,
    # إن True: توصية SELL لا تُنفّذ حتى يتحقق «حد البيع» (هدف الربح). الافتراضي False = SELL يبيع مباشرة.
    "limit_sell_blocks_until_target": False,
    # إن True: توصية SELL من اللوحة لا تُنفّذ ما دام السعر تحت سعر الدخول — الخروج بالخسارة عبر وقف الخسارة (مسار السعر) فقط
    "bot_block_ai_sell_while_losing": True,
    "bot_auto_sl": True,      # السماح للبوت بتنفيذ وقف الخسارة تلقائياً حتى لو البيع التلقائي معطّل
    "buy_conditions": [],   # قائمة معرّفات شروط الشراء (في «بوت خاص») مثل at_support
    # قائمة معرّفات شروط البيع — فارغة = تفعيل كل مسارات البيع التلقائي (السلوك السابق)؛ غير فارغة = فقط المدرَج
    "sell_conditions": [],
    "sell_at_peak_min_profit_pct": 0.5,
    "sell_at_peak_swing_lookback": 15,
    "sell_at_peak_rsi_min": 0.0,
    "sell_at_overbought_rsi_min": 72.0,
    # أدنى ربح % قبل بيع التشبع؛ 0 كان يسمح ببيع عند ~صفر ربح ثم إعادة دخول بحد الشراء عند نفس السعر
    "sell_at_overbought_min_profit_pct": 0.35,
    # إن >0 ومسار sell_at_overbought مفعّل: لا يُنفَّذ حد الشراء ما دام RSI >= (عتبة التشبع − هذا الفرق)
    "sell_at_overbought_limit_buy_rsi_buffer": 5.0,
    # فلاتر decide المتقدّمة + قوائم شروط الشراء/البيع قبل التنفيذ — كل أوضاع الاستراتيجية (مفتاح واحد)
    "bot_apply_execution_filters": True,
    # قوائم buy_conditions / sell_conditions في «متقدّم»: هل تُطبَّق عند strategy_mode = قالب (سكالبينغ…)
    "apply_conditions_to_presets": True,
    # إعدادات أوامر الأمان (DCA)
    "safety_orders_count": 3,     # عدد أوامر الأمان لكل صفقة (1..4)
    "safety_order_step_pct": 3.0, # المسافة بين كل أمر أمان والذي بعده (% نزول السعر)
    "safety_order_volume_scale": 1.0,  # حجم أمر الأمان نسبةً لحجم الصفقة الأساسية (1.0 = نفس الحجم)
    "ai_score_min": 4,           # عتبة النقاط للتوصية (3–6): شراء عند score>=ai_score_min
    "use_ml_model": True,        # دمج تنبؤ النموذج مع التوصية (رفع/خفض الثقة)
    "ml_weight_pct": 30,         # وزن نموذج ML في دمج الثقة (0–100): 0=قواعد فقط، 100=نموذج فقط
    # حساسية المؤشرات/الشموع في websocket_manager + candlestick_patterns:
    # conservative = محافظة، balanced = متوازنة، fast = عالية/سريعة
    "indicator_speed_profile": "balanced",  # "conservative" | "balanced" | "fast"
    # Supertrend: فترة ATR ومضاعفها — القيم السابقة (10 و3) عريضة جداً فتبقى «هابط» طويلاً
    "supertrend_atr_period": 7,
    "supertrend_multiplier": 2.0,
    # +DI / -DI / ADX — فترة Wilder الشائعة 14 (كانت 7 في الكود رغم اسم plus_di14)
    "dmi_adx_period": 14,
    # إعدادات سكالبينغ (سريع)
    "scalp_adx_min": 25,         # فلتر اتجاه: ADX>=25
    # عتبات «حالة السوق» + محرّك الإشارة + decide (حزمة engine_market_readout_bundle)
    "market_readout_adx_strong_min": 30.0,
    "market_readout_rsi_overbought": 70.0,
    "market_readout_rsi_oversold": 30.0,
    "market_readout_rsi_ctx_high": 55.0,
    "market_readout_rsi_ctx_low": 45.0,
    "market_readout_stoch_overbought": 74.0,
    "market_readout_stoch_oversold": 26.0,
    "market_readout_stoch_band_lo": 45.0,
    "market_readout_stoch_band_hi": 55.0,
    "market_readout_stoch_mid_lo": 40.0,
    "market_readout_stoch_mid_hi": 60.0,
    "market_readout_stoch_kd_eps": 0.25,
    "market_readout_stoch_k_bull_min": 55.0,
    "market_readout_stoch_k_bear_max": 45.0,
    "market_readout_atr_high_vol_pct": 0.8,
    "market_readout_supertrend_near_ratio": 0.002,
    "scalp_tp_atr": 0.8,         # هدف الربح = ATR * multiplier
    "scalp_sl_atr": 0.6,         # وقف الخسارة = ATR * multiplier
    "language": "ar",             # "ar" = العربية، "en" = English
    "theme": "dark",              # "dark" = قاتم، "light" = فاتح
    "display_currency": "USD",    # عملة عرض القيم: USD، EUR (نضيف لاحقاً غيرها)
    "currency_rate_eur": 0.92,    # سعر 1 USDT باليورو (لتحويل القيمة والربح/الخسارة عند اختيار EUR)
    # إعدادات تنبيهات تيليجرام
    "telegram_enabled": False,
    "telegram_bot_token": "",
    "telegram_bot_username": "",   # معرف البوت للمراجعة فقط (مثل @gamal_bot) — لا يُستخدم في الإرسال
    "telegram_chat_id": "",
    # حالة الجلسة (تُحفظ عند الإغلاق وتُستعاد عند الفتح)
    "api_hint_shown": False,      # إظهار رسالة تذكير إعداد API عند أول تشغيل فقط
    "last_symbol": "BTCUSDT",     # آخر عملة معروضة
    "chart_interval": "1m",       # إطار الشارت (1m, 5m, 15m, 1h, 4h, 1d)
    "chart_visible_count": 0,     # عدد الشموع المعروضة (0 = تلقائي)
    "chart_view_start": 0,        # بداية العرض (فهرس الشمعة)
    "chart_y_zoom": 1.0,          # تكبير عمودي (مقياس السعر)
    "chart_y_pan": 0.0,           # إزاحة عمودية الشارت
    "show_sr_levels": True,       # إظهار/إخفاء خطوط الدعم/المقاومة (Pivot/S/R) على الشارت
    "splitter_main_sizes": [],    # أحجام المقسم الرئيسي [علوي، سفلي] — فارغ = تلقائي
    "strategy_mode": "custom",    # الاستراتيجية: custom | scalping | bounce | trend | auto (من الواجهة)
    # عند True: لوحة الملخص تقترح استراتيجية → بعد ثباتها (ثوانٍ) يُدمج preset ويُحفظ strategy_mode
    "bot_follow_suggested_strategy": True,
    "bot_follow_suggested_strategy_sec": 50,  # 15–300 بعد أول ظهور للمفتاح قبل تطبيق الإعدادات
    "first_real_order_done": False,  # بعد أول أمر حقيقي (LIVE) — يجب أن يبقى عبر التحميل من الملف
    # رابط HTTPS لملف JSON للتحقق من التحديثات (version، download_url) — فارغ = يطلب ضبط الرابط عند الضغط على «التحقق»
    "update_manifest_url": "",
    # eToro: حدود الرافعة المكتشفة من API (619/764) — 0 = غير معروف؛ تُملأ تلقائياً لتفادي إرسال 10x عندما الحساب 2x
    "etoro_user_max_leverage": 0,
    "etoro_user_min_leverage": 0,
}

# افتراضيات كل استراتيجية — لا تتضمن إعدادات يحددها المستخدم فقط:
# (المبلغ، الرافعة، نسبة التتبع، أدنى ربح لبدء التتبع، عدد أوامر الأمان، المسافة بين أوامر الأمان)
def _migrate_legacy_execution_filter_keys(out: dict, raw: dict) -> None:
    """إزالة المفاتيح الملغاة؛ ترحيل ملفات config القديمة إلى bot_apply_execution_filters."""
    raw_has_new = "bot_apply_execution_filters" in raw and raw["bot_apply_execution_filters"] is not None
    if raw_has_new:
        out["bot_apply_execution_filters"] = bool(raw["bot_apply_execution_filters"])
    elif "apply_conditions_to_presets" in raw or "bot_preset_strategy_only" in raw:
        sm = str(raw.get("strategy_mode") or out.get("strategy_mode") or "custom").strip().lower()
        if sm in ("custom", ""):
            out["bot_apply_execution_filters"] = not bool(raw.get("bot_preset_strategy_only", False))
        else:
            out["bot_apply_execution_filters"] = bool(raw.get("apply_conditions_to_presets", False))
    if "apply_conditions_to_presets" in raw and raw["apply_conditions_to_presets"] is not None:
        out["apply_conditions_to_presets"] = bool(raw["apply_conditions_to_presets"])
    out.pop("bot_preset_strategy_only", None)


STRATEGY_PRESETS = {
    "custom": {},
    "scalping": {
        "tp_type": "percent", "tp_value": 0.4,
        "sl_type": "percent", "sl_value": -0.3,
        "bot_confidence_min": 40,
        "ai_score_min": 3,
    },
    "bounce": {
        "tp_type": "percent", "tp_value": 2.0,
        "sl_type": "percent", "sl_value": -1.0,
        "bot_confidence_min": 50,
        "ai_score_min": 4,
    },
    "trend": {
        "tp_type": "percent", "tp_value": 3.0,
        "sl_type": "percent", "sl_value": -1.5,
        "bot_confidence_min": 55,
        "ai_score_min": 5,
        "composite_score_buy": 12.0,
        "composite_score_strong": 31.0,
        "composite_score_mid": 21.0,
        "composite_adx_for_di": 20.0,
    },
    "dca": {
        "tp_type": "percent", "tp_value": 1.2,
        "sl_type": "percent", "sl_value": -0.8,
        "bot_confidence_min": 45,
        "ai_score_min": 3,
    },
    "grid": {
        "tp_type": "percent", "tp_value": 1.5,
        "sl_type": "percent", "sl_value": -1.0,
        "bot_confidence_min": 48,
        "ai_score_min": 4,
    },
    "3commas": {
        "tp_type": "percent", "tp_value": 1.0,
        "sl_type": "percent", "sl_value": -0.6,
        "bot_confidence_min": 50,
        "ai_score_min": 3,
        "safety_orders_count": 4,
        "safety_order_step_pct": 2.0,
        "safety_order_volume_scale": 1.0,
        "trailing_stop_pct": 2.5,
        "trailing_min_profit_pct": 0.8,
    },
    "breakout": {
        "tp_type": "percent", "tp_value": 2.5,
        "sl_type": "percent", "sl_value": -0.8,
        "bot_confidence_min": 55,
        "ai_score_min": 5,
        "trailing_stop_pct": 2.0,
        "trailing_min_profit_pct": 1.0,
    },
}


def get_circuit_breaker_config(cfg: dict | None) -> dict:
    """
    إعدادات القاطع (Circuit Breaker) — تفضّل المفاتيح circuit_breaker_* ثم الرجوع إلى bot_cb_* / bot_circuit_breaker_*.
    """
    c = cfg if isinstance(cfg, dict) else {}

    def _pick(new_k: str, old_k: str, default):
        if new_k in c and c[new_k] is not None:
            return c[new_k]
        return c.get(old_k, default)

    return {
        "enabled": bool(_pick("circuit_breaker_enabled", "bot_circuit_breaker_enabled", True)),
        "volatility_pct_max": float(_pick("circuit_breaker_volatility_pct_max", "bot_cb_volatility_pct_max", 1.8) or 1.8),
        "adx_min": float(_pick("circuit_breaker_adx_min", "bot_cb_adx_min", 18.0) or 18.0),
        "mtf_bias_floor": float(_pick("circuit_breaker_mtf_bias_floor", "bot_cb_mtf_bias_floor", -0.9) or -0.9),
        "pause_minutes": int(_pick("circuit_breaker_pause_minutes", "bot_cb_pause_minutes", 20) or 20),
        "mtf_rsi_threshold": float(_pick("circuit_breaker_mtf_rsi_threshold", "bot_cb_mtf_rsi_threshold", 45.0) or 45.0),
    }


def merge_strategy_preset_into_config(cfg: dict | None, mode: str) -> dict:
    """
    نسخة من الإعدادات مع دمج STRATEGY_PRESETS وضبط strategy_mode.
    - custom: لا تغيير (يُعاد نفس الإعدادات).
    - auto: يضبط strategy_mode فقط (بدون preset ثابت).
    """
    base = DEFAULTS.copy()
    if isinstance(cfg, dict):
        base.update(cfg)
    out = copy.deepcopy(base)
    mode = (mode or "").strip().lower()
    if mode in ("", "custom"):
        return out
    if mode == "auto":
        out["strategy_mode"] = "auto"
        return out
    preset = STRATEGY_PRESETS.get(mode)
    if isinstance(preset, dict):
        for k, v in preset.items():
            out[k] = v
    out["strategy_mode"] = mode
    return out


def load_config():
    """تحميل الإعدادات من القرص."""
    path = _config_path()
    if not os.path.isfile(path):
        return DEFAULTS.copy()
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
        if not (text or "").strip():
            return DEFAULTS.copy()
        data = json.loads(text)
        out = DEFAULTS.copy()
        # دمج كل المفاتيح المعروفة من الملف (بما فيها حالة الجلسة last_symbol, chart_interval...)
        for k in DEFAULTS:
            if k in data and data[k] is not None:
                out[k] = data[k]
        # مفاتيح محفوظة سابقاً أو من إصدارات أخرى غير المدرجة بعد في DEFAULTS — لا نرميها
        for k, v in data.items():
            if k not in out:
                out[k] = v
        out.pop("bot_second_layer_buy_min_score", None)
        out.pop("bot_preset_full_buy_filters", None)
        _migrate_legacy_execution_filter_keys(out, data)
        return out
    except Exception as e:
        log.warning("Could not load config: %s", e)
        return DEFAULTS.copy()


def save_config(cfg: dict):
    """حفظ الإعدادات على القرص."""
    path = _config_path()
    try:
        to_save = dict(cfg)
        to_save.pop("bot_preset_strategy_only", None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2)
        log.info("Config saved")
        invalidate_config_disk_cache()
    except Exception:
        log.exception("Could not save config")
