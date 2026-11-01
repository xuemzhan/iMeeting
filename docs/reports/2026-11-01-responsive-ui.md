# 2026-11-01 手机 / 平板自适应改造：实施报告

<!-- doc-governance: IM-RPT-009 -->

> 文档类型：报告；状态：阶段快照；日期：2026-11-01。
> 维护责任：待指派（iMeeting 维护者）。[台账](../README.md) · [规则](../governance/README.md)。
> 上游方案：[IM-PLN-001](../plans/2026-11-01-responsive-ui-adaptation.md)。
> 证据：[改造前 IM-EVD-011](../reviews/evidence/2026-11-01-responsive-baseline/README.md) · [改造后 IM-EVD-012](../reviews/evidence/2026-11-01-responsive/README.md)。

## 结论

按方案 IM-PLN-001 的 P0–P3 与 P5 主体**已落地**，P4 做了「零溢出 + 触控」层面的收口，
P6 的字号 codemod 与虚拟滚动**有意未做**（理由见第 5 节）。

关键结果：

- 6 档视口 × 5 个页面（`/`、`/login`、`/history`、`/knowledge`、`/voiceprints`）**横向溢出全部为 0**；
  改造前 390px 溢出 +25px、360px 溢出 +55px。
- 手指操作档（≤1023px）**可点控件 <40px 的数量为 0**；改造前 5/8 个顶栏按钮小于 40。
- 首页「开始会议」**恰好 1 个**且 compact 下高 62px；改造前同屏 2 个。
- compact 下正文计算字号 **16px**；改造前 15px（且部分语义类 10–12px）。
- 会中 390×844：底部三标签可切（转写 / 纪要 / 参会人）、标签栏 55px、控制条 65px 单行、无 JS 异常。
- 桌面（1440×900）既有验收 **14/14 通过**，含分隔条拖动、双击复位、最窄夹取、字号缩放持久化。

## 1 逐阶段落地内容

| 阶段 | 交付 | 关键文件 |
|---|---|---|
| **P0** | 响应式令牌（`--tap-min`/`--btn-height`/`--control-h`/`--panel-header-h`/`--fs-body`/`--fs-ui`/`--fs-meta`/`--safe-bottom`）+ 三档断点（1023/767）+ `100dvh` + `useLayoutMode` 钩子 + 可入库的响应式探针 | `styles/index.css`、`hooks/useLayoutMode.ts`、`scripts/responsive-probe.mjs` |
| **P1** | 顶栏紧凑化（品牌文字/分隔线/「未开始」/波形/导航/设置收进 `max-md:hidden`；标题输入改 flex-1）；新增 `OverflowMenu`（导航 + 设置 + 账号：后台配置/退出登录）；`IdleHero` 单列重排、特性卡改横向行、CTA 去重 | `TopBar.tsx`、`OverflowMenu.tsx`、`IdleHero.tsx`、`ControlBar.tsx`、`DeviceSelector.tsx` |
| **P2** | 会议页 compact：底部三标签（新增「参会人」）、左右滑动切页（阈值 40px、横向位移须大于纵向、选中文本/输入框/横滑容器内不拦截、慢速拖动不切）、`MinutesPanel` **单实例化**、参会人 chips 横滑、`data-pane` 取代 `data-hidden-mobile` | `pages/Meeting.tsx`、`ParticipantsPanel.tsx`、`styles/index.css` |
| **P3** | `PaneSplitter` 只在 wide（≥1024）挂载；medium（768–1023）双栏 60/40（转写 `flex 2`、纪要 `flex 1`，纪要 `min-width: 280px`） | `pages/Meeting.tsx` |
| **P4** | `/voiceprints` 行内 26–30px 按钮与纯文字按钮在 ≤1023 统一抬到 44（93 个）→ 0；`/login` 主按钮与「去注册」抬到 44（2 个）→ 0；其余页面实测无横向溢出 | `pages/Voiceprints.tsx`、`pages/Login.tsx`、`styles/index.css` |
| **P5** | 新增 `Sheet`（compact 底部升起：圆角 16、抓手、`92dvh` 上限、下拉关闭、背景滚动锁；md+ 保持原居中卡片）；迁移 `ShareDialog`/`MindMapDialog`/`SegmentEditDialog`/`MeetingInfoEditor`/`MembersDialog`/`MinutesVersionsDialog`；`SettingsPanel` compact 全宽 + 安全区 | `components/Sheet.tsx` + 6 个弹窗、`SettingsPanel.tsx` |

## 2 行为变化（与改造前的差异）

全部差异都只在 ≤1023px 生效；桌面分支保留原有类名与尺寸。

| 位置 | 改造前 | 改造后 |
|---|---|---|
| 顶栏（compact） | 品牌名 + 5 个入口常驻，390px 下需要 415px | 只留品牌图标 + 标题 + 状态 + ⋮；导航/设置/账号在 ⋮ 里 |
| 首页（compact） | Hero 大 CTA + 控制条按钮并存（2 个） | 只保留底部控制条按钮（1 个，62px 高） |
| 会议页（compact） | 顶部两标签 + 纪要面板渲染两份 | 底部三标签 + 纪要单实例 + 左右滑动 |
| 会议页（768–1023） | 单栏 + 顶部标签 | 双栏 60/40，无分隔条 |
| 底部控制条（compact） | 高 68px 固定、设备选择器与按钮折成两行 | 高 65px 单行；设备选择器与 `Space/F/Esc` 提示在窄屏隐藏（改从「更多 → 设置 → 麦克风」选设备） |
| 弹窗（compact） | 居中卡片，内容多时上下被裁 | 底部升起的 Sheet，最高 92dvh，可下拉关闭，背景不滚 |
| 设置面板（compact） | 右侧 380px 抽屉 | 全宽 + 底部安全区 |
| 登录页（compact） | 内联 36px 主按钮 + 18px 文字按钮 | 44px，错误提示 `role="alert"` 且可换行 |

## 3 验收对照（方案第 6 节）

| # | 判据 | 结果 |
|---|---|---|
| A1 | 6 档视口 `scrollWidth - clientWidth == 0` | **通过**（5 个页面 × 6 档，全部 0） |
| A2 | 390px 下 `header.scrollWidth == header.clientWidth` | **通过**（`headerOverflow = 0`） |
| A3 | compact 主 CTA ≥56、其它控件 ≥44 | **通过**（CTA 62/64；≤1023 档 `smallTargets = 0`） |
| A4 | compact 下 `.reader-body` ≥16px | **通过**（会中实测 16px） |
| A5 | 首页同屏 CTA == 1 | **通过** |
| A6 | 768/1023 双栏、1024 出现分隔条 | **结构通过**：`PaneSplitter` 仅 `layout === 'wide'` 挂载；medium 双栏由 `md:flex-row` + flex 比例实现（未写 e2e，见第 5 节） |
| A9 | 1440 下既有 14 条断言全绿 | **通过**（14/14） |
| A10 | iOS / Android 真机 | **未做**（无真机） |

## 4 验证记录

| 检查 | 命令 | 结果 |
|---|---|---|
| 类型检查 | `npm run typecheck` | 通过 |
| Lint | `npm run lint` | 0 error / 17 warning（与改造前同数，均为既有的 set-state-in-effect 类提示） |
| 单元测试 | `npm test` | 13 文件 / 101 用例通过（与改造前同数） |
| 构建 | `npm run build` | 通过（8.4s，产物 `dist/`） |
| 响应式门禁 | `node scripts/responsive-probe.mjs --strict --paths /,/login,/history,/knowledge,/voiceprints` | **全部通过**（见 IM-EVD-012） |
| 桌面功能验收 | `BASE_URL=http://localhost:5175 API_URL=http://127.0.0.1:8021 node scripts/verify-features.mjs` | **14/14**（临时 e2e 栈，`ASR_MODE=echo`） |
| e2e 全量 | `npx playwright test` | 6 passed / 2 failed；两条失败在**改造前 HEAD 上同样复现**（`git stash` 对照），属既有问题 |
| 会中真机仿真 | Chromium 假麦克风 + 390×844 起会 15s | 三标签切换正常、控制条 65px、溢出 0、正文 16px、无 <40px 控件、无 JS 异常 |

后端未改一行代码，因此未重跑后端 766 项测试（本轮改动面 100% 在 `frontend/`）。

## 5 未完成与已知限制

| 项 | 状态 | 原因 / 后续 |
|---|---|---|
| P6 字号 codemod（423 处 10–12px 硬编码） | **未做** | 一次性把 10–12px 抬到 12–14px 会改变桌面排版（行高、表格列宽、分栏标题不换行等），与「桌面零回归」冲突。改用令牌覆盖 `.reader-body*` 与输入类，其余留给按目录分批、逐批回归 |
| 长会议虚拟滚动（方案 4.6） | **未做** | 需要 500/1000/2000 段真实数据测 FPS 才能决定；本轮没有造这类数据，故不加依赖 |
| P4 的 History 筛选抽屉 / 批量操作底部浮层、MeetingDetail 手机四标签、admin 表格首列 sticky | **未做** | 实测 `/history`、`/knowledge` 在 6 档视口均无横向溢出，属「可用但未重排」；重排需要改交互结构，风险与收益比在本次不划算 |
| 未迁移到 `Sheet` 的弹窗 | **部分** | `VoiceConfirmationDialog`、`MinutesEditor`、`CompliancePanel` 仍是居中卡片（前两个在手机上仍可用，仅内容多时需滚动）；后续按同一模式迁移 |
| 提示条折叠（方案 4.4.8） | **部分** | 只把「会议信息」按钮抬到 44px；三条提示条仍是展开态，未做「单行 + 详情」折叠 |
| A6 的 e2e 断言、A10 真机 | **未做** | 需要新增 `e2e/responsive.spec.ts` 与真机；本轮用探针脚本覆盖了同等指标 |
| CI 接线 | **部分** | 已加 `npm run verify:responsive`（本地 Chrome、`CI=1` 时改用 Playwright 自带 Chromium）。未接进 `.github/workflows/ci.yml`：需要一个「构建 + preview + 探针」的 job，且要确认 runner 上的浏览器与 5173 端口可用；先落命令，接线留给下一轮 |
| 平板横屏 1024×768 归 wide 档 | **已知取舍** | 1024 按方案归 wide（32px 控件）。iPad 横屏用鼠标/触控笔没问题，纯手指操作时控件偏小；若要覆盖，需把 1024 单独成档，属方案变更 |

## 6 回滚

改动全部在 `frontend/`（+ 文档），无后端/DB/接口变化。回滚方式：

- 整体：`git revert <本次提交>`；
- 单阶段：令牌与门禁脚本（P0）可保留，回滚 `TopBar/IdleHero/ControlBar`（P1）、`Meeting/ParticipantsPanel`（P2–P3）、`Sheet` 及其 6 个调用点（P5）互不影响；
- 窄屏样式集中在 `styles/index.css` 末尾三段媒体查询（≤1023 增强、≤767 增强、`data-pane` 隐藏与 `app-footer`），删掉即回到改造前布局。
