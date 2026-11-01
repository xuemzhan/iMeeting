# 2026-11-01 全面审查问题修复验证证据

<!-- doc-governance: IM-EVD-010 -->
> 文档类型：证据；状态：阶段快照；日期：2026-11-01。
> 维护责任：待指派（iMeeting 维护者）。[台账](../../../README.md) · [规则](../../../governance/README.md)。
> 正文：[修复报告 IM-RPT-008](../../../reports/2026-11-01-comprehensive-review-fixes.md)。
> 上游基线：[全面审查报告 IM-REV-004](../../2026-11-01-comprehensive-project-review.md)。

本目录保存「按全面审查报告逐项修复」之后的验证输出。审查当时的失败基线在
[IM-EVD-009](../2026-11-01-comprehensive-review/README.md)，两者对照即可看出变化；
同日的另一轮复核修复（728 项回归）见[2026-11-01 复核修复证据](../2026-11-01-review-fixes/README.md)。

## 环境

| 项 | 值 |
|---|---|
| 分支状态 | 工作副本（在 `f40385b` 之上叠加本轮的修改与新增） |
| Python | 3.14.3（`./.venv-production-review/Scripts/python.exe`） |
| Node / npm | v24.14.0 / 11.9.0 |
| 数据库 | 每个用例独立临时 SQLite；探针同样使用临时目录 |
| 模型 | 未加载（`ASR_MODE=off`） |

## 后端

| 命令 | 结果 | 原始输出 |
|---|---|---|
| `python -m pytest tests -q` | **765 passed, 0 failed**（494.22s） | 见下段说明 |
| `python -m pytest tests --collect-only -q` | `765 tests collected` | `backend-collected.txt` |
| `python -m ruff check src/imeeting tests` | `All checks passed!` | `ruff.txt` |

审查当时是 `2 failed, 741 passed`（743 项）。本轮新增 20 项回归、改写 2 项按旧默认值写死的
用例，合计 765 项全绿。全量日志约 1.2 MB（含 184 条第三方弃用告警），未入库；
上面的命令可一次复跑得到同样结果。

新增回归文件 `backend/tests/test_review_20261101_fixes.py`（20 项）逐条对应审查编号：

| 审查编号 | 用例 |
|---|---|
| P0-1 | `test_encrypted_secret_survives_restart`、`test_secret_reload_skips_legacy_plaintext`、`test_undecryptable_secret_degrades_to_empty` |
| P0-2 | `test_bootstrap_hides_password_in_production`、`test_bootstrap_hides_password_when_auth_required`、`test_bootstrap_password_can_be_opted_in` |
| P1-2 | `test_production_warnings_flag_missing_proxy_trust`、`test_no_warnings_outside_production` |
| P2-1 | `test_metrics_open_in_development`、`test_metrics_requires_token_when_configured`、`test_metrics_closed_to_remote_clients_in_production` |
| P2-2 | `test_wav_is_readable_before_close`、`test_header_matches_data_without_close`、`test_repair_interrupted_wavs_fixes_zero_header`、`test_repair_skips_recent_files` |
| P2-3 | `test_share_access_token_lasts_two_hours` |
| P2-4 | `test_bulk_export_jobs_rejects_non_numeric_ids`、`test_bulk_export_jobs_rejects_empty_ids`、`test_denoise_eval_rejects_bad_payload`、`test_retention_rejects_out_of_range` |

阈值一致性（P1-1）在 `backend/tests/test_adaptive_speaker.py`：
`test_thresholds_have_single_source_of_truth`、`test_thresholds_follow_config_override`，
并把原先按旧默认值写死的两条用例改为显式钉住阈值。

## 前端

| 命令 | 结果 | 原始输出 |
|---|---|---|
| `npm run lint` | **0 error / 17 warning**（warn 均为 React 19 编译器时代建议） | `frontend-lint.txt` |
| `npm test` | **101 passed / 13 files** | `frontend-test.txt` |
| `npm run test:coverage` | 语句 11.93% / 分支 10.49% / 函数 8.4% / 行 12.32%（基线，未设阈值） | `frontend-coverage.txt` |
| `npm run typecheck` + `npm run build` | 通过，`built in 4.82s` | `frontend-build.txt` |

审查当时前端为 89 项、无 lint、无组件测试；本轮新增 12 项组件测试
（`SegmentCard` 7 项、`FontSizeControl` 5 项）。

## 文档

| 检查 | 结果 | 原始输出 |
|---|---|---|
| 相对链接检查（`check_links.py`，见 IM-EVD-009/probes） | 44 篇文档、379 条相对链接、**0 断链** | `doc-links.txt` |

审查当时为 42 篇 / 369 条 / 15 条断链（全部越出仓库指向外层工作区）；
本轮把越界链接改为「外层路径 + 说明」的普通文本，并同步 README 的测试数字。

## 本次未验证

- 未在目标服务器复跑 `docker compose`、未做镜像扫描与 SBOM；
- 未加载真实模型，未复测字错率 / 说话人准确率 / 首字延迟；
- 未跑 Playwright e2e 与 `stress.py`（需要浏览器/常驻服务与音频素材）；
- 未在 PostgreSQL 上验证：本轮只保证非 SQLite 方言不再被 SQLite 专有 DDL 打断，
  迁移路径的彻底收敛仍是未完成项（见报告）。
