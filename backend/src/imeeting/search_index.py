"""可选语义向量索引；未配置 Embedding 时完全回退 FTS。"""

from __future__ import annotations

import logging

import httpx
import numpy as np
from sqlmodel import delete as sqla_delete
from sqlmodel import select

from .db import session_scope
from .models import Segment, SegmentEmbedding
from .settings import get_config

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(
        get_config("embedding.enabled")
        and get_config("embedding.base_url")
        and get_config("embedding.model")
    )


def _embed(texts: list[str]) -> list[np.ndarray]:
    base = str(get_config("embedding.base_url") or "").rstrip("/")
    model = str(get_config("embedding.model") or "")
    key = str(get_config("embedding.api_key") or "")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    with httpx.Client(timeout=60) as client:
        response = client.post(
            f"{base}/embeddings",
            headers=headers,
            json={"model": model, "input": texts},
        )
        response.raise_for_status()
        data = response.json().get("data") or []
        return [
            np.asarray(item["embedding"], dtype=np.float32)
            for item in sorted(data, key=lambda item: item.get("index", 0))
        ]


def rebuild_index(meeting_id: int | None = None) -> dict:
    if not enabled():
        raise RuntimeError("语义检索未配置")
    with session_scope() as db:
        stmt = select(Segment)
        if meeting_id is not None:
            stmt = stmt.where(Segment.meeting_id == meeting_id)
        segments = db.exec(stmt.order_by(Segment.meeting_id, Segment.start_ms)).all()
        if meeting_id is not None:
            db.execute(
                sqla_delete(SegmentEmbedding).where(
                    SegmentEmbedding.meeting_id == meeting_id
                )
            )
        created = 0
        batch_size = 32
        for start in range(0, len(segments), batch_size):
            batch = [row for row in segments[start : start + batch_size] if row.text.strip()]
            if not batch:
                continue
            vectors = _embed([row.text for row in batch])
            for segment, vector in zip(batch, vectors, strict=False):
                norm = float(np.linalg.norm(vector))
                if norm <= 1e-9:
                    continue
                db.add(
                    SegmentEmbedding(
                        meeting_id=segment.meeting_id,
                        segment_id=segment.segment_id,
                        model=str(get_config("embedding.model") or "")[:128],
                        dim=int(vector.size),
                        vector=(vector / norm).astype(np.float32).tobytes(),
                    )
                )
                created += 1
        db.commit()
    return {"segments": created, "meeting_id": meeting_id}


def semantic_matches(query: str, limit: int = 300) -> list[dict]:
    if not enabled() or not query.strip():
        return []
    try:
        query_vec = _embed([query.strip()])[0]
        norm = float(np.linalg.norm(query_vec))
        if norm <= 1e-9:
            return []
        query_vec = query_vec / norm
        with session_scope() as db:
            rows = db.exec(select(SegmentEmbedding)).all()
            segments = {
                (row.meeting_id, row.segment_id): row
                for row in db.exec(select(Segment)).all()
            }
        scored = []
        for row in rows:
            segment = segments.get((row.meeting_id, row.segment_id))
            if segment is None:
                continue
            vector = np.frombuffer(row.vector, dtype=np.float32)
            if vector.size != query_vec.size:
                continue
            score = float(np.dot(query_vec, vector))
            scored.append((score, segment))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [
            {
                "meeting_id": segment.meeting_id,
                "segment_id": segment.segment_id,
                "start_ms": segment.start_ms,
                "text": segment.text[:500],
                "score": round(score, 4),
            }
            for score, segment in scored[:limit]
        ]
    except Exception:
        logger.exception("语义检索失败，回退全文检索")
        return []
