# -*- coding: utf-8 -*-
"""飞书多维表格：股票走势记录（三张表）。

Base: "股票走势记录 · feishu-stock-monitor"
  - 信号流水(tbl97YTB9xcXHp5H)：每条信号一行，复盘时回填5日后涨跌
  - 每日走势快照(tbl57PAJfL99pOBf)：收盘后写自选股快照（价/DIF/防狼术/缠论位置）
  - 缠论结构状态(tbl9qVQha9mvBCUl)：缠论结构参数与次级别判断

写入走 lark-cli（subprocess），比lark-oapi少维护一套认证；
CLI未登录时静默跳过，推送链路永不被表格写入阻断（沿袭降级设计）。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from ..engine.base import Signal
from ..engine.rules_wolf import dif_dea_below_zero

logger = logging.getLogger(__name__)

BASE_TOKEN = "XsAkbU5Bbajq4isYQ2OcFkGUn58"
TBL_SIGNALS = "tbl97YTB9xcXHp5H"
TBL_DAILY = "tbl57PAJfL99pOBf"
TBL_CHAN = "tbl9qVQha9mvBCUl"

_LOCAL_MAP = Path(__file__).resolve().parents[1] / "data" / "bitable_record_ids.json"


_LARK_CLI = "lark-cli"
# Windows下subprocess不走shell找不到npm全局命令——回退.cmd绝对路径
if sys.platform == "win32":
    _candidate = Path(os.environ.get("APPDATA", "")) / "npm" / "lark-cli.cmd"
    if _candidate.exists():
        _LARK_CLI = str(_candidate)


def _cli(args: list[str]) -> dict | None:
    """执行lark-cli命令，失败返回None（静默降级）。"""
    try:
        r = subprocess.run([_LARK_CLI, *args], capture_output=True,
                           text=True, timeout=30, encoding="utf-8")
        out = json.loads(r.stdout)
        if out.get("ok"):
            return out.get("data", {})
        logger.warning("lark-cli失败: %s", out.get("error", {}).get("message", "?")[:120])
        return None
    except Exception as e:
        logger.warning("lark-cli不可用: %s", e)
        return None


def _batch_create(table_id: str, records: list[dict]) -> list[str] | None:
    if not records:
        return []
    payload = {"create_records": records}
    data = _cli(["base", "+record-batch-create",
                 "--base-token", BASE_TOKEN, "--table-id", table_id,
                 "--json", json.dumps(payload, ensure_ascii=False), "--as", "user"])
    if data is None:
        return None
    return data.get("record_id_list") or data.get("ids") or []


# ── 信号流水 ──

def log_signal(signal: Signal) -> None:
    """信号写入信号流水表。record_id本地持久化，供复盘回填定位。"""
    direction = {"bullish": "看涨", "bearish": "看跌"}.get(signal.direction, "中性")
    rec = {
        "信号时间": int(signal.triggered_at.timestamp() * 1000),
        "代码": signal.code, "名称": signal.name,
        "信号类型": signal.signal_type, "方向": direction,
        "信号价": round(float(signal.metrics.get("point", 0) or 0), 2),
        "触发说明": signal.detail[:500],
    }
    ids = _batch_create(TBL_SIGNALS, [rec])
    if ids:
        _save_local_id(signal, ids[0])


def _save_local_id(signal: Signal, record_id: str) -> None:
    mapping = {}
    if _LOCAL_MAP.exists():
        try:
            mapping = json.loads(_LOCAL_MAP.read_text(encoding="utf-8"))
        except Exception:
            mapping = {}
    key = f"{signal.code}|{signal.signal_type}|{signal.triggered_at:%Y%m%d}"
    mapping[key] = {"record_id": record_id,
                    "triggered_at": str(signal.triggered_at),
                    "direction": signal.direction}
    _LOCAL_MAP.parent.mkdir(parents=True, exist_ok=True)
    _LOCAL_MAP.write_text(json.dumps(mapping, ensure_ascii=False, indent=1),
                          encoding="utf-8")


def backfill_signal_review(code: str, signal_type: str,
                           triggered_at: datetime, pct: float) -> None:
    """复盘回填：信号后5日涨跌与是否盈利。"""
    mapping = {}
    if _LOCAL_MAP.exists():
        try:
            mapping = json.loads(_LOCAL_MAP.read_text(encoding="utf-8"))
        except Exception:
            mapping = {}
    key = f"{code}|{signal_type}|{triggered_at:%Y%m%d}"
    rid = (mapping.get(key) or {}).get("record_id")
    if not rid:
        return
    direction = (mapping.get(key) or {}).get("direction", "bullish")
    win = pct > 0 if direction == "bullish" else pct < 0
    payload = {"update_records": {
        rid: {"5日后涨跌%": round(pct, 2), "是否盈利": win,
              "复盘日": int(datetime.now().timestamp() * 1000)}}}
    _cli(["base", "+record-batch-update",
          "--base-token", BASE_TOKEN, "--table-id", TBL_SIGNALS,
          "--json", json.dumps(payload, ensure_ascii=False), "--as", "user"])


# ── 每日走势快照 ──

def log_daily_snapshot(code: str, name: str, kline) -> None:
    """收盘后写当日快照：价格/DIF/防狼术状态/缠论位置（缠论部分由调用方补充）。"""
    close = kline["close"].astype(float)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = float((ema12 - ema26).iloc[-1])
    pct = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) >= 2 else 0.0
    rec = {
        "日期": int(datetime.now().timestamp() * 1000),
        "代码": code, "名称": name,
        "收盘价": round(float(close.iloc[-1]), 2),
        "涨跌幅%": round(pct, 2),
        "DIF": round(dif, 3),
        "防狼术状态": "危险区" if dif_dea_below_zero(close) else "正常",
        "缠论位置": "", "笔方向": "",
    }
    _batch_create(TBL_DAILY, [rec])


def log_daily_snapshot_full(code: str, name: str, kline, chan_summary: dict | None) -> None:
    """带缠论信息的完整快照。chan_summary: {位置, 笔方向}"""
    close = kline["close"].astype(float)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = float((ema12 - ema26).iloc[-1])
    pct = (close.iloc[-1] / close.iloc[-2] - 1) * 100 if len(close) >= 2 else 0.0
    chan = chan_summary or {}
    rec = {
        "日期": int(datetime.now().timestamp() * 1000),
        "代码": code, "名称": name,
        "收盘价": round(float(close.iloc[-1]), 2),
        "涨跌幅%": round(pct, 2),
        "DIF": round(dif, 3),
        "防狼术状态": "危险区" if dif_dea_below_zero(close) else "正常",
        "缠论位置": chan.get("位置", ""), "笔方向": chan.get("笔方向", ""),
    }
    _batch_create(TBL_DAILY, [rec])


# ── 缠论结构状态 ──

def log_chan_status(code: str, name: str, cs, sub_verdict: str = "") -> None:
    """缠论结构参数快照（cs为chan_analysis.ChanStructure）。"""
    zs = cs.last_zs
    last_px = float(cs.c.bars_raw[-1].close)
    pos = ("中枢上方" if zs and last_px > zs.gg else
           "中枢下方" if zs and last_px < zs.dd else "中枢内部" if zs else "无中枢")
    seg = cs.segments[-1] if cs.segments else None
    rec = {
        "更新时间": int(datetime.now().timestamp() * 1000),
        "代码": code, "名称": name,
        "合并K线数": len(cs.c.bars_raw),
        "笔数": len(cs.c.bi_list), "线段数": len(cs.segments),
        "中枢数": len(cs.c.zs_list),
        "当前段": "向上" if seg and seg[4] == 1 else "向下" if seg else "",
        "中枢位置": pos, "次级别判断": sub_verdict,
    }
    _batch_create(TBL_CHAN, [rec])
