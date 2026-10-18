"""对已落库的会议做只读量化分析（长测复盘用）。

用法::

    python scripts/session_report.py --meeting 200
    python scripts/session_report.py --meeting 200 --json

指标口径
--------
- 片段时长 = end_ms - start_ms
- 定稿滞后 = segment.created_at - (meeting.created_at + end_ms)
  （即该片段音频说完之后，前端多久拿到 final）
- 覆盖度 = 片段时长之和 / 音频文件可容纳时长（float32 16k 单声道）
- 相邻重叠 = 相邻两段文本的公共前缀/后缀最大字符数

全部为只读查询，不修改数据库。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import wave
from datetime import datetime
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "meeting.db"
PUNCT = "。！？!?.,，；;：:…"


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))
    return ordered[idx]


def ts(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def overlap(a: str, b: str) -> int:
    """a 的后缀与 b 的前缀的最大公共长度。"""
    limit = min(len(a), len(b), 120)
    for size in range(limit, 0, -1):
        if a[-size:] == b[:size]:
            return size
    return 0


def wav_seconds(path: Path) -> float:
    """按 WAV 头计算真实时长（AudioSink 落盘为 int16，不是 float32）。"""
    try:
        with wave.open(str(path), "rb") as fh:
            rate = fh.getframerate() or 0
            return fh.getnframes() / rate if rate else 0.0
    except Exception:
        return 0.0


def describe(values: list[float], unit: str) -> dict[str, float | int | str]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "min": round(min(values), 1),
        "median": round(statistics.median(values), 1),
        "mean": round(statistics.fmean(values), 1),
        "p90": round(percentile(values, 0.9), 1),
        "p99": round(percentile(values, 0.99), 1),
        "max": round(max(values), 1),
        "unit": unit,
    }


def build_report(db_path: Path, meeting_id: int) -> dict:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row

    meeting = dict(db.execute("select * from meeting where id=?", (meeting_id,)).fetchone())
    segs = [dict(r) for r in db.execute(
        "select * from segment where meeting_id=? order by seq", (meeting_id,))]
    minutes = [dict(r) for r in db.execute(
        "select * from minutes where meeting_id=? order by id", (meeting_id,))]

    start = ts(meeting["created_at"])
    finished = ts(meeting["finished_at"])

    durations = [(s["end_ms"] - s["start_ms"]) / 1000 for s in segs]
    lags = []
    for s in segs:
        created = ts(s["created_at"])
        if start and created:
            lags.append((created - start).total_seconds() - s["end_ms"] / 1000)

    gaps, overlaps, speakers = [], [], {}
    for prev, cur in zip(segs, segs[1:]):
        gaps.append((cur["start_ms"] - prev["end_ms"]) / 1000)
        overlaps.append(overlap(prev["text"] or "", cur["text"] or ""))
    for s in segs:
        speakers[s["speaker"] or "?"] = speakers.get(s["speaker"] or "?", 0) + 1

    audio_bytes = 0
    audio_seconds = 0.0
    audio_path = Path(meeting["audio_path"]) if meeting["audio_path"] else None
    if audio_path and audio_path.exists():
        audio_bytes = audio_path.stat().st_size
        audio_seconds = wav_seconds(audio_path)

    texts = [s["text"] or "" for s in segs]
    chars = sum(len(t) for t in texts)
    speech_seconds = sum(durations)
    session_seconds = (finished - start).total_seconds() if start and finished else 0.0
    gaps_bucket = {
        "lt_0.6s": sum(1 for g in gaps if g < 0.6),
        "0.6_1.5s": sum(1 for g in gaps if 0.6 <= g < 1.5),
        "1.5_3s": sum(1 for g in gaps if 1.5 <= g < 3),
        "3_10s": sum(1 for g in gaps if 3 <= g < 10),
        "ge_10s": sum(1 for g in gaps if g >= 10),
    }
    persist_batches = len({s["created_at"] for s in segs})
    persist_mode = (
        "batch_at_end"
        if len(segs) > 1 and lags and statistics.median(lags) > 60
        else "incremental"
    )

    report = {
        "meeting": {
            "id": meeting_id,
            "status": meeting["status"],
            "segment_count": meeting["segment_count"],
            "db_rows": len(segs),
            "session_wall_seconds": round(session_seconds, 1),
            "audio_file_seconds": round(audio_seconds, 1),
            "audio_file_mb": round(audio_bytes / 1024 / 1024, 1),
            "audio_capture_ratio": round(audio_seconds / session_seconds, 3) if session_seconds else None,
            "speech_seconds": round(speech_seconds, 1),
            "speech_ratio_of_audio": round(speech_seconds / audio_seconds, 3) if audio_seconds else None,
            "chars": chars,
            "chars_per_speech_second": round(chars / speech_seconds, 2) if speech_seconds else None,
            "persist_batches": persist_batches,
            "persist_mode": persist_mode,
        },
        "segment_seconds": describe(durations, "s"),
        "segment_chars": describe([float(len(t)) for t in texts], "chars"),
        # 批量落库时 created_at 是「会议结束统一写入时间」，不能当定稿延迟用。
        "final_lag_seconds": (
            describe(lags, "s")
            if persist_mode == "incremental"
            else {"n": 0, "note": "batch_at_end：created_at 不可用作定稿延迟"}
        ),
        "gap_seconds": describe(gaps, "s"),
        "gap_buckets": gaps_bucket,
        "adjacent_text_overlap_chars": describe(overlaps, "chars"),
        "speakers": speakers,
        "end_without_punct": sum(1 for t in texts if t and t[-1] not in PUNCT),
        "empty_text": sum(1 for t in texts if not t.strip()),
        "duplicate_text": len(texts) - len(set(texts)),
        "slow_final_over_5s": sum(1 for x in lags if x > 5),
        "slow_final_over_10s": sum(1 for x in lags if x > 10),
        "minutes": [
            {
                "status": m["status"],
                "model": m["model_used"],
                "created_at": m["created_at"],
                "latency_from_finish_seconds": round(
                    (ts(m["created_at"]) - finished).total_seconds(), 1)
                if finished and ts(m["created_at"]) else None,
                "topics": len(json.loads(m["topics"])) if m["topics"] else 0,
                "todos": len(json.loads(m["todos"])) if m["todos"] else 0,
                "summary_chars": len(m["summary"] or ""),
                "error": m["error"],
            }
            for m in minutes
        ],
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--meeting", type=int, required=True)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = build_report(args.db, args.meeting)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    m = report["meeting"]
    print(f"meeting {m['id']}  status={m['status']}  segments={m['db_rows']}")
    print(f"  wall={m['session_wall_seconds']}s  audio={m['audio_file_seconds']}s "
          f"({m['audio_file_mb']}MB, capture_ratio={m['audio_capture_ratio']})")
    print(f"  speech={m['speech_seconds']}s  speech/audio={m['speech_ratio_of_audio']}  "
          f"chars={m['chars']}  chars/s={m['chars_per_speech_second']}  "
          f"persist={m['persist_mode']}({m['persist_batches']})")
    for key in ("segment_seconds", "segment_chars", "final_lag_seconds", "gap_seconds",
                "adjacent_text_overlap_chars"):
        print(f"  {key}: {report[key]}")
    print(f"  gap_buckets={report['gap_buckets']}")
    print(f"  speakers={report['speakers']}")
    print(f"  no_punct={report['end_without_punct']}  empty={report['empty_text']}  "
          f"dup_text={report['duplicate_text']}  lag>5s={report['slow_final_over_5s']}  "
          f"lag>10s={report['slow_final_over_10s']}")
    for item in report["minutes"]:
        print(f"  minutes: {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
