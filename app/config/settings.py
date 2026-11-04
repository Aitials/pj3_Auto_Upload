"""集中配置与环境变量读取。

所有路径类配置在这里解析为绝对路径（相对路径以项目根目录为基准），
避免模块内散落 `Path("...")` 导致移动文件或更换工作目录后失效。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def get_env(name: str, default: str = "") -> str:
    """读取环境变量（.env 已在本模块导入时加载）。"""
    return os.getenv(name, default)


def _path_from_env(name: str, default: Path) -> Path:
    value = get_env(name).strip()
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def _float_from_env(name: str, default: float) -> float:
    try:
        return float(get_env(name, str(default)))
    except ValueError:
        return default


STATE_DIR = _path_from_env("STATE_DIR", PROJECT_ROOT / "state")
CHANNEL = get_env("CHANNEL", "wechat").strip().lower()

WECHAT_BASE_URL = get_env(
    "WECHAT_BASE_URL", "https://ilinkai.weixin.qq.com"
).strip()
WECHAT_CDN_BASE = get_env(
    "WECHAT_CDN_BASE", "https://novac2c.cdn.weixin.qq.com/c2c"
).strip()
WECHAT_CHANNEL_VERSION = get_env("WECHAT_CHANNEL_VERSION", "2.1.1").strip()
WECHAT_ROUTE_TAG = get_env("WECHAT_ROUTE_TAG", "").strip()
WECHAT_LOGIN_TIMEOUT = _float_from_env("WECHAT_LOGIN_TIMEOUT", 480.0)
WECHAT_STATE_FILE = _path_from_env(
    "WECHAT_STATE_FILE", STATE_DIR / ".wechat_state.json"
)
WECHAT_QR_FILE = _path_from_env("WECHAT_QR_FILE", STATE_DIR / ".wechat_qr.html")
WECHAT_MEDIA_DIR = _path_from_env("WECHAT_MEDIA_DIR", STATE_DIR / "media")
