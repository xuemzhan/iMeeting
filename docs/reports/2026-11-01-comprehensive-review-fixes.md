# 2026-11-01 全面审查问题修复报告

<!-- doc-governance: IM-RPT-008 -->
> 文档类型：报告；状态：阶段快照；日期：2026-11-01。
> 维护责任：待指派（iMeeting 维护者）。[台账](../README.md) · [规则](../governance/README.md)。
> 上游：[全面审查报告 IM-REV-004](../reviews/2026-11-01-comprehensive-project-review.md)。
> 证据：[IM-EVD-009](../reviews/evidence/2026-11-01-comprehensive-review/README.md) · [IM-EVD-010](../reviews/evidence/2026-11-01-comprehensive-review-fixes/README.md)。

## 结论

审查报告列出的 11 项（P0 2 项、P1 2 项、P2 4 项、P3 5 项）**全部处理完毕**，
其中 P3-3 的「三套迁移路径收敛」按可行范围收敛为「方言护栏 + 声明对齐」，
完整收敛列入未完成项。后端测试由 **741/743 通过**变为**全量通过**；
前端从「无 lint、无组件测试」变为「lint 0 error、101 项测试通过、覆盖率基线可查」。

本轮同时修掉两个**审查未列、但由修复动作暴露**的缺陷：

- 录音文件在崩溃时不只是「头全零」，而是**文件长度 0 字节**——数据全在 Python
  缓冲区里没落盘（`audio.py` 只写不 flush）。
- 声纹确认弹窗点「取消」后**仍会执行改名**——前端先发请求后判断，而后端只把
  `decision` 当作「跳过 409 冲突检查」的开关，并不识别 `cancel`。

## 逐项处置

| 编号 | 审查结论 | 本轮处置 | 关键位置 |
|---|---|---|---|
| P0-1 | 配置密钥重启后变密文 | 装载时按 schema 的 `secret` 解密；旧明文原样保留；解不开时降级为空并告警 | `settings.py` `load_overrides` / `_decode_secret`、`security.is_encrypted_value` |
| P0-2 | 生产 bootstrap 回传管理员口令 | 新增 `bootstrap_password_exposed()`：`APP_ENV=production` 或 `REQUIRE_AUTH=1` 时恒不回传，可用 `ALLOW_BOOTSTRAP_PASSWORD=1` 显式开回 | `api/http/routes/auth.py` |
| P1-1 | 阈值三方不一致、2 项红灯 | 新增 `settings.config_float()` 作为唯一事实来源；删掉 `speaker.py`/`voice_match.py`/`speaker_reconcile.py` 里的字面量默认值；2 个用例改为显式钉住阈值，另加 2 条守「单一事实来源」的用例 | `settings.py`、`engine/speaker.py`、`engine/voice_match.py`、`engine/speaker_reconcile.py`、`tests/test_adaptive_speaker.py` |
| P1-2 | 反代后按 IP 限流退化成全局桶 | compose 设 `FORWARDED_ALLOW_IPS`（RFC1918，uvicorn 原生读取）；新增 `production_config_warnings()` 在启动日志提示缺口；部署手册写清取舍 | `deploy/compose.yaml`、`runtime_checks.py`、`deploy/README.md` |
| P2-1 | `/metrics` 无鉴权 | 支持 `METRICS_TOKEN`（Bearer 或 `?token=`）；生产未配令牌时只允许环回地址 | `main.py` `_metrics_allowed` |
| P2-2 | 崩溃中断的 WAV 不可读、无修复路径 | 写时就写合法头、每块回填长度并 flush；新增 `repair_interrupted_wavs()` 在启动首轮扫描修复历史坏头（跳过 2 分钟内仍在写的文件） | `engine/audio.py`、`main.py` `_audio_cleanup_loop` |
| P2-3 | 分享令牌实际 24h、声明 2h | `create_access_token` 支持 `expires_minutes`；分享固定 120 分钟，`expires_in` 由同一常量推导 | `security.py`、`api/http/routes/meeting_extras.py` |
| P2-4 | 非法请求体返回 500 | 导出任务、去噪评估、索引重建、排空、脱敏、保留策略共 6 个端点改用 Pydantic 模型；非法输入统一 422 | `schemas.py`、`routes/export_jobs.py`、`routes/admin_ops.py`、`routes/meeting_extras.py` |
| P3-1 | `npm run lint` 是死脚本 | 接入 eslint 10 + typescript-eslint + react-hooks（flat config）；`rules-of-hooks` 为 error、其余新建议为 warn；CI 前端 job 增加 lint 步骤 | `frontend/eslint.config.js`、`package.json`、`.github/workflows/ci.yml` |
| P3-2 | 文档数字与 15 条断链 | README 测试数字改为「以 `--collect-only` 为准」并同步当前值；15 条越界链接全部改为「外层工作区 + 代码路径」的普通文本；相对链接检查 0 断链 | `README.md`、3 篇 2026-10-31 文档 |
| P3-3 | 三套迁移路径、PG 声明与 SQLite 专有 DDL 冲突 | `_migrate_schema` 加方言护栏（非 SQLite 直接跳过并记录）；`.env.example` 去掉 PG 示例、写明当前只支持 SQLite；槽位越界的启动错误给出可执行的自救步骤 | `db.py`、`backend/.env.example` |
| P3-4 | `dist-next` 未忽略、行尾漂移 | `.gitignore` 增加 `frontend/dist-next/`、`frontend/coverage/`；32 个受版本控制的文本文件统一为 LF（内容零变化，`git diff --numstat` 前后一致） | `.gitignore`、仓库文件 |
| P3-5 | 前端无组件测试 | 新增 jsdom + Testing Library 组件测试（SegmentCard 7 项、FontSizeControl 5 项）；配置 v8 覆盖率与 `test:coverage` 脚本 | `frontend/src/components/*.test.tsx`、`vitest.config.ts`、`src/test-setup.ts` |

## 行为变化与影响面

1. **默认阈值**：工作副本原已把 `spk.enrolled_threshold` 从 0.68 提到 0.75、
   `spk.enrolled_ambiguous_threshold` 从 0.58 提到 0.68。本轮**保留该收紧**
   （符合现场「宁可不给姓名也不认错」的取舍），但把它变成唯一事实来源：
   引擎侧不再有第二份 0.68/0.58。未显式配置过这两项的部署，升级后行为随之收紧。
2. **生产 bootstrap 不再回传口令**：登录页在 `APP_ENV=production` 或 `REQUIRE_AUTH=1`
   时不再预填口令，用户需手动输入。内部单机演示可设 `ALLOW_BOOTSTRAP_PASSWORD=1` 恢复。
3. **`/metrics` 需要令牌或环回访问**：跨机 Prometheus 采集必须配 `METRICS_TOKEN`；
   原有的 SSH 隧道方式（`curl 127.0.0.1:8000/metrics`）不受影响。
4. **非法请求体由 500 变 422**：任何依赖「500 表示参数错」的脚本需改为按 4xx 判断。
5. **分享访问凭证 2 小时后失效**：与界面声明一致；超时后重新 `POST /share/{token}/open` 即可。

## 验证

完整命令与原始输出见[证据 IM-EVD-010](../reviews/evidence/2026-11-01-comprehensive-review-fixes/README.md)，
摘要：

| 检查 | 结果 |
|---|---|
| `pytest tests -q` | **765 passed, 0 failed**（494.22s；审查当时为 2 failed / 741 passed） |
| `ruff check src/imeeting tests` | All checks passed |
| `npm run lint` | 0 error（17 条 warn，均为 React 19 新建议） |
| `npm test` | 101 项通过（13 个文件，含 2 个组件测试文件） |
| `npm run typecheck` / `npm run build` | 通过 |
| `npm run test:coverage` | 已产出报告，全量行覆盖约 12%，作为基线记录 |
| 文档相对链接检查 | 44 篇 / 379 条相对链接 / 0 断链 |

新增回归用例集中在 `backend/tests/test_review_20261101_fixes.py`（20 项）；
阈值一致性用例在 `backend/tests/test_adaptive_speaker.py`（新增 2 项、改写 2 项失效用例）。

## 未完成项

- **迁移路径未真正收敛**：仍是 `create_all` + 手写兼容 DDL + Alembic 三处；本轮只保证
  其它方言不再被 SQLite 专有 DDL 打断。彻底收敛需要把 `_migrate_schema` 里的每段
  DDL 迁成 Alembic revision，并在 PG 上跑一遍升降级——属于 P2 扩容改造，未在本次做。
- **前端覆盖率无阈值**：当前 12% 是基线，直接设阈值会让 CI 长期红灯；建议先补
  `Pages/` 与 `hooks/` 的关键路径，再逐步抬阈值。
- **17 条 eslint warn**：`set-state-in-effect` 等是 React 19 编译器时代的建议，
  需要逐处结合业务判断（拖拽、波形、store 同步），未在本次批量改。
- **`ALLOW_BOOTSTRAP_PASSWORD`** 属于「显式降低安全性」的开关，只应在内网单机演示使用；
  尚未在部署文档的检查清单里加醒目提示（下一轮补）。
- 未在目标服务器复跑 docker compose、未做镜像扫描、未加载真实模型复测识别指标。

## 关联文档

- [全面审查报告 IM-REV-004](../reviews/2026-11-01-comprehensive-project-review.md)
- [修复证据 IM-EVD-010](../reviews/evidence/2026-11-01-comprehensive-review-fixes/README.md)
- [验证证据 IM-EVD-009](../reviews/evidence/2026-11-01-comprehensive-review/README.md)
- [部署手册](../../deploy/README.md)、[文档规制](../governance/README.md)
