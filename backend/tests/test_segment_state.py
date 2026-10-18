"""Segment state and streaming text merge tests."""

import numpy as np

from imeeting.engine import streaming
from imeeting.engine.segment_state import SAMPLE_RATE, SegmentState, merge_delta


def test_streaming_module_keeps_compatibility_exports() -> None:
    assert streaming.SAMPLE_RATE == SAMPLE_RATE
    assert streaming.SegmentState is SegmentState
    assert streaming._merge_delta is merge_delta


def test_merge_delta_keeps_acc_for_empty_delta() -> None:
    assert merge_delta("已有文本", "") == "已有文本"
    assert merge_delta("已有文本", "   ") == "已有文本"


def test_merge_delta_accepts_first_text_and_cumulative_text() -> None:
    assert merge_delta("", " 第一块 ") == "第一块"
    assert merge_delta("第一块", "第一块第二块") == "第一块第二块"


def test_merge_delta_removes_longest_overlap() -> None:
    assert merge_delta("欢迎大", "大家") == "欢迎大家"


def test_merge_delta_appends_non_overlapping_text() -> None:
    assert merge_delta("欢迎", "参会") == "欢迎参会"


def test_segment_state_takes_and_clears_buffered_audio() -> None:
    segment = SegmentState(segment_id="seg-1")
    segment.add_audio(np.zeros(SAMPLE_RATE, dtype=np.float32))
    segment.add_audio(np.zeros(SAMPLE_RATE // 2, dtype=np.float32))

    assert segment.duration_ms == 1500
    audio = segment.take_audio()

    assert len(audio) == SAMPLE_RATE * 3 // 2
    assert segment.buffer == []
    assert len(segment.take_audio()) == 0
