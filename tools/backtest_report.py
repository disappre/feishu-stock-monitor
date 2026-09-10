#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基线回测报告：用当前 watchlist.yaml 配置全池回放本地规则，
输出各信号类型的历史胜率（data/backtest_report.json）。

用法（仓库根目录）：
    python tools/backtest_report.py [--fwd-days 5] [--pool-limit N] [--rules a,b]
默认规则（本地零外部依赖）：ma_cross,price_surge,volume_surge,rsi,candle_div,fractal_break
pattern_cluster 前视偏差不回测；pattern 可通过 --rules pattern 手动开启（较慢）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yaml  # noqa: E402

from stock_monitor.backtest import backtest_rules  # noqa: E402
from stock_monitor.data import store  # noqa: E402
from stock_monitor.engine.base import RuleEngine  # noqa: E402

DEFAULT_RULES = ["ma_cross", "price_surge", "volume_surge",
                 "rsi", "candle_div", "fractal_break"]
OUT = REPO / "data" / "backtest_report.json"


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
    ap.add_argument("--fwd-days", type=int, default=None,
                    help="前瞻天数（默认读 watchlist.yaml review.forward_days）")
    ap.add_argument("--train-ratio", type=float, default=0.7)
    ap.add_argument("--pool-limit", type=int, default=None)
    ap.add_argument("--rules", type=str, default=",".join(DEFAULT_RULES),
                    help="逗号分隔规则名")
    args = ap.parse_args()

    config = yaml.safe_load((REPO / "config" / "watchlist.yaml").read_text(encoding="utf-8"))
    fwd_days = args.fwd_days or config.get("review", {}).get("forward_days", 5)

    names = load_names()
    codes = list(names)
    if args.pool_limit:
        codes = codes[: args.pool_limit]
    frames = store.load_many(codes)
    frames = {c: f for c, f in frames.items() if len(f) >= 30}
    if not frames:
        print("本地库无可用数据，请先运行: python -m stock_monitor.main --sync-pool")
        return 1
    print(f"回测样本: {len(frames)} 只 × ~{len(next(iter(frames.values())))} 根，"
          f"前瞻 {fwd_days} 日，规则: {args.rules}")

    engine = RuleEngine(config)
    wanted = [r.strip() for r in args.rules.split(",") if r.strip()]
    rules = {}
    for r in engine.rules:
        if getattr(r, "name", None) in wanted:
            rules[r.name] = r
    missing = [w for w in wanted if w not in rules]
    if missing:
        print(f"警告: 规则未装配或已禁用，跳过: {missing}")

    report = backtest_rules(rules, frames, names, fwd_days, args.train_ratio)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "fwd_days": fwd_days, "train_ratio": args.train_ratio,
        "n_stocks": len(frames), "config_source": "watchlist.yaml(当前生效配置)",
        "results": report,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n{'规则':<14}{'信号类型':<26}{'全样本n':>7}{'胜率':>7}{'均值%':>8}"
          f"{'训练n':>7}{'训练胜率':>8}{'测试n':>7}{'测试胜率':>8}")
    for rule_name, r in report.items():
        for section in ("full", "train", "test"):
            for stype, s in r[section].items():
                row = [f"{s['n']}", f"{s['win_rate']:.1%}", f"{s['mean_fwd_pct']:+.2f}"]
                if section == "full":
                    print(f"{rule_name:<14}{stype:<26}{row[0]:>7}{row[1]:>7}{row[2]:>8}",
                          end="")
                else:
                    tag = "train" if section == "train" else "test"
                    print(f"{row[0]:>8}({tag}){row[1]:>9}", end=" " if section == "train" else "\n")
        if not r["full"]:
            print(f"{rule_name:<14}(无信号)")
    print(f"\n报告已写入: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
