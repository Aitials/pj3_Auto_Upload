"""统一聊天通道启动入口（根目录薄入口）。

默认启动微信（ClawBot / iLink）：首次运行会自动用 Chrome 打开二维码页，
手机微信扫码确认后即可对话。也可以用 `--channel wecom` 或 .env 的
`CHANNEL=wecom` 启动原企业微信通道。

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
        raise SystemExit(f"CHANNEL 取值无效：{default_channel}（可选：wechat / wecom）")

    parser = argparse.ArgumentParser(description="启动聊天通道")
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
