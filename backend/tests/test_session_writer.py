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


def test_singleton_is_reused() -> None:
    try:
        first = get_writer()
        second = get_writer()
        assert first is second
    finally:
        shutdown_writer()
