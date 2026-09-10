# -*- coding: utf-8 -*-
"""配对价差规则（协整套利，knowledge/strategies/cointegration-pairs-trading.md 的落地）。

与现有规则的本质区别：不预测单只股票方向，而监控"两只同板块股票的价差
偏离其统计关系"的均值回归机会——与死叉同属均值回归信号族（该族是回测中
唯一被验证有超额胜率的方向）。

两阶段：
【离线配对筛选】tools/find_pairs.py：对池内股票两两做 Engle-Granger 检验
    （p<0.05）+ 价差 ADF 平稳性（p<0.05），产出 data/pairs.json；
    窗口≤6个月，建议每月重跑（协整关系会失效——卡片"周期治理"条）。
【在线监测】本规则：对已筛选配对计算价差 z-score，
    |z| > entry(2.0σ) 时发信号，方向取决于哪条腿相对便宜/昂贵。

信号语义（合规）：价差偏离事实+历史回归统计，非买卖建议。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .base import Signal

logger = logging.getLogger(__name__)

PAIRS_PATH = Path(__file__).resolve().parents[2] / "data" / "pairs.json"


class PairSpreadRule:
    """监控离线筛选出的协整配对，价差 z-score 超阈值发信号。"""
    name = "pair_spread"

    def __init__(self, params: dict):
        self.entry_z = float(params.get("entry_z", 2.0))
        self.exit_z = float(params.get("exit_z", 0.3))
        self.lookback = int(params.get("lookback", 60))
        # 回测实证：配对合成(含做空腿)测试期证伪；仅"价差偏低的多头腿"(z<0,
        # 买相对便宜的一腿)为A股可执行口径 → 默认只输出 z<0 的信号
        self.long_leg_only = bool(params.get("long_leg_only", True))
        self._pairs = None
        self._frame_cache: dict[str, object] = {}  # 避免同轮扫描重复读库

    def _load_pairs(self) -> list[dict]:
        if self._pairs is None:
            if not PAIRS_PATH.exists():
                logger.warning(
                    "配对文件不存在(%s)。先运行 python tools/find_pairs.py 筛选协整配对",
                    PAIRS_PATH.name)
                self._pairs = []
            else:
                self._pairs = json.loads(PAIRS_PATH.read_text(encoding="utf-8")).get("pairs", [])
        return self._pairs

    def check(self, code: str, name: str, kline: pd.DataFrame) -> list[Signal]:
        pairs = self._load_pairs()
        if not pairs:
            return []
        involved = [p for p in pairs if code in (p["a"], p["b"])]
        if not involved:
            return []
        close = kline["close"].astype(float)
        if len(close) < self.lookback:
            return []
        signals: list[Signal] = []
        for p in involved:
            other_code = p["b"] if code == p["a"] else p["a"]
            other_kline = self._get_other_kline(other_code)
            if other_kline is None or len(other_kline) < self.lookback:
                continue
            # 对齐两腿日期（本地库同源，取交集最近 lookback 根）
            own = close.iloc[-self.lookback:]
            other = other_kline["close"].astype(float).iloc[-self.lookback:]
            joined = pd.concat([own, other], axis=1, join="inner",
                               keys=[code, other_code]).dropna()
            if len(joined) < self.lookback * 0.8:
                continue
            hedge = float(p.get("hedge_ratio", 1.0))
            spread = joined[code] - hedge * joined[other_code]
            z = (spread.iloc[-1] - spread.mean()) / (spread.std() + 1e-10)
            if abs(z) < self.entry_z:
                continue
            if self.long_leg_only and z > 0:
                # 只保留多头腿口径：z<0(本腿相对便宜,买本腿)；
                # z>0 的信号是"卖本腿/买另一腿"，另一腿会在它自己的扫描中覆盖
                continue
            # 方向: z>0 → 本腿相对贵(若持有应减仓)；z<0 → 本腿相对便宜
            direction = "bearish" if z > 0 else "bullish"
            signals.append(Signal(
                code=code, name=name,
                signal_type=f"pair_spread_{p['pair_id']}",
                direction=direction,
                title=f"{name} · 配对价差偏离({p['pair_id']})",
                detail=(f"与{p.get('b_name' if code == p['a'] else 'a_name', other_code)}"
                        f"的价差 z={z:+.2f}σ（阈值±{self.entry_z}σ，"
                        f"平仓参考±{self.exit_z}σ）；协整p={p.get('eg_pvalue', 'NA')}，"
                        f"检验窗口{p.get('window_days', 60)}日。"
                        f"统计事实，非买卖建议"),
                metrics={"z": round(float(z), 2),
                         "entry_z": self.entry_z, "hedge": hedge,
                         "spread_mean": round(float(spread.mean()), 3)},
            ))
        # 单股命中多对时只推|z|最大的一条，避免信号刷屏
        if len(signals) > 1:
            signals.sort(key=lambda s: abs(s.metrics["z"]), reverse=True)
            signals = signals[:1]
        return signals

    def _get_other_kline(self, code: str) -> pd.DataFrame | None:
        """在线扫描时从本地库读另一腿（只读，带进程内缓存）。"""
        if code not in self._frame_cache:
            from ..data import store
            self._frame_cache[code] = store.load_recent(code)
        return self._frame_cache[code]
