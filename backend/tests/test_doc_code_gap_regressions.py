"""Regressions for the code/document gaps reproduced on 2026-10-31."""

from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from imeeting import person_extract, persons
from imeeting.db import engine
from imeeting.engine import speaker, voice_match, voice_template
from imeeting.engine.voice_template import Scenario
from imeeting.models import Meeting, Person, Segment, User, Voiceprint, VoiceprintTemplate
from imeeting.security import create_access_token, hash_password


def unit(index=0):
    result = np.zeros(192, dtype=np.float32)
    result[index] = 1
    return result


def setup_pair():
    with Session(engine) as db:
        owner = User(username="audit_owner", display_name="Owner", role="user", password_hash=hash_password("audit-owner-password"), must_change_password=False)
        other = User(username="audit_other", display_name="Other", role="user", password_hash=hash_password("audit-other-password"), must_change_password=False)
        db.add_all([owner, other])
        db.commit()
        return owner.id, other.id


def header(user_id):
    return {"Authorization": f"Bearer {create_access_token(str(user_id), 'user')}"}


def make_voiceprint(owner, *, provisional=False):
    with Session(engine) as db:
        row = Voiceprint(owner_id=owner, name="Audit Voice", embedding=unit().tobytes(), provisional=provisional, speaker_key="audit-key")
        db.add(row)
        db.commit()
        return row.id


def test_foreign_person_cannot_be_deleted(client, monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    owner, other = setup_pair()
    with Session(engine) as db:
        person = Person(owner_id=owner, display_name="Audit Person")
        db.add(person)
        db.commit()
        person_id = person.id
    response = client.delete(f"/api/v1/persons/{person_id}", headers=header(other))
    assert response.status_code in (403, 404), f"foreign delete returned {response.status_code}"


def test_foreign_templates_cannot_be_read(client, monkeypatch):
    monkeypatch.setenv("REQUIRE_AUTH", "1")
    owner, other = setup_pair()
    voice_id = make_voiceprint(owner)
    response = client.get(f"/api/v1/voiceprints/{voice_id}/templates", headers=header(other))
    assert response.status_code in (403, 404), f"foreign read returned {response.status_code}"


def test_unconfirmed_template_is_not_in_global_matching():
    owner, _ = setup_pair()
    voice_id = make_voiceprint(owner)
    with Session(engine) as db:
        voice_template.store_template(db, voiceprint_id=voice_id, embedding=unit(), scenario=Scenario(), quality=0.8, confirmed=False, source_meeting_id=999)
        db.commit()
    _, rows = voice_template.templates_matrix(meeting_id=1000)
    assert not any(row["voiceprint_id"] == voice_id for row in rows), rows


def test_disabled_template_can_be_reenabled():
    owner, _ = setup_pair()
    voice_id = make_voiceprint(owner)
    with Session(engine) as db:
        row = voice_template.store_template(db, voiceprint_id=voice_id, embedding=unit(), scenario=Scenario(), quality=1, confirmed=True)
        db.commit()
        template_id = row.id
        voice_match.set_template_flags(db, template_id=template_id, disabled=True)
        voice_match.set_template_flags(db, template_id=template_id, disabled=False)
        db.refresh(row)
        assert row.slot >= 0 and row.superseded_by is None, f"slot after reenable={row.slot}"


def test_confirming_provisional_identity_makes_it_formal():
    owner, _ = setup_pair()
    voice_id = make_voiceprint(owner, provisional=True)
    with Session(engine) as db:
        voice_match.apply_decision(db, voiceprint_id=voice_id, embedding=unit(), decision="confirm_self", user_id=owner)
        assert db.get(Voiceprint, voice_id).provisional is False


def test_repeated_different_unit_person_is_idempotent():
    owner, _ = setup_pair()
    with Session(engine) as db:
        first, _, _ = persons.upsert_person(db, owner_id=owner, name="李伟", attributes=[{"category": "org", "key": "unit", "value": "单位甲"}])
        second, _, _ = persons.upsert_person(db, owner_id=owner, name="李伟", attributes=[{"category": "org", "key": "unit", "value": "单位乙"}])
        repeated, _, _ = persons.upsert_person(db, owner_id=owner, name="李伟", attributes=[{"category": "org", "key": "unit", "value": "单位乙"}])
        assert repeated.id == second.id, f"IDs: first={first.id}, unit-B={second.id}, repeated-unit-B={repeated.id}"


def test_fuzzy_quote_cannot_introduce_a_hallucinated_name():
    original = "今天我们一起讨论系统建设方案并安排下一阶段的研发任务以及具体工作计划。"
    segment = Segment(meeting_id=1, segment_id="audit-evidence", text=original)
    accepted, _ = person_extract.validate_people([{"name": "赵六", "confidence": 0.9, "evidence": [{"segment_id": segment.segment_id, "quote": original + "我是赵六"}]}], {segment.segment_id: segment})
    assert accepted == [], accepted


def test_malformed_llm_confidence_is_declined():
    segment = Segment(meeting_id=1, segment_id="audit-malformed", text="我是张三")
    try:
        accepted, _ = person_extract.validate_people([{"name": "张三", "confidence": "not-a-number", "evidence": [{"segment_id": segment.segment_id, "quote": segment.text}]}], {segment.segment_id: segment})
    except (ValueError, TypeError) as exc:
        pytest.fail(f"malformed LLM confidence raised {type(exc).__name__}")
    assert accepted == []


def test_repeated_manual_extraction_is_rate_limited(client, monkeypatch):
    owner, _ = setup_pair()
    with Session(engine) as db:
        meeting = Meeting(title="audit-manual-quota", owner_id=owner, status="finished")
        db.add(meeting)
        db.commit()
        meeting_id = meeting.id
        db.add(Segment(meeting_id=meeting_id, segment_id="audit-quota", text="我是张三，今天我们一起讨论系统建设方案并安排下一阶段的研发任务以及具体工作计划。", is_final=True, start_ms=0, end_ms=3000))
        db.commit()
    async def no_llm(*args):
        return [], False
    monkeypatch.setattr(person_extract, "_call_llm", no_llm)
    first = client.post(f"/api/v1/meetings/{meeting_id}/participants/extract", headers=header(owner))
    second = client.post(f"/api/v1/meetings/{meeting_id}/participants/extract", headers=header(owner))
    assert first.status_code == 200
    assert second.status_code == 429, f"back-to-back extraction returned {second.status_code}"


def test_renaming_bound_person_updates_transcript():
    owner, _ = setup_pair()
    with Session(engine) as db:
        meeting = Meeting(title="audit-rename", owner_id=owner)
        db.add(meeting)
        db.flush()
        item = persons.add_manual_participant(db, meeting_id=meeting.id, name="张三", speaker_key="bound-key")
        segment = Segment(meeting_id=meeting.id, segment_id="audit-bound", speaker_key="bound-key", speaker="张三", text="测试", is_final=True)
        db.add(segment)
        db.flush()
        persons.set_participant(db, meeting_id=meeting.id, person_id=item["person_id"], display_name="张建国")
        assert segment.speaker == "张建国", f"stored transcript label={segment.speaker}"


def test_same_meeting_template_update_is_idempotent():
    owner, _ = setup_pair()
    voice_id = make_voiceprint(owner)
    with Session(engine) as db:
        voice_template.store_template(db, voiceprint_id=voice_id, embedding=unit(), scenario=Scenario(), quality=1, sample_ms=1000, confirmed=True)
        vector = 0.95 * unit() + np.sqrt(1 - 0.95 ** 2) * unit(1)
        row = voice_template.store_template(db, voiceprint_id=voice_id, embedding=vector, scenario=Scenario(), quality=1, sample_ms=1000, source_meeting_id=123, source_segment_id="same-source", confirmed=True)
        first = row.embedding
        row = voice_template.store_template(db, voiceprint_id=voice_id, embedding=vector, scenario=Scenario(), quality=1, sample_ms=1000, source_meeting_id=123, source_segment_id="same-source", confirmed=True)
        assert row.embedding == first, "same source changed the centroid a second time"


def test_control_genuine_evidence_is_accepted():
    segment = Segment(meeting_id=1, segment_id="audit-control", text="我是张三")
    accepted, declined = person_extract.validate_people([{"name": "张三", "confidence": 0.9, "evidence": [{"segment_id": segment.segment_id, "quote": segment.text}]}], {segment.segment_id: segment})
    assert len(accepted) == 1 and not declined


def test_bootstrap_does_not_log_administrator_password(monkeypatch, caplog):
    from unittest.mock import MagicMock

    from imeeting import db as database

    fake_session = MagicMock()
    fake_session.exec.return_value.first.return_value = None
    manager = MagicMock()
    manager.__enter__.return_value = fake_session
    monkeypatch.setattr(database, "Session", lambda *args: manager)
    fake_password = "audit-placeholder-never-a-real-secret"
    monkeypatch.setattr(database, "admin_credentials_from_env", lambda: ("audit-admin", fake_password))
    database._ensure_admin()
    assert fake_password not in caplog.text, "bootstrap included the configured password in its log"


def test_single_template_keeps_legacy_identity_result(monkeypatch):
    owner, _ = setup_pair()
    vector = 0.7 * unit() + np.sqrt(1 - 0.7 ** 2) * unit(1)
    with Session(engine) as db:
        row = Voiceprint(owner_id=owner, name="Audit Identity", embedding=vector.astype(np.float32).tobytes(), provisional=False)
        db.add(row)
        db.flush()
        voice_template.store_template(db, voiceprint_id=row.id, embedding=vector, scenario=Scenario(), quality=1, confirmed=True)
        db.commit()
    speaker.invalidate_cache()
    voice_match.invalidate_cache()
    original_get = speaker.get_config
    monkeypatch.setattr(speaker, "get_config", lambda key, *args: False if key == "spk.template_match_enabled" else original_get(key, *args))
    legacy = speaker.match_enrolled(unit(), 0.68)
    monkeypatch.setattr(speaker, "get_config", lambda key, *args: True if key == "spk.template_match_enabled" else original_get(key, *args))
    template = speaker.match_enrolled(unit(), 0.68)
    assert legacy is not None and legacy[0] == "Audit Identity"
    assert template is not None and template[0] == legacy[0], f"legacy={legacy}, template={template}"


def test_database_rejects_six_active_template_slots():
    owner, _ = setup_pair()
    voice_id = make_voiceprint(owner)
    with Session(engine) as db:
        for slot in range(6):
            db.add(VoiceprintTemplate(voiceprint_id=voice_id, slot=slot, embedding=unit().tobytes(), confirmed=True))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return
        pytest.fail("database accepted active slots 0,1,2,3,4,5 for one identity")


def test_voiceprint_with_templates_can_be_deleted_by_its_owner(client):
    owner, _ = setup_pair()
    voice_id = make_voiceprint(owner)
    with Session(engine) as db:
        voice_template.store_template(db, voiceprint_id=voice_id, embedding=unit(), scenario=Scenario(), quality=1, confirmed=True)
        db.commit()
    try:
        response = client.delete(f"/api/v1/voiceprints/{voice_id}", headers=header(owner))
    except IntegrityError:
        pytest.fail("deleting a voiceprint with templates raises a foreign-key IntegrityError")
    assert response.status_code == 204
