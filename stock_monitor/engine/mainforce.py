# -*- coding: utf-8 -*-
"""主力趋势辨别：八阶段量价规则（用户提供CSV，2026-09-11）。

规则表（所处周期 × K线组合 → 主力行为判定）：
┌────┬──────────────────┬─────────────┬──────────────────────────┐
│ 阶段 │ K线组合           │ 主力行为     │ 操作含义                  │
├────┼──────────────────┼─────────────┼──────────────────────────┤
│ 1  │ 放量大涨(涨初期)   │ 建仓主动吸筹 │ 价涨量增,多方吸筹,持续看涨 │
│ 2  │ 缩量小跌(涨中期)   │ 震仓洗盘    │ 抛压减弱,止跌位置,择机进场 │
│ 3  │ 缩量滞涨(涨中期)   │ 测试抛压    │ 量价背离,短期回调,后续拉高 │
│ 4  │ 缩量/平量大涨      │ 锁仓拉升    │ 高控盘,延续上涨           │
│ 5  │ 放量/平量滞涨(涨末)│ 清仓出货    │ 抛压增大,即将见顶,减仓    │
│ 6  │ 放量大跌(跌初期)   │ 清仓砸盘    │ 大量卖出,持续下跌         │
│ 7  │ 缩量/平量大跌      │ 清仓完毕    │ 无人接盘,下跌中继,加速跌  │
│ 8  │ 放量/平量小跌(跌末)│ 建仓被动吸筹│ 越跌越买,见底信号,反转开仓│
└────┴──────────────────┴─────────────┴──────────────────────────┘

量化口径（日线，滚动窗口w=20日，近期段n=5日）：
- 趋势：n日收益的符号与幅度（涨/跌/平，涨跌幅>1%为"涨/跌"）
- 位置：相对w日高低点的位置（前1/3=初期/高位，后1/3=末期/低位，中段=中期）
- 量能：n日均量 vs w日均量的比值（>1.15=放量，<0.85=缩量，其余平量）
判定为硬规则表匹配，输出 (阶段号, 阶段名, 主力行为, 操作含义, 颜色)。
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

# 阶段定义: (名称, 主力行为, 操作含义, 颜色, 看涨性1/-1/0)
STAGES = {
    1: ("上涨初期·主动吸筹", "主力建仓吸筹", "价涨量增 多方吸筹 持续看涨", "#2f9e44", 1),
    2: ("上涨中期·震仓洗盘", "主力洗盘", "抛压减弱 止跌位置 择机进场", "#69db7c", 1),
    3: ("上涨中期·测试抛压", "主力测试", "量价背离 短期回调 后续拉高", "#a9e34b", 1),
    4: ("上涨中期·锁仓拉升", "主力锁仓", "缩量加速 高控盘 延续上涨", "#37b24d", 1),
    5: ("上涨末期·清仓出货", "主力出货", "抛压增大 即将见顶 减仓清仓", "#ffd43b", -1),
    6: ("下跌初期·清仓砸盘", "主力砸盘", "大量卖出 持续下跌", "#ff8787", -1),
    7: ("下跌中期·清仓完毕", "主力清仓完毕", "无人接盘 下跌中继 加速下跌", "#f03e3e", -1),
    8: ("下跌末期·被动吸筹", "主力建仓吸筹", "越跌越买 见底信号 反转开仓", "#74c0fc", 1),
}


@dataclass
class VolumeProfile:
    """量能特征（通达信"成交量量能"源码口径，2026-09-14接入）。

    源码定义：
      倍量: V >= REF(V,1)*1.9 AND (假阴柱条件)      ← 放量1.9倍
      倍缩: REF(V,1) >= V*1.9                        ← 缩量至1/1.9
      平量: RANGE(V/REF(V,1), 0.97, 1.03)            ← 量能持平
      百低: V = LLV(V,125) AND BARSCOUNT>=125        ← 125日最低量
      天量: V = HHV(V,0)                             ← 历史最高量
      量王: 天量 AND 倍量 AND 量比>3 AND BARSCOUNT>120's
      长阴短柱: C/REF(C,1)<=0.96 AND V < HHV(V,30)/1.9  ← 大跌却缩量(惜售)
    """
    ratio: float = 0.0          # 量比 = V/REF(V,1)
    vol_ratio_ma: float = 0.0   # 量/前5日均量(原口径保留)
    倍量: bool = False
    倍缩: bool = False
    平量: bool = False
    百低: bool = False
    天量: bool = False
    量王: bool = False
    长阴短柱: bool = False
    note: str = ""


def volume_profile(kline: pd.DataFrame) -> VolumeProfile:
    """按源码口径识别量能形态。"""
    vp = VolumeProfile()
    vol = kline["volume"].astype(float)
    close = kline["close"].astype(float)
    open_ = kline["open"].astype(float)
    n = len(kline)
    if n < 2:
        return vp
    v, v_prev = float(vol.iloc[-1]), float(vol.iloc[-2])
    if v_prev > 0:
        vp.ratio = round(v / v_prev, 2)
    if n >= 6:
        vp.vol_ratio_ma = round(v / (vol.iloc[-6:-1].mean() + 1e-9), 2)
    # 倍量（源码含假阴柱条件：C<O 但 C>REF(C,1) 也算放量）
    fake_yin = (float(close.iloc[-1]) < float(open_.iloc[-1])
                and n >= 2 and float(close.iloc[-1]) > float(close.iloc[-2]))
    vp.倍量 = v >= v_prev * 1.9 and (fake_yin or float(close.iloc[-1]) >= float(open_.iloc[-1])
                                   or float(close.iloc[-1]) > float(close.iloc[-2]))
    vp.倍缩 = v_prev >= v * 1.9
    vp.平量 = 0.97 <= vp.ratio <= 1.03 and n >= 2
    if n >= 125:
        vp.百低 = v <= float(vol.iloc[-125:].min()) + 1e-9
        vp.天量 = v >= float(vol.max()) - 1e-9
        # 量王: 天量+倍量+量比>3+上市超120日
        vp.量王 = vp.天量 and vp.倍量 and vp.vol_ratio_ma > 3 and n > 120
    if n >= 31:
        vp.长阴短柱 = (float(close.iloc[-1]) / float(close.iloc[-2]) <= 0.96
                       and v < float(vol.iloc[-31:-1].max()) / 1.9)
    tags = [t for t in ("倍量", "倍缩", "平量", "百低", "天量", "量王", "长阴短柱")
            if getattr(vp, t)]
    vp.note = "｜".join(tags) if tags else f"常规量(量比{vp.ratio})"
    return vp


@dataclass
class StageResult:
    stage: int
    name: str
    action: str
    advice: str
    color: str
    bias: int          # 1=看涨 -1=看跌 0=中性


def classify_stage(kline: pd.DataFrame, w: int = 20, n: int = 5) -> StageResult | None:
    """八阶段判定：趋势×位置×量能 → 阶段表硬匹配。"""
    if kline is None or len(kline) < w + n:
        return None
    close = kline["close"].astype(float)
    vol = kline["volume"].astype(float)

    ret_n = (close.iloc[-1] / close.iloc[-1 - n] - 1) * 100
    hi_w = close.iloc[-w:].max()
    lo_w = close.iloc[-w:].min()
    pos = (close.iloc[-1] - lo_w) / (hi_w - lo_w) if hi_w > lo_w else 0.5   # 0=低位 1=高位
    vol_ratio = vol.iloc[-n:].mean() / (vol.iloc[-w:].mean() + 1e-9)

    is_up = ret_n > 1.0
    is_down = ret_n < -1.0
    flat = not is_up and not is_down
    big_move = abs(ret_n) > 5.0
    high_vol = vol_ratio > 1.15
    low_vol = vol_ratio < 0.85

    # ── 规则表匹配（自上而下，先具体后一般） ──
    # 1: 放量大涨(需在非高位——初期判定: pos<0.7)
    if is_up and big_move and high_vol and pos < 0.7:
        return _mk(1)
    # 4: 缩量/平量大涨(中期)
    if is_up and big_move and not high_vol and 0.3 <= pos <= 0.85:
        return _mk(4)
    # 5: 放量/平量滞涨(高位)
    if (is_up and not big_move or flat) and pos > 0.75 and not low_vol:
        return _mk(5)
    # 6: 放量大跌(高位起跌)
    if is_down and big_move and high_vol and pos > 0.4:
        return _mk(6)
    # 7: 缩量/平量大跌
    if is_down and big_move and not high_vol:
        return _mk(7)
    # 8: 放量/平量小跌(低位)
    if is_down and not big_move and pos < 0.35 and not low_vol:
        return _mk(8)
    # 2: 缩量小跌(涨势中的回调)
    if is_down and not big_move and low_vol and pos > 0.4:
        return _mk(2)
    # 3: 缩量滞涨
    if (flat or (is_up and not big_move)) and low_vol and pos > 0.4:
        return _mk(3)
    # 兜底: 按趋势方向归到温和阶段
    if is_up:
        return _mk(4 if not low_vol else 3)
    if is_down:
        return _mk(8 if pos < 0.35 else 7)
    return _mk(3)


def _mk(stage: int) -> StageResult:
    name, action, advice, color, bias = STAGES[stage]
    return StageResult(stage, name, action, advice, color, bias)


def stage_series(kline: pd.DataFrame, w: int = 20, n: int = 5) -> pd.DataFrame:
    """逐日判定（用于图上色带）：返回 DataFrame(stage, bias) 与kline对齐。"""
    rows = []
    for i in range(len(kline)):
        if i < w + n:
            rows.append((0, 0))
            continue
        r = classify_stage(kline.iloc[: i + 1], w, n)
        rows.append((r.stage if r else 0, r.bias if r else 0))
    return pd.DataFrame(rows, columns=["stage", "bias"], index=kline.index)
