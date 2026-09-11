#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""收盘后写走势快照到飞书多维表格（自选股全量）。

每日收盘调度的一部分：对 my_picks.txt 每只股票写入
- 每日走势快照表：价格/DIF/防狼术状态/缠论位置/笔方向
- 缠论结构状态表：结构参数（每3日一次，结构变化慢）

用法： python tools/log_daily_trend.py [--chan]  （--chan=同时写缠论结构表）
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import logging  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("trend")

from stock_monitor.data import store  # noqa: E402
from stock_monitor.notify import trend_log  # noqa: E402


def load_picks() -> list[tuple[str, str]]:
    picks = []
    pf = REPO / "data" / "my_picks.txt"
    if pf.exists():
        for line in pf.read_text(encoding="utf-8").splitlines():
            parts = [p.strip() for p in line.split("#", 1)[0].split(",")]
            if parts and parts[0].isdigit() and len(parts[0]) == 6:
                picks.append((parts[0], parts[1] if len(parts) > 1 else parts[0]))
    return picks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chan", action="store_true", help="同时写缠论结构状态表")
    args = ap.parse_args()

    picks = load_picks()
    logger.info("走势快照: %d只自选股%s", len(picks),
                "（含缠论结构）" if args.chan else "")
    ok = fail = 0
    for code, name in picks:
        kline = store.load_recent(code)
        if kline is None or len(kline) < 40:
            fail += 1
            continue
        chan_info = None
        if args.chan:
            try:
                from chan_analysis import analyze
                cs = analyze(code, name)
                if cs is not None:
                    zs = cs.last_zs
                    last_px = float(cs.c.bars_raw[-1].close)
                    pos = ("中枢上方" if zs and last_px > zs.gg else
                           "中枢下方" if zs and last_px < zs.dd else
                           "中枢内部" if zs else "无中枢")
                    seg = cs.segments[-1] if cs.segments else None
                    chan_info = {"位置": pos,
                                 "笔方向": str(cs.last_bi.direction)}
                    trend_log.log_chan_status(code, name, cs, "")
            except Exception:
                logger.exception("缠论状态写入失败 %s", code)
        trend_log.log_daily_snapshot_full(code, name, kline, chan_info)
        ok += 1
    logger.info("完成: 成功%d 失败%d", ok, fail)
    return 0


if __name__ == "__main__":
    sys.exit(main())
