import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

from imeeting.db import engine
from imeeting.engine import speaker, voice_template
from imeeting.meeting_cleanup import _delete_rows
from imeeting.models import (
    Meeting,
    MeetingParticipant,
    Person,
    PersonAttribute,
    Voiceprint,
    VoiceprintTemplate,
)
from imeeting.settings import config_store
from tests.test_template_rebuild_optimization import _audio, _counting_audio, _meeting, _segments, _voiceprint


@pytest.mark.parametrize(
    "key,value",
    [("asr.model_revision", "review-new-revision"), ("asr.hub", "hf"), ("asr.disable_update", False)],
)
def test_revision_cache(tmp_path, monkeypatch, admin_user, key, value):
    mid = _meeting(admin_user.id, _audio(tmp_path))
    _segments(mid)
    _voiceprint(admin_user.id)
    _counting_audio(monkeypatch)
    voice_template.rebuild_meeting_templates(mid)
    if key == "asr.disable_update":
        config_store.set_overrides({key: True})
        voice_template.rebuild_meeting_templates(mid)
    config_store.set_overrides({key: value})
    result = voice_template.rebuild_meeting_templates(mid)
    assert result["cache_hits"] == 0, result


def test_model_work_does_not_hold_sqlite_write_lock(tmp_path, monkeypatch, admin_user):
    mid = _meeting(admin_user.id, _audio(tmp_path))
    _segments(mid)
    _voiceprint(admin_user.id)
    _counting_audio(monkeypatch)
    seen = []

    def extract(pcm):
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA busy_timeout=1")
            try:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            except OperationalError:
                seen.append("locked")
            finally:
                connection.rollback()
        from tests.test_template_rebuild_optimization import unit

        return unit()

    monkeypatch.setattr(speaker, "extract_embedding", extract)
    voice_template.rebuild_meeting_templates(mid)
    assert not seen, seen


def test_purge_provisional_with_template(tmp_path, admin_user):
    mid = _meeting(admin_user.id, _audio(tmp_path))
    with Session(engine) as db:
        voice = Voiceprint(
            owner_id=admin_user.id, name="temporary", provisional=True, meeting_id=mid, embedding=b""
        )
        db.add(voice)
        db.flush()
        db.add(VoiceprintTemplate(voiceprint_id=voice.id, embedding=b""))
        person = Person(owner_id=admin_user.id, display_name="retained")
        db.add(person)
        db.flush()
        attribute = PersonAttribute(
            person_id=person.id, meeting_id=mid, category="org", key="unit", value="retained unit"
        )
        db.add(attribute)
        db.add(MeetingParticipant(person_id=person.id, meeting_id=mid))
        db.commit()
        attribute_id = attribute.id
        _delete_rows(db, db.get(Meeting, mid))
        db.commit()
        db.expire_all()
        assert db.get(Meeting, mid) is None
        assert db.get(PersonAttribute, attribute_id).meeting_id is None
        assert db.get(PersonAttribute, attribute_id).value == "retained unit"


def test_auto_replay_does_not_overwrite_approved_source(admin_user):
    import numpy as np

    from imeeting.engine.voice_template import Scenario
    from tests.test_template_rebuild_optimization import unit

    with Session(engine) as db:
        voice = Voiceprint(owner_id=admin_user.id, name="approved", embedding=unit().tobytes())
        db.add(voice)
        db.flush()
        row = voice_template.store_template(
            db,
            voiceprint_id=voice.id,
            embedding=unit(0),
            scenario=Scenario(),
            quality=1,
            source_meeting_id=1,
            source_segment_id="s",
            confirmed=True,
        )
        db.commit()
        voice_template.store_template(
            db,
            voiceprint_id=voice.id,
            embedding=unit(1),
            scenario=Scenario(),
            quality=1,
            source_meeting_id=1,
            source_segment_id="s",
            confirmed=False,
        )
        db.flush()
        assert np.allclose(np.frombuffer(row.embedding, dtype=np.float32), unit(0))
        candidates = db.exec(select(VoiceprintTemplate).where(VoiceprintTemplate.confirmed.is_(False))).all()
        assert len(candidates) == 1
        assert np.allclose(np.frombuffer(candidates[0].embedding, dtype=np.float32), unit(1))
        voice_template.store_template(
            db,
            voiceprint_id=voice.id,
            embedding=unit(1),
            scenario=Scenario(),
            quality=1,
            source_meeting_id=1,
            source_segment_id="s",
            confirmed=False,
        )
        assert len(db.exec(select(VoiceprintTemplate)).all()) == 2
