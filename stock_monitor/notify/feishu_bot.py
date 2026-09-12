# -*- coding: utf-8 -*-
"""飞书自定义机器人 webhook 推送：签名校验 + 交互式消息卡片。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import time

import requests

logger = logging.getLogger(__name__)


def _webhook() -> str:
    """惰性读取：main.py 的 load_dotenv() 晚于模块导入，不能在导入时固化。"""
    return os.getenv("FEISHU_WEBHOOK", "")


def _secret() -> str:
    return os.getenv("FEISHU_SECRET", "")


def _sign(secret: str, timestamp: int) -> str:
    """飞书自定义机器人签名：HMAC-SHA256，key 为 timestamp\\nsecret。"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def _tenant_access_token() -> str | None:
    """开放平台凭证（上传图片用）；只配了 webhook 时返回 None。"""
    app_id, app_secret = os.getenv("FEISHU_APP_ID"), os.getenv("FEISHU_APP_SECRET")
    if not (app_id and app_secret):
        return None
    resp = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
    token = resp.json().get("tenant_access_token")
    return token or None


def upload_image(png_path: str) -> str | None:
    """上传PNG到飞书获取 image_key；未配置应用凭证或上传失败返回 None。"""
    token = _tenant_access_token()
    if not token:
        return None
    try:
        with open(png_path, "rb") as f:
            resp = requests.post(
                "https://open.feishu.cn/open-apis/im/v1/images",
                headers={"Authorization": f"Bearer {token}"},
                data={"image_type": "message"},
                files={"image": f}, timeout=15)
        return resp.json().get("data", {}).get("image_key")
    except Exception as e:
        logging.getLogger(__name__).warning("图片上传失败: %s", e)
        return None


def send_card(title: str, elements_markdown: list[str], color: str = "blue",
              image_key: str | None = None,
              images: list[tuple[str, str]] | None = None) -> dict:
    """推送卡片。elements_markdown 为markdown段；images=[(image_key, alt)]多图模式。

    多图用法（图+文交替）：调用方自行把文本段与图片在 elements 里排序——
    传 images 时按顺序追加到全部文本之后，若需图文穿插请用 elements 重组。
    """
    elements = [{"tag": "markdown", "content": text} for text in elements_markdown]
    if image_key:
        elements.append({"tag": "img", "img_key": image_key,
                         "alt": {"tag": "plain_text", "content": title}})
    for key, alt in (images or []):
        elements.append({"tag": "img", "img_key": key,
                         "alt": {"tag": "plain_text", "content": alt}})
    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text",
                      "content": "仅供学习研究，不构成投资建议"}],
    })
    payload = {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,  # bullish 用 green，bearish 用 red
            },
            "elements": elements,
        },
    }
    if _secret():
        ts = int(time.time())
        payload["timestamp"] = str(ts)
        payload["sign"] = _sign(_secret(), ts)
    resp = requests.post(_webhook(), json=payload, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"飞书推送失败: {data}")
    return data


def signal_to_card(signal, image_key: str | None = None) -> dict:
    """把 Signal 转成飞书卡片并推送。"""
    color = {"bullish": "green", "bearish": "red"}.get(signal.direction, "blue")
    metrics_lines = "\n".join(
        f"**{k}**: {v}" for k, v in signal.metrics.items()
    )
    return send_card(
        title=signal.title,
        elements_markdown=[
            signal.detail,
            metrics_lines,
            f"信号类型: `{signal.signal_type}` · 来源: {signal.source}"
            f" · 触发时间: {signal.triggered_at:%Y-%m-%d %H:%M}",
        ],
        color=color,
        image_key=image_key,
    )
