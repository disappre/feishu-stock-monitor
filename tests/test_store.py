# -*- coding: utf-8 -*-
from datetime import datetime

import pandas as pd

from stock_monitor.data import store


def frame(dates):
    return pd.DataFrame({
        "date": dates, "open": [10.0] * len(dates), "close": [10.5] * len(dates),
        "high": [11.0] * len(dates), "low": [9.5] * len(dates),
        "volume": [1000.0] * len(dates), "amount": [10000.0] * len(dates),
    })


def test_save_excludes_today_and_is_idempotent(tmp_path):
    db = tmp_path / "cache.db"
    df = frame(["2026-09-07", "2026-09-08", "2026-09-09"])
    # 盘中(14:00<15:05): 当日bar未收盘,不入库
    midday = datetime(2026, 9, 9, 14, 0)
    assert store.save_confirmed("600519", df, "test", db, midday) == 2
    assert store.save_confirmed("600519", df, "test", db, midday) == 2  # 幂等
    cached = store.load_recent("600519", 10, db)
    assert list(cached["date"]) == ["2026-09-07", "2026-09-08"]
    state = store.status(["600519"], db)["600519"]
    assert state["status"] == "ok" and state["last_trade_date"] == "2026-09-08"
    # 收盘后(16:00>15:05): 当日bar定案入库
    evening = datetime(2026, 9, 9, 16, 0)
    assert store.save_confirmed("600519", df, "test", db, evening) == 3
    cached = store.load_recent("600519", 10, db)
    assert list(cached["date"]) == ["2026-09-07", "2026-09-08", "2026-09-09"]


def test_load_many_limits_and_groups(tmp_path):
    db = tmp_path / "cache.db"
    now = datetime(2026, 9, 10)
    dates = [f"2026-09-{i:02d}" for i in range(1, 10)]
    store.save_confirmed("000001", frame(dates), "test", db, now)
    store.save_confirmed("000002", frame(dates), "test", db, now)
    data = store.load_many(["000001", "000002", "missing"], rows=3, db_path=db)
    assert set(data) == {"000001", "000002"}
    assert all(len(df) == 3 for df in data.values())
    assert data["000001"]["date"].iloc[0] == "2026-09-07"


def test_failed_status_is_explicit(tmp_path):
    db = tmp_path / "cache.db"
    store.mark_failed("300750", "network timeout", db)
    state = store.status(["300750"], db)["300750"]
    assert state["status"] == "failed"
    assert "timeout" in state["error"]
