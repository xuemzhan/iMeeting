"""WebSocket 转写流处理器。

协议
----
客户端 → 服务端：
- 文本帧 ``{"type":"start", "meeting_id": 1}``  开始会话
- 二进制帧  16kHz 单声道 float32 PCM
- 文本帧 ``{"type":"stop"}``                    结束会话
- 文本帧 ``{"type":"ping"}``                    心跳

服务端 → 客户端：
- ``ready``    会话建立
- ``partial``  流式草稿（可被同 segment_id 覆盖）
- ``final``    精确定稿
- ``closed``   会话关闭
- ``error``    错误
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlmodel import Session, func, select

from ...db import engine as db_engine
from ...engine.audio import AudioSink
from ...engine.streaming import StreamSession, unregister_session
from ...models import Meeting, MeetingSession, Segment, User
from ...security import decode_access_token
from ...session_registry import session_registry
from ...settings import get_config

logger = logging.getLogger(__name__)

router = APIRouter()

#: 增量落库（离线任务线程）与收尾批量落库可能并发；串行化避免
#: 「两边都查不到已存在、于是各插一条」的重复片段。
_persist_lock = threading.Lock()


@router.websocket("/ws/stream")
async def stream_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()

    from ...maintenance import is_draining

    if is_draining():
        await websocket.send_json(
            {
                "type": "error",
                "code": "SERVER_DRAINING",
                "message": "服务正在发布维护，请稍后重新开始会议",
            }
        )
        await websocket.close(code=1013)
        return

    session: StreamSession | None = None
    meeting_id: int | None = None
    pending_segments: list[dict] = []
    sink = AudioSink(-1)  # 占位；meeting_id 确定后替换
    # 收尾时用于判定会议最终状态
    graceful_stop = False
    failed = False
    progressive_task: asyncio.Task | None = None
    metadata_task: asyncio.Task | None = None
    pending_audio_seq: int | None = None
    audio_chunk_seq = 0

    async def emit(payload: dict) -> None:
        """发送消息给客户端，并把定稿片段缓存待落库。"""
        if payload.get("type") == "final":
            pending_segments.append(payload)
        try:
            await websocket.send_json(payload)
        except (RuntimeError, WebSocketDisconnect):
            logger.debug("发送失败，连接可能已断开")
        # 增量落库：先保证前端拿到字幕，再写库；单条 commit 只有毫秒级。
        # 这样浏览器崩溃/进程被杀时，已定稿内容不会只存在内存里。
        if payload.get("type") == "final" and meeting_id is not None:
            try:
                await asyncio.to_thread(
                    _persist_segments,
                    meeting_id,
                    [payload],
                    session.duration_ms if session else 0,
                    final_status=None,
                )
            except Exception:
                logger.exception("增量落库失败 meeting=%s", meeting_id)

    try:
        # ---- 第一帧必须是 start ----
        first = await websocket.receive()
        if first.get("text") is None:
            await websocket.send_json({"type": "error", "code": "EXPECTED_START",
                                       "message": "首帧必须是 start 消息"})
            await websocket.close(code=1002)
            return

        import json

        try:
            hello = json.loads(first["text"])
        except json.JSONDecodeError:
            await websocket.send_json({"type": "error", "code": "BAD_JSON",
                                       "message": "首帧不是合法 JSON"})
            await websocket.close(code=1002)
            return

        if hello.get("type") != "start":
            await websocket.send_json({"type": "error", "code": "EXPECTED_START",
                                       "message": "首帧必须是 start 消息"})
            await websocket.close(code=1002)
            return

        # ---- 鉴权（token 可选，配置关闭鉴权时允许匿名）----
        user_id, username = _resolve_identity(hello.get("token"))
        if not _auth_required() and user_id is None:
            user_id, username = _anonymous_identity()

        # ---- 并发限流 ----
        if session_registry.count() >= get_config("session.max_concurrent"):
            await websocket.send_json({
                "type": "error",
                "code": "TOO_MANY_SESSIONS",
                "message": f"当前并发会话已达上限（{get_config('session.max_concurrent')}），请稍后再试",
            })
            await websocket.close(code=1013)
            return

        meeting_id = _resolve_meeting(hello, user_id)
        if meeting_id is None:
            await websocket.send_json({"type": "error", "code": "MEETING_FAILED",
                                       "message": "无法创建或找到会议"})
            await websocket.close(code=1011)
            return
        audio_chunk_seq = _load_audio_seq(meeting_id)

        session_id = uuid.uuid4().hex[:12]
        start_offset_ms = _session_offset_ms(meeting_id)
        _start_session_row(meeting_id, session_id, start_offset_ms)
        session = StreamSession(
            meeting_id=meeting_id,
            emit=emit,
            session_id=session_id,
            start_offset_ms=start_offset_ms,
            start_audio_seq=audio_chunk_seq,
            adaptive_profile=_load_adaptive_profile(meeting_id),
        )
        session_registry.add(session.session_id)
        # 音频留存（受 storage.audio_retention_days 控制；关闭时为空操作）
        sink = AudioSink(meeting_id, session_id)

        await session.start()

        minutes = int(get_config("meeting.metadata_capture_minutes") or 0)
        if minutes > 0:
            metadata_task = asyncio.create_task(
                _metadata_capture_loop(meeting_id, session, minutes)
            )

        # 会中渐进纪要：定时基于已落库转写生成阶段草稿
        try:
            from ...llm.client import LLMClient

            if bool(get_config("llm.progressive_enabled")) and LLMClient().configured:
                progressive_task = asyncio.create_task(
                    _progressive_minutes_loop(meeting_id, session)
                )
        except Exception:
            logger.exception("启动渐进纪要失败 meeting=%s", meeting_id)

        # ---- 主循环 ----
        while True:
            message = await websocket.receive()

            if message.get("type") == "websocket.disconnect":
                break

            # 二进制帧 = 音频
            if message.get("bytes") is not None:
                raw = message["bytes"]
                pcm = _decode_pcm(raw)
                if pcm is not None and len(pcm):
                    seq = pending_audio_seq
                    pending_audio_seq = None
                    if seq is not None and seq <= audio_chunk_seq:
                        await websocket.send_json({"type": "audio_ack", "seq": seq})
                        continue
                    sink.push(pcm)
                    await session.push_audio(pcm)
                    if seq is not None:
                        audio_chunk_seq = max(audio_chunk_seq, seq)
                        await asyncio.to_thread(
                            _record_audio_seq, meeting_id, audio_chunk_seq
                        )
                        await websocket.send_json({"type": "audio_ack", "seq": seq})
                continue

            # 文本帧 = 控制消息
            text = message.get("text")
            if not text:
                continue

            try:
                cmd = json.loads(text)
            except json.JSONDecodeError:
                continue

            kind = cmd.get("type")
            if kind == "ping":
                await websocket.send_json({"type": "pong", "ts": _now_ms()})
            elif kind == "audio_chunk":
                try:
                    pending_audio_seq = int(cmd.get("seq"))
                except (TypeError, ValueError):
                    pending_audio_seq = None
            elif kind == "stop":
                graceful_stop = True
                break
            elif kind == "partial_hint":
                # 客户端主动要求立即定稿当前句（例如用户点了「切一句」）
                await session._finalize_current(force=True, reason="user")

    except WebSocketDisconnect:
        logger.info("客户端断开 (meeting=%s)", meeting_id)
    except Exception:
        logger.exception("WebSocket 会话异常 (meeting=%s)", meeting_id)
        failed = True
        try:
            await websocket.send_json({
                "type": "error",
                "code": "INTERNAL",
                "message": "服务端处理异常，请重试",
            })
        except Exception:
            pass
    finally:
        # ---- 收尾：判定状态 → 释放并发名额 → 定稿落库 → 关连接 ----
        # 只有收到客户端显式 stop 才算正常结束；其余（连接被关、异常、
        # 超时）一律标记 abandoned，避免会议永远停在 recording。
        if failed:
            final_status = "failed"
        elif graceful_stop:
            final_status = "finished"
        else:
            final_status = "abandoned"

        # 先把并发名额还回去，且不依赖后续任何步骤成功。
        # 否则 close() 超时或落库抛错会把名额永久占住，服务很快会「假死」。
        if session is not None:
            session_registry.discard(session.session_id)

        if progressive_task is not None:
            progressive_task.cancel()
            try:
                await progressive_task
            except (asyncio.CancelledError, Exception):
                pass

        if metadata_task is not None:
            metadata_task.cancel()
            try:
                await metadata_task
            except (asyncio.CancelledError, Exception):
                pass

        # 先落一次库把状态收口。close() 可能因为模型推理较慢而耗时数秒，
        # 若把它放在落库之前，连接被强关时进程可能在落库前就被回收，
        # 会议就会永远留在 recording。这里先写一次保证状态确定，
        # 再冲刷会话、把 close() 期间新定稿的片段补写进去。
        if meeting_id is not None:
            _persist_segments(
                meeting_id,
                pending_segments,
                session.meeting_end_ms if session else 0,
                final_status=final_status,
            )
            persisted = len(pending_segments)

        if session is not None:
            # 不要用 wait_for 直接取消：它会在超时时取消正在跑的离线推理，
            # 缓冲里的定稿随之丢失（真实长测里出现过两次）。用 shield 等待，
            # 超时后任务继续在后台跑完，emit 仍会增量落库。
            close_task = asyncio.create_task(session.close())
            timeout_s = float(get_config("session.close_timeout_s") or 120)
            try:
                await asyncio.wait_for(asyncio.shield(close_task), timeout=timeout_s)
            except asyncio.TimeoutError:
                logger.warning(
                    "会话 %s 收尾超过 %.0fs，转入后台继续完成（结果仍会落库）",
                    session.session_id,
                    timeout_s,
                )

                def _on_close_done(task: asyncio.Task) -> None:
                    try:
                        task.result()
                    except Exception:
                        logger.exception("后台收尾任务异常")
                    _finish_session_row(
                        meeting_id,
                        session.session_id,
                        session.duration_ms,
                        final_status,
                        None,
                        None,
                    )

                close_task.add_done_callback(_on_close_done)
            except Exception:
                logger.exception("会话收尾异常")

        # close() 会把缓冲里的最后一句定稿，补齐这部分片段。
        if meeting_id is not None and len(pending_segments) > persisted:
            _persist_segments(
                meeting_id,
                pending_segments,
                session.meeting_end_ms if session else 0,
                final_status=final_status,
            )

        # 音频收尾（回填 WAV 头）并把路径写进会议记录
        audio_path = sink.close()
        if meeting_id is not None and audio_path is not None:
            _record_audio_path(meeting_id, str(audio_path))
        if meeting_id is not None and session is not None:
            _save_adaptive_profile(meeting_id, session.adaptive_snapshot)
            _finish_session_row(
                meeting_id,
                session.session_id,
                session.duration_ms,
                final_status,
                str(audio_path) if audio_path is not None else None,
                None,
            )
            unregister_session(session)

        # 短会/提前结束时补一次元信息提取（持久化任务，不阻塞收尾）
        if meeting_id is not None and final_status in ("finished", "abandoned"):
            try:
                from ...jobs import enqueue_job

                enqueue_job(
                    kind="meeting_meta",
                    meeting_id=meeting_id,
                    dedupe_key=f"meeting_meta:{meeting_id}",
                    priority=20,
                )
            except Exception:
                logger.exception("会议元信息任务入队失败 meeting=%s", meeting_id)

        # 会议正常结束后自动生成终稿。说话人全局合并也由该持久化任务先执行，
        # 因而进程重启、后台任务积压都不会让终稿永久丢失。
        if (
            meeting_id is not None
            and final_status == "finished"
            and bool(get_config("llm.auto_final_on_stop"))
        ):
            try:
                from ...jobs import enqueue_job
                from ...llm.client import LLMClient

                if LLMClient().configured:
                    enqueue_job(
                        kind="final_minutes",
                        meeting_id=meeting_id,
                        dedupe_key=f"final_minutes:{meeting_id}",
                        priority=10,
                    )
            except Exception:
                logger.exception("自动终稿任务入队失败 meeting=%s", meeting_id)

        try:
            await websocket.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _decode_pcm(raw: bytes) -> np.ndarray | None:
    """把二进制帧解成 float32 PCM。

    约定：客户端发裸 float32 小端序 PCM。
    """
    if len(raw) % 4 != 0:
        logger.debug("音频帧长度不是 4 的倍数，丢弃")
        return None
    try:
        return np.frombuffer(raw, dtype="<f4").copy()
    except Exception:
        logger.exception("音频解码失败")
        return None


def _resolve_identity(token: str | None) -> tuple[int | None, str]:
    if not token:
        return None, ""
    payload = decode_access_token(token)
    if not payload:
        return None, ""
    try:
        return int(payload["sub"]), payload.get("username", "")
    except (KeyError, ValueError, TypeError):
        return None, ""


def _auth_required() -> bool:
    import os

    return os.getenv("REQUIRE_AUTH", "0") == "1"


def _anonymous_identity() -> tuple[int | None, str]:
    """未开启鉴权时，把会话挂到 admin 名下，方便本地直接使用。"""
    from ...models import User

    with Session(db_engine) as db:
        admin = db.exec(select(User).where(User.role == "admin")).first()
        if admin is not None:
            return admin.id, admin.username
    return None, "anonymous"


def _resolve_meeting(hello: dict, user_id: int | None) -> int | None:
    """确定这次会话的会议 id：传入已有 id 则复用，否则新建。"""
    requested = hello.get("meeting_id")
    title = (hello.get("title") or "未命名会议")[:200]

    with Session(db_engine) as db:
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


def _persist_segments(
    meeting_id: int,
    segments: list[dict],
    duration_ms: int,
    *,
    final_status: str | None = "finished",
) -> None:
    with _persist_lock:
        _persist_segments_locked(
            meeting_id, segments, duration_ms, final_status=final_status
        )


def _persist_segments_locked(
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
        with Session(db_engine) as db:
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


async def _auto_final_minutes(meeting_id: int) -> None:
    """会议结束后后台生成终稿；失败只记日志。"""
    from ...llm.progressive import generate_final_minutes

    try:
        await generate_final_minutes(
            meeting_id,
            min_chars=int(get_config("llm.final_min_chars") or 80),
        )
    except Exception:
        logger.exception("自动生成终稿失败 meeting=%s", meeting_id)


async def _metadata_capture_loop(meeting_id: int, session, minutes: int) -> None:
    """开场 N 分钟后提取会议主题/参会人/议程并推给前端。"""
    from ...meeting_meta import capture_meeting_metadata

    await asyncio.sleep(max(1, minutes) * 60)
    if session.is_closed:
        return
    try:
        payload = await capture_meeting_metadata(meeting_id)
        if payload:
            await session._emit({"type": "meeting_meta", **payload})
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("口语会议信息提取失败 meeting=%s", meeting_id)


async def _capture_meta_bg(meeting_id: int) -> None:
    from ...meeting_meta import capture_meeting_metadata

    try:
        await capture_meeting_metadata(meeting_id)
    except Exception:
        logger.exception("补提会议信息失败 meeting=%s", meeting_id)


async def _progressive_minutes_loop(meeting_id: int, session) -> None:
    """每 N 分钟生成一版会中纪要草稿。

    只读取已增量落库的片段；生成失败不影响转写，下一轮继续尝试。
    """
    from ...llm.progressive import generate_progressive_draft, load_transcript_lines

    interval_s = max(60, int(get_config("llm.progressive_interval_minutes") or 5) * 60)
    min_chars = int(get_config("llm.progressive_min_chars") or 300)
    last_chars = 0

    while not session.is_closed:
        await asyncio.sleep(interval_s)
        if session.is_closed:
            break
        try:
            lines = await asyncio.to_thread(load_transcript_lines, meeting_id)
            chars = sum(len(line.text) for line in lines)
            if chars < min_chars or chars - last_chars < min_chars:
                continue
            payload = await generate_progressive_draft(
                meeting_id, min_chars=min_chars
            )
            if payload is None:
                continue
            last_chars = chars
            await session._emit(
                {
                    "type": "minutes_update",
                    "meeting_id": meeting_id,
                    "minutes": payload,
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("渐进纪要生成失败 meeting=%s", meeting_id)


def _session_offset_ms(meeting_id: int) -> int:
    """本段会话在会议全局时间轴上的起点。"""
    try:
        with Session(db_engine) as db:
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


def _start_session_row(meeting_id: int, session_id: str, offset_ms: int) -> None:
    try:
        with Session(db_engine) as db:
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


def _finish_session_row(
    meeting_id: int,
    session_id: str,
    duration_ms: int,
    status: str,
    audio_path: str | None,
    segment_count: int | None,
) -> None:
    try:
        with Session(db_engine) as db:
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


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _record_audio_path(meeting_id: int, path: str) -> None:
    """把音频文件路径写进会议记录（失败不影响主流程）。"""
    try:
        with Session(db_engine) as db:
            meeting = db.get(Meeting, meeting_id)
            if meeting is not None:
                meeting.audio_path = path[:500]
                db.add(meeting)
                db.commit()
    except Exception:
        logger.exception("写入 audio_path 失败 meeting=%s", meeting_id)


def _load_adaptive_profile(meeting_id: int) -> dict | None:
    try:
        with Session(db_engine) as db:
            meeting = db.get(Meeting, meeting_id)
            if meeting is None or not meeting.adaptive_profile:
                return None
            return json.loads(meeting.adaptive_profile)
    except Exception:
        logger.exception("读取自适应配置失败 meeting=%s", meeting_id)
        return None


def _save_adaptive_profile(meeting_id: int, profile: dict) -> None:
    try:
        with Session(db_engine) as db:
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


def _load_audio_seq(meeting_id: int) -> int:
    try:
        with Session(db_engine) as db:
            meeting = db.get(Meeting, meeting_id)
            return int(meeting.audio_chunk_seq or 0) if meeting else 0
    except Exception:
        logger.exception("读取音频块序号失败 meeting=%s", meeting_id)
        return 0


def _record_audio_seq(meeting_id: int, seq: int) -> None:
    try:
        with Session(db_engine) as db:
            meeting = db.get(Meeting, meeting_id)
            if meeting is None:
                return
            meeting.audio_chunk_seq = max(int(meeting.audio_chunk_seq or 0), seq)
            db.add(meeting)
            db.commit()
    except Exception:
        logger.exception("写入音频块序号失败 meeting=%s seq=%s", meeting_id, seq)
