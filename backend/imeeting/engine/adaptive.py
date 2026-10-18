"""会议级自适应音频与切分控制器。

目标不是追求一套“最优全局参数”，而是让每场会议从自身噪声、语音电平、
片段长度和停顿分布中学习。所有更新有上下界、迟滞和速率限制。
"""

from __future__ import annotations

import json
import statistics
from collections import deque
from dataclasses import asdict, dataclass

import numpy as np

from ..settings import get_config


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


@dataclass
class AdaptiveProfile:
    version: int = 1
    start_threshold: float = 0.008
    continue_threshold: float = 0.004
    silence_ms: int = 800
    soft_segment_ms: int = 12000
    max_segment_ms: int = 20000
    gain: float = 1.0
    noise_floor: float = 0.0
    speech_level: float = 0.0
    snr_db: float = 0.0
    updated_blocks: int = 0


class AdaptiveMeetingController:
    def __init__(self, initial: dict | None = None) -> None:
        enabled = bool(get_config("adaptive.enabled"))
        self.enabled = enabled
        self.profile = AdaptiveProfile(
            silence_ms=int(get_config("vad.silence_ms") or 800),
            soft_segment_ms=int(
                min(
                    int(get_config("adaptive.initial_soft_segment_ms") or 12000),
                    int(get_config("vad.soft_segment_ms") or 12000),
                )
            ),
            max_segment_ms=int(
                min(
                    int(get_config("adaptive.initial_max_segment_ms") or 20000),
                    int(get_config("vad.max_segment_ms") or 20000),
                )
            ),
        )
        if initial:
            for key, value in initial.items():
                if hasattr(self.profile, key):
                    try:
                        setattr(self.profile, key, type(getattr(self.profile, key))(value))
                    except (TypeError, ValueError):
                        pass
            self.profile.version = max(
                int(initial.get("version") or 0) + 1, self.profile.version
            )
        self._energy: deque[float] = deque(maxlen=100)
        self._segments: deque[int] = deque(maxlen=30)
        self._blocks = 0
        self._last_update_block = 0

    def observe_energy(
        self, energy: float, *, in_speech: bool, chunk_seconds: float
    ) -> AdaptiveProfile:
        if not self.enabled or energy <= 0:
            return self.profile
        self._energy.append(energy)
        self._blocks += 1
        if (
            len(self._energy) < 20
            or self._blocks - self._last_update_block
            < max(5, int(float(get_config("adaptive.update_seconds") or 15)))
        ):
            return self.profile
        self._last_update_block = self._blocks

        noise = float(np.percentile(self._energy, 15))
        speech = float(np.percentile(self._energy, 85))
        snr = 20 * np.log10((speech + 1e-9) / (max(noise, 1e-6)))
        dynamic = speech / max(noise, 1e-6)

        # 远场轻声：提升用于 VAD/ASR 的副本，纯噪声不放大。
        target_rms = float(get_config("adaptive.target_rms") or 0.035)
        max_gain = float(get_config("adaptive.gain_max") or 4.0)
        if speech < target_rms and snr >= 3.0:
            gain_target = _clamp(target_rms / max(speech, 0.0015), 1.0, max_gain)
        else:
            gain_target = 1.0
        # 平滑且限制单次变化，避免呼吸式抖动。
        self.profile.gain = round(
            _clamp(0.75 * self.profile.gain + 0.25 * gain_target, 1.0, max_gain),
            3,
        )

        if dynamic >= 1.7:
            start_target = _clamp(
                max(noise * 2.0, speech * 0.30),
                0.0015,
                0.03,
            )
            continue_target = _clamp(
                max(noise * 1.35, speech * 0.20), 0.0012, start_target * 0.85
            )
            self.profile.start_threshold = round(
                _clamp(
                    0.7 * self.profile.start_threshold + 0.3 * start_target,
                    0.0015,
                    0.03,
                ),
                6,
            )
            self.profile.continue_threshold = round(
                _clamp(
                    0.7 * self.profile.continue_threshold
                    + 0.3 * continue_target,
                    0.0012,
                    self.profile.start_threshold * 0.9,
                ),
                6,
            )

        # 即使窗口里只有稳态噪声，也不能让阈值低于噪声的合理倍数。
        noise_start = _clamp(noise * 2.2, 0.0015, 0.03)
        if noise_start > self.profile.start_threshold:
            self.profile.start_threshold = round(
                min(noise_start, self.profile.start_threshold * 1.25), 6
            )
            self.profile.continue_threshold = min(
                self.profile.continue_threshold,
                self.profile.start_threshold * 0.85,
            )

        if self._segments:
            durations = list(self._segments)
            median_d = statistics.median(durations)
            long_ratio = sum(d > 18000 for d in durations) / len(durations)
            if median_d > 12000 or long_ratio > 0.15:
                self.profile.soft_segment_ms = max(
                    7000, min(self.profile.soft_segment_ms, 12000)
                )
                self.profile.max_segment_ms = max(
                    self.profile.soft_segment_ms + 4000,
                    min(self.profile.max_segment_ms, 18000),
                )
            elif median_d < 2500 and long_ratio == 0:
                self.profile.soft_segment_ms = min(
                    16000, self.profile.soft_segment_ms + 500
                )

        # 噪声很高时更依赖相对阈值，不把停顿判得过短。
        if snr < 5.0:
            self.profile.silence_ms = min(1200, self.profile.silence_ms + 50)
        elif snr > 15.0:
            self.profile.silence_ms = max(650, self.profile.silence_ms - 20)

        self.profile.noise_floor = round(noise, 6)
        self.profile.speech_level = round(speech, 6)
        self.profile.snr_db = round(float(snr), 2)
        self.profile.updated_blocks = self._blocks
        self.profile.version += 1
        return self.profile

    def observe_segment(self, duration_ms: int) -> None:
        if duration_ms > 0:
            self._segments.append(duration_ms)

    def snapshot(self) -> dict:
        return asdict(self.profile)

    def dump(self) -> str:
        return json.dumps(self.snapshot(), ensure_ascii=False)
