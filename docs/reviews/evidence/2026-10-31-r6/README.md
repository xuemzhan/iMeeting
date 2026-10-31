# 2026-10-31 R6 优化验证证据

<!-- doc-governance: IM-EVD-004 -->
> 文档类型：证据；状态：阶段快照；日期：2026-10-31。
> 维护责任：待指派（iMeeting 维护者）。[修复报告](../../../reports/2026-10-31-r6-optimization.md) · [上一轮证据](../2026-10-31-fixes/README.md)。

| 文件 | 来源与范围 |
|---|---|
| [backend-tests.xml](backend-tests.xml) | 后端完整 pytest，699 通过、0 失败、0 跳过；413.58 秒。包含本轮 10 项新增回归与上一轮的 46 项身份/缺口回归 |
| [verification.json](verification.json) | 命令、退出码、环境、产物与未验证边界 |
| [benchmark](../../../../benchmarks/template_rebuild_20261031/README.md) | 模板重建取样缓存与场景粗筛的同机对照：脚本、原始 JSON 与口径 |

本轮测量材料不在本目录：重建/匹配基准与原始输出放在[基准目录的说明页](../../../../benchmarks/template_rebuild_20261031/README.md)，与实验材料同址维护。

在 `iMeeting/` 目录复现后端检查：

```powershell
$env:PYTHONUTF8='1'
.\.venv-production-review\Scripts\python.exe -m ruff check backend/src backend/tests backend/migrations
.\.venv-production-review\Scripts\python.exe -m pytest backend/tests -q --show-capture=no --disable-warnings --junitxml=docs/reviews/evidence/2026-10-31-r6/backend-tests.xml
```

文档检查在联合工作区根目录执行 `python scripts/docs_governance.py --write` 后再执行 `python scripts/docs_governance.py`。

测试夹具仍按测试建立临时 SQLite、隔离配置、禁用模型与网络 LLM。本轮新增的两张表由 `init_db` 的 `create_all` 覆盖，另有一条迁移用例在旧 03/04 表结构上直接执行 05 的升级、降级与再升级。

未执行生产库迁移、真实模型/供应商调用、目标机器性能、TLS/Docker、长稳、漏洞扫描或生产发布；前端本轮没有改动，因此没有重跑 Vitest/Playwright，上一轮的前端数字不覆盖本轮接口。
