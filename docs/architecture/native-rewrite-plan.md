# 会议后端 Go + C++ 渐进式重写方案

Status: Proposed  
Date: 2026-10-18  
Scope: `iMeeting/backend` 实时转写、控制面与后台任务

## 1. 结论

不建议把整个后端一次性改成单一语言。当前系统的主要成本是 FunASR/PyTorch 模型推理和音频前处理，不是 FastAPI、JSON 或 JWT。只把 Web 层换成 Go，端到端提升预计有限；把所有业务和模型都塞进 C++，又会显著放大开发、交付和回滚风险。

推荐目标是：

1. **Go 负责控制面和实时网关**：REST、WebSocket、鉴权、会话状态、背压、数据库、任务队列。
2. **C++ 负责推理和音频热点**：流式/离线 ASR、VAD、标点、热词、声纹嵌入与会话级 cache。
3. **Python 暂时保留为智能侧车**：LLM 纪要、会议元信息、文档导出、检索嵌入等低 CPU、快速迭代逻辑。

迁移顺序是“先校准基线，再替换推理，最后替换网关”。每一步都能单独上线、灰度与回滚，不改变前端协议和已有数据库数据。

## 2. 现状事实

### 2.1 运行形态

- FastAPI 单进程入口注册了 12 个 REST/WebSocket 路由，见 `../../backend/imeeting/main.py:220-231`；由 uvicorn 启动，见 `../../backend/imeeting/main.py:265`。
- WebSocket 主循环同时承担鉴权、会议创建、音频接收、音频落盘、增量写库、心跳和收尾，见 `../../backend/imeeting/ws/stream.py:56`。
- 模型在进程内以单例保存，推理通过线程池调用，见 `../../backend/imeeting/engine/pool.py:82` 和 `../../backend/imeeting/engine/pool.py:259-266`。
- 后台任务目前是一个单协程 worker，见 `../../backend/imeeting/jobs.py:206-215`。

### 2.2 实测负载

对最近 5 场时长超过 1 分钟的 `normal` 模式会议指标统计：

| 指标 | 实测范围 | 5 场平均 |
|---|---:|---:|
| 进程 CPU / wall time | 2.36–2.67 核 | 2.49 核 |
| 离线定稿 P50 | 1.17–1.67 s | 1.38 s |
| 声纹提取 P50 | 0.21–0.26 s | 0.23 s |
| 端到端定稿 P50 | 2.42–3.02 s | 2.61 s |
| 端到端定稿 P95 | 4.15–4.80 s | 4.43 s |
| 常驻内存 | 约 3.5 GB | 约 3.5 GB |

原始数据见 `../../backend/data/metrics/meeting-229-b3c1b6acf1ab.json` 至 `meeting-233-5084a1e8c5fb.json`。指标字段由 `../../backend/imeeting/engine/metrics.py:214-276` 输出。

已有基准还表明：

- 4 线程下 SeACo-Paraformer 离线约 5.6 倍实时，SenseVoice 约 8.4 倍实时，Nano Q5 约 4.4 倍实时，见 `../../benchmarks/asr_model_upgrade_20261017/REPORT.md:230-241`。
- 真实会议 CER：SeACo 11.82%，Paraformer 12.15%，SenseVoice GGUF 12.81%，Nano Q5 11.00%，见 `../../benchmarks/asr_model_upgrade_20261017/REPORT.md:163-168`。
- SenseVoice GGUF 常驻内存约 289 MB，Nano Q5 约 1.22 GB，SeACo/PyTorch 约 3.55 GB，见同一报告 `:163-168`。
- 仓库已有 C++ ONNX 2pass 服务，支持 VAD、流式/离线模型、标点、热词和时间戳，见 `../../../runtime/websocket/bin/websocket-server-2pass.cpp:79-100`、`:187-230` 和 `:469-512`。

### 2.3 当前非模型热点

1. **事件循环内的同步音频写盘**：每个音频块在 `sink.push(pcm)` 中执行 NumPy 转换和文件写入，见 `../../backend/imeeting/ws/stream.py:205`、`../../backend/imeeting/engine/audio.py:106-119`。
2. **每个音频块一次数据库提交**：收到带序号的音频块后等待 `_record_audio_seq()` 完成再发 ack，见 `../../backend/imeeting/ws/stream.py:209-212`；该函数每次打开 Session 并 commit，见 `../../backend/imeeting/ws/stream.py:801`。
3. **每个 final 单独提交一次**：`emit()` 对每条定稿先发前端，再在线程中写库，见 `../../backend/imeeting/ws/stream.py:89-99`。
4. **线程预算可能超订**：自动推理线程数是 CPU 核数减一，见 `../../backend/imeeting/engine/pool.py:34-56`；同时每次 Torch 推理又使用 `asr.ncpu=4`，见 `../../backend/imeeting/settings.py:140-165`。在 16 逻辑核机器上，11 个执行线程与每次 4 个模型线程并存，存在超订风险。
5. **后台任务串行**：长导出、重建索引、音频复核会阻塞同一 worker 队列，见 `../../backend/imeeting/jobs.py:185-215`。

这些热点应在完整重写前先修掉，否则新架构会继承相同的延迟和队列问题。

## 3. 语言边界

| 模块 | 目标语言 | 理由 | 明确不负责 |
|---|---|---|---|
| WebSocket/REST 网关 | Go | 连接多、状态机多、I/O 密集；goroutine 和显式背压更合适 | 不直接加载模型 |
| 会话/任务/权限/配置 | Go | 业务状态集中，测试与并发控制清晰 | 不做 DSP 和推理 |
| 流式/离线 ASR | C++ | 复用已有 ONNX Runtime 与 FunASR C API；可利用 int8、线程控制和零拷贝音频缓冲 | 不处理 JWT、会议权限、LLM 提示词 |
| VAD、标点、热词、时间戳 | C++ | 与 ASR 同进程，减少帧级跨进程调用与会话 cache 同步 | 不决定会议业务状态 |
| CAM++ 声纹与聚类 | C++ 优先，Python 过渡 | 声纹是热点且当前 C++ Runtime/GGUF 尚未覆盖；需要单独补齐 | 不写数据库 |
| LLM 纪要/元信息 | Python 过渡 | 网络等待为主，提示词和结构变化快 | 不接收音频 |
| 文档导出/检索嵌入 | Python 过渡 | 生态成熟，CPU 占比低 | 不属于实时链路 |
| SQLite/PostgreSQL | Go 统一写入 | 单一写入者，便于批量事务与恢复 | C++ 不直接写业务表 |

不建议用 Go 直接 cgo 链接 C++ 模型。进程隔离可以避免模型崩溃带死整个网关，也便于独立重启、限核、升级和回滚。Go 与 C++ 使用 gRPC over Unix Domain Socket；本机部署无需 TCP，也无需为每帧单独建立 RPC。

## 4. 目标架构

```
Browser
  │ WebSocket: binary PCM + JSON control
  ▼
Go Gateway
  ├─ auth / meetings / config / export APIs
  ├─ per-session ring buffer + backpressure
  ├─ durable segment writer + job scheduler
  └─ gRPC bidirectional stream
          │
          ▼
     C++ ASR Engine
       ├─ audio normalization / resampling
       ├─ FSMN-VAD + energy hysteresis
       ├─ streaming Paraformer + per-session cache
       ├─ offline Paraformer + Punc + hotwords
       ├─ CAM++ embedding + cluster state
       └─ partial/final/timestamp events

Go Gateway ──HTTP/gRPC──> Python Intelligence Worker
                           ├─ LLM minutes
                           ├─ meeting metadata
                           └─ DOCX/PDF/search indexing
```

### 4.1 状态归属

| 状态 | 唯一 owner | 生命周期 | 持久化/传递 |
|---|---|---|---|
| WebSocket 连接、心跳、ack | Go | 建连到断开 | 仅内存 |
| 会议、片段、任务、权限 | Go | 数据库记录周期 | SQLite/PostgreSQL |
| 待发送 final 队列 | Go | 会话结束或落库成功 | 内存 + 重试队列 |
| ASR cache、VAD 缓冲 | C++ 会话对象 | `start` 到 `stop` | 仅引擎内存 |
| 当前说话人聚类质心 | C++ 会话对象 | 会话周期，可选快照 | 事件回传，Go 落库 |
| 音频文件元数据 | Go | 文件生命周期 | DB 路径 + 文件系统/对象存储 |
| 模型配置与版本 | Go | 配置版本周期 | DB，启动时下发给 C++ |
| LLM 草稿/终稿 | Python 生成，Go 持久化 | 任务周期 | DB |

同一份状态只能有一个写入者。特别是：C++ 不写 `Segment` 表；Go 不复制 ASR cache；Python 不直接修改实时会话状态。

### 4.2 实时数据流

1. 浏览器发送 `start`，Go 校验 token、并发上限和会议归属。
2. Go 创建 `session_id`，向 C++ 打开一个双向流，发送不可变 `RunProfile`。
3. PCM 帧进入每会话有界 ring buffer，按 `seq` 发送给 C++。
4. C++ 回送 `partial`、`final`、`speaker`、`noise_warning`、`backpressure`。
5. Go 将 `partial` 合并后推送浏览器；将 `final` 放入批量写库队列。
6. `stop` 后 Go 发送半关闭信号，C++ 冲刷最后一句，两者按超时和幂等 `seq` 收尾。
7. Go 更新会议状态并异步投递纪要/元信息任务。

建议协议：

```proto
StartSession { meeting_id, session_id, sample_rate, chunk_ms, profile_version }
AudioFrame   { seq, captured_at_ms, pcm_s16le }
Control      { stop, partial_hint }
EngineEvent  { partial | final | speaker | warning | error | stats }
```

每会话最多缓存 4–8 秒音频。达到高水位时先合并 partial、再向浏览器发 `slow_down`；不得因为磁盘或数据库变慢而无限堆积 PCM。

### 4.3 持久化策略

- `final` 先进入 Go 的持久化队列，批量每 200 ms 或 8 条提交一次。
- `audio_chunk_seq` 不再每 600 ms 写库；每 2–5 秒 checkpoint，断线时以最后 checkpoint 为恢复点。
- WAV 写入使用独立 I/O 线程和顺序缓冲写；音频留存默认 7 天，见 `../../backend/imeeting/settings.py:1071-1078`。
- SQLite 继续使用 WAL；多实例部署后迁移 PostgreSQL。任务领取使用 `FOR UPDATE SKIP LOCKED` 或等价机制。

### 4.4 线程模型

原则是“总线程预算不超过物理核数”，而不是每个会话各开一套线程池。

以当前 i5-1240P 的 12 个物理核为例，起始配置可测试：

- Go gateway：2–4 个 OS 线程，CPU 任务极少。
- C++ 实时引擎：4 个 decode worker × 1–2 个 intra-op 线程。
- C++ 离线队列：2 个 worker，与实时流分开；必要时跨会话 micro-batch。
- 预留 1–2 个物理核给 OS、日志和 Python 侧车。

最终值必须通过配置矩阵确定：`decode_workers ∈ {2,4,6}`、`intra_op_threads ∈ {1,2,4}`、并发 `∈ {1,2,4}`。禁止简单使用“逻辑核数减一”作为推理线程数。

## 5. 备选方案与取舍

| 方案 | 性能上限 | 迁移风险 | 运维复杂度 | 主要问题 | 结论 |
|---|---|---|---|---|---|
| 保持 Python，只做 profiling 和局部优化 | 中低 | 低 | 低 | 无法消除 Python/Torch 的线程与内存开销 | 必须先做，但不是终态 |
| Go 全量重写，继续调用 Python 模型服务 | 低到中 | 中 | 中 | 模型仍占主要时间；多一层 RPC | 不推荐单独采用 |
| C++ 全量重写业务和模型 | 高 | 很高 | 高 | Web、权限、任务、文档生态全部重做 | 不推荐 |
| Go 网关 + C++ 推理 + Python 智能侧车 | 高 | 可控 | 中 | 三进程、协议和版本管理 | **推荐** |
| GGUF 单二进制替换整个后端模型链 | 中高 | 中 | 低 | 当前 GGUF 不含 CAM++，热词与模型质量需复验 | 可作为边缘部署分支 |

## 6. 预期收益与不确定性

### 6.1 较有把握的部分

- 去掉音频同步写盘和逐块数据库提交后，音频 ack 抖动、事件循环阻塞和 SQLite 写放大会明显下降。
- Go 网关的常驻内存和连接抖动会低于 Python 控制面；预计主要改善 P99、重连和大量空闲连接场景，而不是模型 RTF。
- C++/ONNX 可以复用现有 Runtime，避免重新实现模型算法。

### 6.2 尚需实测的部分

- 当前生产离线模型是 SeACo-Paraformer；仓库中成熟的 C++ 2pass 路径主要验证 Paraformer、VAD、Punc 和热词。SeACo 是否能等价导出、精度是否可接受，必须先做导出与 CER 对照。
- GGUF 路径当前没有 CAM++ 声纹嵌入和聚类，见 `../../../runtime/llama.cpp/README.md:83-88`。
- ONNX int8 的官方吞吐数据来自 Xeon，不能直接外推到 i5-1240P。目标应是本机实测 1.5–3 倍定稿吞吐和低于 2 GB 常驻内存，而不是承诺固定倍数。
- Go 网关本身预计只贡献端到端 0–5% 的改善；若 profiling 显示模型占比超过 80%，应优先投入 C++ 推理而不是 Go。

## 7. 分阶段迁移

### Phase 0：基线与门禁（约 1 周）

交付：

- 火焰图/采样 CPU 报告，拆分模型、音频 DSP、数据库、序列化和空闲等待。
- 线程矩阵压测：1/2/4 并发 × 1/2/4 intra-op 线程。
- 真实会议回归集：至少 100 条片段，含远场、噪声、多人、热词。

完成判据：

- 模型 CPU 占比、单会话 CPU、P50/P95 延迟可重复测量。
- 明确“重写目标”：CPU、P95、内存、并发中的至少两项。

回滚：无生产变更。

### Phase 1：先修 Python 关键路径（约 1 周）

交付：

- 音频写入移到独立队列/线程，禁用或批量化逐块 `audio_chunk_seq` 提交。
- streaming 与 offline 使用独立有界队列，避免离线长任务堵住实时 partial。
- 修正 `infer_threads` 与 `asr.ncpu` 的线程预算，默认不超过物理核数。
- 后台任务按类型分池，导出/索引不阻塞纪要。

完成判据：

- 音频 ack P95 不超过 20 ms。
- 4 并发下无超订，partial 不因磁盘/数据库排队。
- 端到端定稿 P95 相比基线改善至少 10%，或证明瓶颈仍在模型。

回滚：保留旧配置开关，按指标关闭批量写与分流。

### Phase 2：C++ 推理侧车影子运行（约 2–3 周）

交付：

- 封装现有 FunASR C++ Runtime，提供 `StartSession / AudioFrame / StopSession` gRPC。
- 离线回放历史音频，影子生成文本、时间戳和 RTF。
- 对比 SeACo、ONNX Paraformer、SenseVoice GGUF 的 CER、热词命中率和内存。

完成判据：

- 真实会议 CER 相对现网增加不超过 0.5 个百分点；超过则不得切流。
- 离线 P95 至少快 1.5 倍，或常驻内存降到 2 GB 以下。
- 4 路并发无崩溃、无跨会话串音。

回滚：影子结果不进入用户可见链路；C++ 进程可随时摘除。

### Phase 3：实时链路灰度到 C++（约 2–3 周）

交付：

- Python 网关通过 gRPC 接入 C++ sidecar，先按 5% 会议灰度。
- 增量 5% → 25% → 100%。
- 断线、模型崩溃、队列满、引擎超时均有降级开关。

完成判据：

- 4 并发下 P95 final latency ≤ 4 s，P95 first partial ≤ 1.5 s。
- 24 小时 soak 无内存增长、无会议卡在 `recording`。
- 任何会话失败都能回退到 Python 推理路径。

回滚：按配置或网关路由切回 `asr.mode=normal` 的 Python 实现。

### Phase 4：Go 网关替换（约 2–3 周）

交付：

- Go 实现 REST、WebSocket、鉴权、配置、会议与任务 API。
- 对外接口保持现有 `/api/v1` 与 `/ws/stream` 契约。
- 先把只读接口切流，再切写接口，最后切 WebSocket。
- Python 仅保留 LLM、元信息、导出与检索。

完成判据：

- API 契约测试与现有前端全部通过。
- 网关 CPU 低于 0.2 核/4 路空闲连接，重连 5 轮无状态泄漏。
- 24 小时生产灰度无 5xx 回归。

回滚：反向代理路由回旧 FastAPI；数据库 schema 不变。

### Phase 5：可选优化（按收益决定）

- C++ 实现 CAM++ ONNX 嵌入与聚类，彻底去掉实时链路中的 Python 声纹调用。
- 离线片段跨会话 micro-batch。
- GPU 部署切换 ORT CUDA/TensorRT，保持 Go 协议不变。
- 若热词和精度允许，评估统一 GGUF 边缘部署包。

## 8. 失败模式与降级

| 失败 | 行为 | 恢复 |
|---|---|---|
| C++ 引擎崩溃 | Go 停止该会话送流，标记 `abandoned`；已落库 final 保留 | supervisor 重启；后续会话回退 Python |
| 模型导出不兼容/精度下降 | 影子阶段即阻断切流 | 保留 PyTorch 网关 |
| gRPC 流超时 | Go 关闭会话流，最多丢失 ring buffer 内 4–8 s 音频 | 重连后新会话，已定稿片段不重写 |
| Go 持久化队列满 | 暂停接收音频并发送明确错误；优先保住 final | 清空积压后恢复，不允许无限内存增长 |
| SQLite 锁/磁盘慢 | 批量写退避，final 进入重试队列；不阻塞 partial | WAL checkpoint/扩容/迁移 Postgres |
| C++ 会话 cache 泄漏 | 会话 TTL + 空闲回收 + 进程级内存告警 | 强制结束会话并重启 worker |
| 引擎版本与 Go 协议不匹配 | 启动握手校验 `protocol_version` 和模型能力 | 回滚到上一版本镜像 |
| 浏览器慢消费 | 合并 partial、丢弃中间草稿，保 final | 发送 `slow_down`，不丢完整音频 |

## 9. 可观测性

所有日志与指标必须带 `meeting_id`、`session_id`、`seq`、`profile_version` 和 `engine_instance`。

新增指标：

- `gateway_ws_connections`
- `gateway_pcm_queue_depth`
- `gateway_pcm_dropped_total`
- `engine_stream_rtf`
- `engine_offline_rtf`
- `engine_queue_wait_ms`
- `engine_partial_latency_ms`
- `engine_final_latency_ms`
- `engine_session_cpu_seconds`
- `engine_rss_mb`
- `persist_batch_latency_ms`
- `reconnect_total`

关键链路追踪字段：`received_at → engine_started_at → first_partial_at → final_generated_at → persisted_at → client_ack_at`。

## 10. 回滚方案

1. **协议向后兼容**：新旧网关都能读取现有 DB；不做破坏性迁移。
2. **双路径开关**：`inference_backend=python|cpp`、`gateway_backend=python|go`。
3. **按会议灰度**：以 `meeting_id` 哈希分流，避免同一会议中途换实现。
4. **影子结果不入库**：C++ 影子阶段只写对比目录，不污染生产数据。
5. **镜像与配置独立发布**：Go 和 C++ 可单独回滚，不回滚数据库。
6. **故障自动降级**：C++ 连续失败达到阈值时自动切回 Python，并记录告警。

## 11. 人力与周期估算

建议 3–4 人并行：

- 1 名 Go 后端：网关、任务、持久化、灰度。
- 1 名 C++/语音工程：Runtime 封装、会话状态机、模型导出。
- 1 名 Python/算法：基线、CER 对照、CAM++、LLM 侧车。
- 0.5–1 名 QA/运维：压测、故障注入、发布回滚。

从 Phase 0 到 Go 全量切换，合理区间为 8–12 周。若 SeACo 无法等价导出或 CAM++ 必须同时 C++ 化，预留额外 2–4 周。

## 12. Alternatives（含否决理由）

- **只做 Go 全量重写**：业务层更快，但模型仍占主要 CPU，收益与成本不匹配。
- **只做 C++ 全量重写**：性能好，但把 Web、权限、任务、导出全部重做，交付与维护风险过高。
- **继续 Python 并只调参数**：短期可取，但无法解决线程超订、同步 I/O、单进程内存与模型运行时问题。
- **直接用 GGUF 二进制替代全部链路**：部署最轻，但当前缺少 CAM++，且真实会议精度优势不显著。

## 13. Consequences

正面：

- 重型模型与业务逻辑解耦，可独立扩缩容、限核与升级。
- 实时链路具备明确背压和故障隔离。
- 可以逐步替换，不需要停机迁移或重写前端。

代价：

- 引入 Go/C++/Python 三语言，需要协议版本管理与跨语言测试。
- 需要维护模型导出、数值一致性和 CER 回归。
- C++ 崩溃排查和 Windows/Linux 双平台构建成本高于单 Python 进程。

## 14. 最终建议

先做 Phase 0 和 Phase 1。如果 profiling 证实模型占比超过 80%，立即进入 C++ 推理侧车；Go 网关在 C++ 推理稳定后再替换。若模型占比低于 50%，说明控制面和 I/O 才是主因，应优先 Go 网关并推迟 C++ 化。无论走哪条路，都不做一次性全量重写。
