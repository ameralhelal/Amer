# سياق موحّد لكل مسارات الإشارة (مؤشرات، عتبات، أسماء شموع)
from __future__ import annotations

from dataclasses import dataclass, field

from composite_signal import compute_composite_signal
from config import load_config
from market_status_readout import engine_market_readout_bundle


@dataclass
class PanelContext:
    ind: dict
    info: dict
    cfg: dict
    lang_ar: bool
    # مؤشرات
    rsi: float
    macd: float
    signal: float
    hist: float
    macd_diff: float
    hist_prev: float
    hist_rising: bool
    trend_up: bool
    trend_down: bool
    st_dir: int
    vwap: float
    st_k: float
    st_d: float
    chart_iv: str
    close: float
    pivot_r1: float
    adx: float
    candle_score: float
    comp_score: float
    bull_names: set
    bear_names: set
    hard_bear: bool
    hard_bear_crash: bool
    master_profile: str
    trade_horizon: str
    strategy_mode: str
    use_regime_router: bool
    # عتبات مشتقة
    buy_comp_need: float
    sell_comp_need: float
    bottom_rebound_rsi: float
    strong_buy_gate: float
    hard_bear_sell_conf: float
    buy_rsi_1: float
    buy_rsi_2: float
    buy_rsi_3: float
    buy_rsi_4: float
    buy_top_guard_rsi: float
    sell_rsi_1: float
    sell_rsi_2: float
    sell_rsi_3: float
    wait_conf_cap: float
    # عتبات RSI من إعدادات market_readout_* (نفس عرض حالة السوق + المحرّك)
    rsi_ob_thr: float
    rsi_os_thr: float
    rsi_hi_thr: float
    rsi_lo_thr: float
    rsi_sell_hot: float
    rsi_sell_st_bear: float
    rsi_neutral_mid: float
    inverse_hs_oversold_max: float
    inverse_hs_st_bear_rsi: float
    inverse_hs_momo_max: float
    inverse_hs_chase_max: float
    mr: dict[str, float] = field(default_factory=dict)

    def _p(self, cons: float, bal: float, agg: float) -> float:
        return agg

    def _h(self, short_v: float, swing_v: float) -> float:
        return swing_v if self.trade_horizon == "swing" else short_v

    @classmethod
    def build(cls, ind: dict, info: dict, cfg: dict | None, lang_ar: bool) -> PanelContext:
        ind = ind if isinstance(ind, dict) else {}
        info = info if isinstance(info, dict) else {}
        try:
            cfg = cfg if isinstance(cfg, dict) else load_config()
        except Exception:
            cfg = {}

        master_profile = str(cfg.get("bot_master_profile", "aggressive") or "aggressive").strip().lower()
        if master_profile != "aggressive":
            master_profile = "aggressive"
        trade_horizon = str(cfg.get("bot_trade_horizon", "short") or "short").strip().lower()
        if trade_horizon not in ("short", "swing"):
            trade_horizon = "short"
        use_regime_router = bool(cfg.get("ai_use_regime_router", True))
        strategy_mode = str(cfg.get("strategy_mode", "custom") or "custom").strip().lower()

        rsi = float(ind.get("rsi", 50) or 50)
        macd = float(ind.get("macd", 0) or 0)
        signal = float(ind.get("signal", 0) or 0)
        hist = float(ind.get("hist", 0) or 0)
        macd_diff = macd - signal
        hist_prev = float(ind.get("hist_prev", hist) or hist)
        hist_rising = bool(hist > hist_prev)
        trend_up = str(info.get("trend", "")).upper() == "UP"
        trend_down = str(info.get("trend", "")).upper() == "DOWN"
        st_dir = int(ind.get("supertrend_dir", 0) or 0)
        vwap = float(ind.get("vwap", 0) or 0)
        st_k = float(ind.get("stoch_rsi_k", 50) or 50)
        st_d = float(ind.get("stoch_rsi_d", 50) or 50)
        chart_iv = str(ind.get("chart_interval", "") or "").lower()
        close = float(ind.get("close", 0) or 0)
        pivot_r1 = float(ind.get("pivot_r1", 0) or 0)
        adx = float(ind.get("adx14", 0) or 0)
        candle_score = float(ind.get("candle_pattern_score", 0) or 0)
        try:
            _comp = compute_composite_signal(ind, info, lang_ar=lang_ar)
            comp_score = float(_comp.get("score", 0.0) or 0.0)
        except Exception:
            comp_score = 0.0

        hard_bear = bool(
            st_dir == -1
            and (
                trend_down
                or (close > 0 and vwap > 0 and close < vwap and macd_diff < 0 and hist < 0)
            )
        )
        hard_bear_crash = bool(
            hard_bear
            and macd_diff < 0
            and hist < 0
            and vwap > 0
            and close > 0
            and close < vwap
        )

        # عتبات (نفس أرقام ai_panel السابقة)
        def _p(cons: float, bal: float, agg: float) -> float:
            return agg

        buy_comp_need = _p(12.0, 10.0, 8.0)
        sell_comp_need = _p(-12.0, -10.0, -8.0)
        bottom_rebound_rsi = _p(34.0, 36.0, 40.0)
        strong_buy_gate = _p(82.0, 80.0, 78.0)
        hard_bear_sell_conf = _p(78.0, 74.0, 68.0)
        _mr = engine_market_readout_bundle(cfg, trade_horizon=trade_horizon)
        rsi_ob_thr = float(_mr["rsi_ob_thr"])
        rsi_os_thr = float(_mr["rsi_os_thr"])
        rsi_hi_thr = float(_mr["rsi_hi_thr"])
        rsi_lo_thr = float(_mr["rsi_lo_thr"])
        rsi_sell_hot = float(_mr["rsi_sell_hot"])
        rsi_sell_st_bear = float(_mr["rsi_sell_st_bear"])
        rsi_neutral_mid = float(_mr["rsi_neutral_mid"])
        inverse_hs_oversold_max = float(_mr["inverse_hs_oversold_max"])
        inverse_hs_st_bear_rsi = float(_mr["inverse_hs_st_bear_rsi"])
        inverse_hs_momo_max = float(_mr["inverse_hs_momo_max"])
        inverse_hs_chase_max = float(_mr["inverse_hs_chase_max"])
        sell_rsi_1 = float(_mr["sell_rsi_1"])
        sell_rsi_2 = float(_mr["sell_rsi_2"])
        sell_rsi_3 = float(_mr["sell_rsi_3"])
        buy_rsi_1 = float(_mr["buy_rsi_1"])
        buy_rsi_2 = float(_mr["buy_rsi_2"])
        buy_rsi_3 = float(_mr["buy_rsi_3"])
        buy_rsi_4 = float(_mr["buy_rsi_4"])
        buy_top_guard_rsi = float(_mr["buy_top_guard_rsi"])

        wait_conf_cap = _p(48.0, 50.0, 52.0)

        return cls(
            ind=ind,
            info=info,
            cfg=cfg,
            lang_ar=lang_ar,
            rsi=rsi,
            macd=macd,
            signal=signal,
            hist=hist,
            macd_diff=macd_diff,
            hist_prev=hist_prev,
            hist_rising=hist_rising,
            trend_up=trend_up,
            trend_down=trend_down,
            st_dir=st_dir,
            vwap=vwap,
            st_k=st_k,
            st_d=st_d,
            chart_iv=chart_iv,
            close=close,
            pivot_r1=pivot_r1,
            adx=adx,
            candle_score=candle_score,
            comp_score=comp_score,
            bull_names=set(ind.get("candle_pattern_bullish") or []),
            bear_names=set(ind.get("candle_pattern_bearish") or []),
            hard_bear=hard_bear,
            hard_bear_crash=hard_bear_crash,
            master_profile=master_profile,
            trade_horizon=trade_horizon,
            strategy_mode=strategy_mode,
            use_regime_router=use_regime_router,
            buy_comp_need=buy_comp_need,
            sell_comp_need=sell_comp_need,
            bottom_rebound_rsi=bottom_rebound_rsi,
            strong_buy_gate=strong_buy_gate,
            hard_bear_sell_conf=hard_bear_sell_conf,
            buy_rsi_1=buy_rsi_1,
            buy_rsi_2=buy_rsi_2,
            buy_rsi_3=buy_rsi_3,
            buy_rsi_4=buy_rsi_4,
            buy_top_guard_rsi=buy_top_guard_rsi,
            sell_rsi_1=sell_rsi_1,
            sell_rsi_2=sell_rsi_2,
            sell_rsi_3=sell_rsi_3,
            wait_conf_cap=wait_conf_cap,
            rsi_ob_thr=rsi_ob_thr,
            rsi_os_thr=rsi_os_thr,
            rsi_hi_thr=rsi_hi_thr,
            rsi_lo_thr=rsi_lo_thr,
            rsi_sell_hot=rsi_sell_hot,
            rsi_sell_st_bear=rsi_sell_st_bear,
            rsi_neutral_mid=rsi_neutral_mid,
            inverse_hs_oversold_max=inverse_hs_oversold_max,
            inverse_hs_st_bear_rsi=inverse_hs_st_bear_rsi,
            inverse_hs_momo_max=inverse_hs_momo_max,
            inverse_hs_chase_max=inverse_hs_chase_max,
            mr=dict(_mr),
        )
