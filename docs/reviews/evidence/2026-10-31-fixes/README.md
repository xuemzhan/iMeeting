# 2026-10-31 修复验证证据

<!-- doc-governance: IM-EVD-003 -->
> 文档类型：证据；状态：阶段快照；日期：2026-10-31。
> 维护责任：待指派（iMeeting 维护者）。[修复报告](../../../reports/2026-10-31-gap-fixes.md) · [原核查](../../README.md)。

| 文件 | 来源与范围 |
|---|---|
| [backend-tests.xml](backend-tests.xml) | 后端完整pytest，685通过、0失败；包含16项原断言常规回归和30项新增身份流程，不与定向运行重复计数 |
| [migration-tests.xml](migration-tests.xml) | 最终disabled索引处理的迁移增量复验，1通过；与完整回归中的迁移用例重叠 |
| [frontend-tests.xml](frontend-tests.xml) | Vitest，89通过，0失败；含取消保留与采用服务端转交姓名 |
| [browser-tests.xml](browser-tests.xml) | Playwright，6通过，0失败；1个真实首次改密，5个受控接口的身份确认/满槽交互 |
| [verification.json](verification.json) | 最终命令、退出状态、测试摘要与环境边界 |
| [source-hashes.json](source-hashes.json) | 验证对应的后端/前端/迁移/测试SHA-256，不包含密钥、环境文件或数据库 |
| [gap-closure.json](gap-closure.json) | GAP-01～16与源码、常规测试和剩余验证的映射 |

在 `iMeeting/` 目录执行后端检查：

```powershell
$env:PYTHONUTF8='1'
.\.venv-production-review\Scripts\python.exe -m ruff check backend/src backend/tests backend/migrations
.\.venv-production-review\Scripts\python.exe -m pytest backend/tests -q --tb=short --show-capture=no --disable-warnings --junitxml=docs/reviews/evidence/2026-10-31-fixes/backend-tests.xml
```

在 `iMeeting/frontend/` 目录执行前端检查：

```powershell
npm run typecheck
npm test -- --reporter=junit --outputFile=../docs/reviews/evidence/2026-10-31-fixes/frontend-tests.xml
npm run build
$env:Path=(Resolve-Path ../.venv-production-review/Scripts).Path+';'+$env:Path
$env:PYTHONUTF8='1'
$env:PLAYWRIGHT_JUNIT_OUTPUT_FILE=(Join-Path (Resolve-Path ../docs/reviews/evidence/2026-10-31-fixes).Path 'browser-tests.xml')
npm run e2e -- --reporter=line,junit --timeout=20000
```

文档检查在联合工作区根目录执行 `python scripts/docs_governance.py --write` 后再执行 `python scripts/docs_governance.py`。虚拟环境为本机已有环境；重现时应使用项目支持的Python环境，不要求复制本机虚拟环境目录。

后端夹具按测试建立临时SQLite，隔离配置、禁用模型和网络LLM，并将日志/数据写临时目录。一般业务测试跳过Alembic启动开销，新增迁移用例单独在旧03表结构执行04升级、写入/更新约束检查、降级和再升级。浏览器服务器由Playwright临时启动，独立临时数据库；身份流程通过受控响应验证真实组件行为，不用其分数声称模型准确。

未执行生产库迁移、真实模型/供应商调用、目标机器性能、TLS/Docker、长稳、漏洞扫描或生产发布。npm本机镜像配置和FastAPI弃用提示不属于业务失败；第三方依赖告警不因本轮通过而视为已清除。
