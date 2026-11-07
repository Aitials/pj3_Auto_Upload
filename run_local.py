"""本地终端测试入口：不经过飞书 / 企业微信，直接走 Agent 与路由层。

运行：
    uv run python run_local.py

输入普通文本即对话；/file <路径> 可模拟发送本地文件。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from pathlib import Path

from app.channels.base import Channel, IncomingMessage, MediaAttachment, MessageHandler
from app.config import PROJECT_ROOT
from app.router import handle_message

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".heic"}
_VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}
_VOICE_SUFFIXES = {".mp3", ".wav", ".opus", ".m4a", ".ogg", ".silk"}

HELP = """命令：
  /paste                  进入多行粘贴模式，最后单独输入 /end 结束
  /textfile <路径>        读取文本文件内容并作为一条消息发送
  /file <路径> [| 问题]   模拟发送本地文件，可附带给 Agent 的问题
  /save                   把上一条 AI 回答保存到 output/terminal_answer_*.txt
  /help                   显示本帮助
  /quit                   退出
  其他输入                 作为普通文本消息"""


def _kind_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return "image"
    if suffix in _VIDEO_SUFFIXES:
        return "video"
    if suffix in _VOICE_SUFFIXES:
        return "voice"
    return "file"


class LocalChannel(Channel):
    """把回复直接打印到终端的测试通道。"""

    name = "local"

    def __init__(self) -> None:
        self.last_answer = ""

    async def start(self, on_message: MessageHandler) -> None:
        return None

    async def reply_text(self, message: IncomingMessage, text: str) -> None:
        self.last_answer = text.strip()
        print(f"AI: {text}\n", flush=True)

    async def reply_stream(self, message: IncomingMessage, chunks) -> str:
        print("AI: ", end="", flush=True)
        answer = ""
        async for delta in chunks:
            answer += delta
            print(delta, end="", flush=True)
        print("\n", flush=True)
        if not answer:
            answer = "（没有生成内容）"
            print(f"{answer}\n", flush=True)
        self.last_answer = answer.strip() or "（没有生成内容）"
        return answer

    async def send_typing(self, message: IncomingMessage, on: bool = True) -> None:
        return None


def _message(text: str, media: list[MediaAttachment] | None = None) -> IncomingMessage:
    return IncomingMessage(
        channel="local",
        message_id=f"local-{uuid.uuid4().hex}",
        user_id="local-user",
        text=text,
        media=media or [],
    )


def _parse_file_command(rest: str) -> tuple[str, str] | None:
    """解析 /file <路径> [| 问题]，返回（路径, 附加问题）。"""
    path_text, _, question = rest.partition("|")
    path_text = path_text.strip().strip('"')
    if not path_text:
        return None
    return path_text, question.strip()


async def _read_paste() -> str:
    """读取多行粘贴内容，直到单独一行输入 /end。"""
    print("进入粘贴模式：粘贴多行文本，最后单独输入 /end 结束。")
    lines: list[str] = []
    while True:
        try:
            line = await asyncio.to_thread(input, "paste> ")
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip() == "/end":
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    print(f"已读取 {len(lines)} 行，{len(text)} 字符。\n")
    return text


def _save_answer(channel: LocalChannel) -> Path:
    if not channel.last_answer:
        raise ValueError("还没有可保存的回答")
    output_dir = PROJECT_ROOT / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"terminal_answer_{datetime.now():%Y%m%d_%H%M%S}.txt"
    path.write_text(channel.last_answer, "utf-8")
    return path


async def main() -> None:
    channel = LocalChannel()
    print("本地终端测试（/help 查看命令，/quit 退出；长文本用 /paste）\n")

    while True:
        try:
            line = await asyncio.to_thread(input, "You: ")
        except (EOFError, KeyboardInterrupt):
            print("\n退出。")
            return

        text = line.strip()
        if not text:
            continue
        if text in ("/quit", "/exit", "quit", "exit"):
            print("退出。")
            return
        if text == "/help":
            print(HELP + "\n")
            continue

        if text == "/save":
            try:
                path = _save_answer(channel)
            except ValueError as exc:
                print(f"{exc}\n")
                continue
            print(f"已保存：{path}\n")
            continue

        if text == "/paste":
            pasted = await _read_paste()
            if not pasted:
                print("没有读取到内容。\n")
                continue
            await handle_message(channel, _message(pasted))
            continue

        if text.startswith("/textfile "):
            path = Path(text[len("/textfile "):].strip().strip('"')).expanduser().resolve()
            if not path.is_file():
                print(f"文件不存在：{path}\n")
                continue
            try:
                content = path.read_text("utf-8").strip()
            except UnicodeDecodeError:
                print(f"文件不是 UTF-8 文本：{path}\n")
                continue
            if not content:
                print(f"文件内容为空：{path}\n")
                continue
            print(f"[已读取文本] {path}（{len(content)} 字符）\n")
            await handle_message(channel, _message(content))
            continue

        if text.startswith("/file "):
            parsed = _parse_file_command(text[len("/file "):])
            if parsed is None:
                print("用法：/file <路径> [| 问题]\n")
                continue
            path_text, question = parsed
            path = Path(path_text).expanduser().resolve()
            if not path.is_file():
                print(f"文件不存在：{path}\n")
                continue
            kind = _kind_of(path)
            media = MediaAttachment(kind=kind, path=str(path), file_name=path.name)
            prompt = f"[{kind}] 本地文件：{path}"
            if question:
                prompt += f"\n用户问题：{question}"
            print(f"[已附加 {kind}] {path}")
            await handle_message(channel, _message(prompt, [media]))
            continue

        await handle_message(channel, _message(text))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
