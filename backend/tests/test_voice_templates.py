"""多场景声纹模板与确认冲突（REQ-002）。

覆盖：场景分桶、聚类上限与差异化、slot 上限与替换、冲突分级、
三选一落库、以及接口层行为。
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlmodel import Session, select

from imeeting.db import engine
from imeeting.engine import speaker, speaker_reconcile, voice_match, voice_template
from imeeting.engine.voice_template import Scenario, scenario_of
from imeeting.models import (
    Meeting,
    Segment,
    User,
    Voiceprint,
    VoiceprintConflict,
    VoiceprintTemplate,
)

DIM = 192


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _unit(index: int) -> np.ndarray:
    vec = np.zeros(DIM, dtype=np.float32)
    vec[index % DIM] = 1.0
    return vec


def _mix(a: float, b: float) -> np.ndarray:
    vec = a * _unit(0) + b * _unit(1)
    return (vec / float(np.linalg.norm(vec))).astype(np.float32)


def _noise(seed: int, scale: float = 0.02) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(DIM) * scale).astype(np.float32)


def _admin_id() -> int:
    with Session(engine) as db:
        row = db.exec(select(User).where(User.role == "admin")).first()
        assert row is not None
        return row.id


def _make_voiceprint(name: str, vector: np.ndarray) -> int:
    with Session(engine) as db:
        row = Voiceprint(
            owner_id=_admin_id(),
            name=name,
            embedding=np.asarray(vector, dtype=np.float32).tobytes(),
            sample_ms=5000,
            sample_count=1,
            provisional=False,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _add_template(voiceprint_id: int, vector: np.ndarray, quality: float = 0.8) -> int:
    with Session(engine) as db:
        row = voice_template.store_template(
            db,
            voiceprint_id=voiceprint_id,
            embedding=vector,
            scenario=Scenario(distance="near", noise="quiet", voice="normal"),
            quality=quality,
            confirmed=True,
        )
        db.commit()
        return row.id


@pytest.fixture(autouse=True)
def _reset_cache():
    voice_match.invalidate_cache()
    yield
    voice_match.invalidate_cache()


# ---------------------------------------------------------------------------
# 场景分桶
# ---------------------------------------------------------------------------


class TestScenario:
    def test_noise_buckets(self):
        assert scenario_of(snr_db=30).noise == "quiet"
        assert scenario_of(snr_db=20).noise == "reverb"
        assert scenario_of(snr_db=5).noise == "noisy"

    def test_distance_buckets(self):
        assert scenario_of(rms=0.2).distance == "near"
        assert scenario_of(rms=0.03).distance == "mid"
        assert scenario_of(rms=0.001).distance == "far"

    def test_voice_rate_buckets(self):
        assert scenario_of(speech_ms=2000, chars=14).voice == "fast"
        assert scenario_of(speech_ms=4000, chars=8).voice == "low"
        assert scenario_of(speech_ms=3000, chars=12).voice == "normal"

    def test_match_score(self):
        base = Scenario(device="local_mic", distance="near", noise="quiet", voice="normal")
        same = Scenario(device="local_mic", distance="near", noise="quiet", voice="normal")
        one_diff = Scenario(device="local_mic", distance="far", noise="quiet", voice="normal")
        assert base.match_score(same) == 1.0
        assert base.match_score(one_diff) == pytest.approx(0.6)
        assert base.match_score(Scenario()) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# 聚类
# ---------------------------------------------------------------------------


class TestClustering:
    def test_respects_max_k(self):
        vectors = []
        for group in range(8):
            base = _unit(group)
            for offset in range(3):
                vectors.append(base + _noise(group * 10 + offset))
        clusters = voice_template.cluster_vectors(vectors, max_k=5)
        assert len(clusters) <= 5
        assert sum(len(item["members"]) for item in clusters) == len(vectors)

    def test_keeps_distinct_groups_apart(self):
        vectors = [
            _unit(0) + _noise(1),
            _unit(0) + _noise(2),
            _unit(1) + _noise(3),
            _unit(1) + _noise(4),
            _unit(2) + _noise(5),
        ]
        clusters = voice_template.cluster_vectors(vectors, max_k=5)
        assert len(clusters) == 3
        centroids = [item["centroid"] for item in clusters]
        for i in range(len(centroids)):
            for j in range(i + 1, len(centroids)):
                assert voice_template.cosine(centroids[i], centroids[j]) < 0.5


# ---------------------------------------------------------------------------
# 模板上限与替换（AC-1 / AC-2 / AC-11）
# ---------------------------------------------------------------------------


class TestTemplateSlots:
    def test_five_slots_then_replace_weakest(self):
        voiceprint_id = _make_voiceprint("张三", _unit(0))
        qualities = [0.9, 0.8, 0.7, 0.6, 0.2]
        for index, quality in enumerate(qualities):
            _add_template(voiceprint_id, _unit(index + 10), quality)
        with Session(engine) as db:
            active = voice_template.active_templates(db, voiceprint_id)
        assert len(active) == 5
        assert sorted(row.slot for row in active) == [0, 1, 2, 3, 4]

        # 第 6 条：替换质量最低的那条，总量仍为 5
        new_id = _add_template(voiceprint_id, _unit(90), 0.95)
        with Session(engine) as db:
            active = voice_template.active_templates(db, voiceprint_id)
            rows = db.exec(
                select(VoiceprintTemplate).where(
                    VoiceprintTemplate.voiceprint_id == voiceprint_id
                )
            ).all()
        assert len(active) == 5
        assert new_id in {row.id for row in active}
        retired = [row for row in rows if row.superseded_by is not None]
        assert len(retired) == 1
        assert retired[0].slot < 0
        assert retired[0].id not in {row.id for row in active}

    def test_similar_embedding_updates_in_place(self):
        voiceprint_id = _make_voiceprint("李四", _unit(0))
        first = _add_template(voiceprint_id, _unit(3), 0.5)
        second = _add_template(voiceprint_id, _unit(3) + _noise(7, 0.01), 0.9)
        with Session(engine) as db:
            active = voice_template.active_templates(db, voiceprint_id)
        assert first == second
        assert len(active) == 1
        assert active[0].quality == pytest.approx(0.9)

    def test_legacy_backfill(self):
        voiceprint_id = _make_voiceprint("王五", _unit(5))
        with Session(engine) as db:
            db.exec(
                select(VoiceprintTemplate).where(
                    VoiceprintTemplate.voiceprint_id == voiceprint_id
                )
            ).all()
        written = voice_template.backfill_legacy_templates()
        assert written >= 1
        assert voice_template.backfill_legacy_templates() == 0
        with Session(engine) as db:
            rows = db.exec(
                select(VoiceprintTemplate).where(
                    VoiceprintTemplate.voiceprint_id == voiceprint_id
                )
            ).all()
        assert len(rows) == 1 and rows[0].slot == 0


# ---------------------------------------------------------------------------
# 冲突分级（AC-3 / AC-4）
# ---------------------------------------------------------------------------


class TestConflictLevels:
    def _pair(self):
        mine = _make_voiceprint("张三", _unit(0))
        other = _make_voiceprint("王强", _unit(1))
        _add_template(mine, _unit(0))
        _add_template(other, _unit(1))
        voice_match.invalidate_cache()
        return mine, other

    def test_close_to_self_is_silent(self):
        mine, _ = self._pair()
        with Session(engine) as db:
            result = voice_match.evaluate_confirm(
                db, voiceprint_id=mine, embedding=_mix(0.98, 0.05)
            )
            db.commit()
        assert result["level"] == "none"
        assert result["blocking"] is False
        assert result["conflict_id"] is None

    def test_looks_like_someone_else_is_high_conflict(self):
        mine, other = self._pair()
        with Session(engine) as db:
            result = voice_match.evaluate_confirm(
                db, voiceprint_id=mine, embedding=_mix(0.3, 0.95)
            )
            db.commit()
        assert result["level"] == "high"
        assert result["blocking"] is True
        assert result["other"]["voiceprint_id"] == other
        assert result["other"]["name"] == "王强"
        assert result["self_score"] < 0.45
        assert result["other"]["score"] >= 0.6

    def test_no_self_template_is_medium(self):
        mine = _make_voiceprint("新人", _unit(7))
        other = _make_voiceprint("老人", _unit(8))
        _add_template(other, _unit(8))
        voice_match.invalidate_cache()
        with Session(engine) as db:
            result = voice_match.evaluate_confirm(
                db, voiceprint_id=mine, embedding=_mix(0.2, 0.9)
            )
            db.commit()
        assert result["level"] == "medium"
        assert result["blocking"] is False

    def test_conflict_record_persisted(self):
        mine, _ = self._pair()
        with Session(engine) as db:
            voice_match.evaluate_confirm(db, voiceprint_id=mine, embedding=_mix(0.3, 0.95))
            db.commit()
            rows = db.exec(
                select(VoiceprintConflict).where(
                    VoiceprintConflict.voiceprint_id == mine
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].status == "pending"
        assert rows[0].reason == "both"


# ---------------------------------------------------------------------------
# 三选一（AC-5 / AC-6）
# ---------------------------------------------------------------------------


class TestDecisions:
    def test_confirm_self_writes_template(self):
        mine = _make_voiceprint("张三", _unit(0))
        with Session(engine) as db:
            result = voice_match.apply_decision(
                db,
                voiceprint_id=mine,
                embedding=_unit(0),
                decision="confirm_self",
            )
        assert result["template_id"] is not None
        with Session(engine) as db:
            rows = voice_template.active_templates(db, mine)
        assert len(rows) == 1 and rows[0].confirmed is True

    def test_reassign_moves_sample_to_target(self):
        mine = _make_voiceprint("张三", _unit(0))
        other = _make_voiceprint("王强", _unit(1))
        with Session(engine) as db:
            result = voice_match.apply_decision(
                db,
                voiceprint_id=mine,
                embedding=_unit(1),
                decision="reassign",
                other_voiceprint_id=other,
            )
        assert result["voiceprint_id"] == other
        with Session(engine) as db:
            mine_rows = voice_template.active_templates(db, mine)
            other_rows = voice_template.active_templates(db, other)
        assert mine_rows == []
        assert len(other_rows) == 1

    def test_cancel_writes_nothing_but_marks_record(self):
        mine, _ = TestConflictLevels()._pair()
        with Session(engine) as db:
            assessment = voice_match.evaluate_confirm(
                db, voiceprint_id=mine, embedding=_mix(0.3, 0.95)
            )
            db.commit()
        with Session(engine) as db:
            voice_match.apply_decision(
                db,
                voiceprint_id=mine,
                embedding=_mix(0.3, 0.95),
                decision="cancel",
                conflict_id=assessment["conflict_id"],
            )
        with Session(engine) as db:
            rows = voice_template.active_templates(db, mine)
            record = db.get(VoiceprintConflict, assessment["conflict_id"])
        assert len(rows) == 1  # 只有原来那条
        assert record.status == "cancelled"

    def test_reassign_requires_target(self):
        mine = _make_voiceprint("张三", _unit(0))
        with Session(engine) as db:
            with pytest.raises(ValueError):
                voice_match.apply_decision(
                    db, voiceprint_id=mine, embedding=_unit(0), decision="reassign"
                )


# ---------------------------------------------------------------------------
# 匹配与阈值
# ---------------------------------------------------------------------------


class TestMatch:
    def test_empty_library_returns_none(self):
        assert voice_match.match(_unit(0)) is None
        assert voice_match.template_count() == 0

    def test_exact_template_hits(self):
        voiceprint_id = _make_voiceprint("张三", _unit(0))
        _add_template(voiceprint_id, _unit(0))
        voice_match.invalidate_cache()
        hit = voice_match.match(_unit(0))
        assert hit is not None
        assert hit["voiceprint_id"] == voiceprint_id
        assert hit["low_confidence"] is False

    def test_mid_score_marked_low_confidence(self):
        voiceprint_id = _make_voiceprint("张三", _unit(0))
        _add_template(voiceprint_id, _unit(0))
        voice_match.invalidate_cache()
        # 融合分落在 (tau_low=0.58, tau_high=0.68) 之间
        hit = voice_match.match(_mix(0.72, 0.69), speech_ms=500)
        assert hit is not None
        assert hit["low_confidence"] is True
        assert hit["name"].startswith("疑似")

    def test_feature_flag_keeps_legacy_path(self):
        assert bool(speaker.get_config("spk.template_match_enabled")) is False


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------


class TestVoiceprintTemplateApi:
    def test_list_templates(self, client, auth_headers):
        voiceprint_id = _make_voiceprint("张三", _unit(0))
        _add_template(voiceprint_id, _unit(0))
        res = client.get(
            f"/api/v1/voiceprints/{voiceprint_id}/templates", headers=auth_headers
        )
        assert res.status_code == 200, res.text
        payload = res.json()
        assert payload["max_templates"] == 5
        assert len(payload["templates"]) == 1
        assert payload["templates"][0]["slot"] == 0

    def test_confirm_sample_assess_only(self, client, auth_headers):
        mine = _make_voiceprint("张三", _unit(0))
        other = _make_voiceprint("王强", _unit(1))
        _add_template(mine, _unit(0))
        _add_template(other, _unit(1))
        voice_match.invalidate_cache()
        res = client.post(
            "/api/v1/voiceprints/confirm-sample",
            headers=auth_headers,
            json={"voiceprint_id": mine, "embedding": _mix(0.3, 0.95).tolist()},
        )
        assert res.status_code == 200, res.text
        payload = res.json()
        assert payload["level"] == "high"
        assert payload["blocking"] is True
        # 只评估不落模板
        with Session(engine) as db:
            assert len(voice_template.active_templates(db, mine)) == 1

    def test_confirm_sample_with_decision_creates_template(self, client, auth_headers):
        mine = _make_voiceprint("张三", _unit(2))
        res = client.post(
            "/api/v1/voiceprints/confirm-sample",
            headers=auth_headers,
            json={
                "voiceprint_id": mine,
                "embedding": _unit(2).tolist(),
                "decision": "confirm_self",
            },
        )
        assert res.status_code == 200, res.text
        assert res.json()["template_id"] is not None
        with Session(engine) as db:
            assert len(voice_template.active_templates(db, mine)) == 1

    def test_confirm_sample_requires_vector_or_segment(self, client, auth_headers):
        mine = _make_voiceprint("张三", _unit(0))
        res = client.post(
            "/api/v1/voiceprints/confirm-sample",
            headers=auth_headers,
            json={"voiceprint_id": mine},
        )
        assert res.status_code == 422

    def test_unknown_voiceprint_404(self, client, auth_headers):
        res = client.post(
            "/api/v1/voiceprints/confirm-sample",
            headers=auth_headers,
            json={"voiceprint_id": 987654, "embedding": [0.0] * DIM},
        )
        assert res.status_code == 404

    def test_conflict_list_and_decide(self, client, auth_headers):
        mine, _ = TestConflictLevels()._pair()
        res = client.post(
            "/api/v1/voiceprints/confirm-sample",
            headers=auth_headers,
            json={"voiceprint_id": mine, "embedding": _mix(0.3, 0.95).tolist()},
        )
        assert res.status_code == 200, res.text
        conflict_id = res.json()["conflict_id"]
        assert conflict_id is not None

        res = client.get("/api/v1/voiceprints/conflicts", headers=auth_headers)
        assert res.status_code == 200, res.text
        assert any(item["conflict_id"] == conflict_id for item in res.json())

        res = client.post(
            f"/api/v1/voiceprints/conflicts/{conflict_id}/decide",
            headers=auth_headers,
            json={"action": "cancel"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "cancelled"

    def test_patch_template_disable(self, client, auth_headers):
        mine = _make_voiceprint("张三", _unit(0))
        template_id = _add_template(mine, _unit(0))
        res = client.patch(
            f"/api/v1/voiceprints/{mine}/templates/{template_id}",
            headers=auth_headers,
            json={"disabled": True, "scenario_device": "phone"},
        )
        assert res.status_code == 200, res.text
        assert res.json()["disabled"] is True
        assert res.json()["active"] is False
        voice_match.invalidate_cache()
        assert voice_match.template_count() == 0

    def test_rebuild_endpoint_requires_meeting(self, client, auth_headers):
        res = client.post(
            "/api/v1/voiceprints/templates/rebuild?meeting_id=987654",
            headers=auth_headers,
        )
        assert res.status_code == 404


# ---------------------------------------------------------------------------
# 会后取样（真实数据回归）
# ---------------------------------------------------------------------------


class TestPostMeetingSamples:
    """会后取样回归：真实浏览器录音的信噪比中位数只有 6–8 dB。

    旧实现写了 ``snr >= 10`` 的硬门槛，导致会议 #7 的 136 段合格片段被全部
    滤掉、模板一条都建不出来（rebuild 返回 skipped=1）。这里锁住两点：
    默认不做 SNR 硬过滤；取样数量受 ``spk.template_sample_max`` 约束。
    """

    def _meeting_with_segments(self, tmp_path, monkeypatch, *, count: int, snr_db: float):
        audio = tmp_path / "m.wav"
        audio.write_bytes(b"RIFF")  # 只用来通过 _audio_path 的存在性检查
        with Session(engine) as db:
            meeting = Meeting(
                title="取样回归",
                owner_id=_admin_id(),
                audio_path=str(audio),
                duration_ms=count * 4000,
            )
            db.add(meeting)
            db.commit()
            db.refresh(meeting)
            for index in range(count):
                db.add(
                    Segment(
                        meeting_id=meeting.id,
                        segment_id=f"s{index}",
                        text="这是一句用于取样的话。",
                        is_final=True,
                        start_ms=index * 4000,
                        end_ms=index * 4000 + 3000,
                        speech_ms=3000,
                        snr_db=snr_db,
                        audio_rms=0.03,
                        speaker_key="spk_regression",
                    )
                )
            db.commit()
            meeting_id = meeting.id

        monkeypatch.setattr(
            speaker_reconcile, "_read_audio", lambda *a, **k: np.zeros(16000, dtype=np.float32)
        )
        monkeypatch.setattr(
            speaker, "extract_embedding", lambda pcm: voice_template._vec(_unit(0).tobytes())
        )
        return meeting_id

    def test_low_snr_segments_still_used(self, tmp_path, monkeypatch):
        meeting_id = self._meeting_with_segments(tmp_path, monkeypatch, count=6, snr_db=6.0)
        with Session(engine) as db:
            vectors, weights, used = voice_template._sample_vectors(
                db, meeting_id, "spk_regression"
            )
        assert len(vectors) == 6, "低信噪比片段被错误过滤，模板永远建不起来"
        assert len(weights) == 6
        assert [seg.segment_id for seg in used] == sorted(seg.segment_id for seg in used)

    def test_sample_count_is_capped(self, tmp_path, monkeypatch):
        meeting_id = self._meeting_with_segments(tmp_path, monkeypatch, count=45, snr_db=7.0)
        with Session(engine) as db:
            vectors, _weights, _used = voice_template._sample_vectors(
                db, meeting_id, "spk_regression"
            )
        assert len(vectors) == 32, "取样上限失效会让长会议耗时失控"

    def test_rebuild_reports_skip_reason(self, tmp_path, monkeypatch):
        meeting_id = self._meeting_with_segments(tmp_path, monkeypatch, count=4, snr_db=7.0)
        stats = voice_template.rebuild_meeting_templates(meeting_id)
        # 该 speaker_key 没有登记声纹，应归入 skipped_no_voiceprint 而非笼统 skipped
        assert stats["skipped_no_voiceprint"] == 1
        assert stats["skipped_no_samples"] == 0
        assert stats["skipped"] == 1

    def test_rebuild_writes_templates_for_registered_voiceprint(self, tmp_path, monkeypatch):
        meeting_id = self._meeting_with_segments(tmp_path, monkeypatch, count=8, snr_db=7.0)
        with Session(engine) as db:
            db.add(
                Voiceprint(
                    owner_id=_admin_id(),
                    name="回归说话人",
                    embedding=_unit(0).tobytes(),
                    speaker_key="spk_regression",
                    sample_ms=8000,
                    sample_count=8,
                    provisional=False,
                )
            )
            db.commit()
        stats = voice_template.rebuild_meeting_templates(meeting_id)
        assert stats["created"] + stats["updated"] >= 1
        with Session(engine) as db:
            voiceprint = db.exec(
                select(Voiceprint).where(Voiceprint.speaker_key == "spk_regression")
            ).first()
            assert voiceprint is not None
            active = voice_template.active_templates(db, voiceprint.id)
            assert 1 <= len(active) <= 5
