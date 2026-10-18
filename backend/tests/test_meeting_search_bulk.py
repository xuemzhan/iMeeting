"""会议搜索与批量删除。"""

from __future__ import annotations

import io
import json
import zipfile

from sqlmodel import Session, select


def _meeting(title: str, status: str = "finished", texts: list[str] | None = None) -> int:
    from imeeting.db import engine
    from imeeting.models import (
        Meeting,
        MeetingSession,
        Minutes,
        Segment,
        User,
        Voiceprint,
    )

    with Session(engine) as db:
        user = db.exec(select(User)).first()
        meeting = Meeting(title=title, owner_id=user.id, status=status)
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        for i, text in enumerate(texts or ["会议内容"]):
            db.add(
                Segment(
                    meeting_id=meeting.id,
                    segment_id=f"s-search-{meeting.id}-{i}",
                    session_id=f"sess-{meeting.id}",
                    text=text,
                    is_final=True,
                    start_ms=i * 2000,
                    end_ms=i * 2000 + 1500,
                    seq=i,
                )
            )
        db.add(
            MeetingSession(
                meeting_id=meeting.id,
                session_id=f"sess-{meeting.id}",
                duration_ms=2000,
                segment_count=1,
                status="recording" if status == "recording" else "finished",
            )
        )
        db.add(
            Minutes(
                meeting_id=meeting.id,
                summary="摘要",
                topics="[]",
                todos="[]",
                status="ready",
                version=1,
            )
        )
        db.add(
            Voiceprint(
                name=f"说话人 {meeting.id}",
                embedding=b"",
                provisional=True,
                meeting_id=meeting.id,
            )
        )
        db.commit()
        return meeting.id


def test_search_by_title_and_transcript(client):
    a = _meeting("轴承故障评审", texts=["今天讨论模型泛化。"])
    b = _meeting("项目周会", texts=["会上提到轴承故障诊断的数据问题。"])
    c = _meeting("客户访谈", texts=["客户关注交付周期。"])

    by_title = client.get("/api/v1/meetings/search", params={"q": "轴承"}).json()
    assert by_title["total"] == 2
    assert {item["id"] for item in by_title["items"]} == {a, b}

    by_text = client.get("/api/v1/meetings/search", params={"q": "交付周期"}).json()
    assert by_text["total"] == 1
    assert by_text["items"][0]["id"] == c

    filtered = client.get(
        "/api/v1/meetings/search", params={"status": "finished", "limit": 1}
    ).json()
    assert filtered["total"] == 3
    assert len(filtered["items"]) == 1


def test_bulk_delete_removes_related_rows_and_skips_recording(client):
    from imeeting.db import engine
    from imeeting.models import (
        AuditLog,
        Meeting,
        MeetingSession,
        Minutes,
        Segment,
        Voiceprint,
    )

    a = _meeting("待删除 A")
    b = _meeting("待删除 B")
    recording = _meeting("录制中", status="recording")

    resp = client.post(
        "/api/v1/meetings/bulk-delete", json={"ids": [a, b, recording]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["deleted"] == 2
    assert set(body["deleted_ids"]) == {a, b}
    assert body["skipped"] == [recording]

    with Session(engine) as db:
        assert db.get(Meeting, a).deleted_at is not None
        assert db.get(Meeting, b).deleted_at is not None
        assert db.get(Meeting, recording).deleted_at is None
        # 回收站阶段片段仍然保留，供恢复使用
        assert db.exec(select(Segment).where(Segment.meeting_id == a)).all()
        audit = db.exec(
            select(AuditLog).where(AuditLog.action == "meeting_bulk_trash")
        ).first()
        assert audit is not None
        assert json.loads(audit.payload)["deleted"] == [a, b]

    trash = client.get("/api/v1/meetings/trash").json()
    assert trash["total"] == 2
    assert {item["id"] for item in trash["items"]} == {a, b}

    restored = client.post("/api/v1/meetings/bulk-restore", json={"ids": [a]})
    assert restored.status_code == 200
    assert restored.json()["updated"] == 1
    with Session(engine) as db:
        assert db.get(Meeting, a).deleted_at is None

    purged = client.post("/api/v1/meetings/bulk-purge", json={"ids": [b]})
    assert purged.status_code == 200
    assert purged.json()["deleted"] == 1
    with Session(engine) as db:
        assert db.get(Meeting, b) is None
        assert db.exec(select(Segment).where(Segment.meeting_id == b)).all() == []
        assert db.exec(select(Minutes).where(Minutes.meeting_id == b)).all() == []
        assert (
            db.exec(
                select(MeetingSession).where(MeetingSession.meeting_id == b)
            ).all()
            == []
        )
        assert (
            db.exec(select(Voiceprint).where(Voiceprint.meeting_id == b)).all() == []
        )


def test_stale_recording_session_does_not_block_delete(client):
    from datetime import timedelta

    from imeeting.db import engine
    from imeeting.models import Meeting, MeetingSession, User, utcnow

    with Session(engine) as db:
        user = db.exec(select(User)).first()
        meeting = Meeting(title="僵尸录制", owner_id=user.id, status="recording")
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
        db.add(
            MeetingSession(
                meeting_id=meeting.id,
                session_id="stale-session",
                status="recording",
                started_at=utcnow() - timedelta(minutes=20),
            )
        )
        db.commit()
        mid = meeting.id
    response = client.post("/api/v1/meetings/bulk-delete", json={"ids": [mid]})
    assert response.status_code == 200
    assert response.json()["deleted_ids"] == [mid]


def test_fts_tracks_segment_update_and_delete(client):
    from imeeting.db import engine
    from imeeting.models import Segment

    mid = _meeting("全文检索", texts=["设备轴承故障诊断方法"])
    found = client.get(
        "/api/v1/meetings/search", params={"q": "故障诊断"}
    ).json()
    assert found["total"] == 1

    with Session(engine) as db:
        seg = db.exec(select(Segment).where(Segment.meeting_id == mid)).one()
        seg.text = "今天讨论交付周期"
        db.add(seg)
        db.commit()

    assert (
        client.get("/api/v1/meetings/search", params={"q": "故障诊断"}).json()["total"]
        == 0
    )
    assert (
        client.get("/api/v1/meetings/search", params={"q": "交付周期"}).json()["total"]
        == 1
    )

    with Session(engine) as db:
        seg = db.exec(select(Segment).where(Segment.meeting_id == mid)).one()
        db.delete(seg)
        db.commit()
    assert (
        client.get("/api/v1/meetings/search", params={"q": "交付周期"}).json()["total"]
        == 0
    )


def test_bulk_rename_archive_tags_and_export(client):
    a = _meeting("项目会议 A")
    b = _meeting("项目会议 B")

    renamed = client.post(
        "/api/v1/meetings/bulk-rename",
        json={"ids": [a, b], "find": "会议", "replace": "评审", "prefix": "【周会】"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["updated"] == 2

    updated = client.post(
        "/api/v1/meetings/bulk-update",
        json={"ids": [a, b], "archived": True, "tags_add": ["重要", "客户"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["updated"] == 2

    archived = client.get(
        "/api/v1/meetings/search",
        params={"archived": "true", "tag": "重要"},
    ).json()
    assert archived["total"] == 2
    assert all(item["archived"] for item in archived["items"])
    assert all("重要" in item["tags"] for item in archived["items"])

    exported = client.post(
        "/api/v1/meetings/bulk-export",
        json={"ids": [a, b], "format": "md"},
    )
    assert exported.status_code == 200, exported.text
    with zipfile.ZipFile(io.BytesIO(exported.content)) as zf:
        names = zf.namelist()
        assert len(names) == 2
        assert all(name.endswith(".md") for name in names)
