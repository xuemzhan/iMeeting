# 生产稳定性、完整性与安全性复核

<!-- doc-governance: IM-REV-002 -->
> 文档类型：评审；状态：阶段快照；内容基准日期：2026-10-29；整理日期：2026-10-31。
> 单 backend + SQLite 的复核结果；不代替目标环境上线验收。
> 维护责任：待指派（iMeeting 文档维护者）。[台账](README.md) · [维护规则](governance/README.md)。

复核日期：2026-10-29（Asia/Shanghai）。

## 信任边界

| 输入来源 | 可信度 | 主要落点（相对 iMeeting 根目录） |
|---|---|---|
| HTTP Bearer、登录及改密请求 | 不可信 | `backend/src/imeeting/api/http/routes/deps.py:39`、`auth.py:55`、`auth.py:150` |
| WebSocket 首帧、控制消息及 PCM | 不可信 | `backend/src/imeeting/api/ws/stream.py:76` |
| 分享令牌、口令及下载请求 | 不可信 | `backend/src/imeeting/api/http/routes/meeting_extras.py:408`、`share_throttle.py:39` |
| 数据库任务状态和结果正文 | 半可信，包含用户派生数据 | `backend/src/imeeting/application/jobs.py:67`、`:296`、`:313` |
| 管理员设置的 LLM/Embedding 地址、模型来源 | 管理权限内可信，上游响应不可信 | `backend/src/imeeting/llm/client.py:25`、`search_index.py:20`、`engine/pool.py:127` |
| 生产环境变量、数据卷、备份文件 | 部署权限内可信，仍需校验 | `backend/src/imeeting/runtime_checks.py:14`、`sqlite_backup.py`、`deploy/compose.yaml` |
| 导出和录音文件路径 | 服务端生成，数据库保存 | `backend/src/imeeting/api/http/routes/export_jobs.py`、`meeting_extras.py:539` |

## 结论与范围

iMeeting 具备单机生产所需的业务和运维基础，但原有“完成”清单存在测试覆盖盲区。
本轮已修复下面列出的具体缺陷，并在独立数据库中进行回归验证。
部署边界仍是**单 backend 进程、单 SQLite 数据卷**，不能据此认定整个 FunASR
仓库、所有推理后端或多实例架构均已达到生产标准。

本目录处于源码快照中，没有可用的 Git 元数据，无法提供可信的原始提交 diff。
代码改动保留在工作目录，未部署至生产服务器。

## 发现与修复

| 发现 | 严重性 / 状态 | 触发链、影响与修复证据 |
|---|---|---|
| 录音线程泄漏 | 高（稳定性）/ 已修复 | 成功开启一场会话时，原先先创建 `AudioSink(-1)`，再覆盖为正式录音对象；占位对象的线程未关闭。默认生产鉴权开启时，已登录用户反复开启/停止会议即可累计线程。现延迟创建正式录音对象，添加连续成功/拒绝连接后的线程数回归。`api/ws/stream.py:95`，路径前缀均为 `backend/src/imeeting/`。 |
| 重启后任务永久处于 running | 高 / 已复现并修复 | 进程在领取任务后十分钟内重启；旧实现只在启动时恢复已经超时的任务，近期任务之后再无恢复机会。现单实例 worker 启动前立即恢复遗留任务；耗尽尝试次数的任务进入 failed。`application/jobs.py:296`。 |
| 已删除会议仍可通过分享下载录音 | 高 / 已复现并修复 | 持有有效分享链接及访问凭证的人，在会议软删除后仍能调用 `/api/v1/share/{token}/audio` 下载文件。现所有分享访问集中校验会议存在且未删除，含 open 和已签发凭证的下载。`api/http/routes/meeting_extras.py:408`。 |
| 修改密码不撤销旧登录凭证 | 高 / 已验证并修复 | 已经获得有效 JWT 的人，在账号主人改密后仍可用旧 JWT 访问 HTTP 和新建 WS。现用户保存 auth_version，改密时递增；HTTP/WS 建连校验版本，当前设备自动领取替代令牌。`models.py:28`、`api/http/routes/auth.py:174`、`deps.py:57`、`api/ws/stream.py` 的 `_resolve_identity`。已有数据库支持幂等升级和 Alembic 版本迁移。 |
| 分享限流状态无界、封禁时间与提示不符 | 中 / 已复现并修复 | 匿名请求不断更换任意不存在的 token，旧 check 会保存空列表，占用永久增长；达到口令失败阈值后，提示 60 秒却可能实际封禁 300 秒。现查询不分配空桶，最多保留 10,000 个桶，单桶有上限，过期回收，按锁定时间解锁。`share_throttle.py:15`、`:39`、`:56`。 |
| 模型未就绪时探针仍返回可用 | 中 / 已验证并修复 | 模型 loading 仍返回 ready=200；流式成功、离线失败时错误也会被 loading 分支遮蔽。现正常识别必须全部模型就绪，loading/degraded 返回 503；默认预热模式下新 WS 在未就绪时被明确拒绝，避免积压无法处理的音频。关闭预热的显式懒加载路径在接收音频前加载模型。`api/http/routes/system.py:17`、`:71`、`api/ws/stream.py:177`。 |
| 非对象控制消息终止会议 | 中 / 已复现并修复 | 连接发送合法 JSON `null`、数组、字符串后直接调用 `.get()` 抛异常，会议进入 failed。现校验首帧对象类型，忽略不符合控制消息结构的后续值。`api/ws/stream.py:150`、主循环控制消息解析。 |
| 握手、空闲和会话时长缺少实际约束 | 中 / 已验证并修复 | 建连后不发 start 或长期不发消息；已声明的 `session.max_duration_minutes` 原先没有执行点。现首帧 15 秒、空闲 90 秒、会话墙钟及累计音频双重时长限制；超时收口为 abandoned 并回收连接。正常前端每 20 秒心跳。`api/ws/stream.py:69`、`:245`。 |
| 磁盘背压阻塞事件循环、关闭确认早于持久化 | 中 / 已验证并修复 | 录音队列变慢时同步 push 会最多阻塞一秒，close 也可能等待写盘。现写入/收尾在线程中执行；closed 在会话记录和录音收尾完成后发送，重连时间轴测试通过。`api/ws/stream.py` 的 `sink.push`、`sink.close` 和 `closed_payload`。 |
| 每次监控扫描所有任务正文 | 中（容量）/ 已测量并修复 | Prometheus 抓取调用 job_stats/job_metrics，两次加载全部 ORM 对象和任务正文。现使用数据库 GROUP BY / MIN 汇总，仅返回状态计数和最早时间；同时兼容 SQLite 朴素时间对象。`application/jobs.py:313`、`:321`。 |
| 领取锁绑定到旧事件循环 | 中 / 已观察异常并修复 | 复用应用生命周期时，模块全局 asyncio.Lock 可能绑定旧 loop，worker 反复抛错。现同步领取过程用线程锁包住整个事务；多线程竞争领取测试通过。`application/jobs.py:19`、`:67`。 |
| JWT 必需字段没有强制存在 | 低 / 加固完成 | 固定算法签名校验原本存在，但缺失 exp/iat/sub 的错误签发令牌仍可能被接受；这不等于外部可伪造签名。现要求三项存在，并拒绝非字符串输入。`security.py:141`。 |
| 依赖安全版本过旧 | 取决于公告与使用前提 / 扫描并修复构建输入 | 前端扫描原有 3 个受影响依赖，主要涉及开发工具 Vite/Vitest，未证明线上静态文件服务可利用。升级至 Vite 6.4.5、Vitest 4.1.12。共享 Python 环境中，项目依赖闭包涉及 certifi、cryptography、Pillow 的公告；为生产构建和 CI 增加安全下限，在独立环境重装并重新审计。详见依赖证据。 |

“改密撤销”作用于之后的 HTTP 请求和 WebSocket 建连；本轮没有加入对已经建立的
WebSocket 的强制踢出机制。历史版本未携带 ver 的令牌按版本 0 处理，账号第一次
改密后也会失效。`/auth/password` 保留原有用户字段，并新增 `access_token`，自定义
客户端也应保存替代令牌。

任务恢复为至少一次执行语义；如果某项外部调用已经成功而进程恰好在记录结果前崩溃，
重试仍可能再次调用外部服务，不能据此宣称端到端恰好一次。

## 验证记录

所有数据库、录音和日志测试均使用独立临时目录；公共测试夹具新增 DATA_DIR 隔离。
真实 HTTP/WS 压测仅监听本机临时端口，结束后已停止测试服务。

| 检查 | 本轮结果 |
|---|---|
| 原有后端基线 | 545 项通过；证明原有测试未覆盖上述所有缺陷 |
| 最终后端独立环境全量 | **568 项全部通过**，347.35 秒；136 条第三方弃用告警，无失败或跳过 |
| 专项协议和新增回归 | 45 项通过，包含线程回收、分享、令牌、时长、重连时间轴 |
| Ruff | `src/imeeting`、tests、迁移和新增基准脚本通过 |
| 前端单元 | 66 项通过 |
| 前端类型检查及生产构建 | 通过，入口 JS gzip 约 130.78KB |
| 浏览器 E2E | 1 项通过：首次登录 → 强制改密 → 新令牌访问后台 |
| 数据库版本迁移 | 临时库首次升级、重复启动、回退至 baseline、再次升级均通过，head=`20261029_01` |
| 独立 Python 环境依赖扫描 | `pip-audit --local --skip-editable`：未发现已知漏洞；`pip check` 通过。未安装 asr extra |
| 前端完整依赖扫描 | npm 官方 registry：0 个已知漏洞；镜像源不提供审计接口，因此切换官方源重扫 |
| 真实 HTTP/WS 链路压测（echo） | 4/4 并发连接、8 类异常输入、5 轮硬断后重连、背压、60 秒音频快速输入均通过 |

压测的“60 秒”是音频时长，该场景快速发送，仅约 0.3 秒处理时间；它不构成运行
60 秒、更不构成 24 小时长稳或真实模型性能证明。并发 4 路各发送 5 秒，约 5.1 秒
结束；此结果只说明当前机器的 echo 链路，不代表真实 ASR 能承受相同并发。

本轮 Python 为 Windows / Python 3.14.3；清洁环境中主要包为 FastAPI 0.162.0、
SQLModel 0.0.48、SQLAlchemy 2.1.6、cryptography 50.0.2、Pillow 13.0.0。
生产镜像的 Python 3.12/Linux 组合仍需单独构建验证。

复现命令（分别在 backend、frontend 目录执行）：

```powershell
# backend：使用与生产安全约束一致的独立环境
..\.venv-production-review\Scripts\python.exe -m pytest tests -q --tb=short
python -m ruff check src/imeeting tests scripts/benchmark_job_metrics.py migrations
python scripts/benchmark_job_metrics.py --jobs 5000 --runs 3

# frontend
npm test
npm run typecheck
npm run build
npm run e2e
npm audit --registry=https://registry.npmjs.org
```

## 性能测量

场景：5,000 条历史已完成任务，每条正文 4,096 字节，重复执行一次
`job_stats()` + `job_metrics()`，预热后同机三次。Python 3.14.3，CPU 标识
Intel64 Family 6 Model 154 Stepping 3，使用标准库 perf_counter / tracemalloc。
没有调用真实模型，也没有把此测量作为识别吞吐提升。

| 实现 | 最小 / 中位 / 最大耗时 | 中位 Python 峰值分配 |
|---|---|---|
| 修改前，全表 ORM 加载 | 581.193 / 599.104 / 634.971 ms | 31,243,733 bytes |
| 修改后，数据库聚合 | 1.413 / 1.454 / 3.526 ms | 14,865 bytes |

瓶颈位于数据库读取后的 Python 对象构造及正文内存分配；修复减少返回行与字段数量。
代价是数据库仍需执行聚合查询，测量也不覆盖百万级数据、磁盘冷缓存或多进程。
这不是进程 RSS、数据库页缓存或整机内存峰值。

## 安全检查覆盖与限制

- 认证、会议属主/ACL、分享凭证、录音/导出访问：检查实现并运行已有接口隔离测试。
- SQL/命令注入、路径处理和解析：搜索动态 SQL、subprocess、eval/exec、pickle、
  yaml.load、上传和文件响应入口。检查范围内主要数据库访问通过 ORM/参数绑定完成；
  这不是对全部第三方库与所有渲染路径的形式化安全证明。
- 出站请求：LLM/Embedding 端点由管理员配置，允许企业内网地址；本轮没有用全局
  私网封禁破坏此功能，也没有开展 DNS 重绑定、代理链或云元数据访问渗透测试。
- 凭据：生产密钥启动门禁、配置加密与分享摘要存储已有实现并受现有测试覆盖。
  本轮未读取用户真实密钥内容，也未扫描 Git 历史。推送前确认 iMeeting 子目录存在
  独立 Git 仓库；外层 FunASR 源码快照没有 Git 元数据。
- 依赖：共享 Python 首次全环境扫描包含大量其他项目包，不能把其所有告警算作
  iMeeting 漏洞。另按 pyproject 的已安装运行时依赖闭包整理了项目相关结果，再建立
  独立环境修复验证；电脑原共享环境未被升级，旧启动方式仍可能使用旧依赖。
- bcrypt 长口令仍保留现有前 72 字节兼容语义；本轮未迁移历史密码哈希方案。
- `/metrics` 必须保持内部访问；当前部署 Nginx 不把这个路径代理至 backend。
  若直接公开 backend 端口，需要额外限制此运维入口。

## 生产发布前仍需验收

1. 在目标 Linux/Python 3.12 环境构建 backend/frontend 镜像，对 **backend 及其 OS 包**
   和实际安装的 `[asr]` 推理依赖重新扫描。本机没有 Docker，未执行容器启动、TLS、
   Nginx/Caddy 校验或 Trivy 镜像扫描。
2. 用目标 CPU/GPU、真实语音及预期并发测量 RTF、首字延迟 p95/p99、定稿延迟、
   峰值 RSS/显存、24 小时运行、磁盘变慢/写满和任务执行中重启。仓库 README 的
   历史识别数据不是本轮测量结果。
3. 固定已验证的模型 revision 和镜像 digest，保存完整依赖清单。当前安全下限并非
   完整跨平台依赖锁，不能保证未来任意解析结果都与今天相同。
4. 在隔离的生产等价环境执行备份恢复和发布排空演练，确认 RPO/RTO、告警能真实送达。
5. 多实例/高可用仍需 PostgreSQL、共享限流及任务协调、对象存储与故障切换改造；
   单 SQLite 数据卷不得挂载多个 backend 副本。

本报告的验证范围仅限 iMeeting，不能直接套用到 FunASR 模型、原生 runtime、portable
分发、OpenClaw 集成和其他示例。后续全仓优化已另行补充 Python 服务鉴权、上传/推理
容量限制、安全解析与分发修复，其验证结果记录在外层 FunASR 的全仓优化方案中；
本报告不提供所有后端的生产安全结论。

参考：[PyJWT 对必需字段的要求](https://pyjwt.readthedocs.io/en/stable/api.html)、
[FastAPI 部署与进程内存边界](https://fastapi.tiangolo.com/deployment/concepts/)。

依赖扫描证据归档于 [audits/2026-10-29](./audits/2026-10-29/README.md)。
