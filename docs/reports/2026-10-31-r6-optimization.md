# 2026-10-31 R6 优化续作：取样缓存、重建进度与先筛后算

<!-- doc-governance: IM-RPT-004 -->
> 文档类型：报告；状态：阶段快照；日期：2026-10-31。
> 维护责任：待指派（iMeeting 维护者）。[文档中心](../README.md) · [验证证据](../reviews/evidence/2026-10-31-r6/README.md) · [上一轮修复报告](2026-10-31-gap-fixes.md)。

本轮承接[上一轮 Gap 修复报告](2026-10-31-gap-fixes.md)第 5 节的“仍待完成”清单，只做本机能够验证的 R6 结构项：会后模板重建的**取样嵌入缓存**、**细粒度进度**、匹配阶段的**先筛后算**，并给出同机对照测量。FunASR 顶层按需导入本轮**没有改代码**，原因和前置条件见第 4 节；R1 的永久外键、R2 的 P1 能力以及所有需要真实模型/目标环境的事项保持原状。

## 1. 与计划的关系

[修复计划](../plans/2026-10-31-gap-remediation-plan.md) R6 行原本把“嵌入缓存、完整进度、性能对照、FunASR 按需导入”列为未完成。本轮结果：

| R6 子项 | 本轮结果 | 证据边界 |
|---|---|---|
| 嵌入缓存 | 已实现：新表 + 迁移 + 配置开关与保留期 + 失效规则 | 本机、合成音频、模型用替身 |
| 细粒度进度 | 已实现：状态表 + 按说话人提交 + 查询接口 | 本机；前端未接进度展示 |
| 先筛后算 | 已实现：场景编码向量化，语义与原 `Scenario.match_score` 等价 | 纯 numpy 路径；随机查询 |
| 性能对照 | 已测：解码路径与匹配路径 | 不含 CAM++ 推理，不外推准确率/吞吐 |
| FunASR 按需导入 | 未实施，仅留测量与决策记录 | 见第 4 节，缺 torch 环境 |
| ANN / 跨语言 | 未开始 | 维持提案 |

## 2. 已实施变更

### 2.1 取样嵌入缓存（新表 `template_sample_embedding`）

- 迁移 [20261031_05](../../backend/migrations/versions/20261031_05_template_optimization.py) 新增表；`(meeting_id, segment_id)` 唯一，一段音频只留一行向量。
- 缓存键 `audio_signature` 由**音频文件大小 + 修改时间 + 片段 `start_ms/end_ms` + `adaptive_gain` + 声纹模型名**派生。换成别的声纹模型、重新转码音频或修正片段边界，旧缓存都会自动失效，不会拿旧模型的向量去匹配。
- [取样的实现](../../backend/src/imeeting/engine/voice_template.py)先按会议一次性读出缓存，命中就直接用向量，未命中才 `_read_audio` + `extract_embedding` 并回写。
- 配置项：`spk.template_embedding_cache_enabled`（默认开，关闭后与旧行为一致）、`spk.template_embedding_cache_days`（默认 30 天，0 表示不按时间清理）；每次重建开始前按保留期清理。
- 会议物理删除时先删缓存与进度两张子表（[meeting_cleanup](../../backend/src/imeeting/meeting_cleanup.py)），避免重演 GAP-15 的外键错误。

### 2.2 细粒度进度（新表 `template_rebuild_state`）

- 状态字段：`stage`（idle/scanning/extracting/storing/done/failed/skipped）、`speakers_total`、`speakers_done`、`samples_done/cached/extracted`、`templates_created/updated`、`note`、`last_error`、`started_at`、`finished_at`。
- 重建改为**每位说话人处理完就提交一次短事务**并写一条进度；中途失败时写 `stage=failed` 与 `last_error` 后原样抛出，未处理完的说话人不计入 `speakers_done`。已完成说话人的模板保留，重跑通过来源唯一键与取样缓存继续，不会重复累计同一条证据。
- 返回统计新增 `speakers`、`cache_hits`、`cache_misses`，旧字段（`created/updated/skipped*`）保持不变。
- 新增 `GET /api/v1/voiceprints/templates/rebuild-status?meeting_id=`，按会议写/读权限校验；没有记录时返回 `stage=idle`。前端本轮没有接进度展示，属于接口先行。

### 2.3 先筛后算

- [匹配实现](../../backend/src/imeeting/engine/voice_match.py)在缓存里预存每行场景的整型编码；每次匹配用 numpy 求候选掩码，再只对候选集做点积与融合。
- 语义与原 `Scenario.match_score(...) >= 0.6` 完全等价（“至少一个维度可比且不同维度 ≤1”），由专门回归用例锁定；候选为空时仍回退全矩阵，与原实现一致。

## 3. 同机测量（摘要）

完整表格、命令与边界见[基准材料](../../benchmarks/template_rebuild_20261031/README.md)与[原始 JSON](../../benchmarks/template_rebuild_20261031/results-20261031.json)。环境：Windows 11、Python 3.14.3、numpy 2.5.4、16 逻辑核、`ASR_MODE=off`、无 torch。

**重建**（4 位说话人 × 32 段 = 每轮 128 条取样，真实 SQLite + 真实 WAV 解码，声纹模型用确定性替身）：

| 运行 | 墙钟(s) | 解码次数 | 缓存命中 |
|---|---:|---:|---:|
| 缓存关闭（首次，建 20 条模板） | 0.652 | 128 | 0 |
| 缓存关闭（重复，仅更新） | 0.222 | 128 | 0 |
| 缓存开启（首次，填充缓存） | 0.319 | 128 | 0 |
| 缓存开启（重复，全部命中） | 0.173 | 0 | 128 |

可比的两次是「缓存关闭（重复）」与「缓存开启（重复）」：128 次逐段解码被完全消掉，墙钟 0.222 → 0.173 s。解码在合成音频上只占其中约 0.05 s，真实会议的主要成本是每条的 CAM++ 推理，本机没有模型，**不能把这个比例外推到生产**。同一配置连跑两次时「缓存重复重建」在 0.19–0.26 s 之间波动。

**匹配**（1980 条活跃模板，200 次随机查询）：

| 项目 | 平均 | p95 |
|---|---:|---:|
| 场景掩码（旧：逐行 Python） | 1.977 ms | 2.171 |
| 场景掩码（新：整型向量化） | 0.059 ms | 0.090 |
| 端到端匹配（粗筛开启，候选占比 41.67%） | 0.680 ms | 0.828 |
| 端到端匹配（全矩阵） | 1.591 ms | 2.037 |

矩阵冷加载 104.047 ms（1980 条），只在缓存过期/失效时发生。单次测量的抖动在重建 0.1–0.2 s、匹配尾部 10–30% 量级；并行跑测试会把这组数字整体抬高（实测过一轮约 +60%），所以测量期间没有其他重负载。

## 4. FunASR 按需导入：本轮不改，先留测量与前置条件

**本机基线**（留档，不作为收益）：`import funasr` 14.459 s、导入 121 个 `funasr.*` 模块、`get_import_errors()` 记录 289 条被静默吞掉的导入错误（缺 torch/hydra/jamo 等）。

**审计结论**：FunASR 的组件表完全依赖导入副作用——17 张表、186 个 `@tables.register`；模型模块不导入自己的 encoder/decoder/predictor，`AutoModel` 只查表、没有 import 兜底；12 个重复键的胜出方由导入顺序决定，`docs/model_registration.md` 也写明“导入及其顺序有意义”。因此把 `import_submodules(__name__)` 直接换成懒加载会立刻打出 `None` 不可调用、组件缺失，以及 `tests_models/` 那批端到端 smoke（Paraformer/SenseVoice/FSMN-VAD/CT-Punc/CAM++）无法验证的问题。

**前置条件（下一轮）**：组件→模块清单；`build_model` 按解析后的配置加载组件；`MissingOptionalDependency` 语义；顶层/CLI/插件兼容测试。`docs/architecture/funasr-refactor.md`（FA-DES-001，状态 proposed）里的 25.468 s / 401 模块是 10-18 的旧基线，不能当作今天的优化收益。本轮不删 `import_submodules`（`tests/test_auto_model.py` 仍在测它），也不改 `funasr/__init__.py`。

## 5. 口径澄清

- 缓存键包含模型名：切换 `spk.embedding_model` 会让整场缓存失效，这一点有回归保护。
- 缓存不改变证据口径：模板仍按 `source_key` 唯一聚合，同源重放不增加权重；缓存只是省掉重复解码与推理。
- 进度是**按说话人**粒度的近似，不是逐样本实时进度；`samples_done = samples_cached + samples_extracted`。
- 重建中途失败会留下已完成说话人的模板（短事务已提交），这是有意的：长会议失败后重跑不必从头再来。
- `spk.template_match_enabled` 仍然默认 `false`；本轮的数字不构成“真实准确率”或“可以打开开关”的依据。

## 6. 验证

| 检查 | 结果 | 证据 |
|---|---|---|
| 后端完整回归 | 699 通过，0 失败；413.58 秒 | [backend-tests.xml](../reviews/evidence/2026-10-31-r6/backend-tests.xml) |
| 本轮新增回归 | 10 项（缓存命中/失效、进度失败保留、清理、粗筛等价性、接口鉴权、迁移升降级） | [test_template_rebuild_optimization.py](../../backend/tests/test_template_rebuild_optimization.py) |
| Ruff（源码/测试/迁移） | 通过 | [verification.json](../reviews/evidence/2026-10-31-r6/verification.json) |
| 迁移 05 升级/降级/再升级 | 通过（旧 03/04 表结构上直接执行） | 同上 |
| 文档规制 | `docs_governance.py` 校验通过 | 同上 |

前端本轮没有改动，因此没有重跑 Vitest/Playwright；上一轮的 89 项前端与 6 项浏览器结果仍然只覆盖那一轮的范围，不能拿来支持本轮的接口。

## 7. 迁移与回滚

- `20261031_05` 只新增两张表，不改任何旧表结构；`downgrade()` 删除这两张表。
- 本轮只在临时数据库验证升降级，**没有对用户现有数据库执行迁移**；启动时按既有 `init_db` 流程执行。
- 运行期回滚：把 `spk.template_embedding_cache_enabled` 置 `false` 即回到旧路径（无需删表）；彻底回退用迁移降级，会丢缓存与进度，但不影响模板本体与声纹数据。

## 8. 仍未完成（不要据此宣称）

- R1：Person 与 Voiceprint 的永久外键统一；R2：全库近邻合并与主模板等 P1 能力。
- R3/R4/R5：真实供应商成本、浏览器与真实模型联测、固定语料校准与灰度签收；`spk.template_match_enabled` 默认保持 `false`。
- R6：FunASR 按需导入（缺 torch 环境）与 ANN/跨语言方案。
- 目标环境多用户验收、TLS/Docker、长稳、漏洞扫描、生产库迁移与备份恢复。

下一步建议顺序：先在目标机用真实会议跑一次重建，靠 `rebuild-status` 记录各阶段耗时与 `cache_hit_rate`；再用同人/异人语料校准阈值；最后才评估按需导入（必须先在带 torch 的环境准备 smoke 用例）。

本轮保留工作区原有修改，没有提交、发布或重启现有业务服务。
