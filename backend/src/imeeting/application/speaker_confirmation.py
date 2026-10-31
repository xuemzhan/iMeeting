"""The existing transcript action uses the same conflict protocol as templates."""

import json

import numpy as np
from fastapi import HTTPException
from sqlmodel import select

from ..engine import voice_match, voice_template
from ..models import AuditLog, Segment, Voiceprint, VoiceprintConflict
from ..settings import get_config
from .identity_access import identity_owner, owned_conflict, owned_voiceprint


def preflight_relabel(db, user, payload, owner_id, vector_loader):
    from_label = payload.from_label.strip()
    to_name = payload.to_name.strip()
    segment = db.exec(select(Segment).where(Segment.meeting_id == payload.meeting_id, Segment.speaker == from_label).order_by(Segment.start_ms)).first()
    candidates = [row for row in db.exec(select(Voiceprint)).all() if identity_owner(db, row) == owner_id]
    source = next((row for row in candidates if segment and row.speaker_key == segment.speaker_key), None)
    source = source or next((row for row in candidates if row.name == from_label and row.meeting_id in (None, payload.meeting_id)), None)
    target = next((row for row in candidates if row.name == to_name), None) or source
    if target is None:
        return to_name  # Text-only correction; no biometric learning is claimed.
    record = None
    if payload.conflict_id is not None:
        record = owned_conflict(db, payload.conflict_id, user)
        if record.meeting_id != payload.meeting_id or record.voiceprint_id != target.id:
            raise HTTPException(409, "确认记录与本场说话人不一致")
        vector = np.frombuffer(record.sample_embedding, dtype=np.float32)
    else:
        vector = vector_loader(db, payload.meeting_id, from_label, segment.speaker_key if segment else None)
        if vector is None and source is not None:
            vector = np.frombuffer(source.embedding, dtype=np.float32)
    if vector is None or vector.size != 192:
        return to_name
    if record is None:
        assessment = voice_match.evaluate_confirm(db, voiceprint_id=target.id, embedding=vector,
            meeting_id=payload.meeting_id, segment_id=segment.segment_id if segment else None,
            include_legacy=True)
    else:
        other = db.get(Voiceprint, record.other_voiceprint_id) if record.other_voiceprint_id else None
        assessment = {"conflict_id": record.id, "level": "high" if record.reason == "both" else "medium",
            "self_score": record.self_score, "other": {"voiceprint_id": other.id, "name": other.name, "score": record.other_score} if other else None}
    templates = voice_match.list_templates(target.id)
    active = [item for item in templates if item["slot"] >= 0 and item["superseded_by"] is None]
    aligned = any(voice_template.cosine(vector, np.frombuffer(row.embedding, dtype=np.float32)) >= float(get_config("spk.template_align_sim")) for row in voice_template.active_templates(db, target.id))
    requires_slot = len(active) >= voice_template._template_max() and not aligned
    changing_known_identity = source is not None and target.id != source.id
    needs_choice = changing_known_identity or assessment["level"] == "high" or (assessment["level"] == "medium" and assessment["self_score"] is not None) or requires_slot
    if needs_choice and assessment["conflict_id"] is None:
        record = VoiceprintConflict(voiceprint_id=target.id, meeting_id=payload.meeting_id, segment_id=segment.segment_id if segment else None, sample_embedding=np.asarray(vector, dtype=np.float32).tobytes(), self_score=assessment["self_score"], other_voiceprint_id=assessment["other"]["voiceprint_id"] if assessment["other"] else None, other_score=assessment["other"]["score"] if assessment["other"] else 0.0, status="pending")
        db.add(record)
        db.flush()
        assessment["conflict_id"] = record.id
    if needs_choice and not payload.decision:
        db.commit()  # Only the pending assessment is persisted, no identity edits.
        raise HTTPException(409, detail={"kind": "voiceprint_confirmation", "assessment": assessment,
            "voiceprint_id": target.id, "name": to_name, "templates": active,
            "requires_slot": requires_slot, "meeting_id": payload.meeting_id,
            "segment_id": segment.segment_id if segment else None,
            "start_ms": segment.start_ms if segment else None})
    if requires_slot and payload.decision == "confirm_self" and payload.slot is None:
        raise HTTPException(422, "请选择要替换的声纹模板")
    decision = payload.decision or "confirm_self"
    other_id = payload.target_voiceprint_id
    if decision == "reassign":
        if other_id is None:
            raise HTTPException(422, "请选择正确的说话人")
        other = owned_voiceprint(db, other_id, user, write=True)
        if identity_owner(db, other) != owner_id:
            raise HTTPException(403, "不能跨账号转交声纹")
        to_name = other.name
    try:
        result = voice_match.apply_decision(db, voiceprint_id=target.id, embedding=vector,
            decision=decision, conflict_id=payload.conflict_id or assessment["conflict_id"],
            other_voiceprint_id=other_id, slot=payload.slot, meeting_id=payload.meeting_id,
            segment_id=segment.segment_id if segment else None, user_id=user.id, commit=False)
    except (LookupError, ValueError) as exc:
        raise HTTPException(409, str(exc)) from exc
    db.add(AuditLog(user_id=user.id, username=user.username, action="speaker_identity_decision",
                   target=f"meeting:{payload.meeting_id}", payload=json.dumps(result, ensure_ascii=False)))
    if decision == "cancel":
        db.commit()
        return None
    return to_name
