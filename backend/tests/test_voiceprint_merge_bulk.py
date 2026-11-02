"""全库近邻合并的执行入口（``POST /voiceprints/merge-suggestions/apply``）。

此前 ``GET /merge-suggestions`` 只给建议、从不闭环，实测库里 vp1≈vp17（0.973）
越过 ``spk.template_merge_sim`` 也无人处理。这里锁定执行入口的四条约束：
默认 dry-run、默认不合并两个已命名身份、同一条声纹一次只并一对、执行后留审计。
"""

from __future__ import annotations

import numpy as np
from sqlmodel import Session, select

from imeeting.db import engine
from imeeting.models import AuditLog, Meeting, Segment, Voiceprint

APPLY = "/api/v1/voiceprints/merge-suggestions/apply"


def _vec(primary: float, secondary: float) -> bytes:
    vector = np.zeros(192, dtype=np.float32)
    vector[0] = primary
    vector[1] = secondary
    vector /= np.linalg.norm(vector)
    return vector.tobytes()


def _voiceprint(name: str, primary: float, secondary: float, *, provisional: bool, owner: int) -> int:
    with Session(engine) as db:
        row = Voiceprint(
            owner_id=owner,
            name=name,
            embedding=_vec(primary, secondary),
            sample_ms=4000,
            sample_count=4,
            provisional=provisional,
            speaker_key=f"spk_{name}",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def test_dry_run_returns_plan_without_writing(client, auth_headers, admin_user):
    provisional = _voiceprint("说话人 1", 1.0, 0.0, provisional=True, owner=admin_user.id)
    named = _voiceprint("周YJ", 0.97, 0.243, provisional=False, owner=admin_user.id)

    resp = client.post(APPLY, json={"threshold": 0.9}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["dry_run"] is True
    assert len(body["plan"]) == 1, body
    assert body["applied"] == []
    pair = body["plan"][0]
    assert pair["score"] >= 0.9
    assert {pair["source"]["id"], pair["target"]["id"]} == {provisional, named}
    # dry-run 不得写库
    with Session(engine) as db:
        assert db.get(Voiceprint, provisional) is not None
        assert db.get(Voiceprint, named) is not None


def test_apply_merges_relabels_segments_and_audits(client, auth_headers, admin_user):
    with Session(engine) as db:
        meeting = Meeting(title="全库合并回归", owner_id=admin_user.id, audio_path="", duration_ms=12000)
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        meeting_id = meeting.id
        db.add(
            Segment(
                meeting_id=meeting_id,
                segment_id="seg-bulk-1",
                speaker="说话人 1",
                speaker_key="spk_说话人 1",
                text="把认知雷达呢认为是智能雷达的一个发展阶段",
                start_ms=0,
                end_ms=4000,
                is_final=True,
            )
        )
        db.commit()

    provisional = _voiceprint("说话人 1", 1.0, 0.0, provisional=True, owner=admin_user.id)
    named = _voiceprint("周YJ", 0.97, 0.243, provisional=False, owner=admin_user.id)

    resp = client.post(APPLY, json={"threshold": 0.9, "dry_run": False}, headers=auth_headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body["dry_run"] is False
    assert len(body["applied"]) == 1, body
    assert body["failed"] == []
    applied = body["applied"][0]
    assert applied["updated_segments"] >= 1
    assert applied["target_name"] == "周YJ"

    with Session(engine) as db:
        assert db.get(Voiceprint, provisional) is None, "source 应被合并删除"
        assert db.get(Voiceprint, named) is not None
        segment = db.exec(select(Segment).where(Segment.meeting_id == meeting_id)).one()
        assert segment.speaker == "周YJ"
        audit = db.exec(select(AuditLog).where(AuditLog.action == "voiceprint_bulk_merge")).first()
        assert audit is not None
        assert "周YJ" in audit.payload


def test_two_named_identities_are_not_merged_by_default(client, auth_headers, admin_user):
    first = _voiceprint("甲", 1.0, 0.0, provisional=False, owner=admin_user.id)
    second = _voiceprint("乙", 0.97, 0.243, provisional=False, owner=admin_user.id)

    body = client.post(APPLY, json={"threshold": 0.9, "dry_run": False}, headers=auth_headers).json()

    assert body["candidates"] >= 1
    assert body["plan"] == [], "两个已命名身份不应被自动合并"
    with Session(engine) as db:
        assert db.get(Voiceprint, first) is not None
        assert db.get(Voiceprint, second) is not None


def test_include_named_opts_in(client, auth_headers, admin_user):
    first = _voiceprint("甲", 1.0, 0.0, provisional=False, owner=admin_user.id)
    second = _voiceprint("乙", 0.97, 0.243, provisional=False, owner=admin_user.id)

    body = client.post(
        APPLY,
        json={"threshold": 0.9, "dry_run": False, "include_named": True},
        headers=auth_headers,
    ).json()

    assert len(body["applied"]) == 1, body
    with Session(engine) as db:
        survivors = [db.get(Voiceprint, first), db.get(Voiceprint, second)]
        assert sum(1 for row in survivors if row is not None) == 1


def test_one_voiceprint_is_merged_at_most_once(client, auth_headers, admin_user):
    _voiceprint("说话人 1", 1.0, 0.0, provisional=True, owner=admin_user.id)
    _voiceprint("说话人 2", 0.97, 0.243, provisional=True, owner=admin_user.id)
    _voiceprint("说话人 3", 0.96, 0.28, provisional=True, owner=admin_user.id)

    body = client.post(APPLY, json={"threshold": 0.9}, headers=auth_headers).json()

    assert body["candidates"] >= 2, body
    assert len(body["plan"]) == 1, "同一条声纹一次只能参与一对合并"
