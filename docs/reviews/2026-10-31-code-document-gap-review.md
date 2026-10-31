# 代码与文档对应性核查（2026-10-31）

<!-- doc-governance: FA-REV-003 -->
> 文档类型：评审；状态：阶段快照；内容基准日期：2026-10-31。
> 维护责任：待指派（FunASR / iMeeting 维护者）。[台账](../../../docs/governance/CATALOG.md) · [规则](../../../docs/governance/POLICY.md)。

## 结论

**代码与文档尚未形成一一对应的验收闭环。** 基础服务防护、音频缓冲和一部分会议可靠性能力已有实现及回归；人员本体与多场景声纹处于“数据结构、函数和部分界面已实现，跨模块流程及边界验收未完成”的阶段。现有测试通过不能支持“REQ-001/002 的全部 P0 已完成”。

最先处理两项安全问题：跨用户对象访问控制、管理员初始化口令写日志。随后修复人员和声纹状态一致性，再接通自动抽取和确认交互。Go/C++ 重写、向量索引及更激进的性能优化应放在这些事项之后。

具体实施见[修复与优化计划](../plans/2026-10-31-gap-remediation-plan.md)。本轮增加核查材料和隔离探针，业务实现保持本轮核查时的状态。

## 范围、证据与结论强度

- 文档范围：核查开始时台账中的 45 份文档，按“当前说明、历史记录、提案、证据”分别对待；两份新需求中的 **83 个编号 FR/AC 条目**逐项建立追溯记录。
- 深入代码范围：人员抽取/本体、声纹匹配/模板、相关 HTTP/WS 调用链、后台任务、前端入口、模型/迁移、服务边界、音频缓冲和顶层导入。
- 测试使用独立临时数据库、占位账号、合成向量与模拟 LLM，不加载真实模型，不读取生产会议库。源码以[文件哈希清单](evidence/2026-10-31-gap-review/source-manifest.json)定位；外层没有 Git 元数据，不能把该清单称为已发布提交。
- [逐条需求映射 JSON](evidence/2026-10-31-gap-review/requirements-traceability.json) / [CSV](evidence/2026-10-31-gap-review/requirements-traceability.csv)包含文档行号、实现位置、结论和对应 Gap；[文档级映射](evidence/2026-10-31-gap-review/document-traceability.json)覆盖 45 份台账材料。
- “已复现”表示探针击中了具体违约行为；“静态确认”表示已读调用方与实现；“待验收”表示没有本轮可用的目标数据/环境。没有对全仓所有函数作穷尽正确性证明，没有重验历史 GPU、线上发布、容器或长稳结论。

## 已有对应关系

| 领域 | 文档 → 实现 | 本轮结果与边界 |
|---|---|---|
| HTTP 密钥、请求体、上传时限和容量 | [P0 方案](../../../docs/production-optimization-plan.md) → [asgi_limits.py](../../../funasr/utils/asgi_limits.py)、REST 工厂及服务入口 | 对应边界测试通过；不推广到全部原生服务 |
| 安全解析与解压 | 同上 → [safe_parse.py](../../../funasr/utils/safe_parse.py)、[分卷解压](../../../portable/extract_from_split.py) | 请求/解析/归档相关测试通过 |
| Rust / Python 分块音频缓冲 | [音频报告](../../../docs/rust-audio-optimization.md) → [audio_buffer.py](../../../funasr/utils/audio_buffer.py)、[Rust 扩展](../../../runtime/rust/audio_buffer/README.md) | 缓冲测试通过；本轮未重测历史性能数字 |
| 人员模型、基本确认/绑定/别名 | [REQ-001](../requirements/REQ-001-person-ontology.md) → models、persons、persons 路由 | 普通路径已有测试；重名、改名传播和对象归属存在反例 |
| 场景桶、模板存储和冲突评估函数 | [REQ-002](../requirements/REQ-002-voiceprint-multiscene.md) → voice_template、voice_match、templates 路由 | 组件已实现；接线、状态、兼容和删除存在反例 |
| 参会人面板与声纹页 | 阶段记录 → [ParticipantsPanel](../../frontend/src/components/ParticipantsPanel.tsx)、[冲突列表](../../frontend/src/components/VoiceprintConflictList.tsx) | 前端 86 项测试及类型检查通过；这些不是 DOM / 完整交互验收 |
| 当前 Python/React 目录结构 | [project-structure](../architecture/project-structure.md) → `api / application / engine` 与前端目录 | 结构说明基本对应；Go/C++ 仍是提案 |

## P0：安全边界

### GAP-01：登录检查没有落实为人员/声纹对象的访问控制（已复现）

**证据：** [人员删除路由](../../backend/src/imeeting/api/http/routes/persons.py#L251)直接按人员 ID 删除，未比较请求者与 `Person.owner_id`；[合并路由](../../backend/src/imeeting/api/http/routes/persons.py#L220)只检查待合并两人是否同账号，没有检查请求者。模板列表、确认、冲突处理及重建也缺少相应归属校验，见 [voiceprint_templates.py](../../backend/src/imeeting/api/http/routes/voiceprint_templates.py#L105)。旧声纹 CRUD / relabel 同样需要纳入修复。

**触发与影响：** `REQUIRE_AUTH=1`，使用另一个普通用户的合法令牌，对他人的人员执行 DELETE 返回 **204**；读取他人的模板返回 **200**。因此这不是“匿名模式有意共享”的解释。匹配矩阵也没有 owner 参数，单靠补 HTTP 检查不能完成隔离。

**修复：** 统一 `owned_person / owned_voiceprint / owned_conflict`，会议关联使用现有 [owned_meeting](../../backend/src/imeeting/application/meeting_access.py#L20)。显式决定管理员和会议协作者权限；所有引用对象均需校验，包括重定向目标、conflict_id、来源会议/片段。查询与匹配缓存同步按授权作用域隔离。

**验收：** 普通用户 A/B、管理员、editor、viewer 的允许/拒绝矩阵；外人不能读改删或改变他人模板；关闭匿名模式也不能绕过。对应探针为 `test_foreign_person_cannot_be_deleted` 与 `test_foreign_templates_cannot_be_read`。

### GAP-12：管理员初始化把配置口令写入日志（已复现）

**证据：** [db.py:590](../../backend/src/imeeting/db.py#L590) 的 `_ensure_admin()` 在创建账号后使用日志格式 `username / password`。生产环境使用自定义 `ADMIN_PASSWORD` 时同样执行此分支。

**触发与影响：** 只模拟初始化分支并注入占位口令，日志中出现完整占位值。现实影响是初始化凭据进入应用日志、容器日志和日志备份；本轮未读取真实凭据。

**修复：** 只记录账号已创建与强制改密提示。对既有部署检查初始化日志的访问和保留范围；若实际口令曾被不应接触的人获取，执行凭据轮换。

**验收：** 自定义、默认、自动生成凭据均不出现在日志中；首次改密流程仍通过。探针 `test_bootstrap_does_not_log_administrator_password`。

## P1：业务链路和状态一致性

### GAP-02：自动人员抽取有配置和函数，没有生产调用链（静态确认）

**证据：** [person_extract.py:394](../../backend/src/imeeting/person_extract.py#L394)定义 `should_extract_on_final`，在运行源码中没有调用者。`extract_people` 只有[手动 HTTP 路由](../../backend/src/imeeting/api/http/routes/persons.py#L114)调用；开场 [meeting_meta](../../backend/src/imeeting/meeting_meta.py#L70)仍只回写字符串，没有接入人员本体抽取。AST 调用结果见[证据索引](evidence/2026-10-31-gap-review/README.md)。

**差距：** REQ-001 FR-1.1/1.2/1.3 的开场、定稿介绍和定时兜底未形成闭环；`person.extract_enabled=True` 的设置说明会让用户误以为功能已启用。`interval or 5` 还会吞掉合法的 0 值；设置说明称 0 关闭定时兜底，需求文本对关闭范围另有歧义。

**修复：** 增加去重的 `person_extract` 后台任务，开场/新增定稿/周期任务只负责投递。用会议级状态记录游标、次数、冷却和失败降级，避免每次读取全场历史；LLM 调用不占用转写处理。先统一 0 值语义。

**验收：** 不点按钮也能完成开场抽取及中途介绍；无新片段不重复调用；关开关、0 间隔、失败三次、重启恢复、费用上限均有断言。确认单纯的“识别到讲话人自动加占位参会人”不等于“自动抽取介绍姓名与属性”。

### GAP-03：未确认模板的作用域未被查询执行（已复现；模板匹配默认关闭）

**证据：** [templates_matrix:590](../../backend/src/imeeting/engine/voice_template.py#L590)只过滤 slot、替代状态和父声纹 `provisional`，把模板 `confirmed` 作为返回字段，却不用它限制作用域。

**触发与影响：** 正式父声纹下存在一条 `confirmed=False`、来源会议为 A 的模板，在会议 B 的匹配矩阵里仍可见。开启模板匹配后，未确认数据可能影响其他会议的身份判断。当前默认关闭开关限制了实时路径影响，不能因此跳过修复。

**修复/验收：** 明确正式模板、会议内候选、停用、替代四种状态；未确认模板仅允许来源会议可见，正式模板还须按 owner 授权。用跨会议、跨账号组合验证。探针 `test_unconfirmed_template_is_not_in_global_matching`。

### GAP-04：单条模板停用后无法重新启用（已复现）

**证据：** [set_template_flags:437](../../backend/src/imeeting/engine/voice_match.py#L437)只处理 `if disabled`，把 slot 变负；传 `disabled=False` 时没有恢复分支。路由说明同时承诺停用/启用。

**触发与影响：** 对同一模板先停用再启用，slot 仍为 **-1**，模板仍不参与匹配。

**修复/验收：** 优先把 `disabled` 独立为状态字段，不再让 slot 同时承担停用与替代语义；或实现有容量和冲突处理的恢复流程。覆盖停用—启用往返、槽位已满、已被替代不能误恢复。探针 `test_disabled_template_can_be_reenabled`。

### GAP-05：确认临时声纹只确认模板，没有转正身份（已复现）

**证据：** [apply_decision:335](../../backend/src/imeeting/engine/voice_match.py#L335)写 `confirmed=True` 模板，却没有更新父 `Voiceprint.provisional`。REQ-002 FR-4.10 / AC-9 要求确认后跨会议生效。

**触发与影响：** 对临时声纹执行 `confirm_self`，父身份仍为 `provisional=True`；与模板自己的确认状态冲突，不能据此认为已跨会议注册完成。

**修复/验收：** 在同一事务内完成身份转正、会议作用域迁移、确认来源与缓存更新；显式选择只确认样本或确认身份的 API 语义。探针 `test_confirming_provisional_identity_makes_it_formal`。

### GAP-06：同名不同单位的重复输入会持续新建人员（已复现）

**证据：** [find_person:190](../../backend/src/imeeting/persons.py#L190)发现基础展示名的单位冲突即返回 None；[upsert_person:331](../../backend/src/imeeting/persons.py#L331)因此再次创建带单位/序号的新展示名，没有先查之前已区分出来的实体。

**触发与影响：** 依次录入“李伟/单位甲”“李伟/单位乙”，再次输入“李伟/单位乙”得到第三个 Person：ID **1 → 2 → 3**。这会污染人员档案和参会关系，后续自动抽取会放大问题。

**修复/验收：** 把归一姓名、实体 ID、单位属性和展示后缀分离；先在同 owner 的候选集合内进行同名+单位匹配。不同单位不能合并，重复相同单位必须命中同一 ID。探针 `test_repeated_different_unit_person_is_idempotent`。

### GAP-07：证据校验仍可接受原文没有的姓名（已复现；也需要加强规范）

**证据：** [validate_people:212](../../backend/src/imeeting/person_extract.py#L212)检查姓名出现在模型返回的 quote 中；[quote_matches:114](../../backend/src/imeeting/person_extract.py#L114)允许 quote 与源片段 80% 字符重合。

**触发与影响：** 在一段较长、没有人名的真实来源文本尾部，给模型 quote 追加“我是赵六”，重合仍超过 80%；`赵六` 被 accepted。代码实现了字面上的两条规则，但规则组合不能保证 AC-3 的“姓名必须来自原文”。

**修复/验收：** 姓名必须在受信任源片段中直接出现或存在可定位字符跨度；宽松引文容错不能给新增实体提供证据。增加长 quote 插入/替换姓名、跨片段引用、标点、别名和合法证据的对照测试。探针 `test_fuzzy_quote_cannot_introduce_a_hallucinated_name`。

### GAP-08：模型结构化输出只在顶层校验，内部非法值仍抛异常（已复现）

**证据：** [person_extract.py:254](../../backend/src/imeeting/person_extract.py#L254)直接 `float(confidence)`；[clean_attributes](../../backend/src/imeeting/persons.py#L130)假定属性项为字典。无效值不是统一转为 declined/degraded。

**触发与影响：** `confidence="not-a-number"` 抛 ValueError。当前手动抽取路径可能返回 500；将其接入自动流程前必须完成故障隔离。

**修复/验收：** 用严格的响应 schema 验证嵌套字段、长度、枚举和有限数值；逐条丢弃坏候选，记录降级原因；输出错误不破坏转写/纪要。增加字符串、None、NaN/Inf、非对象属性等用例。探针 `test_malformed_llm_confidence_is_declined`。

### GAP-09：手动抽取限流与费用上限未落地（已复现）

**证据：** [extract_participants:114](../../backend/src/imeeting/api/http/routes/persons.py#L114)默认 `force=True`，直接进入 `manual=True`，没有 30 秒冷却和每场 10 次限制；自动计数还把 action 相同的手动调用一起算入。

**触发与影响：** 同账号连续两次抽取都返回 **200**，不是需求约定的第二次 429。前端 busy 只能限制一个页面按钮，不能限制并发客户端和脚本。

**修复/验收：** 在服务端原子预留调用额度，区分自动/手动、在途/失败和重试，明确 force 只绕过什么条件。覆盖并发、重启、跨页面和费用记账。探针 `test_repeated_manual_extraction_is_rate_limited`。

### GAP-10：人员、转写标签和运行中说话人状态仍是多套身份路径（已复现一处）

**证据：** [set_participant:531](../../backend/src/imeeting/persons.py#L531)改 Person 展示名和别名，没有同步其绑定片段；[bind_speaker:619](../../backend/src/imeeting/persons.py#L619)更新已有片段，但没有旧 relabel 路径中的活跃 resolver 通知。前端[绑定输入](../../frontend/src/components/ParticipantsPanel.tsx#L294)要求手填 `spk_xxx`。

**触发与影响：** 将绑定人员“张三”改为“张建国”，Person 更新，但已有 Segment 标签仍为 **张三**。确认、改名、重新绑定、旧声纹改名和未来定稿之间缺少统一协调。

**修复/验收：** 建立统一的会议身份应用服务，事务内更新本体/绑定/兼容字段/片段，提交后通知运行中会话并使前端缓存失效；界面从真实说话人列表选择，不让用户输入内部键。覆盖历史片段、后续定稿、解绑、改绑、多人共享会议。探针 `test_renaming_bound_person_updates_transcript`。

### GAP-11：同一来源的模板更新不是幂等的（已复现）

**证据：** [store_template:302](../../backend/src/imeeting/engine/voice_template.py#L302)每次对近似模板滚动混合，没有来源去重；会后重建反复调用该函数。已有 sample_ms 也不足以表示来源贡献集合。

**触发与影响：** 同一会议/片段的同一个向量连续处理两次，模板字节第二次继续改变。这不满足 REQ-002 FR-2.7；任务采用至少一次执行和重试时，某场会议可能被重复加权。

**修复/验收：** 保存来源贡献及唯一键，使用确定性的重建/替换聚合，或在重试时跳过已计入来源。要求同输入重复、任务重放和乱序重放结果一致；新的来源仍应更新。探针 `test_same_meeting_template_update_is_idempotent`。

### GAP-13：模板开关开启后，薄封装没有提供多维输入，兼容承诺不成立（已复现）

**证据：** [speaker.match_enrolled:213](../../backend/src/imeeting/engine/speaker.py#L213)只向 [voice_match.match:99](../../backend/src/imeeting/engine/voice_match.py#L99)传 embedding 与 meeting_id，没有 scenario、speech_ms、context。新分支还没有使用调用者传入的 threshold。

**触发与影响：** 一个模板、余弦 **0.70**、原阈值 **0.68**：旧路径命中正式姓名；新路径按缺省输入计算约 `0.85×0.70 + 0.05×0.5 = 0.62`，降为“疑似”。这不是“单模板逐位一致”。场景/时长/上下文函数存在，也不能说明实时链路已使用这些维度。

**修复/验收：** 定义完整的匹配上下文及阈值语义，贯通 resolver 到 matcher；无特征时明确兼容策略。再用同人/异人、不同设备、短语音和噪声数据校准。当前默认关闭模板匹配的做法应维持，不能直接为了宣称完成而打开。探针 `test_single_template_keeps_legacy_identity_result`。

### GAP-15：新增模板外键后，旧声纹删除路径没有同步适配（已复现）

**证据：** [delete_voiceprint](../../backend/src/imeeting/api/http/routes/voiceprints.py#L729)直接删除父声纹；模型/迁移创建了模板外键，数据库[启用 foreign_keys](../../backend/src/imeeting/db.py#L60)，没有相应级联删除或服务层清理。

**触发与影响：** 声纹已有模板后，所有者删除自己的声纹触发 **FOREIGN KEY IntegrityError**，而不是 204。旧的“无子记录”删除测试不能覆盖这个新增数据模型的回归。

**修复/验收：** 决定软删除或按依赖顺序清理模板、冲突及他人引用；合并声纹路径同时处理新子表。补迁移与旧库兼容测试、删除/合并回滚和两种匹配缓存失效。探针 `test_voiceprint_with_templates_can_be_deleted_by_its_owner`。

### GAP-16：核心确认交互未接入已有 relabel / 确认入口（静态确认）

**证据：** 前端 `confirmSample` 只在[API client](../../frontend/src/api/client.ts#L682)中定义，没有组件调用。旧 [relabel](../../backend/src/imeeting/api/http/routes/voiceprints.py#L373)没有进入 evaluate_confirm / apply_decision；[冲突列表](../../frontend/src/components/VoiceprintConflictList.tsx)只能显示已经产生的冲突，不能代替主确认入口产生并处理冲突。

**差距：** REQ-002 把转写确认、高冲突三选一列为 P0 核心；阶段记录却把主入口未接列为 P1。界面缺少需求要求的来源定位、场景差异和满槽位替换选择。REQ-001 的属性编辑、批量确认/合并与证据跳转也尚未齐备。

**修复/验收：** 统一主入口的评估—用户决定—原子提交协议；高冲突不能由旧 relabel 静默绕过。新增真实组件/浏览器测试，覆盖正确确认、冲突、取消、重定向、容量已满和失败回滚。若明确延期，先调整并评审需求范围，不用阶段摘要代替范围决定。

## P2：规范偏差与优化条件

### GAP-14：唯一 slot 不等于数据库限定最多五条（已复现，常规服务层已有上限）

[模型](../../backend/src/imeeting/models.py#L543)、[迁移](../../backend/migrations/versions/20261031_03_voiceprint_templates.py#L1)和阶段记录声称 `UNIQUE(voiceprint_id, slot)` 已构成数据库硬约束。但 slot 没有范围 CHECK，直接插入 **0、1、2、3、4、5** 六个不同 slot 可以成功。应用分配器限制五槽，不能支持 AC-11 的“绕过服务层也拒绝”。

修复时增加可兼容退役负 slot 的约束及迁移前检查，或改用明确的 active-slot 结构；同时说明五条指活跃模板，历史退役记录可超过五条。探针 `test_database_rejects_six_active_template_slots`。

### 应更新的规格，不宜机械地把代码改回旧设计

| 规范 / 表述 | 当前实现 | 建议 |
|---|---|---|
| REQ-002 FR-2.1 写 SNR≥10 | 阶段实测后改为默认 floor=0、质量排序、每人最多32段 | 保留有依据的调整，补决策并更新需求；确认样本另定质量门槛 |
| average linkage、farthest-first、按覆盖度替换 | 质心相似度聚类、超额合并近簇、存储时替换最低质量 | 写清采用算法和理由，用代表性数据比较后再决定是否升级 |
| 对齐阈值0.85与合并阈值0.92 | store 使用 `max(align_sim, merge_sim)`，实际对齐至少0.92 | 分开两个语义或合并为一个配置，补边界测试 |
| 自动模板有完整人工确认来源 | 会后对正式父声纹直接标 confirmed，可能无 confirmed_by；确认API写 sample_ms=0 | 明确继承信任和逐样本确认的区别，补来源与质量校验 |
| Person 名称仅 active 唯一 | 全状态 UNIQUE，删除/合并后仍占名 | 明确历史标识与展示名政策，再实施部分索引或稳定归一键 |
| 候选低于0.4默认隐藏 | confidence_floor未读，前端只过滤 rejected | 区分“讲话人占位候选”与“低可信语义抽取”，统一展示与确认规则 |
| 转交样本不得入正式模板库（FR-4.6） | apply_decision 会写入目标人的正式模板；AC-5又要求归入目标声纹 | 产品规格自身有冲突，需要明确取消/错误身份/正确目标三种语义 |
| 人员本体是统一事实源 | meeting_meta/backfill 与 persons 各自写 participants；Voiceprint.person_id 属于延期对接 | 统一兼容字符串投影的写入者，保持跨模块一致 |

### 性能优化顺序

1. **先测量重建成本与队列影响。** 会后模板挂在 final_minutes 复合任务之后，耗时仍占 heavy worker；失败只记日志。应拆成独立可重试任务，保存进度，复用同一来源嵌入并保证幂等，再比较纪要耗时、模板完成耗时、队列等待和CPU。
2. **再做匹配查询与缓存优化。** 当前先算全矩阵再筛选；按 meeting_id 缓存完整矩阵的字典没有容量淘汰。先修作用域与确认过滤，再选版本化、有限容量的缓存和真正先筛后算。未经规模/延迟数据，不直接引入 ANN 索引。
3. **按原提案推进导入优化。** [funasr/__init__.py](../../../funasr/__init__.py#L78)仍调用全量 import_submodules；[register.py](../../../funasr/register.py)重名注册仍覆盖。这说明 10-18 提案尚未完整实施，不是该提案承诺已完成后的回归。先建立模型注册清单和基础环境/训练插件兼容测试，再改变导入行为。
4. **跨语言重写仍保留为提案。** 目前没有新的目标硬件测量证明 Go 网关能改善端到端延迟；先关闭身份、安全和任务正确性问题，再评估 C++ 推理侧车。

## 测试为什么没有挡住

现有测试主要覆盖单模块普通路径。例子包括：只测试停用、不测试重新启用；只测同名冲突第一次创建、不测重复输入；只测简单伪引文、不测长引文插入姓名；只测管理员读取自己的记录；只测无模板父声纹删除。

[test_interval_disabled_blocks_auto](../../backend/tests/test_person_ontology.py#L437)仅断言返回值属于 `(True, False)`，没有设置 interval=0，也没有验证是否发生自动调用，不能保护它的测试名称所描述的行为。前端测试不等于真实组件/浏览器确认流程；缺少的正是“用户动作是否触发正确后端和状态变化”。

本轮新增[显式审计探针](../../backend/tests/audit_doc_code_gaps_20261031.py)，采用规范期望做断言。已知缺口用 strict xfail 标识，文件名不进入默认回归集合；用 `--runxfail` 可看到实际违约失败。修复对应功能后应把探针转成正式回归测试，不能把 expected failure 当作已验收。

## 实际验证结果

| 检查 | 结果 | 解释 |
|---|---|---|
| iMeeting 人员、模板、生产回归、身份与重要程度 | 126 passed，32 warnings | 现有回归通过；不包含新增缺口断言 |
| FunASR 请求/存储/解析/音频/解压/GGUF/顶层导入 | 51 passed，1 warning | 本机选定边界检查通过，未重验所有runtime |
| iMeeting 前端 | 86 passed，11个测试文件 | 组件流程仍需浏览器验收 |
| 前端类型检查 | 退出码0 | 不等于交互正确 |
| 审计探针 | 15 failed、1 passed、4 warnings；见[原始失败记录](../reviews/evidence/2026-10-31/gap-probes-observed.xml) | 规范断言的实际失败用于确认缺口；正向证据对照通过 |

命令、最终探针计数及证据位置见[证据索引](evidence/2026-10-31-gap-review/README.md)。原有后端警告保留，未重跑漏洞库扫描；本轮没有声称 GPU、Docker/TLS、24小时长稳、目标并发或真实语料95%识别率通过。

## 对“是否一一对应”的最终判断

设计—代码—测试是多对多关系，需要用需求/验收编号追溯，不能按文件同名或函数存在判断完成。当前同时存在已实现且有边界回归、函数已写但未接线、流程已接但状态有缺陷、原规格已被实测调整、明确延期的设计，以及没有目标环境证据六种情况。后续应按计划逐项关闭 Gap 和验收条件，避免给出无法复核的全仓完成百分比。
