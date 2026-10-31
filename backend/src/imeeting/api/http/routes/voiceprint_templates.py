"""多场景声纹模板与确认冲突接口（REQ-002）。

与 ``voiceprints.py`` 共用 ``/api/v1/voiceprints`` 前缀；单独成文件是为了不把
录入/改名那条已经很长的链路再撑大。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from .... import persons
from ....application.identity_access import owned_conflict, owned_voiceprint
from ....application.meeting_access import owned_meeting
from ....db import get_session
from ....engine import speaker, voice_match, voice_template
from ....engine.speaker_reconcile import _read_audio
from ....engine.voice_template import Scenario, scenario_of
from ....models import (
    AuditLog,
    Meeting,
    Segment,
    User,
    Voiceprint,
    VoiceprintConflict,
    VoiceprintTemplate,
)
from ....settings import get_config
from .deps import get_current_user_or_anonymous

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/voiceprints", tags=["voiceprints"])


class ConfirmSampleRequest(BaseModel):
    """确认一条样本。

    ``embedding`` 与 ``segment_id`` 二者其一：前者用于脚本/测试直接给向量，
    后者从本场音频现抽。``decision`` 为空表示**只评估、不改数据**。
    """

    voiceprint_id: int
    meeting_id: int | None = None
    segment_id: str | None = None
    embedding: list[float] | None = Field(default=None, min_length=192, max_length=192)
    decision: str | None = None
    other_voiceprint_id: int | None = None
    slot: int | None = Field(default=None, ge=0, le=4)
    conflict_id: int | None = None


class ConflictDecide(BaseModel):
    action: str
    target_voiceprint_id: int | None = None
    slot: int | None = Field(default=None, ge=0, le=4)


class TemplatePatch(BaseModel):
    disabled: bool | None = None
    pinned: bool | None = None
    scenario_device: str | None = Field(default=None, max_length=16)
    scenario_distance: str | None = Field(default=None, max_length=16)
    scenario_noise: str | None = Field(default=None, max_length=16)
    scenario_voice: str | None = Field(default=None, max_length=16)


class TemplateMergeRequest(BaseModel):
    """把同一声纹的两条模板合并成一条（FR-5）。"""

    source_template_id: int
    target_template_id: int


def _embedding_of(db: Session, payload: ConfirmSampleRequest) -> np.ndarray:
    """解析请求里的声纹向量：显式向量优先，否则从片段音频现抽。"""
    if payload.embedding:
        vector = voice_match._normalize(payload.embedding)  # noqa: SLF001
        if vector is None:
            raise HTTPException(status_code=422, detail="embedding 必须是 192 维")
        return vector
    if payload.meeting_id is None or payload.segment_id is None:
        raise HTTPException(
            status_code=422, detail="需要提供 embedding 或 (meeting_id, segment_id)"
        )
    meeting = db.get(Meeting, payload.meeting_id)
    if meeting is None or not meeting.audio_path:
        raise HTTPException(status_code=422, detail="该会议没有可用音频，无法抽取声纹")
    segment = db.exec(
        select(Segment).where(
            Segment.meeting_id == payload.meeting_id,
            Segment.segment_id == payload.segment_id,
        )
    ).first()
    if segment is None:
        raise HTTPException(status_code=404, detail="片段不存在")
    speech_ms = segment.speech_ms or max(0, segment.end_ms - segment.start_ms)
    if speech_ms < int(get_config("spk.min_speech_ms")):
        raise HTTPException(422, "有效语音过短，不能用于声纹确认")
    if segment.snr_db is not None and segment.snr_db < float(get_config("spk.template_snr_floor")):
        raise HTTPException(422, "语音质量不足，不能用于声纹确认")
    path = Path(meeting.audio_path)
    if not path.exists():
        raise HTTPException(status_code=422, detail="音频文件已清理，无法抽取声纹")
    audio = _read_audio(
        path, segment.start_ms, segment.end_ms, float(segment.adaptive_gain or 1.0)
    )
    if audio.size == 0 or not np.isfinite(audio).all() or float(np.max(np.abs(audio))) < 1e-6:
        raise HTTPException(422, "音频为空或静音，不能用于声纹确认")
    embedding = speaker.extract_embedding(audio)
    if embedding is None:
        raise HTTPException(status_code=422, detail="音频过短或模型不可用，抽不出声纹")
    return np.asarray(embedding, dtype=np.float32)


@router.get("/{voiceprint_id}/templates")
def list_templates(
    voiceprint_id: int,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """某位人员的声纹模板（≤5 条，含两两相似度）。"""
    owned_voiceprint(db, voiceprint_id, user)
    voice = db.get(Voiceprint, voiceprint_id)
    if voice is None:
        raise HTTPException(status_code=404, detail="声纹不存在")
    person = persons.voiceprint_person(db, voice)
    return {
        "voiceprint_id": voiceprint_id,
        "max_templates": int(get_config("spk.template_max") or 5),
        "templates": voice_match.list_templates(voiceprint_id),
        # 人员是身份事实源；改名/合并后这里显示新姓名，声纹自身名字可以仍旧。
        "person": (
            None
            if person is None
            else {"person_id": person.id, "display_name": person.display_name}
        ),
    }


@router.patch("/{voiceprint_id}/templates/{template_id}")
def patch_template(
    voiceprint_id: int,
    template_id: int,
    payload: TemplatePatch,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """模板治理：停用/启用、改场景标签。"""
    owned_voiceprint(db, voiceprint_id, user, write=True)
    row = db.get(VoiceprintTemplate, template_id)
    if row is None or row.voiceprint_id != voiceprint_id:
        raise HTTPException(status_code=404, detail="模板不存在")
    scenario = None
    if any(
        value is not None
        for value in (
            payload.scenario_device,
            payload.scenario_distance,
            payload.scenario_noise,
            payload.scenario_voice,
        )
    ):
        scenario = Scenario(
            device=(payload.scenario_device or row.scenario_device)[:16],
            distance=(payload.scenario_distance or row.scenario_distance)[:16],
            noise=(payload.scenario_noise or row.scenario_noise)[:16],
            voice=(payload.scenario_voice or row.scenario_voice)[:16],
        )
    try:
        updated = voice_match.set_template_flags(
            db,
            template_id=template_id,
            disabled=payload.disabled,
            scenario=scenario,
            pinned=payload.pinned,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="template_update",
            target=f"voiceprint:{voiceprint_id}/template:{template_id}",
            payload=json.dumps(payload.model_dump(exclude_none=True), ensure_ascii=False),
        )
    )
    db.commit()
    voice_match.invalidate_cache()
    speaker.invalidate_cache()
    return {
        "template_id": updated.id,
        "slot": updated.slot,
        "disabled": updated.disabled,
        "pinned": bool(updated.pinned),
        "active": updated.slot >= 0 and updated.superseded_by is None and not updated.disabled,
        "scenario": {
            "device": updated.scenario_device,
            "distance": updated.scenario_distance,
            "noise": updated.scenario_noise,
            "voice": updated.scenario_voice,
        },
    }


@router.post("/{voiceprint_id}/templates/merge")
def merge_templates(
    voiceprint_id: int,
    payload: TemplateMergeRequest,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """把同一声纹的两条活跃模板合并成 target 一条（FR-5）。

    source 的来源贡献改挂到 target 后重算质心，source 退役但保留可追溯；
    任一方是主模板时 target 继承主模板。跨人、已停用、已退役一律 409。
    """
    owned_voiceprint(db, voiceprint_id, user, write=True)
    try:
        merged = voice_template.merge_templates(
            db,
            voiceprint_id=voiceprint_id,
            source_id=payload.source_template_id,
            target_id=payload.target_template_id,
            user_id=user.id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="template_merge",
            target=(
                f"voiceprint:{voiceprint_id}/template:"
                f"{payload.target_template_id}"
            ),
            payload=json.dumps(
                {
                    "source_template_id": payload.source_template_id,
                    "target_template_id": payload.target_template_id,
                    "sample_ms": merged.sample_ms,
                    "pinned": bool(merged.pinned),
                },
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    voice_match.invalidate_cache()
    speaker.invalidate_cache()
    return {
        "template_id": merged.id,
        "slot": merged.slot,
        "sample_ms": merged.sample_ms,
        "quality": round(float(merged.quality or 0.0), 4),
        "pinned": bool(merged.pinned),
        "retired_template_id": payload.source_template_id,
    }


@router.post("/confirm-sample")
def confirm_sample(
    payload: ConfirmSampleRequest,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """确认样本：先评估冲突（与本人差异大 / 更像别人），再按 ``decision`` 落模板。"""
    owned_voiceprint(db, payload.voiceprint_id, user, write=True)
    if payload.meeting_id is not None:
        owned_meeting(payload.meeting_id, user, db, write=True)
    if payload.other_voiceprint_id is not None:
        owned_voiceprint(db, payload.other_voiceprint_id, user, write=True)
    if payload.conflict_id is not None:
        conflict = owned_conflict(db, payload.conflict_id, user)
        if conflict.voiceprint_id != payload.voiceprint_id:
            raise HTTPException(409, "冲突记录与声纹不一致")
    if db.get(Voiceprint, payload.voiceprint_id) is None:
        raise HTTPException(status_code=404, detail="声纹不存在")
    if payload.conflict_id is not None:
        if conflict.meeting_id != payload.meeting_id or conflict.segment_id != payload.segment_id:
            raise HTTPException(409, "确认来源与冲突记录不一致")
        embedding = np.frombuffer(conflict.sample_embedding, dtype=np.float32)
    else:
        if payload.embedding is not None and user.role != "admin":
            raise HTTPException(403, "直接导入声纹向量仅用于管理员维护；请使用来源片段确认")
        embedding = _embedding_of(db, payload)
    if payload.conflict_id is not None:
        other = db.get(Voiceprint, conflict.other_voiceprint_id) if conflict.other_voiceprint_id else None
        assessment = {"conflict_id": conflict.id, "level": "high" if conflict.reason == "both" else "medium",
                      "self_score": conflict.self_score, "other": {"voiceprint_id": other.id, "name": other.name, "score": conflict.other_score} if other else None}
    else:
        assessment = voice_match.evaluate_confirm(
            db,
            voiceprint_id=payload.voiceprint_id,
            embedding=embedding,
            meeting_id=payload.meeting_id,
            segment_id=payload.segment_id,
        )
    active_templates = db.exec(select(VoiceprintTemplate).where(VoiceprintTemplate.voiceprint_id == payload.voiceprint_id, VoiceprintTemplate.slot >= 0, VoiceprintTemplate.superseded_by.is_(None))).all()
    aligned = any(not row.disabled and voice_template.cosine(embedding, np.frombuffer(row.embedding, dtype=np.float32)) >= float(get_config("spk.template_align_sim")) for row in active_templates)
    assessment["requires_slot"] = len(active_templates) >= voice_template._template_max() and not aligned
    if not payload.decision:
        db.commit()
        voice_match.invalidate_cache()
        speaker.invalidate_cache()
        return assessment

    if assessment["requires_slot"] and payload.decision == "confirm_self" and payload.slot is None:
        raise HTTPException(422, "模板已满，请选择替换项")
    sample_ms, sample_snr = 0, 0.0
    scenario = None
    if payload.meeting_id is not None and payload.segment_id:
        segment = db.exec(
            select(Segment).where(
                Segment.meeting_id == payload.meeting_id,
                Segment.segment_id == payload.segment_id,
            )
        ).first()
        if segment is not None:
            sample_ms = segment.speech_ms or (segment.end_ms - segment.start_ms)
            sample_snr = float(segment.snr_db or 0)
            scenario = scenario_of(
                speech_ms=segment.speech_ms or (segment.end_ms - segment.start_ms),
                chars=len(segment.text or ""),
                snr_db=float(segment.snr_db or 0.0),
                rms=float(segment.audio_rms or 0.0),
            )
    try:
        result = voice_match.apply_decision(
            db,
            voiceprint_id=payload.voiceprint_id,
            embedding=embedding,
            decision=payload.decision,
            conflict_id=payload.conflict_id or assessment.get("conflict_id"),
            other_voiceprint_id=payload.other_voiceprint_id,
            slot=payload.slot,
            meeting_id=payload.meeting_id,
            segment_id=payload.segment_id,
            scenario=scenario,
            sample_ms=sample_ms,
            snr_db=sample_snr,
            user_id=user.id,
            commit=False,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action=(
                "voiceprint_confirm"
                if payload.decision == "confirm_self"
                else "voiceprint_reassign"
            ),
            target=f"voiceprint:{payload.voiceprint_id}",
            payload=json.dumps(
                {
                    "decision": payload.decision,
                    "level": assessment["level"],
                    "self_score": assessment["self_score"],
                    "other": assessment["other"],
                    "slot": payload.slot,
                },
                ensure_ascii=False,
            ),
        )
    )
    db.commit()
    voice_match.invalidate_cache()
    speaker.invalidate_cache()
    return {"assessment": assessment, **result}


@router.get("/conflicts")
def list_conflicts(
    voiceprint_id: int | None = Query(default=None),
    user: User = Depends(get_current_user_or_anonymous),
):
    """待处理的确认冲突（默认全部，可按声纹过滤）。"""
    from ....db import session_scope
    with session_scope() as db:
        if voiceprint_id is not None:
            owned_voiceprint(db, voiceprint_id, user)
        return [item for item in voice_match.pending_conflicts(voiceprint_id)
                if owned_conflict_visible(db, item["conflict_id"], user)]


@router.post("/conflicts/{conflict_id}/decide")
def decide_conflict(
    conflict_id: int,
    payload: ConflictDecide,
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """处理一条冲突记录（accept / reassign / cancel），样本向量取自记录本身。"""
    owned_conflict(db, conflict_id, user)
    if payload.target_voiceprint_id is not None:
        owned_voiceprint(db, payload.target_voiceprint_id, user, write=True)
    record = db.get(VoiceprintConflict, conflict_id)
    if record is None:
        raise HTTPException(status_code=404, detail="冲突记录不存在")
    if not record.sample_embedding:
        raise HTTPException(status_code=422, detail="该记录没有保存样本向量")
    decision = {
        "accept": "confirm_self",
        "reassign": "reassign",
        "cancel": "cancel",
    }.get(payload.action)
    if decision is None:
        raise HTTPException(status_code=422, detail="action 必须是 accept/reassign/cancel")
    try:
        result = voice_match.apply_decision(
            db,
            voiceprint_id=record.voiceprint_id,
            embedding=np.frombuffer(record.sample_embedding, dtype=np.float32),
            decision=decision,
            conflict_id=conflict_id,
            other_voiceprint_id=payload.target_voiceprint_id
            or record.other_voiceprint_id,
            slot=payload.slot,
            meeting_id=record.meeting_id,
            segment_id=record.segment_id,
            user_id=user.id,
            commit=False,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.add(
        AuditLog(
            user_id=user.id,
            username=user.username,
            action="conflict_decide",
            target=f"voiceprint_conflict:{conflict_id}",
            payload=json.dumps({"action": payload.action, **result}, ensure_ascii=False),
        )
    )
    db.commit()
    voice_match.invalidate_cache()
    speaker.invalidate_cache()
    return result


@router.post("/templates/backfill")
def backfill_templates(
    user: User = Depends(get_current_user_or_anonymous),
):
    """把旧的单质心声纹导入为 slot 0 模板（幂等，可重复执行）。"""
    if user.role != "admin":
        raise HTTPException(403, "仅管理员可执行历史声纹回填")
    return {"written": voice_template.backfill_legacy_templates()}


@router.post("/templates/rebuild")
def rebuild_templates(
    meeting_id: int = Query(..., ge=1),
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """对某场会议重跑会后模板聚类（幂等，可用于历史会议补数据）。"""
    owned_meeting(meeting_id, user, db, write=True)
    if db.get(Meeting, meeting_id) is None:
        raise HTTPException(status_code=404, detail="会议不存在")
    return voice_template.rebuild_meeting_templates(meeting_id)


@router.get("/templates/rebuild-status")
def rebuild_status(
    meeting_id: int = Query(..., ge=1),
    user: User = Depends(get_current_user_or_anonymous),
    db: Session = Depends(get_session),
):
    """查询模板重建进度。

    阶段取值：idle / scanning / extracting / storing / done / failed / skipped。
    长会议重建是一次逐段解码 + 聚类，前端可轮询这里显示 ``speakers_done``、
    取样缓存命中数与已建模板数；没有记录时返回 idle。
    """
    owned_meeting(meeting_id, user, db)
    return voice_template.rebuild_state(meeting_id)


def owned_conflict_visible(db, conflict_id, user):
    try:
        owned_conflict(db, conflict_id, user)
        return True
    except HTTPException:
        return False
