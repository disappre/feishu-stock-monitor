# -*- coding: utf-8 -*-
"""规则引擎单元测试：用构造的K线验证信号触发逻辑，不依赖网络。
运行: python -m pytest tests/ -v
"""
import pandas as pd
import pytest

from stock_monitor.engine.base import RuleEngine
from stock_monitor.engine.rules_ma import MACrossRule
from stock_monitor.engine.rules_price import PriceSurgeRule, RSIRule


def make_kline(closes: list[float]) -> pd.DataFrame:
    n = len(closes)
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n),
        "open": closes, "close": closes,
        "high": closes, "low": closes,
        "volume": [1e6] * n,
    })


CFG = {
    "rules": {
        "ma_cross": {"enabled": True, "short": 5, "long": 20},
        "price_surge": {"enabled": True, "pct_threshold": 5.0},
        "volume_surge": {"enabled": True, "ratio_threshold": 2.0},
        "rsi": {"enabled": True, "period": 14, "overbought": 80, "oversold": 20},
    }
}


def test_golden_cross_triggers():
    # 前28天阴跌(长均线在上)，最后3天急拉 -> 交叉恰好落在最后一天
    closes = [100 - i * 0.5 for i in range(28)] + [88, 94]
    signals = MACrossRule({"short": 3, "long": 10}).check("600519", "贵州茅台", make_kline(closes))
    assert any(s.signal_type == "ma_golden_cross" for s in signals)


def test_flat_market_no_signal():
    closes = [100.0] * 30
    engine = RuleEngine(CFG)
    assert engine.scan("600519", "贵州茅台", make_kline(closes)) == []


def test_price_surge_triggers():
    closes = [100.0] * 30
    closes[-1] = 106.0  # 单日 +6%
    signals = PriceSurgeRule({"pct_threshold": 5.0}).check("x", "测试股", make_kline(closes))
    assert len(signals) == 1 and signals[0].direction == "bullish"


def test_rsi_overbought():
    # 连续单边上涨30天 -> RSI 进入超买
    closes = [100 + i * 1.5 for i in range(30)]
    signals = RSIRule({"period": 14, "overbought": 80, "oversold": 20}).check(
        "x", "测试股", make_kline(closes))
    assert any(s.signal_type == "rsi_超买" for s in signals)


def test_engine_skips_empty_kline():
    engine = RuleEngine(CFG)
    assert engine.scan("x", "测试股", pd.DataFrame()) == []
