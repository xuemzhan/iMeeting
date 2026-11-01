# IM-EVD-012 手机 / 平板自适应改造证据

<!-- doc-governance: IM-EVD-012 -->

> 文档类型：证据；状态：有效；日期：2026-11-01。
> 维护责任：待指派（iMeeting 维护者）。[台账](../../../README.md) · [规则](../../../governance/README.md)。
> 关联：[方案 IM-PLN-001](../../../plans/2026-11-01-responsive-ui-adaptation.md) · [实施报告 IM-RPT-009](../../../reports/2026-11-01-responsive-ui.md) · [改造前基线 IM-EVD-011](../2026-11-01-responsive-baseline/README.md)。

## 1 本目录内容

| 文件 / 目录 | 说明 |
|---|---|
| `responsive-gate.txt` | `node scripts/responsive-probe.mjs --strict --paths /,/login,/history,/knowledge,/voiceprints` 的完整输出（30 行量测 + 门禁结论）。 |
| `measurements-after.json` | 同上探针的机器可读结果（6 档视口 × 5 个页面）。 |
| `shots/` | 改造后截图：`phone-360/390`、`phablet-430`、`tablet-768/1024`、`desktop-1440` 各页一张，另含真机会中三标签截图 `meeting-390-transcript/minutes/participants.png`，以及详情页三标签 `detail-390-transcript/minutes/todos.png`。 |
| `e2e-responsive.txt` | （见第 5 节）响应式 e2e 用例与 CI 门禁的执行记录。 |

改造前的量测与截图在 [IM-EVD-011](../2026-11-01-responsive-baseline/README.md)（`measurements.json` + 4 张 before 截图），两套结果的差异即本次改造的效果。

## 2 复现方式

```powershell
# 1) 后端 + 前端（dev）
cd backend; $env:PYTHONPATH='src'; python -m imeeting.main
cd frontend; npm run dev            # 前端只监听 ::1，浏览器/脚本请用 http://localhost:5173

# 2) 响应式门禁（任一指标不达标退出码 1）
cd frontend
node scripts/responsive-probe.mjs --strict `
  --paths /,/login,/history,/knowledge,/voiceprints `
  --out ../docs/reviews/evidence/2026-11-01-responsive/measurements-after.json `
  --shots ../docs/reviews/evidence/2026-11-01-responsive/shots
```

门禁判据（脚本内实现，对应方案 IM-PLN-001 第 6 节 A1/A2/A3/A5）：

- `horizontalOverflow == 0`（所有视口）；
- `headerOverflow == 0`；
- `layoutWidth <= 1023`（手指操作档）时 `smallTargets == 0`（<40px 的可点控件计数）；
- 首页 `startCtaCount == 1`，且 compact 下该按钮高度 `>= 56`；
- compact 下 `.reader-body` 计算字号 `>= 16px`。

## 3 会中三标签的真机（浏览器仿真）验证

`meeting-390-*.png` 来自一次真实会中会话：Chromium 以 `--use-fake-device-for-media-stream` 提供音频、390×844 视口、`isMobile`，点击「开始会议」后运行 15s，再依次点底部三标签截图。同一次运行里量测到：

| 指标 | 值 |
|---|---|
| 三块面板的 `display` | 当前标签 `flex`，其余 `none`（宽度 374 / 0 / 0） |
| 底部标签栏 | 可见，高 55px |
| 底部控制条 | 高 65px（单行；改造中一度为 125px 双行，已通过收起设备选择器与键盘提示修掉） |
| 横向溢出 | 0 |
| 正文计算字号 | 16px |
| <40px 的可点控件 | 0（改造中一度为 4，来源是「会议信息」按钮与字号控件） |
| 页面 JS 异常 | 无 |

## 4 桌面零回归的对照证据

| 检查 | 命令 | 结果 |
|---|---|---|
| 既有功能验收（1440×900，14 条断言） | `BASE_URL=http://localhost:5175 API_URL=http://127.0.0.1:8021 node scripts/verify-features.mjs` | **14/14 通过**（含分隔条拖动 320→440、双击复位、最窄夹取、字号缩放持久化） |
| Playwright e2e 全量 | `npx playwright test` | 6 passed / 2 failed；**两条失败在改造前的 HEAD 上同样失败**（用 `git stash` 暂存全部改动后重跑复现），属既有问题，不在本次范围 |

> `verify-features.mjs` 不在当前 dev 栈（8000/5173）上跑：它按仓库 README 假定 e2e 席位
> （`seed-e2e.py` 生成的临时库 + 8021 后端 + 5175 前端 + `e2e/e2e-pass-123`）。
> 本次用 `ASR_MODE=echo` 起了一套临时栈跑完即回收，未改动开发库。

## 5 响应式 e2e 与 CI 门禁

`e2e/responsive.spec.ts` 覆盖方案第 6 节的 A1/A2/A3/A5（首页与登录页各 6 档 + 390px 顶栏折叠），
记录见 `e2e-responsive.txt`：

- `npx playwright test e2e/responsive.spec.ts` → **14 passed**（会中断点用例自带登录取 token 与首次改密轮换，不再需要环境变量开关）。
- 会中断点用例断言：1024 有 `role="separator"`、1023 双栏但无分隔条、390 只有当前标签且标签栏 3 项。
- 全量 `npx playwright test` → **20 passed / 2 failed / 0 skipped**（两条失败为既有问题，已在改造前 HEAD 上复现）。
- CI：`.github/workflows/ci.yml` 的 frontend job 新增 `Responsive gate` 步骤（`vite preview --port 5173`
  + `npm run verify:responsive`）。该步骤只用构建产物、不依赖后端；本地已用同样方式（5174 端口）复跑通过。

## 6 详情页三标签（390 / 1440 实测）

`detail-390-*.png` 来自登录态下的 `/history/1`：

| 视口 | 结果 |
|---|---|
| 390×844 | 三块面板 `data-pane` 一次只显示一块（其余 `display:none`），标签栏可见 55px，横向溢出 0 |
| 1440×900 | 四块面板同时 `display:block`（桌面并排不变），`nav[aria-label="详情面板切换"]` 不渲染 |

## 7 说明

- 截图含本地会议标题，均为测试生成的数据；临时会议记录（#20–#23）在验证后已从开发库清除，库回到 19 场。
- 未做的验证：真机 iOS Safari / Android Chrome（方案 A10）、会中 500/1000/2000 段的**真机**滚动帧率（桌面引擎实测见下）。

## 8 字号迁移与长列表实测（第三轮）

字号 codemod（`npm run codemod:font`）把 408 处独立的 `text-[10|11|12px]` 迁成语义类
`.text-micro/.text-meta/.text-ui`；桌面像素不变、compact 抬 1px。实测值：

| 视口 | `.text-meta` | `.text-ui` |
|---|---|---|
| 1440×900 | 11px（= 迁移前） | 12px（= 迁移前） |
| 390×844 | 12px | 13px |

长列表（方案 4.6 的「先测」）：用临时 harness 渲染真实 `TranscriptStream`，390×844 视口滚动 1.2s：

| 段数 | 挂载 | DOM 节点 | 帧间隔 p50 / p95 | 长帧（>50ms） |
|---|---|---|---|---|
| 200 | 13ms | 1 415 | 11 / 11ms | 2 |
| 500 | 21ms | 3 515 | 11 / 11ms | 0 |
| 1000 | 14ms | 7 015 | 11 / 11ms | 0 |
| 2000 | 23ms | 14 015 | 11 / 11ms | 0 |

结论：未达方案设定的「<50fps 或长任务 >200ms」门槛，**不引入 `react-window`**。
harness 为临时文件，测完已删除（未入库）；设备是桌面 Chrome 引擎 + 移动视口仿真，真机仍需复核。
