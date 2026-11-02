"""跨设备重新注册（``POST /voiceprints/{id}/enroll``）。

同名录入会**覆盖质心**；换一台设备/换一个房间重录时，那等于把原基线扔掉。
这条路径必须只追加一条场景模板，原质心与其他场景样本保持不动（REQ-002 FR-5 末项）。
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlmodel import Session, select

from imeeting.db import engine
from imeeting.models import AuditLog, Voiceprint, VoiceprintTemplate

from .test_voiceprints_api import make_wav

ENROLL = "/api/v1/voiceprints/{voiceprint_id}/enroll"


@pytest.fixture
def rotating_embedding(monkeypatch):
    """每次调用返回方向不同的归一化向量，模拟不同设备录到的样本。"""
    calls = {"n": 0}

    def fake_extract(_pcm):
        calls["n"] += 1
        vector = np.zeros(192, dtype=np.float32)
        vector[0] = 1.0
        vector[calls["n"] % 191 + 1] = 0.45
        return vector / np.linalg.norm(vector)

    from imeeting.engine import speaker

    monkeypatch.setattr(speaker, "extract_embedding", fake_extract)
    monkeypatch.setattr("imeeting.api.http.routes.voiceprints.speaker.extract_embedding", fake_extract)
    return calls


def _create(client, headers, name: str = "张工") -> dict:
    resp = client.post(
        "/api/v1/voiceprints",
        data={"name": name},
        files={"file": ("a.wav", make_wav(2.0), "audio/wav")},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _templates(voiceprint_id: int) -> list[VoiceprintTemplate]:
    with Session(engine) as db:
        return list(
            db.exec(
                select(VoiceprintTemplate).where(VoiceprintTemplate.voiceprint_id == voiceprint_id)
            ).all()
        )


def test_reenroll_appends_template_and_keeps_centroid(client, auth_headers, rotating_embedding):
    created = _create(client, auth_headers)
    with Session(engine) as db:
        original = db.get(Voiceprint, created["id"]).embedding

    resp = client.post(
        ENROLL.format(voiceprint_id=created["id"]),
        data={"device": "phone"},
        files={"file": ("b.wav", make_wav(2.5), "audio/wav")},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["template_id"] > 0
    assert body["slot"] == 0

    with Session(engine) as db:
        assert db.get(Voiceprint, created["id"]).embedding == original, "重新注册不得覆盖质心"
        audit = db.exec(select(AuditLog).where(AuditLog.action == "voiceprint_reenroll")).first()
        assert audit is not None
        assert "phone" in audit.payload

    templates = _templates(created["id"])
    assert len(templates) == 1
    assert templates[0].scenario_device == "phone"
    assert templates[0].confirmed is True
    assert templates[0].source_meeting_id is None, "设备录入不属于任何会议"


def test_second_device_takes_the_next_free_slot(client, auth_headers, rotating_embedding):
    created = _create(client, auth_headers)

    first = client.post(
        ENROLL.format(voiceprint_id=created["id"]),
        data={"device": "phone"},
        files={"file": ("b.wav", make_wav(2.2), "audio/wav")},
        headers=auth_headers,
    )
    second = client.post(
        ENROLL.format(voiceprint_id=created["id"]),
        data={"device": "room_array"},
        files={"file": ("c.wav", make_wav(2.8), "audio/wav")},
        headers=auth_headers,
    )
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["slot"] != second.json()["slot"]

    templates = _templates(created["id"])
    assert sorted(item.slot for item in templates) == [0, 1]
    assert {item.scenario_device for item in templates} == {"phone", "room_array"}


def test_short_recording_is_rejected(client, auth_headers, rotating_embedding):
    created = _create(client, auth_headers)

    resp = client.post(
        ENROLL.format(voiceprint_id=created["id"]),
        data={"device": "phone"},
        files={"file": ("short.wav", make_wav(0.5), "audio/wav")},
        headers=auth_headers,
    )

    assert resp.status_code == 400
    assert "录音太短" in resp.json()["detail"]
    assert _templates(created["id"]) == []
