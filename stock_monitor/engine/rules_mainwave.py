# -*- coding: utf-8 -*-
"""五步抓主升引擎（教学视频规则，知识卡 five-step-mainwave.md）。

五步: ①三金叉定趋势(5日内) → ②量能破五触发 → ③标杆量(N-1原则)
     → ④四象限持仓纪律 → ⑤二次出货清仓
止损: 触发日最低价 / 5日线 / 20日线(逃命线)

引擎输出两类信号:
- mainwave_trigger: 量能破五触发(进攻信号, bullish)
- mainwave_exit:    二次出货确认(清仓信号, bearish)
状态查询: 持仓状态(拉升/洗盘/一次出货观察)通过 status() 获取,不推信号
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .base import Signal


@dataclass
class MainwaveState:
    """五步法的当前状态快照。"""
    stage: str = "无信号"        # 无信号/定势待触发/已触发/持仓/一次出货/已清仓
    trigger_date: str = ""
    benchmark_vol: float = 0.0   # 标杆量
    over_count: int = 0          # 超标杆量次数
    last_vol: float = 0.0
    note: str = ""


def _triple_golden_cross(close: pd.Series, i: int, window: int = 5) -> bool:
    """第i天往前window日内是否完成三金叉(5>10, 5>20, 10>20 且近期发生上穿)。
    判定: 第i天三线多头排列, 且window日内出现过5上穿20的交叉日。"""
    if i < 21:
        return False
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    if not (ma5.iloc[i] > ma10.iloc[i] > ma20.iloc[i]):
        return False
    # 5日线上穿20日线须发生在window日内
    for j in range(max(21, i - window + 1), i + 1):
        if ma5.iloc[j - 1] <= ma20.iloc[j - 1] and ma5.iloc[j] > ma20.iloc[j]:
            return True
    return False


def _vol_break_five(vol: pd.Series, i: int) -> bool:
    """量能破五: 当日量 > 前5日最高量。"""
    if i < 5:
        return False
    return vol.iloc[i] > vol.iloc[i - 5: i].max()


def analyze_mainwave(kline: pd.DataFrame, max_wait: int = 7) -> MainwaveState:
    """全流程状态分析（最新交易日状态）。"""
    close = kline["close"].astype(float)
    vol = kline["volume"].astype(float)
    n = len(kline)
    state = MainwaveState()

    # ①+② 找最近的"定势+7日内破五"触发点（从后往前找最近一次有效触发）
    trigger_i = None
    for i in range(n - 1, max(21, n - 60), -1):
        if not _vol_break_five(vol, i):
            continue
        # 破五日或其前max_wait日内有三金叉
        for j in range(i, max(21, i - max_wait), -1):
            if _triple_golden_cross(close, j):
                trigger_i = i
                break
        if trigger_i is not None:
            break
    if trigger_i is None:
        state.stage = "无信号"
        state.note = "近期无'三金叉+量能破五'组合"
        return state

    state.trigger_date = str(kline["date"].iloc[trigger_i])[:10]
    state.stage = "已触发"
    state.note = f"{state.trigger_date} 量能破五触发(三金叉定势)"

    # ③ 标杆量（用户口径2026-09-12）：连续放量段中"量能最大那天的前一天"；
    # 放量>3天=强势建仓（强度标注）。放量判定: 量>前5日均量1.5倍
    v_avg5 = vol.rolling(5).mean()
    surge_idx = []
    for i in range(trigger_i, n):
        if pd.isna(v_avg5.iloc[i]) or vol.iloc[i] > v_avg5.iloc[i] * 1.5:
            surge_idx.append(i)
        else:
            break
    if not surge_idx:
        state.benchmark_vol = float(vol.iloc[trigger_i])
        state.note += "；无连续放量,标杆量=触发日量"
    else:
        max_i = max(surge_idx, key=lambda k: vol.iloc[k])
        if max_i == surge_idx[0]:
            state.benchmark_vol = float(vol.iloc[surge_idx[0]])
            state.note += f"；最大量即首日,标杆量=当日"
        else:
            state.benchmark_vol = float(vol.iloc[max_i - 1])
            state.note += f"；标杆量=最大量({str(kline['date'].iloc[max_i])[:10]})前一天"
        if len(surge_idx) > 3:
            state.note += f"；连续放量{len(surge_idx)}天>3天=强势建仓"

    # ④ 四象限: 触发日之后每日量对照标杆量
    over = 0
    for i in range(trigger_i, n):
        v = float(vol.iloc[i])
        if v > state.benchmark_vol:
            over += 1
            if over == 1:
                state.stage = "一次出货·持仓观察"
            elif over >= 2:
                state.stage = "二次出货·清仓离场"
                state.note += f"；{str(kline['date'].iloc[i])[:10]} 出现二次出货量"
                break
        else:
            # 低于标杆量: 拉升量或洗盘量,都是持仓
            state.stage = "持仓(拉升量)" if close.iloc[i] >= kline['open'].astype(float).iloc[i] else "持仓(洗盘量)"
    state.over_count = over
    state.last_vol = float(vol.iloc[-1])

    # 止损检查(当前价与5/20日线)
    ma5 = close.rolling(5).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1]
    if close.iloc[-1] < ma20:
        state.note += f"；⚠️收盘跌破20日线(逃命线),坚决清仓"
        state.stage = "已破20日线·清仓"
    elif close.iloc[-1] < ma5:
        state.note += f"；收盘跌破5日线,减仓观望"
    return state


class MainwaveRule:
    """五步抓主升信号源：触发(买入)与二次出货(清仓)两类推送。"""
    name = "mainwave"

    def __init__(self, params: dict):
        self.max_wait = int(params.get("max_wait", 7))

    def check(self, code: str, name: str, kline: pd.DataFrame) -> list[Signal]:
        st = analyze_mainwave(kline, self.max_wait)
        signals = []
        # 只推送"刚触发"或"刚确认二次出货"（近3日内的事件）
        n = len(kline)
        dates = kline["date"].astype(str).str.slice(0, 10).tolist()
        last_date = dates[-1] if dates else ""
        if st.trigger_date and st.trigger_date in dates[-3:]:
            signals.append(Signal(
                code=code, name=name, signal_type="mainwave_trigger",
                direction="bullish", title=f"{name} · 五步抓主升·进攻触发",
                detail=f"三金叉定势+量能破五触发（{st.trigger_date}）。{st.note}。"
                       f"止损:跌破触发日最低价/5日线减仓/20日线清仓",
                metrics={"trigger_date": st.trigger_date,
                         "benchmark_vol": round(st.benchmark_vol, 0)},
                source="mainwave", confidence=0.7))
        if "二次出货" in st.stage or "破20日线" in st.stage:
            signals.append(Signal(
                code=code, name=name, signal_type="mainwave_exit",
                direction="bearish", title=f"{name} · 五步法·清仓信号",
                detail=st.note or st.stage,
                metrics={"stage": st.stage},
                source="mainwave", confidence=0.75))
        return signals
