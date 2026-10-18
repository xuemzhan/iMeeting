#!/usr/bin/env python3
"""Measure one model's peak RSS in a clean process."""

from __future__ import annotations

import argparse
import time

import psutil
import soundfile as sf

from run_bench import MODEL_SPECS, build_model, result_text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=("seaco", "sensevoice", "paraformer"))
    ap.add_argument("--threads", type=int, default=4)
    args = ap.parse_args()

    audio, sr = sf.read("audio/TEST_MEETING_T0000000001_S00000.wav", dtype="float32")
    model, load_s, rss_load = build_model(args.model, args.threads, use_vad=True)
    gen_kwargs = dict(MODEL_SPECS[args.model]["gen"])
    result_text(model.generate(input=audio[: sr * 3], **gen_kwargs))
    peak = rss_load
    t0 = time.perf_counter()
    result_text(model.generate(input=audio, **gen_kwargs))
    elapsed = time.perf_counter() - t0
    peak = max(peak, psutil.Process().memory_info().rss)
    print(
        f"{args.model}: load={load_s:.1f}s infer={elapsed:.2f}s "
        f"rss_load={rss_load / 1e6:.0f}MB peak={peak / 1e6:.0f}MB"
    )


if __name__ == "__main__":
    main()
