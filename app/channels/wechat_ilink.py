"""微信 ClawBot / iLink 通道实现。

协议要点（已按官方 npm 插件与第三方协议文档核对）：
- 登录：GET /ilink/bot/get_bot_qrcode?bot_type=3，轮询 /ilink/bot/get_qrcode_status
- 收消息：POST /ilink/bot/getupdates（长轮询约 35 秒，get_updates_buf 游标）
- 发消息：POST /ilink/bot/sendmessage（必须带 context_token）
- 输入状态：POST /ilink/bot/getconfig + /ilink/bot/sendtyping
- 会话过期：errcode/ret == -14，必须重新扫码
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import io
import json
import logging
import mimetypes
import os
import re
import shutil
import struct
import subprocess
import time
import uuid
import webbrowser
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from app.channels.base import Channel, IncomingMessage, MediaAttachment, MessageHandler
from app.config import (
    WECHAT_BASE_URL,
    WECHAT_CDN_BASE,
    WECHAT_CHANNEL_VERSION,
    WECHAT_LOGIN_TIMEOUT,
    WECHAT_MEDIA_DIR,
    WECHAT_QR_FILE,
    WECHAT_ROUTE_TAG,
    WECHAT_STATE_FILE,
)

log = logging.getLogger("wechat")

# 二维码请求固定走官方登录网关（协议要求，不随业务 base_url 变化）。
FIXED_QR_BASE = "https://ilinkai.weixin.qq.com"
ILINK_APP_ID = "bot"
BOT_TYPE = "3"
MAX_QR_REFRESH = 3
LONG_POLL_TIMEOUT = 40.0
API_TIMEOUT = 15.0
CONFIG_TIMEOUT = 10.0
TYPING_TICKET_TTL = 24 * 3600

_MEDIA_KINDS = {2: "image", 3: "voice", 4: "file", 5: "video"}
_MEDIA_FIELDS = {2: "image_item", 3: "voice_item", 4: "file_item", 5: "video_item"}
_DEFAULT_SUFFIX = {"image": ".jpg", "voice": ".silk", "file": ".bin", "video": ".mp4"}


def _find_chrome() -> str | None:
    """按常见安装路径、PATH、Windows 注册表顺序探测 Chrome。"""
    candidates: list[Path] = []
    for env_name, subdir in (
        ("PROGRAMFILES", "Google/Chrome/Application/chrome.exe"),
        ("PROGRAMFILES(X86)", "Google/Chrome/Application/chrome.exe"),
        ("LOCALAPPDATA", "Google/Chrome/Application/chrome.exe"),
    ):
        base = os.getenv(env_name)
        if base:
            candidates.append(Path(base) / subdir)

    for path in candidates:
        if path.is_file():
            return str(path)

    for name in ("chrome", "chrome.exe", "google-chrome", "google-chrome-stable", "chromium"):
        found = shutil.which(name)
        if found:
            return found

    if os.name == "nt":
        try:
            import winreg

            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(
                        root,
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe",
                    ) as key:
                        value, _ = winreg.QueryValueEx(key, "")
                    if value and Path(value).is_file():
                        return str(value)
                except OSError:
                    continue
        except Exception:  # noqa: BLE001
            pass

    return None


def _open_target(local_path: Path | None, url: str) -> tuple[bool, bool]:
    """打开本地二维码页/图片或远程链接，返回（是否使用 Chrome，是否成功打开）。"""
    chrome = _find_chrome()
    target = url
    if chrome and local_path is not None:
        # 附加时间戳，让 Chrome 每次按新地址打开，避免命中旧缓存。
        target = f"{local_path.as_uri()}?t={int(time.time() * 1000)}"

    try:
        if chrome:
            subprocess.Popen(
                [chrome, target],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True, True
        if local_path is not None and os.name == "nt":
            os.startfile(str(local_path))  # type: ignore[attr-defined]
            return False, True
        return False, bool(webbrowser.open(url))
    except Exception:  # noqa: BLE001
        try:
            return False, bool(webbrowser.open(url))
        except Exception:  # noqa: BLE001
            return False, False


def _qr_svg(content: str) -> str:
    """把接口返回的二维码内容渲染为内联 SVG。"""
    import qrcode
    from qrcode.image.svg import SvgPathImage

    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(content)
    qr.make(fit=True)
    buffer = io.BytesIO()
    qr.make_image(image_factory=SvgPathImage).save(buffer)
    text = buffer.getvalue().decode("utf-8")
    # 去掉 XML 声明，便于直接内联进 HTML。
    return re.sub(r"^<\?xml.*?\?>\s*", "", text, count=1, flags=re.DOTALL)


def _qr_html(content: str, svg: str) -> str:
    escaped = html.escape(content)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>微信扫码登录</title>
<style>
  body {{
    margin: 0; min-height: 100vh; display: flex; align-items: center;
    justify-content: center; background: #f2f3f5; color: #1f2329;
    font-family: system-ui, -apple-system, "Microsoft YaHei", sans-serif;
  }}
  .card {{
    background: #fff; padding: 32px 40px; border-radius: 16px;
    box-shadow: 0 8px 32px rgba(0, 0, 0, .08); text-align: center;
    max-width: 560px;
  }}
  h1 {{ font-size: 22px; margin: 0 0 8px; }}
  .sub {{ color: #646a73; margin: 0 0 20px; }}
  .qr svg {{ width: 300px; height: 300px; }}
  .tip {{ margin: 18px 0 0; font-size: 14px; color: #1f2329; }}
  .url {{ margin: 18px 0 0; font-size: 12px; color: #8f959e; word-break: break-all; }}
  .url a {{ color: #07c160; text-decoration: none; }}
</style>
</head>
<body>
  <div class="card">
    <h1>微信扫码登录</h1>
    <p class="sub">请用手机微信扫一扫，并在手机上确认授权</p>
    <div class="qr">{svg}</div>
    <p class="tip">登录成功后即可关闭本页；二维码过期会自动刷新。</p>
    <p class="url">兜底链接：<a href="{escaped}" target="_blank" rel="noreferrer">{escaped}</a></p>
  </div>
</body>
</html>
"""


class ILinkError(RuntimeError):
    """iLink 接口错误。"""


class SessionExpired(ILinkError):
    """errcode/ret == -14：会话过期，需要重新扫码。"""


@dataclass
class WeChatState:
    """需要跨重启保存的微信通道状态。"""

    bot_token: str = ""
    base_url: str = WECHAT_BASE_URL
    account_id: str = ""
    user_id: str = ""
    get_updates_buf: str = ""
    context_tokens: dict[str, str] = field(default_factory=dict)
    seen_ids: list[str] = field(default_factory=list)


def _build_client_version(version: str) -> int:
    """把 x.y.z 编码成 iLink-App-ClientVersion 需要的 uint32（0x00MMNNPP）。"""
    parts = (version.split(".") + ["0", "0", "0"])[:3]
    try:
        major, minor, patch = (int(p) for p in parts)
    except ValueError:
        major, minor, patch = 0, 0, 0
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


def _random_wechat_uin() -> str:
    """X-WECHAT-UIN：随机 uint32 -> 十进制字符串 -> base64。"""
    uint32 = struct.unpack(">I", os.urandom(4))[0]
    return base64.b64encode(str(uint32).encode()).decode()


def _parse_aes_key(value: str, extra_hex: str = "") -> bytes:
    """解析 CDNMedia.aes_key / image_item.aeskey 为 16 字节密钥。"""
    if extra_hex and len(extra_hex) == 32:
        return bytes.fromhex(extra_hex)
    raw = base64.b64decode(value)
    if len(raw) == 16:
        return raw
    if len(raw) == 32 and re.fullmatch(rb"[0-9a-fA-F]{32}", raw):
        return bytes.fromhex(raw.decode("ascii"))
    raise ValueError(f"无法解析 AES key（解码后 {len(raw)} 字节）")


def _decrypt_aes_ecb(ciphertext: bytes, key: bytes) -> bytes:
    """AES-128-ECB + PKCS7 解密。"""
    decryptor = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def _safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n]+', "_", name).strip()
    return name[:80]


def _to_plain_text(text: str) -> str:
    """微信不渲染完整 Markdown，发送前做最小化降级。"""
    text = re.sub(r"```[^\n]*\n?([\s\S]*?)```", lambda m: m.group(1).strip(), text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    return text.strip()


def _load_state(path: Path) -> WeChatState:
    if not path.exists():
        return WeChatState()
    try:
        data = json.loads(path.read_text("utf-8"))
        if not isinstance(data, dict):
            return WeChatState()
        state = WeChatState()
        for key in ("bot_token", "base_url", "account_id", "user_id", "get_updates_buf"):
            value = data.get(key)
            if isinstance(value, str):
                setattr(state, key, value)
        tokens = data.get("context_tokens")
        if isinstance(tokens, dict):
            state.context_tokens = {
                str(k): str(v) for k, v in tokens.items() if isinstance(v, str)
            }
        seen = data.get("seen_ids")
        if isinstance(seen, list):
            state.seen_ids = [str(x) for x in seen][-200:]
        return state
    except Exception as exc:  # noqa: BLE001
        log.warning("读取微信状态文件失败（%s），将使用空状态：%s", path, exc)
        return WeChatState()


class WeChatILinkChannel(Channel):
    """微信 iLink（HTTP 长轮询）通道。"""

    name = "wechat"

    def __init__(self) -> None:
        self.state_path = WECHAT_STATE_FILE
        self.media_dir = WECHAT_MEDIA_DIR
        self.channel_version = WECHAT_CHANNEL_VERSION
        self.client_version = _build_client_version(self.channel_version)
        self._route_tag = WECHAT_ROUTE_TAG
        self._http: httpx.AsyncClient | None = None
        self._typing_cache: dict[str, tuple[str, float]] = {}
        self.state = _load_state(self.state_path)
        self.qr_file = WECHAT_QR_FILE

        if WECHAT_BASE_URL.rstrip("/") != FIXED_QR_BASE:
            self.base_url = WECHAT_BASE_URL.rstrip("/")
        elif self.state.base_url:
            self.base_url = self.state.base_url.rstrip("/")
        else:
            self.base_url = WECHAT_BASE_URL.rstrip("/")

        self.cdn_base = WECHAT_CDN_BASE.rstrip("/")

    # ------------------------------------------------------------------ HTTP

    def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0), follow_redirects=True
            )
        return self._http

    def _common_headers(self) -> dict[str, str]:
        headers = {
            "iLink-App-Id": ILINK_APP_ID,
            "iLink-App-ClientVersion": str(self.client_version),
        }
        if self._route_tag:
            headers["SKRouteTag"] = self._route_tag
        return headers

    def _post_headers(self) -> dict[str, str]:
        headers = self._common_headers()
        headers.update(
            {
                "Content-Type": "application/json",
                "AuthorizationType": "ilink_bot_token",
                "X-WECHAT-UIN": _random_wechat_uin(),
            }
        )
        if self.state.bot_token:
            headers["Authorization"] = f"Bearer {self.state.bot_token}"
        return headers

    @staticmethod
    def _parse_response(raw_text: str, label: str) -> dict:
        text = raw_text.strip()
        if not text:
            return {}
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ILinkError(f"{label} 返回非 JSON：{text[:200]}") from exc
        if not isinstance(data, dict):
            raise ILinkError(f"{label} 返回格式异常：{text[:200]}")

        errcode = data.get("errcode")
        ret = data.get("ret")
        if errcode == -14 or ret == -14:
            raise SessionExpired("微信会话已过期（-14）")
        if ret not in (None, 0):
            raise ILinkError(f"{label} 失败：ret={ret} errmsg={data.get('errmsg')}")
        if errcode not in (None, 0):
            raise ILinkError(f"{label} 失败：errcode={errcode} errmsg={data.get('errmsg')}")
        return data

    async def _get(
        self, endpoint: str, timeout: float, base_url: str | None = None
    ) -> dict:
        base = (base_url or self.base_url).rstrip("/")
        url = f"{base}/{endpoint}"
        response = await self._client().get(
            url, headers=self._common_headers(), timeout=timeout
        )
        if response.status_code >= 400:
            raise ILinkError(
                f"GET {endpoint} HTTP {response.status_code}: {response.text[:200]}"
            )
        return self._parse_response(response.text, f"GET {endpoint}")

    async def _post(
        self, endpoint: str, payload: dict, timeout: float = API_TIMEOUT
    ) -> dict:
        body = {**payload, "base_info": {"channel_version": self.channel_version}}
        body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        url = f"{self.base_url.rstrip('/')}/{endpoint}"
        response = await self._client().post(
            url, content=body_bytes, headers=self._post_headers(), timeout=timeout
        )
        if response.status_code >= 400:
            raise ILinkError(
                f"POST {endpoint} HTTP {response.status_code}: {response.text[:200]}"
            )
        return self._parse_response(response.text, f"POST {endpoint}")

    # ------------------------------------------------------------------ 状态

    def _save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(asdict(self.state), ensure_ascii=False, indent=2), "utf-8"
        )
        os.replace(tmp, self.state_path)

    def _remember(self, key: str) -> None:
        self.state.seen_ids.append(key)
        if len(self.state.seen_ids) > 200:
            self.state.seen_ids = self.state.seen_ids[-200:]

    # ------------------------------------------------------------------ 扫码

    def _show_qr(self, content: str) -> None:
        """生成本地二维码页并在浏览器中打开；终端只留提示和兜底链接。"""
        if not content:
            log.warning("未获取到二维码内容，请重新运行")
            return

        self.qr_file.parent.mkdir(parents=True, exist_ok=True)
        local_path: Path | None = None
        page_url = content
        fallback = content

        try:
            if content.startswith("data:image/"):
                header, _, payload = content.partition(",")
                mime = header[5:].split(";", 1)[0] or "image/png"
                suffix = mimetypes.guess_extension(mime) or ".png"
                local_path = self.qr_file.with_suffix(suffix)
                local_path.write_bytes(base64.b64decode(payload))
                page_url = local_path.as_uri()
                fallback = str(local_path)
            else:
                svg = _qr_svg(content)
                local_path = self.qr_file
                local_path.write_text(_qr_html(content, svg), "utf-8")
                page_url = local_path.as_uri()
        except ImportError:
            log.warning("未安装 qrcode，改为直接打开二维码链接")
        except Exception as exc:  # noqa: BLE001
            log.warning("生成二维码页面失败：%s；改为直接打开链接", exc)

        used_chrome, opened = _open_target(local_path, page_url)
        if opened:
            if used_chrome:
                log.info("已打开 Chrome，请用手机微信扫码确认")
            else:
                log.info("已自动打开浏览器，请用手机微信扫码确认")
        else:
            log.warning("自动打开浏览器失败，请手动打开：%s", page_url)

        print(f"二维码链接（可复制兜底）：{fallback}", flush=True)

    async def _fetch_qr(self) -> tuple[str, str]:
        data = await self._get(
            f"ilink/bot/get_bot_qrcode?bot_type={BOT_TYPE}",
            timeout=API_TIMEOUT,
            base_url=FIXED_QR_BASE,
        )
        qrcode = data.get("qrcode")
        content = data.get("qrcode_img_content") or ""
        if not qrcode:
            raise ILinkError("获取二维码失败：响应缺少 qrcode")
        return str(qrcode), str(content)

    async def login(self, timeout: float | None = None) -> None:
        """扫码登录并在成功后落地凭证。"""
        total = timeout if timeout is not None else WECHAT_LOGIN_TIMEOUT
        deadline = time.monotonic() + max(total, 5.0)

        qrcode, content = await self._fetch_qr()
        self._show_qr(content)
        status_base = FIXED_QR_BASE
        refresh_count = 0
        scanned_printed = False

        while time.monotonic() < deadline:
            try:
                status = await self._get(
                    f"ilink/bot/get_qrcode_status?qrcode={quote(qrcode)}",
                    timeout=LONG_POLL_TIMEOUT,
                    base_url=status_base,
                )
            except httpx.TimeoutException:
                await asyncio.sleep(1.0)
                continue
            except ILinkError as exc:
                log.warning("查询扫码状态失败：%s（2 秒后重试）", exc)
                await asyncio.sleep(2.0)
                continue

            state = status.get("status", "wait")
            if state == "wait":
                await asyncio.sleep(1.0)
            elif state == "scaned":
                if not scanned_printed:
                    log.info("已扫码，请在手机上确认登录")
                    scanned_printed = True
                await asyncio.sleep(1.0)
            elif state == "scaned_but_redirect":
                host = status.get("redirect_host")
                if host:
                    status_base = f"https://{host}"
                    log.info("扫码状态触发 IDC 重定向：%s", host)
                else:
                    log.warning("收到 scaned_but_redirect 但缺少 redirect_host，继续当前地址")
                await asyncio.sleep(1.0)
            elif state == "expired":
                refresh_count += 1
                if refresh_count > MAX_QR_REFRESH:
                    raise ILinkError("二维码多次过期，请重新运行程序")
                log.info("二维码已过期，刷新中（%s/%s）", refresh_count, MAX_QR_REFRESH)
                qrcode, content = await self._fetch_qr()
                status_base = FIXED_QR_BASE
                scanned_printed = False
                self._show_qr(content)
            elif state == "confirmed":
                token = str(status.get("bot_token") or "").strip()
                if token.lower().startswith("bearer "):
                    token = token[7:].strip()
                account_id = str(status.get("ilink_bot_id") or "").strip()
                if not token or not account_id:
                    raise ILinkError("登录已确认，但响应缺少 bot_token / ilink_bot_id")

                baseurl = str(status.get("baseurl") or "").strip()
                self.state.bot_token = token
                self.state.account_id = account_id
                self.state.user_id = str(status.get("ilink_user_id") or "")
                if baseurl:
                    self.base_url = baseurl.rstrip("/")
                    self.state.base_url = self.base_url
                # 新会话必须从空游标开始，并丢弃旧的 context_token
                self.state.get_updates_buf = ""
                self.state.context_tokens.clear()
                self.state.seen_ids.clear()
                self._save()
                log.info(
                    "微信登录成功：account=%s；凭证已保存到 %s",
                    account_id,
                    self.state_path,
                )
                return
            else:
                log.debug("未知扫码状态：%s", state)
                await asyncio.sleep(1.0)

        raise ILinkError("登录超时，未完成扫码确认")

    async def _handle_session_expired(self) -> None:
        if not self.state.bot_token:
            return
        log.error("=" * 72)
        log.error("微信会话已过期（errcode=-14）。旧凭证、游标和 context_token 已清除。")
        log.error("请用手机微信扫描接下来生成的新二维码重新授权。")
        log.error("=" * 72)
        self.state.bot_token = ""
        self.state.base_url = WECHAT_BASE_URL
        self.state.get_updates_buf = ""
        self.state.context_tokens.clear()
        self.state.seen_ids.clear()
        self._typing_cache.clear()
        self._save()
        self.base_url = WECHAT_BASE_URL.rstrip("/")

    # ------------------------------------------------------------------ 收消息

    async def start(self, on_message: MessageHandler) -> None:
        backoff = 2.0
        while True:
            if not self.state.bot_token:
                try:
                    await self.login()
                except Exception as exc:  # noqa: BLE001
                    log.error("扫码登录失败：%s；60 秒后重试", exc)
                    await asyncio.sleep(60)
                    continue

            try:
                data = await self._get_updates()
                cursor = data.get("get_updates_buf")
                if isinstance(cursor, str) and cursor != self.state.get_updates_buf:
                    self.state.get_updates_buf = cursor
                    self._save()

                for raw in data.get("msgs") or []:
                    if not isinstance(raw, dict):
                        continue
                    message = await self._to_incoming(raw)
                    if message:
                        await on_message(message)
                backoff = 2.0
            except SessionExpired:
                await self._handle_session_expired()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("拉取消息失败：%s；%s 秒后重试", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _get_updates(self) -> dict:
        try:
            return await self._post(
                "ilink/bot/getupdates",
                {"get_updates_buf": self.state.get_updates_buf},
                timeout=LONG_POLL_TIMEOUT,
            )
        except httpx.TimeoutException:
            # 服务端挂起 35 秒，客户端超时属于正常的空轮询。
            return {"ret": 0, "msgs": [], "get_updates_buf": self.state.get_updates_buf}

    @staticmethod
    def _message_key(raw: dict) -> str:
        message_id = raw.get("message_id")
        if message_id not in (None, ""):
            return f"mid:{message_id}"
        seq = raw.get("seq")
        if seq not in (None, ""):
            return f"seq:{seq}:{raw.get('create_time_ms')}"
        digest = hashlib.md5(
            json.dumps(raw, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return f"hash:{digest}"

    async def _to_incoming(self, raw: dict) -> IncomingMessage | None:
        if raw.get("message_type") == 2:
            # BOT 自己发出的消息，跳过以避免回环。
            return None

        user_id = str(raw.get("from_user_id") or "")
        if not user_id:
            return None

        key = self._message_key(raw)
        if key in self.state.seen_ids:
            return None

        context_token = str(raw.get("context_token") or "")
        if context_token:
            self.state.context_tokens[user_id] = context_token

        texts: list[str] = []
        media: list[MediaAttachment] = []
        for index, item in enumerate(raw.get("item_list") or []):
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == 1:
                text = str((item.get("text_item") or {}).get("text") or "").strip()
                if text:
                    texts.append(text)
            elif item_type in _MEDIA_KINDS:
                attachment = await self._download_item(item, int(item_type), key, index)
                if attachment:
                    media.append(attachment)

        text = "\n".join(texts).strip()
        if media:
            notes = []
            for attachment in media:
                if attachment.kind == "voice" and attachment.text:
                    notes.append(
                        f"[语音] 转写：{attachment.text}；文件：{attachment.path or '（下载失败）'}"
                    )
                elif attachment.path:
                    notes.append(f"[{attachment.kind}] 已保存到本地：{attachment.path}")
                else:
                    notes.append(f"[{attachment.kind}] 下载失败，详见日志")
            text = f"{text}\n\n{chr(10).join(notes)}" if text else "\n".join(notes)

        if not text:
            return None

        self._remember(key)
        self._save()
        return IncomingMessage(
            channel=self.name,
            message_id=key,
            user_id=user_id,
            text=text,
            media=media,
            raw=raw,
        )

    # ------------------------------------------------------------------ 媒体

    async def _download_item(
        self, item: dict, item_type: int, key: str, index: int
    ) -> MediaAttachment | None:
        kind = _MEDIA_KINDS[item_type]
        data = item.get(_MEDIA_FIELDS[item_type]) or {}
        if not isinstance(data, dict):
            data = {}

        media = data.get("media") if isinstance(data.get("media"), dict) else None
        file_name = str(data.get("file_name") or "")
        extra_hex = str(data.get("aeskey") or "")

        try:
            if media:
                content = await self._fetch_and_decrypt(media, extra_hex)
            elif kind == "image" and data.get("url"):
                content = await self._fetch_plain(str(data["url"]))
            else:
                raise ILinkError("媒体缺少 CDN 引用")

            path = self._save_media(content, kind, file_name, key, index)
            return MediaAttachment(
                kind=kind,
                path=str(path),
                file_name=file_name,
                text=str(data.get("text") or "") if kind == "voice" else "",
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("媒体下载失败（%s）：%s", kind, exc)
            return MediaAttachment(kind=kind, path="", file_name=file_name)

    async def _fetch_and_decrypt(self, media: dict, extra_hex: str) -> bytes:
        url = str(media.get("full_url") or "")
        if not url:
            param = str(media.get("encrypt_query_param") or "")
            if not param:
                raise ILinkError("媒体缺少 full_url / encrypt_query_param")
            url = f"{self.cdn_base}/download?encrypted_query_param={quote(param)}"

        encrypted = await self._fetch_plain(url)
        aes_key = extra_hex or str(media.get("aes_key") or "")
        if not aes_key:
            return encrypted
        return _decrypt_aes_ecb(encrypted, _parse_aes_key(aes_key, extra_hex))

    async def _fetch_plain(self, url: str) -> bytes:
        response = await self._client().get(url, timeout=60.0)
        if response.status_code >= 400:
            raise ILinkError(f"CDN 下载 HTTP {response.status_code}")
        return response.content

    def _save_media(
        self, content: bytes, kind: str, file_name: str, key: str, index: int
    ) -> Path:
        self.media_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(file_name).suffix or _DEFAULT_SUFFIX[kind]
        safe = _safe_name(file_name) or f"{kind}_{index}{suffix}"
        if not Path(safe).suffix:
            safe += suffix
        prefix = re.sub(r"[^0-9A-Za-z_-]+", "_", key)[:20]
        filename = f"{datetime.now():%Y%m%d_%H%M%S}_{prefix}_{index}_{safe}"
        path = self.media_dir / filename
        path.write_bytes(content)
        return path

    # ------------------------------------------------------------------ 发消息

    async def reply_text(self, message: IncomingMessage, text: str) -> None:
        token = self.state.context_tokens.get(message.user_id)
        if not token and isinstance(message.raw, dict):
            token = str(message.raw.get("context_token") or "")

        msg: dict[str, Any] = {
            "to_user_id": message.user_id,
            "client_id": f"pj3-{uuid.uuid4().hex}",
            "message_type": 2,
            "message_state": 2,
            "item_list": [
                {
                    "type": 1,
                    "text_item": {"text": _to_plain_text(text) or "（没有生成内容）"},
                }
            ],
        }
        if token:
            msg["context_token"] = token

        try:
            await self._post("ilink/bot/sendmessage", {"msg": msg}, timeout=API_TIMEOUT)
        except SessionExpired:
            await self._handle_session_expired()
            raise

    async def reply_stream(self, message: IncomingMessage, chunks) -> str:
        answer = ""
        last_typing = time.monotonic()
        try:
            async for delta in chunks:
                answer += delta
                if time.monotonic() - last_typing >= 10.0:
                    await self.send_typing(message, True)
                    last_typing = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            log.exception("Agent 调用失败")
            answer = answer or f"抱歉，处理失败了：{exc}"

        # 微信 sendmessage 没有可靠的逐字更新能力，这里收齐后一次性发送。
        await self.reply_text(message, answer or "（没有生成内容）")
        return answer

    # ------------------------------------------------------------------ typing

    async def _typing_ticket(self, user_id: str, context_token: str) -> str:
        cached = self._typing_cache.get(user_id)
        if cached and time.monotonic() - cached[1] < TYPING_TICKET_TTL:
            return cached[0]
        data = await self._post(
            "ilink/bot/getconfig",
            {"ilink_user_id": user_id, "context_token": context_token or ""},
            timeout=CONFIG_TIMEOUT,
        )
        ticket = str(data.get("typing_ticket") or "")
        if ticket:
            self._typing_cache[user_id] = (ticket, time.monotonic())
        return ticket

    async def send_typing(self, message: IncomingMessage, on: bool = True) -> None:
        try:
            context_token = self.state.context_tokens.get(message.user_id, "")
            ticket = await self._typing_ticket(message.user_id, context_token)
            if not ticket:
                return
            await self._post(
                "ilink/bot/sendtyping",
                {
                    "ilink_user_id": message.user_id,
                    "typing_ticket": ticket,
                    "status": 1 if on else 2,
                },
                timeout=CONFIG_TIMEOUT,
            )
        except SessionExpired:
            raise
        except Exception as exc:  # noqa: BLE001
            log.debug("typing 状态发送失败（忽略）：%s", exc)
