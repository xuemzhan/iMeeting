# 2026-10-31 模板治理续作：主模板（pinned）

<!-- doc-governance: IM-RPT-006 -->
> 文档类型：报告；状态：阶段快照；日期：2026-10-31。
> 维护责任：待指派（iMeeting 维护者）。[文档中心](../README.md) · [验证证据](../reviews/evidence/2026-10-31-template-primary/README.md) · [上一轮身份统一](2026-10-31-identity-unify.md)。

REQ-002 FR-5 要求“手动选择主模板（`pinned`）”，接口草案也把“设为主模板”列进了模板 PATCH。上一轮报告把它列为 R2 最后一项未完成能力；本轮按需求实现，并把“主模板不被自动淘汰挤掉”做成可回归的保护规则。

## 1. 规格依据

- [REQ-002 FR-5](../../docs/requirements/REQ-002-voiceprint-multiscene.md)：模板治理需支持手动停用/启用、合并两条模板、**手动选择主模板（`pinned`）**；接口草案 `PATCH /api/v1/voiceprints/{id}/templates/{tid}` 明确“设为主模板”。
- 本轮只做主模板选择与保护，**没有做**“合并两条模板”和“跨设备重新注册向导”，见第 6 节。

## 2. 已实施变更

### 2.1 数据模型与迁移

- `VoiceprintTemplate.pinned`（默认 `false`，带索引）。
- 迁移 [20261031_07](../../backend/migrations/versions/20261031_07_template_pin.py)：补列与索引；在 SQLite 上建**部分唯一索引** `uq_voiceprint_template_pinned ON voiceprint_template(voiceprint_id) WHERE pinned = 1`，保证“每人最多一条主模板”由库层兜底；降级删除索引与列。
- [db.py](../../backend/src/imeeting/db.py) 的幂等增量迁移同步补列/索引，未启用 Alembic 的部署也能启动。
- 只在 SQLite 建部分唯一索引：别的方言上“一个 voiceprint_id 一个唯一索引”会禁止其余四条模板，因此其它方言退化为服务层规则。

### 2.2 选择与保护规则

| 场景 | 行为 |
|---|---|
| `pinned=True` | 只有活跃模板（未停用、未被替代、`slot≥0`）能当主模板；同一人的其它主模板先取消，再置位（先 `flush` 已清空的状态，避免部分唯一索引的中间态冲突） |
| `pinned=False` | 直接取消，不影响模板可用性 |
| 停用主模板 | 拒绝（409），要求先取消主模板；避免“治理动作顺手把主模板弄没了” |
| 自动淘汰（满槽写入） | 主模板**不参与**淘汰，只在非主模板里挑质量最低的；`_weakest_slot` 同步排除 |
| 显式指定槽位替换 | 若目标槽位是主模板则拒绝（明确报错），必须先用 `pinned=false` 取消 |
| 读取 | `list_templates` 增加 `pinned`、`primary`、`primary_source`：有主模板时 `primary_source=pinned`；没有时按质量推导一个候选（`quality`），不与“用户选定”混为一谈 |

`primary_source` 是刻意加的：页面上“用户选的主模板”和“系统按质量推荐的候选”不是同一件事，不能只给一个布尔值让调用方猜。

### 2.3 接口

`PATCH /api/v1/voiceprints/{id}/templates/{tid}` 新增 `pinned` 字段，响应返回 `pinned`；非法操作返回 409（`ValueError` 映射），不做静默修正。

前端 [VoiceprintTemplateSummary](../../frontend/src/components/VoiceprintTemplateSummary.tsx) 同步接入：模板行显示“主模板”/“推荐”标记与“设为主模板/取消主模板”操作，折叠标题带上声纹关联的人员姓名（`person.display_name`）。

### 2.4 需求文档同步

- FR-5 原写“删除 Person 时级联删除模板与冲突记录”，与上一轮的实现决策（删除人员只解除关联、保留生物特征）冲突。本轮把该条修订为“解除关联并保留声纹/模板，销毁须走声纹删除接口”，并写明修订理由。
- 数据模型草案补 `pinned BOOL default 0`。

## 3. 验证

| 检查 | 结果 | 证据 |
|---|---|---|
| 后端完整回归 | 715 通过，0 失败；404.82 秒 | [backend-tests.xml](../reviews/evidence/2026-10-31-template-primary/backend-tests.xml) |
| 本轮新增回归 | 8 项 | [test_voiceprint_template_pin.py](../../backend/tests/test_voiceprint_template_pin.py) |
| Ruff（源码/测试/迁移） | 通过 | [verification.json](../reviews/evidence/2026-10-31-template-primary/verification.json) |
| 迁移 07：列、部分唯一索引、重复主模板被拒、降级 | 通过（在 03 建出的旧表结构上执行） | 同上 |
| 前端类型检查 / 单元测试 / 生产构建 | 通过；89 项单元测试、构建成功 | 同上 |
| 文档规制 | `docs_governance.py` 校验通过 | 同上 |

新增 8 项覆盖：置位时取消其它主模板、无主模板时按质量推导 primary、只能选活跃模板、主模板不能直接停用、满槽自动淘汰跳过主模板、显式替换主模板槽位被拒、PATCH 接口置位/取消/非法 409、迁移部分唯一索引。

## 4. 规格与边界

- 主模板保护只覆盖**自动**动作（满槽自动淘汰、自动更新对齐）；显式替换/停用会返回明确错误要求先取消，不会“帮用户决定”。
- `primary`（按质量推导）是只读展示候选，不影响淘汰顺序与匹配；匹配仍然使用全部活跃模板。
- 部分唯一索引只在 SQLite 生效（本项目部署是单实例 SQLite）；PostgreSQL 等方言下“每人一条主模板”由服务层保证，不能声称库层强制。
- 前端只做了模板摘要行内的标记与操作，没有做独立的模板治理页面；停用主模板仍需先取消，前端会显示服务端返回的 409 文案。
- 未做“合并两条模板”、跨设备重新注册向导；这两项仍是 FR-5 的未完成部分。

## 5. 迁移与回滚

- `20261031_07` 只新增列与索引，不重写表；`downgrade()` 删除部分唯一索引、普通索引与列。
- 本轮只在临时数据库验证升降级，**没有对用户现有数据库执行迁移**。
- 运行期回退不需要迁移：所有模板 `pinned=false` 时行为与上一轮一致（`primary` 退回按质量推导）。

## 6. 仍未完成

- FR-5 剩余：合并两条模板、跨设备重新注册向导（P1）。
- R2 剩余：历史非法槽位人工整理、全库近邻合并的实际执行（`GET /voiceprints/merge-suggestions` 只给建议）。
- R6：FunASR 按需导入（缺 torch 环境与组件清单）、ANN/跨语言。
- R0/R3/R4/R5 的真实环境验收：多用户协作、真实供应商成本、浏览器与真实模型联测、固定语料校准与灰度；`spk.template_match_enabled` 继续默认关闭。

下一步建议：先做“合并两条模板”（需要先定清楚合并后 slot、来源贡献与 confirmed 归属），再评估 FunASR 按需导入；前端模板治理页（批量操作、相似度热力图）按需补。

本轮保留工作区原有修改，没有提交、发布或重启现有业务服务。

接续说明：本轮之后用户要求提交并推送，前四轮报告正文里的“未提交/未发布”描述的是各自轮次结束时的状态，不代表提交前的最新状态。
