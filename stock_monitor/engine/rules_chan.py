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
    """三买/三卖检测（笔级近似次级别，保守+1根确认）。

    direction_gate: 方向门控（默认开）——只发"顺日线段方向"的信号：
    三买须在向上段内、三卖须在向下段内。实证（300只×2年）：
    顺段三买93.0% vs 逆段82.6%，10.4pt增量；逆段绝对水平仍超基线，
    故此门控是优化项而非必须（见知识卡"三级分工判决"）。
    """
    name = "chan_3rd"

    def __init__(self, params: dict):
        self.min_recency = int(params.get("min_recency", 3))
        self.direction_gate = bool(params.get("direction_gate", True))
        self._cache: dict[str, object] = {}   # code -> ChanStructure（进程内）

    def _seg_dir_of(self, cs, bi_idx: int) -> int:
        """信号笔所在日线段方向: 1=向上 -1=向下 0=无。"""
        if not cs.segments or bi_idx >= len(cs.c.bi_list):
            return 0
        d = str(cs.c.bi_list[bi_idx].fx_b.dt)[:10]
        for (sdt, spx, edt, epx, sdir) in cs.segments:
            if str(sdt)[:10] <= d <= str(edt)[:10]:
                return sdir
        return 0

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
        last_px = float(kline["close"].iloc[-1])
        # 失效校验+有效期：现价回中枢区间=信号失效；信号笔距今>min_recency笔=过期
        pts = detect_3rd(cs.c.zs_list, cs.c.bi_list,
                         last_px=last_px, max_age_bars=self.min_recency)
        if not pts:
            return []
        # 近因过滤：只报最近 min_recency 笔内的信号（旧结构无操作意义）
        recent = len(cs.c.bi_list) - self.min_recency
        signals = []
        for idx, kind, px in pts:
            if idx < recent:
                continue
            is_buy = kind == "三买"
            # 方向门控：三买须在向上段、三卖须在向下段（可配置关闭）
            if self.direction_gate:
                sd = self._seg_dir_of(cs, idx)
                if (is_buy and sd != 1) or ((not is_buy) and sd != -1):
                    continue
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
