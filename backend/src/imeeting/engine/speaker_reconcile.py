"""会后声纹复核：用原始音频把被匿名簇吸收的已录入身份纠正回来。"""

from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf
from sqlmodel import select

from ..db import session_scope
from ..models import Meeting, Segment, Voiceprint, utcnow
from .speaker import EMBED_DIM, cosine, extract_embedding, load_enrolled

logger = logging.getLogger(__name__)
SAMPLE_RATE = 16000


def _read_audio(path: Path, start_ms: int, end_ms: int, gain: float) -> np.ndarray:
    start = int(start_ms * SAMPLE_RATE / 1000)
    frames = max(1, int((end_ms - start_ms) * SAMPLE_RATE / 1000))
    audio, sr = sf.read(
        path,
        start=start,
        frames=frames,
        dtype="float32",
        always_2d=False,
    )
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != SAMPLE_RATE and audio.size:
        n = max(1, int(audio.size * SAMPLE_RATE / sr))
        audio = np.interp(
            np.linspace(0, audio.size - 1, n),
            np.arange(audio.size),
            audio,
        ).astype(np.float32)
    if gain > 1.01:
        audio = np.clip(audio * gain, -1.0, 1.0)
    return np.ascontiguousarray(audio)


def _mean_vector(vectors: list[np.ndarray]) -> np.ndarray | None:
    if not vectors:
        return None
    merged = np.sum(vectors, axis=0)
    norm = float(np.linalg.norm(merged))
    return (merged / norm).astype(np.float32) if norm > 1e-9 else None


def reconcile_meeting_speakers(
    meeting_id: int,
    *,
    max_samples: int = 5,
    threshold: float = 0.68,
    segment_threshold: float = 0.62,
) -> dict:
    """重建会议内临时簇，并把高置信片段回溯到正式声纹。

    只处理 `spk_*` 临时簇。先用较长、文本正常的片段重建簇向量，命中
    正式身份后，再逐段复核；低相似片段保留为原「说话人 N」，避免把开场
    杂谈或旁人误标成主讲人。
    """
    with session_scope() as db:
        meeting = db.get(Meeting, meeting_id)
        if meeting is None or not meeting.audio_path:
            return {"matched_groups": 0, "relabeled_segments": 0}
        path = Path(meeting.audio_path)
        if not path.exists():
            return {"matched_groups": 0, "relabeled_segments": 0}
        segment_rows = [
            {
                "id": row.id,
                "start_ms": row.start_ms,
                "end_ms": row.end_ms,
                "speech_ms": row.speech_ms,
                "text": row.text or "",
                "speaker": row.speaker or "",
                "speaker_key": row.speaker_key,
                "adaptive_gain": float(row.adaptive_gain or 1.0),
            }
            for row in db.exec(
                select(Segment)
                .where(Segment.meeting_id == meeting_id)
                .order_by(Segment.start_ms, Segment.seq, Segment.id)
            ).all()
        ]
        provisional = {
            row.speaker_key: {"id": row.id, "name": row.name}
            for row in db.exec(
                select(Voiceprint).where(
                    Voiceprint.meeting_id == meeting_id,
                    Voiceprint.provisional == True,  # noqa: E712
                )
            ).all()
            if row.speaker_key
        }

    groups: dict[str, list[dict]] = defaultdict(list)
    for row in segment_rows:
        key = row["speaker_key"]
        if key and key.startswith("spk_") and key in provisional:
            groups[key].append(row)

    formal = [
        (name, ref, voiceprint_id, speaker_key)
        for name, ref, voiceprint_id, speaker_key in load_enrolled(meeting_id)
        if not name.startswith(("说话人", "疑似"))
    ]
    if not formal:
        return {"matched_groups": 0, "relabeled_segments": 0}

    updates: list[tuple[int, str, str, float]] = []
    vp_updates: list[tuple[int, np.ndarray | None]] = []
    matched_groups = 0
    for key, group in groups.items():
        usable = [
            row
            for row in group
            if row["speech_ms"] >= 1000 and len(row["text"].strip()) >= 5
        ]
        if len(usable) < 2:
            continue
        samples = sorted(
            usable, key=lambda row: row["speech_ms"], reverse=True
        )[:max_samples]
        cache: dict[int, np.ndarray] = {}
        vectors: list[np.ndarray] = []
        for row in samples:
            emb = extract_embedding(
                _read_audio(
                    path,
                    row["start_ms"],
                    row["end_ms"],
                    row["adaptive_gain"],
                )
            )
            if emb is not None:
                cache[row["id"]] = emb
                vectors.append(emb)
        rebuilt = _mean_vector(vectors)
        if rebuilt is None:
            continue

        best: tuple[str, float, int | None, str | None, np.ndarray] | None = None
        for name, ref, voiceprint_id, speaker_key in formal:
            score = cosine(rebuilt, ref)
            if best is None or score > best[1]:
                best = (name, score, voiceprint_id, speaker_key, ref)
        if best is None or best[1] < threshold:
            continue

        name, _group_score, _voiceprint_id, target_key, _reference = best
        target_key = target_key or f"vp_{_voiceprint_id}"
        changed = 0
        residual_vectors: list[np.ndarray] = []
        for row in group:
            emb = cache.get(row["id"])
            if emb is None:
                if row["speech_ms"] < 1000:
                    continue
                emb = extract_embedding(
                    _read_audio(
                        path,
                        row["start_ms"],
                        row["end_ms"],
                        row["adaptive_gain"],
                    )
                )
            if emb is None:
                continue
            # 逐段使用本场重建向量，而不是跨会议正式声纹：不同会议室/
            # 麦克风会让同一人在正式声纹上的余弦明显偏低；本场重建向量
            # 更适合判断“这一段是否属于刚确认的主讲簇”。
            score = cosine(emb, rebuilt)
            if score >= segment_threshold:
                updates.append(
                    (row["id"], name, target_key, round(score, 4))
                )
                changed += 1
            else:
                residual_vectors.append(emb)

        if changed == 0:
            continue
        matched_groups += 1
        residual = _mean_vector(residual_vectors)
        vp_updates.append((provisional[key]["id"], residual))
        logger.info(
            "会后声纹复核 meeting=%s %s -> %s score=%.3f relabeled=%d residual=%d",
            meeting_id,
            key,
            name,
            _group_score,
            changed,
            len(residual_vectors),
        )

    if not updates:
        return {"matched_groups": 0, "relabeled_segments": 0}

    with session_scope() as db:
        for segment_id, name, key, score in updates:
            row = db.get(Segment, segment_id)
            if row is None:
                continue
            row.speaker = name
            row.speaker_key = key
            row.speaker_source = "enrolled_reconciled"
            row.speaker_score = score
            db.add(row)
        for voiceprint_id, residual in vp_updates:
            vp = db.get(Voiceprint, voiceprint_id)
            if vp is None:
                continue
            if residual is None or residual.size != EMBED_DIM:
                db.delete(vp)
                continue
            vp.embedding = residual.astype(np.float32).tobytes()
            vp.updated_at = utcnow()
            db.add(vp)
        db.commit()

    return {
        "matched_groups": matched_groups,
        "relabeled_segments": len(updates),
    }
