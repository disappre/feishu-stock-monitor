# -*- coding: utf-8 -*-
"""自选股清单加载：支持 yaml 内联清单 + 文本文件导入（东方财富选股导出）。

文件格式（data/*.txt，每行一条，支持注释与"代码,名称"两种写法）：
    # 东财选股导出 2026-09-08
    600519,贵州茅台
    300750

每次扫描前重新读取，改完文件即生效，无需重启程序。
"""
from __future__ import annotations

import logging
from pathlib import Path

from .data.feed import fetch_name

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
_name_cache: dict[str, str] = {}


def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.split("#", 1)[0].strip()
    if not line:
        return None
    parts = [p.strip() for p in line.replace("，", ",").split(",")]
    code = parts[0]
    if not (code.isdigit() and len(code) == 6):
        logger.warning("跳过非法代码行: %r", parts[0])
        return None
    return code, parts[1] if len(parts) > 1 else ""


def load_watchlist(config: dict) -> dict[str, str]:
    """返回 {code: name}。内联清单与文件清单合并，文件优先级更高（方便覆盖）。"""
    merged: dict[str, str] = {}
    for w in config.get("watchlist", []):
        merged[str(w["code"])] = w.get("name", "")
    for rel in config.get("watchlist_files", []):
        path = BASE_DIR / rel
        if not path.exists():
            logger.warning("选股清单文件不存在: %s", path)
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            parsed = _parse_line(raw)
            if parsed:
                merged[parsed[0]] = parsed[1]
    # 补齐缺失名称：先用内存缓存，未命中再查行情接口
    missing = [c for c, n in merged.items() if not n]
    for code in missing:
        if code in _name_cache:
            merged[code] = _name_cache[code]
            continue
        name = fetch_name(code)
        _name_cache[code] = name
        merged[code] = name
    return merged
