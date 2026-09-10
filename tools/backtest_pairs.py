#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配对价差信号交叉验证：对已筛选协整配对做双腿对齐回放。

回放语义（与 backtest.py 单腿回放严格一致，防前视）：
- 第 i 根收盘时：两腿各取最近 lookback 根（≤i），对齐日期交集，
  计算价差 z-score（均值/标准差只用 ≤i 数据）；
- z 触发 |z| >= entry_z → 信号；同配对冷却 fwd_days 根；
- 入场 = 两腿各自次日开盘；出场 = 两腿各自第 fwd_days 日收盘；
- 配对收益 = 两腿收益按对冲方向合成（z<0 做多本腿A做空B×hedge，z>0 反向）；
  A股做空受限，故同时输出"仅多头腿"口径作为可执行参照；
- 全局日期 7:3 切分 train/test，与单腿回测同一审判标准。

用法：
    python tools/backtest_pairs.py [--fwd-days 5] [--entry-z 2.0] [--lookback 60]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from stock_monitor.backtest import split_by_date  # noqa: E402
from stock_monitor.data import store  # noqa: E402

OUT = REPO / "data" / "pairs_backtest.json"


class PairRecord:
    __slots__ = ("pair_id", "date", "z", "ret_pair", "ret_long_leg")

    def __init__(self, pair_id, date, z, ret_pair, ret_long_leg):
        self.pair_id = pair_id
        self.date = date
        self.z = z
        self.ret_pair = ret_pair
        self.ret_long_leg = ret_long_leg


def replay_pair(pair: dict, frames: dict, fwd_days: int, entry_z: float,
                lookback: int) -> list[PairRecord]:
    a, b = pair["a"], pair["b"]
    ka, kb = frames.get(a), frames.get(b)
    if ka is None or kb is None:
        return []
    # 日期对齐的联合DataFrame
    ja = ka.set_index("date")[["open", "close"]]
    jb = kb.set_index("date")[["open", "close"]]
    j = ja.join(jb, how="inner", lsuffix="_a", rsuffix="_b").sort_index()
    if len(j) < lookback + fwd_days + 2:
        return []
    hedge = float(pair.get("hedge_ratio", 1.0))
    ca = j["close_a"].to_numpy(float)
    cb = j["close_b"].to_numpy(float)
    oa = j["open_a"].to_numpy(float)
    ob = j["open_b"].to_numpy(float)
    dates = j.index.astype(str).tolist()
    la, lb = np.log(ca), np.log(cb)
    spread = la - hedge * lb

    records: list[PairRecord] = []
    last_bar = -10**9
    for i in range(lookback - 1, len(j) - fwd_days):
        # z-score 只用 ≤i 的 lookback 窗口（无前视）
        w = spread[i - lookback + 1: i + 1]
        sd = w.std()
        if sd < 1e-10:
            continue
        z = (spread[i] - w.mean()) / sd
        if abs(z) < entry_z:
            continue
        if i - last_bar < fwd_days:   # 冷却去重
            continue
        last_bar = i
        # 两腿入场=次日开盘，出场=i+fwd日收盘
        ret_a = (ca[i + fwd_days] / oa[i + 1] - 1) * 100
        ret_b = (cb[i + fwd_days] / ob[i + 1] - 1) * 100
        # 配对收益: z<0 → spread偏低 → 多A空B(hedge倍)；z>0 → 反向
        sign = -1.0 if z < 0 else 1.0
        ret_pair = sign * (ret_a - hedge * ret_b)
        # 仅多头腿口径(A股可执行参照): z<0→买A；z>0→买B
        ret_long = ret_a if z < 0 else ret_b
        records.append(PairRecord(pair["pair_id"], dates[i], float(z),
                                  float(ret_pair), float(ret_long)))
    return records


def summarize(records: list[PairRecord], field: str) -> dict:
    if not records:
        return {"n": 0, "win_rate": None, "mean_pct": None, "median_pct": None}
    vals = [getattr(r, field) for r in records]
    wins = sum(1 for v in vals if v > 0)
    return {"n": len(vals), "win_rate": round(wins / len(vals), 3),
            "mean_pct": round(sum(vals) / len(vals), 3),
            "median_pct": round(sorted(vals)[len(vals) // 2], 3)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fwd-days", type=int, default=5)
    ap.add_argument("--entry-z", type=float, default=2.0)
    ap.add_argument("--lookback", type=int, default=60)
    ap.add_argument("--train-ratio", type=float, default=0.7)
    ap.add_argument("--limit-pairs", type=int, default=None)
    args = ap.parse_args()

    data = json.loads((REPO / "data" / "pairs.json").read_text(encoding="utf-8"))
    pairs = data["pairs"]
    if args.limit_pairs:
        pairs = pairs[: args.limit_pairs]
    codes = sorted({c for p in pairs for c in (p["a"], p["b"])})
    frames = store.load_many(codes)
    print(f"回放配对: {len(pairs)} 对（{len(codes)} 只股票），"
          f"前瞻{args.fwd_days}日，entry_z={args.entry_z}，窗口{args.lookback}")

    records: list[PairRecord] = []
    for idx, p in enumerate(pairs):
        records.extend(replay_pair(p, frames, args.fwd_days,
                                   args.entry_z, args.lookback))
        if (idx + 1) % 100 == 0:
            print(f"  进度 {idx + 1}/{len(pairs)} 对，累计信号 {len(records)}", flush=True)

    train, test, split_date = split_by_date(records, args.train_ratio)
    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "fwd_days": args.fwd_days, "entry_z": args.entry_z,
        "lookback": args.lookback, "n_pairs": len(pairs),
        "split_date": split_date,
        "pair_return": {  # 配对合成收益(理论口径,含做空腿)
            "full": summarize(records, "ret_pair"),
            "train": summarize(train, "ret_pair"),
            "test": summarize(test, "ret_pair"),
        },
        "long_leg_return": {  # 仅多头腿(A股可执行参照)
            "full": summarize(records, "ret_long_leg"),
            "train": summarize(train, "ret_long_leg"),
            "test": summarize(test, "ret_long_leg"),
        },
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    for label, key in (("配对合成(理论)", "pair_return"), ("仅多头腿(A股可执行)", "long_leg_return")):
        r = result[key]
        f, tr, te = r["full"], r["train"], r["test"]
        print(f"\n[{label}]")
        print(f"  全样本: n={f['n']} 胜率={f['win_rate']} 均值={f['mean_pct']}%")
        print(f"  训练期: n={tr['n']} 胜率={tr['win_rate']} 均值={tr['mean_pct']}%")
        print(f"  测试期: n={te['n']} 胜率={te['win_rate']} 均值={te['mean_pct']}%")
    print(f"\n结果已写入: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
