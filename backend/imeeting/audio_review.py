"""会后全音频复核：生成修订建议，不直接覆盖人工可读转写。"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from sqlmodel import select

from .db import session_scope
from .engine import pool
from .glossary import hotwords_text
from .models import Meeting, MeetingSession, Segment, TranscriptReview
from .settings import get_config

logger = logging.getLogger(__name__)


def _load_audio(path: str) -> np.ndarray:
    import soundfile as sf

    audio, rate = sf.read(path, dtype="float32", always_2d=False)
    if getattr(audio, "ndim", 1) > 1:
        audio = audio.mean(axis=1)
    if rate != 16000 and len(audio):
        n = int(len(audio) * 16000 / rate)
        audio = np.interp(
            np.linspace(0, len(audio) - 1, n),
            np.arange(len(audio)),
            audio,
        ).astype(np.float32)
    return np.asarray(audio, dtype=np.float32)


def _sentences(result: object) -> list[dict]:
    if not isinstance(result, list) or not result:
        return []
    item = result[0]
    info = item.get("sentence_info") if isinstance(item, dict) else None
    out: list[dict] = []
    if isinstance(info, list):
        for sentence in info:
            if not isinstance(sentence, dict):
                continue
            text = str(sentence.get("text") or "").strip()
            if text:
                out.append(
                    {
                        "text": text,
                        "start_ms": int(sentence.get("start") or 0),
                        "end_ms": int(sentence.get("end") or sentence.get("start") or 0),
                    }
                )
    if not out and isinstance(item, dict):
        text = str(item.get("text") or "").strip()
        if text:
            out.append({"text": text, "start_ms": 0, "end_ms": 0})
    return out


def run_audio_review(meeting_id: int) -> dict:
    """在两个时间重叠的句子之间找差异，形成待确认建议。"""
    if not pool.is_ready("offline"):
        raise RuntimeError("离线模型未就绪，无法执行会后音频复核")
    with session_scope() as db:
        meeting = db.get(Meeting, meeting_id)
        if meeting is None:
            raise RuntimeError("会议不存在")
        sessions = db.exec(
            select(MeetingSession)
            .where(
                MeetingSession.meeting_id == meeting_id,
                MeetingSession.audio_path.is_not(None),
            )
            .order_by(MeetingSession.start_offset_ms)
        ).all()
        paths = [
            (row.start_offset_ms, row.audio_path)
            for row in sessions
            if row.audio_path and Path(row.audio_path).exists()
        ]
        if not paths and meeting.audio_path and Path(meeting.audio_path).exists():
            paths = [(0, meeting.audio_path)]
        segment_rows = db.exec(
            select(Segment)
            .where(Segment.meeting_id == meeting_id)
            .order_by(Segment.start_ms)
        ).all()
        segments = [
            {
                "segment_id": row.segment_id,
                "text": row.text or "",
                "start_ms": row.start_ms,
                "end_ms": row.end_ms,
            }
            for row in segment_rows
        ]

    if not paths:
        raise RuntimeError("该会议没有可用录音，无法执行全音频复核")
    if not segments:
        return {"suggestions": 0, "sessions": 0}

    model = pool._models["offline"]
    threshold = float(get_config("review.similarity_threshold") or 0.72)
    created = 0
    processed = 0
    for offset, path in paths:
        audio = _load_audio(path)
        if not len(audio):
            continue
        result = model.generate(
            input=audio,
            batch_size_s=get_config("asr.batch_size_s"),
            use_itn=get_config("use_itn"),
            return_spk_res=False,
            sentence_timestamp=True,
            hotword=hotwords_text(get_config("hotwords")),
        )
        sentences = _sentences(result)
        processed += len(sentences)
        for sentence in sentences:
            start = offset + sentence["start_ms"]
            end = offset + sentence["end_ms"]
            best = None
            best_overlap = 0
            for seg in segments:
                overlap = max(
                    0,
                    min(end or start + 1000, seg["end_ms"])
                    - max(start, seg["start_ms"]),
                )
                if overlap > best_overlap:
                    best, best_overlap = seg, overlap
            if best is None:
                continue
            ratio = SequenceMatcher(
                None, best["text"], sentence["text"] or ""
            ).ratio()
            if ratio >= threshold:
                continue
            with session_scope() as db:
                exists = db.exec(
                    select(TranscriptReview).where(
                        TranscriptReview.meeting_id == meeting_id,
                        TranscriptReview.segment_id == best["segment_id"],
                        TranscriptReview.status == "pending",
                    )
                ).first()
                if exists is None:
                    db.add(
                        TranscriptReview(
                            meeting_id=meeting_id,
                            segment_id=best["segment_id"],
                            original_text=best["text"],
                            suggested_text=sentence["text"],
                            start_ms=best["start_ms"],
                            end_ms=best["end_ms"],
                            similarity=round(ratio, 4),
                            reason="全音频重识别结果与原片段差异较大",
                        )
                    )
                    created += 1
    logger.info(
        "会后音频复核完成 meeting=%s sentences=%s suggestions=%s",
        meeting_id,
        processed,
        created,
    )
    return {"suggestions": created, "sentences": processed, "sessions": len(paths)}
