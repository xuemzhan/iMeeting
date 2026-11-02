"""多场景声纹模板（REQ-002）：场景分桶、会后聚类、模板落库。

为什么不是「一条质心算到底」
--------------------------
实测同一个人换个设备/距离/情绪，跨句相似度会掉到 0.54–0.58；而「命中」阈值
（``spk.enrolled_threshold``，默认 0.75）要求更高，单条质心要么认不出，要么
逼着把阈值降到会认错人。这里每人保留 **≤5 条场景化模板**，让「同人不同场景」
和「不同人的相近声音」不再抢同一个阈值。
阈值只从 ``CONFIG_SCHEMA`` 取，本文提到的数字仅作说明，不构成第二份默认值。

slot 与历史
-----------
``slot`` 取 0..4，配合 ``UNIQUE(voiceprint_id, slot)`` 在库层硬约束上限。被替换
的模板不删除：``slot`` 改为负数（仍唯一）、``superseded_by`` 指向新模板，识别时
只看 ``superseded_by IS NULL AND slot >= 0`` 的行，因此可回滚、可追溯。
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Sequence

import numpy as np
from sqlmodel import Session, select

from ..db import session_scope
from ..models import (
    Segment,
    TemplateRebuildState,
    TemplateSampleEmbedding,
    Voiceprint,
    VoiceprintTemplate,
    VoiceprintTemplateSource,
    utcnow,
)
from ..settings import get_config
from .speaker import EMBED_DIM

logger = logging.getLogger(__name__)

#: 同人两条模板允许的最大相似度；超过即视为同一场景（FR-1.2）
DEFAULT_MERGE_SIM = 0.92
#: 聚类时两条向量可合并的相似度（FR-2.2：距离 0.35 ≈ 相似度 0.65）
DEFAULT_CLUSTER_SIM = 0.65
#: 新质心与已有模板「同场景」的对齐阈值（FR-2.4）
DEFAULT_ALIGN_SIM = 0.85

#: 跨会议复用同一 ``speaker_key`` 时的取样闸门：取样质心必须与此声纹已有证据
#: 至少这么像，才允许写进模板。``speaker_key`` 只是会议内的声学簇键，跨会议
#: 复用不代表同一人；没有闸门就会把别的会议的声音灌进既有身份（身份漂移）。
DEFAULT_REBUILD_GUARD_SIM = 0.80

STATE_ACTIVE = ("active",)


@dataclass(frozen=True)
class Scenario:
    device: str = ""
    distance: str = ""
    noise: str = ""
    voice: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "scenario_device": self.device,
            "scenario_distance": self.distance,
            "scenario_noise": self.noise,
            "scenario_voice": self.voice,
        }

    def label(self) -> str:
        return "|".join([self.device, self.distance, self.noise, self.voice])

    def match_score(self, other: "Scenario") -> float:
        """两套场景桶的匹配度：全同 1.0，差一维 0.6，差两维 0.3，更差 0。"""
        pairs = [(self.device, other.device), (self.distance, other.distance),
                 (self.noise, other.noise), (self.voice, other.voice)]
        known = [(a, b) for a, b in pairs if a and b]
        if not known:
            return 0.5
        same = sum(1 for a, b in known if a == b)
        diff = len(known) - same
        if diff == 0:
            return 1.0
        if diff == 1:
            return 0.6
        if diff == 2:
            return 0.3
        return 0.0


def _vec(raw: bytes) -> np.ndarray | None:
    v = np.frombuffer(raw or b"", dtype=np.float32)
    if v.size != EMBED_DIM:
        return None
    norm = float(np.linalg.norm(v))
    return (v / norm).astype(np.float32) if norm > 1e-9 else None


def _normalize(vector: np.ndarray) -> np.ndarray | None:
    arr = np.asarray(vector, dtype=np.float32).reshape(-1)
    if arr.size != EMBED_DIM:
        return None
    norm = float(np.linalg.norm(arr))
    return (arr / norm).astype(np.float32) if norm > 1e-9 else None


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    denom = float(np.linalg.norm(a) * np.linalg.norm(b)) + 1e-9
    return float(np.dot(a, b) / denom)


# ---------------------------------------------------------------------------
# 场景分桶（只用信号特征，不让 LLM 猜）
# ---------------------------------------------------------------------------


def scenario_of(
    *,
    speech_ms: int = 0,
    chars: int = 0,
    snr_db: float = 0.0,
    rms: float = 0.0,
    device: str = "",
) -> Scenario:
    """按信号特征把一条片段映射到场景桶。

    - 噪声：``snr_db`` >25 安静 / 15–25 混响 / <15 嘈杂
    - 距离：能量越低越像远场（阈值取真实录音的经验值）
    - 发声：语速（字/秒）与能量共同判断
    """
    if snr_db >= 25:
        noise = "quiet"
    elif snr_db >= 15:
        noise = "reverb"
    else:
        noise = "noisy"

    if rms >= 0.06:
        distance = "near"
    elif rms >= 0.015:
        distance = "mid"
    else:
        distance = "far"

    rate = (chars / (speech_ms / 1000.0)) if speech_ms > 0 and chars > 0 else 0.0
    if rate >= 5.5:
        voice = "fast"
    elif rate and rate <= 2.2:
        voice = "low"
    else:
        voice = "normal"

    return Scenario(device=device[:16], distance=distance, noise=noise, voice=voice)


def scenario_for_segment(seg: Segment) -> Scenario:
    return scenario_of(
        speech_ms=seg.speech_ms or max(0, (seg.end_ms or 0) - (seg.start_ms or 0)),
        chars=len(seg.text or ""),
        snr_db=float(seg.snr_db or 0.0),
        rms=float(seg.audio_rms or 0.0),
    )


# ---------------------------------------------------------------------------
# 聚类
# ---------------------------------------------------------------------------


def _weighted_centroid(vectors: Sequence[np.ndarray], weights: Sequence[float]) -> np.ndarray:
    total = np.zeros(EMBED_DIM, dtype=np.float32)
    for vec, weight in zip(vectors, weights, strict=False):
        total += vec * float(weight)
    norm = float(np.linalg.norm(total))
    if norm < 1e-9:
        return np.zeros(EMBED_DIM, dtype=np.float32)
    return (total / norm).astype(np.float32)


def cluster_vectors(
    vectors: Sequence[np.ndarray],
    weights: Sequence[float] | None = None,
    *,
    max_k: int = 5,
    cluster_sim: float = DEFAULT_CLUSTER_SIM,
) -> list[dict]:
    """层次聚类（按质心相似度合并），簇数收敛到 ≤ ``max_k``。

    Returns:
        [{"centroid": np.ndarray, "members": [int, ...], "weight": float}, ...]
    """
    cleaned: list[np.ndarray] = []
    cleaned_weights: list[float] = []
    for index, vec in enumerate(vectors):
        normalized = _normalize(vec)
        if normalized is None:
            continue
        cleaned.append(normalized)
        cleaned_weights.append(float(weights[index]) if weights else 1.0)
    if not cleaned:
        return []

    clusters = [
        {"centroid": vec, "members": [index], "weight": cleaned_weights[index]}
        for index, vec in enumerate(cleaned)
    ]

    def merge_once() -> bool:
        best: tuple[int, int, float] | None = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                score = cosine(clusters[i]["centroid"], clusters[j]["centroid"])
                if best is None or score > best[2]:
                    best = (i, j, score)
        if best is None:
            return False
        i, j, score = best
        if score < cluster_sim:
            return False
        left, right = clusters[i], clusters[j]
        merged_members = left["members"] + right["members"]
        merged_weights = [cleaned_weights[k] for k in merged_members]
        clusters[i] = {
            "centroid": _weighted_centroid([cleaned[k] for k in merged_members], merged_weights),
            "members": merged_members,
            "weight": float(sum(merged_weights)),
        }
        clusters.pop(j)
        return True

    while len(clusters) > 1 and merge_once():
        pass

    # 仍然超过上限：合并最相似的两簇，直到 ≤ max_k（保覆盖的同时不丢主簇）
    while len(clusters) > max_k:
        best_pair = (0, 1, -1.0)
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                score = cosine(clusters[i]["centroid"], clusters[j]["centroid"])
                if score > best_pair[2]:
                    best_pair = (i, j, score)
        i, j, _ = best_pair
        left, right = clusters[i], clusters[j]
        members = left["members"] + right["members"]
        merged_weights = [cleaned_weights[k] for k in members]
        clusters[i] = {
            "centroid": _weighted_centroid([cleaned[k] for k in members], merged_weights),
            "members": members,
            "weight": float(sum(merged_weights)),
        }
        clusters.pop(j)

    # 权重（质量）降序，便于 slot 分配时优先保留高质量场景
    clusters.sort(key=lambda item: item["weight"], reverse=True)
    return clusters


# ---------------------------------------------------------------------------
# 模板读写
# ---------------------------------------------------------------------------


def _template_max() -> int:
    try:
        value = int(get_config("spk.template_max") or 5)
    except (TypeError, ValueError):
        value = 5
    return max(1, min(5, value))


def active_templates(db: Session, voiceprint_id: int) -> list[VoiceprintTemplate]:
    return list(
        db.exec(
            select(VoiceprintTemplate)
            .where(
                VoiceprintTemplate.voiceprint_id == voiceprint_id,
                VoiceprintTemplate.slot >= 0,
                VoiceprintTemplate.disabled.is_(False),
                VoiceprintTemplate.superseded_by.is_(None),  # type: ignore[union-attr]
            )
            .order_by(VoiceprintTemplate.slot)
        ).all()
    )


def _retire(db: Session, row: VoiceprintTemplate, new_id: int) -> None:
    """把旧模板移出活跃集合：slot 取负数仍保持唯一，识别时不再参与。"""
    row.superseded_by = new_id
    row.slot = -int(row.id or 0) or -1
    row.updated_at = utcnow()
    db.add(row)


def _free_slot(db: Session, voiceprint_id: int) -> int | None:
    used = {
        row.slot
        for row in db.exec(
            select(VoiceprintTemplate).where(
                VoiceprintTemplate.voiceprint_id == voiceprint_id
            )
        ).all()
    }
    for slot in range(_template_max()):
        if slot not in used:
            return slot
    return None


def _weakest_slot(db: Session, voiceprint_id: int) -> VoiceprintTemplate | None:
    active = [row for row in active_templates(db, voiceprint_id) if not row.pinned]
    if not active:
        return None
    return min(active, key=lambda row: (row.quality, -row.slot))


def _ensure_source_rows(db: Session, row: VoiceprintTemplate) -> None:
    """给历史模板补一条确定性来源行，合并时才能重算质心。"""
    existing = db.exec(
        select(VoiceprintTemplateSource).where(
            VoiceprintTemplateSource.template_id == row.id
        )
    ).first()
    if existing is not None:
        return
    db.add(
        VoiceprintTemplateSource(
            voiceprint_id=row.voiceprint_id,
            template_id=row.id,
            source_key=f"legacy-template:{row.id}",
            embedding=row.embedding,
            weight=max(1.0, (row.sample_ms or 0) / 1000.0),
            sample_ms=max(0, row.sample_ms or 0),
        )
    )
    db.flush()


def merge_templates(
    db: Session,
    *,
    voiceprint_id: int,
    source_id: int,
    target_id: int,
    user_id: int | None = None,
) -> VoiceprintTemplate:
    """把同一人的两条模板合并成 ``target`` 一条（REQ-002 FR-5「合并两条模板」）。

    口径（先定清楚再实现，避免合并出无法解释的模板）：

    - 只允许**同一声纹的两条活跃模板**；跨人、已停用、已退役一律拒绝。
    - ``source`` 的来源贡献改挂到 ``target``（``(voiceprint_id, source_key)``
      唯一，重复的丢弃），随后按来源确定性重算质心、累计样本时长、取信噪比
      与质量的最大值。同一组来源无论合并几次都得到同一质心。
    - ``source`` 退役：``slot`` 取负、``superseded_by`` 指向 target，保留可追溯；
      不删除历史行。
    - 任一方是主模板时，``target`` 成为主模板（先清 source 的标记再置位，
      兼容 SQLite 的部分唯一索引），每人仍只有一条主模板。
    """
    if source_id == target_id:
        raise ValueError("不能把模板合并到自己")
    rows = db.exec(
        select(VoiceprintTemplate).where(
            VoiceprintTemplate.id.in_([source_id, target_id])
        )
    ).all()
    by_id = {row.id: row for row in rows}
    source = by_id.get(source_id)
    target = by_id.get(target_id)
    if source is None or target is None:
        raise LookupError("模板不存在")
    if source.voiceprint_id != voiceprint_id or target.voiceprint_id != voiceprint_id:
        raise ValueError("只能合并同一声纹的模板")
    for row in (source, target):
        if row.slot < 0 or row.superseded_by is not None or row.disabled:
            raise ValueError("只有活跃模板可以合并")

    _ensure_source_rows(db, source)
    _ensure_source_rows(db, target)
    for contribution in db.exec(
        select(VoiceprintTemplateSource).where(
            VoiceprintTemplateSource.template_id == source.id
        )
    ).all():
        clash = db.exec(
            select(VoiceprintTemplateSource).where(
                VoiceprintTemplateSource.voiceprint_id == voiceprint_id,
                VoiceprintTemplateSource.source_key == contribution.source_key,
                VoiceprintTemplateSource.template_id == target.id,
            )
        ).first()
        if clash is not None:
            db.delete(contribution)
        else:
            contribution.template_id = target.id
            db.add(contribution)
    db.flush()

    sources = db.exec(
        select(VoiceprintTemplateSource)
        .where(VoiceprintTemplateSource.template_id == target.id)
        .order_by(VoiceprintTemplateSource.source_key)
    ).all()
    pairs = [
        (vector, item.weight)
        for item in sources
        for vector in (_vec(item.embedding),)
        if vector is not None
    ]
    if pairs:
        target.embedding = _weighted_centroid(
            [vector for vector, _ in pairs], [weight for _, weight in pairs]
        ).astype(np.float32).tobytes()
    target.sample_ms = sum(max(0, item.sample_ms) for item in sources)
    target.snr_db = max(float(target.snr_db or 0.0), float(source.snr_db or 0.0))
    target.quality = max(float(target.quality or 0.0), float(source.quality or 0.0))
    target.updated_at = utcnow()
    db.add(target)

    source_pinned = bool(source.pinned)
    source.pinned = False
    db.add(source)
    db.flush()
    if source.confirmed or target.confirmed:
        target.confirmed = True
        target.confirmed_by = target.confirmed_by or source.confirmed_by or user_id
        target.confirmed_at = target.confirmed_at or source.confirmed_at or utcnow()
    if source_pinned and not target.pinned:
        target.pinned = True
    db.add(target)
    db.flush()
    _retire(db, source, target.id)
    db.flush()
    return target


def store_template(
    db: Session, *, voiceprint_id: int, embedding: np.ndarray, scenario: Scenario,
    quality: float, sample_ms: int = 0, snr_db: float = 0.0,
    source_meeting_id: int | None = None, source_segment_id: str | None = None,
    confirmed: bool = False, confirmed_by: int | None = None,
    preferred_slot: int | None = None,
) -> VoiceprintTemplate:
    """Aggregate unique source contributions; replaying a source never adds weight."""
    normalized = _normalize(embedding)
    if normalized is None or not np.isfinite(normalized).all():
        raise ValueError("声纹必须是有限的192维非零向量")
    encoded = normalized.astype(np.float32).tobytes()
    source_key = (f"meeting:{source_meeting_id}:segment:{source_segment_id}"
                  if source_meeting_id is not None and source_segment_id
                  else "vector:" + hashlib.sha256(encoded).hexdigest())
    contribution = db.exec(select(VoiceprintTemplateSource).where(
        VoiceprintTemplateSource.voiceprint_id == voiceprint_id,
        VoiceprintTemplateSource.source_key == source_key,
    )).first()
    row = db.get(VoiceprintTemplate, contribution.template_id) if contribution else None
    if row is not None and (row.slot < 0 or row.superseded_by is not None):
        return row  # Replaying retired evidence must not resurrect it.
    if row is not None and row.confirmed and not confirmed:
        if contribution.embedding == encoded:
            return row  # Automatic replay cannot alter approved evidence/metadata.
        # Keep changed automatic evidence separate until a person approves it.
        source_key += ":candidate:" + hashlib.sha256(encoded).hexdigest()
        contribution = db.exec(select(VoiceprintTemplateSource).where(
            VoiceprintTemplateSource.voiceprint_id == voiceprint_id,
            VoiceprintTemplateSource.source_key == source_key,
        )).first()
        row = db.get(VoiceprintTemplate, contribution.template_id) if contribution else None
        if row is not None and (row.confirmed or row.slot < 0 or row.superseded_by is not None):
            return row
    if preferred_slot is not None and not 0 <= preferred_slot <= 4:
        raise ValueError("模板槽位必须在0到4之间")
    if row is None and preferred_slot is None:
        align_sim = float(get_config("spk.template_align_sim") or DEFAULT_ALIGN_SIM)
        row = next((item for item in active_templates(db, voiceprint_id)
                    if (confirmed or (not item.confirmed and item.source_meeting_id == source_meeting_id)) and _vec(item.embedding) is not None and cosine(normalized, _vec(item.embedding)) >= align_sim), None)
    if row is None:
        slot = preferred_slot if preferred_slot is not None else _free_slot(db, voiceprint_id)
        retired = None
        if preferred_slot is not None:
            retired = db.exec(select(VoiceprintTemplate).where(
                VoiceprintTemplate.voiceprint_id == voiceprint_id,
                VoiceprintTemplate.slot == preferred_slot)).first()
            if retired is not None:
                if retired.pinned:
                    raise ValueError("目标槽位是主模板，请先取消主模板再替换")
                retired.slot = -int(retired.id)
                db.add(retired)
                db.flush()
        if slot is None:
            # 主模板不参与自动淘汰：它是用户显式选定的代表样本，不能被一次
            # 自动更新悄悄挤掉；要替换它必须显式指定槽位并先取消主模板。
            candidates = [item for item in active_templates(db, voiceprint_id)
                          if (confirmed or not item.confirmed) and not item.pinned]
            retired = min(candidates, key=lambda item: (item.quality, -item.slot)) if candidates else None
            if retired is None:
                raise ValueError("模板槽位已满，请先启用或明确替换一条模板")
            slot = retired.slot
            retired.slot = -int(retired.id)
            db.add(retired)
            db.flush()
        row = VoiceprintTemplate(voiceprint_id=voiceprint_id, slot=slot,
            embedding=encoded, dim=EMBED_DIM, quality=0.0,
            source_meeting_id=source_meeting_id, source_segment_id=source_segment_id,
            **scenario.as_dict())
        db.add(row)
        db.flush()
        if retired is not None:
            retired.superseded_by = row.id
            db.add(retired)
    elif contribution is None:
        # Preserve a pre-migration template as one deterministic baseline.
        prior = db.exec(select(VoiceprintTemplateSource).where(
            VoiceprintTemplateSource.template_id == row.id)).first()
        if prior is None:
            db.add(VoiceprintTemplateSource(voiceprint_id=voiceprint_id, template_id=row.id,
                source_key=f"legacy-template:{row.id}", embedding=row.embedding,
                weight=max(1.0, row.sample_ms / 1000.0), sample_ms=row.sample_ms))
    if contribution is None:
        contribution = VoiceprintTemplateSource(voiceprint_id=voiceprint_id,
            template_id=row.id, source_key=source_key, embedding=encoded)
    contribution.embedding = encoded
    contribution.weight = max(1.0, sample_ms / 1000.0)
    contribution.sample_ms = max(0, sample_ms)
    db.add(contribution)
    db.flush()
    sources = db.exec(select(VoiceprintTemplateSource).where(
        VoiceprintTemplateSource.template_id == row.id).order_by(VoiceprintTemplateSource.source_key)).all()
    row.embedding = _weighted_centroid([_vec(item.embedding) for item in sources],
                                      [item.weight for item in sources]).astype(np.float32).tobytes()
    row.sample_ms = sum(item.sample_ms for item in sources)
    row.quality = max(row.quality, float(np.clip(quality, 0, 1)))
    row.snr_db = max(row.snr_db, float(snr_db))
    if confirmed:
        row.confirmed = True
        row.confirmed_by = confirmed_by if confirmed_by is not None else row.confirmed_by
        row.confirmed_at = row.confirmed_at or utcnow()
    row.updated_at = utcnow()
    db.add(row)
    db.flush()
    return row



# ---------------------------------------------------------------------------
# 取样缓存：同一片段不重复解码
# ---------------------------------------------------------------------------


def _cache_enabled() -> bool:
    return bool(get_config("spk.template_embedding_cache_enabled", True))


def _cache_days() -> int:
    try:
        return max(0, int(get_config("spk.template_embedding_cache_days") or 0))
    except (TypeError, ValueError):
        return 0


def _file_signature(path) -> str | None:
    """音频文件身份：大小 + 修改时间。取不到（文件已清理）就不使用缓存。"""
    try:
        stat = path.stat()
    except OSError:
        return None
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def sample_signature(
    file_signature: str, start_ms: int, end_ms: int, gain: float, model: str = ""
) -> str:
    """缓存键成分：音频身份 + 片段边界 + 增益 + 声纹模型配置签名。

    模型名称、hub、revision或更新策略变化时，必须让旧缓存失效，
    否则会拿旧模型的向量去和新模型的向量比。
    """
    raw = f"{file_signature}|{int(start_ms)}|{int(end_ms)}|{float(gain):.4f}|{model}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cached_samples(db: Session, meeting_id: int) -> dict[str, TemplateSampleEmbedding]:
    rows = db.exec(
        select(TemplateSampleEmbedding).where(
            TemplateSampleEmbedding.meeting_id == meeting_id
        )
    ).all()
    return {row.segment_id: row for row in rows}


def prune_sample_cache(db: Session, days: int | None = None) -> int:
    """删除超过保留期的取样缓存；``days<=0`` 表示不按时间清理。"""
    days = _cache_days() if days is None else max(0, int(days))
    if days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=days)
    rows = db.exec(
        select(TemplateSampleEmbedding).where(TemplateSampleEmbedding.updated_at < cutoff)
    ).all()
    for row in rows:
        db.delete(row)
    db.flush()
    return len(rows)


def _remember_sample(
    db: Session,
    known: dict[str, TemplateSampleEmbedding],
    *,
    meeting_id: int,
    segment_id: str,
    signature: str,
    vector: np.ndarray,
) -> None:
    """写入/刷新一条取样缓存；同一 ``(meeting_id, segment_id)`` 只保留一行。"""
    encoded = np.asarray(vector, dtype=np.float32).tobytes()
    row = known.get(segment_id)
    if row is None:
        row = TemplateSampleEmbedding(
            meeting_id=meeting_id,
            segment_id=segment_id,
            audio_signature=signature,
            dim=EMBED_DIM,
            embedding=encoded,
        )
        known[segment_id] = row
    else:
        row.audio_signature = signature
        row.embedding = encoded
        row.dim = EMBED_DIM
        row.updated_at = utcnow()
    db.add(row)
    db.flush()


# ---------------------------------------------------------------------------
# 重建进度
# ---------------------------------------------------------------------------

def _write_rebuild_state(db: Session, meeting_id: int, **fields) -> TemplateRebuildState:
    row = db.get(TemplateRebuildState, meeting_id)
    if row is None:
        row = TemplateRebuildState(meeting_id=meeting_id, started_at=utcnow())
    for key, value in fields.items():
        if key == "note":
            value = str(value or "")[:200]
        elif key == "last_error":
            value = str(value or "")[:500]
        setattr(row, key, value)
    row.updated_at = utcnow()
    db.add(row)
    return row


def rebuild_state(meeting_id: int) -> dict:
    """查询某场会议上一次/正在进行的模板重建进度；没有记录时返回 idle。"""
    with session_scope() as db:
        row = db.get(TemplateRebuildState, meeting_id)
        if row is None:
            return {
                "meeting_id": meeting_id,
                "job_id": None,
                "stage": "idle",
                "speakers_total": 0,
                "speakers_done": 0,
                "samples_done": 0,
                "samples_cached": 0,
                "samples_extracted": 0,
                "templates_created": 0,
                "templates_updated": 0,
                "note": "",
                "last_error": "",
                "started_at": None,
                "finished_at": None,
                "updated_at": None,
                "cache_hit_rate": None,
            }
        done = int(row.samples_done or 0)
        return {
            "meeting_id": row.meeting_id,
            "job_id": row.job_id,
            "stage": row.stage,
            "speakers_total": int(row.speakers_total or 0),
            "speakers_done": int(row.speakers_done or 0),
            "samples_done": done,
            "samples_cached": int(row.samples_cached or 0),
            "samples_extracted": int(row.samples_extracted or 0),
            "templates_created": int(row.templates_created or 0),
            "templates_updated": int(row.templates_updated or 0),
            "note": row.note,
            "last_error": row.last_error,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
            "updated_at": row.updated_at,
            "cache_hit_rate": (
                round(int(row.samples_cached or 0) / done, 4) if done else None
            ),
        }


# ---------------------------------------------------------------------------
# 会后聚类
# ---------------------------------------------------------------------------


def _quality_of(seg: Segment) -> float:
    speech = max(1, seg.speech_ms or (seg.end_ms - seg.start_ms) or 1)
    duration = min(1.0, speech / 3000.0)
    snr = max(0.0, min(1.0, float(seg.snr_db or 0.0) / 25.0))
    return float(0.6 * duration + 0.4 * snr)


def _sample_vectors(
    db: Session, meeting_id: int, speaker_key: str, *, stats: dict | None = None
) -> tuple[list[np.ndarray], list[float], list[Segment]]:
    """取该说话人最值得入模的若干片段，并算出声纹嵌入。

    逐段解码音频算嵌入是整个会后流程里最贵的一步（数百段的会议要几分钟），
    所以先按「语音时长 + 信噪比」排序取前 ``spk.template_sample_max`` 条。

    取样结果按 ``(meeting_id, segment_id)`` 缓存（见 ``cached_samples``）：重跑
    同一场会议时，只要音频文件与片段边界没变就直接复用向量，不再解码；
    ``stats`` 累计本次的 ``cached`` / ``extracted`` 条数，供进度与报告使用。

    关于 ``spk.template_snr_floor``：历史实现里写了硬阈值 ``snr >= 10``，
    但浏览器麦克风实录的 ``snr_db`` 中位数只有 6–8 dB（本项目真实会议 644 段
    里只有 25 段达到 10），硬过滤会让模板永远建不起来。因此默认 0 = 不过滤，
    只把信噪比计入质量权重。
    """
    from .speaker import extract_embedding  # 延迟导入，避免循环依赖
    from .speaker_reconcile import _read_audio  # 复用已有的音频取样

    audio_path = _audio_path(db, meeting_id)
    if audio_path is None:
        return [], [], []

    segments = db.exec(
        select(Segment)
        .where(
            Segment.meeting_id == meeting_id,
            Segment.speaker_key == speaker_key,
            Segment.is_final == True,  # noqa: E712
        )
        .order_by(Segment.start_ms)
    ).all()

    min_speech = int(get_config("spk.min_speech_ms") or 1200)
    snr_floor = float(get_config("spk.template_snr_floor") or 0.0)
    sample_max = int(get_config("spk.template_sample_max") or 48)

    candidates = [
        seg
        for seg in segments
        if (seg.speech_ms or (seg.end_ms - seg.start_ms)) >= min_speech
        and float(seg.snr_db or 0.0) >= snr_floor
    ]
    candidates.sort(key=_quality_of, reverse=True)
    # 取完最好的若干条后回到时间序，让「代表片段」落在会议早段，便于溯源
    candidates = sorted(candidates[: max(3, sample_max)], key=lambda seg: seg.start_ms)

    file_signature = _file_signature(audio_path) if _cache_enabled() else None
    from .speaker import embedding_model_signature
    model_name = repr(embedding_model_signature())
    known = cached_samples(db, meeting_id) if file_signature else {}

    vectors: list[np.ndarray] = []
    weights: list[float] = []
    used: list[Segment] = []
    pending_samples = []
    for seg in candidates:
        gain = float(seg.adaptive_gain or 1.0)
        signature = (
            sample_signature(file_signature, seg.start_ms, seg.end_ms, gain, model_name)
            if file_signature
            else ""
        )
        vector: np.ndarray | None = None
        if signature:
            cached = known.get(seg.segment_id)
            if cached is not None and cached.audio_signature == signature:
                vector = _vec(cached.embedding)
                if vector is not None and stats is not None:
                    stats["cached"] = int(stats.get("cached", 0)) + 1
        if vector is None:
            audio = _read_audio(audio_path, seg.start_ms, seg.end_ms, gain)
            if audio.size == 0:
                continue
            embedding = extract_embedding(audio)
            if embedding is None:
                continue
            vector = _normalize(embedding)
            if vector is None:
                continue
            if signature:
                pending_samples.append((seg.segment_id, signature, vector))
            if stats is not None:
                stats["extracted"] = int(stats.get("extracted", 0)) + 1
        vectors.append(vector)
        weights.append(_quality_of(seg))
        used.append(seg)
    # Inference must finish before acquiring SQLite's single writer lock.
    for segment_id, signature, vector in pending_samples:
        _remember_sample(db, known, meeting_id=meeting_id, segment_id=segment_id,
                         signature=signature, vector=vector)
    return vectors, weights, used


def _audio_path(db: Session, meeting_id: int):
    from pathlib import Path

    from ..models import Meeting

    meeting = db.get(Meeting, meeting_id)
    if meeting is None or not meeting.audio_path:
        return None
    path = Path(meeting.audio_path)
    return path if path.exists() else None


def _own_meeting(row: Voiceprint) -> int | None:
    """这条声纹属于哪场会议；历史数据两列都为空时返回 None（不设闸门）。"""
    return row.meeting_id if row.meeting_id is not None else row.source_meeting_id


def _sample_belongs_to(
    db: Session,
    voiceprint: Voiceprint,
    vectors: Sequence[np.ndarray],
    weights: Sequence[float],
    meeting_id: int,
) -> bool:
    """本次取样是不是真的属于这条声纹。

    同一会议（或历史无来源）的声纹直接放行；**别的会议**的声纹则要求取样质心
    与它已有的活跃模板/基准向量足够相似，否则判定为「同一 key、不同声音」并
    拒绝写入。这样一次错配不会被会后重建永久固化。
    """
    own = _own_meeting(voiceprint)
    if own is None or own == meeting_id:
        return True
    raw = get_config("spk.rebuild_guard_sim")
    try:
        guard = DEFAULT_REBUILD_GUARD_SIM if raw is None else float(raw)
    except (TypeError, ValueError):
        guard = DEFAULT_REBUILD_GUARD_SIM
    if guard <= 0:
        return True
    if not vectors:
        return False
    centroid = _weighted_centroid(vectors, weights)
    known = [
        vec
        for vec in (_vec(item.embedding) for item in active_templates(db, voiceprint.id))
        if vec is not None
    ]
    baseline = _vec(voiceprint.embedding)
    if baseline is not None:
        known.append(baseline)
    if not known:
        return False
    best = max(cosine(centroid, vec) for vec in known)
    if best < guard:
        logger.warning(
            "会后聚类拒绝跨会议取样 voiceprint=%s guard=%.2f best=%.3f meeting=%s",
            voiceprint.id, guard, best, meeting_id,
        )
        return False
    return True


def rebuild_meeting_templates(meeting_id: int, *, job_id: int | None = None) -> dict:
    """会后为每位说话人生成/更新场景模板（幂等，带进度与取样缓存）。

    与旧实现的差别：每位说话人处理完就提交一次短事务并写一条
    ``template_rebuild_state``，因此长会议可以在处理过程中查询
    ``speakers_done/speakers_total``、取样缓存命中数和已建模板数；中途失败或
    进程重启后，进度停在最后到达的阶段，重跑会通过来源唯一键与取样缓存继续，
    不会重复累计同一条证据。
    """
    if not bool(get_config("spk.post_meeting_reconcile_enabled", True)):
        with session_scope() as db:
            _write_rebuild_state(
                db,
                meeting_id,
                job_id=job_id,
                stage="skipped",
                note="会后复核已关闭",
                finished_at=utcnow(),
            )
            db.commit()
        return {"meeting_id": meeting_id, "skipped": "disabled"}

    created = 0
    updated = 0
    skipped_no_voiceprint = 0
    skipped_no_samples = 0
    skipped_mismatch = 0
    cache_hits = 0
    cache_misses = 0
    processed = 0
    speakers: list[str] = []
    try:
        with session_scope() as db:
            _write_rebuild_state(
                db,
                meeting_id,
                job_id=job_id,
                stage="scanning",
                speakers_total=0,
                speakers_done=0,
                samples_done=0,
                samples_cached=0,
                samples_extracted=0,
                templates_created=0,
                templates_updated=0,
                note="",
                last_error="",
                started_at=utcnow(),
                finished_at=None,
            )
            db.commit()
            pruned = prune_sample_cache(db)
            keys = {
                row.speaker_key
                for row in db.exec(
                    select(Segment).where(Segment.meeting_id == meeting_id)
                ).all()
                if row.speaker_key
            }
            speakers = sorted(keys)
            _write_rebuild_state(
                db,
                meeting_id,
                speakers_total=len(speakers),
                note=f"清理过期取样缓存 {pruned} 条" if pruned else "",
            )
            db.commit()
            for speaker_key in speakers:
                completed = True
                try:
                    from ..application.identity_access import visible_voiceprint
                    matching_voices = db.exec(
                        select(Voiceprint).where(
                            Voiceprint.speaker_key == speaker_key,
                            Voiceprint.disabled.is_(False),  # type: ignore[union-attr]
                        )
                    ).all()
                    visible = [
                        row for row in matching_voices
                        if visible_voiceprint(db, row, meeting_id)
                    ]
                    if not visible:
                        skipped_no_voiceprint += 1
                        logger.info("会后聚类跳过 %s：未登记声纹", speaker_key)
                        continue
                    # 本会议自己的声纹优先：跨会议复用的 speaker_key 可能对应不同人，
                    # 先精确命中 (meeting_id, speaker_key)，再退回任意可见声纹。
                    voiceprint = next(
                        (row for row in visible if _own_meeting(row) == meeting_id),
                        None,
                    ) or visible[0]
                    stats = {"cached": 0, "extracted": 0}
                    vectors, weights, segments = _sample_vectors(
                        db, meeting_id, speaker_key, stats=stats
                    )
                    cache_hits += int(stats["cached"])
                    cache_misses += int(stats["extracted"])
                    if not vectors:
                        skipped_no_samples += 1
                        logger.info("会后聚类跳过 %s：没有可用取样片段", speaker_key)
                        continue
                    if not _sample_belongs_to(db, voiceprint, vectors, weights, meeting_id):
                        skipped_mismatch += 1
                        continue
                    clusters = cluster_vectors(
                        vectors,
                        weights,
                        max_k=_template_max(),
                        cluster_sim=float(
                            get_config("spk.template_cluster_sim") or DEFAULT_CLUSTER_SIM
                        ),
                    )
                    for cluster in clusters:
                        members = cluster["members"]
                        scenario = scenario_for_segment(segments[members[0]])
                        quality = min(1.0, cluster["weight"] / max(1, len(members)) / 1.2)
                        before = len(active_templates(db, voiceprint.id))
                        store_template(
                            db,
                            voiceprint_id=voiceprint.id,
                            embedding=cluster["centroid"],
                            scenario=scenario,
                            quality=quality,
                            sample_ms=sum(
                                max(0, segments[i].end_ms - segments[i].start_ms) for i in members
                            ),
                            snr_db=max(float(segments[i].snr_db or 0.0) for i in members),
                            source_meeting_id=meeting_id,
                            source_segment_id=segments[members[0]].segment_id,
                            confirmed=False,
                        )
                        if len(active_templates(db, voiceprint.id)) > before:
                            created += 1
                        else:
                            updated += 1
                except Exception:
                    completed = False
                    raise
                finally:
                    # 只在说话人处理成功时才推进进度并提交：失败那位说话人的半成品
                    # 事务随异常回滚，由外层把 stage=failed 与 last_error 落库。
                    if completed:
                        processed += 1
                        _write_rebuild_state(
                            db,
                            meeting_id,
                            stage=("storing" if processed >= len(speakers) else "extracting"),
                            speakers_done=processed,
                            samples_done=cache_hits + cache_misses,
                            samples_cached=cache_hits,
                            samples_extracted=cache_misses,
                            templates_created=created,
                            templates_updated=updated,
                            note=speaker_key,
                        )
                        db.commit()
            noise_disabled = disable_noise_identities(db, meeting_id)
            if noise_disabled:
                db.commit()
            result = {
                "meeting_id": meeting_id,
                "created": created,
                "updated": updated,
                "skipped": skipped_no_voiceprint + skipped_no_samples + skipped_mismatch,
                "skipped_no_voiceprint": skipped_no_voiceprint,
                "skipped_no_samples": skipped_no_samples,
                "skipped_mismatch": skipped_mismatch,
                "speakers": len(speakers),
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                # 会后顺手停用的「证据不足」自动声纹（可恢复，不删除）。
                "noise_disabled": len(noise_disabled),
            }
            _write_rebuild_state(
                db,
                meeting_id,
                stage="done",
                speakers_total=len(speakers),
                speakers_done=processed,
                samples_done=cache_hits + cache_misses,
                samples_cached=cache_hits,
                samples_extracted=cache_misses,
                templates_created=created,
                templates_updated=updated,
                note="",
                finished_at=utcnow(),
            )
            db.commit()
            from .voice_match import invalidate_cache
            invalidate_cache()
            return result
    except Exception as exc:  # noqa: BLE001 - 进度必须记录失败阶段后原样抛出
        try:
            with session_scope() as db:
                _write_rebuild_state(
                    db,
                    meeting_id,
                    stage="failed",
                    speakers_done=processed,
                    samples_done=cache_hits + cache_misses,
                    samples_cached=cache_hits,
                    samples_extracted=cache_misses,
                    templates_created=created,
                    templates_updated=updated,
                    last_error=f"{type(exc).__name__}: {exc}",
                    finished_at=utcnow(),
                )
                db.commit()
        except Exception:  # pragma: no cover - 记录失败状态本身失败时保留原始异常
            logger.exception("写入模板重建失败状态失败 meeting=%s", meeting_id)
        raise



def disable_noise_identities(db: Session, meeting_id: int) -> list[str]:
    """停用本场「证据不足」的自动待命名声纹（停用而非删除，随时可恢复）。

    审查 2026-11-01 第 5 节第 4 条：只由一两句话生成的簇不该留下一个能被匹配到的身份
    （实测库里 person 5/6 就来自 1.8 秒的单句）。规则只作用于**本场自动生成**的临时
    声纹：人工命名过的、别的会议的、已停用的都不动；片段数与累计语音**同时**不达标才停用。
    门槛取自 ``spk.auto_identity_min_segments``（默认 3）与 ``spk.auto_identity_min_ms``
    （默认 3000），管理员可在设置里调。
    """
    try:
        min_segments = int(get_config("spk.auto_identity_min_segments") or 3)
        min_ms = int(get_config("spk.auto_identity_min_ms") or 3000)
    except (TypeError, ValueError):
        min_segments, min_ms = 3, 3000
    candidates = db.exec(
        select(Voiceprint).where(
            Voiceprint.meeting_id == meeting_id,
            Voiceprint.provisional.is_(True),
            Voiceprint.disabled.is_(False),  # type: ignore[union-attr]
        )
    ).all()
    disabled: list[str] = []
    for row in candidates:
        if not row.speaker_key:
            continue
        segments = db.exec(
            select(Segment).where(
                Segment.meeting_id == meeting_id,
                Segment.speaker_key == row.speaker_key,
            )
        ).all()
        speech_ms = sum(max(0, (seg.end_ms or 0) - (seg.start_ms or 0)) for seg in segments)
        if len(segments) >= min_segments or speech_ms >= min_ms:
            continue
        row.disabled = True
        row.updated_at = utcnow()
        db.add(row)
        disabled.append(row.name)
        logger.info(
            "停用证据不足的自动声纹 meeting=%s name=%s segments=%d speech_ms=%d",
            meeting_id,
            row.name,
            len(segments),
            speech_ms,
        )
    return disabled

# ---------------------------------------------------------------------------
# 兼容迁移
# ---------------------------------------------------------------------------


def backfill_legacy_templates() -> int:
    """把旧的单质心 ``voiceprint.embedding`` 导入为 slot 0 模板（幂等）。"""
    written = 0
    with session_scope() as db:
        for voiceprint in db.exec(select(Voiceprint)).all():
            if voiceprint.disabled:
                continue
            existing = db.exec(
                select(VoiceprintTemplate).where(
                    VoiceprintTemplate.voiceprint_id == voiceprint.id
                )
            ).first()
            if existing is not None:
                continue
            vector = _vec(voiceprint.embedding)
            if vector is None:
                continue
            db.add(
                VoiceprintTemplate(
                    voiceprint_id=voiceprint.id,
                    slot=0,
                    embedding=vector.astype(np.float32).tobytes(),
                    dim=EMBED_DIM,
                    quality=float(voiceprint.quality_score or 0.5),
                    confirmed=not voiceprint.provisional,
                    source_meeting_id=voiceprint.source_meeting_id,
                )
            )
            written += 1
        db.commit()
    return written


def _baseline_fallback_enabled() -> bool:
    """无模板身份是否用基准向量兜底参与匹配（默认关闭）。"""
    try:
        raw = get_config("spk.baseline_fallback_enabled")
    except Exception:  # noqa: BLE001 - 配置读取异常不得影响匹配主链路
        raw = None
    if raw is None:
        raw = os.environ.get("SPK_BASELINE_FALLBACK_ENABLED", "")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def templates_matrix(meeting_id: int | None = None, owner_id: int | None = None) -> tuple[np.ndarray, list[dict]]:
    """加载活跃模板为矩阵（供匹配用）。

    作用域与 ``speaker.load_enrolled`` 一致：临时声纹只在本会议内参与匹配。
    """
    rows: list[dict] = []
    vectors: list[np.ndarray] = []
    with session_scope() as db:
        from ..application.identity_access import visible_voiceprint_ids
        query = select(VoiceprintTemplate, Voiceprint).join(Voiceprint, Voiceprint.id == VoiceprintTemplate.voiceprint_id).where(
                VoiceprintTemplate.slot >= 0,
                VoiceprintTemplate.disabled.is_(False),
                VoiceprintTemplate.superseded_by.is_(None),  # type: ignore[union-attr]
                Voiceprint.disabled.is_(False),
                Voiceprint.id.in_(visible_voiceprint_ids(db, meeting_id, owner_id)),
                VoiceprintTemplate.confirmed.is_(True) | (VoiceprintTemplate.source_meeting_id == meeting_id if meeting_id is not None else False),
                Voiceprint.provisional.is_(False) | (Voiceprint.meeting_id == meeting_id if meeting_id is not None else False),
            ).order_by(VoiceprintTemplate.id)
        for template, voiceprint in db.exec(query).all():
            vector = _vec(template.embedding)
            if vector is None:
                continue
            vectors.append(vector)
            rows.append(
                {
                    "template_id": template.id,
                    "voiceprint_id": voiceprint.id,
                    "name": voiceprint.name,
                    "provisional": bool(voiceprint.provisional),
                    "confirmed": bool(template.confirmed),
                    "quality": float(template.quality or 0.0),
                    "scenario": Scenario(
                        device=template.scenario_device,
                        distance=template.scenario_distance,
                        noise=template.scenario_noise,
                        voice=template.scenario_voice,
                    ),
                }
            )
        # 可开关的兜底：旧版本录入、或模板被停用的正式身份，此前完全不参与匹配
        # （真实录音里丁效就是这样整场漏检）。默认关闭以保持既有契约，运行时用
        # app_config ``spk.baseline_fallback_enabled`` 或环境变量开启。
        if _baseline_fallback_enabled():
            covered = {row["voiceprint_id"] for row in rows}
            # 只要这条身份「曾经有过任何模板行」就不兜底：模板被停用/退役是用户
            # 明确的治理动作（"别再拿这条证据认人"），不能靠基准向量绕回去。
            retired = {
                int(row[0])
                for row in db.exec(
                    select(VoiceprintTemplate.voiceprint_id).distinct()
                ).all()
            }
            base_query = select(Voiceprint).where(
                Voiceprint.disabled.is_(False),  # type: ignore[union-attr]
                Voiceprint.provisional.is_(False),  # type: ignore[union-attr]
                Voiceprint.id.in_(visible_voiceprint_ids(db, meeting_id, owner_id)),
            )
            for voice in db.exec(base_query).all():
                if voice.id in covered or voice.id in retired:
                    continue
                vector = _vec(voice.embedding)
                if vector is None:
                    continue
                vectors.append(vector)
                rows.append(
                    {
                        "template_id": None,
                        "voiceprint_id": voice.id,
                        "name": voice.name,
                        "provisional": False,
                        "confirmed": False,
                        "quality": 0.3,
                        "scenario": Scenario(),
                        "source": "baseline",
                    }
                )
    if not vectors:
        return np.zeros((0, EMBED_DIM), dtype=np.float32), []
    return np.vstack(vectors), rows
