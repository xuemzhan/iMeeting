#!/bin/sh
set -eu

compose_file="${COMPOSE_FILE:-deploy/compose.yaml}"
env_file="${ENV_FILE:-deploy/production.env}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"

docker compose --env-file "$env_file" -f "$compose_file" exec -T backend \
  python -m imeeting.sqlite_backup backup \
    --source /data/meeting.db \
    --target "/data/backups/meeting-${stamp}.db"
