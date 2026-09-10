#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个股分析 CLI：skill 与 agent 的数据入口。

用法（在仓库根目录）：
    python skills/a-stock-pattern-analyst/scripts/analyze_once.py 600519 [300750 ...]

stdout 输出 JSON；退出码 0=有结果，1=参数/数据错误。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO / ".env")

from stock_monitor.bot.analyzer import analyze, build_card  # noqa: E402


def main() -> int:
    codes = [a for a in sys.argv[1:] if a.isdigit() and len(a) == 6]
    if not codes:
        print(json.dumps({"error": "用法: analyze_once.py <6位股票代码> [...]"},
                         ensure_ascii=False))
        return 1
    results = []
    for code in codes:
        try:
            res = analyze(code)
        except Exception as e:
            results.append({"code": code, "error": f"分析异常: {type(e).__name__}"})
            continue
        if res is None:
            results.append({"code": code, "error": "未取到行情数据"})
            continue
        card = build_card(res)
        results.append({
            "code": code,
            "name": res["name"],
            "price": res["price"],
            "pct": res["pct"],
            "snapshot": res["summary"],
            "signals": [{
                "type": s.signal_type, "direction": s.direction,
                "source": s.source, "confidence": round(s.confidence, 3),
                "title": s.title, "detail": s.detail,
            } for s in res["signals"]],
            "card": card,
        })
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
