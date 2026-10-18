"""服务端 AGC / 谱减降噪的单元测试。"""

from __future__ import annotations

import numpy as np

from imeeting.engine.audio_enhance import (
    AgcProcessor,
    normalize_segment,
    spectral_denoise,
)

SR = 16000


def tone(seconds: float, amp: float, freq: float = 440.0) -> np.ndarray:
    t = np.arange(int(SR * seconds), dtype=np.float32) / SR
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.asarray(x, dtype=np.float32) ** 2)))


def test_agc_boosts_quiet_speech_towards_target():
    agc = AgcProcessor(target_rms=0.05)
    out = None
    for _ in range(12):
        out = agc.process(tone(0.2, 0.008))
    assert out is not None
    assert rms(out) > 0.025, f"AGC 未提升低电平语音: {rms(out):.4f}"
    assert np.max(np.abs(out)) <= 1.0


def test_agc_does_not_amplify_silence():
    agc = AgcProcessor(target_rms=0.05)
    quiet = np.zeros(SR // 5, dtype=np.float32)
    out = agc.process(quiet)
    assert np.allclose(out, 0.0)


def test_segment_normalize_is_bounded():
    out = normalize_segment(tone(1.0, 0.005), target_rms=0.05)
    assert np.max(np.abs(out)) <= 1.0
    assert rms(out) > rms(tone(1.0, 0.005))


def test_spectral_denoise_reduces_broadband_noise():
    rng = np.random.default_rng(7)
    # 间歇语音（0.5s 说话 + 0.5s 静音），更接近真实会议
    clean = np.zeros(SR * 3, dtype=np.float32)
    for start in range(0, clean.size, SR):
        clean[start : start + SR // 2] = tone(0.5, 0.08)
    noisy = clean + rng.normal(0, 0.02, size=clean.size).astype(np.float32)
    out = spectral_denoise(noisy, sr=SR, strength=1.0)

    assert out.shape == noisy.shape
    assert np.isfinite(out).all()

    # 静音区间的底噪应下降
    def silent_rms(sig: np.ndarray) -> float:
        vals = []
        for start in range(0, sig.size, SR):
            vals.append(rms(sig[start + SR // 2 : start + SR]))
        return float(np.mean(vals))

    assert silent_rms(out) < silent_rms(noisy) * 0.7
    # 说话区间不能被整体抹掉
    assert rms(out[: SR // 2]) > 0.02
