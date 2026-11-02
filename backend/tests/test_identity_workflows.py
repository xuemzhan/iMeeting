"""Cross-object and end-to-end regressions for the identity repair."""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlmodel import Session, create_engine, select

from imeeting import person_extract, persons
from imeeting.application import jobs, person_extraction
from imeeting.db import engine
from imeeting.engine import speaker, voice_match, voice_template
from imeeting.engine.voice_template import Scenario
from imeeting.models import (
    Job,
    Meeting,
    MeetingMember,
    PersonExtractionState,
    Segment,
    Voiceprint,
    VoiceprintConflict,
    VoiceprintTemplate,
    VoiceprintTemplateSource,
    utcnow,
)

from .test_doc_code_gap_regressions import header, make_voiceprint, setup_pair, unit


@pytest.fixture(autouse=True)
def reset_matching_caches():
    speaker.invalidate_cache()
    voice_match.invalidate_cache()
    yield
    speaker.invalidate_cache()
    voice_match.invalidate_cache()


def meeting_with_intro(owner):
    with Session(engine) as db:
        meeting = Meeting(owner_id=owner, title="隔离流程验收", status="finished")
        db.add(meeting)
        db.flush()
        db.add(Segment(meeting_id=meeting.id, segment_id="intro", text="我是张三，今天我们讨论系统设计的工作计划，并安排后续研发和验收任务。", is_final=True, start_ms=0, end_ms=5000, speaker="说话人 1", speaker_key="spk-intro"))
        db.commit()
        return meeting.id


async def grounded_llm(*args):
    return [{"name": "张三", "confidence": 0.9, "evidence": [{"segment_id": "intro", "quote": "我是张三"}]}], False


def test_foreign_source_meeting_rejected_before_audio_or_model(client, monkeypatch):
    owner, other = setup_pair()
    meeting_id = meeting_with_intro(owner)
    voice_id = make_voiceprint(other)
    monkeypatch.setattr(speaker, "extract_embedding", lambda *_: pytest.fail("must authorize before model work"))
    response = client.post("/api/v1/voiceprints/confirm-sample", headers=header(other), json={"voiceprint_id": voice_id, "meeting_id": meeting_id, "segment_id": "intro"})
    assert response.status_code == 403


def test_foreign_merge_and_template_mutation_rejected(client):
    owner, other = setup_pair()
    first = make_voiceprint(owner)
    second = make_voiceprint(owner)
    for path in (f"/api/v1/voiceprints/{first}/merge/{second}", "/api/v1/voiceprints/templates/backfill"):
        assert client.post(path, headers=header(other)).status_code == 403
    assert client.patch(f"/api/v1/voiceprints/{first}", headers=header(other), json={"name": "changed"}).status_code == 403


def test_matchers_filter_the_meeting_owner():
    owner, other = setup_pair()
    first, second = make_voiceprint(owner), make_voiceprint(other)
    meeting_id = meeting_with_intro(other)
    with Session(engine) as db:
        for voice_id in (first, second):
            voice_template.store_template(db, voiceprint_id=voice_id, embedding=unit(), scenario=Scenario(), quality=1, confirmed=True)
        db.commit()
    _, rows = voice_template.templates_matrix(meeting_id)
    assert {row["voiceprint_id"] for row in rows} == {second}
    speaker.invalidate_cache()
    assert {row[2] for row in speaker.load_enrolled(meeting_id)} == {second}


def test_editor_can_correct_transcript_but_cannot_rename_private_biometric(client):
    owner, editor = setup_pair()
    meeting_id = meeting_with_intro(owner)
    voice_id = make_voiceprint(owner)
    with Session(engine) as db:
        db.add(MeetingMember(meeting_id=meeting_id, user_id=editor, role="editor"))
        voice = db.get(Voiceprint, voice_id)
        voice.name = "说话人 1"
        voice.speaker_key = "spk-intro"
        db.add(voice)
        db.commit()
    response = client.post("/api/v1/voiceprints/relabel", headers=header(editor), json={"meeting_id": meeting_id, "from_label": "说话人 1", "to_name": "张三"})
    assert response.status_code == 200
    assert not response.json()["renamed_voiceprint"]
    with Session(engine) as db:
        assert db.get(Voiceprint, voice_id).name == "说话人 1"
        assert db.exec(select(Segment).where(Segment.meeting_id == meeting_id)).first().speaker == "张三"


def test_disabled_template_preserves_slot_and_retired_cannot_resume():
    owner, _ = setup_pair()
    voice_id = make_voiceprint(owner)
    with Session(engine) as db:
        row = voice_template.store_template(db, voiceprint_id=voice_id, embedding=unit(), scenario=Scenario(), quality=1, confirmed=True)
        db.commit()
        voice_match.set_template_flags(db, template_id=row.id, disabled=True)
        assert row.slot == 0 and row.disabled
        voice_match.set_template_flags(db, template_id=row.id, disabled=False)
        assert row.slot == 0 and not row.disabled
        row.superseded_by = 999
        db.add(row)
        db.commit()
        with pytest.raises(ValueError, match="替代"):
            voice_match.set_template_flags(db, template_id=row.id, disabled=False)


def test_automatic_candidate_cannot_change_an_approved_template():
    owner, _ = setup_pair()
    voice_id = make_voiceprint(owner)
    with Session(engine) as db:
        approved = voice_template.store_template(db, voiceprint_id=voice_id, embedding=unit(), scenario=Scenario(), quality=1, confirmed=True)
        original = approved.embedding
        candidate = voice_template.store_template(db, voiceprint_id=voice_id, embedding=0.98 * unit() + 0.2 * unit(1), scenario=Scenario(), quality=1, source_meeting_id=123, source_segment_id="candidate", confirmed=False)
        assert candidate.id != approved.id and not candidate.confirmed
        assert approved.embedding == original


def test_reordered_source_replay_is_stable_and_tracks_duration():
    owner, _ = setup_pair()
    voice_id = make_voiceprint(owner)
    with Session(engine) as db:
        vector = 0.99 * unit() + 0.1 * unit(1)
        first = voice_template.store_template(db, voiceprint_id=voice_id, embedding=unit(), scenario=Scenario(), quality=1, sample_ms=3000, source_meeting_id=123, source_segment_id="a", confirmed=True)
        voice_template.store_template(db, voiceprint_id=voice_id, embedding=vector, scenario=Scenario(), quality=1, sample_ms=5000, source_meeting_id=123, source_segment_id="b", confirmed=True)
        original = first.embedding
        voice_template.store_template(db, voiceprint_id=voice_id, embedding=vector, scenario=Scenario(), quality=1, sample_ms=5000, source_meeting_id=123, source_segment_id="b", confirmed=True)
        voice_template.store_template(db, voiceprint_id=voice_id, embedding=unit(), scenario=Scenario(), quality=1, sample_ms=3000, source_meeting_id=123, source_segment_id="a", confirmed=True)
        assert first.embedding == original and first.sample_ms == 8000
        assert len(db.exec(select(VoiceprintTemplateSource)).all()) == 2


def test_explicit_replacement_uses_the_selected_slot():
    owner, _ = setup_pair()
    voice_id = make_voiceprint(owner)
    with Session(engine) as db:
        for index in range(5):
            voice_template.store_template(db, voiceprint_id=voice_id, embedding=unit(index), scenario=Scenario(), quality=1, confirmed=True)
        db.commit()
        result = voice_match.apply_decision(db, voiceprint_id=voice_id, embedding=unit(6), decision="confirm_self", slot=3, user_id=owner)
        assert result["slot"] == 3
        assert len(voice_template.active_templates(db, voice_id)) == 5


def test_cancel_decision_replay_is_idempotent():
    owner, _ = setup_pair()
    voice_id = make_voiceprint(owner)
    with Session(engine) as db:
        assessment = voice_match.evaluate_confirm(db, voiceprint_id=voice_id, embedding=unit(1))
        first = voice_match.apply_decision(db, voiceprint_id=voice_id, embedding=unit(1), decision="cancel", conflict_id=assessment["conflict_id"], user_id=owner)
        second = voice_match.apply_decision(db, voiceprint_id=voice_id, embedding=unit(1), decision="cancel", conflict_id=assessment["conflict_id"], user_id=owner)
        assert first["status"] == second["status"] == "cancelled"
        assert second["already_decided"]
        assert not voice_template.active_templates(db, voice_id)


def test_llm_call_does_not_hold_the_write_transaction(monkeypatch):
    owner, _ = setup_pair()
    meeting_id = meeting_with_intro(owner)

    async def concurrent_write(*args):
        with Session(engine) as db:
            meeting = db.get(Meeting, meeting_id)
            meeting.title = "LLM期间仍可提交"
            db.add(meeting)
            db.commit()
        return await grounded_llm(*args)

    monkeypatch.setattr(person_extract, "_call_llm", concurrent_write)
    result = asyncio.run(person_extract.extract_people(meeting_id, manual=True))
    assert result["created"] == 1


def test_concurrent_manual_extraction_has_only_one_inflight_call(monkeypatch):
    owner, _ = setup_pair()
    meeting_id = meeting_with_intro(owner)
    calls = []

    async def slow_llm(*args):
        calls.append(args)
        await asyncio.sleep(0.05)
        return await grounded_llm(*args)

    async def run():
        return await asyncio.gather(person_extract.extract_people(meeting_id, manual=True), person_extract.extract_people(meeting_id, manual=True), return_exceptions=True)

    monkeypatch.setattr(person_extract, "_call_llm", slow_llm)
    results = asyncio.run(run())
    assert len(calls) == 1
    assert sum(isinstance(result, HTTPException) and result.status_code == 429 for result in results) == 1


def test_periodic_zero_does_not_disable_intro_trigger(monkeypatch):
    owner, _ = setup_pair()
    meeting_id = meeting_with_intro(owner)
    original = person_extraction.get_config
    monkeypatch.setattr(person_extraction, "get_config", lambda key: 0 if key == "person.extract_interval_minutes" else original(key))
    assert person_extraction.request_auto(meeting_id, trigger="periodic") is None
    assert person_extraction.request_auto(meeting_id, text="我是张三") is not None


def test_automatic_trigger_runs_a_persistent_job_and_deduplicates(monkeypatch):
    owner, _ = setup_pair()
    meeting_id = meeting_with_intro(owner)
    monkeypatch.setattr(person_extract, "_call_llm", grounded_llm)
    job = person_extraction.request_auto(meeting_id, trigger="opening")
    assert person_extraction.request_auto(meeting_id, text="我是张三").id == job.id
    asyncio.run(jobs._run_job(job))
    with Session(engine) as db:
        assert db.get(Job, job.id).status == "succeeded"
        state = db.get(PersonExtractionState, meeting_id)
        assert state.auto_count == 1 and state.manual_count == 0 and not state.lease_token
        assert persons.list_participants(db, meeting_id)[0]["speaker"] == "张三"
    assert person_extraction.request_auto(meeting_id, text="我是张三") is None


def test_manual_quota_is_persisted_and_cannot_be_bypassed(monkeypatch):
    owner, _ = setup_pair()
    meeting_id = meeting_with_intro(owner)
    monkeypatch.setattr(person_extract, "_call_llm", grounded_llm)
    for _ in range(10):
        asyncio.run(person_extract.extract_people(meeting_id, manual=True))
        with Session(engine) as db:
            state = db.get(PersonExtractionState, meeting_id)
            state.last_manual_at = utcnow() - timedelta(seconds=31)
            db.add(state)
            db.commit()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(person_extract.extract_people(meeting_id, manual=True))
    assert exc.value.status_code == 429


def test_three_failures_switch_to_rules_without_another_llm_call(monkeypatch):
    owner, _ = setup_pair()
    meeting_id = meeting_with_intro(owner)
    with Session(engine) as db:
        db.add(PersonExtractionState(meeting_id=meeting_id, failures=3))
        db.commit()
    async def forbidden(*args):
        pytest.fail("circuit must use rule fallback")
    monkeypatch.setattr(person_extract, "_call_llm", forbidden)
    assert asyncio.run(person_extract.extract_people(meeting_id, manual=True))["degraded"] is True


def test_reverse_order_rebind_frees_unique_key_before_assignment():
    owner, _ = setup_pair()
    meeting_id = meeting_with_intro(owner)
    with Session(engine) as db:
        first = persons.add_manual_participant(db, meeting_id=meeting_id, name="张三")
        second = persons.add_manual_participant(db, meeting_id=meeting_id, name="李四", speaker_key="spk-intro")
        persons.bind_speaker(db, meeting_id=meeting_id, person_id=first["person_id"], speaker_key="spk-intro")
        db.commit()
        participants = {item["person_id"]: item for item in persons.list_participants(db, meeting_id)}
        assert participants[first["person_id"]]["speaker_key"] == "spk-intro"
        assert participants[second["person_id"]]["speaker_key"] is None


def test_rebinding_keeps_the_previous_speaker_label():
    """解绑/改绑只改当前标注，原标注必须留在 speaker_previous。

    审查第 5 节第 3 条：旧实现把旧 key 的片段就地写成「未确认说话人」，
    原标注（「郭睿」）从此不存在于任何地方，反复改标定会把文本证据抹干净。
    """
    owner, _ = setup_pair()
    meeting_id = meeting_with_intro(owner)
    with Session(engine) as db:
        person = persons.add_manual_participant(db, meeting_id=meeting_id, name="郭睿", speaker_key="spk-keep")
        db.commit()
        for seg in db.exec(select(Segment).where(Segment.meeting_id == meeting_id)).all():
            seg.speaker_key = "spk-keep"
            seg.speaker = "郭睿"
            db.add(seg)
        db.commit()

        persons.bind_speaker(db, meeting_id=meeting_id, person_id=person["person_id"], speaker_key=None)
        db.commit()

        segments = db.exec(select(Segment).where(Segment.meeting_id == meeting_id)).all()
        assert segments
        for seg in segments:
            assert seg.speaker == "未确认说话人"
            assert seg.speaker_source == "unknown"
            assert seg.speaker_previous == "郭睿", "原标注必须保留，不能被抹掉"



def test_speaker_previous_migration_round_trip(tmp_path):
    """新增列必须能在老库上补上、也能降级掉，且默认值为空串。"""
    migration_root = Path(__file__).resolve().parents[1] / "migrations/versions"
    isolated = create_engine(f"sqlite:///{(tmp_path / 'previous.db').as_posix()}")
    with isolated.begin() as conn:
        conn.execute(sa.text("CREATE TABLE segment (id INTEGER PRIMARY KEY, meeting_id INTEGER)"))
        spec = importlib.util.spec_from_file_location(
            "segment_speaker_previous", migration_root / "20261102_01_segment_speaker_previous.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        assert "speaker_previous" in {column["name"] for column in sa.inspect(conn).get_columns("segment")}
        conn.execute(sa.text("INSERT INTO segment(meeting_id) VALUES (1)"))
        assert conn.execute(sa.text("SELECT speaker_previous FROM segment")).scalar() == ""
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
        assert "speaker_previous" not in {column["name"] for column in sa.inspect(conn).get_columns("segment")}
    isolated.dispose()

def test_matching_respects_callers_stricter_threshold():
    owner, _ = setup_pair()
    voice_id = make_voiceprint(owner)
    with Session(engine) as db:
        voice_template.store_template(db, voiceprint_id=voice_id, embedding=0.7 * unit() + np.sqrt(0.51) * unit(1), scenario=Scenario(), quality=1, confirmed=True)
        db.commit()
    from unittest.mock import patch
    with patch.object(speaker, "get_config", return_value=True):
        voice_match.invalidate_cache()
        assert speaker.match_enrolled(unit(), 0.8) is None


def test_existing_sqlite_migration_protects_sixth_slot(tmp_path):
    migration_root = Path(__file__).resolve().parents[1] / "migrations/versions"
    isolated = create_engine(f"sqlite:///{(tmp_path / 'migration.db').as_posix()}")
    with isolated.begin() as conn:
        conn.execute(sa.text("CREATE TABLE voiceprint (id INTEGER PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE meeting (id INTEGER PRIMARY KEY)"))
        for filename in ("20261031_03_voiceprint_templates.py", "20261031_04_identity_integrity.py"):
            spec = importlib.util.spec_from_file_location(filename[:-3], migration_root / filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with Operations.context(MigrationContext.configure(conn)):
                module.upgrade()
        assert "disabled" in {column["name"] for column in sa.inspect(conn).get_columns("voiceprint_template")}
        assert "ix_voiceprint_template_disabled" in {index["name"] for index in sa.inspect(conn).get_indexes("voiceprint_template")}
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(sa.text("INSERT INTO voiceprint_template(voiceprint_id,slot,embedding) VALUES(1,5,X'00')"))
        conn.execute(sa.text("INSERT INTO voiceprint_template(voiceprint_id,slot,embedding) VALUES(1,0,X'00')"))
        with pytest.raises(sa.exc.IntegrityError):
            conn.execute(sa.text("UPDATE voiceprint_template SET slot=5"))
        with Operations.context(MigrationContext.configure(conn)):
            module.downgrade()
            assert not sa.inspect(conn).has_table("person_extraction_state")
            module.upgrade()
    isolated.dispose()


def test_bounded_template_cache():
    voice_match.invalidate_cache()
    for meeting_id in range(1000, 1040):
        voice_match._load(meeting_id)
    assert len(voice_match._CACHE) <= 32


def test_nonfinite_and_nested_invalid_llm_fields_do_not_raise():
    segment = Segment(meeting_id=1, segment_id="safe", text="我是张三")
    for confidence in (None, True, float("nan"), float("inf"), "0.9"):
        accepted, declined = person_extract.validate_people([{"name": "张三", "confidence": confidence, "evidence": [{"segment_id": "safe", "quote": segment.text}]}], {"safe": segment})
        assert not accepted and declined
    accepted, _ = person_extract.validate_people([{"name": "张三", "confidence": 0.9, "attributes": [None, "wrong"], "evidence": [{"segment_id": "safe", "quote": segment.text}]}], {"safe": segment})
    assert accepted[0]["attributes"] == []


@pytest.mark.parametrize("decision", ["cancel", "confirm_self", "reassign"])
def test_real_transcript_conflict_requires_choice_and_keeps_other_identity(client, monkeypatch, decision):
    from imeeting.api.http.routes import voiceprints

    owner, _ = setup_pair()
    meeting_id = meeting_with_intro(owner)
    source_id, target_id = make_voiceprint(owner, provisional=True), make_voiceprint(owner)
    with Session(engine) as db:
        source, target = db.get(Voiceprint, source_id), db.get(Voiceprint, target_id)
        source.name, source.speaker_key, source.meeting_id = "说话人 1", "spk-intro", meeting_id
        target.name, target.speaker_key, target.embedding = "张三", "known-target", unit(1).tobytes()
        db.add_all([source, target])
        db.commit()
    monkeypatch.setattr(voiceprints, "_meeting_speaker_vector", lambda *_: unit())
    body = {"meeting_id": meeting_id, "from_label": "说话人 1", "to_name": "张三"}
    response = client.post("/api/v1/voiceprints/relabel", headers=header(owner), json=body)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["kind"] == "voiceprint_confirmation"
    with Session(engine) as db:
        assert db.exec(select(Segment).where(Segment.meeting_id == meeting_id)).first().speaker == "说话人 1"
        assert not db.exec(select(VoiceprintTemplate)).all()
    body.update(decision=decision, conflict_id=detail["assessment"]["conflict_id"])
    if decision == "reassign":
        body["target_voiceprint_id"] = source_id
    response = client.post("/api/v1/voiceprints/relabel", headers=header(owner), json=body)
    assert response.status_code == 200, response.text
    expected_name = "张三" if decision == "confirm_self" else "说话人 1"
    assert response.json()["name"] == expected_name
    with Session(engine) as db:
        assert db.get(Voiceprint, target_id).name == "张三"
        assert db.exec(select(Segment).where(Segment.meeting_id == meeting_id)).first().speaker == expected_name
        templates = db.exec(select(VoiceprintTemplate)).all()
        assert len(templates) == (0 if decision == "cancel" else 1)
        if templates:
            assert templates[0].voiceprint_id == (source_id if decision == "reassign" else target_id)


def test_repeated_sample_decision_does_not_create_pending_conflicts(client):
    owner, _ = setup_pair()
    first, second = make_voiceprint(owner), make_voiceprint(owner)
    with Session(engine) as db:
        voice_template.store_template(db, voiceprint_id=first, embedding=unit(0), scenario=Scenario(), quality=1, confirmed=True)
        voice_template.store_template(db, voiceprint_id=second, embedding=unit(1), scenario=Scenario(), quality=1, confirmed=True)
        result = voice_match.evaluate_confirm(db, voiceprint_id=first, embedding=unit(1))
        db.commit()
        conflict_id = result["conflict_id"]
    body = {"voiceprint_id": first, "conflict_id": conflict_id, "decision": "cancel"}
    for _ in range(2):
        response = client.post("/api/v1/voiceprints/confirm-sample", headers=header(owner), json=body)
        assert response.status_code == 200, response.text
    with Session(engine) as db:
        conflicts = db.exec(select(VoiceprintConflict)).all()
        assert len(conflicts) == 1 and conflicts[0].status == "cancelled"


def test_full_target_requires_replacement_and_source_replay_preserves_templates():
    owner, _ = setup_pair()
    voice_id = make_voiceprint(owner)
    with Session(engine) as db:
        for index in range(5):
            voice_template.store_template(db, voiceprint_id=voice_id, embedding=unit(index), scenario=Scenario(), quality=1, confirmed=True)
        db.commit()
        with pytest.raises(ValueError, match="模板已满"):
            voice_match.apply_decision(db, voiceprint_id=voice_id, embedding=unit(6), decision="confirm_self")
        db.rollback()
        result = voice_match.apply_decision(db, voiceprint_id=voice_id, embedding=unit(6), decision="confirm_self", slot=1, meeting_id=1, segment_id="new")
        before = [(row.id, row.slot, row.embedding) for row in voice_template.active_templates(db, voice_id)]
        replay = voice_match.apply_decision(db, voiceprint_id=voice_id, embedding=unit(6), decision="confirm_self", slot=1, meeting_id=1, segment_id="new")
        after = [(row.id, row.slot, row.embedding) for row in voice_template.active_templates(db, voice_id)]
        assert result["template_id"] == replay["template_id"] and before == after


def test_merge_person_syncs_compatibility_projection():
    owner, _ = setup_pair()
    meeting_id = meeting_with_intro(owner)
    with Session(engine) as db:
        target, _, _ = persons.upsert_person(db, owner_id=owner, name="张三", meeting_id=meeting_id)
        source, _, _ = persons.upsert_person(db, owner_id=owner, name="张工", meeting_id=meeting_id)
        persons.sync_participants_string(db, meeting_id=meeting_id)
        db.commit()
        persons.merge_persons(db, target_id=target.id, source_id=source.id)
        db.commit()
        assert db.get(Meeting, meeting_id).participants == "张三"


def test_introduction_arriving_during_extraction_is_queued_for_followup(monkeypatch):
    owner, _ = setup_pair()
    meeting_id = meeting_with_intro(owner)
    monkeypatch.setattr(person_extract, "_call_llm", grounded_llm)
    queued = person_extraction.request_auto(meeting_id, text="我是张三")
    job = jobs._claim_job(heavy=False)
    assert job.id == queued.id

    async def with_late_intro(*args):
        with Session(engine) as db:
            db.add(Segment(meeting_id=meeting_id, segment_id="late-intro", text="我是李四", is_final=True, start_ms=6000, end_ms=9000))
            db.commit()
        assert person_extraction.request_auto(meeting_id, text="我是李四").id == job.id
        return await grounded_llm(*args)

    monkeypatch.setattr(person_extract, "_call_llm", with_late_intro)
    asyncio.run(jobs._run_job(job))
    with Session(engine) as db:
        pending = db.exec(select(Job).where(Job.kind == "person_extract", Job.status == "pending")).all()
        assert len(pending) == 1 and pending[0].id != job.id
    # The five-minute throttle defers the new job without consuming a retry.
    followup = jobs._claim_job(heavy=False)
    asyncio.run(jobs._run_job(followup))
    with Session(engine) as db:
        state = db.get(PersonExtractionState, meeting_id)
        row = db.get(Job, followup.id)
        assert row.status == "pending" and row.attempts == 0 and state.auto_count == 1


def test_same_name_rebuild_cannot_reuse_foreign_identity(client, monkeypatch):
    from imeeting.api.http.routes import voiceprints

    owner, other = setup_pair()
    foreign_id = make_voiceprint(owner)
    meeting_id = meeting_with_intro(other)
    monkeypatch.setattr(voiceprints, "_meeting_speaker_vector", lambda *_: unit(2))
    response = client.post("/api/v1/voiceprints/rebuild-identity", headers=header(other), json={"meeting_id": meeting_id, "name": "Audit Voice", "label": "说话人 1"})
    assert response.status_code == 200, response.text
    assert response.json()["id"] != foreign_id
    with Session(engine) as db:
        assert db.get(Voiceprint, response.json()["id"]).owner_id == other
        assert db.get(Voiceprint, foreign_id).embedding == unit().tobytes()


def test_conflict_list_hides_foreign_meeting_and_other_identity(client):
    owner, other = setup_pair()
    voice_id, foreign_id = make_voiceprint(owner), make_voiceprint(other)
    foreign_meeting = meeting_with_intro(other)
    with Session(engine) as db:
        db.add_all([
            VoiceprintConflict(voiceprint_id=voice_id, meeting_id=foreign_meeting, sample_embedding=unit().tobytes()),
            VoiceprintConflict(voiceprint_id=voice_id, other_voiceprint_id=foreign_id, sample_embedding=unit().tobytes()),
        ])
        db.commit()
    response = client.get("/api/v1/voiceprints/conflicts", headers=header(owner))
    assert response.status_code == 200 and response.json() == []


def test_viewer_cannot_change_speaker_or_batch_confirm(client):
    owner, viewer = setup_pair()
    meeting_id = meeting_with_intro(owner)
    with Session(engine) as db:
        db.add(MeetingMember(meeting_id=meeting_id, user_id=viewer, role="viewer"))
        db.commit()
    assert client.post("/api/v1/voiceprints/relabel", headers=header(viewer), json={"meeting_id": meeting_id, "from_label": "说话人 1", "to_name": "张三"}).status_code == 403
    assert client.post(f"/api/v1/meetings/{meeting_id}/participants/confirm-all", headers=header(viewer)).status_code == 403
