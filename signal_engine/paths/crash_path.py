"""هبوط مؤكد: بيع حماية فقط."""
from __future__ import annotations

from typing import Optional

from signal_engine.context import PanelContext

RawSignal = tuple[str, float, str]


def try_hard_bear_crash(ctx: PanelContext) -> Optional[RawSignal]:
    if ctx.hard_bear_crash:
        return ("SELL", ctx.hard_bear_sell_conf, "rule_sell_hard_bear_crash")
    return None
