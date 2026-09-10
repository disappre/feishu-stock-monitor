# -*- coding: utf-8 -*-
"""全池收盘同步：受控并发抓取，主线程单写者写 SQLite。"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Iterable

from .data.feed import fetch_kline
from .data import store

logger = logging.getLogger("sync")


@dataclass
class SyncReport:
    requested: int = 0
    succeeded: int = 0
    failed: int = 0
    rows_written: int = 0
    failures: list[str] | None = None


def _fetch(item: tuple[str, str]) -> tuple[str, str, object, str | None]:
    code, name = item
    try:
        return code, name, fetch_kline(code), None
    except Exception as exc:
        return code, name, None, f"{type(exc).__name__}: {exc}"


def sync_pool(pool: Iterable[tuple[str, str]], workers: int = 4) -> SyncReport:
    """并发获取、主线程顺序落库；返回覆盖情况。"""
    items = list(pool)
    store.initialize()
    report = SyncReport(requested=len(items), failures=[])
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="kline-fetch") as executor:
        futures = [executor.submit(_fetch, item) for item in items]
        for future in as_completed(futures):
            code, name, df, error = future.result()
            if df is None or df.empty:
                report.failed += 1
                msg = error or "未取到行情"
                report.failures.append(f"{code} {name}: {msg}")
                store.mark_failed(code, msg)
                continue
            # 当前 feed 未透传来源；同步状态保守标记为 market_fallback。
            report.rows_written += store.save_confirmed(code, df, provider="market_fallback")
            report.succeeded += 1
    logger.info("同步完成: 请求%d 成功%d 失败%d 写入%d行",
                report.requested, report.succeeded, report.failed, report.rows_written)
    return report
