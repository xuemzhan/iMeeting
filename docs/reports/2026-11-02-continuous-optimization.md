# 2026-11-02 逐项优化：模板治理闭环、重建进度与跨设备注册

> 文档类型：报告；状态：阶段快照；日期：2026-11-02。
> 上承[文档中心](../README.md)与[Gap 修复计划](../plans/2026-10-31-gap-remediation-plan.md)。
> 维护责任：待指派（iMeeting 维护者）。证据随文列出，命令可复跑。

本轮只做**代码可完成且不改变产品语义**的项，并把需要产品决策的项单独列出，不合并宣称。
环境为 Windows 本机工作副本；未在目标服务器、真实模型和真机上验收。

## 1 已完成

| # | 项 | 实现位置 | 行为变化 |
|---|---|---|---|
| 1 | 越界槽位整理脚本，替代「拒绝启动 + 手工 SQL」 | `backend/scripts/repair_template_slots.py`、`db.py` 启动提示 | 新增 `--apply` 整理入口：按 主模板→已确认→质量→创建时间 重排 0..4，超出五槽的模板按引擎既有口径退役（负槽位 + `superseded_by`），默认 dry-run |
| 2 | 会后模板重建进度接前端 | `components/TemplateRebuildStatus.tsx`、`pages/MeetingDetail.tsx` | R6 的按说话人进度、缓存命中率、新建/更新数在「纪要」页可见，并提供「重新重建」；仅在任务进行中轮询 |
| 3 | 模板合并界面补预览与批量闭环 | `components/VoiceprintMergePanel.tsx`、`pages/Voiceprints.tsx` | 每对可展开预览（删除谁、保留谁、各自样本条数与时长、不可撤销提示）；「生成合并计划」dry-run 后才允许执行 |
| 4 | 全库近邻合并的执行入口 | `POST /api/v1/voiceprints/merge-suggestions/apply` | 同一声纹一次只并一对；默认 `dry_run=true`；默认不合并两个已命名身份；逐对留 `voiceprint_bulk_merge` 审计；单对失败只回滚该对 |
| 5 | 跨设备重新注册（REQ-002 FR-5 末项） | `POST /api/v1/voiceprints/{id}/enroll`、`VoiceprintTemplateSummary.tsx` | 旧路径是「同名录入覆盖质心」，等于把原基线扔掉；新路径**只追加**一条场景模板，原质心与其余场景样本保留；设备类型写入 `scenario_device` |
| 6 | 文档口径与部署警示 | `deploy/README.md`、`docs/README.md`、`docs/requirements/README.md`、`docs/plans/2026-10-31-gap-remediation-plan.md` | 补 `ALLOW_BOOTSTRAP_PASSWORD` 危险开关说明；修 3 处过期口径；计划页 R0–R6 状态表刷新到当前事实 |
| 7 | 一次改名只做一件事（第二轮补做） | `RelabelRequest.learn / rename_identity / bind_person`、`VoiceConfirmationDialog`、`client.relabel` | 三个效果各自可关：关 `learn` 不评估冲突、不写模板；关 `rename_identity` 不动声纹库名字、不新建身份；关 `bind_person` 不写参会人绑定。审计 payload 增加 `effects` 分别落账。默认仍全开，行为不变。冲突弹窗新增「只改本场显示名」一键走纯改标签路径 |
| 8 | 解绑不再抹掉原标注（第三轮补做） | `Segment.speaker_previous`、`persons.bind_speaker`、`schemas.SegmentOut`、`TranscriptPanel`、迁移 `20261102_01` | 改绑/解绑时旧 key 的片段仍改为「未确认说话人」，但原标注（如「郭睿」）写进 `speaker_previous` 并在转写里显示「原标注 X」；反复改标定不再销毁文本证据。兼容 DDL 与 Alembic 两条路径都补列 |

新增回归用例：`tests/test_repair_template_slots.py`（4 项）、`tests/test_voiceprint_merge_bulk.py`（5 项）、`tests/test_voiceprint_reenroll.py`（3 项）、`tests/test_relabel_scopes.py`（3 项）、`tests/test_identity_workflows.py`（+2 项：解绑保留原标注、迁移升降级）。

## 2 验证

| 项 | 命令 | 结果 |
|---|---|---|
| 后端静态检查 | `ruff check src/imeeting tests migrations scripts` | All checks passed（顺手修掉 `scripts/bench_offline.py` 一处既有 import 排序） |
| 新增用例 | `pytest tests/test_repair_template_slots.py tests/test_voiceprint_merge_bulk.py tests/test_voiceprint_reenroll.py tests/test_relabel_scopes.py` | 15 passed |
| 既有声纹接口 | `pytest tests/test_voiceprints_api.py` | 26 passed |
| 全量后端 | `pytest tests -q` | **783 passed, 0 failed**（8 分 46 秒；改造前 766 项） |
| 脚本试运行 | `python scripts/repair_template_slots.py --db data/meeting.db` | `OK: 16 条模板均在 0..4 槽位范围内`（实库无越界数据） |
| 迁移实库验证 | 拷贝 `data/meeting.db` 后跑 `init_db()`（兼容 DDL 路径与 Alembic 路径各一次） | 两条路径都补上 `segment.speaker_previous`；`alembic_version` 升到 `20261102_01`；2671 条片段无损 |
| 前端类型 | `npm run typecheck` | 通过 |
| 前端单测 | `npm test` | 101 passed（13 文件） |
| 前端 lint | `npm run lint` | 0 error，17 warn（与改造前同数，新增文件 0 warn） |
| 前端构建 | `npm run build` | 通过，`dist` 已产出 |

## 3 仍未完成（本轮明确不做，附原因）

### 3.1 需要产品决策，不能由实现方单方面改

1. **一次确认只做一件事**（本轮已拆开机械部分，语义仍待决策）：`RelabelRequest` 现在有 `learn` / `rename_identity` / `bind_person` 三个开关，关掉任一项都不会产生对应副作用，审计里分别记账；冲突弹窗也给了「只改本场显示名」入口。**仍未定的**是默认语义本身：用户输入一个名字时，系统该默认「纠正叫错」（现在就是这样，会改全局身份名）还是「这是另一个人」（新建身份），这决定了三个开关的默认值，需要产品拍板。
2. **允许一人多 speaker_key**（本轮修掉破坏性部分，数据结构仍待排期）：原来改绑会把旧 key 名下的片段改写成「未确认说话人」，原标注随之消失——现在原标注保存在 `speaker_previous` 并在转写里可见，反复改标定不再销毁证据。**仍未做**的是真正让一个人同时持有多个簇：需要放宽 `UNIQUE(meeting_id, person_id)`（SQLite 的 UNIQUE 不能就地删除，必须重建表并迁移历史数据），并把按 `(meeting_id, person_id)` 取单行的代码逐个改为按 `speaker_key`。
3. **自动建档的最小证据门槛**（≥3 段且 ≥3 秒）：会改变实时转写链路的建号时机（`engine/streaming.py`、`engine/speaker.py`），可能让正常单人会议延迟出现姓名。需要先定「延迟可见」还是「事后清理」的口径。

### 3.2 需要环境或第三方

- 真实语料阈值校准与 `spk.template_match_enabled` 灰度（开关仍默认关闭）；
- 目标服务器 `docker compose`、TLS、镜像扫描、多用户并发验收；
- iOS Safari / Android Chrome 真机；
- 长稳、故障注入、备份恢复演练；
- FunASR 按需导入（需带 torch 的环境与组件清单）、ANN/跨语种检索。

### 3.3 结构性技术债
- 迁移仍是 `create_all` + 手写兼容 DDL + Alembic 三处并存，彻底收敛需在 PostgreSQL 上跑通升降级；
- 前端覆盖率 12% 且无阈值，直接设门禁会让 CI 长期红灯；
- 17 条既有 eslint warn（`set-state-in-effect` 等）需逐处结合业务判断。

## 4 关联文档

- [Gap 修复计划](../plans/2026-10-31-gap-remediation-plan.md)
- [R6 优化续作](2026-10-31-r6-optimization.md)、[模板合并](2026-11-01-template-merge.md)、[主模板续作](2026-10-31-template-primary.md)
- [声纹识别机制审查](../reviews/2026-11-01-voiceprint-mechanism-audit.md)
- [需求索引](../requirements/README.md)
