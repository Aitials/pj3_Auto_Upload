"""企业微信智能机器人（长连接 API 模式）通道实现。

行为与原 `wecom_bot.py` 保持一致：文本消息交给 Agent，流式回复按 1 秒节流。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time

from aibot import WSClient, WSClientOptions, generate_req_id

from app.channels.base import Channel, IncomingMessage, MessageHandler
from app.config import get_env

log = logging.getLogger("wecom")


def _clean(text: str) -> str:
    """去掉群聊消息开头的 @机器人 前缀。"""
    return re.sub(r"^@\S+\s*", "", text).strip()


class WeComChannel(Channel):
    """企业微信长连接通道。"""

    name = "wecom"

    def __init__(self) -> None:
        bot_id = get_env("WECOM_BOT_ID")
        bot_secret = get_env("WECOM_BOT_SECRET")
        if not bot_id or not bot_secret:
            raise SystemExit("缺少 WECOM_BOT_ID / WECOM_BOT_SECRET，请先在 .env 中配置")

        self._client = WSClient(WSClientOptions(bot_id=bot_id, secret=bot_secret))

        @self._client.on("authenticated")
        def _on_authenticated() -> None:
            log.info("认证成功，长连接已就绪")

        @self._client.on("error")
        def _on_error(error: Exception) -> None:
            log.error("连接错误: %s", error)

    async def start(self, on_message: MessageHandler) -> None:
        @self._client.on("message.text")
        async def _on_text(frame) -> None:
            body = frame.get("body", {})
            question = _clean(body.get("text", {}).get("content", ""))
            if not question:
                return

            message = IncomingMessage(
                channel=self.name,
                message_id=str(body.get("msgid") or body.get("msg_id") or ""),
                user_id=str(body.get("from", {}).get("userid") or ""),
                text=question,
                raw=body,
                reply_handle=frame,
            )
            await on_message(message)

        await self._client.connect()
        await asyncio.Event().wait()

    async def reply_text(self, message: IncomingMessage, text: str) -> None:
        stream_id = generate_req_id("stream")
        await self._client.reply_stream(
            message.reply_handle, stream_id, text or "（没有生成内容）", True
        )

    async def reply_stream(self, message: IncomingMessage, chunks) -> str:
        frame = message.reply_handle
        stream_id = generate_req_id("stream")
        answer = ""
        last_push = 0.0
        try:
            async for delta in chunks:
                answer += delta
                now = time.monotonic()
                if now - last_push >= 1.0:
                    await self._client.reply_stream(frame, stream_id, answer, False)
                    last_push = now
        except Exception as exc:  # noqa: BLE001
            log.exception("GLM 调用失败")
            answer = answer or f"抱歉，处理失败了：{exc}"
        await self._client.reply_stream(
            frame, stream_id, answer or "（没有生成内容）", True
        )
        return answer

    async def send_typing(self, message: IncomingMessage, on: bool = True) -> None:
        # 企业微信智能机器人暂不提供 typing 接口，保持无操作。
        return None
