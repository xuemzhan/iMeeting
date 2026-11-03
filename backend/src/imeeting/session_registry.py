"""Process-local registry for active meeting sessions."""

from __future__ import annotations

import threading


class SessionRegistry:
    """Thread-safe owner of active session identifiers.

    The registry is deliberately small: it only answers capacity and liveness
    questions. It does not own the session state machine or persistence.
    """

    def __init__(self) -> None:
        self._session_ids: set[str] = set()
        self._lock = threading.Lock()

    def add(self, session_id: str) -> None:
        with self._lock:
            self._session_ids.add(session_id)

    def discard(self, session_id: str) -> None:
        with self._lock:
            self._session_ids.discard(session_id)

    def try_acquire(self, session_id: str, limit: int) -> bool:
        """原子地「检查 + 占位」（IM-PLN-004 S-1）。

        旧调用方的写法是 ``count() >= limit`` 再 ``add()``：两个连接可以在
        读与写之间同时通过，实际并发数超过上限；CPU 型推理下这会直接压垮机器。
        这里把判断和占位放进同一把锁，超限时不占位。
        """
        with self._lock:
            if session_id in self._session_ids:
                return True
            if len(self._session_ids) >= limit:
                return False
            self._session_ids.add(session_id)
            return True

    def contains(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._session_ids

    def count(self) -> int:
        with self._lock:
            return len(self._session_ids)

    def snapshot(self) -> set[str]:
        with self._lock:
            return set(self._session_ids)

    def clear(self) -> None:
        """Clear all entries; intended for tests and process shutdown."""
        with self._lock:
            self._session_ids.clear()


session_registry = SessionRegistry()


def active_session_count() -> int:
    return session_registry.count()


def is_session_active(session_id: str) -> bool:
    return session_registry.contains(session_id)
