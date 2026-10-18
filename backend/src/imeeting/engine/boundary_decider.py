"""Stateful boundary decisions for energy-driven streaming segmentation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BoundaryDecision:
    reason: str | None
    started_speech: bool


class BoundaryDecider:
    """Track speech/silence state and decide when the current segment ends."""

    def __init__(self) -> None:
        self.in_speech = False
        self.silence_samples = 0
        self.over_max_ms = 0.0

    def reset(self) -> None:
        self.in_speech = False
        self.silence_samples = 0
        self.over_max_ms = 0.0

    def observe(
        self,
        energy: float,
        *,
        chunk_seconds: float,
        segment_duration_ms: int,
        start_threshold: float,
        continue_threshold: float,
        silence_limit: int,
        short_merge_ms: int,
        soft_segment_ms: int,
        short_silence_samples: int,
        max_segment_ms: int,
        noise_floor: float,
        sample_rate: int = 16000,
    ) -> BoundaryDecision:
        started_speech = False
        if energy >= start_threshold:
            if not self.in_speech:
                started_speech = True
            self.in_speech = True

        threshold = continue_threshold if self.in_speech else start_threshold
        if energy < threshold:
            self.silence_samples += int(chunk_seconds * sample_rate)
        else:
            self.silence_samples = 0

        effective_silence = silence_limit
        if short_merge_ms > 0 and segment_duration_ms < short_merge_ms:
            effective_silence = max(effective_silence, int(1.5 * sample_rate))
        if self.silence_samples >= effective_silence:
            return BoundaryDecision("silence", started_speech)

        if (
            soft_segment_ms > 0
            and segment_duration_ms >= soft_segment_ms
            and self.silence_samples >= short_silence_samples
        ):
            return BoundaryDecision("adaptive_silence", started_speech)

        if segment_duration_ms >= max_segment_ms:
            self.over_max_ms += chunk_seconds * 1000
            local_valley = energy < max(
                continue_threshold * 1.35,
                noise_floor * 1.5,
            )
            if local_valley or self.over_max_ms >= 1500:
                reason = "max_duration_valley" if local_valley else "max_duration"
                return BoundaryDecision(reason, started_speech)

        return BoundaryDecision(None, started_speech)

