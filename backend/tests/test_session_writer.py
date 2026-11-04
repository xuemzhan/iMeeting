"""单写者（SessionWriter）测试（IM-PLN-004 C-3）。

它要保证的是四件事：串行顺序、队列有界、关闭时排空、异常不吞。
这四条任何一条破了，"把写入挪出事件循环"就会变成新的正确性风险。
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from imeeting.application.session_writer import SessionWriter, get_writer, shutdown_writer


def test_executes_in_submission_order() -> None:
    writer = SessionWriter(capacity=64)
    seen: list[int] = []
    lock = threading.Lock()

    def record(i: int) -> None:
        time.sleep(0.001)
        with lock:
            seen.append(i)

    try:
        for i in range(20):
            writer.submit_sync(record, i)
        writer.close(timeout=5)
    finally:
        shutdown_writer()

    assert seen == list(range(20)), "写入必须按提交顺序串行执行"


def test_propagates_exception_to_caller() -> None:
    writer = SessionWriter(capacity=8)

    def boom() -> None:
        raise RuntimeError("磁盘满了")

    try:
        future = writer.submit_sync(boom)
        with pytest.raises(RuntimeError, match="磁盘满了"):
            future.result(timeout=5)
        assert writer.stats()["errors"] == 1
    finally:
        writer.close(timeout=5)
        shutdown_writer()


def test_non_critical_submit_is_dropped_instead_of_blocking() -> None:
    """非关键写在积压时必须丢弃，否则 audio_seq 会把主链路一起拖住。"""
    writer = SessionWriter(capacity=1)
    release = threading.Event()

    def slow() -> None:
        release.wait(5)

    try:
        writer.submit_sync(slow)          # 占住唯一的执行者
        writer.submit_sync(slow)          # 占满队列
        # 队列已满：非关键写应当立刻返回，而不是等 10 秒
        started = time.monotonic()
        writer.submit_sync(slow, critical=False)
        elapsed = time.monotonic() - started
        assert elapsed < 0.5
        assert writer.stats()["dropped"] == 1
    finally:
        release.set()
        writer.close(timeout=5)
        shutdown_writer()


def test_close_drains_pending_work() -> None:
    writer = SessionWriter(capacity=64)
    done: list[int] = []

    def record(i: int) -> None:
        done.append(i)

    for i in range(10):
        writer.submit_sync(record, i)
    writer.close(timeout=5)
    shutdown_writer()

    assert sorted(done) == list(range(10)), "close() 必须把已提交的任务执行完"


def test_async_submit_awaits_result() -> None:
    writer = SessionWriter(capacity=8)

    async def scenario() -> int:
        return await writer.submit(lambda: 42)

    try:
        assert asyncio.run(scenario()) == 42
    finally:
        writer.close(timeout=5)
        shutdown_writer()


def test_submit_after_close_raises() -> None:
    writer = SessionWriter(capacity=4)
    writer.close(timeout=2)
    future = writer.submit_sync(lambda: 1)
    with pytest.raises(RuntimeError, match="已关闭"):
        future.result(timeout=2)
    shutdown_writer()


def test_async_submit_does_not_block_event_loop_when_queue_is_full() -> None:
    """队列满时 critical 提交会阻塞 10 秒——那 10 秒绝不能在事件循环里烧掉。

    这是 C-3 的核心诉求：写入变慢只能拖慢写入，不能把整个服务卡住。
    """
    writer = SessionWriter(capacity=1)
    release = threading.Event()

    def slow() -> None:
        release.wait(5)

    async def scenario() -> float:
        writer.submit_sync(slow)  # 占住唯一的执行者
        writer.submit_sync(slow)  # 占满队列
        task = asyncio.create_task(writer.submit(slow, critical=True))
        # 队列已满：submit 内部会在工作线程里等，事件循环必须照常跑
        started = time.monotonic()
        await asyncio.sleep(0.1)
        elapsed = time.monotonic() - started
        release.set()
        await asyncio.wait_for(task, timeout=5)
        return elapsed

    try:
        elapsed = asyncio.run(scenario())
    finally:
        release.set()
        writer.close(timeout=5)
        shutdown_writer()

    assert elapsed < 0.5, f"提交把事件循环卡住了 {elapsed:.2f}s"


def test_singleton_is_reused() -> None:
    try:
        first = get_writer()
        second = get_writer()
        assert first is second
    finally:
        shutdown_writer()


def test_writer_stats_is_not_created_by_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """看指标不能反过来创建写者。

    ``/metrics`` 抓取频率远高于发生真实写入；若抓一次就懒创建一个后台线程，
    观测本身就成了副作用。
    """

    from imeeting.application import session_writer as module

    monkeypatch.setattr(module, "_writer", None)
    assert module.writer_stats() is None

    try:
        get_writer()
        stats = module.writer_stats()
        assert stats is not None
        assert set(stats) == {"queue_depth", "executed", "dropped", "errors"}
    finally:
        shutdown_writer()


def test_lifespan_drains_the_writer_on_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C-3 的「关闭排空」必须挂在应用生命周期上。

    只测 ``SessionWriter.close()`` 是不够的：生产里没人手动调它。若 lifespan 不调
    ``shutdown_writer()``，SIGTERM 时队列里「已经算完、只差落库」的片段会全部丢失，
    而单元测试仍然全绿。
    """

    from fastapi.testclient import TestClient

    from imeeting.application import session_writer as module
    from imeeting.main import app

    calls: list[int] = []
    monkeypatch.setattr(module, "shutdown_writer", lambda: calls.append(1))

    with TestClient(app):
        pass

    assert calls == [1], "服务关闭时必须排空单写者"


def _seed_meeting(owner_id: int, title: str) -> int:
    """建一条 recording 会议，返回 id。"""
    from sqlmodel import Session

    from imeeting.db import engine
    from imeeting.models import Meeting

    with Session(engine) as db:
        meeting = Meeting(title=title, owner_id=owner_id, status="recording")
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        return meeting.id


def _apply_session_ops(meeting_id: int, session_id: str, run) -> None:
    """按真实收尾顺序执行一遍写入（C-1/C-2/C-3 覆盖到的那些函数）。

    顺序刻意包含「同一片段重复投递」与「先增量、后收口」，因为灰度切换
    真正会踩到的就是这两处语义。
    """
    from imeeting.application.meeting_sessions import (
        finish_session_row,
        persist_segments,
        record_audio_path,
        record_audio_seq,
        save_adaptive_profile,
        start_session_row,
    )

    seg1 = {"segment_id": "s-1", "text": "第一句", "start_ms": 0, "end_ms": 900,
            "speaker": "张三", "seq": 0}
    seg2 = {"segment_id": "s-2", "text": "第二句", "start_ms": 900, "end_ms": 1800,
            "speaker": "李四", "seq": 1}
    seg3 = {"segment_id": "s-3", "text": "第三句", "start_ms": 1800, "end_ms": 3000,
            "speaker": "张三", "seq": 2}

    run(start_session_row, meeting_id, session_id, 0)
    run(persist_segments, meeting_id, [seg1, seg2], 0,
        final_status=None, session_id=session_id)
    run(record_audio_seq, meeting_id, 500)
    # seg2 再投一次：两条路径都必须按 segment_id 去重
    run(persist_segments, meeting_id, [seg2, seg3], 4200,
        final_status="finished", session_id=session_id)
    run(record_audio_path, meeting_id, "audio/x.wav")
    run(save_adaptive_profile, meeting_id, {"gain": 1.2})
    run(finish_session_row, meeting_id, session_id, 4200, "finished", "audio/x.wav", None)


def _snapshot(meeting_id: int) -> dict:
    """把两条路径写出来的库状态压成可比对的快照（去掉 id 与时间戳）。"""
    from sqlmodel import Session, select

    from imeeting.db import engine
    from imeeting.models import Meeting, MeetingSession, Segment

    with Session(engine) as db:
        meeting = db.get(Meeting, meeting_id)
        sessions = db.exec(
            select(MeetingSession).where(MeetingSession.meeting_id == meeting_id)
        ).all()
        segments = db.exec(
            select(Segment).where(Segment.meeting_id == meeting_id).order_by(Segment.seq)
        ).all()
        return {
            "meeting": {
                "status": meeting.status,
                "segment_count": meeting.segment_count,
                "duration_ms": meeting.duration_ms,
                "audio_path": meeting.audio_path,
            },
            "sessions": [
                {
                    "status": row.status,
                    "duration_ms": row.duration_ms,
                    "segment_count": row.segment_count,
                    "audio_path": row.audio_path,
                    "ended": row.ended_at is not None,
                }
                for row in sessions
            ],
            "segments": [
                {
                    "segment_id": seg.segment_id,
                    "text": seg.text,
                    "start_ms": seg.start_ms,
                    "end_ms": seg.end_ms,
                    "speaker": seg.speaker,
                    "seq": seg.seq,
                }
                for seg in segments
            ],
        }


def test_writer_path_matches_direct_path(admin_user) -> None:
    """C-3 灰度开关两个位置必须写出**逐字段相同**的库状态。

    IM-RPT-011 §3.3 把这条列为未做。缺了它，"打开开关"就只能靠人工比对：
    去重、segment_count、状态收口任何一处变了，都不会有人发现。
    """
    writer = SessionWriter(capacity=64)

    def direct(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    def through_writer(fn, *args, **kwargs):
        return writer.submit_sync(fn, *args, **kwargs).result(timeout=10)

    direct_meeting = _seed_meeting(admin_user.id, "直调路径")
    writer_meeting = _seed_meeting(admin_user.id, "单写者路径")

    try:
        _apply_session_ops(direct_meeting, "sess-direct", direct)
        _apply_session_ops(writer_meeting, "sess-writer", through_writer)
    finally:
        writer.close(timeout=5)
        shutdown_writer()

    assert _snapshot(writer_meeting) == _snapshot(direct_meeting)
