"""Shared transcript value objects for minute generation."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TranscriptLine:
    start_ms: int
    speaker: str | None
    text: str
    #: 片段结束时间；用于「短停顿合并」判断。旧调用方可以不给。
    end_ms: int | None = None
    segment_id: str = ""
    segment_ids: list[str] = field(default_factory=list)

