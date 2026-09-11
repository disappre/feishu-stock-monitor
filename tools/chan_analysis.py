#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""缠论结构分析：K线包含合并 + 笔 + 线段 + 中枢，一图呈现 + 全局摘要。

数据源：本地SQLite日线（daily/qfq），与监测系统同源。
理论实现：
- 分型/笔/中枢/K线包含合并：czsc库（Rust核心，6k stars，缠论事实标准）
- 线段：简化"段破坏法"——段方向由首笔决定，反向笔端点突破段起点即段终结
  （非特征序列完整算法，标注【待验证】，见 knowledge/patterns/chan-theory.md）

用法（仓库根目录）：
    python tools/chan_analysis.py 600519              # 单股：保存结构图PNG+打印摘要
    python tools/chan_analysis.py 600519 --push       # 单股：图+摘要推飞书
    python tools/chan_analysis.py --pool --limit 30 --push   # 全局：股票池缠论结构摘要推飞书
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

for _font in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
    try:
        font_manager.fontManager.addfont(_font)
        plt.rcParams["font.sans-serif"] = [
            font_manager.FontProperties(fname=_font).get_name()]
        break
    except Exception:
        continue
plt.rcParams["axes.unicode_minus"] = False

import pandas as pd  # noqa: E402
from czsc import CZSC, Direction, Freq, RawBar  # noqa: E402

from stock_monitor.data import store  # noqa: E402

PLOTS = REPO / "data" / "plots"
UP, DOWN = "#e03131", "#2f9e44"   # A股习惯：红涨绿跌


@dataclass
class ChanStructure:
    code: str
    name: str
    c: CZSC                     # czsc对象(笔/中枢)
    segments: list              # 简化线段 [(start_dt, start_px, end_dt, end_px, dir)]
    df: pd.DataFrame = None     # 分析所用的原始K线(绘图与CZSC严格同源)

    @property
    def last_bi(self):
        return self.c.bi_list[-1]

    @property
    def last_zs(self):
        return self.c.zs_list[-1] if self.c.zs_list else None

    def summary(self) -> str:
        bi = self.last_bi
        last_px = self.c.bars_raw[-1].close
        lines = [f"**{self.name} {self.code}**（{len(self.c.bi_list)}笔/"
                 f"{len(self.segments)}段/{len(self.c.zs_list)}中枢）",
                 f"- 当前笔：{bi.direction}，{str(bi.fx_a.dt)[:10]} {bi.fx_a.fx:.2f} → "
                 f"{str(bi.fx_b.dt)[:10]} {bi.fx_b.fx:.2f}"]
        if self.segments:
            s = self.segments[-1]
            d = "向上" if s[4] == 1 else "向下"
            lines.append(f"- 当前段：{d}，{str(s[0])[:10]} {s[1]:.2f} → {str(s[2])[:10]} {s[3]:.2f}")
        zs = self.last_zs
        if zs:
            pos = ("中枢上方" if last_px > zs.gg else
                   "中枢下方" if last_px < zs.dd else "中枢内部")
            lines.append(f"- 末中枢：{str(zs.sdt)[:10]}~{str(zs.edt)[:10]} "
                         f"GG {zs.gg:.2f} / DD {zs.dd:.2f}，现价{last_px:.2f}位于{pos}")
        # 背驰与三类买卖点（简化实现，只报最近5笔内的信号——旧结构信号无操作意义）
        recent_n = len(self.c.bi_list) - 5
        divs = [d for d in detect_divergence(self.c.bi_list, self.df) if d[0] >= recent_n]
        if divs:
            idx, kind, px, ratio = divs[-1]
            lines.append(f"- ⚠️ {kind}：第{idx}笔动能比{ratio}（MACD柱面积萎缩）")
        pts = [p for p in detect_3rd_points(self.c.zs_list, self.c.bi_list,
                                            last_px=self.c.bars_raw[-1].close,
                                            max_age_bars=5) if p[0] >= recent_n]
        pts12 = [p for p in detect_1st_2nd_points(self.c.zs_list, self.c.bi_list,
                                                  self.segments, self.df)
                 if p[0] >= recent_n]
        pts = sorted(pts + pts12, key=lambda x: x[0])
        if pts:
            idx, kind, px = pts[-1]
            lines.append(f"- ⚠️ {kind}候选：点位{px:.2f}（{str(self.c.bi_list[idx].fx_b.dt)[:10]}）")
        return "\n".join(lines)


def macd(close: pd.Series, fast=12, slow=26, signal=9):
    """标准MACD(12/26/9)，返回(dif, dea, hist)。"""
    ema_f = close.ewm(span=fast, adjust=False).mean()
    ema_s = close.ewm(span=slow, adjust=False).mean()
    dif = ema_f - ema_s
    dea = dif.ewm(span=signal, adjust=False).mean()
    return dif, dea, (dif - dea) * 2


def detect_divergence(bi_list, df: pd.DataFrame, lookback: int = 6) -> list:
    """简化背驰检测：最近lookback笔内，同向相邻两笔对比——
    价格创新高/新低而MACD柱面积(笔区间内|hist|和)萎缩 → 顶/底背驰。
    返回 [(bi_index, '顶背驰'/'底背驰', price, area_ratio), ...]【待验证】
    """
    if len(bi_list) < 3 or df is None or len(df) < 40:
        return []
    _, _, hist = macd(df["close"].astype(float))
    hist.index = df["date"].astype(str).str.slice(0, 10).tolist()
    out = []
    recent = bi_list[-lookback:]
    for a, b in zip(recent, recent[1:]):
        if a.direction != b.direction:
            continue
        up = a.direction == Direction.Up
        # 价格是否创新极值
        new_extreme = b.fx_b.fx > a.fx_b.fx if up else b.fx_b.fx < a.fx_b.fx
        if not new_extreme:
            continue
        # 两笔区间内MACD柱面积
        def area(bi):
            d0, d1 = str(bi.fx_a.dt)[:10], str(bi.fx_b.dt)[:10]
            seg = hist.loc[d0:d1]
            return float(seg.abs().sum()) if len(seg) else 0.0
        aa, ab = area(a), area(b)
        if aa <= 0:
            continue
        if ab < aa * 0.8:                       # 动能萎缩20%以上
            idx = bi_list.index(b)
            out.append((idx, "顶背驰" if up else "底背驰",
                        b.fx_b.fx, round(ab / aa, 2)))
    return out


def detect_1st_2nd_points(zs_list, bi_list, segments, df: pd.DataFrame) -> list:
    """一买/一卖 + 二买/二卖检测（笔级近似次级别）。

    一买：下跌段的末中枢被跌破后，跌破段终点相对前一同向段低点出现底背驰
          （价格新低 + MACD柱面积萎缩）——熊市结束最早信号。
    二买：一买后首次回调笔低点不破一买低点。
          变体"最强二买"：一买后的上涨笔直接突破末中枢GG且后续不回踩中枢。
    卖点镜像。

    返回 [(bi_index, kind, price), ...]，kind ∈ {一买,二买,最强二买,一卖,二卖,最强二卖}【待验证】
    """
    if not bi_list or not segments or df is None:
        return []
    _, _, hist = macd(df["close"].astype(float))
    hist.index = df["date"].astype(str).str.slice(0, 10).tolist()

    def area(d0, d1):
        seg = hist.loc[str(d0)[:10]: str(d1)[:10]]
        return float(seg.abs().sum()) if len(seg) else 0.0

    out = []
    # 逐段扫描：找"离开中枢且创新极值且背驰"的段
    seg_edges = []  # (seg_idx, start_bi_dt, end_bi_dt, end_px, dir, 起点dt)
    bi_dates = [str(b.fx_a.dt)[:10] for b in bi_list]
    for si, (sdt, spx, edt, epx, sdir) in enumerate(segments):
        seg_edges.append((si, str(sdt)[:10], str(edt)[:10], epx, sdir, spx))

    for si in range(1, len(seg_edges)):
        cur = seg_edges[si]
        prev = seg_edges[si - 1]
        if cur[4] != prev[4] or cur[4] != -1:
            continue  # 只看向下段（一买）；一卖在镜像分支
        # 前一同向段（再往前一个向下段）
        if si >= 2 and seg_edges[si - 2][4] == -1:
            ref = seg_edges[si - 2]
        else:
            ref = None
        # 末中枢：与该段起点重叠的中枢
        zs = None
        for z in zs_list:
            if str(z.sdt)[:10] <= cur[1] <= str(z.edt)[:10]:
                zs = z
        new_low = cur[3] < prev[3] if prev else True
        # 跌破中枢确认（段的终点低于中枢DD或起点在低位区间）
        broke_zs = (zs is None) or (cur[3] < zs.dd)
        if not (new_low and broke_zs):
            continue
        # 背驰：当前段MACD面积 vs 参考段
        a_cur = area(cur[1], cur[2])
        a_ref = area(ref[1], ref[2]) if ref else 0.0
        if a_ref <= 0 or a_cur >= a_ref * 0.85:
            continue  # 无背驰(动能未萎缩)
        # 一买：当前段终点(低点)即一买点位
        end_dt = cur[2]
        try:
            bi_idx = next(i for i, d in enumerate(bi_dates) if d >= end_dt)
        except StopIteration:
            continue
        out.append((bi_idx, "一买", cur[3]))
        # 二买：一买后首次回调不破一买低点
        for j in range(bi_idx, len(bi_list) - 1):
            bi = bi_list[j]
            if bi.direction == Direction.Down and bi.fx_b.fx < cur[3]:
                break                      # 破低 → 一买失效
            if bi.direction == Direction.Up and j > bi_idx:
                nxt = bi_list[j + 1] if j + 1 < len(bi_list) else None
                if nxt and nxt.direction == Direction.Down and nxt.fx_b.fx > cur[3]:
                    out.append((bi_list.index(nxt), "二买", nxt.fx_b.fx))
                    break
        # 最强二买：上涨笔直接突破末中枢GG
        if zs is not None:
            for j in range(bi_idx, len(bi_list)):
                bi = bi_list[j]
                if bi.direction == Direction.Up and bi.fx_b.fx > zs.gg:
                    out.append((j, "最强二买", bi.fx_b.fx))
                    break
    # 一卖/二卖镜像：向上段新高+顶背驰
    for si in range(1, len(seg_edges)):
        cur, prev = seg_edges[si], seg_edges[si - 1]
        if cur[4] != 1:
            continue
        ref = seg_edges[si - 2] if si >= 2 and seg_edges[si - 2][4] == 1 else None
        zs = None
        for z in zs_list:
            if str(z.sdt)[:10] <= cur[1] <= str(z.edt)[:10]:
                zs = z
        new_high = cur[3] > prev[3] if prev else True
        broke_zs = (zs is None) or (cur[3] > zs.gg)
        if not (new_high and broke_zs):
            continue
        a_cur = area(cur[1], cur[2])
        a_ref = area(ref[1], ref[2]) if ref else 0.0
        if a_ref <= 0 or a_cur >= a_ref * 0.85:
            continue
        end_dt = cur[2]
        try:
            bi_idx = next(i for i, d in enumerate(bi_dates) if d >= end_dt)
        except StopIteration:
            continue
        out.append((bi_idx, "一卖", cur[3]))
        for j in range(bi_idx, len(bi_list) - 1):
            bi = bi_list[j]
            if bi.direction == Direction.Up and bi.fx_b.fx > cur[3]:
                break
            if bi.direction == Direction.Down and j > bi_idx:
                nxt = bi_list[j + 1] if j + 1 < len(bi_list) else None
                if nxt and nxt.direction == Direction.Up and nxt.fx_b.fx < cur[3]:
                    out.append((bi_list.index(nxt), "二卖", nxt.fx_b.fx))
                    break
        if zs is not None:
            for j in range(bi_idx, len(bi_list)):
                bi = bi_list[j]
                if bi.direction == Direction.Down and bi.fx_b.fx < zs.dd:
                    out.append((j, "最强二卖", bi.fx_b.fx))
                    break
    return out


def detect_3rd_points(zs_list, bi_list, last_px: float | None = None,
                      max_age_bars: int | None = None) -> list:
    """三买/三卖检测（笔级近似次级别）：
    三买 = 向上笔离开中枢(端点>GG)后，回调笔低点不碰中枢上沿GG → 回调结束点
    三卖 = 向下笔离开中枢(端点<DD)后，反弹笔高点不碰中枢下沿DD → 反弹结束点

    失效机制（2026-09-11用户指正后新增）：
    - 价格回到任一中枢区间[DD,GG]内 → 信号失效（三卖后价格回中枢=反弹延续而非反转；
      含用户指出的场景：旧中枢的三卖信号，价格已回到后续中枢内震荡）
    - max_age_bars: 信号笔距当前超过N笔视为过期（旧结构无操作意义）

    返回 [(bi_index, '三买'/'三卖', price), ...]；失效/过期信号自动剔除【待验证】
    """
    out = []
    if not zs_list or not bi_list:
        return out
    n = len(bi_list)
    # 现价若处于任一中枢区间内，所有三买/三卖信号一律失效
    in_any_zs = False
    if last_px is not None:
        for z in zs_list:
            if z.dd <= last_px <= z.gg:
                in_any_zs = True
                break
    if in_any_zs:
        return out
    for zs in zs_list:
        # 找中枢结束之后的笔
        for i, bi in enumerate(bi_list[:-1]):
            if str(bi.fx_a.dt)[:10] <= str(zs.edt)[:10]:
                continue
            nxt = bi_list[i + 1]
            idx = i + 1
            if max_age_bars is not None and n - 1 - idx > max_age_bars:
                break   # 信号过期
            if bi.direction == Direction.Up and bi.fx_b.fx > zs.gg \
                    and nxt.direction == Direction.Down and nxt.fx_b.fx > zs.gg:
                out.append((idx, "三买", nxt.fx_b.fx))
            elif bi.direction == Direction.Down and bi.fx_b.fx < zs.dd \
                    and nxt.direction == Direction.Up and nxt.fx_b.fx < zs.dd:
                out.append((idx, "三卖", nxt.fx_b.fx))
            break   # 每个中枢只看其后的第一组离开-回抽
    return out


def build_segments(bi_list) -> list:
    """简化线段：特征序列分型的近似——段内反向笔端点必须逐个刷新极值
    （向上段的向下笔低点逐个抬升；向下段的向上笔高点逐个降低）；
    一旦反向笔端点劣化，段终结于段内极值点，新段自极值点反向开始。
    至少3笔成段；非特征序列完整算法，标注【待验证】。

    返回 [(start_dt, start_px, end_dt, end_px, dir(1/-1)), ...]
    """
    segments = []
    if not bi_list:
        return segments

    def is_up(b):
        # 注意: 本版czsc的str(direction)返回中文"向上/向下"，必须用枚举比较
        return b.direction == Direction.Up

    def extreme(sub, up: bool):
        """段内极值点(笔端点)：向上段取最高，向下段取最低。返回(dt, px, bi)。"""
        key = (lambda b: b.fx_b.fx if is_up(b) else b.fx_a.fx) if up \
            else (lambda b: b.fx_b.fx if not is_up(b) else b.fx_a.fx)
        bi = (max if up else min)(sub, key=key)
        return bi.fx_b.dt, bi.fx_b.fx, bi

    seg_dir = 1 if is_up(bi_list[0]) else -1
    start_dt, start_px = bi_list[0].fx_a.dt, bi_list[0].fx_a.fx
    start_idx = 0
    # 反向笔端点的"极值约束"：上段中=最近向下笔的低点(须抬升)，初始为段起点
    last_counter = start_px
    for i, bi in enumerate(bi_list[1:], 1):
        if (1 if is_up(bi) else -1) == seg_dir:
            continue
        broke = (bi.fx_b.fx < last_counter) if seg_dir == 1 else (bi.fx_b.fx > last_counter)
        if broke and i - start_idx >= 3:
            end_dt, end_px, ext_bi = extreme(bi_list[start_idx:i], seg_dir == 1)
            segments.append((start_dt, start_px, end_dt, end_px, seg_dir))
            start_dt, start_px = end_dt, end_px
            # 新段从极值笔之后开始（极值笔本体属于旧段，其端点是新段起点）
            start_idx = bi_list.index(ext_bi, start_idx, i) + 1
            seg_dir = -seg_dir
            last_counter = end_px            # 新段的初始约束=新段起点极值
        else:
            last_counter = bi.fx_b.fx        # 未破坏：约束推进到最新反向笔端点
    if bi_list[start_idx:]:
        end_dt, end_px, _ = extreme(bi_list[start_idx:], seg_dir == 1)
        segments.append((start_dt, start_px, end_dt, end_px, seg_dir))
    # 清除退化段（起止点相同，连续快速破坏时产生，不携带结构信息）
    segments = [s for s in segments if (s[0], s[1]) != (s[2], s[3])]
    return segments


def analyze(code: str, name: str = None, rows: int = 500) -> ChanStructure | None:
    df = store.load_recent(code, rows)
    if df is None or len(df) < 60:
        return None
    name = name or code
    bars = [RawBar(symbol=code, dt=pd.Timestamp(r["date"]), freq=Freq.D,
                   open=r["open"], close=r["close"], high=r["high"], low=r["low"],
                   vol=r["volume"], amount=r["amount"]) for _, r in df.iterrows()]
    c = CZSC(bars)
    return ChanStructure(code=code, name=name, c=c,
                         segments=build_segments(c.bi_list), df=df)


def merge_groups(bars_df: pd.DataFrame) -> list:
    """K线包含关系分组（标准缠论合并规则，仅分组不改画原始K线）。

    向上方向合并取 high=max(highs), low=max(lows)；
    向下方向合并取 high=min(highs), low=min(lows)。
    返回 [(start_i, end_i, high, low), ...]；单根K线也是一个组。
    """
    groups = []
    if bars_df.empty:
        return groups
    highs = bars_df["high"].to_numpy(float)
    lows = bars_df["low"].to_numpy(float)
    closes = bars_df["close"].to_numpy(float)
    opens = bars_df["open"].to_numpy(float)

    def group_contains(cur, i):
        """第i根K线与当前组合并后的区间存在包含关系"""
        return (cur["high"] >= highs[i] and cur["low"] <= lows[i]) or \
               (highs[i] >= cur["high"] and lows[i] <= cur["low"])

    # 第一组
    cur = {"s": 0, "e": 0, "high": highs[0], "low": lows[0],
           "up": closes[0] >= opens[0]}
    for i in range(1, len(bars_df)):
        if group_contains(cur, i):
            # 与当前组包含 → 并入（方向决定取值：向上高高，向下低低）
            if cur["up"]:
                cur["high"] = max(cur["high"], highs[i])
                cur["low"] = max(cur["low"], lows[i])
            else:
                cur["high"] = min(cur["high"], highs[i])
                cur["low"] = min(cur["low"], lows[i])
            cur["e"] = i
        else:
            groups.append((cur["s"], cur["e"], cur["high"], cur["low"]))
            # 新组方向：对前组创新高→向上，创新低→向下，否则按实体颜色
            if highs[i] > cur["high"]:
                up = True
            elif lows[i] < cur["low"]:
                up = False
            else:
                up = closes[i] >= opens[i]
            cur = {"s": i, "e": i, "high": highs[i], "low": lows[i], "up": up}
    groups.append((cur["s"], cur["e"], cur["high"], cur["low"]))
    return groups


def plot_structure(cs: ChanStructure, out: Path = None) -> Path:
    """缠论全要素结构图：K线+合并框+分型+笔+线段+中枢+MACD背驰+六类买卖点。

    布局：上方主图（价格结构，占2/3高度），下方副图（MACD柱+背驰标注，占1/3）。
    """
    c = cs.c
    df = cs.df if cs.df is not None else store.load_recent(cs.code)
    n = len(df)
    dates = [str(d)[:10] for d in df["date"]]
    date_index = {d: i for i, d in enumerate(dates)}

    def x_of(dt):
        key = str(dt)[:10]
        if key in date_index:
            return date_index[key]
        import bisect
        keys = sorted(date_index)
        j = bisect.bisect_left(keys, key)
        return date_index[keys[min(j, len(keys) - 1)]]

    fig, (ax, axm) = plt.subplots(
        2, 1, figsize=(14, 9), dpi=130, sharex=True,
        gridspec_kw={"height_ratios": [2.6, 1.0]})

    # ── 主图：原始K线 ──
    for i, (_, r) in enumerate(df.iterrows()):
        color = UP if r["close"] >= r["open"] else DOWN
        ax.plot([i, i], [r["low"], r["high"]], color=color, linewidth=0.6,
                zorder=1, alpha=0.85)
        ax.add_patch(plt.Rectangle((i - 0.34, min(r["open"], r["close"])), 0.68,
                                   max(abs(r["close"] - r["open"]), 1e-9),
                                   facecolor=color, edgecolor=color, zorder=2, alpha=0.85))

    # 包含合并框（可合并成最小单位的K线组）
    groups = merge_groups(df)
    merged_units = [g for g in groups if g[1] > g[0]]
    for (s, e, hi, lo) in merged_units:
        ax.add_patch(Rectangle((s - 0.45, lo), (e - s) + 0.9, max(hi - lo, 1e-9),
                               fill=False, edgecolor="#495057", linewidth=0.8,
                               zorder=3, alpha=0.6))

    # 分型标记：顶分型▲(预示短期下跌)/底分型▼(预示短期上涨)——只画构成笔端点的分型
    for bi in c.bi_list:
        for fx, mark in ((bi.fx_a, "a"), (bi.fx_b, "b")):
            x = x_of(fx.dt)
            if mark == "a" and bi.fx_a == bi.fx_b:
                continue
        # 用fx.mark类型判断顶底
    for fx in c.fx_list:
        x = x_of(fx.dt)
        is_top = "顶" in str(fx.mark) or "g" in str(fx.mark).lower()
        color = "#e8590c" if is_top else "#0b7285"
        # 顶分型标记在上方, 底分型在下方
        if is_top:
            ax.annotate("▲", (x, fx.fx), xytext=(0, 7), textcoords="offset points",
                        ha="center", fontsize=4.5, color=color, zorder=6)
        else:
            ax.annotate("▼", (x, fx.fx), xytext=(0, -11), textcoords="offset points",
                        ha="center", fontsize=4.5, color=color, zorder=6)

    # 笔（蓝色细线）
    for bi in c.bi_list:
        ax.plot([x_of(bi.fx_a.dt), x_of(bi.fx_b.dt)],
                [bi.fx_a.fx, bi.fx_b.fx],
                color="#1c7ed6", linewidth=1.0, zorder=4, alpha=0.9)
    # 线段（橙色粗线）
    for (sdt, spx, edt, epx, sdir) in cs.segments:
        ax.plot([x_of(sdt), x_of(edt)], [spx, epx], color="#f76707",
                linewidth=2.4, zorder=5, alpha=0.95)
    # 中枢（紫色半透明矩形 + GG/DD虚线）
    for zs in c.zs_list:
        x0, x1 = x_of(zs.sdt), x_of(zs.edt)
        ax.add_patch(Rectangle((x0, min(zs.zd, zs.zg)), max(x1 - x0, 1),
                               abs(zs.zg - zs.zd) or 0.5,
                               facecolor="#748ffc", alpha=0.25, zorder=0))
        ax.hlines([zs.gg, zs.dd], x0, x1, colors="#4263eb",
                  linestyles=":", linewidth=0.9, zorder=0, alpha=0.8)

    # 六类买卖点标记（主图大标签）
    pts = (detect_1st_2nd_points(c.zs_list, c.bi_list, cs.segments, df)
           + detect_3rd_points(c.zs_list, c.bi_list))
    KIND_STYLE = {  # kind -> (买/卖, 颜色)
        "一买": ("b", "#2f9e44"), "二买": ("b", "#40c057"), "最强二买": ("b", "#69db7c"),
        "三买": ("b", "#37b24d"), "一卖": ("s", "#c92a2a"), "二卖": ("s", "#e03131"),
        "最强二卖": ("s", "#ff6b6b"), "三卖": ("s", "#f03e3e")}
    seen_kinds = set()
    for idx, kind, px in pts:
        if idx >= len(c.bi_list):
            continue
        side, color = KIND_STYLE.get(kind, ("b", "#495057"))
        x = x_of(c.bi_list[idx].fx_b.dt)
        if side == "b":   # 买点：绿色标记在下方
            ax.annotate(kind, (x, px), xytext=(0, -16), textcoords="offset points",
                        ha="center", fontsize=7, fontweight="bold", color="white",
                        bbox=dict(boxstyle="round,pad=0.22", fc=color, ec="none",
                                  alpha=0.92), zorder=8)
        else:             # 卖点：红色标记在上方
            ax.annotate(kind, (x, px), xytext=(0, 12), textcoords="offset points",
                        ha="center", fontsize=7, fontweight="bold", color="white",
                        bbox=dict(boxstyle="round,pad=0.22", fc=color, ec="none",
                                  alpha=0.92), zorder=8)
        seen_kinds.add(kind)

    # ── 副图：MACD + 背驰标注 ──
    dif, dea, hist = macd(df["close"].astype(float))
    hist_idx = df["close"].astype(float).index
    axm.bar(range(n), hist, width=0.7,
            color=[UP if v >= 0 else DOWN for v in hist], alpha=0.75)
    axm.plot(range(n), dif, color="#1c7ed6", linewidth=0.9, label="DIF")
    axm.plot(range(n), dea, color="#f76707", linewidth=0.9, label="DEA")
    axm.axhline(0, color="#868e96", linewidth=0.5)
    # 背驰标注：笔级背驰(副图橙色圆点+连线)与段级背驰(一买/一卖的组成,主图已有标签)
    for idx, kind, px, ratio in detect_divergence(c.bi_list, df):
        if idx >= len(c.bi_list):
            continue
        bi = c.bi_list[idx]
        x = x_of(bi.fx_b.dt)
        axm.annotate(f"{kind}({ratio})", (x, hist.iloc[x] if x < len(hist) else 0),
                     xytext=(0, 14 if "顶" in kind else -18), textcoords="offset points",
                     ha="center", fontsize=6.5, color="#e8590c", fontweight="bold",
                     arrowprops=dict(arrowstyle="->", color="#e8590c", lw=0.7))

    # ── 图例（主图）──
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    legend_main = [Line2D([0], [0], color="#e03131", lw=6, label="原始K线"),
                   Line2D([0], [0], color="#495057", lw=1.4,
                          label=f"包含合并框({len(merged_units)}组)"),
                   Line2D([0], [0], marker="^", color="none", markerfacecolor="#e8590c",
                          markersize=6, label="顶分型▲/底分型▼"),
                   Line2D([0], [0], color="#1c7ed6", lw=1.5, label="笔"),
                   Line2D([0], [0], color="#f76707", lw=2.5, label="线段"),
                   Patch(facecolor="#748ffc", alpha=0.4, label="中枢[ZD,ZG]+GG/DD虚线")]
    # 买卖点图例只显示实际出现的类别
    for kind in ["一买", "二买", "三买", "一卖", "二卖", "三卖"]:
        if kind in seen_kinds or f"最强{kind}" in seen_kinds:
            side, color = KIND_STYLE.get(kind, ("b", "#495057"))
            legend_main.append(Patch(facecolor=color, label=f"{kind}点"))
    ax.legend(handles=legend_main, loc="upper left", fontsize=7.5,
              framealpha=0.85, ncol=2)
    axm.legend(loc="upper left", fontsize=7, framealpha=0.85)

    step = max(1, n // 12)
    axm.set_xticks(range(0, n, step))
    axm.set_xticklabels([dates[i] for i in range(0, n, step)], fontsize=7, rotation=30)
    ax.set_title(f"{cs.name} {cs.code} 缠论全要素结构图 · {n}根K/"
                 f"{len(groups)}合并单位/{len(c.fx_list)}分型/{len(c.bi_list)}笔/"
                 f"{len(cs.segments)}段/{len(c.zs_list)}中枢/{len(pts)}买卖点", fontsize=11)
    ax.grid(axis="y", linestyle="--", linewidth=0.4, alpha=0.4)
    axm.grid(axis="y", linestyle="--", linewidth=0.4, alpha=0.3)
    ax.spines[["top", "right"]].set_visible(False)
    axm.spines[["top", "right"]].set_visible(False)
    axm.set_ylabel("MACD", fontsize=8)
    fig.tight_layout()
    PLOTS.mkdir(parents=True, exist_ok=True)
    out = out or (PLOTS / f"chan_{cs.code}_{datetime.now():%Y%m%d}.png")
    fig.savefig(out)
    plt.close(fig)
    return out


def push_to_feishu(cs: ChanStructure, png: Path):
    from dotenv import load_dotenv
    load_dotenv(REPO / ".env")
    from stock_monitor.notify import feishu_bot
    image_key = feishu_bot.upload_image(str(png))
    feishu_bot.send_card(f"{cs.name} {cs.code} · 缠论结构分析",
                         [cs.summary(),
                          "**说明**：线段为段破坏法简化实现（非特征序列完整算法）；"
                          "仅供学习研究，不构成投资建议"],
                         color="blue", image_key=image_key)


def load_names() -> dict:
    names = {}
    for line in (REPO / "data" / "screen_pool.txt").read_text(encoding="utf-8").splitlines():
        parts = [p.strip() for p in line.split("#", 1)[0].split(",")]
        if len(parts) >= 2 and parts[0].isdigit() and len(parts[0]) == 6:
            names[parts[0]] = parts[1]
    return names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("code", nargs="?", help="6位股票代码（单股模式）")
    ap.add_argument("--pool", action="store_true", help="全局模式：股票池缠论结构摘要")
    ap.add_argument("--sub", action="store_true",
                    help="全局模式下，对'日线中枢内'的股票附加30分钟次级别判断")
    ap.add_argument("--limit", type=int, default=30, help="全局模式取池前N只")
    ap.add_argument("--push", action="store_true", help="推送飞书")
    args = ap.parse_args()

    if args.pool:
        names = load_names()
        rows = []
        for code, name in list(names.items())[: args.limit]:
            cs = analyze(code, name)
            if cs is None:
                continue
            bi = cs.last_bi
            zs = cs.last_zs
            last_px = cs.c.bars_raw[-1].close
            pos = ("中枢上" if zs and last_px > zs.gg else
                   "中枢下" if zs and last_px < zs.dd else "中枢内" if zs else "无中枢")
            seg = cs.segments[-1] if cs.segments else None
            # 背驰/一二三类买卖点标记（三买卖点带失效校验+有效期）
            tag = ""
            recent_n = len(cs.c.bi_list) - 5
            pts = [p for p in detect_3rd_points(cs.c.zs_list, cs.c.bi_list,
                                                 last_px=last_px, max_age_bars=5) +
                   detect_1st_2nd_points(cs.c.zs_list, cs.c.bi_list, cs.segments, cs.df)
                   if p[0] >= recent_n]
            if pts:
                tag = "⚠️" + "/".join(sorted({p[1] for p in pts}))
            divs = detect_divergence(cs.c.bi_list, cs.df)
            if divs and divs[-1][0] >= recent_n:
                tag += f"⚠️{divs[-1][1]}"
            rows.append((code, name, str(bi.direction), pos,
                         "上段" if seg and seg[4] == 1 else "下段", last_px, tag))
        # 次级别判断：日线中枢内的股票，方向下沉到30分钟结构
        if args.sub:
            from stock_monitor.engine.sublevel import analyze_sublevel
            extra = []
            for r in rows:
                if r[3] == "中枢内":
                    sub = analyze_sublevel(r[0])
                    if sub and sub.verdict != "中性":
                        extra.append((r, sub))
            if extra:
                lines.append("\n**次级别(30分钟)判断——日线中枢内个股：**")
                for (r, sub) in extra:
                    lines.append(f"- {r[1]} `{r[0]}`（现价{r[5]:.2f}）：{sub.detail}")
        lines = [f"**缠论全局摘要**（{len(rows)}只，笔方向/中枢位置/段方向/结构信号）"]
        for r in rows:
            lines.append(f"- {r[1]} `{r[0]}`：笔{r[2][-2:]}｜{r[3]}｜{r[4]}｜{r[5]:.2f} {r[6]}")
        print("\n".join(lines))
        if args.push:
            from dotenv import load_dotenv
            load_dotenv(REPO / ".env")
            from stock_monitor.notify import feishu_bot
            feishu_bot.send_card("缠论全局摘要", lines, color="blue")
        return 0

    if not args.code or len(args.code) != 6:
        print("用法: chan_analysis.py <代码> [--push] 或 --pool")
        return 1
    names = load_names()
    cs = analyze(args.code, names.get(args.code, args.code))
    if cs is None:
        print("数据不足")
        return 1
    png = plot_structure(cs)
    print(cs.summary())
    print(f"结构图: {png}")
    if args.push:
        push_to_feishu(cs, png)
        print("已推送飞书")
    return 0


if __name__ == "__main__":
    sys.exit(main())
