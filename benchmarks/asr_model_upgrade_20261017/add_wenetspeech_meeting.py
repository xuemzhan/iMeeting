#!/usr/bin/env python3
"""Add a stratified WenetSpeech test_meeting subset with human references.

Source: HF dataset ``lmms-lab-audio/WenetSpeech``,
``data/test_meeting-00000-of-00001.parquet``. Each row is one manually
transcribed meeting utterance with Opus audio bytes.
"""

from __future__ import annotations

import io
import json
import random
from pathlib import Path

import pyarrow.parquet as pq
import soundfile as sf


ROOT = Path(__file__).resolve().parent
PARQUET = ROOT / "raw" / "wenetspeech" / "test_meeting.parquet"
MANIFEST = ROOT / "manifest.jsonl"
AUDIO_DIR = ROOT / "audio"
SEED = 20261018
SHORT = 15
MEDIUM = 15
LONG = 10
SR = 16000


def main() -> None:
    parquet = pq.ParquetFile(PARQUET)
    candidates: list[dict] = []
    for group_idx in range(parquet.num_row_groups):
        table = parquet.read_row_group(group_idx)
        for row in table.to_pylist():
            begin = float(row.get("begin_time") or 0.0)
            end = float(row.get("end_time") or 0.0)
            duration = end - begin
            text = (row.get("text") or "").strip()
            audio = row.get("audio") or {}
            if not audio.get("bytes") or duration < 1.8 or duration > 30.0:
                continue
            if len(text) < 5 or len(text) > 120:
                continue
            candidates.append(
                {
                    "utt_id": row["utt_id"],
                    "aid": row["aid"],
                    "begin": begin,
                    "end": end,
                    "duration": round(duration, 3),
                    "text": text,
                    "bytes": audio["bytes"],
                }
            )
    rng = random.Random(SEED)
    short = [r for r in candidates if r["duration"] < 5.0]
    medium = [r for r in candidates if 5.0 <= r["duration"] < 10.0]
    long = [r for r in candidates if r["duration"] >= 10.0]
    picked = (
        rng.sample(short, min(SHORT, len(short)))
        + rng.sample(medium, min(MEDIUM, len(medium)))
        + rng.sample(long, min(LONG, len(long)))
    )
    picked.sort(key=lambda r: (r["aid"], r["begin"]))

    items = [
        json.loads(line)
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    items = [i for i in items if i["corpus"] != "wenetspeech_meeting"]
    added = []
    for row in picked:
        audio, sr = sf.read(io.BytesIO(row["bytes"]), dtype="float32")
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        if sr != SR:
            raise RuntimeError(f"{row['utt_id']}: expected 16 kHz, got {sr}")
        key = row["utt_id"]
        dest = AUDIO_DIR / f"{key}.wav"
        sf.write(str(dest), audio, SR, subtype="PCM_16")
        added.append(
            {
                "id": key,
                "corpus": "wenetspeech_meeting",
                "audio": str(dest.relative_to(ROOT)),
                "ref": row["text"],
                "ref_raw": f"{row['aid']} [{row['begin']:.2f}-{row['end']:.2f}]",
                "duration": round(len(audio) / SR, 3),
                "sample_rate": SR,
            }
        )
    items.extend(added)
    order = {
        "aishell1_test": 0,
        "synth_long": 1,
        "wenetspeech_meeting": 2,
        "meeting_pseudo": 3,
        "aishell4_meeting": 4,
    }
    items.sort(key=lambda i: (order.get(i["corpus"], 9), i["id"]))
    with MANIFEST.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    total = sum(i["duration"] for i in added)
    print(
        f"added {len(added)} WenetSpeech meeting utterances "
        f"(short/medium/long = {len(short)}/{len(medium)}/{len(long)} candidates), "
        f"{total / 60:.1f} min"
    )
    for item in added[:8]:
        print(f"  {item['id']} {item['duration']:.1f}s {item['ref']}")


if __name__ == "__main__":
    main()
