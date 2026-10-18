#!/usr/bin/env python3
"""Create 10 dB and 5 dB white-noise variants of 12 clean AISHELL-1 clips."""

from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "manifest.jsonl"
AUDIO_DIR = ROOT / "audio"
SEED = 20261020
N = 12
SNRS = (10, 5)


def add_noise(audio: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    signal_rms = float(np.sqrt(np.mean(audio**2)))
    noise_rms = signal_rms / (10 ** (snr_db / 20))
    noise = rng.normal(0.0, noise_rms, len(audio)).astype(np.float32)
    mixed = audio + noise
    peak = float(np.max(np.abs(mixed)))
    if peak > 0.99:
        mixed = mixed / peak * 0.99
    return mixed.astype(np.float32)


def main() -> None:
    items = [
        json.loads(line)
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    items = [i for i in items if not i["corpus"].startswith("noise_snr")]
    clean = [i for i in items if i["corpus"] == "aishell1_test" and not i.get("quality_flag")]
    picked = clean[:N]
    added = []
    for snr in SNRS:
        rng = np.random.default_rng(SEED + snr)
        for item in picked:
            audio, sr = sf.read(str(ROOT / item["audio"]), dtype="float32")
            mixed = add_noise(audio, snr, rng)
            key = f"{item['id']}_snr{snr:02d}"
            dest = AUDIO_DIR / f"{key}.wav"
            sf.write(str(dest), mixed, sr, subtype="PCM_16")
            added.append(
                {
                    "id": key,
                    "corpus": f"noise_snr{snr:02d}",
                    "audio": str(dest.relative_to(ROOT)),
                    "ref": item["ref"],
                    "ref_raw": item.get("ref_raw", ""),
                    "duration": round(len(mixed) / sr, 3),
                    "sample_rate": sr,
                }
            )
    items.extend(added)
    order = {
        "aishell1_test": 0,
        "synth_long": 1,
        "wenetspeech_meeting": 2,
        "meeting_pseudo": 3,
        "aishell4_meeting": 4,
        "noise_snr10": 5,
        "noise_snr05": 6,
    }
    items.sort(key=lambda i: (order.get(i["corpus"], 9), i["id"]))
    with MANIFEST.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"added {len(added)} noisy clips: {len(picked)} clean x {len(SNRS)} SNRs")


if __name__ == "__main__":
    main()
