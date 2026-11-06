"""飞书通道启动入口（独立脚本）。

运行：
    uv run python run_feishu.py
"""

from __future__ import annotations

import asyncio
import logging

from app.router import run_channel, setup_logging

log = logging.getLogger("run")


def main() -> None:
    setup_logging()
    log.info("启动通道：feishu")
    try:
        asyncio.run(run_channel("feishu"))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
