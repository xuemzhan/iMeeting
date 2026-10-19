"""产品化增强接口：元信息、编辑、分享、待办、日历、看板、知识库。"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse
from sqlalchemy import exists, func, or_, text
from sqlmodel import Session, select

from ....application.meeting_access import (
    fts_meeting_ids as _fts_meeting_ids,
)
from ....application.meeting_access import (
    minutes_to_out as _minutes_out,
)
from ....application.meeting_access import (
    owned_meeting as _owned,
)
from ....db import get_session
from ....json_utils import safe_json_list
from ....models import (
    ActionItem,
    AuditLog,
    LLMUsage,
    Meeting,
    Minutes,
    Segment,
    ShareLink,
    User,
    utcnow,
)
from ....schemas import (
    ActionItemOut,
    ActionItemUpdate,
    MeetingMetadataUpdate,
    MeetingOut,
    MeetingSearchResult,
    MinutesEditRequest,
    MinutesOut,
    SearchMatchOut,
    SegmentOut,
    SegmentUpdate,
    ShareCreateRequest,
    ShareOpenOut,
    ShareOpenRequest,
    ShareOut,
    TranscriptReviewOut,
)
from ....security import (
    create_access_token,
    decode_access_token,
    hash_password,
    hash_share_token,
    new_share_token,
    verify_password,
)
from ....settings import BASE_DIR, get_config
from ....share_throttle import check_allowed, clear_failures, record_failure
from .deps import get_current_user_or_anonymous, require_admin

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["meeting-extras"])


@router.post("/meetings/{meeting_id}/review-audio")
def queue_audio_review(
    meeting_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    _owned(meeting_id, user, db, write=True)
    from ....application.jobs import enqueue_job

    job = enqueue_job(
        kind="audio_review",
        meeting_id=meeting_id,
        dedupe_key=f"audio_review:{meeting_id}",
        priority=40,
    )
    return {"job_id": job.id, "status": job.status}


@router.get("/meetings/{meeting_id}/reviews", response_model=list[TranscriptReviewOut])
def list_reviews(
    meeting_id: int,
    status_filter: str = Query(default="pending", alias="status"),
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    from ....models import TranscriptReview

    _owned(meeting_id, user, db)
    stmt = select(TranscriptReview).where(TranscriptReview.meeting_id == meeting_id)
    if status_filter:
        stmt = stmt.where(TranscriptReview.status == status_filter)
    return db.exec(stmt.order_by(TranscriptReview.start_ms)).all()


def _review_action(review_id: int, action: str, user: User, db: Session):
    from ....models import TranscriptReview

    review = db.get(TranscriptReview, review_id)
    if review is None:
        raise HTTPException(status_code=404, detail="复核建议不存在")
    _owned(review.meeting_id, user, db, write=True)
    if review.status != "pending":
        raise HTTPException(status_code=409, detail="该建议已处理")
    if action == "accept":
        seg = db.exec(
            select(Segment).where(
                Segment.meeting_id == review.meeting_id,
                Segment.segment_id == review.segment_id,
            )
        ).first()
        if seg is None:
            raise HTTPException(status_code=404, detail="原片段不存在")
        if not seg.original_text:
            seg.original_text = seg.text
        seg.text = review.suggested_text
        seg.edited_at = utcnow()
        db.add(seg)
        review.status = "accepted"
    else:
        review.status = "rejected"
    review.reviewed_at = utcnow()
    db.add(review)
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action=f"transcript_review_{action}",
            target=f"meeting:{review.meeting_id}/segment:{review.segment_id}",
        )
    )
    db.commit()
    db.refresh(review)
    return review


@router.post("/reviews/{review_id}/accept", response_model=TranscriptReviewOut)
def accept_review(
    review_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    return _review_action(review_id, "accept", user, db)


@router.post("/reviews/{review_id}/reject", response_model=TranscriptReviewOut)
def reject_review(
    review_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    return _review_action(review_id, "reject", user, db)


@router.patch("/meetings/{meeting_id}/metadata", response_model=MeetingOut)
def update_meeting_metadata(
    meeting_id: int,
    payload: MeetingMetadataUpdate,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    meeting = _owned(meeting_id, user, db, write=True)
    for field in (
        "title",
        "description",
        "participants",
        "agenda",
        "meeting_type",
        "scheduled_at",
    ):
        value = getattr(payload, field)
        if value is not None:
            setattr(meeting, field, value)
    # 只改标题不应阻断开场口语中的参会人/议程提取。标题本身是否被覆盖，
    # 由 meeting_meta 的「默认标题」判断保护；只有手动补充其它元信息时，
    # 才把整场标记为人工完成。
    if any(
        getattr(payload, field) is not None
        for field in ("description", "participants", "agenda", "meeting_type")
    ):
        meeting.metadata_captured = True
    db.add(meeting)
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="meeting_metadata_update",
            target=f"meeting:{meeting_id}",
        )
    )
    db.commit()
    db.refresh(meeting)
    return MeetingOut.model_validate(meeting)


@router.patch(
    "/meetings/{meeting_id}/segments/{segment_id}", response_model=SegmentOut
)
def edit_segment(
    meeting_id: int,
    segment_id: str,
    payload: SegmentUpdate,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    _owned(meeting_id, user, db, write=True)
    seg = db.exec(
        select(Segment).where(
            Segment.meeting_id == meeting_id, Segment.segment_id == segment_id
        )
    ).first()
    if seg is None:
        raise HTTPException(status_code=404, detail="片段不存在")
    if not seg.original_text:
        seg.original_text = seg.text
    if payload.text is not None:
        seg.text = payload.text
    if payload.speaker is not None:
        seg.speaker = payload.speaker or None
    seg.edited_at = utcnow()
    db.add(seg)
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="segment_edit",
            target=f"meeting:{meeting_id}/segment:{segment_id}",
        )
    )
    db.commit()
    db.refresh(seg)
    return SegmentOut.model_validate(seg)


@router.get("/meetings/{meeting_id}/minutes/versions", response_model=list[MinutesOut])
def list_minutes_versions(
    meeting_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    _owned(meeting_id, user, db)
    rows = db.exec(
        select(Minutes)
        .where(Minutes.meeting_id == meeting_id)
        .order_by(Minutes.version.desc())
    ).all()
    return [_minutes_out(row) for row in rows]


@router.patch("/meetings/{meeting_id}/minutes/{version}", response_model=MinutesOut)
def edit_minutes(
    meeting_id: int,
    version: int,
    payload: MinutesEditRequest,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    _owned(meeting_id, user, db, write=True)
    source = db.exec(
        select(Minutes).where(
            Minutes.meeting_id == meeting_id, Minutes.version == version
        )
    ).first()
    if source is None:
        raise HTTPException(status_code=404, detail="纪要版本不存在")
    latest = db.exec(
        select(func.max(Minutes.version)).where(Minutes.meeting_id == meeting_id)
    ).one()
    row = Minutes(
        meeting_id=meeting_id,
        summary=payload.summary
        if payload.summary is not None
        else source.summary,
        topics=json.dumps(
            payload.topics if payload.topics is not None else safe_json_list(source.topics),
            ensure_ascii=False,
        ),
        todos=json.dumps(
            payload.todos if payload.todos is not None else safe_json_list(source.todos),
            ensure_ascii=False,
        ),
        raw_markdown=source.raw_markdown,
        model_used=source.model_used,
        status="ready",
        version=int(latest or 0) + 1,
        is_draft=False,
        edited=True,
        transcript_end_ms=source.transcript_end_ms,
    )
    db.add(row)
    # 人工编辑的纪要同样要驱动待办闭环，否则页面上的待办状态永远停留在
    # 上一版自动终稿，用户改完纪要后会看到内容与待办不一致。
    from ....llm.progressive import _sync_action_items

    _sync_action_items(db, meeting_id, row)
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="minutes_edit",
            target=f"meeting:{meeting_id}/minutes:{version}",
        )
    )
    db.commit()
    db.refresh(row)
    return _minutes_out(row)


def _share_out(row: ShareLink, plaintext: str | None = None) -> ShareOut:
    return ShareOut(
        id=row.id,
        token=plaintext or "",
        token_hint=row.token_hint,
        meeting_id=row.meeting_id,
        expires_at=row.expires_at,
        allow_download=row.allow_download,
        has_password=bool(row.password_hash),
        url=f"/share/{plaintext}" if plaintext else "",
    )


@router.post("/meetings/{meeting_id}/shares", response_model=ShareOut)
def create_share(
    meeting_id: int,
    payload: ShareCreateRequest,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    _owned(meeting_id, user, db, write=True)
    plaintext, digest, hint = new_share_token()
    row = ShareLink(
        token=digest,
        token_hint=hint,
        meeting_id=meeting_id,
        password_hash=hash_password(payload.password) if payload.password else None,
        expires_at=utcnow() + timedelta(hours=payload.expires_hours),
        allow_download=payload.allow_download,
        created_by=user.id,
    )
    db.add(row)
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="share_create",
            target=f"meeting:{meeting_id}",
        )
    )
    db.commit()
    db.refresh(row)
    return _share_out(row, plaintext)


@router.get("/meetings/{meeting_id}/shares", response_model=list[ShareOut])
def list_shares(
    meeting_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    _owned(meeting_id, user, db)
    rows = db.exec(
        select(ShareLink).where(ShareLink.meeting_id == meeting_id)
    ).all()
    return [_share_out(row) for row in rows]


@router.delete("/meetings/{meeting_id}/shares/{share_ref}", status_code=204)
def revoke_share(
    meeting_id: int,
    share_ref: str,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    _owned(meeting_id, user, db, write=True)
    query = select(ShareLink).where(ShareLink.meeting_id == meeting_id)
    if share_ref.isdigit():
        query = query.where(ShareLink.id == int(share_ref))
    else:
        # Backward compatibility for clients that still hold a plaintext token.
        query = query.where(ShareLink.token == hash_share_token(share_ref))
    row = db.exec(query).first()
    if row is None:
        raise HTTPException(status_code=404, detail="分享链接不存在")
    db.delete(row)
    db.commit()
    return None


def _share_meeting(db: Session, token: str) -> ShareLink:
    row = db.exec(
        select(ShareLink).where(ShareLink.token == hash_share_token(token))
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="分享链接不存在")
    if row.expires_at < utcnow():
        raise HTTPException(status_code=410, detail="分享链接已过期")
    return row


def _authorize_share(db: Session, token: str, authorization: str | None) -> ShareLink:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="请先验证分享访问权限")
    payload = decode_access_token(authorization.split(" ", 1)[1].strip())
    token_digest = hash_share_token(token)
    if (
        not payload
        or payload.get("scope") != "share"
        or not (
            payload.get("share_token_hash") == token_digest
            # Tokens issued before hashed-token support expire within two hours.
            or payload.get("share_token") == token
        )
    ):
        raise HTTPException(status_code=401, detail="分享访问凭证无效或已过期")
    row = _share_meeting(db, token)
    return row


def _share_audit(
    db: Session,
    row: ShareLink,
    action: str,
    request: Request,
) -> None:
    from ....models import utcnow

    db.add(
        AuditLog(
            username=f"share:{row.token_hint or row.token[:8]}",
            action=action,
            target=f"meeting:{row.meeting_id}",
            ip=request.client.host if request.client else None,
        )
    )
    row.access_count += 1
    row.last_accessed_at = utcnow()
    db.add(row)


@router.post("/share/{token}/open", response_model=ShareOpenOut)
def open_share(
    token: str,
    payload: ShareOpenRequest,
    request: Request,
    db: Session = Depends(get_session),
):
    ip = request.client.host if request.client else "unknown"
    throttle_key = hash_share_token(token)
    allowed, retry = check_allowed(throttle_key, ip)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail=f"口令尝试过于频繁，请 {retry} 秒后再试",
            headers={"Retry-After": str(retry)},
        )
    row = _share_meeting(db, token)
    if row.password_hash and not verify_password(payload.password, row.password_hash):
        record_failure(throttle_key, ip)
        from ....models import utcnow as _now

        db.add(
            AuditLog(
                username=f"share:{row.token_hint or row.token[:8]}",
                action="share_auth_failed",
                target=f"meeting:{row.meeting_id}",
                ip=ip,
                payload=json.dumps({"at": _now().isoformat()}),
            )
        )
        db.commit()
        raise HTTPException(status_code=401, detail="访问口令错误")
    clear_failures(throttle_key, ip)
    _share_audit(db, row, "share_open", request)
    db.commit()
    access = create_access_token(
        f"share:{row.id}",
        "share",
        {
            "scope": "share",
            "share_token_hash": hash_share_token(token),
            "meeting_id": row.meeting_id,
        },
    )
    return ShareOpenOut(access_token=access, expires_in=7200)


@router.get("/share/{token}")
def view_share(
    token: str,
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_session),
):
    row = _authorize_share(db, token, authorization)
    meeting = db.get(Meeting, row.meeting_id)
    if meeting is None or meeting.deleted_at is not None:
        raise HTTPException(status_code=404, detail="会议不存在")
    segments = db.exec(
        select(Segment)
        .where(Segment.meeting_id == meeting.id)
        .order_by(Segment.start_ms, Segment.seq, Segment.id)
    ).all()
    minutes = db.exec(
        select(Minutes)
        .where(Minutes.meeting_id == meeting.id, Minutes.status == "ready")
        .order_by(Minutes.version.desc())
    ).first()
    _share_audit(db, row, "share_view", request)
    db.commit()
    return {
        "meeting": MeetingOut.model_validate(meeting),
        "segments": [SegmentOut.model_validate(s) for s in segments],
        "minutes": _minutes_out(minutes) if minutes else None,
        "allow_download": row.allow_download,
    }


@router.get("/share/{token}/audio")
def share_audio(
    token: str,
    request: Request,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_session),
):
    row = _authorize_share(db, token, authorization)
    if not row.allow_download:
        raise HTTPException(status_code=403, detail="该分享未开启音频下载")
    meeting = db.get(Meeting, row.meeting_id)
    if meeting is None or not meeting.audio_path:
        raise HTTPException(status_code=404, detail="没有留存音频")
    path = Path(meeting.audio_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="音频文件不存在")
    _share_audit(db, row, "share_audio_download", request)
    db.commit()
    return FileResponse(path, media_type="audio/wav", filename=f"meeting-{meeting.id}.wav")


@router.get("/meetings/{meeting_id}/actions", response_model=list[ActionItemOut])
def meeting_actions(
    meeting_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    _owned(meeting_id, user, db)
    rows = db.exec(
        select(ActionItem)
        .where(ActionItem.meeting_id == meeting_id)
        .order_by(ActionItem.status, ActionItem.id)
    ).all()
    return rows


@router.get("/actions", response_model=list[ActionItemOut])
def all_actions(
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=1000),
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    stmt = (
        select(ActionItem)
        .join(Meeting, Meeting.id == ActionItem.meeting_id)
        .where(Meeting.deleted_at.is_(None))
    )
    if user.role != "admin":
        stmt = stmt.where(Meeting.owner_id == user.id)
    if status_filter:
        stmt = stmt.where(ActionItem.status == status_filter)
    rows = db.exec(stmt.order_by(ActionItem.updated_at.desc()).limit(limit)).all()
    return rows


@router.patch("/actions/{action_id}", response_model=ActionItemOut)
def update_action(
    action_id: int,
    payload: ActionItemUpdate,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    row = db.get(ActionItem, action_id)
    if row is None:
        raise HTTPException(status_code=404, detail="待办不存在")
    _owned(row.meeting_id, user, db, write=True)
    if payload.task is not None:
        row.task = payload.task[:500]
    if payload.owner is not None:
        row.owner = payload.owner[:64]
    if payload.due is not None:
        row.due = payload.due[:64]
    if payload.status is not None:
        if payload.status not in ("open", "doing", "done"):
            raise HTTPException(status_code=400, detail="状态必须是 open/doing/done")
        row.status = payload.status
    row.updated_at = utcnow()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _parse_ics_datetime(raw: str) -> datetime | None:
    value = (raw or "").strip()
    # 模型的日期列要求带时区。ICS 的 Z 结尾是 UTC；不带时区的本地日历
    # 时间按服务器本地时区解释，避免导入时直接触发 UTCDateTime 校验错误。
    if value.endswith("Z"):
        try:
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None
    local_tz = datetime.now().astimezone().tzinfo or timezone.utc
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=local_tz)
        except ValueError:
            continue
    return None


def _parse_ics(text: str) -> list[dict]:
    lines: list[str] = []
    for line in text.splitlines():
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    events: list[dict] = []
    current: dict | None = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {"attendees": []}
        elif line == "END:VEVENT" and current is not None:
            events.append(current)
            current = None
        elif current is not None and ":" in line:
            key, _, value = line.partition(":")
            name = key.split(";", 1)[0]
            if name == "SUMMARY":
                current["summary"] = value
            elif name == "DTSTART":
                current["start"] = _parse_ics_datetime(value)
            elif name == "DESCRIPTION":
                current["description"] = value
            elif name == "ATTENDEE":
                match = re.search(r"CN=([^;:]+)", key + ":" + value)
                if match:
                    current["attendees"].append(match.group(1))
    return events


@router.post("/meetings/import-ics")
async def import_ics(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    limit = int(get_config("upload.max_ics_bytes") or 2 * 1024 * 1024)
    raw = await file.read(limit + 1)
    if len(raw) > limit:
        raise HTTPException(status_code=413, detail="日历文件超过大小上限")
    text = raw.decode("utf-8-sig", errors="replace")
    events = _parse_ics(text)
    imported = []
    skipped = 0
    for event in events:
        summary = str(event.get("summary") or "").strip()
        start = event.get("start")
        if not summary or start is None:
            skipped += 1
            continue
        existing = db.exec(
            select(Meeting).where(
                Meeting.owner_id == user.id,
                Meeting.title == summary[:200],
                Meeting.scheduled_at == start,
                Meeting.deleted_at.is_(None),
            )
        ).first()
        if existing is not None:
            skipped += 1
            continue
        meeting = Meeting(
            title=summary[:200],
            owner_id=user.id,
            status="scheduled",
            scheduled_at=start,
            participants=", ".join(event.get("attendees") or [])[:1000],
            agenda=str(event.get("description") or "")[:2000],
            meeting_type="日历导入",
        )
        db.add(meeting)
        imported.append(meeting)
    db.commit()
    for meeting in imported:
        db.refresh(meeting)
    return {
        "imported": len(imported),
        "skipped": skipped,
        "meetings": [MeetingOut.model_validate(m) for m in imported],
    }


@router.get("/meetings/scheduled", response_model=list[MeetingOut])
def scheduled_meetings(
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    conditions = [
        Meeting.status == "scheduled",
        Meeting.deleted_at.is_(None),
    ]
    if user.role != "admin":
        conditions.append(Meeting.owner_id == user.id)
    rows = db.exec(
        select(Meeting)
        .where(*conditions)
        .order_by(Meeting.scheduled_at)
        .limit(50)
    ).all()
    return [MeetingOut.model_validate(m) for m in rows]


@router.get("/admin/dashboard")
def admin_dashboard(
    user: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    active = db.exec(
        select(func.count()).select_from(Meeting).where(Meeting.deleted_at.is_(None))
    ).one()
    archived = db.exec(
        select(func.count())
        .select_from(Meeting)
        .where(Meeting.deleted_at.is_(None), Meeting.archived == True)  # noqa: E712
    ).one()
    trash = db.exec(
        select(func.count())
        .select_from(Meeting)
        .where(Meeting.deleted_at.is_not(None))
    ).one()
    duration = db.exec(
        select(func.coalesce(func.sum(Meeting.duration_ms), 0))
    ).one()
    segments = db.exec(select(func.count()).select_from(Segment)).one()
    open_actions = db.exec(
        select(func.count())
        .select_from(ActionItem)
        .where(ActionItem.status != "done")
    ).one()
    usage = db.exec(
        select(
            func.count(),
            func.coalesce(func.sum(LLMUsage.prompt_chars), 0),
            func.coalesce(func.sum(LLMUsage.response_chars), 0),
            func.coalesce(func.avg(LLMUsage.elapsed_ms), 0),
            func.coalesce(func.sum(LLMUsage.prompt_tokens), 0),
            func.coalesce(func.sum(LLMUsage.completion_tokens), 0),
            func.coalesce(func.sum(LLMUsage.cost_usd), 0.0),
        )
    ).one()
    from ....application.jobs import job_stats
    from ....maintenance import drain_status

    metrics_dir = BASE_DIR / "data" / "metrics"
    latencies = []
    noise_warnings = 0
    for path in sorted(metrics_dir.glob("meeting-*.json"))[-500:]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            latency = data.get("final_latency_ms") or {}
            if latency.get("p95"):
                latencies.append(float(latency["p95"]))
            noise_warnings += int(data.get("noise_warnings") or 0)
        except Exception:
            continue

    return {
        "meetings": int(active),
        "archived": int(archived),
        "trash": int(trash),
        "duration_minutes": round(int(duration) / 60000, 1),
        "segments": int(segments),
        "open_actions": int(open_actions),
        "llm_calls": int(usage[0]),
        "llm_prompt_chars": int(usage[1]),
        "llm_response_chars": int(usage[2]),
        "llm_avg_ms": round(float(usage[3]), 1),
        "llm_prompt_tokens": int(usage[4]),
        "llm_completion_tokens": int(usage[5]),
        "llm_cost_usd": round(float(usage[6]), 6),
        "jobs": job_stats(),
        "maintenance": drain_status(),
        "latency_p95_median": round(
            sorted(latencies)[len(latencies) // 2], 1
        )
        if latencies
        else None,
        "noise_warnings": noise_warnings,
    }


@router.get("/admin/audit")
def admin_audit(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    action: str | None = None,
    user: User = Depends(require_admin),
    db: Session = Depends(get_session),
):
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    total = int(
        db.exec(select(func.count()).select_from(stmt.subquery())).one()
    )
    rows = db.exec(
        stmt.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return {
        "total": total,
        "items": [
            {
                "id": row.id,
                "username": row.username,
                "action": row.action,
                "target": row.target,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ],
    }


@router.get("/knowledge/search", response_model=MeetingSearchResult)
async def knowledge_search(
    q: str = Query(default="", max_length=100),
    tag: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    conditions = [Meeting.deleted_at.is_(None), Meeting.archived == False]  # noqa: E712
    if user.role != "admin":
        conditions.append(Meeting.owner_id == user.id)
    if tag:
        conditions.append(Meeting.tags.like(f"%{tag.strip()}%"))
    matches: list[SearchMatchOut] = []
    if q.strip():
        like = f"%{q.strip()}%"
        from ....search_index import semantic_matches

        semantic = await asyncio.to_thread(semantic_matches, q)
        fts_ids = (
            sorted({item["meeting_id"] for item in semantic})
            if semantic
            else (_fts_meeting_ids(db, q) or [])
        )
        if semantic:
            matches = [SearchMatchOut(**item) for item in semantic]
        try:
            if semantic:
                rows = []
            elif len(q.strip()) >= 3 and engine_is_sqlite(db):
                rows = db.execute(
                    text(
                        "SELECT s.meeting_id, s.segment_id, s.start_ms, "
                        "snippet(segment_fts, 0, '', '', '…', 18) AS snip, "
                        "bm25(segment_fts) AS rank "
                        "FROM segment_fts JOIN segment s ON s.id = segment_fts.rowid "
                        "WHERE segment_fts MATCH :q ORDER BY rank LIMIT 300"
                    ),
                    {"q": '"' + q.strip().replace('"', '""') + '"'},
                ).fetchall()
            else:
                rows = db.execute(
                    text(
                        "SELECT meeting_id, segment_id, start_ms, text, 0 "
                        "FROM segment WHERE text LIKE :q LIMIT 300"
                    ),
                    {"q": like},
                ).fetchall()
            for row in rows:
                if row[0] in fts_ids or len(q.strip()) < 3:
                    matches.append(
                        SearchMatchOut(
                            meeting_id=int(row[0]),
                            segment_id=str(row[1]),
                            start_ms=int(row[2] or 0),
                            text=str(row[3] or "")[:500],
                            score=float(row[4] or 0),
                        )
                    )
        except Exception:
            logger.exception("知识库命中片段查询失败")
        conditions.append(
            or_(
                Meeting.title.like(like),
                Meeting.participants.like(like),
                Meeting.agenda.like(like),
                Meeting.description.like(like),
                Meeting.id.in_(fts_ids),
                exists(
                    select(Minutes.id).where(
                        Minutes.meeting_id == Meeting.id,
                        or_(
                            Minutes.summary.like(like),
                            Minutes.topics.like(like),
                        ),
                    )
                ),
            )
        )
    stmt = select(Meeting).where(*conditions)
    total = int(
        db.exec(select(func.count()).select_from(stmt.subquery())).one()
    )
    rows = db.exec(
        stmt.order_by(Meeting.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return MeetingSearchResult(
        items=[MeetingOut.model_validate(m) for m in rows],
        total=total,
        matches=matches,
    )


@router.get("/knowledge/topics")
def knowledge_topics(
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    stmt = (
        select(Minutes, Meeting)
        .join(Meeting, Meeting.id == Minutes.meeting_id)
        .where(
            Meeting.deleted_at.is_(None),
            Minutes.status == "ready",
            Minutes.is_draft == False,  # noqa: E712
        )
    )
    if user.role != "admin":
        stmt = stmt.where(Meeting.owner_id == user.id)
    rows = db.exec(stmt.order_by(Minutes.created_at.desc()).limit(1000)).all()
    counts: dict[str, dict] = {}
    for minutes, meeting in rows:
        for topic in safe_json_list(minutes.topics):
            title = str(topic.get("title") or "").strip()
            if not title:
                continue
            title = _canonical_topic(title)
            entry = counts.setdefault(
                title, {"topic": title, "count": 0, "meetings": []}
            )
            entry["count"] += 1
            if len(entry["meetings"]) < 5:
                entry["meetings"].append(
                    {"id": meeting.id, "title": meeting.title}
                )
    ordered = sorted(
        counts.values(), key=lambda item: item["count"], reverse=True
    )[:limit]
    return ordered


@router.post("/meetings/{meeting_id}/redact")
def redact_meeting(
    meeting_id: int,
    payload: dict | None = None,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """对文本、纪要和待办执行可重复执行的敏感信息脱敏。"""
    from ....privacy import redact_json, redact_text

    meeting = _owned(meeting_id, user, db, write=True)
    dry_run = bool((payload or {}).get("dry_run") or False)
    segments = db.exec(select(Segment).where(Segment.meeting_id == meeting_id)).all()
    changed = 0
    for segment in segments:
        text, count = redact_text(segment.text or "")
        changed += count
        if count and not dry_run:
            if not segment.original_text:
                segment.original_text = segment.text
            segment.text = text
            segment.edited_at = utcnow()
            db.add(segment)
    minutes_rows = db.exec(
        select(Minutes).where(Minutes.meeting_id == meeting_id)
    ).all()
    for row in minutes_rows:
        summary, c1 = redact_text(row.summary or "")
        topics, c2 = redact_json(row.topics or "[]")
        todos, c3 = redact_json(row.todos or "[]")
        raw, c4 = redact_text(row.raw_markdown or "")
        changed += c1 + c2 + c3 + c4
        if (c1 + c2 + c3 + c4) and not dry_run:
            row.summary = summary
            row.topics = topics
            row.todos = todos
            row.raw_markdown = raw
            db.add(row)
    if not dry_run and changed:
        meeting.redacted_at = utcnow()
        db.add(
            AuditLog(
                user_id=user.id,
                username=user.username,
                action="meeting_redact",
                target=f"meeting:{meeting_id}",
                payload=json.dumps({"replacements": changed}),
            )
        )
        db.add(meeting)
        db.commit()
    return {"dry_run": dry_run, "replacements": changed}


@router.patch("/meetings/{meeting_id}/retention")
def update_retention(
    meeting_id: int,
    payload: dict,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    meeting = _owned(meeting_id, user, db, write=True)
    if "retention_days" in payload:
        meeting.retention_days = max(0, min(3650, int(payload["retention_days"])))
    if "legal_hold" in payload:
        meeting.legal_hold = bool(payload["legal_hold"])
    db.add(meeting)
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="meeting_retention_update",
            target=f"meeting:{meeting_id}",
            payload=json.dumps(
                {
                    "retention_days": meeting.retention_days,
                    "legal_hold": meeting.legal_hold,
                }
            ),
        )
    )
    db.commit()
    return {
        "retention_days": meeting.retention_days,
        "legal_hold": meeting.legal_hold,
    }


def engine_is_sqlite(db: Session) -> bool:
    try:
        return db.get_bind().dialect.name == "sqlite"
    except Exception:
        return False


def _canonical_topic(title: str) -> str:
    """用词表别名和企业术语归一主题，避免同一议题被拆成多个节点。"""
    from ....glossary import load_terms

    text = re.sub(r"[\s，。；：、,.!?！？｜|/]+", "", title).strip()
    for term in load_terms():
        aliases = [
            item.strip()
            for item in (term.aliases or "").replace("，", ",").split(",")
            if item.strip()
        ]
        if term.term == title or any(alias in text for alias in aliases):
            return term.term
    return text[:80] or title[:80]
