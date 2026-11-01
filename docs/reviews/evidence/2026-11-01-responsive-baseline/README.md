# 2026-11-01 响应式改造前基线量测

<!-- doc-governance: IM-EVD-011 -->
> 文档类型：证据；状态：阶段快照；日期：2026-11-01。
> 维护责任：待指派（iMeeting 维护者）。[台账](../../../README.md) · [规则](../../../governance/README.md)。
> 用途：[自适应改造方案 IM-PLN-001](../../../plans/2026-11-01-responsive-ui-adaptation.md) 的「改造前」对照基线。

## 环境

| 项 | 值 |
|---|---|
| 页面 | 本地 dev server `http://localhost:5173`（Vite 6 + React 19），未登录匿名首页 |
| 浏览器 | Playwright + 本机 Chrome（`channel: 'chrome'`，headless） |
| 视口 | 390×844 / 360×780（`isMobile`）、768×1024 / 1024×768 |
| 被测版本 | `0f189b4` 工作树（响应式改造尚未开始） |

## 结果

`measurements.json` 原始输出摘要：

| 视口 | 横向溢出 px | 顶栏溢出 px | 三张特性卡宽度 | 触控<40px 控件 | 「开始会议」按钮数 |
|---|---|---|---|---|---|
| 390×844 | **25** | **25** | 106 / 106 / 106 | 5 / 8 | **2** |
| 360×780 | **55** | **55** | 96 / 96 / 96 | 5 / 8 | **2** |
| 768×1024 | 0 | 0 | 205 / 205 / 205 | 5 / 8 | 2 |
| 1024×768 | 0 | 0 | 205 / 205 / 205 | 5 / 8 | 2 |

说明：`innerWidth` 在手机上被内容顶到 415px（`visualViewport.width` 仍是 390/360），
即真机上整页可横向拖动、内容被整体缩小；顶栏自身就需要 415px。

## 文件

| 文件 | 含义 |
|---|---|
| [responsive_probe.mjs](responsive_probe.mjs) | 量测脚本：四视口加载首页，采集横向溢出、顶栏溢出、特性卡宽度、触控尺寸、CTA 数量，并整页截图 |
| [measurements.json](measurements.json) | 上述脚本的原始输出（UTF-8 BOM，PowerShell 重定向所致） |
| [phone-390-home.png](phone-390-home.png) | 390px 首页截图（可看到品牌名换行、卡片挤压、重复按钮） |
| [phone-360-home.png](phone-360-home.png) | 360px 首页截图 |
| [tablet-768-home.png](tablet-768-home.png) | 768px 首页截图（仍走手机单栏模式） |
| [tablet-1024-home.png](tablet-1024-home.png) | 1024px 首页截图（进入桌面布局） |

## 复现

```powershell
cd frontend
# 需 dev server 已在 5173 运行
Copy-Item ..\docs\reviews\evidence\2026-11-01-responsive-baseline\responsive_probe.mjs .tmp\probe.mjs
$env:OUT_DIR = (Resolve-Path ..\docs\reviews\evidence\2026-11-01-responsive-baseline).Path
node .tmp\probe.mjs
```

（脚本放在 `frontend/.tmp/` 下运行是为了能解析 `frontend/node_modules` 里的 `@playwright/test`；
`.tmp/` 已在 `.gitignore` 内。）

## 边界

- 只量测了**未登录的首页**；会议进行中的转写/纪要页需要麦克风与会话，未纳入本轮基线。
- 未使用真机与 Safari；`env(safe-area-inset-*)`、软键盘遮挡等真机行为未在本证据内。
- 截图是 Chromium 渲染结果，与 iOS Safari 存在字体度量差异。
- 触控统计口径为 `button` 与 `a.btn` 的高度 < 40px；纯装饰元素未纳入。
