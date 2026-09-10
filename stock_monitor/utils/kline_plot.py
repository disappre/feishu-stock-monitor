# -*- coding: utf-8 -*-
"""迷你K线图：无 mplfinance 依赖，matplotlib 手绘蜡烛图，输出 PNG 供飞书卡片使用。"""
from __future__ import annotations

from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")  # 服务器无显示环境
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

# 中文字体：Windows 用微软雅黑，其他平台回退 SimHei/系统黑体
for _font in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
              "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"):
    try:
        font_manager.fontManager.addfont(_font)
        plt.rcParams["font.sans-serif"] = [font_manager.FontProperties(fname=_font).get_name()]
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False

UP = "#e03131"    # A股习惯：涨红
DOWN = "#2f9e44"  # 跌绿
_CACHE = Path(__file__).resolve().parents[2] / "data" / "plots"


def plot_kline(kline: pd.DataFrame, code: str, last_n: int = 30,
               title: str = "") -> str | None:
    """绘制最近 last_n 根K线，返回 PNG 路径；绘图失败返回 None（不阻断推送）。"""
    try:
        df = kline.tail(last_n).reset_index(drop=True)
        _CACHE.mkdir(parents=True, exist_ok=True)
        path = _CACHE / f"{code}_{df['date'].iloc[-1]}.png"

        fig, ax = plt.subplots(figsize=(4.2, 2.4), dpi=150)
        for i, row in df.iterrows():
            color = UP if row["close"] >= row["open"] else DOWN
            ax.plot([i, i], [row["low"], row["high"]],
                    color=color, linewidth=0.8, zorder=1)
            ax.add_patch(plt.Rectangle(
                (i - 0.32, min(row["open"], row["close"])), 0.64,
                max(abs(row["close"] - row["open"]), 1e-9),
                facecolor=color, edgecolor=color, zorder=2))
        ax.set_xlim(-1, len(df))
        ax.set_ylim(df["low"].min() * 0.995, df["high"].max() * 1.005)
        ax.set_title(title or str(code), fontsize=9)
        ax.tick_params(labelsize=6)
        ax.set_xticks([])
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", linestyle="--", linewidth=0.4, alpha=0.4)
        fig.tight_layout()
        fig.savefig(path)
        plt.close(fig)
        return str(path)
    except Exception:
        return None
