# -*- coding: utf-8 -*-
"""分形突破 + 双EMA趋势过滤规则。

判定标准提炼自 MQL5 文章 18297（价格行为工具包·第25部分），参数标注【待验证】：
- 分形（Bill Williams）：左右各2根K线确认的局部高点/低点（5根结构）
- 看涨信号：收盘价上穿最近的分形高点（阻力突破），且 价格>EMA14>EMA200（多头过滤）
- 看跌信号：收盘价下穿最近的分形高点对应的分形低点（支撑跌破），且 价格<EMA14、EMA14<EMA200
- 分形天然滞后：右翼2根K线确认后才能使用该水平位（防重绘，同螃蟹形态卡片的教训）
- 注意 EMA200 需要较长历史：data/feed.py 已将拉取长度扩至 250 根
"""
from __future__ import annotations

import pandas as pd

from .base import Signal

WINGS = 2  # 分形确认翼宽：左右各2根


def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def _recent_fractal_levels(high: pd.Series, low: pd.Series) -> tuple[float, float]:
    """返回最近的已确认分形高点/低点价格。无分形时返回 (nan, nan)。

    从后向前找，但跳过最后 WINGS 根（右翼未完成，分形未确认）。
    """
    n = len(high)
    fractal_high = fractal_low = float("nan")
    for i in range(n - 1 - WINGS, WINGS - 1, -1):
        left_h = high.iloc[i - WINGS: i]
        right_h = high.iloc[i + 1: i + 1 + WINGS]
        if pd.isna(fractal_high) and (high.iloc[i] > left_h).all() and (high.iloc[i] > right_h).all():
            fractal_high = float(high.iloc[i])
        left_l = low.iloc[i - WINGS: i]
        right_l = low.iloc[i + 1: i + 1 + WINGS]
        if pd.isna(fractal_low) and (low.iloc[i] < left_l).all() and (low.iloc[i] < right_l).all():
            fractal_low = float(low.iloc[i])
        if not (pd.isna(fractal_high) or pd.isna(fractal_low)):
            break
    return fractal_high, fractal_low


class FractalBreakRule:
    """分形突破 + EMA14/200 趋势方向过滤（突破只在趋势方向上确认）。"""
    name = "fractal_break"

    def __init__(self, params: dict):
        self.ema_fast = int(params.get("ema_fast", 14))
        self.ema_slow = int(params.get("ema_slow", 200))

    def check(self, code: str, name: str, kline: pd.DataFrame) -> list[Signal]:
        if len(kline) < self.ema_slow + WINGS + 2:
            return []
        close = kline["close"].astype(float)
        ema_f = _ema(close, self.ema_fast)
        ema_s = _ema(close, self.ema_slow)
        lvl_up, lvl_down = _recent_fractal_levels(
            kline["high"].astype(float), kline["low"].astype(float))
        if pd.isna(lvl_up) and pd.isna(lvl_down):
            return []

        prev, last = close.iloc[-2], close.iloc[-1]
        signals: list[Signal] = []

        # 看涨：上穿分形高点 + 多头排列（价>EMA14, 价>EMA200, EMA14>EMA200）
        if (not pd.isna(lvl_up) and prev <= lvl_up < last
                and last > ema_f.iloc[-1] and last > ema_s.iloc[-1]
                and ema_f.iloc[-1] > ema_s.iloc[-1]):
            signals.append(Signal(
                code=code, name=name, signal_type="fractal_break_up",
                direction="bullish", title=f"{name} · 分形阻力突破(多头)",
                detail=f"收盘价上穿最近分形高点 {lvl_up:.2f}，且价格站上EMA{self.ema_fast}/EMA{self.ema_slow}，"
                       f"EMA{self.ema_fast}>EMA{self.ema_slow} 确认多头趋势",
                metrics={"level": round(lvl_up, 2),
                         "ema_fast": round(float(ema_f.iloc[-1]), 2),
                         "ema_slow": round(float(ema_s.iloc[-1]), 2)},
            ))
        # 看跌：下穿分形低点 + 空头排列
        if (not pd.isna(lvl_down) and prev >= lvl_down > last
                and last < ema_f.iloc[-1] and last < ema_s.iloc[-1]
                and ema_f.iloc[-1] < ema_s.iloc[-1]):
            signals.append(Signal(
                code=code, name=name, signal_type="fractal_break_down",
                direction="bearish", title=f"{name} · 分形支撑跌破(空头)",
                detail=f"收盘价跌破最近分形低点 {lvl_down:.2f}，且价格位于EMA{self.ema_fast}/EMA{self.ema_slow}下方，"
                       f"EMA{self.ema_fast}<EMA{self.ema_slow} 确认空头趋势",
                metrics={"level": round(lvl_down, 2),
                         "ema_fast": round(float(ema_f.iloc[-1]), 2),
                         "ema_slow": round(float(ema_s.iloc[-1]), 2)},
            ))
        return signals
