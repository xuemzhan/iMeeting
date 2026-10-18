"""审计日志完整性：只追加哈希链与导出辅助。"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import event, text

from .models import AuditLog


def compute_audit_hash(
    *,
    prev_hash: str,
    user_id: int | None,
    username: str,
    action: str,
    target: str | None,
    payload: str | None,
    ip: str | None,
    created_at: str,
) -> str:
    content = json.dumps(
        {
            "prev": prev_hash,
            "user_id": user_id,
            "username": username,
            "action": action,
            "target": target,
            "payload": payload,
            "ip": ip,
            "created_at": created_at,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _created_text(value) -> str:
    """统一审计时间格式，保证 ORM 插入与 SQLite 回读哈希一致。"""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        return value.strftime("%Y-%m-%d %H:%M:%S.%f")
    text = str(value or "")
    if "T" in text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed.strftime("%Y-%m-%d %H:%M:%S.%f")
        except ValueError:
            pass
    return text


@event.listens_for(AuditLog, "before_insert")
def _seal_audit_record(_mapper, connection, target: AuditLog) -> None:
    if target.record_hash:
        return
    prev_hash = (
        connection.execute(
            text(
                "SELECT record_hash FROM audit_log "
                "WHERE record_hash IS NOT NULL AND record_hash != '' "
                "ORDER BY id DESC LIMIT 1"
            )
        ).scalar()
        or ""
    )
    created = _created_text(target.created_at)
    target.prev_hash = prev_hash
    target.record_hash = compute_audit_hash(
        prev_hash=prev_hash,
        user_id=target.user_id,
        username=target.username,
        action=target.action,
        target=target.target,
        payload=target.payload,
        ip=target.ip,
        created_at=created,
    )


def backfill_audit_hashes(connection) -> None:
    """为升级前的历史审计记录补链；已封存记录保持不变。"""
    rows = connection.execute(
        text(
            "SELECT id, user_id, username, action, target, payload, ip, "
            "created_at, prev_hash, record_hash FROM audit_log ORDER BY id"
        )
    ).fetchall()
    prev = ""
    for row in rows:
        created = _created_text(row[7])
        digest = compute_audit_hash(
            prev_hash=prev,
            user_id=row[1],
            username=str(row[2] or ""),
            action=str(row[3] or ""),
            target=row[4],
            payload=row[5],
            ip=row[6],
            created_at=created,
        )
        connection.execute(
            text(
                "UPDATE audit_log SET prev_hash=:prev, record_hash=:digest "
                "WHERE id=:id"
            ),
            {"prev": prev, "digest": digest, "id": row[0]},
        )
        prev = digest


def verify_audit_chain(connection) -> dict:
    rows = connection.execute(
        text(
            "SELECT id, user_id, username, action, target, payload, ip, "
            "created_at, prev_hash, record_hash FROM audit_log ORDER BY id"
        )
    ).fetchall()
    prev = ""
    for row in rows:
        digest = compute_audit_hash(
            prev_hash=prev,
            user_id=row[1],
            username=str(row[2] or ""),
            action=str(row[3] or ""),
            target=row[4],
            payload=row[5],
            ip=row[6],
            created_at=_created_text(row[7]),
        )
        if str(row[8] or "") != prev or str(row[9] or "") != digest:
            return {
                "valid": False,
                "records": len(rows),
                "broken_at": row[0],
                "expected": digest,
                "actual": row[9],
            }
        prev = digest
    return {"valid": True, "records": len(rows), "broken_at": None}
