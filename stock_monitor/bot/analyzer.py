# -*- coding: utf-8 -*-
"""个股分析器：@机器人查询的脑。输入股票代码，输出一张"体检报告"卡片。

无论是否触发信号，都给出基础快照（现价/涨跌/均线/RSI/量能），
再叠加规则引擎与形态识别结果，最后附LLM解读（未配置则用规则文本）。
"""
from __future__ import annotations

from pathlib import Path

import yaml

from ..data.feed import fetch_kline
from ..engine.base import RuleEngine
from ..engine.rules_price import RSIRule
from ..llm.interpreter import interpret
from ..watchlist import load_watchlist

_BASE = Path(__file__).resolve().parents[2]
with open(_BASE / "config" / "watchlist.yaml", encoding="utf-8") as f:
    _CONFIG = yaml.safe_load(f)
_engine = RuleEngine(_CONFIG)


def analyze(code: str) -> dict | None:
    """全量分析一只股票。行情失败返回 None。"""
    kline = fetch_kline(code)
    if kline is None or kline.empty:
        return None

    watchlist = load_watchlist(_CONFIG)  # 每次查询重读，文件新增的代码立刻可查
    name = watchlist.get(code, f"股票{code}")
    close = kline["close"].astype(float)
    last, prev = close.iloc[-1], close.iloc[-2]
    pct = (last / prev - 1) * 100

    ma5 = close.rolling(5).mean().iloc[-1]
    ma20 = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else None
    vol = kline["volume"].astype(float)
    vol_ratio = vol.iloc[-1] / vol.iloc[:-1].tail(5).mean()
    rsi = RSIRule._rsi(close, 14).iloc[-1]

    signals = _engine.scan(code, name, kline)

    if len(close) >= 30:
        chg30 = (last / close.iloc[-31] - 1) * 100
    else:
        chg30 = (last / close.iloc[0] - 1) * 100

    trend = "多头排列" if ma5 > ma20 else "空头排列"
    summary = (
        f"现价 **{last:.2f}**（{pct:+.2f}%），MA5={ma5:.2f} / MA20={ma20:.2f}，{trend}；"
        f"量能为前5日均量的 **{vol_ratio:.1f}倍**，RSI14={rsi:.0f}，近30日{chg30:+.1f}%。"
    )
    return {
        "code": code, "name": name, "price": round(float(last), 2),
        "pct": round(float(pct), 2), "summary": summary,
        "signals": signals, "kline": kline,
    }


def build_card(res: dict) -> dict:
    """把 analyze() 结果组装成飞书 interactive 卡片(JSON对象)。"""
    up = res["pct"] >= 0
    color = "red" if up else "green"  # A股习惯红涨绿跌
    elements = [
        {"tag": "markdown", "content": res["summary"]},
    ]

    if res["signals"]:
        lines = []
        for s in res["signals"]:
            interp = interpret(s) if s.source == "pattern" else s.detail
            emoji = {"bullish": "🟢", "bearish": "🔴"}.get(s.direction, "⚪")
            lines.append(f"{emoji} **{s.title}**（置信度 {s.confidence:.0%}）\n{interp}")
        elements.append({"tag": "hr"})
        elements.append({"tag": "markdown",
                         "content": "**触发信号**\n" + "\n\n".join(lines)})
    else:
        elements.append({"tag": "markdown",
                         "content": "**当前无触发信号**：均线、量能、RSI均在正常区间，未识别到经典形态。"})

    note_text = "数据仅供参考学习，不构成投资建议 · 发送股票代码(如 600519)可再次查询"
    elements.append({"tag": "note",
                     "elements": [{"tag": "plain_text", "content": note_text}]})
    return {
        "header": {
            "title": {"tag": "plain_text",
                      "content": f"{res['name']} {res['code']} · {res['price']} ({res['pct']:+.2f}%)"},
            "template": color,
        },
        "elements": elements,
    }
