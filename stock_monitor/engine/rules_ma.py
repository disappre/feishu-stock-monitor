# -*- coding: utf-8 -*-
"""均线金叉/死叉规则。"""
from __future__ import annotations

import pandas as pd

from .base import Signal


class MACrossRule:
    name = "ma_cross"

    def __init__(self, params: dict):
        self.short = int(params.get("short", 5))
        self.long = int(params.get("long", 20))

    def check(self, code: str, name: str, kline: pd.DataFrame) -> list[Signal]:
        close = kline["close"].astype(float)
        if len(close) < self.long + 2:
            return []

        ma_short = close.rolling(self.short).mean()
        ma_long = close.rolling(self.long).mean()
        diff = ma_short - ma_long

        # 用最近两天的差值符号变化判断交叉，避免反复触发
        prev, last = diff.iloc[-2], diff.iloc[-1]
        if pd.isna(prev) or pd.isna(last) or prev == last:
            return []

        signals = []
        if prev < 0 <= last:
            signals.append(Signal(
                code=code, name=name, signal_type="ma_golden_cross",
                direction="bullish", title=f"{name} · 均线金叉",
                detail=f"MA{self.short} 上穿 MA{self.long}（MA{self.short}={ma_short.iloc[-1]:.2f}，"
                       f"MA{self.long}={ma_long.iloc[-1]:.2f}）",
                metrics={"short": self.short, "long": self.long,
                         "ma_short": round(float(ma_short.iloc[-1]), 2),
                         "ma_long": round(float(ma_long.iloc[-1]), 2)},
            ))
        elif prev > 0 >= last:
            signals.append(Signal(
                code=code, name=name, signal_type="ma_death_cross",
                direction="bearish", title=f"{name} · 均线死叉",
                detail=f"MA{self.short} 下穿 MA{self.long}（MA{self.short}={ma_short.iloc[-1]:.2f}，"
                       f"MA{self.long}={ma_long.iloc[-1]:.2f}）",
                metrics={"short": self.short, "long": self.long,
                         "ma_short": round(float(ma_short.iloc[-1]), 2),
                         "ma_long": round(float(ma_long.iloc[-1]), 2)},
            ))
        return signals
