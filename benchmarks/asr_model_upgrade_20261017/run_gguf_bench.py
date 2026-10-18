#!/usr/bin/env python3
"""Evaluate Fun-ASR-Nano through the official llama.cpp / GGUF CPU runtime."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "manifest.jsonl"
BIN = ROOT / "tools" / "gguf" / "bin" / "llama-funasr-cli.exe"
SV_BIN = ROOT / "tools" / "gguf" / "bin" / "llama-funasr-sensevoice.exe"
ENC = ROOT / "tools" / "gguf" / "funasr-encoder-f16.gguf"
LLM = ROOT / "tools" / "gguf" / "qwen3-0.6b-q5km.gguf"
VAD = ROOT / "tools" / "gguf" / "fsmn-vad.gguf"
DONE_RE = re.compile(r"(?:\[done\]|\[sensevoice\] done)\s+([0-9.]+)s")


def normalize_zh(text: str) -> str:
    text = re.sub(r"<\|[^|]*\|>", "", text or "")
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text).upper()


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


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--out", default=str(ROOT / "results" / "nano_gguf.json"))
    ap.add_argument("--corpora", default="aishell1_test,synth_long")
    ap.add_argument("--engine", choices=("nano", "sensevoice"), default="nano")
    ap.add_argument("--llm", default=str(LLM))
    ap.add_argument("--label", default="Fun-ASR-Nano-2512 (GGUF Q5)")
    ap.add_argument("--vad", action="store_true", default=True)
    args = ap.parse_args()

    wanted = {c.strip() for c in args.corpora.split(",") if c.strip()}
    items = [
        json.loads(line)
        for line in Path(args.manifest).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    items = [i for i in items if i["corpus"] in wanted]

    rows = []
    for idx, item in enumerate(items, 1):
        audio = (ROOT / item["audio"]).resolve()
        if args.engine == "nano":
            cmd = [
                str(BIN),
                "--enc",
                str(ENC),
                "-m",
                str(args.llm),
                "-a",
                str(audio),
            ]
            if args.vad:
                cmd += ["--vad", str(VAD), "--vad-maxseg", "30000"]
        else:
            cmd = [str(SV_BIN), "-m", str(args.llm), "-a", str(audio)]
            if args.vad:
                cmd += ["--vad", str(VAD)]
        t0 = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        wall = time.perf_counter() - t0
        if proc.returncode != 0:
            raise RuntimeError(
                f"{item['id']} failed ({proc.returncode}): {proc.stderr[-2000:]}"
            )
        match = DONE_RE.search(proc.stderr)
        compute = float(match.group(1)) if match else wall
        raw = proc.stdout.strip().splitlines()
        text = raw[-1].strip() if raw else ""
        ref = normalize_zh(item["ref"])
        hyp = normalize_zh(text)
        edits = edit_distance(ref, hyp)
        row = {
            "id": item["id"],
            "corpus": item["corpus"],
            "duration": item["duration"],
            "ref": ref,
            "raw": text,
            "hyp": hyp,
            "edits": edits,
            "ref_chars": len(ref),
            "compute_s": round(compute, 3),
            "wall_s": round(wall, 3),
            "rtf": round(compute / max(item["duration"], 1e-9), 4),
            "completeness": round(len(hyp) / max(len(ref), 1), 3),
            "cer": round(edits / max(len(ref), 1) * 100, 2),
        }
        rows.append(row)
        print(
            f"[{idx:02d}/{len(items)}] {item['id']:<18} {compute:5.2f}s  "
            f"RTF {row['rtf']:.3f}  CER {row['cer']:5.1f}%  len {len(hyp)}/{len(ref)}"
        )
        print(f"     hyp: {hyp}")

    summary = {
        "model": args.label,
        "runtime": (
            "llama-funasr-cli v0.2.6, CPU (encoder 8 threads)"
            if args.engine == "nano"
            else "llama-funasr-sensevoice v0.2.6, CPU"
        ),
        "vad": args.vad,
        "items": len(rows),
    }
    for corpus in sorted({r["corpus"] for r in rows}):
        subset = [r for r in rows if r["corpus"] == corpus]
        edits = sum(r["edits"] for r in subset)
        ref_chars = sum(r["ref_chars"] for r in subset)
        audio_s = sum(r["duration"] for r in subset)
        compute_s = sum(r["compute_s"] for r in subset)
        summary[corpus] = {
            "items": len(subset),
            "audio_s": round(audio_s, 2),
            "compute_s": round(compute_s, 2),
            "rtf": round(compute_s / audio_s, 4),
            "cer": round(edits / ref_chars * 100, 2),
            "mean_completeness": round(
                sum(r["completeness"] for r in subset) / len(subset), 3
            ),
        }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
