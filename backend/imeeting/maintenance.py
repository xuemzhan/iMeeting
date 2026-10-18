"""发布维护状态与会话排空。"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_draining = False
_reason = ""


def set_draining(enabled: bool, reason: str = "") -> None:
    global _draining, _reason
    with _lock:
        _draining = enabled
        _reason = reason[:500] if enabled else ""


def drain_status() -> dict:
    with _lock:
        return {"draining": _draining, "reason": _reason}


def is_draining() -> bool:
    with _lock:
        return _draining
