# -*- coding: utf-8 -*-
"""飞书多维表格：信号流水写入 + N日后走势回填（复盘）+ 胜率聚合。

使用 lark-oapi SDK 调 Bitable OpenAPI。表格字段（在多维表格中预先建好）：
  信号时间(日期) | 代码(文本) | 名称(文本) | 信号类型(文本) | 方向(单选)
  | 触发说明(文本) | 置信度(数字) | 复盘日(日期) | N日后涨跌%(数字) | 是否盈利(复选)
记录 id 映射持久化在 data/signal_records.json，供回填时定位。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_RECORDS_FILE = Path(__file__).resolve().parents[2] / "data" / "signal_records.json"


def _client():
    import lark_oapi as lark
    return lark.Client.builder() \
        .app_id(os.getenv("FEISHU_APP_ID", "")) \
        .app_secret(os.getenv("FEISHU_APP_SECRET", "")) \
        .build()


def _table_ids() -> tuple[str, str]:
    token = os.getenv("FEISHU_BITABLE_APP_TOKEN", "")
    table_id = os.getenv("FEISHU_BITABLE_TABLE_ID", "")
    if not token or not table_id:
        raise RuntimeError("缺少 FEISHU_BITABLE_APP_TOKEN / FEISHU_BITABLE_TABLE_ID 配置")
    return token, table_id


def _save_record_id(signal, record_id: str):
    _RECORDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    mapping = {}
    if _RECORDS_FILE.exists():
        mapping = json.loads(_RECORDS_FILE.read_text(encoding="utf-8"))
    key = f"{signal.code}|{signal.signal_type}|{signal.triggered_at:%Y%m%d}"
    mapping[key] = {"record_id": record_id, "triggered_at": str(signal.triggered_at)}
    _RECORDS_FILE.write_text(json.dumps(mapping, ensure_ascii=False, indent=1),
                             encoding="utf-8")


def append_signal(signal) -> str | None:
    """写入一条信号流水，返回 record_id。失败仅告警，不阻断推送链路。"""
    try:
        from lark_oapi.api.bitable.v1 import AppTableRecord, CreateRecordRequest
        app_token, table_id = _table_ids()
        fields = {
            "信号时间": int(signal.triggered_at.timestamp() * 1000),
            "代码": signal.code,
            "名称": signal.name,
            "信号类型": signal.signal_type,
            "方向": {"bullish": "看多", "bearish": "看空"}.get(signal.direction, "中性"),
            "触发说明": signal.detail,
            "置信度": round(float(signal.confidence), 3),
        }
        req = CreateRecordRequest.builder() \
            .app_token(app_token).table_id(table_id) \
            .request_body(AppTableRecord.builder().fields(fields).build()).build()
        resp = _client().bitable.v1.app_table_record.create(req)
        if not resp.success():
            logger.warning("多维表格写入失败 code=%s msg=%s", resp.code, resp.msg)
            return None
        _save_record_id(signal, resp.data.record.record_id)
        return resp.data.record.record_id
    except ImportError:
        logger.warning("未安装 lark-oapi，跳过多维表格写入（pip install lark-oapi）")
        return None
    except Exception:
        logger.exception("多维表格写入异常")
        return None


def backfill_review(code: str, signal_type: str, triggered_at: datetime,
                    pct_change: float, forward_days: int):
    """N个交易日后回填涨跌与是否盈利。"""
    try:
        from lark_oapi.api.bitable.v1 import AppTableRecord, UpdateRecordRequest
        app_token, table_id = _table_ids()
        key = f"{code}|{signal_type}|{triggered_at:%Y%m%d}"
        if not _RECORDS_FILE.exists() or key not in json.loads(
                _RECORDS_FILE.read_text(encoding="utf-8")):
            logger.warning("未找到信号记录，跳过回填: %s", key)
            return
        record_id = json.loads(_RECORDS_FILE.read_text(encoding="utf-8"))[key]["record_id"]
        fields = {
            "复盘日": int(datetime.now().timestamp() * 1000),
            f"{forward_days}日后涨跌%": round(float(pct_change), 2),
            "是否盈利": pct_change > 0,
        }
        req = UpdateRecordRequest.builder() \
            .app_token(app_token).table_id(table_id).record_id(record_id) \
            .request_body(AppTableRecord.builder().fields(fields).build()).build()
        resp = _client().bitable.v1.app_table_record.update(req)
        if not resp.success():
            logger.warning("多维表格回填失败 code=%s msg=%s", resp.code, resp.msg)
    except ImportError:
        logger.warning("未安装 lark-oapi，跳过多维表格回填")
    except Exception:
        logger.exception("多维表格回填异常")


def win_rate_stats(signal_type: str | None = None) -> dict:
    """按信号类型聚合胜率：{"ma_golden_cross": {"total": 12, "win": 7, "rate": 0.583}, ...}"""
    from lark_oapi.api.bitable.v1 import (SearchAppTableRecordRequest,
                                          SearchAppTableRecordRequestBody)
    app_token, table_id = _table_ids()
    stats: dict[str, dict] = {}
    page_token = None
    while True:
        body = SearchAppTableRecordRequestBody.builder() \
            .field_names(["信号类型", "是否盈利"]).page_size(500)
        if page_token:
            body = body.page_token(page_token)
        req = SearchAppTableRecordRequest.builder() \
            .app_token(app_token).table_id(table_id) \
            .request_body(body.build()).build()
        resp = _client().bitable.v1.app_table_record.search(req)
        if not resp.success():
            raise RuntimeError(f"多维表格查询失败: {resp.code} {resp.msg}")
        items = resp.data.items or []
        for rec in items:
            f = rec.fields
            stype = f.get("信号类型")
            if signal_type and stype != signal_type:
                continue
            bucket = stats.setdefault(stype, {"total": 0, "win": 0})
            bucket["total"] += 1
            if f.get("是否盈利"):
                bucket["win"] += 1
        if not resp.data.has_more:
            break
        page_token = resp.data.page_token
    for bucket in stats.values():
        bucket["rate"] = round(bucket["win"] / bucket["total"], 3) if bucket["total"] else 0.0
    return stats
