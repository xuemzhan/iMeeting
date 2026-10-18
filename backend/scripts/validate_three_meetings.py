"""用三场真实会议做回归验收（只读）。

检查项
------
- 时间轴是否严格有序（多会话偏移是否生效）
- 会话记录 / 音频 / 指标文件是否齐全
- 说话人覆盖率（按片段、按语音时长）
- 切分粒度与碎片率
- 纪要语义合并能减少多少碎片
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from imeeting.llm.minutes import TranscriptLine, merge_fragments

DEFAULT_DB = Path(__file__).resolve().parents[1] / "data" / "meeting.db"
CONT = (
    "然后", "但是", "那", "如果", "所以", "因为", "就是", "不过",
    "而且", "并且", "比如说", "比如", "还有", "另外", "同时",
)


def pct(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * q) - 1)], 1)


def analyse(db: sqlite3.Connection, meeting_id: int) -> dict:
    db.row_factory = sqlite3.Row
    rows = [
        dict(r)
        for r in db.execute(
            "select start_ms,end_ms,speaker,speaker_key,text,session_id "
            "from segment where meeting_id=? order by start_ms,seq,id",
            (meeting_id,),
        )
    ]
    starts = [r["start_ms"] for r in rows]
    inversions = sum(1 for a, b in zip(starts, starts[1:]) if b < a)
    durations = [(r["end_ms"] - r["start_ms"]) / 1000 for r in rows]
    labeled = [d for d, r in zip(durations, rows) if r["speaker"]]
    unknown = [d for d, r in zip(durations, rows) if not r["speaker"]]
    lines = [
        TranscriptLine(r["start_ms"], r["speaker"], r["text"], r["end_ms"])
        for r in rows
    ]
    merged = merge_fragments(lines)

    sessions = sorted({r["session_id"] for r in rows if r["session_id"]})
    metrics_dir = DEFAULT_DB.parent / "metrics"
    audio_dir = DEFAULT_DB.parent / "audio"
    metrics = sorted(metrics_dir.glob(f"meeting-{meeting_id}-*.json"))
    audio = sorted(audio_dir.glob(f"{meeting_id}-*.wav")) + sorted(
        audio_dir.glob(f"{meeting_id}.wav")
    )

    return {
        "meeting_id": meeting_id,
        "segments": len(rows),
        "sessions": len(sessions),
        "session_rows": db.execute(
            "select count(*) from meeting_session where meeting_id=?", (meeting_id,)
        ).fetchone()[0],
        "inversions": inversions,
        "duration_p50": round(statistics.median(durations), 1) if durations else 0,
        "duration_p90": pct(durations, 0.9),
        "duration_max": round(max(durations), 1) if durations else 0,
        "labeled_segments": sum(1 for r in rows if r["speaker"]),
        "labeled_pct_segments": round(100 * len(labeled) / len(rows), 1) if rows else 0,
        "labeled_pct_speech": round(
            100 * sum(labeled) / (sum(labeled) + sum(unknown)), 1
        )
        if rows
        else 0,
        "speakers": dict(Counter(r["speaker"] or "未知" for r in rows)),
        "continuation_pct": round(
            100 * sum(1 for r in rows if r["text"].lstrip().startswith(CONT)) / len(rows), 1
        )
        if rows
        else 0,
        "merged_lines": len(merged),
        "merge_reduction_pct": round(100 * (1 - len(merged) / len(rows)), 1)
        if rows
        else 0,
        "metrics_files": len(metrics),
        "audio_files": len(audio),
        "minutes_versions": [
            dict(r)
            for r in db.execute(
                "select version,is_draft,status,length(summary) as chars "
                "from minutes where meeting_id=? order by version",
                (meeting_id,),
            )
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--meetings", type=int, nargs="+", default=[200, 203, 209])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    report = [analyse(db, mid) for mid in args.meetings]
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    for item in report:
        print(
            f"会议 {item['meeting_id']}: {item['segments']} 段 / {item['sessions']} 会话"
            f" (session 行 {item['session_rows']})"
        )
        print(
            f"  时间轴倒序={item['inversions']}  "
            f"片段 p50={item['duration_p50']}s p90={item['duration_p90']}s "
            f"max={item['duration_max']}s"
        )
        print(
            f"  说话人覆盖: 片段 {item['labeled_pct_segments']}% / "
            f"语音时长 {item['labeled_pct_speech']}%"
        )
        print(
            f"  连接词起句={item['continuation_pct']}%  "
            f"纪要合并 {item['segments']}→{item['merged_lines']} "
            f"(-{item['merge_reduction_pct']}%)"
        )
        print(f"  指标文件={item['metrics_files']} 音频文件={item['audio_files']}")
        print(f"  纪要版本={item['minutes_versions']}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
