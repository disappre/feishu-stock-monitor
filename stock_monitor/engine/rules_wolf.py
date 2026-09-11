# -*- coding: utf-8 -*-
"""防狼术规则（缠师第103课）：回避所有MACD黄白线(DIF/DEA)在0轴之下的股票。

原文明义："回避所有MACD黄白线在0轴下面的股票或市场，这就是最基本的防狼术"。
口径：**日线DIF与DEA均 < 0** → 该股处于"危险区"，一切买入类信号对其失效；
重新站回0轴上方才恢复。

实现为规则引擎的一个特殊过滤器：不产生新信号，而是
【压制买入类信号】——danger状态下的bullish信号直接拦截（bearish回避信号照常放行，
因为危险区的离场提示更重要）。
"""
from __future__ import annotations

import logging

import pandas as pd

from .base import Signal

logger = logging.getLogger(__name__)


def dif_dea_below_zero(close: pd.Series) -> bool:
    """日线DIF与DEA是否均在0轴下方（防狼术危险区判定）。"""
    if len(close) < 35:
        return False
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    return bool(dif.iloc[-1] < 0 and dea.iloc[-1] < 0)


class WolfGuardRule:
    """防狼术：危险区(DIF/DEA<0)内拦截买入类信号，输出状态标记信号。"""
    name = "wolf_guard"

    def __init__(self, params: dict):
        self.only_flag = bool(params.get("only_flag", False))
        # only_flag=True 时只发状态信号不拦截（观察模式）

    def check(self, code: str, name: str, kline: pd.DataFrame) -> list[Signal]:
        close = kline["close"].astype(float)
        if not dif_dea_below_zero(close):
            return []
        sig = Signal(
            code=code, name=name,
            signal_type="wolf_guard_danger",
            direction="bearish",
            title=f"{name} · 防狼术危险区",
            detail=(f"日线MACD黄白线(DIF/DEA)均在0轴之下（缠师第103课防狼术："
                    f"回避所有黄白线0轴下的股票）。该股买入类信号已自动压制，"
                    f"站回0轴上方后恢复。仅作状态提示，不构成投资建议"),
            metrics={"dif": round(float(close.ewm(span=12, adjust=False).mean().iloc[-1]
                                       - close.ewm(span=26, adjust=False).mean().iloc[-1]), 3)},
            source="chan",
            confidence=1.0,
        )
        return [sig]
