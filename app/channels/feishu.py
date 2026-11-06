"""飞书企业自建应用机器人通道（长连接 WebSocket）。

行为对齐现有 Channel 契约：长连接收消息、去重、媒体落盘。
当前先使用纯文本回复，流式卡片代码保留，暂不调用。
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import re
import threading
import time
import uuid
from collections import deque
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any

import lark_oapi as lark
from lark_oapi.api.cardkit.v1 import (
    ContentCardElementRequest,
    ContentCardElementRequestBody,
    CreateCardRequest,
    CreateCardRequestBody,
    SettingsCardRequest,
    SettingsCardRequestBody,
)
from lark_oapi.api.im.v1 import (
    CreateFileRequest,
    CreateFileRequestBody,
    CreateImageRequest,
    CreateImageRequestBody,
    CreateMessageRequest,
    CreateMessageRequestBody,
    GetMessageResourceRequest,
    P2ImMessageReceiveV1,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

from app.channels.base import Channel, IncomingMessage, MediaAttachment, MessageHandler
from app.config import FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_MEDIA_DIR

log = logging.getLogger("feishu")

STREAM_INTERVAL_SECONDS = 0.5
MAX_SEEN_IDS = 500
CARD_ELEMENT_ID = "answer"
MAX_CARD_CHARS = 100_000
MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024

_MEDIA_KINDS = {
    "image": "image",
    "file": "file",
    "audio": "voice",
    "media": "video",
}
_DEFAULT_SUFFIX = {
    "image": ".jpg",
    "voice": ".opus",
    "video": ".mp4",
    "file": ".bin",
}

_active_channel: "FeishuChannel | None" = None
_current_message: ContextVar[IncomingMessage | None] = ContextVar(
    "feishu_current_message", default=None
)


def _safe_name(name: str) -> str:
    """把文件名压成安全的单段名称。"""
    name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip()
    return name[:80]


def _clean_text(text: str) -> str:
    """去掉群聊 @ 标记，保留正文。"""
    text = re.sub(r"@_user_\d+", "", text)
    text = re.sub(r"<at[^>]*>.*?</at>", "", text, flags=re.DOTALL)
    return text.strip()


def _plain_text(text: str) -> str:
    """飞书普通文本不渲染 Markdown，发送前做最小降级。"""
    text = re.sub(r"```[^\n]*\n?([\s\S]*?)```", lambda m: m.group(1).strip(), text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    return text.strip()


def _post_text(content: dict) -> str:
    """从飞书富文本 post 中提取纯文本。"""
    section = content.get("zh_cn") or content.get("en_us") or {}
    if not isinstance(section, dict):
        return ""
    lines: list[str] = []
    title = str(section.get("title") or "").strip()
    if title:
        lines.append(title)
    for paragraph in section.get("content") or []:
        if not isinstance(paragraph, list):
            continue
        parts: list[str] = []
        for node in paragraph:
            if not isinstance(node, dict):
                continue
            tag = node.get("tag")
            if tag in ("text", "a", "code_block", "md"):
                parts.append(str(node.get("text") or ""))
        lines.append("".join(parts))
    return "\n".join(lines).strip()


def _post_image_keys(content: dict) -> list[str]:
    """从飞书富文本 post 中提取图片 key。"""
    section = content.get("zh_cn") or content.get("en_us") or {}
    if not isinstance(section, dict):
        return []
    keys: list[str] = []
    for paragraph in section.get("content") or []:
        if not isinstance(paragraph, list):
            continue
        for node in paragraph:
            if isinstance(node, dict) and node.get("tag") == "img":
                key = str(node.get("image_key") or "")
                if key:
                    keys.append(key)
    return keys


def _stream_card_json() -> str:
    """卡片 JSON 2.0：开启流式更新，markdown 元素用于逐字上屏。"""
    card = {
        "schema": "2.0",
        "header": {"title": {"tag": "plain_text", "content": "回答"}},
        "config": {
            "streaming_mode": True,
            "summary": {"content": "正在生成..."},
            "streaming_config": {
                "print_frequency_ms": {
                    "default": 70,
                    "android": 70,
                    "ios": 70,
                    "pc": 70,
                },
                "print_step": {
                    "default": 1,
                    "android": 1,
                    "ios": 1,
                    "pc": 1,
                },
                "print_strategy": "fast",
            },
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": "正在生成回答...",
                    "element_id": CARD_ELEMENT_ID,
                }
            ]
        },
    }
    return json.dumps(card, ensure_ascii=False)


def get_active_channel() -> "FeishuChannel | None":
    return _active_channel


def get_current_message() -> IncomingMessage | None:
    return _current_message.get()


class FeishuChannel(Channel):
    """飞书长连接通道。"""

    name = "feishu"

    def __init__(self) -> None:
        global _active_channel
        if not FEISHU_APP_ID or not FEISHU_APP_SECRET:
            raise SystemExit("缺少 FEISHU_APP_ID / FEISHU_APP_SECRET，请先在 .env 中配置")

        self.media_dir = FEISHU_MEDIA_DIR
        self._client = (
            lark.Client.builder()
            .app_id(FEISHU_APP_ID)
            .app_secret(FEISHU_APP_SECRET)
            .log_level(lark.LogLevel.INFO)
            .build()
        )
        self._queue: asyncio.Queue[P2ImMessageReceiveV1] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._seen: deque[str] = deque()
        self._seen_set: set[str] = set()
        self._ws_thread: threading.Thread | None = None
        _active_channel = self

    # ------------------------------------------------------------------ 启动

    async def start(self, on_message: MessageHandler) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()

        handler = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(self._on_event)
            .build()
        )
        ws = lark.ws.Client(
            FEISHU_APP_ID,
            FEISHU_APP_SECRET,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
        )
        self._ws_thread = threading.Thread(
            target=self._run_ws, args=(ws,), name="feishu-ws", daemon=True
        )
        self._ws_thread.start()
        log.info("飞书长连接已启动，等待消息")

        while True:
            event = await self._queue.get()
            try:
                message = await self._to_incoming(event)
            except Exception:
                log.exception("飞书消息解析失败，已跳过")
                continue
            if not message:
                continue
            token = _current_message.set(message)
            try:
                await on_message(message)
            finally:
                _current_message.reset(token)

    def _run_ws(self, ws: Any) -> None:
        try:
            ws.start()
        except Exception:
            log.exception("飞书长连接异常退出")

    def _on_event(self, data: P2ImMessageReceiveV1) -> None:
        """事件回调只做去重和入队，必须在 3 秒内返回。"""
        try:
            event = data.event
            if not event or not event.message or not event.sender:
                return
            sender = event.sender
            if sender.sender_type and sender.sender_type != "user":
                return
            message_id = str(event.message.message_id or "")
            if not message_id or message_id in self._seen_set:
                return
            self._remember(message_id)
            if self._loop and self._queue:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, data)
        except Exception:
            log.exception("飞书事件解析失败")

    def _remember(self, key: str) -> None:
        self._seen.append(key)
        self._seen_set.add(key)
        while len(self._seen) > MAX_SEEN_IDS:
            old = self._seen.popleft()
            self._seen_set.discard(old)

    # ------------------------------------------------------------------ 收消息

    async def _to_incoming(self, data: P2ImMessageReceiveV1) -> IncomingMessage | None:
        event = data.event
        if not event or not event.message or not event.sender:
            return None
        message = event.message
        sender = event.sender

        sender_id = sender.sender_id
        user_id = ""
        if sender_id:
            user_id = str(
                sender_id.open_id or sender_id.user_id or sender_id.union_id or ""
            )
        if not user_id:
            return None

        try:
            content = json.loads(message.content or "{}")
        except json.JSONDecodeError:
            log.warning("飞书消息 content 不是合法 JSON：%s", message.content)
            return None
        if not isinstance(content, dict):
            return None

        texts: list[str] = []
        media: list[MediaAttachment] = []
        message_type = str(message.message_type or "")
        message_id = str(message.message_id or "")

        if message_type == "text":
            texts.append(_clean_text(str(content.get("text") or "")))
        elif message_type == "post":
            texts.append(_clean_text(_post_text(content)))
            for image_key in _post_image_keys(content):
                attachment = await self._download_media(
                    message_id, image_key, "image", "image", ""
                )
                if attachment:
                    media.append(attachment)
        elif message_type in _MEDIA_KINDS:
            kind = _MEDIA_KINDS[message_type]
            raw_key = (
                content.get("image_key")
                if message_type == "image"
                else content.get("file_key")
            )
            key = str(raw_key or "")
            file_name = str(content.get("file_name") or "")
            resource_type = "image" if message_type == "image" else "file"
            if key:
                attachment = await self._download_media(
                    message_id, key, kind, resource_type, file_name
                )
                if attachment:
                    media.append(attachment)
        elif message_type in ("sticker", "interactive", "share_chat", "share_user", "system"):
            texts.append(f"[{message_type}] 暂不支持解析该类型消息")
        else:
            texts.append(f"[{message_type}] 暂不支持解析该类型消息")

        text = "\n".join(part for part in texts if part).strip()
        if media:
            notes = []
            for attachment in media:
                if attachment.path:
                    notes.append(f"[{attachment.kind}] 已保存到本地：{attachment.path}")
                else:
                    notes.append(f"[{attachment.kind}] 下载失败，详见日志")
            text = f"{text}\n\n{chr(10).join(notes)}" if text else "\n".join(notes)

        if not text:
            return None

        return IncomingMessage(
            channel=self.name,
            message_id=message_id,
            user_id=user_id,
            text=text,
            media=media,
            raw=message,
            reply_handle={
                "chat_id": str(message.chat_id or ""),
                "message_id": message_id,
                "chat_type": str(message.chat_type or ""),
                "message_type": message_type,
            },
        )

    async def _download_media(
        self,
        message_id: str,
        key: str,
        kind: str,
        resource_type: str,
        file_name: str,
    ) -> MediaAttachment | None:
        try:
            request = (
                GetMessageResourceRequest.builder()
                .message_id(message_id)
                .file_key(key)
                .type(resource_type)
                .build()
            )
            response = await asyncio.to_thread(
                self._client.im.v1.message_resource.get, request
            )
            if not response.success():
                raise RuntimeError(
                    f"下载资源失败：code={response.code} msg={response.msg}"
                )
            if not response.file:
                raise RuntimeError("下载资源失败：响应没有文件内容")
            content = response.file.read()
            name = file_name or response.file_name or ""
            if kind == "image" and not Path(name).suffix:
                content_type = ""
                raw = getattr(response, "raw", None)
                if raw is not None and getattr(raw, "headers", None):
                    content_type = str(raw.headers.get("Content-Type") or "")
                suffix = mimetypes.guess_extension(content_type.split(";")[0]) or ""
                name = f"image{suffix}" if suffix else name
            saved = self._save_media(content, kind, name)
            return MediaAttachment(
                kind=kind,
                path=str(saved),
                file_name=file_name or response.file_name or "",
            )
        except Exception as exc:
            log.warning("飞书媒体下载失败（%s）：%s", kind, exc)
            return MediaAttachment(kind=kind, path="", file_name=file_name)

    def _save_media(self, content: bytes, kind: str, file_name: str) -> Path:
        self.media_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(file_name).suffix or _DEFAULT_SUFFIX.get(kind, ".bin")
        safe = _safe_name(file_name) or f"feishu_{kind}{suffix}"
        if not Path(safe).suffix:
            safe += suffix
        filename = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}_{safe}"
        path = self.media_dir / filename
        path.write_bytes(content)
        return path

    # ------------------------------------------------------------------ 回复

    async def reply_text(self, message: IncomingMessage, text: str) -> None:
        content = json.dumps(
            {"text": _plain_text(text) or "（没有生成内容）"}, ensure_ascii=False
        )
        await self._send_content(message, "text", content)

    async def reply_stream(self, message: IncomingMessage, chunks) -> str:
        """当前先走纯文本回复，不调用流式卡片。"""
        answer = ""
        try:
            async for delta in chunks:
                answer += delta
        except Exception as exc:
            log.exception("飞书回复失败")
            answer = answer or f"抱歉，处理失败了：{exc}"

        await self.reply_text(message, answer or "（没有生成内容）")
        return answer

    async def send_typing(self, message: IncomingMessage, on: bool = True) -> None:
        # 飞书机器人没有官方 typing 接口，保持无操作。
        return None

    async def _create_and_send_card(self, message: IncomingMessage) -> str | None:
        try:
            request = (
                CreateCardRequest.builder()
                .request_body(
                    CreateCardRequestBody.builder()
                    .type("card_json")
                    .data(_stream_card_json())
                    .build()
                )
                .build()
            )
            response = await asyncio.to_thread(
                self._client.cardkit.v1.card.create, request
            )
            self._ensure_success(response, "创建卡片")
            card_id = str(response.data.card_id)
            content = json.dumps(
                {"type": "card", "data": {"card_id": card_id}}, ensure_ascii=False
            )
            await self._send_content(message, "interactive", content)
            return card_id
        except Exception:
            log.exception("飞书流式卡片创建失败，降级为文本回复")
            return None

    async def _update_card(self, card_id: str, content: str, sequence: int) -> bool:
        try:
            request = (
                ContentCardElementRequest.builder()
                .card_id(card_id)
                .element_id(CARD_ELEMENT_ID)
                .request_body(
                    ContentCardElementRequestBody.builder()
                    .content(content[:MAX_CARD_CHARS])
                    .sequence(sequence)
                    .uuid(str(uuid.uuid4()))
                    .build()
                )
                .build()
            )
            response = await asyncio.to_thread(
                self._client.cardkit.v1.card_element.content, request
            )
            if not response.success():
                log.warning(
                    "飞书卡片更新失败：code=%s msg=%s", response.code, response.msg
                )
                return False
            return True
        except Exception:
            log.exception("飞书卡片更新异常")
            return False

    async def _close_card(self, card_id: str, sequence: int) -> bool:
        try:
            settings = json.dumps(
                {"config": {"streaming_mode": False}}, ensure_ascii=False
            )
            request = (
                SettingsCardRequest.builder()
                .card_id(card_id)
                .request_body(
                    SettingsCardRequestBody.builder()
                    .settings(settings)
                    .sequence(sequence)
                    .uuid(str(uuid.uuid4()))
                    .build()
                )
                .build()
            )
            response = await asyncio.to_thread(
                self._client.cardkit.v1.card.settings, request
            )
            if not response.success():
                log.warning(
                    "飞书关闭流式模式失败：code=%s msg=%s", response.code, response.msg
                )
                return False
            return True
        except Exception:
            log.exception("飞书关闭流式模式异常")
            return False

    async def _send_content(
        self, message: IncomingMessage, msg_type: str, content: str
    ) -> None:
        body = (
            ReplyMessageRequestBody.builder()
            .msg_type(msg_type)
            .content(content)
            .build()
        )
        request = (
            ReplyMessageRequest.builder()
            .message_id(message.message_id)
            .request_body(body)
            .build()
        )
        try:
            response = await asyncio.to_thread(
                self._client.im.v1.message.reply, request
            )
            if response.success():
                return
            log.warning(
                "飞书回复消息失败：code=%s msg=%s，尝试直接发送",
                response.code,
                response.msg,
            )
        except Exception:
            log.exception("飞书回复消息异常，尝试直接发送")

        chat_id = self._chat_id(message)
        if not chat_id:
            raise RuntimeError("飞书发送失败：缺少 chat_id")
        create_request = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type(msg_type)
                .content(content)
                .build()
            )
            .build()
        )
        response = await asyncio.to_thread(
            self._client.im.v1.message.create, create_request
        )
        self._ensure_success(response, "发送消息")

    def _chat_id(self, message: IncomingMessage) -> str:
        if isinstance(message.reply_handle, dict):
            chat_id = str(message.reply_handle.get("chat_id") or "")
            if chat_id:
                return chat_id
        return str(getattr(message.raw, "chat_id", "") or "")

    @staticmethod
    def _ensure_success(response: Any, action: str) -> None:
        if not response.success():
            raise RuntimeError(
                f"{action}失败：code={response.code} msg={response.msg}"
            )

    # ------------------------------------------------------------------ 发媒体

    async def send_file(
        self,
        message: IncomingMessage,
        path: str,
        kind: str = "file",
        file_name: str = "",
    ) -> None:
        """向当前飞书会话发送图片、文件、视频或语音。"""
        file_path = Path(path).expanduser().resolve()
        if not file_path.is_file():
            raise FileNotFoundError(f"文件不存在：{file_path}")
        size = file_path.stat().st_size
        name = file_name or file_path.name
        normalized = kind.strip().lower()

        if normalized == "image":
            if size > MAX_IMAGE_BYTES:
                raise ValueError("飞书图片上限 10 MB")
            image_key = await self._upload_image(file_path)
            content = json.dumps({"image_key": image_key}, ensure_ascii=False)
            await self._send_content(message, "image", content)
            return

        if normalized == "video":
            if file_path.suffix.lower() != ".mp4":
                raise ValueError("飞书视频消息仅支持 mp4")
            if size > MAX_FILE_BYTES:
                raise ValueError("飞书 IM 文件上限 30 MB，大视频请改为发送链接")
            file_key = await self._upload_file(file_path, "mp4", name)
            content = json.dumps(
                {"file_key": file_key, "file_name": name}, ensure_ascii=False
            )
            await self._send_content(message, "media", content)
            return

        if normalized in ("voice", "audio"):
            if file_path.suffix.lower() != ".opus":
                raise ValueError("飞书语音消息仅支持 opus")
            if size > MAX_FILE_BYTES:
                raise ValueError("飞书 IM 文件上限 30 MB")
            file_key = await self._upload_file(file_path, "opus", name)
            content = json.dumps(
                {"file_key": file_key, "duration": 0}, ensure_ascii=False
            )
            await self._send_content(message, "audio", content)
            return

        if size > MAX_FILE_BYTES:
            raise ValueError("飞书 IM 文件上限 30 MB，请改为发送链接")
        file_key = await self._upload_file(file_path, "stream", name)
        content = json.dumps({"file_key": file_key}, ensure_ascii=False)
        await self._send_content(message, "file", content)

    async def _upload_file(self, path: Path, file_type: str, file_name: str) -> str:
        with path.open("rb") as file_obj:
            body = (
                CreateFileRequestBody.builder()
                .file_type(file_type)
                .file_name(file_name)
                .file(file_obj)
                .build()
            )
            request = CreateFileRequest.builder().request_body(body).build()
            response = await asyncio.to_thread(
                self._client.im.v1.file.create, request
            )
        self._ensure_success(response, "上传文件")
        if not response.data or not response.data.file_key:
            raise RuntimeError("上传文件失败：响应缺少 file_key")
        return str(response.data.file_key)

    async def _upload_image(self, path: Path) -> str:
        with path.open("rb") as file_obj:
            body = (
                CreateImageRequestBody.builder()
                .image_type("message")
                .image(file_obj)
                .build()
            )
            request = CreateImageRequest.builder().request_body(body).build()
            response = await asyncio.to_thread(
                self._client.im.v1.image.create, request
            )
        self._ensure_success(response, "上传图片")
        if not response.data or not response.data.image_key:
            raise RuntimeError("上传图片失败：响应缺少 image_key")
        return str(response.data.image_key)
