# 2026-10-31 身份统一验证证据

<!-- doc-governance: IM-EVD-006 -->
> 文档类型：证据；状态：阶段快照；日期：2026-10-31。
> 维护责任：待指派（iMeeting 维护者）。[修复报告](../../../reports/2026-10-31-identity-unify.md) · [上一轮 R6 证据](../2026-10-31-r6/README.md)。

| 文件 | 来源与范围 |
|---|---|
| [backend-tests.xml](backend-tests.xml) | 后端完整 pytest，707 通过、0 失败、0 跳过；414.99 秒。包含本轮 8 项声纹—人员关联回归 |
| [verification.json](verification.json) | 命令、退出码、环境、产物与未验证边界 |

在 `iMeeting/` 目录复现：

```powershell
$env:PYTHONUTF8='1'
.\.venv-production-review\Scripts\python.exe -m ruff check backend/src backend/tests backend/migrations
.\.venv-production-review\Scripts\python.exe -m pytest backend/tests -q --show-capture=no --disable-warnings --junitxml=docs/reviews/evidence/2026-10-31-identity-unify/backend-tests.xml
```

文档检查在联合工作区根目录执行 `python scripts/docs_governance.py --write` 后再执行 `python scripts/docs_governance.py`。

迁移用例在最小旧表结构（`meeting/person/meeting_participant/voiceprint`）上直接执行 `20261031_06` 的升级、回填与降级，覆盖“同 owner 唯一命中”“跨 owner 拒绝”“无参会关系留空”“旧的无 owner 数据可回填”四种行。

未执行生产库迁移、真实模型/供应商调用、目标机器性能、TLS/Docker、长稳、漏洞扫描或生产发布；前端本轮未改动，也没有重跑 Vitest/Playwright。
