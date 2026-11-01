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
| `shots/` | 改造后截图：`phone-360/390`、`phablet-430`、`tablet-768/1024`、`desktop-1440` 各页一张，另含真机会中三标签截图 `meeting-390-transcript/minutes/participants.png`。 |

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

## 5 说明

- 截图含本地会议标题，均为测试生成的数据；临时会议记录（#20–#23）在验证后已从开发库清除，库回到 19 场。
- 未做的验证：真机 iOS Safari / Android Chrome（方案 A10）、会中 500/1000/2000 段的滚动帧率实测（方案 4.6）。
