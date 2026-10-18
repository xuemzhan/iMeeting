"""Pure audio analysis tests."""

import numpy as np

from imeeting.engine.audio_analysis import rms, speech_duration_ms, trim_silence


def test_rms_for_silence_and_full_scale_tone() -> None:
    assert rms(np.zeros(160, dtype=np.float32)) == 0.0
    assert rms(np.ones(160, dtype=np.float32)) == 1.0


def test_speech_duration_counts_only_voiced_frames() -> None:
    audio = np.concatenate(
        [
            np.ones(8000, dtype=np.float32),
            np.zeros(8000, dtype=np.float32),
        ]
    )

    assert speech_duration_ms(audio, 0.1) == 500.0


def test_trim_silence_removes_edges_without_touching_content() -> None:
    audio = np.concatenate(
        [
            np.zeros(8000, dtype=np.float32),
            np.ones(16000, dtype=np.float32),
            np.zeros(8000, dtype=np.float32),
        ]
    )

    trimmed = trim_silence(audio, 0.1, margin_ms=0)

    assert len(trimmed) == 16000
    assert np.all(trimmed == 1.0)

