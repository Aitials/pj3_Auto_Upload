# 企业微信 GLM 智能机器人

基于 LangChain + 智谱 GLM-4.5-Air 的最小 Agent，通过企业微信智能机器人「长连接 API 模式」收发消息，无需公网 IP。

## 文件

- `agent.py` —— Agent 核心：模型、工具（时间/计算器）、同步 `ask()` 与流式 `astream_answer()`
- `wecom_bot.py` —— 企业微信机器人入口，长连接收消息、流式回复
- `main.py` —— 命令行调试入口，不需要企业微信

## 配置

```powershell
copy .env.example .env
```

在 `.env` 中填入：

- `ZHIPU_API_KEY`：https://open.bigmodel.cn/usercenter/apikeys
- `WECOM_BOT_ID` / `WECOM_BOT_SECRET`：企业微信管理后台 → 智能专区 → 智能机器人 → 创建 → API 模式 → 使用长连接

## 运行

```powershell
uv sync
uv run python main.py      # 命令行测试
uv run python wecom_bot.py # 启动企业微信机器人
```
