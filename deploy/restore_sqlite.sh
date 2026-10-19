#!/bin/sh
set -eu

if [ "${CONFIRM_RESTORE:-}" != "yes" ]; then
  echo "Refusing to restore without CONFIRM_RESTORE=yes" >&2
  exit 2
fi

backup_name="${1:-}"
case "$backup_name" in
  ""|*/*|*\\*|*..*)
    echo "Usage: CONFIRM_RESTORE=yes $0 <backup-file-name.db>" >&2
    exit 2
    ;;
esac

compose_file="${COMPOSE_FILE:-deploy/compose.yaml}"
env_file="${ENV_FILE:-deploy/production.env}"

compose() {
  docker compose --env-file "$env_file" -f "$compose_file" "$@"
}

compose run --rm --no-deps --entrypoint python backend \
  -m imeeting.sqlite_backup verify \
  --path "/data/backups/${backup_name}"

compose stop backend

wait_for_backend() {
  attempts="${READY_ATTEMPTS:-90}"
  current=0
  while [ "$current" -lt "$attempts" ]; do
    if compose exec -T backend \
      curl -fsS http://127.0.0.1:8000/api/v1/ready >/dev/null 2>&1; then
      return 0
    fi
    current=$((current + 1))
    sleep 2
  done
  echo "Backend did not become ready after restore" >&2
  return 1
}

restore_backend() {
  restore_status=$?
  trap - EXIT
  if ! compose up -d backend; then
    echo "Failed to restart backend after restore" >&2
    exit 1
  fi
  if ! wait_for_backend; then
    exit 1
  fi
  exit "$restore_status"
}
trap restore_backend EXIT

compose run --rm --no-deps \
  --entrypoint python backend -m imeeting.sqlite_backup restore \
  --current /data/meeting.db \
  --backup "/data/backups/${backup_name}" \
  --backup-dir /data/backups
