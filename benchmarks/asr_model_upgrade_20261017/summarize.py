#!/usr/bin/env python3
"""Combine the raw benchmark rows into the decision table."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
DIGITS = str.maketrans("0123456789", "零一二三四五六七八九")
EXCLUDED = {"aishell1_0003"}  # public sample: reference tail is absent from audio


def norm(text: str) -> str:
    text = re.sub(r"<\|[^|]*\|>", "", text or "")
    text = re.sub(r"[^\w\u4e00-\u9fff]", "", text).upper()
    return text.translate(DIGITS)


def edist(ref: str, hyp: str) -> int:
    r, h = list(ref), list(hyp)
    if not r:
        return len(h)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev = d[0]
        d[0] = i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)]


def load_rows(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["rows"]


def metric(rows: list[dict], corpus: str, exclude_bad: bool) -> dict:
    subset = [r for r in rows if r["corpus"] == corpus]
    if exclude_bad:
        subset = [r for r in subset if r["id"] not in EXCLUDED]
    edits = sum(edist(norm(r["ref"]), norm(r["hyp"])) for r in subset)
    ref_chars = sum(len(norm(r["ref"])) for r in subset)
    audio_s = sum(r["duration"] for r in subset)
    compute_s = sum(r.get("infer_s", r.get("compute_s", 0)) for r in subset)
    return {
        "items": len(subset),
        "audio_s": round(audio_s, 2),
        "compute_s": round(compute_s, 2),
        "cer": round(edits / max(ref_chars, 1) * 100, 2),
        "rtf": round(compute_s / max(audio_s, 1e-9), 4),
        "rtfx": round(audio_s / max(compute_s, 1e-9), 2),
    }


def main() -> None:
    models = {
        "seaco": {
            "label": "SeACo-Paraformer-Large（现状）",
            "clean_rows": load_rows(RESULTS / "seaco_vad.json")
            + load_rows(RESULTS / "synth_long" / "seaco_vad.json"),
            "meeting_rows": load_rows(RESULTS / "wenetspeech_meeting" / "seaco_vad.json"),
            "noise10_rows": load_rows(RESULTS / "noise_snr10" / "seaco_vad.json"),
            "noise5_rows": load_rows(RESULTS / "noise_snr05" / "seaco_vad.json"),
            "load_s": 6.6,
            "rss_mb": 3555,
            "runtime": "PyTorch CPU, 4 threads",
        },
        "sensevoice": {
            "label": "SenseVoiceSmall",
            "clean_rows": load_rows(RESULTS / "sensevoice_vad.json")
            + load_rows(RESULTS / "synth_long" / "sensevoice_vad.json"),
            "meeting_rows": load_rows(RESULTS / "wenetspeech_meeting" / "sensevoice_vad.json"),
            "noise10_rows": load_rows(RESULTS / "noise_snr10" / "sensevoice_vad.json"),
            "noise5_rows": load_rows(RESULTS / "noise_snr05" / "sensevoice_vad.json"),
            "load_s": 7.1,
            "rss_mb": 3364,
            "runtime": "PyTorch CPU, 4 threads",
        },
        "paraformer": {
            "label": "Paraformer-Large",
            "clean_rows": None,
            "meeting_rows": load_rows(RESULTS / "wenetspeech_meeting" / "paraformer_vad.json"),
            "load_s": 10.6,
            "rss_mb": 3216,
            "runtime": "PyTorch CPU, 4 threads",
        },
        "sensevoice_gguf": {
            "label": "SenseVoiceSmall (GGUF Q8)",
            "clean_rows": None,
            "meeting_rows": load_rows(RESULTS / "sensevoice_gguf_wenetspeech.json"),
            "load_s": 1.4,
            "rss_mb": 289,
            "runtime": "llama.cpp CPU",
        },
        "nano_q5": {
            "label": "Fun-ASR-Nano-2512 (GGUF Q5)",
            "clean_rows": load_rows(RESULTS / "nano_gguf.json"),
            "meeting_rows": load_rows(RESULTS / "nano_gguf_wenetspeech.json"),
            "noise_rows": load_rows(RESULTS / "nano_noise.json"),
            "load_s": 1.4,
            "rss_mb": 1224,
            "runtime": "llama.cpp CPU, encoder 8 threads",
        },
        "nano_q4": {
            "label": "Fun-ASR-Nano-2512 (GGUF Q4)",
            "clean_rows": None,
            "meeting_rows": load_rows(RESULTS / "nano_q4_wenetspeech.json"),
            "load_s": 1.4,
            "rss_mb": 1383,
            "runtime": "llama.cpp CPU, encoder 8 threads",
        },
        "nano_q8": {
            "label": "Fun-ASR-Nano-2512 (GGUF Q8)",
            "clean_rows": None,
            "meeting_rows": load_rows(RESULTS / "nano_q8_wenetspeech.json"),
            "load_s": 1.4,
            "rss_mb": 1411,
            "runtime": "llama.cpp CPU, encoder 8 threads",
        },
    }
    out = {}
    for name, m in models.items():
        entry = {
            k: v
            for k, v in m.items()
            if k not in ("clean_rows", "meeting_rows", "noise10_rows", "noise5_rows", "noise_rows")
        }
        if m["clean_rows"]:
            entry["aishell1_clean"] = metric(m["clean_rows"], "aishell1_test", True)
            entry["synth_long"] = metric(m["clean_rows"], "synth_long", True)
        if m["meeting_rows"]:
            entry["wenetspeech_meeting"] = metric(
                m["meeting_rows"], "wenetspeech_meeting", True
            )
        if m.get("noise10_rows"):
            entry["noise_snr10"] = metric(m["noise10_rows"], "noise_snr10", True)
        if m.get("noise5_rows"):
            entry["noise_snr05"] = metric(m["noise5_rows"], "noise_snr05", True)
        if m.get("noise_rows"):
            entry["noise_snr10"] = metric(m["noise_rows"], "noise_snr10", True)
            entry["noise_snr05"] = metric(m["noise_rows"], "noise_snr05", True)
        out[name] = entry

    (RESULTS / "decision_summary.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    header = (
        "| 模型 | 运行路径 | AISHELL-1 CER | 20–30s 连续语音 CER | "
        "WenetSpeech 会议 CER | 10 dB CER | 5 dB CER | 会议 RTF | 单模型内存 |"
    )
    lines = [header, "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for name, m in out.items():
        a1 = f"{m['aishell1_clean']['cer']:.2f}%" if "aishell1_clean" in m else "—"
        synth = f"{m['synth_long']['cer']:.2f}%" if "synth_long" in m else "—"
        meet = f"{m['wenetspeech_meeting']['cer']:.2f}%" if "wenetspeech_meeting" in m else "—"
        n10 = f"{m['noise_snr10']['cer']:.2f}%" if "noise_snr10" in m else "—"
        n05 = f"{m['noise_snr05']['cer']:.2f}%" if "noise_snr05" in m else "—"
        rtf = f"{m['wenetspeech_meeting']['rtf']:.3f}" if "wenetspeech_meeting" in m else "—"
        lines.append(
            f"| {m['label']} | {m['runtime']} | {a1} | {synth} | {meet} | {n10} | {n05} | {rtf} | "
            f"{m['rss_mb']} MB |"
        )
    table = "\n".join(lines)
    text = (
        table
        + "\n\n注：AISHELL-1 为 23 条（剔除 1 条公开数据音频/标注不一致样本）；"
        "WenetSpeech 为 40 条人工标注会议语音；10/5 dB 为 12 条干净语音加白噪声。"
        "Nano 的会议 RTF 使用 8 线程编码器，"
        "4 线程同口径见 `thread_scaling.json`（Nano Q5 为 4.4x 实时）。\n"
    )
    (RESULTS / "decision_summary.md").write_text(text, encoding="utf-8")
    (RESULTS / "decision_summary_v2.md").write_text(text, encoding="utf-8")
    print(table)


if __name__ == "__main__":
    main()
