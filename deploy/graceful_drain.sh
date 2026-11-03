#!/bin/sh
set -eu

# 优雅发布前跑一次：请求服务排空并等到活跃会话归零（IM-PLN-004 §6.1）。
# 逻辑在 python -m imeeting.sqlite_backup drain 里，和其它平台同一份实现。
compose_file="${COMPOSE_FILE:-deploy/compose.yaml}"
env_file="${ENV_FILE:-deploy/production.env}"

docker compose --env-file "$env_file" -f "$compose_file" exec -T backend \
  python -m imeeting.sqlite_backup drain \
    --base-url "${BASE_URL:-http://127.0.0.1:8010}" \
    --username "${USERNAME:-admin}" \
    --password "${PASSWORD:?需要设置 PASSWORD（管理员口令）}" \
    --timeout-seconds "${TIMEOUT_SECONDS:-300}"
