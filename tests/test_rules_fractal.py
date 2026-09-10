# -*- coding: utf-8 -*-
"""分形突破+双EMA规则的单元测试（判定标准提炼自 MQL5 文章 18297）。"""
import numpy as np
import pandas as pd

from stock_monitor.engine.rules_fractal import (
    FractalBreakRule,
    _recent_fractal_levels,
)


def make_kline(highs, lows, closes=None):
    n = len(highs)
    closes = closes if closes is not None else [(h + l) / 2 for h, l in zip(highs, lows)]
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n),
        "open": [l + 0.1 for l in lows], "close": closes,
        "high": highs, "low": lows, "volume": [1e6] * n,
    })


def test_fractal_detection():
    # 在 i=100 造一个局部高点: 前后各2根都更低
    highs = [10.0 + 0.01 * i for i in range(120)]
    lows = [9.0 + 0.01 * i for i in range(120)]
    highs[100] = 12.0
    lows[100] = 9.5
    df = make_kline(highs, lows)
    lvl_up, lvl_down = _recent_fractal_levels(df["high"], df["low"])
    assert lvl_up == 12.0


def test_fractal_ignores_unconfirmed_right_wing():
    # 最后一根是尖峰, 但右翼未走完 -> 不应作为分形
    highs = [10.0] * 110 + [13.0]
    lows = [9.0] * 111
    df = make_kline(highs, lows)
    lvl_up, _ = _recent_fractal_levels(df["high"], df["low"])
    assert lvl_up != 13.0


def _uptrend_breakout_kline():
    """上升趋势 + 中途分形高点(峰) + 回调蓄力 + 收盘突破该高点。"""
    closes = [100 + 0.3 * i for i in range(200)]                  # 100→159.7 上行
    closes += [159.5, 160.5, 161.2, 161.5, 161.2, 160.5,          # 200..206 峰=161.5(203)
               159.8, 159.2, 158.8, 158.5]
    closes += [158.3, 158.1, 157.9, 158.0, 158.1, 158.2,          # 回调后横盘蓄力
               158.3, 158.4, 161.9, 162.9]                        # 最后两根: 峰下→突破
    highs = [c + 0.3 for c in closes]
    lows = [c - 0.3 for c in closes]
    highs[203] = closes[203] + 0.8                                # 分形水平位 = 162.3
    return make_kline(highs, lows, closes)


def test_bullish_breakout_in_uptrend():
    rule = FractalBreakRule({"ema_fast": 14, "ema_slow": 200})
    signals = rule.check("x", "测试股", _uptrend_breakout_kline())
    assert any(s.signal_type == "fractal_break_up" for s in signals), \
        f"应触发看涨突破, got {[s.signal_type for s in signals]}"


def test_no_signal_without_trend_filter():
    # 同样的突破结构, 但整体下行趋势 -> 看涨信号必须被 EMA 过滤掉
    closes = [200 - 0.3 * i for i in range(200)]                  # 200→140.3 下行
    closes += [139.5, 140.5, 141.2, 141.5, 141.2, 140.5,
               139.8, 139.2, 138.8, 138.5]
    closes += [138.3, 138.1, 137.9, 138.0, 138.1, 138.2,
               138.3, 138.4, 141.9, 142.9]                        # 突破分形高点
    highs = [c + 0.3 for c in closes]
    lows = [c - 0.3 for c in closes]
    highs[203] = closes[203] + 0.8
    rule = FractalBreakRule({"ema_fast": 14, "ema_slow": 200})
    signals = rule.check("x", "测试股", make_kline(highs, lows, closes))
    assert not any(s.signal_type == "fractal_break_up" for s in signals), \
        f"下行趋势中不应触发看涨, got {[s.signal_type for s in signals]}"


def test_insufficient_history_skipped():
    rule = FractalBreakRule({"ema_fast": 14, "ema_slow": 200})
    df = make_kline([10.0] * 50, [9.0] * 50)
    assert rule.check("x", "测试股", df) == []
