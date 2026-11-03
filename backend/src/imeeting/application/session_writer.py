"""WS 写入的单写者（IM-PLN-004 C-3）。

问题
----
``api/ws/stream.py`` 是 ``async def``，但握手与收尾路径直接调用同步 DB 函数
（``_resolve_meeting`` / ``_start_session_row`` / ``_persist_segments`` / ...）。
单个事务本身只有 ~2ms，但在写锁竞争时可把事件循环拖到秒级，且同文件里另外
两处已经用了 ``asyncio.to_thread`` —— 这种"一半线程一半直调"的不一致才是根因。

为什么不逐个补 ``to_thread``
--------------------------
补 10 处之后，下一个新增调用还会漏。这里收口成**一个写者**：
1 个线程 + 有界队列，所有会话写入都排队串行执行。SQLite 是单写者模型，
串行本来就是既有限制，区别只是从"隐式锁 + 阻塞事件循环"变成"显式队列 + 可观测"。

灰度
----
配置 ``session.writer_enabled`` 默认关闭；开启后写入走本模块，关闭则沿用原直调路径。
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from typing import Any

logger = logging.getLogger(__name__)

#: 队列深度上界。满时：关键写反压等待，非关键写丢弃并计数。
DEFAULT_CAPACITY = 256


class SessionWriter:
    """串行执行同步 DB 写入的写者。

    用法::

        writer = SessionWriter()
        await writer.submit(persist_segments, meeting_id, batch, disk=..., critical=True)
        writer.close()

    ``submit`` 返回一个可 await 的 future；``critical=True`` 时队列满会等待，
    否则直接丢弃并返回已完成 future（配合 ``stats()`` 的 ``dropped`` 观测）。
    """

    def __init__(self, *, capacity: int = DEFAULT_CAPACITY, name: str = "session-writer") -> None:
        self._queue: queue.Queue[tuple[Callable[..., Any], tuple, dict, Future] | None] = queue.Queue(
            maxsize=capacity
        )
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._closed = False
        self._dropped = 0
        self._executed = 0
        self._errors = 0
        self._thread.start()

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def submit_sync(
        self,
        func: Callable[..., Any],
        *args: Any,
        critical: bool = True,
        **kwargs: Any,
    ) -> Future:
        """同步提交（返回 concurrent.futures.Future）。"""
        future: Future = Future()
        if self._closed:
            future.set_exception(RuntimeError("SessionWriter 已关闭"))
            return future
        item = (func, args, kwargs, future)
        try:
            if critical:
                self._queue.put(item, timeout=10.0)
            else:
                self._queue.put_nowait(item)
        except queue.Full:
            # 非关键写（audio_seq、adaptive_profile）在积压时直接丢弃：
            # 它们都有内存副本，收尾时会再写一次，丢一次不影响正确性。
            self._dropped += 1
            future.set_result(None)
        return future

    async def submit(
        self,
        func: Callable[..., Any],
        *args: Any,
        critical: bool = True,
        **kwargs: Any,
    ) -> Any:
        """异步提交并等待结果；异常会原样抛给调用方。"""
        future = self.submit_sync(func, *args, critical=critical, **kwargs)
        return await asyncio.wrap_future(future)

    def stats(self) -> dict[str, int]:
        return {
            "queue_depth": self._queue.qsize(),
            "executed": self._executed,
            "dropped": self._dropped,
            "errors": self._errors,
        }

    def close(self, timeout: float = 10.0) -> None:
        """排空后停止；已提交的任务仍会执行完。"""
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put(None, timeout=timeout)
        except queue.Full:  # pragma: no cover - 极端积压
            logger.error("SessionWriter 队列无法排空，丢弃剩余任务")
        self._thread.join(timeout=timeout)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                func, args, kwargs, future = item
                if future.cancelled():
                    continue
                try:
                    result = func(*args, **kwargs)
                except Exception as exc:  # 异常必须交给提交方，不能吞
                    self._errors += 1
                    logger.exception("SessionWriter 执行 %s 失败", getattr(func, "__name__", func))
                    future.set_exception(exc)
                else:
                    self._executed += 1
                    future.set_result(result)
            finally:
                self._queue.task_done()


_writer: SessionWriter | None = None
_writer_lock = threading.Lock()


def get_writer() -> SessionWriter:
    """进程级单例（懒创建）。"""
    global _writer
    if _writer is not None and not _writer._closed:  # noqa: SLF001 - 同模块内省
        return _writer
    with _writer_lock:
        if _writer is None or _writer._closed:  # noqa: SLF001
            _writer = SessionWriter()
            logger.info("会话写入单写者已启动")
        return _writer


def shutdown_writer() -> None:
    """进程退出/测试收尾时排空并停止。"""
    global _writer
    with _writer_lock:
        if _writer is not None:
            _writer.close()
            _writer = None
