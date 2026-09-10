# -*- coding: utf-8 -*-
"""涨跌幅异动、成交量异动、RSI 超买超卖规则。"""
from __future__ import annotations

import pandas as pd

from .base import Signal


class PriceSurgeRule:
    """单日涨跌幅超过阈值。"""
    name = "price_surge"

    def __init__(self, params: dict):
        self.pct_threshold = float(params.get("pct_threshold", 5.0))

    def check(self, code: str, name: str, kline: pd.DataFrame) -> list[Signal]:
        if len(kline) < 2:
            return []
        close = kline["close"].astype(float)
        pct = (close.iloc[-1] / close.iloc[-2] - 1) * 100
        if abs(pct) < self.pct_threshold:
            return []
        direction = "bullish" if pct > 0 else "bearish"
        label = "大涨" if pct > 0 else "大跌"
        return [Signal(
            code=code, name=name, signal_type="price_surge",
            direction=direction, title=f"{name} · 单日{label}异动",
            detail=f"收盘涨跌幅 {pct:+.2f}%，超过阈值 ±{self.pct_threshold}%",
            metrics={"pct": round(float(pct), 2), "close": round(float(close.iloc[-1]), 2)},
        )]


class VolumeSurgeRule:
    """成交量较前 N 日均量显著放大（放量）。"""
    name = "volume_surge"

    def __init__(self, params: dict):
        self.ratio_threshold = float(params.get("ratio_threshold", 2.0))
        self.baseline_days = int(params.get("baseline_days", 5))

    def check(self, code: str, name: str, kline: pd.DataFrame) -> list[Signal]:
        vol = kline["volume"].astype(float)
        if len(vol) < self.baseline_days + 1:
            return []
        baseline = vol.iloc[-self.baseline_days - 1: -1].mean()
        if baseline <= 0:
            return []
        ratio = vol.iloc[-1] / baseline
        if ratio < self.ratio_threshold:
            return []
        close = kline["close"].astype(float)
        pct = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) >= 2 else 0.0
        return [Signal(
            code=code, name=name, signal_type="volume_surge",
            direction="bullish" if pct >= 0 else "bearish",
            title=f"{name} · 放量异动",
            detail=f"成交量 {ratio:.1f} 倍于前{self.baseline_days}日均量，当日涨跌 {pct:+.2f}%",
            metrics={"volume_ratio": round(float(ratio), 2), "pct": round(float(pct), 2)},
        )]


class RSIRule:
    """Wilder RSI 超买/超卖。"""
    name = "rsi"

    def __init__(self, params: dict):
        self.period = int(params.get("period", 14))
        self.overbought = float(params.get("overbought", 80))
        self.oversold = float(params.get("oversold", 20))

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
        rs = gain / loss
        return 100 - 100 / (1 + rs)

    def check(self, code: str, name: str, kline: pd.DataFrame) -> list[Signal]:
        close = kline["close"].astype(float)
        if len(close) < self.period + 1:
            return []
        rsi = self._rsi(close, self.period).iloc[-1]
        if pd.isna(rsi) or self.oversold < rsi < self.overbought:
            return []
        zone = "超买" if rsi >= self.overbought else "超卖"
        return [Signal(
            code=code, name=name, signal_type=f"rsi_{zone}",
            direction="bearish" if zone == "超买" else "bullish",
            title=f"{name} · RSI{zone}",
            detail=f"RSI({self.period})={rsi:.1f}，进入{zone}区间"
                   f"（阈值 {self.oversold}/{self.overbought}）",
            metrics={"rsi": round(float(rsi), 1)},
        )]
