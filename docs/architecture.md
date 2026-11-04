# 视频内容 Agent 分层架构方案

> 状态：方案稿，不含实现。基于当前已完成的 `app/channels` / `app/router` / `app/agent` / `app/config` 分层。

## 1. 目标与现状

目标：把现有“聊天机器人”演进为“视频内容产出 Agent”：

```text
微信 / 企微消息
  → 识别内容任务
  → 热点检索 → 脚本 → TTS → 素材检索与筛选
  → 数字人口播 → 按需动画 → 剪辑封装
  → 成片交付回微信 / 企微
```

当前已有：

- `app/channels`：传输层（企业微信长连接、微信 iLink 长轮询）
- `app/router`：通道无关的消息路由，调用 Agent
- `app/agent`：LangChain + GLM 对话 Agent（模型、工具、`ask` / `astream_answer`）
- `app/config`：`.env` 与路径集中配置

当前缺口：没有任务编排、长任务队列、能力层、产物存储、可靠交付。

## 2. 总体分层

```text
┌──────────────────────────────────────────────────────────────┐
│ 接入层  app/channels                                         │
│ 微信 iLink / 企业微信；只负责收发、去重、typing、会话过期    │
└──────────────────────────┬───────────────────────────────────┘
                           │ IncomingMessage / DeliveryContext
┌──────────────────────────▼───────────────────────────────────┐
│ 路由层  app/router                                           │
│ 意图分流：普通对话 → agent；内容任务 → jobs.create            │
└───────────────┬──────────────────────────────┬───────────────┘
                │                              │
┌───────────────▼──────────────┐  ┌────────────▼───────────────┐
│ 对话  app/agent              │  │ 编排层  app/jobs           │
│ 现有 LangChain Agent         │  │ 状态机 / 队列 / Worker     │
└──────────────────────────────┘  └────────────┬───────────────┘
                                               │ ContentJob
┌──────────────────────────────────────────────▼───────────────┐
│ 管道层  app/pipelines                                        │
│ 把能力组合成 video pipeline，处理阶段顺序、重试、质检       │
└──────────────────────────┬───────────────────────────────────┘
                           │ Capability 调用
┌──────────────────────────▼───────────────────────────────────┐
│ 能力层  app/capabilities                                     │
│ 热点检索 / 脚本 / TTS / 素材 / 数字人 / 动画 / 剪辑 / 封装   │
└──────────────────────────┬───────────────────────────────────┘
                           │ Artifact
┌──────────────────────────▼───────────────────────────────────┐
│ 基础设施  app/config / app/artifacts / app/delivery            │
│ 配置 / 产物存储 / 进度与成片交付 / 日志 / 重试 / 观测         │
└──────────────────────────────────────────────────────────────┘
```

依赖方向必须单向：

```text
channels → channel 契约
router   → agent / jobs
jobs     → pipelines / artifacts / delivery
pipelines→ capabilities / artifacts
capabilities → 外部服务 + artifacts
delivery → channels 契约（只依赖契约，不依赖具体通道）
agent    → config / LLM
```

`agent` 不依赖 `channels`，`channels` 不承载视频逻辑。

## 3. 层间契约

### 3.1 `DeliveryContext`

当前 `IncomingMessage` 是“某条入站消息的回复句柄”，不适合几分钟后的主动推送。新增：

```python
@dataclass
class DeliveryContext:
    channel: str                 # wechat / wecom
    user_id: str
    session_id: str = ""
    context_token: str = ""      # 微信回复必需；企微可空
    expires_at: float = 0.0      # 过期时间；过期后不可主动推送
    reply_handle: Any = None     # 通道内部句柄（企微 frame 等）
```

`router` 收到内容任务时，把 `IncomingMessage` 转成 `DeliveryContext` 存进 Job。

通道契约在后续阶段增加两个主动推送方法（现有 `reply_text` / `reply_stream` 保留给即时对话）：

```python
async def push_text(self, ctx: DeliveryContext, text: str) -> bool: ...
async def push_artifact(self, ctx: DeliveryContext, artifact: Artifact) -> DeliveryResult: ...
```

`push_artifact` 返回“是否发送成功、失败原因、是否降级为链接”，而不是假设 mp4 一定能发。

### 3.2 `ContentJob`

```python
@dataclass
class ContentJob:
    id: str
    source: DeliveryContext
    request_text: str
    topic: str = ""
    style: str = "口播资讯"
    target_duration_sec: int = 60
    status: JobStatus = JobStatus.RECEIVED
    phase: str = "received"
    progress: int = 0
    artifacts: list[Artifact] = field(default_factory=list)
    error: str | None = None
    retry_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
```

`JobStatus` 建议：

```text
RECEIVED → QUEUED → SCRIPTING → TTS → ASSET_SEARCH
        → ASSEMBLING → AVATAR → ANIMATION → RENDER
        → READY → DELIVERING → DELIVERED

任意阶段 → FAILED（可重试）→ QUEUED
重试超限 → DEAD_LETTER
用户取消 → CANCELLED
```

原则：状态只由 `jobs` 写；能力和管道只返回产物或抛异常，不直接改 Job 状态。

### 3.3 `Artifact`

```python
@dataclass
class Artifact:
    id: str
    job_id: str
    kind: str          # script / audio / image / clip / video / subtitle / thumbnail / manifest
    uri: str           # file:// / https:// / s3://
    mime: str
    size: int
    sha256: str
    meta: dict         # provider、模型、时长、分辨率、版权来源、费用等
    created_at: float
```

约定：

- 所有能力只通过 `Artifact` 交接，不传裸路径；
- `manifest` 记录一次成片的完整输入、版本、prompt、素材来源和校验和；
- `uri` 优先对象存储签名 URL，P0 可用本地文件 + 临时链接。

### 3.4 `ProgressEvent`

```python
@dataclass
class ProgressEvent:
    job_id: str
    phase: str
    message: str
    percent: int
    artifacts: list[Artifact] = field(default_factory=list)
    at: float = 0.0
```

用于：
- 微信：阶段播报（多条消息）；
- 企微：阶段播报或流式；
- 观测：写入任务事件表，前端/运维可查。

### 3.5 `JobStore` / `JobQueue`

```python
class JobStore(Protocol):
    async def create(self, job: ContentJob) -> None: ...
    async def get(self, job_id: str) -> ContentJob | None: ...
    async def update(self, job: ContentJob) -> None: ...
    async def append_event(self, event: ProgressEvent) -> None: ...
    async def list_runnable(self, limit: int) -> list[ContentJob]: ...
```

P0 用 SQLite + `asyncio.Queue`；P1 可换 Redis / Postgres，接口不变。

### 3.6 `Capability`

```python
class Capability(Protocol):
    name: str
    async def run(self, job: ContentJob, ctx: CapabilityContext) -> list[Artifact]: ...
```

`CapabilityContext` 携带：`ArtifactStore`、日志、预算上限、超时、随机种子、幂等键。

能力层不感知微信/企微，不直接发消息。

## 4. 各层职责与改造点

| 层 | 目录 | 职责 | 现状 |
|---|---|---|---|
| 接入 | `app/channels` | 传输、去重、typing、会话过期 | 已有，扩展 `push_text` / `push_artifact` |
| 路由 | `app/router` | 普通聊天 → agent；内容任务 → jobs | 已有，新增意图分流 |
| 对话 | `app/agent` | LLM 对话、工具 | 已有，不动推理逻辑 |
| 编排 | `app/jobs` | 状态机、队列、Worker、重试、取消、幂等 | 新增 |
| 管道 | `app/pipelines` | 视频流水线组合、阶段顺序、质检 | 新增 |
| 能力 | `app/capabilities` | 热点、脚本、TTS、素材、数字人、动画、剪辑 | 新增 |
| 产物 | `app/artifacts` | 本地/对象存储、校验和、签名 URL、生命周期 | 新增 |
| 交付 | `app/delivery` | 阶段播报、成片发送、链接降级、频控 | 新增 |
| 配置 | `app/config` | 环境变量、路径、预算、开关 | 已有，扩展 |

## 5. 关键难点与取舍

### 5.1 分钟级长任务 vs 同步回复

视频生成是分钟级任务，而微信/企微消息处理是同步回复模型。

必须：

1. 入站后 **1~3 秒内**回“已收到，正在生成”，并立即返回；
2. `ContentJob` 入队，由独立 Worker 执行；
3. 阶段完成主动推送；
4. 失败时推送“失败 + 原因 + 是否重试”，不要静默。

不要：在 `getupdates` 的同步处理链里直接跑 5 分钟视频生成，否则长轮询会停、消息会堆积、微信侧可能超时。

### 5.2 iLink `context_token` 的硬约束

微信 iLink 的 `sendmessage` 依赖入站消息产生的 `context_token`。长期静默后再主动推送，token 可能失效或无法定位会话。这是**最高风险**。

缓解顺序：

1. `DeliveryContext` 保存 `context_token` + 收到时间，设置保守有效期；
2. 每阶段播报尽量复用最近一次入站 token；
3. 推送失败时明确提示“请回复任意消息后输入「进度」”，用新的入站消息刷新 token；
4. 重要成片同时走 **企微通道** 或 **链接/网盘** 交付；
5. 不把“主动推送成功”作为唯一交付路径。

### 5.3 微信不支持流式

微信没有消息编辑接口，无法像企微那样逐字更新同一条消息。

策略分开：

- **微信**：阶段播报多条消息，建议最多 4 条：已受理 → 脚本完成 → 配音/素材完成 → 成片就绪。控制频次，避免刷屏。
- **企微**：普通对话继续流式；长任务首条 ack 结束后，后续阶段用独立消息，避免长时间占用一个 stream。

### 5.4 大文件 mp4 回传不确定

此前调研发现官方文档与腾讯云教程对微信文件/视频回传口径存在矛盾，因此不能把“直接发 mp4”当成必然能力。

交付优先级：

1. **链接/网盘兜底**（签名 URL、对象存储、企业网盘）；
2. 企微文件发送（若账号权限和大小限制满足）；
3. 微信 CDN 媒体上传（需实测账号权限、大小、格式、时效）；
4. 最后才考虑 base64/分片等非标准方式。

`push_artifact` 必须返回 `DeliveryResult(sent, fallback_url, reason)`，由 `delivery` 决定是否降级。

### 5.5 外部依赖、费用与合规

不要假设任何能力“免费 unlimited”：

- **TTS**：按字符/时长计费；缓存相同文案；声音克隆需授权。
- **数字人**：肖像权、声音权、肖像素材授权、服务商条款、数据出境/存储位置。
- **素材**：Pexels/Pixabay 也有署名、商用、人物/商标限制；本地库需记录授权来源。
- **动画/渲染**：Remotion 商业授权、FFmpeg 编解码许可、字体授权。
- **平台条款**：微信/企微自动化、频控、内容审核；避免批量骚扰。
- **预算**：每个 Job 设置费用上限，超限暂停并告知用户。

## 6. 技术选型（每类两个候选）

### TTS

| 候选 | 优点 | 代价 |
|---|---|---|
| 火山引擎 / 阿里云 TTS | 稳定 SLA、中文音色多、支持克隆、可商用 | 需账号/密钥、按量计费、合规审核 |
| Edge-TTS | 零成本、接入快、适合 P0 | 非官方接口、限流/条款风险、无 SLA |

建议：P0 用 Edge-TTS 打通；P1 抽象 `TTSProvider`，切商业 TTS，Edge-TTS 仅保留开发模式。

### 数字人

| 候选 | 优点 | 代价 |
|---|---|---|
| 云服务（硅基智能 / 闪剪 / HeyGen 类） | 上手快、口型质量稳定、按分钟计费 | 费用、数据位置、肖像授权、平台条款 |
| 本地 LatentSync / SadTalker | 无按分钟 API 费、可离线 | 需 GPU、推理慢、质量/稳定性要自己扛、模型许可 |

建议：P2 先用云服务做小样本验收；本地方案作为离线/成本兜底，不承诺同等质量。

### 素材

| 候选 | 优点 | 代价 |
|---|---|---|
| Pexels / Pixabay API | 免费额度、接口简单、适合 P1 | 中文/竖屏/热点相关性有限、需遵守许可与署名 |
| 本地授权素材库 | 相关性和品牌一致性好、版权清晰 | 采购/整理成本、更新慢 |

建议：P1 先用 Pexels/Pixabay，建立“素材候选 + 授权元数据”；P2 接本地库。

### 动画 / 剪辑

| 候选 | 优点 | 代价 |
|---|---|---|
| Remotion（React） | 排版/动效精细、模板化强 | 需 Node/Chromium、商业授权需评估、渲染资源 |
| FFmpeg / MoviePy | 部署轻、字幕/拼接/转场快 | 复杂动效弱、代码维护成本 |

建议：FFmpeg 做基线（剪切、字幕、混音、封装）；Remotion 只做品牌模板和片头片尾。

## 7. 分阶段路线

### P0：打通链路（假数据/占位素材）

- 意图分流：识别“生成视频/做一条视频”；
- 入队、状态机、Worker、失败重试、阶段播报；
- 假脚本 + 占位音频 + 占位视频，FFmpeg 拼成 15 秒 mp4；
- 微信/企微都能收到“已受理”和“成片就绪 + 链接”。

验收：

- 一个 Job 从 RECEIVED 到 DELIVERED 全状态可追踪；
- 失败可重试，重试不重复生成已成功产物；
- 微信至少收到 2 条阶段消息；成片有可访问链接；
- 不调用真实付费 API。

### P1：真 TTS + 真素材

- LLM 生成脚本；商业 TTS；Pexels/Pixabay 素材检索与筛选；
- 字幕、混音、封面、30~60 秒成片；
- 费用、耗时、成功率指标；素材授权元数据。

验收：

- 3 个热点主题端到端成功；
- 成片可播放、音画同步、字幕无溢出；
- 单条成本有上限、可观测；
- 外部 API 失败有降级/重试，不丢 Job。

### P2：数字人

- 数字人口播视频；口型/表情/背景/服装模板；
- 肖像与声音授权记录；
- 云端/本地双 provider 抽象。

验收：

- 60 秒口播成片，口型质量达到内部评审线；
- 授权记录可追溯；
- 云服务超时/失败可降级为“音频 + 素材”版本；
- 单条耗时与费用在预算内。

### P3：动画与质量打磨

- 片头片尾、数据动画、关键词强调、品牌模板；
- 批量任务、优先级队列、配额；
- 质量检查（黑帧、静音、字幕越界、时长、码率）；
- 观测与告警、死信队列、人工复审。

验收：

- 模板化产出稳定；批量 10 条成功率 ≥ 目标值；
- 质量问题可自动拦截并重跑；
- 全链路有 job_id 日志、指标和告警。

## 8. 目录树扩展建议

```text
pj3_Auto_Upload-main/
├── app/
│   ├── agent/            # 现有：对话 Agent
│   ├── router/           # 现有：意图分流（新增内容任务分支）
│   ├── channels/         # 现有：传输层（新增 push_text/push_artifact）
│   ├── config/           # 现有：配置与路径
│   ├── jobs/             # 新增：状态机、队列、Worker、JobStore
│   ├── pipelines/        # 新增：video pipeline 组合
│   ├── capabilities/     # 新增：hotspot/script/tts/assets/avatar/animation/render
│   ├── artifacts/        # 新增：ArtifactStore（本地/S3/OSS/COS）
│   └── delivery/         # 新增：阶段播报、成片交付、链接降级
├── state/
│   ├── jobs.db           # P0 SQLite
│   ├── artifacts/        # P0 本地产物
│   └── media/            # 微信入站媒体
├── docs/architecture.md
└── tests/
```

边界：

- `channels` 只做传输，不 import `jobs` / `capabilities`；
- `router` 只做分流，不跑 pipeline；
- `jobs` 不 import 具体通道，只依赖 `delivery` 接口和 `DeliveryContext`；
- `pipelines` 不 import `jobs` / `channels`，只组合 `capabilities`；
- `capabilities` 不 import `channels` / `jobs`，只接收 Job 快照和上下文；
- `delivery` 只依赖 `channels` 契约，负责“怎么发、发失败怎么办”。

## 9. 关键风险与待决策

1. **微信主动推送可靠性**：`context_token` 过期是硬风险；是否接受“用户需回一条消息才能继续推送”？
2. **成片交付形态**：优先链接/网盘，还是投入验证微信 mp4 直传？需要账号实测。
3. **数字人合规**：是否允许使用真人肖像/声音克隆？谁负责授权与留存？
4. **成本模型**：单条视频预算上限、TTS/数字人供应商、是否需要缓存与配额。
5. **数据与隐私**：用户素材、成片、脚本保留多久；是否允许上传到第三方云。
6. **多实例与可靠性**：P0 单机 SQLite 足够；何时需要 Redis/Postgres、对象存储和独立 Worker？
7. **协议稳定性**：iLink 与企微长连接均可能变更；交付层必须有链接兜底。
