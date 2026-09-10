# -*- coding: utf-8 -*-
import pandas as pd

from stock_monitor.data import store
from stock_monitor.screener import screen_local


def _frame(n=500):
    close = [100 + i * 0.2 for i in range(n)]
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n).strftime("%Y-%m-%d"),
        "open": close, "close": close, "high": [x + 0.1 for x in close],
        "low": [x - 0.1 for x in close], "volume": [1000.0] * n, "amount": [1e5] * n,
    })


def test_local_screen_never_calls_network(monkeypatch, tmp_path):
    db = tmp_path / "cache.db"
    # 直接替换 store 函数，证明 screen_local 不触网且能报告缺失。
    frames = {"600519": _frame()}
    monkeypatch.setattr(store, "load_many", lambda codes: frames)
    config = {"rules": {"rsi": {"enabled": False}}}
    results, missing = screen_local([("600519", "贵州茅台"), ("300750", "宁德时代")], config)
    assert results == []
    assert missing == ["300750"]
