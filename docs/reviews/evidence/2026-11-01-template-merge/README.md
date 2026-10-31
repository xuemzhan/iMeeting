# 2026-11-01 模板合并验证证据

<!-- doc-governance: IM-EVD-008 -->
> 文档类型：证据；状态：阶段快照；日期：2026-11-01。
> 维护责任：待指派（iMeeting 维护者）。[修复报告](../../../reports/2026-11-01-template-merge.md) · [上一轮主模板证据](../2026-10-31-template-primary/README.md)。

| 文件 | 来源与范围 |
|---|---|
| [backend-tests.xml](backend-tests.xml) | 后端完整 pytest，722 通过、0 失败、0 跳过；425.45 秒。包含本轮 5 项模板合并回归 |
| [verification.json](verification.json) | 命令、退出码、环境、产物与未验证边界 |

在 `iMeeting/` 目录复现：

```powershell
$env:PYTHONUTF8='1'
.\.venv-production-review\Scripts\python.exe -m ruff check backend/src backend/tests backend/migrations
.\.venv-production-review\Scripts\python.exe -m pytest backend/tests -q --show-capture=no --disable-warnings --junitxml=docs/reviews/evidence/2026-11-01-template-merge/backend-tests.xml
```

文档检查在联合工作区根目录执行 `python scripts/docs_governance.py --write` 后再执行 `python scripts/docs_governance.py`。

本轮的合并用例同时覆盖服务层与接口层：来源改挂与质心重算、同一组来源可重放、跨声纹/自合并/停用模板被拒、主模板标记转移、接口鉴权 + 审计 + 重放 409。前端本轮未改动，没有重跑 Vitest/Playwright。
