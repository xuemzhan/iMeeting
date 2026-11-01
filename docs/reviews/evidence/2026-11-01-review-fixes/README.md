# 2026-11-01 复核修复证据

<!-- doc-governance: IM-EVD-009 -->
> 文档类型：证据；状态：阶段快照；日期：2026-11-01。
> 维护责任：待指派（iMeeting 维护者）。[修复报告](../../../reports/2026-11-01-review-fixes.md)。

- [修复前反例](review-probes-before.xml)：四项规范断言均失败，证明批准样本污染、缓存版本缺失、推理写锁与父对象清理问题；原临时探针已迁为常规测试。
- [完整后端回归](backend-tests.xml)：728通过、0失败、0跳过；包含新增六项测试（三种模型签名变化、推理锁、清理依赖及批准来源保护），与定向运行重叠，不重复累加。
- [验证记录](verification.json)：实际命令、测试摘要、退出码、当前源文件哈希与验证边界。

在 `iMeeting/` 下执行：

```powershell
.\.venv-production-review\Scripts\python.exe -m pytest backend/tests/test_review_followup_fixes.py backend/tests/test_template_rebuild_optimization.py -q --tb=short --show-capture=no --disable-warnings
.\.venv-production-review\Scripts\python.exe -m pytest backend/tests -q --tb=short --show-capture=no --disable-warnings --junitxml=docs/reviews/evidence/2026-11-01-review-fixes/backend-tests.xml
.\.venv-production-review\Scripts\python.exe -m ruff check backend/src backend/tests backend/migrations
```

| 项 | 值 |
|---|---|
| 基线提交 | `f40385b` |
| 范围 | 四项已复现 P1 修复；本地 SQLite 与模型替身；无前端改动、无库结构迁移、未访问生产 |
| 定向用例 | 6 项 / 0 失败（已包含在全量回归内，不重复累加） |
| 全量后端回归 | 728 项 / 0 失败 / 0 错误 / 0 跳过，490.84 秒，171 条告警 |
| 静态检查 | `ruff check backend/src backend/tests backend/migrations` 退出码 0；`git diff --check` 退出码 0 |
| 未验证 | 真实 CAM++ 推理、目标服务器、Docker/TLS、长时验收 |

全部测试采用临时 SQLite 与模型替身，未访问生产数据库或执行真实模型推理。
写锁检查使用独立数据库连接，覆盖六段取样各次推理；不是只检查函数存在性。
清理测试同时验证人员属性保留且来源解除。
