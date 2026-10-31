"""分享链接口令防爆破（进程内实现，多实例部署需替换为 Redis）。"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from math import ceil

from .settings import get_config

_lock = threading.Lock()
_failures: OrderedDict[str, list[float]] = OrderedDict()
_MAX_ENTRIES = 10_000
_WINDOW_SECONDS = 300.0


def _limits() -> tuple[int, int]:
    return (
        max(1, min(int(get_config("share.max_failures") or 5), 1000)),
        max(1, int(get_config("share.lock_seconds") or 60)),
    )


def _purge(now: float, lock_seconds: int) -> None:
    # Ordered by last failure; checks for unknown tokens never allocate a bucket.
    horizon = max(_WINDOW_SECONDS, lock_seconds)
    while _failures:
        oldest = next(iter(_failures))
        if _failures[oldest][-1] + horizon > now:
            break
        _failures.popitem(last=False)


def _key(token: str, ip: str) -> str:
    return f"{token[:32]}:{ip or 'unknown'}"


def check_allowed(token: str, ip: str) -> tuple[bool, int]:
    limit, lock_seconds = _limits()
    now = time.time()
    key = _key(token, ip)
    with _lock:
        _purge(now, lock_seconds)
        stamps = _failures.get(key)
        if not stamps:
            return True, 0
        if len(stamps) >= limit:
            remaining = stamps[-1] + lock_seconds - now
            if remaining > 0:
                return False, max(1, ceil(remaining))
            _failures.pop(key, None)
        return True, 0


def record_failure(token: str, ip: str) -> None:
    limit, lock_seconds = _limits()
    now = time.time()
    key = _key(token, ip)
    with _lock:
        _purge(now, lock_seconds)
        stamps = [stamp for stamp in _failures.pop(key, []) if now - stamp < _WINDOW_SECONDS]
        _failures[key] = (stamps + [now])[-limit:]
        while len(_failures) > _MAX_ENTRIES:
            _failures.popitem(last=False)


def clear_failures(token: str, ip: str) -> None:
    with _lock:
        _failures.pop(_key(token, ip), None)
