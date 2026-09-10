# -*- coding: utf-8 -*-
"""LLM 解读层：把 Signal 转成一段适合放进飞书卡片的中文解读。

定位约束（写进 system prompt，防止模型越界荐股）：
- 只做事实复述、指标解释、历史形态统计复述；
- 禁止输出"建议买入/卖出/加仓/止损"等操作指令；
- 固定附风险提示。
"""
from __future__ import annotations

import json
import os

SYSTEM_PROMPT = """你是证券数据播报助手。根据给定的监测信号 JSON，用不超过120字生成解读，包含：
1. 触发原因（复述关键数值）；
2. 该信号的一般技术含义（中性表述）；
3. 一句风险提示。
严禁出现任何买卖、仓位、止损建议。语气客观，直接输出正文，不要标题。"""


def interpret(signal) -> str:
    """调用 LLM 生成解读。LLM 不可用时回退到信号自带的 detail，保证推送链路不中断。"""
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        return signal.detail

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=os.getenv("LLM_BASE_URL"))
        resp = client.chat.completions.create(
            model=os.getenv("LLM_MODEL", "deepseek-chat"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({
                    "code": signal.code, "name": signal.name,
                    "signal_type": signal.signal_type, "direction": signal.direction,
                    "metrics": signal.metrics,
                }, ensure_ascii=False)},
            ],
            temperature=0.3,
            max_tokens=200,
        )
        return resp.choices[0].message.content.strip()
    except Exception:
        return signal.detail
