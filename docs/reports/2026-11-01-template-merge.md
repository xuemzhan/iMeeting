# 2026-11-01 模板治理收尾：合并两条模板

<!-- doc-governance: IM-RPT-007 -->
> 文档类型：报告；状态：阶段快照；日期：2026-11-01。
> 维护责任：待指派（iMeeting 维护者）。[文档中心](../README.md) · [验证证据](../reviews/evidence/2026-11-01-template-merge/README.md) · [上一轮主模板](2026-10-31-template-primary.md)。

REQ-002 FR-5 的模板治理还剩最后一项“合并两条模板”。主模板那轮把“合并后 slot、来源贡献与 confirmed 归属”列为必须先定清楚的前提；本轮先把口径写进代码与文档，再实现。

## 1. 合并口径（先定清楚）

| 问题 | 决定 |
|---|---|
| 保留哪一条 | 调用方显式指定 target，target 的 slot 与行保留；source 退役 |
| 来源贡献 | source 的 `VoiceprintTemplateSource` 改挂到 target（`(voiceprint_id, source_key)` 唯一，重复的丢弃），随后按来源确定性重算质心 |
| 样本时长 / 信噪 / 质量 | `sample_ms` 累计；`snr_db`、`quality` 取两者最大值 |
| confirmed | 任一方已确认即视为已确认，`confirmed_by/confirmed_at` 保留已有的 |
| 主模板 | 任一方是主模板时 target 成为主模板（先清 source 标记再置位，兼容部分唯一索引） |
| 退役行 | `slot` 取负、`superseded_by` 指向 target；不删除，可追溯 |
| 拒绝的情况 | 自己合并自己、跨声纹（等于跨人）、已停用、已退役 |

## 2. 已实施变更

- [merge_templates](../../backend/src/imeeting/engine/voice_template.py)：服务层实现，返回合并后的 target；历史模板没有来源行时先补一条 `legacy-template:<id>` 的确定性来源，保证质心可重算。
- 接口 `POST /api/v1/voiceprints/{id}/templates/merge`（[voiceprint_templates.py](../../backend/src/imeeting/api/http/routes/voiceprint_templates.py)）：body `{source_template_id, target_template_id}`；鉴权走 `owned_voiceprint(write=True)`；写 `AuditLog`（`action=template_merge`）；404/409 区分不存在与口径拒绝。
- 明确不做自动合并：`GET /voiceprints/merge-suggestions` 仍只给建议，合并必须由人显式指定 target。

## 3. 验证

| 检查 | 结果 | 证据 |
|---|---|---|
| 后端完整回归 | 722 通过，0 失败；425.45 秒 | [backend-tests.xml](../reviews/evidence/2026-11-01-template-merge/backend-tests.xml) |
| 本轮新增回归 | 5 项 | [test_template_merge.py](../../backend/tests/test_template_merge.py) |
| Ruff（源码/测试/迁移） | 通过 | [verification.json](../reviews/evidence/2026-11-01-template-merge/verification.json) |
| 文档规制 | `docs_governance.py` 校验通过 | 同上 |

新增 5 项覆盖：来源改挂 + 质心重算 + 时长累计 + source 退役、同一组来源合并结果可重放、拒绝跨声纹/自合并/停用模板、主模板标记转移与目标已有主模板时保留、接口鉴权 + 审计 + 重放 409。

## 4. 规格与边界

- 合并是**破坏性**操作（source 退役），只由用户显式调用；没有自动合并、没有批量合并。
- 质心由来源行重算，因此合并前后同一组证据得到同一向量；但如果历史模板缺少来源行，补的那条 `legacy-template:<id>` 只是把当时的质心当作一条基线，不等于还原了当时的逐条证据。
- 合并后 target 的 `source_segment_id` 等展示字段保持 target 原值（不猜“代表片段”）。
- 前端本轮没有加合并入口；接口与审计字段已就绪。
- 部分唯一索引只在 SQLite 生效，与上一轮一致。

## 5. 仍未完成

- FR-5 最后一项：跨设备“重新注册”向导（P1）。
- R2 剩余：历史非法槽位人工整理、全库近邻合并的实际执行（`merge-suggestions` 只给建议）。
- R6：FunASR 按需导入（缺 torch 环境与组件清单）、ANN/跨语言。
- R0/R3/R4/R5 的真实环境验收；`spk.template_match_enabled` 继续默认关闭。
- 前端：合并入口与合并预览（相似度、来源条数）。

下一步建议：先做前端合并入口（复用 `merge-suggestions` 的建议列表），再做跨设备注册向导；FunASR 按需导入仍需带 torch 的环境与组件清单。

本轮按用户要求提交并推送，见本页之后的提交记录。
