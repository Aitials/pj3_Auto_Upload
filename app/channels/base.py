"""统一聊天通道接口。

企业微信（wecom）与微信 ClawBot/iLink（wechat）都实现这套接口，
上层只依赖 IncomingMessage / Channel，不关心具体传输方式。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable


@dataclass
class MediaAttachment:
    """已下载到本地的媒体文件。"""

    kind: str
    """媒体类型：image / voice / file / video。"""

    path: str
    """本地绝对路径。"""

    file_name: str = ""
    """原始文件名（可能为空）。"""

    text: str = ""
    """语音消息的转写文本（微信服务端可能提供）。"""


@dataclass
class IncomingMessage:
    """通道无关的入站消息。"""

    channel: str
    """通道名：wecom / wechat。"""

    message_id: str
    """消息唯一 ID，用于去重。"""

    user_id: str
    """发送者 ID。"""

    text: str
    """待交给 Agent 的文本（媒体消息会附加本地路径说明）。"""

    media: list[MediaAttachment] = field(default_factory=list)
    """已下载的媒体文件。"""

    raw: Any = None
    """原始消息体，排障用。"""

    reply_handle: Any = None
    """通道内部回复所需的句柄（企微为原始 frame）。"""


MessageHandler = Callable[[IncomingMessage], Awaitable[None]]


class Channel(ABC):
    """聊天通道抽象。"""

    name: str

    @abstractmethod
    async def start(self, on_message: MessageHandler) -> None:
        """启动通道并持续接收消息；协程通常不会返回。"""

    @abstractmethod
    async def reply_text(self, message: IncomingMessage, text: str) -> None:
        """发送一条完整文本回复。"""

    @abstractmethod
    async def reply_stream(
        self, message: IncomingMessage, chunks: AsyncIterator[str]
    ) -> str:
        """消费 Agent 的流式增量并完成回复，返回完整回答文本。"""

    @abstractmethod
    async def send_typing(self, message: IncomingMessage, on: bool = True) -> None:
        """显示/隐藏“正在输入”状态；不支持的通道可为空实现。"""
