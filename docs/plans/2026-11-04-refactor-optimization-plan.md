# iMeeting 重构与优化方案（高效 / 稳定 / 可扩展 / Windows+Linux 双平台）

<!-- doc-governance: IM-PLN-003 -->
> 文档类型：计划；状态：**已被替代** → [v2（IM-PLN-004）](2026-11-04-refactor-optimization-plan-v2.md)；内容基准：`perf` 分支，commit `55b0cfd`；整理日期：2026-11-04。
> 维护责任：待指派（iMeeting 文档维护者）。[文档中心](../README.md) · [维护规则](../governance/README.md)。
>
> **本版保留原样不修订**：它是 [IM-REV-006](../reviews/2026-11-04-refactor-plan-review.md) 的受审对象，
> 评审报告逐条引用了本文的措辞；修订内容全部落在 v2。已知问题见评审报告 §2（含"12 项"应为 14 项等事实性错误）。

**本文只交付方案，不含实现。** 所有"现状"结论均来自对当前代码的静态核查，已在正文标注文件与行号；
标注 **[未实测]** 的条目表示结论来自代码推理，实施前需先用本文给出的命令复现。

---

## 0. 需求理解与范围

### 0.1 需求

> 仔细分析整个项目的代码，站在**高效、稳定、可扩展、Windows/Linux 都适配**的特性进行重构和优化。

拆解为四条可判定的目标：

| 特性 | 本方案的可判定含义 | 不做的事 |
|---|---|---|
| 高效 | 单场 2 小时会议的**逐段落库成本从 O(N²) 降到 O(N)**；前端每处理 1 条 `partial` 的成本从 O(n log n) 降到 O(1)；事件循环不再被同步 DB 调用阻塞 | 不改 ASR 模型、不自研流式神经网络 VAD、不动 RTF 基线 |
| 稳定 | 无界队列/线程/内存有上界；配置变更不泄漏线程；启动不做全表重写；单点故障有降级路径 | 不做多实例、不引 Redis/PostgreSQL |
| 可扩展 | 新增一个后台任务类型、一个配置项、一种导出格式，只需新增文件 + 一行注册，不改核心分支 | 不重写技术栈（Go+C++ 重写在 [IM-DES-003](../architecture/native-rewrite-plan.md) 独立推进） |
| 双平台适配 | CI 有 Windows 门禁；运维能力（备份/恢复/优雅重启）在两个平台都可用且**同一份实现**；文档命令分平台给出 | 不做 Windows 服务/安装包 |

### 0.2 现状规模（本次核查基线）

| 项 | 数值 | 来源 |
|---|---|---|
| 后端业务代码 | 20,367 行 / 193 个 py | `git ls-files backend/src` |
| 后端测试 | 12,115 行 / 60 个文件 | `git ls-files backend/tests` |
| 前端代码 | 19,340 行 / 132 个文件 | `git ls-files frontend/src` |
| 已知历史评审 | 已修复 7 轮（IM-REV-001…005） | `docs/README.md` |

结论：**这不是一个"从零重构"的项目，而是一个功能已完备、局部存在结构性欠债的项目。** 因此本方案不推翻架构，
只在四个方向上做"外科式"改造，并保证每一批改动都可以独立合并与回滚。

### 0.3 明确不采纳的建议

- **不引入虚拟滚动**。IM-RPT-009（`docs/reports/2026-11-01-responsive-ui.md`）已在真实长列表上实测并明确不引入；
  本方案改用"消除每次 partial 的全量排序与强制重排"来解决同一问题，代价更小、风险更低。
- **不按 `OPTIMIZATION.md` 第三节改流式 VAD**。那属于识别功能变更，不属于重构；该文档已标注为历史记录（IM-REV-001）。
- **不动 SQLite 单实例边界**。多实例属 P2 扩容，见 [生产可用性清单](../production-readiness.md)。

---

## 1. 结论先行：12 项关键问题

按"C/P/R/X"编号：**C** 正确性与稳定性、**P** 性能与资源、**R** 可扩展性、**X** 跨平台。

| 编号 | 问题 | 位置（已验证） | 影响 | 批次 |
|---|---|---|---|---|
| C-1 | 每条定稿都重查本场**全部** `segment_id` 并重算 `COUNT(*)`，单场总成本 O(N²) | `backend/src/imeeting/application/meeting_sessions.py:98`、`:150` | 2 小时会议约 1200 段 → 约 144 万行扫描 + 1200 次全局写锁 | 1 |
| C-2 | 每个音频块提交一次事务写 `audio_chunk_seq` | `backend/src/imeeting/api/ws/stream.py:327` | 每会话约 2–10 次/秒的 SQLite 写，与 C-1 抢同一把写锁 | 1 |
| C-3 | 异步 WS 处理器内直接调用同步 DB（收尾路径最多 6 次） | `backend/src/imeeting/api/ws/stream.py:244,250,253,254,261,416,460,471,473,474` | 每次最长可阻塞事件循环 `busy_timeout=5000ms`，期间所有会话与 HTTP 请求停摆 | 1 |
| C-4 | 启动时执行约 700 行幂等 DDL/DML，含全表 `DELETE`、全表 `UPDATE`、逐行 `UPDATE` | `backend/src/imeeting/db.py:110`（`:352`、`:364`、`:471`） | 启动耗时随数据量线性增长；每次启动都产生写入与备份差异 | 2 |
| P-1 | 前端每收到 1 条 `partial` 就复制整个数组 + 全量 `sort` + 重建整张 `Map` | `frontend/src/store/session.ts:606` | 2 小时会议约 1.2 万条 partial，末段每次 O(n log n)，明显掉帧 | 1 |
| P-2 | `useLayoutEffect` 依赖 `[segments]`，每条 partial 强制回流并改写 `scrollTop` | `frontend/src/components/TranscriptStream.tsx:38`、`:46` | 每 0.6 秒一次同步重排（forced reflow） | 1 |
| P-3 | 页面级订阅 `segments` 后在渲染体内做 3 次 `filter` | `frontend/src/pages/Meeting.tsx:44,90,185,332` | 整页随 partial 重渲染 | 1 |
| P-4 | 推理线程数变更时新建池但不关闭旧池 | `backend/src/imeeting/engine/pool.py:59`（`:73`） | 每次改动配置泄漏 `N` 个常驻线程（含模型句柄引用） | 2 |
| P-5 | 声纹快照写入无条件清空全局缓存（10s TTL） | `backend/src/imeeting/engine/speaker.py:359` | 快照每 5 段一次 → 缓存几乎永不命中，每段回表 | 2 |
| P-6 | 定稿并发任务与推理队列无上界；`AudioSink` 每块 2 次 seek + flush | `backend/src/imeeting/engine/streaming.py:693`、`engine/audio.py:177` | 过载时内存与 IO 无界增长 | 2 |
| R-1 | `StreamSession` 一个类 1283 行、约 90 个字段，混合 VAD/双路 ASR/声纹/翻译/指标/排序/会话注册 | `backend/src/imeeting/engine/streaming.py:58` | 任何单点改动都要通读全类；无法单测单段 | 3 |
| R-2 | 配置项 1380 行内联在单个模块；同一 key 需在 `CONFIG_SCHEMA` 与 `ENV_OVERRIDES` 两处登记 | `backend/src/imeeting/settings.py:59`、`:1506` | 新增配置易漏登记、易漂移 | 3 |
| R-3 | 后台任务用 if/elif 长链分发；新增类型必须改 `_run_job` | `backend/src/imeeting/application/jobs.py:143` | 与 P-4/P-6 一起构成扩展瓶颈 | 3 |
| X-1 | CI 只有 `ubuntu-latest`，Windows 零门禁 | `.github/workflows/ci.yml` | "Windows 适配"没有任何强制保障，回归只能靠人工 | 1 |

> 其余 10 项次要问题见 §4–§7 各表的"其他"小节。

---

## 2. 高效：性能与资源

### 2.1 后端热路径

#### C-1 逐段落库是 O(N²)（最高优先级）

**现状证据**（`meeting_sessions.py`）：

```python
existing_ids = set(db.exec(select(Segment.segment_id).where(Segment.meeting_id == meeting_id)).all())  # :98
...
db.flush()
meeting.segment_count = int(db.exec(select(func.count()).select_from(Segment)
                                   .where(Segment.meeting_id == meeting_id)).one())                 # :150
```

`stream.py:155` 在**每条定稿**上调用一次 `_persist_segments(meeting_id, [payload])`，
于是第 k 段落库时要扫 k 行、算一次 k 行 `COUNT`。整场总工作量 ≈ N²/2 行扫描，且全部持有
`_persist_lock`（`meeting_sessions.py:17`），与 C-2 争抢同一把 SQLite 写锁。

**方案**（不改语义，只改算法）：

1. 唯一索引 `ux_segment_meeting_sid (meeting_id, segment_id)` 已经存在（`db.py:358`），
   直接把插入改成 `INSERT ... ON CONFLICT(meeting_id, segment_id) DO NOTHING`，
   用 `rowcount` 得到 `added`，彻底删除 `existing_ids` 预查询；
2. `segment_count` 改为 `meeting.segment_count = (meeting.segment_count or 0) + added`，
   删除每次的 `COUNT(*)`；收尾时用一次 `COUNT(*)` 校正（幂等，成本 O(N) 且只发生一次）；
3. `_persist_lock` 的持有范围缩到"构造 + commit"，查询阶段不持锁。

**验收**：单场 1200 段的模拟会议（`backend/tests/test_persistence_race.py` 已有并发落库用例可扩展），
断言落库总 `SELECT` 次数与段数呈线性（新增计数器或在测试里统计 `event.listen` 的 SQL 次数），
且 `segment_count` 与 `COUNT(*)` 一致；重复投递同一批 payload 不产生重复行。

> 相关既有保障：`db.py:360` 的唯一索引与去重 DELETE 已经兜住历史脏数据，本次只是不再依赖"先查后插"。

#### C-2 每块一次 `audio_chunk_seq` 提交

`stream.py:325` 每收到带 `seq` 的音频块就 `_record_audio_seq(...)`（一次独立事务）。该字段的唯一用途是
断线补传时告诉前端从哪继续（`_load_audio_seq`），**不需要逐块持久化**。

**方案**：会话内维护内存值，按"每 32 块 或 每 5 秒 或 收到 `stop`"三种触发条件合并写库；
收尾路径已经会写 `_finish_session_row`（`stream.py:474`），把最终值并入即可。
风险点：进程被强杀时最多回退 5 秒的补传范围 → 前端 `flushStoredAudio` 会重发少量已收到的块，
服务端按 `seq <= audio_chunk_seq` 幂等丢弃（`stream.py:316`），**行为不变**。

**验收**：`frontend` 侧断线重连冒烟 + `backend/tests/test_ws_protocol.py`；同时断言"一次 60 秒会话的
`meeting` 表写事务数 ≤ 5"（用 SQLAlchemy `before_cursor_execute` 计数）。

#### C-3 事件循环被同步 DB 阻塞

`stream.py` 是 `async def`，但以下调用是同步阻塞的：`_resolve_meeting`(244)、`_load_audio_seq`(250)、
`_session_offset_ms`(253)、`_start_session_row`(254)、`_load_adaptive_profile`(261)、
`_persist_segments`(416/460)、`_record_audio_path`(471)、`_save_adaptive_profile`(473)、
`_finish_session_row`(445/474)。同文件里 `_record_audio_seq`(327) 与 `_persist_segments`(157) 已经用了
`asyncio.to_thread`，**说明这是实现不一致造成的遗漏，不是有意的设计**。

**方案**：不要在 10 处逐个补 `to_thread`（下次新增调用还会漏）。改为收口一个**单写者**：

```
application/session_writer.py
  class SessionWriter:            # 每进程一个，内部一个线程 + 有界队列
      submit(op) -> Future        # 异步提交，返回可 await 的 future
      close()                     # 排空后停止
```

- WS 处理器只 `await writer.submit(...)`，不再直接 import `meeting_sessions`；
- 单写者天然序列化 SQLite 写入 → 顺带取代 `_persist_lock`；
- 队列有界（例如 256），满时对"非关键写"（audio_seq、adaptive_profile）直接丢弃并计数，
  对"关键写"（定稿片段、会议状态）反压等待。

**验收**：新增 `tests/test_session_writer.py`（顺序性、有界、关闭排空、异常不吞）；
并用 `scripts/stress.py --only concurrency --sessions 4` 对比改造前后的 P95 首字延迟与 5xx 数。

#### P-6 无界队列与 IO 放大

| 子项 | 现状 | 方案 |
|---|---|---|
| 定稿任务 | `streaming.py:693` `asyncio.create_task` 无数量上限；`pool.run_in_executor` 的待执行队列无界 | 每会话给一个 `asyncio.Semaphore(asr.infer_threads)`；队列深度进 `/metrics`，超过阈值时对"无语音片段"提前丢弃而不入队 |
| 音频写盘 | `engine/audio.py:177` 每块 `seek(0)+write(44)+seek(0,2)+flush()` | 头回填降频到"每 64 块或每 1 秒"，`close()` 与进程退出时强制回填一次；`queue.Queue` 已有界（保持） |
| 流式缓冲 | `_take_streaming` 用 `list.pop(0)`（`streaming.py:430`） | 换成 `collections.deque` + 单次 `np.concatenate`，避免 O(n) 头删 |

### 2.2 前端热路径

#### P-1 `upsertSegment` 是全量重排

```ts
const segments = state.segments.slice()
segments[index] = seg
segments.sort((a, b) => a.startMs - b.startMs || a.seq - b.seq)   // session.ts:628
const indexById = new Map(segments.map(...))                       // :629
```

`indexById` 的注释写的是"用于 O(1) 覆盖更新"（`:47`），但每次覆盖都把**整张 Map 重建**，O(1) 名存实亡。

**方案**（三档，可只做前两档）：

1. **partial 快路径**：若 `seg.isFinal === false` 且命中的是"当前最后一段"，
   直接 `segments[segments.length - 1] = seg`（新数组，长度不变），**不排序、不重建 Map**；
2. **final 路径**：命中已有索引且 `startMs` 未变 → 只替换该下标，仍不排序；
   只有"新片段或时间戳变化"才走排序 + 增量 Map 更新（只 `set` 变化的那一个键）；
3. 把 `indexById` 从 state 移到 `useRef`/模块级 `WeakMap`，避免每次 setState 连带重建。

**验收**：`src/store/session.test.ts` 扩充——连续 2000 条 partial 后 `segments` 顺序与全量排序结果一致、
`indexById` 与 `segments` 双向一致；用 `performance.now()` 断言单条 partial 处理 < 0.2ms（jsdom 下取相对值）。

#### P-2 每条 partial 强制回流

`TranscriptStream.tsx:38` 的 `useLayoutEffect` 依赖 `[segments]`，第 46 行 `el.scrollTop = el.scrollHeight`
——这是**同步布局**读取，每条 partial 触发一次（0.6 秒一次，长会议期间持续）。

**方案**：依赖改为 `[segments.length, lastSegmentText, pinned]`；滚动动作包进 `requestAnimationFrame`
并在 `pinned === false` 时完全跳过。行为不变（贴底才跟随，`deriveScrollState` 不变）。

#### P-3 页面级重渲染

`Meeting.tsx:44` 订阅 `s.segments`，渲染体内 `:90`、`:332` 各做一次 `filter(s => s.isFinal)`，
`:185` 一次 `some`。→ 每条 partial 都跑 3 次 O(n) 扫描并重渲染整个 `<Meeting>` 子树。

**方案**：用 `useSession((s) => s.segments.length)` 之类的细粒度 selector 拆分订阅；
把 `finalSegmentCount` 收进 store（在 final 路径里 `+1`）或用 `useMemo([segments])`；
给 `TranscriptStream` 的 props（`onSeek`/`onRenameSpeaker`/`importanceByKey`）统一 `useCallback/useMemo`，
让 `SegmentCard`（已 `memo`）真正短路。

#### P-4 发送路径被本地磁盘写入拖住

`api/ws.ts:204` 的 `sendAudio` 先 `await putChunk(...)`（IndexedDB 事务）再发网络：

```ts
this.sendChain = this.sendChain.then(async () => {
  const seq = this.nextSeq++
  await putChunk(meetingId, seq, buffer)   // ws.ts:212
  this.sendChunk(seq, buffer)
})
```

含义：每块音频的实际发送延迟 = IndexedDB 提交时间 + 网络。且这条 Promise 链无 `.catch`、无深度上限
（当前 `putChunk` 内部把所有错误都 `resolve`（`audioStore.ts:41`），所以暂时不会中毒——但这是**隐式契约**，
任何后续改动加一条可能抛错的步骤都会让整条链永久停摆）。

**方案**：改成 write-behind——先 `sendChunk`，再把 `putChunk` 丢进独立的串行队列（不阻塞发送）；
链上加 `.catch()` 并计数；`preReady` 数组加上界（例如 300 块，超出丢最旧并提示）。

---

## 3. 稳定：故障面与上界

### 3.1 已验证的稳定性缺口

| 编号 | 缺口 | 位置 | 处置 |
|---|---|---|---|
| S-1 | 并发会话上限是"先读后写"（check-then-act），两个连接可同时通过 | `api/ws/stream.py:235` | 把 `session_registry.count()` 判断改成 `try_acquire() -> bool` 原子操作 |
| S-2 | 配置变更时旧线程池不 `shutdown`，线程常驻且持有旧模型引用 | `engine/pool.py:71` | `old.shutdown(wait=False)` + 记录在 `/metrics` 的线程数；并给"改线程数"加一条告警日志 |
| S-3 | 声纹缓存与每次快照写入互相失效 | `engine/speaker.py:359` | 快照写入改为"只失效单个 `meeting_id` 的缓存项"，提供 `invalidate(meeting_id)`；`save_discovered` 只失效本会议 |
| S-4 | `pool._models` / `pool._cache` 私有字典被直接读取 | `engine/streaming.py:439`、`:799` | 增加 `pool.acquire(prefix)` 返回（模型, 版本号）；模型重建时版本号变化，让会话感知并重载，避免拿到已被替换的实例 |
| S-5 | 帧长无上限检查，仅靠 uvicorn 默认 16MB | `api/ws/stream.py:543` | 显式上限（如 1MB，约 32 秒音频），超限直接丢弃并计数 |
| S-6 | 启动期三条迁移机制并存（`create_all` + 手写 DDL + Alembic） | `db.py:69` | 见 C-4 |

### 3.2 C-4 启动期全表改写

`db.py:110` 的 `_migrate_schema()` 在**每次启动**执行：

- `:352` `DELETE FROM segment WHERE id NOT IN (SELECT MIN(id) ... GROUP BY ...)` —— 全表扫描 + 排序；
- `:364` `UPDATE meeting SET segment_count = (SELECT COUNT(*) ...)` —— 会议数 × 段数；
- `:471` 逐条 `voiceprint` 循环里再逐条 `UPDATE segment` —— N+1 写；
- `:595` `_backfill_session_offsets()` —— 每次启动都重新分组扫描。

**方案**（分两步，第一步就能消掉绝大部分成本）：

1. **一次性化**：用 Alembic 已有的版本号判断"这些兼容迁移是否已经跑过"，跑过一次就跳过；
   保留代码路径以支持从老库升级，但不再每次启动执行；
2. **去重与计数改为约束 + 触发**：唯一索引已存在，历史脏数据用一次脚本清理（参考 `scripts/repair_template_slots.py` 的 dry-run/`--apply` 风格），
   `segment_count` 用触发器或"落库时增量 + 收尾校正"（与 C-1 同一处改动）。

**验收**：构造一个 5 万段、200 场的库，断言第二次及以后启动时**零 DML**（用 `before_cursor_execute` 统计，
只允许 `SELECT` 与 PRAGMA）；启动耗时从 O(N) 变为 O(1)。

---

## 4. 可扩展：结构与扩展点

### 4.1 R-1 拆分 `StreamSession`

现状：1 个类，1283 行，约 90 个实例字段，职责包括

`VAD/断句` · `自适应档位` · `噪声底跟踪` · `流式推理` · `离线定稿` · `声纹解析` · `字幕翻译` ·
`指标采集` · `定稿排序` · `WS 推送` · `活跃会话注册`

**目标结构**（按"数据流阶段"切，不按技术分层切）：

```
engine/session/
  session.py        # StreamSession：只做编排，约 200 行，持有下述组件
  ingestion.py      # push_audio：类型规整 → 归一化 → 分发
  segmentation.py   # 从 _detect_boundary 抽出：边界判定 + Segment 生命周期（不再直接 await WS）
  streaming_asr.py  # 流式增量：buffer 管理 + _run_streaming + delta 合并
  offline_asr.py    # 离线定稿：trim/AGC/denoise + 推理 + 质量门禁 + 降级
  speaker.py        # 从 _resolve_speaker/_apply_speaker/_maybe_snapshot 抽出
  ordering.py       # FinalOrderer 的调用方（_emit_final/_drain_finals）
  metrics_hooks.py  # 把 MeetingMetrics 的调用点集中成钩子
```

**约束**（这些约束是本次重构的验收标准）：

- 每个组件只通过构造参数获得依赖，**不 import 全局单例**（`pool`、`db`、`get_config` 都由编排层注入）；
- 组件之间只通过数据结构通信（`SegmentState`、`BoundaryDecision`、`SpeakerResolution`），不互相 import；
- `StreamSession` 的公开 API（`start/close/push_audio/rename_speaker_sync/adaptive_snapshot/...`）
  **保持不变**，`stream.py` 与测试不需要同步改（降低回归面）。

**收益量化**：现有 `backend/tests/test_streaming_engine.py`、`test_segment_state.py`、`test_boundary_decider.py`
已把部分逻辑独立测过——说明这些部件**本来就该是独立单元**，只是被装进了同一个类。

### 4.2 R-2 配置注册表

现状：`settings.py` 1757 行，其中 `CONFIG_SCHEMA`（`:59`–`:1497`，1380 行）是内联字面量；
`ENV_OVERRIDES`（`:1506`）把同一批 key 再登记一次。

**方案**：

```
settings/
  __init__.py       # 对外只暴露 get_config / config_float / config_store / CONFIG_SCHEMA
  field.py          # ConfigField + register() + 校验
  schema/
    asr.py  vad.py  spk.py  session.py  llm.py  auth.py  job.py  storage.py  export.py  ...
```

- 每个模块用 `register(_f(key=..., ...))` 声明自己那组的配置，`settings/__init__` 汇总；
- `ENV_OVERRIDES` 合并进同一个声明（`env="ASR_MODE"` 字段），消除两处登记；
- `type="password"` 的字段自动进"密钥加密落盘"路径，不再靠两处约定；
- 从注册表**生成** `.env.example` 与文档表格（新增 `python -m imeeting.settings.dump --example`），
  消除"代码 / `.env.example` / 文档"三处漂移。

### 4.3 R-3 任务处理注册表

`application/jobs.py:143` `_run_job` 是 `if job.kind == ...: return` 长链（7 个分支）。

**方案**：

```python
JOB_HANDLERS: dict[str, JobHandler] = {}

def handler(kind: str, *, heavy: bool = False):
    def wrap(fn): JOB_HANDLERS[kind] = JobHandler(kind=kind, fn=fn, heavy=heavy); return fn
    return wrap
```

- `_HEAVY_JOB_KINDS`（`:22`）由注册表的 `heavy` 标志推导，删除第二份清单；
- 新增任务类型 = 新增一个带 `@handler("export_csv")` 的函数文件，不需要动 `jobs.py`；
- 顺带把 worker 的 1 秒轮询（`:277`/`:294`）改成 `asyncio.Event` 唤醒：入队时 `set()`，
  领取不到时 `await event.wait()` 带超时兜底。既降空闲 DB 查询（当前 3 worker × 1.2 次/秒），
  又把新任务启动延迟从"最多 1 秒"降到接近 0。

### 4.4 其他结构项（按性价比排序）

| 项 | 现状 | 方案 |
|---|---|---|
| 路由文件过大 | `api/http/routes/meeting_extras.py` 1015 行、`voiceprints.py` 917 行、`meetings.py` 904 行 | 按子资源拆成同目录多文件，`router.py` 汇总；只移动代码，不改路径 |
| 前后端类型漂移 | `api/types.ts` 559 行手写，与 `schemas.py` 415 行无自动校验 | 加一个 `scripts/dump-openapi-types.py` 从 `/openapi.json` 生成 TS 类型，CI 里比对（只提示不阻断，逐步替换） |
| worklet 双份同源 | `src/audio/pcm-worklet.js` 与 `public/pcm-worklet.js` 手工保持一致（`recorder.ts:10` 有注释说明） | 单一来源放进 `src`，构建/开发时由插件复制到 `public`；加一条测试断言两文件字节相同 |
| 死依赖 | `react-window@1.8.10` 在 `package.json` 中但 `src` 无引用 | 移除 |
| 弱 lint | ruff 只开 `E4,E7,E9,F,I`（`pyproject.toml`） | 分批开 `B`（bugbear：可变默认参数、泄漏的 f-string 等）与 `ASYNC`（async 里的同步调用），先 `noqa` 存量 |

---

## 5. Windows / Linux 双平台适配

### 5.1 核查结论：代码层比预期干净

已核查并**确认无问题**的点（不必改）：

- 无 `os.fork` / `fcntl` / `multiprocessing` / `signal` 自管理代码（全仓 grep 结果为空）；
- 路径统一用 `Path / "x"`，无手拼 `"/"`；
- 文本 I/O 基本都带 `encoding`（`main.py:58` 显式 `utf-8`），`open()` 缺省编码的 4 处全是二进制模式；
- 原子替换统一用 `os.replace`（`sqlite_backup.py:112` 有说明），未用 `os.rename`；
- `.gitattributes` 已设 `* text=auto eol=lf`，仓库内换行正确。

**真正的适配缺口在"保障"和"运维面"，不在业务代码。**

### 5.2 X-1 CI 无 Windows 门禁（最重要的单点）

`.github/workflows/ci.yml` 四个 job 全部 `runs-on: ubuntu-latest`。

**方案**：backend job 改成矩阵，并区分"可在 Windows 上跑"的集合：

```yaml
strategy:
  matrix:
    os: [ubuntu-latest, windows-latest]
    python: ["3.10", "3.12"]
```

- Windows 上的后端测试用 `SKIP_MODEL_WARMUP=1` + `ASR_MODE=echo`，不装 `[asr]` 额外依赖
  （`pyproject.toml` 的 `asr` extra 是 torch/funasr，Windows CPU 轮子体积大且与本次改造无关）；
- 前端 job 也在 Windows 上跑 `npm ci && npm run typecheck && npm test && npm run build`——
  这条能直接暴露 shell 脚本、路径大小写、换行符问题；
- 已知 Windows 上会失败的用例（如有）单独打 `@pytest.mark.skipif(sys.platform == "win32")`，
  **并在测试里写清原因**，不允许整体跳过。

### 5.3 运维脚本不对称

| 能力 | Linux | Windows | 方案 |
|---|---|---|---|
| 备份 | `deploy/backup_sqlite.sh` | 无（README 要求 `sh ...`，即需 Git Bash/WSL） | 把逻辑下沉到 `python -m imeeting.sqlite_backup backup`（`sqlite_backup.py` 已有实现），`.sh` 与新增 `.ps1` 都变成 5 行薄包装 |
| 恢复 | `deploy/restore_sqlite.sh` | 无 | 同上（`restore` 子命令） |
| 优雅重启 | 无 | `scripts/graceful_restart.ps1` | 同上（`drain`/`ready` 检查已在 API 里），补一个 `.sh` 薄包装 |
| 文档命令 | `sh deploy/...` | README 里混用 `set DATABASE_URL=` 与 `sh` | 分平台写两段；PowerShell 用 `$env:NAME="value"` |

**为什么下沉到 Python 而不是维护两套 shell**：备份/恢复的正确性依赖"完整性校验 + 安全副本 + 原子替换"
（`sqlite_backup.py` 已有），这套逻辑在两份 shell 里各写一遍必然漂移。

### 5.4 平台行为差异清单（需实测确认）

| 项 | 现象 | 影响 | 建议 |
|---|---|---|---|
| 事件循环 | `uvicorn[standard]` 在 POSIX 上用 uvloop，Windows 用 asyncio Proactor | 同一份代码在两个平台延迟特性不同；README 技术栈表写 "uvicorn(uvloop)" 未标注平台 | README 加平台限定；性能基线**分平台**记录（当前基线来自 Windows 本机，Linux 容器未记录） |
| 文件锁 | 记录中的 WAV 被自身打开时，Windows 上删除/重命名会 `WinError 32`，Linux 允许 | 保留期清理、日志轮转、恢复脚本在 Windows 上可能报错 | `cleanup_expired` / `RotatingFileHandler` 路径加"失败即退避重试一次"，并把错误计数进 `/metrics`；**[未实测]** |
| 工作区换行 | 本地 `deploy/*.sh` 当前是 CRLF（仓库里是 LF） | 直接在 Git Bash 里跑会报 `\r` 错误 | 新增 `scripts/check-line-endings.py`（或 pre-commit）断言 `*.sh/*.py` 在**工作区**也是 LF |
| 依赖解析 | `torch>=2.2`、`funasr>=1.4` 无平台约束 | Windows 与 Linux 装到的实际版本不同 | 提供 `constraints/win-cpu.txt` 与 `constraints/linux-cpu.txt` 两套锁，CI 分别用 |
| SQLite WAL | 网络盘/挂载卷上 WAL 不可靠 | 数据卷放在 SMB/NFS 上会静默退化 | 启动自检：`journal_mode` 实际值不等于 `wal` 时打 WARN（`db.py:64` 只设不断言） |

---

## 6. 实施批次

每批独立可合并、可回滚，**每批结束前必须全绿**：`ruff` + 后端 `pytest` + 前端 `lint/test/typecheck/build`
+ 覆盖率棘轮（`backend/scripts/coverage_ratchet.py`、`frontend/scripts/coverage-ratchet.mjs`）。

### 批次 1：把热路径的成本降下来（1–2 天）

1. C-1 落库 O(N²) → O(N)
2. C-2 `audio_chunk_seq` 合并写
3. P-1 / P-2 / P-3 前端 partial 快路径
4. X-1 CI 加 Windows 矩阵（先只跑后端非 ASR 用例 + 前端四项检查，容忍既有失败并逐个标 `skipif`）

回滚点：C-1/C-2 只动 `meeting_sessions.py` 与 `stream.py` 的写库节奏，前端只动 `session.ts`/`TranscriptStream.tsx`/`Meeting.tsx`。

### 批次 2：稳定性收口（2–3 天）

5. C-3 单写者 `SessionWriter`（引入后可顺手把 C-1 的锁去掉）
6. C-4 启动迁移一次性化
7. S-1 / S-2 / S-3 / S-4 / S-5
8. P-6 队列上界 + `deque` + 音频头回填降频

### 批次 3：可扩展性（3–5 天，可与批次 2 并行）

9. R-1 拆 `StreamSession`（先做"提取组件、保持公开 API"，再逐个把测试改成直接测组件）
10. R-2 配置注册表拆分 + `.env.example` 生成
11. R-3 任务注册表 + 事件驱动唤醒
12. §4.4 其他项（路由拆分、死依赖、lint 收紧）

### 批次 4：双平台（1–2 天）

13. §5.3 运维能力统一下沉到 Python CLI（备份/恢复/排空）
14. §5.4 清单逐项实测 + 启动自检 + 文档分平台命令
15. 两套 constraints + 平台基线记录

---

## 7. 验收与度量

### 7.1 现有可直接复用的度量

| 度量 | 命令 | 说明 |
|---|---|---|
| 后端回归 | `cd backend && python -m pytest -q` | 当前收集 765 项（README 记录），耗时约 9 分钟 |
| 后端静态检查 | `python -m ruff check src/imeeting tests` | |
| 前端四项 | `cd frontend && npm run lint && npm test && npm run typecheck && npm run build` | |
| 端到端 | `npm run e2e` | 登录/强制改密/进入后台 |
| 响应式门禁 | `npm run verify:responsive` | CI 已跑 |
| 压力与 RTF | `cd backend && python scripts/stress.py --only throughput --sessions 4` | **必须用真实语音**（脚本会自动定位 `runtime/funasr_api/asr_example.wav`） |
| 内存泄漏 | `python scripts/stress.py --only longrun` | |

### 7.2 需要新增的度量（否则"优化"无法证伪）

1. **DB 写次数与扫描行数**：给 `db.py` 的 engine 挂 `before_cursor_execute` 计数器，按 meeting_id 归集，
   暴露为 `/metrics` 的 `meeting_db_statements_total{sql_op="select|insert|update"}`。
   → 直接量化 C-1/C-2/C-4。
2. **前端 partial 处理耗时**：在 `upsertSegment` 前后打 `performance.mark/measure`，
   开发模式下把每分钟的 p50/p95 打到 console；e2e 里断言。
   → 直接量化 P-1/P-2/P-3。
3. **启动耗时分解**：`lifespan` 里对 `initialize_app_database` / `_migrate_schema` / warmup 分别计时并写日志。
   → 量化 C-4。
4. **端到端定稿延迟**：`streaming.py` 已有 `MeetingMetrics.note_final(final_latency_ms=...)`，
   把它接到 `/metrics` 直方图（现在只落 per-session 文件）。
   → 量化 C-3/P-6 对用户可感知延迟的影响。

### 7.3 每项的验收口径（摘要）

| 编号 | 通过标准 |
|---|---|
| C-1 | 1200 段落库的 `SELECT` 次数 < 2×段数；重复投递不产生重复行；`segment_count` 恒等于 `COUNT(*)` |
| C-2 | 60 秒会话对 `meeting` 表的写事务 ≤ 5；断线重连补传无重复片段 |
| C-3 | 4 路并发 + 同时 100 rps HTTP 下，HTTP P95 延迟劣化 < 20%；WS 收尾不产生 > 100ms 的事件循环停顿 |
| C-4 | 5 万段库的第二次启动零 DML；启动耗时不随段数增长 |
| P-1 | 单条 partial 处理耗时与 `segments.length` 无关（线性回归斜率为 0） |
| P-2 | 长会议中不再出现每 0.6 秒一次的 layout 记录（Performance 面板 Layout 事件数下降 ≥ 90%） |
| P-4 | 反复改 `asr.infer_threads` 5 次后，进程线程数回到基线（`psutil` 断言，`dev` extra 已含 psutil） |
| R-1 | `engine/streaming.py` < 250 行；`StreamSession` 公开 API 零变更；拆出的组件各自有直接单元测试 |
| R-2 | 新增配置项只改 1 个文件；`.env.example` 由代码生成且 CI 校验无差异 |
| R-3 | 新增任务类型不改 `jobs.py`；空闲时任务 worker 的 DB 查询为 0 |
| X-1 | PR 检查里出现 Windows 的 backend + frontend 两个绿勾 |

---

## 8. 风险与依赖

| 风险 | 影响 | 缓解 |
|---|---|---|
| C-1 用 `ON CONFLICT` 掩盖真实重复写入 | 数据正确性 | 保留"重复投递计数"指标并告警；唯一索引不动 |
| C-2 合并写导致断线补传范围回退 | 少量重复上传 | 服务端 `seq <= audio_chunk_seq` 已幂等丢弃（`stream.py:316`）；补传上限由前端 `preReady` 界控制 |
| C-3 单写者成为新瓶颈 | 吞吐下降 | 单写者是 SQLite 的**既有限制**，只是把它从"隐式锁 + 事件循环阻塞"改成"显式队列"；队列深度进指标，超限丢弃非关键写 |
| R-1 拆分引入回归 | 转写链路 | 保持公开 API 不变；批次 3 前后各跑一次 `stress.py` 全 7 项对照 |
| X-1 Windows CI 首次引入会有既有失败 | 阻塞合并 | 先用 `continue-on-error` + 失败清单，逐个修；不整体跳过 |
| 依赖既有历史文档结论 | 重复劳动 | §0.3 已列出不采纳项，实施前先查 `docs/README.md` 索引 |

**未验证项（本轮未做）**：未运行任何测试套件、未跑压力脚本、未在 Linux 上实测任何行为。
本文所有"性能"数字均引用自 README/OPTIMIZATION 记录的历史实测（标注了来源），
本次只做了代码静态核查。§5.4 标注 **[未实测]** 的条目需按批次 4 逐项复现。

---

## 9. 关联文档

- [项目 README](../../README.md) —— 使用、部署、性能基线
- [生产可用性清单](../production-readiness.md) —— P0/P1 状态与 P2 扩容边界（IM-REV-003）
- [工程结构](../architecture/project-structure.md) —— 目录边界（IM-DES-002）
- [Go + C++ 渐进式重写提案](architecture/native-rewrite-plan.md) —— 与本方案并行的长期路线（IM-DES-003）
- [早期优化评估](../../OPTIMIZATION.md) —— 历史记录，含已修复项（IM-REV-001）
- [2026-11-03 收尾阶段深度审查](../reviews/2026-11-03-final-deep-review.md) —— 本轮之前的最后一份全量审查（IM-REV-005）
