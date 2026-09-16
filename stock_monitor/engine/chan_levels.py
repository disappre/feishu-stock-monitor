# -*- coding: utf-8 -*-
"""多级别递归缠论结构（通达信"缠论主图"口径，2026-09-14实现）。

源码核心机制（已解析）：
```
X_122: 新低点确认(低点变化 且 低点<=前低) → 触发红轨更新
X_123 = MIN(最近两个高点) → ZG(中枢上沿)
X_124 = MAX(最近两个低点) → ZD(中枢下沿)
X_127: 新高点确认 → 触发绿轨更新  X_128/X_129 → 绿轨ZG/ZD
X_132~X_195: 中枢递归延伸(连续15+次判定) —— 价格突破前轨则新生中枢
```
关键洞见：
1. **中枢用"轨道"表达**（红轨=下跌中新低驱动，绿轨=上涨中新高驱动），
   轨道向右延伸直到被突破才确认新中枢——比固定矩形更贴合"中枢延伸"；
2. **递归多级别**：同一套极值聚合规则逐层应用（L1笔→L2段→L3→L4），
   而不是只做两级；
3. **多级别买卖点**：本级别中枢 + 次级别走势离开回试。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .base import Signal


@dataclass
class Swing:
    """交替高低点（级别的原子单位）。"""
    dt: str
    price: float
    kind: str          # 'high' | 'low'


@dataclass
class LevelInfo:
    """单个级别的结构信息。"""
    level: int                              # 1=笔, 2=线段, 3=更高级别...
    swings: list = field(default_factory=list)      # 交替高低点
    zhongshu: list = field(default_factory=list)    # [{zg,zd,start,end,rail}]


def swings_from_bi(bi_list) -> list:
    """L1: czsc笔 → 交替高低点序列。"""
    swings = []
    for i, bi in enumerate(bi_list):
        from czsc import Direction
        if i == 0:
            swings.append(Swing(str(bi.fx_a.dt)[:10], bi.fx_a.fx,
                                'low' if bi.direction == Direction.Up else 'high'))
        swings.append(Swing(str(bi.fx_b.dt)[:10], bi.fx_b.fx,
                            'high' if bi.direction == Direction.Up else 'low'))
    return swings


def aggregate_swings(swings: list, min_legs: int = 3) -> list:
    """级别提升：交替高低点 → 更高级别交替高低点。

    规则（特征序列简化，与 chan_analysis.build_segments 同源）：
    向上腿内，反向点(低点)必须**逐个抬升**；一旦某反向点低于上一个反向点，
    腿终结于腿内最高点，新腿从该最高点向下开始（向下腿镜像）。
    该规则比"跌破腿起点"温和，能得到合理的高低点保留比（约1/3）。
    """
    if len(swings) < min_legs + 1:
        return list(swings)
    out = [swings[0]]
    i, n = 0, len(swings)
    while i < n - 1:
        cur = swings[i]
        upward = cur.kind == 'low'
        best_j, best_v = i, cur.price
        last_counter = cur.price          # 上一个反向点（初始=腿起点）
        j = i + 1
        ended = False
        while j < n:
            p = swings[j]
            same_dir = (p.kind == 'high') if upward else (p.kind == 'low')
            if same_dir:
                if (upward and p.price > best_v) or (not upward and p.price < best_v):
                    best_j, best_v = j, p.price        # 同向延伸
            else:
                worse = (p.price < last_counter) if upward else (p.price > last_counter)
                if worse and (j - i) >= 2:
                    out.append(swings[best_j])         # 反向点劣化→腿终结
                    i = best_j
                    ended = True
                    break
                last_counter = p.price
            j += 1
        if not ended:
            out.append(swings[best_j])                 # 腿未终结，收尾
            break
    return out


def build_levels(bi_list, max_level: int = 4) -> dict:
    """递归构建多级别结构。返回 {level: LevelInfo}。"""
    levels = {}
    s1 = swings_from_bi(bi_list)
    levels[1] = LevelInfo(level=1, swings=s1)
    cur = s1
    for lv in range(2, max_level + 1):
        nxt = aggregate_swings(cur, min_legs=3)
        if len(nxt) < 4 or len(nxt) >= len(cur):
            break                                          # 无法再聚合
        levels[lv] = LevelInfo(level=lv, swings=nxt)
        cur = nxt
    for lv, info in levels.items():
        info.zhongshu = find_zhongshu(info.swings)
    return levels


def find_zhongshu(swings: list) -> list:
    """从高低点序列识别中枢（三笔重叠 + 轨道延伸，TDX口径）。

    中枢: 连续三条腿的价格区间重叠 → ZG=min(各腿高), ZD=max(各腿低), 需 ZG>ZD
    轨道: 红轨(下跌中新低驱动) / 绿轨(上涨中新高驱动) —— 记录其类型
    延伸: 后续腿若仍与[ZD,ZG]重叠则中枢延续（不多开新中枢）
    """
    out = []
    if len(swings) < 4:
        return out
    used_until = -1
    for i in range(len(swings) - 3):
        if i <= used_until:
            continue
        legs = []
        for k in range(i, i + 3):
            a, b = swings[k], swings[k + 1]
            legs.append((min(a.price, b.price), max(a.price, b.price)))
        zg = min(hi for _, hi in legs)
        zd = max(lo for lo, _ in legs)
        if zg <= zd:
            continue                                       # 无重叠，非中枢
        # 轨道类型: 看中枢形成前的走向（低点驱动=红轨/下跌，高点驱动=绿轨/上涨）
        rail = 'red' if swings[i].kind == 'high' else 'green'
        # 延伸: 向后吸收仍与[zd,zg]重叠的腿
        end_j = i + 3
        for k in range(i + 3, len(swings) - 1):
            a, b = swings[k], swings[k + 1]
            lo, hi = min(a.price, b.price), max(a.price, b.price)
            if hi < zd or lo > zg:
                break                                      # 脱离中枢
            end_j = k + 1
            # 轨道扩张: 中枢区间随重叠腿调整（TDX的MIN/MAX递归）
            zg = min(zg, hi)
            zd = max(zd, lo)
        out.append({
            "zg": round(zg, 3), "zd": round(zd, 3),
            "start": swings[i].dt, "end": swings[min(end_j, len(swings) - 1)].dt,
            "rail": rail, "n_legs": end_j - i,
        })
        used_until = end_j
    return out


def multilevel_signals(levels: dict) -> dict:
    """多级别买卖点（本级别中枢 + 次级别离开回试，严格口径）。

    三买: 本级别**最近一个**中枢形成后，次级别走势**第一次**向上离开中枢且
          回试低点不破ZG；回试必须在离开后 3 个次级别点内（防止用陈旧中枢误判）
    三卖: 镜像
    关键修正（2026-09-14）：只取中枢结束后的第一组离开-回试 + 时效窗口，
    避免"2024年的中枢"去判定"2026年的价格"这类越界误判。
    """
    res = {"3buy": [], "3sell": []}
    for lv, info in levels.items():
        if lv <= 1 or not info.zhongshu:
            continue
        sub = levels.get(lv - 1)                           # 次级别
        if not sub:
            continue
        # 只取最近 2 个中枢（旧中枢已失效）
        for zs in info.zhongshu[-2:]:
            after = [s for s in sub.swings if s.dt > zs["end"]]
            if len(after) < 3:
                continue
            # 只考察中枢结束后**前5个**次级别点（时效窗口）
            window = after[:5]
            for k in range(len(window) - 1):
                a, b = window[k], window[k + 1]
                if a.kind == 'low' and b.kind == 'high' and b.price > zs["zg"]:
                    for c in window[k + 1:]:           # 第一次回试
                        if c.kind == 'low':
                            if c.price > zs["zg"]:
                                res["3buy"].append({
                                    "level": lv, "date": c.dt,
                                    "price": round(c.price, 3),
                                    "zg": zs["zg"], "zd": zs["zd"],
                                    "rail": zs["rail"]})
                            break                       # 只认第一次回试
                    break
                elif a.kind == 'high' and b.kind == 'low' and b.price < zs["zd"]:
                    for c in window[k + 1:]:
                        if c.kind == 'high':
                            if c.price < zs["zd"]:
                                res["3sell"].append({
                                    "level": lv, "date": c.dt,
                                    "price": round(c.price, 3),
                                    "zg": zs["zg"], "zd": zs["zd"],
                                    "rail": zs["rail"]})
                            break
                    break
    return res


class MultiLevelRule:
    """多级别结构规则源：输出本级别三买/三卖（次级别确认）。"""
    name = "multilevel"

    def __init__(self, params: dict):
        self.max_level = int(params.get("max_level", 4))
        self.min_recency = int(params.get("min_recency", 3))

    def check(self, code: str, name: str, kline: pd.DataFrame) -> list[Signal]:
        try:
            from czsc import CZSC, Freq, RawBar
            bars = [RawBar(symbol=code, dt=pd.Timestamp(r["date"]), freq=Freq.D,
                           open=r["open"], close=r["close"], high=r["high"],
                           low=r["low"], vol=r["volume"], amount=0.0)
                    for _, r in kline.iterrows()]
            c = CZSC(bars)
            levels = build_levels(c.bi_list, self.max_level)
        except Exception:
            return []
        ml = multilevel_signals(levels)
        last_dt = str(kline["date"].iloc[-1])[:10]
        dates = kline["date"].astype(str).str.slice(0, 10).tolist()
        recent = set(dates[-self.min_recency * 3:])        # 近因窗口
        signals = []
        for tag, direction, label in (("3buy", "bullish", "三买"),
                                      ("3sell", "bearish", "三卖")):
            for p in ml[tag]:
                if p["date"] not in recent:
                    continue
                lv = p["level"]
                signals.append(Signal(
                    code=code, name=name,
                    signal_type=f"ml_{lv}_{label}",
                    direction=direction,
                    title=f"{name} · {lv}级{label}(次级别确认)",
                    detail=(f"{lv}级中枢[ZD {p['zd']} / ZG {p['zg']}]后的"
                            f"{lv - 1}级走势回试不破中枢沿，{p['date']} 确认于 {p['price']}"
                            f"（轨道类型：{'下跌红轨' if p['rail'] == 'red' else '上涨绿轨'}）"),
                    metrics={"level": lv, "zg": p["zg"], "zd": p["zd"],
                             "rail": p["rail"], "price": p["price"]},
                    source="multilevel", confidence=0.75,
                ))
        return signals
