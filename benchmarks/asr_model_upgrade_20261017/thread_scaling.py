#!/usr/bin/env python3
"""Measure how each model scales with 1/2/4/8 CPU threads on one 26 s clip."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

import psutil
import soundfile as sf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
CLIP = ROOT / "audio" / "synth_long_03.wav"
DONE_RE = re.compile(r"(?:\[done\]|\[sensevoice\] done)\s+([0-9.]+)s")
THREADS = (1, 2, 4, 8)
NANO_BIN = ROOT / "tools" / "gguf" / "bin" / "llama-funasr-cli.exe"
NANO_ENC = ROOT / "tools" / "gguf" / "funasr-encoder-f16.gguf"
NANO_LLM = ROOT / "tools" / "gguf" / "qwen3-0.6b-q5km.gguf"
NANO_VAD = ROOT / "tools" / "gguf" / "fsmn-vad.gguf"


def run_nano(n: int) -> float:
    cmd = [
        str(NANO_BIN),
        "--enc",
        str(NANO_ENC),
        "-m",
        str(NANO_LLM),
        "-a",
        str(CLIP),
        "--vad",
        str(NANO_VAD),
        "--vad-maxseg",
        "30000",
    ]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"
    )
    try:
        psutil.Process(proc.pid).cpu_affinity(list(range(n)))
    except psutil.Error:
        pass
    _, err = proc.communicate()
    match = DONE_RE.search(err)
    if proc.returncode != 0 or not match:
        raise RuntimeError(f"nano {n} threads failed: {err[-1000:]}")
    return float(match.group(1))


def main() -> None:
    from run_bench import MODEL_SPECS, build_model, result_text

    audio, sr = sf.read(str(CLIP), dtype="float32")
    duration = len(audio) / sr
    warm = audio[: sr * 3]
    out = {"clip": CLIP.name, "duration": round(duration, 2), "threads": {}}
    print(f"clip {CLIP.name}: {duration:.1f}s")

    for model_name in ("seaco", "sensevoice", "paraformer"):
        spec = MODEL_SPECS[model_name]
        out["threads"][model_name] = {}
        for n in THREADS:
            model, load_s, _ = build_model(model_name, n, use_vad=True)
            gen_kwargs = dict(spec["gen"])
            result_text(model.generate(input=warm, **gen_kwargs))
            times = []
            for _ in range(2):
                t0 = time.perf_counter()
                result_text(model.generate(input=audio, **gen_kwargs))
                times.append(time.perf_counter() - t0)
            best = min(times)
            out["threads"][model_name][str(n)] = {
                "load_s": round(load_s, 2),
                "compute_s": round(best, 3),
                "rtf": round(best / duration, 4),
                "rtfx": round(duration / best, 2),
            }
            print(
                f"{model_name:11s} threads={n:2d}  load={load_s:5.1f}s  "
                f"compute={best:5.2f}s  RTF={best / duration:.3f}  x{duration / best:.1f}"
            )

    out["threads"]["nano_q5"] = {}
    for n in THREADS:
        times = [run_nano(n) for _ in range(2)]
        best = min(times)
        out["threads"]["nano_q5"][str(n)] = {
            "compute_s": round(best, 3),
            "rtf": round(best / duration, 4),
            "rtfx": round(duration / best, 2),
        }
        print(
            f"{'nano_q5':11s} cores={n:2d}  compute={best:5.2f}s  "
            f"RTF={best / duration:.3f}  x{duration / best:.1f}"
        )

    (ROOT / "results" / "thread_scaling.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("wrote results/thread_scaling.json")


if __name__ == "__main__":
    main()
