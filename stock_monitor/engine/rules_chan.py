# -*- coding: utf-8 -*-
"""缠论规则：三买/三卖信号源（回测验证版本）。

回测实证（300只×2年，前瞻5日）：三买91.7%/三卖88.9%双期稳定；
一卖/最强二卖已证伪（胜率14-20%），不做信号源。
检测延迟：信号笔终点分型需右翼1根确认，保守口径=延迟1根（胜率87.6%）。

接入说明：作为第10个规则挂入RuleEngine，与其他信号共用推送/复盘链路。
每轮扫描重新构建CZSC（500根日线本地库，零联网）。
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from .base import Signal

logger = logging.getLogger(__name__)

# 模块级缓存：同一轮扫描多只股票时避免重复加载tools路径
_chan_loaded = False


def _load_chan():
    global _chan_loaded
    if not _chan_loaded:
        repo = Path(__file__).resolve().parents[2]
        import sys
        tools = str(repo / "tools")
        if tools not in sys.path:
            sys.path.insert(0, tools)
        _chan_loaded = True
    from chan_analysis import analyze, detect_3rd_points
    return analyze, detect_3rd_points


class ChanThirdPointRule:
    """三买/三卖检测（笔级近似次级别，保守+1根确认）。"""
    name = "chan_3rd"

    def __init__(self, params: dict):
        self.min_recency = int(params.get("min_recency", 3))
        self._cache: dict[str, object] = {}   # code -> ChanStructure（进程内）

    def _get_cs(self, code: str, name: str):
        if code not in self._cache:
            try:
                analyze, _ = _load_chan()
                self._cache[code] = analyze(code, name)
            except Exception:
                logger.exception("缠论分析失败 %s", code)
                self._cache[code] = None
        return self._cache[code]

    def check(self, code: str, name: str, kline: pd.DataFrame) -> list[Signal]:
        cs = self._get_cs(code, name)
        if cs is None:
            return []
        analyze, detect_3rd = _load_chan()
        pts = detect_3rd(cs.c.zs_list, cs.c.bi_list)
        if not pts:
            return []
        # 近因过滤：只报最近 min_recency 笔内的信号（旧结构无操作意义）
        recent = len(cs.c.bi_list) - self.min_recency
        signals = []
        for idx, kind, px in pts:
            if idx < recent:
                continue
            is_buy = kind == "三买"
            zs = cs.c.zs_list[-1] if cs.c.zs_list else None
            zs_desc = (f"中枢GG {zs.gg:.2f}/DD {zs.dd:.2f}" if zs else "无中枢")
            signals.append(Signal(
                code=code, name=name,
                signal_type=f"chan_{kind}",
                direction="bullish" if is_buy else "bearish",
                title=f"{name} · 缠论{kind}",
                detail=(f"离开中枢后回抽不碰中枢沿（{zs_desc}），信号点{px:.2f}，"
                        f"回测胜率{('91.7%' if is_buy else '88.9%')}（300只×2年，"
                        f"前瞻5日口径）。结构信号仅供参考学习，不构成投资建议"),
                metrics={"point": round(float(px), 2), "kind": kind,
                         "confirmed_at": str(cs.c.bi_list[idx].fx_b.dt)[:10]},
                source="chan",
                confidence=0.8 if is_buy else 0.75,
            ))
        return signals
