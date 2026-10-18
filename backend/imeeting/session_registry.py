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
