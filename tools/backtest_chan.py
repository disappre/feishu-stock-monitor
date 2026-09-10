#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缠论六类买卖点历史回测：复用 backtest.py 的口径（方向感知+日期切分）。

信号→交易翻译（与单腿规则回测一致）：
- 买点(一买/二买/最强二买/三买)：信号笔终点日 i 收盘确认 → 次日开盘入场 →
  第 fwd_days 日收盘出场，win = 前瞻收益 > 0
- 卖点(一卖/二卖/最强二卖/三卖)：方向 bearish，win = 前瞻收益 < 0
  （A股可空仓不可做空，卖点语义=离场回避；胜率含义同 rsi_超买 等回避信号）

用法：
    python tools/backtest_chan.py [--fwd-days 5] [--pool-limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

from stock_monitor.backtest import BacktestRecord, split_by_date  # noqa: E402
from stock_monitor.data import store  # noqa: E402

from chan_analysis import (  # noqa: E402
    analyze, detect_1st_2nd_points, detect_3rd_points)

OUT = REPO / "data" / "chan_backtest.json"
BUY_KINDS = {"一买", "二买", "最强二买", "三买"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fwd-days", type=int, default=5)
    ap.add_argument("--train-ratio", type=float, default=0.7)
    ap.add_argument("--pool-limit", type=int, default=None)
    args = ap.parse_args()

    names = {}
    for line in (REPO / "data" / "screen_pool.txt").read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in line.split("#", 1)[0].split(",")]
        if len(parts) >= 2 and parts[0].isdigit() and len(parts[0]) == 6:
            names[parts[0]] = parts[1]
    codes = list(names)
    if args.pool_limit:
        codes = codes[: args.pool_limit]

    frames = store.load_many(codes)
    print(f"回测: {len(codes)}只, 前瞻{args.fwd_days}日", flush=True)

    # (kind, date, direction, fwd_ret)
    records = []
    for n, (code, name) in enumerate(names.items()):
        if code not in frames or frames[code] is None or len(frames[code]) < 60:
            continue
        cs = analyze(code, name)
        if cs is None:
            continue
        pts = detect_1st_2nd_points(cs.c.zs_list, cs.c.bi_list,
                                    cs.segments, cs.df) \
            + detect_3rd_points(cs.c.zs_list, cs.c.bi_list)
        kline = cs.df.reset_index(drop=True)
        dates = kline["date"].astype(str).str.slice(0, 10).tolist()
        close = kline["close"].to_numpy(float)
        open_ = kline["open"].to_numpy(float)
        dmap = {d: i for i, d in enumerate(dates)}
        for bi_idx, kind, px in pts:
            if bi_idx >= len(cs.c.bi_list):
                continue
            d = str(cs.c.bi_list[bi_idx].fx_b.dt)[:10]
            i = dmap.get(d)
            if i is None or i + args.fwd_days >= len(kline) or i + 1 >= len(kline):
                continue
            entry, exit_ = open_[i + 1], close[i + args.fwd_days]
            if entry <= 0:
                continue
            ret = (exit_ / entry - 1) * 100
            records.append(BacktestRecord(
                code=code, date=dates[i],
                signal_type=f"chan_{kind}",
                direction="bullish" if kind in BUY_KINDS else "bearish",
                fwd_ret=ret))
        if (n + 1) % 50 == 0:
            print(f"  进度 {n + 1}/{len(names)}, 信号{len(records)}", flush=True)

    print(f"\n总信号: {len(records)}")
    train, test, split_date = split_by_date(records, args.train_ratio)
    print(f"切分: {split_date}, train={len(train)}, test={len(test)}")

    def summarize(recs):
        groups = {}
        for r in recs:
            kind = r.signal_type.replace("chan_", "")
            groups.setdefault(kind, []).append(r)
        stats = {}
        for kind, items in sorted(groups.items()):
            wins = sum(1 for r in items if r.is_win)
            rets = [r.fwd_ret for r in items]
            stats[kind] = {"n": len(items),
                           "win_rate": round(wins / len(items), 3),
                           "mean_pct": round(sum(rets) / len(rets), 3)}
        return stats

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "fwd_days": args.fwd_days, "split_date": split_date,
        "n_stocks": len(frames), "n_signals": len(records),
        "full": summarize(records), "train": summarize(train),
        "test": summarize(test),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{'信号':<8}{'全n':>5}{'全胜率':>8}{'全均值':>8}"
          f"{'训n':>5}{'训胜率':>8}{'测n':>5}{'测胜率':>8}{'测均值':>8}")
    for kind in result["full"]:
        f, tr, te = result["full"][kind], result["train"].get(kind), result["test"].get(kind)
        print(f"{kind:<8}{f['n']:>5}{f['win_rate']:>8.1%}{f['mean_pct']:>+8.2f}"
              f"{(tr or {}).get('n', 0):>5}{(tr or {}).get('win_rate', 0):>8.1%}"
              f"{(te or {}).get('n', 0):>5}{(te or {}).get('win_rate', 0):>8.1%}"
              f"{(te or {}).get('mean_pct', 0):>+8.2f}")
    print(f"\n报告: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
