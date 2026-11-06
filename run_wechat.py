"""微信 / 企业微信通道启动入口（根目录薄入口）。

飞书通道请使用独立的 run_feishu.py。

默认读 .env 的 CHANNEL（仅接受 wechat / wecom）；若 CHANNEL 不是这两个值，
则默认启动微信。也可以用 --channel 覆盖。

运行：
    uv run python run_wechat.py
    uv run python run_wechat.py --channel wecom
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.config import CHANNEL
from app.router import run_channel, setup_logging

log = logging.getLogger("run")


def main() -> None:
    default_channel = CHANNEL
    if default_channel not in ("wechat", "wecom"):
        default_channel = "wechat"

    parser = argparse.ArgumentParser(description="启动微信 / 企业微信通道")
    parser.add_argument(
        "--channel",
        choices=("wechat", "wecom"),
        default=default_channel,
        help="要启动的通道，默认读取 .env 的 CHANNEL",
    )
    args = parser.parse_args()

    setup_logging()
    log.info("启动通道：%s", args.channel)
    try:
        asyncio.run(run_channel(args.channel))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
