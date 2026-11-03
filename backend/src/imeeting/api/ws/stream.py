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
from ...ops import count_dropped_frame
from ...security import decode_access_token
from ...session_registry import session_registry
from ...settings import get_config

logger = logging.getLogger(__name__)
_HANDSHAKE_TIMEOUT_SECONDS = 15.0
_IDLE_TIMEOUT_SECONDS = 90.0
#: audio_chunk_seq 的合并写节奏（C-2）：每 32 块或每 5 秒落库一次。
_AUDIO_SEQ_FLUSH_CHUNKS = 32
_AUDIO_SEQ_FLUSH_SECONDS = 5.0


async def _db(fn, *args, critical: bool = True, **kwargs):
    """把同步 DB 调用挪出事件循环；开关打开时改走单写者（IM-PLN-004 C-3）。

    为什么不逐个补 ``asyncio.to_thread``：写在这里就只有一个入口，
    新增调用想漏也漏不掉；而 ``session.writer_enabled`` 让它可以灰度切换。
    """
    if bool(get_config("session.writer_enabled")):
        from ...application.session_writer import get_writer

        return await get_writer().submit(fn, *args, critical=critical, **kwargs)
    return await asyncio.to_thread(fn, *args, **kwargs)

router = APIRouter()


class SocketSender:
    """Serialize sends and close; treat transport closure as a disconnect."""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self.lock = asyncio.Lock()
        self.closed = False

    async def send_json(self, payload: dict) -> None:
        async with self.lock:
            if self.closed:
                raise WebSocketDisconnect(code=1006)
            try:
                await self.websocket.send_json(payload)
            except (WebSocketDisconnect, OSError):
                self.closed = True
                raise WebSocketDisconnect(code=1006) from None
            except RuntimeError as exc:
                # Starlette and Uvicorn report a closed transport differently.
                if 'close' not in str(exc).lower():
                    raise
                self.closed = True
                raise WebSocketDisconnect(code=1006) from exc

    async def close(self, code: int = 1000) -> None:
        async with self.lock:
            if self.closed:
                return
            self.closed = True
            await self.websocket.close(code=code)


@router.websocket("/ws/stream")
async def stream_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    sender = SocketSender(websocket)

    from ...maintenance import is_draining

    if is_draining():
        await sender.send_json(
            {
                "type": "error",
                "code": "SERVER_DRAINING",
                "message": "服务正在发布维护，请稍后重新开始会议",
            }
        )
        await sender.close(code=1013)
        return

    session: StreamSession | None = None
    meeting_id: int | None = None
    pending_segments: list[dict] = []
    sink: AudioSink | None = None
    # 收尾时用于判定会议最终状态
    graceful_stop = False
    failed = False
    progressive_task: asyncio.Task | None = None
    metadata_task: asyncio.Task | None = None
    person_task: asyncio.Task | None = None
    pending_audio_seq: int | None = None
    audio_chunk_seq = 0
    #: 提前 return 的路径（握手失败）也会进 finally，所以这里必须先绑定。
    session_id: str | None = None
    #: C-2 的合并写状态：累计块数与上次落库时刻。
    _seq_writes = 0
    _seq_flushed_at = asyncio.get_running_loop().time()
    closed_payload: dict | None = None

    async def emit(payload: dict) -> None:
        """发送消息给客户端，并把定稿片段缓存待落库。"""
        nonlocal closed_payload
        if payload.get("type") == "closed":
            # Acknowledge stop only after WAV/session persistence below. Clients
            # may disconnect immediately when they receive this message.
            closed_payload = payload
            return
        if payload.get("type") == "final":
            pending_segments.append(payload)
        try:
            await sender.send_json(payload)
        except (RuntimeError, WebSocketDisconnect):
            logger.debug("发送失败，连接可能已断开")
        # 增量落库：先保证前端拿到字幕，再写库；单条 commit 只有毫秒级。
        # 这样浏览器崩溃/进程被杀时，已定稿内容不会只存在内存里。
        if payload.get("type") == "final" and meeting_id is not None:
            try:
                await _db(
                    _persist_segments,
                    meeting_id,
                    [payload],
                    session.duration_ms if session else 0,
                    final_status=None,
                )
                from ...application.person_extraction import request_auto
                await _db(request_auto, meeting_id, text=payload.get("text", ""), critical=False)
            except Exception:
                logger.exception("增量落库失败 meeting=%s", meeting_id)

    try:
        # ---- 第一帧必须是 start ----
        try:
            first = await asyncio.wait_for(websocket.receive(), _HANDSHAKE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            await sender.send_json({"type": "error", "code": "START_TIMEOUT",
                                       "message": "等待开始消息超时，请重新连接"})
            await sender.close(code=1008)
            return
        if first.get("text") is None:
            await sender.send_json({"type": "error", "code": "EXPECTED_START",
                                       "message": "首帧必须是 start 消息"})
            await sender.close(code=1002)
            return

        try:
            hello = json.loads(first["text"])
        except json.JSONDecodeError:
            await sender.send_json({"type": "error", "code": "BAD_JSON",
                                       "message": "首帧不是合法 JSON"})
            await sender.close(code=1002)
            return

        if not isinstance(hello, dict) or hello.get("type") != "start":
            await sender.send_json({"type": "error", "code": "EXPECTED_START",
                                       "message": "首帧必须是 start 消息"})
            await sender.close(code=1002)
            return

        # ---- 鉴权（token 可选，配置关闭鉴权时允许匿名）----
        user_id, username, must_change_password = _resolve_identity(hello.get("token"))
        if not _auth_required() and user_id is None:
            user_id, username = _anonymous_identity()
            must_change_password = False
        if must_change_password:
            await sender.send_json(
                {
                    "type": "error",
                    "code": "PASSWORD_CHANGE_REQUIRED",
                    "message": "请先修改初始密码后再开始会议",
                }
            )
            await sender.close(code=1008)
            return

        if _auth_required() and user_id is None:
            await sender.send_json({"type": "error", "code": "UNAUTHORIZED",
                                       "message": "请重新登录后开始会议"})
            await sender.close(code=1008)
            return

        if str(get_config("asr.mode") or "normal") == "normal":
            from ...engine import pool

            if not get_config("asr.warmup_on_start"):
                # Explicit lazy loading: do not acknowledge audio until both
                # inference paths have loaded successfully.
                for prefix in ("streaming", "offline"):
                    await asyncio.to_thread(pool.get_model, prefix)
            if not pool.is_ready("streaming") or not pool.is_ready("offline"):
                await sender.send_json({"type": "error", "code": "MODELS_NOT_READY",
                                           "message": "识别模型尚未就绪，请稍后重试"})
                await sender.close(code=1013)
                return
        # ---- 并发限流（S-1：原子占用，不能先读后写）----
        # 先生成 session_id，再用 try_acquire 一次性判断+占位：
        # 旧写法 count() >= max 与后面的 add() 之间有两个连接同时通过的时间窗。
        session_id = uuid.uuid4().hex[:12]
        _max_concurrent = int(get_config("session.max_concurrent") or 4)
        if not session_registry.try_acquire(session_id, _max_concurrent):
            await sender.send_json({
                "type": "error",
                "code": "TOO_MANY_SESSIONS",
                "message": f"当前并发会话已达上限（{_max_concurrent}），请稍后再试",
            })
            await sender.close(code=1013)
            return

        try:
            meeting_id = await _db(_resolve_meeting, hello, user_id)
            if meeting_id is None:
                await sender.send_json({"type": "error", "code": "MEETING_FAILED",
                                           "message": "无法创建或找到会议"})
                await sender.close(code=1011)
                return
            audio_chunk_seq = await _db(_load_audio_seq, meeting_id)

            start_offset_ms = await _db(_session_offset_ms, meeting_id)
            await _db(_start_session_row, meeting_id, session_id, start_offset_ms)
            adaptive_profile = await _db(_load_adaptive_profile, meeting_id)
            session = StreamSession(
                meeting_id=meeting_id,
                emit=emit,
                session_id=session_id,
                start_offset_ms=start_offset_ms,
                start_audio_seq=audio_chunk_seq,
                adaptive_profile=adaptive_profile,
            )
        except Exception:
            # 占位成功但后续建立失败：必须把名额还回去，否则并发上限会被慢性耗尽。
            session_registry.discard(session_id)
            raise
        # 音频留存（受 storage.audio_retention_days 控制；关闭时为空操作）
        sink = AudioSink(meeting_id, session_id)

        await session.start()

        minutes = int(get_config("meeting.metadata_capture_minutes") or 0)
        if minutes > 0:
            metadata_task = asyncio.create_task(
                _metadata_capture_loop(meeting_id, session, minutes)
            )

        person_task = asyncio.create_task(_person_extraction_loop(meeting_id, session))

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
        max_duration_s = float(get_config("session.max_duration_minutes") or 180) * 60
        deadline = asyncio.get_running_loop().time() + max_duration_s
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                await sender.send_json({"type": "error", "code": "SESSION_LIMIT",
                                           "message": "已达到单会话最长时长，请重新开始"})
                break
            try:
                message = await asyncio.wait_for(
                    websocket.receive(), min(_IDLE_TIMEOUT_SECONDS, remaining)
                )
            except asyncio.TimeoutError:
                await sender.send_json({"type": "error", "code": "SESSION_TIMEOUT",
                                           "message": "会话等待超时，请重新连接"})
                break

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
                        await sender.send_json({"type": "audio_ack", "seq": seq})
                        continue
                    if session.duration_ms / 1000 + len(pcm) / 16000 > max_duration_s:
                        await sender.send_json({"type": "error", "code": "SESSION_LIMIT",
                                                   "message": "已达到单会话最长时长，请重新开始"})
                        break
                    await asyncio.to_thread(sink.push, pcm)
                    await session.push_audio(pcm)
                    if seq is not None:
                        audio_chunk_seq = max(audio_chunk_seq, seq)
                        # C-2：audio_chunk_seq 只用于断线补传的起点，不需要逐块持久化。
                        # 合并为「每 32 块或每 5 秒」写一次，收尾时再补一次最终值。
                        # 最坏情况只是补传范围回退几秒，服务端按 seq <= audio_chunk_seq
                        # 幂等丢弃（见上面 322 行），用户无感。
                        _seq_writes += 1
                        if (
                            _seq_writes >= _AUDIO_SEQ_FLUSH_CHUNKS
                            or asyncio.get_running_loop().time() - _seq_flushed_at
                            >= _AUDIO_SEQ_FLUSH_SECONDS
                        ):
                            await _db(
                                _record_audio_seq, meeting_id, audio_chunk_seq, critical=False
                            )
                            _seq_writes = 0
                            _seq_flushed_at = asyncio.get_running_loop().time()
                        await sender.send_json({"type": "audio_ack", "seq": seq})
                continue

            # 文本帧 = 控制消息
            text = message.get("text")
            if not text:
                continue

            try:
                cmd = json.loads(text)
            except json.JSONDecodeError:
                continue

            if not isinstance(cmd, dict):
                continue

            kind = cmd.get("type")
            if kind == "ping":
                await sender.send_json({"type": "pong", "ts": _now_ms()})
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
            await sender.send_json({
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
        elif session_id is not None:
            # 提前 return 的路径（MEETING_FAILED 等）也要把 S-1 占到的名额还回去，
            # 否则每失败一次就永久少一个并发槽。握手就失败的连接没有占过名额。
            session_registry.discard(session_id)

        if person_task is not None:
            person_task.cancel()
            try:
                await person_task
            except (asyncio.CancelledError, Exception):
                pass

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
        #
        # 注意：这一次写入**必须是同步直调**，不能走 _db()/to_thread。
        # 连接被硬断时 endpoint 任务会被取消，finally 里的 await 会在取消点
        # 直接抛 CancelledError —— 收口写入就被跳过了，"僵尸会议"随之回归
        # （test_abnormal_disconnect_marks_abandoned 覆盖此路径）。
        # 代价只是关闭路径上一次几毫秒的事务，换来的是状态一定落库。
        if meeting_id is not None:
            _persist_segments(
                meeting_id,
                pending_segments,
                session.meeting_end_ms if session else 0,
                final_status=final_status,
                session_id=session.session_id if session else None,
            )
            persisted = len(pending_segments)
            if session is not None:
                # 先把 session 行收口（同步、无取消点）：硬断连时 endpoint 任务会在
                # 下面 `close_task` 的 await 处被取消，若不先写，这一行会永远停在
                # recording，活跃会话注册表也会泄漏。
                # 末尾还会用真实时长与音频路径再写一次（finish_session_row 取 max，
                # 重复调用是安全的），所以这里的时长只是"至少这么多"。
                _finish_session_row(
                    meeting_id,
                    session.session_id,
                    session.duration_ms,
                    final_status,
                    None,
                    None,
                )
                unregister_session(session)

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
                session_id=session.session_id if session else None,
            )

        # ---- 收尾写入：必须不可被打断 ----
        # 背景：连接被硬断时 endpoint 任务会被取消，finally 里任何 await 都会在取消点
        # 抛 CancelledError 并跳过其后所有语句。实测后果是 session 行停在 recording、
        # 活跃会话注册表泄漏（test_abrupt_disconnect_closes_session_row 覆盖）。
        # 因此把「可能被打断的部分」（关音频文件）与「必须成功的状态写入」分开：
        # 后者放进嵌套 finally，且全部同步调用，不再有任何取消点。
        audio_path = None
        try:
            if sink is not None:
                # to_thread 是原有写法；它被打断的损失可由 repair_interrupted_wavs 补偿。
                audio_path = await asyncio.to_thread(sink.close)
        finally:
            if meeting_id is not None and _seq_writes:
                _record_audio_seq(meeting_id, audio_chunk_seq)
                _seq_writes = 0
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
                # 注册表必须收口，否则已结束的会话仍会被 rename_active_speaker 命中
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

        if meeting_id is not None and final_status in ("finished", "abandoned"):
            from ...application.jobs import enqueue_job
            from ...application.person_extraction import request_auto
            try:
                await asyncio.to_thread(request_auto, meeting_id, trigger="opening")
                if get_config("spk.template_rebuild_enabled"):
                    enqueue_job(kind="template_rebuild", meeting_id=meeting_id,
                                dedupe_key=f"template_rebuild:{meeting_id}", priority=40)
            except Exception:
                logger.exception("会议人员/模板任务入队失败 meeting=%s", meeting_id)

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
            if closed_payload is not None:
                await sender.send_json(closed_payload)
            await sender.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


#: 单个音频帧的字节上限（IM-PLN-004 S-5）。1MB ≈ 32 秒 16kHz float32，
#: 远超正常分块（约 600ms / 19KB），超过即视作异常输入丢弃。
_MAX_FRAME_BYTES = 1024 * 1024


def _decode_pcm(raw: bytes) -> np.ndarray | None:
    """把二进制帧解成 float32 PCM。

    约定：客户端发裸 float32 小端序 PCM。
    """
    if not raw or len(raw) % 4 != 0:
        logger.debug("音频帧长度不是 4 的倍数，丢弃")
        return None
    if len(raw) > _MAX_FRAME_BYTES:
        # 旧实现只依赖 uvicorn 的 16MB 上限：单帧被放大到 16MB 时会解码成
        # 400 万个 float（约 16MB numpy 对象），属于可被轻易触发的内存放大。
        # 必须计数：只记日志的话，被打屏截断就等于没发生（IM-PLN-004 S-5）。
        count_dropped_frame()
        logger.warning("音频帧过大（%d 字节 > %d），丢弃", len(raw), _MAX_FRAME_BYTES)
        return None
    try:
        return np.frombuffer(raw, dtype="<f4").copy()
    except Exception:
        logger.exception("音频解码失败")
        return None


def _now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _resolve_identity(token: str | None) -> tuple[int | None, str, bool]:
    if not token:
        return None, "", False
    payload = decode_access_token(token)
    if not payload:
        return None, "", False
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError, TypeError):
        return None, "", False
    from ...models import User

    with Session(db_engine) as db:
        user = db.get(User, user_id)
        if user is None or not user.is_active or payload.get("ver", 0) != user.auth_version:
            return None, "", False
        return user.id, user.username, user.must_change_password


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
        from ...application.person_extraction import request_auto
        await asyncio.to_thread(request_auto, meeting_id, trigger="opening")
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

    interval_s = max(60, int(get_config("llm.progressive_interval_minutes") or 3) * 60)
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
            # A bounded batch may cover only part of the backlog. Count what
            # was actually consumed so the next tick continues without waiting
            # for further speech (including when the speaker has paused).
            covered_end = payload.get("transcript_end_ms", 0)
            last_chars = sum(len(line.text) for line in lines
                             if (line.end_ms or line.start_ms + 1000) <= covered_end)
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


async def _person_extraction_loop(meeting_id, session):
    from ...application.person_extraction import request_auto
    while not session.is_closed:
        interval = int(get_config("person.extract_interval_minutes"))
        await asyncio.sleep(max(30, interval * 60))
        if session.is_closed:
            return
        try:
            await asyncio.to_thread(request_auto, meeting_id, trigger="periodic")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("人员定时任务入队失败 meeting=%s", meeting_id)
