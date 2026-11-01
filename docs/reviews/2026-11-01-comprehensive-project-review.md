# 2026-11-01 全面审查报告

<!-- doc-governance: IM-REV-004 -->

> 文档类型：评审；状态：阶段快照；日期：2026-11-01。
> 受审版本：`f40385b` + 工作副本未提交改动（21 个已改文件、11 个新增）。
> 维护责任：待指派（iMeeting 维护者）。[台账](../README.md) · [规则](../governance/README.md)。
> 验证证据：[IM-EVD-009](evidence/2026-11-01-comprehensive-review/README.md)。


> **状态更新（2026-11-01 本轮）**：本报告的 P0/P1/P2/P3 共 11 项已按第五节顺序修复完毕，
> 处置与行为变化见[修复报告 IM-RPT-008](../reports/2026-11-01-comprehensive-review-fixes.md)，
> 修复后的验证输出见[IM-EVD-010](evidence/2026-11-01-comprehensive-review-fixes/README.md)。
> 下面的「问题清单」保留审查当时的结论与复现方式，作为追溯基线，不再代表当前代码状态。
## 结论先行

工程完整度高于同类项目：文档纪律、回归覆盖、生产配置 fail-fast、分享令牌摘要化、
容器非 root 都已落地，本次实测 ruff 通过、前端 typecheck/build 通过、后端 741/743 通过。
但存在**两处可直接利用或直接导致业务失效的缺陷**，以及一处**测试红灯未收口**的工作副本：

| 优先级 | 问题                          | 一句话                                          |
| --- | --------------------------- | -------------------------------------------- |
| P0  | 配置密钥重启后变密文                  | 后台填的 LLM 密钥在**下一次重启**后失效，当场测试却是通的            |
| P0  | `/auth/bootstrap` 回传生产管理员口令 | 未鉴权接口把 `ADMIN_PASSWORD` 明文交给任何访问者，且能直接登录     |
| P1  | 阈值默认值三方不一致                  | 工作副本把阈值改成 0.75/0.68，代码回退值与 2 个用例仍按 0.68/0.58 |
| P1  | 代理后按 IP 限流退化                | 全员共用一个 IP 桶，任何人可让所有人登录被锁 15 分钟               |

这两条 P0 都**不会在任何现有测试里失败**，因为测试套件不模拟"进程重启"，
也不在 `APP_ENV=production` 下调用 `bootstrap`。这类缺口比单个功能 bug 更值得修，
建议按第五节顺序处理，并为每条补一条会失败在先的回归。

## 一、审查范围与方法

范围：后端 `backend/src/imeeting`（93 文件 / 19,947 行）、后端测试
（50 文件 / 11,154 行）、前端 `frontend/src`（112 文件 / 16,031 行）、
部署与 CI、文档体系与需求追溯。

方法（全部实跑，命令与原始输出见[证据](evidence/2026-11-01-comprehensive-review/README.md)）：

1. 通读装配与边界代码：`main.py`、`deps.py`、`security.py`、`db.py`、`settings.py`、
   `api/ws/stream.py`、`application/*`、`engine/pool.py`、`engine/audio.py`、`llm/*`。
2. 通读所有路由的鉴权口径、对象归属校验与文件下载路径，检查越权与路径穿越。
3. 跑后端全量测试、ruff、前端单测/typecheck/build/lint，并做文档相对链接检查。
4. 针对可疑点写独立探针（一次性临时库、不加载模型、不调真实 LLM）复现，而非只读代码下结论。

## 二、验证结果总览

| 检查      | 命令                                        | 结果                                                    |
| ------- | ----------------------------------------- | ----------------------------------------------------- |
| 后端全量测试  | `python -m pytest tests -q`               | **2 failed / 741 passed**，收集 743 项，511.65s            |
| 后端静态检查  | `python -m ruff check src/imeeting tests` | 通过（`All checks passed!`）                              |
| 前端单测    | `npm test`                                | **89 passed** / 11 文件                                 |
| 前端类型    | `npm run typecheck`                       | 通过                                                    |
| 前端构建    | `npm run build`                           | 通过（`built in 4.95s`，首屏包 453 kB / gzip 138 kB）         |
| 前端 lint | `npm run lint`                            | **失败**：`eslint` 未安装、无配置文件                             |
| 文档链接    | `probes/check_links.py`                   | 42 篇 / 369 条相对链接，**15 条断链**（本次新增两篇无断链）                |
| 冒烟探针    | `probes/probe_smoke.py`                   | 首次登录 428、未鉴权 401、`/metrics` 200、非数字 id 抛 `ValueError` |

失败用例（工作副本当前状态）：

* `tests/test_adaptive_speaker.py::test_ambiguous_global_voiceprint_is_not_confirmed`
* `tests/test_adaptive_speaker.py::test_near_known_identity_is_not_absorbed_by_cluster`

两者均由工作副本未提交的阈值默认值改动引起，见 P1-1。

## 三、问题清单

### P0-1 配置密钥加密落库后从不解密，重启即失效

* **位置**：[config.py](../../backend/src/imeeting/api/http/routes/config.py)（写入时
  `encrypt_value` 后落库，并把明文塞回内存）、[settings.py](../../backend/src/imeeting/settings.py)
  `ConfigStore.load_overrides`（启动时原样装载，无解密）、
  [llm/client.py](../../backend/src/imeeting/llm/client.py)（把取到的值当 Bearer 用）。
* **事实**：`security.decrypt_value` 在 `src/` 下**没有任何调用点**，只有定义与
  `tests/test_security.py` 的直接单测。
* **复现**（[probe\_secret\_reload.out.txt](evidence/2026-11-01-comprehensive-review/probes/probe_secret_reload.out.txt)）：
  按后台同一路径加密写入 `llm.api_key`，再新建 `ConfigStore` 只从数据库装载
  （等价于重启），取到的值 `is still ciphertext: True`、`is plaintext: False`。
* **影响**：管理员在「LLM 纪要」里填好密钥 → 当场可用（内存里是明文）→ 任何一次
  重启、滚动发布或崩溃恢复之后，`Authorization: Bearer gAAAAA…` 一律 401，
  纪要/embedding 全线静默失败；后台显示 `********`，看不出是密文。这是本次审查
  里最容易被当成"供应商抽风"而长期误判的一类故障。
* **建议**：`load_overrides` 对 `secret` 字段解密（失败时给显式告警而不是回退空串）；
  回归用例按"新建 store + 从库装载"来写，而不是"写后同进程读"。

### P0-2 生产环境下 `/api/v1/auth/bootstrap` 明文回传管理员口令

* **位置**：[auth.py](../../backend/src/imeeting/api/http/routes/auth.py) 的 `bootstrap`
  （无鉴权依赖；`using_default` 为真时把 `env_password` 放进响应）、
  [runtime\_checks.py](../../backend/src/imeeting/runtime_checks.py)（生产只禁止
  `ADMIN_PASSWORD` 等于出厂值，不禁止回传）。
* **复现**（[probe\_bootstrap.out.txt](evidence/2026-11-01-comprehensive-review/probes/probe_bootstrap.out.txt)）：
  `APP_ENV=production`、`REQUIRE_AUTH=1`、`ADMIN_PASSWORD=Sup3r-Secret-Prod-Pass` →
  `GET /api/v1/auth/bootstrap` 返回 `using_default_password: True` 且
  `returned password: Sup3r-Secret-Prod-Pass`；用该值登录返回 200。
* **影响**：从部署完成到管理员首次改密的整个窗口内，任何能访问服务的人都能拿到
  管理员口令并获得完整权限（全部会议、配置、审计导出）。攻击者不需要爆破，
  也不需要猜时间——轮询该端点即可，而登录限流对它完全不生效。
* **建议**：`APP_ENV=production`（或 `REQUIRE_AUTH=1`）时该端点恒返回
  `default_password=None`；若必须保留一键登录，限定 `request.client.host` 为环回地址，
  或改成一次性引导令牌。回归用例在 production 环境下断言口令为 `None`。

### P1-1 阈值默认值、代码回退值、用例三方不一致（当前测试红灯）

* **位置**：[settings.py](../../backend/src/imeeting/settings.py) 的
  `spk.enrolled_threshold` / `spk.enrolled_ambiguous_threshold`（工作副本由
  0.68/0.58 改为 0.75/0.68）对比
  [speaker.py](../../backend/src/imeeting/engine/speaker.py)（`or 0.68` / `or 0.58` 硬编码回退）、
  [voice\_match.py](../../backend/src/imeeting/engine/voice_match.py)（同样的硬编码回退）、
  [speaker\_reconcile.py](../../backend/src/imeeting/engine/speaker_reconcile.py)（默认参数 0.68）。
* **复现**：`python -m pytest tests/test_adaptive_speaker.py -q` → 2 failed / 5 passed；
  用例里的相似度 0.62 与 0.64 在新默认值下落到"疑似"阈值之外，标签退化成"说话人 1"。
* **影响**：一是当前工作副本在 CI 语义下是红的，任何一次提交都会让后端 job 失败；
  二是阈值有两套事实来源，配置项缺失/为空时实际生效值与后台显示值不同；
  三是**改了默认值等于改了所有未显式配置过该值的既有部署行为**（本次是收紧，
  误认下降但漏认上升），需要在报告里显式声明，不能静默随代码升级生效。
* **建议**：阈值只在 `CONFIG_SCHEMA` 定义一次，`get_config` 无 override 时自动取默认，
  删掉引擎里的字面量回退；用例改成显式传阈值或在测试内覆盖配置；
  默认值调整单独写报告并标注影响面。

### P1-2 反向代理后按 IP 限流退化为全局限流，审计 IP 失真

* **位置**：[nginx.conf.template](../../deploy/nginx.conf.template) 设置
  `X-Forwarded-For` / `X-Real-IP`，但 [compose.yaml](../../deploy/compose.yaml) 未设
  `FORWARDED_ALLOW_IPS`，后端也未启用可信代理；`login_throttle` 用
  `request.client.host` 计数，且默认 `auth.login_throttle_by_ip=1`。
* **影响**：容器网络里 uvicorn 只信任来自 `127.0.0.1` 的转发头，因此看到的是前端
  容器地址 → 所有用户共用一个 IP 桶。默认 5 次失败即进入退避，30 秒起指数退避封顶
  15 分钟：任何人连错 5 次就能让**全体用户**登录被 429（可用性攻击），
  同时审计日志里的登录来源 IP 全部是代理地址，取证价值下降。
* **建议**：明确信任边界——`uvicorn.run(proxy_headers=True, forwarded_allow_ips=<代理网段>)`，
  或让后端只读 `X-Real-IP` 并要求 nginx 覆盖该头；如果暂时不想动信任链，
  至少把 `auth.login_throttle_by_ip` 默认关掉并在部署文档写明取舍。

### P2-1 `/metrics` 无任何鉴权

* **位置**：[main.py](../../backend/src/imeeting/main.py) 的 `prometheus_metrics`。
* **复现**：`probe_smoke.py` → `GET /metrics (no token): (200, True)`。
* **影响**：泄露 LLM 成本、活跃会话数、任务积压、磁盘水位、模型状态。compose 默认
  不发布后端端口，风险可控；但单端口绿色包部署（`IMEETING_WEB_DIR`，见
  [web\_static.py](../../backend/src/imeeting/web_static.py)）时 `/metrics` 直接对外。
* **建议**：加静态 Bearer 或仅绑定内网监听；把"不应直接暴露"从文档约束升格为代码约束。

### P2-2 崩溃中断的 WAV 不可读，且仓库内没有修复路径

* **位置**：[audio.py](../../backend/src/imeeting/engine/audio.py) 先写 44 字节零头、
  仅在 `close()` 回填真实长度。
* **影响**：进程被杀/机器断电时留下"数据完好但头全零"的文件（现场已发生一次
  75 MB / 39 分钟录音需要手工回填头）。当前代码里没有任何启动扫描或修复工具，
  同类故障下次仍需人工介入。
* **建议**：写入时用 `0xFFFFFFFF` 占位并周期性回填（或按固定块大小滚动写头）；
  启动时扫描 `data/audio` 中头为零的 WAV 并自动修复、记录告警。

### P2-3 分享访问令牌有效期与声明不符

* **位置**：[meeting\_extras.py](../../backend/src/imeeting/api/http/routes/meeting_extras.py)
  返回 `expires_in=7200`；实际令牌由 `create_access_token` 签发，有效期取
  `ACCESS_TOKEN_EXPIRE_MINUTES`（默认 1440 分钟）。
* **影响**：前端按 2 小时提示，实际可复用 24 小时；口令分享链接一旦外泄，
  暴露窗口是声明值的 12 倍。
* **建议**：为分享令牌单独传 `exp`（例如 2 小时），或把 `expires_in` 改成真实值。

### P2-4 请求体直接 `int()` 转换，非法输入返回 500

* **位置**：[export\_jobs.py](../../backend/src/imeeting/api/http/routes/export_jobs.py)
  的 `ids = [int(value) for value in …]`；同类还有
  [admin\_ops.py](../../backend/src/imeeting/api/http/routes/admin_ops.py) 的
  `meeting_id` / `limit` / `strength`。
* **复现**：`probe_smoke.py` → `POST /meetings/bulk-export-jobs {"ids": ["x"]}` 抛
  `ValueError`（对外即 500 Internal Server Error）。
* **影响**：契约输入错误被报成服务端故障，前端无法区分；也污染 5xx 指标和告警。
* **建议**：这些端点改用 Pydantic 请求模型（`list[int]`、`ge/le`），一并解决
  `payload: dict` 带来的"任意 JSON 都能进"问题。

### P3-1 前端 `npm run lint` 是死脚本，CI 也没有前端 lint 闸门

* **位置**：[package.json](../../frontend/package.json) 声明 `"lint": "eslint ."`，
  但 `devDependencies` 无 eslint、仓库无 eslint 配置；[ci.yml](../../.github/workflows/ci.yml)
  的前端 job 只跑 audit / test / typecheck / build。
* **影响**：前端实际上完全没有静态规则（hooks 依赖、未使用变量、a11y 属性等）。
* **建议**：要么补齐 eslint 配置（`eslint-plugin-react-hooks` 优先，收益最大）并纳入 CI，
  要么删掉该脚本，避免"看起来有闸门"。

### P3-2 文档数字与链接已失真

* [README.md](../../README.md) 写后端"544 项"、前端"66 项"，实测 743 / 89。
  这类数字每次加测试都会过期，建议改为"当前以 `pytest --collect-only` 为准"。
* 15 条相对链接断链，**全部越出仓库**指向外层 FunASR 工作区
  （如 [2026-10-31 核查文档](2026-10-31-code-document-gap-review.md) 的
  `../../../funasr/...`、`../../../docs/governance/CATALOG.md`，以及
  [修复计划](../plans/2026-10-31-gap-remediation-plan.md) 与
  [核查证据](evidence/2026-10-31-gap-review/README.md) 里的台账链接），违反
  [文档规制](../governance/README.md) 关于"独立检出即可读"的约定。

### P3-3 三套迁移路径并存，且 PostgreSQL 声明与实现冲突

* **位置**：[db.py](../../backend/src/imeeting/db.py) 顺序执行
  `SQLModel.create_all` → 手写 `_migrate_schema`（数百行幂等 DDL）→ Alembic。
* **风险**：一是同一件事有两个事实来源，改表结构必须同时改三处；二是
  `_migrate_schema` 里 `CREATE TRIGGER … RAISE(ABORT, …)`（槽位上限）是 SQLite 专有语法，
  而 [.env.example](../../backend/.env.example) 明确给出 `postgresql+psycopg://` 示例，
  按该示例切库会在启动迁移阶段直接失败；三是历史数据不合规时（如模板 slot>4）
  会 `raise RuntimeError` 拒绝启动，缺少"自动归档 + 启动"的降级路径。

### P3-4 仓库卫生

* `frontend/dist-next/`（24 文件 / 0.6 MB）是构建产物且**未被 `.gitignore` 覆盖**
  （规则只忽略了 `frontend/dist/`），当前以未跟踪状态挂在 `git status` 里。
* 5 个被改动的后端文件在工作副本里是 CRLF，提交后会与 `LF` 约定产生整文件 diff。

### P3-5 前端测试分布不均

* 11 个单测文件全部集中在 `lib/`、`store/`、`api/`；**没有任何组件测试**，
  `ParticipantsPanel` / `SpeakerRolesPanel` 这类交互较多的组件仅靠 3 个 e2e 用例兜底，
  且未配置覆盖率统计。

## 四、做得好的部分（建议保持）

* **文档纪律**：编号、状态、证据目录、结论边界分列；本轮现场报告已明确写"未重跑全后端套件"，
  没有把部分验证说成全量验证，这种自限声明值得保留。
* **测试隔离**：[conftest.py](../../backend/tests/conftest.py) 对临时库、`.env` 击穿、
  限流状态、模型池、活跃会话表逐项重置，注释写的是踩过的坑而不是套话。
* **生产 fail-fast**：[runtime\_checks.py](../../backend/src/imeeting/runtime_checks.py)
  在 `APP_ENV=production` 下拒绝弱密钥、默认口令、关闭鉴权与通配 CORS。
* **分享链路**：令牌只以 SHA-256 摘要落库、一次性明文返回、口令尝试独立限流、
  访问凭证 `scope=share` 且无法冒充用户令牌（`sub` 非整数会被拒）。
* **鉴权细节**：JWT 带 `ver`，改密后旧令牌立即失效；首次登录 428 强制改密；
  对象级归属校验集中到 `owned_meeting` / `owned_voiceprint`，路由里基本没有散落的判断。
* **静态资源**：SPA 兜底对 `/api/`、`/ws/` 保留 404，且做了真实路径包含校验，
  不存在目录穿越。
* **前端安全基线**：全仓库无 `dangerouslySetInnerHTML`、无 `eval`、无 `any`/`@ts-ignore`
  逃逸；令牌走消息帧而非 query string。
* **容器**：后端镜像非 root（uid 10001）、前端镜像带 healthcheck、compose 默认只绑定
  `127.0.0.1`。
* **现场教训固化**：WS 收尾"先还并发名额再落库"、会议状态四态收口、模型池按模型分锁，
  都有注释说明"为什么不能反过来"。

## 五、建议修复顺序

1. **P0-1 密钥解密**（含"重启后仍可用"回归）——影响面最广、最隐蔽。
2. **P0-2 bootstrap**（production 不回传口令 + 回归）。
3. **P1-1 阈值单一事实来源**，同时把 2 个红灯用例改到与意图一致；默认值变更单独写报告。
4. **P1-2 代理可信链**：先决定"信任代理"还是"关闭按 IP 计数"，两者都比现状安全。
5. P2 四项（metrics 鉴权、WAV 头回填、分享令牌 exp、Pydantic 请求模型）。
6. P3：lint 闸门、文档数字与断链、迁移收敛、仓库卫生、组件测试。

## 六、复验方式

```powershell
# 后端（预期 743 项全绿）
cd ../backend; python -m pytest tests -q; python -m ruff check src/imeeting tests
# 前端
cd ../frontend; npm test; npm run typecheck; npm run build
# 本轮新增探针（各自独立临时库）
python ../docs/reviews/evidence/2026-11-01-comprehensive-review/probes/probe_secret_reload.py
python ../docs/reviews/evidence/2026-11-01-comprehensive-review/probes/probe_bootstrap.py
```

修复 P0 后，建议同时把这两条探针改写成 `backend/tests/` 下的常规回归
（断言"重启后密钥可用"、"production 的 bootstrap 不回传口令"），
让它们成为会失败在先的用例，而不是一次性脚本。

## 七、关联文档

* [验证证据 IM-EVD-009](evidence/2026-11-01-comprehensive-review/README.md)
* [2026-11-01 实测故障修复](../reports/2026-11-01-field-fixes.md)：本轮工作副本改动的来源
* [2026-11-01 独立复核修复](../reports/2026-11-01-review-fixes.md)
* [2026-10-31 代码与文档对应性核查](2026-10-31-code-document-gap-review.md)：断链来源
* [生产可用性清单](../production-readiness.md)、[部署手册](../../deploy/README.md)
* [文档规制](../governance/README.md)

