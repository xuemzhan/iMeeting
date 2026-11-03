# iMeeting 重构与优化方案 v2（按 IM-REV-006 修订）

<!-- doc-governance: IM-PLN-004 -->
> 文档类型：计划；状态：**已实施**（结果见 [IM-RPT-011](../reports/2026-11-04-refactor-implementation.md)：14 项完整实施、3 项部分、3 项未做、1 项被否决回退）；受审基线：IM-PLN-003 @ `981e189`；依据：[IM-REV-006](../reviews/2026-11-04-refactor-plan-review.md)；修订日期：2026-11-04。
> 维护责任：待指派（iMeeting 文档维护者）。[文档中心](../README.md) · [维护规则](../governance/README.md) · [v1 原文](2026-11-04-refactor-optimization-plan.md)。

**v2 的核心变化：把"按推理排优先级"改成"按实测排优先级"，并补一条 v1 漏掉的用户可见缺陷。**

---

## 0. 修订说明

### 0.1 为什么另起一版而不原地改

IM-PLN-003 已被 IM-REV-006 逐条引用（含对具体措辞的批评，如"144 万行扫描""明显掉帧"）。
原地改写会让评审报告失去可核对的锚点。因此 **v1 原文保留、标注"已被替代"**，本文件为唯一现行版；
两者差异见 §0.2 的逐条处置表。

### 0.2 逐条处置（IM-REV-006 → v2）

| 评审项 | v2 处置 | 位置 |
|---|---|---|
| **B-1** 优先级排序不成立 | C-1/C-2/P-1 从 P0–P1 降为 **P3（卫生类）**；删除"144 万行扫描""明显掉帧"等未测量措辞；补"千段级结论不适用于万段级"的边界条件；实测表作为基线入档 | §2、§5.1、§11.1 |
| **B-2** 漏掉 PDF 中文字体 | 新增 **X-2**，列为 **P0**；处置方案与降级链已在本地实测（§3.2） | §3 |
| **B-3** "stream.py 不需要改"不成立 | 改为"先把 3 处私有成员提升为公开方法 + 特征化测试，再拆分"；补"`stream.py` 改动 < 20 行"的可判定口径 | §7.1 |
| **B-4** 计数与交叉引用错误 | 全表 **14 项**（v1 误写 12）；删除指向不存在锚点的"§4–§7 其他小节" | §2 |
| **B-5** 缺安全不变量 | 新增独立一节"不变量与必须保持的测试"，覆盖密钥脱敏/解密降级/审计链/令牌摘要/权限矩阵 | §8 |
| **M-1** P-4 因果错误 | 改为"线程泄漏；worker 阻塞在空队列、**不持有模型引用**" | §5.4 |
| **M-2** 连接池被忽略 | 新增 **S-7**（`QueuePool(5+10)`，实测 `pool_size=5`），定为 P1，并与单写者联动 | §4.4 |
| **M-3** R-1 工作量低估 | R-1 升为独立轨道，估算改为 8–12 人日 / 4–6 个 PR，并注明估算依据与未验证性 | §7.2 |
| **M-4** X-1 口径可被绕过 | 通过标准改为"Windows **实际执行**用例数 ≥ Linux 的 95%，跳过清单有到期日且只减不增" | §6.2、§11.3 |
| **M-5** C-2 依赖无界结构 | 明确顺序：先给 `preReady` 加上界，再放宽服务端持久化节奏 | §5.2 |
| **M-6** 缺"批次 0" | 新增 **批次 0：先测量**，作为一切改动的准入条件 | §10 |
| **M-7** 平台断言无置信度 | 平台差异表加"置信度/核实方法"列；Windows 事件循环具体策略标 [待核实] | §6.3 |
| **M-8** 无回滚机制 | 新增"灰度、回滚与数据修复"一节，给出配置开关、双写比对与修复脚本 | §9 |
| **m-1** P-5 表述过强 | 改为"`invalidate_cache()` 清空**整个**缓存"；"每段回表"改为待实测项 | §5.5 |
| **m-2** P-3 混淆两种成本 | 拆分为"数组扫描成本（微秒级，可忽略）"与"整页重渲染成本（待测）" | §5.3 |
| **m-3** 文件锁重试有副作用 | 退避重试必须**同时计数进 `/metrics`**，否则问题被掩盖 | §6.3 |
| **m-4** 缺 baseline→target | §11 给出每项"基线（已测/待测）→ 目标"两列 | §11 |
| **m-5** 三项判断已复核正确 | 死依赖 / worklet 双份 / 弱 lint 保留 | §5.6 |
| **m-6** 术语混用 | 统一为"以固定事务开销为主，二次项在大 N 下才显现" | §5.1 |

### 0.3 v2 的排序原则

按三条可判定的标准排序，而不是按"看起来多严重"：

1. **用户是否可感知**（导出中文失效 > 启动变慢 > 内部写入节奏）；
2. **能否被验证**（有实测或用例可写 > 只有复杂度推理）；
3. **是否随数据/规模增长**（随规模线性变差的优先）。

未通过第 2 条的条目，一律不进 P0/P1。

---

## 1. 目标与范围

### 1.1 需求与可判定目标

> 仔细分析整个项目的代码，站在**高效、稳定、可扩展、Windows/Linux 都适配**的特性进行重构和优化。

| 特性 | v2 的可判定含义（已按测量优先改写） | 不做的事 |
|---|---|---|
| 高效 | 单场千段级的**已测成本已可接受**（§11.1）；真正要降的是**随数据增长的启动成本**（C-4）与**连接池竞争**（S-7）；任何性能改动必须附基线数字 | 不改 ASR 模型、不自研流式神经网络 VAD、不动 RTF 基线 |
| 稳定 | 无界队列/线程/连接池有上界；配置变更不泄漏线程；启动不做全表重写；**导出在 Linux 上产出正确文档** | 不做多实例、不引 Redis/PostgreSQL |
| 可扩展 | 新增任务类型/配置项/导出格式 = 新增文件 + 一行注册，且不破坏 §8 的不变量 | 不重写技术栈（见 [IM-DES-003](../architecture/native-rewrite-plan.md)） |
| 双平台适配 | CI 有 Windows 门禁且**实际执行率 ≥ 95%**；导出与运维能力在两个平台**同一份实现**且输出等价 | 不做 Windows 服务/安装包 |

### 1.2 现状规模

| 项 | 数值 | 来源 |
|---|---|---|
| 后端业务代码 | 20,367 行 / 193 个 py | `git ls-files backend/src` |
| 后端测试 | 12,115 行（README 记录 765 项） | `git ls-files backend/tests` |
| 前端代码 | 19,340 行 / 132 个文件 | `git ls-files frontend/src` |

### 1.3 已测量基线（v2 新增，来自 IM-REV-006 §7）

| 场景 | 实测 | 环境 |
|---|---|---|
| 逐段落库（N=1200） | 约 **2.4 s / 场**（固定开销 ≈1.9 ms/次） | Windows / SQLite WAL / `synchronous=NORMAL` |
| 合并落库（同 N） | 约 0.11 s（N=800 实测外推） | 同上 |
| `audio_chunk_seq` 逐块 commit | **0.54 ms/次**；12000 次 ≈ 6.5 s / 场 | 同上 |
| 前端 `upsertSegment`（N=1200） | **0.109 ms/条 partial**；12000 条 ≈ 1.3 s CPU / 场 | Node v24 |
| 连接池 | `QueuePool`，`pool_size=5`（+ 默认 `max_overflow=10` → 上限 15） | SQLAlchemy 2.0.51 |
| PDF 中文（Linux 镜像） | 无 CJK 字体 → 回退 `Helvetica` → **字形缺失且不报错** | 本地实测（§3.2） |

> 这些数字把两个原本"看起来像危机"的项（C-1/C-2/P-1）判定为卫生类改动（占空比 0.02%–0.09%）。

### 1.4 明确不采纳（沿用 v1，经复核正确）

- **不引入虚拟滚动**：IM-RPT-009 已在真实长列表实测否决；
- **不按 OPTIMIZATION.md 第三节改流式 VAD**：属识别功能变更，不属重构；
- **不动 SQLite 单实例边界**：多实例属 P2 扩容，见 [生产可用性清单](../production-readiness.md)。

---

## 2. 优先级总表（20 项）

编号沿用 v1；`★` 为 v2 新增。级别：**P0** 必须最先做；**P1** 紧随；**P2** 排期内；**P3** 顺手做/按意愿。

| 编号 | 问题 | 位置（已复核） | 原级别 | **新级别** | 依据 |
|---|---|---|---|---|---|
| ★ X-2 | PDF 导出在 Linux 上中文不可读（硬编码 Windows 字体 + Helvetica 兜底，且不报错） | `backend/src/imeeting/export/document_export.py:113` | （漏项） | **P0** | 用户可见、静默失效、测试盲区 |
| ★ M0 | 无度量基线，改动无法证伪 | 全仓 | （放验收） | **P0** | 本评审已证明不测量的后果 |
| C-4 | 启动期执行全表 DDL/DML，`UPDATE meeting` 改写所有会议行 | `db.py:110`、`:352`、`:364`、`:471`、`:595` | P1 | **P1** | 唯一随数据线性变差的性能项 |
| C-3 | async WS 处理器内同步调 DB | `stream.py:244,250,253,254,261,416,460,471,473,474` | P0 | **P1** | 设计一致性（同文件 `:157`/`:327` 已用 `to_thread`） |
| ★ S-7 | 连接池上限 15，而并发线程可达 40+，满载可能 30 s 超时 | `db.py:46`（未配置 pool） | （漏项） | **P1** | 稳定性直接内容 |
| X-1 | CI 无 Windows 门禁 | `.github/workflows/ci.yml` | P1 | **P2** | 制度保障，成本低 |
| S-2 | 线程池重建不 `shutdown` | `engine/pool.py:59`、`:71` | P2 | **P2** | 改动小 |
| P-4 | 同上（并入 S-2 处理，v2 修正因果） | `engine/pool.py:71`–`:78` | P1 | **P2** | 触发场景罕见 |
| S-3 | 声纹缓存被每次快照写入整体清空 | `engine/speaker.py:359`、`:223` | P2 | **P2** | 改动小；量级待测 |
| S-5 | 帧长无上限 | `api/ws/stream.py:543` | P2 | **P2** | 一行防御 |
| S-1 | 并发会话上限 check-then-act | `api/ws/stream.py:235` | P2 | **P2** | 竞态窗口极小，正确性卫生 |
| P-6 | 定稿任务/推理队列无界；`AudioSink` 每块 2 次 seek+flush；`_take_streaming` 用 `list.pop(0)` | `engine/streaming.py:693`、`:430`、`engine/audio.py:177` | P2 | **P2** | 过载时内存与 IO 无界（v1 已列，v2 保留） |
| C-1 | 逐段落库：固定开销主导，二次项在大 N 才显现 | `meeting_sessions.py:98`、`:150` | P0 | **P3** | 实测 2.4 s/场（0.03% 占空比） |
| C-2 | 每块一次 `audio_chunk_seq` commit | `api/ws/stream.py:327` | P0 | **P3** | 实测 0.54 ms/次（0.09% 占空比） |
| P-1 | `upsertSegment` 全量 sort + 重建 Map | `frontend/src/store/session.ts:606` | P0 | **P3** | 实测 0.109 ms/条（帧预算 0.7%） |
| P-2 | 每条 partial 强制回流 | `TranscriptStream.tsx:38`、`:46` | P1 | **P3** | **量级未测**，需先测（§11.2） |
| P-3 | 页面级重渲染 + 3 次数组扫描 | `pages/Meeting.tsx:44,90,185,332` | P1 | **P3** | 扫描微秒级；重渲染待测 |
| R-1 | `StreamSession` 1283 行 / 约 90 字段 | `engine/streaming.py:58` | P3 | **P3（独立轨道）** | 需前置条件与分 PR（§7.2） |
| R-2 | 配置 schema 1380 行内联 + 二次登记 | `settings.py:59`、`:1506` | P3 | **P3** | 可维护性 |
| R-3 | 任务分发 if/elif 长链 | `applications/jobs.py:143` | P3 | **P3** | 可维护性 |

> 计数：**20 条** = C×4（C-1..C-4）+ P×4（P-1..P-4）+ P-6 + R×3 + S×5（S-1/2/3/5/7）+ X×2 + M0。表中逐行可核。
> 与 v1 的对照：v1 主表 14 条 + §3.1 的 S 表 6 条 → v2 合并为一张表；新增 **X-2**、**S-7**、**M0**；
> v1 的 **P-5** 并入 S-3（同一问题，避免两个编号指向一处代码），**S-4** 并入 C-3（单写者方案自然覆盖），
> **S-6** 并入 C-4（同一处"三条迁移机制并存"）。

---

## 3. P0-A：X-2 —— PDF 导出在 Linux 上中文不可读

### 3.1 现状（代码事实 + 镜像事实）

```python
# backend/src/imeeting/export/document_export.py:113
font_path = r"C:\Windows\Fonts\msyh.ttc"
try:
    pdfmetrics.registerFont(TTFont("MSYH", font_path, subfontIndex=0)); font_name = "MSYH"
except Exception:
    font_name = "Helvetica"     # Linux 上必然走这里
```

- 生产镜像 `deploy/backend.Dockerfile` 基于 `python:3.12-slim`，apt 只装
  `curl / ffmpeg / libgomp1 / libsndfile1`（`:14`–`:17`）——**无任何 CJK 字体**；
- 本地实测确认：`Helvetica` 渲染中文**不抛异常**，只是字形缺失（生成 1527 字节的"看起来正常"的 PDF）；
- 测试抓不到：`tests/test_meeting_product_extras.py:343` 只断言 `pdf.content.startswith(b"%PDF")`。

**影响**：在文档记载的部署形态（Docker/Linux）下，导出 PDF 的中文正文、纪要、页眉页脚全部不可读；
而 DOCX 不受影响（字体由客户端提供），所以问题只在 PDF 上出现，容易被误判为"个别电脑打不开"。

### 3.2 处置（本地已实测可行的降级链）

按优先级探测，**每一级都打日志说明用了哪一级**：

```
1) env IMEETING_PDF_FONT            显式指定 TTF/TTC（运维可注入企业字体）
2) 常见 CJK 字体路径（跨平台候选）
   Linux : /usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc
           /usr/share/fonts/truetype/arphic/uming.ttc
   Windows: C:\Windows\Fonts\msyh.ttc, simsun.ttc
   macOS  : /System/Library/Fonts/PingFang.ttc
3) reportlab 内置 CID 字体 UnicodeCIDFont("STSong-Light")   ← 本地实测可用，无需任何字体文件
4) Helvetica                                              ← 仅当文本为纯 ASCII
```

本地实测结果（本轮）：

- `UnicodeCIDFont('STSong-Light')` 注册成功，生成含中文的 PDF（2508 字节），字体资源为 `CIDFontType0`；
- **注意**：该 CID 字体**不内嵌**（PDF 内无 `FontFile2/3`），依赖阅读器自带 CJK 字体。
  因此它解决"完全无字形"的问题，但不能满足 PDF/A 或严格自包含要求 → 需要自包含时走第 1/2 级并安装字体。

### 3.3 配套改动

| 改动 | 内容 |
|---|---|
| 镜像 | `deploy/backend.Dockerfile` 增装 `fonts-noto-cjk`（约 60 MB）；若不可接受，则在部署文档中要求挂载字体目录并设置 `IMEETING_PDF_FONT` |
| 配置 | 新增配置项 `export.pdf_font_path`（group=导出，type=string，默认空），便于后台改而不重启；与 env 的关系遵守三级回退 |
| 测试（**必须能失败**） | ① 断言导出 PDF 的字体资源**不是** `Helvetica`；② 断言 `b"FontFile2" in pdf or b"CIDFontType0" in pdf`；③ 用 `PDF` 文本抽取（pypdf 或 reportlab 自己的解析）校验中文串存在——三条至少实现两条 |
| 文档 | `backend/README.md` 与 `deploy/README.md` 说明字体来源与 `IMEETING_PDF_FONT` |

### 3.4 验收

- Linux 容器内导出含中文的会议 PDF，正文可见（人工目视 1 次，作为上线前检查项）；
- 新增测试在**未安装 CJK 字体**的环境下失败、在安装后通过（用 `imeeting` 的临时目录模拟字体缺失）；
- `export.pdf_font_path` 非法路径 → 落到第 3 级而不是抛错，并记一条 WARNING。

---

## 4. P0-B / P1：可测量性与真实瓶颈

### 4.1 M0：先测量（批次 0）

四项指标，全部为"只增不改"的旁路观测，失败不影响主链路：

| 指标 | 实现点 | 用途 |
|---|---|---|
| `meeting_db_statements_total{op}` | `db.py` engine 挂 `before_cursor_execute` 计数 | 量化 C-1/C-2/C-4/S-7 |
| `meeting_db_pool_in_use` / `_overflow` / `_timeout_total` | `engine.pool.status()` 周期采样 | 量化 S-7 |
| 前端 partial 处理耗时 p50/p95 | `upsertSegment` 前后 `performance.mark/measure`，开发模式汇总 | 量化 P-1/P-2/P-3 |
| 启动分解（db 迁移 / 预热 / 总计） | `lifespan` 分段计时并写日志 | 量化 C-4 |

**准入规则**：任何 P1 以下的改动，其收益描述必须引用上述指标中的一个（有基线数字），否则不进排期。
这条规则本身就是 v2 对 v1 最重要的修正。

### 4.2 C-4：启动期全表改写（P1，最值得做的性能项）

现状（每次启动都执行）：`db.py:352` 全表去重 DELETE、`:364` 无条件 `UPDATE meeting SET segment_count`
（改写**所有**会议行，即使值未变）、`:471` 逐条 `UPDATE segment`、`:595` 每次重扫偏移。

处置：

1. **一次性化**：以 Alembic 版本号判断兼容迁移是否已执行；执行过则跳过（保留代码路径支持老库升级）；
2. **去重与计数改为约束/增量**：唯一索引已存在（`db.py:358`）；历史脏数据用一次性脚本清理
   （沿用 `scripts/repair_template_slots.py` 的 dry-run/`--apply` 风格）；
3. 与 C-1 同处改动：落库时增量维护 `segment_count`，收尾时校正一次。

验收：5 万段/200 场的库，**第二次及以后启动的 DML 数为 0**（只允许 SELECT/PRAGMA），启动耗时不随段数增长。

### 4.3 C-3：async 上下文的同步 DB 调用（P1）

现状：`stream.py` 是 `async def`，但握手/收尾路径有 10 处同步调用；
同文件 `:157`、`:327` 已用 `asyncio.to_thread` → 不一致是事实。实测单次事务约 2 ms，
真实风险不是"每次 5 秒"，而是**在写锁竞争时把事件循环拖到秒级**，且新增调用仍会继续漏。

处置：收口为单写者（不在 10 处逐个补 `to_thread`）：

```
application/session_writer.py
  class SessionWriter:          # 每进程一个；1 个线程 + 有界队列（默认 256）
      submit(op) -> Awaitable   # 异步提交；关键写反压，非关键写可丢弃并计数
      close()                   # 排空后停止
```

- WS 处理器只 `await writer.submit(...)`；单写者天然序列化 SQLite 写入 → 取代 `_persist_lock`；
- 单写者把并发连接压到 1–2 条，直接缓解 S-7（§4.4）；
- 引入开关 `session.writer_enabled`（默认 `false`），灰度见 §9。

验收：`tests/test_session_writer.py`（顺序性/有界/关闭排空/异常不吞）；
4 路并发 + 100 rps HTTP 下，事件循环单次停顿 < 100 ms（`asyncio` 慢回调日志统计）。

### 4.4 S-7：连接池上限（P1，v2 新增）

实测：`create_engine` 未配置池参数（`db.py:46`）→ `QueuePool(pool_size=5, max_overflow=10)` → **上限 15**。
并发方来自：anyio 线程池（默认 40）跑同步 `def` 端点、`asyncio.to_thread` 落库、2 条 job worker、清理线程。
满载时可能 `QueuePool limit ... timed out`（默认 30 s）→ 500 与请求堆积。

处置：

```python
_engine_kwargs = {
    ...,
    "pool_size": int(_env_or("DB_POOL_SIZE", "5")),
    "max_overflow": int(_env_or("DB_POOL_MAX_OVERFLOW", "10")),
    "pool_timeout": float(_env_or("DB_POOL_TIMEOUT", "10")),   # 30s 太长：快速失败优于堆积
    "pool_pre_ping": False,   # SQLite 本地连接，无需探活
}
```

同时把池的 in-use/overflow/timeout 暴露为指标（§4.1），并据此决定是否需要提高 `pool_size`。

验收：`scripts/stress.py --only concurrency --sessions 4` + 200 rps HTTP 混合负载 5 分钟，
`meeting_db_pool_timeout_total` 为 0，5xx 为 0。

---

## 5. P2–P3：卫生类与稳定性小改

### 5.1 C-1 / C-2：写入节奏（P3，定位为卫生）

**先摆数字**（实测，见 §1.3）：C-1 在 1200 段时约 2.4 s/场（0.03% 占空比）；C-2 约 6.5 s/场（0.09%）。
**这不是性能危机**，改动价值在于：缩短写锁持有时间、减少 WAL 抖动、让 `indexById` 名副其实。

处置（不变，但从 P0 降级）：

- C-1：`INSERT ... ON CONFLICT(meeting_id, segment_id) DO NOTHING` 替代"先查后插"；
  `segment_count` 增量维护；`_persist_lock` 范围缩到提交阶段；
- C-2：`audio_chunk_seq` 按"每 32 块 / 每 5 秒 / 收到 stop"合并写；
- **边界条件（v2 新增）**：上述结论对**单场千段级**成立。N=800 时二次项占单次成本 < 2%；
  当单场段数上升到万级时二次项会反超固定开销——若要支持超长会议（>6 小时）或批量导入，
  应先按 §4.1 的指标重新测量再决定是否提前做。

验收：1200 段落库的 `SELECT` 次数 < 2×段数；重复投递不产生重复行；`segment_count == COUNT(*)`。

### 5.2 C-2 的前置依赖（M-5）

`frontend/src/api/ws.ts:45` 的 `preReady` 当前**无上界**。放宽服务端持久化节奏前，
必须先给 `preReady` 加上界（如 300 块，超出丢最旧并提示），否则断线补传量会被放大。
**顺序：P-4 的 `preReady` 上界 → C-2 合并写 → 复测。**

### 5.3 P-1 / P-2 / P-3（P3，先测再改）

| 项 | v2 的准确表述 | 处置 |
|---|---|---|
| P-1 | 实测 0.109 ms/条（N=1200），**不构成掉帧**；问题是算法与注释不符（O(1) 名不副实） | partial 快路径：命中末段时只替换、不排序不重建 Map；final 才走排序 + 增量 Map |
| P-2 | **量级未测**：`useLayoutEffect` 依赖 `[segments]`，每条 partial 读写 `scrollTop`，会触发同步布局 | 先用 Performance trace 在 1000+ 卡片下量化；确认有成本再改依赖为 `[segments.length, 最后一段文本, pinned]` + rAF |
| P-3 | **两种成本要分开**：3 次 `.filter/.some` 是微秒级；真正的成本可能来自整页重渲染 | 用 selector 拆分订阅 + `useMemo`；`SegmentCard` 已 `memo`，需确保父级回调引用稳定 |

验收：P-1 的"单条 partial 处理耗时与 N 无关"用现有 `session.test.ts` 断言；
P-2/P-3 以 §4.1 的前端指标前后对比（必须给出数字，否则视为未完成）。

### 5.4 S-2 / P-4：线程池泄漏（P2，因果已修正）

现状：`_get_executor()`（`pool.py:59`）在线程数变化时新建池，旧池不 `shutdown`（`:71`–`:78`）。
**修正后的因果**：旧池的 worker 线程阻塞在空队列上并常驻——**不持有模型对象**
（模型在 `_models` 字典里，与线程池无关）；仅当旧池尚有排队任务时短暂持有闭包引用。
后果是**线程泄漏**（每次改 `asr.infer_threads` / `session.max_concurrent` 泄漏 N 个线程）。

处置：`old.shutdown(wait=False)`；线程总数进 `/metrics`；配置变更写一条 INFO 日志便于关联。
验收：连续改 5 次 `asr.infer_threads`，进程线程数回到基线（`psutil` 断言，`dev` extra 已含）。

### 5.5 S-3 / S-1 / S-5（P2）

| 项 | 现状（已修正表述） | 处置 |
|---|---|---|
| S-3 | `save_discovered` 调用 `invalidate_cache()`，而它是**清空整个缓存**（`speaker.py:223`），不止本会议；快照每 5 段一次 → 缓存几乎不命中。"每段回表"的具体成本**未测** | 提供 `invalidate(meeting_id)` 只失效本会议；先按 §4.1 测回表成本再决定是否值得改 |
| S-1 | `session_registry.count() >= max` 是 check-then-act（`stream.py:235`） | 改 `try_acquire() -> bool` 原子操作；竞态窗口极小，属正确性卫生 |
| S-5 | 帧长依赖 uvicorn 默认 16 MB | 显式上限 1 MB（约 32 秒音频），超限丢弃并计数 |

### 5.6 P-6：无界队列与 IO 放大（P2）

| 子项 | 现状 | 处置 |
|---|---|---|
| 定稿任务并发 | `streaming.py:693` 的 `asyncio.create_task` 无数量上限；`pool.run_in_executor` 待执行队列无界 → 过载时内存无界增长 | 每会话一把 `asyncio.Semaphore(asr.infer_threads)`；队列深度进 `/metrics`；超阈时对无语音片段提前丢弃而不入队 |
| 音频头回填 | `engine/audio.py:177` 每块 `seek(0)+write(44)+seek(0,2)+flush()` | 降频到"每 64 块或每 1 秒"，`close()` 与进程退出时强制回填一次（`queue.Queue` 已有界，保持） |
| 流式缓冲头删 | `streaming.py:430` 的 `list.pop(0)` 是 O(n) | 换 `collections.deque` + 单次 `np.concatenate` |

验收：`stress.py --only backpressure` 与 `--only longrun` 下内存无单调增长；
新增 `meeting_finalize_queue_depth` 可见；音频文件任意时刻仍可解析（现有 `_wav_data_size_ok` 用例保持通过）。

### 5.7 已复核正确的三项（保留）

- `react-window@1.8.10` 在 `src` 下零引用 → 移除；
- `src/audio/pcm-worklet.js` 与 `public/pcm-worklet.js` 手工同步 → 单一来源 + 构建期复制 + 字节一致性测试；
- ruff 仅 `E4,E7,E9,F,I` → 分批开 `B` 与 `ASYNC`（async 内的同步调用正是 C-3 这类问题的静态抓手）。

---

## 6. 双平台（P2）

### 6.1 运维能力统一下沉（Linux 与 Windows 同一份实现）

| 能力 | 现状 | v2 处置 |
|---|---|---|
| 备份 | 仅 `deploy/backup_sqlite.sh` | 逻辑下沉为 `python -m imeeting.sqlite_backup backup`（`sqlite_backup.py` 已有实现），`.sh` 与新增 `.ps1` 均为薄包装 |
| 恢复 | 仅 `deploy/restore_sqlite.sh` | 同上 `restore` 子命令（完整性校验 + 安全副本 + `os.replace` 已在实现里） |
| 优雅重启 | 仅 `scripts/graceful_restart.ps1` | 同上 `drain` 子命令，补 `.sh` 薄包装 |
| 文档命令 | README 混用 `set X=` 与 `sh` | 分平台两段：cmd `set`、PowerShell `$env:`、bash `export` |

理由：备份/恢复的正确性依赖"校验 + 安全副本 + 原子替换"，两份 shell 各写一遍必然漂移。

### 6.2 X-1：Windows CI 门禁（P2）

```yaml
strategy:
  matrix:
    os: [ubuntu-latest, windows-latest]
    python: ["3.10", "3.12"]
```

已知有利条件：现有 CI 就是 `pip install -e ".[dev]"`（**不含 `[asr]`**）→ 测试套件不依赖 torch/funasr，
Windows 上跑得通的前提已具备。

**通过标准（按 M-4 修订）**：

- Windows 上**实际执行**的用例数 ≥ Linux 的 **95%**；
- 跳过清单必须是显式 allowlist，每项写明原因与移除期限，**只减不增**；
- 前端在 Windows 上跑 `npm ci && lint && test && typecheck && build`；
- `continue-on-error` 只允许用于首个 PR 的探测，**不允许长期保留**。

预期失败类型（需实测确认，不在本版断言）：文件路径大小写、换行符、时区/浮点格式、Windows 文件占用。

### 6.3 平台差异清单（加置信度列，M-7）

| 项 | 现象 | 置信度 / 核实方法 | 处置 |
|---|---|---|---|
| 事件循环 | POSIX 用 uvloop；Windows 用 asyncio（**具体策略 Proactor/Selector 待核实**） | uvloop 部分：确定；Windows 部分：**[待核实]**，`asyncio.get_running_loop().__class__` 打印 | 性能基线分平台记录；README 技术栈表加平台限定 |
| 字体 | 见 X-2 | **已实测**（本轮） | §3 |
| 文件锁 | Windows 上被占用文件无法删除/重命名（`WinError 32`） | [未实测] | 退避重试**且计数进 `/metrics`**，避免掩盖问题（m-3） |
| 工作区换行 | 本地 `deploy/*.sh` 为 CRLF，仓库为 LF | **已核实**（读取字节确认） | 加 `scripts/check-line-endings.py` 或 pre-commit |
| 依赖解析 | `torch`/`funasr` 无平台约束 | [未实测] | 提供 `constraints/win-cpu.txt`、`constraints/linux-cpu.txt` |
| SQLite WAL | 网络盘上 WAL 不可靠 | 文档事实 | 启动自检 `journal_mode` 实际值，非 `wal` 打 WARN |

---

## 7. 可维护性轨道（P3）

### 7.1 R-1 的前置条件（B-3 修订）

`StreamSession` 的"公开 API 不变"**不成立**：`stream.py` 已在调用私有成员——

```
stream.py:359  await session._finalize_current(force=True, reason="user")
stream.py:623  await session._emit({...})
stream.py:670  await session._emit({...})
```

**前置步骤（必须先做，独立 PR）**：

1. 把 `_finalize_current` 提升为公开 `finalize_current()`（用户主动切句本就是外部事件驱动，理应公开）；
2. 提供公开的 `emit_to_client(payload)` 或明确的事件钩子，替换两处 `_emit`；
3. 为这 3 处补直接单元测试（当前只经 WS 间接覆盖）；
4. 补**特征化测试**（characterization tests）：固定一段合成音频，记录现有实现的 partial/final 序列，
   重构后逐条比对——这是拆分能否安全合并的唯一判据。

**验收口径**：拆分完成后 `stream.py` 的改动总行数 **< 20 行**（即只替换 3 处调用）。

### 7.2 R-1 的拆分与估算（M-3 修订）

目标结构（按数据流阶段切，不按技术分层切）：`engine/session/{session,ingestion,segmentation,streaming_asr,offline_asr,speaker,ordering,metrics_hooks}.py`，
`session.py` 只做编排（目标 < 250 行）。

**估算（v2 修正）**：**8–12 人日，4–6 个可独立回滚的 PR**。
估算依据：1283 行 / 约 90 字段；直接或间接依赖内部状态的测试文件至少 5 个
（`test_streaming_engine.py`、`test_stability.py`、`test_ws_protocol.py`、`test_persistence_race.py`、`test_field_log_fixes.py`）。
**该估算未经验证**，需在第 1 个 PR 之后按实际速率修正。

### 7.3 R-2 / R-3（P3）

- **R-2**：`settings/` 按 group 拆模块 + 字段内联 `env=` 消除 `ENV_OVERRIDES` 二次登记（`:1506`）。
  **新增约束**：拆分必须**保持 schema 注册顺序**（前端分组渲染依赖顺序）→ 加"key 顺序快照"测试；
  从注册表生成 `.env.example` 与文档表格，消除三处漂移。
- **R-3**：`@handler(kind, heavy=...)` 注册表替代 `jobs.py:143` 的 7 段 if/elif；
  `_HEAVY_JOB_KINDS`(`:22`) 由注册表推导；worker 轮询改 `asyncio.Event` 唤醒（保留超时兜底）。
  **修正 v1 的口径**：因保留超时兜底，"空闲时 DB 查询为 0"不成立，改为
  "空闲时 DB 查询 ≤ 1/(worker·兜底周期)"，兜底周期默认 30 s。

---

## 8. 不变量与必须保持的测试（B-5 新增）

重构下列区域时，以下断言**必须始终成立**；任何一项失败即视为重构失败，不得以"后续再修"合入。

| 不变量 | 现状位置 | 必须保持的断言 |
|---|---|---|
| 密钥不以明文出库 | `settings.py` `snapshot(redact=True)` | 任何返回配置的接口，`type=password` 字段值只能是 `********` |
| 解密失败降级为空串 | `settings.py:1692` | 密文无法解密时返回 `""`（不得返回密文），且记 ERROR 日志 |
| 审计链顺序 | `audit.py` `prev_hash`/`record_hash` | 并发写入下链不断裂；`backfill_audit_hashes` 幂等 |
| 分享令牌只存摘要 | `db.py:553`–`:571` 迁移逻辑 + `share_throttle.py` | 库内无明文令牌；旧链接迁移后仍可用 |
| 权限矩阵 | `api/http/routes/deps.py` | 每类资源的所有者/可编辑/只读三档不因重构改变 |
| 会议状态收口 | `stream.py` 收尾路径 | 任何结束路径都把会议移出 `recording`（四种状态之一 + `finished_at`） |
| 片段唯一性 | `db.py:358` 唯一索引 | 重复投递不产生重复行 |

**新增的守门测试**（对应上表，逐条一项）：`test_invariants_config_secrecy.py`、`test_invariants_audit_chain.py`、
`test_invariants_share_token.py`、`test_invariants_permissions.py`、`test_invariants_meeting_state.py`。

---

## 9. 灰度、回滚与数据修复（M-8 新增）

| 改动 | 灰度 | 回滚 | 数据修复 |
|---|---|---|---|
| C-3 `SessionWriter` | `session.writer_enabled`（默认 false）；开启后**双写比对**：同一批片段走两条路径，比较落库结果，差异记 ERROR | 关闭配置即回到原路径，无需回滚代码 | 若出现缺段：按 `pending_segments` 与库内 `segment_id` 差集补写；`segment_count` 用一次 `COUNT(*)` 校正 |
| C-4 启动迁移一次性化 | 先在**副本库**上跑 dry-run | 保留原迁移代码路径 + 一个 `--force-legacy-migrate` 开关 | 备份后重跑；脚本沿用 `repair_template_slots.py` 的 dry-run/`--apply` 风格 |
| R-1 会话拆分 | 按组件分 PR，每个 PR 可独立 revert | 逐 PR revert（公开 API 已在前置步骤固化） | 无数据影响（纯代码结构） |
| X-2 字体 | 新增配置项即时生效 | 清空 `export.pdf_font_path` 即回落内置 CID 字体 | 无 |

**回滚演练**：批次 2 合并前，用 `ASR_MODE=echo` + 一场合成会议跑一次"开关打开→关闭"的演练并留档。

---

## 10. 实施批次

每批结束前必须全绿：`ruff` + 后端 `pytest` + 前端 `lint/test/typecheck/build` + 覆盖率棘轮。

### 批次 0：先测量（0.5–1 天，准入条件）

M0 四项指标落地（§4.1），跑一次基线并记录：单场 1200 段的 DB 语句数、池水位、前端 partial 耗时、启动分解。
**没有基线数字的改动不进后续批次。**

### 批次 1：用户可见 + 真实瓶颈（2–3 天）

1. **X-2** 字体降级链 + 镜像 + 配置项 + 能失败的测试（P0）
2. **S-7** 连接池显式配置 + 指标（P1）
3. **C-4** 启动迁移一次性化（P1）
4. 文档：分平台命令（§6.1 的文档部分）

回滚点：X-2 只动 `export/document_export.py` + Dockerfile + 一个配置项；S-7 只动 `db.py:46`。

### 批次 2：稳定性收口（2–3 天）

5. **C-3** 单写者（开关 + 双写比对）
6. **S-2/P-4** 线程池关闭、**S-3** 缓存失效粒度、**S-1** 原子占用、**S-5** 帧长上限
7. **P-6** 定稿任务并发上界 + 音频头回填降频 + `deque`（§5.6）
8. **`preReady` 上界**（M-5：为批次 3 的 C-2 铺路）

### 批次 3：卫生类（1–2 天，随时可插入）

9. **C-1** + **C-2** 写入节奏（按 §5.1/§5.2 顺序）
10. **P-1** partial 快路径；**P-2/P-3** 先测再改
11. §5.7 三项（死依赖 / worklet 单一来源 / lint 收紧）

### 批次 4：制度与可维护性（按意愿排期）

12. **X-1** Windows CI（含 95% 口径）
13. **R-1** 前置步骤 → 拆分（4–6 PR）
14. **R-2** / **R-3**
15. §6.1 运维能力下沉（`sqlite_backup` CLI + `.sh`/`.ps1` 薄包装）

---

## 11. 验收与度量

### 11.1 基线 → 目标（m-4）

| 指标 | 基线（已测） | 目标 | 归属 |
|---|---|---|---|
| 单场 1200 段落库耗时 | ≈2.4 s | ≈0.2 s（合批后） | C-1（P3，可选） |
| 单场 audio_seq 写事务 | ≈12000 次 | ≤ 400 次 | C-2（P3，可选） |
| 前端单条 partial 处理 | 0.109 ms | ≤ 0.05 ms | P-1（P3，可选） |
| 数据库语句数/场（SELECT） | 待测（批次 0） | ≤ 2×段数 | C-1 |
| 连接池 timeout 计数 | 待测 | **0** | S-7 |
| 启动 DML 数（二次启动） | 待测（约等于"会议数 + 段数"级） | **0** | C-4 |
| Linux 导出 PDF 中文 | 不可读 | 可读（目视 + 测试双证） | X-2 |
| Windows CI 实际执行率 | **0%** | ≥ 95% | X-1 |

### 11.2 现有可复用命令

| 用途 | 命令 |
|---|---|
| 后端回归 | `cd backend && python -m pytest -q`（README 记录 765 项，约 9 分钟） |
| 静态检查 | `python -m ruff check src/imeeting tests` |
| 前端四项 | `cd frontend && npm run lint && npm test && npm run typecheck && npm run build` |
| 端到端 / 响应式 | `npm run e2e`、`npm run verify:responsive` |
| 压力 / RTF | `python scripts/stress.py --only throughput --sessions 4`（**必须真实语音**） |
| 泄漏 | `python scripts/stress.py --only longrun` |

### 11.3 各项通过标准（含 X-1 修订口径）

| 编号 | 通过标准 |
|---|---|
| X-2 | Linux 容器导出中文 PDF 可读；新增测试在缺字体环境**失败**、装字体后通过；非法 `export.pdf_font_path` 走第 3 级并 WARN |
| M0 | 4 项指标在 `/metrics` 或日志可见，且有一份基线记录入档 |
| C-4 | 5 万段库二次启动 DML=0；启动耗时不随段数增长 |
| C-3 | 4 路并发 + 100 rps 下事件循环单次停顿 < 100 ms；开关关闭时行为与现状逐字节一致（双写比对无差异） |
| S-7 | 混合负载 5 分钟 `pool_timeout_total=0`、5xx=0 |
| X-1 | Windows 实际执行率 ≥95%，跳过清单有到期日且只减不增 |
| S-2/P-4 | 改 5 次线程数后线程数回基线 |
| C-1/C-2 | SELECT 次数 < 2×段数；`segment_count == COUNT(*)`；重复投递无重复行 |
| P-1 | 单条 partial 耗时与 N 无关（测试断言） |
| P-2/P-3 | 前端指标前后对比有数字，且 p95 下降（无数字视为未完成） |
| R-1 | `session.py` < 250 行；`stream.py` 改动 < 20 行；特征化测试逐条比对通过 |
| R-2 | 新增配置只改 1 个文件；key 顺序快照测试通过；`.env.example` 生成无差异 |
| R-3 | 新任务类型不改 `jobs.py`；空闲查询 ≤ 1/(worker·兜底周期) |

---

## 12. 风险与未验证项

| 风险 | 影响 | 缓解 |
|---|---|---|
| X-2 用 CID 字体但不内嵌 | 严格阅读器/PDF-A 场景仍可能显示异常 | 生产安装 `fonts-noto-cjk` 走第 2 级；第 3 级只作兜底并 WARN |
| C-1 `ON CONFLICT` 静默吞掉真实重复写入 | 数据正确性 | 保留"重复投递计数"指标并告警；唯一索引不动 |
| C-3 单写者成为新瓶颈 | 吞吐下降 | 单写者是 SQLite 的既有限制，只是把"隐式锁 + 事件循环阻塞"改成"显式队列"；队列深度进指标 |
| S-7 提高 pool_size 反而加剧 SQLite 写竞争 | 性能 | 以批次 0 的池水位与 timeout 指标为准；优先靠单写者（C-3）而不是加池 |
| R-1 拆分引入回归 | 转写链路 | 前置公开方法 + 特征化测试；批次前后各跑一次 `stress.py` 全 7 项对照 |
| X-1 首次引入即有失败 | 阻塞合并 | 先探测再逐个修；用"实际执行率"而非"绿勾"作为标准 |
| 估算失真 | 排期 | 所有工时为**未验证估算**；第 1 个 PR 后按实际速率修正 |

**本版未验证项**：

1. 未在 Linux 上运行任何东西；X-2 的"不可读"是代码事实 + 镜像事实的推断，**具体表现未目视确认**；
2. P-2/P-3 的成本未测（本版已明确要求先测）；
3. S-7 是否真的会耗尽未压测复现；
4. Windows CI 的预期失败清单未实测；
5. 后端 765 项测试、前端四项检查在 v1/v2 撰写期间**均未运行**。

---

## 13. 关联文档

- [v1 原文（已被替代）](2026-11-04-refactor-optimization-plan.md)
- [第三方评审 IM-REV-006](../reviews/2026-11-04-refactor-plan-review.md) —— 本版的全部修订依据
- [项目 README](../../README.md) —— 使用、部署、性能基线
- [生产可用性清单](../production-readiness.md) —— P0/P1 状态与 P2 扩容边界（IM-REV-003）
- [工程结构](../architecture/project-structure.md) —— 目录边界（IM-DES-002）
- [早期优化评估](../../OPTIMIZATION.md) —— 历史记录（IM-REV-001）
- [2026-11-03 收尾阶段深度审查](../reviews/2026-11-03-final-deep-review.md) —— 上一份全量审查（IM-REV-005）
