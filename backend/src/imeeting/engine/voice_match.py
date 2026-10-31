"""多维声纹识别与人工确认冲突检测（REQ-002）。

识别分两阶段：

- **Stage 1 粗筛**：模板数够大时先按场景桶过滤（同桶优先），再用一次矩阵点积
  取得候选；库小的时候直接全量点积，避免为了「优化」反而更慢。
- **Stage 2 融合**：声纹相似度 + 场景匹配 + 有效语音时长 + 会话上下文，加权后
  决策：≥τ_high 命中；≤τ_low 未知；中间区间给「疑似」并标低置信。

确认（``evaluate_confirm``）回答两个问题，且都要能向用户解释清楚：

1. 这个样本和**本人**已有模板像不像？（``self_score``）
2. 它和**别人**的模板像不像？像谁？（``other_score`` / ``other_voiceprint_id``）

「跟自己差得远」+「跟别人更像」都成立时是**高冲突**，必须让用户三选一；
只成立一个时给非阻塞建议；都不成立就静默更新模板。
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Iterable, Sequence

import numpy as np
from sqlmodel import Session, select

from ..db import session_scope
from ..models import (
    MeetingParticipant,
    Voiceprint,
    VoiceprintConflict,
    VoiceprintTemplate,
    utcnow,
)
from ..settings import get_config
from . import voice_template
from .speaker import EMBED_DIM
from .voice_template import Scenario

logger = logging.getLogger(__name__)

#: 模板矩阵缓存：(作用域 meeting_id -> (时间戳, 矩阵, 行元数据))
_CACHE = OrderedDict()
_CACHE_TTL = 10.0
_CACHE_LOCK = __import__("threading").Lock()

#: 冲突判定档位
LEVEL_NONE = "none"
LEVEL_MEDIUM = "medium"
LEVEL_HIGH = "high"

#: 同一 (本人, 他人) 组合被接受几次后不再打扰
TOLERATE_AFTER = 2


def invalidate_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


_SCENARIO_DIMS = ("device", "distance", "noise", "voice")


def _scenario_tables(rows: list[dict]) -> tuple[dict[str, dict[str, int]], dict[str, np.ndarray]]:
    """把每行的场景桶离散化，供「先筛后算」用 numpy 一次性求候选集合。

    旧实现每次匹配都用 Python 列表推导逐行调 ``Scenario.match_score``；模板数
    上千时光构造掩码就比矩阵点积还贵（见 benchmarks/template_rebuild_20261031）。
    这里在缓存里预存类别编码，匹配时只做整型比较。
    """
    vocab: dict[str, dict[str, int]] = {dim: {"": 0} for dim in _SCENARIO_DIMS}
    codes: dict[str, np.ndarray] = {
        dim: np.zeros(len(rows), dtype=np.int32) for dim in _SCENARIO_DIMS
    }
    for index, row in enumerate(rows):
        scenario = row.get("scenario")
        if not isinstance(scenario, Scenario):
            continue
        for dim in _SCENARIO_DIMS:
            value = getattr(scenario, dim, "") or ""
            table = vocab[dim]
            code = table.get(value)
            if code is None:
                code = len(table)
                table[value] = code
            codes[dim][index] = code
    return vocab, codes


def scenario_mask(
    rows: list[dict],
    codes: dict[str, np.ndarray],
    vocab: dict[str, dict[str, int]],
    scenario: Scenario,
) -> np.ndarray | None:
    """等价于 ``Scenario.match_score(scenario) >= 0.6``，但整型向量化。

    ``match_score`` 只在「两边都已知」的维度上比较：全同 1.0、差一维 0.6、
    差两维 0.3、更差 0，没有可比维度时 0.5。因此分数 ≥0.6 等价于
    「至少一个维度可比，且不同维度数 ≤1」。
    """
    total = codes["device"].size
    if total == 0:
        return None
    known = np.zeros(total, dtype=bool)
    different = np.zeros(total, dtype=np.int32)
    for dim in _SCENARIO_DIMS:
        query = getattr(scenario, dim, "") or ""
        if not query:
            continue
        row_codes = codes[dim]
        comparable = row_codes != 0
        if not comparable.any():
            continue
        query_code = vocab[dim].get(query, -1)
        same = row_codes == query_code if query_code >= 0 else np.zeros(total, dtype=bool)
        known |= comparable
        different += (comparable & ~same).astype(np.int32)
    return known & (different <= 1)


def _load_scope(meeting_id: int | None) -> dict:
    """取（或重建）本作用域的模板矩阵、行元数据与场景编码。"""
    now = time.monotonic()
    with _CACHE_LOCK:
        for key in list(_CACHE):
            if now - _CACHE[key][0] >= _CACHE_TTL:
                _CACHE.pop(key, None)
        cached = _CACHE.get(meeting_id)
        if cached and now - cached[0] < _CACHE_TTL:
            return cached[1]
    matrix, rows = voice_template.templates_matrix(meeting_id)
    vocab, codes = _scenario_tables(rows)
    scope = {"matrix": matrix, "rows": rows, "vocab": vocab, "codes": codes}
    with _CACHE_LOCK:
        _CACHE[meeting_id] = (now, scope)
        _CACHE.move_to_end(meeting_id)
        while len(_CACHE) > 32:
            _CACHE.popitem(last=False)
    return scope


def _load(meeting_id: int | None) -> tuple[np.ndarray, list[dict]]:
    scope = _load_scope(meeting_id)
    return scope["matrix"], scope["rows"]


def _normalize(vector: Sequence[float] | np.ndarray) -> np.ndarray | None:
    arr = np.asarray(vector, dtype=np.float32).reshape(-1)
    if arr.size != EMBED_DIM:
        return None
    norm = float(np.linalg.norm(arr))
    if not np.isfinite(arr).all() or not np.isfinite(norm) or norm < 1e-9:
        return None
    return (arr / norm).astype(np.float32)


def _float(key: str, default: float) -> float:
    try:
        value = get_config(key)
        return float(default if value is None else value)
    except (TypeError, ValueError):
        return default


def _int(key: str, default: int) -> int:
    try:
        value = get_config(key)
        return int(default if value is None else value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# 识别
# ---------------------------------------------------------------------------


def match(
    embedding: Sequence[float] | np.ndarray,
    *,
    meeting_id: int | None = None,
    scenario: Scenario | None = None,
    speech_ms: int = 0,
    context_same_speaker: bool = False,
    threshold: float | None = None,
    context_voiceprint_id: int | None = None,
) -> dict | None:
    """模板匹配；未命中返回 ``None``，中间区间返回带 ``low_confidence`` 的结果。"""
    query = _normalize(embedding)
    if query is None:
        return None
    scope = _load_scope(meeting_id)
    matrix, rows = scope["matrix"], scope["rows"]
    if matrix.shape[0] == 0 or not rows:
        return None

    stage1_min = _int("spk.stage1_min_templates", 50)
    top_k = _int("spk.stage1_topk", 200)
    eligible = np.arange(matrix.shape[0])
    if matrix.shape[0] >= stage1_min and scenario is not None:
        mask = scenario_mask(rows, scope["codes"], scope["vocab"], scenario)
        if mask is not None and mask.any():
            eligible = np.flatnonzero(mask)
    scores = np.full(matrix.shape[0], -1.0, dtype=np.float32)
    scores[eligible] = matrix[eligible] @ query
    candidates = eligible[np.argsort(scores[eligible])[::-1][:max(1, top_k)]]

    w_emb = _float("spk.fuse_w_emb", 0.85)
    w_scene = _float("spk.fuse_w_scene", 0.05)
    w_dur = _float("spk.fuse_w_dur", 0.05)
    w_ctx = _float("spk.fuse_w_ctx", 0.05)
    if scenario is None and speech_ms <= 0 and not context_same_speaker:
        w_emb, w_scene, w_dur, w_ctx = 1.0, 0.0, 0.0, 0.0
    total_weight = max(1e-6, w_emb + w_scene + w_dur + w_ctx)

    duration_score = 0.0
    if speech_ms >= 3000:
        duration_score = 1.0
    elif speech_ms > 0:
        duration_score = max(0.0, min(1.0, (speech_ms - 1000) / 2000.0))

    best: dict | None = None
    best_per_person: dict[int, float] = {}
    for index in candidates:
        row = rows[int(index)]
        s_emb = float(scores[int(index)])
        s_scene = (
            row["scenario"].match_score(scenario)
            if scenario is not None and isinstance(row.get("scenario"), Scenario)
            else 0.5
        )
        s_ctx = 1.0 if (context_voiceprint_id == row["voiceprint_id"] or context_same_speaker) else 0.0
        fused = (
            w_emb * s_emb + w_scene * s_scene + w_dur * duration_score + w_ctx * s_ctx
        ) / total_weight
        voiceprint_id = int(row["voiceprint_id"])
        best_per_person[voiceprint_id] = max(
            best_per_person.get(voiceprint_id, -1.0), fused
        )
        if best is None or fused > best["score"]:
            best = {
                "voiceprint_id": voiceprint_id,
                "name": row["name"],
                "template_id": row["template_id"],
                "score": fused,
                "emb_score": s_emb,
                "scenario_score": s_scene,
                "source": "template",
            }

    if best is None:
        return None

    # 同一人可能有多个模板进候选：按「每人最高分」重排，避免同人模板互相顶掉
    ranked = sorted(best_per_person.items(), key=lambda item: item[1], reverse=True)
    ranked_person, ranked_score = ranked[0]
    if ranked_person != best["voiceprint_id"]:
        row = next(
            (item for item in rows if int(item["voiceprint_id"]) == ranked_person), None
        )
        if row is not None:
            best = {
                "voiceprint_id": ranked_person,
                "name": row["name"],
                "template_id": row["template_id"],
                "score": ranked_score,
                "emb_score": float(scores[int(np.argmax(scores))]) if scores.size else 0.0,
                "scenario_score": 0.0,
                "source": "template",
            }
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0

    tau_high = threshold if threshold is not None else _float("spk.enrolled_threshold", 0.68)
    tau_low = _float("spk.enrolled_ambiguous_threshold", 0.58)
    margin = _float("spk.arbitration_margin", 0.03)

    if best["score"] >= tau_high + 0.06 and best["score"] - runner_up >= margin:
        # early-exit 情形：分数足够高且领先明显，不再细分档
        best["low_confidence"] = False
        return best
    if best["score"] >= tau_high:
        best["low_confidence"] = False
        return best
    if best["score"] <= tau_low:
        return None
    best["low_confidence"] = True
    best["name"] = f"疑似 {best['name']}"
    return best


def best_scores_for(
    voiceprint_id: int, embedding: Sequence[float] | np.ndarray, *, include_legacy: bool = False
) -> tuple[float | None, float, int | None]:
    """返回 (本人最高相似度, 他人最高相似度, 他人 voiceprint_id)。

    「本人」用该声纹的全部活跃模板；「他人」排除自己。相似度是**原始余弦**，
    不带融合权重——冲突提示要说人话，给用户看的就是「像不像」。
    """
    query = _normalize(embedding)
    if query is None:
        return None, 0.0, None
    from ..application.identity_access import identity_owner
    with session_scope() as db:
        identity = db.get(Voiceprint, voiceprint_id)
        owner_id = identity_owner(db, identity) if identity is not None else None
    matrix, rows = voice_template.templates_matrix(None, owner_id=owner_id)
    if include_legacy:
        with session_scope() as db:
            for voice in db.exec(select(Voiceprint).where(Voiceprint.disabled.is_(False), Voiceprint.provisional.is_(False))).all():
                if identity_owner(db, voice) != owner_id:
                    continue
                vector = _normalize(np.frombuffer(voice.embedding, dtype=np.float32))
                if vector is not None:
                    matrix = np.vstack([matrix, vector])
                    rows.append({"voiceprint_id": voice.id})
    if matrix.shape[0] == 0:
        return None, 0.0, None
    scores = matrix @ query
    self_best: float | None = None
    other_best = 0.0
    other_id: int | None = None
    for score, row in zip(scores, rows, strict=False):
        if int(row["voiceprint_id"]) == voiceprint_id:
            value = float(score)
            self_best = value if self_best is None else max(self_best, value)
        elif float(score) > other_best:
            other_best = float(score)
            other_id = int(row["voiceprint_id"])
    return self_best, other_best, other_id


def _tolerated(db: Session, voiceprint_id: int, other_id: int | None) -> bool:
    if other_id is None:
        return False
    rows = db.exec(
        select(VoiceprintConflict).where(
            VoiceprintConflict.voiceprint_id == voiceprint_id,
            VoiceprintConflict.other_voiceprint_id == other_id,
            VoiceprintConflict.status == "accepted",
        )
    ).all()
    return len(rows) >= TOLERATE_AFTER


def evaluate_confirm(
    db: Session,
    *,
    voiceprint_id: int,
    embedding: Sequence[float] | np.ndarray,
    meeting_id: int | None = None,
    segment_id: str | None = None,
    include_legacy: bool = False,
) -> dict:
    """确认前的冲突评估；同时在需要时落一条 ``voiceprint_conflict``。"""
    self_score, other_score, other_id = best_scores_for(voiceprint_id, embedding, include_legacy=include_legacy)
    self_min = _float("spk.self_min_sim", 0.45)
    conflict_sim = _float("spk.conflict_sim", 0.60)
    conflict_margin = _float("spk.conflict_margin", 0.05)

    diverge = self_score is None or self_score < self_min
    conflict = other_score >= conflict_sim and other_score >= (self_score or 0.0) + conflict_margin

    reason = "both" if diverge and conflict else (
        "diverge" if diverge else ("conflict" if conflict else "")
    )
    level = LEVEL_HIGH if (diverge and conflict) else (
        LEVEL_MEDIUM if (diverge or conflict) else LEVEL_NONE
    )
    tolerated = level != LEVEL_NONE and _tolerated(db, voiceprint_id, other_id)
    if tolerated:
        level = LEVEL_MEDIUM

    other_name = None
    if other_id is not None:
        other = db.get(Voiceprint, other_id)
        other_name = other.name if other is not None else None

    record = None
    if level != LEVEL_NONE:
        normalized = _normalize(embedding)
        record = VoiceprintConflict(
            voiceprint_id=voiceprint_id,
            meeting_id=meeting_id,
            segment_id=(segment_id or "")[:64] or None,
            sample_embedding=(
                normalized.astype(np.float32).tobytes() if normalized is not None else b""
            ),
            self_score=self_score,
            other_voiceprint_id=other_id,
            other_score=other_score,
            reason=reason,
            status="pending",
        )
        existing = db.exec(select(VoiceprintConflict).where(
            VoiceprintConflict.voiceprint_id == voiceprint_id, VoiceprintConflict.meeting_id == meeting_id,
            VoiceprintConflict.segment_id == ((segment_id or "")[:64] or None),
            VoiceprintConflict.status == "pending", VoiceprintConflict.sample_embedding == record.sample_embedding)).first()
        record = existing or record
        db.add(record)
        db.flush()

    return {
        "conflict_id": record.id if record is not None else None,
        "self_score": None if self_score is None else round(self_score, 4),
        "other": (
            None
            if other_id is None
            else {
                "voiceprint_id": other_id,
                "name": other_name,
                "score": round(other_score, 4),
            }
        ),
        "reason": reason,
        "level": level,
        "tolerated": tolerated,
        "blocking": level == LEVEL_HIGH,
        "allowed_actions": (
            ["confirm_self", "reassign", "cancel"] if level != LEVEL_NONE else ["confirm_self"]
        ),
    }


def apply_decision(
    db: Session,
    *,
    voiceprint_id: int,
    embedding: Sequence[float] | np.ndarray,
    decision: str,
    conflict_id: int | None = None,
    other_voiceprint_id: int | None = None,
    slot: int | None = None,
    meeting_id: int | None = None,
    segment_id: str | None = None,
    scenario: Scenario | None = None,
    quality: float = 1.0,
    user_id: int | None = None,
    sample_ms: int = 0,
    snr_db: float = 0.0,
    commit: bool = True,
) -> dict:
    """执行用户在冲突提示里的三选一。

    - ``confirm_self``：写入/更新本人模板（满槽且不对齐时必须明确指定替换项）
    - ``reassign``：样本归到目标声纹，本人不变
    - ``cancel``：不写模板，只留冲突记录
    """
    normalized = _normalize(embedding)
    if normalized is None:
        raise ValueError("声纹向量维度必须是 192")
    if decision not in ("confirm_self", "reassign", "cancel"):
        raise ValueError(f"未知决策：{decision}")

    target_id = voiceprint_id
    if decision == "reassign":
        if other_voiceprint_id is None:
            raise ValueError("reassign 需要指定目标声纹")
        if db.get(Voiceprint, other_voiceprint_id) is None:
            raise LookupError("目标声纹不存在")
        target_id = other_voiceprint_id

    if decision == "cancel":
        status = "cancelled"
    elif decision == "reassign":
        status = "reassigned"
    else:
        status = "accepted"

    record = None
    if conflict_id is not None:
        record = db.get(VoiceprintConflict, conflict_id)
    if record is not None:
        if record.voiceprint_id != voiceprint_id:
            raise ValueError("冲突记录与声纹不一致")
        if record.status != "pending":
            if record.status != status or (decision == "reassign" and record.other_voiceprint_id != target_id):
                raise ValueError("冲突已经处理，请刷新后重试")
            return {"decision": decision, "status": status, "voiceprint_id": target_id, "template_id": None, "slot": slot, "already_decided": True}
        record.status = status
        record.decided_by = user_id
        record.decided_at = utcnow()
        if decision == "reassign":
            record.other_voiceprint_id = target_id
        db.add(record)

    row = None
    if decision in ("confirm_self", "reassign"):
        identity = db.get(Voiceprint, target_id)
        if identity is None:
            raise LookupError("声纹不存在")
        identity.provisional = False
        db.add(identity)
        occupied = db.exec(select(VoiceprintTemplate).where(
            VoiceprintTemplate.voiceprint_id == target_id, VoiceprintTemplate.slot >= 0,
            VoiceprintTemplate.superseded_by.is_(None))).all()
        aligned = any(not item.disabled and voice_template.cosine(normalized, np.frombuffer(item.embedding, dtype=np.float32)) >= float(get_config("spk.template_align_sim")) for item in occupied)
        if len(occupied) >= voice_template._template_max() and slot is None and not aligned:
            raise ValueError("模板已满，请明确选择要替换的样本")
        row = voice_template.store_template(
            db,
            voiceprint_id=target_id,
            embedding=normalized,
            scenario=scenario or Scenario(),
            quality=quality,
            sample_ms=sample_ms,
            snr_db=snr_db,
            preferred_slot=slot,
            source_meeting_id=meeting_id,
            source_segment_id=segment_id,
            confirmed=True,
            confirmed_by=user_id,
        )
        # 确认即把声纹挂到本场该 speaker_key 对应的人员上（有人员才建关联）。
        if meeting_id is not None and identity.speaker_key:
            from .. import persons as persons_module

            part = db.exec(
                select(MeetingParticipant).where(
                    MeetingParticipant.meeting_id == meeting_id,
                    MeetingParticipant.speaker_key == identity.speaker_key,
                )
            ).first()
            if part is not None:
                persons_module.link_voiceprints_by_speaker(
                    db,
                    person_id=part.person_id,
                    meeting_id=meeting_id,
                    speaker_key=identity.speaker_key,
                )
    if commit:
        db.commit()
    else:
        db.flush()
    invalidate_cache()
    from .speaker import invalidate_cache as invalidate_legacy
    invalidate_legacy()
    return {
        "decision": decision,
        "status": status,
        "template_id": row.id if row is not None else None,
        "voiceprint_id": target_id,
        "slot": row.slot if row is not None else None,
    }


def set_template_flags(
    db: Session,
    *,
    template_id: int,
    disabled: bool | None = None,
    scenario: Scenario | None = None,
    pinned: bool | None = None,
) -> VoiceprintTemplate:
    """模板治理：停用/启用、改场景标签、设为主模板。

    ``pinned=True`` 会先取消同一人的其它主模板（库层还有部分唯一索引兜底）；
    只有活跃模板（未停用、未被替代、slot≥0）能当主模板。主模板不能直接停用或
    被自动淘汰，必须显式取消后再操作，避免「治理动作顺手把主模板弄没了」。
    """
    row = db.get(VoiceprintTemplate, template_id)
    if row is None:
        raise LookupError("模板不存在")
    if scenario is not None:
        row.scenario_device = scenario.device
        row.scenario_distance = scenario.distance
        row.scenario_noise = scenario.noise
        row.scenario_voice = scenario.voice
    if pinned is not None:
        if pinned:
            if row.disabled or row.slot < 0 or row.superseded_by is not None:
                raise ValueError("只有活跃模板可以设为主模板")
            others = db.exec(
                select(VoiceprintTemplate).where(
                    VoiceprintTemplate.voiceprint_id == row.voiceprint_id,
                    VoiceprintTemplate.pinned.is_(True),
                    VoiceprintTemplate.id != row.id,
                )
            ).all()
            for other in others:
                other.pinned = False
                other.updated_at = utcnow()
                db.add(other)
            # 先落库再置位：SQLite 的部分唯一索引在语句级检查，同一事务里
            # 两条 pinned=1 的中间状态必须是可见的「已清空」。
            db.flush()
        row.pinned = pinned
    if disabled is not None:
        if disabled and row.pinned:
            raise ValueError("主模板不能直接停用，请先取消主模板")
        if not disabled and row.superseded_by is not None:
            raise ValueError("已被替代的模板不能重新启用")
        if not disabled and row.slot < 0:
            slot = voice_template._free_slot(db, row.voiceprint_id)
            if slot is None:
                raise ValueError("模板槽位已满，请先选择替换项")
            row.slot = slot
        row.disabled = disabled
    row.updated_at = utcnow()
    db.add(row)
    db.commit()
    invalidate_cache()
    return row


def list_templates(voiceprint_id: int) -> list[dict]:
    """某人的模板清单（含与其它模板的相似度）。"""
    with session_scope() as db:
        rows = db.exec(
            select(VoiceprintTemplate)
            .where(VoiceprintTemplate.voiceprint_id == voiceprint_id)
            .order_by(VoiceprintTemplate.slot)
        ).all()
        out: list[dict] = []
        vectors: dict[int, np.ndarray] = {}
        for row in rows:
            vector = voice_template._vec(row.embedding)  # noqa: SLF001
            if vector is not None:
                vectors[int(row.id or 0)] = vector
            out.append(
                {
                    "template_id": row.id,
                    "slot": row.slot,
                    "active": row.slot >= 0 and row.superseded_by is None and not row.disabled,
                    "disabled": row.disabled,
                    "confirmed": bool(row.confirmed),
                    "quality": round(float(row.quality or 0.0), 4),
                    "sample_ms": row.sample_ms,
                    "snr_db": round(float(row.snr_db or 0.0), 2),
                    "scenario": {
                        "device": row.scenario_device,
                        "distance": row.scenario_distance,
                        "noise": row.scenario_noise,
                        "voice": row.scenario_voice,
                    },
                    "source_meeting_id": row.source_meeting_id,
                    "source_segment_id": row.source_segment_id,
                    "superseded_by": row.superseded_by,
                    "pinned": bool(row.pinned),
                }
            )
        active = [item for item in out if item["active"]]
        pinned = [item for item in active if item["pinned"]]
        if pinned:
            primary_id = pinned[0]["template_id"]
            primary_source = "pinned"
        elif active:
            best = max(active, key=lambda item: (item["quality"], -item["slot"]))
            primary_id = best["template_id"]
            primary_source = "quality"
        else:
            primary_id, primary_source = None, "none"
        for item in out:
            item["primary"] = item["template_id"] == primary_id
            # pinned = 用户显式选择的；quality = 没选主模板时按质量推导的候选。
            item["primary_source"] = primary_source
        for item in out:
            own = vectors.get(int(item["template_id"] or 0))
            if own is None:
                item["similarities"] = {}
                continue
            item["similarities"] = {
                str(other_id): round(voice_template.cosine(own, vec), 4)
                for other_id, vec in vectors.items()
                if other_id != int(item["template_id"] or 0)
            }
        return out


def pending_conflicts(voiceprint_id: int | None = None) -> list[dict]:
    with session_scope() as db:
        stmt = select(VoiceprintConflict).where(VoiceprintConflict.status == "pending")
        if voiceprint_id is not None:
            stmt = stmt.where(VoiceprintConflict.voiceprint_id == voiceprint_id)
        rows = db.exec(stmt.order_by(VoiceprintConflict.created_at.desc())).all()
        out = []
        for row in rows:
            other = db.get(Voiceprint, row.other_voiceprint_id) if row.other_voiceprint_id else None
            mine = db.get(Voiceprint, row.voiceprint_id)
            out.append(
                {
                    "conflict_id": row.id,
                    "voiceprint_id": row.voiceprint_id,
                    "voiceprint_name": mine.name if mine else None,
                    "other_voiceprint_id": row.other_voiceprint_id,
                    "other_name": other.name if other else None,
                    "self_score": row.self_score,
                    "other_score": row.other_score,
                    "reason": row.reason,
                    "status": row.status,
                    "meeting_id": row.meeting_id,
                    "segment_id": row.segment_id,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
            )
        return out


def list_conflicts_all(limit: int = 50) -> Iterable[dict]:
    return pending_conflicts()[:limit]


def template_count(meeting_id: int | None = None) -> int:
    """当前参与匹配的模板条数（0 表示还没建模板，调用方可退回旧路径）。"""
    matrix, _ = _load(meeting_id)
    return int(matrix.shape[0])
