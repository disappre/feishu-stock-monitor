# -*- coding: utf-8 -*-
"""回测核心单元测试：边沿触发、电平冷却、前瞻收益、无未来函数、日期切分。"""
import pandas as pd

from stock_monitor.backtest import (
    BacktestRecord, replay_rule, split_by_date, summarize)
from stock_monitor.engine.rules_ma import MACrossRule
from stock_monitor.engine.rules_price import RSIRule


def make_kline(closes, volume=1e6):
    n = len(closes)
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n).strftime("%Y-%m-%d"),
        "open": [c * 0.999 for c in closes], "close": closes,
        "high": [c * 1.005 for c in closes], "low": [c * 0.995 for c in closes],
        "volume": [volume] * n,
    })


def test_golden_cross_counted_once_with_cooldown():
    # 30天阴跌后急拉 → 一次金叉；即便之后趋势延续也不重复计数
    closes = [100 - i * 0.5 for i in range(28)] + [88, 94, 96, 98, 100, 102]
    rule = MACrossRule({"short": 3, "long": 10})
    records = replay_rule(rule, "600519", "测试股", make_kline(closes), fwd_days=3)
    golden = [r for r in records if r.signal_type == "ma_golden_cross"]
    assert len(golden) == 1


def test_fwd_return_uses_next_open_and_future_close():
    # 阴跌后急拉产生一次金叉；入场=信号次日开盘，出场=信号+fwd_days日收盘。
    # 信号下标由回放结果反推（日期→行号），避免测试硬编码与构造漂移。
    closes = [100 - i * 0.5 for i in range(28)] + [88, 94] + [96.0, 98.0, 99.0, 100.0, 100.5]
    kline = make_kline(closes)
    rule = MACrossRule({"short": 3, "long": 10})
    records = replay_rule(rule, "x", "测试股", kline, fwd_days=3)
    golden = [r for r in records if r.signal_type == "ma_golden_cross"]
    assert len(golden) == 1
    r = golden[0]
    i = kline.index[kline["date"] == r.date][0]        # 信号所在行
    entry = kline["open"].iloc[i + 1]
    exit_ = kline["close"].iloc[i + 3]
    expected = (exit_ / entry - 1) * 100
    assert abs(r.fwd_ret - expected) < 1e-9


def test_no_future_beyond_last_bar():
    # 末尾 fwd_days 根不产生信号（无法计算前瞻收益）
    closes = [100 - i * 0.5 for i in range(28)] + [88, 94] + [float(90 + i) for i in range(3)]
    rule = MACrossRule({"short": 3, "long": 10})
    records = replay_rule(rule, "x", "测试股", make_kline(closes), fwd_days=3)
    assert all(r.date < str(pd.Timestamp("2026-01-31")) for r in records)


def test_rsi_level_triggered_cooldown():
    # 连续单边上涨 → RSI持续超买；冷却期内只应计1次
    closes = [100 + i * 1.5 for i in range(40)]
    rule = RSIRule({"period": 14, "overbought": 70, "oversold": 30})
    records = replay_rule(rule, "x", "测试股", make_kline(closes), fwd_days=5)
    overbought = [r for r in records if r.signal_type == "rsi_超买"]
    assert len(overbought) >= 1
    # 冷却校验：任意两次触发间隔 >= fwd_days
    bars = [int((pd.Timestamp(r.date) - pd.Timestamp("2026-01-01")).days) for r in overbought]
    for a, b in zip(bars, bars[1:]):
        assert b - a >= 5


def test_split_by_date_no_overlap():
    recs = [BacktestRecord("a", f"2026-01-{d:02d}", "t", "bullish", 1.0)
            for d in range(1, 31)]
    train, test, split = split_by_date(recs, 0.7)
    assert train and test
    assert max(r.date for r in train) <= split < min(r.date for r in test)


def test_summarize_direction_aware():
    recs = [
        BacktestRecord("a", "2026-01-01", "ma_golden_cross", "bullish", 2.0),   # win
        BacktestRecord("b", "2026-01-02", "ma_golden_cross", "bullish", -1.0),  # lose
        BacktestRecord("c", "2026-01-03", "ma_golden_cross", "bearish", -2.0),  # bearish跌=win
    ]
    s = summarize(recs)
    assert s["ma_golden_cross"]["n"] == 3
    assert s["ma_golden_cross"]["win_rate"] == round(2 / 3, 3)
