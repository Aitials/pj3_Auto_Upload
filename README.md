# GLM 多通道智能机器人

基于 LangChain + 智谱 GLM 的多通道聊天机器人。模型调用、工具调用和消息路由共用一套 Agent 核心，对外提供飞书、企业微信、微信个人号（ClawBot / iLink）三个聊天通道，另有不依赖任何平台的本地终端调试通道。默认通道为飞书。

## 通道一览

| 通道 | 传输方式 | 启动入口 | 说明 |
|---|---|---|---|
| 飞书 `feishu` | WebSocket 长连接 | `run_feishu.py` | 默认通道；流式卡片回复，支持媒体收发 |
| 企业微信 `wecom` | 长连接 API 模式 | `run_wechat.py --channel wecom` | 当前只处理文本消息，流式回复按 1 秒节流 |
| 微信个人号 `wechat` | HTTP 长轮询 + 扫码登录 | `run_wechat.py --channel wechat` | 首次运行生成二维码；媒体下载并解密 |
| 本地终端 `local` | 无 | `run_local.py` | 调试用，不连接任何平台 |

飞书和企微的长连接都不需要公网 IP。

## 目录结构

```text
pj3_Auto_Upload/
├── app/
│   ├── agent/core.py          # Agent 核心：模型、工具、ask / astream_answer
│   ├── channels/
│   │   ├── base.py            # IncomingMessage / Channel 统一契约
│   │   ├── feishu.py          # 飞书长连接、流式卡片、媒体收发
│   │   ├── wecom.py           # 企业微信长连接
│   │   └── wechat_ilink.py    # 微信扫码登录 + 长轮询
│   ├── router/                # 通道无关的消息处理与启动调度
│   ├── config/settings.py     # .env 读取与路径配置
│   ├── tools/                 # read_skill、send_feishu_file
│   └── skills/                # 技能规范文档（TTS_skill/tts.md、video-script-splitter/skill.md）
├── docs/architecture.md       # 架构方案稿
├── tests/test_router.py       # 路由层回归测试
├── run_local.py               # 本地终端调试入口
├── run_feishu.py              # 飞书入口
├── run_wechat.py              # 微信 / 企业微信入口
├── main.py                    # 命令行单次问答示例
├── wecom_bot.py               # 企业微信兼容入口
├── pyproject.toml / uv.lock / .python-version / .env.example / README.md
├── state/                     # 运行时生成：凭证、二维码、媒体（git 忽略）
└── output/                    # 运行时生成：保存的回答等（git 忽略）
```

分层关系：通道把平台消息转换成 `IncomingMessage`，`app/router` 调用 `app/agent` 的流式回答，再把增量交回对应通道发送。`agent` 不依赖 `channels`，`channels` 只负责收发、去重和媒体落盘。

主要模块：

- [`app/agent/core.py`](app/agent/core.py) —— 用 `ChatOpenAI` 兼容写法接入智谱 GLM，提供计算器、当前时间、技能读取、飞书文件发送四个工具，以及同步 `ask()` 和流式 `astream_answer()`。
- [`app/channels/base.py`](app/channels/base.py) —— `IncomingMessage` / `MediaAttachment` / `Channel` 抽象，定义 `start`、`reply_text`、`reply_stream`、`send_typing`。
- [`app/channels/feishu.py`](app/channels/feishu.py) —— 长连接收消息、500 条消息 ID 去重、流式卡片、媒体上传下载。
- [`app/channels/wecom.py`](app/channels/wecom.py) —— 企业微信长连接，文本消息交给 Agent。
- [`app/channels/wechat_ilink.py`](app/channels/wechat_ilink.py) —— 扫码登录、`getupdates` 长轮询、消息去重、媒体 AES 解密、typing 状态。
- [`app/router/__init__.py`](app/router/__init__.py) —— `handle_message()`、`create_channel()`、`run_channel()`。
- [`app/config/settings.py`](app/config/settings.py) —— 读取 `.env`，把路径统一解析为绝对路径。
- [`app/tools/`](app/tools) —— `read_skill` 读取技能规范；`send_feishu_file` 只允许发送 `state/` 下的文件。

## 快速开始

需要先安装 [uv](https://docs.astral.sh/uv/)。项目在 `.python-version` 中固定 Python 3.12，`pyproject.toml` 用 `python-preference = only-managed` 要求 uv 使用受管 Python。

```powershell
uv sync
Copy-Item .env.example .env
# 编辑 .env，至少填入 ZHIPU_API_KEY
```

启动方式：

```powershell
uv run python run_local.py                    # 本地终端调试，不连接平台
uv run python run_feishu.py                   # 飞书（默认通道）
uv run python run_wechat.py --channel wecom   # 企业微信
uv run python run_wechat.py --channel wechat  # 微信个人号
uv run python wecom_bot.py                    # 企业微信兼容入口，等价于 --channel wecom
uv run python main.py                         # 单次问答示例（问题写死在 main.py，不是交互终端）
```

`run_wechat.py` 只启动 `wechat` / `wecom`：不带 `--channel` 时读取 `.env` 的 `CHANNEL`，若该值不是这两个之一（默认 `feishu` 就属于这种情况），会按 `wechat` 启动。飞书请直接用 `run_feishu.py`。

## 配置说明

| 变量 | 必填/可选 | 默认值 | 说明 |
|---|---|---|---|
| `ZHIPU_API_KEY` | 必填 | 无 | 智谱 API Key，占位符见 `.env.example` |
| `GLM_BASE_URL` | 可选 | `https://open.bigmodel.cn/api/paas/v4` | 智谱 OpenAI 兼容接口地址 |
| `GLM_MODEL` | 可选 | `glm-4.5-air` | 模型名称 |
| `WECOM_BOT_ID` | 使用企微时必填 | 无 | 企业微信机器人 Bot ID |
| `WECOM_BOT_SECRET` | 使用企微时必填 | 无 | 企业微信机器人 Secret |
| `FEISHU_APP_ID` | 使用飞书时必填 | 无 | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | 使用飞书时必填 | 无 | 飞书应用 App Secret |
| `CHANNEL` | 可选 | `feishu` | 仅 `run_wechat.py` 读取；只接受 `wechat` / `wecom`，其他值回退为 `wechat` |
| `WECHAT_STATE_FILE` | 可选 | `state/.wechat_state.json` | 微信登录凭证、游标、`context_token`，不要提交 |
| `WECHAT_QR_FILE` | 可选 | `state/.wechat_qr.html` | 微信二维码本地页面 |
| `WECHAT_MEDIA_DIR` | 可选 | `state/media` | 微信媒体下载目录 |
| `WECHAT_BASE_URL` | 可选 | `https://ilinkai.weixin.qq.com` | iLink 接口地址 |
| `FEISHU_MEDIA_DIR` | 可选 | `state/feishu_media` | 飞书入站媒体保存目录 |

> `.env` 已在 `.gitignore` 中忽略。文档和示例里只使用占位符，不要填入或提交真实密钥。

## 平台侧准备

### 企业微信

1. 企业微信管理后台 → **智能专区 → 智能机器人 → 创建**。
2. 选择 **API 模式**，并选择 **使用长连接**。
3. 获取 Bot ID 和 Secret，填入 `.env` 的 `WECOM_BOT_ID` / `WECOM_BOT_SECRET`。
4. 启动 `uv run python run_wechat.py --channel wecom`。

### 飞书

1. 在飞书开发者后台创建企业自建应用。
2. 在「凭证与基础信息」获取 App ID / App Secret，填入 `.env` 的 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`。
3. 为应用添加「机器人」能力。
4. 在「事件与回调」中选择**使用长连接接收事件**，订阅 `im.message.receive_v1`。
5. 开通所需权限，至少覆盖接收私聊消息、以机器人身份发送消息、读取消息资源、卡片流式更新，例如 `im:message.p2p_msg:readonly`、`im:message:send_as_bot`、`im:message`、`im:resource`、`cardkit:card:write`（具体名称以控制台可申请项为准）。
6. 发布应用版本，并确保可用范围包含测试账号。

飞书发送媒体受平台限制：文件上限 30 MB、图片上限 10 MB，视频仅支持 mp4，语音仅支持 opus。

## 能力与边界

已实现：

- 文本对话、流式输出；工具包括当前时间、四则计算器、技能规范读取、飞书本地文件发送。
- 飞书：文本、富文本、图片、文件、音视频入站；流式卡片回复；图片/文件/视频/语音发送（限 `state/` 目录、受平台大小和格式限制）。
- 企业微信：文本消息入站与流式回复。
- 微信个人号：扫码登录、文本及图片/语音/文件/视频入站、媒体下载解密、typing 状态、文本回复（收齐后一次性发送）。
- 本地终端：`/paste` 多行粘贴、`/textfile` 读取 UTF-8 文本、`/file` 模拟附件、`/save` 保存上一条回答、`/help`、`/quit`。`/file` 只把路径交给 Agent，不读取文件内容。

未实现 / 不要误用：

- `app/skills/TTS_skill/tts.md` 和 `app/skills/video-script-splitter/skill.md` **目前只有规范文档，没有实现代码**；`app/skills/*/__init__.py` 是空文件。
- Agent 的工具里**没有写文件工具**，因此不能生成音频文件，也不能把拆句结果导出为 txt 文件；`read_skill` 只能加载规范文本。
- 当前模型是文本模型，图片、文件、视频只会落盘并把路径交给 Agent，不做视觉理解。
- `requirements.txt` 与 `pyproject.toml` 内容重复，且缺少 `lark-oapi`，不适合作为安装来源；请统一使用 `uv sync`。

## 已知限制

- 单个通道按消息到达顺序串行处理，生成回答期间收到的新消息会排队；微信长轮询下尤其明显。
- 飞书入站队列使用无上限的 `asyncio.Queue`，持续高频消息可能堆积。
- 只有模型调用级超时（60 秒，重试 2 次），没有针对一次用户请求的总超时。
- 发送侧没有自动重试；失败通常只记日志或降级为普通文本。
- 微信依赖入站消息产生的 `context_token`，会话过期（`-14`）后需要重新扫码；语音 SILK 文件未做转码。

## 开发与测试

```powershell
uv run pytest
```

[`tests/test_router.py`](tests/test_router.py) 是路由层回归测试，不依赖真实通道和真实模型：会替换 `astream_answer`，验证流式回复、typing 开关和 `run_channel` 的处理器绑定。

[`docs/architecture.md`](docs/architecture.md) 是面向“视频内容 Agent”的架构方案稿，其中的 `app/jobs`、`app/pipelines`、`app/capabilities`、`app/artifacts`、`app/delivery` 等目录**尚未实现**；当前实际代码只有 `channels` → `router` → `agent` 这条链路。
