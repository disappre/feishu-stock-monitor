# -*- coding: utf-8 -*-
"""K线反转形态+RSI背离规则的单元测试（判定标准提炼自 MQL5 文章 17962）。"""
import pandas as pd

from stock_monitor.engine.rules_candlestick import (
    CandlestickDivergenceRule,
    _bearish_engulfing,
    _bearish_pin_bar,
    _bullish_divergence,
    _bullish_engulfing,
    _bullish_pin_bar,
)


def row(open_, close, high, low):
    return pd.Series({"open": open_, "close": close, "high": high, "low": low})


def test_bullish_pin_bar():
    # 锤子线: 小实体在顶部, 长下影, 极短上影
    assert _bullish_pin_bar(10.0, 10.3, 10.4, 9.0)          # 实体0.3, 下影1.0, 上影0.1
    assert not _bullish_pin_bar(10.0, 10.3, 10.4, 9.8)      # 下影不足2倍实体
    assert not _bullish_pin_bar(10.0, 10.3, 10.7, 9.9)      # 上影超过0.5倍实体


def test_bearish_pin_bar():
    assert _bearish_pin_bar(10.3, 10.0, 11.0, 9.9)          # 长上影射击之星
    assert not _bearish_pin_bar(10.3, 10.0, 10.4, 9.9)      # 上影不足


def test_engulfing():
    o = pd.Series([10.5, 10.2])
    c = pd.Series([10.2, 10.8])   # 第二根阳线实体 [10.2,10.8] 包住第一根 [10.5,10.2]... 严格: open<=prev_close(10.2), close>=prev_open(10.5)
    assert _bullish_engulfing(o, c)
    o2 = pd.Series([10.0, 10.5])
    c2 = pd.Series([10.4, 9.9])   # 阴线实体 [10.5,9.9] 包住 [10.0,10.4]
    assert _bearish_engulfing(o2, c2)


def test_bullish_divergence():
    # 价格: 回溯窗口低点 11 高于信号K线低点 10 (创新低)
    lows = pd.Series([11.0, 11.5, 11.2, 11.8, 11.4, 11.6, 10.0])
    # RSI: 回溯窗口 25 低于信号K线 28 (低点抬高), 且信号K线 28 < 超卖30
    rsis = pd.Series([25.0, 30.0, 26.0, 32.0, 27.0, 29.0, 28.0])
    assert _bullish_divergence(lows, rsis, oversold=30, lo=1, hi=5)
    # RSI 同步新低 -> 无背离
    rsis_flat = pd.Series([32.0, 30.0, 31.0, 29.0, 30.0, 28.0, 28.0])
    assert not _bullish_divergence(lows, rsis_flat, oversold=30, lo=1, hi=5)
    # RSI 不在超卖区 -> 无信号
    rsis_warm = pd.Series([45.0, 50.0, 46.0, 52.0, 47.0, 49.0, 48.0])
    assert not _bullish_divergence(lows, rsis_warm, oversold=30, lo=1, hi=5)


def test_rule_skips_short_kline():
    rule = CandlestickDivergenceRule({"lookback_max": 15})
    assert rule.check("x", "测试股", pd.DataFrame({
        "open": [10.0] * 5, "close": [10.0] * 5,
        "high": [10.1] * 5, "low": [9.9] * 5, "volume": [1e6] * 5,
    })) == []
