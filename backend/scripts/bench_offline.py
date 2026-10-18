"""离线 ASR 性能与质量测量。

用法::

    python scripts/bench_offline.py [--wav PATH] [--ncpu N]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[3]  # repo root (scripts → backend → iMeeting → FunASR-main)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default=str(ROOT / "runtime/funasr_api/asr_example.wav"))
    ap.add_argument("--ncpu", type=int, default=4)
    ap.add_argument("--out", default=str(ROOT / "iMeeting/backend/.bench-offline.json"))
    args = ap.parse_args()

    torch.set_num_threads(args.ncpu)

    import soundfile as sf

    from funasr import AutoModel

    t0 = time.time()
    model = AutoModel(
        model="paraformer-zh",
        vad_model="fsmn-vad",
        punc_model="ct-punc",
        device="cpu",
        ncpu=args.ncpu,
        disable_update=True,
        disable_pbar=True,
    )
    load_s = time.time() - t0

    data, sr = sf.read(args.wav, dtype="float32")
    duration_s = len(data) / sr

    t1 = time.time()
    res = model.generate(input=args.wav, batch_size_s=300)
    infer_s = time.time() - t1

    payload = {
        "wav": args.wav,
        "sr": sr,
        "duration_s": round(duration_s, 2),
        "load_s": round(load_s, 1),
        "infer_s": round(infer_s, 2),
        "rtf": round(infer_s / duration_s, 3),
        "results": [],
    }
    for r in res:
        item = {"text": r.get("text", "")}
        si = r.get("sentence_info")
        if si:
            item["sentences"] = [
                {"start": s.get("start"), "end": s.get("end"), "text": s.get("text")} for s in si
            ]
        payload["results"].append(item)

    Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("BENCH_DONE " + args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
