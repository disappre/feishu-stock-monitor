# -*- coding: utf-8 -*-
"""形态识别适配层：桥接 stock-clustering-system 的 TechnicalPatternRecognizer。

对接方式：
- 仓库 clone 到本地后，通过环境变量 CLUSTER_REPO_PATH 指定路径（默认取上级目录）；
- 该仓库的 recognize_all_patterns(klines: List[Dict]) 输入标准K线字典列表，
  输出 PatternSignal(pattern_name / signal_type=buy|sell / confidence / reason)，
  本层负责 DataFrame -> dict 转换、方向映射、置信度过滤与信号去重。
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

import pandas as pd

from .base import SOURCE_PATTERN, Signal

logger = logging.getLogger(__name__)

DIRECTION_MAP = {"buy": "bullish", "sell": "bearish"}

# 与规则引擎重复的形态默认跳过（金叉已由 rules_ma 覆盖，避免同日双推）
DEFAULT_SKIP = {"金叉"}


def _load_recognizer():
    """从聚类系统仓库导入 TechnicalPatternRecognizer。"""
    repo = Path(os.getenv("CLUSTER_REPO_PATH", "../stock-clustering-system")).resolve()
    if not (repo / "ml_models" / "pattern_recognition.py").exists():
        raise FileNotFoundError(
            f"未找到聚类系统仓库: {repo}。请先 git clone 并通过 CLUSTER_REPO_PATH 指定路径")
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from ml_models.pattern_recognition import TechnicalPatternRecognizer  # noqa: E402
    return TechnicalPatternRecognizer()


class PatternRule:
    name = "pattern"

    def __init__(self, params: dict):
        self.min_confidence = float(params.get("min_confidence", 0.7))
        self.skip_overlaps = set(params.get("skip_overlaps", DEFAULT_SKIP))
        self._recognizer = None

    def check(self, code: str, name: str, kline: pd.DataFrame) -> list[Signal]:
        try:
            if self._recognizer is None:
                self._recognizer = _load_recognizer()
        except (FileNotFoundError, ImportError) as e:
            logger.warning("形态识别不可用，本轮跳过: %s", e)
            self._recognizer = _Unavailable()
            return []
        except Exception:
            logger.exception("形态识别器加载失败: %s", code)
            return []

        try:
            # 仓库接口吃 List[Dict]，标准K线列名直接对齐（date/open/close/high/low/volume）
            records = kline.to_dict("records")
            patterns = self._recognizer.recognize_all_patterns(records)
        except Exception:
            logger.exception("形态识别执行失败: %s", code)
            return []

        signals: list[Signal] = []
        for p in patterns or []:
            if p.confidence < self.min_confidence:
                continue
            if any(skip in p.pattern_name for skip in self.skip_overlaps):
                continue
            direction = DIRECTION_MAP.get((p.signal_type or "").lower(), "neutral")
            signals.append(Signal(
                code=code, name=name,
                signal_type=f"pattern_{p.pattern_name}",
                direction=direction,
                title=f"{name} · 识别到{p.pattern_name}形态",
                detail=p.reason or f"聚类/形态模型判定出现「{p.pattern_name}」，"
                                   f"信号价 {p.price:.2f}，置信度 {p.confidence:.0%}",
                metrics={
                    "pattern": p.pattern_name,
                    "confidence": round(float(p.confidence), 3),
                    "price": round(float(p.price), 2),
                },
                source=SOURCE_PATTERN,
                confidence=float(p.confidence),
            ))
        return signals


class _Unavailable:
    """聚类系统未就位时的占位对象，保证规则引擎其余部分正常运转。"""

    def recognize_all_patterns(self, records):
        return []
