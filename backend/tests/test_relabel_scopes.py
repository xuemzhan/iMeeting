"""一次改名到底改什么：改名 / 学声纹 / 绑人员三个效果要能分开。

审查（2026-11-01-voiceprint-mechanism-audit）第 5 节的第 2 条「一次确认只做一件事」：
同一个请求既写模板、又改全局显示名、又绑人员，用户没法只想要其中一件。
现在 `RelabelRequest` 有三个独立开关，默认全开（保持既有行为），关掉任何一个都不得产生副作用。
"""

from __future__ import annotations

import numpy as np
from sqlmodel import Session, select

from imeeting.db import engine
from imeeting.models import (
    AuditLog,
    Meeting,
    MeetingParticipant,
    Segment,
    Voiceprint,
    VoiceprintTemplate,
)

RELABEL = "/api/v1/voiceprints/relabel"


def _unit(index: int = 0):
    vector = np.zeros(192, dtype=np.float32)
    vector[index] = 1.0
    return vector


def _seed(**kwargs):
    """建一场会议、一个本场待命名声纹、一条转写片段；可选再建一个已命名的全局身份。"""
    with Session(engine) as db:
        from imeeting.models import User

        admin = db.exec(select(User).where(User.username == "admin")).one()
        meeting = Meeting(title="改名口径回归", owner_id=admin.id, audio_path="", duration_ms=9000)
        db.add(meeting)
        db.commit()
        db.refresh(meeting)

        provisional = Voiceprint(
            owner_id=admin.id,
            name="说话人 1",
            embedding=_unit(0).tobytes(),
            sample_ms=4000,
            sample_count=4,
            provisional=True,
            meeting_id=meeting.id,
            source_meeting_id=meeting.id,
            speaker_key="spk_prov",
        )
        db.add(provisional)
        db.commit()
        db.refresh(provisional)

        db.add(
            Segment(
                meeting_id=meeting.id,
                segment_id="seg-scope-1",
                speaker="说话人 1",
                speaker_key="spk_prov",
                text="把认知雷达呢认为是智能雷达的一个发展阶段",
                start_ms=0,
                end_ms=4000,
                is_final=True,
                seq=0,
            )
        )
        db.commit()

        named_id = None
        if kwargs.get("with_named"):
            named = Voiceprint(
                owner_id=admin.id,
                name="张三",
                embedding=_unit(1).tobytes(),
                sample_ms=6000,
                sample_count=6,
                provisional=False,
                speaker_key="spk_named",
            )
            db.add(named)
            db.commit()
            db.refresh(named)
            named_id = named.id
            db.add(
                VoiceprintTemplate(
                    voiceprint_id=named.id,
                    slot=0,
                    embedding=_unit(1).tobytes(),
                    confirmed=True,
                )
            )
            db.commit()
        return meeting.id, provisional.id, named_id


def _payload(meeting_id: int, **extra):
    return {
        "meeting_id": meeting_id,
        "from_label": "说话人 1",
        "to_name": "张三",
        **extra,
    }


def _templates(voiceprint_id: int) -> int:
    with Session(engine) as db:
        return len(
            db.exec(
                select(VoiceprintTemplate).where(VoiceprintTemplate.voiceprint_id == voiceprint_id)
            ).all()
        )


def test_label_only_leaves_library_untouched(client, auth_headers, monkeypatch):
    import imeeting.api.http.routes.voiceprints as vp_router

    monkeypatch.setattr(vp_router, "_meeting_speaker_vector", lambda *_a, **_k: _unit(0).copy())
    meeting_id, provisional_id, _ = _seed()

    resp = client.post(
        RELABEL,
        headers=auth_headers,
        json=_payload(meeting_id, learn=False, rename_identity=False, bind_person=False),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated_segments"] == 1

    with Session(engine) as db:
        segment = db.exec(select(Segment).where(Segment.meeting_id == meeting_id)).one()
        assert segment.speaker == "张三", "转写必须改"

        row = db.get(Voiceprint, provisional_id)
        assert row.name == "说话人 1", "关掉 rename_identity 后不得改声纹库的名字"
        assert row.provisional is True

        assert db.exec(
            select(VoiceprintTemplate).where(VoiceprintTemplate.voiceprint_id == provisional_id)
        ).all() == [], "关掉 learn 后不得写模板"
        assert db.exec(
            select(MeetingParticipant).where(MeetingParticipant.meeting_id == meeting_id)
        ).all() == [], "关掉 bind_person 后不得建参会人绑定"

        audit = db.exec(
            select(AuditLog).where(AuditLog.action == "relabel_speaker")
        ).one()
        assert '"learned": false' in audit.payload
        assert '"bonnded"' not in audit.payload
        assert '"bound": false' in audit.payload

def test_learn_false_skips_the_conflict_gate_that_learn_true_raises(
    client, auth_headers, monkeypatch
):
    import imeeting.api.http.routes.voiceprints as vp_router

    monkeypatch.setattr(vp_router, "_meeting_speaker_vector", lambda *_a, **_k: _unit(0).copy())
    meeting_id, _, named_id = _seed(with_named=True)

    # 默认（learn=True）：本场待命名身份要改成另一个已命名身份 → 先 409 让人裁决
    conflicted = client.post(RELABEL, headers=auth_headers, json=_payload(meeting_id))
    assert conflicted.status_code == 409, conflicted.text
    assert conflicted.json()["detail"]["kind"] == "voiceprint_confirmation"

    # learn=False + rename_identity=False：只改标签，不写模板、不改声纹库，
    # 因此既不该弹声纹确认，也不该被重名（张三 已存在）挡住。
    allowed = client.post(
        RELABEL,
        headers=auth_headers,
        json=_payload(meeting_id, learn=False, rename_identity=False),
    )
    assert allowed.status_code == 200, allowed.text

    assert _templates(named_id) == 1, "既有模板不得被追加"
    with Session(engine) as db:
        assert db.exec(select(Segment).where(Segment.meeting_id == meeting_id)).one().speaker == "张三"


def test_defaults_still_rename_the_identity(client, auth_headers, monkeypatch):
    """不传三个开关时必须保持改造前的行为：改名 + 学模板 + 绑人员都发生。"""
    import imeeting.api.http.routes.voiceprints as vp_router

    monkeypatch.setattr(vp_router, "_meeting_speaker_vector", lambda *_a, **_k: _unit(0).copy())
    meeting_id, provisional_id, _ = _seed()

    resp = client.post(RELABEL, headers=auth_headers, json=_payload(meeting_id))
    assert resp.status_code == 200, resp.text
    assert resp.json()["renamed_voiceprint"] is True

    with Session(engine) as db:
        row = db.get(Voiceprint, provisional_id)
        assert row.name == "张三" and row.provisional is False
