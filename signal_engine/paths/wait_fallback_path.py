"""انتظار نهائي: هبوط بلا زخم أو لا إشارة."""
from __future__ import annotations

from typing import Optional

from signal_engine.context import PanelContext

RawSignal = tuple[str, float, str]


def try_hard_bear_momentum_wait(ctx: PanelContext) -> Optional[RawSignal]:
    if ctx.hard_bear and ctx.hist <= 0.0 and ctx.rsi < 48 and ctx.macd_diff > 0:
        return ("WAIT", ctx.wait_conf_cap, "rule_wait_hard_bear_no_momentum")
    return None


def default_wait(ctx: PanelContext) -> RawSignal:
    return ("WAIT", ctx.wait_conf_cap, "rule_wait_no_clear_signal")
