"""企业微信智能机器人（长连接 API 模式）入口，复用 agent.ask 的 GLM 能力。

运行前在 .env 中配置：
    WECOM_BOT_ID=...
    WECOM_BOT_SECRET=...

运行：
    uv run python wecom_bot.py
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time

from dotenv import load_dotenv
from aibot import WSClient, WSClientOptions, generate_req_id
from agent import astream_answer

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wecom")

BOT_ID = os.getenv("WECOM_BOT_ID", "")
BOT_SECRET = os.getenv("WECOM_BOT_SECRET", "")

if not BOT_ID or not BOT_SECRET:
    raise SystemExit("缺少 WECOM_BOT_ID / WECOM_BOT_SECRET，请先在 .env 中配置")

ws_client = WSClient(WSClientOptions(bot_id=BOT_ID, secret=BOT_SECRET))


@ws_client.on("authenticated")
def on_authenticated() -> None:
    log.info("认证成功，长连接已就绪")


@ws_client.on("error")
def on_error(error: Exception) -> None:
    log.error("连接错误: %s", error)


def _clean(text: str) -> str:
    """去掉群聊消息开头的 @机器人 前缀。"""
    return re.sub(r"^@\S+\s*", "", text).strip()


@ws_client.on("message.text")
async def on_text(frame) -> None:
    body = frame.get("body", {})
    question = _clean(body.get("text", {}).get("content", ""))
    if not question:
        return

    log.info("收到 [%s] %s: %s", body.get("chattype"), body.get("from", {}).get("userid"), question)
    stream_id = generate_req_id("stream")
    answer = ""
    last_push = 0.0
    try:
        async for delta in astream_answer(question):
            answer += delta
            now = time.monotonic()
            if now - last_push >= 1.0:
                await ws_client.reply_stream(frame, stream_id, answer, False)
                last_push = now
    except Exception as exc:  # noqa: BLE001
        log.exception("GLM 调用失败")
        answer = answer or f"抱歉，处理失败了：{exc}"
    await ws_client.reply_stream(frame, stream_id, answer or "（没有生成内容）", True)


async def main() -> None:
    await ws_client.connect()
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
