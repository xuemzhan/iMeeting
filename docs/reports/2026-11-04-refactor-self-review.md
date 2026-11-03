# IM-PLN-004 实施的自查与修复记录

<!-- doc-governance: IM-RPT-012 -->
> 文档类型：报告；状态：阶段快照；受审对象：[IM-RPT-011](2026-11-04-refactor-implementation.md) 所述的实现；审查日期：2026-11-04。
> 维护责任：待指派（iMeeting 文档维护者）。[文档中心](../README.md) · [维护规则](../governance/README.md)。

**结论**：对照 [IM-PLN-004](../plans/2026-11-04-refactor-optimization-plan-v2.md) 逐条复核实现，
**发现 10 个问题**，全部已修复并补测试：

- 3 个真实缺陷（D-1、D-2、D-7）——其中两个是本轮改造自己引入的；
- 1 个既有测试缺陷（D-6）；
- 6 个"计划写了、代码只做了一半或没做"的偏离（D-3、D-4、D-5、D-8、D-9、D-10）。

修复后全量回归见 §4。

---

## 1. 发现的问题与处置

| 编号 | 问题 | 性质 | 处置 |
|---|---|---|---|
| D-1 | 硬断连后 `meeting_session` 行停在 `recording`，活跃会话注册表泄漏 | **真实缺陷**（C-3 改造多引入取消点 + 既有取消传播问题叠加） | §2.1 |
| D-2 | `SessionWriter.submit(critical=True)` 在队列满时会在**事件循环里**阻塞最长 10s | **真实缺陷**（本轮新增代码自带，恰好违背 C-3 的目的） | §2.2 |
| D-3 | `TranscriptStream` 仍然每条 partial 读一次 `scrollHeight`（布局读） | 与计划 §5.3 不符（只做了一半） | §2.3 |
| D-4 | 计划 §8 要求的"不变量守门测试"没有落地 | 与计划不符 | §2.4 |
| D-5 | 连接池指标名与计划 §4.1 的约定不一致；单写者队列深度未进 `/metrics` | 与计划不符 | §2.5 |
| D-6 | 新增的 `test_abrupt_disconnect_closes_session_row` 断言进程级全局注册表为空 | **测试自身缺陷**（跨用例污染）：全量跑必红、单跑必绿 | §2.6 |
| D-7 | `shutdown_writer()` 写了却**从来没人调用**——lifespan 没有排空单写者 | **真实缺陷**（SIGTERM 时队列里的写直接丢） | §2.7 |
| D-8 | S-2/P-4 只做了 `shutdown`，没做计划要求的"线程数回基线"验收，线程总数也没进 `/metrics` | 与计划不符 | §2.8 |
| D-9 | S-5 超限帧只打日志，没有计划要求的"丢弃并计数" | 与计划不符 | §2.9 |
| D-10 | §6.1 的优雅重启没有下沉（只有 `.ps1`，缺 `drain` 子命令与 `.sh` 薄包装），README 也没有分平台写法 | 与计划不符 | §2.10 |

---

## 2. 逐项分析、修复与验证

### 2.1 D-1：硬断连后 session 行不收口（真实缺陷）

**现象与证据**：新增测试 `test_abrupt_disconnect_closes_session_row` 复现：

```
AssertionError: 硬断连后 session 行仍停在 recording：['recording']
```

**根因**：`stream_endpoint` 的 `finally` 里顺序是「归还名额（同步）→ 取消子任务（await）→
`_persist_segments`（同步）→ `close()`（await）→ 收尾写入（本轮改成了 await `_db`）」。
连接被硬断时 endpoint 任务被取消，`finally` 里**第一个 await 就抛 `CancelledError`**，
其后的语句统统跳过。会议状态之所以还正确，是因为它由同步写完成；而 session 行与注册表
在那之后，于是永远停在 `recording`，`rename_active_speaker` 还会命中已结束的会话。

**修复**：
1. 把 session 行收口**提前**到 `_persist_segments` 之后，全部同步、无取消点；
2. 收尾写入恢复**同步直调**（只有 `sink.close` 保留 `to_thread`），放进嵌套 `finally`，
   即使关音频文件被打断，状态写入仍会执行；
3. 末尾再用真实时长 + 音频路径写一次（`finish_session_row` 取 `max`，重复调用安全）。

代价是每个会话多一次 `UPDATE + COUNT`。收尾只发生一次，换来"行一定不在 recording"。

**验证**：`test_abrupt_disconnect_closes_session_row` 通过；`test_abnormal_disconnect_marks_abandoned`、
`test_abandoned_meeting_still_persists_received_segments`、`TestWriterPath` 全通过。

### 2.2 D-2：`submit(critical=True)` 会阻塞事件循环（真实缺陷）

**现象**：`submit_sync` 在队列满时 `queue.put(item, timeout=10.0)`；异步入口 `submit()`
直接在协程里调用它 → **队列满时事件循环被卡最多 10 秒**。这正是 C-3 要消灭的模式。

**修复**：`submit()` 改为 `future = await asyncio.to_thread(self.submit_sync, ...)`，
阻塞只发生在工作线程。

**验证**：`test_async_submit_does_not_block_event_loop_when_queue_is_full`——打满队列后
提交 critical 任务，断言 `await asyncio.sleep(0.1)` 的实际耗时 < 0.5s。

### 2.3 D-3：`TranscriptStream` 仍在每条 partial 读布局

**现象**：上一轮只把"写 `scrollTop`"挪进 `requestAnimationFrame`，但 `isScrollable(el.scrollHeight, ...)`
仍在同一个 effect 里、依赖含 `lastText` → **每条 partial 仍触发一次布局读**。

**修复**：拆成两个 effect——布局读只依赖 `[segments.length]`；写 `scrollTop` 的那个依赖
`[segments.length, lastText, lastEndMs, pinned]` 并在 rAF 内执行。

**验证**：`npm run typecheck` / `npm run lint` / 组件与 store 测试通过。
**仍未量化浏览器内的回流次数**（无 trace），见 §5。

### 2.4 D-4：计划 §8 的不变量测试没有落地

计划 §8 明确要求"重构下列区域时断言必须始终成立"。新增 `backend/tests/test_invariants.py`：

| 用例 | 锚定的不变量 |
|---|---|
| `test_config_endpoint_never_returns_plaintext_secrets` | 密钥类配置只以 `********` 出库 |
| `test_undecryptable_secret_degrades_to_empty_not_ciphertext` | 密文解不开 → 空串，绝不把密文当密钥 |
| `test_audit_chain_stays_linked_under_repeated_writes` | 连续写入后审计哈希链仍完整 |
| `test_share_token_is_stored_only_as_digest` | 分享令牌只存摘要 |
| `test_every_session_end_leaves_no_recording_meeting` | 任何结束路径都不留 `recording` 会议 |

计划里第 5 条"权限矩阵"已由 `test_meetings_api.py` 的跨用户隔离用例覆盖，此处不重复，
避免同一断言两处维护。

### 2.5 D-5：指标命名不一致、单写者队列深度未上报

- 池指标改为计划约定的名字：`meeting_db_pool_in_use` / `meeting_db_pool_size` /
  `meeting_db_pool_overflow_limit` / `meeting_db_pool_timeout_total`；
- `/metrics` 增加 `meeting_session_writer_queue_depth`（gauge）与
  `meeting_session_writer_{executed,dropped,errors}_total`（counter），经 `writer_stats()`
  读取，**未启用时不创建写者**（避免观测本身产生副作用）。

**补测试**（上一轮只改名字、没有任何断言盯着它们）：
`test_metrics_render_db_pool_gauges`、`test_metrics_render_writer_stats_when_writer_exists`、
以及 `test_writer_stats_is_not_created_by_observation`（抓指标不能反过来创建写者）。

**有意偏离**：未使用 `meeting_db_statements_total{sql_op="select"}` 这种带标签形式。
本项目导出器只支持"无标签 counter / 单标签 gauge"，拆成 `meeting_db_statements_select_total`
等独立名字不影响 `rate()`。

### 2.6 D-6：新增用例自身的全局状态污染（测试缺陷）

**现象**：`test_abrupt_disconnect_closes_session_row` 断言 `_SESSIONS_BY_MEETING` 为空。
该表是**进程级**全局量，而 `meeting_id` 每个用例都从 1 开始——前面用例留下的残留
会让这条用例**全量跑必红、单独跑必绿**。

**修复**：用例开头显式 `streaming_module._SESSIONS_BY_MEETING.clear()`。
不放宽断言：这条用例的价值正是"本次会话确实从注册表里摘掉了"。

### 2.7 D-7：`shutdown_writer()` 从未被调用（真实缺陷）

**现象**：`session_writer.py` 提供了 `shutdown_writer()`，注释写着"进程退出/测试收尾时排空并停止"，
但**全仓没有任何调用点**（`main.py` 的 lifespan 关闭段只停 job worker 与清理任务）。

**根因**：C-3 的验收写的是"`close()` 排空后停止"，实现者测了 `close()` 本身，
却忘了把"什么时候调 close"接上——单元测试因此全绿。

**后果**：`session.writer_enabled=true` 时，SIGTERM 后队列里"已经算完、只差落库"的片段与
状态写入全部丢失；这在用户眼里就是"最后几句话没了"。

**修复**：lifespan 关闭段加 `await asyncio.to_thread(shutdown_writer)`——`close()` 会 join
工作线程（最长 10s），必须放到线程里，不能卡在关闭路径的事件循环上。

**验证**：`test_lifespan_drains_the_writer_on_shutdown` 用 spy 断言 lifespan 确实调了它
（这条用例在旧代码上必红：没有调用点）。

### 2.8 D-8：S-2/P-4 只做了一半

计划 §5.4 的处置是「`old.shutdown(wait=False)` + 线程总数进 `/metrics` + 配置变更 INFO 日志」，
验收是「连续改 5 次 `asr.infer_threads`，进程线程数回到基线」。

上一轮只落地了 `shutdown` 与日志，**验收用例没写、指标也没加**（IM-RPT-011 §2 表格里
自己写了"未做线程数复测"，但"未做"和"没写指标"都没进验收清单）。

**修复**：
1. 新增 `test_rebuilding_returns_threads_to_baseline`：连改 5 次线程数，每次都让新池真的起线程，
   最后只关掉最后一个池，断言线程数回到基线。用线程名（`asr` 前缀）计数而非 psutil——断言
   的是同一件事，却不引入额外依赖。**判别性已验证**：模拟"不 shutdown 旧池"时该场景残留 5 条线程。
2. `/metrics` 新增 `meeting_process_threads` gauge（+ `test_process_thread_gauge_is_exposed`）。

### 2.9 D-9：S-5 只丢不计数

计划 §5.5 的处置是"超限丢弃**并计数**"。原实现只有 `logger.warning`——日志会被刷屏、
被采样、被打包丢掉，等于没有观测。

**修复**：`ops.count_dropped_frame()` / `ops.dropped_frames_total()`，`_decode_pcm` 超限时计数，
`/metrics` 暴露 `meeting_ws_frame_dropped_total`。
**验证**：`test_oversized_audio_frame_is_counted`（丢弃 → 计数 +1 → 指标可见）。

### 2.10 D-10：§6.1 的优雅重启没有下沉

计划 §6.1 要求"备份 / 恢复 / 优雅重启"三者都下沉到 `python -m imeeting.sqlite_backup`，
`.sh` 与 `.ps1` 只做薄包装；并给 README 补分平台写法。实际只补了两个 `.ps1`，
**`drain` 子命令不存在**、Linux 侧没有包装、README 仍只有 cmd 的 `set`。

**修复**：
1. `sqlite_backup.py` 新增 `drain` 子命令与 `drain_server()`：登录 → 触发
   `/admin/maintenance/drain` → 轮询 `active_sessions` → 超时返回剩余数；
   剩余数非 0 时 CLI **退出码 3**（发布流水线的门禁不能是"看起来成功了"）。
2. `deploy/graceful_drain.sh` 薄包装（与 `backup_sqlite.sh` 同风格）；`scripts/graceful_restart.ps1`
   保持可用，不再承担唯一的实现。
3. README 补"cmd / PowerShell / bash"三种环境变量写法对照表。

**验证**：`tests/test_drain_cli.py` 5 条（轮询到 0 / 超时报剩余数 / 缺 token / CLI 退出码 0 与 3），
用 `httpx.MockTransport`，不需要真起服务。

---

## 3. 复核后仍然成立的实现（未改动）

| 项 | 复核结论 |
|---|---|
| X-2 字体降级链 | 4 级顺序正确；配置失效**先告警**再降级；缓存键含 override（改配置即时生效）；6 条测试含"旧实现会失败"的判别性断言 |
| C-4 一次性回填 | 标记表与"只跑一次"边界正确：加列 DDL / 分享令牌摘要迁移仍每次执行（否则 `test_legacy_share_token...` 会红）；`_backfill_session_offsets` 在外层事务外执行（避免 `database is locked`） |
| S-7 连接池 | `_CountingQueuePool.connect(self)` 与 SQLAlchemy 2.0.51 的 `QueuePool.connect(self)` 签名一致（已核对源码） |
| C-3 单写者 | 顺序性 / 有界 / 丢弃计数 / 异常不吞 / 关闭排空均有用例；开关默认关闭，关闭时走原路径 |
| C-1 按批次查重 | 语义与旧实现等价（新增/去重/计数一致），400 段实测 658ms、`segment_count` 与实际行数一致 |
| C-2 合并写 | 收尾补写最终值；服务端 `seq <= audio_chunk_seq` 幂等丢弃 |
| P-1 快路径 | `startMs` 未变时只替换下标；索引与数组双向一致的断言已加 |
| S-1 原子占位 | 提前 return 的路径归还名额，`elif session_id is not None` 覆盖"握手就失败"的情形 |
| S-3 局部失效 | `invalidate(meeting_id)` 只失效本会议，全量 `invalidate_cache()` 仍供改名/删除使用 |
| §5.7 三项 | `react-window` 已移除；worklet 单一来源 + `worklet:check` 字节一致性；ruff 开 `B` + `ASYNC` |

---

## 4. 修复后的验证结果

| 检查 | 结果 |
|---|---|
| 后端全量回归 | **820 passed / 0 failed**（8 分 39 秒） |
| 后端静态检查（`ruff`，含 `B` + `ASYNC`） | All checks passed |
| 后端覆盖率棘轮 | **81.20% → 81.73%（+0.53），通过** |
| 前端 lint / typecheck / build | 均通过（`npm run lint` / `typecheck` / `build` 退出码 0） |
| 前端单测 | 23 个文件 / 184 条用例通过 |
| 前端覆盖率棘轮 | lines 23.51% → 23.80%、statements 22.57% → 22.82%，通过 |
| 本轮新增用例 | 后端 +19（D-1 1、D-2 1、D-4 5、D-5 2、D-7 2、D-8 2、D-9 1、D-10 5），前端 0 新增 |

---

## 5. 仍未验证的边界（与 IM-RPT-011 §6 相同，未因本轮修复而消除）

1. 未在 Linux 上运行；X-2 的容器内中文 PDF 仍未目视确认。
2. Windows CI 未真实跑过（只有静态校验）。
3. P-2 的收益仍未在浏览器 trace 里量化；D-3 同样只有代码层结论。
4. 未跑 `scripts/stress.py`（需真实语音与模型）：S-7 的池耗尽、C-3 的事件循环停顿
   4 路并发 + 100rps 仍未复现验证。
5. C-3 的双写比对仍未实现（IM-RPT-011 §3.3）。
6. D-10 的 `drain` 只在 `MockTransport` 上验证过协议交互，没有对着真实服务跑过；
   `deploy/graceful_drain.sh` 未在 Linux 上执行过。
7. R-1 / R-2 / R-3 仍未开始。
