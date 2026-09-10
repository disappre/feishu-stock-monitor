# -*- coding: utf-8 -*-
"""聚类在线信号源：把 stock-api-master 的 ImprovedKMeans（K-means++/DTW/21维金融特征）
改造成"离线训练 + 在线匹配"的形态类信号引擎。

两阶段设计（对应 ImprovedKMeans 的 fit/predict 分工）：

【阶段一 离线训练】 tools/train_pattern_model.py
    对股票池每只股票取近 window 根K线的标准化收益序列 → ImprovedKMeans.fit
    → 产出 data/pattern_model.json：各簇中心特征、成员统计、每簇的
      后验行为标注（簇内成员未来N日平均涨跌 -> 簇的方向性）。

【阶段二 在线匹配】 本文件 PatternClusterRule
    每次扫描把最新K线窗口交给已训练模型的 predict（簇标签），
    命中"方向性显著"的簇即发信号；簇行为标注随复盘数据更新。

训练产物不存在时静默跳过（不影响其余规则），提示先跑阶段一。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from .base import SOURCE_PATTERN, Signal

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).resolve().parents[2] / "data" / "pattern_model.json"

# 方向性显著阈值：簇后验N日均涨跌的绝对值需超过该百分比才算"有方向"
DIRECTION_SIGNIFICANCE = 0.5
# 匹配显著阈值：样本到簇中心的标准化距离需小于该值才算"属于"该簇
MAX_MATCH_DISTANCE = 2.0


def _standardize_returns(close: pd.Series, window: int) -> np.ndarray | None:
    """取最近 window 根收盘的简单收益序列（去量纲，跨股票可比）。"""
    if len(close) < window + 1:
        return None
    c = close.astype(float).iloc[-(window + 1):]
    rets = c.pct_change().dropna().to_numpy()
    if not np.isfinite(rets).all() or np.std(rets) < 1e-10:
        return None
    return rets


class PatternClusterRule:
    """ImprovedKMeans 模型的在线匹配规则。"""
    name = "pattern_cluster"

    def __init__(self, params: dict):
        self.window = int(params.get("window", 30))
        self.min_confidence = float(params.get("min_confidence", 0.6))
        self._model_meta: dict | None = None

    def _load_model(self) -> dict | None:
        if self._model_meta is not None:
            return self._model_meta
        if not MODEL_PATH.exists():
            logger.warning(
                "聚类模型未训练（%s 不存在）。先运行 "
                "python tools/train_pattern_model.py 生成后再启用本规则", MODEL_PATH.name)
            return None
        self._model_meta = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
        return self._model_meta

    def check(self, code: str, name: str, kline: pd.DataFrame) -> list[Signal]:
        meta = self._load_model()
        if meta is None:
            return []
        try:
            sys_path = meta["repo_path"]
            if sys_path not in __import__("sys").path:
                __import__("sys").path.insert(0, sys_path)
            from ml_models.improved_kmeans import ImprovedKMeans
        except ImportError:
            logger.warning("无法导入 ImprovedKMeans，检查 CLUSTER_REPO_PATH/repo_path")
            return []

        rets = _standardize_returns(kline["close"], self.window)
        if rets is None:
            return []

        # 用训练时的特征维度在线提取：ImprovedKMeans.predict 内部会做
        # extract_features + scaler.transform（scaler 参数保存在 meta 中重建）
        try:
            model = ImprovedKMeans(n_clusters=meta["n_clusters"], use_dtw=False,
                                   use_features=True)
            import numpy as _np
            scaler_mean = _np.array(meta["scaler_mean"])
            scaler_scale = _np.array(meta["scaler_scale"])
            feats = model.feature_engineer.extract_features(
                _np.column_stack([kline["high"].astype(float).to_numpy()[-self.window:],
                                  kline["low"].astype(float).to_numpy()[-self.window:],
                                  kline["close"].astype(float).to_numpy()[-self.window:]]))
            feats_scaled = (feats - scaler_mean) / (scaler_scale + 1e-10)
            # 最近簇中心（欧氏距离，与 fit 的特征空间一致）
            centers = _np.array(meta["centroids"])
            dists = _np.linalg.norm(centers - feats_scaled, axis=1)
            cluster = int(_np.argmin(dists))
            dist = float(dists[cluster])
        except Exception:
            logger.exception("聚类在线匹配失败: %s", code)
            return []

        behavior = meta["cluster_behaviors"].get(str(cluster), {})
        if not behavior or abs(behavior.get("mean_fwd", 0.0)) < DIRECTION_SIGNIFICANCE:
            return []  # 该簇无显著方向性
        if dist > MAX_MATCH_DISTANCE:
            return []  # 距离过远，不属于任何已知形态簇

        bullish = behavior["mean_fwd"] > 0
        conf = float(np.clip(1.0 - dist / MAX_MATCH_DISTANCE, 0.0, 1.0))
        if conf < self.min_confidence:
            return []
        label = behavior.get("label", f"簇{cluster}")
        fwd = behavior["mean_fwd"]
        return [Signal(
            code=code, name=name,
            signal_type=f"cluster_{label}",
            direction="bullish" if bullish else "bearish",
            title=f"{name} · 匹配形态簇「{label}」",
            detail=(f"近{self.window}日收益形态特征匹配训练簇{cluster}（{label}，"
                    f"{behavior['n_members']}个历史样本），该簇历史后验{meta['fwd_days']}日"
                    f"均涨跌 {fwd:+.2f}%，匹配距离 {dist:.2f}"),
            metrics={"cluster": cluster, "distance": round(dist, 3),
                     "mean_fwd_pct": round(fwd, 2),
                     "fwd_days": meta["fwd_days"]},
            source=SOURCE_PATTERN,
            confidence=conf,
        )]
