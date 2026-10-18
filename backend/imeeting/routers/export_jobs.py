"""异步导出任务接口。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlmodel import Session

from ..db import get_session
from ..jobs import enqueue_job
from ..models import Job, User
from ..schemas import JobOut
from .deps import get_current_user_or_anonymous
from .meetings import _owned

router = APIRouter(prefix="/api/v1", tags=["exports"])


@router.post("/meetings/{meeting_id}/export-jobs", response_model=JobOut)
def create_single_export(
    meeting_id: int,
    payload: dict,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    _owned(meeting_id, user, db)
    job = enqueue_job(
        kind="export_single",
        meeting_id=meeting_id,
        owner_id=user.id,
        payload={"ids": [meeting_id], "format": str(payload.get("format") or "md")},
        priority=50,
    )
    return job


@router.post("/meetings/bulk-export-jobs", response_model=JobOut)
def create_bulk_export(
    payload: dict,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    ids = [int(value) for value in payload.get("ids") or []]
    if not ids:
        raise HTTPException(status_code=400, detail="没有要导出的会议")
    for meeting_id in ids:
        _owned(meeting_id, user, db)
    job = enqueue_job(
        kind="export_bulk",
        owner_id=user.id,
        payload={"ids": ids, "format": str(payload.get("format") or "md")},
        priority=60,
    )
    return job


def _job_for_user(job_id: int, user: User, db: Session) -> Job:
    row = db.get(Job, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="导出任务不存在")
    if user.role != "admin" and row.owner_id != user.id:
        raise HTTPException(status_code=403, detail="无权访问该导出任务")
    return row


@router.get("/export-jobs/{job_id}", response_model=JobOut)
def export_status(
    job_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    return _job_for_user(job_id, user, db)


@router.get("/export-jobs/{job_id}/download")
def export_download(
    job_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    row = _job_for_user(job_id, user, db)
    if row.status != "succeeded" or not row.result_path:
        raise HTTPException(status_code=409, detail="导出尚未完成")
    path = Path(row.result_path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="导出文件已过期")
    return FileResponse(
        path,
        filename=row.download_name or path.name,
        media_type="application/octet-stream",
    )
