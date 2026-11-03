# IM-PLN-002 独立审核：剩余问题实施方案

> 文档类型：审核意见；审核对象：[剩余问题实施方案](../plans/2026-11-02-remaining-optimization-plan.md)（IM-PLN-002）；
> 日期：2026-11-02；立场：独立审核，只看证据与可执行性，不替方案补设计。

## 结论

**不建议按现方案直接分批开工。** 4 条阻塞项里有 2 条是「照做会做坏」（会引入功能缺陷），2 条是「照做做不到」（映射漏项、目标算术不可达）。修正后可开工，其余 7 条在对应批次开工前就地改。

已核对为**成立**的部分：§2.1 的规则结论（派生 loading 不消警告）、9 条告警的性质归类、批次顺序「先 A3/A4 后 A1/A2」、D2 的迁移步骤与 SQLite 必须重建表的判断、硬门禁里的命令本身可跑。

## 1 阻塞项

### S1-1（阻塞）B1 的 `key` 重挂载方案在现有代码上不成立，且会取消排队中的确认

方案写：「父层 `<VoiceConfirmationDialog key={item.id}>` 重挂载，effect 里那两次同步 `setState` 全部消失」。

核对结果：**这个组件没有父层可传的 props，它的队列也在自己身上。**

- `app/App.tsx:37` 是 `<VoiceConfirmationDialog />`——无 props，挂载一次，`current` 由窗口事件在组件内部取得（`VoiceConfirmationDialog.tsx:21-26` 把请求 push 进 `pending.current`）。
- 该组件的 cleanup（`:28-32`）会 `pending.current.forEach((request) => request.resolve({ decision: 'cancel' }))`。

影响：给这个组件加 `key` 需要父层拿到 `item.id`，而父层根本没有 `item`。若强行在内部改 key，每次 `current` 变化都会卸载重挂载 → cleanup 执行 → **队列里所有待确认的请求被静默判为 cancel**。这是数据/交互层面的真实缺陷，不是风格问题。

怎么改：保留队列在组件内，把「随 `current` 重置的界面状态」改成**带身份戳的派生**，不重挂载：

```tsx
// 现在：effect 里 setTarget(...) / setSlot('')
// 改成：状态自带 current.id，读的时候比对
const [picked, setPicked] = useState<{ id: string; target: string } | null>(null)
const target =
  picked && picked.id === currentId ? picked.target
  : String(current?.confirmation.assessment.other?.voiceprint_id ?? '')
const setTarget = (value: string) => setPicked({ id: currentId, target: value })
```

`targetTemplates` / `targetReady` 同理改为 `useQuery(['voiceprint-templates', target])` 的派生值（这条方案里是对的）。

### S1-2（阻塞）映射表漏了 `useParticipants`，9 条只映射了 8 条

方案 §2.2 列出 A1–A4、B1（2 条）、B2、C1 = **8 条**，但 §1 与验收写的是「9 条 → 0」。

`npm run lint` 的 9 条里包含 `hooks/useParticipants.ts:88`（`void fetchList()`），这是上一轮刚改过、告警**故意保留**的那一处（见 IM-RPT-010 §3.3）。方案没有给它任何归属，因此 P4 结束时 lint 会停在 1，而不是 0，验收失败。

怎么改：把 `useParticipants` 补成 A5（`useQuery(['participants', meetingId])` + `patch` 走 `setQueryData`），并在 §7「不做什么」里删掉「不单独提前做」这类与之冲突的表述。

### S1-3（阻塞）A2 的 `draft[k] ?? field.value` 是错的，且漏了 3 个调用点

两个问题：

**(a) 语义错误。** 覆盖层用 `??` 取基值，配置项里的 `false`、`0`、`''` 都是合法值且都是假值 —— 用户把开关从 `true` 改成 `false` 后，界面会显示回 `true`。必须用「键存在与否」判断：

```ts
const valueOf = (key: string) => (key in draft ? draft[key] : baseValues[key])
```

**(b) 调用点未列。** `draft` 目前被直接读取，改成稀疏覆盖层会破坏它们：

| 位置 | 现状 |
|---|---|
| `components/ConfigGroupFields.tsx:40` | `<Control field={f} value={draft[f.key]} …>` |
| `components/SettingsBackendConfig.tsx:91` | `draft={cfg.draft}` |
| `hooks/useConfigEditor.ts:71-72` | `changed` 用 `JSON.stringify(f.value) !== JSON.stringify(draft[f.key])` 计算 |

影响：不列这三处，改完配置页会**整页空白输入框**（undefined）且 `changed` 恒为「全部配置都改了」，保存时会把未改动的密钥掩码一并写回——这正是该文件注释里明确要避免的事（`useConfigEditor.ts:65`）。

怎么改：`useConfigEditor` 额外导出 `valueOf(key)` 与 `values`（合并后的完整字典），三处调用点改用 `values[f.key]`；`changed` 改成「只在 `draft` 里出现且与基值不同的键」。把「改 3 个调用点」写进 A2 的动作项，并把估时从 1.0d 上调（见 S3-3）。

### S1-4（阻塞）P5 的「覆盖率 ≥ 30%」算术上不可达，「四个零测试模块」的表述也不准确

**(a) 算术。** 当前 `787/3742 = 21.03%`（`coverage/coverage-summary.json`）。方案所指的四个模块**可覆盖行**合计只有：

| 文件 | 可覆盖行 |
|---|---|
| `pages/History.tsx` | 32 |
| `pages/Meeting.tsx` | 128（+ 子条目 8） |
| `components/TranscriptStream.tsx` | 76 |
| `store/session.ts` | 203（已覆盖 88） |

即使把这四个文件**全部 100% 覆盖**，也只到 `(787 + 244 + 115) / 3742 = 30.6%`。把「全部行都测到」当成批次目标不现实。

怎么改：目标改为绝对量并留余量 —— P5 目标 `≥ 26%`（≈ +190 行），或把口径改成「四个文件各自行覆盖 ≥ 60%」。要冲 30% 只能靠 P1–P4 迁移后那批 0 覆盖的 hook/组件（合计 390 行），方案把 30% 归给 P5 是归错了来源。

**(b) 表述。** 「零测试模块」四字对两个文件不成立：

- `store/session.ts` 已有 `session.test.ts`（20+ 项：`ready/partial/final/speakers/minutes_update/persistTitle`），当前 88/203 行；**没有测试的是 `start`/`stop` 录音链路**，不是整个文件。
- `TranscriptStream` 的滚动逻辑已有 `lib/transcriptScroll.test.ts`；组件本体未测。

影响不大，但会让读者误判工作量。改为「未覆盖部分」的精确表述。

## 2 需修正（对应批次开工前就地改）

| # | 问题 | 证据 | 修正 |
|---|---|---|---|
| S2-1 | A1 的「缓存键冲突」风险方向错了：仓库里**没有** `['voiceprints']` 键 | `grep queryKey` 全量结果只有 `meetings` / `meeting` / `minutes` / `mindmap` / `reviews` / `knowledge-*` / `meeting-info` / `shared-meeting` | 真风险是反的：新建共享缓存后，`VoiceprintMergePanel` 合并、`VoiceprintTemplateSummary` 重建等**不会自动失效**该键，必须手工接线。把这句换掉 |
| S2-2 | A3 删掉 effect 会丢掉「他人删除 / 跨页操作导致当前页为空」的自动回退 | `useHistoryMeetings.ts:85-89` 的触发条件只看 `isLoading/meetings/total/page`，不只本人操作 | 要么承认这是有意的行为收窄（写进方案），要么保留一个「仅当 `page>0 && total>0 && meetings.length===0` 时」的极窄 effect 并接受该条告警 |
| S2-3 | A3 改 `refresh` 签名会波及另一个 hook | `hooks/useHistoryBulkActions.ts:8-11` 的 `Options.refresh: () => Promise<void>`，三个批量动作都调它 | 把「同步改 `useHistoryBulkActions` 接口与 3 个调用点」写进 A3 |
| S2-4 | A4 的 `effectiveImportance` 每次渲染新建对象，`speakers` 的 `useMemo` 直接失效 | `useSpeakerRoles.ts:87-93` 依赖 `importanceByKey`/`stateByKey` | 合并结果加 `useMemo`，依赖 `[baseImportance, importanceByKey]` |
| S2-5 | E1 步骤顺序自相矛盾：表里 E1-1（autogenerate 归零）在 E1-2（Postgres CI）之前，正文却说「必须先有 E1-2 才能验证」 | 方案 §5.1 表与末段 | 调成 E1-2 先、E1-1 后；并注明 SQLite 上的 autogenerate 差异不可信（无 ALTER、无 server_default 比较） |
| S2-6 | 硬门禁**没有 e2e**，而被改动最大的 B1 恰好有 e2e 守卫 | `frontend/e2e/identity-confirmation.spec.ts` 存在 | 门禁加 e2e（至少 `identity-confirmation` + `responsive` 两个 spec）；这一条同时能兜住 S1-1 的回归 |
| S2-7 | D1 说「前端两处调用点」，实际 **3 处** | `useMeetingDetail.ts:78`、`:99`、`store/session.ts:404` | 改成 3 处，并说明 `session.ts` 那处是录音中改名（走事件流），默认值需要单独定 |
| S2-8 | P5「先抽一层 `RealtimeFactory`」与现状不符：两个缝已经存在 | `store/session.ts:4` 已从 `../audio/recorder` 引入 `MicrophoneRecorder`、`:7` 从 `../api/ws` 引入 `TranscriptSocket` | 先 `vi.mock` 这两个模块测 `start/stop`；只有当 recorder 自身也要测时才新增注入层。方案当前的写法会让 P5 多出一层没人用的抽象 |
| S2-9 | 估算只有总数、没有每批的「行为不变」判据 | 方案 §6 表 | 每批补一条 DoD：现有 167 项测试 + 对应 e2e spec 通过，且 `git diff` 不含与告警无关的行为改动 |

## 3 建议（不阻塞）

- **S3-1** C1 把 `access_token` 放进 query 缓存要设 `gcTime: 0`（或 `staleTime: 0` + 卸载即清），否则分享口令换来的凭据会在内存里滞留到 GC，且 `['share-open', token]` 会随 token 累积条目。
- **S3-2** §2.1 的规则结论目前只有**一次观察**（`useParticipants` 改完仍报警）。建议补一个 5 行最小复现文件放 `frontend/scripts/` 或测试夹具里，让这条结论可被后来者一行命令证伪，否则下一个人会重新踩一遍。
- **S3-3** 估时给区间而非点值，并按 S1-3/S1-1 上调：P1 1.5d、P2 3.0d、P3 1.5d、P4 2.0d（合计 8.0d，原 5.0d）。
- **S3-4** P1 选 A3+A4 仍是对的（不引入缓存键、不动跨文件契约），但 A4 的覆盖层与 A2 同源，建议合并成一批做「覆盖层模式」的一次性验证，避免同一套模式被验证两次、两次都只验证一半。

## 4 断言核对表

| 方案断言 | 核对方法 | 判定 |
|---|---|---|
| 9 条 warn 全为 `set-state-in-effect` | `npm run lint` | 成立 |
| 映射表覆盖全部 9 条 | 逐行比对 lint 位置 | **不成立**（漏 `useParticipants:88`） |
| 规则对「effect 内调用含 setState 的函数」一律报警 | 上一轮实测 + `useParticipants.ts:88` | 成立（但只有单点证据，见 S3-2） |
| `['voiceprints']` 会与现有组件冲突 | 全量 grep `queryKey` | **不成立**（无该键） |
| B1 可用 `key` 重挂载 | `App.tsx:37`、`VoiceConfirmationDialog.tsx:8/21-33` | **不成立**（无 props，队列在组件内） |
| A2 覆盖层 `??` 取基值 | 配置项含 `false/0/''` | **不成立**（假值覆盖失效） |
| 四个模块零测试 | `session.test.ts`、`lib/transcriptScroll.test.ts` | **不成立**（仅 `start/stop` 与组件本体未测） |
| 覆盖率可到 30% | `coverage-summary.json` 逐文件行数 | **不成立**（全量覆盖才 30.6%） |
| `start/stop` 需先新建 `RealtimeFactory` | `session.ts:4/7` | **不成立**（缝已存在） |
| D1 前端两处调用点 | `voiceprintApi.relabel` 生产调用点 | **不成立**（3 处） |
| D2 SQLite 需重建表 | `migrations/versions/` 现状 + SQLite 无 DROP CONSTRAINT | 成立 |
| 门禁命令可跑 | 逐条跑过 | 成立 |
| E1 步骤顺序 | 方案 §5.1 表与末段自相矛盾 | **不成立** |

## 5 修正后的最小开工序列

1. 先改方案：补 A5（`useParticipants`）、重写 B1（身份戳派生，不重挂载）、修 A2（`key in draft`）、改 P5 目标口径、门禁加 e2e、E1 换序、D1 改 3 处。
2. P1：A3 + A4（含 S2-2/S2-3/S2-4 的修正），DoD 见 S2-9。
3. P2：A1 + A2 + A5（三者都是 queryKey 迁移，一次把缓存键表立起来）。
4. P3：C1 + B2；P4：B1。
5. P5 起按修正后的覆盖口径推进。

审核意见不替方案做决策：S2-2 的「行为收窄是否可接受」、S1-1 的两种重置写法、S1-4 的覆盖口径，都需要方案作者或产品确认后再改文档。
