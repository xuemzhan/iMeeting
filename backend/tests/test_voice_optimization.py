"""Measured query bounds, audio offsets and repeatable voice rebuilds."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import soundfile as sf
import sqlalchemy as sa
from sqlmodel import Session, select

from imeeting.db import engine
from imeeting.engine import speaker, speaker_reconcile, voice_template
from imeeting.models import Meeting, User, Voiceprint, VoiceprintTemplate


def unit(index=0):
    vector = np.zeros(192, dtype=np.float32)
    vector[index] = 1
    return vector


def test_scoped_template_load_has_constant_query_count():
    with Session(engine) as db:
        owner = db.exec(select(User).where(User.role == "admin")).first().id
        meeting = Meeting(owner_id=owner, title="query bound")
        db.add(meeting)
        db.flush()
        for index in range(30):
            voice = Voiceprint(owner_id=owner if index % 2 else None, name=f"query-{index}", embedding=unit().tobytes())
            db.add(voice)
            db.flush()
            db.add(VoiceprintTemplate(voiceprint_id=voice.id, embedding=unit().tobytes(), confirmed=True))
        db.commit()
        meeting_id = meeting.id
    queries = []

    def counted(*args):
        queries.append(args[2])

    sa.event.listen(engine, "before_cursor_execute", counted)
    try:
        matrix, rows = voice_template.templates_matrix(meeting_id)
    finally:
        sa.event.remove(engine, "before_cursor_execute", counted)
    assert len(rows) == 30 and matrix.shape == (30, 192)
    assert len(queries) <= 3, f"one source lookup per template: {len(queries)} queries"


def test_missing_meeting_does_not_disable_identity_filter():
    with Session(engine) as db:
        owner = db.exec(select(User).where(User.role == "admin")).first().id
        voice = Voiceprint(owner_id=owner, name="private", embedding=unit().tobytes())
        db.add(voice)
        db.flush()
        db.add(VoiceprintTemplate(voiceprint_id=voice.id, embedding=unit().tobytes(), confirmed=True))
        db.commit()
        meeting_id = 987654321
    assert voice_template.templates_matrix(meeting_id)[1] == []
    speaker.invalidate_cache()
    assert speaker.load_enrolled(meeting_id) == []


def test_audio_seek_uses_the_files_actual_sample_rate(tmp_path):
    path = tmp_path / "32k.wav"
    audio = np.concatenate([np.full(32000, 0.1), np.full(32000, 0.5)]).astype(np.float32)
    sf.write(path, audio, 32000, subtype="FLOAT")
    excerpt = speaker_reconcile._read_audio(path, 1000, 1500, 1.0)
    assert len(excerpt) == 8000
    assert np.allclose(excerpt, 0.5, atol=1e-5)


def test_model_revision_change_reloads_the_embedding_model(monkeypatch):
    calls = []
    config = {"spk.embedding_model": "fixture/model", "asr.hub": "ms", "asr.model_revision": "v1", "asr.disable_update": True}

    def model(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setitem(sys.modules, "funasr", SimpleNamespace(AutoModel=model))
    monkeypatch.setattr(speaker, "get_config", lambda key: config.get(key))
    monkeypatch.setattr(speaker, "_model", None)
    monkeypatch.setattr(speaker, "_model_name", None)
    first = speaker._load_model()
    assert speaker._load_model() is first
    config["asr.model_revision"] = "v2"
    assert speaker._load_model() is not first
    assert [item["model_revision"] for item in calls] == ["v1", "v2"]
