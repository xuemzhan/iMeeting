# IM-PLN-004 实施的自查与修复记录

<!-- doc-governance: IM-RPT-012 -->
> 文档类型：报告；状态：阶段快照；受审对象：[IM-RPT-011](2026-11-04-refactor-implementation.md) 所述的实现；审查日期：2026-11-04。
> 维护责任：待指派（iMeeting 文档维护者）。[文档中心](../README.md) · [维护规则](../governance/README.md)。

**结论**：对照 [IM-PLN-004](../plans/2026-11-04-refactor-optimization-plan-v2.md) 逐条复核实现，
**发现 13 个问题**，全部已修复并补验证：

- 6 个真实缺陷（D-1、D-2、D-7、D-11、D-13，外加 D-11 的后果"CI 从未运行"）；
- 1 个既有测试缺陷（D-6）；
- 6 个"计划写了、代码只做了一半或没做"的偏离（D-3、D-4、D-5、D-8、D-9、D-10、D-12）。

其中 D-11 / D-12 / D-13 只有在 GitHub 上真实跑 CI（Linux + Windows）才暴露；
修复后该次运行 `41741208223` **7/7 作业全绿**。逐项证据见 §2，验证结果见 §4。


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

> 下面三条是**首次在 GitHub 上真实跑 CI（Linux + Windows）才暴露**的——本地只做静态校验永远看不出来。
>
> | 编号 | 问题 | 性质 | 处置 |
> |---|---|---|---|
> | D-11 | `ci.yml` 里一个 step 写了两个 `run:`，GitHub 判定整份工作流非法，**最近两个提交的 CI 全是 0 秒失败** | **真实缺陷**（门禁形同不存在） | §2.11 |
> | D-12 | `py3.10` 矩阵腿在 `pip install` 阶段就死：安全底线 `pillow>=13` 的 `requires-python` 是 `>=3.11` | 与声明不符（`requires-python = ">=3.10"`） | §2.12 |
> | D-13 | 前端 Windows 腿在响应式门禁处 `ParserError`：脚本是 bash 写法，Windows 默认 PowerShell | **真实缺陷**（X-1 要抓的正是这类） | §2.13 |

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


### 2.11 D-11：工作流本身非法，CI 从来没跑起来（真实缺陷）

**现象**：`gh workflow run ci.yml --ref perf` 直接 422：

```
HTTP 422: Invalid Argument - failed to parse workflow: (Line: 97, Col: 9):
'run' is already defined
```

查 run 列表，`39ee83d` 与 `805c6fb` 两次推送的 CI 都是 **0 秒失败**——也就是说
IM-RPT-011 里"矩阵与 YAML 只做了静态校验"这句话的实际后果比写得更严重：
不是"没跑过 Windows"，而是**整条 CI 一条都没跑过**。

**根因**：frontend 作业的 Typecheck step 里写了两行 `run:`。YAML 解析器把重复键
当错误（GitHub 用严格解析），整份工作流作废。

**修复**：删掉重复行并留注释；同时补一个本地严格校验脚本（自写 PyYAML loader，
把重复键变成异常），避免同类错误再次只靠肉眼发现。校验结果：`ci.yml` / `compose.yaml`
`/prometheus-alerts.yml` 均通过。

### 2.12 D-12：声明支持 3.10，但装不上（与声明不符）

**现象**：`backend (ubuntu-latest, py3.10)` 在 `pip install -c constraints-production.txt -e ".[dev]"`
阶段 `ResolutionImpossible`，报 `reportlab ... depends on pillow>=9.0.0` 且
"no matching distributions available for your environment"。

**根因**：安全底线 `constraints-production.txt` 要求 `pillow>=13.0.0`，而 PyPI 上
Pillow 13.0.0 的 `requires-python` 是 `>=3.11` 且**没有 cp310 wheel**。
`pyproject.toml` 却还写着 `requires-python = ">=3.10"`——声明与实际可安装性矛盾。

**修复**：`requires-python` 抬到 `">=3.11"`；CI 的最低版本腿由 3.10 改为 3.11；
README 两处（安装要求表、快速开始）同步。
**验证**：`backend (ubuntu-latest, py3.11)` 腿由"装不上"变为 820 passed 全绿。

### 2.13 D-13：前端 Windows 腿的响应式门禁死在 shell 方言（真实缺陷）

**现象**：`frontend (windows-latest)` 直到响应式门禁前都是绿的（23 文件 / 184 用例通过），
然后：

```
ParserError: D:\a\_temp\....ps1:3
```

**根因**：该 step 的脚本是 bash 写法（`npx vite preview ... &`、`for i in $(seq 1 60)`、
`curl`），但 Windows runner 默认 shell 是 PowerShell。

**修复**：该 step 显式 `shell: bash`（Windows runner 自带 Git Bash），两平台共用一份脚本。
**验证**：`frontend (windows-latest)` 由失败变为 success。

### 2.14 附带发现：依赖审计在非基准平台上是噪音

py3.11 腿在 820 项全绿之后仍失败：`pip-audit --local` 报了 runner 镜像**预装**的
`setuptools 79.0.1`（PYSEC-2026-3447）。它不属于本仓库依赖，且 py3.12 镜像没有——
同一条规则在两个 runner 上给出相反结论，说明条件写错了。改为与覆盖率棘轮一致，
只在基准平台（ubuntu + py3.12）执行。



### 2.15 X-2 收尾：把"Linux 上中文可读"从人眼确认改成断言

计划 §11.3 对 X-2 的通过标准里有一句"Linux 容器导出中文 PDF 可读"，而原先所有用例只断言
结构（`CIDFontType0` / `STSong` 存在）——**旧实现的静默回退恰好满足这些结构断言**：
PDF 合法、能打开、中文全缺字。所以"可读"一直挂在"人眼看一次"上。

**做法**：用 `pypdf` 把导出的 PDF 重新解析，把抽出的文本与源字符串比对。新增两条用例：

| 用例 | 覆盖的路径 |
|---|---|
| `test_cid_font_pdf_text_is_actually_readable` | 只有内置 CID 字体（Linux 容器缺 CJK 字体时的真实路径） |
| `test_pdf_text_is_readable_whatever_font_this_host_offers` | 不 mock 探测：Windows 走内嵌 TTF、Linux 走 CID，两条都要过 |

**判别性已验证**：把字体强制降级到 Helvetica 后，两条用例里的中文断言全部为 `False`
（抽出来的是乱码/缺字），也就是说这两条能真正区分"看起来正常的废文件"与"能读的文件"。

**代价**：`pypdf>=5` 进入 `dev` 额外依赖（仅测试用，`constraints-production.txt` 不动）。

### 2.16 C-3 收尾：补上灰度开关的双写比对

IM-RPT-011 §3.3 把"双写比对"列为未做，于是 `session.writer_enabled` 这个开关的
"打开等同不打开"只能靠人工读代码确认——去重、`segment_count`、状态收口中任何一处
在单写者路径上变了，现有用例都不会红。

**做法**：对两条路径各建一场会议，按真实收尾顺序跑同一批写入
（`start_session_row` → 增量 `persist_segments` → `record_audio_seq` →
含**重复 segment_id** 的收口写入 → `record_audio_path` → `save_adaptive_profile` →
`finish_session_row`），然后把 `meeting` / `meeting_session` / `segment` 三张表的
结果压成快照（去掉 id 与时间戳）逐字段比较，断言完全相等。

**证据**：`test_writer_path_matches_direct_path`；GitHub Actions `41769830227`
在 ubuntu 3.12/3.11 与 windows 3.12 上均 **823 passed**（Windows 10 分 58 秒）。

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
| 本地后端全量回归（Windows） | **820 passed / 0 failed**（8 分 39 秒，早期一轮；最终为 823） |
| 本地后端静态检查（`ruff`，含 `B` + `ASYNC`） | All checks passed |
| 本地后端覆盖率棘轮 | **81.20% → 81.73%（+0.53）通过** |
| 本地前端 lint / typecheck / build | 均通过（退出码 0） |
| 本地前端单测 | 23 个文件 / 184 条用例通过 |
| 本地前端覆盖率棘轮 | lines 23.51% → 23.80%、statements 22.57% → 22.82%，通过 |
| 本轮新增用例 | 后端 +22（D-1 1、D-2 1、D-4 5、D-5 2、D-7 2、D-8 2、D-9 1、D-10 5、X-2 可读性 2、C-3 双写比对 1），前端 0 新增 |
| **GitHub Actions `41741208223`（`87a8281`）** | **7 / 7 作业全绿**：backend（ubuntu 3.12 / 3.11 / windows 3.12）、frontend（ubuntu / windows）、e2e、deploy |
| **GitHub Actions `41769830227`（`937844a`，最终版本）** | **7 / 7 作业全绿**；Linux 基准平台 ruff 通过、**823 passed**（7 分 33 秒）、覆盖率 81.20% → **81.69%**；Windows **823 passed**（10 分 58 秒）；py3.11 腿同样 green |
| Linux 前端（ubuntu） | lint / 184 用例 / typecheck / build / worklet / 覆盖率棘轮 / 响应式门禁 全通过 |
| deploy 作业（ubuntu） | compose config（含 tls profile）、promtool、caddy validate、前端镜像构建 + trivy、SBOM、三个部署脚本 `sh -n` 全通过 |

---

## 5. 仍未验证的边界（与 IM-RPT-011 §6 相同，未因本轮修复而消除）

1. **X-2 的"中文可读"已从"人眼看"改为自动断言**（§2.15）：Linux CI 上把导出的 PDF 重新解析、
   比对中文文本。**仍然成立的风险**：内置 CID 字体不内嵌，严格阅读器 / PDF-A 场景仍可能显示异常
   ——生产镜像应装 `fonts-noto-cjk` 走第 2 级（原风险未变）。
2. Windows CI 已真实跑通（见 §4），但只在 `windows-latest` + Python 3.12 上；
   更老的 Windows / 其它 Python 版本未覆盖。
3. P-2 的收益仍未在浏览器 trace 里量化；D-3 同样只有代码层结论。
4. 未跑 `scripts/stress.py`（需真实语音与模型）：S-7 的池耗尽、C-3 的事件循环停顿
   4 路并发 + 100rps 仍未复现验证。
5. C-3 的双写比对**已补**（§2.16），但只覆盖"顺序执行同一批写入"的等价性；
   真并发（4 路会话同时压队列）下的顺序性与吞吐仍未实测。
6. D-10 的 `drain` 只在 `MockTransport` 上验证过协议交互，没有对着真实服务跑过；
   `deploy/graceful_drain.sh` 只做 `sh -n` 语法检查（本轮已把它加进 `deploy` 作业），未真实执行。
7. R-1 / R-2 / R-3 仍未开始。
8. 本轮未在 GitHub Codespaces 中验证：`gh` token 缺 `codespace` 作用域，
   创建 codespace 需要账号级授权（`gh auth refresh -h github.com -s codespace`），未执行。
