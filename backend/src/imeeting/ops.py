"""轻量 Prometheus 指标与结构化日志支持。"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
import time
from collections import defaultdict
from pathlib import Path

_DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

_lock = threading.Lock()
_requests: dict[tuple[str, str, str], int] = defaultdict(int)
_duration_sum: dict[tuple[str, str], float] = defaultdict(float)
_duration_count: dict[tuple[str, str], int] = defaultdict(int)
_duration_buckets: dict[tuple[str, str], list[int]] = {}
_started = time.time()


def observe_request(method: str, path: str, status: int, seconds: float) -> None:
    # 避免把会议 id、token 等高基数值直接作为指标标签。
    normalized = re.sub(r"/\d+", "/{id}", path)
    normalized = re.sub(r"/share/[^/]+", "/share/{token}", normalized)
    with _lock:
        _requests[(method, normalized, str(status))] += 1
        _duration_sum[(method, normalized)] += seconds
        _duration_count[(method, normalized)] += 1
        bucket_counts = _duration_buckets.setdefault(
            (method, normalized),
            [0] * len(_DURATION_BUCKETS),
        )
        for index, upper_bound in enumerate(_DURATION_BUCKETS):
            if seconds <= upper_bound:
                bucket_counts[index] += 1


def reset_metrics() -> None:
    """Clear in-process metrics; intended for tests and process shutdown."""
    with _lock:
        _requests.clear()
        _duration_sum.clear()
        _duration_count.clear()
        _duration_buckets.clear()


def prometheus_text(
    extra: dict[str, dict[str, int]] | None = None,
    gauges: dict[str, float] | None = None,
) -> str:
    lines = [
        "# HELP meeting_uptime_seconds Process uptime.",
        "# TYPE meeting_uptime_seconds gauge",
        f"meeting_uptime_seconds {time.time() - _started:.1f}",
    ]
    with _lock:
        lines.extend(
            [
                "# HELP meeting_http_requests_total HTTP requests by method, path and status.",
                "# TYPE meeting_http_requests_total counter",
            ]
        )
        for (method, path, status), count in sorted(_requests.items()):
            lines.append(
                f'meeting_http_requests_total{{method="{method}",path="{path}",status="{status}"}} {count}'
            )
        lines.extend(
            [
                "# HELP meeting_http_request_duration_seconds HTTP request duration.",
                "# TYPE meeting_http_request_duration_seconds histogram",
            ]
        )
        for (method, path), total in sorted(_duration_sum.items()):
            count = _duration_count[(method, path)]
            for upper_bound, bucket_count in zip(
                _DURATION_BUCKETS,
                _duration_buckets[(method, path)],
                strict=True,
            ):
                lines.append(
                    "meeting_http_request_duration_seconds_bucket"
                    f'{{method="{method}",path="{path}",le="{upper_bound:g}"}} '
                    f"{bucket_count}"
                )
            lines.append(
                "meeting_http_request_duration_seconds_bucket"
                f'{{method="{method}",path="{path}",le="+Inf"}} {count}'
            )
            lines.append(
                f'meeting_http_request_duration_seconds_sum{{method="{method}",path="{path}"}} {total:.6f}'
            )
            lines.append(
                f'meeting_http_request_duration_seconds_count{{method="{method}",path="{path}"}} {count}'
            )
    for name, values in (extra or {}).items():
        lines.append(f"# TYPE {name} gauge")
        for label, value in values.items():
            lines.append(f'{name}{{status="{label}"}} {value}')
    for name, value in (gauges or {}).items():
        lines.append(f"# TYPE {name} gauge")
        lines.append(f"{name} {value:.6f}")
    return "\n".join(lines) + "\n"


def disk_gauges(path: str | Path) -> dict[str, float]:
    """Capacity gauges for the data volume; omit them if stat fails."""
    try:
        usage = shutil.disk_usage(Path(path))
    except OSError:
        return {}
    total = max(int(usage.total), 1)
    return {
        "meeting_disk_free_bytes": float(usage.free),
        "meeting_disk_used_ratio": float(usage.used) / total,
    }


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)
