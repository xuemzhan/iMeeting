#!/usr/bin/env python3
"""Build a small, reproducible Mandarin ASR evaluation set.

Two sources:
  * AISHELL-1 test (clean read speech), sampled deterministically.
  * AISHELL-4 validation (real far-field meetings), with short excerpts cut
    from the public recordings and references taken from the supplied
    timestamped transcript.

The output is ``manifest.jsonl`` plus 16 kHz mono WAV files under ``audio/``.
"""

from __future__ import annotations

import json
import random
import re
import sys
import time
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


ROOT = Path(__file__).resolve().parent
AUDIO_DIR = ROOT / "audio"
MANIFEST = ROOT / "manifest.jsonl"
SAMPLE_RATE = 16000
SEED = 20261017

AISHELL1_DATASET = "AudioLLMs/aishell_1_zh_test"
AISHELL1_ROWS_URL = "https://datasets-server.huggingface.co/rows"
AISHELL4_DATASET = "zhaochenyang20/AISHELL4"
AISHELL4_REV = "664919348e0b87bfe2907c38d16da56fe1421538"

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "funasr-cpu-benchmark/1.0"})


def download(url: str, dest: Path, *, timeout: int = 180) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  cached {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")
        return
    tmp = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with HTTP.get(url, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        last = time.time()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_content(1024 * 256):
                if not chunk:
                    continue
                fh.write(chunk)
                done += len(chunk)
                if time.time() - last > 5:
                    pct = f" {done / total * 100:5.1f}%" if total else ""
                    print(f"  {dest.name}: {done / 1e6:.1f} MB{pct}")
                    last = time.time()
    tmp.replace(dest)
    print(f"  saved {dest.name} ({dest.stat().st_size / 1e6:.1f} MB)")


def normalize_ref(text: str) -> str:
    text = re.sub(r"<\|[^|]*\|>", "", text)
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"<(?:sil|#|%|->|/sil)>", "", text)
    text = re.sub(r"[<>&]", "", text)
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text).upper()


def get_rows_page(row_offset: int, length: int = 100) -> list[dict]:
    params = {
        "dataset": AISHELL1_DATASET,
        "config": "default",
        "split": "test",
        "offset": row_offset,
        "length": length,
    }
    for attempt in range(6):
        resp = HTTP.get(AISHELL1_ROWS_URL, params=params, timeout=120)
        if resp.status_code != 429:
            resp.raise_for_status()
            return resp.json()["rows"]
        wait = 10 * (attempt + 1)
        print(f"  datasets-server rate-limited, retrying in {wait}s ...")
        time.sleep(wait)
    resp.raise_for_status()
    raise RuntimeError("unreachable")


def fetch_aishell1(page_offsets: list[int], per_page: int, rng: random.Random) -> list[dict]:
    items: list[dict] = []
    picked_rows: list[dict] = []
    for page_offset in page_offsets:
        page = get_rows_page(page_offset)
        chosen = rng.sample(page, min(per_page, len(page)))
        picked_rows.extend(chosen)
        print(f"  page {page_offset}: picked {len(chosen)} rows")
        time.sleep(2.0)
    for n, item in enumerate(picked_rows, 1):
        row_idx = item["row_idx"]
        row = item["row"]
        ref = row["answer"].strip()
        audio_url = row["context"][0]["src"]
        key = f"aishell1_{row_idx:04d}"
        dest = AUDIO_DIR / f"{key}.wav"
        download(audio_url, dest)
        info = sf.info(dest)
        items.append(
            {
                "id": key,
                "corpus": "aishell1_test",
                "audio": str(dest.relative_to(ROOT)),
                "ref": normalize_ref(ref),
                "ref_raw": ref,
                "duration": round(info.duration, 3),
                "sample_rate": info.samplerate,
            }
        )
        print(f"[AISHELL-1 {n}/{len(picked_rows)}] {key}: {ref}")
    return items


SEG_RE = re.compile(r"\[(\d+(?:\.\d+)?)\](?:\[(\d+(?:\.\d+)?)\])?\[S(\d+)\]")


def parse_aishell4(transcript: str) -> list[dict]:
    matches = list(SEG_RE.finditer(transcript))
    segs: list[dict] = []
    for idx, m in enumerate(matches):
        start = float(m.group(1))
        declared_end = float(m.group(2)) if m.group(2) else None
        spk = int(m.group(3))
        text_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(transcript)
        text = transcript[m.end() : text_end]
        next_start = float(matches[idx + 1].group(1)) if idx + 1 < len(matches) else start + 5.0
        end = declared_end if declared_end and declared_end > start else next_start
        clean = normalize_ref(text)
        if not clean or end <= start:
            continue
        segs.append({"start": start, "end": end, "spk": spk, "text": clean})
    return segs


def cut_excerpt(flac: Path, segs: list[dict], target_start: float, max_seconds: float = 45.0):
    chosen: list[dict] = []
    for seg in segs:
        if seg["start"] < target_start:
            continue
        if not chosen and seg["end"] - seg["start"] > max_seconds:
            chosen.append(seg)
            break
        if chosen and seg["end"] - chosen[0]["start"] > max_seconds:
            break
        chosen.append(seg)
        if chosen[-1]["end"] - chosen[0]["start"] >= 18.0:
            break
    if not chosen:
        return None
    start = chosen[0]["start"]
    end = min(chosen[-1]["end"], start + max_seconds)
    ref = "".join(s["text"] for s in chosen)
    if len(ref) < 40 or end - start < 15:
        return None
    data, sr = sf.read(str(flac), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != SAMPLE_RATE:
        raise RuntimeError(f"unexpected sample rate {sr} for {flac}")
    left = max(0, int(start * sr))
    right = min(len(data), int(end * sr))
    return data[left:right].copy(), ref, start, end


def fetch_aishell4() -> list[dict]:
    items: list[dict] = []
    meta_url = (
        f"https://huggingface.co/datasets/{AISHELL4_DATASET}/resolve/{AISHELL4_REV}/"
        "validation/metadata.jsonl"
    )
    meta_path = ROOT / "raw" / "aishell4_metadata.jsonl"
    download(meta_url, meta_path)
    rows = [json.loads(line) for line in meta_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    picked = rows[:2]
    for meeting_idx, row in enumerate(picked):
        rel = "validation/" + row["file_name"]
        url = (
            f"https://huggingface.co/datasets/{AISHELL4_DATASET}/resolve/{AISHELL4_REV}/{rel}"
        )
        flac = ROOT / "raw" / Path(rel).name
        download(url, flac, timeout=600)
        segs = parse_aishell4(row["transcription"])
        if not segs:
            print(f"  no segments parsed for {rel}", file=sys.stderr)
            continue
        total = segs[-1]["end"]
        targets = [total * 0.18, total * 0.48, total * 0.75]
        for excerpt_idx, target in enumerate(targets, 1):
            cut = cut_excerpt(flac, segs, target)
            if cut is None:
                print(f"  skipped empty window at {target:.0f}s", file=sys.stderr)
                continue
            data, ref, start, end = cut
            key = f"aishell4_m{meeting_idx}_e{excerpt_idx}"
            dest = AUDIO_DIR / f"{key}.wav"
            sf.write(str(dest), data, SAMPLE_RATE, subtype="PCM_16")
            items.append(
                {
                    "id": key,
                    "corpus": "aishell4_meeting",
                    "audio": str(dest.relative_to(ROOT)),
                    "ref": ref,
                    "ref_raw": f"{rel} [{start:.2f}-{end:.2f}]",
                    "duration": round(len(data) / SAMPLE_RATE, 3),
                    "sample_rate": SAMPLE_RATE,
                }
            )
            print(
                f"[AISHELL-4 {meeting_idx} #{excerpt_idx}] {key}: "
                f"{start:.1f}-{end:.1f}s, {len(ref)} chars"
            )
    return items


def main() -> None:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)
    page_offsets = [0, 1700, 3400, 5100, 6800]
    print(f"sampling AISHELL-1 pages with seed {SEED}: {page_offsets}")
    items = fetch_aishell1(page_offsets, per_page=5, rng=rng)[:24]
    items += fetch_aishell4()
    with MANIFEST.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")
    total_audio = sum(i["duration"] for i in items)
    total_chars = sum(len(i["ref"]) for i in items)
    print(
        f"wrote {len(items)} items, {total_audio / 60:.1f} min audio, "
        f"{total_chars} reference chars -> {MANIFEST}"
    )


if __name__ == "__main__":
    main()
