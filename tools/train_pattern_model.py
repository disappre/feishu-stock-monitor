#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""阶段一：离线训练形态聚类模型。

对筛选池（data/screen_pool.txt）每只股票取近 window 根K线，
用 stock-api-master 的 ImprovedKMeans（K-means++/21维金融特征）聚类，
并计算每簇的后验行为（簇内成员未来 fwd_days 日平均涨跌）→
产出 data/pattern_model.json 供 engine/rules_cluster.py 在线匹配。

用法（仓库根目录）：
    python tools/train_pattern_model.py [--limit 100] [--window 30] [--fwd-days 5] [--k 5]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from stock_monitor.data.feed import fetch_kline  # noqa: E402

REPO_PATH = __import__("os").environ.get(
    "CLUSTER_REPO_PATH",
    str(Path.home() / "Desktop" / "stock-api-master" / "stock-api-master"))
sys.path.insert(0, REPO_PATH)
from ml_models.improved_kmeans import ImprovedKMeans  # noqa: E402

MODEL_OUT = REPO / "data" / "pattern_model.json"


def load_pool(limit: int | None) -> list[tuple[str, str]]:
    pool = []
    for raw in (REPO / "data" / "screen_pool.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if parts[0].isdigit() and len(parts[0]) == 6:
            pool.append((parts[0], parts[1] if len(parts) > 1 else parts[0]))
    return pool[:limit] if limit else pool


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="只用池中前N只（调试）")
    ap.add_argument("--window", type=int, default=30, help="特征窗口（交易日）")
    ap.add_argument("--fwd-days", type=int, default=5, help="后验行为统计的未来天数")
    ap.add_argument("--k", type=int, default=5, help="簇数")
    ap.add_argument("--seed", type=int, default=11, help="随机种子（复现聚类系统默认11）")
    args = ap.parse_args()

    pool = load_pool(args.limit)
    print(f"训练样本: 池中 {len(pool)} 只，窗口 {args.window}，后验 {args.fwd_days} 日，K={args.k}")

    series_list, meta_rows = [], []
    for i, (code, name) in enumerate(pool):
        if i:
            time.sleep(0.25)
        try:
            kline = fetch_kline(code)
        except Exception:
            kline = None
        if kline is None or len(kline) < args.window + args.fwd_days + 1:
            continue
        close = kline["close"].astype(float)
        seg = close.iloc[-(args.window + args.fwd_days):]
        past, future = seg.iloc[:args.window], seg.iloc[args.window:]
        rets = past.pct_change().dropna().to_numpy()
        if len(rets) < args.window - 2 or np.std(rets) < 1e-10:
            continue
        fwd = (future.iloc[-1] / future.iloc[0] - 1) * 100  # 未来fwd日涨跌%
        series_list.append(rets)
        meta_rows.append({"code": code, "name": name, "fwd": float(fwd)})
        if (i + 1) % 25 == 0:
            print(f"  已取 {len(series_list)} 只有效样本...")

    if len(series_list) < args.k * 3:
        print(f"有效样本不足（{len(series_list)} < {args.k * 3}），先扩大股票池或减小K")
        return 1

    # ImprovedKMeans：use_features=True 时内部做21维特征+标准化。
    # 为让在线匹配能复现同一变换，这里手动走特征+scaler后再fit(use_features=False)。
    from ml_models.improved_kmeans import FinancialFeatureEngineer
    from sklearn.preprocessing import StandardScaler

    fe = FinancialFeatureEngineer()
    feats = np.array([fe.extract_features(s) for s in series_list])
    scaler = StandardScaler().fit(feats)
    feats_scaled = scaler.transform(feats)

    model = ImprovedKMeans(n_clusters=args.k, use_dtw=False, use_features=False,
                           random_state=args.seed)
    model.fit(feats_scaled)
    labels = model.labels_
    print(f"聚类完成: {args.k} 簇, inertia={model.inertia_:.1f}")

    # 每簇后验行为：成员未来N日平均涨跌 -> 簇的方向性标注
    behaviors = {}
    for c in range(args.k):
        members = [m for m, lb in zip(meta_rows, labels) if lb == c]
        if not members:
            continue
        mean_fwd = float(np.mean([m["fwd"] for m in members]))
        behaviors[str(c)] = {
            "label": ("上涨动能簇" if mean_fwd > 0.5 else
                      "下跌动能簇" if mean_fwd < -0.5 else
                      f"中性簇{c}"),
            "n_members": len(members),
            "mean_fwd": round(mean_fwd, 3),
            "win_rate": round(sum(1 for m in members if m["fwd"] > 0) / len(members), 3),
            "sample_codes": [m["code"] for m in members[:5]],
        }
        print(f"  簇{c}: {behaviors[str(c)]['label']} n={len(members)} "
              f"后验均值{mean_fwd:+.2f}% 胜率{behaviors[str(c)]['win_rate']:.0%}")

    MODEL_OUT.write_text(json.dumps({
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "repo_path": REPO_PATH,
        "window": args.window, "fwd_days": args.fwd_days,
        "n_clusters": args.k, "n_samples": len(series_list),
        "seed": args.seed,
        "centroids": model.centroids.tolist(),
        "scaler_mean": scaler.mean_.tolist(),
        "scaler_scale": scaler.scale_.tolist(),
        "cluster_behaviors": behaviors,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"模型已保存: {MODEL_OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
