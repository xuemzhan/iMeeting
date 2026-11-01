"""自适应参数与声纹身份纠正回归。"""

from __future__ import annotations

import numpy as np
from sqlmodel import Session, select


def test_adaptive_profile_lowers_vad_threshold_and_adds_gain():
    from imeeting.engine.adaptive import AdaptiveMeetingController

    controller = AdaptiveMeetingController()
    energies = [0.0015] * 10 + [0.0038] * 30
    profile = controller.profile
    for energy in energies:
        profile = controller.observe_energy(
            energy, in_speech=energy > 0.002, chunk_seconds=0.6
        )
    assert profile.gain > 1.1
    assert profile.start_threshold < 0.006
    assert profile.continue_threshold < profile.start_threshold
    assert profile.soft_segment_ms <= 16000
    assert profile.max_segment_ms <= 20000

    controller.observe_segment(24000)
    controller.observe_segment(22000)
    for _ in range(16):
        profile = controller.observe_energy(
            0.0038, in_speech=True, chunk_seconds=0.6
        )
    assert profile.soft_segment_ms <= 12000
    assert profile.max_segment_ms <= 18000


def _pin_thresholds(monkeypatch, *, hit: float, suspect: float) -> None:
    """把「命中 / 疑似」两个阈值钉死在本用例要验证的数值上。

    这些用例验证的是**分档逻辑**（命中 / 疑似 / 匿名簇三者的优先级），
    不应该随 CONFIG_SCHEMA 的默认值调整而变红；阈值默认值的正确性
    由 ``test_thresholds_have_single_source_of_truth`` 单独守。
    """
    from imeeting.settings import config_store

    monkeypatch.setitem(config_store._db_overrides, "spk.enrolled_threshold", hit)
    monkeypatch.setitem(config_store._db_overrides, "spk.enrolled_ambiguous_threshold", suspect)


def test_thresholds_have_single_source_of_truth():
    """引擎用的阈值必须等于 CONFIG_SCHEMA 的默认值，不得有第二份字面量。"""
    from imeeting.engine import speaker, speaker_reconcile
    from imeeting.engine.voice_match import _float
    from imeeting.settings import SCHEMA_BY_KEY, config_float

    hit_default = SCHEMA_BY_KEY["spk.enrolled_threshold"].default
    suspect_default = SCHEMA_BY_KEY["spk.enrolled_ambiguous_threshold"].default
    resolver = speaker.SpeakerResolver(threshold=0.5, meeting_id=1)

    assert resolver.enrolled_threshold == hit_default
    assert resolver.ambiguous_threshold == min(hit_default, max(0.5, suspect_default))
    assert config_float("spk.enrolled_threshold") == hit_default
    assert _float("spk.enrolled_threshold") == hit_default
    assert _float("spk.enrolled_ambiguous_threshold") == suspect_default
    assert speaker_reconcile.reconcile_meeting_speakers.__defaults__ is None
    assert all(
        value is None
        for value in speaker_reconcile.reconcile_meeting_speakers.__kwdefaults__.values()
    ), "复核阈值不得再以字面量默认参数形式存在"


def test_thresholds_follow_config_override(monkeypatch):
    """后台改阈值后，引擎必须立刻跟着变（证明只有一处事实来源）。"""
    from imeeting.engine import speaker

    _pin_thresholds(monkeypatch, hit=0.9, suspect=0.85)
    resolver = speaker.SpeakerResolver(threshold=0.5, meeting_id=1)
    assert resolver.enrolled_threshold == 0.9
    assert resolver.ambiguous_threshold == 0.85


def test_ambiguous_global_voiceprint_is_not_confirmed(monkeypatch):
    from imeeting.engine import speaker

    _pin_thresholds(monkeypatch, hit=0.68, suspect=0.58)

    def fake_match(_emb, _threshold, _meeting_id=None, **_kwargs):
        return ("旧姓名", 0.62, 99, "spk_old")

    monkeypatch.setattr(speaker, "match_enrolled", fake_match)
    resolver = speaker.SpeakerResolver(threshold=0.5, meeting_id=1)
    emb = np.ones(192, dtype=np.float32)
    emb /= np.linalg.norm(emb)
    result = resolver.resolve_with_meta(emb)
    assert result.label.startswith("疑似")
    assert result.source == "enrolled_ambiguous"
    assert result.key != "spk_old"


def test_enrolled_identity_wins_over_anonymous_cluster(monkeypatch):
    """匿名簇相似度 0.8 也不能遮蔽 0.93 的已录入陈建峰。"""
    from imeeting.engine import speaker

    def fake_match(_emb, _threshold, _meeting_id=None, **_kwargs):
        return ("陈建峰", 0.93, 22, "spk_6b274da1")

    monkeypatch.setattr(speaker, "match_enrolled", fake_match)
    resolver = speaker.SpeakerResolver(threshold=0.5, meeting_id=1)
    cluster_vec = np.zeros(192, dtype=np.float32)
    cluster_vec[0] = 1.0
    resolver._clusters.append(
        speaker.SpeakerCluster("spk_anon", cluster_vec, "说话人 1")
    )
    emb = np.zeros(192, dtype=np.float32)
    emb[0], emb[1] = 0.8, 0.6
    result = resolver.resolve_with_meta(emb)
    assert result.label == "陈建峰"
    assert result.key == "spk_6b274da1"
    assert result.source == "enrolled"


def test_near_known_identity_is_not_absorbed_by_cluster(monkeypatch):
    """已知 0.64、匿名簇 0.60 时保留“疑似姓名”，不要并入匿名簇。"""
    from imeeting.engine import speaker

    _pin_thresholds(monkeypatch, hit=0.68, suspect=0.58)

    def fake_match(_emb, _threshold, _meeting_id=None, **_kwargs):
        return ("陈建峰", 0.64, 22, "spk_6b274da1")

    monkeypatch.setattr(speaker, "match_enrolled", fake_match)
    resolver = speaker.SpeakerResolver(threshold=0.5, meeting_id=1)
    cluster_vec = np.zeros(192, dtype=np.float32)
    cluster_vec[0] = 1.0
    resolver._clusters.append(
        speaker.SpeakerCluster("spk_anon", cluster_vec, "说话人 1")
    )
    emb = np.zeros(192, dtype=np.float32)
    emb[0], emb[1] = 0.6, 0.8
    result = resolver.resolve_with_meta(emb)
    assert result.label == "疑似 陈建峰"
    assert result.source == "enrolled_ambiguous"
    assert result.key != "spk_anon"

    # 下一段相近语音应复用同一个疑似簇，而不是每段新建“疑似 陈建峰”。
    result2 = resolver.resolve_with_meta(emb)
    assert result2.key == result.key


def test_cluster_wins_when_it_is_much_more_similar(monkeypatch):
    from imeeting.engine import speaker

    def fake_match(_emb, _threshold, _meeting_id=None, **_kwargs):
        return ("陈建峰", 0.60, 22, "spk_6b274da1")

    monkeypatch.setattr(speaker, "match_enrolled", fake_match)
    resolver = speaker.SpeakerResolver(threshold=0.5, meeting_id=1)
    cluster_vec = np.zeros(192, dtype=np.float32)
    cluster_vec[0] = 1.0
    resolver._clusters.append(
        speaker.SpeakerCluster("spk_anon", cluster_vec, "说话人 1")
    )
    emb = np.zeros(192, dtype=np.float32)
    emb[0], emb[1] = 0.95, 0.3122
    result = resolver.resolve_with_meta(emb)
    assert result.label == "说话人 1"
    assert result.key == "spk_anon"


def test_relabel_false_global_match_creates_new_identity(
    client, auth_headers, monkeypatch
):
    from imeeting.db import engine
    from imeeting.models import Meeting, Segment, User, Voiceprint

    vector = np.ones(192, dtype=np.float32)
    vector /= np.linalg.norm(vector)
    with Session(engine) as db:
        user = db.exec(select(User).where(User.username == "admin")).one()
        old = Voiceprint(
            name="李响",
            embedding=vector.tobytes(),
            provisional=False,
            meeting_id=209,
            source_meeting_id=209,
            speaker_key="spk_old",
        )
        meeting = Meeting(title="声纹纠错", owner_id=user.id, status="finished")
        db.add(old)
        db.add(meeting)
        db.commit()
        db.refresh(old)
        db.refresh(meeting)
        db.add(
            Segment(
                meeting_id=meeting.id,
                segment_id="s-speaker-fix",
                text="这是陈建峰老师的讲座。",
                is_final=True,
                start_ms=0,
                end_ms=3000,
                speaker="李响",
                speaker_key="spk_old",
                seq=0,
            )
        )
        db.commit()
        mid = meeting.id
        old_id = old.id

    import imeeting.api.http.routes.voiceprints as vp_router

    monkeypatch.setattr(
        vp_router,
        "_meeting_speaker_vector",
        lambda *_args, **_kwargs: vector.copy(),
    )
    response = client.post(
        "/api/v1/voiceprints/relabel",
        headers=auth_headers,
        json={"meeting_id": mid, "from_label": "李响", "to_name": "陈建峰"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["corrected_false_match"] is True
    assert body["identity_created"] is True
    with Session(engine) as db:
        old = db.get(Voiceprint, old_id)
        assert old is not None and old.name == "李响"
        new = db.exec(
            select(Voiceprint).where(Voiceprint.name == "陈建峰")
        ).first()
        assert new is not None and new.embedding
        segment = db.exec(
            select(Segment).where(Segment.meeting_id == mid)
        ).one()
        assert segment.speaker == "陈建峰"


def test_rebuild_voiceprint_identity_from_meeting(client, auth_headers, monkeypatch):
    from imeeting.db import engine
    from imeeting.models import Meeting, Segment, User, Voiceprint

    vector = np.ones(192, dtype=np.float32)
    vector /= np.linalg.norm(vector)
    with Session(engine) as db:
        user = db.exec(select(User).where(User.username == "admin")).one()
        meeting = Meeting(title="声纹重建", owner_id=user.id, status="finished")
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        db.add(
            Segment(
                meeting_id=meeting.id,
                segment_id="s-rebuild",
                text="重建声纹。",
                is_final=True,
                start_ms=0,
                end_ms=2000,
                speaker="陈建峰",
                speaker_key="spk_rebuild",
                seq=0,
            )
        )
        db.commit()
        mid = meeting.id
    import imeeting.api.http.routes.voiceprints as vp_router

    monkeypatch.setattr(
        vp_router, "_meeting_speaker_vector", lambda *_a, **_k: vector.copy()
    )
    response = client.post(
        "/api/v1/voiceprints/rebuild-identity",
        headers=auth_headers,
        json={"meeting_id": mid, "label": "陈建峰", "name": "陈建峰"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "陈建峰"
    with Session(engine) as db:
        row = db.exec(
            select(Voiceprint).where(Voiceprint.name == "陈建峰")
        ).first()
        assert row is not None and len(row.embedding) == 768
