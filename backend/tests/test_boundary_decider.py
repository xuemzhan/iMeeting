"""Boundary decision tests for streaming segmentation."""

from imeeting.engine.boundary_decider import BoundaryDecider


def _observe(
    decider: BoundaryDecider,
    energy: float,
    *,
    duration_ms: int,
    chunk_seconds: float = 0.5,
    silence_limit: int = 16000,
    soft_segment_ms: int = 3000,
    short_silence_samples: int = 4000,
    max_segment_ms: int = 30000,
):
    return decider.observe(
        energy,
        chunk_seconds=chunk_seconds,
        segment_duration_ms=duration_ms,
        start_threshold=0.01,
        continue_threshold=0.006,
        silence_limit=silence_limit,
        short_merge_ms=0,
        soft_segment_ms=soft_segment_ms,
        short_silence_samples=short_silence_samples,
        max_segment_ms=max_segment_ms,
        noise_floor=0.002,
    )


def test_silence_boundary_after_speech() -> None:
    decider = BoundaryDecider()
    assert _observe(decider, 0.02, duration_ms=500).reason is None

    start = _observe(decider, 0.0, duration_ms=1000, chunk_seconds=0.25)
    end = _observe(decider, 0.0, duration_ms=1250, chunk_seconds=0.75)

    assert start.reason is None
    assert end.reason == "silence"


def test_soft_boundary_uses_short_pause() -> None:
    decider = BoundaryDecider()
    _observe(decider, 0.02, duration_ms=4000)

    decision = _observe(decider, 0.0, duration_ms=4200, chunk_seconds=0.3)

    assert decision.reason == "adaptive_silence"


def test_max_duration_prefers_local_valley() -> None:
    decider = BoundaryDecider()

    decision = _observe(
        decider,
        0.007,
        duration_ms=30000,
        soft_segment_ms=0,
    )

    assert decision.reason == "max_duration_valley"


def test_max_duration_forces_after_continuous_speech() -> None:
    decider = BoundaryDecider()
    reasons = [
        _observe(
            decider,
            0.02,
            duration_ms=30000,
            soft_segment_ms=0,
        ).reason,
        _observe(
            decider,
            0.02,
            duration_ms=30500,
            soft_segment_ms=0,
        ).reason,
        _observe(
            decider,
            0.02,
            duration_ms=31000,
            soft_segment_ms=0,
        ).reason,
    ]

    assert reasons[-1] == "max_duration"

