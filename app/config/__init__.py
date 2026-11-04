"""应用配置包。"""

from __future__ import annotations

from .settings import (
    CHANNEL,
    PROJECT_ROOT,
    STATE_DIR,
    WECHAT_BASE_URL,
    WECHAT_CDN_BASE,
    WECHAT_CHANNEL_VERSION,
    WECHAT_LOGIN_TIMEOUT,
    WECHAT_MEDIA_DIR,
    WECHAT_QR_FILE,
    WECHAT_ROUTE_TAG,
    WECHAT_STATE_FILE,
    get_env,
)

__all__ = [
    "CHANNEL",
    "PROJECT_ROOT",
    "STATE_DIR",
    "WECHAT_BASE_URL",
    "WECHAT_CDN_BASE",
    "WECHAT_CHANNEL_VERSION",
    "WECHAT_LOGIN_TIMEOUT",
    "WECHAT_MEDIA_DIR",
    "WECHAT_QR_FILE",
    "WECHAT_ROUTE_TAG",
    "WECHAT_STATE_FILE",
    "get_env",
]
