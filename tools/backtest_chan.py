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

    # (kind, date, direction, fwd_ret) + 方向门控分组：信号是否顺日线段方向
    records = []
    records_gated = []     # (record, aligned: bool) aligned=顺日线段方向
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

        def seg_dir_at(di) -> int:
            """信号日的日线段方向: 1=向上, -1=向下, 0=无"""
            for (sdt, spx, edt, epx, sdir) in cs.segments:
                if str(sdt)[:10] <= dates[di] <= str(edt)[:10]:
                    return sdir
            return 0

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
            rec = BacktestRecord(
                code=code, date=dates[i],
                signal_type=f"chan_{kind}",
                direction="bullish" if kind in BUY_KINDS else "bearish",
                fwd_ret=ret)
            records.append(rec)
            # 方向门控: 买点顺向上段=aligned; 卖点顺向下段=aligned
            sd = seg_dir_at(i)
            is_buy = kind in BUY_KINDS
            aligned = (is_buy and sd == 1) or ((not is_buy) and sd == -1)
            records_gated.append((rec, aligned))
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

    # ── alpha审判：基线（全池任意日负收益占比） ──
    def baseline_neg_rate(lo: str, hi: str):
        import numpy as np
        rets = []
        for k in frames.values():
            k = k.reset_index(drop=True)
            c, o = k["close"].to_numpy(float), k["open"].to_numpy(float)
            d = k["date"].astype(str)
            for i in range(1, len(k) - args.fwd_days):
                if lo <= d.iloc[i] <= hi and o[i + 1] > 0:
                    rets.append(c[i + args.fwd_days] / o[i + 1] - 1)
        return float(np.mean(np.array(rets) < 0)) if rets else None

    base_test = baseline_neg_rate(split_date, "9999-12-31")
    base_full = baseline_neg_rate("0000-01-01", "9999-12-31")
    result["baseline_neg_rate_test"] = round(base_test, 3) if base_test else None
    result["baseline_neg_rate_full"] = round(base_full, 3) if base_full else None

    # ── 方向门控分组：顺/逆日线段方向的信号质量对比（三级分工体系检验） ──
    def gated_stats(recs_g):
        groups = {}
        for r, aligned in recs_g:
            key = f"{'顺' if aligned else '逆'}段·{r.signal_type.replace('chan_', '')}"
            groups.setdefault(key, []).append(r)
        out = {}
        for key, items in sorted(groups.items()):
            wins = sum(1 for r in items if r.is_win)
            rets = [r.fwd_ret for r in items]
            out[key] = {"n": len(items), "win_rate": round(wins / len(items), 3),
                        "mean_pct": round(sum(rets) / len(rets), 3)}
        return out

    test_g = [rg for rg in records_gated if rg[0].date > split_date]
    result["gated_full"] = gated_stats(records_gated)
    result["gated_test"] = gated_stats(test_g)

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n基线(负收益占比): 全样本={base_full:.1%} 测试期={base_test:.1%}")
    print(f"\n{'信号':<8}{'全n':>5}{'全胜率':>8}{'全alpha':>8}"
          f"{'测n':>5}{'测胜率':>8}{'测alpha':>8}{'测均值':>8}")
    for kind in result["full"]:
        f, te = result["full"][kind], result["test"].get(kind)
        fa = f["win_rate"] - base_full if base_full else 0
        ta = (te["win_rate"] - base_test) if te and base_test else 0
        print(f"{kind:<8}{f['n']:>5}{f['win_rate']:>8.1%}{fa:>+8.1%}"
              f"{(te or {}).get('n', 0):>5}{(te or {}).get('win_rate', 0):>8.1%}"
              f"{ta:>+8.1%}{(te or {}).get('mean_pct', 0):>+8.2f}")
    print(f"\n── 方向门控分组（顺/逆日线段方向，三级分工检验）──")
    print(f"{'分组':<16}{'全n':>5}{'全胜率':>8}{'测n':>5}{'测胜率':>8}")
    for key, s in result["gated_full"].items():
        t = result["gated_test"].get(key, {"n": 0, "win_rate": 0})
        print(f"{key:<16}{s['n']:>5}{s['win_rate']:>8.1%}{t['n']:>5}{t['win_rate']:>8.1%}")
    print(f"\n报告: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
