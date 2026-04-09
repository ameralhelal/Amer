import logging

from PyQt6.QtWidgets import QWidget, QLabel, QVBoxLayout, QTextEdit, QGroupBox, QGridLayout, QFrame
from PyQt6.QtCore import pyqtSignal

from composite_signal import compute_composite_signal
from config import load_config, load_config_cached
from market_decision_detail import build_decision_indicator_explain
from market_status_readout import engine_market_readout_bundle
from translations import get_language

_log_ai = logging.getLogger("trading.ai_panel")

# تلوين «قرار اللوحة»: تصنيف منع شراء / منع بيع من مفاتيح الدمج والقواعد
_MARKET_ACCENT_BLOCK_SELL_MK = frozenset(
    {
        "m_merge_strong_buy_blocked_sell",
        "m_merge_comp_score_blocked_sell",
    }
)
_MARKET_ACCENT_BLOCK_BUY_MK = frozenset(
    {
        "m_merge_strong_sell_blocked_buy",
        "m_merge_struct_bear_buy_to_wait",
        "m_merge_comp_score_blocked_buy",
        "m_merge_st_bear_high_composite_need_momentum",
        "m_merge_struct_bear_blocked_composite_buy",
        "m_merge_st_bear_mid_composite_need_momentum",
        "m_merge_htf_st_bear_blocked_buy",
    }
)
_MARKET_ACCENT_BLOCK_BUY_RULE_KEYS = frozenset(
    {
        "rule_wait_hard_bear_no_hist_strong_bull",
        "rule_wait_hard_bear_candle_bull_score2",
        "rule_wait_hard_bear_candle_bull_score1",
        "rule_wait_hard_bear_no_momentum",
        "rule_wait_no_15m_breakout_confirmation",
        "rule_wait_chase_resistance_bb",
        "rule_wait_need_composite_candle_buy",
        "rule_wait_ihs_conservative",
    }
)
_MARKET_ACCENT_BLOCK_SELL_RULE_KEYS = frozenset(
    {
        "rule_wait_need_composite_candle_sell",
        "rule_wait_late_sell_near_bottom",
    }
)


class AIPanel(QWidget):
    """توصية AI تُرسل للربوت الآلي (BUY/SELL/WAIT + نسبة الثقة)."""

    recommendation_updated = pyqtSignal(str, float, dict, dict)

    def __init__(self):
        super().__init__()

        self.setWindowTitle("لوحة الذكاء الاصطناعي")
        self.setMinimumWidth(480)
        self.setMinimumHeight(600)

        self._last_indicators = None
        self._last_market_info = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        summary_group = QGroupBox("ملخص الذكاء الاصطناعي")
        sg_layout = QGridLayout(summary_group)

        self.ai_recommendation = QLabel("التوصية: N/A")
        self.ai_confidence = QLabel("الثقة: N/A")
        self.ai_trend = QLabel("الاتجاه: N/A")
        self.signal_strength = QLabel("قوة الإشارة: N/A")

        sg_layout.addWidget(self.ai_recommendation, 0, 0)
        sg_layout.addWidget(self.ai_confidence, 0, 1)
        sg_layout.addWidget(self.ai_trend, 1, 0, 1, 2)
        sg_layout.addWidget(self.signal_strength, 2, 0, 1, 2)

        layout.addWidget(summary_group)

        ind_group = QGroupBox("المؤشرات")
        ig_layout = QGridLayout(ind_group)

        self.macd_label = QLabel("MACD: N/A")
        self.signal_label = QLabel("Signal: N/A")
        self.hist_label = QLabel("Histogram: N/A")

        self.rsi_label = QLabel("RSI: N/A")
        self.ma20_label = QLabel("MA20: N/A")
        self.ma50_label = QLabel("MA50: N/A")

        self.bb_upper_label = QLabel("BB Upper: N/A")
        self.bb_middle_label = QLabel("BB Middle: N/A")
        self.bb_lower_label = QLabel("BB Lower: N/A")

        ig_layout.addWidget(self.macd_label, 0, 0)
        ig_layout.addWidget(self.signal_label, 0, 1)
        ig_layout.addWidget(self.hist_label, 1, 0)

        ig_layout.addWidget(self.rsi_label, 1, 1)
        ig_layout.addWidget(self.ma20_label, 2, 0)
        ig_layout.addWidget(self.ma50_label, 2, 1)

        ig_layout.addWidget(self.bb_upper_label, 3, 0)
        ig_layout.addWidget(self.bb_middle_label, 3, 1)
        ig_layout.addWidget(self.bb_lower_label, 4, 0)

        layout.addWidget(ind_group)

        market_group = QGroupBox("معلومات السوق")
        mg_layout = QGridLayout(market_group)

        self.market_trend_label = QLabel("اتجاه السوق: N/A")
        self.volume_strength_label = QLabel("قوة الحجم: N/A")
        self.volatility_label = QLabel("التقلب: N/A")

        mg_layout.addWidget(self.market_trend_label, 0, 0)
        mg_layout.addWidget(self.volume_strength_label, 0, 1)
        mg_layout.addWidget(self.volatility_label, 1, 0, 1, 2)

        layout.addWidget(market_group)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(sep)

        self.analysis_box = QTextEdit()
        self.analysis_box.setReadOnly(True)
        self.analysis_box.setMinimumHeight(200)
        layout.addWidget(self.analysis_box)

    def update_indicators(self, interval, ind: dict):
        if not isinstance(ind, dict):
            return
        ind = dict(ind)
        if interval:
            ind["chart_interval"] = str(interval)
        macd = float(ind.get("macd", 0.0))
        signal = float(ind.get("signal", 0.0))
        hist = float(ind.get("hist", 0.0))
        rsi = float(ind.get("rsi", 50.0))
        ma20 = float(ind.get("ma20", 0.0))
        ma50 = float(ind.get("ma50", 0.0))
        bb_upper = float(ind.get("bb_upper", 0.0))
        bb_middle = float(ind.get("bb_middle", 0.0))
        bb_lower = float(ind.get("bb_lower", 0.0))

        self.macd_label.setText(f"MACD: {macd:.6f}")
        self.macd_label.setStyleSheet("color: #00cc44;" if macd > signal else "color: #ff3333;")

        self.signal_label.setText(f"Signal: {signal:.6f}")

        self.hist_label.setText(f"Histogram: {hist:.6f}")
        self.hist_label.setStyleSheet("color: #00cc44;" if hist > 0 else "color: #ff3333;")

        self.rsi_label.setText(f"RSI: {rsi:.2f}")
        if rsi > 70:
            self.rsi_label.setStyleSheet("color: #ff3333; font-weight: bold;")
        elif rsi < 30:
            self.rsi_label.setStyleSheet("color: #3366ff; font-weight: bold;")
        else:
            self.rsi_label.setStyleSheet("color: #00cc44;")

        self.ma20_label.setText(f"MA20: {ma20:.4f}")
        self.ma50_label.setText(f"MA50: {ma50:.4f}")

        self.bb_upper_label.setText(f"BB Upper: {bb_upper:.4f}")
        self.bb_middle_label.setText(f"BB Middle: {bb_middle:.4f}")
        self.bb_lower_label.setText(f"BB Lower: {bb_lower:.4f}")

        self._last_indicators = ind
        self._try_update_ai()

    def _try_update_ai(self):
        # يكفي وجود المؤشرات؛ معلومات السوق تُمرَّر كـ {} إن لم تصل بعد — وإلا تبقى التوصية عالقة (غالباً على آخر BUY).
        if self._last_indicators:
            self.update_ai_recommendation(self._last_indicators, self._last_market_info or {})

    def update_market_info(self, interval, info: dict):
        trend = info.get("trend", "N/A")
        volume = float(info.get("volume_strength", 1.0))
        volatility_pct = float(info.get("volatility_pct", 0.0))

        self.market_trend_label.setText(f"اتجاه السوق: {trend}")
        self.volume_strength_label.setText(f"قوة الحجم: {volume}")
        self.volatility_label.setText(f"التقلب: {volatility_pct:.2f}%")

        self.market_trend_label.setStyleSheet(
            "color: #00cc44; font-weight: bold;" if trend == "UP" else "color: #ff3333; font-weight: bold;"
        )

        self._last_market_info = info
        self._try_update_ai()

    def update_ai_recommendation(self, indicators: dict, market_info: dict):
        self.generate_ai_analysis(indicators, market_info)

    @staticmethod
    def get_recommendation(ind: dict, info: dict, _config: dict = None) -> tuple:
        """(توصية، ثقة) — للتوافق مع الباك‌تيست والملخص."""
        r, c, _, _ = AIPanel._get_recommendation_with_trace(ind, info, _config)
        return r, c

    @staticmethod
    def decision_explain_line_for_market_status(ind: dict, info: dict) -> str:
        """سطر مختصر: القرار + سبب/سببين مهمين فقط."""
        from translations import tr

        r, c, rk, mk = AIPanel._get_recommendation_with_trace(ind, info, None)
        try:
            cfg = load_config()
        except Exception:
            cfg = {}
        ru = str(r or "").strip().upper()
        rec_display = (
            tr("trading_side_buy")
            if ru == "BUY"
            else tr("trading_side_sell")
            if ru == "SELL"
            else tr("trading_side_wait")
            if ru == "WAIT"
            else str(r)
        )
        head = tr("market_status_decision_rec_conf").format(rec=rec_display, conf=f"{c:.0f}")
        parts = [head]
        reasons: list[str] = []
        max_reasons = 2
        max_reason_len = 220

        def _push_reason(txt: str | None):
            if not txt:
                return
            if len(reasons) >= max_reasons:
                return
            t = str(txt).replace("\n", " ").strip()
            if not t:
                return
            if len(t) > max_reason_len:
                t = t[: max_reason_len - 1].rstrip() + "…"
            reasons.append(t)

        ar = get_language() == "ar"
        indicator_line = build_decision_indicator_explain(
            rule_key=rk or None,
            merge_key=mk or None,
            ind=ind if isinstance(ind, dict) else {},
            info=info if isinstance(info, dict) else {},
            cfg=cfg if isinstance(cfg, dict) else {},
            lang_ar=ar,
        )
        if indicator_line:
            _push_reason(indicator_line)
        else:
            if rk:
                _push_reason(tr(rk))
            try:
                comp = compute_composite_signal(
                    ind if isinstance(ind, dict) else {},
                    info if isinstance(info, dict) else {},
                    lang_ar=ar,
                )
                comp_score = float(comp.get("score", 0.0) or 0.0)
                comp_level = str(comp.get("level") or "neutral")
                comp_key = {
                    "strong_buy": "composite_state_strong_buy",
                    "buy": "composite_state_buy",
                    "neutral": "composite_state_neutral",
                    "sell": "composite_state_sell",
                    "strong_sell": "composite_state_strong_sell",
                }.get(comp_level, "composite_state_neutral")
                if mk:
                    _push_reason(
                        tr("market_status_composite_effect_changed").format(
                            score=f"{comp_score:+.1f}",
                            state=tr(comp_key),
                            reason=tr(mk),
                        )
                    )
            except Exception:
                if mk:
                    _push_reason(tr(mk))
        if reasons:
            parts.append(" | ".join(reasons))
        return " — ".join(parts)

    @staticmethod
    def decision_accent_for_market_status(ind: dict, info: dict) -> str:
        """
        buy | sell | block_buy | block_sell | neutral
        — يتطابق مع مسار decision_explain_line_for_market_status (محرّك + دمج مركّب).
        """
        r, c, rk, mk = AIPanel._get_recommendation_with_trace(ind, info, None)
        rf = str(r or "").strip().upper()
        if rf == "BUY":
            return "buy"
        if rf == "SELL":
            return "sell"
        mk_s = str(mk or "").strip()
        if mk_s in _MARKET_ACCENT_BLOCK_SELL_MK:
            return "block_sell"
        if mk_s in _MARKET_ACCENT_BLOCK_BUY_MK:
            return "block_buy"
        rk_s = str(rk or "").strip()
        if rk_s in _MARKET_ACCENT_BLOCK_SELL_RULE_KEYS:
            return "block_sell"
        if rk_s in _MARKET_ACCENT_BLOCK_BUY_RULE_KEYS:
            return "block_buy"
        return "neutral"

    @staticmethod
    def _conservative_inverse_hs_buy_ok(
        _ind: dict,
        _info: dict,
        bull_names: set,
        *,
        rsi: float,
        macd_diff: float,
        hist: float,
        close: float,
        vwap: float,
        st_dir: int,
        hard_bear: bool,
        comp_score: float,
    ) -> bool:
        from signal_engine.candle_guards import inverse_hs_buy_ok

        try:
            _cfg = load_config_cached()
            _h = str(_cfg.get("bot_trade_horizon") or "short").strip().lower()
            if _h not in ("short", "swing"):
                _h = "short"
            _rz = engine_market_readout_bundle(_cfg, trade_horizon=_h)
        except Exception:
            _rz = {
                "inverse_hs_oversold_max": 44.0,
                "inverse_hs_st_bear_rsi": 45.0,
                "inverse_hs_momo_max": 62.0,
                "inverse_hs_chase_max": 66.0,
            }
        return inverse_hs_buy_ok(
            _ind,
            _info,
            bull_names,
            rsi=rsi,
            macd_diff=macd_diff,
            hist=hist,
            close=close,
            vwap=vwap,
            st_dir=st_dir,
            hard_bear=hard_bear,
            comp_score=comp_score,
            oversold_max=float(_rz["inverse_hs_oversold_max"]),
            st_bear_rsi=float(_rz["inverse_hs_st_bear_rsi"]),
            momo_max=float(_rz["inverse_hs_momo_max"]),
            chase_max=float(_rz["inverse_hs_chase_max"]),
        )

    @staticmethod
    def _get_recommendation_with_trace(ind: dict, info: dict, _config: dict | None) -> tuple:
        """التوصية عبر signal_engine: مسارات في ملفات منفصلة + موجّه نظام سوق (ai_use_regime_router)."""
        from signal_engine.coordinator import evaluate_with_trace

        ind = ind if isinstance(ind, dict) else {}
        info = info if isinstance(info, dict) else {}
        _lang_ar = get_language() == "ar"
        try:
            cfg = _config if isinstance(_config, dict) else load_config()
        except Exception:
            cfg = {}
        return evaluate_with_trace(ind, info, cfg, _lang_ar)


    def generate_ai_analysis(self, ind: dict, info: dict):
        # للإيجاز: احتفظنا بالمنطق الأصلي كما في الملف السابق
        # ونُحدّث الملخص ونصدر الإشارة
        try:
            rec, conf = self.get_recommendation(ind, info)
        except Exception as e:
            _log_ai.warning("get_recommendation failed, using WAIT: %s", e)
            rec, conf = "WAIT", 50.0
        trend = (info or {}).get("trend", "N/A")
        self.update_ai_summary(rec, conf, trend, ind, info)
        self.update_analysis(f"AI: {rec} ({conf:.0f}%) trend={trend}")

    def update_ai_summary(self, recommendation: str, confidence: float, trend: str, indicators: dict = None, market_info: dict = None):
        self.ai_recommendation.setText(f"التوصية: {recommendation}")
        self.ai_confidence.setText(f"الثقة: {confidence:.1f}%")
        self.ai_trend.setText(f"الاتجاه: {trend}")
        ind = indicators if isinstance(indicators, dict) else {}
        info = market_info if isinstance(market_info, dict) else {}
        self.recommendation_updated.emit(recommendation, confidence, ind, info)

    def _make_strength_bar(self, strength: int):
        filled = int(strength / 10)
        empty = 10 - filled
        bar = "█" * filled + "░" * empty
        return f"{bar} {strength}%"

    def update_analysis(self, text: str):
        # QTextEdit مع append بدون حد سيتراكم نصاً بشكل غير محدود مع تحديثات websocket
        # مما يؤدي لتباطؤ شديد/تجمّد للواجهة. نحدّث النص الحالي فقط.
        try:
            self.analysis_box.setPlainText(text)
        except Exception:
            # في حال أي مشكلة شكلية في widget، لا نمنع باقي تحديثات اللوحة.
            pass
