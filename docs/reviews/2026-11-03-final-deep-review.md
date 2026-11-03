# 2026-11-03 收尾阶段深度审查

> 文档类型：审查记录；状态：阶段快照；日期：2026-11-03。
> 审查对象：`main` @ `efb9954`（工作区干净，无未提交改动）。
> 边界：Windows 本机工作副本；未在目标服务器、真实模型与真机上验收。

## 0 结论

**没有阻断级缺陷**：没有功能错误、没有测试断言失败、没有可利用的越权或注入、迁移链单一 head、文档零断链。
但**不能直接宣布收尾完成**：有 1 项会让 CI 偶发变红的稳定性问题、2 项录音链路的真实缺陷、1 项与项目自身标准不一致的上传防护缺口，以及 P5–P7 明确未启动。

## 1 实跑结果（本次亲自执行）

| 门禁 | 命令 | 结果 |
|---|---|---|
| 后端静态检查 | `python -m ruff check src/imeeting tests migrations scripts` | All checks passed |
| 后端全量测试 | `python -m pytest tests -q` | **785 passed, 0 failed**（498s） |
| 迁移链 | `python -m alembic heads` | 单一 head `20261102_01` |
| 前端类型 | `npm run typecheck` | 通过 |
| 前端 lint | `npm run lint` | 0 warning / 0 error |
| 前端构建 | `npm run build` | 通过（5.13s） |
| 前端单测 | `npm test` | **3 次运行 2 次退出码 1**（见 2.1）；通过的一次为 21 文件 / 172 测试 |
| 前端覆盖率 | `npm run test:coverage && npm run coverage:check` | 23.03 / 22.08 / 15.16 / 17.44，与基线持平 |
| 文档断链 | `check_links.py` | 51 文档 / 431 链接 / **0 断链** |
| 前端依赖 | `npm audit --audit-level=high` | 12 moderate，全在 devDependencies，低于 CI 阈值 |

与文档宣称的偏差（不影响结论，需知悉）：

- IM-RPT-010 记后端全量 109 秒，本次 498 秒。本机 `.venv-production-review` 为 **Python 3.14.3**，CI 为 3.12；属环境差异，不是回归。
- IM-PLN-002 §0.1 记前端 172 passed 稳定复现，本次 3 跑 2 崩（见 2.1）。

## 2 问题清单

### 2.1 前端测试套件偶发 worker 崩溃（重要）

`npm test` 三次：145 passed（18/21 文件）、165 passed（20/21）、172 passed（21/21）。失败两次的表现均为
`[vitest-pool]: Worker forks emitted error` / `Worker exited unexpectedly`，退出码 1，无任何断言失败。
每次崩掉的都是不同的文件；单独运行 `frontend/src/components/PaneSplitter.test.tsx` 稳定 7 passed。

即：**不是用例写错，是 fork worker 进程被环境杀掉**（Windows + Node 24 + vitest 4 组合下概率偏高）。
影响：CI 的 frontend job 会随机变红，且"红"的日志里看不到失败用例，容易误判为代码问题。
建议：给 `frontend/vitest.config.ts` 显式指定 `pool: 'threads'`（或限制 `maxWorkers` / 提高 worker 内存）后重跑 5 次确认；若 Ubuntu CI 不复现，也应记录该已知抖动。

### 2.2 录音 chunk 本地缓存永不回收（重要）

`frontend/src/audio/audioStore.ts` 里的 `clearMeeting()` 是**死代码**，全仓无调用点。而 `frontend/src/api/ws.ts:129`
只在 `this.meetingId !== null` 时处理 `audio_ack`，同文件 `ws.ts:239` 的 `close()` 又立刻把 `meetingId` 置空——
于是停止录音后 1.2 秒窗口内到达的 ack 全部被丢弃，未确认的 chunk 永久留在 IndexedDB，且没有 TTL、配额或按会议清理。
用户侧表现：长时间使用后浏览器存储持续增长，最终写入失败（`putChunk` 静默 resolve），断线补传能力随之失效。
建议：`close()` 中保留一份局部 `meetingId` 用于收尾 ack，并在 `store/session.ts` 的 `stop()`/`reset()` 收尾时调用 `clearMeeting(meetingId)`；再加一个基于 `createdAt` 的过期清理。

### 2.3 `TranscriptSocket.close()` 的延迟关闭会误杀新连接（重要）

`frontend/src/api/ws.ts:233` 写的是 `window.setTimeout(() => this.ws?.close(), 1200)`，回调读的是**当时的 `this.ws`**。
若用户在停止后的 0.9–1.2 秒内重新开始（`store/session.ts` 的 `stop()` 在 900ms 后即把状态置回 `idle`，窗口真实存在），
新连接会被这个旧定时器关掉，表现为"刚开始就断线、反复重连"。
修复很小：先把连接捕获为局部变量（`const ws = this.ws`）再延迟关闭。

### 2.4 声纹上传缺少大小上限（中）

项目对上传有一套统一做法：日历 `read(limit + 1)`、纪要模板 `read(limit + 1)`、词表 `read(2MB)`。
但 `backend/src/imeeting/api/http/routes/voiceprints.py` 的第 259 行与第 983 行都是裸 `await file.read()`，
先整体读进内存，再按解码后的时长判断 `MAX_MS`——判断发生得太晚。
`REQUIRE_AUTH=0` 的默认形态下（本地/内网部署）任何能访问端口的人都能用一个大文件把进程内存打满。
建议：与其它上传一致，先 `read(limit + 1)` 并校验超出即 413。

### 2.5 `/api/v1/auth/bootstrap` 在生产仍回传真实管理员用户名（低）

`backend/src/imeeting/api/http/routes/auth.py:214` 的开关注释已经正确地在生产形态下禁用了**口令**回传。
但同文件第 252 行的 `default_username=admin.username` 无条件返回，而这个端点不需要登录。
生产环境管理员用户名通常已被改掉，等于向未认证访问者泄露一个有效账号名，降低爆破搜索空间。
建议：`expose_password` 为假时，`default_username` 回退到环境变量默认值（`env_username`）而不是库里的真实值。

### 2.6 词表 CSV 导入失败静默（低）

`frontend/src/components/GlossaryPanel.tsx:86` 直接 `fetch` 并在之后无条件 `await load()`：
不检查响应状态、不解析错误体，也不复用 `api/client.ts` 的错误处理。导入被拒（403/400/413）时界面看起来像成功。
顺带：这里硬编码了 `'imeeting-token'`，与 `api/client.ts` 的 `TOKEN_KEY` 是两处事实来源，改名时会静默失效。

### 2.7 其它已核对、判定为"可接受/无需改动"的点

- 会议续录的归属校验（`application/meeting_sessions.py` 的 `resolve_meeting`）、分享令牌（hash 绑定 + 2 小时 + 限流 + 审计）、WS 鉴权、静态兜底的路径穿越防护、生产配置校验（`APP_ENV=production` 时强制 SECRET_KEY / ADMIN_PASSWORD / REQUIRE_AUTH=1 / 禁 CORS `*`）均实现到位，`deploy/compose.yaml` 也已正确注入这些变量。
- 前端无 `dangerouslySetInnerHTML`；令牌存 localStorage 属常规取舍。
- `require_admin` 相关的 8 个后台接口均有守卫；14 个未走通用守卫的端点逐个核对后均为设计内（登录、注册、bootstrap、分享三件套、健康/探针、metrics、SPA 兜底）。
- 裸 `except Exception: pass` 全仓仅 1 处。
- `security.py` 的 `_clip()` 会把超过 72 字节的口令截断（bcrypt 限制），已有注释说明，属可接受设计。
- `login` 对停用账号返回 403 并带独立文案，与"不区分账号是否存在"的自述略有不一致，但只有密码正确时才会走到该分支，实际不可用于枚举。

## 3 已知未完成（非本轮新发现，记录以免误判为遗漏）

- **P5–P7 未启动**：`session.start/stop`、`TranscriptStream`、`History`、`Meeting` 四块覆盖率（目标全局 ≥27%、四文件各 ≥60%，当前全局 23.03%）；Postgres 迁移往返 CI（E1-1…E1-4）；D1 改名语义、D2 一人多 speaker_key、D3 自动身份门槛三项决策债。
- **e2e 未在本机验证**：4 个 spec 存在，本机 `playwright` 无法启动浏览器（环境限制，文档已声明）。
- 目标服务器 compose/TLS、iOS Safari 与 Android Chrome 真机、24h 长稳与断网续传、真实语料阈值校准仍待环境。

## 4 建议处置顺序

1. 2.3（一行修复，风险最低、影响最直接）
2. 2.2（录音链路正确性，涉及一点接口收敛）
3. 2.4（照抄既有上传写法）
4. 2.1（先定位 pool 配置，再复跑 5 次固定结论）
5. 2.5 / 2.6（顺手清理）
6. 再决定是否推进 P5–P7

## 5 关联文档

- [剩余问题实施方案](../plans/2026-11-02-remaining-optimization-plan.md)（P5–P7、决策债）
- [逐项优化报告](../reports/2026-11-02-continuous-optimization.md)（IM-RPT-010）
- [全面审查](2026-11-01-comprehensive-project-review.md)（上一轮独立复核）
- [文档中心](../README.md)
