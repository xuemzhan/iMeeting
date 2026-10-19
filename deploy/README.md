# iMeeting 单机生产部署

该部署包使用两个容器：

- `frontend`：Nginx 托管前端静态资源，并代理 `/api` 与 `/ws`；
- `backend`：FastAPI、FunASR、后台任务与 SQLite。

只有前端端口暴露给宿主机，后端仅在 Compose 内部网络可达。

## 资源要求

- CPU：4 核以上；
- 内存：8GB 以上，模型常驻约 4.7GB；
- 磁盘：建议 30GB 以上；
- 首次启动会下载模型，模型缓存位于 `imeeting-models` 卷；
- 当前只支持**单后端实例**，因为活跃会话注册表、登录限流和任务 worker 是进程内状态。

## 启动

```sh
cp deploy/production.env.example deploy/production.env
```

生成随机密钥并修改 `deploy/production.env`：

```sh
openssl rand -hex 32
```

启动：

```sh
docker compose --env-file deploy/production.env -f deploy/compose.yaml up -d --build
```

首次启动需等待模型加载。检查：

```sh
curl -fsS http://127.0.0.1:8080/healthz
docker compose --env-file deploy/production.env -f deploy/compose.yaml ps
docker compose --env-file deploy/production.env -f deploy/compose.yaml logs -f backend
```

## 数据与模型

- `imeeting-data`：数据库、音频、导出、日志、指标与备份；
- `imeeting-models`：ModelScope、HuggingFace 与 Torch 模型缓存。

不要把数据库或模型文件直接复制进镜像。升级时只重建镜像，不删除卷。

## 备份

备份脚本使用 SQLite 在线备份 API，可在服务运行时执行：

```sh
sh deploy/backup_sqlite.sh
```

备份写入 `/data/backups/meeting-<UTC时间>.db`。生产环境应再同步到异机或对象存储。

恢复必须在停写状态下进行，脚本会先保存一份 `pre-restore-*.db` 安全副本：

```sh
CONFIRM_RESTORE=yes sh deploy/restore_sqlite.sh meeting-20261019T010203Z.db
```

恢复完成后脚本会自动重新启动后端并等待 `/api/v1/ready`，恢复失败时也会尽力重启，
避免服务停留在停止状态。若当前数据库已损坏，脚本会保留
`pre-restore-<UTC时间>.corrupt.db` 原始文件，不会因无法校验旧库而拒绝恢复。

## 升级与回滚

升级：

```sh
docker compose --env-file deploy/production.env -f deploy/compose.yaml up -d --build
```

回滚时切换到上一个镜像标签或 Git 版本后重新执行上述命令。数据库升级前先完成备份。

## HTTPS

默认情况下前端只绑定 `127.0.0.1:8080`，可通过 SSH 隧道或本机访问，避免绕过 HTTPS
直接暴露容器端口。

启用内置 Caddy TLS 网关：

```sh
PUBLIC_ORIGIN=https://meeting.example.com
SITE_ADDRESS=meeting.example.com
```

然后：

```sh
docker compose --env-file deploy/production.env -f deploy/compose.yaml \
  --profile tls up -d --build
```

Caddy 会自动申请和续期证书，并将 HTTP/WebSocket 请求代理到前端容器。生产环境必须
使用真实 DNS 名称；`localhost` 仅用于本机验证。

## 安全注意事项

- `APP_ENV=production` 会拒绝弱 `SECRET_KEY`、默认管理员口令、关闭鉴权和通配 CORS；
- 管理员首次登录必须修改初始密码，修改前无法访问业务页面；
- `deploy/production.env` 不要提交到 Git；
- `/metrics` 未通过 Nginx 暴露，监控系统应通过内网采集。
