"""回收站过期清理：软删除超过保留期的会议物理删除。"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import delete as sa_delete
from sqlmodel import select

from .db import session_scope
from .meeting_files import cleanup_meeting_files
from .models import (
    ActionItem,
    Job,
    Meeting,
    MeetingMember,
    MeetingSession,
    Minutes,
    Segment,
    SegmentEmbedding,
    ShareLink,
    TranscriptReview,
    Voiceprint,
    utcnow,
)
from .settings import get_config

logger = logging.getLogger(__name__)


def _delete_rows(db, meeting: Meeting) -> None:
    mid = meeting.id
    db.execute(sa_delete(Segment).where(Segment.meeting_id == mid))
    db.execute(sa_delete(Minutes).where(Minutes.meeting_id == mid))
    db.execute(sa_delete(MeetingSession).where(MeetingSession.meeting_id == mid))
    db.execute(sa_delete(ActionItem).where(ActionItem.meeting_id == mid))
    db.execute(sa_delete(ShareLink).where(ShareLink.meeting_id == mid))
    db.execute(sa_delete(TranscriptReview).where(TranscriptReview.meeting_id == mid))
    db.execute(sa_delete(MeetingMember).where(MeetingMember.meeting_id == mid))
    db.execute(sa_delete(Job).where(Job.meeting_id == mid))
    db.execute(sa_delete(SegmentEmbedding).where(SegmentEmbedding.meeting_id == mid))
    db.execute(
        sa_delete(Voiceprint).where(
            Voiceprint.meeting_id == mid,
            Voiceprint.provisional == True,  # noqa: E712
        )
    )
    db.delete(meeting)


def purge_expired_meetings(retention_days: int | None = None) -> int:
    retention_days = (
        int(retention_days)
        if retention_days is not None
        else int(get_config("storage.trash_retention_days") or 30)
    )
    retention_days = max(1, retention_days)
    cutoff = utcnow() - timedelta(days=max(1, retention_days))
    purged: list[tuple[int, str | None]] = []
    with session_scope() as db:
        rows = db.exec(
            select(Meeting).where(
                Meeting.deleted_at.is_not(None),
                Meeting.deleted_at < cutoff,
            )
        ).all()
        for meeting in rows:
            mid = meeting.id
            purged.append((mid, meeting.audio_path))
            _delete_rows(db, meeting)
        db.commit()

    for mid, audio_path in purged:
        try:
            cleanup_meeting_files(mid, audio_path)
        except Exception:
            logger.exception("回收站清理文件失败 meeting=%s", mid)
    if purged:
        logger.info("回收站已清理 %d 场过期会议", len(purged))
    return len(purged)


def purge_retention_expired() -> int:
    """按全局/会议自定义保留期删除已结束会议；法律保留永远豁免。"""
    global_days = int(get_config("storage.meeting_retention_days") or 0)
    if global_days <= 0:
        return 0
    now = utcnow()
    purged: list[tuple[int, str | None]] = []
    with session_scope() as db:
        rows = db.exec(
            select(Meeting).where(
                Meeting.deleted_at.is_(None),
                Meeting.legal_hold == False,  # noqa: E712
                Meeting.status.in_(("finished", "abandoned", "failed")),
            )
        ).all()
        for meeting in rows:
            days = meeting.retention_days or global_days
            if days <= 0:
                continue
            anchor = meeting.finished_at or meeting.created_at
            if anchor is None or anchor >= now - timedelta(days=days):
                continue
            purged.append((meeting.id, meeting.audio_path))
            _delete_rows(db, meeting)
        db.commit()
    for mid, audio_path in purged:
        try:
            cleanup_meeting_files(mid, audio_path)
        except Exception:
            logger.exception("保留期清理文件失败 meeting=%s", mid)
    return len(purged)
