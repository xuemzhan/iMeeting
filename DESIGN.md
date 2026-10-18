# iMeeting · 智能会议系统设计方案

> 基于 FunASR 的实时会议转写与纪要系统
> 版本：v1.0 设计稿 · 状态：待评审

---

## 1. 项目目标

构建一个**简约、有科技感、傻瓜式操作**的智能会议 Web 应用：

- 用户只需点击「开始会议」，无需任何配置即可实时看到转写字幕
- 所有技术参数（模型、设备、阈值、LLM 密钥等）全部收进后台管理
- 会议结束后自动生成结构化纪要：议题、结论、待办、责任人
- 支持导出 SRT / VTT / Markdown / JSON

### 核心设计原则

| 原则 | 具体体现 |
|---|---|
| **傻瓜式** | 主界面只有一个主按钮；配置项默认隐藏，管理员登录后才可见 |
| **简约** | 左侧转写流 + 右侧纪要面板，两栏布局，无多余装饰 |
| **科技感** | 深色可选主题、实时波形指示、字幕打字机效果、微动效 |
| **高性能** | 流式增量推送、虚拟滚动、二进制帧传输、模型单例复用 |
| **可配置** | 三级配置回退（默认值 → 数据库 → 环境变量），支持热更新 |

---

## 2. 技术选型

### 2.1 前端

| 组件 | 选型 | 理由 |
|---|---|---|
| 框架 | **React 19** | 并发特性、`useTransition` 适合高频字幕更新 |
| 构建 | **Vite 7** (Rolldown) | Rust 打包器，冷启动与 HMR 极快 |
| 语言 | **TypeScript 5.7** | 类型安全，接口契约明确 |
| 样式 | **Tailwind CSS v4** | 零运行时，Oxide 引擎编译快 |
| 状态 | **Zustand** | 比 Redux 轻量，适合流式数据 |
| 数据请求 | **TanStack Query v5** | 缓存、重试、乐观更新 |
| 长列表 | **react-window** | 虚拟滚动，万级字幕不卡顿 |
| 音频采集 | **AudioWorklet** | 独立线程，不阻塞主线程 |
| 图标 | **Lucide React** | 树摇友好，风格统一 |
| 动效 | **Motion (Framer)** | 声明式，支持 GPU 加速 |

**音频链路**：`getUserMedia` → `AudioWorklet`（重采样至 16kHz 单声道）→ 累积 600ms → `MediaRecorder`(Opus) 或裸 PCM → WebSocket 二进制帧。

> **编解码选择**：CPU 环境下服务端解码 Opus 有开销，建议直接传 **裸 Float32 PCM**（16kHz 单声道，600ms = 9600 个采样 = 38.4KB/帧）。带宽可接受且零解码成本。若带宽敏感再切 Opus。

### 2.2 后端

| 组件 | 选型 | 理由 |
|---|---|---|
| Web 框架 | **FastAPI** | 原生 async、WebSocket 一等支持、自动 OpenAPI |
| ASGI | **uvicorn[standard]** | 含 uvloop + httptools，性能最优 |
| JSON | **orjson** | 比标准库快 3-5 倍 |
| 校验 | **pydantic v2** | Rust 核心，比 v1 快 5-50 倍 |
| ORM | **SQLModel** | pydantic + SQLAlchemy 融合，类型友好 |
| 数据库 | **SQLite**（默认）/ PostgreSQL | 零配置起步，可平滑升级 |
| 迁移 | **Alembic** | 标准方案 |
| 鉴权 | **PyJWT** + **passlib[bcrypt]** | JWT + bcrypt 哈希 |
| HTTP 客户端 | **httpx** | 调 LLM API，支持异步与流式 |
| ASR | **funasr** (本仓库) | 直接复用，无需额外部署 |

### 2.3 模型配置（CPU 环境）

| 用途 | 模型 | 说明 |
|---|---|---|
| 流式 ASR | `iic/speech_paraformer_asr_nat-zh-cn-16k-common-vocab8404-online` | 600ms 出字 |
| 离线 ASR | `paraformer-zh`（实际解析为 SeACo-Paraformer） | 高精度定稿 |
| VAD | `fsmn-vad` | 断句与静音切分 |
| 标点 | `ct-punc` | 标点恢复 |
| 说话人 | `cam++` | 声纹嵌入 + 聚类 |
| 备选（更快） | `iic/SenseVoiceSmall` | 单模型多任务，CPU 友好 |

---

## 3. 系统架构

### 3.1 分层

```
┌─────────────────────────────────────────────────┐
│  浏览器前端 (React 19)                           │
│  麦克风 → AudioWorklet → 600ms 分块 → WS 二进制   │
└──────────────────┬──────────────────────────────┘
                   │ WebSocket /ws/stream
┌──────────────────▼──────────────────────────────┐
│  后端网关 (FastAPI + uvicorn)                    │
│  会话管理 · 配置中心 · JWT 鉴权 · REST API        │
└────────┬────────────────────────┬───────────────┘
         │                        │
┌────────▼─────────┐    ┌─────────▼──────────────┐
│  ASR 引擎         │    │  智能层                 │
│  FunASR           │    │  LLM 纪要 + 存储        │
│  VAD/ASR/Punc/SPK │    │  导出 SRT/MD/JSON       │
└───────────────────┘    └────────────────────────┘
```

### 3.2 双路 ASR 设计（核心）

这是保证「**实时看起来流畅 + 最终结果精确**」的关键：

| 路径 | 模型 | 触发 | 输出 | 用途 |
|---|---|---|---|---|
| **A · 流式** | Paraformer-streaming | 每 600ms | `partial` 帧 | 屏幕滚动字幕，低延迟 |
| **B · 离线** | Paraformer-zh + CT-Punc | VAD 检测到静音断句 | `final` 帧 | 定稿文本，替换草稿 |

**补偿机制**：
- 每个 utterance 分配唯一 `segment_id`
- partial 帧携带 `segment_id`，前端按 id 更新对应卡片（**不重排、不闪烁**）
- final 帧用同一 `segment_id` 覆盖，前端做文本 diff 高亮变化部分
- 离线路径跑在**线程池**中，绝不阻塞 WebSocket 事件循环

**流式调用示例**（对齐仓库 `examples/industrial_data_pretraining/paraformer_streaming/demo.py`）：

```python
chunk_size = [0, 10, 5]              # 600ms
encoder_chunk_look_back = 4
decoder_chunk_look_back = 1
cache = {}                            # 每会话独立

for chunk in audio_chunks:
    res = model.generate(
        input=chunk,
        cache=cache,
        is_final=is_last,
        chunk_size=chunk_size,
        encoder_chunk_look_back=encoder_chunk_look_back,
        decoder_chunk_look_back=decoder_chunk_look_back,
    )
```

> ⚠️ `cache` 必须**按会话隔离**，不同会议共用会串音。

### 3.3 技术选型性能要点

| 层 | 优化手段 |
|---|---|
| 前端 | 虚拟滚动、`useTransition` 降优先级、Web Worker 处理音频、字幕卡片 memo 化 |
| 传输 | 二进制帧（ArrayBuffer）、避免 JSON 编码音频、压缩开关（permessage-deflate） |
| 后端 | 模型单例懒加载、线程池隔离 ASR 推理、orjson、uvloop |
| ASR | 复用 `AutoModel` 实例、CPU 线程数绑定 `ncpu`、batch_size_s 动态批 |
| 数据库 | 字幕批量插入、按 meeting_id 索引、音频文件独立存储 |

---

## 4. 界面设计

### 4.1 起始页（傻瓜式核心）

```
              ┌──────────────────────────┐
              │   ◉  开始会议             │
              │   点击即可，无需任何配置    │
              └──────────────────────────┘
        [上传音频]   [历史会议]
```

- 中央一个大圆形主按钮，点击即开始
- 权限申请内联引导，失败给出明确修复提示
- 右上角「设置」仅管理员可见

### 4.2 会议主界面

```
┌────────────────────────────────────────────────────┐
│ ◉ iMeeting   未命名会议 · 00:12:34   [● 识别中] [导出][设置] │
├──────────────────────────────┬─────────────────────┤
│ 实时转写          虚拟滚动 ▾  │ 会议纪要             │
│ ┌──────────────────────────┐ │ ┌─────────────────┐ │
│ │ 说话人1  我们这次会议…    │ │ │ 议题一 · 技术方案 │ │
│ ├──────────────────────────┤ │ ├─────────────────┤ │
│ │ 说话人2  关于第一个议题…  │ │ │ 结论 · 两周验证   │ │
│ ├──────────────────────────┤ │ ├─────────────────┤ │
│ │ 说话人1  同意，那我们把…  │ │ │ 待办 · 输出报告   │ │
│ ├──────────────────────────┤ │ └─────────────────┘ │
│ │ 识别中  正在聆听…        │ │ 说话人               │
│ └──────────────────────────┘ │ ● 说话人1  68%       │
│ 自动滚动 · 时间戳可点击跳转    │ ● 说话人2  32%       │
│                              │ [生成完整纪要]        │
└──────────────────────────────┴─────────────────────┘
```

**交互细节**：
- 字幕卡片：说话人用左侧色条 + 头像圆点区分，颜色由 `speaker_id` 哈希稳定映射
- 未定稿字幕：虚线边框 + 斜体 + 呼吸动画，视觉上明确「暂定」
- 定稿瞬间：边框实线化 + 轻微高亮闪烁 300ms
- 新字幕到达时若用户已手动上滚，暂停自动滚动并显示「回到最新」浮标
- 时间戳点击 → 音频回放跳转到对应位置
- 说话人占比：环形进度或横向条形，实时更新

### 4.3 视觉风格 · 科技感

| 元素 | 设计 |
|---|---|
| 主色 | 靛蓝 `#534AB7`（品牌）/ 青绿 `#0F6E56`（状态） |
| 背景 | 浅色模式：`#FAFAF8`；深色模式：`#1A1A18` |
| 圆角 | 卡片 12px，按钮 8px，标签 6px |
| 字体 | 正文 13px / 字幕 15px / 标题 15px，字重仅 400 与 500 |
| 状态指示 | 呼吸圆点（识别中）/ 静态圆点（已停止）|
| 波形 | 底部细条实时音量波形，Web Audio `AnalyserNode` |
| 动效 | 字幕入场 150ms 淡入上移；切页 200ms；全部尊重 `prefers-reduced-motion` |

---

## 5. 数据模型

```python
# 用户
class User(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str
    role: str = "user"              # admin | user
    created_at: datetime

# 会议
class Meeting(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str = "未命名会议"
    owner_id: int = Field(foreign_key="user.id")
    status: str = "recording"       # recording | finished | failed
    duration_ms: int = 0
    audio_path: str | None = None
    created_at: datetime
    finished_at: datetime | None = None

# 转写片段（核心表，写入最频繁）
class Segment(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    segment_id: str = Field(index=True)   # 会话内唯一，用于 partial/final 覆盖
    text: str
    is_final: bool = False
    start_ms: int
    end_ms: int
    speaker: str | None = None
    seq: int = 0                          # 排序序号

# 会议纪要
class Minutes(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    meeting_id: int = Field(foreign_key="meeting.id", index=True)
    summary: str = ""
    topics: str = "[]"                    # JSON: [{title, detail}]
    todos: str = "[]"                     # JSON: [{task, owner, due}]
    raw_markdown: str = ""
    model_used: str = ""
    created_at: datetime

# 全局配置
class AppConfig(SQLModel, table=True):
    key: str = Field(primary_key=True)
    value: str                            # JSON 字符串
    scope: str = "global"
    updated_by: int | None = None
    updated_at: datetime

# 审计日志
class AuditLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = None
    action: str
    target: str | None = None
    payload: str | None = None
    ip: str | None = None
    created_at: datetime
```

---

## 6. 后台配置体系

### 6.1 三级回退

```
环境变量 (.env / Docker secrets)   优先级最高  ← 密钥、连接串
        ↓ 未命中则回退
数据库 app_config                   管理员可视化修改  ← 业务参数
        ↓ 未命中则回退
代码默认值 settings.py              保证零配置可运行
```

### 6.2 可配置项清单

**模型与推理**
- `asr.streaming_model` — 流式 ASR 模型名
- `asr.offline_model` — 离线 ASR 模型名
- `asr.device` — `cpu` / `cuda:0`
- `asr.ncpu` — CPU 线程数（默认 4）
- `asr.chunk_size` — 分块大小（默认 `[0,10,5]`）
- `asr.batch_size_s` — 动态批总时长
- `vad.enabled` / `vad.silence_ms` — VAD 开关与静音阈值（默认 800ms）
- `punc.enabled` — 标点恢复开关
- `spk.enabled` / `spk.cluster_threshold` — 说话人开关与聚类阈值
- `hotwords` — 热词表（数组）
- `use_itn` — 逆文本归一化

**LLM 纪要**
- `llm.base_url` — OpenAI 兼容端点
- `llm.api_key` — 加密存储（Fernet，主密钥来自环境变量）
- `llm.model` — 模型名
- `llm.temperature` / `llm.max_tokens` / `llm.timeout` / `llm.retries`
- `llm.minutes_prompt` — 纪要提示词模板
- `llm.summary_window_minutes` — 分段摘要窗口（默认 10 分钟）
- `llm.output_schema` — 输出结构定义

**会话与安全**
- `session.max_concurrent` — 最大并发会话数
- `session.max_duration_minutes` — 单会话最长时长
- `storage.audio_retention_days` — 音频留存天数（0 = 不保留）
- `export.formats` — 允许的导出格式
- `auth.allow_register` — 是否开放注册
- `auth.password_policy` — 密码强度要求

### 6.3 配置热更新

- 修改后写入 `app_config` 并广播到所有活跃会话
- 需要重建模型的配置（如 `asr.device`）标记为「需重启」并前置提示
- 所有变更写入 `audit_log`

---

## 7. API 契约

### 7.1 WebSocket `/ws/stream`

**客户端 → 服务端**

| 帧类型 | 载荷 | 说明 |
|---|---|---|
| 文本 | `{"type":"start","title":"...","options":{...}}` | 开始会话 |
| 二进制 | `ArrayBuffer` | 16kHz Float32 PCM 音频块 |
| 文本 | `{"type":"stop"}` | 结束会话 |
| 文本 | `{"type":"ping"}` | 心跳 |

**服务端 → 客户端**

```jsonc
// 会话已建立
{"type":"ready","meeting_id":123,"session_id":"abc"}

// 流式草稿（可多次覆盖同一 segment_id）
{"type":"partial","segment_id":"s-1","text":"我们这次会议",
 "start_ms":0,"end_ms":1800,"speaker":"1"}

// 精确定稿
{"type":"final","segment_id":"s-1","text":"我们这次会议主要讨论三个议题。",
 "start_ms":0,"end_ms":2400,"speaker":"1","seq":1}

// 说话人统计更新
{"type":"speakers","data":[{"id":"1","ratio":0.68},{"id":"2","ratio":0.32}]}

// 错误
{"type":"error","code":"MODEL_NOT_LOADED","message":"模型加载中，请稍候"}
```

**注意**：`partial` 与 `final` 使用**同一个 `segment_id`**，前端据此覆盖而非新增。

### 7.2 REST API

| 方法 | 路径 | 说明 | 权限 |
|---|---|---|---|
| POST | `/api/v1/auth/login` | 登录，返回 JWT | 公开 |
| POST | `/api/v1/auth/register` | 注册（受配置开关约束） | 公开 |
| GET | `/api/v1/meetings` | 会议列表（分页） | 登录 |
| GET | `/api/v1/meetings/{id}` | 会议详情含片段 | 登录 |
| DELETE | `/api/v1/meetings/{id}` | 删除会议 | 所有者/管理员 |
| POST | `/api/v1/meetings/{id}/minutes` | 生成纪要 | 登录 |
| GET | `/api/v1/meetings/{id}/export?format=srt` | 导出 | 登录 |
| POST | `/api/v1/transcribe/file` | 上传音频文件转写 | 登录 |
| GET | `/api/v1/config` | 读取配置（脱敏） | 登录 |
| PUT | `/api/v1/config` | 修改配置 | **管理员** |
| GET | `/api/v1/config/schema` | 配置项元数据（供后台表单渲染） | 管理员 |
| GET | `/api/v1/health` | 健康检查 | 公开 |

---

## 8. 目录结构

```
iMeeting/
├── .github/workflows/ci.yml
├── DESIGN.md
├── OPTIMIZATION.md
├── README.md
├── backend/
│   ├── pyproject.toml
│   ├── .env.example
│   ├── src/imeeting/
│   │   ├── main.py              # 应用装配 + 生命周期
│   │   ├── api/
│   │   │   ├── http/routes/     # REST 路由
│   │   │   └── ws/stream.py     # WebSocket 转写流
│   │   ├── engine/              # FunASR 模型池、音频、流式会话
│   │   ├── llm/                 # LLM 客户端与纪要生成
│   │   ├── export/              # 字幕与文档导出
│   │   ├── settings.py
│   │   ├── db.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   ├── security.py
│   │   ├── session_registry.py
│   │   └── jobs.py
│   ├── migrations/
│   ├── scripts/
│   └── tests/
├── frontend/
│   └── src/
│       ├── main.tsx             # 渲染入口
│       ├── app/                 # Provider、路由、守卫
│       ├── api/
│       │   ├── client.ts
│       │   ├── ws.ts
│       │   └── types.ts
│       ├── audio/
│       │   ├── recorder.ts
│       │   └── pcm-worklet.js
│       ├── store/
│       │   ├── session.ts
│       │   └── auth.ts
│       ├── pages/
│       ├── components/
│       └── styles/
├── benchmarks/
├── docs/
└── scripts/
```

---

## 9. 实施路线

| 阶段 | 内容 | 产出 |
|---|---|---|
| **P0 骨架** | 项目脚手架、FastAPI 起服务、React 起页面、WebSocket 回声测试 | 能跑通前后端连通 |
| **P1 转写闭环** | FunASR 流式接入、音频采集、partial 字幕上屏 | 能实时看到字幕 |
| **P2 精度补偿** | 离线定稿替换、VAD 断句、标点恢复 | 字幕准确且不断句 |
| **P3 说话人** | CAM++ 接入、聚类、说话人着色 | 区分多人对话 |
| **P4 纪要** | LLM 接入、map-reduce 摘要、结构化输出 | 能生成会议纪要 |
| **P5 后台配置** | 配置中心、管理界面、热更新、审计 | 管理员可视化配置 |
| **P6 账号体系** | JWT 登录、RBAC、多用户隔离 | 完整权限控制 |
| **P7 导出与历史** | SRT/MD/JSON 导出、历史会议回看 | 完整产品闭环 |
| **P8 打磨** | 深色模式、动效、波形、性能优化 | 科技感体验 |

---

## 10. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| CPU 实时性不足 | 字幕延迟累积 | 优先用 Paraformer-streaming（轻量）；降级到 800ms 分块；必要时上 SenseVoiceSmall |
| 模型加载慢（首次 30s+） | 用户等待 | 服务启动时**预热加载**；就绪探针；前端显示加载进度 |
| 流式 cache 串音 | 多人会议混乱 | `cache` 严格按 session_id 隔离，会话结束立即释放 |
| 长会议上下文超限 | 纪要失败 | map-reduce 分段摘要；10 分钟窗口；最后归并 |
| LLM 输出不稳定 | 纪要格式错乱 | 强制 JSON schema；失败重试；降级为纯文本摘要 |
| 浏览器权限被拒 | 无法录音 | 内联引导 + 明确修复步骤 + HTTPS/localhost 说明 |
| 音频传输带宽 | 网络抖动 | 裸 PCM 38.4KB/帧；可切 Opus；丢帧不影响已定稿内容 |

---

## 11. 关键设计决策记录

1. **为什么双路 ASR 而不是只用流式？**
   流式模型为了低延迟牺牲精度。只用流式会导致最终文稿质量不可接受。双路设计用流式保证「体感」，用离线保证「结果」。

2. **为什么用 `segment_id` 覆盖而不是追加？**
   流式输出是渐进修正的（"我们这次" → "我们这次会议" → "我们这次会议主要"），若每帧追加会产生大量重复文本。用同一 id 覆盖才能得到正确结果。

3. **为什么音频传裸 PCM 而不是 Opus？**
   CPU 环境下服务端解码 Opus 会占用本已紧张的算力。38.4KB/600ms 的带宽（约 64KB/s）在局域网与常规宽带上完全可接受。

4. **为什么配置要三级回退？**
   密钥不能进数据库明文（用环境变量）；业务参数需要管理员可视化改（用数据库）；同时保证零配置能跑起来（用默认值）。

5. **为什么用 `react-window` 而不自己写虚拟滚动？**
   转写流是典型的定高列表，`react-window` 成熟稳定。自研容易在滚动锚定、动态高度上踩坑，不值得。

---

## 12. 待确认问题

- [ ] 是否需要**系统音频**采集（录会议软件的声音），而不只是麦克风？
- [ ] 是否需要**多端同步**（同一场会议多人同时看字幕）？
- [ ] 是否需要**实时翻译**（中英互译字幕）？
- [ ] LLM 用云端 API 还是本地部署（如 vLLM + Qwen）？
- [ ] 部署环境是 Windows 还是 Linux？是否需要 Docker 交付？
