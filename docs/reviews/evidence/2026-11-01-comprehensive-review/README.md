# 全面审查验证证据（2026-11-01）

<!-- doc-governance: IM-EVD-009 -->
> 文档类型：证据；状态：阶段快照；日期：2026-11-01。
> 维护责任：待指派（iMeeting 维护者）。[台账](../../../README.md) · [规则](../../../governance/README.md)。
> 正文见[全面审查报告](../../2026-11-01-comprehensive-project-review.md)。

本目录保存 2026-11-01 全面审查实际执行到的命令与原始输出。所有结果在本机
（Windows、仓库工作副本）一次跑出，未做二次修饰；失败项保留失败状态。

## 环境

| 项 | 值 |
|---|---|
| 受审版本 | `f40385b`（`docs: move the workspace gap review into this repository`）+ 工作副本未提交改动 21 个文件 |
| Python | 3.14.3（`./.venv-production-review/Scripts/python.exe`） |
| Node / npm | v24.14.0 / 11.9.0 |
| 数据库 | 每个用例独立临时 SQLite（`tests/conftest.py`） |
| 模型 | 未加载（`ASR_MODE=off` 跑测试；探针用 `echo` + `SKIP_MODEL_WARMUP=1`） |

## 命令与结果
| 命令 | 结果 | 原始输出 |
|---|---|---|
| `python -m pytest tests -q` | **2 failed, 741 passed**（511.65s）；收集 **743** 项 | `backend-pytest-summary.txt` |
| `python -m ruff check backend/src/imeeting backend/tests` | `All checks passed!` | `ruff.txt` |
| `npm test` | **89 passed**（11 个文件） | `frontend-checks.txt` |
| `npm run typecheck` | 退出码 0，无错误 | `frontend-checks.txt` |
| `npm run build` | `✓ built in 4.95s` | `frontend-checks.txt` |
| `npm run lint` | **退出码 1**：`'eslint' is not recognized` | `frontend-lint.txt` |
| 文档相对链接检查（`probes/check_links.py`） | 42 篇文档、369 条相对链接、**15 条断链（全部越出仓库）**；本次新增两篇无断链 | `doc-links.txt` |

## 探针（`probes/`）

均为独立脚本，使用 `tempfile` 建的一次性 SQLite，不接触 `backend/data` 生产库，
不加载模型，不发起真实 LLM 调用。

| 脚本 | 断言的事实 | 输出 |
|---|---|---|
| `probe_secret_reload.py` | 后台写入的 `llm.api_key` 加密落库后，模拟重启重建配置（`ConfigStore.load_overrides`）得到的仍是 Fernet 密文，不是明文 | `probe_secret_reload.out.txt` |
| `probe_bootstrap.py` | `APP_ENV=production` + 强口令 `ADMIN_PASSWORD` 时，未鉴权的 `GET /api/v1/auth/bootstrap` 回传该口令，且用它登录返回 200 | `probe_bootstrap.out.txt` |
| `probe_smoke.py` | 首次登录强制改密（428）、鉴权边界、`/metrics` 无令牌 200、`bulk-export-jobs` 非数字 id 抛 `ValueError` | `probe_smoke.out.txt` |
| `check_links.py` | 文档相对链接可解析性 | `doc-links.txt` |

## 本次未验证的边界

- 未跑 `npm run e2e`（Playwright）：需要额外安装浏览器与耗时，本次以单测 + typecheck + build 为准。
- 未跑 `backend/scripts/stress.py`、`scripts/ws_smoke.py`、`frontend/scripts/verify-features.mjs`：
  这些脚本要求已启动的服务与真实/合成音频，本次不启动常驻服务。
- 未加载 FunASR 真实模型，因此没有重新测量字错率、说话人准确率、定稿延迟。
- 未执行 `docker compose build/up`、未做镜像扫描与 SBOM 生成，CI 的 `deploy` job 未在本机复跑。
- 未在 PostgreSQL 上启动验证；相关结论来自代码阅读（SQLite 专有 DDL）。
- 未验证多实例、跨机灾备与真实公网 TLS 部署。
