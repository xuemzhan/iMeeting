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
import uuid
from datetime import datetime, timezone

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlmodel import Session, select

from ...application.meeting_sessions import (
    finish_session_row as _finish_session_row,
)
from ...application.meeting_sessions import (
    load_adaptive_profile as _load_adaptive_profile,
)
from ...application.meeting_sessions import (
    load_audio_seq as _load_audio_seq,
)
from ...application.meeting_sessions import (
    persist_segments as _persist_segments,
)
from ...application.meeting_sessions import (
    record_audio_path as _record_audio_path,
)
from ...application.meeting_sessions import (
    record_audio_seq as _record_audio_seq,
)
from ...application.meeting_sessions import (
    resolve_meeting as _resolve_meeting,
)
from ...application.meeting_sessions import (
    save_adaptive_profile as _save_adaptive_profile,
)
from ...application.meeting_sessions import (
    session_offset_ms as _session_offset_ms,
)
from ...application.meeting_sessions import (
    start_session_row as _start_session_row,
)
from ...db import engine as db_engine
from ...engine.audio import AudioSink
from ...engine.streaming import StreamSession, unregister_session
from ...security import decode_access_token
from ...session_registry import session_registry
from ...settings import get_config

logger = logging.getLogger(__name__)

router = APIRouter()


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
                from ...application.jobs import enqueue_job

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
                from ...application.jobs import enqueue_job
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


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


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
