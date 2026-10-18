"""Meeting/session persistence used by the WebSocket transport."""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from sqlmodel import Session, func, select

from ..db import engine
from ..models import Meeting, MeetingSession, Segment, User

logger = logging.getLogger(__name__)

_persist_lock = threading.Lock()


def resolve_meeting(hello: dict, user_id: int | None) -> int | None:
    """确定这次会话的会议 id：传入已有 id 则复用，否则新建。"""
    requested = hello.get("meeting_id")
    title = (hello.get("title") or "未命名会议")[:200]

    with Session(engine) as db:
        if requested is not None:
            try:
                meeting_id = int(requested)
            except (TypeError, ValueError):
                return None
            meeting = db.get(Meeting, meeting_id)
            # 继续已有会议时必须校验归属；否则任何人只要猜到连续的数字 id
            # 就能把音频和片段写进别人的会议。
            if meeting is not None and meeting.deleted_at is None:
                owner = db.get(User, user_id) if user_id is not None else None
                if (
                    user_id is None
                    or (
                        meeting.owner_id != user_id
                        and (owner is None or owner.role != "admin")
                    )
                ):
                    return None
                meeting.status = "recording"
                meeting.finished_at = None
                db.add(meeting)
                db.commit()
                return meeting.id

        if user_id is None:
            return None

        meeting = Meeting(title=title, owner_id=user_id, status="recording")
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        return meeting.id


def persist_segments(
    meeting_id: int,
    segments: list[dict],
    duration_ms: int,
    *,
    final_status: str | None = "finished",
) -> None:
    with _persist_lock:
        persist_segments_locked(
            meeting_id, segments, duration_ms, final_status=final_status
        )


def persist_segments_locked(
    meeting_id: int,
    segments: list[dict],
    duration_ms: int,
    *,
    final_status: str | None = "finished",
) -> None:
    """把定稿片段批量落库，并更新会议状态。

    ``final_status`` 由调用方根据会话结束方式决定：

    - ``finished``   客户端显式 stop，正常结束
    - ``abandoned``  连接被关/超时等中断，已收内容照常落库但标记为中断
    - ``failed``     处理过程抛异常

    ``None`` 表示「会话进行中的增量落库」：只写片段与计数，
    不修改会议状态/结束时间，避免把 recording 会议提前收口。

    无论是否有片段，都必须把会议从 ``recording`` 里「解出来」——
    否则每次浏览器异常关闭都会在库里留下一条永远进行中的僵尸会议。
    """
    try:
        with Session(engine) as db:
            existing_ids = set(
                db.exec(select(Segment.segment_id).where(Segment.meeting_id == meeting_id)).all()
            )
            added = 0
            for seg in segments:
                sid = seg.get("segment_id")
                if not sid or sid in existing_ids:
                    continue
                db.add(
                    Segment(
                        meeting_id=meeting_id,
                        segment_id=sid,
                        session_id=seg.get("session_id"),
                        text=seg.get("text", ""),
                        is_final=True,
                        start_ms=seg.get("start_ms", 0),
                        end_ms=seg.get("end_ms", 0),
                        speaker=seg.get("speaker"),
                        speaker_key=seg.get("speaker_key"),
                        speaker_score=float(seg.get("speaker_score") or 0),
                        speaker_source=str(seg.get("speaker_source") or ""),
                        asr_source=str(seg.get("asr_source") or ""),
                        asr_error=seg.get("asr_error"),
                        finalize_reason=str(seg.get("finalize_reason") or ""),
                        speech_ms=int(seg.get("speech_ms") or 0),
                        audio_rms=float(seg.get("audio_rms") or 0),
                        noise_floor=float(seg.get("noise_floor") or 0),
                        snr_db=float(seg.get("snr_db") or 0),
                        adaptive_gain=float(seg.get("adaptive_gain") or 1),
                        profile_version=int(seg.get("profile_version") or 0),
                        seq=seg.get("seq", 0),
                    )
                )
                existing_ids.add(sid)
                added += 1

            meeting = db.get(Meeting, meeting_id)
            if meeting is not None:
                if final_status is not None:
                    meeting.status = final_status
                    meeting.finished_at = datetime.now(timezone.utc)
                if duration_ms:
                    # 多段会话：只增不减，避免重连后把整场时长改小
                    meeting.duration_ms = max(meeting.duration_ms or 0, duration_ms)
                # flush 后计数才包含本批次新增的片段；用 count 避免把整场文本拉回内存
                db.flush()
                meeting.segment_count = int(
                    db.exec(
                        select(func.count())
                        .select_from(Segment)
                        .where(Segment.meeting_id == meeting_id)
                    ).one()
                )
                db.add(meeting)

            db.commit()
            logger.info(
                "会议 %s 落库 %d 段，状态=%s",
                meeting_id,
                added,
                final_status or "recording",
            )
    except Exception:
        logger.exception("片段落库失败 meeting=%s", meeting_id)


def session_offset_ms(meeting_id: int) -> int:
    """本段会话在会议全局时间轴上的起点。"""
    try:
        with Session(engine) as db:
            rows = db.exec(
                select(MeetingSession).where(MeetingSession.meeting_id == meeting_id)
            ).all()
            meeting = db.get(Meeting, meeting_id)
            from_db = max(
                (r.start_offset_ms + r.duration_ms for r in rows), default=0
            )
            return int(max(from_db, meeting.duration_ms if meeting is not None else 0))
    except Exception:
        logger.exception("计算会话时间轴偏移失败 meeting=%s", meeting_id)
        return 0


def start_session_row(meeting_id: int, session_id: str, offset_ms: int) -> None:
    try:
        with Session(engine) as db:
            existing = db.exec(
                select(MeetingSession).where(MeetingSession.session_id == session_id)
            ).first()
            if existing is None:
                db.add(
                    MeetingSession(
                        meeting_id=meeting_id,
                        session_id=session_id,
                        start_offset_ms=offset_ms,
                        status="recording",
                    )
                )
                db.commit()
    except Exception:
        logger.exception("创建会话记录失败 meeting=%s session=%s", meeting_id, session_id)


def finish_session_row(
    meeting_id: int,
    session_id: str,
    duration_ms: int,
    status: str,
    audio_path: str | None,
    segment_count: int | None,
) -> None:
    try:
        with Session(engine) as db:
            row = db.exec(
                select(MeetingSession).where(MeetingSession.session_id == session_id)
            ).first()
            if row is None:
                return
            row.ended_at = datetime.now(timezone.utc)
            row.duration_ms = max(row.duration_ms or 0, int(duration_ms))
            row.status = status
            if audio_path:
                row.audio_path = audio_path[:500]
            if segment_count is None:
                row.segment_count = int(
                    db.exec(
                        select(func.count())
                        .select_from(Segment)
                        .where(
                            Segment.meeting_id == meeting_id,
                            Segment.session_id == session_id,
                        )
                    ).one()
                )
            else:
                row.segment_count = segment_count
            meeting = db.get(Meeting, meeting_id)
            if meeting is not None:
                meeting.duration_ms = max(
                    meeting.duration_ms or 0,
                    row.start_offset_ms + row.duration_ms,
                )
                db.add(meeting)
            db.add(row)
            db.commit()
    except Exception:
        logger.exception("更新会话记录失败 session=%s", session_id)


def record_audio_path(meeting_id: int, path: str) -> None:
    """把音频文件路径写进会议记录（失败不影响主流程）。"""
    try:
        with Session(engine) as db:
            meeting = db.get(Meeting, meeting_id)
            if meeting is not None:
                meeting.audio_path = path[:500]
                db.add(meeting)
                db.commit()
    except Exception:
        logger.exception("写入 audio_path 失败 meeting=%s", meeting_id)


def load_adaptive_profile(meeting_id: int) -> dict | None:
    try:
        with Session(engine) as db:
            meeting = db.get(Meeting, meeting_id)
            if meeting is None or not meeting.adaptive_profile:
                return None
            return json.loads(meeting.adaptive_profile)
    except Exception:
        logger.exception("读取自适应配置失败 meeting=%s", meeting_id)
        return None


def save_adaptive_profile(meeting_id: int, profile: dict) -> None:
    try:
        with Session(engine) as db:
            meeting = db.get(Meeting, meeting_id)
            if meeting is None:
                return
            meeting.adaptive_profile = json.dumps(
                profile, ensure_ascii=False, separators=(",", ":")
            )
            db.add(meeting)
            db.commit()
    except Exception:
        logger.exception("写入自适应配置失败 meeting=%s", meeting_id)


def load_audio_seq(meeting_id: int) -> int:
    try:
        with Session(engine) as db:
            meeting = db.get(Meeting, meeting_id)
            return int(meeting.audio_chunk_seq or 0) if meeting else 0
    except Exception:
        logger.exception("读取音频块序号失败 meeting=%s", meeting_id)
        return 0


def record_audio_seq(meeting_id: int, seq: int) -> None:
    try:
        with Session(engine) as db:
            meeting = db.get(Meeting, meeting_id)
            if meeting is None:
                return
            meeting.audio_chunk_seq = max(int(meeting.audio_chunk_seq or 0), seq)
            db.add(meeting)
            db.commit()
    except Exception:
        logger.exception("写入音频块序号失败 meeting=%s seq=%s", meeting_id, seq)
