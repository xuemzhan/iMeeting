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
| 9 | 证据不足的自动声纹会后停用（第三轮补做） | `engine/voice_template.disable_noise_identities`、两个 `spk.auto_identity_min_*` 配置、`TemplateRebuildStatus` | 会后重建结束时扫一遍**本场自动生成**的「待命名」声纹：片段数 < 3 **且** 累计语音 < 3 秒就停用（停用可恢复，不删除；人工命名过的、别的会议的都不动）。重建返回值新增 `noise_disabled`，前端给一行提示 |
| 10 | 前端覆盖率棘轮（第三轮补做） | `frontend/scripts/coverage-ratchet.mjs`、`coverage-baseline.json`、`vitest.config.ts`、`.github/workflows/ci.yml` | 不设绝对阈值（现状 11.5% 行覆盖，一设就长期红），改为**只保证不倒退**：基线 11.5 / 11.13 / 7.94 / 9.64（行/语句/函数/分支），任一项下降超过 0.2 个百分点即失败；`--update` 显式抬高门槛。CI 的 frontend job 增加该步骤 |
| 11 | 关键路径测试 + 抬高覆盖率基线（第四轮补做） | `hooks/useParticipants.test.ts`、`components/VoiceprintMergePanel.test.tsx`、`components/TemplateRebuildStatus.test.tsx`、`coverage-baseline.json` | 三个此前零覆盖的文件补了 20 项测试；顺带**修掉一个真缺陷**：`extract()` 的「额度用完 / 转写不足」提示会被紧随其后的 `reload()` 清空，用户永远看不到。基线从 11.5/11.13/7.94/9.64 抬到 15.97/15.42/11.14/12.72 |
| 12 | 请求层测试 + 基线再抬（第五轮补做） | `api/client.test.ts`、`coverage-baseline.json` | 17 项测试覆盖「出错时怎么办」：detail 三种形状（字符串 / FastAPI 数组 / 不认识）、401 的两种含义（会话失效 vs 密码错 vs 匿名）、204 与纯文本响应、FormData 不设 Content-Type、`relabel` 的 cancel / label_only / confirm_self 三条分流。基线再抬到 17.39/16.85/11.88/14.25 |
| 13 | 详情页动作层测试（第六轮补做） | `hooks/useMeetingDetail.test.tsx`、`store/session.test.ts`（+5 项） | 19 项测试锁住「改动后必须让相关 query 失效、失败必须落到 genError、没有转写不许生成纪要」；覆盖改名/改正文/确认说话人/生成/导出/跳转/URL 时间戳自动定位，以及 store 的 `persistTitle`（无会议、空标题、成功裁剪、失败留错）与 `reset`。基线再抬到 19.62/18.91/12.92/15.19 |
| 14 | 后端覆盖率门禁（第七轮补做） | `backend/scripts/coverage_ratchet.py`、`backend/coverage-baseline.json`、`pyproject.toml`（`pytest-cov` + `[tool.coverage.*]`）、`ci.yml` | 与前端同一套棘轮思路：当前 81.20%（11148 条语句）设为基线，低于基线减去 0.2 个百分点即失败；倒退时打印缺口最大的 5 个文件。CI 的 backend job 由「pytest」改为「pytest --cov + ratchet」，只多跑一次（约 11 分钟） |
| 15 | eslint 告警清理（第八轮补做，17 → 10） | `ActionsPanel`/`MembersDialog`/`ShareDialog`、`pages/Meeting`、`useHistoryMeetings`、`Waveform` | 修 7 条：3 条 `exhaustive-deps`（把 `load` 收进 `useCallback` 并补进依赖，避免每次渲染重建导致的漏依赖）、2 条 `purity`（日期范围从渲染期 `Date.now()` 挪进取数函数，queryKey 改用 `range`）、1 条 `set-state-in-effect`（浏览器能力检查改惰性初始值，少一轮渲染）、1 条 `refs`（波形电平改为在 effect 里写 ref，符合渲染纯函数约束） |

新增回归用例：`tests/test_repair_template_slots.py`（4 项）、`tests/test_voiceprint_merge_bulk.py`（5 项）、`tests/test_voiceprint_reenroll.py`（3 项）、`tests/test_relabel_scopes.py`（3 项）、`tests/test_noise_identity_cleanup.py`（2 项）、`tests/test_identity_workflows.py`（+2 项：解绑保留原标注、迁移升降级）。

## 2 验证

| 项 | 命令 | 结果 |
|---|---|---|
| 后端静态检查 | `ruff check src/imeeting tests migrations scripts` | All checks passed（顺手修掉 `scripts/bench_offline.py` 一处既有 import 排序） |
| 新增用例 | `pytest tests/test_repair_template_slots.py tests/test_voiceprint_merge_bulk.py tests/test_voiceprint_reenroll.py tests/test_relabel_scopes.py` | 15 passed |
| 既有声纹接口 | `pytest tests/test_voiceprints_api.py` | 26 passed |
| 全量后端 | `pytest tests -q` | **785 passed, 0 failed**（8 分 09 秒；改造前 766 项） |
| 脚本试运行 | `python scripts/repair_template_slots.py --db data/meeting.db` | `OK: 16 条模板均在 0..4 槽位范围内`（实库无越界数据） |
| 迁移实库验证 | 拷贝 `data/meeting.db` 后跑 `init_db()`（兼容 DDL 路径与 Alembic 路径各一次） | 两条路径都补上 `segment.speaker_previous`；`alembic_version` 升到 `20261102_01`；2671 条片段无损 |
| 前端类型 | `npm run typecheck` | 通过 |
| 前端单测 | `npm test` | 157 passed（17 文件；本轮 +19） |
| 前端 lint | `npm run lint` | 0 error，17 warn（与改造前同数，新增文件 0 warn） |
| 前端构建 | `npm run build` | 通过，`dist` 已产出 |
| 覆盖率棘轮（前端） | `npm run test:coverage && npm run coverage:check` | 行 19.62% / 语句 18.91% / 函数 12.92% / 分支 15.19%，与基线持平；反向验证仍有效 |
| 覆盖率棘轮（后端） | `pytest -q --cov=imeeting --cov-report=json:coverage.json` + `python scripts/coverage_ratchet.py` | 语句覆盖 **81.20%**（11148 条语句、93 个文件）；持平通过；人为把基线抬高 2 个点后确实 exit 1 并列出缺口文件（已复原） |

## 3 仍未完成（本轮明确不做，附原因）

### 3.1 需要产品决策，不能由实现方单方面改

1. **一次确认只做一件事**（本轮已拆开机械部分，语义仍待决策）：`RelabelRequest` 现在有 `learn` / `rename_identity` / `bind_person` 三个开关，关掉任一项都不会产生对应副作用，审计里分别记账；冲突弹窗也给了「只改本场显示名」入口。**仍未定的**是默认语义本身：用户输入一个名字时，系统该默认「纠正叫错」（现在就是这样，会改全局身份名）还是「这是另一个人」（新建身份），这决定了三个开关的默认值，需要产品拍板。
2. **允许一人多 speaker_key**（本轮修掉破坏性部分；另外发现它不是唯一解）：原来改绑会把旧 key 名下的片段改写成「未确认说话人」，原标注随之消失——现在原标注保存在 `speaker_previous` 并在转写里可见，反复改标定不再销毁证据。而且「同一人的两个簇」本来就有 `POST /meetings/{id}/merge-speakers` 这个入口（按相似度合并聚类），所以再引入「一人绑多 key」会变成第二套等价机制。**仍未做**的只剩真正放宽 `UNIQUE(meeting_id, person_id)`——SQLite 必须重建表并迁移历史数据，等有明确产品需求（比如「两个簇是同一个人但不想合并」）再排期。
3. **自动建档的最小证据门槛**（本轮已落地为「会后停用」，未改实时链路）：审查原建议是「少于 3 段且少于 3 秒不得建身份」，但那会推迟实时转写里「说话人 1」的出现。折中做法是保持实时建档不变，在**会后重建结束时**把证据不足的自动身份停用（可恢复）。仍未定的只是默认门槛值（现为 3 段 / 3000 毫秒），管理员可在设置里改，不需要代码变更。

### 3.2 需要环境或第三方

- 真实语料阈值校准与 `spk.template_match_enabled` 灰度（开关仍默认关闭）；
- 目标服务器 `docker compose`、TLS、镜像扫描、多用户并发验收；
- iOS Safari / Android Chrome 真机；
- 长稳、故障注入、备份恢复演练；
- FunASR 按需导入（需带 torch 的环境与组件清单）、ANN/跨语种检索。

### 3.3 结构性技术债
- 迁移仍是 `create_all` + 手写兼容 DDL + Alembic 三处并存，彻底收敛需在 PostgreSQL 上跑通升降级；
- 前端覆盖率 19.62%（行）/ 12.92%（函数），**棘轮只能防倒退、不能代替补测**：`pages/History.tsx`、`pages/Meeting.tsx`、`components/TranscriptStream.tsx`、`store/session.ts` 的 `start/stop`（WebSocket + 录音）仍是大头；
- 仍有 **10 条** eslint warn：9 条 `set-state-in-effect`（挂载时同步 `setState` 的取数 hook）+ 1 条 `react-hooks/refs`（`PaneSplitter` 渲染期读容器宽度）。这两类都要结构性改动——把取数迁到 `useQuery`、把容器宽度改成受控状态——不适合顺带做，已单列在 IM-RPT-010 第 15 行；

## 4 关联文档

- [Gap 修复计划](../plans/2026-10-31-gap-remediation-plan.md)
- [R6 优化续作](2026-10-31-r6-optimization.md)、[模板合并](2026-11-01-template-merge.md)、[主模板续作](2026-10-31-template-primary.md)
- [声纹识别机制审查](../reviews/2026-11-01-voiceprint-mechanism-audit.md)
- [需求索引](../requirements/README.md)
