# -*- coding: utf-8 -*-
"""akshare 行情采集：带重试与基础频控。统一返回标准K线 DataFrame：
columns = [date, open, close, high, low, volume, amount]
"""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime
from functools import wraps
from pathlib import Path

import akshare as ak
import pandas as pd
import requests

logger = logging.getLogger(__name__)

KLINE_ROWS = 500  # 历史K线长度：约两年交易日。覆盖EMA200/形态窗口，并为回测提供
                  # 第二段市场状态样本（250根只有一段趋势市，状态依赖信号无法复检）

# 东方财富K线直连接口（备用源）
_EM_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_EM_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# 腾讯K线接口（主源：与东财独立，任一方风控时自动切换）
_TX_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

# 本地K线缓存（对标 Sequoia-X 的 SQLite 增量存储，见 knowledge/strategies/benchmark-sequoia-x.md）：
# 盘中扫描先读本地已收盘历史，再只补当日实时bar，大幅减少对数据源的请求量
_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "kline_cache.db"


def _db() -> "sqlite3.Connection":
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS kline ("
        " code TEXT, date TEXT, open REAL, close REAL, high REAL, low REAL,"
        " volume REAL, amount REAL, PRIMARY KEY (code, date))")
    return conn


def _cache_load(code: str, upto: str) -> pd.DataFrame | None:
    """读取截至 upto（含）的本地缓存K线。空表返回 None。"""
    with _db() as conn:
        rows = conn.execute(
            "SELECT date, open, close, high, low, volume, amount FROM kline"
            " WHERE code=? AND date<=? ORDER BY date", (code, upto)).fetchall()
    if not rows:
        return None
    return pd.DataFrame(rows, columns=["date", "open", "close", "high", "low",
                                       "volume", "amount"])


def _cache_save(code: str, df: pd.DataFrame) -> None:
    """落库（只写已收盘的K线；当日bar在15:05收盘缓冲后才入库）。"""
    if df is None or df.empty:
        return
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    today_closed = (now.hour, now.minute) >= (15, 5)
    rows = []
    for _, r in df.iterrows():
        d = str(r["date"])[:10]
        if d > today or (d == today and not today_closed):
            continue  # 未来/盘中未收盘的不落库
        rows.append((code, d, float(r["open"]), float(r["close"]),
                     float(r["high"]), float(r["low"]),
                     float(r["volume"]), float(r.get("amount", 0) or 0)))
    if not rows:
        return
    with _db() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO kline"
            " (code, date, open, close, high, low, volume, amount)"
            " VALUES (?,?,?,?,?,?,?,?)", rows)


def _retry(times: int = 3, pause: float = 2.0):
    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            for i in range(times):
                try:
                    return fn(*args, **kwargs)
                except Exception as e:
                    logger.warning("akshare 请求失败(%s/%s): %s", i + 1, times, e)
                    time.sleep(pause * (i + 1))
            return None
        return wrapper
    return deco


def _standardize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns={
        "日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
        "最低": "low", "成交量": "volume", "成交额": "amount",
    })
    cols = ["date", "open", "close", "high", "low", "volume", "amount"]
    df = df[[c for c in cols if c in df.columns]].tail(KLINE_ROWS)
    return df.reset_index(drop=True)


def _fetch_eastmoney_direct(code: str) -> pd.DataFrame | None:
    """直连东方财富K线接口（前复权日线）。沪市前缀1.，深市/创业板0.。"""
    market = "1" if code.startswith(("6", "5", "9")) else "0"
    params = {
        "secid": f"{market}.{code}",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "klt": "101", "fqt": "1", "end": "20500101", "lmt": str(KLINE_ROWS),
    }
    resp = requests.get(_EM_URL, params=params, headers=_EM_HEADERS, timeout=10)
    resp.raise_for_status()
    klines = (resp.json().get("data") or {}).get("klines") or []
    if not klines:
        return None
    rows = []
    for line in klines:  # 格式: 日期,开,收,高,低,成交量,成交额,振幅
        p = line.split(",")
        rows.append({"date": p[0], "open": float(p[1]), "close": float(p[2]),
                     "high": float(p[3]), "low": float(p[4]),
                     "volume": int(p[5]), "amount": float(p[6])})
    df = pd.DataFrame(rows)
    logger.info("行情就绪 %s: %d 根K线 (东财直连)", code, len(df))
    return df


def _fetch_tencent(code: str) -> pd.DataFrame | None:
    """腾讯K线接口（前复权日线）。行格式: [日期, 开, 收, 高, 低, 成交量(手)]。"""
    symbol = ("sh" if code.startswith(("6", "5", "9")) else "sz") + code
    params = {"param": f"{symbol},day,,,{KLINE_ROWS},qfq"}
    resp = requests.get(_TX_URL, params=params, headers=_EM_HEADERS, timeout=10)
    resp.raise_for_status()
    node = (resp.json().get("data") or {}).get(symbol) or {}
    klines = node.get("qfqday") or node.get("day") or []
    if not klines:
        return None
    df = pd.DataFrame([{
        "date": p[0], "open": float(p[1]), "close": float(p[2]),
        "high": float(p[3]), "low": float(p[4]), "volume": int(float(p[5])),
    } for p in klines if len(p) >= 6])
    logger.info("行情就绪 %s: %d 根K线 (腾讯)", code, len(df))
    return df


def fetch_name(code: str) -> str:
    """查询股票名称（腾讯行情实时接口，GBK编码，字段以 ~ 分隔）。失败回退占位名。"""
    try:
        symbol = ("sh" if code.startswith(("6", "5", "9")) else "sz") + code
        resp = requests.get(f"https://qt.gtimg.cn/q={symbol}",
                            headers=_EM_HEADERS, timeout=10)
        resp.encoding = "gbk"
        parts = resp.text.split("~")
        if len(parts) > 1 and parts[1]:
            return parts[1]
    except Exception as e:
        logger.warning("名称查询失败 %s: %s", code, e)
    return f"股票{code}"


def fetch_kline(code: str, period: str = "daily", adjust: str = "qfq") -> pd.DataFrame | None:
    """拉取单只股票近 N 日K线（前复权）。

    四级冗余：本地缓存+当日实时bar → 腾讯 → 东财直连 → akshare。
    本地有足够历史时只需一次"当日bar"请求，筛选300只池不再逐只全量拉取。
    """
    today = datetime.now().strftime("%Y-%m-%d")
    cached = _cache_load(code, "9999-12-31")
    need_full = cached is None or len(cached) < KLINE_ROWS - 5  # 缓存不足则全量拉

    if not need_full:
        try:
            fresh = _fetch_tencent(code)
            if fresh is not None and not fresh.empty:
                _cache_save(code, fresh)  # 落库已收盘部分
                live = fresh[fresh["date"].astype(str).str[:10] >= today]
                if not live.empty:  # 拼上当日实时bar
                    cached = pd.concat([cached, live], ignore_index=True)
                logger.info("行情就绪 %s: %d 根 (缓存%d+实时%d)",
                            code, len(cached), len(cached) - len(live), len(live))
                return cached.tail(KLINE_ROWS).reset_index(drop=True)
        except Exception as e:
            logger.warning("当日bar获取失败 %s: %s，退回纯缓存", code, type(e).__name__)
        return cached.tail(KLINE_ROWS).reset_index(drop=True)

    sources = [
        ("腾讯", lambda: _fetch_tencent(code)),
        ("东财直连", lambda: _fetch_eastmoney_direct(code)),
    ]
    if period == "daily":
        sources.append(("akshare", lambda: _standardize(
            ak.stock_zh_a_hist(symbol=code, period="daily", adjust=adjust))))
    else:
        sources.append(("akshare", lambda: _standardize(
            ak.stock_zh_a_hist_min_em(symbol=code, period="5"))))

    for name, fn in sources:
        try:
            df = fn()
            if df is not None and not df.empty:
                if name == "akshare":
                    logger.info("行情就绪 %s: %d 根K线 (akshare)", code, len(df))
                _cache_save(code, df)  # 全量模式落库已收盘部分
                return df.tail(KLINE_ROWS).reset_index(drop=True)
        except Exception as e:
            logger.warning("%s 源失败 %s: %s", name, code, type(e).__name__)
    logger.error("所有数据源均失败: %s", code)
    return None
