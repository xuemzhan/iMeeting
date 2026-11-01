# 2026-11-01 手机 / 平板自适应改造：实施报告

<!-- doc-governance: IM-RPT-009 -->

> 文档类型：报告；状态：阶段快照；日期：2026-11-01。
> 维护责任：待指派（iMeeting 维护者）。[台账](../README.md) · [规则](../governance/README.md)。
> 上游方案：[IM-PLN-001](../plans/2026-11-01-responsive-ui-adaptation.md)。
> 证据：[改造前 IM-EVD-011](../reviews/evidence/2026-11-01-responsive-baseline/README.md) · [改造后 IM-EVD-012](../reviews/evidence/2026-11-01-responsive/README.md)。

## 结论

按方案 IM-PLN-001 的 P0–P6 **全部落地**：字号 codemod 已把 408 处独立硬编码字号迁移为语义类
（桌面像素不变、compact 抬 1px），长列表按方案「先测后定」的要求实测后**决定不引入虚拟滚动**
（2000 段滚动 p95 = 11ms）。剩下的只有真机验收与两处交互型重排（见第 5 节）。

第二轮（同日追加）补齐了首轮列为遗留的项：`e2e/responsive.spec.ts` + CI 响应式门禁、
剩余 3 个弹窗迁移（`MinutesEditor`/`CompliancePanel` 迁到 `Sheet`，`VoiceConfirmationDialog`
做 compact 适配）、会议页提示条折叠、History 批量条改底部浮层、MeetingDetail 手机三标签、
admin 表格首列吸附。

第三轮：字号 codemod（`scripts/codemod-font-scale.mjs`，408 处）、长列表性能实测与结论、
`SharedMeeting`/`ChangePassword` 的 compact 内边距对齐。

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
| **P4** | `/voiceprints` 行内 26–30px 按钮与纯文字按钮在 ≤1023 统一抬到 44（93 个）→ 0；`/login` 主按钮与「去注册」抬到 44（2 个）→ 0；`/history` 批量操作条改 compact 底部吸附浮层；admin 表格首列在横向滚动时吸附 | `pages/Voiceprints.tsx`、`pages/Login.tsx`、`pages/HistoryBulkActions.tsx`、`pages/admin/AdminAuditLog.tsx`、`styles/index.css` |
| **P5** | 新增 `Sheet`（compact 底部升起：圆角 16、抓手、`92dvh` 上限、下拉关闭、背景滚动锁；md+ 保持原居中卡片）；迁移 `ShareDialog`/`MindMapDialog`/`SegmentEditDialog`/`MeetingInfoEditor`/`MembersDialog`/`MinutesVersionsDialog`/`MinutesEditor`/`CompliancePanel` 共 8 个弹窗；`SettingsPanel` compact 全宽 + 安全区；`VoiceConfirmationDialog` 保留自身焦点陷阱，只做 compact 高度/内边距适配 | `components/Sheet.tsx` + 8 个弹窗、`SettingsPanel.tsx`、`VoiceConfirmationDialog.tsx` |
| **P6** | 字号 codemod 落地：`scripts/codemod-font-scale.mjs` 把 **408 处** 独立写死的 `text-[10/11/12px]` 换成语义类 `.text-micro/.text-meta/.text-ui`（桌面像素不变、compact 抬 1px；21 处带响应式变体的按护栏跳过）；语义类字号另在 compact 抬一档（`.label`/`.chip`/`.panel-header h2`/`.count-badge`）；输入类 16px 覆盖到 ≤1023（含 iPadOS 聚焦放大）；长列表**实测后决定不上虚拟滚动**（见第 4 节） | `scripts/codemod-font-scale.mjs`、`styles/index.css`、60 个组件/页面 |
| **追加** | 详情页 compact 三标签（转写 / 纪要 / 待办，替代「并排 + 长滚动」）；会议页顶部提示条 compact 折叠为单行 +「详情」 | `pages/MeetingDetail.tsx`、`pages/Meeting.tsx` |
| **验收工程** | `e2e/responsive.spec.ts`（6 档视口 × 首页/登录页断言 + 会中断点用例）+ CI `Responsive gate` 步骤 + `npm run verify:responsive` | `e2e/responsive.spec.ts`、`playwright.config.ts`、`.github/workflows/ci.yml` |

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
| 详情页（compact） | 转写 + 纪要并排堆叠、待办/复核在下面，一屏要滚很久 | 底部三标签（转写 / 纪要 / 待办），一次只显示一块；参会人并入「纪要」 |
| 会议页提示条（compact） | 三条提示全部展开，最多吃掉 3 行 | 折成一行 +「详情」展开，`role`/`aria-expanded` 齐全 |
| History 批量条（compact） | 列表顶部，选中后要往上滚才点得到 | `sticky bottom-2` 浮层，滚动时始终可达 |
| 后台审计表（≤1023） | 横向滚动后看不到「谁/哪一条」 | 首列 `position: sticky` 吸附 |

## 3 验收对照（方案第 6 节）

| # | 判据 | 结果 |
|---|---|---|
| A1 | 6 档视口 `scrollWidth - clientWidth == 0` | **通过**（5 个页面 × 6 档，全部 0） |
| A2 | 390px 下 `header.scrollWidth == header.clientWidth` | **通过**（`headerOverflow = 0`） |
| A3 | compact 主 CTA ≥56、其它控件 ≥44 | **通过**（CTA 62/64；≤1023 档 `smallTargets = 0`） |
| A4 | compact 下 `.reader-body` ≥16px | **通过**（会中实测 16px） |
| A5 | 首页同屏 CTA == 1 | **通过** |
| A6 | 768/1023 双栏、1024 出现分隔条 | **通过**（`e2e/responsive.spec.ts` 的会中断点用例：1024 有 `role="separator"`、1023 无但仍是双栏、390 只有当前标签；本轮在 dev 栈实跑通过；CI 默认 skip，需 `RESPONSIVE_MEETING=1` 才启用——原因见第 5 节） |
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
| e2e 全量 | `npx playwright test` | **19 passed / 2 failed / 1 skipped**；两条失败在**改造前 HEAD 上同样复现**（`git stash` 对照），属既有问题；skip 的是会中断点用例（CI 席位需登录，见第 5 节） |
| 响应式 e2e | `npx playwright test e2e/responsive.spec.ts` | 首页 6 档 + 登录页 6 档 + 顶栏折叠共 13 条全部通过（无溢出、CTA 唯一且 ≥56、触控 ≥40、输入 16px） |
| 会中断点 e2e | `PLAYWRIGHT_BASE_URL=http://localhost:5173 RESPONSIVE_MEETING=1 npx playwright test -g 会中断点` | 通过（1024 有分隔条 / 1023 双栏无分隔条 / 390 三标签且只显示当前块） |
| 详情页三标签（真机仿真） | 390×844 + 1440×900 实测 | 390：一次只显示一块、标签栏 55px、溢出 0；1440：四块同时可见、标签栏不渲染 |
| CI 门禁 | `.github/workflows/ci.yml` 的 `Responsive gate`（`vite preview` + `npm run verify:responsive`） | 已在本地用 `vite preview`（5174）复跑通过，确认不依赖后端 |
| 会中真机仿真 | Chromium 假麦克风 + 390×844 起会 15s | 三标签切换正常、控制条 65px、溢出 0、正文 16px、无 <40px 控件、无 JS 异常 |
| 字号迁移效果 | 1440 与 390 实测 `getComputedStyle` | 桌面 `text-meta=11px`、`text-ui=12px`（与迁移前一致）；390 分别 12px / 13px |
| 长会议渲染（方案 4.6 的「先测」） | 临时 harness 渲染真实 `TranscriptStream`，200/500/1000/2000 段，390×844 视口滚动 1.2s | 挂载 13–23ms；滚动帧间隔 **p50/p95 = 11/11ms（≈90fps）**，长帧 0–2 个，**未触发 <50fps 阈值** → 不引入 `react-window` |

后端未改一行代码，因此未重跑后端 766 项测试（本轮改动面 100% 在 `frontend/`）。

## 5 未完成与已知限制


| 项 | 状态 | 原因 / 后续 |
|---|---|---|
| 仍写死的字号 | **少量残留** | codemod 按护栏跳过了 21 处「同一 className 里已有 `md:`/`max-md:` 字号」的写法（如 `text-[11px] max-md:text-[12px]`）。这些位置已经各自处理过窄屏值，语义化收益为零，保持原样更安全 |
| 长会议虚拟滚动（方案 4.6） | **实测后不做** | 200/500/1000/2000 段实测：挂载 13–23ms、滚动帧间隔 p95 = 11ms（≈90fps）、无长任务，未达方案设定的「<50fps 或长任务 >200ms」门槛。harness 是临时的（测完已删），设备为桌面 Chrome 引擎仿真，真机仍需按 A10 复核 |
| History 筛选抽屉 / 筛选项折叠 | **未做** | `/history` 6 档视口均无横向溢出，筛选行本身已 `flex-wrap`；做抽屉需要重排交互且收益有限。已做的是批量操作条改底部吸附浮层 |
| 详情页第四标签「导图」 | **未做** | 导图是纪要栏里的一个切换按钮，不是独立面板；做成第四标签要么复制一份侧栏、要么改组件契约。当前三标签（转写/纪要/待办）已消除长滚动，导图仍从「纪要」标签里打开 |
| 真机 iOS Safari / Android Chrome（A10） | **未做** | 无真机。已用 Chromium 设备仿真 + 假麦克风覆盖布局与三标签，真机仍需按第 4 节的清单人工过一遍 |
| 会中断点 e2e 在 CI 默认 skip | **已知限制** | CI 的 e2e 席位 `REQUIRE_AUTH=1`，无法匿名起会。用例用 `RESPONSIVE_MEETING=1` 显式开启（本轮在 dev 栈跑通）；若要让它在 CI 常跑，需要给席位加一个可匿名的测试开关或预置 token |
| 平板横屏 1024×768 归 wide 档 | **已知取舍** | 1024 按方案归 wide（32px 控件）。iPad 横屏用鼠标/触控笔没问题，纯手指操作时控件偏小；若要覆盖，需把 1024 单独成档，属方案变更 |
## 6 回滚

改动全部在 `frontend/`（+ 文档），无后端/DB/接口变化。回滚方式：

- 整体：`git revert <本次提交>`；
- 单阶段：令牌与门禁脚本（P0）可保留，回滚 `TopBar/IdleHero/ControlBar`（P1）、`Meeting/ParticipantsPanel`（P2–P3）、`Sheet` 及其 6 个调用点（P5）互不影响；
- 窄屏样式集中在 `styles/index.css` 末尾三段媒体查询（≤1023 增强、≤767 增强、`data-pane` 隐藏与 `app-footer`），删掉即回到改造前布局。
