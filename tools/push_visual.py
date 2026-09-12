#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""图文详报推送：重点标的 + K线缠论结构图（图文穿插卡片）。

选取规则（可组合，默认活跃标的）：
- 五步法持仓状态 + 八阶段看涨（教学规则视角）
- 或 --chan-3rd: 当前三买信号标的（回测验证视角）
每只标的: 文字摘要行 + 全要素K线图（含缠论结构+主力色带+买卖点）。

用法：
    python tools/push_visual.py [--n 3] [--chan-3rd]
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
logger = logging.getLogger("visual")

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

import requests  # noqa: E402
import time  # noqa: E402

from stock_monitor.data import store  # noqa: E402
from stock_monitor.engine.mainforce import classify_stage  # noqa: E402
from stock_monitor.engine.rules_mainwave import analyze_mainwave  # noqa: E402
from stock_monitor.notify import feishu_bot  # noqa: E402
from stock_monitor.notify.feishu_bot import _secret, _sign, _webhook  # noqa: E402
from chan_analysis import analyze as chan_analyze, plot_structure  # noqa: E402


def load_names() -> dict:
    names = {}
    for line in (REPO / "data" / "screen_pool.txt").read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in line.split("#", 1)[0].split(",")]
        if len(parts) >= 2 and parts[0].isdigit() and len(parts[0]) == 6:
            names[parts[0]] = parts[1]
    return names


def pick_stocks(mode: str, limit: int) -> list[tuple[str, str, str]]:
    """选标的，返回[(code, name, 理由)]。"""
    names = load_names()
    picks = []
    for code, nm in names.items():
        kline = store.load_recent(code)
        if kline is None or len(kline) < 60:
            continue
        if mode == "chan_3rd":
            reason = "缠论三买（A级·alpha+40.5%）"
            picks.append((code, nm, reason))
        else:  # active: 五步持仓+八阶段看涨
            st = analyze_mainwave(kline)
            mf = classify_stage(kline)
            if st.stage.startswith("持仓") and mf and mf.bias == 1:
                picks.append((code, nm, f"{st.stage[3:]}｜主力{mf.action}"))
        if len(picks) >= limit:
            break
    return picks


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3, help="推送标的数（默认3，图多卡片重）")
    ap.add_argument("--chan-3rd", action="store_true", help="选三买信号标的而非教学规则组合")
    args = ap.parse_args()

    mode = "chan_3rd" if args.chan_3rd else "active"
    picks = pick_stocks(mode, args.n)
    if not picks:
        logger.info("无符合条件的标的")
        return 0

    title = ("三买信号标的 · 图文详报" if mode == "chan_3rd"
             else "重点标的 · 图文详报（K线缠论+主力色带）")
    elements = [{"tag": "markdown", "content": f"**📈 {title}**"}]
    n_img = 0
    for code, nm, reason in picks:
        try:
            cs = chan_analyze(code, nm)
            png = plot_structure(cs)
            image_key = feishu_bot.upload_image(png)
        except Exception:
            logger.exception("图生成失败 %s", code)
            image_key = None
        zs = cs.last_zs if cs else None
        last_px = float(cs.c.bars_raw[-1].close) if cs else 0
        pos = ("中枢上方" if zs and last_px > zs.gg else
               "中枢下方" if zs and last_px < zs.dd else "中枢内部")
        seg = cs.segments[-1] if cs and cs.segments else None
        elements.append({"tag": "markdown", "content":
            f"**{nm}** `{code}`｜{reason}｜缠论:{pos}"
            + (f"·{'向上段' if seg[4] == 1 else '向下段'}" if seg else "")})
        if image_key:
            elements.append({"tag": "img", "img_key": image_key,
                             "alt": {"tag": "plain_text", "content": f"{nm}K线"}})
            n_img += 1
    elements.append({"tag": "note", "elements": [
        {"tag": "plain_text",
         "content": "五步法/八阶段为教学规则未回测,组合无统计增量(已验证);"
                    "仅供学习研究，不构成投资建议"}]})

    payload = {"msg_type": "interactive", "card": {
        "header": {"title": {"tag": "plain_text", "content": title},
                   "template": "blue"},
        "elements": elements}}
    if _secret():
        ts = int(time.time())
        payload["timestamp"] = str(ts)
        payload["sign"] = _sign(_secret(), ts)
    resp = requests.post(_webhook(), json=payload, timeout=15).json()
    ok = resp.get("code") == 0
    logger.info("推送: %s (%d只标的, %d张图)", "成功" if ok else resp, len(picks), n_img)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
