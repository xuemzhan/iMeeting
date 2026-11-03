# 2026-11-02 剩余问题实施方案：取数架构收敛、测试补齐、迁移收敛

> 文档类型：实施方案；状态：待评审（P1–P4 可立即开工，P5 测试补齐，P6 需 Postgres 环境，P7 需产品拍板）；日期：2026-11-02。
> 上承 [连续优化报告](../reports/2026-11-02-continuous-optimization.md)（IM-RPT-010）§3 的「仍未完成」清单；
> 关联 [手机/平板自适应方案](2026-11-01-responsive-ui-adaptation.md)（IM-PLN-001）、[Gap 修复计划](2026-10-31-gap-remediation-plan.md)。
> **状态提示（2026-11-02）**：本文尚未按[独立审核意见](../reviews/2026-11-02-remaining-plan-review.md)修订。审核判定 4 条阻塞项（B1 的 `key` 重挂载不成立、映射表漏 `useParticipants`、A2 的覆盖层取值语义错误、P5 覆盖率目标不可达），**修订前不要按 §2.2 与 §6 直接开工**。

## 0 摘要

剩余工作只有两类，混在一起谈会失焦：

- **工程债**：可以定位到文件与行、可以独立验收、不需要任何人拍板。9 处 `react-hooks/set-state-in-effect` 与 4 个零测试模块属此类，本文给出到函数级的改法与验收命令。
- **决策债**：改名语义、一人多 speaker_key、自动身份门槛值、迁移三处并存。这些**改代码就是改产品语义或改数据结构**，本文只给选项、影响面与推荐，不预设结论。

预估：工程债约 8.5 人日，测试补齐约 5 人日，环境类约 4 人日（不含真机验收本身）。

## 1 现状证据（可复跑）

| 口径 | 现状 | 命令 |
|---|---|---|
| eslint | 9 条 warn，全部 `react-hooks/set-state-in-effect` | `cd frontend && npm run lint` |
| 前端覆盖率 | 行 21.03 / 语句 20.22 / 函数 13.69 / 分支 16.16 | `npm run test:coverage && npm run coverage:check` |
| 前端测试 | 167 passed / 19 文件 | `npm test` |
| 后端覆盖率 | 81.20%（11148 条语句 / 93 文件） | `pytest -q --cov=imeeting --cov-report=json:coverage.json` + `python scripts/coverage_ratchet.py` |
| 后端测试 | 785 passed | `pytest tests -q` |
| 关键零测试文件 | `pages/History.tsx` 317 行、`pages/Meeting.tsx` 508 行、`components/TranscriptStream.tsx` 215 行、`store/session.ts` 618 行（`start/stop`） | — |

## 2 工程债 A：9 处 `set-state-in-effect`

### 2.1 根因（上一轮的实测结论，勿再按老说法做）

规则**不是**只抓「effect 体里直接 `setState`」，而是抓「effect 体里调用任何最终含 `setState` 的函数」，与 `await` 边界无关。
证据：`useParticipants` 把 `loading` 改成派生值后，告警仍停在 `void fetchList()` 那一行（见 IM-RPT-010 §3.3）。

因此统一修法只有一条主线：**让 effect 体里不再出现取数调用**。

| 状态性质 | 迁移目标 |
|---|---|
| 服务端状态（列表、schema、模板、冲突项） | `useQuery`（仓库已在用，`src/app/queryClient.ts`） |
| 随 props 变化而重置的界面状态（当前确认项、目标声纹） | 用 `key` 重挂载，或让状态成为 query 的派生值 |
| 命令式动作（提交分享口令、保存配置、决定冲突） | `useMutation` |

### 2.2 逐处映射

| # | 位置 | 现状 | 目标形态 |
|---|---|---|---|
| A1 | `hooks/useVoiceprintAdmin.ts:39` | `load()` 同步 `setLoading(true)`；`refreshDevices()` 也在同一 effect | `useQuery(['voiceprints'])` + `useQuery(['voiceprint-suggestions'])`；`load()` 改为 `qc.invalidateQueries`；`loading` 取 `isLoading` |
| A2 | `hooks/useConfigEditor.ts:52` | `void load()` → `setIsLoading(true)` + `setDraft(...)` | `useQuery(['config-schema'], enabled)`；`draft` 退化为**覆盖层**（只存用户改过的 key），值取 `draft[k] ?? field.value` |
| A3 | `hooks/useHistoryMeetings.ts:87` | 删空当前页后 effect 里 `setPage(p-1)` | 删除 effect；`refresh({ removed })` 在做失效前先就地回退一页 |
| A4 | `hooks/useSpeakerRoles.ts:84` | 详情数据晚到，effect 里 `adopt(initial)` | `importanceByKey` / `stateByKey` 退化为覆盖层，基线由 `apiSpeakers`/`initial` 派生，不再补种 |
| B1 | `components/VoiceConfirmationDialog.tsx:37,46` | 两条 effect 重置 `target`/`slot`/`targetTemplates`/`targetReady` 并取数 | 父层 `<VoiceConfirmationDialog key={item.id}>` 重挂载；模板改 `useQuery(['voiceprint-templates', target])`，`targetReady` 取 `isSuccess` |
| B2 | `components/VoiceprintConflictList.tsx:39` | `void load()` → `setLoading(true)` | `useQuery(['voiceprint-conflicts'])`；`decide` 成功后就地过滤缓存 |
| C1 | `hooks/useSharedMeeting.ts:43` | effect 里 `void open('')`（还带一条 `exhaustive-deps` 豁免） | 自动开门改 `useQuery(['share-open', token])`，手工提交改 `useMutation`；`opening` / `authError` 全部派生 |

### 2.3 三处需要小心的实现要点

**(a) 覆盖层状态（A2、A4）——这是本批唯一的语义改动点**

现在「服务器数据到达 = 整体覆盖本地」，改成「服务器数据 = 基线，本地只存覆盖」后，必须保证**服务器刷新后覆盖层清空**，否则改名/改重要程度之后界面会一直显示旧覆盖值。

```ts
// A4 关键形态
const baseSpeakers = apiSpeakers.length > 0 ? apiSpeakers : (initial ?? [])
const baseImportance = useMemo(() => importanceMapFromApi(baseSpeakers), [baseSpeakers])
const effectiveImportance = { ...baseImportance, ...importanceByKey }   // 覆盖层在前

const adopt = useCallback((list: MeetingSpeaker[]) => {
  setApiSpeakers(list)
  setImportanceByKey({})      // 服务端已是权威，覆盖层清空
  setStateByKey({})
}, [])
```

失败回滚也随之简化：删掉那个 key 即可，不需要记住 `previous`。

**(b) 用 `key` 重置（B1）**

`VoiceConfirmationDialog` 的 `current` 是一个「待确认队列的头」，把它换成 `<VoiceConfirmationDialog key={current.id} …>` 后，`target`/`slot` 的 `useState` 初值直接来自 props，effect 里那两次同步 `setState` 全部消失。**代价**：父组件要保证 `key` 稳定（用 `confirmation.id`，不要用下标或对象引用）。落地时同步改调用点（grep `VoiceConfirmationDialog`）。

**(c) 命令式动作留在事件里（C1）**

`shareApi.open()` 既是「首屏自动尝试空口令」也是「用户提交口令」。前者是查询，后者是变更，拆开后 `opening` 由 `auto.isFetching || submit.isPending` 派生，`authError` 由 `auto.isError || submit.isError` 派生。改完可顺带删掉 `useSharedMeeting.ts:45` 的 `eslint-disable`。

### 2.4 A 批的验收与风险

验收：
```powershell
cd frontend
npm run lint          # 期望 9 -> 0 warn（本批全部完成后）
npm test              # 现有 167 项不得失败；每处新增至少 2 项
npm run typecheck
npm run build
npm run test:coverage; npm run coverage:check   # 基线只许升
```
风险与对策：
- **缓存键冲突**：`['voiceprints']` 将与 `VoiceprintTemplateSummary`、`VoiceprintMergePanel` 等处的键共用。开工第一步先 `grep -rn "queryKey" src` 出一张键表，复用同名键时必须保证 `queryFn` 完全一致，否则拆成不同键。
- **交互反馈丢失**：`useVoiceprintAdmin` 的 `ok`/`error` 是操作级 toast，迁移后仍保留本地 state，不要塞进 query。
- 每处独立 commit，出问题 `git revert <sha>` 即可，不涉及数据结构。

## 3 工程债 B：4 个零测试模块

### 3.1 顺序与理由

先测「有副作用但可隔离」的，再测「依赖渲染细节」的：

1. `store/session.ts` 的 `start/stop`（最长、最容易出错，且是录音链路）
2. `components/TranscriptStream.tsx`（纯展示 + store 交互）
3. `pages/History.tsx`（react-query + 批量操作，A3 改完正好锁行为）
4. `pages/Meeting.tsx`（508 行、聚合多个 hook，最后做）

### 3.2 `session.ts` 的 `start/stop`：先抽缝再测

jsdom 既没有 `MediaRecorder` 也没有可用的 `WebSocket`。直接 stub 全局对象会把测试写成「测桩」，所以先做一层可注入的缝：

```ts
// src/lib/realtime.ts（新增）
export interface Recorder { start(): void; stop(): void; ondataavailable: ((e: { data: Blob }) => void) | null }
export interface RealtimeTransport { send(chunk: Blob): void; close(): void; onMessage(cb: (msg: unknown) => void): void }
export interface RealtimeFactory { createRecorder(stream: MediaStream): Recorder; connect(url: string): RealtimeTransport }
export const browserFactory: RealtimeFactory = { /* 现在 session.ts 里的实现原样搬过来 */ }
```

`session.ts` 改为从构造参数或模块级 `setFactory()` 取，测试注入 `FakeFactory`。要锁的行为：
- `start` 后立刻进入 `recording`，且**不会**在 `stop` 前重复 `start`；
- `stop` 等到最后一块 chunk 发完再转 `processing`（这是最容易写错的地方）；
- 权限被拒（`getUserMedia` reject）时回到 `idle` 并给可读错误，不留悬挂状态；
- 组件卸载/切会议时 `close()` 被调用一次。

### 3.3 其余三个

- `TranscriptStream`：桩掉 `scrollHeight`/`clientHeight`（jsdom 恒为 0），锁「新段落到达时自动滚动」「用户手动上滚后不再强制滚动」「空态文案」。
- `History`：沿用 `hooks/useMeetingDetail.test.tsx` 的 `renderHook` + `QueryClientProvider` 模式，锁 A3 的页码回退（删掉本页最后一条 → 页码 -1；删中间一条 → 页码不变）。
- `Meeting`：只测「决策与分支」（能力检查、无转写不许生成、录音中离场提示），不测布局；布局由 e2e 兜。

### 3.4 覆盖率目标

| 里程碑 | 行覆盖 | 备注 |
|---|---|---|
| 当前 | 21.03% | 基线，只许升 |
| A 批完成 | ≥ 23% | 迁移后的 hook 顺带补测 |
| B 批完成 | ≥ 30% | 四个大文件是主要来源 |

每完成一批跑 `npm run coverage:baseline` 抬门槛，不做一次性跳变。

## 4 决策债 D：需产品拍板

### D1 一次改名到底意味着什么（`RelabelRequest` 三个开关的默认值）

现状：`learn` / `rename_identity` / `bind_person` 默认全 `True`（`api/http/routes/voiceprints.py:311`），即「用户输入一个名字 = 纠正叫错 = 改全局身份名」。三个选项：

| 方案 | 语义 | 代价 |
|---|---|---|
| D1-a（现状） | 默认「纠正叫错」，弹窗提供「只改本场显示名」 | 用户想说「这是另一个人」时会误改声纹库 |
| D1-b | 默认「只改本场显示名」，另设「同时改声纹库名」勾选 | 声纹库命名不再自动收敛，需要额外运营动作 |
| D1-c（推荐） | **按来源分流**：来自自动识别结果的手工纠正 → 默认 D1-a；来自新建/手动输入的未知说话人 → 默认 D1-b；弹窗始终展示当前选择 | 需要区分「改标签的入口」，前端两处调用点分别传参 |

D1-c 只需在现有三开关上补一个 `intent` 参数，不新增接口。**决策点**：是否接受「同一个动作、不同入口、默认不同」。

### D2 允许一人多 speaker_key

现状：`UNIQUE(meeting_id, person_id)` 决定了「一人一簇」，且 `POST /meetings/{id}/merge-speakers` 已覆盖「同人两簇」。
若产品坚持要放宽，落地步骤：①新增 `person_speaker_key` 关联表（`person_id`, `speaker_key`, `meeting_id`, `source`）；②双写期：绑定/改绑同时写旧列与新表；③读路径改为「按 person_id 聚合多 key」；④SQLite 需要重建表（`ALTER` 不支持删约束），必须配迁移 `20261102_02` + 实库副本验证（沿用 `20261102_01` 的双路径验证方法：兼容 DDL 与 Alembic 各跑一次，断言片段数无损）。预估 2–3 人日 + 一次实库演练。

### D3 自动身份的门槛默认值

现状：`spk.auto_identity_min_segments=3`、`spk.auto_identity_min_ms=3000`（`settings.py:861`/`:874`），会后停用证据不足的自动声纹。
决策需要数据：在真实语料上统计「正确识别但被误停用」与「噪声被正确停用」的分布，取一个折中点。当前无环境，只能给方法：

```
脚本：backend/scripts/bench_voice_templates.py 加一档 --sweep-min-evidence
输出：按 (段数, 毫秒) 网格的「误停用率 / 噪声拦截率」
选点：误停用率 < 2% 的前提下最大化拦截率
```

## 5 环境债 E

### E1 迁移三处并存（`create_all` + 兼容 DDL + Alembic）

目标：Alembic 作唯一真相，启动只做 `alembic upgrade head`。

| 步 | 动作 | 出口条件 |
|---|---|---|
| E1-1 | 对现有 10 个版本跑 `alembic upgrade head` 后 `alembic revision --autogenerate`，把产出的 diff 归零 | 空 diff，或差异项逐条写明「故意不迁移」的理由 |
| E1-2 | CI 增加 Postgres service job，跑 `upgrade head` → `downgrade base` → `upgrade head` 往返 | 往返成功且表结构哈希一致 |
| E1-3 | `db.py` 引入 `DB_SCHEMA_MODE=alembic\|legacy`，默认 `legacy`，灰度到 `alembic` | 老库升级与新库初始化两条路径都过 |
| E1-4 | 下掉兼容 DDL 分支 | 线上连续两个版本无回退 |

E1-1 是本项的全部难点：差异多半集中在索引名、默认值与 SQLite/PG 的类型差异上，必须先有 E1-2 的 Postgres CI 才能验证。

### E2 需要环境的验收项

| 项 | 前置 | 验收口径 |
|---|---|---|
| 真实语料阈值校准 | 带音频的会议数据集 | D3 的网格结果 + 一次回放对齐 |
| 目标服务器 compose / TLS | 一台目标服务器 | `docker compose up` 后 HTTPS 可访问、证书链完整、镜像扫描 0 高危 |
| iOS Safari / Android Chrome 真机 | 真机 | 录音权限、后台切换、锁屏回前台、弱网重连四项全过 |
| 长稳与故障注入 | 目标服务器 | 连续 24h 录制不泄漏、断网 5 分钟可续传 |
| FunASR 按需导入 | 带 torch 的环境 | 未装 torch 时启动不报错，功能入口提示按需安装 |

## 6 执行顺序、门禁与回滚

| 批次 | 内容 | 验收 | 回滚 | 预估 |
|---|---|---|---|---|
| P1 | A3（页码回退）、A4（覆盖层） | lint 9→7，`npm test`，覆盖率不降 | 独立 commit 回退 | 1.0d |
| P2 | A1、A2（引入 queryKey，先出缓存键表） | lint 9→5，新增测试 ≥ 4 项 | 同上 | 2.0d |
| P3 | C1、B2 | lint 5→3 | 同上 | 1.0d |
| P4 | B1（`key` 重挂载，改父组件契约） | lint 3→0 | 同上；若界面回归则先回退 P4 | 1.0d |
| P5 | 测试补齐（session → TranscriptStream → History → Meeting） | 覆盖率 ≥ 30%，基线抬高 | 只增测试，无回滚风险 | 5.0d |
| P6 | E1 迁移收敛（E1-1/E1-2 先做） | Postgres 往返 CI 绿 | 保持 `legacy` 模式 | 4.0d |
| P7 | D1/D2/D3 落地 | 按各决策项的验收 | 决策前不动代码 | 视决策 |

硬门禁（每批都必须满足，否则不进下一批）：
```powershell
cd frontend; npm run lint; npm run typecheck; npm test; npm run build; npm run test:coverage; npm run coverage:check
cd backend;  & ..\.venv-production-review\Scripts\ruff.exe check src/imeeting tests migrations scripts
cd backend;  & ..\.venv-production-review\Scripts\python.exe -m pytest tests -q
cd backend;  & ..\.venv-production-review\Scripts\python.exe "..\docs\reviews\evidence\2026-11-01-comprehensive-review\probes\check_links.py"
```

## 7 不做什么（明确排除）

- **不关规则、不加 `eslint-disable` 了事**：现有的唯一一处豁免（`useSharedMeeting.ts:45`）会在 C1 里消失，不新增。
- **不引入第二套取数库**：已经有 TanStack Query，`swr`/`ahooks` 之类一律不引入。
- **不为消警告重写 `patch` 的乐观更新**：`useParticipants.patch` 现在直接改本地数组，迁 `useQuery` 时要整体搬到 `setQueryData`，但这属于 P2 的一部分，不单独提前做。
- **不在没有 Postgres CI 的情况下动迁移代码**（E1-1 之前不改 `db.py`）。
- **不把产品决策当技术债顺手改**：D1/D2 的默认值与表结构，未拍板前保持现状。
