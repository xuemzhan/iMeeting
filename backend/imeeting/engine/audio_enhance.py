"""可选的服务端音频增强：AGC 与谱减降噪。

默认关闭。开启后：
- 流式路径：只做 AGC（逐块平滑增益），不改 VAD 的原始能量判据；
- 离线定稿：先做谱减降噪（含 80Hz 以下高通），再做整段 AGC。

所有处理都保持 16kHz 单声道 float32、长度不变。
"""

from __future__ import annotations

import numpy as np

SAMPLE_RATE = 16000


class AgcProcessor:
    """逐块平滑增益，避免把静音段一起放大。"""

    def __init__(
        self,
        target_rms: float = 0.05,
        max_gain: float = 8.0,
        min_gain: float = 0.7,
    ) -> None:
        self.target_rms = max(0.005, float(target_rms))
        self.max_gain = max(1.0, float(max_gain))
        self.min_gain = max(0.1, float(min_gain))
        self.gain = 1.0

    def process(self, pcm: np.ndarray, noise_floor: float = 0.0) -> np.ndarray:
        if pcm.size == 0:
            return pcm
        rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))
        # 静音/纯底噪不放大，否则 AGC 会把空调声拉成“人声”
        if rms < max(noise_floor * 1.2, 1e-4):
            return pcm
        desired = float(
            np.clip(self.target_rms / (rms + 1e-9), self.min_gain, self.max_gain)
        )
        alpha = 0.25 if desired > self.gain else 0.08
        self.gain = (1.0 - alpha) * self.gain + alpha * desired
        return np.clip(pcm.astype(np.float32) * self.gain, -1.0, 1.0)


def normalize_segment(
    pcm: np.ndarray, target_rms: float = 0.05, max_gain: float = 8.0
) -> np.ndarray:
    """整段归一化（离线定稿用），不做逐块平滑。"""
    if pcm.size == 0:
        return pcm
    rms = float(np.sqrt(np.mean(pcm.astype(np.float32) ** 2)))
    if rms < 1e-5:
        return pcm
    gain = float(np.clip(target_rms / (rms + 1e-9), 0.7, max_gain))
    return np.clip(pcm.astype(np.float32) * gain, -1.0, 1.0)


def spectral_denoise(
    pcm: np.ndarray,
    *,
    sr: int = SAMPLE_RATE,
    strength: float = 1.0,
    n_fft: int = 512,
    hop: int = 128,
) -> np.ndarray:
    """轻量谱减：按频点估计 20 分位噪声谱，再做软门限。

    不做神经网络降噪，CPU 开销低、可解释；对风扇/空调这类稳态噪声有效。
    强语音段会保留，最多衰减到 8%，避免过度“水下音”。
    """
    audio = np.asarray(pcm, dtype=np.float32).reshape(-1)
    if audio.size < n_fft * 2:
        return audio
    strength = float(max(0.0, min(3.0, strength)))
    window = np.hanning(n_fft).astype(np.float32)
    # 两端反射填充，避免边界窗口权重接近 0 时把残差放大
    pad = n_fft
    padded = np.pad(audio, (pad, pad), mode="reflect")
    frames = np.lib.stride_tricks.sliding_window_view(padded, n_fft)[::hop]
    if frames.shape[0] == 0:
        return audio
    spec = np.fft.rfft(frames * window, axis=1)
    mag = np.abs(spec)
    noise = np.percentile(mag, 20, axis=0)
    gain = np.clip((mag - strength * noise) / (mag + 1e-9), 0.08, 1.0)
    # 时间方向平滑，避免逐帧增益跳变引入“鸟鸣/水声”类伪影
    if gain.shape[0] >= 3:
        gain_padded = np.pad(gain, ((1, 1), (0, 0)), mode="edge")
        gain = (gain_padded[:-2] + gain_padded[1:-1] + gain_padded[2:]) / 3.0

    # 80Hz 以下（交流声/桌面震动）额外压掉
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sr)
    gain[:, freqs < 80.0] *= 0.2

    clean = np.fft.irfft(spec * gain, n=n_fft, axis=1) * window
    out = np.zeros_like(padded)
    weight = np.zeros_like(padded)
    for i in range(frames.shape[0]):
        start = i * hop
        out[start : start + n_fft] += clean[i]
        weight[start : start + n_fft] += window**2
    out /= np.maximum(weight, 1e-6)
    out = np.clip(out[pad : pad + audio.size], -1.0, 1.0)
    return out.astype(np.float32)
