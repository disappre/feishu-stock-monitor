# -*- coding: utf-8 -*-
"""飞书机器人：长连接(WebSocket)模式接收 @消息，个股查询自动分析回卡片。

无需公网IP/域名，本地运行即可。前置条件见 README「机器人交互」一节：
1. 飞书开放平台创建企业自建应用，开通机器人能力；
2. 事件订阅方式选择「使用长连接接收事件」，订阅 im.message.receive_v1；
3. 申请权限 im:message.group_at_msg / im:message.p2p_msg / im:message:send_as_bot；
4. 发布应用版本，把应用拉进群（或私聊）后 @它 发送股票代码。

用法： python -m stock_monitor.bot.server
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading

from dotenv import load_dotenv

from ..bot.analyzer import analyze, build_card

load_dotenv()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("bot")

# A股代码: 60/68/00/30/8x开头。用数字前后瞻而非 \b —— Python正则中汉字也算 \w，
# "下300750谢" 这类中文紧邻数字的场景 \b 不成立
_CODE_RE = re.compile(r"(?<![0-9])([03568]\d{5})(?![0-9])")
_AT_RE = re.compile(r"@_user_\d+")              # 飞书@提及占位符
_seen_lock = threading.Lock()
_seen: set[str] = set()                          # 事件去重(飞书可能重投递)


def _extract_code(text: str) -> str | None:
    text = _AT_RE.sub("", text)
    m = _CODE_RE.search(text)
    return m.group(1) if m else None


def _reply_card(client, message_id: str, card: dict) -> None:
    """用 im/v1 reply 接口回一张 interactive 卡片。"""
    from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
    req = ReplyMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(ReplyMessageRequestBody.builder()
                      .msg_type("interactive")
                      .content(json.dumps(card, ensure_ascii=False))
                      .build()).build()
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        logger.warning("回复失败 code=%s msg=%s", resp.code, resp.msg)


def _reply_text(client, message_id: str, text: str) -> None:
    _reply_card(client, message_id, {
        "elements": [{"tag": "markdown", "content": text}],
    })


def _reply_chan(client, message_id: str, code: str):
    """缠论结构分析：生成结构图并回卡片（图+摘要）。"""
    import json
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo / "tools"))
    from chan_analysis import analyze, plot_structure  # noqa: E402
    from stock_monitor.notify import feishu_bot

    names = {}
    pool = repo / "data" / "screen_pool.txt"
    if pool.exists():
        for line in pool.read_text(encoding="utf-8").splitlines():
            parts = [p.strip() for p in line.split("#", 1)[0].split(",")]
            if len(parts) >= 2 and parts[0].isdigit() and len(parts[0]) == 6:
                names[parts[0]] = parts[1]
    cs = analyze(code, names.get(code, code))
    if cs is None:
        _reply_text(client, message_id, f"`{code}` 数据不足，无法分析")
        return
    png = plot_structure(cs)
    image_key = feishu_bot.upload_image(png)
    card = {
        "header": {"title": {"tag": "plain_text",
                             "content": f"{cs.name} {code} · 缠论结构"},
                   "template": "blue"},
        "elements": [
            {"tag": "markdown", "content": cs.summary()},
            {"tag": "markdown", "content":
                "线段为特征序列分型简化实现（非完整算法）；"
                "仅供学习研究，不构成投资建议"},
        ] + ([{"tag": "img", "img_key": image_key,
               "alt": {"tag": "plain_text", "content": "缠论结构图"}}]
             if image_key else []),
    }
    _reply_card(client, message_id, card)


def _handle(client, data) -> None:
    """im.message.receive_v1 事件处理：解析消息 -> 分析 -> 回卡片。"""
    event = data.event
    msg = event.message
    msg_id = msg.message_id

    with _seen_lock:
        if msg_id in _seen:
            return
        _seen.add(msg_id)
        if len(_seen) > 2000:
            _seen.clear()

    try:
        content = json.loads(msg.content or "{}")
    except json.JSONDecodeError:
        return
    text = content.get("text", "")
    if not text:
        return
    logger.info("收到消息: %r (chat=%s)", text[:40], msg.chat_id)

    # 缠论结构分析：消息含"缠论"或"chan" + 代码
    if re.search(r"缠论|chan", text, re.I):
        code = _extract_code(text)
        if code:
            _reply_chan(client, msg_id, code)
            return
        _reply_text(client, msg_id,
                    "请发送 **缠论 + 6位代码**，例如 `缠论 600519`，"
                    "我将返回缠论结构图（合并K线/笔/线段/中枢）。")
        return

    code = _extract_code(text)
    if not code:
        _reply_text(client, msg_id,
                    "请发送**6位A股代码**，例如 `600519`，我来跑一轮规则+形态分析。")
        return

    try:
        res = analyze(code)
    except Exception:
        logger.exception("分析异常: %s", code)
        _reply_text(client, msg_id, f"分析 `{code}` 时出错，请稍后重试。")
        return
    if res is None:
        _reply_text(client, msg_id,
                    f"未取到 `{code}` 的行情数据，请确认代码是否正确。")
        return
    _reply_card(client, msg_id, build_card(res))
    logger.info("已回复分析: %s %s", res["name"], res["code"])


def main():
    import lark_oapi as lark
    app_id = os.getenv("FEISHU_APP_ID", "")
    app_secret = os.getenv("FEISHU_APP_SECRET", "")
    if not (app_id and app_secret):
        raise SystemExit("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET，请先在 .env 中配置（见 README）")

    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(
        lambda _, data: _handle(client_ref[0], data)).build()

    client_ref = [lark.Client.builder().app_id(app_id).app_secret(app_secret).build()]
    ws = lark.ws.Client(app_id, app_secret,
                        event_handler=handler,
                        log_level=lark.LogLevel.INFO)
    logger.info("飞书机器人已启动（长连接模式），在群里 @机器人 发送股票代码即可查询")
    ws.start()


if __name__ == "__main__":
    main()
