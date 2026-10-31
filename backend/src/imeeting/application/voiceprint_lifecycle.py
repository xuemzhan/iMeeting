"""Remove dependent biometric records in the caller's transaction."""

from sqlalchemy import or_
from sqlmodel import select

from ..models import VoiceprintConflict, VoiceprintTemplate, VoiceprintTemplateSource


def remove_dependents(db, voiceprint_id):
    for row in db.exec(select(VoiceprintTemplateSource).where(VoiceprintTemplateSource.voiceprint_id == voiceprint_id)).all():
        db.delete(row)
    db.flush()
    for row in db.exec(select(VoiceprintTemplate).where(VoiceprintTemplate.voiceprint_id == voiceprint_id)).all():
        db.delete(row)
    for row in db.exec(select(VoiceprintConflict).where(or_(VoiceprintConflict.voiceprint_id == voiceprint_id, VoiceprintConflict.other_voiceprint_id == voiceprint_id))).all():
        db.delete(row)
    db.flush()


def merge_dependents(db, source_id, target_id):
    from ..engine.voice_template import Scenario, store_template

    rows = db.exec(select(VoiceprintTemplate).where(VoiceprintTemplate.voiceprint_id == source_id, VoiceprintTemplate.slot >= 0, VoiceprintTemplate.disabled.is_(False), VoiceprintTemplate.superseded_by.is_(None))).all()
    for row in rows:
        import numpy as np

        store_template(db, voiceprint_id=target_id, embedding=np.frombuffer(row.embedding, dtype=np.float32), scenario=Scenario(device=row.scenario_device, distance=row.scenario_distance, noise=row.scenario_noise, voice=row.scenario_voice), quality=row.quality, sample_ms=row.sample_ms, snr_db=row.snr_db, source_meeting_id=row.source_meeting_id, source_segment_id=f"merge-{source_id}-{row.id}", confirmed=row.confirmed, confirmed_by=row.confirmed_by)
    for conflict in db.exec(select(VoiceprintConflict).where(or_(VoiceprintConflict.voiceprint_id == source_id, VoiceprintConflict.other_voiceprint_id == source_id))).all():
        if conflict.voiceprint_id == source_id:
            conflict.voiceprint_id = target_id
        if conflict.other_voiceprint_id == source_id:
            conflict.other_voiceprint_id = target_id
        db.add(conflict)
    db.flush()
    remove_dependents(db, source_id)
