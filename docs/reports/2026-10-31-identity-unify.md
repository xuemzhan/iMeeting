# 2026-10-31 身份统一续作：声纹与人员的永久关联

<!-- doc-governance: IM-RPT-005 -->
> 文档类型：报告；状态：阶段快照；日期：2026-10-31。
> 维护责任：待指派（iMeeting 维护者）。[文档中心](../README.md) · [验证证据](../reviews/evidence/2026-10-31-identity-unify/README.md) · [上一轮 R6 续作](2026-10-31-r6-optimization.md)。

本轮关闭修复报告里被两次点名的 P1 遗留项：**Person 与 Voiceprint 之间没有永久关联，只有过渡性的 speaker_key/姓名猜测**。做法是给声纹加一条可空的永久人员外键，并让绑定、确认、合并、删除四条生命周期路径维护它；历史数据只回填“唯一且同 owner”的部分，歧义保持空值等人工整理。

## 1. 这一项为什么算 P1

- REQ-001 说“人员本体是身份事实源”，但声纹只靠 `(meeting_id, speaker_key)` 和显示名去猜人：改名、合并、删除人员之后，声纹与人员的关系要么靠当场 key 兜住，要么直接断开。
- 上一轮报告在“规格与限制”和“仍未完成”里都写了“Person–Voiceprint 永久外键尚未建立；当前仍有过渡关联，不能宣称统一身份架构已完成”。本轮把这句话变成可验收的代码和测试。

## 2. 已实施变更

### 2.1 数据模型与迁移

- `Voiceprint.person_id`：可空、带索引、新库指向 `person.id` 的外键（[models.py](../../backend/src/imeeting/models.py)）。
- 迁移 [20261031_06](../../backend/migrations/versions/20261031_06_voiceprint_person_link.py)：
  - 给已有库补 `person_id` 列与 `ix_voiceprint_person_id` 索引；
  - **只回填无歧义关联**：同一 `(meeting_id, speaker_key)` 恰好命中一位 `active` 人员，且 `owner_id` 一致（或声纹是旧的无 owner 数据）。跨账号、无参会关系、多来源冲突一律留 `NULL`，不猜。
  - 降级删除索引与列（不触碰声纹本体与模板）。
- [db.py](../../backend/src/imeeting/db.py) 的幂等增量迁移同步补列，保证未启用 Alembic 的部署也能启动。

### 2.2 生命周期维护（四条路径）

| 动作 | 行为 |
|---|---|
| `persons.bind_speaker` | 绑定 speaker_key 后，把本场该 key 的声纹关联到该人员；owner 不一致时拒绝并记日志；改绑到新的正确人员时允许改挂 |
| `voice_match.apply_decision`（确认/转交） | 按 `(meeting_id, speaker_key)` 找参会人，建立关联；这是转写确认与冲突裁决两条入口共用的路径 |
| `persons.merge_persons` | source 名下声纹改挂到 target，不留悬空引用 |
| `persons.delete_person` | 解除关联但**保留声纹与模板**：删档案不等于销毁生物特征数据 |
| 声纹合并（`/voiceprints/{source}/merge/{target}`） | target 未关联时继承 source 的人员关联 |

### 2.3 读取与展示

`GET /api/v1/voiceprints/{id}/templates` 增加 `person` 字段（`{person_id, display_name}` 或 `null`）：人员改名后这里显示新姓名，`voiceprint.name` 不必被改写。前端本轮没有展示该字段（接口先行）。

### 2.4 明确没有做的授权改动

`identity_owner` / `visible_voiceprint_ids` 保持原有 owner 与会议作用域规则，**没有**把 `person.owner_id` 引入可见性 SQL：跨账号关联在写入时已被拒绝，若同时在读路径改口径，会让模板矩阵的作用域与旧数据不一致。这是有意的取舍，不是遗漏。

## 3. 验证

| 检查 | 结果 | 证据 |
|---|---|---|
| 后端完整回归 | 707 通过，0 失败；414.99 秒 | [backend-tests.xml](../reviews/evidence/2026-10-31-identity-unify/backend-tests.xml) |
| 本轮新增回归 | 8 项 | [test_voiceprint_person_link.py](../../backend/tests/test_voiceprint_person_link.py) |
| Ruff（源码/测试/迁移） | 通过 | [verification.json](../reviews/evidence/2026-10-31-identity-unify/verification.json) |
| 迁移 06 升级/回填/降级 | 通过（在最小旧表结构上直接执行） | 同上 |
| 文档规制 | `docs_governance.py` 校验通过 | 同上 |

新增 8 项覆盖：绑定建立关联、改名经人员透出、人员合并改挂、删除人员解绑但保留声纹与模板、跨账号拒绝、确认样本按 speaker_key 建链、声纹合并继承关联、迁移只回填无歧义行。

## 4. 规格与边界

- **老库没有数据库级外键**：SQLite 不能给已有表补外键约束，升级库只有列 + 索引，一致性由服务层保证；新建库（`create_all`）才有 `FOREIGN KEY(person.id)`。这一点必须如实写，不能宣称“数据库层已强制”。
- 历史歧义（跨账号、无参会关系、一人多 key）保持 `NULL`，需要人工在人员面板整理；本轮没有做“全库近邻合并”。
- 关联是**可改的**：同一段声音改判给别人时，声纹会改挂到新人员；这不影响已确认模板的 `source_key` 幂等口径。
- 删除人员只解除关联，声纹与模板保留；如需彻底销毁生物特征，要走声纹删除接口，这是两条不同权限的动作。
- 前端未消费 `person` 字段；未做 UI 文案与展示。

## 5. 迁移与回滚

- `20261031_06` 只新增列与索引，不重写表；`downgrade()` 删除它们，会丢失关联但保留声纹、模板与来源贡献。
- 本轮只在临时数据库验证升级/降级，**没有对用户现有数据库执行迁移**。
- 运行期回退不需要迁移：服务层对 `person_id IS NULL` 的行为与旧实现一致（读取时返回 `person = null`）。

## 6. 仍未完成

- R2 的 P1 能力：全库近邻合并、主模板（main template）语义、历史非法槽位人工整理。
- R3/R4/R5：真实供应商成本、浏览器与真实模型联测、固定语料校准与灰度；`spk.template_match_enabled` 继续默认关闭。
- R6：FunASR 按需导入（缺 torch 环境与组件清单）、ANN/跨语言。
- 目标环境多用户验收、TLS/Docker、长稳、漏洞扫描、生产库迁移与备份恢复。
- 前端：声纹页展示人员姓名与关联状态；本轮只提供接口字段。

下一步建议：先在目标库备份后跑一次 `20261031_06`，统计回填率与 `NULL` 的原因分布（跨账号/无参会关系），再决定是否需要一次人工整理工具；R2 的“主模板”需要先定义清楚“哪一条算主模板、替换时谁优先”，不适合在数据未整理前动。

本轮保留工作区原有修改，没有提交、发布或重启现有业务服务。
