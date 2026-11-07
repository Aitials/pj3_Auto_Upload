"""通道包，只在这里暴露统一契约。"""

from __future__ import annotations

from .base import Channel, IncomingMessage, MediaAttachment, MessageHandler

__all__ = ["Channel", "IncomingMessage", "MediaAttachment", "MessageHandler"]
