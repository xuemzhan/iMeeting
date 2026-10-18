"""会后说话人全局合并：把只有少量片段的误分裂聚类并入最近的稳定说话人。"""

from __future__ import annotations

import logging

import numpy as np
from sqlalchemy import update
from sqlmodel import select

from ..db import session_scope
from ..models import Segment, Voiceprint, utcnow
from .speaker import EMBED_DIM, cosine

logger = logging.getLogger(__name__)


def _vec(raw: bytes) -> np.ndarray | None:
    v = np.frombuffer(raw or b"", dtype=np.float32)
    if v.size != EMBED_DIM:
        return None
    norm = float(np.linalg.norm(v))
    return v / norm if norm > 1e-9 else None


def merge_meeting_speakers(
    meeting_id: int,
    *,
    min_segments: int = 3,
    threshold: float = 0.45,
) -> dict:
    """把 ``provisional`` 且片段数 < ``min_segments`` 的聚类并入更稳定的聚类。

    规则：
    - 小聚类只并入片段更多（或已人工命名）的目标；
    - 目标也是临时声纹时用 ``threshold``；目标是正式身份时提高到 0.55；
    - 合并后更新片段标签/key、目标质心，并删除小聚类；
    - 没有任何相似目标时保留，避免把不同的人硬合并。
    """
    with session_scope() as db:
        segments = db.exec(
            select(Segment).where(Segment.meeting_id == meeting_id)
        ).all()
        keys = {s.speaker_key for s in segments if s.speaker_key}
        if not keys:
            return {"merged_clusters": 0, "relabeled_segments": 0}

        # 会议内临时声纹
        by_key: dict[str, Voiceprint] = {}
        for vp in db.exec(
            select(Voiceprint).where(Voiceprint.meeting_id == meeting_id)
        ).all():
            if vp.speaker_key:
                by_key[vp.speaker_key] = vp

        # 片段引用到的正式身份（形如 vp_<id>，或 speaker_key 已归一）
        for key in keys:
            if key in by_key:
                continue
            vp = None
            if key.startswith("vp_"):
                try:
                    vp = db.get(Voiceprint, int(key.split("_", 1)[1]))
                except ValueError:
                    vp = None
            if vp is None:
                vp = db.exec(
                    select(Voiceprint).where(Voiceprint.speaker_key == key)
                ).first()
            if vp is not None:
                by_key[key] = vp

        counts = {key: 0 for key in by_key}
        for seg in segments:
            if seg.speaker_key in counts:
                counts[seg.speaker_key] += 1

        # 清理没有任何片段的残留临时声纹
        for key, vp in list(by_key.items()):
            if vp.provisional and counts.get(key, 0) == 0:
                db.delete(vp)
                by_key.pop(key, None)
                counts.pop(key, None)

        small = [
            key
            for key, vp in by_key.items()
            if vp.provisional and 0 < counts.get(key, 0) < min_segments
        ]
        merged_clusters = 0
        relabeled = 0

        for small_key in sorted(small, key=lambda k: counts.get(k, 0)):
            source = by_key.get(small_key)
            if source is None:
                continue
            svec = _vec(source.embedding)
            if svec is None:
                continue
            best_key: str | None = None
            best_score = 0.0
            for target_key, target in by_key.items():
                if target_key == small_key:
                    continue
                tcount = counts.get(target_key, 0)
                if target.provisional and tcount <= counts.get(small_key, 0):
                    continue
                tvec = _vec(target.embedding)
                if tvec is None:
                    continue
                score = cosine(svec, tvec)
                need = threshold if target.provisional else max(threshold, 0.55)
                if score >= need and score > best_score:
                    best_key, best_score = target_key, score
            if best_key is None:
                continue

            target = by_key[best_key]
            db.execute(
                update(Segment)
                .where(
                    Segment.meeting_id == meeting_id,
                    Segment.speaker_key == small_key,
                )
                .values(speaker=target.name, speaker_key=best_key)
            )
            relabeled += counts.get(small_key, 0)

            # 加权更新目标质心
            s_w = counts.get(small_key, 0)
            t_w = counts.get(best_key, 0)
            tvec = _vec(target.embedding)
            if tvec is not None and s_w > 0:
                merged_vec = tvec * t_w + svec * s_w
                norm = float(np.linalg.norm(merged_vec))
                if norm > 1e-9:
                    target.embedding = (merged_vec / norm).astype(
                        np.float32
                    ).tobytes()
                    target.updated_at = utcnow()
                    db.add(target)
            counts[best_key] = t_w + s_w
            counts[small_key] = 0
            logger.info(
                "合并说话人 meeting=%s %s(%s) -> %s(%s) score=%.3f",
                meeting_id,
                source.name,
                small_key,
                target.name,
                best_key,
                best_score,
            )
            db.delete(source)
            by_key.pop(small_key, None)
            merged_clusters += 1

        db.commit()
        return {
            "merged_clusters": merged_clusters,
            "relabeled_segments": relabeled,
            "remaining_speakers": sum(1 for count in counts.values() if count > 0),
        }
