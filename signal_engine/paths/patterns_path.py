"""شموع: انتظار هابط ثقيل، شراء من أنماط صاعدة قوية / درجة شموع."""
from __future__ import annotations

from typing import Callable, Optional

from composite_signal import get_composite_thresholds
from signal_engine.candle_guards import inverse_hs_buy_ok
from signal_engine.context import PanelContext

RawSignal = tuple[str, float, str]


def _pattern_composite_on(ctx: PanelContext) -> bool:
    """مركّب يؤثّر على مسارات الشموع فقط عند فلاتر التنفيذ + تفعيل دمج المركّب."""
    from bot_logic import apply_execution_filters, bot_uses_composite

    c = ctx.cfg if isinstance(ctx.cfg, dict) else {}
    return apply_execution_filters(c) and bot_uses_composite(c)

_STRONG_BULL = frozenset({
    "MorningStar", "BullishEngulfing", "ThreeWhiteSoldiers", "Hammer", "InvertedHammer",
    "PiercingLine", "InverseHeadAndShoulders", "TweezerBottoms", "BullishKicker",
    "ThreeInsideUp", "ThreeOutsideUp", "MarubozuBull", "AbandonedBabyBull",
    "DoubleBottom", "RoundingBottom",
})
_BEAR_STALL = frozenset({"EveningStar", "ThreeBlackCrows", "HeadAndShoulders"})
_BEAR_TWO_RSI = frozenset({"BearishEngulfing", "DarkCloudCover"})


def _momentum_context_ok(ctx: PanelContext) -> bool:
    """اتجاه صاعد من الترند أو Supertrend — يُستخدم لرفع سقف RSI دون توسيع الشراء في الهبوط."""
    return bool(ctx.trend_up or ctx.st_dir >= 1)


def _rsi_hi_strong_bull(ctx: PanelContext) -> float:
    """أعلى RSI مسموح لشراء نمط صاعد قوي — أوسع قليلاً عند زخم صاعد + مركّب يؤيد الشراء."""
    _cap = float(min(72.0, max(58.0, ctx.rsi_sell_hot)))
    if (
        _pattern_composite_on(ctx)
        and _momentum_context_ok(ctx)
        and float(ctx.comp_score) >= float(ctx.buy_comp_need)
    ):
        return _cap
    return float(min(66.0, max(54.0, ctx.rsi_sell_hot - 2.0)))


def _rsi_hi_candle_score2(ctx: PanelContext) -> float:
    """سقف RSI لفرع شموع قوية (درجة ≥2) مع أنماط صاعدة."""
    if (
        _pattern_composite_on(ctx)
        and _momentum_context_ok(ctx)
        and float(ctx.comp_score) >= float(ctx.buy_comp_need)
    ):
        return float(min(70.0, max(56.0, ctx.rsi_sell_hot - 2.0)))
    if _momentum_context_ok(ctx):
        return float(min(66.0, max(54.0, ctx.rsi_sell_hot - 4.0)))
    return float(min(62.0, max(50.0, ctx.rsi_sell_hot - 6.0)))


def try_bear_pattern_wait(ctx: PanelContext) -> Optional[RawSignal]:
    """لا نُثبّت WAIT على أنماط هابطة خفيفة إذا المركّب صاعد بوضوح والزخم صاعد — وإلا يبقى الشراء الأخضر حبيساً قبل bull()."""
    try:
        _mid_c = float(get_composite_thresholds()["mid"])
    except (TypeError, ValueError):
        _mid_c = 18.0
    if (
        _pattern_composite_on(ctx)
        and float(ctx.comp_score) >= _mid_c
        and _momentum_context_ok(ctx)
    ):
        return None
    rsi, bear_names = ctx.rsi, ctx.bear_names
    candle_score, macd_diff = ctx.candle_score, ctx.macd_diff
    _sh = float(ctx.rsi_sell_hot)
    if (bear_names & _BEAR_STALL) and candle_score <= -1 and rsi > 40 and rsi < _sh:
        if rsi > 48 or macd_diff <= 0:
            return ("WAIT", min(60.0, 50.0 - candle_score * 2.0), "rule_wait_bear_heavy_patterns")
    if (
        (bear_names & _BEAR_TWO_RSI)
        and 58 <= rsi < _sh
        and candle_score <= -1
        and macd_diff <= 0
    ):
        return ("WAIT", 52.0, "rule_wait_bear_engulf_dark_rsi")
    return None


def try_bull_pattern_buys(
    ctx: PanelContext,
    *,
    hs_check: Callable[..., bool],
) -> Optional[RawSignal]:
    ind, info = ctx.ind, ctx.info
    rsi = ctx.rsi
    bull_names = ctx.bull_names
    _comp_hs = float(ctx.comp_score) if _pattern_composite_on(ctx) else 0.0
    strong_bull_hit = bool(bull_names & _STRONG_BULL)
    cap_sb = _rsi_hi_strong_bull(ctx)

    if strong_bull_hit and rsi < cap_sb:
        if ctx.hard_bear and ctx.hist <= 0.0 and rsi > 42:
            return ("WAIT", 50.0, "rule_wait_hard_bear_no_hist_strong_bull")
        if not hs_check(
            ind,
            info,
            bull_names,
            rsi=rsi,
            macd_diff=ctx.macd_diff,
            hist=ctx.hist,
            close=ctx.close,
            vwap=ctx.vwap,
            st_dir=ctx.st_dir,
            hard_bear=ctx.hard_bear,
            comp_score=_comp_hs,
        ):
            return ("WAIT", 52.0, "rule_wait_ihs_conservative")
        near_top = rsi >= max(52.0, float(ctx.rsi_hi_thr) + 3.0)
        if not (near_top and ctx.macd_diff <= 0 and ctx.candle_score < 2):
            conf_c = 54.0 + min(16.0, max(0.0, ctx.candle_score) * 3.0)
            if ctx.macd_diff > 0:
                conf_c += 6.0
            elif ctx.macd_diff > -0.02:
                conf_c += 2.0
            if ctx.trend_up:
                conf_c += 4.0
            if rsi < max(42.0, float(ctx.rsi_lo_thr) + 3.0):
                conf_c += 5.0
            if "MorningStar" in bull_names or "BullishEngulfing" in bull_names:
                conf_c += 4.0
            return ("BUY", min(90.0, conf_c), "rule_buy_strong_bull_candle")

    hi2 = _rsi_hi_candle_score2(ctx)
    if ctx.candle_score >= 2 and bull_names and 28 <= rsi < hi2:
        if ctx.hard_bear and not ctx.trend_up and ctx.hist <= 0.0:
            return ("WAIT", 50.0, "rule_wait_hard_bear_candle_bull_score2")
        if "InverseHeadAndShoulders" in bull_names and not hs_check(
            ind,
            info,
            bull_names,
            rsi=rsi,
            macd_diff=ctx.macd_diff,
            hist=ctx.hist,
            close=ctx.close,
            vwap=ctx.vwap,
            st_dir=ctx.st_dir,
            hard_bear=ctx.hard_bear,
            comp_score=_comp_hs,
        ):
            return ("WAIT", 52.0, "rule_wait_ihs_conservative")
        conf_c = 52.0 + min(12.0, ctx.candle_score * 2.5)
        if ctx.macd_diff > 0:
            conf_c += 5.0
        if ctx.trend_up:
            conf_c += 3.0
        return ("BUY", min(86.0, conf_c), "rule_buy_candle_score2_bull")

    if ctx.candle_score >= 1 and bull_names and rsi < max(48.0, float(ctx.rsi_lo_thr) + 7.0):
        if ctx.hard_bear and ctx.hist <= 0.0:
            return ("WAIT", 50.0, "rule_wait_hard_bear_candle_bull_score1")
        if "InverseHeadAndShoulders" in bull_names and not hs_check(
            ind,
            info,
            bull_names,
            rsi=rsi,
            macd_diff=ctx.macd_diff,
            hist=ctx.hist,
            close=ctx.close,
            vwap=ctx.vwap,
            st_dir=ctx.st_dir,
            hard_bear=ctx.hard_bear,
            comp_score=_comp_hs,
        ):
            return ("WAIT", 52.0, "rule_wait_ihs_conservative")
        conf_c = 50.0 + min(10.0, ctx.candle_score * 3.0)
        if ctx.macd_diff > 0:
            conf_c += 4.0
        return ("BUY", min(78.0, conf_c), "rule_buy_candle_score1_bull")

    return None


def default_hs_check(ind: dict, info: dict, bull_names: set, **kwargs) -> bool:
    return inverse_hs_buy_ok(ind, info, bull_names, **kwargs)
