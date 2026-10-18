#!/usr/bin/env python3
"""Run a CPU-only ASR model comparison on the prepared manifest.

Scope: offline ASR core, one utterance per call, no VAD / punctuation / speaker
sub-models. That isolates the accuracy and compute cost of the acoustic model,
which is what this upgrade decision is about. The live app's streaming path and
its CAM++ speaker path are unchanged by this benchmark.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import psutil
import soundfile as sf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "manifest.jsonl"
RESULTS = ROOT / "results"


def normalize_zh(text: str) -> str:
    import re

    text = re.sub(r"<\|[^|]*\|>", "", text or "")
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text).upper()


def strip_fillers(text: str) -> str:
    import re

    return re.sub(r"[嗯呃啊哦唉]", "", text)


def edit_distance(ref: str, hyp: str) -> int:
    r, h = list(ref), list(hyp)
    if not r:
        return len(h)
    d = np.arange(len(h) + 1)
    for i in range(1, len(r) + 1):
        prev = d[0]
        d[0] = i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return int(d[len(h)])


def load_manifest(path: Path) -> list[dict]:
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            item = json.loads(line)
            item["audio_path"] = str((ROOT / item["audio"]).resolve())
            items.append(item)
    return items


class PeakRSS:
    def __init__(self) -> None:
        self.peak = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        proc = psutil.Process()
        while not self._stop.is_set():
            self.peak = max(self.peak, proc.memory_info().rss)
            self._stop.wait(0.2)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=2)


MODEL_SPECS: dict[str, dict[str, Any]] = {
    "seaco": {
        "label": "SeACo-Paraformer-Large（当前离线）",
        "init": {
            "model": "iic/speech_seaco_paraformer_large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        },
        "gen": {"batch_size_s": 200, "use_itn": False},
    },
    "paraformer": {
        "label": "Paraformer-Large（同代对照）",
        "init": {
            "model": "iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
        },
        "gen": {"batch_size_s": 200, "use_itn": False},
    },
    "sensevoice": {
        "label": "SenseVoiceSmall（2024，234M）",
        "init": {"model": "iic/SenseVoiceSmall"},
        "gen": {"language": "zh", "use_itn": False, "batch_size": 1},
    },
    "nano": {
        "label": "Fun-ASR-Nano-2512（0.8B LLM-ASR）",
        "init": {"model": "FunAudioLLM/Fun-ASR-Nano-2512"},
        "gen": {"language": "中文", "itn": False, "batch_size": 1},
    },
}


def build_model(name: str, threads: int, use_vad: bool):
    from funasr import AutoModel

    spec = MODEL_SPECS[name]
    kwargs = dict(spec["init"])
    kwargs.update(
        device="cpu",
        ncpu=threads,
        disable_update=True,
        disable_pbar=True,
        hub="ms",
    )
    if use_vad:
        kwargs.update(
            vad_model="fsmn-vad",
            vad_kwargs={"max_single_segment_time": 30000},
        )
    t0 = time.perf_counter()
    model = AutoModel(**kwargs)
    load_s = time.perf_counter() - t0
    rss = psutil.Process().memory_info().rss
    return model, load_s, rss


def result_text(res: Any) -> str:
    if isinstance(res, dict):
        return str(res.get("text", "") or "")
    if isinstance(res, list) and res:
        first = res[0]
        if isinstance(first, dict):
            return str(first.get("text", "") or "")
    return ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="seaco,sensevoice")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--corpus", default="", help="only run one manifest corpus")
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--out-dir", default=str(RESULTS))
    ap.add_argument("--skip-warmup", action="store_true")
    ap.add_argument(
        "--vad",
        action="store_true",
        help="segment long audio with FSMN-VAD before ASR (app-like final path)",
    )
    args = ap.parse_args()

    os.environ.setdefault("OMP_NUM_THREADS", str(args.threads))
    import torch

    torch.set_num_threads(args.threads)
    try:
        torch.set_num_interop_threads(1)
    except RuntimeError:
        pass

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    items = load_manifest(Path(args.manifest))
    if args.corpus:
        items = [i for i in items if i["corpus"] == args.corpus]
    if args.limit:
        items = items[: args.limit]

    selected = [m.strip() for m in args.models.split(",") if m.strip()]
    for name in selected:
        if name not in MODEL_SPECS:
            raise SystemExit(f"unknown model {name!r}; choose from {sorted(MODEL_SPECS)}")

    all_summaries = []
    for name in selected:
        spec = MODEL_SPECS[name]
        print(f"\n=== {spec['label']} ===")
        print(f"loading with {args.threads} threads ...")
        model, load_s, rss_after_load = build_model(name, args.threads, args.vad)
        print(
            f"loaded in {load_s:.1f}s, RSS {rss_after_load / 1e6:.0f} MB; "
            f"warmup={'no' if args.skip_warmup else 'yes'}"
        )

        gen_kwargs = dict(spec["gen"])

        def infer(audio: np.ndarray) -> str:
            return result_text(model.generate(input=audio, **gen_kwargs))

        if not args.skip_warmup and items:
            warm_audio, _ = sf.read(items[0]["audio_path"], dtype="float32")
            t0 = time.perf_counter()
            infer(warm_audio[: len(warm_audio) // 2])
            print(f"warmup inference: {time.perf_counter() - t0:.2f}s")

        rows = []
        peak = 0
        with PeakRSS() as meter:
            for idx, item in enumerate(items, 1):
                audio, sr = sf.read(item["audio_path"], dtype="float32", always_2d=False)
                if audio.ndim > 1:
                    audio = audio.mean(axis=1)
                if sr != 16000:
                    raise RuntimeError(f"{item['id']}: expected 16 kHz, got {sr}")
                t0 = time.perf_counter()
                raw = infer(audio)
                elapsed = time.perf_counter() - t0
                ref_norm = normalize_zh(item["ref"])
                hyp_norm = normalize_zh(raw)
                row = {
                    "id": item["id"],
                    "corpus": item["corpus"],
                    "duration": item["duration"],
                    "ref": ref_norm,
                    "raw": raw,
                    "hyp": hyp_norm,
                    "edits": edit_distance(ref_norm, hyp_norm),
                    "ref_chars": len(ref_norm),
                    "infer_s": round(elapsed, 4),
                    "rtf": round(elapsed / max(item["duration"], 1e-9), 4),
                    "cer": round(edit_distance(ref_norm, hyp_norm) / max(len(ref_norm), 1) * 100, 2),
                }
                rows.append(row)
                peak = max(peak, psutil.Process().memory_info().rss)
                print(
                    f"[{idx:02d}/{len(items)}] {item['id']:<18} "
                    f"{elapsed:6.2f}s  RTF {row['rtf']:.3f}  CER {row['cer']:5.1f}%"
                )
                print(f"     ref: {ref_norm}")
                print(f"     hyp: {hyp_norm}")
        peak = max(peak, meter.peak)

        total_infer = sum(r["infer_s"] for r in rows)
        total_audio = sum(r["duration"] for r in rows)
        edits = sum(r["edits"] for r in rows)
        ref_chars = sum(r["ref_chars"] for r in rows)
        summary = {
            "model": name,
            "label": spec["label"] + (" + FSMN-VAD" if args.vad else ""),
            "vad": args.vad,
            "threads": args.threads,
            "items": len(rows),
            "audio_s": round(total_audio, 2),
            "infer_s": round(total_infer, 2),
            "rtf": round(total_infer / max(total_audio, 1e-9), 4),
            "rtfx": round(total_audio / max(total_infer, 1e-9), 2),
            "cer": round(edits / max(ref_chars, 1) * 100, 2),
            "median_item_rtf": round(statistics.median(r["rtf"] for r in rows), 4),
            "p95_latency_s": round(sorted(r["infer_s"] for r in rows)[max(0, int(len(rows) * 0.95) - 1)], 3),
            "load_s": round(load_s, 2),
            "rss_after_load_mb": round(rss_after_load / 1e6, 1),
            "peak_rss_mb": round(peak / 1e6, 1),
        }
        for corpus in ("aishell1_test", "synth_long", "aishell4_meeting"):
            subset = [r for r in rows if r["corpus"] == corpus]
            if subset:
                e = sum(r["edits"] for r in subset)
                n = sum(r["ref_chars"] for r in subset)
                a = sum(r["duration"] for r in subset)
                t = sum(r["infer_s"] for r in subset)
                summary[f"cer_{corpus}"] = round(e / max(n, 1) * 100, 2)
                summary[f"rtf_{corpus}"] = round(t / max(a, 1e-9), 4)
                filler_e = sum(
                    edit_distance(strip_fillers(r["ref"]), strip_fillers(r["hyp"])) for r in subset
                )
                filler_n = sum(len(strip_fillers(r["ref"])) for r in subset)
                summary[f"cer_content_{corpus}"] = round(filler_e / max(filler_n, 1) * 100, 2)

        suffix = "_vad" if args.vad else ""
        (out_dir / f"{name}{suffix}.json").write_text(
            json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        all_summaries.append(summary)
        print(
            f"summary: CER {summary['cer']:.2f}%  RTF {summary['rtf']:.3f} "
            f"({summary['rtfx']:.1f}x)  peak RSS {summary['peak_rss_mb']:.0f} MB"
        )

    summary_name = "summary_vad.json" if args.vad else "summary.json"
    (out_dir / summary_name).write_text(
        json.dumps(all_summaries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nwrote {out_dir / summary_name}")


if __name__ == "__main__":
    main()
