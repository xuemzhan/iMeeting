"""Pure audio energy calculations used by the streaming session."""

from __future__ import annotations

import numpy as np


def rms(pcm: np.ndarray) -> float:
    if len(pcm) == 0:
        return 0.0
    return float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))


def is_silent(audio: np.ndarray, threshold: float) -> bool:
    if len(audio) == 0:
        return True
    return rms(audio) < threshold


def speech_duration_ms(
    audio: np.ndarray,
    threshold: float,
    *,
    sample_rate: int = 16000,
    frame_ms: int = 20,
) -> float:
    if len(audio) == 0:
        return 0.0
    frame = int(sample_rate * frame_ms / 1000)
    total_frames = len(audio) // frame
    if total_frames == 0:
        return len(audio) / sample_rate * 1000 if not is_silent(audio, threshold) else 0.0
    frames = audio[: total_frames * frame].reshape(total_frames, frame)
    frame_rms = np.sqrt(np.mean(frames.astype(np.float32) ** 2, axis=1))
    voiced = int(np.count_nonzero(frame_rms >= threshold))
    return voiced * float(frame_ms)


def trim_silence(
    audio: np.ndarray,
    threshold: float,
    *,
    margin_ms: int,
    sample_rate: int = 16000,
    frame_ms: int = 20,
) -> np.ndarray:
    if len(audio) == 0:
        return audio
    frame = int(sample_rate * frame_ms / 1000)
    total = len(audio) // frame
    if total < 8:
        return audio
    frames = audio[: total * frame].reshape(total, frame).astype(np.float32)
    energy = np.sqrt(np.mean(frames**2, axis=1))
    voiced = np.flatnonzero(energy >= threshold)
    if voiced.size == 0:
        return audio
    margin = int(margin_ms / 1000 * sample_rate)
    start = max(0, int(voiced[0]) * frame - margin)
    end = min(len(audio), (int(voiced[-1]) + 1) * frame + margin)
    if end - start >= len(audio) * 0.9:
        return audio
    return np.ascontiguousarray(audio[start:end])

