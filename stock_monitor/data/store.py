# -*- coding: utf-8 -*-
"""日线 SQLite 存储：固定 daily/qfq 口径，供收盘同步与本地筛选使用。"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Iterable

import pandas as pd

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "kline_daily_cache.db"
SPEC_PERIOD = "daily"
SPEC_ADJUST = "qfq"


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("""CREATE TABLE IF NOT EXISTS kline_daily (
        code TEXT NOT NULL, date TEXT NOT NULL, open REAL NOT NULL, close REAL NOT NULL,
        high REAL NOT NULL, low REAL NOT NULL, volume REAL, amount REAL,
        period TEXT NOT NULL DEFAULT 'daily', adjust TEXT NOT NULL DEFAULT 'qfq',
        provider TEXT NOT NULL DEFAULT 'unknown', PRIMARY KEY (code, date, period, adjust))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sync_meta (
        code TEXT NOT NULL, period TEXT NOT NULL, adjust TEXT NOT NULL,
        last_trade_date TEXT, updated_at TEXT NOT NULL, provider TEXT, status TEXT NOT NULL,
        error TEXT, PRIMARY KEY (code, period, adjust))""")
    return conn


def initialize(db_path: Path | str = DB_PATH) -> None:
    with connect(db_path):
        pass


MARKET_CLOSE = (15, 5)   # A股15:00收盘，留5分钟数据落地缓冲


def _closed_rows(df: pd.DataFrame, now: datetime | None = None) -> list[tuple]:
    """筛选已收盘K线。当日bar在收盘时刻（15:05）后才算定案入库——
    修复bug：此前date>=today一律跳过，收盘后重新同步也永远缺当日数据。"""
    now = now or datetime.now()
    today = now.strftime("%Y-%m-%d")
    today_closed = (now.hour, now.minute) >= MARKET_CLOSE
    rows = []
    for _, r in df.iterrows():
        date = str(r["date"])[:10]
        if date > today:
            continue                      # 未来数据不可能
        if date == today and not today_closed:
            continue                      # 盘中未收盘，不落库
        rows.append((date, float(r["open"]), float(r["close"]), float(r["high"]),
                     float(r["low"]), float(r["volume"]), float(r.get("amount", 0) or 0)))
    return rows


def save_confirmed(code: str, df: pd.DataFrame, provider: str,
                   db_path: Path | str = DB_PATH, now: datetime | None = None) -> int:
    """保存已收盘日线并刷新元数据；同一日覆盖，保留最近来源。"""
    rows = _closed_rows(df, now)
    stamp = (now or datetime.now()).isoformat(timespec="seconds")
    with connect(db_path) as conn:
        if rows:
            conn.executemany("""INSERT OR REPLACE INTO kline_daily
                (code,date,open,close,high,low,volume,amount,period,adjust,provider)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [(code, *r, SPEC_PERIOD, SPEC_ADJUST, provider) for r in rows])
        latest = rows[-1][0] if rows else None
        conn.execute("""INSERT INTO sync_meta
            (code,period,adjust,last_trade_date,updated_at,provider,status,error)
            VALUES (?,?,?,?,?,?,?,NULL)
            ON CONFLICT(code,period,adjust) DO UPDATE SET
              last_trade_date=excluded.last_trade_date, updated_at=excluded.updated_at,
              provider=excluded.provider, status=excluded.status, error=NULL""",
            (code, SPEC_PERIOD, SPEC_ADJUST, latest, stamp, provider, "ok"))
    return len(rows)


def mark_failed(code: str, error: str, db_path: Path | str = DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.execute("""INSERT INTO sync_meta
            (code,period,adjust,last_trade_date,updated_at,provider,status,error)
            VALUES (?,?,?,NULL,?,?,?,?)
            ON CONFLICT(code,period,adjust) DO UPDATE SET
              updated_at=excluded.updated_at, status=excluded.status, error=excluded.error""",
            (code, SPEC_PERIOD, SPEC_ADJUST, datetime.now().isoformat(timespec="seconds"),
             None, "failed", error[:300]))


def load_recent(code: str, rows: int = 500, db_path: Path | str = DB_PATH) -> pd.DataFrame | None:
    with connect(db_path) as conn:
        data = conn.execute("""SELECT date,open,close,high,low,volume,amount FROM (
            SELECT date,open,close,high,low,volume,amount FROM kline_daily
            WHERE code=? AND period=? AND adjust=? ORDER BY date DESC LIMIT ?)
            ORDER BY date""", (code, SPEC_PERIOD, SPEC_ADJUST, rows)).fetchall()
    if not data:
        return None
    return pd.DataFrame(data, columns=["date","open","close","high","low","volume","amount"])


def load_many(codes: Iterable[str], rows: int = 500,
              db_path: Path | str = DB_PATH) -> dict[str, pd.DataFrame]:
    codes = list(codes)
    if not codes:
        return {}
    placeholders = ",".join("?" * len(codes))
    sql = f"""SELECT code,date,open,close,high,low,volume,amount FROM (
        SELECT code,date,open,close,high,low,volume,amount,
        ROW_NUMBER() OVER(PARTITION BY code ORDER BY date DESC) AS rn FROM kline_daily
        WHERE code IN ({placeholders}) AND period=? AND adjust=?)
        WHERE rn<=? ORDER BY code,date"""
    with connect(db_path) as conn:
        data = conn.execute(sql, (*codes, SPEC_PERIOD, SPEC_ADJUST, rows)).fetchall()
    groups: dict[str, list[tuple]] = {}
    for code, *row in data:
        groups.setdefault(code, []).append(tuple(row))
    cols = ["date","open","close","high","low","volume","amount"]
    return {code: pd.DataFrame(items, columns=cols) for code, items in groups.items()}


def status(codes: Iterable[str], db_path: Path | str = DB_PATH) -> dict[str, dict]:
    codes = list(codes)
    if not codes:
        return {}
    marks = ",".join("?" * len(codes))
    with connect(db_path) as conn:
        rows = conn.execute(f"SELECT code,last_trade_date,status,error FROM sync_meta WHERE code IN ({marks})",
                            codes).fetchall()
    return {r[0]: {"last_trade_date": r[1], "status": r[2], "error": r[3]} for r in rows}
