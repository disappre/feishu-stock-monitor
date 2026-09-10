#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""规则阈值寻优：按规则独立网格搜索，训练期选参、测试期样本外验证。

方法论对齐 knowledge/strategies/metaheuristic-optimization-a3.md：
- 目标函数 = 训练期胜率（信号方向感知），要求 n_train >= min_samples；
- 平局取训练样本更多者（更稳健）；
- 选出的参数必须在测试期复检——样本外胜率大幅回撤即过拟合警告；
- 结果不自动写回 watchlist.yaml，由人决策（A3卡红线）。

性能设计：逐根回放是 O(n²)（每根重算rolling），串行全网格约40分钟；
故按 (参数组合 × 股票) 拆任务多进程并行（每个worker自带SQLite连接与K线缓存），
8进程约6分钟，回放语义与在线扫描完全一致。

用法（仓库根目录）：
    python tools/tune_rule_thresholds.py [--fwd-days 5] [--train-ratio 0.7]
        [--min-samples 20] [--pool-limit N] [--rules a,b] [--workers 8]
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from stock_monitor.backtest import BacktestRecord, split_by_date  # noqa: E402
from stock_monitor.data import store  # noqa: E402
from stock_monitor.engine.rules_candlestick import CandlestickDivergenceRule  # noqa: E402
from stock_monitor.engine.rules_fractal import FractalBreakRule  # noqa: E402
from stock_monitor.engine.rules_ma import MACrossRule  # noqa: E402
from stock_monitor.engine.rules_price import (PriceSurgeRule, RSIRule,  # noqa: E402
                                              VolumeSurgeRule)

OUT = REPO / "data" / "threshold_tuning.json"

BUILDERS = {
    "ma_cross": MACrossRule, "price_surge": PriceSurgeRule,
    "volume_surge": VolumeSurgeRule, "rsi": RSIRule,
    "candle_div": CandlestickDivergenceRule, "fractal_break": FractalBreakRule,
}

# 每规则参数网格：键=规则名，值=参数名→候选值列表（笛卡尔积）
GRIDS: dict[str, dict[str, list]] = {
    "ma_cross": {"short": [5, 10, 20], "long": [20, 30, 60]},
    "price_surge": {"pct_threshold": [3.0, 4.0, 5.0, 6.0, 8.0]},
    "volume_surge": {"ratio_threshold": [1.5, 2.0, 2.5, 3.0],
                     "baseline_days": [5, 10]},
    "rsi": {"overbought": [70, 75, 80], "oversold": [20, 25, 30]},
    "candle_div": {"overbought": [65, 70, 75], "oversold": [25, 30, 35]},
    "fractal_break": {"ema_fast": [10, 14], "ema_slow": [60, 100, 200]},
}

# worker进程内的K线缓存：每进程对每只股票只读一次库
_FRAMES: dict[str, object] = {}


def _valid(rule: str, params: dict) -> bool:
    if rule == "ma_cross":
        return params["short"] < params["long"]
    if rule == "fractal_break":
        return params["ema_fast"] < params["ema_slow"]
    if rule in ("rsi", "candle_div"):
        return params["overbought"] > params["oversold"]
    return True


def _run_task(task: tuple) -> list[tuple]:
    """worker：回放 (规则, 参数组合, 股票)，返回信号记录元组列表。"""
    rule_name, combo, base_cfg, code, name, fwd_days = task
    from stock_monitor.backtest import replay_rule
    if code not in _FRAMES:
        frame = store.load_recent(code)
        _FRAMES[code] = frame if frame is not None and len(frame) >= 30 else False
    kline = _FRAMES[code]
    if kline is False:
        return []
    params = {**base_cfg, **combo, "enabled": True}
    rule = BUILDERS[rule_name](params)
    records = replay_rule(rule, code, name, kline, fwd_days)
    return [(r.code, r.date, r.signal_type, r.direction, r.fwd_ret) for r in records]


def _overall(records: list[BacktestRecord]) -> dict:
    if not records:
        return {"win_rate": None, "mean_fwd_pct": None}
    rets = [r.fwd_ret for r in records]
    wins = sum(1 for r in records if r.is_win)
    return {"win_rate": round(wins / len(records), 3),
            "mean_fwd_pct": round(sum(rets) / len(rets), 3)}


def load_names() -> dict[str, str]:
    names = {}
    pool = REPO / "data" / "screen_pool.txt"
    if pool.exists():
        for line in pool.read_text(encoding="utf-8").splitlines():
            parts = [p.strip() for p in line.split("#", 1)[0].split(",")]
            if len(parts) >= 2 and parts[0].isdigit() and len(parts[0]) == 6:
                names[parts[0]] = parts[1]
    return names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fwd-days", type=int, default=None)
    ap.add_argument("--train-ratio", type=float, default=0.7)
    ap.add_argument("--min-samples", type=int, default=20)
    ap.add_argument("--pool-limit", type=int, default=None)
    ap.add_argument("--rules", type=str, default=",".join(GRIDS))
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 4))
    args = ap.parse_args()

    config = yaml.safe_load((REPO / "config" / "watchlist.yaml").read_text(encoding="utf-8"))
    fwd_days = args.fwd_days or config.get("review", {}).get("forward_days", 5)
    rule_cfg = config.get("rules", {})

    names = load_names()
    codes = list(names)
    if args.pool_limit:
        codes = codes[: args.pool_limit]
    print(f"寻优样本: {len(codes)} 只，前瞻{fwd_days}日，"
          f"训练/测试={args.train_ratio:.0%}/{1 - args.train_ratio:.0%}，"
          f"最少训练样本={args.min_samples}，workers={args.workers}", flush=True)

    wanted = [r.strip() for r in args.rules.split(",") if r.strip()]
    # 组装全部任务: (规则, 组合, 股票)
    tasks = []
    combo_index: dict[tuple, dict] = {}   # (rule, combo_tuple) -> combo dict
    for rule_name in wanted:
        if rule_name not in GRIDS or rule_name not in BUILDERS:
            print(f"跳过 {rule_name}: 无网格或builder", flush=True)
            continue
        base_cfg = dict(rule_cfg.get(rule_name, {}))
        keys = list(GRIDS[rule_name])
        for values in itertools.product(*GRIDS[rule_name].values()):
            combo = dict(zip(keys, values))
            if not _valid(rule_name, combo):
                continue
            combo_index[(rule_name, tuple(sorted(combo.items())))] = combo
            for code in codes:
                tasks.append((rule_name, combo, base_cfg, code,
                              names.get(code, code), fwd_days))
    if not tasks:
        print("无有效任务")
        return 1
    print(f"任务总数: {len(tasks)}（{len(combo_index)}个参数组合 × {len(codes)}只）",
          flush=True)

    # 并行执行并按组合聚合（map保序，与任务zip得到归属）
    per_combo: dict[tuple, list[BacktestRecord]] = {}
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for task, result in zip(tasks, pool.map(_run_task, tasks, chunksize=32)):
            rule_name, combo = task[0], task[1]
            key = (rule_name, tuple(sorted(combo.items())))
            bucket = per_combo.setdefault(key, [])
            bucket.extend(BacktestRecord(*r) for r in result)
            done += 1
            if done % 2000 == 0:
                print(f"  进度 {done}/{len(tasks)}", flush=True)

    results = {}
    for (rule_name, combo_key), records in per_combo.items():
        combo = combo_index[(rule_name, combo_key)]
        train, test, _ = split_by_date(records, args.train_ratio)
        results.setdefault(rule_name, []).append({
            "params": combo, "n_train": len(train), "n_test": len(test),
            "train": _overall(train), "test": _overall(test),
        })

    # 每规则选最优并做样本外复检
    final = {}
    for rule_name, candidates in results.items():
        eligible = [c for c in candidates if c["n_train"] >= args.min_samples
                    and c["train"]["win_rate"] is not None]
        pool_ok = eligible or [c for c in candidates if c["train"]["win_rate"] is not None]
        best = max(pool_ok, key=lambda c: (c["train"]["win_rate"], c["n_train"])) \
            if pool_ok else None
        entry = {"min_samples": args.min_samples,
                 "status": "ok" if eligible else "insufficient_samples"}
        if best:
            entry.update({
                "best_params": best["params"], "train": best["train"],
                "test": best["test"], "n_train": best["n_train"],
                "n_test": best["n_test"], "n_combos": len(candidates),
            })
            if best["test"]["win_rate"] is not None:
                drop = best["train"]["win_rate"] - best["test"]["win_rate"]
                entry["overfit_drop"] = round(drop, 3) if drop > 0.15 else None
        final[rule_name] = entry
        b = entry.get("best_params")
        tr, te = entry.get("train"), entry.get("test")
        print(f"{rule_name}: 最优{b} 训练胜率="
              f"{tr['win_rate'] if tr else 'NA'}(n={entry.get('n_train')}) "
              f"测试胜率={te['win_rate'] if te else 'NA'}(n={entry.get('n_test')})"
              + (f" ⚠️过拟合回撤{entry['overfit_drop']:.0%}"
                 if entry.get("overfit_drop") else ""), flush=True)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "fwd_days": fwd_days, "train_ratio": args.train_ratio,
        "min_samples": args.min_samples, "n_stocks": len(codes),
        "grids": {k: v for k, v in GRIDS.items() if k in final},
        "results": final,
        "note": "结果未写回watchlist.yaml；overfit_drop>15%的参数不建议采用",
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n寻优结果已写入: {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
