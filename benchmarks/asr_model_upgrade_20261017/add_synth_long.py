#!/usr/bin/env python3
"""Add controlled 20-30 s continuous-speech clips to the benchmark manifest.

The AISHELL-4 excerpts cannot be used as CER gold: their timestamped transcript
is not time-aligned with the public mono audio. To test the app's real
long-segment path without relying on that transcript, concatenate short
AISHELL-1 utterances whose text is known and verified.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "manifest.jsonl"
AUDIO_DIR = ROOT / "audio"
SR = 16000
GAP_SECONDS = 0.12
GROUP_SIZE = 4


def main() -> None:
    items = [
        json.loads(line)
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    items = [i for i in items if i["corpus"] != "synth_long"]
    for item in items:
        if item["id"] == "aishell1_0003":
            item["quality_flag"] = "reference_has_text_absent_from_audio_tail"

    clean = [
        i
        for i in items
        if i["corpus"] == "aishell1_test" and not i.get("quality_flag")
    ]
    gap = np.zeros(int(SR * GAP_SECONDS), dtype=np.float32)
    synth = []
    for group_idx in range(0, len(clean), GROUP_SIZE):
        group = clean[group_idx : group_idx + GROUP_SIZE]
        if len(group) < 3:
            continue
        chunks = []
        for item in group:
            audio, sr = sf.read(str(ROOT / item["audio"]), dtype="float32")
            if sr != SR or audio.ndim != 1:
                raise RuntimeError(f"unexpected audio format for {item['id']}")
            chunks.extend([audio, gap])
        audio = np.concatenate(chunks[:-1])
        key = f"synth_long_{group_idx // GROUP_SIZE + 1:02d}"
        dest = AUDIO_DIR / f"{key}.wav"
        sf.write(str(dest), audio, SR, subtype="PCM_16")
        synth.append(
            {
                "id": key,
                "corpus": "synth_long",
                "audio": str(dest.relative_to(ROOT)),
                "ref": "".join(item["ref"] for item in group),
                "ref_raw": " + ".join(item["id"] for item in group),
                "duration": round(len(audio) / SR, 3),
                "sample_rate": SR,
            }
        )

    items.extend(synth)
    order = {"aishell1_test": 0, "synth_long": 1, "aishell4_meeting": 2}
    items.sort(key=lambda i: (order.get(i["corpus"], 9), i["id"]))
    with MANIFEST.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    total = sum(i["duration"] for i in items)
    print(
        f"added {len(synth)} synthetic long clips; "
        f"manifest now {len(items)} items / {total / 60:.1f} min"
    )
    for item in synth:
        print(f"  {item['id']}: {item['duration']:.1f}s, {len(item['ref'])} chars")


if __name__ == "__main__":
    main()
