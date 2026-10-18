# iMeeting

> 基于 [FunASR](https://github.com/modelscope/FunASR) 的实时会议转写与智能纪要系统
>
> GitHub: <https://github.com/xuemzhan/iMeeting>

简约、有科技感、傻瓜式操作。点击「开始会议」即可，所有技术参数收进后台。

---

## 功能

- **实时转写**：边说边出字幕，延迟约 1 秒
- **双路 ASR**：流式出草稿保证体感，离线出定稿保证精度
- **自动断句**：静音检测切分，无需手动操作
- **标点恢复**：自动补全标点，输出可直接阅读
- **说话人分离**：自动区分多人对话并着色
- **口语录入会议信息**：开场前 N 分钟（默认 3 分钟、可配置）说出主题、参会人与议程，自动提取并支持手动修订
- **渐进式纪要**：会中定时滚动更新草稿，结束后自动生成终稿；自动草稿、终稿、人工修订全部版本化
- **可追溯编辑**：逐句修订转写并保留原始识别，纪要修订生成新版本，支持版本对比
- **待办闭环**：自动同步责任人、截止时间与 open/doing/done 状态
- **企业检索与归档**：FTS5 全文检索、分页、批量重命名/标签/归档/导出、30 天回收站、跨会议知识库与主题图谱
- **安全分享**：只读链接、过期时间、访问口令、可选音频下载
- **多格式导出**：SRT / VTT / Markdown / TXT / JSON / Word / PDF（支持企业页眉页脚）
- **会议运营能力**：预约会议与 `.ics` 日历导入、AI 调用成本看板、审计日志、噪声告警统计
- **生产可靠性**：SQLite WAL、Alembic 版本迁移、持久化任务队列与重试、录音块 IndexedDB 补传、优雅排空发布
- **会议级自适应识别**：根据噪声底、语音电平、SNR、片段长度和延迟自动调整 ASR 增益、VAD 阈值、静音断句和软/硬切分
- **逐段诊断**：保存 ASR 来源/错误、定稿原因、有效语音、RMS、SNR、声纹相似度和身份来源
- **AI 治理**：Token/成本统计、单场与月度预算、供应商速率限制、供应商 429 保护
- **可信纪要**：议题、待办和脑图节点可追溯到原始片段时间戳
- **企业知识**：可选 Embedding 向量检索、FTS 命中片段跳转、主题别名归一、企业词表和热词
- **数据治理**：会后全音频复核建议、声纹质量/停用/合并建议、文本脱敏、法律保留、审计哈希链与 CSV 导出
- **协作权限**：会议所有者、可编辑、只读三级 ACL
- **可选增强**：服务端 AGC/降噪、会后说话人全局合并、实时双语字幕、PWA 安装
- **后台配置**：模型、推理参数、LLM、会话策略全部可视化配置，支持热更新
- **用户权限**：JWT 鉴权 + RBAC，普通用户与管理员分离

## 技术栈

| 层 | 选型 |
|---|---|
| 前端 | React 19 · Vite · TypeScript · Tailwind v4 · Zustand · TanStack Query |
| 后端 | FastAPI · uvicorn(uvloop) · orjson · pydantic v2 · SQLModel |
| ASR | FunASR（Paraformer 流式 + 离线 · FSMN-VAD · CT-Punc · CAM++） |
| 音频 | AudioWorklet 重采样 · 16kHz 单声道 Float32 PCM · WebSocket 二进制帧 |

## 快速开始

需要 Python 3.10+ 与 Node 18+。

### 1. 后端

```bash
cd backend

# CPU 环境可先从 PyTorch 官方源安装匹配版本
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

# 安装 iMeeting 后端及 FunASR 推理依赖
pip install -e ".[asr,dev]"

cp .env.example .env
python -m imeeting.main
```

服务监听地址与端口读取 `backend/.env`，默认是 `http://127.0.0.1:8000`。
首次启动会下载模型（约 1GB）并预热，需要几分钟。

> **想先不下载模型就跑通前后端？** 设 `ASR_MODE=echo` 启动。
> 此模式不加载任何模型，只回显占位文本，用于验证麦克风、WebSocket、
> 字幕渲染、落库、导出整条链路。等链路确认无误再切回 `ASR_MODE=normal`。

### 2. 前端

```bash
cd frontend
npm install
npm run dev
```

打开 http://localhost:5173，点「开始会议」并允许麦克风权限。

> **若 `npm install` 报 esbuild / rollup 相关错误**：这是可选平台依赖没装上
> （国内镜像偶发）。补装即可：
> ```bash
> npm install @esbuild/win32-x64 @rollup/rollup-win32-x64-msvc
> ```
> （macOS 用 `@esbuild/darwin-arm64`、`@rollup/rollup-darwin-arm64`；
> Linux 用 `@esbuild/linux-x64`、`@rollup/rollup-linux-x64-gnu`）

### 3. 配置 LLM 纪要（可选）

访问 http://localhost:5173/login，用 `admin / admin123` 登录（**请立即改密码**），
在「LLM 纪要」分组填写 API 端点与密钥。

## 文档

- [设计方案](./DESIGN.md) —— 架构、数据模型、接口契约、实施路线
- [项目结构](./docs/architecture/project-structure.md) —— 前后端目录边界与验证方式
- [后端说明](./backend/README.md) —— 环境变量、WebSocket 协议、性能调优
- [前端说明](./frontend/README.md) —— 音频链路、字幕渲染、主题

## 环境要求

| 项 | 要求 |
|---|---|
| Python | 3.10+ |
| Node | 18+ |
| 内存 | 建议 8GB+（模型常驻约 1.5GB） |
| 浏览器 | Chrome/Edge 90+、Firefox 90+、Safari 15+ |
| 麦克风 | 需 `https` 或 `localhost`（安全上下文限制） |

## 自测

### 一、后端单元 / 接口测试（475 项，不依赖模型，约 5 分钟）

```bash
cd backend
python -m pytest tests/ -q
```

覆盖范围：

| 文件 | 内容 |
|---|---|
| `test_security.py` | 口令哈希（含 72 字节截断边界）、JWT 伪造/过期、配置字段加解密 |
| `test_auth_api.py` | 登录、用户名枚举防护、令牌校验、权限边界、注册开关 |
| `test_config_api.py` | schema 完整性、三级回退优先级、写入校验、密钥加密落盘、审计日志 |
| `test_meetings_api.py` | 会议 CRUD、**跨用户权限隔离**、导出格式、纪要异常路径 |
| `test_export_formats.py` | SRT/VTT 时间码边界、零长片段、畸形纪要容错 |
| `test_streaming_engine.py` | 双路覆盖语义、静音/噪声过滤、会话 cache 隔离、三种模式 |
| `test_ws_protocol.py` | 握手校验、心跳、停流收尾、落库计数、**异常断开状态收口** |
| `test_stability.py` | 并发会话隔离、长时缓冲、模型崩溃容错、并发加载去重 |
| `test_llm_minutes.py` | JSON 抽取鲁棒性、map-reduce 切分、字段规整 |

### 二、协议与识别链路（需服务已启动）

```bash
# WebSocket 协议冒烟（任何模式可跑）
python scripts/ws_smoke.py --port 8010

# 真实 ASR 端到端（需 ASR_MODE=normal 且模型已加载）
python scripts/e2e_asr.py --wav ../../runtime/funasr_api/asr_example.wav --port 8010
```

### 三、生产级压力与稳定性测试

```bash
cd backend
# 全套（7 项），建议先以 ASR_MODE=echo 跑通链路
python scripts/stress.py --port 8010

# 加大压力
python scripts/stress.py --port 8010 --sessions 8 --duration 60

# 单项
python scripts/stress.py --port 8010 --only throughput  # 吞吐 / RTF
python scripts/stress.py --port 8010 --only latency     # 首字延迟
python scripts/stress.py --port 8010 --only concurrency # 并发
python scripts/stress.py --port 8010 --only longrun     # 泄漏检测
python scripts/stress.py --port 8010 --only backpressure# 背压
python scripts/stress.py --port 8010 --only malformed   # 异常输入
python scripts/stress.py --port 8010 --only abrupt      # 断连韧性
```

> **首字延迟与 RTF 必须用真实语音测。** 流式 Paraformer 对合成正弦音不产出
> token，用合成音测出来的只是超时值。脚本会自动定位仓库内
> `runtime/funasr_api/asr_example.wav`，也可用 `STRESS_WAV=/path/to.wav` 指定。

实测基线 —— **纯 CPU（4 核）+ 真实模型**（`ASR_MODE=normal`）：

| 项 | 结果 |
|---|---|
| 吞吐 / RTF | **0.231**（单核理论并发≈4.3 路，可实时） |
| 首字延迟 | **中位 1288ms**（1270–1300ms，真实语音） |
| 异常输入韧性 | 8/8 通过（非对齐帧、超大帧、畸形 JSON、超长文本…） |
| 断连韧性 | 5 轮硬断后仍可正常建连，会议均正确收口为 `abandoned` |
| 背压 | 内存 −3MB，稳定 |
| 长时运行 | 60 秒音频，内存 −6MB（无泄漏） |
| 并发压力 | 3/3 会话成功，内存 +1MB |

同一套测试在 `ASR_MODE=echo`（纯链路）下：首字延迟中位 3ms，
背压 7.5MB/s 突发内存 +0MB，长时 120s 内存 +4MB。

### 四、前端

```bash
cd frontend && npm test && npm run typecheck && npm run build
```

## CPU 性能说明

本项目面向**无 GPU 环境**设计，与 FunASR 部署在同一台机器上。实测参考：

| 项 | 实测值 |
|---|---|
| 流式模型加载 | 28.4s |
| 离线模型加载 | 29.0s |
| 全模型常驻内存 | 约 4.7GB |
| **流式 RTF** | **0.17**（600ms 分块，约 6 倍实时余量） |
| **离线 RTF** | **0.182** |

> RTF（实时因子）= 处理耗时 / 音频时长，小于 1 表示比实时快。
> 0.17 意味着 1 秒音频只需 170ms 处理，CPU 有充足余量。

模型在服务启动时**后台预热**，首请求不必等待。首次调用有 warmup 开销，
稳态性能远好于首次。

**如果延迟累积**，按顺序尝试：放宽分块到 800ms → 换 SenseVoiceSmall → 关闭说话人分离。

## 运行与运维

### 并发上限

`session.max_concurrent` 默认 **4**。超过上限的连接会收到
`TOO_MANY_SESSIONS` 错误并关闭（不排队），避免 CPU 被压垮。
只有 CPU 的机器上不建议调高。

### 健康检查

`GET /api/v1/health` 返回 `status`：

| 值 | 含义 | 是否应摘除实例 |
|---|---|---|
| `ok` | 模型就绪，可正常转写 | 否 |
| `loading` | 模型仍在加载 | 否（稍后即可用） |
| `degraded` | 模型加载失败 | 是 |
| `disabled` | 配置为 `off`/`echo`，本就不需要模型 | 否 |

`disabled` 是必须区分的状态：`echo`/`off` 模式下模型永不加载，
若沿用「全加载完才算 ok」，探针会永远停在 `loading`。

生产编排应使用：

- `GET /api/v1/live`：进程存活；
- `GET /api/v1/ready`：模型就绪且未处于排空状态；
- `GET /metrics`：Prometheus 文本指标，包含 HTTP、活跃会话和任务状态。

### 优雅发布

发布前先调用 `POST /api/v1/admin/maintenance/drain`，等待
`GET /api/v1/admin/maintenance` 的 `active_sessions` 归零，再停止旧进程。
辅助脚本见 `scripts/graceful_restart.ps1`。

### 后台任务

终稿、说话人合并、导出、音频复核和语义索引重建均写入 `job` 表。
进程重启后会自动恢复 pending 任务；崩溃遗留的 running 任务在锁超时后重新入队。

### 会议状态

`meeting.status` 有四种取值，前端与导出一律据此判断：

| 值 | 含义 |
|---|---|
| `recording` | 会话进行中 |
| `finished` | 客户端显式发送 `stop`，正常结束 |
| `abandoned` | 连接被关/超时等非正常中断，**已收到的内容照常保留** |
| `failed` | 会话处理过程中抛出异常 |

**任何结束路径都必须把会议移出 `recording`。** 浏览器关标签、断网、
进程被杀都不会发 `stop`——早期版本在这种情况会留下永远「进行中」的僵尸会议，
现已通过「先落库收口再冲刷会话」的顺序修复。`finished_at` 在四种状态下都会写入。

### 生产部署注意

1. **必须设置 `SECRET_KEY`**（≥32 字节随机串）。未设置时服务会用内置开发密钥
   并在日志打印告警——任何人都能伪造登录令牌。
2. `ADMIN_PASSWORD` 应显式设置，不要沿用默认的 `admin123`。
3. 密钥类配置（如 `llm.api_key`）在数据库中是 **Fernet 加密**存储，即使
   数据库文件泄露也不会直接暴露明文。
4. 麦克风需 `https` 或 `localhost`（浏览器安全上下文限制）。

## 已知限制

- 音频回放功能未接入（时间戳点击暂为占位），需先实现音频文件落盘
- 说话人编号仅在单场会议内有效，不是注册身份识别
- 静音检测用能量判据（整段 RMS + 有效语音时长双重判据），
  嘈杂环境可能误切；可调 `vad.silence_ms` 与 `vad.min_speech_ms`

## 许可证

本项目基于 FunASR 构建。FunASR 为 MIT 许可，但其**模型权重各有独立许可**，
商用前请查阅对应 checkpoint 的模型卡。
