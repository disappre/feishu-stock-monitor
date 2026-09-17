#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""盘中扫描：午盘(11:35)与尾盘(14:50)定时运行，实时数据+精简卡片推飞书。

与收盘链路的区别：
- 用**实时行情**（fetch_kline 含当日进行中bar），不做本地库依赖
- 输出**盘面速览**：防狼术温度计 + 持仓状态 + A级信号 + 异动榜
- 限速礼貌（每只间隔0.2s），自选股规模下约30-60秒完成

用法：
    python tools/intraday_scan.py --session noon    # 午盘
    python tools/intraday_scan.py --session close   # 尾盘
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import logging  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("intraday")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

import yaml  # noqa: E402

from stock_monitor.data.feed import fetch_kline  # noqa: E402
from stock_monitor.engine.mainforce import classify_stage  # noqa: E402
from stock_monitor.engine.rules_mainwave import analyze_mainwave  # noqa: E402
from stock_monitor.engine.rules_wolf import dif_dea_below_zero  # noqa: E402
from stock_monitor.notify import feishu_bot  # noqa: E402
from stock_monitor.watchlist import load_watchlist  # noqa: E402

SESSION_LABEL = {"noon": "午盘速览", "close": "尾盘速览"}


def scan_picks(sleep_s: float = 0.2) -> dict:
    """实时扫描自选股，返回汇总数据。"""
    config = yaml.safe_load((REPO / "config" / "watchlist.yaml").read_text(encoding="utf-8"))
    picks = load_watchlist(config)
    rows, danger, errors = [], 0, 0
    for i, (code, name) in enumerate(picks.items()):
        if i:
            time.sleep(sleep_s)
        try:
            k = fetch_kline(code)
        except Exception:
            k = None
        if k is None or len(k) < 40:
            errors += 1
            continue
        close = k["close"].astype(float)
        last = float(close.iloc[-1])
        pct = (last / float(close.iloc[-2]) - 1) * 100 if len(close) >= 2 else 0.0
        is_danger = dif_dea_below_zero(close)
        if is_danger:
            danger += 1
        mf = classify_stage(k)
        mw = analyze_mainwave(k)
        rows.append({
            "code": code, "name": name, "price": last, "pct": pct,
            "danger": is_danger,
            "mf_action": mf.action if mf else "",
            "mf_bias": mf.bias if mf else 0,
            "mw_stage": mw.stage,
        })
    return {"rows": rows, "danger": danger, "errors": errors, "total": len(picks)}


def build_card(data: dict, session: str) -> tuple[str, list]:
    rows = data["rows"]
    total = len(rows)
    if total == 0:
        return "扫描失败", ["无有效数据"]
    up = sum(1 for r in rows if r["pct"] > 0)
    avg = sum(r["pct"] for r in rows) / total
    label = SESSION_LABEL.get(session, "盘中速览")
    now = datetime.now().strftime("%H:%M")

    lines = [f"**{label} · {now}**（自选{total}只，实时行情）", ""]
    lines.append(f"**盘面**: 涨{up}跌{total - up}｜均涨幅 **{avg:+.2f}%**"
                 f"｜防狼术危险区 **{data['danger']}只**（{data['danger'] * 100 // total}%）")
    lines.append("")

    # 异动榜（涨跌幅绝对值Top5）
    movers = sorted(rows, key=lambda r: abs(r["pct"]), reverse=True)[:5]
    lines.append("**异动榜**:")
    for r in movers:
        flag = "⚠️危险区" if r["danger"] else ""
        lines.append(f"- {r['name']} `{r['code']}` {r['price']:.2f} "
                     f"**{r['pct']:+.2f}%** {flag}")
    lines.append("")

    # 持仓状态股（五步法）
    holding = [r for r in rows if r["mw_stage"].startswith("持仓")]
    if holding:
        lines.append(f"**五步法持仓中（{len(holding)}只）**:")
        for r in holding[:6]:
            lines.append(f"- {r['name']} `{r['code']}`：{r['mw_stage'][3:]}"
                         f"｜{r['mf_action']}")
    else:
        lines.append("**五步法无持仓状态标的**")

    # 主力吸筹/锁仓（看涨性质）
    bullish_mf = [r for r in rows if r["mf_bias"] == 1 and not r["danger"]][:5]
    if bullish_mf:
        lines.append("")
        lines.append("**主力看涨且非危险区**:")
        for r in bullish_mf:
            lines.append(f"- {r['name']} `{r['code']}`：{r['mf_action']} "
                         f"{r['pct']:+.2f}%")
    lines.append("")
    lines.append("数据为盘中实时，非收盘定案；仅供学习研究，不构成投资建议")
    return label, lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", default="noon", choices=["noon", "close"])
    ap.add_argument("--push", action="store_true", default=True)
    ap.add_argument("--no-push", dest="push", action="store_false")
    args = ap.parse_args()

    t0 = time.time()
    data = scan_picks()
    title, lines = build_card(data, args.session)
    logger.warning("扫描完成: %d只, 危险区%d, 失败%d, 耗时%.0fs",
                   len(data["rows"]), data["danger"], data["errors"], time.time() - t0)
    if args.push:
        feishu_bot.send_card(f"自选股 · {title}", lines, color="blue")
        logger.warning("已推送飞书")
    else:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
