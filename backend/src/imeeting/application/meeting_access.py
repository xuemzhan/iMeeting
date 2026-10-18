"""Meeting access, search and response projection helpers."""

from __future__ import annotations

import logging

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlmodel import Session, select

from ..json_utils import safe_json_list
from ..models import Meeting, MeetingMember, Minutes, User
from ..schemas import MinutesOut

logger = logging.getLogger(__name__)


def owned_meeting(
    meeting_id: int,
    user: User,
    db: Session,
    *,
    include_deleted: bool = False,
    write: bool = False,
) -> Meeting:
    """Return a meeting visible to the user or raise the established HTTP error."""
    meeting = db.get(Meeting, meeting_id)
    if meeting is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会议不存在")
    if meeting.deleted_at is not None and not include_deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会议已在回收站")
    if meeting.owner_id != user.id and user.role != "admin":
        member = db.exec(
            select(MeetingMember).where(
                MeetingMember.meeting_id == meeting_id,
                MeetingMember.user_id == user.id,
            )
        ).first()
        allowed = member is not None and (
            not write or member.role in ("editor", "owner")
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权访问或修改该会议",
            )
    return meeting


def fts_meeting_ids(db: Session, query: str) -> list[int] | None:
    """Search the trigram FTS index, returning None when LIKE fallback is needed."""
    term = query.strip()
    if len(term) < 3:
        return None
    try:
        phrase = '"' + term.replace('"', '""') + '"'
        rows = db.execute(
            text(
                "SELECT DISTINCT meeting_id FROM segment_fts "
                "WHERE segment_fts MATCH :q"
            ),
            {"q": phrase},
        ).all()
        return [int(row[0]) for row in rows]
    except Exception:
        logger.exception("FTS 检索失败，回退 LIKE")
        return None


def minutes_to_out(row: Minutes) -> MinutesOut:
    return MinutesOut(
        id=row.id or 0,
        meeting_id=row.meeting_id,
        summary=row.summary,
        topics=safe_json_list(row.topics),
        todos=safe_json_list(row.todos),
        raw_markdown=row.raw_markdown,
        model_used=row.model_used,
        status=row.status,
        error=row.error,
        version=row.version,
        is_draft=row.is_draft,
        edited=row.edited,
        created_at=row.created_at,
    )

