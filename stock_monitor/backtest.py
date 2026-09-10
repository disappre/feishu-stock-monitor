# -*- coding: utf-8 -*-
"""规则历史回测核心：逐根K线回放规则引擎，统计信号后N日胜率。

回放语义（与在线扫描严格一致，无未来函数）：
- 第 i 根收盘时调用 rule.check(code, name, kline.iloc[:i+1])；
- 信号交易翻译（Python回测器卡）：入场=次日开盘 open[i+1]，
  出场=第 fwd_days 日收盘 close[i+fwd_days]；
- 电平触发规则（rsi/surge 等）按 (code, signal_type) 冷却 fwd_days 根去重；
- 胜负方向感知：bullish 信号 win = fwd_ret > 0，bearish 反之；
- 末尾 fwd_days 根不产生信号（前瞻收益不可得）。

train/test 切分：按全局日期前 train_ratio 比例切分，信号按日期归属；
回放只跑一遍，两份指标从同一信号流划分，训练期选参、测试期只验证。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from .engine.base import Signal


@dataclass
class BacktestRecord:
    code: str
    date: str
    signal_type: str
    direction: str
    fwd_ret: float          # 出场/入场-1（百分比）

    @property
    def is_win(self) -> bool:
        return self.fwd_ret > 0 if self.direction == "bullish" else self.fwd_ret < 0


def replay_rule(rule, code: str, name: str, kline: pd.DataFrame,
                fwd_days: int = 5) -> list[BacktestRecord]:
    """逐根回放单个规则。冷却=同(code,signal_type)触发后 fwd_days 根内忽略。"""
    n = len(kline)
    if n < fwd_days + 2:
        return []
    close = kline["close"].to_numpy(dtype=float)
    open_ = kline["open"].to_numpy(dtype=float)
    dates = kline["date"].astype(str).str.slice(0, 10).tolist()

    records: list[BacktestRecord] = []
    last_bar: dict[str, int] = {}   # signal_type -> 上次触发根序号
    # 最小起步历史 2 根（各规则自身会再校验各自窗口），可评估终点 n-fwd_days-1
    for i in range(1, n - fwd_days):
        signals: list[Signal] = rule.check(code, name, kline.iloc[: i + 1])
        if not signals:
            continue
        entry = open_[i + 1]
        exit_ = close[i + fwd_days]
        if entry <= 0:
            continue
        fwd_ret = (exit_ / entry - 1) * 100
        for s in signals:
            prev = last_bar.get(s.signal_type)
            if prev is not None and i - prev < fwd_days:
                continue
            last_bar[s.signal_type] = i
            records.append(BacktestRecord(code, dates[i], s.signal_type,
                                          s.direction, fwd_ret))
    return records


def split_by_date(records: list[BacktestRecord], train_ratio: float = 0.7
                  ) -> tuple[list[BacktestRecord], list[BacktestRecord], str]:
    """按全局日期切分。返回 (train, test, split_date)。"""
    if not records:
        return [], [], ""
    dates = sorted({r.date for r in records})
    split_date = dates[min(len(dates) - 1, int(len(dates) * train_ratio))]
    train = [r for r in records if r.date <= split_date]
    test = [r for r in records if r.date > split_date]
    return train, test, split_date


def summarize(records: list[BacktestRecord]) -> dict[str, dict]:
    """按 signal_type 聚合：n / win_rate / mean_fwd_pct / median_fwd_pct。"""
    groups: dict[str, list[BacktestRecord]] = {}
    for r in records:
        groups.setdefault(r.signal_type, []).append(r)
    stats = {}
    for stype, items in sorted(groups.items()):
        rets = [r.fwd_ret for r in items]
        wins = sum(1 for r in items if r.is_win)
        stats[stype] = {
            "n": len(items),
            "win_rate": round(wins / len(items), 3),
            "mean_fwd_pct": round(sum(rets) / len(rets), 3),
            "median_fwd_pct": round(sorted(rets)[len(rets) // 2], 3),
        }
    return stats


def backtest_rules(rules: dict, frames: dict[str, pd.DataFrame],
                   names: dict[str, str], fwd_days: int = 5,
                   train_ratio: float = 0.7) -> dict:
    """rules: {rule_name: rule实例}；frames: {code: kline}。

    返回 {rule_name: {"full"/"train"/"test": summarize结果, "split_date": str}}。
    """
    out: dict = {}
    for rule_name, rule in rules.items():
        records: list[BacktestRecord] = []
        for code, kline in frames.items():
            records.extend(replay_rule(rule, code, names.get(code, code),
                                       kline, fwd_days))
        train, test, split_date = split_by_date(records, train_ratio)
        out[rule_name] = {
            "full": summarize(records),
            "train": summarize(train),
            "test": summarize(test),
            "split_date": split_date,
        }
    return out
