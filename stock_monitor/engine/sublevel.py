# -*- coding: utf-8 -*-
"""双级别缠论分析：日线中枢 + 30分钟次级别结构（自同构性的实战落地）。

用户需求（2026-09-11）：日线价格在中枢内震荡时，方向判断下沉到次级别——
"次级别出现三卖 → 反弹力度不足，日线下降趋势延续"。

数据：腾讯 proxy.finance.qq.com mkline m30（约60个交易日），实时拉取不落库
（30分钟数据量大、更新频繁，收盘日线库已够回测；次级别只服务盘中判断）。

判定逻辑（日线中枢内时）：
1. 拉取该股30分钟K线 → czsc构建次级别笔/中枢
2. 次级别三卖（反弹笔不碰次中枢上沿）→ 日线"反弹衰竭"预警（bearish）
3. 次级别三买（回调笔不碰次中枢下沿）→ 日线"回调企稳"提示（bullish）
4. 日线不在中枢内 → 不做次级别判断（日线结构信号已足够）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_M30_URL = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def fetch_m30(code: str, count: int = 480) -> pd.DataFrame | None:
    """腾讯30分钟K线（前复权）。行格式: [YYYYMMDDHHMM, open, close, high, low, volume, {}, amount]"""
    try:
        symbol = ("sh" if code.startswith(("6", "5", "9")) else "sz") + code
        r = requests.get(_M30_URL, params={"param": f"{symbol},m30,,,{count},qfq"},
                         headers=_HEADERS, timeout=10)
        node = r.json().get("data", {}).get(symbol, {})
        key = next((k for k in node if "m30" in k), None)
        if not key:
            return None
        rows = []
        for p in node[key]:
            rows.append({
                "dt": pd.to_datetime(p[0], format="%Y%m%d%H%M"),
                "open": float(p[1]), "close": float(p[2]),
                "high": float(p[3]), "low": float(p[4]),
                "volume": float(p[5]),
            })
        return pd.DataFrame(rows)
    except Exception as e:
        logger.warning("30分钟数据获取失败 %s: %s", code, e)
        return None


@dataclass
class SubLevelResult:
    """次级别(30分钟)结构判断。"""
    n_bi: int = 0
    n_zs: int = 0
    last_bi_dir: str = ""           # 次级别当前笔方向
    verdict: str = ""               # 结论: 反弹衰竭/回调企稳/中性
    detail: str = ""

    @property
    def direction(self) -> str:
        return {"反弹衰竭": "bearish", "回调企稳": "bullish"}.get(self.verdict, "neutral")


def analyze_sublevel(code: str) -> SubLevelResult | None:
    """30分钟级别结构分析：次级别三买/三卖 → 日线反弹/回调判断。"""
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    tools = str(repo / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from chan_analysis import detect_3rd_points
    from czsc import CZSC, Direction, Freq, RawBar

    df = fetch_m30(code)
    if df is None or len(df) < 60:
        return None
    bars = [RawBar(symbol=code, dt=r["dt"], freq=Freq.F30,
                   open=r["open"], close=r["close"], high=r["high"],
                   low=r["low"], vol=r["volume"], amount=0.0)
            for _, r in df.iterrows()]
    c = CZSC(bars)
    last_px = float(df["close"].iloc[-1])
    # 次级别三买卖：近因过滤（最近8笔内）+失效校验
    pts = detect_3rd_points(c.zs_list, c.bi_list,
                            last_px=last_px, max_age_bars=8)
    res = SubLevelResult(n_bi=len(c.bi_list), n_zs=len(c.zs_list),
                         last_bi_dir=str(c.bi_list[-1].direction) if c.bi_list else "")
    if not pts:
        res.verdict = "中性"
        res.detail = f"次级别{len(c.bi_list)}笔/{len(c.zs_list)}中枢，无三买卖结构"
        return res
    idx, kind, px = pts[-1]
    when = str(c.bi_list[idx].fx_b.dt)[:16]
    if kind == "三卖":
        res.verdict = "反弹衰竭"
        res.detail = (f"次级别(30分钟)三卖@{when} 点位{px:.2f}——反弹不碰次中枢沿，"
                      f"日线下跌趋势大概率延续，本波属反弹")
    else:
        res.verdict = "回调企稳"
        res.detail = (f"次级别(30分钟)三买@{when} 点位{px:.2f}——回调不碰次中枢沿，"
                      f"日线反弹结构完好")
    return res
