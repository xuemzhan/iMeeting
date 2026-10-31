# 代码与文档对应性核查证据

<!-- doc-governance: FA-EVD-002 -->
> 文档类型：证据；状态：阶段快照；日期：2026-10-31。
> 维护责任：待指派（FunASR / iMeeting 维护者）。[台账](../../../../../docs/governance/CATALOG.md) · [规则](../../../../../docs/governance/POLICY.md)。

正文：[核查报告](../../2026-10-31-code-document-gap-review.md)。下一步：[修复与优化计划](../../../plans/2026-10-31-gap-remediation-plan.md)。

## 文件

| 文件 | 含义 |
|---|---|
| [source-manifest.json](source-manifest.json) | 本次核查引用文件的 SHA-256；不是发布提交或性能环境证明 |
| [document-traceability.json](document-traceability.json) | 核查开始时45份台账材料的文档级对应关系和验证边界 |
| [requirements-traceability.json](requirements-traceability.json) / [CSV](requirements-traceability.csv) | REQ-001 的42项、REQ-002 的41项，共83个编号 FR/AC；保留原条目和具体结论 |
| [call-edges.json](call-edges.json) | 运行源码内关键函数的直接 AST 调用点，结合人工追读调用链使用 |
| [verification-summary.json](verification-summary.json) | 已执行命令、环境、退出码和结果摘要；没有补造未保存的全量日志 |
| [审计探针原始结果](../../evidence/2026-10-31/gap-probes-observed.xml) | 规范断言实际15失败、1正向对照通过；用于证明缺口存在 |
| [探针源码](../../../../backend/tests/audit_doc_code_gaps_20261031.py) | 使用临时数据库、占位账号、合成向量和模拟LLM；默认不被常规回归自动收集 |

## 已执行的常规检查

仓库根目录：

```powershell
$env:PYTHONUTF8='1'
.\.venv-repository-review\Scripts\python.exe -m pytest tests/test_production_service_guards.py tests/test_production_http_storage.py tests/test_production_safe_parsing.py tests/test_audio_buffer.py tests/test_portable_archive_security.py runtime/llama.cpp/tests/test_gguf_server.py tests/test_top_level_import.py -q --tb=short --disable-warnings
```

结果：**51 passed，1 warning，22.85秒**。

工作目录 `iMeeting/`：

```powershell
$env:PYTHONUTF8='1'
.\.venv-production-review\Scripts\python.exe -m pytest backend/tests/test_person_ontology.py backend/tests/test_voice_templates.py backend/tests/test_production_regressions.py backend/tests/test_speaker_identity.py backend/tests/test_speaker_importance.py -q --tb=short --disable-warnings
```

结果：**126 passed，32 warnings，76.21秒**。

工作目录 `iMeeting/frontend/`：

```powershell
npm test -- --reporter=dot
npm run typecheck
```

结果：**11个文件、86项通过**；类型检查退出码0。保留 npm 用户配置弃用提示；没有据此声称浏览器业务链路通过。

## 已执行的反例检查

工作目录 `iMeeting/`：

```powershell
$env:PYTHONUTF8='1'
.\.venv-production-review\Scripts\python.exe -m pytest backend/tests/audit_doc_code_gaps_20261031.py --runxfail -q --tb=line --show-capture=no --disable-warnings --junitxml=docs/reviews/evidence/2026-10-31/gap-probes-observed.xml
```

实际结果：**15 failed，1 passed，4 warnings，10.80秒，退出码1**。每个失败已检查断言内容，确认来自报告描述的违约行为，不能视为测试环境初始化失败，也不能将这次运行表述为“测试全绿”。15个失败场景中，两项分别验证同一个对象授权问题的读/删分支。

不加 `--runxfail` 时，已知问题用 strict xfail 表示；未来修复后产生 XPASS，需要转为正式回归断言。早期 `gap-probes.xml` 是探针尚未补齐时的中间运行，不是最终计数来源。

本轮没有读取生产数据库或真实凭据；口令日志探针使用专用占位值。没有执行实际模型、GPU、Docker/TLS、漏洞库更新、目标并发和长稳测试。
