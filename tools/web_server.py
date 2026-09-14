# -*- coding: utf-8 -*-
"""Web前端后端API：缠论结构+多级别K线+全部结论JSON输出。

端点：
  GET /api/chan/<code>?level=daily|m30|m5   结构分析(笔/线段/中枢/背驰/买卖点/主力/防狼术)
  GET /api/kline/<code>?level=...&rows=500  原始K线
  GET /api/levels/<code>                    可用级别列表
启动: python tools/web_server.py  → http://localhost:8787
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

import pandas as pd  # noqa: E402
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from stock_monitor.data import store  # noqa: E402
from stock_monitor.engine.mainforce import classify_stage  # noqa: E402
from stock_monitor.engine.rules_wolf import dif_dea_below_zero  # noqa: E402
from stock_monitor.engine.rules_mainwave import analyze_mainwave  # noqa: E402
from chan_analysis import (  # noqa: E402
    analyze, detect_1st_2nd_points, detect_3rd_points, detect_divergence, macd)
from czsc import Direction  # noqa: E402

app = FastAPI(title="feishu-stock-monitor web")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

BUY_KINDS = {"一买", "二买", "最强二买", "三买"}
KIND_LABEL = {"一买": "1B", "二买": "2B", "最强二买": "2B+", "三买": "3B",
              "一卖": "1S", "二卖": "2S", "最强二卖": "2S+", "三卖": "3S"}


def _fetch(code: str, level: str, rows: int = 500):
    """日线=本地库；m30/m5=腾讯实时接口。"""
    if level == "daily":
        df = store.load_recent(code, rows)
        return df
    from stock_monitor.engine.sublevel import fetch_m30
    if level == "m30":
        df = fetch_m30(code, count=480)
        if df is not None:
            df = df.rename(columns={"dt": "date"})
            df["date"] = df["date"].dt.strftime("%Y-%m-%d %H:%M")
            df["amount"] = 0.0
            return df
        return None
    if level == "m5":
        # 腾讯5分钟K线
        import requests
        try:
            symbol = ("sh" if code.startswith(("6", "5", "9")) else "sz") + code
            r = requests.get("https://proxy.finance.qq.com/ifzqgtimg/appstock/app/kline/mkline",
                             params={"param": f"{symbol},m5,,,600,qfq"},
                             headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            node = r.json().get("data", {}).get(symbol, {})
            key = next((k for k in node if "m5" in k), None)
            if not key:
                return None
            rows_ = []
            for p in node[key]:
                rows_.append({"date": pd.to_datetime(p[0], format="%Y%m%d%H%M").strftime("%Y-%m-%d %H:%M"),
                              "open": float(p[1]), "close": float(p[2]),
                              "high": float(p[3]), "low": float(p[4]),
                              "volume": float(p[5]), "amount": 0.0})
            return pd.DataFrame(rows_)
        except Exception:
            return None
    return None


def _structure_payload(code: str, name: str, df: pd.DataFrame) -> dict:
    """czsc全结构 → 前端友好JSON。"""
    from czsc import CZSC, Freq, RawBar
    freq_map = {"daily": Freq.D, "m30": Freq.F30, "m5": Freq.F5}
    level = "daily" if len(str(df["date"].iloc[-1])) == 10 else (
        "m30" if str(df["date"].iloc[-1]).endswith("00") and "3" in str(df["date"].iloc[-1])[11:13] else "m5")
    # 用date长度判定级别不可靠, 由调用方传入
    bars = [RawBar(symbol=code, dt=pd.Timestamp(r["date"]),
                   freq=Freq.D, open=r["open"], close=r["close"],
                   high=r["high"], low=r["low"], vol=r["volume"], amount=0.0)
            for _, r in df.iterrows()]
    c = CZSC(bars)

    def fxj(fx):
        return {"dt": str(fx.dt)[:16], "fx": round(fx.fx, 2),
                "kind": "g" if "G" in str(fx.mark) else "d"}

    # 线段(特征序列简化)——从chan_analysis导入构建函数
    from chan_analysis import build_segments
    segments = build_segments(c.bi_list)

    dif, dea, hist = macd(df["close"].astype(float))

    # 背驰(全量窗口,近因优先标注)
    divs = []
    try:
        for idx, kind, px, ratio, n_zs in detect_divergence(c.bi_list, df, c.zs_list):
            divs.append({"bi": idx, "kind": kind, "ratio": ratio,
                         "n_zs": n_zs, "date": str(c.bi_list[idx].fx_b.dt)[:10]})
    except Exception:
        pass

    # 买卖点（全量历史供前端展示；三买卖不传last_px=不做失效校验）
    points = []
    try:
        for idx, kind, px in (detect_3rd_points(c.zs_list, c.bi_list)
                              + detect_1st_2nd_points(c.zs_list, c.bi_list,
                                                      segments, df)):
            if 0 <= idx < len(c.bi_list):
                points.append({"bi": idx, "kind": kind, "label": KIND_LABEL.get(kind, kind),
                               "buy": kind in BUY_KINDS, "price": round(px, 2),
                               "date": str(c.bi_list[idx].fx_b.dt)[:10]})
    except Exception:
        pass

    # 包含合并分组（merge_groups：可合并的K线组，前端画框）
    try:
        from chan_analysis import merge_groups
        groups = merge_groups(df)
        merge_units = [{"s": s, "e": e, "hi": round(hi, 2), "lo": round(lo, 2)}
                       for (s, e, hi, lo) in groups if e > s]
    except Exception:
        merge_units = []

    last_px = float(df["close"].iloc[-1])
    zs_list = c.zs_list[-6:]   # 最多6个中枢,避免payload过大
    return {
        "code": code, "name": name, "last_px": round(last_px, 2),
        "n_bars": len(df), "n_bi": len(c.bi_list), "n_zs": len(c.zs_list),
        "fx": [fxj(fx) for fx in c.fx_list[-120:]],
        "bi": [{"a": fxj(b.fx_a), "b": fxj(b.fx_b),
                "up": b.direction == Direction.Up} for b in c.bi_list],
        "segments": [{"s_dt": str(s[0])[:10], "s_px": round(s[1], 2),
                      "e_dt": str(s[2])[:10], "e_px": round(s[3], 2),
                      "up": s[4] == 1} for s in segments],
        "zs": [{"s_dt": str(z.sdt)[:10], "e_dt": str(z.edt)[:10],
                "zg": round(z.zg, 2), "zd": round(z.zd, 2),
                "gg": round(z.gg, 2), "dd": round(z.dd, 2)} for z in zs_list],
        "divergence": divs[-8:],
        "points": points[-20:],
        "merge_units": merge_units,
        "macd": {"dif": [round(v, 3) if pd.notna(v) else 0 for v in dif.tolist()[-500:]],
                 "dea": [round(v, 3) if pd.notna(v) else 0 for v in dea.tolist()[-500:]],
                 "hist": [round(v, 3) if pd.notna(v) else 0 for v in hist.tolist()[-500:]]},
    }


@app.get("/api/levels/{code}")
def levels(code: str):
    return {"levels": ["daily", "m30", "m5"]}


@app.get("/api/kline/{code}")
def kline(code: str, level: str = "daily", rows: int = 500):
    df = _fetch(code, level, rows)
    if df is None or df.empty:
        raise HTTPException(404, f"无数据: {code} {level}")
    return {"code": code, "level": level, "dates": df["date"].astype(str).tolist(),
            "klines": df[["open", "close", "high", "low", "volume"]].values.tolist()}


@app.get("/api/chan/{code}")
def chan(code: str, level: str = "daily", rows: int = 500):
    name = _name_of(code)
    df = _fetch(code, level, rows)
    if df is None or len(df) < 40:
        raise HTTPException(404, f"数据不足: {code} {level}")
    payload = _structure_payload(code, name, df)
    # 日线级别附加主力阶段/防狼术/五步法
    if level == "daily":
        mf = classify_stage(df)
        if mf:
            payload["mainforce"] = {"stage": mf.stage, "name": mf.name,
                                    "action": mf.action, "advice": mf.advice}
        payload["wolf"] = {"danger": dif_dea_below_zero(df["close"].astype(float))}
        st = analyze_mainwave(df)
        payload["mainwave"] = {"stage": st.stage, "note": st.note[:120],
                               "benchmark_vol": st.benchmark_vol}
    return payload


def _name_of(code: str) -> str:
    for pf in ("data/screen_pool.txt", "data/pool_836.txt", "data/full_market.txt"):
        p = REPO / pf
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                parts = [x.strip() for x in line.split("#", 1)[0].split(",")]
                if parts and parts[0] == code and len(parts) > 1:
                    return parts[1]
    return code


class SPAStaticFiles(StaticFiles):
    """前端单页应用的history回退。"""
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except Exception:
            return await super().get_response("index.html", scope)


web_dir = REPO / "web"
if web_dir.exists():
    app.mount("/", SPAStaticFiles(directory=str(web_dir)), name="web")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8787)
