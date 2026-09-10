# -*- coding: utf-8 -*-
"""信号数据类与规则引擎调度核心。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

import pandas as pd

logger = logging.getLogger(__name__)

# 信号来源，用于复盘时区分规则引擎与模型引擎
SOURCE_RULE = "rule"
SOURCE_PATTERN = "pattern"


@dataclass
class Signal:
    """一条监测信号。所有规则/模型统一产出该结构，下游（LLM/飞书/表格）只认它。"""
    code: str
    name: str
    signal_type: str          # 如 ma_golden_cross / price_surge / pattern_head_shoulder_top
    direction: str            # bullish / bearish / neutral
    title: str                # 卡片标题，如 "贵州茅台 · 均线金叉"
    detail: str               # 人可读的触发说明
    metrics: dict = field(default_factory=dict)   # 关键数值，如 {"short": 5, "long": 20, "diff": 0.8}
    source: str = SOURCE_RULE
    triggered_at: datetime = field(default_factory=datetime.now)
    confidence: float = 1.0   # 模型类信号带置信度，规则类恒为 1.0


class Rule(Protocol):
    """规则协议：输入个股K线，输 出0~N条信号。"""
    name: str

    def check(self, code: str, name: str, kline: pd.DataFrame) -> list[Signal]: ...


class RuleEngine:
    """按配置装配规则，逐只股票扫描并汇总信号。"""

    def __init__(self, config: dict):
        self.config = config
        self.rules: list[Rule] = self._build_rules(config.get("rules", {}))

    def _build_rules(self, rule_cfg: dict) -> list[Rule]:
        from .rules_candlestick import CandlestickDivergenceRule
        from .rules_chan import ChanThirdPointRule
        from .rules_cluster import PatternClusterRule
        from .rules_fractal import FractalBreakRule
        from .rules_ma import MACrossRule
        from .rules_pair import PairSpreadRule
        from .rules_price import PriceSurgeRule, RSIRule, VolumeSurgeRule

        builders = {
            "ma_cross": MACrossRule,
            "price_surge": PriceSurgeRule,
            "volume_surge": VolumeSurgeRule,
            "rsi": RSIRule,
            "candle_div": CandlestickDivergenceRule,
            "fractal_break": FractalBreakRule,
            "pattern_cluster": PatternClusterRule,
            "pair_spread": PairSpreadRule,
            "chan_3rd": ChanThirdPointRule,
        }
        rules: list[Rule] = []
        for key, params in rule_cfg.items():
            if isinstance(params, dict) and params.get("enabled") and key in builders:
                rules.append(builders[key](params))
        # 模型引擎单独装配（依赖聚类系统，见 pattern.py）
        if (rule_cfg.get("pattern") or {}).get("enabled"):
            from .pattern import PatternRule
            rules.append(PatternRule(rule_cfg["pattern"]))
        return rules

    def scan(self, code: str, name: str, kline: pd.DataFrame) -> list[Signal]:
        if kline is None or kline.empty:
            return []
        signals: list[Signal] = []
        for rule in self.rules:
            try:
                signals.extend(rule.check(code, name, kline))
            except Exception:
                logger.exception("规则 %s 执行失败: %s", rule.name, code)
        return signals
