#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成筛选股票池：拉取沪深300成分股写入 data/screen_pool.txt。

数据源：中证指数官网（akshare index_stock_cons_csindex，不经过东方财富）。
建议每月重跑一次刷新成分（指数调样在6月/12月）。

用法（仓库根目录）：
    python tools/gen_pool.py            # 默认沪深300
    python tools/gen_pool.py 000905     # 指数代码，如中证500
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

POOL = Path(__file__).resolve().parents[1] / "data" / "screen_pool.txt"


def main() -> int:
    import akshare as ak
    symbol = sys.argv[1] if len(sys.argv) > 1 else "000300"
    for attempt in range(3):
        try:
            df = ak.index_stock_cons_csindex(symbol=symbol)
            break
        except Exception as e:
            print(f"重试 {attempt + 1}/3: {e}")
            time.sleep(3)
    else:
        print("拉取成分股失败")
        return 1
    lines = [f"# 筛选股票池 · 指数{symbol} · {time.strftime('%Y-%m-%d')} 生成"]
    for _, r in df.iterrows():
        lines.append(f"{r['成分券代码']},{r['成分券名称']}")
    POOL.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"已写入 {POOL}（{len(df)} 只）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
