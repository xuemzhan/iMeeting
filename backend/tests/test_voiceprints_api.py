"""声纹接口测试。

覆盖三件事：
1. 鉴权策略：默认（REQUIRE_AUTH=0）允许匿名，开启鉴权后必须带令牌；
2. 录入 / 改名 / 删除的基本行为与边界；
3. 自动发现：开会聚出来的陌生说话人能落库并允许改名转正。
"""

from __future__ import annotations

import io
import struct
import wave

import numpy as np
import pytest


def make_wav(seconds: float = 2.0, freq: float = 200.0) -> bytes:
    """生成一段 16kHz 单声道 16bit PCM WAV（带真实波形，非静音）。"""
    n = int(16000 * seconds)
    frames = b"".join(
        struct.pack("<h", int(0.3 * 32767 * np.sin(2 * np.pi * freq * i / 16000)))
        for i in range(n)
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(frames)
    return buf.getvalue()


@pytest.fixture
def stub_embedding(monkeypatch):
    """把声纹抽取替身化，避免单测真的加载 CAM++ 模型。"""

    def fake_extract(pcm):
        v = np.ones(192, dtype=np.float32)
        return v / np.linalg.norm(v)

    from imeeting.engine import speaker

    monkeypatch.setattr(speaker, "extract_embedding", fake_extract)
    monkeypatch.setattr("imeeting.api.http.routes.voiceprints.speaker.extract_embedding", fake_extract)
    return fake_extract


class TestAuthPolicy:
    def test_anonymous_allowed_when_auth_disabled(self, client, monkeypatch):
        """默认 REQUIRE_AUTH=0：不登录也要能列声纹。

        这是实际踩到的 bug —— 应用允许匿名转写，但声纹接口硬要令牌，
        表现为「转写能用、录声纹却报未提供访问令牌」。
        """
        monkeypatch.setenv("REQUIRE_AUTH", "0")
        assert client.get("/api/v1/voiceprints").status_code == 200

    def test_anonymous_rejected_when_auth_enabled(self, client, monkeypatch):
        """开启鉴权后，匿名必须被拒——生产环境不能被绕过。"""
        monkeypatch.setenv("REQUIRE_AUTH", "1")
        resp = client.get("/api/v1/voiceprints")
        assert resp.status_code == 401
        assert "未提供访问令牌" in resp.json()["detail"]

    def test_token_still_works_when_auth_enabled(self, client, auth_headers, monkeypatch):
        monkeypatch.setenv("REQUIRE_AUTH", "1")
        assert client.get("/api/v1/voiceprints", headers=auth_headers).status_code == 200


class TestEnrollment:
    def test_enroll_and_list(self, client, stub_embedding):
        r = client.post(
            "/api/v1/voiceprints",
            data={"name": "张工"},
            files={"file": ("a.wav", make_wav(), "audio/wav")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "张工"
        assert body["provisional"] is False
        assert body["sample_ms"] >= 1900

        listed = client.get("/api/v1/voiceprints").json()
        assert [x["name"] for x in listed] == ["张工"]

    def test_same_name_overwrites(self, client, stub_embedding):
        """同一个人重录不应该产生两条记录。"""
        for _ in range(2):
            client.post(
                "/api/v1/voiceprints",
                data={"name": "张工"},
                files={"file": ("a.wav", make_wav(), "audio/wav")},
            )
        listed = client.get("/api/v1/voiceprints").json()
        assert len(listed) == 1

    def test_blank_name_rejected(self, client, stub_embedding):
        r = client.post(
            "/api/v1/voiceprints",
            data={"name": "   "},
            files={"file": ("a.wav", make_wav(), "audio/wav")},
        )
        assert r.status_code == 400

    def test_too_short_rejected(self, client, stub_embedding):
        r = client.post(
            "/api/v1/voiceprints",
            data={"name": "张工"},
            files={"file": ("a.wav", make_wav(0.5), "audio/wav")},
        )
        assert r.status_code == 400
        assert "太短" in r.json()["detail"]

    def test_bad_audio_rejected(self, client, stub_embedding):
        r = client.post(
            "/api/v1/voiceprints",
            data={"name": "张工"},
            files={"file": ("a.wav", b"not a wav at all", "audio/wav")},
        )
        assert r.status_code == 400


class TestRename:
    def _enroll(self, client, name="说话人 1", provisional=False):
        from sqlmodel import Session

        from imeeting.db import engine
        from imeeting.models import Voiceprint

        emb = np.ones(192, dtype=np.float32)
        emb /= np.linalg.norm(emb)
        with Session(engine) as db:
            row = Voiceprint(
                name=name, embedding=emb.tobytes(), provisional=provisional
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id

    def test_rename_promotes_provisional(self, client):
        vid = self._enroll(client, "说话人 1", provisional=True)
        r = client.patch(f"/api/v1/voiceprints/{vid}", json={"name": "王工"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "王工"
        # 改名即转正
        assert body["provisional"] is False

    def test_rename_blank_rejected(self, client):
        vid = self._enroll(client)
        assert client.patch(f"/api/v1/voiceprints/{vid}", json={"name": "  "}).status_code == 400

    def test_rename_conflict_rejected(self, client):
        self._enroll(client, "王工")
        vid = self._enroll(client, "说话人 1", provisional=True)
        assert client.patch(f"/api/v1/voiceprints/{vid}", json={"name": "王工"}).status_code == 409

    def test_rename_missing_404(self, client):
        assert client.patch("/api/v1/voiceprints/99999", json={"name": "谁"}).status_code == 404

    def test_delete(self, client):
        vid = self._enroll(client)
        assert client.delete(f"/api/v1/voiceprints/{vid}").status_code == 204
        assert client.get("/api/v1/voiceprints").json() == []


class TestRelabel:
    """改名必须同时改声纹库与历史片段，否则用户看着像「没生效」。"""

    def _seed(self, admin_user, speakers: list[str]):
        from sqlmodel import Session

        from imeeting.db import engine
        from imeeting.models import Meeting, Segment, Voiceprint

        emb = np.ones(192, dtype=np.float32)
        emb /= np.linalg.norm(emb)
        with Session(engine) as db:
            m = Meeting(title="t", owner_id=admin_user.id)
            db.add(m)
            db.commit()
            db.refresh(m)
            for i, spk in enumerate(speakers):
                db.add(
                    Segment(
                        meeting_id=m.id,
                        segment_id=f"s-{m.id}-{i}",
                        text=f"文本{i}",
                        is_final=True,
                        speaker=spk,
                        seq=i,
                    )
                )
            db.add(
                Voiceprint(
                    name="说话人 1",
                    embedding=emb.tobytes(),
                    provisional=True,
                    meeting_id=m.id,
                    speaker_key="spk_test1",
                )
            )
            db.commit()
            return m.id

    def test_relabel_updates_all_matching_segments(self, client, admin_user):
        from sqlmodel import Session, select

        from imeeting.db import engine
        from imeeting.models import Segment, Voiceprint

        mid = self._seed(admin_user, ["说话人 1", "说话人 1", "说话人 2"])

        r = client.post(
            "/api/v1/voiceprints/relabel",
            json={"meeting_id": mid, "from_label": "说话人 1", "to_name": "王工"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["renamed_voiceprint"] is True
        assert body["updated_segments"] == 2  # 只改匹配的两条

        with Session(engine) as db:
            segs = db.exec(select(Segment).order_by(Segment.seq)).all()
            assert [s.speaker for s in segs] == ["王工", "王工", "说话人 2"]
            vp = db.exec(select(Voiceprint)).first()
            assert vp.name == "王工"
            assert vp.provisional is False  # 改名即转正

    def test_relabel_without_voiceprint_still_updates_segments(self, client, admin_user):
        """声纹库里没有对应条目时，片段也要改——否则用户点了没反应。"""
        from sqlmodel import Session, select

        from imeeting.db import engine
        from imeeting.models import Segment

        mid = self._seed(admin_user, ["说话人 9"])
        r = client.post(
            "/api/v1/voiceprints/relabel",
            json={"meeting_id": mid, "from_label": "说话人 9", "to_name": "李工"},
        )
        assert r.status_code == 200
        assert r.json()["renamed_voiceprint"] is False
        assert r.json()["updated_segments"] == 1

        with Session(engine) as db:
            assert db.exec(select(Segment)).first().speaker == "李工"

    def test_relabel_does_not_touch_other_meetings(self, client, admin_user):
        """改名必须限定会议；旧实现按全局标签更新，误伤了另一场 43 条记录。"""
        from sqlmodel import Session, select

        from imeeting.db import engine
        from imeeting.models import Segment

        mid_a = self._seed(admin_user, ["说话人 1"])
        mid_b = self._seed(admin_user, ["说话人 1"])

        r = client.post(
            "/api/v1/voiceprints/relabel",
            json={"meeting_id": mid_a, "from_label": "说话人 1", "to_name": "王工"},
        )
        assert r.status_code == 200, r.text

        with Session(engine) as db:
            a = db.exec(select(Segment).where(Segment.meeting_id == mid_a)).all()
            b = db.exec(select(Segment).where(Segment.meeting_id == mid_b)).all()
        assert a and all(s.speaker == "王工" for s in a)
        assert b and all(s.speaker == "说话人 1" for s in b)

    def test_relabel_blank_rejected(self, client):
        r = client.post(
            "/api/v1/voiceprints/relabel",
            json={"meeting_id": 1, "from_label": "说话人 1", "to_name": " "},
        )
        assert r.status_code == 400

    def test_relabel_same_name_without_segments_is_noop(self, client):
        r = client.post(
            "/api/v1/voiceprints/relabel",
            json={"meeting_id": 1, "from_label": "说话人 1", "to_name": "说话人 1"},
        )
        assert r.status_code == 200
        assert r.json()["updated_segments"] == 0

    def test_same_name_confirms_low_confidence_speaker(self, client, admin_user):
        """同名提交是人工确认：标记 manual、分数置 1，并写审计。"""
        from sqlmodel import Session, select

        from imeeting.db import engine
        from imeeting.models import AuditLog, Segment

        mid = self._seed(admin_user, ["说话人 1", "说话人 1", "说话人 2"])
        with Session(engine) as db:
            segs = db.exec(
                select(Segment).where(
                    Segment.meeting_id == mid,
                    Segment.speaker == "说话人 1",
                )
            ).all()
            for seg in segs:
                seg.speaker_score = 0.51
                seg.speaker_source = "enrolled"
                db.add(seg)
            db.commit()

        r = client.post(
            "/api/v1/voiceprints/relabel",
            json={
                "meeting_id": mid,
                "from_label": "说话人 1",
                "to_name": "说话人 1",
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["updated_segments"] == 2

        with Session(engine) as db:
            confirmed = db.exec(
                select(Segment).where(
                    Segment.meeting_id == mid,
                    Segment.speaker == "说话人 1",
                )
            ).all()
            assert confirmed
            assert all(
                seg.speaker_source == "manual" and seg.speaker_score == 1.0
                for seg in confirmed
            )
            audit = db.exec(
                select(AuditLog).where(AuditLog.action == "speaker_confirm")
            ).first()
            assert audit is not None
            assert f"meeting:{mid}" == audit.target

    def test_merge_moves_segments_and_deletes_source(self, client, admin_user):
        """合并：同一人被拆成多个声纹时的补救手段。"""
        from sqlmodel import Session, select

        from imeeting.db import engine
        from imeeting.models import Segment, Voiceprint

        self._seed(admin_user, ["说话人 2", "说话人 3"])
        emb = np.ones(192, dtype=np.float32)
        emb /= np.linalg.norm(emb)
        with Session(engine) as db:
            src = Voiceprint(name="说话人 3", embedding=emb.tobytes(), provisional=True)
            db.add(src)
            db.commit()
            db.refresh(src)
            src_id = src.id

        with Session(engine) as db:
            tgt = db.exec(select(Voiceprint).where(Voiceprint.name == "说话人 1")).first()
            tgt_id = tgt.id

        r = client.post(f"/api/v1/voiceprints/{src_id}/merge/{tgt_id}")
        assert r.status_code == 200, r.text
        assert r.json()["updated_segments"] == 1

        with Session(engine) as db:
            segs = db.exec(select(Segment).order_by(Segment.seq)).all()
            # 只有「说话人 3」那一条改挂到「说话人 1」；「说话人 2」不受影响
            assert [s.speaker for s in segs] == ["说话人 2", "说话人 1"]
            names = {v.name for v in db.exec(select(Voiceprint)).all()}
            assert "说话人 3" not in names

    def test_merge_self_rejected(self, client, admin_user):
        from sqlmodel import Session, select

        from imeeting.db import engine
        from imeeting.models import Voiceprint

        self._seed(admin_user, ["说话人 1"])
        with Session(engine) as db:
            vid = db.exec(select(Voiceprint)).first().id
        assert client.post(f"/api/v1/voiceprints/{vid}/merge/{vid}").status_code == 400

    def test_merge_missing_404(self, client, admin_user):
        from sqlmodel import Session, select

        from imeeting.db import engine
        from imeeting.models import Voiceprint

        self._seed(admin_user, ["说话人 1"])
        with Session(engine) as db:
            vid = db.exec(select(Voiceprint)).first().id
        assert client.post(f"/api/v1/voiceprints/99999/merge/{vid}").status_code == 404

    def test_relabel_conflict_409(self, client, admin_user):
        from sqlmodel import Session

        from imeeting.db import engine
        from imeeting.models import Voiceprint

        mid = self._seed(admin_user, ["说话人 1"])
        emb = np.ones(192, dtype=np.float32)
        emb /= np.linalg.norm(emb)
        with Session(engine) as db:
            db.add(Voiceprint(name="王工", embedding=emb.tobytes()))
            db.commit()

        r = client.post(
            "/api/v1/voiceprints/relabel",
            json={"meeting_id": mid, "from_label": "说话人 1", "to_name": "王工"},
        )
        assert r.status_code == 409


class TestAutoDiscovery:
    """开会时自动聚出来的陌生说话人，应能落库并被后续会议复用。"""

    def test_save_discovered_creates_provisional(self, client):
        from imeeting.engine import speaker
        from imeeting.models import Voiceprint

        emb = np.ones(192, dtype=np.float32)
        emb /= np.linalg.norm(emb)
        speaker.save_discovered("说话人 1", emb, source_meeting_id=42)

        listed = client.get("/api/v1/voiceprints").json()
        assert len(listed) == 1
        assert listed[0]["name"] == "说话人 1"
        assert listed[0]["provisional"] is True
        assert listed[0]["source_meeting_id"] == 42
        assert listed[0]["sample_ms"] == 0
        del Voiceprint

    def test_save_discovered_is_idempotent(self, client):
        """同一场会议里同一个类被反复命中，不应写出多条。"""
        from imeeting.engine import speaker

        emb = np.ones(192, dtype=np.float32)
        emb /= np.linalg.norm(emb)
        for _ in range(3):
            speaker.save_discovered("说话人 1", emb, source_meeting_id=42)
        assert len(client.get("/api/v1/voiceprints").json()) == 1

    def test_resolver_reports_new_cluster(self):
        """resolver 必须把新聚出的说话人交出来，否则无从落库。"""
        from imeeting.engine.speaker import SpeakerResolver

        emb = np.ones(192, dtype=np.float32)
        emb /= np.linalg.norm(emb)
        r = SpeakerResolver(threshold=0.99)
        # 阈值拉满且无已录入声纹 → 必然新建聚类
        r.resolve(emb)
        assert len(r.new_clusters) == 1
        assert r.new_clusters[0][1] == "说话人 1"
