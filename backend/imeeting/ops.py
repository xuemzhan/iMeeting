"""轻量 Prometheus 指标与结构化日志支持。"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from collections import defaultdict

_lock = threading.Lock()
_requests: dict[tuple[str, str, str], int] = defaultdict(int)
_duration_sum: dict[tuple[str, str], float] = defaultdict(float)
_duration_count: dict[tuple[str, str], int] = defaultdict(int)
_started = time.time()


def observe_request(method: str, path: str, status: int, seconds: float) -> None:
    # 避免把会议 id、token 等高基数值直接作为指标标签。
    normalized = re.sub(r"/\d+", "/{id}", path)
    normalized = re.sub(r"/share/[^/]+", "/share/{token}", normalized)
    with _lock:
        _requests[(method, normalized, str(status))] += 1
        _duration_sum[(method, normalized)] += seconds
        _duration_count[(method, normalized)] += 1


def prometheus_text(extra: dict[str, dict[str, int]] | None = None) -> str:
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
                "# HELP meeting_http_request_duration_seconds_sum Total request duration.",
                "# TYPE meeting_http_request_duration_seconds_sum counter",
                "# HELP meeting_http_request_duration_seconds_count Request count.",
                "# TYPE meeting_http_request_duration_seconds_count counter",
            ]
        )
        for (method, path), total in sorted(_duration_sum.items()):
            count = _duration_count[(method, path)]
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
    return "\n".join(lines) + "\n"


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
