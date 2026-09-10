# -*- coding: utf-8 -*-
"""K线反转形态 + RSI背离确认规则。

判定标准提炼自 MQL5 文章 17962（价格行为工具包·第26部分），参数标注【待验证】：
- Pin Bar：小实体 + 长影线（影线>2倍实体，反向影线<0.5倍实体，实体>10%振幅）
- 吞没形态：第二根K线实体完全包住第一根实体
- RSI背离：价格新低+RSI低点抬高（看涨）/ 价格新高+RSI高点降低（看跌），
  且信号K线RSI处于超卖/超买区
- 双重确认：形态与背离同时满足才发信号，单一条件不推送（降低假信号）
"""
from __future__ import annotations

import pandas as pd

from .base import Signal
from .rules_price import RSIRule


def _bullish_pin_bar(open_: float, close: float, high: float, low: float) -> bool:
    body = abs(open_ - close)
    rng = high - low
    lw = min(open_, close) - low
    uw = high - max(open_, close)
    return close > open_ and lw > 2.0 * body and uw < 0.5 * body and body > 0.1 * rng


def _bearish_pin_bar(open_: float, close: float, high: float, low: float) -> bool:
    body = abs(open_ - close)
    uw = high - max(open_, close)
    lw = min(open_, close) - low
    return close < open_ and uw > 2.0 * body and lw < 0.5 * body and body > 0.1 * (high - low)


def _bullish_engulfing(o: pd.Series, c: pd.Series) -> bool:
    """阳线实体完全包住前一根（阴线）实体。"""
    return bool(c.iloc[-1] > o.iloc[-1]
                and o.iloc[-1] <= c.iloc[-2]
                and c.iloc[-1] >= o.iloc[-2])


def _bearish_engulfing(o: pd.Series, c: pd.Series) -> bool:
    return bool(c.iloc[-1] < o.iloc[-1]
                and o.iloc[-1] >= c.iloc[-2]
                and c.iloc[-1] <= o.iloc[-2])


def _bullish_divergence(lows: pd.Series, rsi: pd.Series,
                        oversold: float, lo: int, hi: int) -> bool:
    """价格创新低（信号K线低点低于回溯窗口内低点）而RSI低点抬高，且处于超卖区。"""
    if len(lows) < hi + 1:
        return False
    for i in range(-hi - 1, -lo):  # 回溯 hi..lo 根（不含信号K线本身）
        if lows.iloc[i] > lows.iloc[-1] and rsi.iloc[i] < rsi.iloc[-1] and rsi.iloc[-1] < oversold:
            return True
    return False


def _bearish_divergence(highs: pd.Series, rsi: pd.Series,
                        overbought: float, lo: int, hi: int) -> bool:
    if len(highs) < hi + 1:
        return False
    for i in range(-hi - 1, -lo):
        if highs.iloc[i] < highs.iloc[-1] and rsi.iloc[i] > rsi.iloc[-1] and rsi.iloc[-1] > overbought:
            return True
    return False


class CandlestickDivergenceRule:
    """Pin Bar/吞没 + RSI背离 双重确认信号。"""
    name = "candle_div"

    def __init__(self, params: dict):
        self.rsi_period = int(params.get("rsi_period", 14))
        self.overbought = float(params.get("overbought", 70))
        self.oversold = float(params.get("oversold", 30))
        self.lookback_min = int(params.get("lookback_min", 5))
        self.lookback_max = int(params.get("lookback_max", 15))

    def check(self, code: str, name: str, kline: pd.DataFrame) -> list[Signal]:
        need = max(self.lookback_max + 2, self.rsi_period + 2)
        if len(kline) < need:
            return []
        o, c = kline["open"].astype(float), kline["close"].astype(float)
        h, l = kline["high"].astype(float), kline["low"].astype(float)
        rsi = RSIRule._rsi(c, self.rsi_period)

        bull_div = _bullish_divergence(l, rsi, self.oversold, self.lookback_min, self.lookback_max)
        bear_div = _bearish_divergence(h, rsi, self.overbought, self.lookback_min, self.lookback_max)
        if not (bull_div or bear_div):
            return []

        signals: list[Signal] = []
        last = kline.iloc[-1]
        if bull_div and (_bullish_pin_bar(o.iloc[-1], c.iloc[-1], h.iloc[-1], l.iloc[-1])
                         or _bullish_engulfing(o, c)):
            signals.append(Signal(
                code=code, name=name, signal_type="bull_reversal_rsi_div",
                direction="bullish", title=f"{name} · 看涨反转（形态+RSI底背离）",
                detail=f"信号K线出现{'看涨Pin Bar' if _bullish_pin_bar(o.iloc[-1], c.iloc[-1], h.iloc[-1], l.iloc[-1]) else '看涨吞没'}，"
                       f"同时价格新低而RSI({self.rsi_period})未创新低且处于超卖区（{rsi.iloc[-1]:.1f}）",
                metrics={"rsi": round(float(rsi.iloc[-1]), 1),
                         "oversold": self.oversold,
                         "lookback": f"{self.lookback_min}-{self.lookback_max}"},
            ))
        if bear_div and (_bearish_pin_bar(o.iloc[-1], c.iloc[-1], h.iloc[-1], l.iloc[-1])
                         or _bearish_engulfing(o, c)):
            signals.append(Signal(
                code=code, name=name, signal_type="bear_reversal_rsi_div",
                direction="bearish", title=f"{name} · 看跌反转（形态+RSI顶背离）",
                detail=f"价格新高而RSI({self.rsi_period})未创新高且处于超买区（{rsi.iloc[-1]:.1f}），"
                       f"信号K线确认反转形态",
                metrics={"rsi": round(float(rsi.iloc[-1]), 1),
                         "overbought": self.overbought,
                         "lookback": f"{self.lookback_min}-{self.lookback_max}"},
            ))
        return signals
