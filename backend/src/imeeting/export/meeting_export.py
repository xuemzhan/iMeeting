"""Render meeting exports without depending on HTTP route modules."""

from __future__ import annotations

from sqlmodel import Session, select

from ..json_utils import safe_json_list
from ..models import Meeting, Minutes, Segment
from . import formats
from .document_export import build_docx, build_pdf


def _load_export_rows(db: Session, meeting_id: int):
    segments = db.exec(
        select(Segment)
        .where(Segment.meeting_id == meeting_id)
        .order_by(Segment.start_ms, Segment.seq, Segment.id)
    ).all()
    minutes_row = db.exec(
        select(Minutes)
        .where(Minutes.meeting_id == meeting_id, Minutes.status == "ready")
        .order_by(Minutes.version.desc())
    ).first()
    return segments, minutes_row


def render_meeting_export(
    db: Session,
    meeting: Meeting,
    fmt: str,
    *,
    company: str = "",
    footer: str = "",
) -> bytes:
    """Render a meeting in any configured export format."""
    segments, minutes_row = _load_export_rows(db, meeting.id)
    if fmt in ("docx", "pdf"):
        builder = build_docx if fmt == "docx" else build_pdf
        return builder(
            meeting,
            segments,
            minutes_row,
            company=company,
            footer=footer,
        )

    segment_rows = [
        {
            "start_ms": segment.start_ms,
            "end_ms": segment.end_ms,
            "text": segment.text,
            "speaker": segment.speaker,
        }
        for segment in segments
    ]
    meeting_row = {
        "id": meeting.id,
        "title": meeting.title,
        "duration_ms": meeting.duration_ms,
        "created_at": meeting.created_at.isoformat() if meeting.created_at else "",
    }
    minutes_dict = None
    if minutes_row is not None:
        minutes_dict = {
            "summary": minutes_row.summary,
            "topics": safe_json_list(minutes_row.topics),
            "todos": safe_json_list(minutes_row.todos),
            "model": minutes_row.model_used,
        }

    if fmt == "srt":
        body = formats.to_srt(segment_rows)
    elif fmt == "vtt":
        body = formats.to_vtt(segment_rows)
    elif fmt == "txt":
        body = formats.to_text(segment_rows)
    elif fmt == "md":
        body = formats.to_markdown(meeting_row, segment_rows, minutes_dict)
    else:
        body = formats.to_json(meeting_row, segment_rows, minutes_dict)
    return body.encode("utf-8")

