# iMeeting 文档中心

最新独立复核修复见[2026-11-01修复报告](reports/2026-11-01-review-fixes.md)及[验证证据](reviews/evidence/2026-11-01-review-fixes/README.md)：覆盖批准来源保护、会议清理、推理写锁和模型版本缓存失效。

面向维护者和接手人。整理日期：2026-10-31。当前使用和部署入口分别是[项目 README](../README.md)和[部署手册](../deploy/README.md)。下表区分文档状态与实现状态，编号用于跨文档追溯。

## 设计、需求与计划

| 编号 | 正文 | 文档状态 | 使用提示 |
|---|---|---|---|
| IM-DES-001 | [初始系统设计](../DESIGN.md) | 历史设计稿 | 待评审 v1.0 意图，不是全部现行行为 |
| IM-DES-002 | [工程结构](architecture/project-structure.md) | 持续维护 | 当前 Python/React 目录与依赖方向 |
| IM-DES-003 | [Go + C++ 渐进式重写](architecture/native-rewrite-plan.md) | 提案 | 尚不能据此认定已迁移；排期是估算 |
| IM-REQ-001 | [人员本体](requirements/REQ-001-person-ontology.md) | 需求草案 | P0 已有阶段实现，评审/验收与实现分别记录 |
| IM-REQ-002 | [多场景声纹库](requirements/REQ-002-voiceprint-multiscene.md) | 需求草案 | 模板及 UI 已有实现；实时模板匹配仍默认关闭 |
| IM-PLN-001 | [手机 / 平板自适应改造方案](plans/2026-11-01-responsive-ui-adaptation.md) | 已实施（P0–P3、P5 主体） | 断点/令牌/逐页改造/验收口径；落地结果见 IM-RPT-009 |

需求依赖和未决问题见[需求索引](requirements/README.md)。新实施计划放入 `docs/plans/YYYY-MM-DD-topic.md`，关联需求与设计，阶段结果另写报告。

## 评审、报告与证据

| 编号 | 正文 | 文档状态 | 适用边界 |
|---|---|---|---|
| IM-REV-001 | [早期优化评估](../OPTIMIZATION.md) | 历史记录 | 当时的缺陷和建议，现行状态查生产清单与复核 |
| IM-REV-002 | [2026-10-29 生产复核](production-review-2026-10-29.md) | 阶段快照 | 单 backend / SQLite；不代替目标服务器验收 |
| IM-REV-003 | [生产可用性清单](production-readiness.md) | 持续维护 | 能力清单和 P2 扩容边界 |
| IM-EVD-001 | [2026-10-29 审计证据](audits/2026-10-29/README.md) | 阶段快照 | XML/JSON 原始结果及扫描边界 |
| IM-RPT-001 | [2026-10-17 模型升级验证](../benchmarks/asr_model_upgrade_20261017/REPORT.md) | 阶段快照 | CPU、指定语料、CER 和资源口径 |
| IM-RPT-002 | [2026-10-31 人员/声纹阶段记录](reports/2026-10-31-person-voiceprint-progress.md) | 阶段快照 | P0 实现、原实测、已知限制与待办 |
| IM-EVD-002 | [2026-10-31 代码与需求核查证据](reviews/README.md) | 阶段快照 | 新增权限、身份与模板反例；包含复现命令 |
| IM-RPT-003 | [2026-10-31 Gap 修复报告](reports/2026-10-31-gap-fixes.md) | 阶段快照 | 逐项修复、规格决定、迁移与未完成项 |
| IM-EVD-003 | [修复验证证据](reviews/evidence/2026-10-31-fixes/README.md) | 阶段快照 | 完整回归、浏览器流程、环境与修复后源码哈希 |
| IM-RPT-004 | [2026-10-31 R6 优化续作](reports/2026-10-31-r6-optimization.md) | 阶段快照 | 取样缓存、重建进度与先筛后算；同机对照不含模型推理 |
| IM-EVD-004 | [R6 优化验证证据](reviews/evidence/2026-10-31-r6/README.md) | 阶段快照 | 699 项后端回归、Ruff、迁移升降级与未验证边界 |
| IM-EVD-005 | [模板重建与匹配基准](../benchmarks/template_rebuild_20261031/README.md) | 阶段快照 | 合成音频、模型替身；原始 JSON 与复现命令同址 |
| IM-RPT-005 | [2026-10-31 身份统一续作](reports/2026-10-31-identity-unify.md) | 阶段快照 | 声纹与人员的永久关联；老库无数据库级外键，歧义不回填 |
| IM-EVD-006 | [身份统一验证证据](reviews/evidence/2026-10-31-identity-unify/README.md) | 阶段快照 | 707 项后端回归、Ruff、迁移回填/降级与未验证边界 |
| IM-RPT-006 | [2026-10-31 主模板续作](reports/2026-10-31-template-primary.md) | 阶段快照 | 主模板（pinned）选择与自动淘汰保护；模板合并仍未做 |
| IM-EVD-007 | [主模板验证证据](reviews/evidence/2026-10-31-template-primary/README.md) | 阶段快照 | 715 项后端回归、Ruff、迁移部分唯一索引/降级与未验证边界 |
| IM-RPT-007 | [2026-11-01 模板合并](reports/2026-11-01-template-merge.md) | 阶段快照 | 合并两条模板的口径、接口与审计；跨设备向导仍未做 |
| IM-EVD-008 | [模板合并验证证据](reviews/evidence/2026-11-01-template-merge/README.md) | 阶段快照 | 722 项后端回归、Ruff 与合并用例（服务层+接口层） |
| IM-REV-004 | [2026-11-01 全面审查](reviews/2026-11-01-comprehensive-project-review.md) | 阶段快照 | 全栈复核：2 条 P0、2 条 P1、4 条 P2、5 条 P3；均已在本轮修复 |
| IM-EVD-009 | [全面审查验证证据](reviews/evidence/2026-11-01-comprehensive-review/README.md) | 阶段快照 | 审查当时的 743 项后端测试原始输出（2 失败）、Ruff、前端四项检查与 4 个独立探针 |
| IM-RPT-008 | [2026-11-01 全面审查问题修复](reports/2026-11-01-comprehensive-review-fixes.md) | 阶段快照 | 11 项逐条处置、行为变化与影响面、未完成项 |
| IM-EVD-010 | [修复验证证据](reviews/evidence/2026-11-01-comprehensive-review-fixes/README.md) | 阶段快照 | 修复后的后端 765 项全绿、Ruff、前端四项检查与 0 断链结果 |
| IM-RPT-009 | [2026-11-01 手机/平板自适应改造](reports/2026-11-01-responsive-ui.md) | 阶段快照 | P0–P6 全部落地：令牌与断点、顶栏紧凑化、会中/详情页三标签、平板双栏、8 个弹窗 Sheet、提示条折叠、408 处字号语义化；6 档视口零溢出、触控 ≥44、1440 验收 14/14、响应式 e2e 13 条通过；长列表实测后不引入虚拟滚动；未完成项逐条列出 |
| IM-EVD-011 | [改造前基线](reviews/evidence/2026-11-01-responsive-baseline/README.md) | 阶段快照 | 改造前四视口量测（390px 溢出 +25px、CTA 2 个）与 4 张截图 |
| IM-EVD-012 | [改造后验证证据](reviews/evidence/2026-11-01-responsive/README.md) | 阶段快照 | 6 档 × 5 页探针输出、会中/详情页 390px 三标签截图、响应式 e2e 记录、CI 门禁与桌面验收结果 |

报告列表见[报告索引](reports/README.md)，新增和归档规则见[子项目文档规制](governance/README.md)。最新修复结果查 IM-RPT-007、IM-RPT-006 与 IM-RPT-005；旧模型数据与漏洞扫描仍只在各期原范围内有效，本次未生产部署。

联合工作区的核查材料已并入本仓库：修复计划在 [plans/](plans/)，原核查在[代码与文档对应性核查](reviews/2026-10-31-code-document-gap-review.md)，逐条追溯证据在[原始证据](reviews/evidence/2026-10-31-gap-review/)。跨工作区的全量台账（含 FunASR 侧文档）仍由根目录 `docs/governance/` 维护，不随本次迁移。
