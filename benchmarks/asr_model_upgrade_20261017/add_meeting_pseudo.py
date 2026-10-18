#!/usr/bin/env python3
"""Add two real meeting excerpts using the current production transcript as a
consistency reference. This is explicitly NOT human ground truth.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import soundfile as sf


ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "manifest.jsonl"
DB = ROOT.parents[1] / "backend" / "data" / "meeting.db"
AUDIO = ROOT.parents[1] / "backend" / "data" / "audio" / "200.wav"
WINDOWS = [(277800, 321000), (103200, 136200)]


def main() -> None:
    items = [
        json.loads(line)
        for line in MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    items = [i for i in items if i["corpus"] != "meeting_pseudo"]
    audio, sr = sf.read(str(AUDIO), dtype="float32")
    if sr != 16000 or audio.ndim != 1:
        raise RuntimeError(f"unexpected audio format: {sr=}, shape={audio.shape}")

    conn = sqlite3.connect(str(DB))
    added = []
    for idx, (start_ms, end_ms) in enumerate(WINDOWS, 1):
        rows = conn.execute(
            "select start_ms, end_ms, text from segment "
            "where meeting_id=200 and is_final=1 and start_ms>=? and end_ms<=? "
            "order by start_ms",
            (start_ms, end_ms),
        ).fetchall()
        if not rows:
            continue
        left = max(0, rows[0][0] * 16)
        right = min(len(audio), rows[-1][1] * 16)
        key = f"meeting200_pseudo_{idx}"
        dest = ROOT / "audio" / f"{key}.wav"
        sf.write(str(dest), audio[left:right], sr, subtype="PCM_16")
        added.append(
            {
                "id": key,
                "corpus": "meeting_pseudo",
                "audio": str(dest.relative_to(ROOT)),
                "ref": "".join(r[2] for r in rows),
                "ref_raw": f"meeting 200 {start_ms}-{end_ms} ms",
                "duration": round((right - left) / sr, 3),
                "sample_rate": sr,
                "quality_flag": "reference_is_current_production_transcript",
            }
        )
    items.extend(added)
    order = {"aishell1_test": 0, "synth_long": 1, "meeting_pseudo": 2, "aishell4_meeting": 3}
    items.sort(key=lambda i: (order.get(i["corpus"], 9), i["id"]))
    with MANIFEST.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    for item in added:
        print(f"{item['id']}: {item['duration']:.1f}s, {len(item['ref'])} chars")
        print(f"  {item['ref']}")


if __name__ == "__main__":
    main()
