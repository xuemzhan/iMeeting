# 2026-11-02 剩余问题实施方案：取数架构收敛、测试补齐、迁移收敛（v2）

> 文档类型：实施方案；状态：**v2，已按[独立审核意见](../reviews/2026-11-02-remaining-plan-review.md)逐条修订，待实施**；日期：2026-11-02。
> 上承 [连续优化报告](../reports/2026-11-02-continuous-optimization.md)（IM-RPT-010）§3 的「仍未完成」清单；
> 关联 [手机/平板自适应方案](2026-11-01-responsive-ui-adaptation.md)（IM-PLN-001）、[Gap 修复计划](2026-10-31-gap-remediation-plan.md)。
> v1 的 4 条阻塞项（B1 重挂载、映射漏项、A2 取值语义、P5 覆盖目标）已在本文修正；对照表见 §8。

## 0 摘要

剩余工作分两类，不混谈：

- **工程债**：能定位到文件与行、能独立验收、不需要任何人拍板。9 处 `react-hooks/set-state-in-effect` 与未覆盖的录音链路、历史页、转写流、会中页属此类。
- **决策债**：改名语义、一人多 speaker_key、自动身份门槛值、迁移三处并存。改代码即改产品语义或数据结构，只给选项与推荐。

预估（v2 已按审核上调）：P1–P4 **8.0 人日**（原 5.0），P5 **5.0**，P6 **4.0**；P7 视决策。区间按 ±40% 理解。

## 1 现状证据（可复跑）

| 口径 | 现状 | 命令 |
|---|---|---|
| eslint | 9 条 warn，全部 `react-hooks/set-state-in-effect` | `cd frontend && npm run lint` |
| 前端覆盖率 | 行 **787/3742 = 21.03%**；语句 20.22 / 函数 13.69 / 分支 16.16 | `npm run test:coverage && npm run coverage:check` |
| 前端测试 | 167 passed / 19 文件 | `npm test` |
| e2e | 4 个 spec：`auth`、`identity-confirmation`、`participant-controls`、`responsive` | `frontend/e2e/` |
| 后端覆盖率 | 81.20%（11148 条语句 / 93 文件） | `pytest -q --cov=imeeting --cov-report=json:coverage.json` + `python scripts/coverage_ratchet.py` |
| 后端测试 | 785 passed | `pytest tests -q` |

**未覆盖部分**（不是「零测试文件」，v2 修正表述）：

| 文件 | 可覆盖行 | 已覆盖 | 说明 |
|---|---|---|---|
| `store/session.ts` | 203 | 88 | 已有 `session.test.ts` 20+ 项；缺的是 `start`/`stop` 录音链路 |
| `components/TranscriptStream.tsx` | 76 | 0 | 滚动逻辑已有 `lib/transcriptScroll.test.ts`；组件本体未测 |
| `pages/Meeting.tsx` | 128（+8） | 0 | — |
| `pages/History.tsx` | 32 | 0 | — |
| 待迁移的 7 个 hook/组件 | 390 | 0 | A1/A2/A4/A5/B1/B2/C1 |

## 2 9 处告警的正确分类（v2 修正）

v1 把所有告警归为「effect 里取数」，不准确。按性质分两组，修法不同：

| 组 | 性质 | 条目 | 条数 |
|---|---|---|---|
| I | effect 体里发起取数或调用了会 `setState` 的函数 | A1 `useVoiceprintAdmin:39`、A2 `useConfigEditor:52`、A5 `useParticipants:88`、B2 `VoiceprintConflictList:39`、C1 `useSharedMeeting:43`、B1 第二条 effect `VoiceConfirmationDialog:46` | 6 |
| II | 随数据/道具变化调整本地 state（不是取数） | A3 `useHistoryMeetings:87`、A4 `useSpeakerRoles:84`、B1 第一条 effect `VoiceConfirmationDialog:37` | 3 |

组 I 的规则结论（已实测，勿按老说法做）：规则抓的是「effect 体内调用**任何最终含 `setState` 的函数**」，与 `await` 边界无关——派生 loading 不消警告（IM-RPT-010 §3.3）。
组 II 不需要 `useQuery`，只需要「派生」或「身份戳」。

## 3 组 I：取数迁出 effect

### A1 `useVoiceprintAdmin.ts:39`

```ts
const qc = useQueryClient()
const printsQuery = useQuery({ queryKey: ['voiceprints'], queryFn: () => voiceprintApi.list() })
const suggestionsQuery = useQuery({
  queryKey: ['voiceprint-suggestions'],
  queryFn: () => voiceprintApi.suggestions(),
  enabled: printsQuery.isSuccess,
})
const prints = printsQuery.data ?? []
const loading = printsQuery.isLoading
const load = () =>
  Promise.all([
    qc.invalidateQueries({ queryKey: ['voiceprints'] }),
    qc.invalidateQueries({ queryKey: ['voiceprint-suggestions'] }),
  ]).then(() => undefined)
```

- `ok`/`error` 是操作级反馈，**继续留本地 state**，不要塞进 query。
- 设备：`refreshDevices()` 与 `load()` 在同一个 effect 里，拆掉一个另一个仍在 → 一并处理。方案定稿为 `useQuery(['audio-devices'])`，`queryFn` 内 `await refreshDevices()` 后返回 `useSession.getState().devices`；store 仍是 devices 的唯一真相，query 只负责「何时拉、怎么失效」。
- **风险（v2 订正）**：仓库里没有 `['voiceprints']` 键（`grep queryKey` 只出现 `meetings`/`meeting`/`minutes`/`mindmap`/`reviews`/`knowledge-*`/`meeting-info`/`shared-meeting`）。所以不是「怕撞键」，而是**新建共享缓存后必须手工接线失效**：`VoiceprintMergePanel` 合并成功、`VoiceprintTemplateSummary` 重建完成、冲突裁定之后，都要 `invalidateQueries(['voiceprints'])`，否则列表不再像以前那样每次进页面就重拉。

### A2 `useConfigEditor.ts:52`（本次改动最需小心）

```ts
const schemaQuery = useQuery({ queryKey: ['config-schema'], queryFn: () => configApi.schema(), enabled })
const base = schemaQuery.data ?? null
const [draft, setDraft] = useState<Record<string, unknown>>({})   // 只存用户改过的键
const baseValues = useMemo(() => flattenValues(base), [base])     // 扁平化成 key -> value
const values = useMemo(() => ({ ...baseValues, ...draft }), [baseValues, draft])
```

- **取值必须靠「键是否存在」，不能用 `??`**：配置项里的 `false`、`0`、`''` 都是合法值也是假值，`??` 会让它们回落到基值（v1 的写法是错的）。
- `changed` 改为：`Object.keys(draft)` 里「与基值不同」的键；不要再用 `schema` 全量遍历比较。
- `save()` 成功后 `qc.invalidateQueries(['config-schema'])` + `setDraft({})`；`reset()` 即 `setDraft({})`。
- **必须同步改的 3 个调用点**（v1 漏了，不改就是整页空白输入框）：
  - `components/ConfigGroupFields.tsx:40` 的 `value={draft[f.key]}` → 改收 `values`；
  - `components/SettingsBackendConfig.tsx:91` 的 `draft={cfg.draft}` → 传 `cfg.values`；
  - `pages/Admin.tsx`（后台配置页）同源改造。
- 导出契约新增 `values` 与 `valueOf(key)`，旧字段 `draft` 保留但注明「只含改动」。

### A5 `useParticipants.ts:88`（v1 漏项，本次补上）

```ts
const key = ['participants', meetingId]
const participantsQuery = useQuery({
  queryKey: key,
  queryFn: () => personApi.participants(meetingId as number),
  enabled: meetingId != null,
})
const items = meetingId == null ? NO_ITEMS : (participantsQuery.data ?? NO_ITEMS)
const loading = participantsQuery.isLoading
const reload = () => qc.invalidateQueries({ queryKey: key })
```

- `patch` 的乐观更新从「改本地数组」搬到 `qc.setQueryData`：先存快照，失败回滚到快照，成功用服务端返回值覆盖。
- 已有的那条真缺陷修复（`extract` 的「额度用完 / 转写不足」必须活过 `reload`）**顺序不变**：先 `await reload()`，再 `setError(skipped)`。
- 现有 12 项测试全部继续有效；`mounted` ref 可以删掉（query 自己处理卸载）。

### B1 `VoiceConfirmationDialog.tsx:46`（模板取数那条）

`targetTemplates` / `targetReady` 改 `useQuery(['voiceprint-templates', target], { enabled: Boolean(target) })`，`targetReady = query.isSuccess`，不再用三个 state 承接。

### B2 `VoiceprintConflictList.tsx:39`

`useQuery(['voiceprint-conflicts'])`；`decide` 成功后就地 `setQueryData` 过滤该行，再 `onChanged()`。`loading` 取 `isLoading`，`busyId` 留本地。

### C1 `useSharedMeeting.ts:43`

`shareApi.open()` 是两种动作，拆开：

```ts
const auto = useQuery({
  queryKey: ['share-open', token],
  queryFn: () => shareApi.open(token, ''),
  enabled: Boolean(token),
  retry: false,
  gcTime: 0,          // 凭据不留在内存里，见审核 S3-1
})
const submit = useMutation({ mutationFn: (pwd: string) => shareApi.open(token, pwd) })
const accessToken = submit.data?.access_token ?? auto.data?.access_token ?? ''
const opening = auto.isFetching || submit.isPending
const authError = submit.isError || auto.isError ? '…' : null
```

改完顺带删掉 `useSharedMeeting.ts:45` 的 `eslint-disable`。

## 4 组 II：不取数，只调整状态

### A3 `useHistoryMeetings.ts:87`（v2 换了方案）

v1 打算改成「删除后就地回退页码」，但那会丢掉「他人删空 / 跨页操作导致当前页为空」的回退（审核 S2-2），还要改 `useHistoryBulkActions` 的 `refresh` 签名（S2-3）。**改为派生页码，两个问题一起消失**：

```ts
const [requestedPage, setPage] = useState(0)
const totalPages = Math.max(1, Math.ceil(total / pageSize))
const page = Math.min(requestedPage, totalPages - 1)      // 派生的安全页
// queryKey 用 page（不是 requestedPage）
```

收敛性：`page` 越界 → 用末端页取数 → `total` 更新 → `totalPages` 稳定 → `page` 稳定，最多多一次请求，不会来回跳。effect 删除，`refresh` 签名不变，`useHistoryBulkActions` 不用动。

### A4 `useSpeakerRoles.ts:84`

覆盖层 + 派生基线，且**合并结果必须 `useMemo`**（v1 漏了，见审核 S2-4）：

```ts
const baseSpeakers = apiSpeakers.length > 0 ? apiSpeakers : (initial ?? [])
const baseImportance = useMemo(() => importanceMapFromApi(baseSpeakers), [baseSpeakers])
const effectiveImportance = useMemo(
  () => ({ ...baseImportance, ...importanceByKey }),
  [baseImportance, importanceByKey],
)
const adopt = useCallback((list: MeetingSpeaker[]) => {
  setApiSpeakers(list)
  setImportanceByKey({})   // 服务端已是权威，覆盖层清空
  setStateByKey({})
}, [])
```

失败回滚简化为「删键」。第二个 effect（`:82-85`）删除；第一个 effect（`.then(adopt)`）保留——回调里 setState 不算 effect 体同步 setState，这一处此前也未被报。

### B1 `VoiceConfirmationDialog.tsx:37`

**不重挂载**（v1 的 `key` 写法不成立，见审核 S1-1：`App.tsx:37` 处该组件无 props，队列在自身 ref 里，重挂载会让 cleanup 把排队请求全判 cancel）。改为带身份戳的派生：

```tsx
const currentId = current?.confirmation.id ?? ''
const [picked, setPicked] = useState<{ id: string; target: string } | null>(null)
const target = picked?.id === currentId ? picked.target : String(current?.confirmation.assessment.other?.voiceprint_id ?? '')
const setTarget = (value: string) => setPicked({ id: currentId, target: value })
// slot 同理
```

## 5 P5 未覆盖部分

| 顺序 | 对象 | 要点 |
|---|---|---|
| 1 | `session.ts` 的 `start`/`stop` | **不新建抽象**（v1 想加的 `RealtimeFactory` 是多余的，见审核 S2-8）：`vi.mock('../audio/recorder')` + `vi.mock('../api/ws')`，两个缝已存在于 `session.ts:4/7` |
| 2 | `TranscriptStream` | 桩 `scrollHeight`/`clientHeight`；锁自动滚动、手动上滚后不强制滚动、空态文案 |
| 3 | `History` | `renderHook` + `QueryClientProvider`；锁 A3 的派生页码（删空当前页 → 自动落到末页） |
| 4 | `Meeting` | 只测决策分支（能力检查、无转写不许生成、录音中离场提示） |

`start/stop` 要锁的行为：start 幂等；stop 等最后一块 chunk 发完才转 `processing`；`getUserMedia` 被拒回到 `idle` 且给可读错误；切会议 `close()` 恰好一次。

**覆盖率口径（v2 修正，v1 的 30% 算术不可达）**：

| 里程碑 | 目标 | 依据 |
|---|---|---|
| P1–P4 后 | 全局行覆盖 **≥ 23%**（+75 行） | 迁移的 7 个文件共 390 行零覆盖 |
| P5 后 | 全局行覆盖 **≥ 27%**（需 1010 行，+223）+ 四个文件**各自 ≥ 60%** | 四个文件可覆盖行合计 359，全量覆盖上限仅 30.6% |

每批完成跑 `npm run coverage:baseline` 抬门槛。

## 6 决策债与迁移

### D1 改名语义（frontend 调用点是 **3 处**，v1 说 2 处）

现状：`learn` / `rename_identity` / `bind_person` 默认全 `True`（`api/http/routes/voiceprints.py:311`）。三个选项：D1-a 现状；D1-b 默认只改本场；**D1-c（推荐）按来源分流**——补一个 `intent` 参数，弹窗始终展示当前选择。
调用点：`useMeetingDetail.ts:78`（改片段说话人）、`:99`（取消确认）、`store/session.ts:404`（**录音中改名，走事件流，默认值要单独定**）。

### D2 一人多 speaker_key

四步：关联表 → 双写 → 读路径聚合 → SQLite 重建表（无 `DROP CONSTRAINT`）+ 迁移 `20261102_02` + 实库副本双路径验证。2–3 人日 + 一次演练。建议先不做（`merge-speakers` 已覆盖「同人两簇」）。

### D3 门槛值

`spk.auto_identity_min_segments=3`、`spk.auto_identity_min_ms=3000`（`settings.py:861/874`）。校准方法：`bench_voice_templates.py` 加 `--sweep-min-evidence`，按（段数, 毫秒）网格出「误停用率 / 噪声拦截率」，取误停用率 < 2% 下最优。

### E1 迁移收敛（**顺序已换**，v1 自相矛盾）

| 步 | 动作 | 出口条件 |
|---|---|---|
| E1-1 | **先做**：CI 加 Postgres service job，跑 `upgrade head → downgrade base → upgrade head` | 往返成功、结构哈希一致 |
| E1-2 | 再对现有 10 个版本跑 `revision --autogenerate`，把 diff 归零 | 空 diff，或逐条写明「故意不迁移」；**SQLite 上的 diff 不可信**（无 ALTER、无 server_default 比较），一律以 Postgres 为准 |
| E1-3 | `db.py` 引入 `DB_SCHEMA_MODE=alembic\|legacy`，默认 `legacy` | 老库升级与新库初始化两条路径都过 |
| E1-4 | 下掉兼容 DDL 分支 | 连续两个版本无回退 |

### E2 需要环境的验收项

真实语料阈值校准 / 目标服务器 compose 与 TLS / iOS Safari 与 Android Chrome 真机 / 24h 长稳与断网续传 / FunASR 按需导入。口径同 v1 §5.2。

## 7 执行顺序、门禁与回滚

| 批次 | 内容 | DoD（除 lint 归零） | 预估 |
|---|---|---|---|
| P1 | A3（派生页码）、A4（覆盖层 + `useMemo`） | 现有 167 项不失败；`responsive` e2e 通过；`git diff` 无与告警无关的行为改动 | 1.5d |
| P2 | A1、A2、A5（一次把 queryKey 表立起来） | 新增测试 ≥ 6 项；`ConfigGroupFields`/`SettingsBackendConfig`/`Admin` 三处改造完成且有渲染测试 | 3.0d |
| P3 | C1、B2 | `identity-confirmation` e2e 通过 | 1.5d |
| P4 | B1（身份戳派生，不重挂载） | `identity-confirmation` e2e 通过；确认队列连续两条不丢 | 2.0d |
| P5 | 四个未覆盖部分 | 全局 ≥ 27% 且四文件各自 ≥ 60%；基线抬高 | 5.0d |
| P6 | E1-1 → E1-4 | Postgres 往返 CI 绿 | 4.0d |
| P7 | D1/D2/D3 落地 | 按各决策项 | 视决策 |

硬门禁（每批必跑，v2 已加 e2e）：

```powershell
cd frontend; npm run lint; npm run typecheck; npm test; npm run build
cd frontend; npm run test:coverage; npm run coverage:check
cd frontend; npx playwright test e2e/identity-confirmation.spec.ts e2e/responsive.spec.ts
cd backend;  & ..\.venv-production-review\Scripts\ruff.exe check src/imeeting tests migrations scripts
cd backend;  & ..\.venv-production-review\Scripts\python.exe -m pytest tests -q
cd backend;  & ..\.venv-production-review\Scripts\python.exe "..\docs\reviews\evidence\2026-11-01-comprehensive-review\probes\check_links.py"
```

## 8 与审核意见的对照

| 审核项 | 本文处置 |
|---|---|
| S1-1 B1 重挂载不成立 | §4 改为身份戳派生，明确「不重挂载」及原因 |
| S1-2 漏 `useParticipants` | 新增 A5（§3），9 条归属齐了：组 I 6 条 + 组 II 3 条 |
| S1-3 A2 取值语义 + 3 个调用点 | §3 A2 用「键是否存在」语义，列出 3 处必改点 |
| S1-4 覆盖目标不可达 / 表述不准 | §1 改「未覆盖部分」，§5 改口径（全局 27% + 各自 60%） |
| S2-1 缓存键风险方向错 | §3 A1 改为「手工接线失效」 |
| S2-2 A3 丢他人删除场景 | §4 改派生页码，两问题同时消失 |
| S2-3 A3 波及 `useHistoryBulkActions` | 同上，`refresh` 签名不变 |
| S2-4 A4 缺 `useMemo` | §4 已加 |
| S2-5 E1 顺序矛盾 | §6 已换序（E1-1 先做 Postgres CI） |
| S2-6 门禁缺 e2e | §7 硬门禁含 e2e，且 P1/P3/P4 的 DoD 各自挂 spec |
| S2-7 D1 调用点 2 → 3 | §6 已改，并标注 `session.ts` 那处是录音中改名 |
| S2-8 P5 多造抽象 | §5 改为 `vi.mock` 现有两个模块 |
| S2-9 缺 DoD | §7 表增加 DoD 列 |
| S3-1 凭据滞留 | C1 加 `gcTime: 0` |
| S3-2 结论需可证伪 | 列入 P2 的补充项（不进 DoD）：加一个最小复现夹具，让「派生 loading 不消警告」这条结论可被一行命令证伪 |
| S3-3 估时上调 | P1–P4 由 5.0 → 8.0d |
| S3-4 P1 合并覆盖层验证 | 未采纳：A4 与 A2 分属组 II / 组 I，合并会让 P2 同时改 3 个调用点，风险集中；改为 P1 只验模式、P2 再验跨文件契约 |

## 9 本次修订所做的决定（三处，均可回退）

审核要求这三处由方案作者或产品确认，本文按下列默认定稿：

1. **A3 用派生页码，而不是事件后回退**。理由：同时覆盖「他人删空」与「跨页操作」，且不改 `useHistoryBulkActions` 契约。若担心 queryKey 抖动，回退方案是「保留极窄 effect」并接受那条告警。
2. **B1 用身份戳派生，而不是把队列提到 App 级**。理由：队列与该组件的挂载生命周期绑定，提级要搬事件监听与 resolve 契约，改动面更大。
3. **覆盖率用「全局 27% + 四文件各 60%」双口径**。理由：单看全局百分比会被分母稀释，单看文件会漏掉回归。

仍需产品拍板的（本文不代决）：D1 的默认语义、D2 是否放宽、D3 的门槛取值。

## 10 不做什么

- 不关规则、不加 `eslint-disable`；唯一现存豁免（`useSharedMeeting.ts:45`）在 C1 里消失，不新增。
- 不引入第二套取数库。
- 不在 Postgres CI（E1-1）之前动 `db.py` 或迁移文件。
- 不建 `src/lib/realtime.ts` 之类的新抽象层（审核 S2-8）。
- 不把 D1/D2/D3 当技术债顺手改。
