/**
 * إعدادات المخاطر — مطابقة هيكل risk_settings_window.py + مفاتيح config.DEFAULTS
 * تُحفظ في localStorage فقط (لا تكتب config.json).
 */
(function (global) {
  var STORAGE = "cryptoweb_hub_risk_v2";
  var STORAGE_LEGACY = "cryptoweb_hub_risk_v1";

  var STRATEGY_PRESETS = {
    custom: {},
    auto: {},
    scalping: {
      tp_type: "percent",
      tp_value: 0.4,
      sl_type: "percent",
      sl_value: -0.3,
      bot_confidence_min: 40,
      ai_score_min: 3,
    },
    bounce: {
      tp_type: "percent",
      tp_value: 2.0,
      sl_type: "percent",
      sl_value: -1.0,
      bot_confidence_min: 50,
      ai_score_min: 4,
    },
    trend: {
      tp_type: "percent",
      tp_value: 3.0,
      sl_type: "percent",
      sl_value: -1.5,
      bot_confidence_min: 55,
      ai_score_min: 5,
    },
    dca: {
      tp_type: "percent",
      tp_value: 1.2,
      sl_type: "percent",
      sl_value: -0.8,
      bot_confidence_min: 45,
      ai_score_min: 3,
    },
    grid: {
      tp_type: "percent",
      tp_value: 1.5,
      sl_type: "percent",
      sl_value: -1.0,
      bot_confidence_min: 48,
      ai_score_min: 4,
    },
    "3commas": {
      tp_type: "percent",
      tp_value: 1.0,
      sl_type: "percent",
      sl_value: -0.6,
      bot_confidence_min: 50,
      ai_score_min: 3,
      safety_orders_count: 4,
      safety_order_step_pct: 2.0,
      safety_order_volume_scale: 1.0,
      trailing_stop_pct: 2.5,
      trailing_min_profit_pct: 0.8,
    },
    breakout: {
      tp_type: "percent",
      tp_value: 2.5,
      sl_type: "percent",
      sl_value: -0.8,
      bot_confidence_min: 55,
      ai_score_min: 5,
      trailing_stop_pct: 2.0,
      trailing_min_profit_pct: 1.0,
    },
  };

  function defaults() {
    return {
      bot_master_profile: "aggressive",
      bot_trade_horizon: "short",
      strategy_mode: "custom",
      amount_usdt: 50,
      amount_type: "value",
      amount_percent: 10,
      leverage: 10,
      market_type: "auto",
      update_manifest_url: "",
      bot_follow_suggested_strategy: true,
      bot_follow_suggested_strategy_sec: 50,
      bot_max_open_trades: 1,
      max_trades_per_day: 0,
      max_trades_per_symbol: 0,
      daily_loss_limit_usdt: 0,
      bot_max_consecutive_losses: 0,
      portfolio_max_exposure_usdt: 0,
      bot_same_symbol_buy_min_interval_min: 5,
      bot_circuit_breaker_enabled: true,
      bot_cb_volatility_pct_max: 1.8,
      bot_cb_adx_min: 18,
      bot_cb_mtf_bias_floor: -0.9,
      bot_cb_pause_minutes: 20,
      display_currency: "USD",
      currency_rate_eur: 0.92,
      market_scanner_pool_size: 50,
      market_scanner_min_quote_volume_usdt: 5000000,
      market_scanner_min_change_pct: 0.3,
      market_scanner_min_range_pct: 1.0,
      bot_confidence_min: 60,
      bot_entry_profile: "aggressive",
      bot_buy_require_early_bounce_15m: false,
      bot_live_auto_tune_bounce: false,
      bot_buy_bounce_use_rsi: true,
      bot_buy_bounce_context_rsi_max: 52.0,
      bot_buy_bounce_use_vwap: true,
      bot_buy_bounce_vwap_max_ratio: 1.006,
      bot_buy_bounce_use_stoch: true,
      bot_buy_bounce_stoch_k_max: 58.0,
      bot_buy_bounce_use_adx: false,
      bot_buy_bounce_adx_min: 14.0,
      bot_buy_bounce_use_macd: true,
      bot_buy_bounce_macd_diff_min: -0.025,
      bot_expected_value_gate_enabled: true,
      bot_expected_value_min_pct: 0.03,
      bot_expected_value_min_pct_trend_up: 0.01,
      bot_expected_value_min_pct_trend_down: 0.08,
      bot_expected_value_min_pct_range: 0.03,
      bot_expected_value_min_pct_volatile: 0.12,
      ml_wf_cost_per_trade_pct: 0.08,
      ml_wf_train_min: 40,
      ml_wf_test_window: 10,
      tp_type: "percent",
      tp_value: 2.0,
      sl_type: "percent",
      sl_value: -1.0,
      trailing_stop_pct: 3.0,
      trailing_min_profit_pct: 5.0,
      limit_buy_type: "percent",
      limit_buy_value: -2.0,
      limit_sell_type: "percent",
      limit_sell_value: 0,
      bot_auto_sell: false,
      bot_auto_sell_requires_robot: false,
      bot_block_ai_sell_while_losing: true,
      sell_at_overbought_rsi_min: 72.0,
      sell_at_overbought_min_profit_pct: 0.35,
      sell_at_peak_rsi_min: 0.0,
      sell_at_peak_min_profit_pct: 0.5,
      sell_at_overbought_limit_buy_rsi_buffer: 5.0,
      bot_merge_composite: false,
      composite_score_buy: 12.0,
      composite_score_strong: 31.0,
      composite_score_mid: 21.0,
      composite_adx_for_di: 20.0,
      ai_promote_wait_from_composite: false,
      ai_use_regime_router: true,
      limit_sell_blocks_until_target: false,
      bot_signal_sell_bypass_tp_barrier: false,
      bot_trailing_bypass_tp_barrier: false,
      bot_auto_sl: true,
      bot_trailing_enabled: true,
      bot_apply_execution_filters: true,
      apply_conditions_to_presets: true,
      ml_weight_pct: 30,
      safety_orders_count: 3,
      safety_order_step_pct: 3.0,
      safety_order_volume_scale: 1.0,
      buy_conditions: [],
      sell_conditions: [],
    };
  }

  function clone(d) {
    return JSON.parse(JSON.stringify(d));
  }

  /** مفتاح واحد؛ ترحيل مرة واحدة من localStorage القديم ثم حذف المفاتيح الملغاة */
  function normalizeExecFilters(o) {
    var savedACP = o.apply_conditions_to_presets;
    if (
      Object.prototype.hasOwnProperty.call(o, "bot_apply_execution_filters") &&
      o.bot_apply_execution_filters != null
    ) {
      o.bot_apply_execution_filters = !!o.bot_apply_execution_filters;
    } else {
      var sm = String(o.strategy_mode || "custom").toLowerCase();
      if (sm === "custom" || sm === "") {
        o.bot_apply_execution_filters =
          o.bot_preset_strategy_only != null ? !o.bot_preset_strategy_only : true;
      } else {
        o.bot_apply_execution_filters =
          o.apply_conditions_to_presets != null ? !!o.apply_conditions_to_presets : true;
      }
    }
    if (savedACP != null) {
      o.apply_conditions_to_presets = !!savedACP;
    } else if (o.apply_conditions_to_presets == null) {
      o.apply_conditions_to_presets = true;
    }
    delete o.bot_preset_strategy_only;
  }

  function loadMerged() {
    var o = clone(defaults());
    try {
      var r2 = localStorage.getItem(STORAGE);
      if (r2) {
        var p = JSON.parse(r2);
        if (p.strategy_preset != null && p.strategy_mode == null) {
          p.strategy_mode = p.strategy_preset;
        }
        for (var k in p) {
          if (Object.prototype.hasOwnProperty.call(p, k)) o[k] = p[k];
        }
        normalizeExecFilters(o);
        return o;
      }
      var r1 = localStorage.getItem(STORAGE_LEGACY);
      if (r1) {
        var p1 = JSON.parse(r1);
        if (p1.leverage != null) o.leverage = Math.min(125, Math.max(1, parseInt(p1.leverage, 10) || 10));
        if (p1.maxTrades != null) o.bot_max_open_trades = Math.max(0, parseInt(p1.maxTrades, 10) || 0);
      }
    } catch (e) {}
    normalizeExecFilters(o);
    return o;
  }

  function saveObj(o) {
    try {
      localStorage.setItem(STORAGE, JSON.stringify(o));
    } catch (e) {}
  }

  function setVal(id, v) {
    var el = document.getElementById(id);
    if (!el || v === undefined || v === null) return;
    if (el.type === "checkbox") {
      el.checked = !!v;
    } else {
      el.value = String(v);
    }
  }

  function getNum(id, def) {
    var el = document.getElementById(id);
    if (!el) return def;
    var v = parseFloat(el.value);
    return isNaN(v) ? def : v;
  }

  function getInt(id, def) {
    var el = document.getElementById(id);
    if (!el) return def;
    var v = parseInt(el.value, 10);
    return isNaN(v) ? def : v;
  }

  function getStr(id, def) {
    var el = document.getElementById(id);
    if (!el || el.value === "") return def;
    return el.value;
  }

  function getChk(id) {
    var el = document.getElementById(id);
    return el ? !!el.checked : false;
  }

  var BOUNCE_DETAIL_IDS = [
    "risk-bounce-tune",
    "risk-bounce-rsi",
    "risk-bounce-rsi-max",
    "risk-bounce-vwap",
    "risk-bounce-vwap-ratio",
    "risk-bounce-stoch",
    "risk-bounce-stoch-max",
    "risk-bounce-adx",
    "risk-bounce-adx-min",
    "risk-bounce-macd",
    "risk-bounce-macd-min",
  ];
  function syncBounceDetailControls() {
    var master = document.getElementById("risk-bounce-15m");
    var on = !!(master && master.checked);
    BOUNCE_DETAIL_IDS.forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.disabled = !on;
    });
  }

  function applyToForm(o) {
    setVal("risk-horizon", o.bot_trade_horizon);
    setVal("risk-strategy-mode", o.strategy_mode);
    setVal("risk-amount-usdt", o.amount_usdt);
    setVal("risk-amount-type", o.amount_type);
    setVal("risk-amount-percent", o.amount_percent);
    setVal("risk-leverage", Math.min(125, Math.max(1, parseInt(o.leverage, 10) || 10)));
    setVal("risk-market-type", o.market_type);
    setVal("risk-update-url", o.update_manifest_url);
    setVal("risk-follow-strategy", o.bot_follow_suggested_strategy);
    setVal("risk-follow-sec", o.bot_follow_suggested_strategy_sec);
    setVal("risk-daily-loss-usdt", o.daily_loss_limit_usdt);
    setVal("risk-max-trades-day", o.max_trades_per_day);
    setVal("risk-max-consec-losses", o.bot_max_consecutive_losses);
    setVal("risk-max-open-trades", o.bot_max_open_trades);
    setVal("risk-max-trades-symbol", o.max_trades_per_symbol);
    setVal("risk-portfolio-exposure", o.portfolio_max_exposure_usdt);
    setVal("risk-same-symbol-interval", o.bot_same_symbol_buy_min_interval_min);
    setVal("risk-cb-enabled", o.bot_circuit_breaker_enabled);
    setVal("risk-cb-vol", o.bot_cb_volatility_pct_max);
    setVal("risk-cb-adx", o.bot_cb_adx_min);
    setVal("risk-cb-mtf", o.bot_cb_mtf_bias_floor);
    setVal("risk-cb-pause", o.bot_cb_pause_minutes);
    setVal("risk-display-currency", o.display_currency);
    setVal("risk-eur-rate", o.currency_rate_eur);
    setVal("risk-scanner-pool", o.market_scanner_pool_size);
    setVal("risk-scanner-vol", o.market_scanner_min_quote_volume_usdt);
    setVal("risk-scanner-change", o.market_scanner_min_change_pct);
    setVal("risk-scanner-range", o.market_scanner_min_range_pct);
    setVal("risk-bot-confidence", o.bot_confidence_min);
    setVal("risk-bounce-15m", o.bot_buy_require_early_bounce_15m);
    setVal("risk-bounce-tune", o.bot_live_auto_tune_bounce);
    setVal("risk-bounce-rsi", o.bot_buy_bounce_use_rsi);
    setVal("risk-bounce-rsi-max", o.bot_buy_bounce_context_rsi_max);
    setVal("risk-bounce-vwap", o.bot_buy_bounce_use_vwap);
    setVal("risk-bounce-vwap-ratio", o.bot_buy_bounce_vwap_max_ratio);
    setVal("risk-bounce-stoch", o.bot_buy_bounce_use_stoch);
    setVal("risk-bounce-stoch-max", o.bot_buy_bounce_stoch_k_max);
    setVal("risk-bounce-adx", o.bot_buy_bounce_use_adx);
    setVal("risk-bounce-adx-min", o.bot_buy_bounce_adx_min);
    setVal("risk-bounce-macd", o.bot_buy_bounce_use_macd);
    setVal("risk-bounce-macd-min", o.bot_buy_bounce_macd_diff_min);
    setVal("risk-ev-enabled", o.bot_expected_value_gate_enabled);
    setVal("risk-ev-min", o.bot_expected_value_min_pct);
    setVal("risk-ev-up", o.bot_expected_value_min_pct_trend_up);
    setVal("risk-ev-down", o.bot_expected_value_min_pct_trend_down);
    setVal("risk-ev-range", o.bot_expected_value_min_pct_range);
    setVal("risk-ev-volatile", o.bot_expected_value_min_pct_volatile);
    setVal("risk-ml-wf-cost", o.ml_wf_cost_per_trade_pct);
    setVal("risk-ml-wf-train", o.ml_wf_train_min);
    setVal("risk-ml-wf-test", o.ml_wf_test_window);
    setVal("risk-tp-type", o.tp_type);
    setVal("risk-tp-value", o.tp_value);
    setVal("risk-sl-type", o.sl_type);
    setVal("risk-sl-value", o.sl_value);
    setVal("risk-trailing-pct", o.trailing_stop_pct);
    setVal("risk-trailing-min-profit", o.trailing_min_profit_pct);
    setVal("risk-limit-buy", o.limit_buy_value);
    setVal("risk-limit-sell", o.limit_sell_value);
    setVal("risk-bot-auto-sell", o.bot_auto_sell);
    setVal("risk-bot-auto-sell-robot", o.bot_auto_sell_requires_robot);
    setVal("risk-bot-block-sell-loss", o.bot_block_ai_sell_while_losing);
    setVal("risk-sell-ob-rsi", o.sell_at_overbought_rsi_min);
    setVal("risk-sell-ob-profit", o.sell_at_overbought_min_profit_pct);
    setVal("risk-sell-peak-rsi", o.sell_at_peak_rsi_min);
    setVal("risk-sell-peak-profit", o.sell_at_peak_min_profit_pct);
    setVal("risk-sell-ob-buffer", o.sell_at_overbought_limit_buy_rsi_buffer);
    setVal("risk-merge-composite", o.bot_merge_composite);
    setVal("risk-comp-buy", o.composite_score_buy);
    setVal("risk-comp-strong", o.composite_score_strong);
    setVal("risk-comp-mid", o.composite_score_mid);
    setVal("risk-comp-adx", o.composite_adx_for_di);
    setVal("risk-ai-promote-wait", o.ai_promote_wait_from_composite);
    setVal("risk-ai-regime", o.ai_use_regime_router);
    setVal("risk-limit-sell-blocks", o.limit_sell_blocks_until_target);
    setVal("risk-signal-bypass-tp", o.bot_signal_sell_bypass_tp_barrier);
    setVal("risk-trail-bypass-tp", o.bot_trailing_bypass_tp_barrier);
    setVal("risk-bot-auto-sl", o.bot_auto_sl);
    setVal("risk-bot-trailing", o.bot_trailing_enabled);
    setVal("risk-apply-exec-filters", o.bot_apply_execution_filters);
    setVal("risk-apply-conditions-presets", o.apply_conditions_to_presets !== false);
    setVal("risk-ml-weight", o.ml_weight_pct);
    setVal("risk-dca-count", o.safety_orders_count);
    setVal("risk-dca-step", o.safety_order_step_pct);
    setVal("risk-dca-vol", o.safety_order_volume_scale);
    setMulti("risk-buy-conditions", o.buy_conditions || []);
    setMulti("risk-sell-conditions", o.sell_conditions || []);
    syncBounceDetailControls();
  }

  function setMulti(id, arr) {
    var el = document.getElementById(id);
    if (!el || !el.options) return;
    var set = {};
    (arr || []).forEach(function (x) {
      set[String(x)] = true;
    });
    for (var i = 0; i < el.options.length; i++) {
      el.options[i].selected = !!set[el.options[i].value];
    }
  }

  function getMulti(id) {
    var el = document.getElementById(id);
    if (!el || !el.options) return [];
    var out = [];
    for (var i = 0; i < el.options.length; i++) {
      if (el.options[i].selected) out.push(el.options[i].value);
    }
    return out;
  }

  function collectFromForm() {
    var o = clone(defaults());
    o.bot_master_profile = "aggressive";
    o.bot_trade_horizon = getStr("risk-horizon", o.bot_trade_horizon);
    o.strategy_mode = getStr("risk-strategy-mode", o.strategy_mode);
    o.amount_usdt = Math.max(1, getInt("risk-amount-usdt", o.amount_usdt));
    o.amount_type = getStr("risk-amount-type", o.amount_type);
    o.amount_percent = getNum("risk-amount-percent", o.amount_percent);
    o.leverage = Math.min(125, Math.max(1, getInt("risk-leverage", o.leverage)));
    o.market_type = getStr("risk-market-type", o.market_type);
    o.update_manifest_url = getStr("risk-update-url", o.update_manifest_url);
    o.bot_follow_suggested_strategy = getChk("risk-follow-strategy");
    o.bot_follow_suggested_strategy_sec = Math.min(300, Math.max(15, getInt("risk-follow-sec", o.bot_follow_suggested_strategy_sec)));
    o.daily_loss_limit_usdt = Math.max(0, getInt("risk-daily-loss-usdt", o.daily_loss_limit_usdt));
    o.max_trades_per_day = Math.max(0, getInt("risk-max-trades-day", o.max_trades_per_day));
    o.bot_max_consecutive_losses = Math.max(0, getInt("risk-max-consec-losses", o.bot_max_consecutive_losses));
    o.bot_max_open_trades = Math.max(0, getInt("risk-max-open-trades", o.bot_max_open_trades));
    o.max_trades_per_symbol = Math.max(0, getInt("risk-max-trades-symbol", o.max_trades_per_symbol));
    o.portfolio_max_exposure_usdt = Math.max(0, getInt("risk-portfolio-exposure", o.portfolio_max_exposure_usdt));
    o.bot_same_symbol_buy_min_interval_min = Math.max(0, getInt("risk-same-symbol-interval", o.bot_same_symbol_buy_min_interval_min));
    o.bot_circuit_breaker_enabled = getChk("risk-cb-enabled");
    o.bot_cb_volatility_pct_max = getNum("risk-cb-vol", o.bot_cb_volatility_pct_max);
    o.bot_cb_adx_min = getNum("risk-cb-adx", o.bot_cb_adx_min);
    o.bot_cb_mtf_bias_floor = getNum("risk-cb-mtf", o.bot_cb_mtf_bias_floor);
    o.bot_cb_pause_minutes = Math.max(1, getInt("risk-cb-pause", o.bot_cb_pause_minutes));
    o.display_currency = getStr("risk-display-currency", o.display_currency);
    o.currency_rate_eur = getNum("risk-eur-rate", o.currency_rate_eur);
    o.market_scanner_pool_size = Math.max(10, getInt("risk-scanner-pool", o.market_scanner_pool_size));
    o.market_scanner_min_quote_volume_usdt = Math.max(100000, getInt("risk-scanner-vol", o.market_scanner_min_quote_volume_usdt));
    o.market_scanner_min_change_pct = getNum("risk-scanner-change", o.market_scanner_min_change_pct);
    o.market_scanner_min_range_pct = Math.max(0, getNum("risk-scanner-range", o.market_scanner_min_range_pct));
    o.bot_confidence_min = Math.min(100, Math.max(30, getInt("risk-bot-confidence", o.bot_confidence_min)));
    o.bot_entry_profile = "aggressive";
    o.bot_buy_require_early_bounce_15m = getChk("risk-bounce-15m");
    o.bot_live_auto_tune_bounce = getChk("risk-bounce-tune");
    o.bot_buy_bounce_use_rsi = getChk("risk-bounce-rsi");
    o.bot_buy_bounce_context_rsi_max = getNum("risk-bounce-rsi-max", o.bot_buy_bounce_context_rsi_max);
    o.bot_buy_bounce_use_vwap = getChk("risk-bounce-vwap");
    o.bot_buy_bounce_vwap_max_ratio = getNum("risk-bounce-vwap-ratio", o.bot_buy_bounce_vwap_max_ratio);
    o.bot_buy_bounce_use_stoch = getChk("risk-bounce-stoch");
    o.bot_buy_bounce_stoch_k_max = getNum("risk-bounce-stoch-max", o.bot_buy_bounce_stoch_k_max);
    o.bot_buy_bounce_use_adx = getChk("risk-bounce-adx");
    o.bot_buy_bounce_adx_min = getNum("risk-bounce-adx-min", o.bot_buy_bounce_adx_min);
    o.bot_buy_bounce_use_macd = getChk("risk-bounce-macd");
    o.bot_buy_bounce_macd_diff_min = getNum("risk-bounce-macd-min", o.bot_buy_bounce_macd_diff_min);
    o.bot_expected_value_gate_enabled = getChk("risk-ev-enabled");
    o.bot_expected_value_min_pct = getNum("risk-ev-min", o.bot_expected_value_min_pct);
    o.bot_expected_value_min_pct_trend_up = getNum("risk-ev-up", o.bot_expected_value_min_pct_trend_up);
    o.bot_expected_value_min_pct_trend_down = getNum("risk-ev-down", o.bot_expected_value_min_pct_trend_down);
    o.bot_expected_value_min_pct_range = getNum("risk-ev-range", o.bot_expected_value_min_pct_range);
    o.bot_expected_value_min_pct_volatile = getNum("risk-ev-volatile", o.bot_expected_value_min_pct_volatile);
    o.ml_wf_cost_per_trade_pct = getNum("risk-ml-wf-cost", o.ml_wf_cost_per_trade_pct);
    o.ml_wf_train_min = Math.max(20, getInt("risk-ml-wf-train", o.ml_wf_train_min));
    o.ml_wf_test_window = Math.max(5, getInt("risk-ml-wf-test", o.ml_wf_test_window));
    o.tp_type = getStr("risk-tp-type", o.tp_type);
    o.tp_value = getNum("risk-tp-value", o.tp_value);
    o.sl_type = getStr("risk-sl-type", o.sl_type);
    o.sl_value = getNum("risk-sl-value", o.sl_value);
    o.trailing_stop_pct = Math.max(0.1, getNum("risk-trailing-pct", o.trailing_stop_pct));
    o.trailing_min_profit_pct = Math.max(0, getNum("risk-trailing-min-profit", o.trailing_min_profit_pct));
    o.limit_buy_value = getNum("risk-limit-buy", o.limit_buy_value);
    o.limit_sell_value = getNum("risk-limit-sell", o.limit_sell_value);
    o.bot_auto_sell = getChk("risk-bot-auto-sell");
    o.bot_auto_sell_requires_robot = getChk("risk-bot-auto-sell-robot");
    o.bot_block_ai_sell_while_losing = getChk("risk-bot-block-sell-loss");
    o.sell_at_overbought_rsi_min = getNum("risk-sell-ob-rsi", o.sell_at_overbought_rsi_min);
    o.sell_at_overbought_min_profit_pct = getNum("risk-sell-ob-profit", o.sell_at_overbought_min_profit_pct);
    o.sell_at_peak_rsi_min = getNum("risk-sell-peak-rsi", o.sell_at_peak_rsi_min);
    o.sell_at_peak_min_profit_pct = getNum("risk-sell-peak-profit", o.sell_at_peak_min_profit_pct);
    o.sell_at_overbought_limit_buy_rsi_buffer = getNum("risk-sell-ob-buffer", o.sell_at_overbought_limit_buy_rsi_buffer);
    o.bot_merge_composite = getChk("risk-merge-composite");
    o.composite_score_buy = getNum("risk-comp-buy", o.composite_score_buy);
    o.composite_score_strong = getNum("risk-comp-strong", o.composite_score_strong);
    o.composite_score_mid = getNum("risk-comp-mid", o.composite_score_mid);
    o.composite_adx_for_di = getNum("risk-comp-adx", o.composite_adx_for_di);
    o.ai_promote_wait_from_composite = getChk("risk-ai-promote-wait");
    o.ai_use_regime_router = getChk("risk-ai-regime");
    o.limit_sell_blocks_until_target = getChk("risk-limit-sell-blocks");
    o.bot_signal_sell_bypass_tp_barrier = getChk("risk-signal-bypass-tp");
    o.bot_trailing_bypass_tp_barrier = getChk("risk-trail-bypass-tp");
    o.bot_auto_sl = getChk("risk-bot-auto-sl");
    o.bot_trailing_enabled = getChk("risk-bot-trailing");
    o.bot_apply_execution_filters = getChk("risk-apply-exec-filters");
    o.apply_conditions_to_presets = getChk("risk-apply-conditions-presets");
    o.ml_weight_pct = Math.min(100, Math.max(0, getInt("risk-ml-weight", o.ml_weight_pct)));
    o.safety_orders_count = Math.min(4, Math.max(1, getInt("risk-dca-count", o.safety_orders_count)));
    o.safety_order_step_pct = Math.min(5, Math.max(1, getInt("risk-dca-step", o.safety_order_step_pct)));
    o.safety_order_volume_scale = Math.min(3, Math.max(0.5, getNum("risk-dca-vol", o.safety_order_volume_scale)));
    o.buy_conditions = getMulti("risk-buy-conditions");
    o.sell_conditions = getMulti("risk-sell-conditions");
    return o;
  }

  function applyPresetToForm(mode) {
    var o = collectFromForm();
    mode = (mode || "").toLowerCase();
    if (mode === "custom") return;
    if (mode === "auto") {
      o.strategy_mode = "auto";
      applyToForm(o);
      return;
    }
    var pr = STRATEGY_PRESETS[mode];
    if (pr) {
      for (var k in pr) {
        if (Object.prototype.hasOwnProperty.call(pr, k)) o[k] = pr[k];
      }
    }
    o.strategy_mode = mode;
    applyToForm(o);
  }

  function updateQuickPills(o) {
    var lev = document.getElementById("pill-leverage-display");
    if (lev) lev.textContent = "الرافعة: " + o.leverage + "x";
    var amt = document.getElementById("pill-amount-display");
    if (amt) {
      amt.textContent =
        o.amount_type === "percent"
          ? "المبلغ: " + o.amount_percent + "% رصيد"
          : "المبلغ: " + o.amount_usdt + " USDT";
    }
  }

  function initRiskModal(dlgR, closeModals) {
    dlgR.querySelectorAll(".risk-main-tab[data-risk-main]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var m = btn.getAttribute("data-risk-main");
        dlgR.querySelectorAll(".risk-main-tab").forEach(function (b) {
          b.classList.toggle("active", b === btn);
        });
        dlgR.querySelectorAll(".risk-main-panel").forEach(function (p) {
          p.classList.toggle("active", p.id === "risk-main-" + m);
        });
      });
    });

    dlgR.querySelectorAll("#risk-main-general .risk-subtab[data-risk-tab]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var tab = btn.getAttribute("data-risk-tab");
        dlgR.querySelectorAll("#risk-main-general .risk-subtab").forEach(function (b) {
          b.classList.toggle("active", b === btn);
        });
        dlgR.querySelectorAll("#risk-main-general .risk-tab-panel").forEach(function (p) {
          p.classList.toggle("active", p.id === "risk-tab-" + tab);
        });
      });
    });

    var btnApply = document.getElementById("btn-risk-apply-preset");
    if (btnApply) {
      btnApply.addEventListener("click", function () {
        var sel = document.getElementById("risk-strategy-mode");
        var mode = sel ? sel.value : "custom";
        applyPresetToForm(mode);
      });
    }

    var btnDef = document.getElementById("btn-risk-defaults");
    if (btnDef) {
      btnDef.addEventListener("click", function () {
        applyToForm(clone(defaults()));
      });
    }

    var btnSave = document.getElementById("btn-risk-save");
    if (btnSave) {
      btnSave.addEventListener("click", function () {
        var o = collectFromForm();
        saveObj(o);
        updateQuickPills(o);
        closeModals();
      });
    }

    var btnEx = document.getElementById("btn-risk-export");
    if (btnEx) {
      btnEx.addEventListener("click", function () {
        var o = collectFromForm();
        var blob = new Blob([JSON.stringify(o, null, 2)], { type: "application/json" });
        var a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = "cryptoweb-hub-risk.json";
        a.click();
        URL.revokeObjectURL(a.href);
      });
    }

    var fin = document.getElementById("risk-import-file");
    var btnIm = document.getElementById("btn-risk-import");
    var bm = dlgR.querySelector("#risk-bounce-15m");
    if (bm) bm.addEventListener("change", syncBounceDetailControls);

    if (fin && btnIm) {
      btnIm.addEventListener("click", function () {
        fin.click();
      });
      fin.addEventListener("change", function () {
        var f = fin.files && fin.files[0];
        if (!f) return;
        var r = new FileReader();
        r.onload = function () {
          try {
            var p = JSON.parse(r.result);
            var base = clone(defaults());
            for (var k in p) {
              if (Object.prototype.hasOwnProperty.call(p, k)) base[k] = p[k];
            }
            applyToForm(base);
          } catch (e) {}
          fin.value = "";
        };
        r.readAsText(f);
      });
    }
  }

  function openRiskDialog(dlgR) {
    applyToForm(loadMerged());
    dlgR.querySelectorAll(".risk-main-tab").forEach(function (b, i) {
      b.classList.toggle("active", i === 0);
    });
    dlgR.querySelectorAll(".risk-main-panel").forEach(function (p) {
      p.classList.toggle("active", p.id === "risk-main-general");
    });
    dlgR.querySelectorAll("#risk-main-general .risk-subtab").forEach(function (b, i) {
      b.classList.toggle("active", i === 0);
    });
    dlgR.querySelectorAll("#risk-main-general .risk-tab-panel").forEach(function (p) {
      p.classList.toggle("active", p.id === "risk-tab-basic");
    });
  }

  global.CryptoWebHubRisk = {
    loadMerged: loadMerged,
    applyToForm: applyToForm,
    collectFromForm: collectFromForm,
    saveObj: saveObj,
    updateQuickPills: updateQuickPills,
    initRiskModal: initRiskModal,
    openRiskDialog: openRiskDialog,
    defaults: defaults,
    applyPresetToForm: applyPresetToForm,
  };
})(typeof window !== "undefined" ? window : this);
