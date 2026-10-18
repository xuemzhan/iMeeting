"""生产运维：任务队列、排空发布、审计完整性。"""

from __future__ import annotations

import csv
import io
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlmodel import Session, select

from ....audit import verify_audit_chain
from ....db import engine, get_session
from ....maintenance import drain_status, set_draining
from ....models import AuditLog, Job, User, utcnow
from ....schemas import JobOut
from .deps import require_admin

router = APIRouter(prefix="/api/v1/admin", tags=["admin-ops"])


@router.post("/search/reindex")
def reindex_search(
    payload: dict | None = None,
    _: User = Depends(require_admin),
):
    from ....jobs import enqueue_job
    from ....search_index import enabled

    if not enabled():
        raise HTTPException(status_code=400, detail="语义检索未配置")
    meeting_id = (payload or {}).get("meeting_id")
    job = enqueue_job(
        kind="embedding_reindex",
        meeting_id=int(meeting_id) if meeting_id is not None else None,
        payload={"meeting_id": meeting_id},
        dedupe_key=f"embedding:{meeting_id or 'all'}",
        priority=80,
    )
    return {"job_id": job.id, "status": job.status}


@router.post("/denoise-eval")
async def denoise_eval(
    payload: dict,
    _: User = Depends(require_admin),
):
    from ....session_registry import active_session_count

    if active_session_count() > 0:
        raise HTTPException(status_code=409, detail="会议进行中，暂不执行离线去噪评估")
    from ....denoise_eval import evaluate_denoise

    meeting_id = int(payload.get("meeting_id") or 0)
    if meeting_id <= 0:
        raise HTTPException(status_code=400, detail="meeting_id 无效")
    try:
        import asyncio

        return await asyncio.to_thread(
            evaluate_denoise,
            meeting_id,
            limit=int(payload.get("limit") or 8),
            strength=float(payload.get("strength") or 0.3),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(
    status: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    stmt = select(Job)
    if status:
        stmt = stmt.where(Job.status == status)
    return db.exec(stmt.order_by(Job.id.desc()).limit(limit)).all()


@router.post("/jobs/{job_id}/retry", response_model=JobOut)
def retry_job(
    job_id: int,
    _: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    row = db.get(Job, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if row.status == "running":
        raise HTTPException(status_code=409, detail="任务正在执行")
    row.status = "pending"
    row.attempts = 0
    row.run_after = utcnow()
    row.last_error = None
    row.locked_at = None
    row.updated_at = utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/maintenance")
def maintenance_status(_: User = Depends(require_admin)) -> dict:
    from ....session_registry import active_session_count

    return {**drain_status(), "active_sessions": active_session_count()}


@router.post("/maintenance/drain")
def start_drain(
    payload: dict | None = None,
    _: User = Depends(require_admin),
) -> dict:
    reason = str((payload or {}).get("reason") or "release")[:500]
    set_draining(True, reason)
    from ....session_registry import active_session_count

    return {
        "draining": True,
        "reason": reason,
        "active_sessions": active_session_count(),
    }


@router.post("/maintenance/resume")
def resume_service(_: User = Depends(require_admin)) -> dict:
    set_draining(False)
    return {"draining": False}


@router.get("/audit/verify")
def audit_verify(_: User = Depends(require_admin)) -> dict:
    with engine.connect() as conn:
        return verify_audit_chain(conn)


@router.get("/audit/export")
def audit_export(
    action: str | None = None,
    _: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    rows = db.exec(stmt.order_by(AuditLog.id)).all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "id",
            "created_at",
            "username",
            "action",
            "target",
            "ip",
            "payload",
            "prev_hash",
            "record_hash",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                row.id,
                row.created_at.isoformat() if row.created_at else "",
                row.username,
                row.action,
                row.target or "",
                row.ip or "",
                row.payload or "",
                row.prev_hash,
                row.record_hash,
            ]
        )
    name = f"audit-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
    return Response(
        content=buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
