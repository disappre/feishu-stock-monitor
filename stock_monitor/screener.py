# -*- coding: utf-8 -*-
"""每日信号筛选器：扫描股票池 → 按信号强度打分排序 → 飞书推送榜单。

定位（合规红线）：输出的是"哪些股票触发了什么信号"的事实榜单，
不是"推荐买入"。每条上榜原因都摊开列明规则与置信度，附免责声明。

评分模型（可调）：
    pattern_*   2.0 × 置信度   形态识别（含聚类/谐波类，证据最复合）
    fractal_break 2.0          分形突破+双EMA趋势确认
    candle_div  1.5            PinBar/吞没+RSI背离双确认
    ma_*_cross  1.0            均线交叉
    rsi_*       0.5            RSI极值
    *_surge     0.5            涨跌/量能异动
    看涨信号加分、看跌信号减分，净分为正排看涨榜、为负排看跌榜。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .data.feed import fetch_kline
from .data import store
from .engine.base import RuleEngine, Signal
from .notify import feishu_bot

logger = logging.getLogger("screener")

BASE_DIR = Path(__file__).resolve().parent.parent
POOL_FILE = BASE_DIR / "data" / "screen_pool.txt"

WEIGHTS = {
    "pattern": 2.0,      # 按 source 加权
    "fractal_break": 2.0,
    "candle_div": 1.5,
    "ma_cross": 1.0,
    "rsi": 0.5,
    "surge": 0.5,
    "chan_3rd": 2.5,     # 缠论三买/三卖：回测双期胜率88-93%，全系统最强结构信号
}


def _weight(s: Signal) -> float:
    if s.source == "pattern":
        return WEIGHTS["pattern"] * s.confidence
    if s.source == "chan":
        return WEIGHTS["chan_3rd"] * s.confidence
    if s.signal_type.startswith("fractal_break"):
        return WEIGHTS["fractal_break"]
    if s.signal_type.endswith(("rsi_div",)):  # bull_reversal_rsi_div 等
        return WEIGHTS["candle_div"]
    if "cross" in s.signal_type:
        return WEIGHTS["ma_cross"]
    if s.signal_type.startswith("rsi"):
        return WEIGHTS["rsi"]
    return WEIGHTS["surge"]


@dataclass
class PoolResult:
    code: str
    name: str
    score: float = 0.0
    signals: list = field(default_factory=list)


def load_pool(limit: int | None = None) -> list[tuple[str, str]]:
    """读取筛选池文件（code,name 每行一条，# 注释）。"""
    pool: list[tuple[str, str]] = []
    for raw in POOL_FILE.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = [p.strip() for p in line.replace("，", ",").split(",")]
        if parts[0].isdigit() and len(parts[0]) == 6:
            pool.append((parts[0], parts[1] if len(parts) > 1 else f"股票{parts[0]}"))
    return pool[:limit] if limit else pool


def screen(pool: list[tuple[str, str]], config: dict,
           sleep_s: float = 0.25) -> list[PoolResult]:
    """逐只扫描股票池，返回有信号的结果（按净分绝对值降序）。"""
    engine = RuleEngine(config)
    results: list[PoolResult] = []
    for i, (code, name) in enumerate(pool):
        if i:
            time.sleep(sleep_s)  # 数据源礼貌限速
        try:
            kline = fetch_kline(code)
            if kline is None:
                continue
            signals = engine.scan(code, name, kline)
        except Exception:
            logger.warning("筛选异常，跳过 %s", code, exc_info=True)
            continue
        if not signals:
            continue
        r = PoolResult(code=code, name=name, signals=signals)
        for s in signals:
            r.score += _weight(s) if s.direction == "bullish" else -_weight(s)
        results.append(r)
        logger.info("[%d/%d] %s %s 净分%.1f", i + 1, len(pool), name, code, r.score)
    results.sort(key=lambda r: r.score, reverse=True)
    return results


def screen_local(pool: list[tuple[str, str]], config: dict) -> tuple[list[PoolResult], list[str]]:
    """纯本地 SQLite 扫描：绝不触网，缺失/不足数据显式返回。"""
    engine = RuleEngine(config)
    frames = store.load_many([code for code, _ in pool])
    results: list[PoolResult] = []
    missing: list[str] = []
    for code, name in pool:
        kline = frames.get(code)
        if kline is None or len(kline) < 455:
            missing.append(code)
            continue
        signals = engine.scan(code, name, kline)
        if not signals:
            continue
        r = PoolResult(code=code, name=name, signals=signals)
        for s in signals:
            r.score += _weight(s) if s.direction == "bullish" else -_weight(s)
        results.append(r)
    results.sort(key=lambda r: r.score, reverse=True)
    logger.info("本地筛选完成: %d只可用, %d只缺失/不足, %d只触发信号",
                len(pool) - len(missing), len(missing), len(results))
    return results, missing


def _result_line(r: PoolResult) -> str:
    parts = [f"**{s.title}**" for s in r.signals]
    arrow = "看涨" if r.score > 0 else "看跌"
    return f"{r.name} `{r.code}`（净分 {r.score:+.1f}，{arrow}）：" + "；".join(parts)


def build_digest(results: list[PoolResult], scanned: int, top_n: int = 8) -> dict:
    """把筛选结果组装成飞书卡片。"""
    bulls = [r for r in results if r.score > 0][:top_n]
    bears = [r for r in reversed(results) if r.score < 0][:top_n]

    def section(title: str, rows: list[PoolResult]) -> str:
        if not rows:
            return f"**{title}**\n- 无"
        return f"**{title}**\n" + "\n".join(
            f"- {_result_line(r)}" for r in rows)

    elements = [
        {"tag": "markdown", "content":
            f"扫描 {scanned} 只 · 触发信号 {len(results)} 只 · "
            f"看涨 {sum(1 for r in results if r.score > 0)} / "
            f"看跌 {sum(1 for r in results if r.score < 0)}"},
        {"tag": "markdown",
         "content": section("🟢 看涨信号榜（净分Top）", bulls)},
        {"tag": "markdown",
         "content": section("🔴 看跌信号榜（净分绝对值Top）", bears)},
        {"tag": "markdown", "content":
            "**评分说明**：形态识别2.0×置信度 / 分形突破2.0 / 形态+背离1.5 / 均线交叉1.0 / "
            "RSI与异动0.5；净分=看涨-看跌。规则参数多来自外部文献，未经本项目回测验证。"},
        {"tag": "note", "elements": [{"tag": "plain_text",
            "content": "信号筛选=规则触发的事实清单，非投资建议，不构成任何买卖依据"}]},
    ]
    return {
        "header": {"title": {"tag": "plain_text",
                             "content": f"信号筛选速览 · {time.strftime('%Y-%m-%d %H:%M')}"},
                   "template": "blue"},
        "elements": elements,
    }


def run_screening(config: dict, top_n: int = 8, pool_limit: int | None = None,
                  sleep_s: float = 0.25, push: bool = True,
                  local_only: bool = False) -> list[PoolResult]:
    pool = load_pool(pool_limit)
    logger.info("筛选开始: %d 只 (%s)", len(pool), "本地" if local_only else "实时")
    missing: list[str] = []
    if local_only:
        results, missing = screen_local(pool, config)
    else:
        results = screen(pool, config, sleep_s=sleep_s)
    if push:
        card = build_digest(results, scanned=len(pool), top_n=top_n)
        if missing:
            card["elements"].insert(1, {"tag": "markdown",
                "content": f"⚠️ 本地缓存不足/缺失 {len(missing)} 只，未参与本次筛选"})
        feishu_bot.send_card(card["header"]["title"]["content"],
                             [e["content"] for e in card["elements"]
                              if e["tag"] == "markdown"],
                             color="blue")
        logger.info("筛选榜单已推送飞书")
    return results
