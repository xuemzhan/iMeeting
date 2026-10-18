"""Segment data and streaming text accumulation."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

SAMPLE_RATE = 16000


def merge_delta(acc: str, delta: str) -> str:
    """把流式模型的一块输出并入已累积草稿。

    Paraformer-streaming 的 ``generate(is_final=False)`` 每次只返回**本块新增**
    的文本（增量）。但也存在两种需要兼容的情况：

    1. 模型偶发返回整段累积文本（部分实现/版本会这样）；
    2. 因编码器 look-back 重叠，返回的增量开头与已累积内容尾部重复。

    因此这里做三步归一：完全重复 → 不追加；前缀重复 → 只取新增部分；
    其余情况直接拼接。
    """
    delta = (delta or "").strip()
    if not delta:
        return acc
    if not acc:
        return delta
    # 情况 1：模型这次返回的是从头开始的累积全文，直接替换更可靠
    if delta.startswith(acc):
        return delta
    # 情况 2：增量与已累积尾部有重叠，取最长重叠后截断
    max_overlap = min(len(acc), len(delta))
    for k in range(max_overlap, 0, -1):
        if acc.endswith(delta[:k]):
            return acc + delta[k:]
    # 情况 3：无重叠，正常追加
    return acc + delta


@dataclass
class SegmentState:
    """一句话的状态。"""

    segment_id: str
    buffer: list[np.ndarray] = field(default_factory=list)
    text: str = ""
    is_final: bool = False
    start_ms: int = 0
    end_ms: int = 0
    speaker: str | None = None
    speaker_key: str | None = None
    speaker_score: float = 0.0
    speaker_source: str = ""
    asr_source: str = ""
    asr_error: str | None = None
    speech_ms: int = 0
    audio_rms: float = 0.0
    noise_floor: float = 0.0
    snr_db: float = 0.0
    adaptive_gain: float = 1.0
    profile_version: int = 0
    seq: int = 0
    partial_sent: bool = False
    #: 流式模型返回的是**增量文本**（每个 chunk 只给这一块新增的字），
    #: 必须按序累积才是完整草稿。这里保存已累积的草稿全文，
    #: 与 ``text`` 分离：``text`` 在定稿时会被离线结果覆盖。
    streaming_text: str = ""
    #: 断句原因：silence / max_duration / user / stop（指标用）
    finalize_reason: str = ""
    #: 定稿实际发生的单调时钟；用于计算端到端定稿延迟
    end_perf: float = 0.0
    offline_ms: float = 0.0
    speaker_ms: float = 0.0
    #: 离线模型没给出文本、降级用流式草稿封定稿
    fallback: bool = False
    #: 本段是否真正进入过语音（能量越过起始阈值）
    had_speech: bool = False

    def add_audio(self, chunk: np.ndarray) -> None:
        self.buffer.append(chunk)

    def take_audio(self) -> np.ndarray:
        if not self.buffer:
            return np.zeros(0, dtype=np.float32)
        merged = np.concatenate(self.buffer)
        self.buffer.clear()
        return merged

    @property
    def duration_ms(self) -> int:
        total = sum(len(c) for c in self.buffer)
        return int(total / SAMPLE_RATE * 1000)
