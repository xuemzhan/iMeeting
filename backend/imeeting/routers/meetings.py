"""会议路由：列表、详情、重命名、删除、纪要、导出。"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy import delete as sa_delete
from sqlalchemy import exists, or_, text
from sqlmodel import Session, func, select

from ..db import get_session
from ..export import formats
from ..export.document_export import build_docx, build_pdf
from ..llm.client import LLMError
from ..llm.minutes import TranscriptLine, generate_minutes
from ..meeting_files import cleanup_meeting_files
from ..models import (
    ActionItem,
    AuditLog,
    Meeting,
    MeetingMember,
    MeetingSession,
    Minutes,
    MinutesTemplate,
    Segment,
    SegmentEmbedding,
    ShareLink,
    User,
    Voiceprint,
    utcnow,
)
from ..schemas import (
    MeetingBulkDeleteRequest,
    MeetingBulkDeleteResult,
    MeetingBulkExportRequest,
    MeetingBulkRenameRequest,
    MeetingBulkResult,
    MeetingBulkUpdateRequest,
    MeetingDetail,
    MeetingOut,
    MeetingSearchResult,
    MeetingUpdate,
    MinutesGenerateRequest,
    MinutesOut,
    SegmentOut,
)
from ..settings import get_config
from .deps import get_current_user_or_anonymous

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/meetings", tags=["meetings"])


def _owned(
    meeting_id: int,
    user: User,
    db: Session,
    *,
    include_deleted: bool = False,
    write: bool = False,
) -> Meeting:
    """校验会议存在且当前用户有权访问。"""
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


def _meeting_filters(
    user: User,
    q: str = "",
    status_value: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    archived: str | None = None,
    tag: str | None = None,
    include_deleted: bool = False,
    fts_ids: list[int] | None = None,
) -> list:
    """搜索条件：标题 + 转写正文 + 状态 + 日期范围。"""
    conditions = []
    if not include_deleted:
        conditions.append(Meeting.deleted_at.is_(None))
    if user.role != "admin":
        conditions.append(Meeting.owner_id == user.id)
    if status_value:
        conditions.append(Meeting.status == status_value)
    if archived in ("true", "false"):
        conditions.append(Meeting.archived == (archived == "true"))
    if tag:
        conditions.append(Meeting.tags.like(f"%{tag.strip()}%"))
    if q.strip():
        like = f"%{q.strip()}%"
        if fts_ids:
            conditions.append(
                or_(
                    Meeting.title.like(like),
                    Meeting.id.in_(fts_ids),
                    exists(
                        select(Minutes.id).where(
                            Minutes.meeting_id == Meeting.id,
                            Minutes.summary.like(like),
                        )
                    ),
                )
            )
        else:
            conditions.append(
                or_(
                    Meeting.title.like(like),
                    exists(
                        select(Segment.id).where(
                            Segment.meeting_id == Meeting.id,
                            Segment.text.like(like),
                        )
                    ),
                    exists(
                        select(Minutes.id).where(
                            Minutes.meeting_id == Meeting.id,
                            Minutes.summary.like(like),
                        )
                    ),
                )
            )
    if date_from:
        try:
            conditions.append(Meeting.created_at >= datetime.fromisoformat(date_from))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date_from 格式应为 YYYY-MM-DD") from exc
    if date_to:
        try:
            conditions.append(
                Meeting.created_at
                < datetime.fromisoformat(date_to) + timedelta(days=1)
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date_to 格式应为 YYYY-MM-DD") from exc
    return conditions


def _fts_meeting_ids(db: Session, q: str) -> list[int] | None:
    """FTS5 trigram 检索；短查询或 FTS 不可用时返回 None（回退 LIKE）。"""
    term = q.strip()
    if len(term) < 3:
        return None
    try:
        phrase = '"' + term.replace('"', '""') + '"'
        # SQLModel 的 Session.exec() 只接收语句对象，不能像 SQLAlchemy
        # Session.execute() 那样把参数字典作为第二个位置参数。之前这里用错后
        # 每次三字以上检索都会退回 LIKE，FTS5 索引实际上没有生效。
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


@router.get("", response_model=list[MeetingOut])
def list_meetings(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    stmt = (
        select(Meeting)
        .where(Meeting.deleted_at.is_(None))
        .order_by(Meeting.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    if user.role != "admin":
        stmt = stmt.where(Meeting.owner_id == user.id)
    return [MeetingOut.model_validate(m) for m in db.exec(stmt).all()]


@router.get("/search", response_model=MeetingSearchResult)
def search_meetings(
    q: str = Query(default="", max_length=100),
    status_value: str | None = Query(default=None, alias="status"),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    archived: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    fts_ids = _fts_meeting_ids(db, q) if q.strip() else None
    conditions = _meeting_filters(
        user,
        q,
        status_value,
        date_from,
        date_to,
        archived=archived,
        tag=tag,
        fts_ids=fts_ids,
    )
    stmt = select(Meeting).where(*conditions)
    total = int(
        db.exec(select(func.count()).select_from(stmt.subquery())).one()
    )
    rows = db.exec(
        stmt.order_by(Meeting.created_at.desc()).offset(offset).limit(limit)
    ).all()
    return MeetingSearchResult(
        items=[MeetingOut.model_validate(m) for m in rows], total=total
    )


def _delete_meeting_rows(db: Session, meeting: Meeting) -> None:
    mid = meeting.id
    db.execute(sa_delete(Segment).where(Segment.meeting_id == mid))
    db.execute(sa_delete(Minutes).where(Minutes.meeting_id == mid))
    db.execute(sa_delete(MeetingSession).where(MeetingSession.meeting_id == mid))
    db.execute(sa_delete(ActionItem).where(ActionItem.meeting_id == mid))
    db.execute(sa_delete(ShareLink).where(ShareLink.meeting_id == mid))
    db.execute(sa_delete(SegmentEmbedding).where(SegmentEmbedding.meeting_id == mid))
    # 只删除本场自动发现的临时声纹；人工命名的正式身份保留给其它会议使用
    db.execute(
        sa_delete(Voiceprint).where(
            Voiceprint.meeting_id == mid, Voiceprint.provisional == True  # noqa: E712
        )
    )
    db.delete(meeting)


def _is_actively_recording(db: Session, meeting_id: int) -> bool:
    """只有存在进行中的会话记录时才阻止删除。

    历史上崩溃的会议会停在 ``recording`` 但没有会话行；这类僵尸会议
    必须允许用户清理。
    """
    rows = db.exec(
        select(MeetingSession).where(
            MeetingSession.meeting_id == meeting_id,
            MeetingSession.status == "recording",
        )
    ).all()
    if not rows:
        return False
    try:
        from ..session_registry import session_registry

        if any(session_registry.contains(row.session_id) for row in rows):
            return True
    except Exception:
        pass
    # 进程崩溃后遗留的 recording 行不能永久阻塞删除；仅保留一个短窗口，
    # 防止多进程部署时另一实例仍在收尾。
    recent = utcnow() - timedelta(minutes=10)
    return any(
        row.started_at is not None and row.started_at >= recent for row in rows
    )


@router.post("/bulk-delete", response_model=MeetingBulkDeleteResult)
def bulk_delete_meetings(
    payload: MeetingBulkDeleteRequest,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """批量移入回收站（30 天内可恢复）。"""
    ids = list(dict.fromkeys(payload.ids))
    deleted_ids: list[int] = []
    skipped: list[int] = []

    for mid in ids:
        meeting = db.get(Meeting, mid)
        if meeting is None or (
            meeting.owner_id != user.id and user.role != "admin"
        ):
            skipped.append(mid)
            continue
        if meeting.deleted_at is not None:
            skipped.append(mid)
            continue
        if _is_actively_recording(db, mid):
            skipped.append(mid)
            continue
        meeting.deleted_at = utcnow()
        db.add(meeting)
        deleted_ids.append(mid)

    if deleted_ids:
        db.add(
            AuditLog(
                user_id=user.id,
                username=user.username,
                action="meeting_bulk_trash",
                target=f"meetings:{','.join(map(str, deleted_ids))}"[:200],
                payload=json.dumps(
                    {"deleted": deleted_ids, "skipped": skipped},
                    ensure_ascii=False,
                ),
            )
        )
    db.commit()
    return MeetingBulkDeleteResult(
        deleted=len(deleted_ids), deleted_ids=deleted_ids, skipped=skipped
    )


@router.get("/trash", response_model=MeetingSearchResult)
def list_trash(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    conditions = [Meeting.deleted_at.is_not(None)]
    if user.role != "admin":
        conditions.append(Meeting.owner_id == user.id)
    stmt = select(Meeting).where(*conditions)
    total = int(
        db.exec(select(func.count()).select_from(stmt.subquery())).one()
    )
    rows = db.exec(
        stmt.order_by(Meeting.deleted_at.desc()).offset(offset).limit(limit)
    ).all()
    return MeetingSearchResult(
        items=[MeetingOut.model_validate(m) for m in rows], total=total
    )


@router.post("/bulk-restore", response_model=MeetingBulkResult)
def bulk_restore(
    payload: MeetingBulkDeleteRequest,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    ids = list(dict.fromkeys(payload.ids))
    restored: list[int] = []
    skipped: list[int] = []
    for mid in ids:
        meeting = db.get(Meeting, mid)
        if (
            meeting is None
            or (meeting.owner_id != user.id and user.role != "admin")
            or meeting.deleted_at is None
        ):
            skipped.append(mid)
            continue
        meeting.deleted_at = None
        db.add(meeting)
        restored.append(mid)
    if restored:
        db.add(
            AuditLog(
                user_id=user.id,
                username=user.username,
                action="meeting_bulk_restore",
                target=f"meetings:{','.join(map(str, restored))}"[:200],
            )
        )
    db.commit()
    return MeetingBulkResult(updated=len(restored), ids=restored, skipped=skipped)


@router.post("/{meeting_id}/restore", response_model=MeetingOut)
def restore_meeting(
    meeting_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    meeting = _owned(meeting_id, user, db, include_deleted=True, write=True)
    if meeting.deleted_at is None:
        raise HTTPException(status_code=400, detail="该会议不在回收站")
    meeting.deleted_at = None
    db.add(meeting)
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="meeting_restore",
            target=f"meeting:{meeting_id}",
        )
    )
    db.commit()
    db.refresh(meeting)
    return MeetingOut.model_validate(meeting)


@router.post("/bulk-purge", response_model=MeetingBulkDeleteResult)
def bulk_purge(
    payload: MeetingBulkDeleteRequest,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """从回收站彻底删除（不可恢复），并清理音频/指标。"""
    ids = list(dict.fromkeys(payload.ids))
    purged: list[int] = []
    skipped: list[int] = []
    audio_paths: dict[int, str | None] = {}
    for mid in ids:
        meeting = db.get(Meeting, mid)
        if (
            meeting is None
            or (meeting.owner_id != user.id and user.role != "admin")
            or meeting.deleted_at is None
        ):
            skipped.append(mid)
            continue
        audio_paths[mid] = meeting.audio_path
        _delete_meeting_rows(db, meeting)
        purged.append(mid)
    if purged:
        db.add(
            AuditLog(
                user_id=user.id,
                username=user.username,
                action="meeting_bulk_purge",
                target=f"meetings:{','.join(map(str, purged))}"[:200],
            )
        )
    db.commit()
    for mid in purged:
        cleanup_meeting_files(mid, audio_paths.get(mid))
    return MeetingBulkDeleteResult(
        deleted=len(purged), deleted_ids=purged, skipped=skipped
    )


@router.post("/bulk-rename", response_model=MeetingBulkResult)
def bulk_rename(
    payload: MeetingBulkRenameRequest,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    ids = list(dict.fromkeys(payload.ids))
    updated: list[int] = []
    skipped: list[int] = []
    for mid in ids:
        meeting = db.get(Meeting, mid)
        if (
            meeting is None
            or (meeting.owner_id != user.id and user.role != "admin")
            or meeting.deleted_at is not None
        ):
            skipped.append(mid)
            continue
        title = meeting.title
        if payload.find:
            title = title.replace(payload.find, payload.replace)
        if payload.prefix:
            title = payload.prefix + title
        if payload.suffix:
            title = title + payload.suffix
        title = title.strip()[:200]
        if not title:
            skipped.append(mid)
            continue
        meeting.title = title
        db.add(meeting)
        updated.append(mid)
    if updated:
        db.add(
            AuditLog(
                user_id=user.id,
                username=user.username,
                action="meeting_bulk_rename",
                target=f"meetings:{','.join(map(str, updated))}"[:200],
            )
        )
    db.commit()
    return MeetingBulkResult(updated=len(updated), ids=updated, skipped=skipped)


@router.post("/bulk-update", response_model=MeetingBulkResult)
def bulk_update(
    payload: MeetingBulkUpdateRequest,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """批量归档 / 打标签。"""
    ids = list(dict.fromkeys(payload.ids))
    updated: list[int] = []
    skipped: list[int] = []
    for mid in ids:
        meeting = db.get(Meeting, mid)
        if (
            meeting is None
            or (meeting.owner_id != user.id and user.role != "admin")
            or meeting.deleted_at is not None
        ):
            skipped.append(mid)
            continue
        if payload.archived is not None:
            meeting.archived = payload.archived
        tags = [t.strip() for t in (meeting.tags or "").split(",") if t.strip()]
        for tag in payload.tags_add:
            if tag.strip() and tag.strip() not in tags:
                tags.append(tag.strip())
        remove = {t.strip() for t in payload.tags_remove if t.strip()}
        tags = [t for t in tags if t not in remove]
        meeting.tags = ",".join(tags)[:500]
        db.add(meeting)
        updated.append(mid)
    if updated:
        db.add(
            AuditLog(
                user_id=user.id,
                username=user.username,
                action="meeting_bulk_update",
                target=f"meetings:{','.join(map(str, updated))}"[:200],
            )
        )
    db.commit()
    return MeetingBulkResult(updated=len(updated), ids=updated, skipped=skipped)


@router.post("/bulk-export")
def bulk_export(
    payload: MeetingBulkExportRequest,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    allowed = get_config("export.formats") or ["json"]
    fmt = payload.format.lower()
    if fmt not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的导出格式 {fmt}，可用：{', '.join(allowed)}",
        )
    ext, _media_type = formats.EXPORTERS[fmt]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for mid in dict.fromkeys(payload.ids):
            meeting = db.get(Meeting, mid)
            if (
                meeting is None
                or (meeting.owner_id != user.id and user.role != "admin")
                or meeting.deleted_at is not None
            ):
                continue
            safe = "".join(
                c if c not in '\\/:*?"<>|' else "_" for c in meeting.title
            )[:60]
            if fmt in ("docx", "pdf"):
                segments = db.exec(
                    select(Segment)
                    .where(Segment.meeting_id == mid)
                    .order_by(Segment.start_ms, Segment.seq, Segment.id)
                ).all()
                minutes_row = db.exec(
                    select(Minutes)
                    .where(Minutes.meeting_id == mid, Minutes.status == "ready")
                    .order_by(Minutes.version.desc())
                ).first()
                builder = build_docx if fmt == "docx" else build_pdf
                body = builder(
                    meeting,
                    segments,
                    minutes_row,
                    company=str(get_config("export.company_name") or ""),
                    footer=str(get_config("export.footer_text") or ""),
                )
                archive.writestr(f"{mid}-{safe}.{ext}", body)
            else:
                archive.writestr(
                    f"{mid}-{safe}.{ext}", _export_body(db, meeting, fmt)
                )
    buffer.seek(0)
    return Response(
        content=buffer.read(),
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="meetings-export.zip"'
        },
    )


@router.get("/{meeting_id}", response_model=MeetingDetail)
def get_meeting(
    meeting_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    meeting = _owned(meeting_id, user, db)
    segments = db.exec(
        select(Segment)
        .where(Segment.meeting_id == meeting_id)
        .order_by(Segment.start_ms, Segment.seq, Segment.id)
    ).all()

    detail = MeetingDetail.model_validate(meeting)
    detail.segments = [SegmentOut.model_validate(s) for s in segments]
    detail.speaker_low_confidence_threshold = float(
        get_config("spk.low_confidence_threshold") or 0.62
    )
    return detail


@router.patch("/{meeting_id}", response_model=MeetingOut)
def rename_meeting(
    meeting_id: int,
    payload: MeetingUpdate,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    meeting = _owned(meeting_id, user, db, write=True)
    meeting.title = payload.title
    # 用户手动命名后，口语自动提取不再覆盖
    meeting.metadata_captured = True
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    return MeetingOut.model_validate(meeting)


@router.delete("/{meeting_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meeting(
    meeting_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    meeting = _owned(meeting_id, user, db, write=True)
    if _is_actively_recording(db, meeting_id):
        raise HTTPException(status_code=409, detail="会议正在录制，无法删除")
    meeting.deleted_at = utcnow()
    db.add(meeting)
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="meeting_trash",
            target=f"meeting:{meeting_id}",
        )
    )
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{meeting_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
def purge_meeting(
    meeting_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """从回收站彻底删除（不可恢复）。"""
    meeting = _owned(meeting_id, user, db, include_deleted=True, write=True)
    if meeting.deleted_at is None:
        raise HTTPException(status_code=400, detail="请先将会议移入回收站")
    audio_path = meeting.audio_path
    _delete_meeting_rows(db, meeting)
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="meeting_purge",
            target=f"meeting:{meeting_id}",
        )
    )
    db.commit()
    cleanup_meeting_files(meeting_id, audio_path)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{meeting_id}/audio")
def get_meeting_audio(
    meeting_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """回放会议留存音频。

    Starlette 的 FileResponse 会自动处理 HTTP Range，音频播放器因此
    可以拖动进度条；若手工返回整段字节流，浏览器就得下完才能跳转。
    """
    from pathlib import Path

    from fastapi.responses import FileResponse

    meeting = _owned(meeting_id, user, db)
    if not meeting.audio_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="该会议没有留存音频")

    path = Path(meeting.audio_path)
    if not path.exists():
        logger.warning("音频文件已丢失: %s", path)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="音频文件已不存在")

    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"meeting-{meeting_id}.wav",
    )


# ---------------------------------------------------------------------------
# 纪要
# ---------------------------------------------------------------------------


@router.get("/{meeting_id}/minutes", response_model=MinutesOut | None)
def get_minutes(
    meeting_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    _owned(meeting_id, user, db)
    row = db.exec(
        select(Minutes)
        .where(Minutes.meeting_id == meeting_id)
        .order_by(Minutes.version.desc(), Minutes.created_at.desc())
    ).first()
    return _minutes_out(row) if row else None


@router.get("/{meeting_id}/mindmap")
def get_mindmap(
    meeting_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """把最新一版纪要转换成思维导图树。"""
    meeting = _owned(meeting_id, user, db)
    row = db.exec(
        select(Minutes)
        .where(Minutes.meeting_id == meeting_id)
        .order_by(Minutes.version.desc(), Minutes.created_at.desc())
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="该会议还没有纪要")

    topics = _safe_json(row.topics)
    todos = _safe_json(row.todos)
    children: list[dict] = []
    for i, topic in enumerate(topics):
        node = {
            "id": f"topic-{i}",
            "label": str(topic.get("title") or f"议题 {i + 1}"),
            "children": [],
            "evidence": topic.get("evidence") or [],
        }
        if topic.get("detail"):
            node["children"].append(
                {"id": f"topic-{i}-detail", "label": str(topic["detail"])}
            )
        if topic.get("conclusion"):
            node["children"].append(
                {
                    "id": f"topic-{i}-conclusion",
                    "label": f"结论：{topic['conclusion']}",
                }
            )
        children.append(node)

    if todos:
        todo_node = {"id": "todos", "label": "待办事项", "children": []}
        for i, todo in enumerate(todos):
            meta = []
            if todo.get("owner"):
                meta.append(f"责任人：{todo['owner']}")
            if todo.get("due"):
                meta.append(f"截止：{todo['due']}")
            todo_node["children"].append(
                {
                    "id": f"todo-{i}",
                    "label": str(todo.get("task") or f"待办 {i + 1}"),
                    "meta": " · ".join(meta),
                    "evidence": todo.get("evidence") or [],
                }
            )
        children.append(todo_node)

    return {
        "meeting_id": meeting_id,
        "title": meeting.title,
        "version": row.version,
        "is_draft": row.is_draft,
        "root": {"id": "root", "label": meeting.title, "children": children},
    }


@router.post("/{meeting_id}/merge-speakers")
def merge_speakers(
    meeting_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """手动触发会后说话人合并（也用于修复历史会议）。"""
    _owned(meeting_id, user, db, write=True)
    from ..engine.speaker_merge import merge_meeting_speakers

    return merge_meeting_speakers(
        meeting_id,
        min_segments=int(get_config("spk.merge_min_segments") or 3),
        threshold=float(get_config("spk.merge_threshold") or 0.45),
    )


@router.post("/{meeting_id}/minutes", response_model=MinutesOut)
async def create_minutes(
    meeting_id: int,
    payload: MinutesGenerateRequest,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """生成会议纪要（终稿）。已有终稿且未指定 force 时直接返回缓存。

    会中自动生成的草稿（``is_draft=True``）不阻止终稿生成。
    """
    meeting = _owned(meeting_id, user, db, write=True)

    existing = db.exec(
        select(Minutes)
        .where(Minutes.meeting_id == meeting_id)
        .order_by(Minutes.version.desc(), Minutes.created_at.desc())
    ).first()
    if (
        existing is not None
        and not payload.force
        and existing.status == "ready"
        and not existing.is_draft
    ):
        return _minutes_out(existing)

    segments = db.exec(
        select(Segment)
        .where(Segment.meeting_id == meeting_id)
        .order_by(Segment.start_ms, Segment.seq, Segment.id)
    ).all()

    if not segments:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="该会议还没有转写内容")

    lines = [
        TranscriptLine(
            start_ms=s.start_ms,
            speaker=s.speaker,
            text=s.text,
            end_ms=s.end_ms,
            segment_id=s.segment_id,
            segment_ids=[s.segment_id],
        )
        for s in segments
        if s.text.strip()
    ]

    template_text = None
    if payload.template_id is not None:
        template = db.get(MinutesTemplate, payload.template_id)
        if template is None:
            raise HTTPException(status_code=404, detail="纪要模板不存在")
        template_text = template.content

    try:
        result = await generate_minutes(
            lines,
            model_label=meeting.title,
            template=template_text,
            meeting_id=meeting_id,
        )
    except LLMError as exc:
        # 失败也落库，便于前端展示错误原因
        latest_version = db.exec(
            select(func.max(Minutes.version)).where(Minutes.meeting_id == meeting_id)
        ).one()
        row = Minutes(
            meeting_id=meeting_id,
            status="failed",
            error=str(exc),
            version=int(latest_version or 0) + 1,
            is_draft=False,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    # 版本化写入：保留会中草稿，终稿另起一版，便于对比
    from ..llm.progressive import save_generated_minutes

    end_ms = max((line.end_ms or (line.start_ms + 1000)) for line in lines)
    row = save_generated_minutes(
        meeting_id,
        result,
        is_draft=False,
        transcript_end_ms=end_ms,
    )
    return _minutes_out(row)


def _minutes_out(row: Minutes) -> MinutesOut:
    def _load(raw: str) -> list:
        try:
            parsed = json.loads(raw or "[]")
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []

    return MinutesOut(
        id=row.id or 0,
        meeting_id=row.meeting_id,
        summary=row.summary,
        topics=_load(row.topics),
        todos=_load(row.todos),
        raw_markdown=row.raw_markdown,
        model_used=row.model_used,
        status=row.status,
        error=row.error,
        version=row.version,
        is_draft=row.is_draft,
        edited=row.edited,
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------


def _export_body(db: Session, meeting: Meeting, fmt: str) -> str:
    segments = db.exec(
        select(Segment)
        .where(Segment.meeting_id == meeting.id)
        .order_by(Segment.start_ms, Segment.seq, Segment.id)
    ).all()
    seg_dicts = [
        {
            "start_ms": s.start_ms,
            "end_ms": s.end_ms,
            "text": s.text,
            "speaker": s.speaker,
        }
        for s in segments
    ]
    meeting_dict = {
        "id": meeting.id,
        "title": meeting.title,
        "duration_ms": meeting.duration_ms,
        "created_at": meeting.created_at.isoformat() if meeting.created_at else "",
    }
    if fmt == "srt":
        return formats.to_srt(seg_dicts)
    if fmt == "vtt":
        return formats.to_vtt(seg_dicts)
    if fmt == "txt":
        return formats.to_text(seg_dicts)

    minutes_row = db.exec(
        select(Minutes)
        .where(Minutes.meeting_id == meeting.id, Minutes.status == "ready")
        .order_by(Minutes.version.desc())
    ).first()
    minutes_dict = None
    if minutes_row is not None:
        minutes_dict = {
            "summary": minutes_row.summary,
            "topics": _safe_json(minutes_row.topics),
            "todos": _safe_json(minutes_row.todos),
            "model": minutes_row.model_used,
        }
    if fmt == "md":
        return formats.to_markdown(meeting_dict, seg_dicts, minutes_dict)
    return formats.to_json(meeting_dict, seg_dicts, minutes_dict)


@router.get("/{meeting_id}/export")
def export_meeting(
    meeting_id: int,
    format: str = Query(default="srt"),
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    meeting = _owned(meeting_id, user, db)

    allowed = get_config("export.formats") or ["srt"]
    fmt = format.lower()
    if fmt not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的导出格式 {fmt}，可用：{', '.join(allowed)}",
        )

    ext, media_type = formats.EXPORTERS[fmt]
    if fmt in ("docx", "pdf"):
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
        company = str(get_config("export.company_name") or "")
        footer = str(get_config("export.footer_text") or "")
        builder = build_docx if fmt == "docx" else build_pdf
        payload = builder(
            meeting,
            segments,
            minutes_row,
            company=company,
            footer=footer,
        )
        return Response(
            content=payload,
            media_type=media_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="meeting-{meeting_id}.{ext}"'
                )
            },
        )
    body = _export_body(db, meeting, fmt)

    filename = f"meeting-{meeting_id}.{ext}"
    return Response(
        content=body.encode("utf-8"),
        media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _safe_json(raw: str) -> list:
    try:
        parsed = json.loads(raw or "[]")
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []
