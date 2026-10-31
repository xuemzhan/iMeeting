# 2026-10-31 主模板验证证据

<!-- doc-governance: IM-EVD-007 -->
> 文档类型：证据；状态：阶段快照；日期：2026-10-31。
> 维护责任：待指派（iMeeting 维护者）。[修复报告](../../../reports/2026-10-31-template-primary.md) · [上一轮身份证据](../2026-10-31-identity-unify/README.md)。

| 文件 | 来源与范围 |
|---|---|
| [backend-tests.xml](backend-tests.xml) | 后端完整 pytest，715 通过、0 失败、0 跳过；404.82 秒。包含本轮 8 项主模板回归 |
| [verification.json](verification.json) | 命令、退出码、环境、产物与未验证边界 |

在 `iMeeting/` 目录复现：

```powershell
$env:PYTHONUTF8='1'
.\.venv-production-review\Scripts\python.exe -m ruff check backend/src backend/tests backend/migrations
.\.venv-production-review\Scripts\python.exe -m pytest backend/tests -q --show-capture=no --disable-warnings --junitxml=docs/reviews/evidence/2026-10-31-template-primary/backend-tests.xml
```

文档检查在联合工作区根目录执行 `python scripts/docs_governance.py --write` 后再执行 `python scripts/docs_governance.py`。

迁移用例在 `20261031_03` 建出的旧 `voiceprint_template` 表上直接执行 `20261031_07`：验证补列、部分唯一索引拒绝同一声纹的第二条主模板、不同声纹各自允许一条、非主模板不受限制，以及降级移除列。

前端本轮改动了模板摘要组件，在 `iMeeting/frontend/` 目录复现：

```powershell
npm run typecheck
npm test -- --run
npm run build
```

结果：类型检查通过、89 项单元测试通过、生产构建成功。Playwright 端到端本轮没有重跑（需要临时启动服务与浏览器）。

未执行生产库迁移、真实模型/供应商调用、目标机器性能、TLS/Docker、长稳、漏洞扫描或生产发布。
