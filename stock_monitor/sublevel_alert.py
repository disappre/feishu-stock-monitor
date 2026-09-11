# -*- coding: utf-8 -*-
"""次级别(30分钟)信号推送：日线中枢内的股票，次级别结构变化自动推飞书。

使用场景（缠师第35课操作立体性）：日线级别看不清方向（中枢震荡）时，
次级别的三买/三卖给出反弹/回调的结构确认：
- 次级别三卖 = "反弹衰竭"（日线下跌延续，本波属反弹）→ 回避提示
- 次级别三买 = "回调企稳"（日线反弹结构完好）→ 观察提示

触发条件（避免刷屏）：仅对【自选股 my_picks.txt】做次级别扫描——
30分钟信号频率高，全池扫描会产生过多推送；自选股是用户真正关心的标的。
"""
from __future__ import annotations

import logging
from pathlib import Path

from .engine.base import Signal
from .engine.sublevel import analyze_sublevel

logger = logging.getLogger(__name__)

BASE = Path(__file__).resolve().parents[1]


def load_picks() -> list[tuple[str, str]]:
    picks = []
    pf = BASE / "data" / "my_picks.txt"
    if pf.exists():
        for line in pf.read_text(encoding="utf-8").splitlines():
            parts = [p.strip() for p in line.split("#", 1)[0].split(",")]
            if parts and parts[0].isdigit() and len(parts[0]) == 6:
                picks.append((parts[0], parts[1] if len(parts) > 1 else parts[0]))
    return picks


def sublevel_signal(code: str, name: str) -> Signal | None:
    """次级别结构 → 信号（仅反弹衰竭/回调企稳两种，中性不推）。"""
    sub = analyze_sublevel(code)
    if sub is None or sub.verdict == "中性":
        return None
    return Signal(
        code=code, name=name,
        signal_type=f"sublevel_{sub.verdict}",
        direction=sub.direction,
        title=f"{name} · 次级别{sub.verdict}",
        detail=sub.detail,
        metrics={"n_bi": sub.n_bi, "n_zs": sub.n_zs,
                 "verdict": sub.verdict},
        source="chan",
        confidence=0.7,
    )


def scan_sublevel(push: bool = True) -> list[Signal]:
    """对自选股做次级别扫描，信号推飞书。"""
    from .notify import feishu_bot
    signals = []
    for code, name in load_picks():
        try:
            sig = sublevel_signal(code, name)
        except Exception:
            logger.exception("次级别扫描失败 %s", code)
            continue
        if sig:
            signals.append(sig)
            if push:
                feishu_bot.signal_to_card(sig)
                logger.info("已推送: %s", sig.title)
    logger.info("次级别扫描完成: %d只自选, %d条信号", len(load_picks()), len(signals))
    return signals
