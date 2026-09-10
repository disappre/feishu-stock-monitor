#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""离线协整配对筛选：对股票池两两做 Engle-Granger 检验 + 价差 ADF 平稳性检验。

方法论：knowledge/strategies/cointegration-pairs-trading.md（提炼自 MQL5 19052）
- 相关性仅用于缩小候选（相关≠协整，不做硬过滤，避免漏掉低相关协整对）
- EG 检验 p<0.05 → 价差 ADF p<0.05 → 记录对冲系数与检验统计量
- 产出 data/pairs.json，供 engine/rules_pair.py 在线监测
- 协整关系会失效：窗口默认120个交易日，建议每月重跑

用法（仓库根目录）：
    python tools/find_pairs.py [--pool-limit 100] [--pvalue 0.05] [--min-overlap 100]
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from statsmodels.tsa.stattools import adfuller, coint  # noqa: E402

from stock_monitor.data import store  # noqa: E402

OUT = REPO / "data" / "pairs.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-limit", type=int, default=None)
    ap.add_argument("--pvalue", type=float, default=0.05, help="EG/ADF 显著性阈值")
    ap.add_argument("--min-overlap", type=int, default=100, help="两腿最少重叠交易日")
    ap.add_argument("--top-corr", type=int, default=50,
                    help="按相关性初筛：每只股票只与相关性最高的N只配对（控制组合数）")
    ap.add_argument("--strict-p", type=float, default=0.01,
                    help="入选阈值(EG与ADF均需低于此值)。默认0.01：p<0.05在1.3万次"
                         "多重检验下期望~650个假阳性，收紧到0.01压噪声")
    args = ap.parse_args()

    names = {}
    pool_file = REPO / "data" / "screen_pool.txt"
    for line in pool_file.read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in line.split("#", 1)[0].split(",")]
        if len(parts) >= 2 and parts[0].isdigit() and len(parts[0]) == 6:
            names[parts[0]] = parts[1]
    codes = list(names)
    if args.pool_limit:
        codes = codes[: args.pool_limit]

    frames = store.load_many(codes, rows=120)
    frames = {c: f for c, f in frames.items() if len(f) >= args.min_overlap}
    closes = pd.DataFrame({c: f.set_index("date")["close"]
                           for c, f in frames.items()}).sort_index()
    print(f"参与筛选: {len(closes)} 只（{closes.index[0]}~{closes.index[-1]}）")

    corr = closes.corr(min_periods=args.min_overlap)  # 相关性矩阵：逐对可用数据
    logc = np.log(closes)

    pairs, tested = [], 0
    for a, b in itertools.combinations(closes.columns, 2):
        if not np.isfinite(corr.loc[a, b]) or corr.loc[a, b] < 0.5:
            continue   # 相关性初筛：数据不足或相关性低直接跳过（省检验次数）
        # 逐对取交集（各股票窗口起点不同，联合索引会有NaN——统计上必须逐对对齐）
        joined = logc[[a, b]].replace([np.inf, -np.inf], np.nan).dropna()
        if len(joined) < args.min_overlap or (joined <= 0).any().any():
            continue
        tested += 1
        # 对冲系数 = log(b) 对 log(a) 的OLS斜率（coint()不返回系数，需自行回归）
        hedge = float(np.polyfit(joined[b], joined[a], 1)[0])
        spread = joined[a] - hedge * joined[b]
        # EG检验（协整）+ 价差ADF（平稳性）双重确认，阈值用 --strict-p（多重检验校正）
        eg_p = coint(joined[a], joined[b])[1]
        if eg_p >= args.strict_p:
            continue
        adf_p = adfuller(spread, regression="c")[1]
        if adf_p >= args.strict_p:
            continue
        pairs.append({
            "pair_id": f"{a}-{b}",
            "a": a, "b": b, "a_name": names[a], "b_name": names[b],
            "hedge_ratio": round(float(hedge), 4),
            "eg_pvalue": round(float(eg_p), 4),
            "adf_pvalue": round(float(adf_p), 4),
            "corr": round(float(corr.loc[a, b]), 3),
            "window_days": int(len(joined)),
        })
        print(f"  [配对] {names[a]}({a}) x {names[b]}({b}) "
              f"EG p={eg_p:.4f} ADF p={adf_p:.4f} hedge={hedge:.4f}")

    OUT.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window_days": int(closes.shape[0]), "pvalue": args.pvalue,
        "n_tested": tested, "n_stocks": len(closes),
        "pairs": pairs,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n检验 {tested} 个组合（相关性>=0.5），命中 {len(pairs)} 对 → {OUT}")
    print("提醒：协整关系会随时间失效，建议每月重跑本脚本刷新配对。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
