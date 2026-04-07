# حماية شراء Inverse H&S وغيره — منطق محافظ مستقل عن المسارات
from __future__ import annotations


def inverse_hs_buy_ok(
    ind: dict,
    info: dict,
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
    oversold_max: float = 44.0,
    st_bear_rsi: float = 45.0,
    momo_max: float = 62.0,
    chase_max: float = 66.0,
) -> bool:
    if "InverseHeadAndShoulders" not in bull_names:
        return True
    if comp_score <= -18:
        return False
    if vwap > 0 and close > 0 and close < vwap * 0.994 and hist < 0 and macd_diff < 0:
        return False
    if hard_bear and hist <= 0.0 and rsi > 40:
        return False
    if st_dir < 0 and hist < 0 and macd_diff < -0.02 and rsi > st_bear_rsi:
        return False
    oversold_or_value = rsi <= oversold_max
    momentum_confirm = hist > 0 and macd_diff > -0.01 and rsi <= momo_max
    not_chasing_top = rsi < chase_max
    if not (oversold_or_value or momentum_confirm):
        return False
    if not not_chasing_top:
        return False
    return True
