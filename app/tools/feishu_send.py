"""飞书文件发送工具：把本地文件、图片、视频或语音发回当前飞书会话。"""

from __future__ import annotations

from pathlib import Path

from langchain_core.tools import tool

from app.config import STATE_DIR

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".heic"}
_VIDEO_SUFFIXES = {".mp4"}
_VOICE_SUFFIXES = {".opus"}


@tool
async def send_feishu_file(path: str, kind: str = "auto") -> str:
    """向当前飞书会话发送本地文件。

    Args:
        path: 本地文件路径，必须位于项目的 state/ 目录下。
        kind: auto / file / image / video / voice，默认按后缀自动判断。
    """
    from app.channels.feishu import get_active_channel, get_current_message

    channel = get_active_channel()
    message = get_current_message()
    if channel is None or message is None:
        return "当前没有可用的飞书会话，无法发送文件"

    file_path = Path(path).expanduser().resolve()
    if not file_path.is_file():
        return f"文件不存在：{file_path}"
    if not file_path.is_relative_to(STATE_DIR):
        return f"只允许发送 state/ 目录下的文件：{file_path}"

    normalized = kind.strip().lower()
    if normalized == "auto":
        suffix = file_path.suffix.lower()
        if suffix in _IMAGE_SUFFIXES:
            normalized = "image"
        elif suffix in _VIDEO_SUFFIXES:
            normalized = "video"
        elif suffix in _VOICE_SUFFIXES:
            normalized = "voice"
        else:
            normalized = "file"

    try:
        await channel.send_file(message, str(file_path), normalized, file_path.name)
    except Exception as exc:
        return f"发送失败：{exc}"
    return f"已发送：{file_path.name}"
