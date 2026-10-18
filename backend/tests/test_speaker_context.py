"""Speaker context fallback tests."""

from imeeting.engine.speaker_context import SpeakerContext


def test_context_uses_consistent_recent_speaker() -> None:
    context = SpeakerContext()
    context.record("张三", "vp_1", 1000)
    context.record("张三", "vp_1", 2000)
    context.record("张三", "vp_1", 3000)

    result = context.resolve(3500, max_gap_ms=5000, source="context_short")

    assert result is not None
    assert result.label == "张三"
    assert result.key == "vp_1"
    assert result.source == "context_short"


def test_context_rejects_stale_history() -> None:
    context = SpeakerContext()
    context.record("张三", "vp_1", 1000)

    assert context.resolve(9000, max_gap_ms=5000, source="context_short") is None


def test_context_rejects_ambiguous_history() -> None:
    context = SpeakerContext()
    context.record("张三", "vp_1", 1000)
    context.record("李四", "vp_2", 2000)
    context.record("李四", "vp_2", 3000)

    assert context.resolve(3500, max_gap_ms=5000, source="context_short") is None

