#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回放式四类背驰回测：解决"只扫最近8笔导致信号集中在近期"的验证缺陷。

原理：在每个历史时点 t，用截至 t 的K线构建CZSC，取**当时的**最近lookback笔
做背驰判定——信号在历史上逐点产生，训练/测试切分才有意义。
性能：逐点重建CZSC太慢（O(n²)），故用增量近似——CZSC一次构建后，
笔列表本身是历史序列，第i笔形成时的"最近8笔"= bi_list[i-8:i]，
直接在完整笔列表上滑动窗口即可，无需重建（笔的划分用全量数据，
但笔形成于其终点分型确认时，历史不重写=缠论第69课，故窗口切片有效）。

用法：
    python tools/backtest_divergence.py [--pool-file 沪深] [--pool-limit N]
"""
from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import pandas as pd  # noqa: E402

from stock_monitor.backtest import BacktestRecord, split_by_date  # noqa: E402
from chan_analysis import analyze, macd  # noqa: E402
from czsc import Direction  # noqa: E402

OUT = REPO / "data" / "divergence_backtest.json"
FULL_LIST = Path(r"C:\Users\Administrator\Desktop\stock-api-master"
                 r"\stock-api-master\data\沪深股票列表.json")
DIV_BUY = {"盘整下", "趋势下"}


def load_pool(pool_file: str | None, limit: int | None) -> list[tuple[str, str]]:
    if pool_file == "full" or pool_file is None and FULL_LIST.exists():
        data = json.loads(FULL_LIST.read_text(encoding="utf-8"))
        pool = [(x["dm"].split(".")[0], x["mc"]) for x in data]
    else:
        pool = []
        for line in (REPO / "data" / "screen_pool.txt").read_text(encoding="utf-8").splitlines():
            parts = [p.strip() for p in line.split("#", 1)[0].split(",")]
            if len(parts) >= 2 and parts[0].isdigit() and len(parts[0]) == 6:
                pool.append((parts[0], parts[1]))
    return pool[:limit] if limit else pool


def replay_divergences(code: str, name: str, lookback: int = 8,
                       area_ratio: float = 0.8) -> list[tuple]:
    """单只股票的回放式背驰：在完整笔序列上滑动窗口，
    第i笔处的"最近lookback笔"= bi_list[max(0, i-lookback):i+1]。
    返回 [(signal_date, kind, direction, fwd_ret), ...]
    """
    cs = analyze(code, name)
    if cs is None or len(cs.c.bi_list) < 5:
        return []
    bi_list = cs.c.bi_list
    zs_list = cs.c.zs_list
    df = cs.df.reset_index(drop=True)
    dates = df["date"].astype(str).str.slice(0, 10).tolist()
    close = df["close"].to_numpy(float)
    open_ = df["open"].to_numpy(float)
    dmap = {d: i for i, d in enumerate(dates)}
    _, _, hist = macd(df["close"].astype(float))
    hist.index = df["date"].astype(str).str.slice(0, 10).tolist()

    from chan_analysis import macd_area

    def area(bi, up: bool):
        """源码口径红绿分离: 向上段比红面积, 向下段比绿面积"""
        red, green = macd_area(hist, str(bi.fx_a.dt)[:10], str(bi.fx_b.dt)[:10])
        return red if up else green

    out = []
    # 滑过笔序列: 窗口终点b_idx从lookback-1到末尾，每步判定"当下"背驰
    for b_idx in range(2, len(bi_list)):
        window = bi_list[max(0, b_idx - lookback + 1): b_idx + 1]
        # 找窗口内隔一笔的同向对: a=window[j], b=window[j+2], b必须是window末笔
        # （只判"当下刚形成"的背驰——b=bi_list[b_idx]，避免同一背驰重复计数）
        b = bi_list[b_idx]
        j = b_idx - 2
        if j < 0:
            continue
        a = bi_list[j]
        if a.direction != b.direction:
            continue
        up = a.direction == Direction.Up
        # 中枢锚定（防未来但不苛求）：中枢**起点**在信号时可知（三次重叠完成即成立），
        # 终点未知——只要求中枢已开始(zs_s<=信号日)且与[a终点,b起点]区间有交集
        n_zs = 0
        a_d, b_d = str(a.fx_b.dt)[:10], str(b.fx_a.dt)[:10]
        b_end = str(b.fx_b.dt)[:10]
        for z in zs_list:
            zs_s, zs_e = str(z.sdt)[:10], str(z.edt)[:10]
            if zs_s > b_end:
                continue           # 中枢尚未开始=未来信息
            if (a_d <= zs_s <= b_d) or (a_d <= zs_e) or (zs_s <= a_d):
                n_zs += 1
        if n_zs == 0:
            continue
        new_extreme = b.fx_b.fx > a.fx_b.fx if up else b.fx_b.fx < a.fx_b.fx
        if not new_extreme:
            continue
        aa, ab = area(a, up), area(b, up)
        if aa <= 0 or ab >= aa * area_ratio:
            continue
        kind = ("趋势上" if n_zs >= 2 else "盘整上") if up \
            else ("趋势下" if n_zs >= 2 else "盘整下")
        # 前瞻收益: 信号=b笔终点日, 次日开盘入场
        d = b_end
        i = dmap.get(d)
        if i is None or i + 6 >= len(df) or open_[i + 1] <= 0:
            continue
        ret = (close[i + 5] / open_[i + 1] - 1) * 100
        out.append((d, kind, "bullish" if kind in DIV_BUY else "bearish", ret))
    return out


def _run_task(task):
    code, name = task
    try:
        from stock_monitor.data import store
        if store.load_recent(code) is None or len(store.load_recent(code)) < 60:
            return []
        return [(code, name, *r) for r in replay_divergences(code, name)]
    except Exception:
        return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", default="csi300", help="csi300或full")
    ap.add_argument("--pool-limit", type=int, default=None)
    ap.add_argument("--train-ratio", type=float, default=0.7)
    ap.add_argument("--workers", type=int, default=8)
    args = ap.parse_args()

    pool = load_pool("full" if args.pool == "full" else None, args.pool_limit)
    print(f"回放式背驰回测: {len(pool)}只, workers={args.workers}", flush=True)

    records = []
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for result in ex.map(_run_task, pool, chunksize=8):
            records.extend(result)
            done += 1
            if done % 200 == 0:
                print(f"  进度 {done}/{len(pool)}, 信号{len(records)}", flush=True)

    print(f"\n总信号: {len(records)}")
    recs = [BacktestRecord(code=c, date=d, signal_type=f"chan_{k}",
                           direction=dr, fwd_ret=r)
            for c, n, d, k, dr, r in records]
    train, test, split_date = split_by_date(recs, args.train_ratio)
    print(f"切分: {split_date}, train={len(train)}, test={len(test)}")

    def summarize(recs):
        groups = {}
        for r in recs:
            groups.setdefault(r.signal_type, []).append(r)
        return {k: {"n": len(v),
                    "win_rate": round(sum(1 for r in v if r.is_win) / len(v), 3),
                    "mean_pct": round(sum(r.fwd_ret for r in v) / len(v), 3)}
                for k, v in sorted(groups.items())}

    result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "n_pool": len(pool), "n_signals": len(recs), "split_date": split_date,
        "full": summarize(recs), "train": summarize(train), "test": summarize(test),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{'信号':<10}{'全n':>6}{'全胜率':>8}{'训n':>6}{'训胜率':>8}"
          f"{'测n':>6}{'测胜率':>8}{'测均值':>9}")
    for kind in result["full"]:
        f, tr, te = (result["full"][kind], result["train"].get(kind),
                     result["test"].get(kind))
        print(f"{kind.replace('chan_', ''):<10}{f['n']:>6}{f['win_rate']:>8.1%}"
              f"{(tr or {}).get('n', 0):>6}{(tr or {}).get('win_rate', 0):>8.1%}"
              f"{(te or {}).get('n', 0):>6}{(te or {}).get('win_rate', 0):>8.1%}"
              f"{(te or {}).get('mean_pct', 0):>+9.2f}")
    print(f"\n报告: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
