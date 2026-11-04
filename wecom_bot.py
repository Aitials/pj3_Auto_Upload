"""企业微信智能机器人（长连接 API 模式）入口（根目录薄入口）。

运行前在 .env 中配置：
    WECOM_BOT_ID=...
    WECOM_BOT_SECRET=...

运行：
    uv run python wecom_bot.py
"""

from __future__ import annotations

import asyncio

from app.router import run_channel, setup_logging


def main() -> None:
    setup_logging()
    try:
        asyncio.run(run_channel("wecom"))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
