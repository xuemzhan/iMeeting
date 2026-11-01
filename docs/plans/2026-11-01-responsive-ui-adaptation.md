# 2026-11-01 手机 / 平板自适应改造方案

<!-- doc-governance: IM-PLN-001 -->

> 文档类型：计划；状态：已实施（P0–P5；P6 部分，codemod 与虚拟滚动未做）；日期：2026-11-01。
> 维护责任：待指派（iMeeting 维护者）。[台账](../README.md) · [规则](../governance/README.md)。
> 关联：[工程结构](../architecture/project-structure.md)、[前端说明](../../frontend/README.md)、[生产可用性清单](../production-readiness.md)。
> 基线证据：[IM-EVD-011](../reviews/evidence/2026-11-01-responsive-baseline/README.md)（改造前四视口量测与截图）。
> 状态：**已实施**（P0–P5 全部；P6 只做「语义类字号在 compact 抬一档」，codemod 与长列表虚拟滚动未做）。
> 落地结果与未完成项见[实施报告 IM-RPT-009](../reports/2026-11-01-responsive-ui.md)，证据见 [IM-EVD-012](../reviews/evidence/2026-11-01-responsive/README.md)。

让「总师会议智能伙伴」在 360–1440px 全区间可用，重点补齐 **手机（≤479）** 与
**平板（768–1023）** 两档，且不改变 1440 桌面端现有视觉与交互基线。

三条结果目标：

1. **不横向溢出**：任意 320–1440px 宽度下 `document.scrollWidth == clientWidth`，
   用户永远不需要左右拖动页面。
2. **可点即可用**：主要控件命中区 ≥44×44（compact 主 CTA ≥56 高），正文 ≥16px，
   底部主按钮不被 iOS Home Indicator 遮挡。
3. **布局随设备变形态**：手机单栏 + 底部标签、平板双栏、桌面三区可拖分栏，
   而不是「把桌面版缩小」。

## 2 现状与证据

### 2.1 量化量测（改造前，dev server 实跑）

用 Playwright 在四档视口加载首页（脚本与截图已入库，见
[基线证据 IM-EVD-011](../reviews/evidence/2026-11-01-responsive-baseline/README.md)）：

| 视口                      | 布局宽  | 文档实际宽 | 横向溢出      | header 需要/可用 | 特性卡宽度 | 触控<40 的控件 | 「开始会议」按钮数 |
| ----------------------- | ---- | ----- | --------- | ------------ | ----- | --------- | --------- |
| 390×844（iPhone 13）      | 390  | 415   | **+25px** | 415 / 390    | 106×3 | 5 / 8     | **2**     |
| 360×780（小屏安卓/iPhone SE） | 360  | 415   | **+55px** | 415 / 360    | 96×3  | 5 / 8     | **2**     |
| 768×1024（iPad 竖屏）       | 768  | 768   | 0         | 768 / 768    | 205×3 | 5 / 8     | 2         |
| 1024×768（iPad 横屏）       | 1024 | 1024  | 0         | 1024 / 1024  | 205×3 | 5 / 8     | 2         |

读法：390px 宽的手机上，文档被内容顶到 **415px**（`visualViewport.width` 仍是 390），
即整页横向可拖动、内容整体缩小约 6%；顶栏自身就需要 415px，靠挤压品牌文字换行收场。
360px 屏上这个差值扩大到 55px。三张特性卡在手机上只有 96–106px 宽，中文被压成 3 行。

### 2.2 结构证据（代码位置）

| 现象                  | 位置                                                                                                                                                                                                      | 说明                                           |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| 视口声明本身没问题           | `frontend/index.html:5`                                                                                                                                                                                 | 已有 `viewport-fit=cover`，缺的是后续布局              |
| 只有 1 个断点            | `frontend/src/styles/index.css:435`                                                                                                                                                                     | 仅 `@media (max-width:1023px)` 隐藏转写面板，无 768 档 |
| 全站几乎不用响应式前缀         | 全仓 `sm:` 1 处、`md:` 2 处、`lg:` 8 处                                                                                                                                                                        | 其余全部硬编码，等于桌面单版式                              |
| 顶栏固定高、内容不收缩         | `frontend/src/components/TopBar.tsx:38-41`（`h-14`）、`:54-59`（品牌 15px 不截断）、`:68-78`（标题 `max-w-[280px]`）、`:111-135`（五个入口常驻）                                                                                | 手机端溢出的直接来源                                   |
| 首页三列固定              | `frontend/src/components/IdleHero.tsx:93`（`grid-cols-3`）、`:69`（`px-6 py-8`）、`:71`（`max-w-[640px]`）、`:78`（`text-[28px]`）                                                                                 | 手机仅 96–106px/卡                               |
| 首页两个相同 CTA          | `IdleHero.tsx`（Hero 大按钮）+ `frontend/src/components/ControlBar.tsx:31-45`（页脚同款按钮）                                                                                                                        | 手机首屏出现两次「开始会议」                               |
| 会议页只有「粗」移动模式        | `frontend/src/pages/Meeting.tsx:272-295`（两标签，`lg:hidden`）、`:302-306`（`data-hidden-mobile`）、`:341-347`（桌面纪要栏 `hidden lg:flex`，宽度写 px + `maxWidth:60%`）、`:384-421`（手机端复制一份纪要面板）、`:428-433`（页脚 `h-[68px]`） | 能切换但无 768 档；纪要面板整段复制带来维护双份                   |
| 分隔条仅桌面              | `frontend/src/components/PaneSplitter.tsx:114`                                                                                                                                                          | `lg:block`，实现正确，平板需另一种解法                     |
| 控件尺寸偏小              | `index.css:182-200`（`.btn` 32px 高 / 13px 字）、`:263-272`（`.chip` 22/11px）、`:274-283`（`.panel-header` 40/13px）                                                                                             | 手机可点区低于 44                                   |
| 字号硬编码约 423 处        | `text-[10px]`65 / `text-[11px]`204 / `text-[12px]`154（另 13px 24 处）                                                                                                                                      | 手机正文 11–13px 偏小                              |
| 高度用 `100%` 而非 `dvh` | `index.css:120-123`、`:126-136`（`body` 亦无 `safe-area`）                                                                                                                                                   | iOS 地址栏伸缩时高度跳变、底部按钮有被遮挡风险                    |
| 转写列表未虚拟化            | `frontend/src/components/TranscriptStream.tsx:155`                                                                                                                                                      | 长会议（1000+ 段）在中低端手机上滚动开销需实测                   |
| 弹窗按桌面尺寸写死           | `MindMapDialog.tsx:46-48`（`h-[82vh]`/`max-w-[1120px]`）、`ShareDialog.tsx:53-54`（`max-w-[560px]`）、`SettingsPanel.tsx:61-69`（右侧抽屉 `max-w-[380px]`）                                                         | 手机上留白过大/内容挤压                                 |
| 输入框 12px            | `Login.tsx:139`、多个面板输入                                                                                                                                                                                  | iOS 聚焦 <16px 会自动放大页面                         |
| 验收脚本只测桌面            | `frontend/scripts/verify-features.mjs:81`（`1440×900`）、`frontend/playwright.config.ts:25`（仅 `Desktop Chrome`）                                                                                            | 改造必须同时扩展验收矩阵，否则回归无感                          |

### 2.3 已有可复用资产

* `data-hidden-mobile` + 媒体查询的「按标签隐藏面板」机制（`index.css:435`）可以保留，改为断点化。
* 分隔条已支持键盘与指针捕获（`PaneSplitter.tsx:56-92`），桌面端无需重做。
* 设计令牌已集中在 `index.css` 的 `:root` / `@theme`，适合作为响应式的唯一改动入口。
* `prefers-reduced-motion` 已全局处理（`index.css:473`）。

## 3 非目标

* 不改后端接口、数据库、ASR/LLM 链路；不动 `store/session.ts` 的会话状态机。
* 不新增主题（暗色）与品牌视觉重设计，只做自适应与可达性。
* 不做原生壳 / 小程序 / 独立移动端 App；不做多语言。
* 不重构信息架构（路由与页面职责不变）。
* 不在本方案内引入虚拟滚动库改造，除非第 5 节 P6 的实测数据显示必要。
* 不重做桌面 1440 的视觉与交互（只允许「同一设计语言的响应式收放」）。

## 4 设计方案

### 4.1 断点与三种布局模式

| 名称   | 宽度        | 布局模式           | 说明                                  |
| ---- | --------- | -------------- | ----------------------------------- |
| `xs` | 320–479   | compact        | 单栏 + 底部标签 + 底部控制条；顶栏收成品牌图标 + 标题 + ⋮ |
| `sm` | 480–767   | compact（宽松）    | 同 compact，特性卡改 3 列、正文 16px 保持       |
| `md` | 768–1023  | **medium（新增）** | 会议页双栏 3:2（转写/纪要），无分隔条；参会人横向条；控制条底部  |
| `lg` | 1024–1439 | wide           | 现有三区布局 + 可拖分栏                       |
| `xl` | ≥1440     | wide           | 同上，容器居中上限 1440                      |

规则：**只有 `compact` 与 `medium/wide` 两种交互语义**。`compact` 用标签切换与抽屉，
`medium/wide` 用并排；避免出现「平板既像手机又像桌面」的第三套例外。

### 4.2 设计令牌与字号体系

新增（`index.css`，`@theme` + `:root`）：

| 令牌                 | compact                       | medium | wide    | 用途                      |
| ------------------ | ----------------------------- | ------ | ------- | ----------------------- |
| `--tap-min`        | 44px                          | 40px   | 32px    | 可点区最小高度                 |
| `--btn-height`     | 44px                          | 36px   | 32px    | `.btn` 高度（桌面不变）         |
| `--chip-height`    | 24px                          | 22px   | 22px    | `.chip`                 |
| `--panel-header-h` | 48px                          | 44px   | 40px    | `.panel-header`         |
| `--fs-body`        | 16px                          | 15px   | 15px    | 转写/纪要正文（`.reader-body`） |
| `--fs-ui`          | 14px                          | 13px   | 13px    | 常规 UI 文本                |
| `--fs-meta`        | 12.5px                        | 12px   | 11–12px | 辅助信息（原 11px 那批）         |
| `--safe-bottom`    | `env(safe-area-inset-bottom)` | 同      | 0       | 页脚与抽屉留白                 |

字号迁移策略（关键：423 处硬编码，**不做一次性全量替换**）：

1. 新增语义类 `.t-title / .t-body / .t-ui / .t-meta / .t-caption`，用 `var(--fs-*)`。
2. 用可重复执行的 codemod（`frontend/scripts/codemod-font-scale.mjs`，仅替换整类名，
   不碰其它 token）把 `text-[11px]`→`.t-meta`、`text-[12px]`→`.t-meta`、
   `text-[13px]`→`.t-ui`，按文件分批提交。
3. 每批提交后跑 1440 视口截图对比（`toHaveScreenshot`），确保桌面像素差异为零。

### 4.3 视口、安全区与键盘

* `html, body, #root` 高度改 `100dvh`，并保留 `100vh` 回退；
  `--app-height` 由 `visualViewport` 监听写入（iOS 旧版/软键盘场景兜底）。
* 页脚、底部抽屉加 `padding-bottom: max(8px, var(--safe-bottom))`；
  顶栏在 standalone（PWA）下加 `padding-top: env(safe-area-inset-top)`。
* 输入类控件统一 `font-size: 16px`（compact），消除 iOS 聚焦自动放大。
* `overscroll-behavior`：`body` 保留 `none`，滚动容器改 `contain`（已有 `.overscroll-contain` 用法可复用）。

### 4.4 逐页改造

#### 4.4.1 顶栏（`TopBar.tsx`）

* compact：`品牌图标(28) + 会议标题(可截断) + 状态胶囊`，其余入口（历史/知识库/声纹/设置/账号）
  收进右侧「⋮」菜单（新增 `OverflowMenu`，与 `AccountEntry` 共用弹层逻辑）。
* 计时与波形在 compact 移到「实时状态条」（标题下方 32px 一行），避免与状态胶囊抢宽度。
* 标题输入 `min-w-0 flex-1 truncate`，`max-w` 只在 ≥md 生效。
* 目标：390px 下顶栏 `scrollWidth == clientWidth`（量测项）。

#### 4.4.2 首页 / `IdleHero`

* 特性卡：`xs` 单列（图标左、文案右，行高 44），`sm` 3 列，`md+` 3 列但卡宽 ≥180。
* 标题 `28px → clamp(22px, 6vw, 28px)`；副标题 13px；上下留白 `py-8 → py-5`。
* **CTA 去重**：compact 只在底部控制条保留「开始会议」（拇指区），Hero 区改一行提示文案；
  ≥md 保留 Hero 大按钮并隐藏页脚 CTA（idle 态）。二者互斥，由 `layoutMode` 决定。
* 最近会议/日程列表：整行可点高度 ≥56，元信息换行到两行（`flex-wrap`）。

#### 4.4.3 会议页 compact（`Meeting.tsx`）

* 结构：`TopBar(紧凑)` → `状态条` → `参会人 chips 横滑条` → `主面板` → `底部标签栏` → `底部控制条`。
* 底部标签栏三档：**转写 / 纪要 / 参会人**（新增第三项，把现在顶部的参会人块收进来），
  带条数与未确认提醒点；保留 `data-hidden-mobile` 机制改为 `data-pane` 状态。
* 手势：左右滑动切换标签（阈值 40px 且 |dx|>|dy|，带 0.3px/ms 速度判定）；
  在文本可选中区域、横向滚动容器内不拦截；`prefers-reduced-motion` 时仅切页不滑动动画。
* 控制条：高 64 + 安全区，主按钮 56 圆形（开始/停止），暂停与切句为 44 次级按钮，
  设备选择收进「更多」抽屉。
* 纪要面板：**不再复制一份**，改为同一组件在不同容器渲染（`<MinutesPanel>` 只保留一个实例，
  由容器 `display` 决定位置），消除 `Meeting.tsx:384-421` 的重复代码。
* 转写排版：`.reader-body` 16px、段间距 8px、说话人标签 sticky、时间戳右对齐可点跳转（≥44 命中）。

#### 4.4.4 会议页 medium（768–1023）

* 双栏：转写 `flex-2` / 纪要 `flex-1`（约 60/40），栏间距 12；不显示分隔条（保留
  `aria-separator` 的键盘能力但视觉隐藏，或直接移除并在 ≥lg 才挂载）。
* 参会人：顶部横向 chips 条（可横向滚动），点击展开右侧抽屉。
* 页脚控制条同 desktop 尺寸。

#### 4.4.5 列表类页面

* `History`：筛选行在 compact 折成两行 + 「筛选」按钮打开抽屉；批量操作条改为底部浮层
  （选中态才出现）；行元信息 `flex-wrap`；分页控件保持 44 命中。
* `MeetingDetail`：compact 下按「转写 / 纪要 / 待办 / 导图」四标签页切换，替代并排。
* `Voiceprints` / `Knowledge`：卡片单列；筛选项收进抽屉。
* `admin/*`：表格外层 `overflow-x-auto` + 首列 `position: sticky`；表单 `grid-cols-1`。

#### 4.4.6 弹窗体系

* 统一新增 `Sheet` 基础组件：compact 下从底部升起（圆角 16、抓手、`max-h:92dvh`、下拉关闭、
  背景滚动锁），`md+` 维持现有居中卡片。
* 迁移顺序：`ShareDialog`、`MembersDialog`、`MeetingInfoEditor`、`SegmentEditDialog`、
  `SettingsPanel`（compact 全屏）、`MinutesVersionsDialog` 与 `MindMapDialog`（compact 全屏）。

#### 4.4.7 登录 / 改密 / 共享页

* 卡片 `w-full max-w-[360px]` 保持，`p-6 → p-5`；输入与按钮全宽、16px；
  错误提示改 `role="alert"` 并允许换行。
* `SharedMeeting`：正文 16px、音频播放器 sticky 底部、目录折叠。

#### 4.4.8 提示条与空态

* 顶部三处提示条（`Meeting.tsx:188-251`）在 compact 改为可折叠单行 + 「详情」展开，
  避免占用首屏高度；空态文案 13px 并居中。

### 4.5 交互与动效

* 触控：`touch-action: manipulation`（按钮）、`-webkit-tap-highlight-color: transparent`。
* 手势与选择共存：滑动判定前检查 `window.getSelection()?.isCollapsed`；
  文本编辑中的 `textarea` 内不启用切页手势。
* 动效：切页 220ms `ease-out`，抽屉 260ms；全部受 `prefers-reduced-motion` 降级。
* 反馈：开始/停止提供 `navigator.vibrate?.(10)`（存在才调用），并提供设置项关闭。

### 4.6 性能

* 先测：在 500/1000/2000 段会议下记录 compact 滚动 FPS 与长任务；
  若 <50fps 或长任务 >200ms，则对 `TranscriptStream` 引入已有依赖 `react-window` 的
  可变高度列表（`VariableSizeList`），并保留「跳到底部」与「按时间定位」。
* 分段渲染兜底：先只渲染最近 200 段 + 「加载更早」，无需新依赖。
* 图标与头像保持 SVG/文字首字母，避免新增位图。

## 5 实施步骤

| 阶段     | 内容                                                                                                                   | 依赖    | 主要产物                                                                                       | 预估   |
| ------ | -------------------------------------------------------------------------------------------------------------------- | ----- | ------------------------------------------------------------------------------------------ | ---- |
| **P0** | 令牌、断点、dvh/安全区、`useLayoutMode` 钩子；把量测脚本入库为 `frontend/scripts/responsive-probe.mjs` 并在 CI 增加「无横向溢出」门禁；Playwright 增设备项目 | 无     | `index.css`、`hooks/useLayoutMode.ts`、`scripts/responsive-probe.mjs`、`playwright.config.ts` | 0.5d |
| **P1** | 顶栏紧凑化 + `OverflowMenu`；首页 `IdleHero` 重排与 CTA 去重                                                                      | P0    | `TopBar.tsx`、`OverflowMenu.tsx`、`IdleHero.tsx`、`ControlBar.tsx`                            | 1d   |
| **P2** | 会议页 compact：三标签 + 手势 + 控制条 + 参会人 chips + 纪要单实例化                                                                      | P0,P1 | `Meeting.tsx`、`ParticipantsPanel.tsx`、`ControlBar.tsx`、`MinutePanel` 容器化                   | 1.5d |
| **P3** | 会议页 medium 双栏；分隔条仅 ≥lg                                                                                               | P2    | `Meeting.tsx`、`PaneSplitter.tsx` 挂载条件                                                      | 0.5d |
| **P4** | 列表类页面（History / MeetingDetail / Knowledge / Voiceprints / admin）                                                     | P0    | 上述页面与 `HistoryFilters`/`HistoryBulkActions`                                                | 1.5d |
| **P5** | `Sheet` 组件与弹窗迁移、登录/改密/共享页、提示条折叠                                                                                      | P0    | `components/Sheet.tsx` + 6 个弹窗、`Login.tsx`、`SharedMeeting.tsx`                             | 1d   |
| **P6** | 字号 codemod 分批迁移（按目录切 4 批）、长列表性能实测与取舍、真机验收                                                                            | P1–P5 | `scripts/codemod-font-scale.mjs`、分批提交、性能记录                                                 | 1d   |

总计约 **7 人日**。每阶段独立提交、独立验收，可单独回滚。

## 6 验收标准（可判定）

| #   | 验收项     | 判定                                                                 | 验证方式                                                                             |
| --- | ------- | ------------------------------------------------------------------ | -------------------------------------------------------------------------------- |
| A1  | 无横向溢出   | 6 档宽度（360/390/430/768/1024/1440）下 `scrollWidth - clientWidth == 0` | `node frontend/scripts/responsive-probe.mjs --base http://localhost:5173` 输出全部 0 |
| A2  | 顶栏不溢出   | 390px 下 `header.scrollWidth == header.clientWidth`                 | 同上（探针输出 `headerOverflow`）                                                        |
| A3  | 触控可用    | compact 下主 CTA ≥56 高、其它交互控件 ≥44 高                                  | 探针 `smallTargets == 0`（白名单：纯装饰图标）                                                |
| A4  | 字号可读    | compact 下 `.reader-body` 计算值 ≥16px，辅助文本 ≥12px                      | `vitest` 组件测试断言 `getComputedStyle`                                               |
| A5  | CTA 不重复 | 首页同屏「开始会议」按钮数 == 1                                                 | 探针 `startCtaCount == 1`                                                          |
| A6  | 断点行为    | 768px 显示双栏、1023px 仍双栏、1024px 出现分隔条                                 | `e2e/responsive.spec.ts` 断言 `separator` 可见性                                      |
| A7  | 安全区     | compact 页脚底部含 `env(safe-area-inset-bottom)`                        | `e2e` 断言计算样式 padding-bottom > 0（模拟 iOS 设备）                                       |
| A8  | 桌面零回归   | 1440×900 截图与基线一致（仅允许预期变化的区域）                                       | `toHaveScreenshot` 基线对比                                                          |
| A9  | 既有验收购建  | `verify-features.mjs` 的 14 项断言在 1440 仍全绿，并新增 compact 变体            | `node frontend/scripts/verify-features.mjs`（扩展后）                                 |
| A10 | 真机      | iOS Safari + Android Chrome 各 1 台：无横向滚动、底部按钮不被遮挡、录制 30s 无布局跳动      | 人工清单（记录机型/系统版本进证据目录）                                                             |

## 7 风险与应对

| 风险                | 触发条件                       | 应对                                             |
| ----------------- | -------------------------- | ---------------------------------------------- |
| 字号 codemod 误伤桌面观感 | 替换后 1440 截图出现差异            | 每批只改一个目录 + `toHaveScreenshot` 门禁；差异非零即回退该批     |
| 手势与文本选择冲突         | 用户在段落上横向滑动想选字              | 判定前检查 selection；编辑态禁用；阈值 40px + 方向锁            |
| `dvh` 兼容性         | 旧版 iOS WebView / 微信内核      | 保留 `100vh` 回退 + `--app-height` JS 写入           |
| 平板双栏挤压转写          | 768px 时纪要列过窄导致中文竖排         | 纪要列 `min-width: 280px`，不足则退化为单栏标签模式            |
| 纪要面板单实例化引入状态错乱    | 同一 `MinutesPanel` 在不同容器间移动 | 用 CSS 容器控制位置、不卸载组件；用现有 `session` 断言回归          |
| 长会议滚动卡顿           | 1000+ 段、低端机                | P6 实测；不满意则启用 `react-window` 或「最近 200 段 + 加载更早」 |
| 移动端录入被键盘遮挡        | 会议信息/纪要编辑聚焦                | `visualViewport` 监听 + 抽屉 `max-height` 跟随       |
| CI 无设备模拟导致回归无感    | 只跑 Desktop Chrome          | P0 起把 4 个设备项目加入 `playwright.config.ts` 并设为必过   |

## 8 回滚方案

* 全部改动在新分支 `feat/responsive-ui`，按 P0–P6 分 6 个提交，逐阶段可 `git revert` 单个提交。
* 令牌集中在 `frontend/src/styles/index.css`；断点失效时只需还原该文件 + 各组件类名批次。
* 新增文件（`useLayoutMode.ts`、`Sheet.tsx`、`OverflowMenu.tsx`、`responsive-probe.mjs`）
  都是**增量**，删除即恢复原状态；不改后端、不改 store 结构。
* 移动端入口通过 `layoutMode` 开关：紧急情况下可强制 `document.documentElement.dataset.layout="wide"` 回到桌面排版。

## 9 待确认问题（每项给默认，可直接回「按默认」）

1. 视频/音频类功能是否要求在手机上也能开会（含麦克风采集与后台保持）？
   **默认：是**，按本方案 compact 会议页设计；若只要求「手机上查看与回顾」，P2 可缩为只读态。
2. 首页 compact 隐藏 Hero 大按钮、只保留底部 CTA？
   **默认：是。**
3. 会议页 compact 是否加「参会人」第三标签（取代现在的顶部独立块）？
   **默认：是。**
4. 平板 768–1023 强制双栏（转写 60 / 纪要 40）？
   **默认：是**；若希望平板也用标签，请说明。
5. 是否允许顶栏把 历史/知识库/声纹/设置 收进「⋮」？
   **默认：是**（紧凑模式）；≥md 恢复常驻图标。
6. 是否需要 iOS 添加到主屏（standalone）适配？
   **默认：只做安全区与 dvh，不改 manifest 的 display。**
7. 长列表是否直接引入虚拟滚动？
   **默认：先实测（P6），达标不引入。**

## 10 交付物清单

* 代码：P0–P6 六个提交（见第 5 节）。
* 脚本：`frontend/scripts/responsive-probe.mjs`（量测 + CI 门禁）、`frontend/scripts/codemod-font-scale.mjs`。
* 测试：`frontend/e2e/responsive.spec.ts`、`useLayoutMode`/`Sheet` 的 vitest 组件测试、
  `playwright.config.ts` 设备矩阵。
* 文档：本方案定稿、改造完成后的阶段报告（`docs/reports/YYYY-MM-DD-responsive-ui.md`）
  与证据目录（各视口截图 + 探针 JSON + 真机记录）。

