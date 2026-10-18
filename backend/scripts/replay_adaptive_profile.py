"""用真实会议 WAV 回放固定阈值与自适应阈值（只模拟 VAD，不跑 ASR）。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from imeeting.engine.adaptive import AdaptiveMeetingController


def block_energies(path: Path, block_ms: int = 600) -> tuple[np.ndarray, int]:
    audio, sr = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != 16000:
        n = int(len(audio) * 16000 / sr)
        audio = np.interp(
            np.linspace(0, len(audio) - 1, n),
            np.arange(len(audio)),
            audio,
        ).astype(np.float32)
        sr = 16000
    frame = int(sr * block_ms / 1000)
    count = len(audio) // frame
    energies = np.sqrt(
        np.mean(audio[: count * frame].reshape(count, frame) ** 2, axis=1)
    )
    return energies, sr


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wav", required=True)
    parser.add_argument("--fixed-threshold", type=float, default=0.005)
    args = parser.parse_args()

    energies, sr = block_energies(Path(args.wav))
    controller = AdaptiveMeetingController()
    profile = controller.profile
    for energy in energies:
        profile = controller.observe_energy(
            energy,
            in_speech=energy >= profile.start_threshold,
            chunk_seconds=0.6,
        )

    fixed = int(np.count_nonzero(energies >= args.fixed_threshold))
    adaptive_accepted = int(
        np.count_nonzero(energies >= profile.start_threshold)
    )
    result = {
        "seconds": round(len(energies) * 0.6, 1),
        "blocks": int(len(energies)),
        "fixed_threshold": args.fixed_threshold,
        "fixed_accept_ratio": round(fixed / max(1, len(energies)), 4),
        "adaptive_profile": controller.snapshot(),
        "adaptive_accept_ratio_at_final_threshold": round(
            adaptive_accepted / max(1, len(energies)), 4
        ),
        "energy_p10": round(float(np.percentile(energies, 10)), 6),
        "energy_p50": round(float(np.percentile(energies, 50)), 6),
        "energy_p90": round(float(np.percentile(energies, 90)), 6),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
