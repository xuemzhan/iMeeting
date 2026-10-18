"""分享链接口令防爆破（进程内实现，多实例部署需替换为 Redis）。"""

from __future__ import annotations

import threading
import time

from .settings import get_config

_lock = threading.Lock()
_failures: dict[str, list[float]] = {}


def _key(token: str, ip: str) -> str:
    return f"{token[:32]}:{ip or 'unknown'}"


def check_allowed(token: str, ip: str) -> tuple[bool, int]:
    window = 300.0
    limit = int(get_config("share.max_failures") or 5)
    lock_seconds = int(get_config("share.lock_seconds") or 60)
    now = time.time()
    key = _key(token, ip)
    with _lock:
        stamps = [t for t in _failures.get(key, []) if now - t < window]
        _failures[key] = stamps
        if len(stamps) < limit:
            return True, 0
        retry = max(1, int(stamps[-1] + lock_seconds - now))
        return False, retry


def record_failure(token: str, ip: str) -> None:
    with _lock:
        _failures.setdefault(_key(token, ip), []).append(time.time())


def clear_failures(token: str, ip: str) -> None:
    with _lock:
        _failures.pop(_key(token, ip), None)
