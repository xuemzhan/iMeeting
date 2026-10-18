"""异步导出任务：直接写文件，避免长会议 ZIP 占用内存。"""

from __future__ import annotations

import time
import zipfile
from pathlib import Path

from sqlmodel import select

from .db import session_scope
from .export import formats
from .export.document_export import build_docx, build_pdf
from .models import Job, Meeting, Minutes, Segment
from .settings import SERVER_CONFIG, get_config


def export_dir() -> Path:
    path = Path(SERVER_CONFIG["data_dir"]) / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _export_one(db, meeting: Meeting, fmt: str) -> bytes:
    segments = db.exec(
        select(Segment)
        .where(Segment.meeting_id == meeting.id)
        .order_by(Segment.start_ms, Segment.seq, Segment.id)
    ).all()
    minutes_row = db.exec(
        select(Minutes)
        .where(Minutes.meeting_id == meeting.id, Minutes.status == "ready")
        .order_by(Minutes.version.desc())
    ).first()
    if fmt in ("docx", "pdf"):
        builder = build_docx if fmt == "docx" else build_pdf
        return builder(
            meeting,
            segments,
            minutes_row,
            company=str(get_config("export.company_name") or ""),
            footer=str(get_config("export.footer_text") or ""),
        )
    from .api.http.routes.meetings import _export_body

    return _export_body(db, meeting, fmt).encode("utf-8")


def run_export_job(job: Job, payload: dict) -> tuple[Path, str, dict]:
    fmt = str(payload.get("format") or "md").lower()
    allowed = get_config("export.formats") or ["md"]
    if fmt not in allowed:
        raise RuntimeError(f"不支持的导出格式：{fmt}")
    ids = [int(value) for value in payload.get("ids") or []]
    if not ids and job.meeting_id is not None:
        ids = [job.meeting_id]
    if not ids:
        raise RuntimeError("没有要导出的会议")

    ext, _ = formats.EXPORTERS[fmt]
    stamp = int(time.time())
    if job.kind == "export_single" and len(ids) == 1:
        output = export_dir() / f"meeting-{ids[0]}-{job.id}.{ext}"
        with session_scope() as db:
            meeting = db.get(Meeting, ids[0])
            if meeting is None or meeting.deleted_at is not None:
                raise RuntimeError("会议不存在")
            output.write_bytes(_export_one(db, meeting, fmt))
        return output, output.name, {"meetings": 1, "format": fmt}

    output = export_dir() / f"meetings-{job.id}-{stamp}.zip"
    count = 0
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        with session_scope() as db:
            for meeting_id in dict.fromkeys(ids):
                meeting = db.get(Meeting, meeting_id)
                if meeting is None or meeting.deleted_at is not None:
                    continue
                safe = "".join(
                    c if c not in '\\/:*?"<>|' else "_" for c in meeting.title
                )[:60]
                archive.writestr(
                    f"{meeting_id}-{safe}.{ext}",
                    _export_one(db, meeting, fmt),
                )
                count += 1
    return output, output.name, {"meetings": count, "format": fmt}


def cleanup_exports(max_age_hours: int = 24) -> int:
    cutoff = time.time() - max_age_hours * 3600
    removed = 0
    for path in export_dir().glob("*"):
        try:
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
