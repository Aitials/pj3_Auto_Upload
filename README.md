# GLM 智能机器人（飞书 / 企业微信 / 微信）

基于 LangChain + 智谱 GLM-4.5-Air 的最小 Agent，支持三个聊天通道：

- **飞书企业自建应用机器人**：WebSocket 长连接，无需公网 IP（当前默认通道）
- **企业微信智能机器人**：长连接 API 模式，无需公网 IP（可用 `--channel wecom` 启动）
- **微信 ClawBot / iLink**：扫码登录 + HTTP 长轮询，无需公网 IP

## 目录结构

```text
pj3_Auto_Upload-main/
├── app/
│   ├── agent/            # Agent 核心（模型、工具、ask/astream_answer）
│   ├── router/           # 通道无关的消息路由
│   ├── channels/         # 通道契约 + 飞书 / 企业微信 / 微信 iLink 实现
│   └── config/           # .env 读取与路径集中配置
├── state/                # 运行态：凭证、二维码页、媒体（git 忽略）
├── logs/                 # 运行日志（git 忽略）
├── docs/                 # 架构方案
├── tests/                # 路由回归测试
├── run_feishu.py         # 飞书启动入口（根目录薄入口）
├── run_wechat.py         # 微信 / 企业微信启动入口
├── run_local.py          # 本地终端测试入口（不经过飞书/企微）
├── wecom_bot.py          # 企业微信启动入口（根目录薄入口）
├── main.py               # 命令行 Agent 调试入口
└── pyproject.toml / uv.lock / .python-version / README.md / .gitignore
```

## 主要模块

- `app/agent/core.py` —— 模型、工具（时间/计算器）、同步 `ask()` 与流式 `astream_answer()`
- `app/router/__init__.py` —— 调用 Agent，把回复交回通道
- `app/channels/base.py` —— 通道统一接口：`IncomingMessage` / `Channel`
- `app/channels/feishu.py` —— 飞书通道：长连接、流式卡片、媒体收发、消息去重
- `app/channels/wecom.py` —— 企业微信通道实现（流式回复按 1 秒节流）
- `app/channels/wechat_ilink.py` —— 微信 iLink 通道：扫码登录、长轮询、去重、媒体下载解密、`-14` 重扫提示
- `app/config/settings.py` —— `.env` 读取与所有路径常量
- `run_feishu.py` / `run_wechat.py` / `run_local.py` / `wecom_bot.py` / `main.py` —— 根目录薄入口

## 配置

```powershell
copy .env.example .env
```

在 `.env` 中填入：

- `ZHIPU_API_KEY`：https://open.bigmodel.cn/usercenter/apikeys
- `CHANNEL`：默认 `feishu`；`run_wechat.py` 接受 `wechat` / `wecom`，飞书请用 `run_feishu.py`
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`：仅飞书通道需要
- `WECOM_BOT_ID` / `WECOM_BOT_SECRET`：仅企微通道需要
- 微信通道可选配置：
  - `WECHAT_STATE_FILE`：状态文件，默认 `state/.wechat_state.json`（含 `bot_token`、游标、`context_token`，不要提交）
  - `WECHAT_QR_FILE`：二维码本地页面，默认 `state/.wechat_qr.html`
  - `WECHAT_MEDIA_DIR`：媒体下载目录，默认 `state/media/`
  - `STATE_DIR`：运行态根目录，默认 `state/`
  - `WECHAT_BASE_URL`：默认 `https://ilinkai.weixin.qq.com`

## 运行

```powershell
uv sync

uv run python run_feishu.py                   # 启动飞书（当前默认）
uv run python run_wechat.py --channel wecom   # 启动企业微信
uv run python run_wechat.py --channel wechat  # 启动微信；首次运行会自动打开 Chrome 显示二维码
uv run python run_local.py                    # 本地终端测试，不经过飞书/企微
uv run python wecom_bot.py                    # 企微原入口，仍然可用
uv run python main.py                         # 命令行测试
```

## 本地终端测试

```powershell
uv run python run_local.py
```

支持：

- 直接输入文本，流式打印 Agent 回复；
- `/paste`：进入多行粘贴模式，粘贴完成后单独输入 `/end`，适合长文案；
- `/textfile <路径>`：读取 UTF-8 文本文件并作为一条消息发送；
- `/file <路径> [| 问题]`：模拟发送本地附件；
- `/save`：把上一条 AI 回答保存到 `output/terminal_answer_*.txt`；
- `/help`：查看命令；
- `/quit`：退出。

说明：

- 终端本身可以鼠标选中复制；但如果直接向单行输入框粘贴多行长文，会被 `input()` 按行拆成多条消息，所以长文请用 `/paste` 或 `/textfile`；
- 长回答建议用 `/save` 落盘后再从 txt 复制；
- `/file` 只模拟“通道收到附件并把路径交给 Agent”，不会读取文件内容；Agent 仍是文本模型。

## 飞书通道：启动前检查

1. 应用类型为企业自建应用，并已添加“机器人”能力；
2. 事件与回调 → 事件订阅方式选“使用长连接接收事件”，订阅 `im.message.receive_v1`；
3. 权限至少开通：`im:message.p2p_msg:readonly`、`im:message:send_as_bot`、`im:message`、`im:resource`、`cardkit:card:write`；
4. 应用版本已发布，可用范围包含你自己；
5. 在 `.env` 填入 `FEISHU_APP_ID` / `FEISHU_APP_SECRET`。

飞书入站媒体保存到 `state/feishu_media/`；发送文件/图片/视频由 Agent 调用 `send_feishu_file` 工具完成，只允许发送 `state/` 下的文件。飞书 IM 文件上限 30 MB、图片 10 MB，视频仅支持 mp4。

## 飞书通道尝试记录

日期：2026-11-06

尝试用飞书企业自建应用接入：

- 应用：`cli_aa465da732b89bd8`（应用名“壕z的飞书 CLI”，账号显示“正式应用@豆包账号”）；
- 长连接可以建立，能收到用户消息，Agent 也能正常调用模型；
- 但发送回复时获取 `tenant_access_token` 失败：

```text
ObtainAccessTokenException:
code: 21001
msg: Access denied due to platform policy
```

- 改成纯文本回复后仍然失败，排除流式卡片因素；
- 应用本身已启用、1.0.0 已发布，因此判断为账号/应用类型的平台策略限制，不是通道代码问题。

结论：暂停飞书通道，默认通道改回企业微信。飞书代码仍保留在 `app/channels/feishu.py` 和 `run_feishu.py`；后续如果切换到标准企业飞书租户，并创建权限齐全的机器人应用，填入新的 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 即可恢复。

后续：2026-11-06 创建测试企业并换成新应用 `cli_aa67c9646bb81d07` 后，`tenant_access_token` 已能正常获取（`code: 0`），飞书通道重新启用并设为默认。

## 微信通道：用户自测步骤

1. 安装依赖：

   ```powershell
   uv sync
   ```

2. 确认 `.env` 至少包含 `ZHIPU_API_KEY`；启动微信使用 `--channel wechat`，或把 `CHANNEL` 改成 `wechat`。

3. 启动：

   ```powershell
   uv run python run_wechat.py --channel wechat
   ```

4. 程序会生成本地二维码页并**自动用 Chrome 打开**（找不到 Chrome 时退回系统默认浏览器）；终端只打印一行提示和可复制的二维码链接（形如 `https://liteapp.weixin.qq.com/q/...`）。

5. 用**手机微信**扫码，在手机上确认授权。终端出现“已扫码，请在手机上确认登录”后，确认成功会打印“微信登录成功”，并把凭证写入 `WECHAT_STATE_FILE`。

6. 在微信里打开 ClawBot 机器人私聊，发送：

   ```text
   现在几点了？顺便算一下 365 * 24 * 60 等于多少
   ```

7. 预期：先出现“对方正在输入”，随后收到 Agent 的文本回答（时间 + 计算结果）。

8. 失败时看哪里：
   - 终端日志：扫码状态、`getupdates` 重试、`-14` 提示都在这里
   - `.wechat_state.json`：在 `state/` 下，检查是否存在、是否保存了 `bot_token`
   - 二维码过期：程序会自动刷新并重新打开浏览器，最多 3 次；仍失败会提示重新运行
   - 媒体：下载到 `state/media/`，失败会写日志并在消息里标注“下载失败”
   - 重启续跑：凭证、游标、`context_token` 已落盘；`-14` 时会清空并要求重扫

## 限制与风险

- 二维码通过本地 `state/.wechat_qr.html` 展示并自动用 Chrome 打开；未安装 Chrome 时退回系统默认浏览器。
- 无桌面环境（纯服务器 / SSH）无法自动弹浏览器，需要手动打开 `state/.wechat_qr.html` 或终端打印的二维码链接。
- 仅支持**私聊**，不能进群；一个微信号只能绑定一个 bot。
- 微信会话会过期，接口返回 `errcode/ret = -14` 时必须重新扫码；程序会清除旧状态并提示。
- iLink 是腾讯托管的 Bot 通道，字段与行为可能随版本变化；本项目按已核对的 `2.1.1` 协议实现，未文档化字段无法保证长期兼容。
- 没有历史消息查询接口，只能实时长轮询。
- 媒体会下载并用 AES-128-ECB + PKCS7 解密；语音若服务端提供转写则直接使用文本，SILK 音频未做转码。
- 当前 Agent 使用文本模型，图片/文件/视频只会保存到本地并把路径交给 Agent，不做视觉理解。
- 微信不完整渲染 Markdown，回复发送前会做纯文本降级。

## 架构方案

视频内容 Agent 的分层与演进方案见 `docs/architecture.md`。
