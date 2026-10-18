#!/bin/sh
set -eu

compose_file="${COMPOSE_FILE:-deploy/compose.yaml}"
env_file="${ENV_FILE:-deploy/production.env}"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"

docker compose --env-file "$env_file" -f "$compose_file" exec -T backend \
  python - "$stamp" <<'PY'
import sqlite3
import sys
from pathlib import Path

stamp = sys.argv[1]
source_path = Path("/data/meeting.db")
backup_dir = Path("/data/backups")
backup_dir.mkdir(parents=True, exist_ok=True)
target_path = backup_dir / f"meeting-{stamp}.db"

source = sqlite3.connect(source_path)
target = sqlite3.connect(target_path)
try:
    source.backup(target)
finally:
    target.close()
    source.close()

print(target_path)
PY
